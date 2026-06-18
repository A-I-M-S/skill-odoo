# Odoo tools reference

Per-subcommand output shapes, flag reference, and worked examples. The
contract lives in `SKILL.md`; this doc is the deep reference for each
subcommand.

## `probe`

Verify Odoo connectivity, return the near-static lookups (user, company
currency, journal, shareholder account, COA status), and report which
lookups came from cache vs fresh Odoo.

```bash
./bin/odoo probe
./bin/odoo probe --refresh-cache
```

Output:
```json
{
  "ok": true,
  "odoo": {
    "url": "https://yourcompany.odoo.com",
    "db": "yourcompany",
    "user": {"id": 7, "name": "Alice", "login": "alice@example.com", "tz": "Asia/Singapore"},
    "company": {"id": 42, "name": "Aloysius Pte Ltd", "currency": {"id": 1, "name": "SGD"}}
  },
  "journal": {"id": 9, "code": "MISC", "name": "Miscellaneous Operations", "type": "general"},
  "shareholder_account": {"id": 202, "code": "202040", "name": "Shareholder Notes Payable"},
  "coa": {"full_count": 87, "short_count": 23},
  "cache": {
    "sources": {
      "user": "cache|odoo",
      "currency": "cache|odoo",
      "journal": "cache|odoo",
      "shareholder": "cache|odoo",
      "coa": "cache|odoo"
    }
  }
}
```

## `chart-of-accounts`

List accounts. With no filter, returns the full COA from cache (fast).
With a filter, does a direct Odoo search.

```bash
./bin/odoo chart-of-accounts
./bin/odoo chart-of-accounts --code-prefix 6
./bin/odoo chart-of-accounts --type expense
./bin/odoo chart-of-accounts --code-prefix 6 --type expense --limit 50
```

Output:
```json
{
  "ok": true,
  "count": 3,
  "source": "odoo",
  "filters": {"code_prefix": "6", "type": "expense", "limit": 500},
  "items": [
    {"id": 624, "code": "624000", "name": "Office Supplies", "account_type": "expense"},
    ...
  ]
}
```

## `get-move`

Fetch a single `account.move` by id or ref/name, with its lines.

```bash
./bin/odoo get-move --id 1042
./bin/odoo get-move --ref 26May
```

Output (success):
```json
{
  "ok": true,
  "move": {
    "id": 1042,
    "name": "MISC/2026/0042",
    "ref": "26Jun",
    "state": "draft",
    "date": "2026-06-15",
    "journal_id": {"id": 9, "name": "Miscellaneous Operations"},
    "amount_total": 45.00,
    "lines": [
      {"id": 801, "name": "...", "account_id": {"id": 624, "name": "Office Supplies"}, "debit": 45.00, "credit": 0.0},
      {"id": 802, "name": "...", "account_id": {"id": 202, "name": "Shareholder Notes Payable"}, "debit": 0.0, "credit": 45.00}
    ]
  }
}
```

Output (not found):
```json
{"ok": false, "error": "move not found: id=999 ref=None", "code": 4, "error_kind": "not_found"}
```

## `list-drafts`

List draft `account.move` records. Defaults to "this month" when no date
filter is given.

```bash
./bin/odoo list-drafts
./bin/odoo list-drafts --ref 26Jun
./bin/odoo list-drafts --date-from 2026-06-01 --date-to 2026-06-30
./bin/odoo list-drafts --journal-code MISC
```

Output:
```json
{
  "ok": true,
  "count": 1,
  "filters": {
    "ref": null,
    "date_from": "2026-06-01",
    "date_to": "2026-06-30",
    "journal_code": null,
    "limit": 100
  },
  "moves": [
    {
      "id": 1042, "name": "MISC/2026/0042", "ref": "26Jun",
      "state": "draft", "date": "2026-06-15",
      "journal_id": {"id": 9, "name": "Misc"},
      "amount_total": 45.00
    }
  ]
}
```

## `list-invoices`, `list-bills`, `list-partners`

```bash
./bin/odoo list-invoices
./bin/odoo list-invoices --partner-id 42 --state posted --limit 50
./bin/odoo list-bills
./bin/odoo list-bills --date-from 2026-06-01 --date-to 2026-06-30
./bin/odoo list-partners
./bin/odoo list-partners --name-contains acme
```

Output shapes:
- `list-invoices` → `{ok, count, filters, invoices: [...]}` (move_type='out_invoice')
- `list-bills` → `{ok, count, filters, bills: [...]}` (move_type='in_invoice')
- `list-partners` → `{ok, count, filters, partners: [...]}`

## `search-read`

Generic escape hatch for any Odoo model. **Unrestricted** per Aloy.
Every call is audit-logged with a redacted domain summary.

```bash
./bin/odoo search-read --model res.partner --domain '[["is_company","=",true]]'
./bin/odoo search-read --model account.move.line --domain '[["move_id","=","26Jun"]]' --fields account_id,debit,credit
./bin/odoo search-read --model res.users --domain '[["active","=",true]]' --limit 50
```

Output:
```json
{
  "ok": true,
  "model": "res.partner",
  "count": 12,
  "records": [
    {"id": 1, "display_name": "Acme Corp"},
    ...
  ]
}
```

Errors:
- Invalid JSON domain → `{ok: false, code: 2, error_kind: "bad_args"}`
- Domain not an array → same.

