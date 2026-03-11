#!/usr/bin/env bash
# For every local branch that starts with ingest/:
#   1. Rebase onto origin/main
#   2. Trigger the "Normalize Requests" workflow via gh workflow run
#
# Usage: run from repo root (or scripts/). Requires gh CLI and a clean working tree is recommended.

set -e

date

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Require gh
if ! command -v gh &>/dev/null; then
  echo "error: gh CLI not found" >&2
  exit 1
fi

git fetch origin

ORIGINAL_BRANCH="$(git branch --show-current)"
INGEST_BRANCHES="$(git branch --list 'ingest/*' --format='%(refname:short)')"

if [ -z "$INGEST_BRANCHES" ]; then
  echo "No local branches matching ingest/*"
  exit 0
fi

FAILED_REBASE=()
FAILED_TRIGGER=()
SKIPPED_CHECKOUT=()

while IFS= read -r branch; do
  [ -z "$branch" ] && continue
  run_folder="${branch#ingest/}"
  echo "--- $branch (run_folder=$run_folder) ---"

  git checkout "$branch" || { echo "  skip: checkout failed" >&2; SKIPPED_CHECKOUT+=("$branch"); continue; }

  if ! git rebase origin/main; then
    echo "  rebase failed; aborting rebase and skipping workflow trigger" >&2
    git rebase --abort
    FAILED_REBASE+=("$branch")
    continue
  fi

  # Ensure workflow file matches current main (replayed commits may have an old version)
  git checkout origin/main -- .github/workflows/normalize-requests.yml
  if ! git diff --staged --quiet; then
    git commit -m "chore: align normalize workflow with main"
  fi

  # Push so --ref exists on remote for gh workflow run
  if ! git push --force-with-lease origin "$branch"; then
    echo "  push failed; skipping workflow trigger" >&2
    FAILED_TRIGGER+=("$branch (push failed)")
    continue
  fi

  if ! gh workflow run "Normalize Requests" --ref "$branch" -f "run_folder=$run_folder"; then
    echo "  workflow trigger failed" >&2
    FAILED_TRIGGER+=("$branch")
  else
    echo "  triggered Normalize Requests for $branch"
  fi
done <<< "$INGEST_BRANCHES"

# Restore original branch if we're not on it (e.g. we switched away)
CURRENT="$(git branch --show-current)"
if [ -n "$ORIGINAL_BRANCH" ] && [ "$CURRENT" != "$ORIGINAL_BRANCH" ]; then
  git checkout "$ORIGINAL_BRANCH"
fi

# Summary
if [ ${#SKIPPED_CHECKOUT[@]} -gt 0 ]; then
  echo ""
  echo "Branches skipped (checkout failed, e.g. untracked files in the way):"
  printf '  %s\n' "${SKIPPED_CHECKOUT[@]}"
fi
if [ ${#FAILED_REBASE[@]} -gt 0 ]; then
  echo "" >&2
  echo "Branches with rebase conflicts (workflow not triggered):" >&2
  printf '  %s\n' "${FAILED_REBASE[@]}" >&2
fi
if [ ${#FAILED_TRIGGER[@]} -gt 0 ]; then
  echo "" >&2
  echo "Branches where workflow trigger (or push) failed:" >&2
  printf '  %s\n' "${FAILED_TRIGGER[@]}" >&2
fi

[ ${#FAILED_REBASE[@]} -eq 0 ] && [ ${#FAILED_TRIGGER[@]} -eq 0 ] && exit 0 || exit 1
