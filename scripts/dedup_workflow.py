#!/usr/bin/env python3
"""Dedup workflow: org merge + request dedup + migrate to requests/.

Phase 1 — Org dedup:   merge variant org folders in newly_orged_requests/ via ORG_CANONICAL.
Phase 2 — Request dedup: within each org, detect and semantically merge duplicate requests.
Phase 3 — Migrate:     move deduped requests into requests/org-slug/slug/slug.md.

Creates a single PR with all changes.

Usage:
    uv run python scripts/dedup_workflow.py              # all phases
    uv run python scripts/dedup_workflow.py --phase orgs
    uv run python scripts/dedup_workflow.py --phase requests
    uv run python scripts/dedup_workflow.py --phase migrate
    uv run python scripts/dedup_workflow.py --dry-run
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from dedupe_newly_orged import dedupe_orgs
from normalize_requests import generate_request_filename
from utils import (
    cursor_agent_run,
    gh_pr_create,
    git,
    log,
    make_slug,
    parse_frontmatter,
    render_frontmatter,
)

SCRIPT_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
STAGING_DIR = os.path.join(PROJECT_ROOT, "newly_orged_requests")
REQUESTS_DIR = os.path.join(PROJECT_ROOT, "requests")
PROMPTS_DIR = os.path.join(PROJECT_ROOT, "prompts")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_prompt(name: str, **kwargs: str) -> str:
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    with open(path) as f:
        template = f.read()
    for key, value in kwargs.items():
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


def _parse_json_response(text: str) -> list | None:
    """Extract a JSON array from a (possibly fenced) LLM response."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    try:
        result = json.loads(text)
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


def _unique_slug(parent: str, base: str) -> str:
    """Return a slug that doesn't already exist as a subfolder of parent."""
    candidate = base
    n = 1
    while os.path.exists(os.path.join(parent, candidate)):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _summarize_request(request_md_path: str) -> dict:
    """Read a newly_orged_requests request.md and return a summary dict."""
    with open(request_md_path) as f:
        text = f.read()
    meta, body = parse_frontmatter(text)
    return {
        "path": request_md_path,
        "org": os.path.basename(os.path.dirname(os.path.dirname(request_md_path))),
        "date_received": str(meta.get("date_received", "")),
        "body": body[:1500],
    }


def _consolidate_attachments(src_folder: str, dst_folder: str, dry_run: bool) -> int:
    """Copy non-request.md files from src into dst, deduplicating by content hash.

    Returns count of files copied.
    """
    existing_hashes = {
        _file_hash(os.path.join(dst_folder, f))
        for f in os.listdir(dst_folder)
        if f != "request.md" and os.path.isfile(os.path.join(dst_folder, f))
    }

    copied = 0
    for fname in sorted(os.listdir(src_folder)):
        if fname == "request.md":
            continue
        src = os.path.join(src_folder, fname)
        if not os.path.isfile(src):
            continue
        h = _file_hash(src)
        if h in existing_hashes:
            log(f"    Skipping duplicate attachment (same hash): {fname}")
            continue
        # Find a unique destination filename
        dst_name = fname
        stem, ext = os.path.splitext(fname)
        n = 1
        while os.path.exists(os.path.join(dst_folder, dst_name)):
            dst_name = f"{stem}-{n}{ext}"
            n += 1
        if not dry_run:
            shutil.copy2(src, os.path.join(dst_folder, dst_name))
        existing_hashes.add(h)
        copied += 1
    return copied


# ---------------------------------------------------------------------------
# Phase 1: Org dedup
# ---------------------------------------------------------------------------

def phase_orgs(dry_run: bool) -> int:
    log("Phase 1: merging duplicate org folders...")
    moved = dedupe_orgs(STAGING_DIR, dry_run)
    log(f"  {'Would move' if dry_run else 'Moved'} {moved} request folder(s)")
    return moved


# ---------------------------------------------------------------------------
# Phase 2: Request dedup within each org
# ---------------------------------------------------------------------------

