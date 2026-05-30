---
name: skill-odoo
description: "Turn receipts (PDF, image, scanned PDF) into balanced monthly draft journal entries in Odoo. Watches an inbox folder (or accepts uploads via Telegram), OCRs the file (OpenAI-compatible vision via OpenRouter + MiniMax-01 by default, with Zo and local Tesseract as alternatives), classifies the expense against the live Chart of Accounts via an LLM, converts non-SGD amounts to SGD via Frankfurter, appends a debit line to the current month's draft account.move, recomputes a single balancing credit to 202040 Shareholder Notes Payable, attaches the original file, and deletes the local copy on success. Entries stay draft for human review before posting."
---

# OpenClaw Accounting Extraction Skill

## Purpose

Convert expense documents (receipts / invoices) into Odoo-ready, balanced
journal entries — one consolidated `account.move` per month, attachment
included, ready for one-click posting.

## Inputs

- Receipt file (PDF or image; scanned PDFs are OCRed)
- Odoo connection: URL, DB, login, API key
- Live Chart of Accounts (fetched from Odoo, cached locally)

## Workflow

1. **Detect document type**
   - If PDF contains a text layer, parse with `pdfplumber`.
   - If scanned/image-based, OCR via the configured provider
     (`openai` / `zo` / `tesseract`).
2. **Extract fields**
   - Vendor / supplier, document date, currency, subtotal/tax/total,
     line-item hints when available.
3. **Load accounting context**
   - Use cached Chart of Accounts JSON if fresh, else refresh from Odoo.
4. **Classify accounting entry**
   - LLM picks the best matching expense/asset account from the live chart.
5. **Validate**
   - Non-zero total, currency known, account code exists, FX rate available.
6. **Append to monthly draft**
   - Find/adopt/create the month's draft `account.move` in the configured
     journal, append a debit line, recompute the single 202040 credit line
     so the move stays balanced.
7. **Attach + clean up**
   - Upload the source file as an `ir.attachment` on the move,
     delete the local file, write an audit log entry.

## Output (audit log per receipt)

```json
{
  "file": "2026-05-22_uber.pdf",
  "vendor": "Uber",
  "date": "2026-05-22",
  "currency": "SGD",
  "total": 18.40,
  "debit_account_code": "624000",
  "credit_account_code": "202040",
  "move_ref": "26May",
  "attachment_id": 14821,
  "stage": "posted_draft",
  "ocr_source": "openai_ocr_pdf",
  "model": "minimax/minimax-01"
}
```

## Guardrails

- Entries are always created as **draft** — never auto-posted.
- Move is rejected if debits ≠ credits after recomputation.
- Receipts with missing totals or amount `0` are routed to `./failed_receipts/`
  with a `.error.txt` sidecar instead of being uploaded.
- Raw OCR text and LLM response are preserved in `./audit_logs/` for traceability.
- API keys and binary file contents are never written to logs.
