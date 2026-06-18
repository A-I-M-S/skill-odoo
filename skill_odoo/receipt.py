"""End-to-end receipt processor: OCR → classify → FX → monthly draft → attach.

This is the centerpiece of the skill. \`process-receipt\` is the single subcommand
the agent calls when it has a receipt file to ingest. Per Aloy's decisions:

- The agent's own OCR is preferred (pass via \`--text\`). If provided, the
  in-skill OCR chain is skipped entirely.
- Non-SGD receipts are converted to the company base currency at the
  transaction date via Frankfurter.
- Per-receipt: one debit line (chosen by the LLM from the COA) + a single
  202040 Shareholder Notes Payable credit line that balances the move.
- One consolidated draft \`account.move\` per month (e.g. \`26May\`).
- Failed files are moved to \`tmp/failed_receipts/<ts>-<name>.<error.txt>\`.
- Every step is audit-logged (no secrets in the JSONL).
"""
from __future__ import annotations

import logging
import mimetypes
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import fx
from .ai_automation import classify_receipt_with_debug, shortlist_coa
from .audit import write_audit
from .config import Settings
from .monthly_journal import (
    MonthDraft,
    ensure_month_draft,
    rebuild_lines_with_shareholder_balance,
    ref_for,
)
from .models import ReceiptExtraction
from .ocr_router import extract_receipt_text
from .odoo_cache import (
    get_account,
    get_chart_of_accounts,
    get_company_currency,
    get_journal,
    get_user_info,
)
from .odoo_client import Odoo

LOG = logging.getLogger("skill-odoo.receipt")


SUPPORTED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".txt"}


@dataclass
class FileResult:
    path: Path
    ok: bool
    extraction: ReceiptExtraction | None
    debit_sgd: float
    rate: float
    move_id: int | None
    attachment_id: int | None
    error: str = ""
    failed_path: str = ""


@dataclass
class PendingReceipt:
    path: Path
    extraction: ReceiptExtraction
    debit_sgd: float
    rate: float
    debit_line: dict[str, Any]


