# Spec: Improve Analyze Workflow — Cursor Agent Migration

**Date:** 2026-03-11
**Source:** `docs/improve-analyze-workflow.md`
**Scope:** Replace opencode/OpenRouter with Cursor agent CLI, externalize prompts, improve output naming.

---

## Summary

The normalize-requests pipeline currently uses a 2-step process:
1. `openrouter_extract_pdf()` — calls OpenRouter API directly to extract text from PDFs
2. `opencode_run()` — calls the opencode CLI to normalize email + extracted text into structured JSON

Cursor agent can parse PDFs natively, so this simplifies to a single step. This spec also covers externalizing inline prompts, generating descriptive filenames via a second agent call, and improving output naming.

---

## Tasks

### 1. Create branch `improve/analyze-workflow`

```bash
git checkout -b improve/analyze-workflow
```

### 2. Generate descriptive filenames via a second agent call

**File:** `scripts/normalize_requests.py` — `process_folder()` (lines ~398–411)

**Current behavior:**
Output is always named `request.md` inside an org-named folder:
```
requests/YYYY-MM-DD/<org-slug>/request.md
```

**New behavior:**
After normalization produces the structured JSON (with `summary`, `organization`, `request_type`), a second lightweight Cursor agent call generates a short, descriptive filename. The output path becomes:
```
requests/YYYY-MM-DD/<generated-name>/<generated-name>.md
```
For example: `requests/2026-03-11/zaka-emergency-rescue-equipment/zaka-emergency-rescue-equipment.md`

**New prompt file:** `prompts/generate-filename.md`
```markdown
Generate a short, descriptive filename (no extension) for a request document.

Rules:
- Use lowercase kebab-case (e.g. `zaka-emergency-rescue-equipment`)
- Include the organization name and a brief descriptor of the request
- Maximum 80 characters
- Use only [a-z0-9-] characters
- Return ONLY the filename, nothing else — no explanation, no quotes, no extension

Input: the summary and organization name of the request.
```

**New function:** `generate_request_filename()` in `normalize_requests.py`
```python
def generate_request_filename(req: dict) -> str:
    """Generate a descriptive filename for a request via Cursor agent.

    Uses the request summary + organization as input.
    Falls back to org slug if the agent call fails.
    """
    org = req.get("organization", "unknown")
    summary = req.get("summary", "")

    prompt_path = os.path.join(SCRIPT_DIR, "..", "prompts", "generate-filename.md")
    with open(prompt_path) as f:
        system_prompt = f.read().strip()

    message = f"{system_prompt}\n\nOrganization: {org}\nSummary: {summary}"

    try:
        raw = cursor_agent_run(message, cwd=PROJECT_ROOT)
        name = raw.strip().strip('"').strip("'").strip("`")
        # Validate: only allow [a-z0-9-], max 80 chars
        name = re.sub(r"[^a-z0-9-]", "", name.lower())[:80].strip("-")
        if name:
            return name
    except Exception as e:
        log(f"  Filename generation failed, using org slug: {e}")

    return make_slug("", org, include_date=False)
```

**Changes to `process_folder()`:**
```python
# Replace:
out_slug = make_slug("", org, include_date=False)
out_path = os.path.join(out_dir, "request.md")

# With:
out_slug = generate_request_filename(req)
out_dir = os.path.join(REQUESTS_DIR, today, out_slug)
out_path = os.path.join(out_dir, f"{out_slug}.md")
```

### 3. Externalize inline prompts to `.md` files

**Current state:**
- `normalize_requests.py:231-234` has an inline prompt: `"Normalize the attached email..."`
- `utils.py:154` has an inline PDF extraction system prompt
- `prompts/normalize-request.md` and `prompts/opencode-normalize.md` exist but are **not loaded by code**

**Changes:**
- Consolidate into a single `prompts/normalize-request.md` (already exists, keep as-is — it's comprehensive)
- Delete `prompts/opencode-normalize.md` (duplicate of `normalize-request.md`)
- Delete `prompts/extract-pdf.md` if created (no longer needed — Cursor agent handles PDFs)
- Update `normalize_email()` to read the prompt from `prompts/normalize-request.md` and pass it to the agent

### 4. Replace `opencode_run()` with `cursor_agent_run()` in `utils.py`

**File:** `scripts/utils.py`

**Delete:**
- `OPENROUTER_API_URL` constant (line 69)
- `_openrouter_model()` (lines 72–77)
- `openrouter_chat()` (lines 80–138)
- `openrouter_extract_pdf()` (lines 141–169)
- `opencode_run()` (lines 177–230)

**Add `cursor_agent_run()`:**
```python
def cursor_agent_run(
    prompt: str,
    *,
    files: list[str] | None = None,
    cwd: str | None = None,
) -> str:
    """Run Cursor agent CLI and return its text output.

    Files are referenced inside the prompt as relative paths
    (Cursor agent can read and parse PDFs natively).

    Requires CURSOR_API_KEY env var.
    """
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("CURSOR_API_KEY not set")

    cmd = [
        "agent",
        "--api-key", api_key,
        "--model", "auto",
        "--output-format", "text",
        "--trust",
        "--force",
        "-p",
        prompt,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cursor agent failed (exit {result.returncode}):\n{result.stderr}"
        )
    out = (result.stdout or "").strip()
    if not out:
        raise RuntimeError("cursor agent produced no output")
    return out
