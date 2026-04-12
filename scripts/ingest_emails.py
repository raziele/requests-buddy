#!/usr/bin/env python3
"""Process 1: Fetch unread Gmail messages and save raw emails to raw_emails/.

Each ingestion run creates a timestamped folder:
    raw_emails/<timestamp>/
        <slug>/
            email.md          # frontmatter + raw body
            attachment1.pdf   # any attachments
"""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone

from utils import (
    gws,
    git_commit,
    git_create_branch,
    git_push,
    log,
    make_slug,
    render_frontmatter,
)


RAW_DIR = "raw_emails"


def _today_label_name() -> str:
    return datetime.now(timezone.utc).strftime("%y_%m_%d_processed")


def _list_all_labels() -> list[dict]:
    result = gws("gmail", "users", "labels", "list", "--params", '{"userId": "me"}')
    return result.get("labels", [])


def ensure_processed_label() -> str:
    """Return the label ID for today's YY_MM_DD_processed label, creating it if needed."""
    label_name = _today_label_name()
    labels = _list_all_labels()
    for label in labels:
        if label.get("name") == label_name:
            return label["id"]

    log(f"Creating '{label_name}' label...")
    created = gws(
        "gmail", "users", "labels", "create",
        "--params", '{"userId": "me"}',
        "--json", json.dumps({
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }),
    )
    return created["id"]


def list_unread_emails() -> list[dict]:
    """Return list of unread messages not yet labeled with any *_processed label."""
    labels = _list_all_labels()
    processed_names = [
        l["name"] for l in labels
        if l.get("name", "") == "processed"
        or l.get("name", "").endswith("_processed")
    ]
    exclude = " ".join(f"-label:{name}" for name in processed_names)
    q = f"is:unread {exclude}".strip()
    log(f"  Gmail query: {q}")

    result = gws(
        "gmail", "users", "messages", "list",
        "--params", json.dumps({
            "userId": "me",
            "q": q,
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

    for part in payload.get("parts", []):
        result = decode_body(part)
        if result:
            return result

    if mime == "text/html" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    return ""


def extract_attachments(msg: dict, dest_dir: str) -> list[str]:
    """Download attachments into dest_dir and return list of filenames."""
    msg_id = msg["id"]
    filenames = []

    for part in msg.get("payload", {}).get("parts", []):
        filename = os.path.basename(part.get("filename") or "")
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
            if not os.path.realpath(filepath).startswith(os.path.realpath(dest_dir) + os.sep):
                log(f"  Skipping unsafe attachment filename: {filename}")
                continue
            with open(filepath, "wb") as f:
                f.write(content)
            filenames.append(filename)
            log(f"  Saved attachment: {filepath}")

    return filenames


def mark_processed(msg_id: str, label_id: str):
    """Remove UNREAD label and add today's dated processed label."""
    gws(
        "gmail", "users", "messages", "modify",
        "--params", json.dumps({"userId": "me", "id": msg_id}),
        "--json", json.dumps({
            "removeLabelIds": ["UNREAD"],
            "addLabelIds": [label_id],
        }),
    )


def build_raw_markdown(headers: dict, body: str, attachments: list[str]) -> str:
    """Build a raw Markdown document from email data."""
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
            sections.append(f"- [{fname}]({fname})")

    return render_frontmatter(meta, "\n".join(sections))


def process_message(msg_stub: dict, label_id: str, run_dir: str) -> tuple[str, list[str], dict[str, str]] | None:
    """Fetch a single message and save it to run_dir/<slug>/.

    Returns (slug, created_files, headers) or None on failure.
    """
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
    body = decode_body(msg.get("payload", {}))

    slug = msg_id
    folder = os.path.join(run_dir, slug)
    os.makedirs(folder, exist_ok=True)

    attachment_filenames = extract_attachments(msg, folder)

    email_path = os.path.join(folder, "email.md")
    raw_md = build_raw_markdown(headers, body, attachment_filenames)
    with open(email_path, "w") as f:
        f.write(raw_md)
    log(f"  Wrote {email_path}")

    mark_processed(msg_id, label_id)
    log(f"  Marked as processed")

    created_files = [email_path]
    for fname in attachment_filenames:
        created_files.append(os.path.join(folder, fname))

    return slug, created_files, headers


def build_commit_message(headers: dict, run_ts: str, slug: str, num_attachments: int) -> str:
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
        f"File: raw_emails/{run_ts}/{slug}/"
    )



def main():
    log("Ensuring processed label exists...")
    label_id = ensure_processed_label()

    log("Fetching unread emails...")
    messages = list_unread_emails()

    if not messages:
        log("No new emails to process.")
        return

    log(f"Found {len(messages)} unread email(s).")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(RAW_DIR, ts)
    os.makedirs(run_dir, exist_ok=True)

    branch = f"ingest/{ts}"
    git_create_branch(branch)
    log(f"Created branch {branch}")

    ingested: list[tuple[str, dict]] = []

    for msg_stub in messages:
        result = process_message(msg_stub, label_id, run_dir)
        if not result:
            continue

        slug, created_files, headers = result
        att_count = len(created_files) - 1

        commit_msg = build_commit_message(headers, ts, slug, att_count)
        git_commit(created_files, commit_msg)
        ingested.append((slug, headers))
        log(f"  Committed raw_emails/{ts}/{slug}/")

    if not ingested:
        log("No emails were successfully processed.")
        return

    git_push(branch)
    log("Ingestion complete — normalize will run automatically on push.")


if __name__ == "__main__":
    main()
