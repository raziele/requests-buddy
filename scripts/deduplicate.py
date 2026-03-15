#!/usr/bin/env python3
"""Process 4: Detect semantically duplicate requests and create a merge PR."""

import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from glob import glob

sys.path.insert(0, os.path.dirname(__file__))

from normalize_requests import generate_request_filename
from utils import (
    cursor_agent_run,
    git,
    git_commit_and_push,
    gh_pr_create,
    log,
    make_slug,
    parse_frontmatter,
)


REQUESTS_DIR = "requests"
MARKER_FILE = "logs/last-dedup-marker"
PROMPTS_DIR = "prompts"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DETECT_BATCH_SIZE = 20  # max new files per LLM call to stay under Linux ARG_MAX


def get_new_files(marker_path: str) -> tuple[list[str], list[str]]:
    """Identify files added since the last dedup run.

    Returns (new_files, existing_files) — both as paths relative to repo root.
    """
    all_files = sorted(glob(os.path.join(REQUESTS_DIR, "**", "*.md"), recursive=True))

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


def _extract_json_array(text: str) -> list | None:
    """Extract a JSON array from an LLM response, tolerating surrounding prose and code fences."""
    # Try the whole response first
    try:
        result = json.loads(text.strip())
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    # Find the first [...] block in the text (handles fenced + prose responses)
    match = re.search(r'\[[\s\S]*\]', text)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
    return None


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def summarize_file(path: str, full: bool = False) -> dict:
    """Read a request file and return a summary dict."""
    with open(path) as f:
        text = f.read()

    meta, body = parse_frontmatter(text)

    summary = {
        "file": path,
        "organization": meta.get("organization", meta.get("from", "")),
        "date_received": meta.get("date_received", meta.get("date", "")),
        "subject": meta.get("subject", meta.get("summary", "")),
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


def _detect_duplicates_batch(new_summaries: list[dict], existing_summaries: list[dict]) -> list[list[str]]:
    """Run one LLM call for a single batch of new files against all existing files."""
    prompt = load_prompt(
        "detect-duplicates",
        new_requests=json.dumps(new_summaries, indent=2),
        existing_requests=json.dumps(existing_summaries, indent=2),
    )

    response = cursor_agent_run(prompt, cwd=PROJECT_ROOT)

    groups = _extract_json_array(response)
    if groups is None:
        log(f"Failed to parse LLM response as JSON:\n{response}")
        return []

    return [g for g in groups if isinstance(g, list) and len(g) >= 2]


def detect_duplicates(new_summaries: list[dict], existing_summaries: list[dict]) -> list[list[str]]:
    """Use Cursor agent to find duplicate groups between new and existing files.

    Processes new files in batches of DETECT_BATCH_SIZE to stay under the
    Linux per-argument size limit (MAX_ARG_STRLEN ~128 KB).

    Returns a list of groups, where each group is a list of file paths.
    """
    if not new_summaries:
        return []

    all_groups: list[list[str]] = []
    for i in range(0, len(new_summaries), DETECT_BATCH_SIZE):
        batch = new_summaries[i:i + DETECT_BATCH_SIZE]
        log(f"  Batch {i // DETECT_BATCH_SIZE + 1}/{-(-len(new_summaries) // DETECT_BATCH_SIZE)}: {len(batch)} new file(s)")
        groups = _detect_duplicates_batch(batch, existing_summaries)
        all_groups.extend(groups)

    return all_groups


def merge_group(group_files: list[str]) -> tuple[str, list[str]]:
    """Use Cursor agent to merge a group of duplicate files into one unified document.

    Writes the merged result to requests/org-slug/slug/slug.md, copies attachments
    (deduped by hash), and removes the source directories.

    Returns (new_md_path, removed_dirs).
    """
    documents_block = ""
    for path in group_files:
        with open(path) as f:
            documents_block += f"=== {path} ===\n{f.read()}\n\n"

    prompt = load_prompt("merge-duplicates", documents=documents_block)
    merged = cursor_agent_run(prompt, cwd=PROJECT_ROOT)

    text = merged.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])

    # Derive destination path from merged content
    meta, body = parse_frontmatter(text)
    # If the merged frontmatter yields "unknown", fall back to the first non-"unknown"
    # org slug already present in the source file paths (requests/org-slug/req-slug/slug.md)
    org_slug = make_slug("", meta.get("organization", "unknown"), include_date=False)
    if org_slug == "unknown":
        for src in group_files:
            candidate = src.replace("\\", "/").split("/")[1]
            if candidate and candidate != "unknown":
                org_slug = candidate
                break
    req = {"organization": meta.get("organization", org_slug), "summary": body[:500]}
    slug = generate_request_filename(req)

    dest_org_dir = os.path.join(REQUESTS_DIR, org_slug)
    dest_slug = slug
    n = 1
    while os.path.exists(os.path.join(dest_org_dir, dest_slug)):
        dest_slug = f"{slug}-{n}"
        n += 1
    dest_dir = os.path.join(dest_org_dir, dest_slug)
    dest_md = os.path.join(dest_dir, f"{dest_slug}.md")

    os.makedirs(dest_dir, exist_ok=True)
    with open(dest_md, "w") as f:
        f.write(text)

    # Copy attachments from all source dirs, dedup by content hash
    existing_hashes: set[str] = set()
    for group_md in group_files:
        src_dir = os.path.dirname(group_md)
        for fname in sorted(os.listdir(src_dir)):
            if fname.endswith(".md"):
                continue
            src = os.path.join(src_dir, fname)
            if not os.path.isfile(src):
                continue
            h = _file_hash(src)
            if h in existing_hashes:
                log(f"    Skipping duplicate attachment (same hash): {fname}")
                continue
            dst_name = fname
            stem, ext = os.path.splitext(fname)
            k = 1
            while os.path.exists(os.path.join(dest_dir, dst_name)):
                dst_name = f"{stem}-{k}{ext}"
                k += 1
            shutil.copy2(src, os.path.join(dest_dir, dst_name))
            existing_hashes.add(h)

    # Remove source dirs
    removed_dirs = [os.path.dirname(p) for p in group_files]
    for src_dir in removed_dirs:
        shutil.rmtree(src_dir)

    return dest_md, removed_dirs