def _merge_group(group_paths: list[str], dry_run: bool) -> str:
    """Semantic merge of duplicate requests into the first folder.

    Returns the path of the surviving request.md.
    """
    keeper_dir = os.path.dirname(group_paths[0])
    keeper_md = group_paths[0]

    documents_block = ""
    for path in group_paths:
        with open(path) as f:
            documents_block += f"=== {path} ===\n{f.read()}\n\n"

    prompt = _load_prompt("merge-duplicates", documents=documents_block)
    log(f"    Running merge agent for {len(group_paths)} request(s)...")

    if not dry_run:
        merged = cursor_agent_run(prompt, cwd=PROJECT_ROOT)
        text = merged.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1])

        # Inject merged_from into frontmatter
        meta, body = parse_frontmatter(text)
        meta["merged_from"] = group_paths
        text = render_frontmatter(meta, body)

        with open(keeper_md, "w") as f:
            f.write(text)

    # Consolidate attachments from other folders into keeper
    for path in group_paths[1:]:
        src_dir = os.path.dirname(path)
        n = _consolidate_attachments(src_dir, keeper_dir, dry_run)
        log(f"    Copied {n} attachment(s) from {os.path.basename(src_dir)}")
        if not dry_run:
            shutil.rmtree(src_dir)
        else:
            log(f"    [dry-run] Would remove {src_dir}")

    return keeper_md


def phase_requests(dry_run: bool) -> int:
    log("Phase 2: detecting and merging duplicate requests within each org...")
    total_merged = 0

    for org_slug in sorted(os.listdir(STAGING_DIR)):
        org_dir = os.path.join(STAGING_DIR, org_slug)
        if not os.path.isdir(org_dir):
            continue

        req_folders = [
            os.path.join(org_dir, r)
            for r in sorted(os.listdir(org_dir))
            if os.path.isdir(os.path.join(org_dir, r))
            and os.path.exists(os.path.join(org_dir, r, "request.md"))
        ]

        if len(req_folders) < 2:
            log(f"  {org_slug}: {len(req_folders)} request(s) — skipping dedup")
            continue

        log(f"  {org_slug}: {len(req_folders)} request(s) — detecting duplicates...")

        summaries = [_summarize_request(os.path.join(d, "request.md")) for d in req_folders]
        prompt = _load_prompt(
            "detect-duplicates-within-org",
            requests=json.dumps(summaries, indent=2, ensure_ascii=False),
        )

        try:
            response = cursor_agent_run(prompt, cwd=PROJECT_ROOT)
        except RuntimeError as e:
            log(f"  {org_slug}: agent error — {e}; skipping")
            continue

        groups = _parse_json_response(response)
        if groups is None:
            log(f"  {org_slug}: failed to parse agent response; skipping")
            continue

        groups = [g for g in groups if isinstance(g, list) and len(g) >= 2]
        if not groups:
            log(f"  {org_slug}: no duplicates found")
            continue

        log(f"  {org_slug}: {len(groups)} duplicate group(s)")
        for i, group in enumerate(groups, 1):
            valid = [p for p in group if os.path.exists(p)]
            if len(valid) < 2:
                log(f"    Group {i}: not enough valid paths, skipping")
                continue
            log(f"    Group {i}: merging {[os.path.basename(os.path.dirname(p)) for p in valid]}")
            _merge_group(valid, dry_run)
            total_merged += len(valid) - 1

    log(f"Phase 2 done: removed {total_merged} duplicate request folder(s)")
    return total_merged


# ---------------------------------------------------------------------------
# Phase 3: Migrate to requests/
# ---------------------------------------------------------------------------

