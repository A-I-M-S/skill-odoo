#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec ./skill-odoo telegram-bot >> logs/telegram-bot.log 2>&1
