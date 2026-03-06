# Requests Buddy

See the [workspace rules](.cursor/rules/agents.mdc) for architecture, conventions, and external CLI details.

## Cursor Cloud specific instructions

### Services overview

| Process | Script | External dependency |
|---|---|---|
| Ingest | `scripts/ingest_emails.py` | `gws` CLI + Gmail OAuth credentials |
| Dedup | `scripts/deduplicate.py` | `opencode` CLI + `OPENROUTER_API_KEY` |
| Sync | `scripts/sync_notebooklm.py` | `notebooklm` CLI + `NOTEBOOKLM_NOTEBOOK_ID` + Playwright browser state |

All three require credentials that must be provided via environment secrets (see below).

### Running scripts

Always use `uv run` to execute scripts so they use the project venv:

```bash
uv run python scripts/ingest_emails.py
uv run python scripts/deduplicate.py
uv run python scripts/sync_notebooklm.py
```

### Linting

```bash
uvx ruff check .
```

There are 2 pre-existing lint warnings (unused import in `deduplicate.py`, unnecessary f-string in `ingest_emails.py`).

### Gotchas

- The `opencode` npm package name is `opencode-ai` (not `@opencode-ai/cli` or `@opencode/cli`). Install with `npm install -g opencode-ai@latest`.
- `opencode` performs a one-time SQLite migration on first run; this is normal and takes a few seconds.
- The `gws` CLI requires prior OAuth authentication (`gws auth login -s gmail`) before any Gmail operations work. Without credentials, the ingest script fails at the first `gws` call.
- `sync_notebooklm.py` requires the `NOTEBOOKLM_NOTEBOOK_ID` environment variable to be set.
- Playwright + Chromium must be installed for the NotebookLM sync process: `uv run playwright install --with-deps chromium`.
- `python-frontmatter` is in `pyproject.toml` but is unused; frontmatter is handled manually via PyYAML in `utils.py`.

### Required secrets (for full end-to-end runs)

| Secret | Used by |
|---|---|
| `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` | Ingest (path to exported Gmail OAuth JSON) |
| `OPENROUTER_API_KEY` | Dedup (LLM calls via OpenRouter) |
| `NOTEBOOKLM_NOTEBOOK_ID` | Sync (target notebook ID) |
| `NOTEBOOKLM_CREDENTIALS` | Sync (base64-encoded Playwright browser state) |
