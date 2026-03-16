#!/bin/bash

# =============================================================================
# HTML Email Sender using Google Workspace CLI
# Usage: ./send_email.sh recipient@example.com [subject]
# =============================================================================

set -e

# Configuration
TEMPLATE_FILE="${TEMPLATE_FILE:-./email_template.html}"
DEFAULT_SUBJECT="תודה על תרומתכם למאגר בקשות החירום"
FROM_EMAIL="${FROM_EMAIL:-me}"  # "me" uses authenticated user

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# -----------------------------------------------------------------------------
# Functions
# -----------------------------------------------------------------------------

usage() {
    echo "Usage: $0 <recipient_email> [subject]"
    echo ""
    echo "Environment variables:"
    echo "  TEMPLATE_FILE  - Path to HTML template (default: ./email_template.html)"
    echo "  FROM_EMAIL     - Sender email or 'me' for authenticated user (default: me)"
    echo ""
    echo "Examples:"
    echo "  $0 user@example.com"
    echo "  $0 user@example.com 'Custom Subject Line'"
    echo "  TEMPLATE_FILE=./custom.html $0 user@example.com"
    exit 1
}

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Base64url encode (Gmail API requires base64url, not standard base64)
base64url_encode() {
    base64 -w 0 | tr '+/' '-_' | tr -d '='
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

# Check arguments
if [[ $# -lt 1 ]]; then
    usage
fi

TO_EMAIL="$1"
SUBJECT="${2:-$DEFAULT_SUBJECT}"

# Validate email format (basic check)
if [[ ! "$TO_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
    log_error "Invalid email format: $TO_EMAIL"
    exit 1
fi

# Check template file exists
if [[ ! -f "$TEMPLATE_FILE" ]]; then
    log_error "Template file not found: $TEMPLATE_FILE"
    exit 1
fi

# Check gws is installed
if ! command -v gws &> /dev/null; then
    log_error "gws CLI not found. Install with: npm install -g @googleworkspace/cli"
    exit 1
fi

log_info "Preparing email..."
log_info "  To: $TO_EMAIL"
log_info "  Subject: $SUBJECT"
log_info "  Template: $TEMPLATE_FILE"

# Read HTML content
HTML_CONTENT=$(cat "$TEMPLATE_FILE")

# Build RFC 2822 MIME message
# Using a heredoc to construct the message with proper headers
MIME_MESSAGE=$(cat <<EOF
To: ${TO_EMAIL}
From: ${FROM_EMAIL}
Subject: =?UTF-8?B?$(echo -n "$SUBJECT" | base64 -w 0)?=
MIME-Version: 1.0
Content-Type: text/html; charset="UTF-8"
Content-Transfer-Encoding: base64

$(echo -n "$HTML_CONTENT" | base64 -w 76)
EOF
)

# Base64url encode the entire message for Gmail API
RAW_MESSAGE=$(echo -n "$MIME_MESSAGE" | base64url_encode)

# Create JSON payload
JSON_PAYLOAD=$(jq -n --arg raw "$RAW_MESSAGE" '{"raw": $raw}')

log_info "Sending email..."

# Send via gws (userId required for Gmail API path)
RESPONSE=$(gws gmail users messages send --params '{"userId": "me"}' --json "$JSON_PAYLOAD" 2>&1)

# Check response
if echo "$RESPONSE" | jq -e '.id' > /dev/null 2>&1; then
    MESSAGE_ID=$(echo "$RESPONSE" | jq -r '.id')
    log_info "Email sent successfully!"
    log_info "  Message ID: $MESSAGE_ID"
else
    log_error "Failed to send email"
    echo "$RESPONSE"
    exit 1
fi
