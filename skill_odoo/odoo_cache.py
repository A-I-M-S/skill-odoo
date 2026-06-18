"""Local file cache for near-static Odoo lookups.

We avoid hitting Odoo RPC on every CLI run / Telegram message for things that
change rarely: the Chart of Accounts, the logged-in user info, the company
currency, journals, and accounts looked up by code.

Cache layout (single JSON file, ``.odoo_cache.json`` by default):

    {
        "meta": {"url": "...", "db": "..."},
        "entries": {
            "<key>": {"fetched_at": "<iso-8601>", "value": <any-json>}
        }
    }

Invalidation:
- TTL expired (default 24h, configurable via ``ODOO_CACHE_TTL_SECONDS``).
- Cached ``meta.url`` / ``meta.db`` differ from current settings.
- ``force_refresh=True`` (used by the ``--refresh-cache`` flag).
- ``ODOO_CACHE_TTL_SECONDS=0`` disables caching entirely.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger("skill-odoo.cache")

DEFAULT_TTL_SECONDS = 24 * 3600
DEFAULT_CACHE_PATH = ".odoo_cache.json"
LEGACY_COA_CACHE_PATH = ".coa_cache.json"


@dataclass
class CacheConfig:
    path: Path
    ttl_seconds: int
    url: str
    db: str


def _config(settings: Any) -> CacheConfig:
    path = Path(os.getenv("ODOO_CACHE_PATH", DEFAULT_CACHE_PATH))
    try:
        ttl = int(os.getenv("ODOO_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    except ValueError:
        ttl = DEFAULT_TTL_SECONDS
    return CacheConfig(
        path=path,
        ttl_seconds=ttl,
        url=getattr(settings, "odoo_url", "") or "",
        db=getattr(settings, "odoo_db", "") or "",
    )


def _load_raw(cfg: CacheConfig) -> dict[str, Any]:
    # One-time migration: absorb legacy .coa_cache.json if present.
    legacy = Path(LEGACY_COA_CACHE_PATH)
    if not cfg.path.exists() and legacy.exists():
        try:
            old = json.loads(legacy.read_text())
            items = old.get("items") or []
            fetched_at = old.get("fetched_at") or datetime.now(timezone.utc).isoformat()
            migrated = {
                "meta": {"url": old.get("url", ""), "db": old.get("db", "")},
                "entries": {
                    "coa": {"fetched_at": fetched_at, "value": items},
                },
            }
            cfg.path.write_text(json.dumps(migrated, indent=2))
            legacy.unlink()
            LOG.info("migrated legacy %s -> %s", legacy, cfg.path)
        except Exception as exc:  # pragma: no cover
            LOG.warning("failed to migrate legacy coa cache: %s", exc)

    if not cfg.path.exists():
        return {"meta": {"url": cfg.url, "db": cfg.db}, "entries": {}}
    try:
        data = json.loads(cfg.path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("cache file unreadable (%s); ignoring", exc)
        return {"meta": {"url": cfg.url, "db": cfg.db}, "entries": {}}

    meta = data.get("meta") or {}
    if meta.get("url") != cfg.url or meta.get("db") != cfg.db:
        LOG.info("cache invalidated (meta mismatch): %s/%s vs %s/%s",
                 meta.get("url"), meta.get("db"), cfg.url, cfg.db)
        return {"meta": {"url": cfg.url, "db": cfg.db}, "entries": {}}

    data.setdefault("entries", {})
    return data


def _save_raw(cfg: CacheConfig, data: dict[str, Any]) -> None:
    try:
        cfg.path.parent.mkdir(parents=True, exist_ok=True)
        cfg.path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    except OSError as exc:
        LOG.warning("failed to write cache (%s); continuing", exc)


def _entry_is_fresh(entry: dict[str, Any], ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return False
    fetched = entry.get("fetched_at")
    if not fetched:
        return False
    try:
        ts = datetime.fromisoformat(fetched).timestamp()
    except ValueError:
        return False
    return (time.time() - ts) < ttl_seconds


def cached_call(
    settings: Any,
    key: str,
    fetch: Callable[[], Any],
    *,
    force_refresh: bool = False,
) -> tuple[Any, str]:
    """Return ``(value, source)`` where ``source`` is ``"cache"`` or ``"odoo"``.

    Looks up ``key`` in the shared cache file. On miss / expired / forced,
    calls ``fetch()`` and writes the result back.
    """
    cfg = _config(settings)

    if cfg.ttl_seconds <= 0 or force_refresh:
        value = fetch()
        if cfg.ttl_seconds > 0:
            data = _load_raw(cfg)
            data["entries"][key] = {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "value": value,
            }
            _save_raw(cfg, data)
        return value, "odoo"

    data = _load_raw(cfg)
    entry = data["entries"].get(key)
    if entry and _entry_is_fresh(entry, cfg.ttl_seconds):
        return entry["value"], "cache"

    value = fetch()
    data["entries"][key] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "value": value,
    }
    _save_raw(cfg, data)
    return value, "odoo"


# ---------- typed wrappers ----------

def get_user_info(odoo: Any, settings: Any, *, force_refresh: bool = False) -> tuple[dict[str, Any], str]:
    return cached_call(settings, "user_info", odoo.user_info, force_refresh=force_refresh)


def get_company_currency(odoo: Any, company_id: int, settings: Any, *, force_refresh: bool = False) -> tuple[tuple[int, str], str]:
    val, src = cached_call(
        settings,
        f"company_currency:{company_id}",
        lambda: odoo.company_currency(company_id),
        force_refresh=force_refresh,
    )
    # JSON round-trip turns tuples into lists.
    if isinstance(val, list) and len(val) == 2:
        val = (val[0], val[1])
    return val, src


def get_journal(odoo: Any, code: str, type_: str, settings: Any, *, force_refresh: bool = False) -> tuple[dict[str, Any], str]:
    return cached_call(
        settings,
        f"journal:{code}:{type_}",
        lambda: odoo.find_journal(code, type_),
        force_refresh=force_refresh,
    )


def get_account(odoo: Any, code: str, settings: Any, *, force_refresh: bool = False) -> tuple[dict[str, Any], str]:
    return cached_call(
        settings,
        f"account:{code}",
        lambda: odoo.find_account(code),
        force_refresh=force_refresh,
    )


def get_chart_of_accounts(odoo: Any, settings: Any, *, force_refresh: bool = False) -> tuple[list[dict[str, Any]], str]:
    return cached_call(
        settings,
        "coa",
        odoo.chart_of_accounts,
        force_refresh=force_refresh,
    )


def read_cache(settings: Any) -> dict[str, Any]:
    """Return the raw cache contents for inspection."""
    return _load_raw(_config(settings))


def clear_cache(settings: Any) -> None:
    cfg = _config(settings)
    if cfg.path.exists():
        cfg.path.unlink()
