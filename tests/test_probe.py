"""Tests for the probe and cache subcommands.

Strategy: skip-on-no-env, otherwise mock the Odoo client and exercise the
``probe.py`` helpers directly. The end-to-end against a real Odoo is covered
by the manual ``bin/odoo probe`` check (no test runs a live RPC).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from skill_odoo.config import Settings
from skill_odoo.probe import (
    _shortlist_coa,
    run_cache_clear,
    run_cache_refresh,
    run_cache_show,
    run_probe,
)


SKILL_ROOT = Path(__file__).resolve().parent.parent
BIN_ODOO = SKILL_ROOT / "bin" / "odoo"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings instance pointed at temp dirs (no real Odoo)."""
    monkeypatch.setenv("ODOO_CACHE_PATH", str(tmp_path / ".odoo_cache.json"))
    monkeypatch.setenv("ODOO_CACHE_TTL_SECONDS", "86400")
    return Settings(
        odoo_url="https://test.odoo.com",
        odoo_db="testdb",
        odoo_login="alice@example.com",
        odoo_api_key="***",  # noqa: S106
        shareholder_account_code="202040",
        journal_code="MISC",
        journal_type="general",
        monthly_consolidate=True,
        move_ref_format="%y%b",
        move_name_from_ref=True,
        default_currency="SGD",
        fx_provider="frankfurter",
        fx_base_currency="SGD",
        ocr_provider="openai",
        ocr_base_url="https://openrouter.ai/api/v1",
        ocr_api_key="***",  # noqa: S106
        ocr_model="google/gemma-4-26b-a4b-it:free",
        receipts_inbox=tmp_path / "inbox",
        receipts_processed_delete=True,
        ai_chat_url="https://openrouter.ai/api/v1/chat/completions",
        ai_model="google/gemma-4-26b-a4b-it:free",
        ai_secret="***",  # noqa: S106
        ai_provider_order="",
        audit_log_dir=tmp_path / "audit",
    )


@pytest.fixture
def mock_odoo() -> Any:
    """A mock Odoo client that returns realistic data for each method."""
    from unittest.mock import MagicMock
    m = MagicMock()
    m.uid = 7
    m.user_info.return_value = {
        "name": "Alice Ong",
        "login": "alice@example.com",
        "tz": "Asia/Singapore",
        "company_id": [42, "Aloysius Pte Ltd"],
        "company_id_int": 42,
    }
    m.company_currency.return_value = (1, "SGD")
    m.find_journal.return_value = {"id": 9, "code": "MISC", "name": "Miscellaneous Operations", "type": "general"}
    m.find_account.return_value = {"id": 202, "code": "202040", "name": "Shareholder Notes Payable"}
    m.chart_of_accounts.return_value = [
        {"id": 1, "code": "100000", "name": "Cash", "account_type": "asset_cash"},
        {"id": 2, "code": "200000", "name": "Trade Payables", "account_type": "liability_payable"},
        {"id": 3, "code": "202040", "name": "Shareholder Notes Payable", "account_type": "liability_payable"},
        {"id": 4, "code": "400000", "name": "Sales", "account_type": "income"},
        {"id": 5, "code": "500000", "name": "Cost of Sales", "account_type": "expense_direct_cost"},
        {"id": 6, "code": "624000", "name": "Office Supplies", "account_type": "expense"},
        {"id": 7, "code": "625000", "name": "Travel", "account_type": "expense"},
        {"id": 8, "code": "900000", "name": "Tax", "account_type": "liability_current"},
    ]
    return m


# ── _shortlist_coa ──────────────────────────────────────────────────────────


def test_shortlist_coa_picks_relevant_types() -> None:
    coa = [
        {"code": "1", "name": "Cash", "account_type": "asset_cash"},
        {"code": "2", "name": "Payable", "account_type": "liability_payable"},
        {"code": "3", "name": "Office", "account_type": "expense"},
        {"code": "4", "name": "Other", "account_type": "off_balance"},
    ]
    out = _shortlist_coa(coa)
    codes = [a["code"] for a in out]
    assert codes == ["1", "2", "3"]


def test_shortlist_coa_respects_limit() -> None:
    coa = [
        {"code": str(i), "name": f"X{i}", "account_type": "expense"} for i in range(200)
    ]
    out = _shortlist_coa(coa, limit=10)
    assert len(out) == 10


def test_shortlist_coa_sorted_by_code() -> None:
    coa = [
        {"code": "999", "name": "Z", "account_type": "expense"},
        {"code": "100", "name": "A", "account_type": "expense"},
        {"code": "500", "name": "M", "account_type": "expense"},
    ]
    out = _shortlist_coa(coa)
    codes = [a["code"] for a in out]
    assert codes == ["100", "500", "999"]


# ── run_probe ───────────────────────────────────────────────────────────────