def _detect_within_org_duplicates(files: list[str]) -> list[list[str]]:
    """Detect duplicates within each org among a list of files.

    Groups files by org slug (requests/org-slug/req-slug/slug.md) and runs
    detect-duplicates-within-org for any org with 2+ files. This catches
    duplicates that were all added in the same batch (no existing files to
    compare against in the cross-org pass).
    """
    by_org: dict[str, list[str]] = {}
    for f in files:
        parts = f.replace("\\", "/").split("/")
        try:
            idx = parts.index("requests")
            org = parts[idx + 1] if idx + 1 < len(parts) else "unknown"
        except ValueError:
            org = "unknown"
        by_org.setdefault(org, []).append(f)

    all_groups: list[list[str]] = []
    for org, org_files in sorted(by_org.items()):
        if len(org_files) < 2:
            continue
        log(f"  Within-org dedup: {org} ({len(org_files)} file(s))")
        summaries = [summarize_file(f, full=True) for f in org_files]
        prompt = load_prompt(
            "detect-duplicates-within-org",
            requests=json.dumps(summaries, indent=2, ensure_ascii=False),
        )
        try:
            response = cursor_agent_run(prompt, cwd=PROJECT_ROOT)
        except RuntimeError as e:
            log(f"  {org}: agent error — {e}; skipping")
            continue
        groups = _extract_json_array(response)
        if groups is None:
            log(f"  {org}: failed to parse response; skipping")
            continue
        all_groups.extend(g for g in groups if isinstance(g, list) and len(g) >= 2)

    return all_groups


def update_marker():
    """Write the current HEAD SHA to the marker file."""
    sha = git("rev-parse", "HEAD")
    os.makedirs(os.path.dirname(MARKER_FILE), exist_ok=True)
    with open(MARKER_FILE, "w") as f:
        f.write(sha)


def _commit_marker(message: str):
    """Commit and push the marker file; log a warning if push fails (e.g. local runs)."""
    current_branch = git("rev-parse", "--abbrev-ref", "HEAD")
    try:
        git_commit_and_push([MARKER_FILE], message, branch=current_branch)
    except Exception as e:
        log(f"Warning: could not push marker update ({e}). Marker written locally.")


def main():
    log("Starting deduplication run...")

    new_files, existing_files = get_new_files(MARKER_FILE)

    if not new_files:
        log("No new files since last dedup run.")
        update_marker()
        _commit_marker("dedup: update marker (no new files)")
        return

    log(f"Found {len(new_files)} new file(s) and {len(existing_files)} existing file(s).")

    new_summaries = [summarize_file(f, full=True) for f in new_files]
    existing_summaries = [summarize_file(f, full=False) for f in existing_files]

    log("Detecting duplicates via LLM (cross-org)...")
    groups = detect_duplicates(new_summaries, existing_summaries)

    log("Detecting duplicates within each org...")
    within_org_groups = _detect_within_org_duplicates(new_files)

    # Merge both group lists, deduplicating by file-set identity
    seen: set[frozenset] = {frozenset(g) for g in groups}
    for g in within_org_groups:
        key = frozenset(g)
        if key not in seen:
            seen.add(key)
            groups.append(g)

    if not groups:
        log("No duplicates found.")
        update_marker()
        _commit_marker("dedup: update marker (no duplicates found)")
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
        dest_md, removed_dirs = merge_group(valid_files)

        # Stage removed dirs and new file
        git("add", "-u", REQUESTS_DIR)
        git("add", dest_md)

        dest_slug = os.path.basename(os.path.dirname(dest_md))
        files_list = ", ".join(os.path.basename(os.path.dirname(f)) for f in valid_files)
        git("commit", "-m", f"dedup: merge group {i} — {files_list} -> {dest_slug}")

        all_removed.extend(removed_dirs)
        all_created.append(dest_md)

        pr_body_lines.append(f"### Group {i}")
        pr_body_lines.append(f"Merged into `{os.path.relpath(dest_md, PROJECT_ROOT)}`:")
        for f in valid_files:
            pr_body_lines.append(f"- `{f}`")
        pr_body_lines.append("")

    if not all_created:
        log("No valid merges were performed.")
        git("checkout", "main")
        git("branch", "-D", branch)
        update_marker()
        _commit_marker("dedup: update marker (no valid merges)")
        return

    git("push", "-u", "origin", branch)

    pr_title = f"Merge duplicate requests ({today})"
    pr_body = "\n".join(pr_body_lines)
    pr_url = gh_pr_create(pr_title, pr_body)
    log(f"Created PR: {pr_url}")

    git("checkout", "main")
    update_marker()
    _commit_marker(f"dedup: update marker after {pr_url}")

    log("Deduplication complete.")


if __name__ == "__main__":
    main()
