---
name: skill-odoo
description: OpenClaw skill: Odoo bridge (read/write moves, partners, COA, attachments) and agent-first receipt pipeline.
metadata: {"openclaw":{"requires":{"bins":["python3"],"env":["ODOO_URL","ODOO_DB","ODOO_LOGIN","ODOO_API_KEY"]}}}
---

# skill-odoo

OpenClaw skill that talks to an Odoo instance and processes receipts
end-to-end. Driven by the OpenClaw agent via `{baseDir}/bin/odoo <subcommand>
[args]`. Every subcommand prints JSON to stdout; errors return
`{"ok": false, "error": "...", "code": <int>}` and exit non-zero.

## Decision table

| If you want to … | Run … |
|---|---|
| Verify Odoo credentials | `bin/odoo probe` |
| Get a list of expense accounts | `bin/odoo chart-of-accounts --code-prefix 6` |
| Get a list of accounts by type | `bin/odoo chart-of-accounts --type expense` |
| Fetch a single move by id | `bin/odoo get-move --id 1234` |
| Fetch a single move by ref | `bin/odoo get-move --ref 26May` |
| List draft moves for this month | `bin/odoo list-drafts` |
| List draft moves in a date range | `bin/odoo list-drafts --date-from 2026-06-01 --date-to 2026-06-30` |
| List customer invoices | `bin/odoo list-invoices` |
| List vendor bills | `bin/odoo list-bills` |
| Find a partner by name | `bin/odoo list-partners --name-contains acme` |
| Query any Odoo model | `bin/odoo search-read --model X --domain '…' [--fields …]` |
| Create a vendor bill | `bin/odoo create-bill --partner-name "Acme" --invoice-date 2026-06-15 --lines '…'` |
| Post a draft move | `bin/odoo post-move --id 1234` |
| Reset a posted move to draft | `bin/odoo cancel-move --id 1234` |
| Upload a file to a record | `bin/odoo attach-file --model X --id 42 --file-path /path/to/file` |
| Process a receipt end-to-end | `bin/odoo process-receipt --file-path /path/to/receipt.pdf [--text "agent-OCR text"]` |
| Inspect / rebuild the Odoo cache | `bin/odoo cache show\|refresh\|clear` |
| Run the OCR router standalone | `bin/odoo _ocr-test /path/to/file` |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.sample .env
$EDITOR .env             # fill in ODOO_URL, ODOO_DB, ODOO_LOGIN, ODOO_API_KEY
./bin/odoo probe         # verify connectivity
./bin/odoo --help        # tool list
```

## OCR routing

The agent's own OCR is the primary path. If the agent can read the receipt
itself, pass the extracted text to `process-receipt --text "<text>"` together
with `--file-path <path>` for the attachment. When `--text` is absent, the
in-skill chain runs in this order:

1. `pdfplumber` (PDF text layer) — free, no network
2. Gemma via OpenRouter (vision LLM) — costs a few API cents
3. `pytesseract` (local OCR) — offline fallback

The winning path is recorded in the audit log as `agent` / `plumber` /
`openai` / `tesseract` / `failed`. When the agent's text is supplied via
`--text`, the in-skill chain is NOT called and `ocr_source: agent` is logged.

## Output contract

- **Success:** `{"ok": true, ...}` to stdout, exit 0
- **Failure:** `{"ok": false, "error": "<message>", "code": <int>}` to stdout, exit = `code`
- **Exit codes:**
  - `0` — ok
  - `2` — bad args / missing input
  - `3` — upstream (Odoo / LLM / OCR / FX) error
  - `4` — file not found / not_found
  - `5` — auth / missing env

For `process-receipt` specifically, exit codes are:
- `0` — receipt posted to Odoo
- `3` — receipt-level failure (Odoo / FX / classify error)
- `4` — missing file or unsupported type
- `5` — missing env

## Tool reference

Each subcommand returns a JSON object. Full output shapes are in
`references/odoo-tools.md`. The cheat sheet:

| Subcommand | Read/Write | Key flags |
|---|---|---|
| `probe` | R | `--refresh-cache` |
| `chart-of-accounts` | R | `--code-prefix`, `--type`, `--limit` |
| `get-move` | R | `--id` XOR `--ref` |
| `list-drafts` | R | `--ref`, `--date-from`, `--date-to`, `--journal-code`, `--limit` |
| `list-invoices` | R | `--partner-id`, `--state`, `--date-from`, `--date-to`, `--limit` |
| `list-bills` | R | `--partner-id`, `--state`, `--date-from`, `--date-to`, `--limit` |
| `list-partners` | R | `--name-contains`, `--limit` |
| `search-read` | R | `--model`, `--domain` (JSON), `--fields` (CSV), `--limit` |
| `create-bill` | W | `--partner-name`, `--invoice-date`, `--lines` (JSON), `--ref`, `--currency` |
| `post-move` | W | `--id` |
| `cancel-move` | W | `--id` |
| `attach-file` | W | `--model`, `--id`, `--file-path`, `--name` |
| `process-receipt` | W | `--file-path`, `--text` |
| `cache show\|refresh\|clear` | R | (cache_action) |
| `_ocr-test` | R (internal) | `<file_path>`, `--text` |

### Read tool highlights

- **`chart-of-accounts`** uses the local cache when called with no filter
  (fast path, same as `probe`). With a filter, it goes straight to Odoo.
- **`list-drafts`** defaults to "this month" when no date filter is given
  (the most common request). Pass `--date-from` / `--date-to` to override.
- **`get-move`** requires **exactly one** of `--id` or `--ref`. Mutually
  exclusive — argparse enforces this.
- **`search-read`** is the **unrestricted** escape hatch (per Aloy, issue #5).
  The agent is responsible for not reading sensitive models unless
  intentional. Every call is audit-logged with a redacted domain summary
  (sensitive-shaped values masked to `***`).

### Write tool highlights

- **`create-bill`** uses **exact-match** partner resolution. If the partner
  isn't found, the tool returns `{ok: false, code: 2, suggestions: [...]}` —
  no silent creation. Use `list-partners --name-contains …` to find the
  correct name.
- **`create-bill --currency USD`** creates a vendor bill in a foreign
  currency. The receipt pipeline applies live FX conversion (Frankfurter) at
  the transaction date; the `create-bill` tool alone stores the bill in the
  foreign currency as-is.
- **`post-move`** calls `action_post` (Odoo 17+). If the move is already
  posted, returns `{ok: true, move, note: "already posted"}` without error.
- **`cancel-move`** calls `action_draft` (NOT `button_cancel`). The move
  state reverts to `draft`; the journal entry is **not** reversed. If the
  move is already in draft, returns `{ok: true, move, note: "already draft"}`.
- **`attach-file`** validates the file (exists, non-empty, ≤25 MB) and
  returns the SHA-256 checksum alongside the Odoo attachment id.
- **`process-receipt`** is the composite. It does **not** post the move to
  Odoo — entries always stay `draft` so a human reviews and posts. Call
  `post-move --id <move_id>` after review.

## Security

- **No secrets in audit log.** The audit module strips known secret-shaped
  KEY names (`ODOO_API_KEY`, `AI_SECRET`, `OCR_API_KEY`, `TELEGRAM_*`,
  `*_API_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`) from every JSONL line.
  `search-read` also redacts sensitive-shaped values inside the domain.
- **Unrestricted `search-read`.** Per Aloy's decision (issue #5), the
  `search-read` subcommand can read any Odoo model. The agent is responsible
  for not reading sensitive models unless intentional. All calls are
  audit-logged.
- **Trusted writes.** Per Aloy (issue #6), write operations
  (`create-bill`, `post-move`, `cancel-move`, `attach-file`, `process-receipt`)
  are trusted — no human-confirm step. Every write is audit-logged.
- **`.env` is gitignored.** Credentials live in `.env` (not committed).
  `bin/odoo` sources `.env` automatically.

## Caveats

- Entries always stay `draft`. After `process-receipt` or `create-bill`,
  the move must be reviewed in Odoo UI and posted via `post-move --id <id>`
  or the Odoo "Confirm" button.
- The receipt pipeline expects a single-receipt file. Multi-receipt PDFs
  are not supported — split them upstream.
- `cancel-move` reverts a posted move to `draft`. It does **not** cancel
  (void) the move. To void a posted move, use Odoo UI's "Cancel" button
  (which calls `button_cancel` and reverses the journal entry).
- The receipt pipeline deletes the local file on success. Failed files
  are moved to `<inbox>/../failed_receipts/<ts>-<name>.<error.txt>` so
  the agent can inspect the failure.

## Not here (dropped from the original standalone version)

- No Telegram bot
- No PM2 / `ecosystem.config.js`
- No `install.sh` system installer
- No long-polling — the OpenClaw agent drives the skill
- No folder-based inbox watcher — the agent calls `process-receipt` per file

## Status

Feature-complete. Every subcommand is real (no stubs). 14 subcommands + 1
internal helper (`_ocr-test`).

| # | Subcommand | Implemented in |
|---|---|---|
| 1 | `probe` | issue #5 |
| 2 | `chart-of-accounts` | issue #6 |
| 3 | `get-move` | issue #6 |
| 4 | `list-drafts` | issue #6 |
| 5 | `list-invoices` | issue #7 |
| 6 | `list-bills` | issue #7 |
| 7 | `list-partners` | issue #7 |
| 8 | `search-read` | issue #7 |
| 9 | `create-bill` | issue #8 |
| 10 | `post-move` | issue #8 |
| 11 | `cancel-move` | issue #8 |
| 12 | `attach-file` | issue #9 |
| 13 | `process-receipt` | issue #12 |
| 14 | `cache show\|refresh\|clear` | issue #5 |
| 15 | `_ocr-test` (internal) | issue #10 |
