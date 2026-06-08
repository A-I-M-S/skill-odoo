# skill-odoo — AI receipt → Odoo journal-entry pipeline

Turn receipts (PDF, image, or scanned-PDF) into a single **balanced monthly
draft entry** in Odoo, with the original file attached and the local copy
removed on success. Entries always stay **draft** — you review and post in Odoo.

## What it does

1. Watches a folder (default `./tmp/incoming_receipts/`).
2. Extracts text: PDF text layer via `pdfplumber`; otherwise the page image is
   **uploaded to a vision LLM** (Gemma via OpenRouter by default) which reads
   the receipt. Local Tesseract is only an offline fallback.
3. Classifies the receipt against your live Odoo Chart of Accounts using an
   OpenAI-compatible LLM.
4. Finds the current month's draft `account.move` in the configured journal
   (or adopts an empty draft, or creates one), named/referenced like `26May`.
5. Appends one debit line per receipt. Non-SGD amounts are auto-converted via
   [frankfurter.dev](https://frankfurter.dev) (no API key needed).
6. Recomputes a single credit line to **202040 Shareholder Notes Payable** so
   the move stays balanced no matter how many receipts are added.
7. Attaches the receipt file to the entry (`ir.attachment`).
8. Deletes the local file on success.

## Install

```bash
git clone git@github.com:A-I-M-S/skill-odoo.git
cd skill-odoo
./install.sh          # system OCR deps + venv + tmp/ tree + .env
$EDITOR .env          # fill in Odoo + AI credentials
./skill-odoo probe    # read-only connectivity check
```

In Odoo, generate the API key under **Preferences → Account Security →
API Keys → New** and use it as `ODOO_API_KEY`.

## Usage

```bash
./skill-odoo probe              # auth + journal + shareholder account (read-only)
./skill-odoo run --dry-run      # list inbox, show plan, no writes
./skill-odoo run --output run.json   # OCR + classify + draft + attach + delete
./skill-odoo coa show           # inspect cached Chart of Accounts
./skill-odoo cache refresh      # force-refresh all cached Odoo lookups
```

Drop receipts into `tmp/incoming_receipts/` before running `run`.

## Configuration (`.env`)

| Key | Meaning |
| --- | --- |
| `ODOO_URL` / `ODOO_DB` / `ODOO_LOGIN` / `ODOO_API_KEY` | Odoo connection |
| `SHAREHOLDER_ACCOUNT_CODE` | Credit-side account (default `202040`) |
| `JOURNAL_CODE` / `JOURNAL_TYPE` | Target journal (default `MISC` / `general`) |
| `MOVE_REF_FORMAT` | strftime format for the move ref (default `%y%b` → `26May`) |
| `MOVE_NAME_FROM_REF` | also set `account.move.name` to the ref |
| `MONTHLY_CONSOLIDATE` | reuse the month's draft instead of creating new ones |
| `DEFAULT_CURRENCY` / `FX_BASE_CURRENCY` | book currency (default `SGD`) |
| `FX_PROVIDER` | `frankfurter` (free, no key) |
| `AI_CHAT_URL` / `AI_MODEL` / `AI_SECRET` | OpenAI-compatible classifier endpoint |
| `AI_PROVIDER_ORDER` | OpenRouter provider routing (e.g. `google-ai-studio`) |
| `OCR_PROVIDER` | `openai` (vision LLM, default) or `tesseract` (local) |
| `OCR_BASE_URL` / `OCR_MODEL` / `OCR_API_KEY` | OpenAI-compatible vision OCR |
| `RECEIPTS_INBOX` | Folder to scan (default `./tmp/incoming_receipts`) |
| `RECEIPTS_PROCESSED_DELETE` | Delete local file after successful posting |
| `AUDIT_LOG_DIR` | JSONL audit logs (default `./tmp/audit_logs`) |

## Accounting model

Every receipt becomes:

| | Debit | Credit |
| --- | --- | --- |
| Expense / asset account chosen by the LLM | gross SGD | — |
| `202040 Shareholder Notes Payable` | — | gross SGD |

Per month, **one** `account.move` accumulates all debit lines plus a single
balancing credit line — ready for one-click posting.

## Project layout

```
skill-odoo/
├── skill-odoo            # launcher (auto-bootstraps venv, runs python -m scripts)
├── install.sh            # one-shot installer
├── ecosystem.config.js   # PM2 process definition for the Telegram bot
├── requirements.txt
├── .env.sample
├── scripts/              # the Python package (python -m scripts <command>)
│   ├── __main__.py       # module entrypoint
│   ├── cli.py            # commands: probe, run, coa, cache, telegram-bot
│   ├── config.py         # .env loader + Settings
│   ├── extraction.py     # PDF-text / OCR extraction
│   ├── openai_ocr.py     # vision-LLM OCR (uploads the image)
│   ├── ai_automation.py  # LLM classifier
│   ├── fx.py             # Frankfurter FX → SGD
│   ├── odoo_client.py    # XML-RPC wrapper
│   ├── odoo_cache.py     # local cache for near-static Odoo lookups
│   ├── monthly_journal.py# find / adopt / balance the month's draft
│   ├── processor.py      # end-to-end orchestration
│   ├── models.py         # dataclasses
│   └── telegram_bot.py   # Telegram ingestion bot
└── tmp/                  # git-ignored runtime data (created by install.sh)
    ├── incoming_receipts/
    ├── failed_receipts/
    ├── audit_logs/
    └── logs/
```

## OCR providers

| `OCR_PROVIDER` | Backend | Use when |
| --- | --- | --- |
| `openai` (default) | Uploads the image to any OpenAI-compatible vision endpoint (OpenRouter → Gemma by default) | Crumpled phone photos, low-light receipts, mixed English/Chinese |
| `tesseract` | Local `tesseract-ocr`, no upload | Offline / no-network fallback |

PDFs are tried as text first via `pdfplumber`; OCR only runs when the PDF is
image-only or the input is an image. If the vision API call fails, extraction
falls back to local Tesseract automatically.

The shipped default is OpenRouter + Gemma:

```ini
OCR_PROVIDER=openai
OCR_BASE_URL=https://openrouter.ai/api/v1
OCR_MODEL=google/gemma-4-26b-a4b-it:free
OCR_API_KEY=sk-or-v1-...
```

## Telegram bot

A private long-polling ingestor for receipts.

1. Create a bot with BotFather and set `TELEGRAM_BOT_TOKEN` in `.env`.
2. Start it once with no allowed users: `./skill-odoo telegram-bot`.
3. Send `/start`; the bot replies with your numeric Telegram user ID.
4. Add it to `.env`: `TELEGRAM_ALLOWED_USER_IDS=123456789`.
5. Run it permanently under PM2:

```bash
pm2 start ecosystem.config.js   # start the bot (run from the repo root)
pm2 save                        # persist the process list across reboots
pm2 startup                     # (one-time) print the systemd boot command to run

# monitoring
pm2 status                      # process table
pm2 logs skill-odoo-bot         # tail logs
pm2 restart skill-odoo-bot      # restart after a code change
pm2 stop skill-odoo-bot         # stop
```

Send the bot a receipt photo, image, or PDF; it saves the file into
`RECEIPTS_INBOX`, runs the pipeline immediately, and replies with the Odoo
draft ref, total, debit account, and attachment id. Logs go to
`tmp/logs/telegram-bot.log` (also viewable with `pm2 logs skill-odoo-bot`).

Commands: `/process` (process the inbox now), `/status` (inbox + config).

## Audit logs

Each run writes structured JSONL to `tmp/audit_logs/YYYY-MM.jsonl`: filename,
OCR text, model/provider, LLM raw response, parsed extraction, success/failure
stage, and Odoo attachment metadata. **API keys and binary files are never
logged.**

```bash
tail -n 20 tmp/audit_logs/$(date +%Y-%m).jsonl | jq .
```

If OCR fails or the model returns amount `0`, the receipt is moved to
`tmp/failed_receipts/` with a `.error.txt` sidecar instead of being uploaded.
