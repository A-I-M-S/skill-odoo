#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p tmp/logs
exec ./skill-odoo telegram-bot >> tmp/logs/telegram-bot.log 2>&1
