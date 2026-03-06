"""Shared helpers for requests-buddy scripts."""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import yaml


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown string.

    Returns (metadata_dict, body_string).
    """
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return meta, body


def render_frontmatter(meta: dict, body: str) -> str:
    """Render a metadata dict and body into a markdown string with YAML frontmatter."""
    fm = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body}\n"


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

def make_slug(date_str: str, subject: str, max_len: int = 80) -> str:
    """Generate a filesystem-safe slug from a date and subject line."""
    slug = subject.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")

    date_prefix = date_str[:10] if date_str else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{date_prefix}-{slug}"


# ---------------------------------------------------------------------------
# OpenCode CLI
# ---------------------------------------------------------------------------

def opencode_run(prompt: str, model: str | None = None) -> str:
    """Run a prompt through the opencode CLI and return the response text.

    Uses ``opencode run`` in non-interactive mode.  A temporary file is used
    to pass the prompt so we avoid command-line length limits with large
    payloads.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False
    ) as tmp:
        tmp.write(prompt)
        tmp_path = tmp.name

    try:
        cmd = ["opencode", "run", "--file", tmp_path]
        if model:
            cmd.extend(["--model", model])
        cmd.append(
            "Follow the instructions in the attached file. "
            "Return ONLY the requested output — no explanations, no tool use."
        )

        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"opencode run failed (exit {result.returncode}):\n{result.stderr}"
            )
        return result.stdout.strip()
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# gws CLI wrapper
# ---------------------------------------------------------------------------

def gws(*args: str) -> dict | list | str:
    """Run a gws CLI command and return parsed JSON output."""
    cmd = ["gws", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        print(f"gws command failed: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"gws exited with code {result.returncode}")

    output = result.stdout.strip()
    if not output:
        return {}
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git(*args: str, check: bool = True) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True, text=True, check=check,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def git_commit_and_push(files: list[str], message: str, branch: str = "main"):
    """Stage files, commit with message, and push to remote."""
    if not files:
        return
    for f in files:
        git("add", f)
    git("commit", "-m", message)
    git("push", "origin", branch)


def git_has_changes() -> bool:
    """Check if there are any staged or unstaged changes."""
    status = git("status", "--porcelain")
    return bool(status)


def gh_pr_create(title: str, body: str, base: str = "main") -> str:
    """Create a pull request via gh CLI and return the PR URL."""
    result = subprocess.run(
        ["gh", "pr", "create", "--title", title, "--body", body, "--base", base],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(message: str):
    """Print a timestamped log message to stderr."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {message}", file=sys.stderr)
