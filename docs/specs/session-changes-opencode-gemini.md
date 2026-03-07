# Session summary: OpenCode + Gemini for normalize

Summary of changes made to get opencode using Gemini for the normalize step and to fix CI.

## Root cause

- **CI:** `opencode.json` referenced `{file:.secrets/openrouter-api-key}` (and gemini), which don’t exist on the runner → config error.
- **Local / provider:** OpenCode was using OpenRouter as the default provider instead of Google. Removing the OpenRouter provider from `opencode.json` fixed that so the default is Google (Gemini).

---

## 1. `opencode.json`

- **API keys:** Switched from file refs to env vars so CI can use GitHub Secrets:
  - Was: `"apiKey": "{file:.secrets/openrouter-api-key}"` and `{file:.secrets/gemini-api-key}`.
  - Now: no `apiKey` in config; OpenCode uses `GOOGLE_GENERATIVE_AI_API_KEY` from the environment (see [opencode.ai](https://opencode.ai/docs/cli#auth)).
- **Default model:** Set to `google/gemini-2.5-flash-lite` (was `arcee-ai/trinity-mini:free`).
- **Default agent:** Set to `normalize`.
- **OpenRouter removed:** OpenRouter provider was removed so the default provider is Google. Normalize and any other opencode usage now use Gemini when the key is set.

(No `apiKey` under `provider.google`; key comes only from env.)

---

## 2. `scripts/utils.py`

- **Load `.env` in `opencode_run`:** At the start of `opencode_run`, call `load_dotenv(repo_root/.env)` so env vars are in `os.environ` before building the subprocess env (fixes “uv run” not loading `.env` for the opencode child).
- **Optional `dotenv`:** `from dotenv import load_dotenv` in a try/except; no-op if not installed.
- **Stderr as output:** If opencode exits 0 but stdout is empty, use stderr as the command output (known opencode behavior).
- **Clearer “no output” error:** Include a short stderr snippet in the error when both stdout and stderr are empty.
- **Debug log:** Print whether `GOOGLE_GENERATIVE_AI_API_KEY` is set in the subprocess env and its length (no key value). Can be removed once things are stable.

---

## 3. `scripts/normalize_requests.py`

- **Key source:** Use `GOOGLE_GENERATIVE_AI_API_KEY` or, if unset, `GEMINI_API_KEY` for the Google key.
- **Export before opencode:** Set `os.environ["GOOGLE_GENERATIVE_AI_API_KEY"] = google_key` before calling `opencode_run` so the subprocess sees it.
- **Parse-failure logging:** When opencode returns something that doesn’t parse as JSON, log “opencode returned but parse failed (raw length …), falling back to OpenRouter”. If the raw string is short and contains `"Error:"`, also log that string (e.g. API key errors).

---

## 4. `.github/workflows/ingest-emails.yml`

- **Env for Run ingestion step:** Set `GOOGLE_GENERATIVE_AI_API_KEY: ${{ secrets.GEMINI_API_KEY }}` (no `GEMINI_API_KEY`). OpenCode reads `GOOGLE_GENERATIVE_AI_API_KEY`; secret name stays `GEMINI_API_KEY` in GitHub.

---

## 5. `.env.example`

- **New:** `GOOGLE_GENERATIVE_AI_API_KEY=...` with a short comment that opencode reads this.

---

## 6. `README.md`

- **Running locally:** Instructions use `uv run python scripts/...` and note that scripts load `.env` from the repo root; set `GOOGLE_GENERATIVE_AI_API_KEY` (or `GEMINI_API_KEY`) there for opencode normalize.

---

## 7. `docs/opencode-auth.md`

- **New:** Short doc on how OpenCode auth works (`auth.json`, env, config precedence) and how this project uses env vars and no OpenRouter in config.

---

## 8. Reverted / not kept

- **Bash wrapper `test_normalize.sh`:** Added then removed; fix was to load `.env` in Python before running opencode.
- **`apiKey` in `opencode.json` for Google:** Tried `{env:GOOGLE_GENERATIVE_AI_API_KEY}`; reverted because “it doesn’t work” and you wanted to rely on export/env only.
- **`OPENCODE_CONFIG_CONTENT` / `enabled_providers: ["google"]`:** Tried to force Google-only for the run; reverted; didn’t fix subprocess behavior.

---

## Deduplicate workflow note

- **`deduplicate.yml`** still sets only `OPENROUTER_API_KEY` for the run step. OpenRouter is no longer in `opencode.json`, so opencode there will use the default (Google). To avoid failures in CI, the deduplicate job should also get the Gemini key, e.g. add `GOOGLE_GENERATIVE_AI_API_KEY: ${{ secrets.GEMINI_API_KEY }}` to the “Run deduplication” step env (same as ingest).
