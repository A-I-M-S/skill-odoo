"""Write-side Odoo tools: create-bill, post-move, cancel-move.

Issue A-I-M-S/skill-odoo#8. The dispatch lives here; ``__main__.py`` calls
into these functions and prints the result as JSON.

Per Aloy's decision (issue #6): write operations are trusted (no human-
confirm step), and every write is audit-logged.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .audit import write_audit
from .config import Settings
from .odoo_client import Odoo

LOG = logging.getLogger("skill-odoo.write")


def run_create_bill(
    settings: Settings,
    *,
    partner_name: str,
    invoice_date: str,
    lines: list[dict[str, Any]] | str,
    ref: str | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """Create a vendor bill.

    ``lines`` may be a Python list (preferred) or a JSON string. Each line
    is ``{account_code, name?, quantity?, price_unit?, tax_ids?}``.

    Per Aloy: partner resolution is **exact match only**. If the partner
    isn't found, return code 2 with a "did you mean" suggestions list.
    No silent creation.
    """
    if isinstance(lines, str):
        try:
            lines = json.loads(lines)
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "error": f"invalid --lines JSON: {exc}",
                "code": 2,
                "error_kind": "bad_args",
            }
    if not isinstance(lines, list) or not lines:
        return {
            "ok": False,
            "error": "--lines must be a non-empty JSON array of line objects",
            "code": 2,
            "error_kind": "bad_args",
        }
    # Validate each line shape.
    for i, line in enumerate(lines):
        if not isinstance(line, dict) or "account_code" not in line:
            return {
                "ok": False,
                "error": f"--lines[{i}] must be an object with at least 'account_code'",
                "code": 2,
                "error_kind": "bad_args",
            }

    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    partner = odoo.get_partner_by_name(partner_name)
    if partner is None:
        suggestions = odoo.suggest_partners(partner_name, limit=5)
        return {
            "ok": False,
            "error": f"partner not found: {partner_name!r}",
            "code": 2,
            "error_kind": "not_found",
            "suggestions": [s["name"] for s in suggestions],
        }
    currency_id: int | None = None
    if currency and currency.upper() != settings.default_currency:
        currency_id = odoo.find_currency(currency)
        if currency_id is None:
            return {
                "ok": False,
                "error": f"currency not found: {currency!r}",
                "code": 4,
                "error_kind": "not_found",
            }
    try:
        move_id = odoo.create_bill(
            partner_id=partner["id"],
            invoice_date=invoice_date,
            lines=lines,
            ref=ref,
            currency_id=currency_id,
        )
    except RuntimeError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "code": 3,
            "error_kind": "odoo_error",
        }
    summary = odoo.read_move_summary(move_id)
    # Audit-log the write (input + response).
    try:
        write_audit(
            settings.audit_log_dir,
            {
                "event": "create_bill",
                "ok": True,
                "move_id": move_id,
                "partner_id": partner["id"],
                "invoice_date": invoice_date,
                "ref": ref,
                "currency": currency,
                "line_count": len(lines),
                "amount_total": summary.get("amount_total") if summary else None,
            },
        )
    except Exception:
        pass
    return {
        "ok": True,
        "move": summary,
    }


def run_post_move(
    settings: Settings,
    *,
    move_id: int,
) -> dict[str, Any]:
    """Confirm (post) a draft ``account.move``.

    If the move is already posted, returns ``{ok, move, note: 'already
    posted'}`` without error.
    """
    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    current = odoo.read_move_summary(move_id)
    if current is None:
        return {
            "ok": False,
            "error": f"move not found: id={move_id}",
            "code": 4,
            "error_kind": "not_found",
        }
    if current.get("state") == "posted":
        return {"ok": True, "move": current, "note": "already posted"}
    try:
        updated = odoo.post_move(move_id)
    except RuntimeError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "code": 3,
            "error_kind": "odoo_error",
        }
    try:
        write_audit(
            settings.audit_log_dir,
            {"event": "post_move", "ok": True, "move_id": move_id, "state": updated.get("state")},
        )
    except Exception:
        pass
    return {"ok": True, "move": updated}


def run_cancel_move(
    settings: Settings,
    *,
    move_id: int,
) -> dict[str, Any]:
    """Reset a posted ``account.move`` back to draft.

    Per Aloy: do NOT call ``button_cancel`` (which sets state to ``cancel``
    and reverses the journal entry). Use the Odoo method that reverts a
    posted move back to ``draft`` (``action_draft`` / ``button_draft``).

    If the move is already in draft, returns ``{ok, move, note: 'already
    draft'}`` without error.
    """
    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    current = odoo.read_move_summary(move_id)
    if current is None:
        return {
            "ok": False,
            "error": f"move not found: id={move_id}",
            "code": 4,
            "error_kind": "not_found",
        }
    if current.get("state") == "draft":
        return {"ok": True, "move": current, "note": "already draft"}
    try:
        updated = odoo.reset_move_to_draft(move_id)
    except RuntimeError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "code": 3,
            "error_kind": "odoo_error",
        }
    try:
        write_audit(
            settings.audit_log_dir,
            {"event": "cancel_move", "ok": True, "move_id": move_id, "state": updated.get("state")},
        )
    except Exception:
        pass
    return {"ok": True, "move": updated}
