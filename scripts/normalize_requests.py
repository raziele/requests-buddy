#!/usr/bin/env python3
"""Step 2: Normalize raw emails into structured request documents.

Reads each folder in raw_emails/, sends the email through opencode with
the normalize-request prompt, and writes the result to requests/.

Usage:
    uv run python scripts/normalize_requests.py                     # all pending
    uv run python scripts/normalize_requests.py raw_emails/<slug>   # specific folder
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from utils import log, make_slug, opencode_run, parse_frontmatter, render_frontmatter

RAW_DIR = "raw_emails"
REQUESTS_DIR = "requests"
PROMPT_REF = "@prompts/normalize-request.md"


def normalize_email(email_file: str) -> list[dict]:
    """Run opencode on a raw email file and return structured request dicts.

    Uses @file references so opencode reads the prompt and email natively.
    Falls back to a minimal dict on error.
    """
    fallback = [{"_fallback": True}]

    message = (
        f"Read {PROMPT_REF} and apply the instructions on @{email_file}. "
        "Return ONLY the JSON output — no explanations, no markdown fences."
    )

    try:
        raw = opencode_run(message)
    except RuntimeError as e:
        log(f"  opencode failed: {e}")
        return fallback

    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log(f"  Could not parse JSON: {e}")
        log(f"  Raw output: {raw[:500]}")
        return fallback

    requests = data.get("requests")
    if not isinstance(requests, list) or not requests:
        log("  Empty or invalid requests list")
        return fallback

    for req in requests:
        if not isinstance(req, dict) or "summary" not in req:
            log("  Malformed request entry, using fallback")
            return fallback

    log(f"  Normalized into {len(requests)} request(s)")
    return requests


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

    lines.extend([
        "",
        "## Internal Notes",
        "",
        "_To be filled by reviewer._",
    ])

    return render_frontmatter(meta, "\n".join(lines))


def process_folder(folder: str) -> list[str]:
    """Normalize a single raw_emails/<slug>/ folder.

    Returns list of created request file paths.
    """
    email_path = os.path.join(folder, "email.md")
    if not os.path.exists(email_path):
        log(f"  Skipping {folder}: no email.md found")
        return []

    with open(email_path) as f:
        text = f.read()

    headers, _ = parse_frontmatter(text)
    slug = os.path.basename(folder)

    log(f"Normalizing {folder}...")
    normalized = normalize_email(email_path)

    created = []
    for i, req in enumerate(normalized):
        if req.get("_fallback"):
            log(f"  Skipping fallback result for {folder}")
            continue

        org = req.get("organization") or headers.get("subject", slug)
        out_slug = make_slug(headers.get("date", ""), org)
        out_path = os.path.join(REQUESTS_DIR, f"{out_slug}.md")

        if os.path.exists(out_path):
            out_slug = f"{out_slug}-{i+1}"
            out_path = os.path.join(REQUESTS_DIR, f"{out_slug}.md")

        md = build_normalized_markdown(req, headers, seq=i + 1)

        os.makedirs(REQUESTS_DIR, exist_ok=True)
        with open(out_path, "w") as f:
            f.write(md)
        log(f"  Wrote {out_path}")
        created.append(out_path)

    return created


def find_pending_folders() -> list[str]:
    """Return raw_emails/ folders that don't yet have a normalized request."""
    if not os.path.isdir(RAW_DIR):
        return []

    pending = []
    for name in sorted(os.listdir(RAW_DIR)):
        folder = os.path.join(RAW_DIR, name)
        if not os.path.isdir(folder):
            continue
        if not os.path.exists(os.path.join(folder, "email.md")):
            continue
        pending.append(folder)

    return pending


def main():
    if len(sys.argv) > 1:
        folders = sys.argv[1:]
    else:
        folders = find_pending_folders()

    if not folders:
        log("No raw emails to normalize.")
        return

    log(f"Found {len(folders)} folder(s) to normalize.")

    total_created = []
    for folder in folders:
        created = process_folder(folder)
        total_created.extend(created)

    log(f"Done. Created {len(total_created)} request document(s).")


if __name__ == "__main__":
    main()
