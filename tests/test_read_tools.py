"""Tests for chart-of-accounts, get-move, list-drafts (issue A-I-M-S/skill-odoo#6).

Strategy: mock the Odoo client (no live RPC), then exercise the dispatch
helpers in ``skill_odoo.read_tools``. Also asserts the end-to-end CLI shape
(missing env → code 5, bad args → code 2, etc.).
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
    run_chart_of_accounts,
    run_get_move,
    run_list_drafts,
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
    coa: list[dict[str, Any]] | None = None,
    moves_by_id: dict[int, dict[str, Any]] | None = None,
    move_search_ref: dict[str, int] | None = None,
    draft_search: list[int] | None = None,
    journals_by_code: dict[str, dict[str, Any]] | None = None,
) -> Any:
    from unittest.mock import MagicMock
    m = MagicMock()
    m.uid = 7
    coa = coa or [
        {"id": 1, "code": "100000", "name": "Cash", "account_type": "asset_cash"},
        {"id": 2, "code": "200000", "name": "Payables", "account_type": "liability_payable"},
        {"id": 3, "code": "202040", "name": "Shareholder", "account_type": "liability_payable"},
        {"id": 4, "code": "624000", "name": "Office Supplies", "account_type": "expense"},
        {"id": 5, "code": "625000", "name": "Travel", "account_type": "expense"},
        {"id": 6, "code": "900000", "name": "Tax", "account_type": "liability_current"},
    ]
    moves_by_id = moves_by_id or {}
    journals_by_code = journals_by_code or {"MISC": {"id": 9, "code": "MISC", "name": "Misc", "type": "general"}}

    def fake_rpc(model: str, method: str, *args: Any, **kw: Any) -> Any:
        # args[0] for search is the domain; for read it's [[id, id, ...]] (a list-of-list).
        first = args[0] if args else None
        domain = first or kw.get("domain") or []
        # For read, the caller passes [[ids...]]; unwrap one level.
        if method == "read" and isinstance(first, list) and len(first) == 1 and isinstance(first[0], list):
            ids = first[0]
        else:
            ids = first if method == "read" else []

        if model == "account.account" and method == "search":
            out = []
            for a in coa:
                if not domain:
                    out.append(a["id"])
                    continue
                all_match = True
                for clause in domain:
                    if isinstance(clause, list) and len(clause) == 3:
                        field, op, val = clause
                        if field == "code" and op == "=ilike":
                            prefix = val.rstrip("%")
                            if not (a.get("code") or "").startswith(prefix):
                                all_match = False
                                break
                        if field == "account_type" and op == "=" and a.get("account_type") != val:
                            all_match = False
                            break
                if all_match:
                    out.append(a["id"])
            return out

        if model == "account.account" and method == "read":
            return [a for a in coa if a["id"] in ids]

        if model == "account.move" and method == "search":
            for clause in domain:
                if isinstance(clause, list) and len(clause) == 3 and clause[0] == "id" and clause[1] == "=":
                    return [clause[2]] if clause[2] in moves_by_id else []
            for clause in domain:
                if isinstance(clause, list) and len(clause) == 3 and clause[0] in ("ref", "name") and clause[1] == "=":
                    rid = (move_search_ref or {}).get(clause[2])
                    return [rid] if rid else []
            if draft_search is not None:
                return draft_search
            return []

        if model == "account.move" and method == "read":
            return [
                {"id": i, "name": "", "ref": False, "state": "draft", "date": "2026-06-15",
                 "journal_id": [9, "MISC"], "line_ids": [], "amount_total": 0.0,
                 **(moves_by_id.get(i) or {})}
                for i in ids
            ]

        if model == "account.move.line" and method == "read":
            return []

        if model == "account.journal" and method == "search":
            for clause in domain:
                if isinstance(clause, list) and len(clause) == 3 and clause[0] == "code" and clause[1] == "=":
                    j = journals_by_code.get(clause[2])
                    return [j["id"]] if j else []
            return []

        if model == "account.journal" and method == "read":
            return [j for j in journals_by_code.values() if j.get("id") in ids]

        raise AssertionError(f"unmocked rpc: model={model!r} method={method!r} domain={domain!r}")

    m.rpc.side_effect = fake_rpc

    # Bind real method implementations on the mock so that calls like
    # `m.list_drafts(...)` go through the real Odoo class logic, which in
    # turn calls `self.rpc(...)` (the fake_rpc above). Without this, the
    # MagicMock would auto-generate `m.list_drafts` as a child MagicMock and
    # bypass the real method entirely.
    from skill_odoo.odoo_client import Odoo as _RealOdoo

    def _bind(name: str) -> None:
        method = getattr(_RealOdoo, name)
        # Bind the unbound function to the mock instance.
        setattr(m, name, method.__get__(m, type(m)))

    for name in (
        "user_info",
        "company_currency",
        "find_journal",
        "find_account",
        "chart_of_accounts",
        "search_accounts",
        "get_move_by_id",
        "get_move_by_ref",
        "list_drafts",
        "resolve_journal_id",
        "find_month_draft",
        "read_move",
        "read_lines",
        "_read_move_with_lines",
    ):
        _bind(name)

    return m


# ── chart-of-accounts ──────────────────────────────────────────────────────


def test_chart_of_accounts_no_filter_uses_cache(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_chart_of_accounts(settings)
    assert payload["ok"] is True
    assert payload["count"] >= 5
    assert payload["source"] in ("cache", "odoo")
    assert payload["filters"] == {"code_prefix": None, "type": None, "limit": 500}
    assert all("code" in a and "name" in a and "account_type" in a for a in payload["items"])


def test_chart_of_accounts_with_prefix_filter(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_chart_of_accounts(settings, code_prefix="6")
    assert payload["ok"] is True
    assert payload["source"] == "odoo"  # filtered queries go straight to Odoo
    codes = [a["code"] for a in payload["items"]]
    assert all(c.startswith("6") for c in codes)
    assert "624000" in codes


def test_chart_of_accounts_with_type_filter(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_chart_of_accounts(settings, account_type="expense")
    assert payload["ok"] is True
    assert all(a["account_type"] == "expense" for a in payload["items"])


def test_chart_of_accounts_with_both_filters(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_chart_of_accounts(settings, code_prefix="6", account_type="expense")
    assert payload["ok"] is True
    for a in payload["items"]:
        assert a["code"].startswith("6")
        assert a["account_type"] == "expense"


def test_chart_of_accounts_respects_limit(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_chart_of_accounts(settings, limit=2)
    assert len(payload["items"]) == 2


# ── get-move ───────────────────────────────────────────────────────────────


def test_get_move_by_id(settings: Settings) -> None:
    mock = _make_mock_odoo(
        moves_by_id={123: {
            "name": "MISC/2026/0001",
            "ref": "26May",
            "state": "draft",
            "date": "2026-05-15",
            "amount_total": 123.45,
        }},
    )
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_get_move(settings, move_id=123)
    assert payload["ok"] is True
    assert payload["move"]["id"] == 123
    assert payload["move"]["ref"] == "26May"
    assert payload["move"]["state"] == "draft"
    assert payload["move"]["date"] == "2026-05-15"
    assert payload["move"]["amount_total"] == 123.45
    assert isinstance(payload["move"]["lines"], list)


def test_get_move_by_id_not_found(settings: Settings) -> None:
    mock = _make_mock_odoo(moves_by_id={})
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_get_move(settings, move_id=999)
    assert payload["ok"] is False
    assert payload["code"] == 4
    assert payload["error_kind"] == "not_found"


def test_get_move_by_ref(settings: Settings) -> None:
    mock = _make_mock_odoo(
        moves_by_id={456: {
            "name": "MISC/2026/0042",
            "ref": "26Jun",
            "state": "draft",
            "date": "2026-06-15",
            "amount_total": 200.0,
        }},
        move_search_ref={"26Jun": 456},
    )
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_get_move(settings, ref="26Jun")
    assert payload["ok"] is True
    assert payload["move"]["id"] == 456
    assert payload["move"]["ref"] == "26Jun"


def test_get_move_requires_exactly_one_id_or_ref(settings: Settings) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        run_get_move(settings)
    with pytest.raises(ValueError, match="exactly one"):
        run_get_move(settings, move_id=1, ref="x")


# ── list-drafts ────────────────────────────────────────────────────────────


def test_list_drafts_defaults_to_this_month(settings: Settings) -> None:
    mock = _make_mock_odoo(
        draft_search=[10, 11],
        moves_by_id={
            10: {"name": "MISC/2026/0010", "ref": "26May", "state": "draft",
                 "date": "2026-05-15", "amount_total": 50.0},
            11: {"name": "MISC/2026/0011", "ref": "26May", "state": "draft",
                 "date": "2026-05-16", "amount_total": 75.0},
        },
    )
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_drafts(settings)
    assert payload["ok"] is True
    assert payload["count"] == 2
    from datetime import date
    today = date.today()
    expected_first = today.replace(day=1).isoformat()
    assert payload["filters"]["date_from"] == expected_first
    assert payload["filters"]["date_to"] >= expected_first
    assert payload["filters"]["ref"] is None
    assert payload["filters"]["journal_code"] is None


def test_list_drafts_with_explicit_dates(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_drafts(settings, date_from="2026-06-01", date_to="2026-06-30")
    assert payload["ok"] is True
    assert payload["filters"]["date_from"] == "2026-06-01"
    assert payload["filters"]["date_to"] == "2026-06-30"


def test_list_drafts_with_ref(settings: Settings) -> None:
    mock = _make_mock_odoo(
        move_search_ref={"26Jun": 42},
        moves_by_id={
            42: {"name": "MISC/2026/0042", "ref": "26Jun", "state": "draft",
                 "date": "2026-06-15", "amount_total": 100.0},
        },
    )
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_drafts(settings, ref="26Jun")
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["moves"][0]["ref"] == "26Jun"


def test_list_drafts_with_unknown_journal(settings: Settings) -> None:
    mock = _make_mock_odoo(journals_by_code={})  # no MISC journal
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_drafts(settings, journal_code="NOPE")
    assert payload["ok"] is False
    assert payload["code"] == 4
    assert payload["error_kind"] == "not_found"


def test_list_drafts_empty_result(settings: Settings) -> None:
    mock = _make_mock_odoo(draft_search=[])
    with patch("skill_odoo.read_tools.Odoo", return_value=mock):
        payload = run_list_drafts(settings)
    assert payload["ok"] is True
    assert payload["count"] == 0
    assert payload["moves"] == []


# ── End-to-end CLI ────────────────────────────────────────────────────────


def test_bin_odoo_chart_of_accounts_no_env() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "chart-of-accounts"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_kind"] == "missing_env"


def test_bin_odoo_get_move_requires_id_or_ref() -> None:
    """argparse enforces --id / --ref required=True; bin/odoo should exit 2."""
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "get-move"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 2
    assert "must provide" in proc.stderr.lower() or "one of the arguments" in proc.stderr.lower()


def test_bin_odoo_get_move_with_both_id_and_ref_rejected() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "get-move", "--id", "1", "--ref", "x"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 2
    assert "not allowed" in proc.stderr.lower() or "argument" in proc.stderr.lower()


def test_bin_odoo_list_drafts_no_env() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "list-drafts"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_kind"] == "missing_env"
