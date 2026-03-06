#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

SECRETS_DIR=".secrets"
mkdir -p "$SECRETS_DIR"

echo "============================================"
echo "  Requests Buddy — First-Time Setup"
echo "============================================"
echo ""

# --- Prerequisites check ---
for cmd in gws notebooklm gh git; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: '$cmd' is not installed. See README.md for prerequisites."
    exit 1
  fi
done

# --- Step 1: Gmail auth ---
echo "--- Step 1/6: Gmail Authentication ---"
if [[ -f "$SECRETS_DIR/gws-credentials.json" ]]; then
  echo "Found existing $SECRETS_DIR/gws-credentials.json — skipping."
else
  echo "This will open a browser for Google OAuth consent."
  echo "If you haven't set up a GCP project yet, run: gws auth setup"
  read -rp "Press Enter to start Gmail login (or Ctrl-C to abort)..."
  gws auth login -s gmail
  echo "Exporting credentials..."
  gws auth export --unmasked > "$SECRETS_DIR/gws-credentials.json"
  echo "Saved to $SECRETS_DIR/gws-credentials.json"
fi
echo ""

# --- Step 2: NotebookLM auth ---
echo "--- Step 2/6: NotebookLM Authentication ---"
if [[ -d "$SECRETS_DIR/notebooklm-credentials" ]]; then
  echo "Found existing $SECRETS_DIR/notebooklm-credentials/ — skipping."
else
  echo "This will open a browser for Google login."
  read -rp "Press Enter to start NotebookLM login (or Ctrl-C to abort)..."
  notebooklm login
  if [[ -d "$HOME/.notebooklm" ]]; then
    cp -r "$HOME/.notebooklm" "$SECRETS_DIR/notebooklm-credentials"
    echo "Copied credentials to $SECRETS_DIR/notebooklm-credentials/"
  else
    echo "WARNING: Could not find ~/.notebooklm/ — you may need to copy credentials manually."
  fi
fi
echo ""

# --- Step 3: OpenRouter API key ---
echo "--- Step 3/6: OpenRouter API Key ---"
if [[ -f "$SECRETS_DIR/openrouter-api-key" ]]; then
  echo "Found existing $SECRETS_DIR/openrouter-api-key — skipping."
else
  echo "Get your key at https://openrouter.ai/keys"
  read -rp "Paste your OpenRouter API key: " api_key
  echo -n "$api_key" > "$SECRETS_DIR/openrouter-api-key"
  echo "Saved to $SECRETS_DIR/openrouter-api-key"
fi
echo ""

# --- Step 4: NotebookLM notebook ID ---
echo "--- Step 4/6: NotebookLM Notebook ID ---"
if [[ -f "$SECRETS_DIR/notebooklm-notebook-id" ]]; then
  echo "Found existing $SECRETS_DIR/notebooklm-notebook-id — skipping."
else
  echo "Open your notebook in NotebookLM and copy the ID from the URL."
  read -rp "Paste your NotebookLM notebook ID: " nb_id
  echo -n "$nb_id" > "$SECRETS_DIR/notebooklm-notebook-id"
  echo "Saved to $SECRETS_DIR/notebooklm-notebook-id"
fi
echo ""

# --- Step 5: Upload secrets to GitHub ---
echo "--- Step 5/6: Upload Secrets to GitHub ---"
bash scripts/upload-secrets.sh
echo ""

# --- Step 6: Create Gmail "processed" label ---
echo "--- Step 6/6: Create Gmail 'processed' Label ---"
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
