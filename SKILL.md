---
name: skill-odoo
description: "Integrate with Odoo (OpenERP)"
---

# OpenClaw Accounting Extraction Skill

## Purpose

Use this skill to convert expense documents (receipts/invoices) into Odoo-ready accounting entries.

## Inputs

- Document file (PDF/image)
- Odoo API key
- Company Chart of Accounts (JSON)

## Workflow

1. **Detect document type**
   - If PDF contains text, parse with `pdfplumber`.
   - If scanned/image-based, convert with `pdf2image` and OCR with `tesseract-ocr`.
2. **Extract fields**
   - Vendor/supplier
   - Document date
   - Currency
   - Subtotal/tax/total
   - Line-item hints (if available)
3. **Load accounting context**
   - Use cached Chart of Accounts JSON if available.
   - Refresh from source if missing/outdated.
4. **Classify accounting entries**
   - Map expense type to most suitable account(s).
   - Produce balanced double-entry output.
5. **Validate output**
   - Ensure debits equal credits.
   - Ensure account codes exist in current chart.
6. **Post to Odoo**
   - Submit journal entry via API using the provided key.

## Output Format (recommended)

```json
{
  "document_type": "receipt",
  "vendor": "Sample Vendor",
  "date": "2026-03-08",
  "currency": "USD",
  "entries": [
    {"account_code": "600100", "debit": 100.00, "credit": 0.0},
    {"account_code": "200100", "debit": 0.0, "credit": 100.00}
  ],
  "confidence": 0.92,
  "notes": "OCR source with clear totals"
}
```

## Guardrails

- Never post if entry is unbalanced.
- Never post if required totals are missing.
- Flag low-confidence OCR/classification for manual review.
- Preserve raw extracted text for traceability.