def test_run_probe_returns_full_payload(settings: Settings, mock_odoo: Any) -> None:
    with patch("skill_odoo.probe.Odoo", return_value=mock_odoo):
        payload = run_probe(settings, force_refresh=True)
    assert payload["ok"] is True
    assert payload["odoo"]["url"] == "https://test.odoo.com"
    assert payload["odoo"]["user"]["id"] == 7
    assert payload["odoo"]["user"]["name"] == "Alice Ong"
    assert payload["odoo"]["company"]["id"] == 42
    assert payload["odoo"]["company"]["currency"] == {"id": 1, "name": "SGD"}
    assert payload["journal"]["code"] == "MISC"
    assert payload["shareholder_account"]["code"] == "202040"
    assert payload["coa"]["full_count"] == 8
    assert payload["coa"]["short_count"] >= 4  # asset + liability + 2 expenses
    # All sources should be "odoo" on force_refresh
    assert all(v == "odoo" for v in payload["cache"]["sources"].values())


def test_run_probe_uses_cache_on_second_call(settings: Settings, mock_odoo: Any) -> None:
    """First call writes cache, second call reads from cache for unchanged data."""
    with patch("skill_odoo.probe.Odoo", return_value=mock_odoo):
        first = run_probe(settings, force_refresh=True)
        # second call without force_refresh: everything is fresh and comes from cache
        second = run_probe(settings, force_refresh=False)
    assert first["ok"] is True
    assert second["ok"] is True
    for key in ["user", "currency", "journal", "shareholder", "coa"]:
        assert second["cache"]["sources"][key] == "cache", f"{key} not from cache"


def test_run_probe_audit_log_written(settings: Settings, mock_odoo: Any) -> None:
    with patch("skill_odoo.probe.Odoo", return_value=mock_odoo):
        run_probe(settings, force_refresh=True)
    audit_files = list(settings.audit_log_dir.glob("*.jsonl"))
    assert len(audit_files) == 1
    line = audit_files[0].read_text(encoding="utf-8").strip()
    # No secrets in the audit line
    assert "***" not in line
    assert "ODOO_API_KEY" not in line
    assert "AI_SECRET" not in line
    payload = json.loads(line)
    assert payload["event"] == "probe"
    assert payload["ok"] is True
    assert "cache_sources" in payload


# ── cache subcommand helpers ─────────────────────────────────────────────────


def test_cache_show_when_no_cache(settings: Settings) -> None:
    payload = run_cache_show(settings)
    assert payload["ok"] is True
    assert payload["entry_count"] == 0
    assert payload["entries"] == []


def test_cache_refresh_writes_cache(settings: Settings, mock_odoo: Any) -> None:
    with patch("skill_odoo.probe.Odoo", return_value=mock_odoo):
        payload = run_cache_refresh(settings)
    assert payload["ok"] is True
    assert payload.get("refreshed") is True
    # Now the cache should be populated.
    show = run_cache_show(settings)
    assert show["entry_count"] >= 5  # user, currency, journal, shareholder, coa
    keys = {e["key"] for e in show["entries"]}
    assert "user_info" in keys
    assert "coa" in keys


def test_cache_clear_deletes_file(settings: Settings, mock_odoo: Any) -> None:
    with patch("skill_odoo.probe.Odoo", return_value=mock_odoo):
        run_cache_refresh(settings)
    # The cache should now exist on disk.
    from skill_odoo.odoo_cache import _config
    cache_path = _config(settings).path
    assert cache_path.exists()
    payload = run_cache_clear(settings)
    assert payload["ok"] is True
    assert payload["existed"] is True
    assert payload["deleted"] is True
    assert not cache_path.exists()


def test_cache_clear_when_no_cache(settings: Settings) -> None:
    """clearing a non-existent cache is a no-op success."""
    payload = run_cache_clear(settings)
    assert payload["ok"] is True
    assert payload["existed"] is False
    assert payload["deleted"] is False


# ── End-to-end via bin/odoo CLI ─────────────────────────────────────────────


@pytest.mark.skipif(
    not all(os.environ.get(k) for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY")),
    reason="real Odoo env vars not set",
)
def test_bin_odoo_probe_returns_odoo_error_when_creds_invalid() -> None:
    """When env is set but creds are bogus, probe should return a structured 503-ish error
    instead of crashing."""
    env = os.environ.copy()
    env["ODOO_API_KEY"] = "definitely-wrong-key"
    proc = subprocess.run(
        [str(BIN_ODOO), "probe", "--refresh-cache"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    # If auth fails we exit with the odoo_error code (3) and a JSON body.
    assert proc.returncode != 0
    assert proc.stdout.startswith("{")
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["code"] in (3, 5)


def test_bin_odoo_probe_missing_env_returns_error() -> None:
    """When .env is missing, probe should return code 5 (missing_env) and not crash."""
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "probe"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["code"] == 5
    assert payload["error_kind"] == "missing_env"
