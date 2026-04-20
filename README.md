# OpenClaw Odoo Integration

OpenClaw Odoo Integration automates expense bookkeeping by extracting data from receipts/invoices and posting journal entries to Odoo.

## Goal

Turn documents sent over WhatsApp (receipts, invoices, and other expenses) into correctly categorized double-entry accounting records in Odoo.

## High-Level Workflow

1. **Set up Odoo**
   - Create a free Odoo account.
   - Enable and use the **Accounting** module.
2. **Generate API access**
   - In Odoo, go to **Preferences → Security** and generate an API key.
3. **Receive expense documents**
   - Users send receipts/invoices/expense documents through WhatsApp to OpenClaw.
4. **Extract document content**
   - If the PDF contains embedded text, use `pdfplumber`.
   - If the PDF is image-based, convert pages to images with `pdf2image`.
   - Run OCR with `tesseract-ocr` to extract text from images/scans.
5. **Load accounting context**
   - Check whether a Chart of Accounts JSON is already available.
   - If missing, fetch it.
   - Allow manual chart-of-accounts refresh when data is outdated.
6. **Classify accounting treatment with AI**
   - Send extracted document content plus chart-of-accounts context to an agent.
   - Agent decides:
     - Account classification
     - Required double-entry postings
7. **Post to Odoo**
   - Use the generated API key to insert the finalized accounting records into Odoo.

## Core Components

- **Ingestion**: WhatsApp document intake
- **Extraction**: `pdfplumber`, `pdf2image`, `tesseract-ocr`
- **Accounting context**: Chart of Accounts JSON loader/updater
- **AI reasoning**: Account mapping + double-entry decisioning
- **Execution**: Odoo API insertion

## Suggested Next Improvements

- Add confidence scoring for OCR and classification outputs.
- Add human-in-the-loop approval for low-confidence postings.
- Add retry/error queues for failed API insertions.
- Add audit logs for every accounting decision and posted entry.
## OpenClaw Skill

For agent-oriented execution instructions, see [`SKILL.md`](SKILL.md).

## CLI Script for Bot Integration

Use the `openclaw_bot_cli/` package as a callable command-line entrypoint for your bot.

Setup env config first:

```bash
cp .env.sample .env
# then edit .env with your real keys
```

Example:

```bash
python -m openclaw_bot_cli ./sample_receipt.txt --coa ./chart_of_accounts.json --output ./result.json
```

Options:
- `--coa`: chart of accounts JSON file (required)
- `--refresh-coa-cmd`: optional shell command to refresh chart-of-accounts JSON before processing
- `--allow-post`: enable posting step (kept as a safe stub until Odoo API call is wired)
- `--api-key`: Odoo API key (used with `--allow-post`)
- `ODOO_API_KEY`: API key loaded from `.env` (or shell env)
- `--use-ai`: use AI-based account mapping with `AI_CHAT_URL`, `AI_MODEL`, `AI_SECRET` from `.env`

API key input examples:

```bash
python -m openclaw_bot_cli ./sample_receipt.txt --coa ./chart_of_accounts.json --allow-post --api-key "your_api_key"
```

```bash
export ODOO_API_KEY="your_api_key"
python -m openclaw_bot_cli ./sample_receipt.txt --coa ./chart_of_accounts.json --allow-post
```

AI automation example:

```bash
python -m openclaw_bot_cli ./sample_receipt.txt --coa ./chart_of_accounts.json --use-ai --output ./result.json
```

The script outputs the accounting JSON payload to stdout and to the `--output` file.
