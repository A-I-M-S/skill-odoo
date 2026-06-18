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


def run_list_invoices(
    settings: Settings,
    *,
    partner_id: int | None = None,
    state: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List customer invoices (move_type='out_invoice')."""
    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    invoices = odoo.list_moves(
        move_type="out_invoice",
        partner_id=partner_id,
        state=state,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return {
        "ok": True,
        "count": len(invoices),
        "filters": {
            "partner_id": partner_id,
            "state": state,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        },
        "invoices": invoices,
    }


def run_list_bills(
    settings: Settings,
    *,
    partner_id: int | None = None,
    state: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List vendor bills (move_type='in_invoice')."""
    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    bills = odoo.list_moves(
        move_type="in_invoice",
        partner_id=partner_id,
        state=state,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return {
        "ok": True,
        "count": len(bills),
        "filters": {
            "partner_id": partner_id,
            "state": state,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        },
        "bills": bills,
    }


def run_list_partners(
    settings: Settings,
    *,
    name_contains: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Search res.partner by name (substring, case-insensitive)."""
    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    partners = odoo.search_partners(name_contains=name_contains, limit=limit)
    return {
        "ok": True,
        "count": len(partners),
        "filters": {"name_contains": name_contains, "limit": limit},
        "partners": partners,
    }


def run_search_read(
    settings: Settings,
    *,
    model: str,
    domain: str,
    fields: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Generic Odoo model query (escape hatch).

    Per Aloy's decision (issue #5): unrestricted. The agent is responsible
    for not reading sensitive models unless intentional. Every call is
    audit-logged with the model + domain summary.
    """
    import json as _json
    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    try:
        parsed_domain = _json.loads(domain) if isinstance(domain, str) else domain
        parsed_fields = (
            [f.strip() for f in fields.split(",") if f.strip()]
            if isinstance(fields, str) and fields
            else (fields or None)
        )
    except _json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"invalid --domain JSON: {exc}",
            "code": 2,
            "error_kind": "bad_args",
        }
    if not isinstance(parsed_domain, list):
        return {
            "ok": False,
            "error": "--domain must be a JSON array of clauses",
            "code": 2,
            "error_kind": "bad_args",
        }
    # Audit-log the call (model + domain summary, not full result).
    try:
        from .audit import write_audit
        write_audit(
            settings.audit_log_dir,
            {
                "event": "search_read",
                "model": model,
                "domain_summary": _summarize_domain(parsed_domain),
                "fields": parsed_fields,
                "limit": limit,
            },
        )
    except Exception:
        pass  # audit failure is non-fatal
    records = odoo.search_read(
        model=model,
        domain=parsed_domain,
        fields=parsed_fields,
        limit=limit,
    )
    return {
        "ok": True,
        "model": model,
        "count": len(records),
        "records": records,
    }


def _summarize_domain(domain: list[Any]) -> str:
    """Compact string summary of an Odoo domain for the audit log.

    Sensitive-shaped values (anything that's a long string, especially with
    hash / token / password semantics) are masked to ``***``. The point of
    the summary is to record WHICH fields were filtered, not the values.
    """
    def _summarize_clause(clause: Any) -> str:
        if isinstance(clause, str):
            return clause  # operator like "|", "&"
        if isinstance(clause, list) and len(clause) == 3:
            field, op, val = clause
            masked = _maybe_mask_value(field, val)
            return f"[{field!r}, {op!r}, {masked!r}]"
        return repr(clause)

    def _maybe_mask_value(field: str, val: Any) -> Any:
        if not isinstance(val, str):
            return val
        lower_field = field.lower()
        if any(k in lower_field for k in ("password", "token", "secret", "key", "login")):
            return "***"
        if len(val) > 40:
            return val[:20] + "..." + val[-5:]
        return val

    parts: list[str] = [_summarize_clause(c) for c in domain[:5]]
    suffix = "..." if len(domain) > 5 else ""
    return f"[{', '.join(parts)}{suffix}]"
