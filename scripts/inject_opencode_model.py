#!/usr/bin/env python3
"""Replace {env:OPENROUTER_MODEL} in opencode.json with the value from OPENROUTER_MODEL env.

Run before opencode so the model key under provider.openrouter.models and
the model values are resolved at runtime.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPENCODE_JSON = os.path.join(REPO_ROOT, "opencode.json")
PLACEHOLDER = "{env:OPENROUTER_MODEL}"


def main() -> None:
    model = os.environ.get("OPENROUTER_MODEL", "").strip()
    if not model:
        print("OPENROUTER_MODEL not set", file=sys.stderr)
        sys.exit(1)

    with open(OPENCODE_JSON) as f:
        content = f.read()

    if PLACEHOLDER not in content:
        return  # Already injected (e.g. by workflow step)

    content = content.replace(PLACEHOLDER, model)

    with open(OPENCODE_JSON, "w") as f:
        f.write(content)


if __name__ == "__main__":
    main()
