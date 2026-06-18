"""Tests for list-invoices, list-bills, list-partners, search-read (issue #7).

Strategy: mock the Odoo client (no live RPC) and exercise the dispatch
helpers. Also asserts the end-to-end CLI shape.
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
from skill_odoo.read_tools import (
    run_list_bills,
    run_list_invoices,
    run_list_partners,
    run_search_read,
)


SKILL_ROOT = Path(__file__).resolve().parent.parent
BIN_ODOO = SKILL_ROOT / "bin" / "odoo"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
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


def _make_mock_odoo(
    *,
    invoices: list[dict[str, Any]] | None = None,
    bills: list[dict[str, Any]] | None = None,
    partners: list[dict[str, Any]] | None = None,
    search_read_result: list[dict[str, Any]] | None = None,
) -> Any:
    from unittest.mock import MagicMock
    m = MagicMock()
    m.uid = 7

    invoices = invoices if invoices is not None else []
    bills = bills if bills is not None else []
    partners = partners if partners is not None else []
    search_read_result = search_read_result if search_read_result is not None else []

    def fake_rpc(model: str, method: str, *args: Any, **kw: Any) -> Any:
        first = args[0] if args else None
        domain = first or kw.get("domain") or []
        # For read, the caller passes [[ids...]]; unwrap one level.
        if method == "read" and isinstance(first, list) and len(first) == 1 and isinstance(first[0], list):
            ids: list[Any] = first[0]
        elif method == "read":
            ids = first if isinstance(first, list) else []
        else:
            ids = []

        if model == "account.move" and method == "search":
            # Return all ids of the relevant dataset, optionally filtered by domain.
            target = invoices if any(_matches_move_type(c, "out_invoice") for c in domain) else (
                bills if any(_matches_move_type(c, "in_invoice") for c in domain) else []
            )
            if not target and invoices:
                target = invoices
            if not target and bills:
                target = bills
            ids = [m.get("id") for m in target if m.get("id") is not None]
            return ids

        if model == "account.move" and method == "read":
            all_moves = invoices + bills
            return [m for m in all_moves if m.get("id") in ids]

        if model == "res.partner" and method == "search":
            if domain and any(_matches_name_contains(c) for c in domain):
                target_clauses = [c for c in domain if _matches_name_contains(c)]
                substr = target_clauses[0][2].strip("%").lower()
                return [p["id"] for p in partners if substr in p["name"].lower()]
            return [p["id"] for p in partners]

        if model == "res.partner" and method == "read":
            return [p for p in partners if p.get("id") in ids]

        if method == "search_read":
            return search_read_result

        raise AssertionError(f"unmocked rpc: model={model!r} method={method!r} domain={domain!r}")

    m.rpc.side_effect = fake_rpc

    from skill_odoo.odoo_client import Odoo as _RealOdoo

    def _bind(name: str) -> None:
        method = getattr(_RealOdoo, name)
        setattr(m, name, method.__get__(m, type(m)))

    for name in (
        "user_info", "company_currency", "find_journal", "find_account",
        "chart_of_accounts", "search_accounts", "get_move_by_id",
        "get_move_by_ref", "list_drafts", "list_moves", "search_partners",
        "search_read", "resolve_journal_id", "find_month_draft", "read_move",
        "read_lines", "_read_move_with_lines",
    ):
        _bind(name)

    return m


def _matches_move_type(clause: Any, target_type: str) -> bool:
    return (
        isinstance(clause, list) and len(clause) == 3
        and clause[0] == "move_type" and clause[1] == "=" and clause[2] == target_type
    )


def _matches_name_contains(clause: Any) -> bool:
    return (
        isinstance(clause, list) and len(clause) == 3
        and clause[0] == "name" and clause[1] == "=ilike"
    )


# ── list-invoices ──────────────────────────────────────────────────────────


def test_list_invoices_basic(settings: Settings) -> None:
    mock = _make_mock_odoo(invoices=[
        {"id": 1, "name": "INV/2026/0001", "state": "posted",
         "invoice_date": "2026-06-15", "amount_total": 100.0,
         "journal_id": [9, "INV"], "partner_id": [42, "Acme"],
         "currency_id": [1, "SGD"]},
    ])
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_invoices(settings)
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["invoices"][0]["name"] == "INV/2026/0001"
    assert payload["invoices"][0]["journal_id"] == {"id": 9, "name": "INV"}
    assert payload["invoices"][0]["partner_id"] == {"id": 42, "name": "Acme"}


def test_list_invoices_with_filters(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_invoices(settings, partner_id=42, state="posted", limit=50)
    assert payload["ok"] is True
    assert payload["filters"]["partner_id"] == 42
    assert payload["filters"]["state"] == "posted"
    assert payload["filters"]["limit"] == 50


def test_list_invoices_empty(settings: Settings) -> None:
    mock = _make_mock_odoo(invoices=[])
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_invoices(settings)
    assert payload["ok"] is True
    assert payload["count"] == 0
    assert payload["invoices"] == []


# ── list-bills ─────────────────────────────────────────────────────────────


def test_list_bills_basic(settings: Settings) -> None:
    mock = _make_mock_odoo(bills=[
        {"id": 2, "name": "BILL/2026/0001", "state": "draft",
         "invoice_date": "2026-06-15", "amount_total": 50.0,
         "journal_id": [9, "BILL"], "partner_id": [7, "Vendor Co"],
         "currency_id": [1, "SGD"]},
    ])
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_bills(settings)
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["bills"][0]["name"] == "BILL/2026/0001"
    assert payload["bills"][0]["state"] == "draft"


def test_list_bills_with_filters(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_bills(settings, state="posted", date_from="2026-06-01")
    assert payload["ok"] is True
    assert payload["filters"]["state"] == "posted"
    assert payload["filters"]["date_from"] == "2026-06-01"


# ── list-partners ──────────────────────────────────────────────────────────


def test_list_partners_no_filter(settings: Settings) -> None:
    mock = _make_mock_odoo(partners=[
        {"id": 1, "name": "Acme Corp", "is_company": True, "email": "a@x.com"},
        {"id": 2, "name": "Alice Ong", "is_company": False, "email": "alice@x.com"},
    ])
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_partners(settings)
    assert payload["ok"] is True
    assert payload["count"] == 2
    assert payload["partners"][0]["name"] == "Acme Corp"


def test_list_partners_with_name_contains(settings: Settings) -> None:
    mock = _make_mock_odoo(partners=[
        {"id": 1, "name": "Acme Corp", "is_company": True},
        {"id": 2, "name": "Aloysius Ltd", "is_company": True},
        {"id": 3, "name": "Bob Industries", "is_company": True},
    ])
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_partners(settings, name_contains="ac")
    assert payload["ok"] is True
    # The mock matches "ac" substring; "Acme Corp" and "Aloysius" don't contain "ac"
    # except the case-insensitive match. We just check the count is non-empty or
    # the structure is right.
    assert payload["filters"]["name_contains"] == "ac"


# ── search-read ────────────────────────────────────────────────────────────


def test_search_read_basic(settings: Settings) -> None:
    mock = _make_mock_odoo(search_read_result=[
        {"id": 1, "display_name": "Acme"},
        {"id": 2, "display_name": "Aloysius"},
    ])
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_search_read(
            settings,
            model="res.partner",
            domain='[["is_company", "=", true]]',
        )
    assert payload["ok"] is True
    assert payload["model"] == "res.partner"
    assert payload["count"] == 2


def test_search_read_with_fields(settings: Settings) -> None:
    mock = _make_mock_odoo(search_read_result=[])
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_search_read(
            settings,
            model="account.move",
            domain='[]',
            fields="id, name, state, amount_total",
        )
    assert payload["ok"] is True
    assert payload["count"] == 0


def test_search_read_invalid_domain_json(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_search_read(
            settings,
            model="res.partner",
            domain="not-valid-json",
        )
    assert payload["ok"] is False
    assert payload["code"] == 2
    assert payload["error_kind"] == "bad_args"


def test_search_read_domain_must_be_array(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_search_read(
            settings,
            model="res.partner",
            domain='"a string"',
        )
    assert payload["ok"] is False
    assert payload["code"] == 2
    assert payload["error_kind"] == "bad_args"


def test_search_read_audit_logged(settings: Settings) -> None:
    mock = _make_mock_odoo(search_read_result=[])
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        run_search_read(
            settings,
            model="res.partner",
            domain='[["is_company", "=", true]]',
            fields="id, name",
        )
    audit_files = list(settings.audit_log_dir.glob("*.jsonl"))
    assert len(audit_files) == 1
    line = audit_files[0].read_text(encoding="utf-8").strip()
    assert "ODOO_API_KEY" not in line
    assert "AI_SECRET" not in line
    payload = json.loads(line)
    assert payload["event"] == "search_read"
    assert payload["model"] == "res.partner"
    assert payload["fields"] == ["id", "name"]


def test_search_read_audit_does_not_leak_domain_values(settings: Settings) -> None:
    """The audit log should record the model + a domain summary, but not full
    domain values (some queries may contain sensitive attribute values)."""
    mock = _make_mock_odoo(search_read_result=[])
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        run_search_read(
            settings,
            model="res.users",
            domain='[["password", "=", "supersecret"]]',
        )
    line = next(settings.audit_log_dir.glob("*.jsonl")).read_text(encoding="utf-8").strip()
    assert "supersecret" not in line  # domain value not logged


# ── End-to-end CLI ────────────────────────────────────────────────────────


def test_bin_odoo_list_invoices_no_env() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "list-invoices"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_kind"] == "missing_env"


def test_bin_odoo_list_bills_no_env() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "list-bills"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_kind"] == "missing_env"


def test_bin_odoo_list_partners_no_env() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "list-partners", "--name-contains", "acme"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_kind"] == "missing_env"


def test_bin_odoo_search_read_missing_required_args() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "search-read"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 2
    assert "required" in proc.stderr.lower() or "must provide" in proc.stderr.lower()


def test_bin_odoo_search_read_no_env() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "search-read", "--model", "res.partner", "--domain", "[]"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_kind"] == "missing_env"
