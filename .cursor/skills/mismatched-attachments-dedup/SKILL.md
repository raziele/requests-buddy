---
name: mismatched-attachments-dedup
description: Find and fix misplaced attachments in request folders. Uses filename-based org matching to flag files in wrong folders. Use when deduplicating requests, after bulk imports/syncs, or when the user mentions mismatched attachments, misplaced files, or attachment cleanup.
---

# Mismatched Attachments Dedup

Find files in request folders that don't relate to the request (e.g. batch-forwarded docs, multi-org bundles). Run after bulk imports or as part of dedup.

## Workflow

Run from project root. Script lives in the skill:

```bash
python3 .cursor/skills/mismatched-attachments-dedup/scripts/find_mismatched_attachments.py
```

Or use the wrapper: `python3 requests-buddy/scripts/find_mismatched_attachments.py`

1. **Scan** — Run script (no args)
2. **Review** — Check for false positives; add to EXCLUSIONS in script if needed
3. **Dry-run** — `--fix --dry-run` to preview deletions
4. **Fix** — `--fix` to delete
5. **Verify** — Re-run scan; expect 0 mismatches

## Pre-Run Checklist

- [ ] New orgs? Add patterns to `FILE_TO_ORG` (distinctive filename substrings, lowercase)
- [ ] New aliases? Add to `orgs_match()` when folder org name ≠ file org name
- [ ] Known false positives? Add to `EXCLUSIONS` as `(folder_pattern, filename_pattern)`

## Decision Framework

| Situation | Action |
|-----------|--------|
| Correct org folder exists and has the file | Delete from wrong folder |
| Correct org folder exists but lacks the file | Move manually (script doesn't support) |
| Correct org folder does not exist | Orphan — create folder or move to unknown/ |
| File is combined/multi-org | Exclude — add to EXCLUSIONS |
| Filename generic; content matches folder | Exclude — add to EXCLUSIONS |

## Common Patterns

- **Batch forward** — Same 6–7 files in many folders → delete extras
- **Multi-org bundle** — Same 4–5 PDFs in several folders → delete extras
- **Combined doc** — One PDF, multiple org sections → exclude

## Reference

For what to extend (FILE_TO_ORG, aliases, EXCLUSIONS) and detailed guidance, see [reference.md](reference.md) or [docs/learnings/mismatched-attachments-cleanup.md](../../docs/learnings/mismatched-attachments-cleanup.md).
