# Requests Buddy

## Why
This pipleine consolidates NGO fund requests and index them, allowing philantropic funds and donors to find requests easily using a NotebookLM interface.

## What is it
Automated pipeline that ingests email requests from Gmail, normalizes them with AI, deduplicates, and syncs everything to Google NotebookLM — orchestrated by GitHub Actions.

## Pipeline Overview

```
Gmail inbox
    │
    ▼ (every hour)
┌─────────────────────────────────────────────┐
│  Process 1: Ingest Emails                   │
│  raw_emails/<timestamp>/<slug>/email.md            │
│  branch: ingest/<timestamp>                        │
└───────────────────┬─────────────────────────┘
                    │ push to ingest/* triggers
                    ▼
┌─────────────────────────────────────────────┐
│  Process 2: Normalize Requests              │
│  requests/<org-slug>/<req-slug>/<req>.md    │
│  PR created → auto-merged to main           │
└───────────────────┬─────────────────────────┘
                    │ push to main triggers
                    ▼
┌─────────────────────────────────────────────┐
│  Process 3: Sync NotebookLM                 │
│  adds/removes sources in your notebook      │
└─────────────────────────────────────────────┘

    (daily at 06:00 UTC, runs independently)
┌─────────────────────────────────────────────┐
│  Process 4: Deduplicate                     │
│  merges similar orgs & requests             │
│  branch: dedup/<date> → opens PR for review │
└─────────────────────────────────────────────┘
```

### Steps at a Glance

| Process | Trigger | Reads from | Writes to | Merge |
|---|---|---|---|---|
| 1 — Ingest Emails | Every hour (cron) | Gmail inbox | `raw_emails/<timestamp>/` + branch `ingest/<timestamp>` | — |
| 2 — Normalize Requests | Push to `ingest/*` | `raw_emails/<timestamp>/` | `requests/<org>/<req>/` | Auto-merges to `main` |
| 3 — Sync NotebookLM | Push to `main` (touching `requests/**`) | `requests/` | NotebookLM notebook + `logs/` | — |
| 4 — Deduplicate | Daily 06:00 UTC (cron) | `requests/` | `requests/` (merged files) + branch `dedup/<date>` | Manual review PR |

All processes can also be triggered manually via `workflow_dispatch` in the GitHub Actions UI.

---

## Process 1: Ingest Emails

### Scope

Fetches all unread Gmail messages matching the inbox query, converts each to a structured Markdown file with YAML frontmatter, downloads any attachments (PDFs, images), and pushes the batch to a new git branch to trigger normalization.

### How It Works

1. **Authentication** — Uses `@googleworkspace/cli` (`gws`) with OAuth credentials stored in `.secrets/gws-credentials.json` (or the path in `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE`).
2. **Fetch messages** — Queries unread inbox messages. Each message is fetched in full (headers + body + attachments).
3. **Build raw folder** — For each message, a timestamped folder is created at `raw_emails/<YYYYMMDD-HHMMSS>/<slug>/`. Inside:
   - `email.md` — raw email body with YAML frontmatter (`message_id`, `from`, `to`, `subject`, `date`, `labels`, `thread_id`)
   - Any attachments (PDFs, images) saved as-is
4. **Label as processed** — The message is labeled `processed` in Gmail so it won't be fetched again.
5. **Push to git** — All new files are committed to a branch named `ingest/<YYYYMMDD-HHMMSS>` and pushed. This push triggers Process 2 automatically via GitHub Actions.

### Accessing Results

- **Raw files**: `raw_emails/<timestamp>/` — each subfolder is one email
- **Git branches**: `ingest/*` branches contain the raw ingest batches before normalization
- **GitHub Actions**: [Ingest Emails workflow](../../actions/workflows/ingest-emails.yml) — runs every hour, also manually triggerable

---

## Process 2: Normalize Requests

### Scope

Takes each raw email folder from Process 1 and converts it into a structured, AI-normalized request document. Extracts organization details, contact info, request type, funding ask, sector classification, and synthesizes a clean context summary. Creates a PR and auto-merges it to `main`.

### How It Works

