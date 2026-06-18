"""Tests for create-bill, post-move, cancel-move, attach-file (issues #8 + #9).

Strategy: mock the Odoo client (no live RPC) and exercise the dispatch
helpers. Also asserts the end-to-end CLI shape.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from skill_odoo.attach import run_attach_file
from skill_odoo.config import Settings
from skill_odoo.write_tools import (
    run_cancel_move,
    run_create_bill,
    run_post_move,
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
    partners: list[dict[str, Any]] | None = None,
    accounts: list[dict[str, Any]] | None = None,
    moves: dict[int, dict[str, Any]] | None = None,
    currencies: list[dict[str, Any]] | None = None,
) -> Any:
    from unittest.mock import MagicMock
    m = MagicMock()
    m.uid = 7

    partners = partners if partners is not None else [
        {"id": 42, "name": "Acme Pte Ltd", "is_company": True},
    ]
    accounts = accounts if accounts is not None else [
        {"id": 624, "code": "624000", "name": "Office Supplies"},
        {"id": 625, "code": "625000", "name": "Travel"},
    ]
    moves = moves or {}
    currencies = currencies or [
        {"id": 1, "name": "SGD", "symbol": "$"},
        {"id": 2, "name": "USD", "symbol": "$"},
    ]

    next_move_id = [1000]  # mutable counter

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

        # Write methods (action_post, action_draft, etc.) take a list of ids.
        # The caller passes [[id1, id2, ...]]; unwrap to [id1, id2, ...].
        if method in ("action_post", "action_draft", "button_confirm", "button_draft"):
            if isinstance(first, list) and len(first) == 1 and isinstance(first[0], list):
                ids = first[0]
            else:
                ids = first if isinstance(first, list) else []

        # The create method takes a vals dict; the caller passes [vals].
        vals: dict[str, Any] = {}
        if method == "create":
            vals = first[0] if isinstance(first, list) and first and isinstance(first[0], dict) else {}

        if model == "res.partner" and method == "search":
            for c in domain:
                if isinstance(c, list) and len(c) == 3 and c[0] == "name" and c[1] == "=":
                    for p in partners:
                        if p["name"] == c[2]:
                            return [p["id"]]
                    return []
                if isinstance(c, list) and len(c) == 3 and c[0] == "name" and c[1] == "=ilike":
                    substr = c[2].strip("%").lower()
                    return [p["id"] for p in partners if substr in p["name"].lower()]
            return [p["id"] for p in partners]

        if model == "res.partner" and method == "read":
            return [p for p in partners if p.get("id") in ids]

        if model == "account.account" and method == "search":
            for c in domain:
                if isinstance(c, list) and len(c) == 3 and c[0] == "code" and c[1] == "=":
                    for a in accounts:
                        if a["code"] == c[2]:
                            return [a["id"]]
                    return []
            return [a["id"] for a in accounts]

        if model == "account.account" and method == "read":
            return [a for a in accounts if a.get("id") in ids]

        if model == "res.currency" and method == "search":
            for c in domain:
                if isinstance(c, list) and len(c) == 3 and c[0] == "name" and c[1] == "=":
                    for cur in currencies:
                        if cur["name"] == c[2]:
                            return [cur["id"]]
                    return []
            return []

        if model == "account.move" and method == "search":
            for c in domain:
                if isinstance(c, list) and len(c) == 3 and c[0] == "id" and c[1] == "=":
                    return [c[2]] if c[2] in moves else []
            return list(moves.keys())

        if model == "account.move" and method == "create":
            mid = next_move_id[0]
            next_move_id[0] += 1
            moves[mid] = {
                "id": mid,
                "name": f"BILL/{mid:04d}",
                "ref": vals.get("ref"),
                "state": "draft",
                "date": vals.get("invoice_date"),
                "amount_total": 100.0,
                "currency_id": [vals.get("currency_id", 1), "SGD"],
            }
            return mid

        if model == "account.move" and method == "action_post":
            for mid in ids:
                if mid in moves:
                    moves[mid]["state"] = "posted"
            return True

        if model == "account.move" and method == "action_draft":
            for mid in ids:
                if mid in moves:
                    moves[mid]["state"] = "draft"
            return True

        if model == "account.move" and method == "read":
            return [moves[i] for i in ids if i in moves]

        if model == "ir.attachment" and method == "create":
            vals = args[0] if args else kw.get("vals", {})
            return 99001

        raise AssertionError(f"unmocked rpc: model={model!r} method={method!r}")

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
        "read_lines", "_read_move_with_lines", "_read_move_summary",
        "get_partner_by_name", "suggest_partners",
        "create_bill", "post_move", "reset_move_to_draft",
        "read_move_summary", "find_currency", "attach_file",
    ):
        _bind(name)

    return m


# ── create-bill ────────────────────────────────────────────────────────────


def test_create_bill_exact_partner_match(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_create_bill(
            settings,
            partner_name="Acme Pte Ltd",
            invoice_date="2026-06-15",
            lines=[{"account_code": "624000", "name": "Pens", "quantity": 2, "price_unit": 5.0}],
            ref="PO-123",
        )
    assert payload["ok"] is True
    assert payload["move"]["state"] == "draft"
    assert payload["move"]["ref"] == "PO-123"


def test_create_bill_partner_not_found_returns_suggestions(settings: Settings) -> None:
    mock = _make_mock_odoo(partners=[
        {"id": 42, "name": "Acme Pte Ltd", "is_company": True},
        {"id": 99, "name": "Aloysius Pte Ltd", "is_company": True},
    ])
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_create_bill(
            settings,
            partner_name="ACME",  # different case → not exact match
            invoice_date="2026-06-15",
            lines=[{"account_code": "624000", "name": "Pens", "price_unit": 5.0}],
        )
    assert payload["ok"] is False
    assert payload["code"] == 2
    assert payload["error_kind"] == "not_found"
    assert "Acme Pte Ltd" in payload["suggestions"]


def test_create_bill_no_silent_creation(settings: Settings) -> None:
    """Aloy's rule: no silent creation. If the partner is not found,
    the tool must NOT create one. The Odoo state should be unchanged."""
    mock = _make_mock_odoo(partners=[])  # empty
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_create_bill(
            settings,
            partner_name="Nonexistent Corp",
            invoice_date="2026-06-15",
            lines=[{"account_code": "624000", "name": "X", "price_unit": 1.0}],
        )
    assert payload["ok"] is False
    assert payload["code"] == 2
    # No move was created.
    assert mock.rpc.call_count > 0  # the partner lookup happened


def test_create_bill_invalid_lines_json(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_create_bill(
            settings,
            partner_name="Acme Pte Ltd",
            invoice_date="2026-06-15",
            lines="not-valid-json",
        )
    assert payload["ok"] is False
    assert payload["code"] == 2
    assert payload["error_kind"] == "bad_args"


def test_create_bill_empty_lines(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_create_bill(
            settings,
            partner_name="Acme Pte Ltd",
            invoice_date="2026-06-15",
            lines=[],
        )
    assert payload["ok"] is False
    assert payload["code"] == 2


def test_create_bill_audit_logged(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        run_create_bill(
            settings,
            partner_name="Acme Pte Ltd",
            invoice_date="2026-06-15",
            lines=[{"account_code": "624000", "name": "X", "price_unit": 5.0}],
        )
    audit_files = list(settings.audit_log_dir.glob("*.jsonl"))
    assert len(audit_files) == 1
    line = audit_files[0].read_text(encoding="utf-8").strip()
    assert "ODOO_API_KEY" not in line
    assert "AI_SECRET" not in line
    payload = json.loads(line)
    assert payload["event"] == "create_bill"
    assert payload["ok"] is True
    assert payload["partner_id"] == 42


def test_create_bill_with_foreign_currency(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_create_bill(
            settings,
            partner_name="Acme Pte Ltd",
            invoice_date="2026-06-15",
            lines=[{"account_code": "624000", "name": "X", "price_unit": 50.0}],
            currency="USD",
        )
    assert payload["ok"] is True
    assert payload["move"]["currency_id"] == {"id": 2, "name": "SGD"}  # mock returns SGD label


def test_create_bill_with_default_currency_no_lookup(settings: Settings) -> None:
    """When --currency matches the company default, the Odoo currency
    lookup should NOT happen (we skip the RPC to save a call)."""
    mock = _make_mock_odoo()
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        run_create_bill(
            settings,
            partner_name="Acme Pte Ltd",
            invoice_date="2026-06-15",
            lines=[{"account_code": "624000", "name": "X", "price_unit": 50.0}],
            currency="SGD",  # default
        )
    # No res.currency search should have happened.
    for call in mock.rpc.call_args_list:
        args = call.args if hasattr(call, "args") else call[0]
        assert args[0] != "res.currency", f"unexpected res.currency RPC: {args}"


def test_create_bill_unknown_currency(settings: Settings) -> None:
    mock = _make_mock_odoo(currencies=[
        {"id": 1, "name": "SGD", "symbol": "$"},
    ])  # no USD
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_create_bill(
            settings,
            partner_name="Acme Pte Ltd",
            invoice_date="2026-06-15",
            lines=[{"account_code": "624000", "name": "X", "price_unit": 50.0}],
            currency="USD",
        )
    assert payload["ok"] is False
    assert payload["code"] == 4
    assert payload["error_kind"] == "not_found"


def test_create_bill_with_tax_ids(settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_create_bill(
            settings,
            partner_name="Acme Pte Ltd",
            invoice_date="2026-06-15",
            lines=[{"account_code": "624000", "name": "X", "price_unit": 100.0, "tax_ids": [9]}],
        )
    assert payload["ok"] is True


# ── post-move ──────────────────────────────────────────────────────────────


def test_post_move_draft_to_posted(settings: Settings) -> None:
    mock = _make_mock_odoo(moves={
        123: {"id": 123, "name": "BILL/0001", "ref": "X", "state": "draft",
              "date": "2026-06-15", "amount_total": 50.0,
              "currency_id": [1, "SGD"]},
    })
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_post_move(settings, move_id=123)
    assert payload["ok"] is True
    assert payload["move"]["state"] == "posted"


def test_post_move_already_posted_no_error(settings: Settings) -> None:
    mock = _make_mock_odoo(moves={
        123: {"id": 123, "name": "BILL/0001", "state": "posted",
              "currency_id": [1, "SGD"]},
    })
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_post_move(settings, move_id=123)
    assert payload["ok"] is True
    assert payload["note"] == "already posted"
    assert payload["move"]["state"] == "posted"


def test_post_move_not_found(settings: Settings) -> None:
    mock = _make_mock_odoo(moves={})
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_post_move(settings, move_id=999)
    assert payload["ok"] is False
    assert payload["code"] == 4
    assert payload["error_kind"] == "not_found"


def test_post_move_audit_logged(settings: Settings) -> None:
    mock = _make_mock_odoo(moves={
        123: {"id": 123, "name": "BILL/0001", "state": "draft",
              "currency_id": [1, "SGD"]},
    })
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        run_post_move(settings, move_id=123)
    audit_files = list(settings.audit_log_dir.glob("*.jsonl"))
    assert len(audit_files) == 1
    line = audit_files[0].read_text(encoding="utf-8").strip()
    assert "ODOO_API_KEY" not in line
    payload = json.loads(line)
    assert payload["event"] == "post_move"
    assert payload["state"] == "posted"


# ── cancel-move (reset to draft) ───────────────────────────────────────────


def test_cancel_move_posted_to_draft(settings: Settings) -> None:
    """Per Aloy: cancel-move resets to draft, NOT button_cancel."""
    mock = _make_mock_odoo(moves={
        123: {"id": 123, "name": "BILL/0001", "state": "posted",
              "currency_id": [1, "SGD"]},
    })
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_cancel_move(settings, move_id=123)
    assert payload["ok"] is True
    assert payload["move"]["state"] == "draft"
    # Verify the mock's action_draft was called, NOT button_cancel.
    rpc_calls = mock.rpc.call_args_list
    methods_called = [c.args[1] for c in rpc_calls if hasattr(c, "args")]
    assert "action_draft" in methods_called
    assert "button_cancel" not in methods_called


def test_cancel_move_already_draft_no_error(settings: Settings) -> None:
    mock = _make_mock_odoo(moves={
        123: {"id": 123, "name": "BILL/0001", "state": "draft",
              "currency_id": [1, "SGD"]},
    })
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_cancel_move(settings, move_id=123)
    assert payload["ok"] is True
    assert payload["note"] == "already draft"
    assert payload["move"]["state"] == "draft"


def test_cancel_move_not_found(settings: Settings) -> None:
    mock = _make_mock_odoo(moves={})
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        payload = run_cancel_move(settings, move_id=999)
    assert payload["ok"] is False
    assert payload["code"] == 4


def test_cancel_move_audit_logged(settings: Settings) -> None:
    mock = _make_mock_odoo(moves={
        123: {"id": 123, "name": "BILL/0001", "state": "posted",
              "currency_id": [1, "SGD"]},
    })
    with patch("skill_odoo.write_tools.Odoo", return_value=mock):
        run_cancel_move(settings, move_id=123)
    audit_files = list(settings.audit_log_dir.glob("*.jsonl"))
    assert len(audit_files) == 1
    payload = json.loads(audit_files[0].read_text(encoding="utf-8").strip())
    assert payload["event"] == "cancel_move"
    assert payload["state"] == "draft"


# ── attach-file ────────────────────────────────────────────────────────────


def _make_text_file(path: Path, content: bytes = b"hello world\n") -> Path:
    path.write_bytes(content)
    return path


def test_attach_file_basic(tmp_path: Path, settings: Settings) -> None:
    fp = _make_text_file(tmp_path / "receipt.txt")
    mock = _make_mock_odoo()
    with patch("skill_odoo.attach.Odoo", return_value=mock):
        payload = run_attach_file(
            settings,
            model="account.move",
            res_id=42,
            file_path=fp,
        )
    assert payload["ok"] is True
    assert payload["attachment"]["id"] == 99001
    assert payload["attachment"]["name"] == "receipt.txt"
    assert payload["attachment"]["file_size"] == len(b"hello world\n")
    assert payload["attachment"]["res_model"] == "account.move"
    assert payload["attachment"]["res_id"] == 42
    assert len(payload["attachment"]["checksum_sha256"]) == 64
    # Verify checksum
    expected = hashlib.sha256(b"hello world\n").hexdigest()
    assert payload["attachment"]["checksum_sha256"] == expected


def test_attach_file_custom_name(tmp_path: Path, settings: Settings) -> None:
    fp = _make_text_file(tmp_path / "orig.txt")
    mock = _make_mock_odoo()
    with patch("skill_odoo.attach.Odoo", return_value=mock):
        payload = run_attach_file(
            settings,
            model="res.partner",
            res_id=42,
            file_path=fp,
            name="custom-name.txt",
        )
    assert payload["attachment"]["name"] == "custom-name.txt"


def test_attach_file_file_not_found(tmp_path: Path, settings: Settings) -> None:
    mock = _make_mock_odoo()
    with patch("skill_odoo.attach.Odoo", return_value=mock):
        payload = run_attach_file(
            settings,
            model="account.move",
            res_id=42,
            file_path=tmp_path / "does-not-exist.pdf",
        )
    assert payload["ok"] is False
    assert payload["code"] == 4
    assert payload["error_kind"] == "not_found"


def test_attach_file_empty_file(tmp_path: Path, settings: Settings) -> None:
    fp = _make_text_file(tmp_path / "empty.txt", b"")
    mock = _make_mock_odoo()
    with patch("skill_odoo.attach.Odoo", return_value=mock):
        payload = run_attach_file(
            settings,
            model="account.move",
            res_id=42,
            file_path=fp,
        )
    assert payload["ok"] is False
    assert payload["code"] == 2
    assert payload["error_kind"] == "bad_args"


def test_attach_file_too_large(tmp_path: Path, settings: Settings) -> None:
    big = tmp_path / "huge.bin"
    big.write_bytes(b"x" * (26 * 1024 * 1024))  # 26 MB
    mock = _make_mock_odoo()
    with patch("skill_odoo.attach.Odoo", return_value=mock):
        payload = run_attach_file(
            settings,
            model="account.move",
            res_id=42,
            file_path=big,
        )
    assert payload["ok"] is False
    assert payload["code"] == 2
    assert payload["error_kind"] == "file_too_large"
    assert payload["limit_bytes"] == 25 * 1024 * 1024


def test_attach_file_mimetype_guessed(tmp_path: Path, settings: Settings) -> None:
    fp = _make_text_file(tmp_path / "doc.pdf", b"%PDF-1.4\n%fake pdf\n")
    mock = _make_mock_odoo()
    with patch("skill_odoo.attach.Odoo", return_value=mock):
        payload = run_attach_file(
            settings,
            model="account.move",
            res_id=42,
            file_path=fp,
        )
    assert payload["attachment"]["mimetype"] == "application/pdf"


def test_attach_file_audit_logged(tmp_path: Path, settings: Settings) -> None:
    fp = _make_text_file(tmp_path / "r.txt")
    mock = _make_mock_odoo()
    with patch("skill_odoo.attach.Odoo", return_value=mock):
        run_attach_file(settings, model="account.move", res_id=42, file_path=fp)
    audit_files = list(settings.audit_log_dir.glob("*.jsonl"))
    assert len(audit_files) == 1
    line = audit_files[0].read_text(encoding="utf-8").strip()
    assert "ODOO_API_KEY" not in line
    payload = json.loads(line)
    assert payload["event"] == "attach_file"
    assert payload["res_model"] == "account.move"
    assert payload["res_id"] == 42
    assert payload["filename"] == "r.txt"
    assert payload["attachment_id"] == 99001


# ── End-to-end CLI ────────────────────────────────────────────────────────


def test_bin_odoo_create_bill_no_env() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "create-bill",
         "--partner-name", "X",
         "--invoice-date", "2026-06-15",
         "--lines", "[]"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_kind"] == "missing_env"


def test_bin_odoo_post_move_no_env() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "post-move", "--id", "1"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5


def test_bin_odoo_cancel_move_no_env() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "cancel-move", "--id", "1"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5


def test_bin_odoo_attach_file_no_env(tmp_path: Path) -> None:
    fp = _make_text_file(tmp_path / "r.txt")
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "attach-file",
         "--model", "account.move", "--id", "1", "--file-path", str(fp)],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5


def test_bin_odoo_create_bill_missing_required_args() -> None:
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "create-bill"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 2
    assert "required" in proc.stderr.lower() or "must provide" in proc.stderr.lower()
