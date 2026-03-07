#!/usr/bin/env python3
"""Step 2: Normalize raw emails into structured request documents.

Reads each folder in raw_emails/, sends the email + PDF attachments to
the OpenRouter API with the normalize-request prompt, and writes the
result to requests/.

Usage:
    uv run python scripts/normalize_requests.py                     # all pending
    uv run python scripts/normalize_requests.py raw_emails/<slug>   # specific folder
"""

import base64
import json
import mimetypes
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from utils import (
    GEMINI_MODEL,
    log,
    make_slug,
    opencode_run,
    openrouter_chat,
    parse_frontmatter,
    render_frontmatter,
)

RAW_DIR = "raw_emails"
REQUESTS_DIR = "requests"
SCRIPT_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
PROMPT_PATH = os.path.join(PROJECT_ROOT, "prompts", "normalize-request.md")


def _load_system_prompt() -> str:
    with open(PROMPT_PATH) as f:
        return f.read()


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


def _build_user_content(folder: str) -> list[dict]:
    """Build the OpenRouter multimodal content array from a raw email folder.

    Includes the email.md text and any PDF attachments as base64 file parts.
    """
    parts: list[dict] = []

    email_path = os.path.join(folder, "email.md")
    with open(email_path) as f:
        email_text = f.read()
    parts.append({"type": "text", "text": email_text})

    for fname in sorted(os.listdir(folder)):
        if fname == "email.md":
            continue
        fpath = os.path.join(folder, fname)
        if not os.path.isfile(fpath):
            continue

        mime, _ = mimetypes.guess_type(fname)
        if mime == "application/pdf":
            with open(fpath, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            parts.append({
                "type": "file",
                "file": {
                    "filename": fname,
                    "file_data": f"data:application/pdf;base64,{b64}",
                },
            })
            log(f"  Attached PDF: {fname}")
        else:
            try:
                with open(fpath) as f:
                    text = f.read()
                parts.append({
                    "type": "text",
                    "text": f"--- Attachment: {fname} ---\n{text}",
                })
            except (UnicodeDecodeError, OSError):
                log(f"  Skipping binary attachment: {fname}")

    return parts


def _parse_normalize_response(raw: str) -> list[dict] | None:
    """Parse opencode/OpenRouter JSON response into requests list or None."""
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


def normalize_email(folder: str) -> list[dict]:
    """Normalize raw email folder: try opencode+Gemini if GEMINI_API_KEY set, else OpenRouter."""
    fallback = [{"_fallback": True}]

    email_path = os.path.join(folder, "email.md")
    if not os.path.exists(email_path):
        log(f"  No email.md in {folder}")
        return fallback

    # Prefer opencode with Gemini when GEMINI_API_KEY is set
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        file_paths = _folder_file_paths(folder)
        if file_paths:
            file_names = ", ".join(os.path.basename(p) for p in file_paths)
            message = f"Normalize the attached email and any attachments ({file_names}). Return only the JSON."
            try:
                log("  Trying opencode (Gemini)...")
                raw = opencode_run(
                    message,
                    files=file_paths,
                    agent="normalize",
                    model=GEMINI_MODEL,
                    cwd=PROJECT_ROOT,
                    env={"GEMINI_API_KEY": gemini_key},
                )
                parsed = _parse_normalize_response(raw)
                if parsed:
                    log(f"  Normalized into {len(parsed)} request(s) via opencode (Gemini)")
                    return parsed
            except Exception as e:
                log(f"  opencode failed: {e}, falling back to OpenRouter")

    # OpenRouter path (or fallback)
    system_prompt = _load_system_prompt()
    user_content = _build_user_content(folder)
    try:
        raw = openrouter_chat(system_prompt, user_content)
    except Exception as e:
        log(f"  OpenRouter call failed: {e}")
        return fallback

    parsed = _parse_normalize_response(raw)
    if not parsed:
        log(f"  Could not parse JSON from OpenRouter")
        log(f"  Raw output: {raw[:500]}")
        return fallback
    log(f"  Normalized into {len(parsed)} request(s) via OpenRouter")
    return parsed


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
    normalized = normalize_email(folder)

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

        created.extend(_copy_attachments(folder, out_path))

    return created


def _copy_attachments(raw_folder: str, md_path: str) -> list[str]:
    """Copy non-email.md files from raw_folder into a subfolder next to md_path.

    E.g. requests/2026-03-05-org.md  ->  requests/2026-03-05-org/<filename>
    """
    att_dir = md_path.removesuffix(".md")
    copied = []
    for fname in sorted(os.listdir(raw_folder)):
        if fname == "email.md":
            continue
        src = os.path.join(raw_folder, fname)
        if not os.path.isfile(src):
            continue
        os.makedirs(att_dir, exist_ok=True)
        dst = os.path.join(att_dir, fname)
        shutil.copy2(src, dst)
        copied.append(dst)
        log(f"  Copied attachment: {dst}")
    return copied


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
