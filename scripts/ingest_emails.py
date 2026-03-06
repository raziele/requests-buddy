#!/usr/bin/env python3
"""Process 1: Fetch unread Gmail messages and convert to structured Markdown."""

import base64
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone

from utils import (
    gws,
    gh_pr_create,
    gh_pr_merge,
    git_commit_and_push,
    git_create_branch,
    log,
    make_slug,
    opencode_run,
    render_frontmatter,
)


REQUESTS_DIR = "requests"
NORMALIZE_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "normalize-request.md")


def ensure_processed_label() -> str:
    """Return the label ID for 'processed', creating it if necessary."""
    result = gws("gmail", "users", "labels", "list", "--params", '{"userId": "me"}')
    labels = result.get("labels", [])
    for label in labels:
        if label.get("name") == "processed":
            return label["id"]

    log("Creating 'processed' label...")
    created = gws(
        "gmail", "users", "labels", "create",
        "--params", '{"userId": "me"}',
        "--json", '{"name": "processed", "labelListVisibility": "labelShow", "messageListVisibility": "show"}',
    )
    return created["id"]


def list_unread_emails() -> list[dict]:
    """Return list of unread, unprocessed message stubs."""
    result = gws(
        "gmail", "users", "messages", "list",
        "--params", json.dumps({
            "userId": "me",
            "q": "is:unread -label:processed",
        }),
    )
    return result.get("messages", [])


def get_message(msg_id: str) -> dict:
    """Fetch full message by ID."""
    return gws(
        "gmail", "users", "messages", "get",
        "--params", json.dumps({
            "userId": "me",
            "id": msg_id,
            "format": "full",
        }),
    )


def extract_headers(msg: dict) -> dict[str, str]:
    """Extract common headers from a message payload."""
    headers = {}
    for h in msg.get("payload", {}).get("headers", []):
        name = h["name"].lower()
        if name in ("from", "subject", "date", "message-id"):
            headers[name] = h["value"]
    return headers


def decode_body(payload: dict) -> str:
    """Recursively extract the text body from a message payload."""
    mime = payload.get("mimeType", "")

    if mime == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")

    # Fallback: try html or nested parts
    for part in payload.get("parts", []):
        result = decode_body(part)
        if result:
            return result

    if mime == "text/html" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    return ""


def normalize_requests(subject: str, body: str, attachments: list[str]) -> list[dict]:
    """Use opencode to clean the email and produce normalized request documents.

    Returns a list of dicts with all template fields populated.
    Falls back to a minimal dict with just 'organization' and 'summary' on error.
    """
    fallback = [{
        "organization": subject,
        "summary": body[:2000],
        "context": body[:4000],
        "_fallback": True,
    }]

    try:
        with open(NORMALIZE_PROMPT_PATH) as f:
            template = f.read()
    except OSError as e:
        log(f"  Could not load normalize-request prompt: {e}")
        return fallback

    att_str = ", ".join(attachments) if attachments else "(none)"
    prompt = (
        template
        .replace("{{subject}}", subject)
        .replace("{{body}}", body)
        .replace("{{attachments}}", att_str)
    )

    try:
        raw = opencode_run(prompt)
    except RuntimeError as e:
        log(f"  opencode failed, skipping normalize: {e}")
        return fallback

    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log(f"  Could not parse opencode JSON response: {e}")
        return fallback

    requests = data.get("requests")
    if not isinstance(requests, list) or not requests:
        log("  opencode returned empty or invalid requests list")
        return fallback

    for req in requests:
        if not isinstance(req, dict) or "summary" not in req:
            log("  opencode returned malformed request entry, using fallback")
            return fallback

    log(f"  Normalized email into {len(requests)} request(s)")
    return requests


def extract_attachments(msg: dict, dest_dir: str) -> list[str]:
    """Download attachments and return list of filenames."""
    msg_id = msg["id"]
    filenames = []

    for part in msg.get("payload", {}).get("parts", []):
        filename = part.get("filename")
        if not filename:
            continue

        att_id = part.get("body", {}).get("attachmentId")
        if not att_id:
            continue

        att_data = gws(
            "gmail", "users", "messages", "attachments", "get",
            "--params", json.dumps({
                "userId": "me",
                "messageId": msg_id,
                "id": att_id,
            }),
        )

        raw = att_data.get("data", "")
        if raw:
            os.makedirs(dest_dir, exist_ok=True)
            content = base64.urlsafe_b64decode(raw)
            filepath = os.path.join(dest_dir, filename)
            with open(filepath, "wb") as f:
                f.write(content)
            filenames.append(filename)
            log(f"  Saved attachment: {filepath}")

    return filenames


def mark_processed(msg_id: str, label_id: str):
    """Remove UNREAD label and add 'processed' label."""
    gws(
        "gmail", "users", "messages", "modify",
        "--params", json.dumps({"userId": "me", "id": msg_id}),
        "--json", json.dumps({
            "removeLabelIds": ["UNREAD"],
            "addLabelIds": [label_id],
        }),
    )