1. **Trigger** — Fires automatically when a push to an `ingest/*` branch touches `raw_emails/**`. Can also be triggered manually with a `run_folder` input.
2. **AI normalization** — For each raw email folder, the Cursor agent CLI is invoked with `prompts/normalize-request.md` as the system prompt. The agent reads `email.md` and all attachments, then outputs a JSON object with fields: `org`, `org_slug`, `contact_name`, `contact_email`, `contact_phone`, `website`, `forwarded_by`, `request_type`, `urgency`, `sectors`, `target_population`, `geography`, `language`, `summary`, `funding_requested`, `funding_breakdown`, `context`, `attachments`.
3. **Filename generation** — A second Cursor agent call with `prompts/generate-filename.md` produces a descriptive slug for the request (e.g., `trauma-center-rehabilitation-grant-2026`).
4. **Write normalized file** — The structured data is rendered into `requests/<org-slug>/<request-slug>/<request-slug>.md` with:
   - YAML frontmatter: `id`, `source_email_id`, `date_received`, `status`
   - Quick Reference table (org, contact, email, phone, website)
   - Classification table (request type, urgency, sector, target population, geography, language)
   - The Ask section (summary, funding amount, breakdown)
   - Context & Background (2–4 paragraph synthesis)
   - Attachments list with descriptions
   - Extracted Data (full PDF/image content per attachment)
5. **Error recovery** — The script handles LLM failures: truncated JSON is repaired, repeated output patterns are detected and trimmed, and a `--fallback` mode captures raw agent output if parsing fails entirely.
6. **PR creation** — Once all folders in the run are normalized, a PR is created from the `ingest/<timestamp>` branch into `main` and auto-merged (squash).

### Accessing Results

- **Normalized requests**: `requests/<org-slug>/<request-slug>/<request-slug>.md`
- **GitHub Actions**: [Normalize Requests workflow](../../actions/workflows/normalize-requests.yml)
- **PRs**: each ingest run creates one auto-merged PR visible in the closed PRs list

---

## Process 3: Sync NotebookLM

### Scope

Keeps a Google NotebookLM notebook in sync with the entire `requests/` folder. Adds new sources, removes stale ones, and updates a metadata document with the last-sync timestamp. Runs automatically on every push to `main` that touches `requests/**`.

### How It Works

