#!/usr/bin/env bash
# One-shot installer for skill-odoo.
#   - installs system OCR tools (tesseract, poppler) via apt when missing
#   - creates a venv and installs Python deps
#   - creates the local tmp/ runtime tree
#   - copies .env.sample -> .env if .env doesn't exist
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

need_bin() { command -v "$1" >/dev/null 2>&1; }

# 1. System OCR dependencies (only needed for the local tesseract fallback).
if ! need_bin tesseract || ! need_bin pdftoppm; then
  if need_bin apt-get; then
    SUDO=""
    [ "$(id -u)" -ne 0 ] && SUDO="sudo"
    $SUDO apt-get update -y
    $SUDO apt-get install -y tesseract-ocr poppler-utils
  else
    echo "WARN: install 'tesseract' and 'poppler' (pdftoppm) manually" >&2
  fi
fi

# 2. Python virtualenv + deps.
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# 3. Runtime data tree (git-ignored).
mkdir -p tmp/incoming_receipts tmp/failed_receipts tmp/audit_logs tmp/logs

# 4. Config.
[ -f .env ] || cp .env.sample .env

chmod +x ./skill-odoo

cat <<'EOF'

Setup complete. Next steps:
  1) edit .env                          # Odoo + AI credentials
  2) ./skill-odoo probe                 # verify Odoo connectivity
  3) drop receipts into tmp/incoming_receipts/ and run ./skill-odoo run

To run the Telegram bot under PM2 (recommended):
  pm2 start ecosystem.config.js
  pm2 save
  pm2 logs skill-odoo-bot
EOF
