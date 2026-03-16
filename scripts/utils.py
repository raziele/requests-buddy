"""Shared helpers for requests-buddy scripts."""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import yaml

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        pass


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

def make_slug(date_str: str, subject: str, max_len: int = 80, include_date: bool = True) -> str:
    """Generate a filesystem-safe slug from a date and subject line."""
    slug = subject.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")

    if not include_date:
        return slug

    date_prefix = date_str[:10] if date_str else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{date_prefix}-{slug}"


# ---------------------------------------------------------------------------
# Cursor Agent CLI
# ---------------------------------------------------------------------------


def cursor_agent_run(
    prompt: str,
    *,
    cwd: str | None = None,
) -> str:
    """Run Cursor agent CLI and return its text output.

    Files are referenced inside the prompt as relative paths
    (Cursor agent can read and parse PDFs natively).

    Requires CURSOR_API_KEY env var.
    """
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("CURSOR_API_KEY not set")

    cmd = [
        "agent",
        "--api-key", api_key,
        "--model", "auto",
        "--output-format", "text",
        "--trust",
        "--force",
        "-p",
        prompt,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cursor agent failed (exit {result.returncode}):\n{result.stderr}"
        )
    out = (result.stdout or "").strip()
    if not out:
        raise RuntimeError("cursor agent produced no output")
    return out


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
        capture_output=True, text=True, check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def git_create_branch(name: str):
    """Create and checkout a new branch from the current HEAD."""
    git("checkout", "-b", name)


def git_commit(files: list[str], message: str):
    """Stage files and commit (without pushing)."""
    if not files:
        return
    for f in files:
        git("add", f)
    git("commit", "-m", message)


def git_push(branch: str = "main"):
    """Push current branch to origin."""
    git("push", "origin", branch)


def git_commit_and_push(files: list[str], message: str, branch: str = "main"):
    """Stage files, commit with message, and push to remote."""
    git_commit(files, message)
    git_push(branch)


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


def gh_pr_merge(pr_url: str, method: str = "squash"):
    """Merge a pull request via gh CLI.

    Requires GH_TOKEN to be set to a PAT with pull-requests:write and
    contents:write so it can merge directly without admin-bypass flags.
    """
    subprocess.run(
        ["gh", "pr", "merge", pr_url, f"--{method}", "--delete-branch"],
        capture_output=True, text=True, check=True,
    )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(message: str):
    """Print a timestamped log message to stderr."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {message}", file=sys.stderr)
