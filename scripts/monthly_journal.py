"""Monthly draft-entry consolidation.

Every receipt becomes one debit line; a single credit line to the Shareholder
Notes Payable account balances the whole move.  We never create a second draft
for the same month — we adopt or update the existing one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .odoo_client import Odoo


@dataclass
class MonthDraft:
    move_id: int
    ref: str
    name_set: bool
    journal_id: int
    journal_code: str


def ref_for(today: date, fmt: str = "%y%b") -> str:
    """Format a month ref like ``26May``."""
    return today.strftime(fmt)


def ensure_month_draft(
    odoo: Odoo,
    *,
    journal_id: int,
    journal_code: str,
    today: date,
    ref: str,
    name_from_ref: bool,
    shareholder_account_id: int,
) -> MonthDraft:
    """Find an adoptable draft for the month or create a fresh empty one.

    Always ensures the move's ``ref`` (and ``name`` if requested) equals ``ref``.
    """
    move_id = odoo.find_month_draft(journal_id=journal_id, ref=ref, today=today)
    if move_id is None:
        # Create a placeholder zero-amount move so we have something to append to.
        zero_line = {
            "account_id": shareholder_account_id,
            "name": f"{ref} – opening placeholder",
            "debit": 0.0,
            "credit": 0.0,
        }
        move_id = odoo.create_move(
            journal_id=journal_id,
            ref=ref,
            name=ref if name_from_ref else None,
            on=today,
            lines=[zero_line, dict(zero_line)],   # 2 zero-lines so Odoo accepts the move
        )
    # Re-stamp ref/name in case we adopted an empty draft.
    update: dict[str, Any] = {"ref": ref}
    current = odoo.read_move(move_id)
    if name_from_ref and current.get("name") in (False, "/", ref) and current.get("name") != ref:
        update["name"] = ref
    odoo.write_move(move_id, update)
    return MonthDraft(
        move_id=move_id,
        ref=ref,
        name_set=name_from_ref,
        journal_id=journal_id,
        journal_code=journal_code,
    )


def rebuild_lines_with_shareholder_balance(
    odoo: Odoo,
    *,
    move_id: int,
    shareholder_account_id: int,
    ref: str,
    new_debit_lines: list[dict[str, Any]],
) -> dict[str, float]:
    """Read existing non-shareholder debit lines, add the new ones, recompute credit.

    Returns ``{"total_debit": x, "credit": x}``.
    """
    current = odoo.read_move(move_id)
    line_ids = current.get("line_ids") or []
    existing = odoo.read_lines(line_ids)

    kept_debits: list[dict[str, Any]] = []
    for ln in existing:
        acc_id = ln.get("account_id")[0] if isinstance(ln.get("account_id"), list) else ln.get("account_id")
        if acc_id == shareholder_account_id:
            continue  # we'll rewrite the credit line
        debit = float(ln.get("debit") or 0.0)
        credit = float(ln.get("credit") or 0.0)
        if debit == 0 and credit == 0:
            continue  # drop zero placeholders
        kept_debits.append(
            {"account_id": acc_id, "name": ln.get("name") or "", "debit": debit, "credit": credit}
        )

    all_debits = kept_debits + new_debit_lines
    total_debit = round(sum(line["debit"] for line in all_debits), 2)
    credit_line = {
        "account_id": shareholder_account_id,
        "name": f"{ref} – Shareholder reimbursement",
        "debit": 0.0,
        "credit": total_debit,
    }
    odoo.replace_move_lines(move_id, all_debits + [credit_line])
    return {"total_debit": total_debit, "credit": total_debit}
