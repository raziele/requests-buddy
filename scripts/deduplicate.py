#!/usr/bin/env python3
"""Deduplicate requests: org merge, unknown resolution, per-org request dedup.

Runs every time on all files in requests/ — no marker state.

Phases:
  1. Org dedup   — merge org folders with similar names (confirmed by LLM)
  2. Unknown     — match requests/unknown/ entries to real orgs or suggest new slug
  3. Requests    — within each org, merge duplicate requests
                   (attachment-hash grouping first, then LLM semantic dedup)
  4. PR          — commit all changes and open a pull request

Usage:
    uv run python scripts/deduplicate.py
    uv run python scripts/deduplicate.py --phase orgs
    uv run python scripts/deduplicate.py --phase unknown
    uv run python scripts/deduplicate.py --phase requests
    uv run python scripts/deduplicate.py --dry-run
"""

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import re
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from normalize_requests import generate_request_filename
from normalize_requests import generate_request_filename
from utils import (
    cursor_agent_run,
    gh_pr_create,
    git,
    log,
    make_slug,
    make_slug,
    parse_frontmatter,
)

REQUESTS_DIR = "requests"
PROMPTS_DIR = "prompts"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SIMILARITY_THRESHOLD = 0.7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _attachment_hashes(md_path: str) -> list[str]:
    """Return sorted SHA-256 hashes of all non-.md files in the same directory."""
    folder = os.path.dirname(md_path)
    hashes = []
    for fname in sorted(os.listdir(folder)):
        if fname.endswith(".md"):
            continue
        fpath = os.path.join(folder, fname)
        if os.path.isfile(fpath):
            hashes.append(_file_hash(fpath))
    return sorted(hashes)


def _extract_json(text: str) -> list | dict | None:
    """Extract a JSON value from an LLM response, tolerating prose and code fences."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r'[\[{][\s\S]*[\]}]', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def _load_prompt(name: str, **kwargs: str) -> str:
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    with open(path) as f:
        template = f.read()
    for key, value in kwargs.items():
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


def _list_org_dirs() -> list[str]:
    return [
        d for d in sorted(os.listdir(REQUESTS_DIR))
        if os.path.isdir(os.path.join(REQUESTS_DIR, d))
    ]


def _list_request_mds(org_dir: str) -> list[str]:
    """Return one .md file per request subfolder (skips loose files at org level)."""
    result = []
    for req_name in sorted(os.listdir(org_dir)):
        req_dir = os.path.join(org_dir, req_name)
        if not os.path.isdir(req_dir):
            continue
        mds = [f for f in os.listdir(req_dir) if f.endswith(".md")]
        if not mds:
            log(f"  Warning: no .md in {req_dir}, skipping")
            continue
        result.append(os.path.join(req_dir, mds[0]))
    return result


def _unique_dest(parent_dir: str, base_name: str) -> str:
    """Return a name that doesn't already exist as a child of parent_dir."""
    candidate = base_name
    n = 1
    while os.path.exists(os.path.join(parent_dir, candidate)):
        candidate = f"{base_name}-{n}"
        n += 1
    return candidate


def _safe_slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", s.lower()).strip("-")


# ---------------------------------------------------------------------------
# Phase 1: Org dedup
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        px, py = self.find(x), self.find(y)
        if px != py:
            self.parent[px] = py

    def groups(self) -> list[list]:
        buckets: dict = {}
        for x in self.parent:
            buckets.setdefault(self.find(x), []).append(x)
        return list(buckets.values())


def _similar_org_groups(slugs: list[str]) -> list[list[str]]:
    """Return groups of slugs with pairwise similarity >= SIMILARITY_THRESHOLD."""
    uf = _UnionFind(slugs)
    for i, a in enumerate(slugs):
        for j, b in enumerate(slugs):
            if j <= i:
                continue
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            if ratio >= SIMILARITY_THRESHOLD:
                uf.union(a, b)
    return [g for g in uf.groups() if len(g) >= 2]


