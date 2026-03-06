#!/usr/bin/env python3
"""Process 1: Fetch unread Gmail messages and convert to structured Markdown."""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from utils import gws, git_commit_and_push, log, make_slug, render_frontmatter


REQUESTS_DIR = "requests"


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


def build_markdown(headers: dict, body: str, attachments: list[str]) -> str:
    """Build a structured Markdown document from email data."""
    meta = {
        "id": headers.get("message-id", ""),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
    }
    if attachments:
        meta["attachments"] = attachments

    sections = ["## Content", "", body.strip()]

    if attachments:
        sections += ["", "## Attachments", ""]
        for fname in attachments:
            sections.append(f"- [{fname}](attachments/{fname})")

    return render_frontmatter(meta, "\n".join(sections))


def process_message(msg_stub: dict, label_id: str) -> tuple[str, list[str], dict[str, str]] | None:
    """Process a single message. Returns (slug, created_files, headers) or None on failure."""
    msg_id = msg_stub["id"]
    log(f"Processing message {msg_id}...")

    try:
        msg = get_message(msg_id)
    except RuntimeError as e:
        log(f"  Failed to fetch message: {e}")
        return None

    headers = extract_headers(msg)
    subject = headers.get("subject", "no-subject")
    date = headers.get("date", "")

    slug = make_slug(date, subject)
    md_path = os.path.join(REQUESTS_DIR, f"{slug}.md")

    if os.path.exists(md_path):
        slug = f"{slug}-{msg_id[:8]}"
        md_path = os.path.join(REQUESTS_DIR, f"{slug}.md")

    att_dir = os.path.join(REQUESTS_DIR, slug, "attachments")
    attachments = extract_attachments(msg, att_dir)

    body = decode_body(msg.get("payload", {}))
    markdown = build_markdown(headers, body, attachments)

    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    with open(md_path, "w") as f:
        f.write(markdown)
    log(f"  Wrote {md_path}")

    created_files = [md_path]
    for fname in attachments:
        created_files.append(os.path.join(att_dir, fname))

    mark_processed(msg_id, label_id)
    log(f"  Marked as processed")

    return slug, created_files, headers


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

    for msg_stub in messages:
        result = process_message(msg_stub, label_id)
        if result is None:
            continue

        slug, created_files, headers = result
        attachments = headers.get("attachments", [])
        if isinstance(attachments, str):
            attachments = [attachments]

        att_dir = os.path.join(REQUESTS_DIR, slug, "attachments")
        att_count = len([f for f in created_files if "attachments/" in f])

        commit_msg = build_commit_message(headers, slug, att_count)
        git_commit_and_push(created_files, commit_msg)
        log(f"  Committed and pushed: {slug}")

    log("Ingestion complete.")


if __name__ == "__main__":
    main()
