#!/usr/bin/env bash
# One-shot installer for a fresh clone of skill-odoo.
# - installs system OCR tools (tesseract, poppler) via apt if needed
# - creates a venv and installs Python deps
# - copies .env.sample -> .env if .env doesn't exist
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

need_bin() { command -v "$1" >/dev/null 2>&1; }

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

python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install -r requirements.txt

[ -f .env ] || cp .env.sample .env
[ -d incoming_receipts ] || mkdir -p incoming_receipts

chmod +x ./skill-odoo

echo
echo "Setup complete. Next steps:"
echo "  1) edit .env (Odoo + AI credentials)"
echo "  2) ./skill-odoo probe       # verify Odoo connectivity"
echo "  3) drop receipts into incoming_receipts/ and run ./skill-odoo run"
