"""End-to-end folder processor: OCR -> classify -> post -> attach -> delete."""
from __future__ import annotations

import logging
import mimetypes
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .ai_automation import classify_receipt_with_debug, shortlist_coa
from .config import Settings
from .extraction import extract_text
from . import fx
from .models import ReceiptExtraction
from .monthly_journal import ensure_month_draft, ref_for, rebuild_lines_with_shareholder_balance
from .odoo_client import Odoo
from .audit import write_audit


log = logging.getLogger("skill-odoo")


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


def process_inbox(
    settings: Settings,
    *,
    today: date | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    today = today or date.today()
    ref = ref_for(today, settings.move_ref_format)
    inbox = settings.receipts_inbox
    if not inbox.exists():
        inbox.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXT)
    log.info("inbox %s files=%d ref=%s dry_run=%s", inbox, len(files), ref, dry_run)
    if not files:
        return {"ref": ref, "files": [], "move_id": None, "skipped": "empty inbox"}

    odoo = Odoo(url=settings.odoo_url, db=settings.odoo_db, login=settings.odoo_login, api_key=settings.odoo_api_key)
    user = odoo.user_info()
    co_id = user["company_id_int"]
    co_ccy_id, co_ccy_name = odoo.company_currency(co_id)
    journal = odoo.find_journal(settings.journal_code, settings.journal_type)
    shareholder = odoo.find_account(settings.shareholder_account_code)
    coa_full = odoo.chart_of_accounts()
    coa_short = shortlist_coa(coa_full)
    code_to_id = {a["code"]: a["id"] for a in coa_full}

    log.info("odoo company=%s ccy=%s journal=%s/%s shareholder=%s coa_short=%d/%d",
             user["company_id"][1], co_ccy_name, journal["code"], journal["id"], shareholder["code"], len(coa_short), len(coa_full))

    if dry_run:
        return {
            "ref": ref,
            "journal": journal,
            "shareholder": shareholder,
            "coa_short_count": len(coa_short),
            "coa_full_count": len(coa_full),
            "files": [str(p) for p in files],
        }

    draft = ensure_month_draft(
        odoo,
        journal_id=journal["id"],
        journal_code=journal["code"],
        today=today,
        ref=ref,
        name_from_ref=settings.move_name_from_ref,
        shareholder_account_id=shareholder["id"],
    )

    pending: list[PendingReceipt] = []
    results: list[FileResult] = []

    for path in files:
        try:
            text, kind = extract_text(
                path,
                ocr_provider=settings.ocr_provider,
                zo_ocr_token=settings.zo_ocr_token,
                zo_ocr_model=settings.zo_ocr_model,
            )
            audit_base = {
                "event": "receipt_processing",
                "file": path.name,
                "source_kind": kind,
                "ocr_text": text,
                "ocr_text_len": len(text),
                "move_ref": ref,
                "journal_code": journal["code"],
            }
            write_audit(settings.audit_log_dir, {**audit_base, "stage": "ocr_done"})
            if not text.strip():
                raise RuntimeError("OCR produced no text")
            extraction, ai_debug = classify_receipt_with_debug(
                text,
                coa_short,
                chat_url=settings.ai_chat_url,
                model=settings.ai_model,
                api_key=settings.ai_secret,
                provider_order=settings.ai_provider_order,
                default_currency=settings.default_currency,
            )
            write_audit(settings.audit_log_dir, {
                **audit_base,
                "stage": "ai_done",
                "ai": ai_debug,
                "extraction": extraction.to_dict(),
            })
            if extraction.debit_account_code not in code_to_id:
                raise RuntimeError(f"AI returned unknown account code {extraction.debit_account_code!r}")
            if extraction.amount <= 0:
                raise RuntimeError("Receipt total is zero or unreadable")

            tx_dt = _parse_date(extraction.tx_date) or today
            converted, rate = fx.convert(
                extraction.amount,
                from_ccy=extraction.currency,
                to_ccy=settings.fx_base_currency,
                on=tx_dt,
                provider=settings.fx_provider,
            )
            if converted <= 0:
                raise RuntimeError("Converted receipt total is zero or unreadable")

            label = _line_label(extraction, converted, rate, settings.fx_base_currency)
            debit_line = {
                "account_id": code_to_id[extraction.debit_account_code],
                "name": label,
                "debit": converted,
                "credit": 0.0,
            }
            pending.append(PendingReceipt(path, extraction, converted, rate, debit_line))
            log.info("CLASSIFIED %s -> %s %s%.2f rate=%.4f acct=%s", path.name, extraction.vendor,
                     settings.fx_base_currency, converted, rate, extraction.debit_account_code)
        except Exception as exc:
            log.exception("FAIL %s", path.name)
            try:
                write_audit(settings.audit_log_dir, {"event": "receipt_processing", "file": path.name, "stage": "failed", "error": str(exc)})
            except Exception:
                pass
            failed_path = _move_to_failed(path, str(exc))
            results.append(FileResult(
                path=path, ok=False, extraction=None, debit_sgd=0.0,
                rate=0.0, move_id=None, attachment_id=None, error=str(exc),
                failed_path=str(failed_path) if failed_path else "",
            ))

    if pending:
        totals = rebuild_lines_with_shareholder_balance(
            odoo,
            move_id=draft.move_id,
            shareholder_account_id=shareholder["id"],
            ref=ref,
            new_debit_lines=[p.debit_line for p in pending],
        )
    else:
        totals = {"total_debit": 0.0, "credit": 0.0}

    for item in pending:
        attach_id = odoo.attach_file(
            move_id=draft.move_id,
            file_path=item.path,
            mimetype=mimetypes.guess_type(item.path.name)[0],
        )
        results.append(FileResult(
            path=item.path, ok=True, extraction=item.extraction,
            debit_sgd=item.debit_sgd, rate=item.rate, move_id=draft.move_id,
            attachment_id=attach_id,
        ))
        write_audit(settings.audit_log_dir, {"event": "receipt_processing", "file": item.path.name, "stage": "odoo_done", "move_id": draft.move_id, "attachment_id": attach_id, "debit_sgd": item.debit_sgd, "rate": item.rate, "extraction": item.extraction.to_dict()})
        log.info("OK %s -> %s %s%.2f rate=%.4f acct=%s", item.path.name, item.extraction.vendor,
                 settings.fx_base_currency, item.debit_sgd, item.rate, item.extraction.debit_account_code)

    if settings.receipts_processed_delete:
        for r in results:
            if r.ok:
                try:
                    r.path.unlink()
                except FileNotFoundError:
                    pass

    return {
        "ref": ref,
        "move_id": draft.move_id,
        "journal": journal,
        "shareholder_account": shareholder,
        "totals": totals,
        "results": [
            {
                "file": r.path.name,
                "ok": r.ok,
                "error": r.error,
                "extraction": r.extraction.to_dict() if r.extraction else None,
                "debit_sgd": r.debit_sgd,
                "rate": r.rate,
                "attachment_id": r.attachment_id,
                "failed_path": r.failed_path,
            }
            for r in results
        ],
    }


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


def _move_to_failed(path: Path, reason: str) -> Path | None:
    if not path.exists():
        return None
    failed_dir = path.parent.parent / "failed_receipts"
    failed_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", path.name).strip("-") or "receipt"
    dest = failed_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_name}"
    path.replace(dest)
    (dest.with_suffix(dest.suffix + ".error.txt")).write_text(reason, encoding="utf-8")
    return dest
