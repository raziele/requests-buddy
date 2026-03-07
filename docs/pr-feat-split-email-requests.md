# PR: feat/split-email-requests

## Title

**Normalize: OpenRouter + PDF attachments, prompts refresh, setup & config**

---

## Description

This branch extends the request-normalization pipeline with OpenRouter API support, PDF attachment handling, and clearer prompts. It also adds optional Langfuse observability and updates opencode/NotebookLM config.

### Summary of changes

- **Normalize pipeline**
  - Call OpenRouter chat API directly for normalization (with `OPENROUTER_API_KEY`), sending email + PDFs as multimodal content.
  - Optional **Gemini** path: when `GEMINI_API_KEY` is set, use opencode CLI with Gemini first; fall back to OpenRouter on failure.
  - Copy raw-email attachments into each request’s output folder (e.g. `requests/2026-03-05-org/` next to `requests/2026-03-05-org.md`).
  - Output includes an **Extracted Data** section from PDF content when the model returns `extracted_data`.

- **Prompts**
  - **normalize-request**: Input described as email.md + optional attachment files; new `extracted_data` field for substantive PDF content (markdown, per-file subheadings).
  - **split-requests**: Reframed as “email content extractor”; explicit lists of what to extract vs remove (signatures, forwarding headers, quoted blocks, tracking URLs, etc.) before splitting into requests.

- **Setup & secrets**
  - Setup script extended to 8 steps with an optional **Langfuse** step (public + secret key in `.secrets/`).
  - `upload-secrets.sh` uploads `GEMINI_API_KEY`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY` to GitHub when present.

- **Config & tooling**
  - **opencode.json**: Multiple providers (OpenAI/OpenRouter, Google), optional Langfuse plugin, OpenTelemetry.
  - **NotebookLM sync**: Discovers all syncable files under `requests/` recursively (`.md`, `.pdf`, `.png`, etc.) so nested attachment folders are included.
  - New **test_split.py** for local testing of the split-requests flow.

- **Dependencies**
  - Added `python-dotenv`, `requests`, and `pymupdf` for env loading, HTTP calls, and PDF handling where needed.

### Commits (5)

1. `chore(deps): add python-dotenv, requests, pymupdf`
2. `docs(prompts): add PDF/extracted_data to normalize, refine split-requests extract instructions`
3. `feat(normalize): OpenRouter API, PDF attachments, Gemini fallback, copy attachments to request folder`
4. `chore(setup): add optional Langfuse step, upload GEMINI and Langfuse secrets to GitHub`
5. `chore(config): opencode providers (OpenAI/OpenRouter, Google), sync NotebookLM with nested attachments, add test_split.py`

### Not included in this PR

- **`.opencode/`** — left untracked (local/tooling).
- **`requests/Fri, 6 Mar-the-kibbutz-movement-rehabilitation-fund*`** — sample output; add in a separate commit if you want it in the repo.

### How to open the PR

```bash
git push origin feat/split-email-requests
```

Then on GitHub: **New pull request** from `feat/split-email-requests` → `main`, and paste the **Title** and **Description** above into the PR title and body.
