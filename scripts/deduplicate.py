#!/usr/bin/env python3
"""Process 2: Detect semantically duplicate requests and create a merge PR."""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from glob import glob

sys.path.insert(0, os.path.dirname(__file__))

from utils import (
    git,
    git_commit_and_push,
    gh_pr_create,
    log,
    opencode_run,
    parse_frontmatter,
    render_frontmatter,
)


REQUESTS_DIR = "requests"
MARKER_FILE = "logs/last-dedup-marker"
PROMPTS_DIR = "prompts"


def get_new_files(marker_path: str) -> tuple[list[str], list[str]]:
    """Identify files added since the last dedup run.

    Returns (new_files, existing_files) — both as paths relative to repo root.
    """
    all_files = sorted(glob(os.path.join(REQUESTS_DIR, "*.md")))

    if not os.path.exists(marker_path):
        return all_files, []

    with open(marker_path) as f:
        last_sha = f.read().strip()

    if not last_sha:
        return all_files, []

    try:
        diff_output = git(
            "log", f"{last_sha}..HEAD",
            "--diff-filter=A", "--name-only", "--pretty=format:",
        )
    except RuntimeError:
        log(f"Could not diff from marker {last_sha} — treating all files as new.")
        return all_files, []

    added = {line.strip() for line in diff_output.splitlines() if line.strip()}
    new_files = [f for f in all_files if f in added]
    existing_files = [f for f in all_files if f not in added]

    return new_files, existing_files


def summarize_file(path: str, full: bool = False) -> dict:
    """Read a request file and return a summary dict."""
    with open(path) as f:
        text = f.read()

    meta, body = parse_frontmatter(text)

    summary = {
        "file": path,
        "from": meta.get("from", ""),
        "subject": meta.get("subject", ""),
        "date": meta.get("date", ""),
    }

    if full:
        summary["body"] = body[:2000]
    else:
        summary["body"] = body[:500]

    return summary


def load_prompt(name: str, **kwargs: str) -> str:
    """Load a prompt template from prompts/ and fill in {{placeholder}} values."""
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    with open(path) as f:
        template = f.read()
    for key, value in kwargs.items():
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


def detect_duplicates(new_summaries: list[dict], existing_summaries: list[dict]) -> list[list[str]]:
    """Use opencode to find duplicate groups between new and existing files.

    Returns a list of groups, where each group is a list of file paths.
    """
    if not new_summaries:
        return []

    prompt = load_prompt(
        "detect-duplicates",
        new_requests=json.dumps(new_summaries, indent=2),
        existing_requests=json.dumps(existing_summaries, indent=2),
    )

    response = opencode_run(prompt)

    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])

    try:
        groups = json.loads(text)
    except json.JSONDecodeError:
        log(f"Failed to parse LLM response as JSON:\n{response}")
        return []

    if not isinstance(groups, list):
        return []

    return [g for g in groups if isinstance(g, list) and len(g) >= 2]


def merge_group(group_files: list[str]) -> tuple[str, str]:
    """Use opencode to merge a group of duplicate files into one unified document.

    Returns (merged_markdown, suggested_filename).
    """
    documents_block = ""
    for path in group_files:
        with open(path) as f:
            documents_block += f"=== {path} ===\n{f.read()}\n\n"

    prompt = load_prompt("merge-duplicates", documents=documents_block)

    merged = opencode_run(prompt)

    # Strip markdown code fences if present
    text = merged.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])

    short_hash = hashlib.sha256("|".join(sorted(group_files)).encode()).hexdigest()[:8]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"{today}-merged-{short_hash}.md"

    return text, filename


def update_marker():
    """Write the current HEAD SHA to the marker file."""
    sha = git("rev-parse", "HEAD")
    os.makedirs(os.path.dirname(MARKER_FILE), exist_ok=True)
    with open(MARKER_FILE, "w") as f:
        f.write(sha)


def main():
    log("Starting deduplication run...")

    new_files, existing_files = get_new_files(MARKER_FILE)

    if not new_files:
        log("No new files since last dedup run.")
        update_marker()
        git_commit_and_push([MARKER_FILE], "dedup: update marker (no new files)")
        return

    log(f"Found {len(new_files)} new file(s) and {len(existing_files)} existing file(s).")

    new_summaries = [summarize_file(f, full=True) for f in new_files]
    existing_summaries = [summarize_file(f, full=False) for f in existing_files]

    log("Detecting duplicates via LLM...")
    groups = detect_duplicates(new_summaries, existing_summaries)

    if not groups:
        log("No duplicates found.")
        update_marker()
        git_commit_and_push([MARKER_FILE], "dedup: update marker (no duplicates found)")
        return

    log(f"Found {len(groups)} duplicate group(s).")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    short_hash = hashlib.sha256(str(groups).encode()).hexdigest()[:6]
    branch = f"dedup/{today}-{short_hash}"

    git("checkout", "-b", branch)

    all_removed = []
    all_created = []
    pr_body_lines = ["## Merged Duplicate Requests\n"]

    for i, group in enumerate(groups, 1):
        valid_files = [f for f in group if os.path.exists(f)]
        if len(valid_files) < 2:
            log(f"  Group {i}: not enough valid files, skipping.")
            continue

        log(f"  Merging group {i}: {valid_files}")
        merged_text, filename = merge_group(valid_files)

        merged_path = os.path.join(REQUESTS_DIR, filename)
        with open(merged_path, "w") as f:
            f.write(merged_text)

        for old_file in valid_files:
            os.remove(old_file)
            git("add", old_file)

        git("add", merged_path)

        files_list = ", ".join(os.path.basename(f) for f in valid_files)
        git("commit", "-m", f"dedup: merge group {i} — {files_list} -> {filename}")

        all_removed.extend(valid_files)
        all_created.append(merged_path)

        pr_body_lines.append(f"### Group {i}")
        pr_body_lines.append(f"Merged into `{filename}`:")
        for f in valid_files:
            pr_body_lines.append(f"- `{f}`")
        pr_body_lines.append("")

    if not all_created:
        log("No valid merges were performed.")
        git("checkout", "main")
        git("branch", "-D", branch)
        update_marker()
        git_commit_and_push([MARKER_FILE], "dedup: update marker (no valid merges)")
        return

    git("push", "-u", "origin", branch)

    pr_title = f"Merge duplicate requests ({today})"
    pr_body = "\n".join(pr_body_lines)
    pr_url = gh_pr_create(pr_title, pr_body)
    log(f"Created PR: {pr_url}")

    git("checkout", "main")
    update_marker()
    git_commit_and_push([MARKER_FILE], f"dedup: update marker after {pr_url}")

    log("Deduplication complete.")


if __name__ == "__main__":
    main()
