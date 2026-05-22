# skill-odoo — AI receipt → Odoo journal entry pipeline

Folder-based pipeline that turns receipts (PDF, image, or scanned-PDF) into a
single balanced monthly draft entry in Odoo, with the original file attached
and the local copy deleted on success.

## What it does

1. Watches a folder (default `./incoming_receipts/`).
2. OCR / PDF-text extracts each file.
3. Classifies it against your live Odoo Chart of Accounts using an
   OpenAI-compatible LLM (default: Gemma via OpenRouter / Google AI Studio).
4. Finds the current month's draft `account.move` in the configured journal
   (or adopts an existing empty draft, or creates one). The move is named and
   referenced like `26May`.
5. Appends a single debit line per receipt. Currency ≠ SGD is auto-converted
   to SGD via [frankfurter.dev](https://frankfurter.dev) (no API key needed).
6. Recomputes a single credit line to **202040 Shareholder Notes Payable** so
   the move stays balanced regardless of how many receipts are added.
7. Attaches the receipt file to the journal entry (`ir.attachment`).
8. Deletes the local file on success.
9. **Leaves the entry as draft** — you review and post manually in Odoo.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install pdfplumber pdf2image pytesseract Pillow requests
# system: tesseract-ocr + poppler-utils
cp .env.sample .env  # then fill in values
```

In Odoo: **Preferences → Account Security → API Keys → New** to generate the
key used as `ODOO_API_KEY`.

## Usage

```bash
# Read-only check: auth, journal, shareholder account
python -m openclaw_bot_cli probe

# Dry-run: list inbox files, show what would happen, no writes
python -m openclaw_bot_cli run --dry-run

# Real run: OCR + classify + post draft + attach + delete locals
python -m openclaw_bot_cli run --output ./run.json
```

## Configuration (`.env`)

| Key | Meaning |
| --- | --- |
| `ODOO_URL` / `ODOO_DB` / `ODOO_LOGIN` / `ODOO_API_KEY` | Odoo connection |
| `SHAREHOLDER_ACCOUNT_CODE` | Credit-side account (default `202040`) |
| `JOURNAL_CODE` / `JOURNAL_TYPE` | Target journal (default `MISC` / `general`) |
| `MOVE_REF_FORMAT` | strftime format for the move ref (default `%y%b` → `26May`) |
| `MOVE_NAME_FROM_REF` | also set `account.move.name` to the ref |
| `MONTHLY_CONSOLIDATE` | reuse the month's draft instead of creating new ones |
| `AUTO_POST` | always `false` — entries stay draft until you post in Odoo |
| `DEFAULT_CURRENCY` / `FX_BASE_CURRENCY` | book currency (default `SGD`) |
| `FX_PROVIDER` | `frankfurter` (free, no key) |
| `AI_CHAT_URL` / `AI_MODEL` / `AI_SECRET` | OpenAI-compatible chat endpoint |
| `AI_PROVIDER_ORDER` | OpenRouter provider routing (e.g. `google-ai-studio`) |
| `RECEIPTS_INBOX` | Folder to scan (default `./incoming_receipts`) |
| `RECEIPTS_PROCESSED_DELETE` | Delete local file after successful posting |

## Accounting model

Every receipt becomes:

| | Debit | Credit |
| --- | --- | --- |
| Expense / asset account chosen by the LLM | gross SGD | — |
| `202040 Shareholder Notes Payable` | — | gross SGD |

Per month, **one** `account.move` accumulates all debit lines plus one credit
line summing to the total — perfectly balanced, ready for one-click posting.

## Module layout

```
openclaw_bot_cli/
├── cli.py              # entrypoints: probe, run
├── config.py           # .env loader + Settings dataclass
├── extraction.py       # OCR / PDF-text extraction
├── ai_automation.py    # LLM classifier (OpenAI-compatible)
├── fx.py               # Frankfurter live FX → SGD
├── odoo_client.py      # XML-RPC wrapper
├── monthly_journal.py  # Find / adopt / balance the month's draft entry
├── processor.py        # End-to-end orchestration
└── models.py           # Dataclasses
```


## Telegram bot

The Telegram bot is a private long-polling ingestor for receipts.

1. Create a bot with BotFather and set `TELEGRAM_BOT_TOKEN` in `.env`.
2. Start it once with no allowed users:
   ```bash
   ./skill-odoo telegram-bot
   ```
3. Send `/start` to the bot; it will reply with your numeric Telegram user ID.
4. Add that ID to `.env`:
   ```bash
   TELEGRAM_ALLOWED_USER_IDS=123456789
   ```
5. Run it permanently as a background service/process.

Usage after setup: send the bot a receipt photo, image file, or PDF. It saves the
file into `RECEIPTS_INBOX`, runs the normal Odoo receipt pipeline immediately,
and replies with the Odoo draft/ref, total, debit account, and attachment id.


## Audit logs

Each receipt run writes structured JSONL records to `./audit_logs/YYYY-MM.jsonl`
(or `AUDIT_LOG_DIR`). Logs include the filename, OCR text, model/provider,
LLM raw response, parsed extraction, success/failure stage, and Odoo attachment
metadata. API keys and binary files are not logged.

Useful diagnostics:

```bash
tail -n 20 audit_logs/$(date +%Y-%m).jsonl | jq .
```

If OCR fails or the model returns amount `0`, the receipt is moved to
`./failed_receipts/` with a `.error.txt` sidecar instead of being uploaded or
deleted.
