#!/usr/bin/env python3
"""Reset script: clean NotebookLM sources, delete request files, and open a PR."""

import os
import subprocess
import sys
from datetime import datetime, timezone
from glob import glob

sys.path.insert(0, os.path.dirname(__file__))

from sync_notebooklm import (
    load_manifest,
    save_manifest,
    MANIFEST_FILE,
    SYNC_LOG_FILE,
)
from utils import git, git_commit_and_push, gh_pr_create, log

REQUESTS_DIR = "requests"
SECRETS_DIR = ".secrets"
NOTEBOOKLM_TIMEOUT = 60


def get_notebook_id() -> str:
    """Resolve notebook ID from env var or local secrets file."""
    nb_id = os.environ.get("NOTEBOOKLM_NOTEBOOK_ID")
    if nb_id:
        return nb_id

    secrets_path = os.path.join(SECRETS_DIR, "notebooklm-notebook-id")
    if os.path.exists(secrets_path):
        with open(secrets_path) as f:
            nb_id = f.read().strip()
        if nb_id:
            return nb_id

    raise RuntimeError(
        "NOTEBOOKLM_NOTEBOOK_ID not set and .secrets/notebooklm-notebook-id not found"
    )


def notebooklm_delete(notebook_id: str, source_id: str):
    """Delete a single source, passing --yes to skip the confirmation prompt."""
    cmd = ["notebooklm", "source", "delete", source_id, "-n", notebook_id, "--yes"]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=NOTEBOOKLM_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"notebooklm source delete failed (exit {result.returncode}): {result.stderr.strip()}"
        )


def remove_all_sources(notebook_id: str, manifest: dict[str, str]) -> tuple[int, int]:
    """Remove every source in the manifest from the notebook.

    Returns (success_count, failure_count).
    """
    succeeded, failed = 0, 0
    for key, source_id in sorted(manifest.items()):
        label = key if key != "__sync_metadata__" else "sync-metadata"
        log(f"  Deleting source: {label} ({source_id})")
        try:
            notebooklm_delete(notebook_id, source_id)
            succeeded += 1
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            log(f"  Failed to delete {label}: {e}")
            failed += 1

    return succeeded, failed


def delete_request_files() -> list[str]:
    """Delete all markdown files under requests/. Returns deleted paths."""
    files = sorted(glob(os.path.join(REQUESTS_DIR, "*.md")))
    for path in files:
        os.remove(path)
        log(f"  Deleted {path}")
    return files


def main():
    log("Starting reset...")

    notebook_id = get_notebook_id()
    manifest = load_manifest()

    # --- 1. Create cleaning branch ---
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    branch = f"reset/{today}"
    git("checkout", "-b", branch)
    log(f"Created branch {branch}")

    # --- 2. Remove all NotebookLM sources ---
    if manifest:
        log(f"Removing {len(manifest)} source(s) from NotebookLM...")
        ok, fail = remove_all_sources(notebook_id, manifest)
        log(f"NotebookLM cleanup done: {ok} removed, {fail} failed.")
    else:
        log("Manifest is empty — nothing to remove from NotebookLM.")

    # --- 3. Delete request files ---
    deleted = delete_request_files()
    if deleted:
        log(f"Deleted {len(deleted)} request file(s).")
    else:
        log("No request files to delete.")

    # --- 4. Reset manifest and sync log ---
    save_manifest({})
    if os.path.exists(SYNC_LOG_FILE):
        open(SYNC_LOG_FILE, "w").close()

    # --- 5. Commit, push, and open PR ---
    paths_to_stage = [MANIFEST_FILE, SYNC_LOG_FILE] + deleted
    for p in paths_to_stage:
        git("add", p)

    git("commit", "-m", f"reset: remove all sources and request files ({today})")
    git("push", "-u", "origin", branch)

    pr_url = gh_pr_create(
        title=f"Reset — clean all sources and requests ({today})",
        body=(
            "## What this does\n\n"
            "- Removed **all** sources from the NotebookLM notebook\n"
            f"- Deleted **{len(deleted)}** request file(s) from `requests/`\n"
            "- Cleared the source manifest and sync log\n"
        ),
    )
    log(f"PR created: {pr_url}")

    git("checkout", "main")
    log("Reset complete.")


if __name__ == "__main__":
    main()
