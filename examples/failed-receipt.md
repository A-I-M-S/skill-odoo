# Example: failed receipt

When the LLM classifier can't decide on an account code (e.g. the OCR
text is too garbled, or the receipt is a category the COA doesn't have a
matching account for), the receipt pipeline moves the file to
`tmp/failed_receipts/<ts>-<name>.<error.txt>` and returns a structured
error. The agent should report the failure to the user and ask for guidance.

## Conversation

**User:** Here's another receipt, can you book it?

*(agent downloads the file at `/tmp/incoming/mystery-2026-06-15.pdf`)*

**Agent:** The OCR text is:

> *RECEIPT — No clear vendor*
> *Date: 2026-06-15*
> *Random collection of words*
> *Amount: ???*

```bash
./bin/odoo process-receipt --file-path /tmp/incoming/mystery-2026-06-15.pdf \
    --text "RECEIPT — No clear vendor
Date: 2026-06-15
Random collection of words
Amount: ???"
```

```json
{
  "ok": false,
  "file": "mystery-2026-06-15.pdf",
  "ocr_source": "agent",
  "extraction": null,
  "fx": null,
  "move_id": null,
  "attachment_id": null,
  "totals": null,
  "error": "Receipt total is zero or unreadable",
  "failed_path": "/tmp/incoming/../failed_receipts/20260618-184255-mystery-2026-06-15.pdf"
}
```

**Agent:** I couldn't process this receipt — the OCR text doesn't have a
readable total amount, so the classifier rejected it. The file has been
moved to `failed_receipts/20260618-184255-mystery-2026-06-15.pdf` with a
sidecar `.error.txt` containing the failure reason. Could you:

1. Confirm the receipt amount, or
2. Re-scan with a better image, or
3. Tell me which expense account it should go to (I can then call
   `create-bill` directly with the right account code).

## What happened

The pipeline ran four stages:

1. **OCR** — succeeded (`ocr_source: agent`).
2. **AI classify** — LLM responded with a JSON object, but `amount` was 0
   or unreadable. The pipeline raised `AIError("LLM could not identify a
   positive final paid total from OCR text; leaving receipt unprocessed")`,
   which is surfaced as `error: "Receipt total is zero or unreadable"`.
3. **No FX, no journal, no attach** — the failure short-circuited the rest.
4. **Cleanup** — the file was moved to `failed_receipts/`, the local
   path is gone, but the receipt is still inspectable in Odoo's
   "Attachments" (none was created).

## Audit trail

```json
{"ts_utc": "...", "event": "receipt_processing", "file": "mystery-2026-06-15.pdf", "stage": "ocr_done", "ocr_source": "agent", "ocr_text_len": 87, "move_ref": "26Jun", "journal_code": "MISC"}
{"ts_utc": "...", "event": "receipt_processing", "file": "mystery-2026-06-15.pdf", "stage": "failed_zero_amount", "error": "Receipt total is zero or unreadable"}
```

## Recovery

The agent can recover by:
- **Re-running** with a better OCR or a known amount:
  ```bash
  ./bin/odoo process-receipt --file-path failed_receipts/20260618-184255-mystery-2026-06-15.pdf \
      --text "Acme Pte Ltd ... Total: SGD 45.00"
  ```
- **Manually creating a bill** if the agent can identify the right account:
  ```bash
  ./bin/odoo create-bill --partner-name "Acme" --invoice-date 2026-06-15 \
      --lines '[{"account_code": "624000", "name": "Office", "price_unit": 45.00}]'
  ./bin/odoo attach-file --model account.move --id 1234 \
      --file-path failed_receipts/20260618-184255-mystery-2026-06-15.pdf
  ```
- **Deleting the failed file** if it's truly unprocessable.

## When do failures happen?

The most common reasons a receipt lands in `failed_receipts/`:

1. **Zero / unreadable amount** — LLM can't find a TOTAL line in the OCR text.
2. **Unknown account code** — LLM picks a code that isn't in the COA
   (rare; the shortlist usually covers it).
3. **Classify error** — LLM call failed (network, rate limit, malformed JSON).
4. **FX error** — Frankfurter is down or doesn't have the currency pair.
5. **OCR failure** — the in-skill chain produced no text (the agent's
   `--text` was empty AND pdfplumber + openai_ocr + tesseract all failed).
6. **Missing file** — `--file-path` doesn't exist on disk.
7. **Unsupported file type** — extension not in `{.pdf, .png, .jpg, .jpeg,
   .webp, .tif, .tiff, .bmp, .txt}`.

In all cases, the original file is preserved at the `failed_path` and the
audit log has a one-line summary of what went wrong.
