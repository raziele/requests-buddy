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

So: **explicit `apiKey` in config overrides `auth.json`** for that provider. Using `{env:GEMINI_API_KEY}` in config means the key is taken from the environment (or `.env`), not from `auth.json`, and any key for that provider in `auth.json` is ignored.

---

## How this project is set up

### Config: `opencode.json`

This project does **not** rely on `auth.json` for OpenCode. It uses **explicit provider config** with **env var references**:

```json
"provider": {
  "openrouter": {
    "options": {
      "apiKey": "{env:OPENROUTER_API_KEY}",
      "baseURL": "https://openrouter.ai/api/v1"
    }
  },
  "google": {
    "options": {
      "apiKey": "{env:GEMINI_API_KEY}"
    }
  }
}
```

So for both OpenRouter and Google (Gemini):

- The key is **always** taken from the environment (`OPENROUTER_API_KEY`, `GEMINI_API_KEY`).
- Anything in `~/.local/share/opencode/auth.json` for these providers is **not** used when running in this repo.

### Where the env vars get their values

| Context        | How keys are set |
|----------------|------------------|
| **Local**      | `.env` in repo root (from `scripts/..` load_dotenv). Values can be copied from `.secrets/` (see below) or set by hand. |
| **CI (GitHub)**| Workflow passes `OPENROUTER_API_KEY` and `GEMINI_API_KEY` from GitHub Actions secrets into the job env. No `auth.json` or `.secrets/` on the runner. |

### Local secrets: `.secrets/` (not used by OpenCode directly)

- **`scripts/setup.sh`** prompts for OpenRouter and Gemini keys and writes them to:
  - `.secrets/openrouter-api-key`
  - `.secrets/gemini-api-key`
- **`scripts/upload-secrets.sh`** reads those files and uploads their contents to GitHub secrets (`OPENROUTER_API_KEY`, `GEMINI_API_KEY`).
- OpenCode **never** reads `.secrets/` itself. To use those keys locally you must either:
  - Put them in **`.env`** (e.g. `GEMINI_API_KEY=$(cat .secrets/gemini-api-key)`), or
  - Run `opencode auth login` and add the same keys so they end up in **`~/.local/share/opencode/auth.json`** — but in this project the **config overrides** that file, so you still need `GEMINI_API_KEY` / `OPENROUTER_API_KEY` in the environment (e.g. via `.env`) for the project’s `opencode.json` to work.

So in practice, local dev uses **`.env`** (or exported vars); CI uses **GitHub secrets → job env**.

### Summary

| Question | Answer |
|----------|--------|
| Does this project use `auth.json`? | Only indirectly: OpenCode may load it, but **explicit `apiKey` in `opencode.json` overrides it** for OpenRouter and Google. We rely on env vars. |
| Where do keys live locally? | In `.env` (or env); optionally in `.secrets/` for upload to GitHub and for reference. |
| Where do keys live in CI? | In GitHub Actions secrets, passed as env vars to the workflow. |
| How to add/rotate keys? | Update `.secrets/` and run `upload-secrets.sh` for CI; ensure `.env` (or your shell) has the same values for local OpenCode runs. |
