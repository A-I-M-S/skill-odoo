---
name: skill-odoo
description: OpenClaw skill: Odoo bridge (read/write moves, partners, COA, attachments) and agent-first receipt pipeline.
metadata: {"openclaw":{"requires":{"bins":["python3"],"env":["ODOO_URL","ODOO_DB","ODOO_LOGIN","ODOO_API_KEY"]}}}
---

# skill-odoo

OpenClaw skill for an Odoo instance. Driven by the OpenClaw agent via
`{baseDir}/bin/odoo <subcommand> [args]`. Every subcommand prints JSON to
stdout; errors return `{"ok": false, "error": "...", "code": <int>}` and exit
non-zero. No plaintext output. The full body for every subcommand lives in
issue #13 — this is the bootstrap; the dispatch table below is the contract
the agent can rely on for routing decisions.

## OCR routing rule

The agent's own OCR is the primary path. If the agent can read the receipt
itself, it should pass the extracted text to `process-receipt --text "<text>"`
along with `--file-path <path>` for the attachment. When `--text` is absent,
the in-skill chain runs in this order: `pdfplumber` (PDF text layer) →
`pytesseract` (local OCR) → Gemma via OpenRouter (vision LLM). The winning
path is recorded in the audit log.

## Tools (subcommand → purpose)

| Subcommand | Purpose |
|---|---|
| `probe` | Verify Odoo credentials; return user, company, journal, shareholder account, cache status |
| `chart-of-accounts` | List accounts (filter by code prefix or account type) |
| `get-move` | Fetch a single `account.move` by id or ref, with its lines |
| `list-drafts` | List draft `account.move`s (this month, by ref, by date range) |
| `list-invoices` | List customer invoices |
| `list-bills` | List vendor bills |
| `list-partners` | Search partners by name |
| `search-read` | Generic Odoo model query (escape hatch, audit-logged) |
| `create-bill` | Create a vendor bill (one or more lines) |
| `post-move` | Confirm (post) a draft `account.move` |
| `cancel-move` | Reset a posted `account.move` back to draft |
| `attach-file` | Upload an `ir.attachment` to any Odoo record |
| `process-receipt` | Composite: OCR → classify → FX → monthly draft → attach → delete |
| `cache show\|refresh\|clear` | Inspect / rebuild the Odoo lookup cache |

## Output contract

- Success: `{"ok": true, ...}` to stdout, exit 0
- Failure: `{"ok": false, "error": "<message>", "code": <int>}` to stdout, exit = code
- `code`: 0 = ok, 2 = bad args / missing input, 3 = upstream (Odoo) error, 4 = file not found, 5 = auth failed, 501 = not implemented (this issue returns 501 for all subcommands)

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.sample .env
$EDITOR .env             # fill in ODOO_URL, ODOO_DB, ODOO_LOGIN, ODOO_API_KEY
./bin/odoo probe         # verify connectivity
./bin/odoo --help        # tool list
```

## Not here

- No Telegram bot (dropped)
- No PM2 / ecosystem.config.js (dropped)
- No `install.sh` system installer (dropped)
- No long-polling — the OpenClaw agent drives the skill

## Status

Bootstrap complete. See A-I-M-S/skill-odoo#4 onwards for the implementation
of each subcommand.
