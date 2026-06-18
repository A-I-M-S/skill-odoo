"""Probe and cache subcommands.

``probe`` authenticates against Odoo, fetches the near-static lookups (user,
company currency, journal, shareholder account, COA) via the local cache, and
returns a JSON status payload. This is the first subcommand the agent should
call when verifying a new ``.env`` or when diagnosing a stale cache.

``cache show|refresh|clear`` inspects and manages the same local cache file.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import write_audit
from .config import Settings
from .odoo_cache import (
    clear_cache,
    get_account,
    get_chart_of_accounts,
    get_company_currency,
    get_journal,
    get_user_info,
    read_cache,
)
from .odoo_client import Odoo

LOG = logging.getLogger("skill-odoo.probe")


def _shortlist_coa(coa: list[dict[str, Any]], *, limit: int = 80) -> list[dict[str, Any]]:
    """A shortlist of likely expense / asset accounts from the full COA.

    Used as a hint for the LLM classifier (issue #9/#12). For probe we just
    return the count; the full shortlist is built on demand when classifying.
    """
    interesting_types = {
        "expense",
        "expense_direct_cost",
        "expense_depreciation",
        "income",
        "income_other",
        "asset_current",
        "asset_non_current",
        "asset_receivable",
        "asset_cash",
        "asset_prepayment",
        "liability_current",
        "liability_non_current",
        "liability_payable",
    }
    out = [a for a in coa if (a.get("account_type") or "") in interesting_types]
    out.sort(key=lambda a: a.get("code") or "")
    return out[:limit]


def run_probe(
    settings: Settings,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Authenticate and return the probe payload. Does not print."""
    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    user, src_user = get_user_info(odoo, settings, force_refresh=force_refresh)
    co_id = user["company_id_int"]
    (co_ccy_id, co_ccy_name), src_ccy = get_company_currency(odoo, co_id, settings, force_refresh=force_refresh)
    journal, src_jrn = get_journal(
        odoo, settings.journal_code, settings.journal_type, settings, force_refresh=force_refresh
    )
    shareholder, src_acc = get_account(
        odoo, settings.shareholder_account_code, settings, force_refresh=force_refresh
    )
    coa_full, src_coa = get_chart_of_accounts(odoo, settings, force_refresh=force_refresh)
    coa_short = _shortlist_coa(coa_full)

    sources = {
        "user": src_user,
        "currency": src_ccy,
        "journal": src_jrn,
        "shareholder": src_acc,
        "coa": src_coa,
    }

    payload: dict[str, Any] = {
        "ok": True,
        "odoo": {
            "url": settings.odoo_url,
            "db": settings.odoo_db,
            "user": {
                "id": odoo.uid,
                "name": user.get("name"),
                "login": user.get("login"),
                "tz": user.get("tz"),
            },
            "company": {
                "id": co_id,
                "name": user.get("company_id", [None, None])[1] if isinstance(user.get("company_id"), list) else None,
                "currency": {"id": co_ccy_id, "name": co_ccy_name},
            },
        },
        "journal": {
            "id": journal.get("id"),
            "code": journal.get("code"),
            "name": journal.get("name"),
            "type": journal.get("type"),
        },
        "shareholder_account": {
            "id": shareholder.get("id"),
            "code": shareholder.get("code"),
            "name": shareholder.get("name"),
        },
        "coa": {
            "full_count": len(coa_full),
            "short_count": len(coa_short),
        },
        "cache": {
            "sources": sources,
        },
    }

    # Audit-log the probe, including the cache sources. No secrets in the event.
    try:
        write_audit(
            settings.audit_log_dir,
            {
                "event": "probe",
                "ok": True,
                "company_id": co_id,
                "user_id": odoo.uid,
                "journal_id": journal.get("id"),
                "shareholder_account_id": shareholder.get("id"),
                "coa_count": len(coa_full),
                "cache_sources": sources,
            },
        )
    except Exception as exc:  # pragma: no cover
        LOG.warning("audit write failed: %s", exc)

    return payload


def run_cache_show(settings: Settings) -> dict[str, Any]:
    """Return the raw cache contents for inspection."""
    raw = read_cache(settings)
    entries: list[dict[str, Any]] = []
    for key, entry in (raw.get("entries") or {}).items():
        entries.append({
            "key": key,
            "fetched_at": entry.get("fetched_at"),
            "value_preview": _preview(entry.get("value")),
        })
    return {
        "ok": True,
        "path": str(Path(settings.odoo_url or ".") / ".odoo_cache.json"),  # see _cache_path
        "meta": raw.get("meta") or {},
        "entry_count": len(entries),
        "entries": entries,
    }


def _cache_path(settings: Settings) -> Path:
    """Return the actual on-disk cache file path."""
    from .odoo_cache import _config  # local import to avoid leaking the helper
    return _config(settings).path


def _preview(value: Any) -> Any:
    """Compact preview of a cached value (avoid huge blobs in JSON output)."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"dict[{len(value)} keys]"
    return type(value).__name__


def run_cache_refresh(settings: Settings) -> dict[str, Any]:
    """Force-refresh the five near-static lookups and return the probe payload."""
    payload = run_probe(settings, force_refresh=True)
    payload["refreshed"] = True
    return payload


def run_cache_clear(settings: Settings) -> dict[str, Any]:
    """Delete the on-disk cache file."""
    path = _cache_path(settings)
    existed = path.exists()
    clear_cache(settings)
    return {
        "ok": True,
        "path": str(path),
        "existed": existed,
        "deleted": existed,
    }
