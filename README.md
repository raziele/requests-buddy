# Requests Buddy

Automated pipeline that ingests email requests from Gmail, deduplicates them using AI, and syncs everything to Google NotebookLM — orchestrated by GitHub Actions.

## What It Does

1. **Email Ingestion** (hourly) — Fetches unread Gmail messages, converts them to structured Markdown files under `requests/`, and commits them to `main`.
2. **Deduplication** (daily) — Compares new requests against existing ones using an LLM via OpenRouter. Merges duplicates into unified documents and opens a PR for review.
3. **NotebookLM Sync** (on push) — Keeps a NotebookLM notebook in sync with the `requests/` folder — adding new sources, removing stale ones, and updating a metadata source with the last sync timestamp.

## How to Start

### Prerequisites

- **Node.js 18+**
- **uv** — [install](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.12+ is installed automatically)
- **GitHub CLI** (`gh`) — authenticated with `gh auth login`
- **Google Cloud project** with the Gmail API enabled
- **Google NotebookLM** account with an existing notebook
- **OpenRouter** API key — get one at [openrouter.ai](https://openrouter.ai)

### 1. Install CLI tools

```bash
npm install -g @googleworkspace/cli
uv sync
uv run playwright install chromium
```

### 2. Clone and run first-time setup

```bash
git clone https://github.com/raziele/requests-buddy.git
cd requests-buddy
./scripts/setup.sh
```

The setup script walks you through:

- **Gmail OAuth** (opens browser) — Use the **Gmail scope only**: `gws auth login -s gmail`. That scope covers reading mail, attachments, and modifying/creating labels; no Drive/Calendar/Sheets. Using `-s gmail` also helps stay under the ~25-scope limit for unverified (testing) apps.
- NotebookLM login (opens browser)
- Entering your OpenRouter API key and NotebookLM notebook ID
- Uploading all credentials as GitHub Actions secrets
- Creating the Gmail "processed" label

### 3. Verify

Go to the [Actions tab](https://github.com/raziele/requests-buddy/actions) and trigger a manual run of the **Ingest Emails** workflow. Check the logs to confirm headless operation works.

### 4. Done

All three processes now run unattended on their schedules. Email ingestion runs every hour, deduplication runs daily at 06:00 UTC, and NotebookLM sync triggers whenever new files are pushed to `requests/`.

## Running scripts locally

Use `uv run` so scripts use the project environment:

```bash
uv run python scripts/ingest_emails.py
uv run python scripts/deduplicate.py
uv run python scripts/sync_notebooklm.py
uv run python scripts/test_normalize.py raw_emails/some-folder
```

Scripts load `.env` from the repo root; set `GOOGLE_GENERATIVE_AI_API_KEY` (or `GEMINI_API_KEY`) there for opencode normalize.

To reset the project (clean NotebookLM sources, delete all requests, open a PR):

```bash
uv run python scripts/reset.py
```

For one-off tools (e.g. linters), use `uvx`: `uvx ruff check .`

## Troubleshooting

### "Error 403: access_denied" or "app is being tested, only developer-approved testers"

Your GCP OAuth app is in **Testing** mode. Only accounts listed as **Test users** can sign in.

**Fix:** In [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services** → **OAuth consent screen** → **Test users** → **Add users** → add the Gmail address you use for `gws auth login`. Then try again.

### "Google hasn't verified this app"

Expected when the app is in testing mode. Click **Advanced** → **Go to &lt;app&gt; (unsafe)** to continue. Safe for personal use.

## Updating Credentials

If you need to rotate a secret (e.g., new OpenRouter API key), update the file in `.secrets/` and re-run:

```bash
./scripts/upload-secrets.sh
```

## Project Structure

```
.github/workflows/
  ingest-emails.yml       # Hourly cron
  deduplicate.yml         # Daily cron
  sync-notebooklm.yml     # Trigger on push to requests/
scripts/
  setup.sh                # Interactive first-time setup
  upload-secrets.sh       # Upload local credentials to GitHub secrets
  ingest_emails.py        # Process 1: email ingestion
  deduplicate.py          # Process 2: AI deduplication
  sync_notebooklm.py      # Process 3: NotebookLM sync
  reset.py                # Reset: clean notebook + delete requests
  utils.py                # Shared helpers
requests/                  # Ingested request Markdown files
logs/                      # NotebookLM sync logs and source manifest
```
