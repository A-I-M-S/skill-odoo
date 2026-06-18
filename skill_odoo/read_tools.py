"""Read-side Odoo tools: chart-of-accounts, get-move, list-drafts.

Issue A-I-M-S/skill-odoo#6. The dispatch lives here; ``__main__.py`` calls
into these functions and prints the result as JSON.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .config import Settings
from .odoo_cache import get_chart_of_accounts
from .odoo_client import Odoo

LOG = logging.getLogger("skill-odoo.read")


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def _first_of_next_month(d: date) -> date:
    if d.month == 12:
        return d.replace(year=d.year + 1, month=1, day=1)
    return d.replace(month=d.month + 1, day=1)


def run_chart_of_accounts(
    settings: Settings,
    *,
    code_prefix: str | None = None,
    account_type: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """List accounts (filterable by code prefix or account type).

    With no filter: returns the full COA (uses the local cache, same as probe).
    With a filter: does a direct Odoo search using ``=ilike`` for the prefix
    and exact match for the type (cache is not used here because filtered
    queries are dynamic and not worth caching).
    """
    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    if code_prefix is None and account_type is None:
        items, source = get_chart_of_accounts(odoo, settings)
        items = items[:limit]
    else:
        items = odoo.search_accounts(
            code_prefix=code_prefix,
            account_type=account_type,
            limit=limit,
        )
        source = "odoo"
    return {
        "ok": True,
        "count": len(items),
        "source": source,
        "filters": {"code_prefix": code_prefix, "type": account_type, "limit": limit},
        "items": items,
    }


def run_get_move(
    settings: Settings,
    *,
    move_id: int | None = None,
    ref: str | None = None,
) -> dict[str, Any]:
    """Fetch a single ``account.move`` by id or by ref/name.

    Exactly one of ``move_id`` or ``ref`` must be provided. If neither is
    given, the caller should have already raised — this function asserts.
    """
    if (move_id is None) == (ref is None):
        raise ValueError("exactly one of --id or --ref must be given")
    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    if move_id is not None:
        move = odoo.get_move_by_id(move_id)
    else:
        assert ref is not None
        move = odoo.get_move_by_ref(ref)
    if move is None:
        return {
            "ok": False,
            "error": f"move not found: id={move_id!r} ref={ref!r}",
            "code": 4,
            "error_kind": "not_found",
        }
    return {"ok": True, "move": move}


def run_list_drafts(
    settings: Settings,
    *,
    ref: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    journal_code: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List draft ``account.move`` records (state='draft', move_type='entry').

    Date defaults: when no date filter is given, this function defaults to
    "this month" (the agent's most common request). The caller can override
    with explicit ``--date-from`` / ``--date-to`` flags.
    """
    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    journal_id: int | None = None
    if journal_code is not None:
        journal_id = odoo.resolve_journal_id(journal_code, settings.journal_type)
        if journal_id is None:
            return {
                "ok": False,
                "error": f"journal not found: code={journal_code!r}",
                "code": 4,
                "error_kind": "not_found",
            }
    if date_from is None and date_to is None:
        today = date.today()
        first = _first_of_month(today)
        next_month = _first_of_next_month(today)
        from datetime import timedelta
        last = next_month - timedelta(days=1)
        date_from_s = first.isoformat()
        date_to_s = last.isoformat()
    else:
        date_from_s = date_from
        date_to_s = date_to
    moves = odoo.list_drafts(
        ref=ref,
        date_from=date_from_s,
        date_to=date_to_s,
        journal_id=journal_id,
        limit=limit,
    )
    return {
        "ok": True,
        "count": len(moves),
        "filters": {
            "ref": ref,
            "date_from": date_from_s,
            "date_to": date_to_s,
            "journal_code": journal_code,
            "limit": limit,
        },
        "moves": moves,
    }
