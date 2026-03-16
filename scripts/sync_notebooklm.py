#!/usr/bin/env python3
"""Process 3: Sync requests/ folder with NotebookLM notebook sources.

Recursively discovers .md files and attachment files (PDFs, images) under
requests/ and uploads them as NotebookLM sources.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from utils import git_commit_and_push, log


REQUESTS_DIR = "requests"
MANIFEST_FILE = "logs/notebooklm-sources.json"
SYNC_LOG_FILE = "logs/notebooklm-sync.log"
MD_ONLY_EXTENSIONS = {".md"}
ALL_EXTENSIONS = {".md", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}


def get_notebook_id() -> str:
    nb_id = os.environ.get("NOTEBOOKLM_NOTEBOOK_ID")
    if not nb_id:
        raise RuntimeError("NOTEBOOKLM_NOTEBOOK_ID environment variable is not set")
    return nb_id


def notebooklm(*args: str) -> str:
    """Run a notebooklm CLI command and return stdout."""
    cmd = ["notebooklm", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        print(f"notebooklm command failed: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"notebooklm exited with code {result.returncode}")

    return result.stdout.strip()


def load_manifest() -> dict[str, str]:
    """Load the file->source_id manifest."""
    if not os.path.exists(MANIFEST_FILE):
        return {}
    with open(MANIFEST_FILE) as f:
        return json.load(f)


def save_manifest(manifest: dict[str, str]):
    os.makedirs(os.path.dirname(MANIFEST_FILE), exist_ok=True)
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2)


def append_sync_log(message: str):
    os.makedirs(os.path.dirname(SYNC_LOG_FILE), exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(SYNC_LOG_FILE, "a") as f:
        f.write(f"[{ts}] {message}\n")


def list_notebook_sources(notebook_id: str) -> dict[str, str]:
    """Return {source_id: title} for all sources currently in the notebook."""
    notebooklm("use", notebook_id)
    raw = notebooklm("source", "list", "--json")
    data = json.loads(raw) if raw else {}
    sources = data.get("sources", []) if isinstance(data, dict) else data
    return {s["id"]: s.get("title", "") for s in sources}


def add_source(notebook_id: str, filepath: str) -> str:
    """Add a file as a source to NotebookLM. Returns the source ID."""
    notebooklm("use", notebook_id)
    output = notebooklm("source", "add", filepath)

    # Try to extract source ID from output
    # The CLI typically prints something like "Added source: <id>"
    for line in output.splitlines():
        if "source" in line.lower() and any(c.isalnum() for c in line):
            parts = line.split()
            if parts:
                return parts[-1]

    return output.strip()


def remove_source(notebook_id: str, source_id: str):
    """Remove a source from NotebookLM by ID."""
    notebooklm("use", notebook_id)
    notebooklm("source", "delete", source_id, "--yes")


def update_metadata_source(notebook_id: str, manifest: dict[str, str]):
    """Update (or create) a metadata source with sync info."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    metadata_text = (
        f"# Requests Buddy — Sync Metadata\n\n"
        f"Last synced: {ts}\n"
        f"Active sources: {len(manifest)}\n\n"
        f"## Source Mapping\n\n"
    )
    for filepath, source_id in sorted(manifest.items()):
        metadata_text += f"- `{filepath}` -> `{source_id}`\n"

    metadata_file = "/tmp/requests-buddy-sync-metadata.md"
    with open(metadata_file, "w") as f:
        f.write(metadata_text)

    # Check if metadata source already exists in manifest
    meta_key = "__sync_metadata__"
    notebooklm("use", notebook_id)

    if meta_key in manifest:
        try:
            remove_source(notebook_id, manifest[meta_key])
        except RuntimeError:
            pass

    try:
        source_id = add_source(notebook_id, metadata_file)
        manifest[meta_key] = source_id
    except RuntimeError as e:
        log(f"Failed to update metadata source: {e}")


def discover_syncable_files(extensions: set[str]) -> set[str]:
    """Recursively walk requests/ and return paths with syncable extensions."""
    files: set[str] = set()
    for dirpath, _, filenames in os.walk(REQUESTS_DIR):
        for fname in filenames:
            if os.path.splitext(fname)[1].lower() in extensions:
                files.add(os.path.join(dirpath, fname))
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="Sync all supported file types (PDF, images) in addition to .md files.",
    )
    args = parser.parse_args()

    extensions = ALL_EXTENSIONS if args.all_files else MD_ONLY_EXTENSIONS

    notebook_id = get_notebook_id()
    manifest = load_manifest()

    # Fetch live source list so we can delete orphans not in the manifest
    log("Fetching current notebook sources...")
    live_sources = list_notebook_sources(notebook_id)
    log(f"  Notebook has {len(live_sources)} source(s).")

    repo_files = discover_syncable_files(extensions)
    manifest_files = {k for k in manifest if k != "__sync_metadata__"}

    new_files = repo_files - manifest_files

    # Stale = in manifest but not in requests/, OR in notebook but not in manifest
    stale_files = manifest_files - repo_files
    manifest_source_ids = {v for k, v in manifest.items() if k != "__sync_metadata__"}
    orphan_source_ids = set(live_sources.keys()) - manifest_source_ids - {manifest.get("__sync_metadata__")}

    if not new_files and not stale_files and not orphan_source_ids:
        log("No changes to sync.")
        return

    log(f"Sync: {len(new_files)} to add, {len(stale_files)} stale, {len(orphan_source_ids)} orphan(s) to remove.")

    added = []
    for filepath in sorted(new_files):
        log(f"  Adding source: {filepath}")
        try:
            source_id = add_source(notebook_id, filepath)
            manifest[filepath] = source_id
            added.append(filepath)
            append_sync_log(f"ADDED {filepath} -> {source_id}")
        except RuntimeError as e:
            log(f"  Failed to add {filepath}: {e}")
            append_sync_log(f"FAILED to add {filepath}: {e}")

    removed = []
    for filepath in sorted(stale_files):
        source_id = manifest.get(filepath)
        if not source_id:
            continue
        log(f"  Removing source: {filepath} ({source_id})")
        try:
            remove_source(notebook_id, source_id)
            removed.append(filepath)
            append_sync_log(f"REMOVED {filepath} (was {source_id})")
        except RuntimeError as e:
            log(f"  Failed to remove {filepath}: {e}")
            append_sync_log(f"FAILED to remove {filepath}: {e}")
        del manifest[filepath]

    for source_id in sorted(orphan_source_ids):
        title = live_sources.get(source_id, "")
        log(f"  Removing orphan source: {source_id} ({title!r})")
        try:
            remove_source(notebook_id, source_id)
            removed.append(source_id)
            append_sync_log(f"REMOVED orphan {source_id} ({title!r})")
        except RuntimeError as e:
            log(f"  Failed to remove orphan {source_id}: {e}")
            append_sync_log(f"FAILED to remove orphan {source_id}: {e}")

    update_metadata_source(notebook_id, manifest)
    save_manifest(manifest)

    changed_files = [MANIFEST_FILE, SYNC_LOG_FILE]
    summary_parts = []
    if added:
        summary_parts.append(f"+{len(added)} source(s)")
    if removed:
        summary_parts.append(f"-{len(removed)} source(s)")
    summary = ", ".join(summary_parts)

    git_commit_and_push(changed_files, f"sync: notebooklm {summary}")
    log("Sync complete.")


if __name__ == "__main__":
    main()
