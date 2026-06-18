# skill-odoo

OpenClaw skill for an Odoo instance. Read and write journal entries, partners,
chart of accounts, attachments — and process a receipt end-to-end (OCR → LLM
classify → FX → monthly draft → attach → delete).

Driven by the OpenClaw agent via `bin/odoo <subcommand> [args]`. Every
subcommand prints JSON to stdout.

## Quick start

```bash
git clone https://github.com/A-I-M-S/skill-odoo
cd skill-odoo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.sample .env
$EDITOR .env               # fill in ODOO_URL, ODOO_DB, ODOO_LOGIN, ODOO_API_KEY
./bin/odoo probe           # verify connectivity
./bin/odoo --help          # tool list
```

## Tools

| Subcommand | Purpose |
|---|---|
| `probe` | Verify Odoo credentials; return user, company, journal, cache status |
| `chart-of-accounts` | List accounts (filter by code prefix or type) |
| `get-move` | Fetch a single `account.move` by id or ref, with its lines |
| `list-drafts` | List draft `account.move`s |
| `list-invoices` | List customer invoices |
| `list-bills` | List vendor bills |
| `list-partners` | Search partners by name |
| `search-read` | Generic Odoo model query (escape hatch, audit-logged) |
| `create-bill` | Create a vendor bill |
| `post-move` | Confirm (post) a draft `account.move` |
| `cancel-move` | Reset a posted `account.move` back to draft |
| `attach-file` | Upload an `ir.attachment` to any Odoo record |
| `process-receipt` | OCR → classify → FX → monthly draft → attach → delete |
| `cache show\|refresh\|clear` | Inspect / rebuild the Odoo lookup cache |

See `SKILL.md` for the full tool reference, the OCR routing rule, the output
contract, and the per-subcommand flag docs.

## OCR routing

The OpenClaw agent's own OCR is the primary path. If the agent can read the
receipt itself, it passes the extracted text to `process-receipt --text ...`
along with `--file-path ...` for the attachment. When `--text` is absent, the
in-skill chain runs: `pdfplumber` → `pytesseract` → Gemma via OpenRouter.
The winning path is recorded in the audit log.

## Not here

- No Telegram bot (dropped)
- No PM2 / `ecosystem.config.js` (dropped)
- No `install.sh` system installer (dropped)
- No long-polling — the OpenClaw agent drives the skill

## License

Same as the original `A-I-M-S/skill-odoo` project (see `LICENSE` if present,
otherwise follow the upstream repo).
