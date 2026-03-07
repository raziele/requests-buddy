#!/usr/bin/env python3
"""Quick local test for the split_requests function."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from utils import parse_frontmatter
from ingest_emails import split_requests


def test_file(path: str):
    print(f"\n{'='*60}")
    print(f"Testing: {path}")
    print(f"{'='*60}")

    with open(path) as f:
        text = f.read()

    meta, body = parse_frontmatter(text)
    subject = meta.get("subject", "no-subject")

    body_section = body
    if body_section.startswith("## Content"):
        body_section = body_section[len("## Content"):].strip()
    att_idx = body_section.find("\n## Attachments")
    if att_idx != -1:
        body_section = body_section[:att_idx].strip()

    print(f"\nSubject: {subject}")
    print(f"Body length: {len(body_section)} chars")
    print(f"\n--- Calling split_requests ---\n")

    results = split_requests(subject, body_section)

    print(f"\nGot {len(results)} request(s):\n")
    for i, req in enumerate(results):
        print(f"  [{i+1}] Title: {req['title']}")
        content_preview = req["content"][:200].replace("\n", " ")
        print(f"      Content: {content_preview}...")
        print()

    print(json.dumps(results, indent=2, ensure_ascii=False))


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