def phase_migrate(dry_run: bool) -> list[str]:
    log("Phase 3: migrating to requests/...")
    created: list[str] = []

    for org_slug in sorted(os.listdir(STAGING_DIR)):
        org_dir = os.path.join(STAGING_DIR, org_slug)
        if not os.path.isdir(org_dir):
            continue

        for req_name in sorted(os.listdir(org_dir)):
            req_dir = os.path.join(org_dir, req_name)
            req_md = os.path.join(req_dir, "request.md")
            if not os.path.isdir(req_dir) or not os.path.exists(req_md):
                continue

            with open(req_md) as f:
                text = f.read()
            meta, body = parse_frontmatter(text)

            # Generate slug via Cursor agent
            req_dict = {
                "organization": meta.get("organization", org_slug),
                "summary": body[:500],
            }
            try:
                slug = generate_request_filename(req_dict)
            except Exception as e:
                log(f"  {org_slug}/{req_name}: filename generation failed ({e}), using fallback")
                slug = make_slug("", req_dict["organization"], include_date=False)

            # Destination: requests/org-slug/slug/slug.md
            dest_org_dir = os.path.join(REQUESTS_DIR, org_slug)
            dest_slug = _unique_slug(dest_org_dir, slug)
            dest_dir = os.path.join(dest_org_dir, dest_slug)
            dest_md = os.path.join(dest_dir, f"{dest_slug}.md")

            log(f"  {org_slug}/{req_name} -> requests/{org_slug}/{dest_slug}/")

            if not dry_run:
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(req_md, dest_md)
                created.append(dest_md)

                # Copy attachments
                for fname in sorted(os.listdir(req_dir)):
                    if fname == "request.md":
                        continue
                    src = os.path.join(req_dir, fname)
                    if os.path.isfile(src):
                        shutil.copy2(src, os.path.join(dest_dir, fname))
                        created.append(os.path.join(dest_dir, fname))

                shutil.rmtree(req_dir)
            else:
                created.append(dest_md)

        # Remove empty org dir
        if not dry_run and os.path.isdir(org_dir):
            try:
                os.rmdir(org_dir)
            except OSError:
                pass

    log(f"Phase 3 done: migrated {len([f for f in created if f.endswith('.md')])} request(s)")
    return created


# ---------------------------------------------------------------------------
# PR creation
# ---------------------------------------------------------------------------

def _create_pr(phases_run: list[str], migrated: list[str], dry_run: bool):
    if dry_run:
        log("[dry-run] Would create PR")
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    short_hash = hashlib.sha256(str(migrated).encode()).hexdigest()[:6]
    branch = f"dedup/{today}-{short_hash}"

    git("checkout", "-b", branch)

    # Stage all changes
    git("add", "newly_orged_requests", "requests")
    status = git("status", "--porcelain")
    if not status:
        log("No changes to commit.")
        git("checkout", "main")
        git("branch", "-D", branch)
        return

    phases_label = "+".join(phases_run)
    git("commit", "-m", f"dedup({phases_label}): merge orgs, dedup requests, migrate to requests/")
    git("push", "-u", "origin", branch)

    md_files = [f for f in migrated if f.endswith(".md")]
    body_lines = [f"## Dedup workflow — {today}\n", f"Phases: {phases_label}\n"]
    body_lines.append(f"Migrated **{len(md_files)}** request(s) into `requests/`:\n")
    for f in md_files:
        body_lines.append(f"- `{os.path.relpath(f, PROJECT_ROOT)}`")

    pr_title = f"dedup: org merge + request dedup + migrate ({today})"
    pr_url = gh_pr_create(pr_title, "\n".join(body_lines))
    log(f"Created PR: {pr_url}")

    git("checkout", "main")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Dedup workflow: orgs + requests + migrate")
    parser.add_argument(
        "--phase",
        choices=["orgs", "requests", "migrate"],
        help="Run a single phase only",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without modifying files")
    args = parser.parse_args()

    if not os.path.isdir(STAGING_DIR):
        log(f"Staging dir not found: {STAGING_DIR}")
        sys.exit(1)

    phases_run = []
    migrated: list[str] = []

    if args.phase in (None, "orgs"):
        phase_orgs(args.dry_run)
        phases_run.append("orgs")

    if args.phase in (None, "requests"):
        phase_requests(args.dry_run)
        phases_run.append("requests")

    if args.phase in (None, "migrate"):
        migrated = phase_migrate(args.dry_run)
        phases_run.append("migrate")

    if not args.dry_run and migrated:
        _create_pr(phases_run, migrated, args.dry_run)

    log("Done.")


if __name__ == "__main__":
    main()
