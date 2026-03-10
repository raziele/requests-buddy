#!/usr/bin/env bash
set -euo pipefail

REPO="raziele/requests-buddy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SECRETS_DIR=".secrets"

cd "$REPO_ROOT"

echo "=== Upload secrets to GitHub ==="
echo "Target repo: $REPO"
echo "Looking for .secrets in: $REPO_ROOT"
echo ""

missing=0
for f in gws-credentials.json openrouter-api-key notebooklm-notebook-id; do
  if [[ ! -f "$SECRETS_DIR/$f" ]]; then
    echo "ERROR: Missing $SECRETS_DIR/$f"
    missing=1
  fi
done
if [[ ! -f "$SECRETS_DIR/notebooklm-credentials/storage_state.json" ]]; then
  echo "ERROR: Missing $SECRETS_DIR/notebooklm-credentials/storage_state.json"
  missing=1
fi
if [[ $missing -eq 1 ]]; then
  echo ""
  echo "Run setup.sh from the requests-buddy repo to generate these files:"
  echo "  cd $REPO_ROOT && ./scripts/setup.sh"
  echo ""
  echo "If you already ran setup, check that the files are in $REPO_ROOT/.secrets/"
  exit 1
fi

read -rp "Upload secrets to $REPO? [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

echo ""
echo "Uploading GWS_CREDENTIALS..."
gh secret set GWS_CREDENTIALS -R "$REPO" < "$SECRETS_DIR/gws-credentials.json"

echo "Uploading OPENROUTER_API_KEY..."
gh secret set OPENROUTER_API_KEY -R "$REPO" < "$SECRETS_DIR/openrouter-api-key"

echo "Uploading NOTEBOOKLM_NOTEBOOK_ID..."
gh secret set NOTEBOOKLM_NOTEBOOK_ID -R "$REPO" < "$SECRETS_DIR/notebooklm-notebook-id"

echo "Uploading NOTEBOOKLM_CREDENTIALS (storage_state.json only)..."
base64 < "$SECRETS_DIR/notebooklm-credentials/storage_state.json" | \
  gh secret set NOTEBOOKLM_CREDENTIALS -R "$REPO"

if [[ -f "$SECRETS_DIR/langfuse-public-key" ]]; then
  echo "Uploading LANGFUSE_PUBLIC_KEY..."
  gh secret set LANGFUSE_PUBLIC_KEY -R "$REPO" < "$SECRETS_DIR/langfuse-public-key"
fi

if [[ -f "$SECRETS_DIR/langfuse-secret-key" ]]; then
  echo "Uploading LANGFUSE_SECRET_KEY..."
  gh secret set LANGFUSE_SECRET_KEY -R "$REPO" < "$SECRETS_DIR/langfuse-secret-key"
fi

echo ""
echo "All secrets uploaded to $REPO."