## `create-bill`

Create a vendor bill (`move_type='in_invoice'`). **Exact-match** partner
resolution. No silent creation.

```bash
./bin/odoo create-bill \
    --partner-name "Acme Pte Ltd" \
    --invoice-date 2026-06-15 \
    --lines '[{"account_code":"624000","name":"Pens","quantity":2,"price_unit":5.0}]' \
    --ref PO-123 \
    --currency USD
```

Output (success):
```json
{
  "ok": true,
  "move": {
    "id": 1043, "name": "BILL/2026/0001", "ref": "PO-123",
    "state": "draft", "date": "2026-06-15", "amount_total": 10.00,
    "currency_id": {"id": 2, "name": "SGD"}
  }
}
```

Output (partner not found):
```json
{
  "ok": false,
  "error": "partner not found: 'ACME'",
  "code": 2,
  "error_kind": "not_found",
  "suggestions": ["Acme Pte Ltd", "Aloysius Pte Ltd"]
}
```

## `post-move`, `cancel-move`

```bash
./bin/odoo post-move --id 1042
./bin/odoo cancel-move --id 1042
```

Both return `{ok, move, note?}` where `note` is `"already posted"` /
`"already draft"` if the move was already in the target state (no-op success).

`cancel-move` reverts to `draft` via `action_draft`. It does NOT call
`button_cancel` (which would void the move and reverse the journal entry).

## `attach-file`

Upload a file as `ir.attachment` to any Odoo record.

```bash
./bin/odoo attach-file --model account.move --id 1042 --file-path /path/to/receipt.pdf
./bin/odoo attach-file --model res.partner --id 42 --file-path /path/to/contract.pdf --name MSA-2026.pdf
```

Output:
```json
{
  "ok": true,
  "attachment": {
    "id": 99001,
    "name": "receipt.pdf",
    "file_size": 142000,
    "mimetype": "application/pdf",
    "checksum_sha256": "ab12...",
    "res_model": "account.move",
    "res_id": 1042
  }
}
```

Limits: file must exist, be non-empty, and ≤ 25 MB.

## `process-receipt`

The composite. OCR → LLM classify → FX → monthly journal → attach →
delete. See `examples/receipt-upload.md` for the full flow.

```bash
./bin/odoo process-receipt --file-path /tmp/incoming/receipt.pdf
./bin/odoo process-receipt --file-path /tmp/incoming/receipt.pdf --text "ACME\n$45.00"
```

Output (success):
```json
{
  "ok": true,
  "file": "receipt.pdf",
  "ocr_source": "agent|plumber|openai|tesseract",
  "extraction": {
    "vendor": "...", "date": "YYYY-MM-DD",
    "currency": "SGD", "amount": 12.50,
    "debit_account_code": "624000",
    "description": "...",
    "confidence": 0.92, "notes": ""
  },
  "fx": {"from": "SGD", "to": "SGD", "rate": 1.0, "converted": 12.50},
  "move_id": 1042,
  "attachment_id": 99001,
  "totals": {"total_debit": 12.50, "credit": 12.50},
  "error": null,
  "failed_path": null
}
```

Output (failure):
```json
{
  "ok": false,
  "file": "...",
  "error": "Receipt total is zero or unreadable",
  "failed_path": "/tmp/incoming/../failed_receipts/20260618-184255-...pdf"
}
```

The move is always left in `draft` state. Call `post-move --id <id>` to confirm.

## `cache show|refresh|clear`

```bash
./bin/odoo cache show       # show the cache file's contents
./bin/odoo cache refresh   # force-refresh all 5 lookups (calls Odoo, then probe)
./bin/odoo cache clear     # delete the cache file
```

The cache is at `tmp/odoo_cache.json` by default. Override with
`ODOO_CACHE_PATH` and `ODOO_CACHE_TTL_SECONDS` env vars.

## `_ocr-test` (internal)

Run the OCR router standalone. Useful for debugging.

```bash
./bin/odoo _ocr-test /path/to/receipt.pdf
./bin/odoo _ocr-test /path/to/receipt.pdf --text "fallback text"
```

Output: `{ok, text, source, file_path, file_size, error?}`.

## Exit code summary

| Subcommand | Success | Bad args | Upstream error | Not found | Missing env |
|---|---|---|---|---|---|
| `probe`, `cache`, all reads | 0 | 2 | 3 | 4 | 5 |
| `create-bill` | 0 | 2 (partner not found) | 3 | 4 | 5 |
| `post-move`, `cancel-move` | 0 | 2 | 3 | 4 | 5 |
| `attach-file` | 0 | 2 (file too large, etc.) | 3 | 4 (file not found) | 5 |
| `process-receipt` | 0 | n/a (errors as `ok: false` in JSON) | 3 (errors as `ok: false` in JSON) | 4 (errors as `ok: false` in JSON) | 5 |
| `_ocr-test` | 0 | 2 | 3 | n/a | 5 |
| `search-read` | 0 | 2 (invalid JSON domain) | 3 | n/a | 5 |

For `process-receipt`, the exit code is determined by the receipt-level
outcome, not by the agent's interpretation of the JSON. The JSON body
ALWAYS has `ok: bool` so the agent can inspect the failure regardless of
exit code.
