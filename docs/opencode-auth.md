# OpenCode auth and how this project uses it

## How OpenCode handles auth (opencode.ai docs)

### Credentials file: `auth.json`

- **Location:** `~/.local/share/opencode/auth.json` (macOS/Linux); `%USERPROFILE%\.local\share\opencode\auth.json` (Windows).
- **Contents:** API keys and OAuth tokens for providers, stored when you add them via:
  - **TUI:** `/connect` command
  - **CLI:** `opencode auth login`
- OpenCode loads this file at startup. If you also have keys in **environment variables** or a **project `.env`**, those are loaded too.

Docs: [CLI → auth](https://opencode.ai/docs/cli#auth), [Providers → Credentials](https://opencode.ai/docs/providers/#credentials).

### Where the API key comes from (precedence)

For each provider, OpenCode resolves the key in this order:

1. **Explicit config** — If `opencode.json` (or another config source) sets `provider.<id>.options.apiKey`, that value is used. Stored auth for that provider is **skipped** (OpenCode may log: “skipping stored auth for provider with explicit config”).
2. **Stored credentials** — If there is no explicit `apiKey` for that provider, OpenCode uses the key from `~/.local/share/opencode/auth.json` (from `/connect` or `opencode auth login`).
3. **Environment / `.env`** — Keys can also come from env vars or a project `.env`; the config can reference them with `{env:VAR_NAME}` (see [Config → Variables](https://opencode.ai/docs/config#env-vars)).

So: **explicit `apiKey` in config overrides `auth.json`** for that provider. Using `{env:GOOGLE_GENERATIVE_AI_API_KEY}` in config means the key is taken from the environment (or `.env`), not from `auth.json`, and any key for that provider in `auth.json` is ignored.

---

## How this project is set up

### Config: `opencode.json`

This project uses **Gemini only** (no OpenRouter). OpenCode gets the key from the environment:

- **Env vars:** `GOOGLE_GENERATIVE_AI_API_KEY` (scripts and workflows use this).
- Anything in `~/.local/share/opencode/auth.json` for the Google provider may be used by OpenCode if the project config doesn’t override it; in practice we rely on env vars for CI and local `.env`.

### Where the env vars get their values

| Context        | How keys are set |
|----------------|------------------|
| **Local**      | `.env` in repo root (from `scripts/..` load_dotenv). Values can be copied from `.secrets/` or set by hand. |
| **CI (GitHub)**| Workflows pass `GOOGLE_GENERATIVE_AI_API_KEY` from GitHub Actions secrets into the job env. No `auth.json` or `.secrets/` on the runner. |

### Local secrets: `.secrets/` (not used by OpenCode directly)

- **`scripts/setup.sh`** prompts for a Gemini key and writes it to `.secrets/` (e.g. `google-generative-ai-api-key` or `gemini-api-key`).
- **`scripts/upload-secrets.sh`** uploads contents to GitHub secrets (e.g. `GOOGLE_GENERATIVE_AI_API_KEY`).
- OpenCode **never** reads `.secrets/` itself. Put the key in **`.env`** (e.g. `GOOGLE_GENERATIVE_AI_API_KEY=...`) for local runs.

So in practice, local dev uses **`.env`** (or exported vars); CI uses **GitHub secrets → job env**.

### Summary

| Question | Answer |
|----------|--------|
| Does this project use `auth.json`? | Only if OpenCode falls back to it; we rely on env vars. |
| Where do keys live locally? | In `.env` (or env); optionally in `.secrets/` for upload to GitHub. |
| Where do keys live in CI? | In GitHub Actions secrets, passed as env vars to the workflow. |
| How to add/rotate keys? | Update `.secrets/` and run `upload-secrets.sh` for CI; ensure `.env` has the same value for local OpenCode runs. |
