# Mismatched Attachments — Reference

**Script:** `requests-buddy/scripts/find_mismatched_attachments.py`

## What to Extend

### FILE_TO_ORG

When new orgs appear, add:

```python
"org-slug": ["distinctive substring 1", "hebrew if relevant", "program name"],
```

- Lowercase substrings that appear in filenames
- Prefer distinctive terms (avoid "emergency", "request", "2026")
- First match wins — put more specific orgs before generic ones

### orgs_match() aliases

When folder org name ≠ file org name but they're the same org:

```python
"folder org name from md": ["file org name", "abbreviation"],
```

### EXCLUSIONS

When the script flags a file that is correctly placed:

```python
("folder-path-substring", "filename-substring"),  # comment: why excluded
```

Both patterns must match (substring, case-insensitive).

## Script Location

**Canonical:** `.cursor/skills/mismatched-attachments-dedup/scripts/find_mismatched_attachments.py`

**Wrapper:** `requests-buddy/scripts/find_mismatched_attachments.py` — delegates to the skill script.

## Full Learning Doc

See [docs/learnings/mismatched-attachments-cleanup.md](../../docs/learnings/mismatched-attachments-cleanup.md) for the complete guide.
