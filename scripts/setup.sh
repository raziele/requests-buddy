#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

SECRETS_DIR=".secrets"
mkdir -p "$SECRETS_DIR"

echo "============================================"
echo "  Requests Buddy — First-Time Setup"
echo "============================================"
echo ""

# --- Prerequisites: uv, gh, git (required) ---
for cmd in uv gh git; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: '$cmd' is not installed. See README.md for prerequisites."
    exit 1
  fi
done

# --- Install gws if missing ---
if ! command -v gws &>/dev/null; then
  echo "Installing gws (Google Workspace CLI)..."
  npm install -g @googleworkspace/cli
  echo "Installed gws."
else
  echo "gws already installed."
fi

# --- Install opencode if missing ---
if ! command -v opencode &>/dev/null; then
  echo "Installing opencode..."
  npm install -g @opencode-ai/cli
  echo "Installed opencode."
else
  echo "opencode already installed."
fi
echo ""

# --- Ensure project venv exists (uv sync) ---
echo "Ensuring virtual environment (uv sync)..."
uv sync
echo ""

# --- Step 1: Gmail auth ---
echo "--- Step 1/7: Gmail Authentication ---"
if [[ -f "$SECRETS_DIR/gws-credentials.json" ]]; then
  echo "Found existing $SECRETS_DIR/gws-credentials.json — skipping."
else
  echo "This will open a browser for Google OAuth consent."
  echo "We use the Gmail scope only (-s gmail): read mail, attachments, and modify/create labels."
  echo "No Drive/Calendar/Sheets; this also helps with unverified app scope limits."
  echo ""
  echo "If you get '403 access_denied': add your Gmail as a Test user in GCP:"
  echo "  OAuth consent screen → Test users → Add users → your-email@gmail.com"
  echo ""
  echo "If you haven't set up a GCP project yet, run: gws auth setup"
  read -rp "Press Enter to start Gmail login (or Ctrl-C to abort)..."
  gws auth login -s gmail
  echo "Exporting credentials..."
  gws auth export --unmasked > "$SECRETS_DIR/gws-credentials.json"
  echo "Saved to $SECRETS_DIR/gws-credentials.json"
fi
echo ""

# --- Step 2: NotebookLM auth ---
echo "--- Step 2/7: NotebookLM Authentication ---"
if [[ -d "$SECRETS_DIR/notebooklm-credentials" ]]; then
  echo "Found existing $SECRETS_DIR/notebooklm-credentials/ — skipping."
else
  echo "This will open a browser for Google login."
  read -rp "Press Enter to start NotebookLM login (or Ctrl-C to abort)..."
  uv run notebooklm login
  if [[ -d "$HOME/.notebooklm" ]]; then
    cp -r "$HOME/.notebooklm" "$SECRETS_DIR/notebooklm-credentials"
    echo "Copied credentials to $SECRETS_DIR/notebooklm-credentials/"
  else
    echo "WARNING: Could not find ~/.notebooklm/ — you may need to copy credentials manually."
  fi
fi
echo ""

# --- Step 3: Google Generative AI API key (for opencode) ---
echo "--- Step 3/7: Google Generative AI API Key (opencode) ---"
if [[ -f "$SECRETS_DIR/google-generative-ai-api-key" ]]; then
  echo "Found existing $SECRETS_DIR/google-generative-ai-api-key — skipping."
else
  echo "Get your key at https://aistudio.google.com/apikey"
  read -rp "Paste your Google Generative AI API key: " api_key
  echo -n "$api_key" > "$SECRETS_DIR/google-generative-ai-api-key"
  echo "Saved to $SECRETS_DIR/google-generative-ai-api-key"
fi
echo ""

# --- Step 4: NotebookLM notebook ID ---
echo "--- Step 4/7: NotebookLM Notebook ID ---"
if [[ -f "$SECRETS_DIR/notebooklm-notebook-id" ]]; then
  echo "Found existing $SECRETS_DIR/notebooklm-notebook-id — skipping."
else
  echo "Open your notebook in NotebookLM and copy the ID from the URL."
  read -rp "Paste your NotebookLM notebook ID: " nb_id
  echo -n "$nb_id" > "$SECRETS_DIR/notebooklm-notebook-id"
  echo "Saved to $SECRETS_DIR/notebooklm-notebook-id"
fi
echo ""

# --- Step 5 (optional): Langfuse observability ---
echo "--- Step 5/7: Langfuse Observability (optional) ---"
if [[ -f "$SECRETS_DIR/langfuse-public-key" && -f "$SECRETS_DIR/langfuse-secret-key" ]]; then
  echo "Found existing Langfuse keys — skipping."
else
  echo "Langfuse provides LLM observability for OpenCode sessions."
  echo "Sign up at https://cloud.langfuse.com and go to Settings → API Keys."
  read -rp "Set up Langfuse? [y/N] " langfuse_confirm
  if [[ "$langfuse_confirm" == "y" || "$langfuse_confirm" == "Y" ]]; then
    read -rp "Paste your Langfuse public key (pk-lf-...): " lf_public
    echo -n "$lf_public" > "$SECRETS_DIR/langfuse-public-key"
    echo "Saved to $SECRETS_DIR/langfuse-public-key"
    read -rp "Paste your Langfuse secret key (sk-lf-...): " lf_secret
    echo -n "$lf_secret" > "$SECRETS_DIR/langfuse-secret-key"
    echo "Saved to $SECRETS_DIR/langfuse-secret-key"
  else
    echo "Skipped — you can set this up later by adding keys to .secrets/"
  fi
fi
echo ""

# --- Step 6: Upload secrets to GitHub ---
echo "--- Step 6/7: Upload Secrets to GitHub ---"
bash scripts/upload-secrets.sh
echo ""

# --- Step 7: Create Gmail "processed" label ---
echo "--- Step 7/7: Create Gmail 'processed' Label ---"
echo "Creating label (will fail harmlessly if it already exists)..."
gws gmail users labels create \
  --params '{"userId": "me"}' \
  --json '{"name": "processed", "labelListVisibility": "labelShow", "messageListVisibility": "show"}' \
  2>/dev/null || echo "Label may already exist — continuing."
echo ""

echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Push to GitHub:  git push -u origin main"
echo "  2. Go to https://github.com/raziele/requests-buddy/actions"
echo "  3. Trigger a manual run to verify headless operation."
