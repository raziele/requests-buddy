#!/usr/bin/env python3
"""Process 2: Normalize raw emails into structured request documents.

Two-step pipeline, both via OpenRouter:
  1. Extract text from PDF attachments (arcee-ai/trinity-large-preview:free + pdf-text plugin)
  2. Normalize email + extracted text into structured JSON (opencode + arcee-ai)

When invoked with --run-folder, operates on a specific ingest run,
commits results, creates a PR, and auto-merges it to main.

Usage:
    uv run python scripts/normalize_requests.py --run-folder 20260307-120000
    uv run python scripts/normalize_requests.py raw_emails/<ts>/<slug>   # specific folder
    uv run python scripts/normalize_requests.py                          # all pending
"""

import argparse
import json
import mimetypes
import os
import re
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from utils import (
    gh_pr_create,
    gh_pr_merge,
    git_commit_and_push,
    log,
    make_slug,
    opencode_run,
    openrouter_extract_pdf,
    parse_frontmatter,
    render_frontmatter,
)

RAW_DIR = "raw_emails"
REQUESTS_DIR = "requests"
SCRIPT_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))


def _folder_file_paths(folder: str) -> list[str]:
    """Return ordered list of file paths in folder (email.md first, then rest)."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    paths = [
        os.path.join(folder, f)
        for f in names
        if os.path.isfile(os.path.join(folder, f))
    ]
    paths.sort(key=lambda p: (0 if p.endswith("email.md") else 1, p))
    return paths


def _parse_normalize_response(raw: str) -> list[dict] | None:
    """Parse opencode JSON response into requests list or None."""
    raw = raw.strip()
    json_str = raw
    if "```" in raw:
        match = re.search(r"```(?:json)?\s*\n(.*?)\n?```", raw, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
    if not json_str.strip().startswith("{"):
        idx = json_str.find("{")
        if idx >= 0:
            json_str = json_str[idx:]
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None
    requests = data.get("requests")
    if not isinstance(requests, list) or not requests:
        return None
    for req in requests:
        if not isinstance(req, dict) or "summary" not in req:
            return None
    return requests


def _extract_pdfs(folder: str) -> dict[str, str]:
    """Step 1: Extract text from all PDFs in folder via OpenRouter pdf-text plugin.

    Returns {filename: extracted_text} for each successfully extracted PDF.
    """
    extracts: dict[str, str] = {}
    for fname in sorted(os.listdir(folder)):
        if fname == "email.md":
            continue
        fpath = os.path.join(folder, fname)
        if not os.path.isfile(fpath):
            continue
        mime, _ = mimetypes.guess_type(fname)
        if mime != "application/pdf":
            continue
        try:
            log(f"  Extracting PDF: {fname} (OpenRouter pdf-text)...")
            text = openrouter_extract_pdf(fpath)
            if text:
                extracts[fname] = text
                log(f"  Extracted {len(text)} chars from {fname}")
            else:
                log(f"  Empty extraction for {fname}")
        except Exception as e:
            log(f"  PDF extraction failed for {fname}: {e}")
    return extracts


def normalize_email(folder: str) -> list[dict]:
    """Normalize raw email folder via two-step OpenRouter pipeline.

    Step 1: Extract text from PDFs using arcee-ai/trinity-large-preview:free + pdf-text plugin.
    Step 2: Normalize email + extracted text using opencode + arcee-ai/trinity-mini.
    """
    fallback = [{"_fallback": True}]

    email_path = os.path.join(folder, "email.md")
    if not os.path.exists(email_path):
        log(f"  No email.md in {folder}")
        return fallback

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        log("  OPENROUTER_API_KEY not set; skipping")
        return fallback

    # Step 1: extract PDF text via OpenRouter
    pdf_extracts = _extract_pdfs(folder)

    # Step 2: build message with email + extracted PDF text, send to opencode
    parts = ["Normalize the attached email"]
    if pdf_extracts:
        parts.append(f" and {len(pdf_extracts)} PDF attachment(s)")
    parts.append(". Return only the JSON.")

    if pdf_extracts:
        parts.append("\n\n## Extracted PDF Contents\n")
        for fname, text in pdf_extracts.items():
            parts.append(f"### {fname}\n\n{text}\n")

    message = "".join(parts)

    try:
        log("  Running opencode (arcee-ai via OpenRouter)...")
        raw = opencode_run(
            message,
            files=[email_path],
            agent="normalize",
            cwd=PROJECT_ROOT,
        )
        parsed = _parse_normalize_response(raw)
        if parsed:
            log(f"  Normalized into {len(parsed)} request(s)")
            return parsed
        log(f"  opencode returned but parse failed (raw length {len(raw)})")
        if len(raw) < 500 and "Error:" in raw:
            log(f"  opencode message: {raw.strip()}")
    except Exception as e:
        log(f"  opencode failed: {e}")

    return fallback


def build_normalized_markdown(req: dict, headers: dict, seq: int) -> str:
    """Render a normalized request dict into the standard template."""

    org = req.get("organization") or headers.get("subject", "Unknown")
    summary = req.get("summary") or ""
    req_id = f"REQ-{headers.get('date', '')[:10]}-{seq:03d}"

    meta = {
        "id": req_id,
        "source_email_id": headers.get("id", ""),
        "date_received": headers.get("date", ""),
        "status": "new",
    }

    def val(key: str, default: str = "—") -> str:
        v = req.get(key)
        if v is None or v == "null" or v == "":
            return default
        return str(v)

    lines = [
        f"# {org} — {val('request_type', 'Request').replace('_', ' ').title()}",
        "",
        "## Quick Reference",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| **Organization** | {org} |",
        f"| **Contact** | {val('contact_name')} — {val('contact_role')} |",
        f"| **Contact Email** | {val('contact_email')} |",
        f"| **Contact Phone** | {val('contact_phone')} |",
        f"| **Website** | {val('website')} |",
        f"| **Original Date** | {val('original_date', headers.get('date', '—'))} |",
        f"| **Forwarded By** | {headers.get('from', '—')} |",
        "",
        "## Classification",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| **Request Type** | {val('request_type')} |",
        f"| **Urgency** | {val('urgency')} |",
        f"| **Sector** | {val('sector')} |",
        f"| **Target Population** | {val('target_population')} |",
        f"| **Geographic Focus** | {val('geographic_focus')} |",
        f"| **Language** | {val('language')} |",
        "",
        "## The Ask",
        "",
        summary,
        "",
        f"**Funding Requested:** {val('funding_requested', 'Not specified')}",
    ]

    breakdown = req.get("funding_breakdown")
    if isinstance(breakdown, list) and breakdown:
        lines.append("")
        lines.append("**Funding Breakdown:**")
        for item in breakdown:
            if isinstance(item, dict):
                lines.append(f"- {item.get('item', '?')}: {item.get('amount', '?')}")

    nfa = req.get("non_financial_ask")
    if nfa and nfa != "null":
        lines.append("")
        lines.append(f"**Non-Financial Ask:** {nfa}")

    lines.extend([
        "",
        "## Context & Background",
        "",
        val("context", summary),
        "",
        "## Attachments",
        "",
    ])

    att_list = req.get("attachments")
    if isinstance(att_list, list) and att_list:
        lines.append("| Filename | Description |")
        lines.append("|---|---|")
        for att in att_list:
            if isinstance(att, dict):
                lines.append(f"| {att.get('filename', '?')} | {att.get('description', '—')} |")
    else:
        lines.append("(none)")

    extracted = req.get("extracted_data")
    if extracted and str(extracted).strip() and extracted != "null":
        lines.extend([
            "",
            "## Extracted Data",
            "",
            str(extracted).strip(),
            "",
        ])

    lines.extend([
        "",
        "## Internal Notes",
        "",
        "_To be filled by reviewer._",
    ])

    return render_frontmatter(meta, "\n".join(lines))


def process_folder(folder: str) -> list[str]:
    """Normalize a single raw_emails/<slug>/ folder.

    Output structure: requests/YYYY-MM-DD/<slug>/request.md + attachments.
    Returns list of created file paths.
    """
    email_path = os.path.join(folder, "email.md")
    if not os.path.exists(email_path):
        log(f"  Skipping {folder}: no email.md found")
        return []

    with open(email_path) as f:
        text = f.read()

    headers, _ = parse_frontmatter(text)
    raw_slug = os.path.basename(folder)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    log(f"Normalizing {folder}...")
    normalized = normalize_email(folder)

    created = []
    for i, req in enumerate(normalized):
        if req.get("_fallback"):
            log(f"  Skipping fallback result for {folder}")
            continue

        org = req.get("organization") or headers.get("subject", raw_slug)
        out_slug = make_slug("", org, include_date=False)

        out_dir = os.path.join(REQUESTS_DIR, today, out_slug)
        if os.path.exists(out_dir):
            out_dir = os.path.join(REQUESTS_DIR, today, f"{out_slug}-{i+1}")

        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "request.md")

        md = build_normalized_markdown(req, headers, seq=i + 1)
        with open(out_path, "w") as f:
            f.write(md)
        log(f"  Wrote {out_path}")
        created.append(out_path)

        created.extend(_copy_attachments(folder, out_dir))

    return created


def _copy_attachments(raw_folder: str, dest_dir: str) -> list[str]:
    """Copy non-email.md files from raw_folder into dest_dir."""
    copied = []
    for fname in sorted(os.listdir(raw_folder)):
        if fname == "email.md":
            continue
        src = os.path.join(raw_folder, fname)
        if not os.path.isfile(src):
            continue
        os.makedirs(dest_dir, exist_ok=True)
        dst = os.path.join(dest_dir, fname)
        shutil.copy2(src, dst)
        copied.append(dst)
        log(f"  Copied attachment: {dst}")
    return copied


def find_folders_in_run(run_folder: str) -> list[str]:
    """Return all email folders inside a specific run directory (raw_emails/<ts>/)."""
    run_dir = os.path.join(RAW_DIR, run_folder)
    if not os.path.isdir(run_dir):
        return []

    folders = []
    for name in sorted(os.listdir(run_dir)):
        folder = os.path.join(run_dir, name)
        if not os.path.isdir(folder):
            continue
        if not os.path.exists(os.path.join(folder, "email.md")):
            continue
        folders.append(folder)
    return folders


def find_pending_folders() -> list[str]:
    """Return raw_emails/ folders that don't yet have a normalized request.

    Handles both flat (raw_emails/<slug>/) and nested (raw_emails/<ts>/<slug>/) layouts.
    """
    if not os.path.isdir(RAW_DIR):
        return []

    pending = []
    for name in sorted(os.listdir(RAW_DIR)):
        folder = os.path.join(RAW_DIR, name)
        if not os.path.isdir(folder):
            continue
        if os.path.exists(os.path.join(folder, "email.md")):
            pending.append(folder)
            continue
        for sub in sorted(os.listdir(folder)):
            subfolder = os.path.join(folder, sub)
            if os.path.isdir(subfolder) and os.path.exists(os.path.join(subfolder, "email.md")):
                pending.append(subfolder)

    return pending


def _build_pr_body(run_folder: str, created_files: list[str]) -> str:
    lines = [
        f"Normalized **{len(created_files)}** request document(s) "
        f"from ingest run `{run_folder}`.\n",
    ]
    for f in created_files:
        if f.endswith(".md"):
            lines.append(f"- `{f}`")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Normalize raw emails into requests")
    parser.add_argument("folders", nargs="*", help="Specific raw_emails/<ts>/<slug> folders")
    parser.add_argument("--run-folder", help="Timestamp of the ingest run to normalize")
    parser.add_argument("--branch", help="Branch name (for PR creation)")
    args = parser.parse_args()

    if args.run_folder:
        folders = find_folders_in_run(args.run_folder)
    elif args.folders:
        folders = args.folders
    else:
        folders = find_pending_folders()

    if not folders:
        log("No raw emails to normalize.")
        return

    log(f"Found {len(folders)} folder(s) to normalize.")

    total_created: list[str] = []
    for folder in folders:
        created = process_folder(folder)
        total_created.extend(created)

    log(f"Done. Created {len(total_created)} request document(s).")

    if not total_created:
        log("Nothing to commit.")
        return

    if args.run_folder and args.branch:
        git_commit_and_push(
            total_created,
            f"normalize: {len(total_created)} request document(s) from run {args.run_folder}",
            branch=args.branch,
        )
        log(f"Committed {len(total_created)} normalized file(s)")

        pr_title = f"ingest+normalize: {len(total_created)} request(s) — {args.run_folder}"
        pr_body = _build_pr_body(args.run_folder, total_created)
        pr_url = gh_pr_create(pr_title, pr_body)
        log(f"Created PR: {pr_url}")

        gh_pr_merge(pr_url)
        log(f"PR merged: {pr_url}")


if __name__ == "__main__":
    main()