def process_receipt(
    settings: Settings,
    *,
    file_path: Path,
    provided_text: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Process a single receipt file end-to-end.

    Returns a JSON-serializable dict with:
    - ok (bool)
    - file (str): the basename
    - ocr_source (str): agent | plumber | openai | tesseract | failed
    - extraction (dict | null): the ReceiptExtraction.to_dict() result
    - fx (dict | null): {from, to, rate, converted} or null
    - move_id (int | null)
    - attachment_id (int | null)
    - totals (dict | null): {total_debit, credit} for the monthly move
    - error (str | null)
    - failed_path (str | null)
    """
    today = today or date.today()
    ref = ref_for(today, settings.move_ref_format)
    result: dict[str, Any] = {
        "ok": False,
        "file": file_path.name,
        "ocr_source": None,
        "extraction": None,
        "fx": None,
        "move_id": None,
        "attachment_id": None,
        "totals": None,
        "error": None,
        "failed_path": None,
    }
    if not file_path.exists():
        result["error"] = f"file not found: {file_path}"
        _audit_failure(settings, file_path, "missing_file", result["error"])
        return result
    if file_path.suffix.lower() not in SUPPORTED_EXT:
        result["error"] = f"unsupported file type: {file_path.suffix}"
        _audit_failure(settings, file_path, "unsupported_type", result["error"])
        return result

    # 1) Authenticate and load the near-static Odoo lookups (cached).
    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key
    )
    user, _ = get_user_info(odoo, settings)
    co_id = user["company_id_int"]
    (_, _), _ = get_company_currency(odoo, co_id, settings)
    journal, _ = get_journal(
        odoo, settings.journal_code, settings.journal_type, settings
    )
    shareholder, _ = get_account(odoo, settings.shareholder_account_code, settings)
    coa_full, _ = get_chart_of_accounts(odoo, settings)
    coa_short = shortlist_coa(coa_full)
    code_to_id = {a["code"]: a["id"] for a in coa_full}

    # 2) OCR via the router.
    try:
        ocr_result = extract_receipt_text(
            settings, file_path, provided_text=provided_text
        )
        text = ocr_result["text"]
        ocr_source = ocr_result["source"]
    except Exception as exc:
        result["error"] = f"ocr failed: {exc}"
        _move_to_failed(file_path, result["error"], settings)
        result["failed_path"] = str(_move_to_failed(file_path, result["error"], settings)) or None
        _audit_failure(settings, file_path, "ocr_error", result["error"])
        return result
    result["ocr_source"] = ocr_source
    if not text.strip():
        result["error"] = "OCR produced no text"
        result["failed_path"] = str(_move_to_failed(file_path, result["error"], settings) or "")
        _audit_failure(settings, file_path, "ocr_empty", result["error"])
        return result

    _audit_step(settings, file_path, "ocr_done", {
        "ocr_source": ocr_source,
        "ocr_text_len": len(text),
        "move_ref": ref,
        "journal_code": journal["code"],
    })

    # 3) LLM classify.
    try:
        extraction, ai_debug = classify_receipt_with_debug(
            text,
            coa_short,
            chat_url=settings.ai_chat_url,
            model=settings.ai_model,
            api_key=settings.ai_secret,
            provider_order=settings.ai_provider_order,
            default_currency=settings.default_currency,
        )
    except Exception as exc:
        result["error"] = f"classify failed: {exc}"
        result["failed_path"] = str(_move_to_failed(file_path, result["error"], settings) or "")
        _audit_failure(settings, file_path, "classify_error", result["error"])
        return result

    _audit_step(settings, file_path, "ai_done", {
        "extraction": extraction.to_dict(),
        "ai": _safe_ai_debug(ai_debug),
    })

    if extraction.debit_account_code not in code_to_id:
        result["error"] = f"AI returned unknown account code {extraction.debit_account_code!r}"
        result["failed_path"] = str(_move_to_failed(file_path, result["error"], settings) or "")
        _audit_failure(settings, file_path, "unknown_account", result["error"])
        return result
    if extraction.amount <= 0:
        result["error"] = "Receipt total is zero or unreadable"
        result["failed_path"] = str(_move_to_failed(file_path, result["error"], settings) or "")
        _audit_failure(settings, file_path, "zero_amount", result["error"])
        return result

    # 4) FX.
    tx_dt = _parse_date(extraction.tx_date) or today
    try:
        converted, rate = fx.convert(
            extraction.amount,
            from_ccy=extraction.currency,
            to_ccy=settings.fx_base_currency,
            on=tx_dt,
            provider=settings.fx_provider,
        )
    except Exception as exc:
        result["error"] = f"fx failed: {exc}"
        result["failed_path"] = str(_move_to_failed(file_path, result["error"], settings) or "")
        _audit_failure(settings, file_path, "fx_error", result["error"])
        return result
    if converted <= 0:
        result["error"] = "Converted receipt total is zero or unreadable"
        result["failed_path"] = str(_move_to_failed(file_path, result["error"], settings) or "")
        _audit_failure(settings, file_path, "zero_converted", result["error"])
        return result
    result["fx"] = {
        "from": extraction.currency,
        "to": settings.fx_base_currency,
        "rate": rate,
        "converted": converted,
    }
    result["extraction"] = extraction.to_dict()

    label = _line_label(extraction, converted, rate, settings.fx_base_currency)
    debit_line = {
        "account_id": code_to_id[extraction.debit_account_code],
        "name": label,
        "debit": converted,
        "credit": 0.0,
    }
    pending = PendingReceipt(
        path=file_path,
        extraction=extraction,
        debit_sgd=converted,
        rate=rate,
        debit_line=debit_line,
    )

    # 5) Find / create the month's draft move.
    try:
        draft = ensure_month_draft(
            odoo,
            journal_id=journal["id"],
            journal_code=journal["code"],
            today=today,
            ref=ref,
            name_from_ref=settings.move_name_from_ref,
            shareholder_account_id=shareholder["id"],
        )
        totals = rebuild_lines_with_shareholder_balance(
            odoo,
            move_id=draft.move_id,
            shareholder_account_id=shareholder["id"],
            ref=ref,
            new_debit_lines=[pending.debit_line],
        )
    except Exception as exc:
        result["error"] = f"monthly journal failed: {exc}"
        result["failed_path"] = str(_move_to_failed(file_path, result["error"], settings) or "")
        _audit_failure(settings, file_path, "journal_error", result["error"])
        return result

    # 6) Attach the original file to the move.
    try:
        attach_id = odoo.attach_file(
            move_id=draft.move_id,
            file_path=pending.path,
            mimetype=mimetypes.guess_type(pending.path.name)[0],
        )
    except Exception as exc:
        result["error"] = f"attach failed: {exc}"
        result["failed_path"] = str(_move_to_failed(file_path, result["error"], settings) or "")
        _audit_failure(settings, file_path, "attach_error", result["error"])
        return result

    result["move_id"] = draft.move_id
    result["attachment_id"] = attach_id
    result["totals"] = totals
    result["ok"] = True

    # 7) Delete the local file on success (per the original behavior).
    if settings.receipts_processed_delete:
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass

    _audit_step(settings, file_path, "odoo_done", {
        "move_id": draft.move_id,
        "attachment_id": attach_id,
        "debit_sgd": pending.debit_sgd,
        "rate": pending.rate,
        "extraction": extraction.to_dict(),
    })
    return result


# ── helpers ────────────────────────────────────────────────────────────────


def _line_label(e: ReceiptExtraction, converted: float, rate: float, to_ccy: str) -> str:
    base = f"{e.tx_date} {e.vendor} — {e.description}".strip(" —")
    if e.currency.upper() == to_ccy:
        return base[:240]
    return f"{base} [{e.currency} {e.amount:.2f} @ {rate:.4f} = {to_ccy} {converted:.2f}]"[:240]


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    return None


def _move_to_failed(path: Path, reason: str, settings: Settings) -> Path | None:
    if not path.exists():
        return None
    failed_dir = settings.receipts_inbox.parent / "failed_receipts"
    failed_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", path.name).strip("-") or "receipt"
    dest = failed_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_name}"
    try:
        path.replace(dest)
    except FileNotFoundError:
        return None
    try:
        (dest.with_suffix(dest.suffix + ".error.txt")).write_text(reason, encoding="utf-8")
    except OSError:
        pass
    return dest


def _audit_step(settings: Settings, file_path: Path, stage: str, payload: dict[str, Any]) -> None:
    try:
        write_audit(
            settings.audit_log_dir,
            {"event": "receipt_processing", "file": file_path.name, "stage": stage, **payload},
        )
    except Exception:
        pass


def _audit_failure(settings: Settings, file_path: Path, stage: str, error: str) -> None:
    _audit_step(settings, file_path, f"failed_{stage}", {"error": error})


def _safe_ai_debug(ai_debug: dict[str, Any]) -> dict[str, Any]:
    """Strip the LLM's chat URL out of the audit log — it may carry the
    OpenRouter account id in some configurations."""
    safe = dict(ai_debug)
    safe.pop("chat_url", None)
    return safe
