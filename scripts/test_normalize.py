#!/usr/bin/env python3
"""Local test for the normalize_requests pipeline.

Usage:
    uv run python scripts/test_normalize.py                          # first 2 files
    uv run python scripts/test_normalize.py requests/some-file.md    # specific file
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from utils import parse_frontmatter
from ingest_emails import normalize_requests, build_normalized_markdown


def test_file(path: str):
    print(f"\n{'='*60}")
    print(f"Testing: {path}")
    print(f"{'='*60}")

    with open(path) as f:
        text = f.read()

    meta, body = parse_frontmatter(text)
    subject = meta.get("subject", "no-subject")
    attachments = meta.get("attachments", [])

    body_section = body
    if body_section.startswith("## Content"):
        body_section = body_section[len("## Content"):].strip()
    att_idx = body_section.find("\n## Attachments")
    if att_idx != -1:
        body_section = body_section[:att_idx].strip()

    print(f"\nSubject: {subject}")
    print(f"Attachments: {attachments}")
    print(f"Body length: {len(body_section)} chars")
    print(f"\n--- Calling normalize_requests ---\n")

    results = normalize_requests(subject, body_section, attachments)

    print(f"\nGot {len(results)} request(s):\n")
    for i, req in enumerate(results):
        org = req.get("organization", "?")
        print(f"  [{i+1}] Organization: {org}")
        print(f"      Type: {req.get('request_type', '?')}")
        print(f"      Urgency: {req.get('urgency', '?')}")
        print(f"      Sector: {req.get('sector', '?')}")
        summary_preview = (req.get("summary") or "")[:200].replace("\n", " ")
        print(f"      Summary: {summary_preview}...")
        print()

    print("--- Raw JSON ---")
    print(json.dumps(results, indent=2, ensure_ascii=False))

    print("\n--- Rendered Markdown (first request) ---\n")
    fake_headers = {
        "message-id": "<test@example.com>",
        "from": meta.get("from", "test@example.com"),
        "subject": subject,
        "date": meta.get("date", "2026-03-06"),
    }
    md = build_normalized_markdown(results[0], fake_headers, seq=1)
    print(md)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        requests_dir = os.path.join(os.path.dirname(__file__), "..", "requests")
        files = [
            os.path.join(requests_dir, f)
            for f in sorted(os.listdir(requests_dir))
            if f.endswith(".md")
        ][:2]

    for path in files:
        test_file(path)
