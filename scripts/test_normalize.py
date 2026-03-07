#!/usr/bin/env python3
"""Test the normalize step on a raw_emails/ folder.

Usage:
    uv run python scripts/test_normalize.py                                # first pending folder
    uv run python scripts/test_normalize.py raw_emails/test-kibbutzim      # specific folder
    uv run python scripts/test_normalize.py raw_emails/test-kibbutzim -o out.txt
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from normalize_requests import normalize_email, build_normalized_markdown, find_pending_folders
from utils import parse_frontmatter


def test_folder(folder: str, out=None):
    f = out or sys.stdout

    def p(msg):
        print(msg, file=f, flush=True)
        if f is not sys.stdout:
            print(msg, flush=True)

    email_path = os.path.join(folder, "email.md")
    if not os.path.exists(email_path):
        p(f"ERROR: {email_path} not found")
        return

    with open(email_path) as fp:
        text = fp.read()
    headers, _ = parse_frontmatter(text)

    p(f"\n{'='*60}")
    p(f"Folder:  {folder}")
    p(f"Subject: {headers.get('subject', '?')}")
    p(f"From:    {headers.get('from', '?')}")
    p(f"{'='*60}")
    p(f"\n--- Running opencode normalize ---\n")

    results = normalize_email(email_path)

    p(f"\nGot {len(results)} request(s):\n")
    for i, req in enumerate(results):
        if req.get("_fallback"):
            p(f"  [{i+1}] FALLBACK (opencode failed)")
            continue
        p(f"  [{i+1}] Organization: {req.get('organization', '?')}")
        p(f"      Type:     {req.get('request_type', '?')}")
        p(f"      Urgency:  {req.get('urgency', '?')}")
        p(f"      Sector:   {req.get('sector', '?')}")
        p(f"      Summary:  {(req.get('summary') or '')[:200]}...")
        p("")

    import json
    p("--- Raw JSON ---")
    p(json.dumps(results, indent=2, ensure_ascii=False))

    good = [r for r in results if not r.get("_fallback")]
    if good:
        p("\n--- Rendered Markdown (first request) ---\n")
        md = build_normalized_markdown(good[0], headers, seq=1)
        p(md)


if __name__ == "__main__":
    args = sys.argv[1:]
    output_path = None

    if "-o" in args:
        i = args.index("-o")
        output_path = args[i + 1]
        args = args[:i] + args[i + 2:]

    if args:
        folders = args
    else:
        folders = find_pending_folders()[:1]

    if not folders:
        print("No folders to test. Provide a path or add folders to raw_emails/.")
        sys.exit(1)

    out_f = open(output_path, "w") if output_path else None
    try:
        for folder in folders:
            test_folder(folder, out=out_f)
    finally:
        if out_f:
            out_f.close()
            print(f"Output written to {output_path}")
