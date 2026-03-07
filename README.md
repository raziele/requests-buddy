# Requests Buddy

Automated pipeline that ingests email requests from Gmail, normalizes them with AI, deduplicates, and syncs everything to Google NotebookLM — orchestrated by GitHub Actions.

## What It Does

Four processes run as separate GitHub Actions workflows:

1. **Ingest Emails** (hourly) — Fetches unread Gmail messages and saves them as raw Markdown to `raw_emails/<timestamp>/`. Each run gets its own timestamped folder on a dedicated branch (`ingest/<ts>`), then triggers Process 2.
2. **Normalize Requests** (triggered by Process 1) — Runs the AI normalize agent on the raw emails from an ingest branch, writes structured request documents to `requests/`, creates a PR, and auto-merges it to `main`.
3. **Sync NotebookLM** (on push to main) — Keeps a NotebookLM notebook in sync with the `requests/` folder — adding new sources, removing stale ones, and updating a metadata source with the last sync timestamp.
4. **Deduplicate** (daily) — Scans requests for semantic duplicates using AI, merges them, and opens a PR for human review (not auto-merged).

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

All four processes now run unattended. Email ingestion runs every hour and automatically triggers normalization. Deduplication runs daily at 06:00 UTC and opens PRs for human review. NotebookLM sync triggers whenever normalized requests are merged to `main`.

## Running scripts locally

Use `uv run` so scripts use the project environment:

```bash
# Process 1: ingest emails (creates branch, triggers Process 2)
uv run python scripts/ingest_emails.py

# Process 2: normalize a specific ingest run (with PR + merge)
uv run python scripts/normalize_requests.py --run-folder 20260307-120000 --branch ingest/20260307-120000

# Process 2: normalize specific folders (no PR)
uv run python scripts/normalize_requests.py raw_emails/20260307-120000/some-folder

# Process 3: sync to NotebookLM
uv run python scripts/sync_notebooklm.py

# Process 4: deduplicate
uv run python scripts/deduplicate.py

# Test normalize on a folder
uv run python scripts/test_normalize.py raw_emails/20260307-120000/some-folder
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
  ingest-emails.yml         # Process 1: hourly cron — emails → raw_emails/
  normalize-requests.yml    # Process 2: dispatched by P1 — raw_emails/ → requests/ → merge
  sync-notebooklm.yml      # Process 3: on push to main — requests/ → NotebookLM
  deduplicate.yml           # Process 4: daily cron — detect & merge duplicates → open PR
scripts/
  setup.sh                  # Interactive first-time setup
  upload-secrets.sh          # Upload local credentials to GitHub secrets
  ingest_emails.py           # Process 1: fetch Gmail → raw_emails/<ts>/<slug>/
  normalize_requests.py      # Process 2: normalize raw emails → requests/
  deduplicate.py             # Process 4: AI deduplication
  sync_notebooklm.py         # Process 3: NotebookLM sync
  reset.py                   # Reset: clean notebook + delete requests
  utils.py                   # Shared helpers
raw_emails/                   # Raw ingested emails (timestamped run folders)
requests/                     # Normalized request Markdown files
logs/                         # NotebookLM sync logs and source manifest
```
