# Example: agent uploading a receipt

This is a worked example of the OpenClaw agent driving the skill to process
a single receipt end-to-end. The agent has its own OCR (e.g. via the
`image` tool) and passes the extracted text to the skill.

## Setup

```bash
# 1. The agent has the receipt file at a known path
ls -lh /tmp/incoming/acme-2026-06-15.pdf
# -rw-r--r-- 1 user user 142K Jun 15 12:34 /tmp/incoming/acme-2026-06-15.pdf
```

## Conversation

**User:** Here's a receipt from Acme, 2026-06-15. Book it.

**Agent:** *(uses the `image` tool to OCR the PDF; receives the text)*

> *Acme Pte Ltd*
> *Date: 15/06/2026*
> *Item: Office supplies — 3 boxes of A4 paper*
> *Total: SGD 45.00*
> *Payment: VISA ****1234*

*(agent decides to delegate to the skill, since the receipt has a known vendor and amount)*

**Agent:** Calling the skill to process this receipt.

```bash
# 1. Verify the skill can talk to Odoo
./bin/odoo probe
```

```json
{
  "ok": true,
  "odoo": {
    "user": {"id": 7, "name": "Alice", "login": "alice@example.com"},
    "company": {"id": 42, "name": "Aloysius Pte Ltd", "currency": {"id": 1, "name": "SGD"}}
  },
  "journal": {"id": 9, "code": "MISC"},
  "shareholder_account": {"id": 202, "code": "202040"},
  "coa": {"full_count": 87, "short_count": 23},
  "cache": {"sources": {"user": "cache", "currency": "cache", "journal": "cache", "shareholder": "cache", "coa": "cache"}}
}
```

```bash
# 2. Find the right expense account for "Office supplies"
./bin/odoo chart-of-accounts --code-prefix 624
```

```json
{
  "ok": true,
  "count": 3,
  "source": "odoo",
  "filters": {"code_prefix": "624", "type": null, "limit": 500},
  "items": [
    {"code": "624000", "name": "Office Supplies", "account_type": "expense"},
    {"code": "624100", "name": "Stationery", "account_type": "expense"},
    {"code": "624200", "name": "Printer Supplies", "account_type": "expense"}
  ]
}
```

```bash
# 3. Process the receipt. The agent's own OCR text is passed via --text.
./bin/odoo process-receipt \
    --file-path /tmp/incoming/acme-2026-06-15.pdf \
    --text "Acme Pte Ltd
Date: 15/06/2026
Item: Office supplies — 3 boxes of A4 paper
Total: SGD 45.00
Payment: VISA ****1234"
```

```json
{
  "ok": true,
  "file": "acme-2026-06-15.pdf",
  "ocr_source": "agent",
  "extraction": {
    "vendor": "Acme Pte Ltd",
    "date": "2026-06-15",
    "currency": "SGD",
    "amount": 45.00,
    "debit_account_code": "624000",
    "description": "3 boxes of A4 paper",
    "confidence": 0.92,
    "notes": ""
  },
  "fx": {"from": "SGD", "to": "SGD", "rate": 1.0, "converted": 45.00},
  "move_id": 1042,
  "attachment_id": 99012,
  "totals": {"total_debit": 45.00, "credit": 45.00},
  "error": null,
  "failed_path": null
}
```

```bash
# 4. Confirm the move and check the draft
./bin/odoo get-move --id 1042
```

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
      {"id": 801, "name": "2026-06-15 Acme Pte Ltd — 3 boxes of A4 paper", "account_id": {"id": 624, "name": "Office Supplies"}, "debit": 45.00, "credit": 0.0},
      {"id": 802, "name": "26Jun – Shareholder reimbursement", "account_id": {"id": 202, "name": "Shareholder Notes Payable"}, "debit": 0.0, "credit": 45.00}
    ]
  }
}
```

```bash
# 5. User reviews in Odoo UI and approves
./bin/odoo post-move --id 1042
```

```json
{
  "ok": true,
  "move": {
    "id": 1042,
    "name": "MISC/2026/0042",
    "ref": "26Jun",
    "state": "posted"
  }
}
```

**Agent:** Done. The receipt is posted as move `MISC/2026/0042` in the
Miscellaneous Operations journal:

- **Debit:** 45.00 SGD to 624000 Office Supplies
- **Credit:** 45.00 SGD to 202040 Shareholder Notes Payable
- **Ref:** `26Jun` (June 2026 consolidated draft)
- **Attachment:** the original PDF

The local file was deleted after the Odoo upload succeeded.

## Audit trail

The audit log at `tmp/audit_logs/2026-06.jsonl` has one line per stage:

```json
{"ts_utc": "...", "event": "receipt_processing", "file": "acme-2026-06-15.pdf", "stage": "ocr_done", "ocr_source": "agent", "ocr_text_len": 137, "move_ref": "26Jun", "journal_code": "MISC"}
{"ts_utc": "...", "event": "receipt_processing", "file": "acme-2026-06-15.pdf", "stage": "ai_done", "extraction": {...}, "ai": {"model": "..."}}
{"ts_utc": "...", "event": "receipt_processing", "file": "acme-2026-06-15.pdf", "stage": "odoo_done", "move_id": 1042, "attachment_id": 99012, "debit_sgd": 45.00, "rate": 1.0, "extraction": {...}}
```

No API keys in any line.