def build_normalized_markdown(req: dict, headers: dict, seq: int) -> str:
    """Render a normalized request dict into the standard template."""

    org = req.get("organization") or headers.get("subject", "Unknown")
    summary = req.get("summary") or ""
    req_id = f"REQ-{headers.get('date', '')[:10]}-{seq:03d}"

    meta = {
        "id": req_id,
        "source_email_id": headers.get("message-id", ""),
        "date_received": headers.get("date", "")[:25],
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


def process_message(msg_stub: dict, label_id: str) -> list[tuple[str, list[str], dict[str, str]]]:
    """Process a single message: download attachments, normalize via LLM, write docs.

    Returns a list of (slug, created_files, headers) tuples — one per normalized request.
    Returns an empty list on failure.
    """
    msg_id = msg_stub["id"]
    log(f"Processing message {msg_id}...")

    try:
        msg = get_message(msg_id)
    except RuntimeError as e:
        log(f"  Failed to fetch message: {e}")
        return []

    headers = extract_headers(msg)
    subject = headers.get("subject", "no-subject")
    date = headers.get("date", "")

    body = decode_body(msg.get("payload", {}))

    base_slug = make_slug(date, subject)
    att_dir = os.path.join(REQUESTS_DIR, base_slug, "attachments")
    attachment_filenames = extract_attachments(msg, att_dir)

    normalized = normalize_requests(subject, body, attachment_filenames)

    results: list[tuple[str, list[str], dict[str, str]]] = []

    for i, req in enumerate(normalized):
        org = req.get("organization") or subject
        slug = make_slug(date, org)
        md_path = os.path.join(REQUESTS_DIR, f"{slug}.md")

        if os.path.exists(md_path):
            slug = f"{slug}-{msg_id[:8]}"
            md_path = os.path.join(REQUESTS_DIR, f"{slug}.md")

        req_headers = {**headers}
        if len(normalized) > 1:
            req_headers["split_from"] = headers.get("message-id", msg_id)
            req_headers["split_index"] = i + 1
            req_headers["split_total"] = len(normalized)

        markdown = build_normalized_markdown(req, req_headers, seq=i + 1)

        os.makedirs(os.path.dirname(md_path) if os.path.dirname(md_path) else ".", exist_ok=True)
        with open(md_path, "w") as f:
            f.write(markdown)
        log(f"  Wrote {md_path}")

        created_files = [md_path]
        if i == 0:
            for fname in attachment_filenames:
                created_files.append(os.path.join(att_dir, fname))

        results.append((slug, created_files, req_headers))

    mark_processed(msg_id, label_id)
    log(f"  Marked as processed")

    return results


def build_commit_message(headers: dict, slug: str, num_attachments: int) -> str:
    """Build a structured commit message for the ingestion log."""
    subject = headers.get("subject", "no-subject")
    sender = headers.get("from", "unknown")
    date = headers.get("date", "unknown")
    msg_id = headers.get("message-id", "unknown")

    att_label = f" [{num_attachments} attachment{'s' if num_attachments != 1 else ''}]" if num_attachments else ""

    return (
        f"ingest: {subject}{att_label}\n"
        f"\n"
        f"From: {sender}\n"
        f"Date: {date}\n"
        f"Message-ID: {msg_id}\n"
        f"File: requests/{slug}.md"
    )


def build_pr_body(ingested: list[tuple[str, dict]]) -> str:
    """Build a PR body summarizing all ingested emails."""
    lines = [f"Ingested **{len(ingested)}** email(s).\n"]
    for slug, headers in ingested:
        subject = headers.get("subject", "no-subject")
        sender = headers.get("from", "unknown")
        lines.append(f"- **{subject}** — from {sender} → `requests/{slug}.md`")
    return "\n".join(lines)


def main():
    os.makedirs(REQUESTS_DIR, exist_ok=True)

    log("Ensuring 'processed' label exists...")
    label_id = ensure_processed_label()

    log("Fetching unread emails...")
    messages = list_unread_emails()

    if not messages:
        log("No new emails to process.")
        return

    log(f"Found {len(messages)} unread email(s).")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"ingest/{ts}"
    git_create_branch(branch)
    log(f"Created branch {branch}")

    ingested: list[tuple[str, dict]] = []

    for msg_stub in messages:
        results = process_message(msg_stub, label_id)
        if not results:
            continue

        all_files = []
        for slug, created_files, headers in results:
            all_files.extend(created_files)
            ingested.append((slug, headers))

        first_slug = results[0][0]
        first_headers = results[0][2]
        att_count = len([f for f in all_files if "attachments/" in f])

        commit_msg = build_commit_message(first_headers, first_slug, att_count)
        if len(results) > 1:
            extra_slugs = ", ".join(s for s, _, _ in results[1:])
            commit_msg += f"\nAlso: {extra_slugs}"

        git_commit_and_push(all_files, commit_msg, branch=branch)
        log(f"  Committed {len(results)} request(s) from message {msg_stub['id']}")

    if not ingested:
        log("No emails were successfully processed.")
        return

    pr_title = f"ingest: {len(ingested)} new request(s) — {ts}"
    pr_body = build_pr_body(ingested)
    pr_url = gh_pr_create(pr_title, pr_body)
    log(f"Created PR: {pr_url}")

    gh_pr_merge(pr_url)
    log(f"PR merged: {pr_url}")

    log("Ingestion complete.")


if __name__ == "__main__":
    main()
