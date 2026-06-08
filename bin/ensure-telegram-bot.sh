#!/usr/bin/env bash
# Idempotent launcher: start the Telegram bot if it isn't already running.
# Intended for @reboot cron or manual use.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p tmp/logs
if pgrep -f 'python -m scripts telegram-bot' >/dev/null; then
  exit 0
fi
nohup ./bin/run-telegram-bot.sh >/dev/null 2>&1 &