def phase_orgs(dry_run: bool = False) -> int:
    log("Phase 1: org dedup...")
    org_slugs = [s for s in _list_org_dirs() if s != "unknown"]
    candidate_groups = _similar_org_groups(org_slugs)

    if not candidate_groups:
        log("  No similar org names found.")
        return 0

    merged = 0
    for group in candidate_groups:
        log(f"  Candidate group: {group}")
        try:
            response = cursor_agent_run(
                _load_prompt("detect-similar-orgs", orgs=json.dumps(group)),
                cwd=PROJECT_ROOT,
            )
        except RuntimeError as e:
            log(f"  Agent error: {e}; skipping")
            continue

        result = _extract_json(response)
        if not isinstance(result, dict) or not result.get("canonical"):
            log("  LLM: not the same org, skipping")
            continue

        canonical = _safe_slug(result["canonical"])
        if not canonical:
            log("  LLM returned empty canonical, skipping")
            continue

        canonical_dir = os.path.join(REQUESTS_DIR, canonical)
        variants = [s for s in group if s != canonical]
        log(f"  Merging {variants} -> {canonical}")

        if not dry_run:
            os.makedirs(canonical_dir, exist_ok=True)
            for variant in variants:
                variant_dir = os.path.join(REQUESTS_DIR, variant)
                for req_name in os.listdir(variant_dir):
                    src = os.path.join(variant_dir, req_name)
                    if not os.path.isdir(src):
                        continue
                    dest_name = _unique_dest(canonical_dir, req_name)
                    shutil.move(src, os.path.join(canonical_dir, dest_name))
                try:
                    os.rmdir(variant_dir)
                except OSError:
                    log(f"  Warning: could not remove {variant_dir} (not empty)")
                merged += 1

    log(f"Phase 1 done: merged {merged} variant org(s)")
    return merged


# ---------------------------------------------------------------------------
# Phase 2: Unknown folder
# ---------------------------------------------------------------------------

def phase_unknown(dry_run: bool = False) -> int:
    unknown_dir = os.path.join(REQUESTS_DIR, "unknown")
    if not os.path.isdir(unknown_dir):
        log("Phase 2: no unknown/ folder, skipping.")
        return 0

    log("Phase 2: resolving unknown/ requests...")
    existing_orgs = [d for d in _list_org_dirs() if d != "unknown"]
    moved = 0

    for req_name in sorted(os.listdir(unknown_dir)):
        req_dir = os.path.join(unknown_dir, req_name)
        if not os.path.isdir(req_dir):
            continue

        mds = [f for f in os.listdir(req_dir) if f.endswith(".md")]
        if not mds:
            log(f"  {req_name}: no .md file, skipping")
            continue

        md_path = os.path.join(req_dir, mds[0])
        with open(md_path) as f:
            text = f.read()
        meta, body = parse_frontmatter(text)

        summary = {
            "organization": meta.get("organization", ""),
            "subject": meta.get("subject", meta.get("summary", "")),
            "body_excerpt": body[:500],
        }

        try:
            response = cursor_agent_run(
                _load_prompt(
                    "match-unknown-org",
                    request=json.dumps(summary, ensure_ascii=False),
                    existing_orgs=json.dumps(existing_orgs),
                ),
                cwd=PROJECT_ROOT,
            )
        except RuntimeError as e:
            log(f"  {req_name}: agent error — {e}; skipping")
            continue

        result = _extract_json(response)
        if not isinstance(result, dict) or not result.get("org_slug"):
            log(f"  {req_name}: no org suggested, skipping")
            continue

        org_slug = _safe_slug(result["org_slug"])
        if not org_slug:
            log(f"  {req_name}: invalid org slug returned, skipping")
            continue

        dest_org_dir = os.path.join(REQUESTS_DIR, org_slug)
        dest_name = _unique_dest(dest_org_dir, req_name)
        log(f"  unknown/{req_name} -> {org_slug}/{dest_name}")

        if not dry_run:
            os.makedirs(dest_org_dir, exist_ok=True)
            shutil.move(req_dir, os.path.join(dest_org_dir, dest_name))
            moved += 1

    if not dry_run:
        try:
            os.rmdir(unknown_dir)
        except OSError:
            pass

    log(f"Phase 2 done: moved {moved} request(s) out of unknown/")
    return moved


# ---------------------------------------------------------------------------
# Phase 3: Per-org request dedup
# ---------------------------------------------------------------------------

def _merge_group(group_files: list[str], dry_run: bool = False) -> str | None:
    """Merge duplicate .md files into one. Returns new md path, or None on dry-run."""
    documents_block = ""
    for path in group_files:
        with open(path) as f:
            documents_block += f"=== {path} ===\n{f.read()}\n\n"

    try:
        merged = cursor_agent_run(
            _load_prompt("merge-duplicates", documents=documents_block),
            cwd=PROJECT_ROOT,
        )
    except RuntimeError as e:
        log(f"    Merge agent error: {e}; skipping group")
        return None

    text = merged.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])

    meta, body = parse_frontmatter(text)
    org_slug = make_slug("", meta.get("organization", ""), include_date=False)
    if not org_slug or org_slug == "unknown":
        for src in group_files:
            candidate = src.replace("\\", "/").split("/")[1]
            if candidate and candidate != "unknown":
                org_slug = candidate
                break

    req = {"organization": meta.get("organization", org_slug), "summary": body[:500]}
    try:
        slug = generate_request_filename(req)
    except Exception:
        slug = make_slug("", req["organization"], include_date=False)

    dest_org_dir = os.path.join(REQUESTS_DIR, org_slug)
    dest_slug = _unique_dest(dest_org_dir, slug)
    dest_dir = os.path.join(dest_org_dir, dest_slug)
    dest_md = os.path.join(dest_dir, f"{dest_slug}.md")

    if dry_run:
        log(f"    [dry-run] Would write {dest_md}")
        return dest_md

    os.makedirs(dest_dir, exist_ok=True)
    with open(dest_md, "w") as f:
        f.write(text)

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
                log(f"    Skipping duplicate attachment: {fname}")
                continue
            dst_name = _unique_dest(dest_dir, fname)
            shutil.copy2(src, os.path.join(dest_dir, dst_name))
            existing_hashes.add(h)

    for path in group_files:
        shutil.rmtree(os.path.dirname(path))

    return dest_md


