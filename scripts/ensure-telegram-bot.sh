#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
if pgrep -f 'python -m openclaw_bot_cli telegram-bot' >/dev/null; then
  exit 0
fi
nohup ./scripts/run-telegram-bot.sh >/dev/null 2>&1 &
