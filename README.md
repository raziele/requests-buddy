# Requests Buddy

Automated pipeline that ingests email requests from Gmail, deduplicates them using AI, and syncs everything to Google NotebookLM — orchestrated by GitHub Actions.

## What It Does

1. **Email Ingestion** (hourly) — Fetches unread Gmail messages, converts them to structured Markdown files under `requests/`, and commits them to `main`.
2. **Deduplication** (daily) — Compares new requests against existing ones using an LLM via OpenRouter. Merges duplicates into unified documents and opens a PR for review.
3. **NotebookLM Sync** (on push) — Keeps a NotebookLM notebook in sync with the `requests/` folder — adding new sources, removing stale ones, and updating a metadata source with the last sync timestamp.

## How to Start

### Prerequisites

- **Node.js 18+**
- **Python 3.12+**
- **GitHub CLI** (`gh`) — authenticated with `gh auth login`
- **Google Cloud project** with the Gmail API enabled
- **Google NotebookLM** account with an existing notebook
- **OpenRouter** API key — get one at [openrouter.ai](https://openrouter.ai)

### 1. Install CLI tools

```bash
npm install -g @googleworkspace/cli
pip install "notebooklm-py[browser]"
playwright install chromium
```

### 2. Clone and run first-time setup

```bash
git clone https://github.com/raziele/requests-buddy.git
cd requests-buddy
./scripts/setup.sh
```

The setup script walks you through:

- Gmail OAuth authentication (opens browser)
- NotebookLM login (opens browser)
- Entering your OpenRouter API key and NotebookLM notebook ID
- Uploading all credentials as GitHub Actions secrets
- Creating the Gmail "processed" label

### 3. Verify

Go to the [Actions tab](https://github.com/raziele/requests-buddy/actions) and trigger a manual run of the **Ingest Emails** workflow. Check the logs to confirm headless operation works.

### 4. Done

All three processes now run unattended on their schedules. Email ingestion runs every hour, deduplication runs daily at 06:00 UTC, and NotebookLM sync triggers whenever new files are pushed to `requests/`.

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
  utils.py                # Shared helpers
requests/                  # Ingested request Markdown files
logs/                      # NotebookLM sync logs and source manifest
```