def phase_requests(dry_run: bool = False) -> int:
    log("Phase 3: per-org request dedup...")
    total_merged = 0

    for org_slug in _list_org_dirs():
        org_dir = os.path.join(REQUESTS_DIR, org_slug)
        req_mds = _list_request_mds(org_dir)

        if len(req_mds) < 2:
            continue

        log(f"  {org_slug}: {len(req_mds)} request(s)")

        # Pass A: group by shared attachment hash (union-find)
        uf = _UnionFind(req_mds)
        hash_to_paths: dict[str, list[str]] = {}
        for md_path in req_mds:
            for h in _attachment_hashes(md_path):
                hash_to_paths.setdefault(h, []).append(md_path)
        for paths in hash_to_paths.values():
            for p in paths[1:]:
                uf.union(paths[0], p)
        hash_groups = [g for g in uf.groups() if len(g) >= 2]
        already_grouped = {p for g in hash_groups for p in g}

        # Pass B: LLM semantic dedup on remaining ungrouped files
        remaining = [p for p in req_mds if p not in already_grouped]
        llm_groups: list[list[str]] = []
        if len(remaining) >= 2:
            summaries = []
            for md_path in remaining:
                with open(md_path) as f:
                    raw = f.read()
                meta, body = parse_frontmatter(raw)
                summaries.append({
                    "file": md_path,
                    "organization": meta.get("organization", ""),
                    "date_received": meta.get("date_received", ""),
                    "subject": meta.get("subject", meta.get("summary", "")),
                    "attachment_hashes": _attachment_hashes(md_path),
                    "body": body[:1500],
                })
            try:
                response = cursor_agent_run(
                    _load_prompt(
                        "detect-duplicates-within-org",
                        requests=json.dumps(summaries, indent=2, ensure_ascii=False),
                    ),
                    cwd=PROJECT_ROOT,
                )
                groups = _extract_json(response)
                if isinstance(groups, list):
                    llm_groups = [g for g in groups if isinstance(g, list) and len(g) >= 2]
            except RuntimeError as e:
                log(f"  {org_slug}: LLM error — {e}")

        all_groups = hash_groups + llm_groups
        if not all_groups:
            log(f"  {org_slug}: no duplicates found")
            continue

        log(f"  {org_slug}: {len(all_groups)} group(s)")
        for group in all_groups:
            valid = [p for p in group if os.path.exists(p)]
            if len(valid) < 2:
                continue
            log(f"    Merging: {[os.path.basename(os.path.dirname(p)) for p in valid]}")
            _merge_group(valid, dry_run)
            total_merged += len(valid) - 1

    log(f"Phase 3 done: removed {total_merged} duplicate(s)")
    return total_merged


# ---------------------------------------------------------------------------
# Phase 4: PR
# ---------------------------------------------------------------------------

def _create_pr(dry_run: bool = False) -> str | None:
    if dry_run:
        log("[dry-run] Would create PR")
        return None

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    short_hash = hashlib.sha256(today.encode()).hexdigest()[:6]
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
        return None

    git("commit", "-m", f"dedup: org merge + request dedup ({today})")
    git("push", "-u", "origin", branch)

    pr_url = None
    try:
        pr_url = gh_pr_create(
            f"dedup: org merge + request dedup ({today})",
            f"## Dedup run — {today}\n\nPhases: org merge, unknown resolution, per-org request dedup.\n\n"
            f"🤖 Generated with requests-buddy dedup workflow",
        )
        log(f"Created PR: {pr_url}")
    finally:
        git("checkout", "main")

    return pr_url


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Deduplicate requests/")
    parser.add_argument(
        "--phase", choices=["orgs", "unknown", "requests", "pr"],
        help="Run a single phase only (default: all)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.phase in (None, "orgs"):
        phase_orgs(args.dry_run)

    if args.phase in (None, "unknown"):
        phase_unknown(args.dry_run)

    if args.phase in (None, "requests"):
        phase_requests(args.dry_run)

    if args.phase in (None, "pr"):
        _create_pr(args.dry_run)

    log("Done.")


if __name__ == "__main__":
    main()