1. **Trigger** — Fires on push to `main` when `requests/**` files change. Also manually triggerable.
2. **Credential restore** — The NotebookLM browser session state is stored as a base64-encoded secret (`NOTEBOOKLM_CREDENTIALS`) and restored to `~/.notebooklm/storage_state.json` before running.
3. **Discover syncable files** — Recursively walks `requests/` and collects all `.md` files and PDF attachments.
4. **Load manifest** — `logs/notebooklm-sources.json` maps each file path to its NotebookLM source ID, tracking what's already synced.
5. **Delta sync**:
   - **New files** — uploaded as sources to the notebook via [`notebooklm-py`](https://github.com/drengskapur/notebooklm-py)
   - **Stale files** — files whose content has changed since last sync are removed and re-uploaded
   - **Orphan sources** — sources in NotebookLM that no longer have a corresponding file are removed
6. **Metadata source** — A special source is updated with the current sync timestamp and a summary of the sync operation.
7. **Commit manifest** — The updated `logs/notebooklm-sources.json` and `logs/notebooklm-sync.log` are committed back to `main`.

### Accessing Results

- **NotebookLM notebook**: open your notebook — all `requests/` documents should appear as sources
- **Sync manifest**: `logs/notebooklm-sources.json` — maps file paths to source IDs
- **Sync history**: `logs/notebooklm-sync.log` — timestamped log of every sync run
- **GitHub Actions**: [Sync NotebookLM workflow](../../actions/workflows/sync-notebooklm.yml)

---

## Process 4: Deduplicate

### Scope

Scans the entire `requests/` repository for duplicate or near-duplicate organizations and requests, merges them, and opens a PR for human review. Runs daily at 06:00 UTC. Unlike the other processes, this PR is **not** auto-merged — a human reviews the proposed changes before merging.

### How It Works

The workflow is split into four sequential GitHub Actions jobs, each running one phase:

**Phase 1 — Merge similar org folders** (`--phase orgs`)
- Loads all org-level folder names from `requests/`
- Uses the Cursor agent with `prompts/detect-similar-orgs.md` to identify orgs that are likely the same entity (e.g., different spellings, abbreviations)
- For confirmed matches, all files from the secondary org are moved into the canonical org folder
- Uses a union-find data structure to handle transitive merges (A=B, B=C → all merged into one)

**Phase 2 — Resolve unknown requests** (`--phase unknown`)
- Looks at `requests/unknown/` — requests where the org couldn't be identified during normalization
- Uses the Cursor agent with `prompts/match-unknown-org.md` to match each unknown request to a known org
- Confirmed matches are moved to the appropriate org folder

**Phase 3 — Deduplicate requests within each org** (`--phase requests`)
- For each org folder, groups requests by attachment hash (identical PDFs → likely duplicates)
- For each group and for the remaining requests, uses the Cursor agent with `prompts/detect-duplicates-within-org.md` to identify semantic duplicates
- Confirmed duplicates are merged using `prompts/merge-duplicates.md`, combining information from both requests into a single canonical document

**Phase 4 — Open pull request** (`--phase pr`)
- All changes from phases 1–3 are committed to a `dedup/<YYYY-MM-DD>-<hash>` branch
- A PR is opened against `main` with a summary of all merges performed
- The PR is **not** auto-merged — a human reviews it

### Accessing Results

- **Dedup PR**: open PRs list — look for PRs from `dedup/*` branches
- **Merged org structure**: after merging, `requests/` is cleaner with canonical org folders
- **GitHub Actions**: [Deduplicate workflow](../../actions/workflows/deduplicate.yml) — runs daily, also manually triggerable

---

## How to Start

### Prerequisites

- **Cursor account** with an API key — used for AI normalization and deduplication
- **Google Cloud project** with the Gmail API enabled
- **Google NotebookLM** account with an existing notebook

### 1. Install CLI tools

**uv** (Python package manager — installs Python 3.12+ automatically):

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**GitHub CLI** (`gh`):

```bash
# macOS
brew install gh

# Linux (Debian/Ubuntu)
sudo apt install gh

# Windows
winget install --id GitHub.cli
```

Authenticate after installing:

```bash
gh auth login
```

**Google Workspace CLI** (`gws`) — for Gmail access:

```bash
# Requires Node.js 18+. Install Node first if needed: https://nodejs.org/
npm install -g @googleworkspace/cli
```

**Cursor Agent CLI** (`agent`) — for AI normalization and deduplication:

```bash
curl -fsSL https://cursor.com/install | bash
```

After installing, verify with:

```bash
agent --version
```

**Python dependencies** (includes `notebooklm-py` for NotebookLM automation):

```bash
uv sync
```

[`notebooklm-py`](https://github.com/drengskapur/notebooklm-py) is a Python client and CLI for NotebookLM. It's used by this project to add and remove sources, and can also be used directly to query the notebook — for example, to pull a summary of all ingested requests into a CSV:

```bash
# Generate a data table from the notebook (no install needed — uv fetches on the fly)
uv run --with=notebooklm-py notebooklm generate data-table "summarize all requests in the notebook"

# Download the generated table as a CSV
uv run --with=notebooklm-py notebooklm download data-table ./data.csv

# Open the CSV in visidata for quick exploration
uv run --with=visidata vd data.csv
```

### 2. Clone and run first-time setup

```bash
git clone https://github.com/raziele/requests-buddy.git
cd requests-buddy
./scripts/setup.sh
```

The setup script walks you through:

- **Gmail OAuth** (opens browser) — Use the **Gmail scope only**: `gws auth login -s gmail`. That scope covers reading mail, attachments, and modifying/creating labels; no Drive/Calendar/Sheets. Using `-s gmail` also helps stay under the ~25-scope limit for unverified (testing) apps.
- **NotebookLM login** (opens browser)
- Entering your Cursor API key and NotebookLM notebook ID
- Uploading all credentials as GitHub Actions secrets
- Creating the Gmail "processed" label

### 3. Verify

Go to the [Actions tab](../../actions) and trigger a manual run of the **Ingest Emails** workflow. Check the logs to confirm headless operation works.

### 4. Done

All four processes now run unattended. Email ingestion runs every hour and automatically triggers normalization. Deduplication runs daily at 06:00 UTC and opens PRs for human review. NotebookLM sync triggers whenever normalized requests are merged to `main`.

---

## Running Scripts Locally

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

# Process 4: deduplicate (all phases)
uv run python scripts/deduplicate.py

# Process 4: run a single phase
uv run python scripts/deduplicate.py --phase orgs
uv run python scripts/deduplicate.py --phase unknown
uv run python scripts/deduplicate.py --phase requests
uv run python scripts/deduplicate.py --phase pr

# Test normalize on a specific folder (no PR, verbose output)
uv run python scripts/test_normalize.py raw_emails/20260307-120000/some-folder
```

Scripts load `.env` from the repo root. Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
# Edit .env with your values
```

To reset the project (clean NotebookLM sources, delete all requests, open a reset PR):

```bash
uv run python scripts/reset.py
```

For one-off tools (e.g. linters): `uvx ruff check .`

---

## Configuration

All secrets are environment variables. For local development, set them in `.env`. For GitHub Actions, they are stored as repository secrets (uploaded by `scripts/upload-secrets.sh`).

| Variable | Required | Description |
|---|---|---|
| `CURSOR_API_KEY` | Yes | Cursor agent CLI API key — used by Processes 2 and 4 for AI normalization and deduplication |
| `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` | Yes | Path to Gmail OAuth credentials JSON file (generated by `gws auth login`) |
| `NOTEBOOKLM_NOTEBOOK_ID` | Yes | ID of your NotebookLM notebook (visible in the notebook URL) |
| `REPO_ACCESS_TOKEN` | Recommended | GitHub Personal Access Token with `repo` scope — required for Process 1 pushes to trigger Process 2 (see Troubleshooting below) |

GitHub Actions secrets also include:
- `GWS_CREDENTIALS` — full JSON content of the Gmail credentials file
- `NOTEBOOKLM_CREDENTIALS` — base64-encoded NotebookLM browser session state

---

## Project Structure

```
.github/workflows/
  ingest-emails.yml         # Process 1: hourly cron — emails → raw_emails/
  normalize-requests.yml    # Process 2: triggered by ingest/* push — raw_emails/ → requests/ → PR
  sync-notebooklm.yml       # Process 3: on push to main → requests/ → NotebookLM
  deduplicate.yml           # Process 4: daily cron — detect & merge duplicates → open PR
scripts/
  ingest_emails.py          # Process 1: fetch Gmail → raw_emails/<timestamp>/<slug>/
  normalize_requests.py     # Process 2: normalize raw emails → requests/
  sync_notebooklm.py        # Process 3: NotebookLM sync
  deduplicate.py            # Process 4: AI deduplication (--phase orgs|unknown|requests|pr)
  utils.py                  # Shared helpers (git, logging, CLI wrappers)
  setup.sh                  # Interactive first-time setup
  upload-secrets.sh         # Upload local credentials to GitHub secrets
  reset.py                  # Reset: clean notebook + delete all requests
prompts/
  normalize-request.md      # AI instructions for normalizing a raw email into structured data
  generate-filename.md      # AI instructions for generating a descriptive request slug
  detect-similar-orgs.md    # AI instructions for finding similar org folder names
  detect-duplicates-within-org.md  # AI instructions for finding duplicate requests
  match-unknown-org.md      # AI instructions for matching unknown/ requests to real orgs
  merge-duplicates.md       # AI instructions for merging two duplicate requests
raw_emails/                 # Raw ingested emails (timestamped run folders, one per ingest run)
requests/                   # Normalized request Markdown files (organized by org)
logs/
  notebooklm-sources.json   # Manifest mapping file paths to NotebookLM source IDs
  notebooklm-sync.log       # Timestamped sync history
```

---

## Updating Credentials

If you need to rotate a secret (e.g., new Cursor API key or refreshed NotebookLM session), update the file in `.secrets/` and re-run:

```bash
./scripts/upload-secrets.sh
```