```

**Key flags** (per `docs/improve-analyze-workflow.md`):
| Flag | Purpose |
|------|---------|
| `--api-key $CURSOR_API_KEY` | Authentication |
| `-p` | Print mode — output to console |
| `--model auto` | Auto model selection |
| `--output-format text` | Plain text output |
| `--trust` | Trust project configuration |
| `--force` | Yolo mode — auto-approve all actions |

**Constraint:** File paths in the prompt MUST be relative to the project directory.

### 5. Simplify `normalize_email()` — single Cursor agent call

**File:** `scripts/normalize_requests.py`

**Delete:**
- `_extract_pdfs()` function (lines 181–206)
- PDF extraction step in `normalize_email()` (lines 228–239)

**New `normalize_email()`:**
```python
def normalize_email(folder: str) -> list[dict]:
    """Normalize raw email folder via Cursor agent (single step).

    Cursor agent reads email.md + PDF attachments natively.
    """
    fallback = [{"_fallback": True}]

    email_path = os.path.join(folder, "email.md")
    if not os.path.exists(email_path):
        log(f"  No email.md in {folder}")
        return fallback

    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        log("  CURSOR_API_KEY not set; skipping")
        return fallback

    # Load prompt from prompts/normalize-request.md
    prompt_path = os.path.join(SCRIPT_DIR, "..", "prompts", "normalize-request.md")
    with open(prompt_path) as f:
        system_prompt = f.read().strip()

    # Build file list as relative paths (required by Cursor agent)
    file_paths = _folder_file_paths(folder)
    rel_paths = [os.path.relpath(p, PROJECT_ROOT) for p in file_paths]

    # Compose the prompt: system prompt + file references
    file_refs = "\n".join(f"- ./{p}" for p in rel_paths)
    message = f"{system_prompt}\n\n## Files to analyze\n\n{file_refs}"

    try:
        log(f"  Running Cursor agent on {len(rel_paths)} file(s)...")
        raw = cursor_agent_run(message, cwd=PROJECT_ROOT)
        parsed = _parse_normalize_response(raw)
        if parsed:
            log(f"  Normalized into {len(parsed)} request(s)")
            return parsed
        log(f"  Cursor agent returned but parse failed (raw length {len(raw)})")
    except Exception as e:
        log(f"  Cursor agent failed: {e}")

    return fallback
```

### 6. Add Cursor agent installation to setup

**File:** `scripts/setup.sh` (or create if missing)

Add:
```bash
# Install Cursor agent CLI
if ! command -v agent &>/dev/null; then
  echo "Installing Cursor agent CLI..."
  curl https://cursor.com/install -fsS | bash
fi
```

Also update `.env.example` (if it exists) to include `CURSOR_API_KEY` and remove `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` if they're no longer needed by other scripts.

### 7. Commit, push, and open PR

```bash
git add scripts/utils.py scripts/normalize_requests.py prompts/ docs/
git commit -m "improve: migrate to Cursor agent CLI, externalize prompts, NGO-named output files"
git push -u origin improve/analyze-workflow
gh pr create \
  --title "improve: Cursor agent migration + prompt externalization" \
  --body "## Summary
- Replace opencode + OpenRouter direct calls with Cursor agent CLI
- Remove PDF extraction step (Cursor agent handles PDFs natively)
- Externalize inline prompts to prompts/*.md files
- Generate descriptive filenames via a second agent call (org + summary → kebab-case name)

## Source
docs/improve-analyze-workflow.md"
```

---

## Files changed

| File | Action |
|------|--------|
| `scripts/utils.py` | Remove `openrouter_*`, `opencode_run`; add `cursor_agent_run` |
| `scripts/normalize_requests.py` | Remove `_extract_pdfs`; simplify `normalize_email`; add `generate_request_filename()` |
| `prompts/normalize-request.md` | Keep (already comprehensive) |
| `prompts/generate-filename.md` | **New** — prompt for generating descriptive kebab-case filenames from org + summary |
| `prompts/opencode-normalize.md` | Delete (duplicate) |
| `scripts/setup.sh` | Add Cursor agent install step |
| `.env` / `.env.example` | Add `CURSOR_API_KEY`; optionally remove `OPENROUTER_*` if unused elsewhere |

## Environment variables

| Old | New | Notes |
|-----|-----|-------|
| `OPENROUTER_API_KEY` | `CURSOR_API_KEY` | Check if other scripts still need OpenRouter |
| `OPENROUTER_MODEL` | _(removed)_ | Cursor agent uses `--model auto` |
