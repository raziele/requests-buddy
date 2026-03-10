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
# OpenRouter API (direct)
# ---------------------------------------------------------------------------

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_FREE_MODEL = "openrouter/free"


def openrouter_chat(
    system_prompt: str,
    user_content: str | list,
    *,
    model: str | None = None,
    temperature: float = 0.1,
) -> str:
    """Call the OpenRouter chat completions API and return the assistant message.

    *user_content* is either a plain string or a list of content parts
    (text, file objects) per the OpenRouter multimodal spec.
    When file parts are present, the pdf-text plugin is automatically enabled.
    """
    import requests as _requests

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    if isinstance(user_content, str):
        user_content = [{"type": "text", "text": user_content}]

    has_files = any(
        isinstance(p, dict) and p.get("type") == "file" for p in user_content
    )

    payload: dict = {
        "model": model or OPENROUTER_FREE_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    if has_files:
        payload["plugins"] = [
            {"id": "file-parser", "pdf": {"engine": "pdf-text"}}
        ]

    resp = _requests.post(
        OPENROUTER_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=300,
    )
    resp.raise_for_status()

    data = resp.json()
    choices = data.get("choices")
    if not choices:
        raise RuntimeError(f"OpenRouter returned no choices: {data}")

    content = choices[0]["message"].get("content")
    if content is None:
        raise RuntimeError("OpenRouter returned null content")
    return content.strip()


def openrouter_extract_pdf(pdf_path: str) -> str:
    """Extract text from a PDF file using OpenRouter's pdf-text plugin.

    Sends the PDF as a base64 file part to the openrouter/free model and
    returns the extracted text content.
    """
    import base64

    with open(pdf_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    filename = os.path.basename(pdf_path)
    user_content = [
        {"type": "text", "text": "Extract all text content from this PDF document. Return the full text as-is, preserving structure (headings, lists, tables, paragraphs)."},
        {
            "type": "file",
            "file": {
                "filename": filename,
                "file_data": f"data:application/pdf;base64,{b64}",
            },
        },
    ]

    return openrouter_chat(
        "You are a document text extractor. Return only the extracted text, no commentary.",
        user_content,
        model=OPENROUTER_FREE_MODEL,
        temperature=0.0,
    )


# ---------------------------------------------------------------------------
# OpenCode CLI (via OpenRouter)
# ---------------------------------------------------------------------------


def opencode_run(
    message: str,
    *,
    files: list[str],
    agent: str = "normalize",
    model: str | None = None,
    cwd: str | None = None,
    env: dict | None = None,
) -> str:
    """Run opencode CLI with the given message and file attachments.

    Requires OPENROUTER_API_KEY in env so opencode can reach the OpenRouter provider.
    """
    _scripts_dir = os.path.dirname(os.path.abspath(__file__))
    _repo_root = os.path.join(_scripts_dir, "..")
    load_dotenv(os.path.join(_repo_root, ".env"))

    cmd = ["opencode", "run", "--agent", agent]
    if model:
        cmd.extend(["--model", model])
    for fp in files:
        cmd.extend(["--file", fp])
    cmd.append("--")
    cmd.append(message)

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
        env=run_env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"opencode run failed (exit {result.returncode}):\n{result.stderr}"
        )
    out = (result.stdout or "").strip()
    # Known opencode behavior: run output sometimes goes to stderr (e.g. issue #369)
    if not out and (result.stderr or "").strip():
        out = (result.stderr or "").strip()
    if not out:
        raise RuntimeError(
            "opencode run produced no output"
            + (f"; stderr: {result.stderr[:500]!r}" if result.stderr else "")
        )
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
        capture_output=True, text=True, check=check,
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

    Uses --auto so GitHub merges it once any required status checks pass,
    without needing admin-bypass rights on the token.
    """
    subprocess.run(
        ["gh", "pr", "merge", pr_url, f"--{method}", "--auto", "--delete-branch"],
        capture_output=True, text=True, check=True,
    )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(message: str):
    """Print a timestamped log message to stderr."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {message}", file=sys.stderr)
