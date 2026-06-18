"""Tests for the process-receipt composite (issue #12).

Strategy: mock every external call (Odoo client, ocr_router, ai classify, FX)
and verify the pipeline. End-to-end CLI tests for the missing-env and
file-not-found paths.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from skill_odoo.config import Settings
from skill_odoo.models import ReceiptExtraction
from skill_odoo.receipt import process_receipt


SKILL_ROOT = Path(__file__).resolve().parent.parent
BIN_ODOO = SKILL_ROOT / "bin" / "odoo"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("ODOO_CACHE_PATH", str(tmp_path / ".odoo_cache.json"))
    monkeypatch.setenv("ODOO_CACHE_TTL_SECONDS", "86400")
    # Set inbox to a temp dir so failed-receipts land somewhere predictable.
    inbox = tmp_path / "inbox"
    inbox.mkdir()
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
        receipts_inbox=inbox,
        receipts_processed_delete=True,
        ai_chat_url="https://openrouter.ai/api/v1/chat/completions",
        ai_model="google/gemma-4-26b-a4b-it:free",
        ai_secret="***",  # noqa: S106
        ai_provider_order="",
        audit_log_dir=tmp_path / "audit",
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_odoo(
    *,
    shareholder_id: int = 202,
    journal_id: int = 9,
    coa: list[dict[str, Any]] | None = None,
    next_move_id: int = 1000,
) -> Any:
    """Build a MagicMock Odoo client with the primitives process-receipt uses.

    We set ``return_value`` directly on the mock attributes (NOT bind the real
    Odoo methods). Binding would route calls through ``self.rpc`` which is a
    bare MagicMock returning a child MagicMock. Setting return_value on the
    mock attribute directly is simpler and works for our purposes.
    """
    m = MagicMock()
    m.uid = 7
    coa = coa or [
        {"id": 624, "code": "624000", "name": "Office Supplies", "account_type": "expense"},
        {"id": 625, "code": "625000", "name": "Travel", "account_type": "expense"},
    ]
    m.user_info.return_value = {
        "name": "Alice",
        "login": "alice@example.com",
        "tz": "Asia/Singapore",
        "company_id": [42, "Aloysius Pte Ltd"],
        "company_id_int": 42,
    }
    m.company_currency.return_value = (1, "SGD")
    m.find_journal.return_value = {"id": journal_id, "code": "MISC", "name": "Misc", "type": "general"}
    m.find_account.return_value = {"id": shareholder_id, "code": "202040", "name": "Shareholder Notes Payable"}
    m.chart_of_accounts.return_value = coa
    m.find_month_draft.return_value = None  # always create fresh

    def fake_read_move(move_id: int) -> dict[str, Any]:
        return {
            "id": move_id, "name": "MISC/2026/0001", "ref": "26Jun",
            "state": "draft", "date": "2026-06-15",
            "journal_id": [journal_id, "MISC"],
            "line_ids": [], "amount_total": 0.0,
        }

    def fake_create_move(*args: Any, **kwargs: Any) -> int:
        return next_move_id

    def fake_read_lines(_line_ids: list[int]) -> list[dict[str, Any]]:
        return []

    def fake_replace_lines(_move_id: int, _lines: list[dict[str, Any]]) -> None:
        return None

    def fake_write_move(_move_id: int, _vals: dict[str, Any]) -> None:
        return None

    def fake_attach_file(*args: Any, **kwargs: Any) -> int:
        return 99001

    m.read_move.side_effect = fake_read_move
    m.read_lines.side_effect = fake_read_lines
    m.create_move.side_effect = fake_create_move
    m.replace_move_lines.side_effect = fake_replace_lines
    m.write_move.side_effect = fake_write_move
    m.attach_file.side_effect = fake_attach_file

    return m


def _make_extraction(
    *,
    vendor: str = "Acme Corp",
    tx_date: str = "2026-06-15",
    currency: str = "SGD",
    amount: float = 12.50,
    debit_account_code: str = "624000",
    description: str = "Pens and paper",
    confidence: float = 0.92,
) -> ReceiptExtraction:
    return ReceiptExtraction(
        vendor=vendor,
        tx_date=tx_date,
        currency=currency,
        amount=amount,
        debit_account_code=debit_account_code,
        description=description,
        confidence=confidence,
        notes="",
        raw_text="ACME\n$12.50",
    )


# ── process-receipt ─────────────────────────────────────────────────────────


def test_process_receipt_agent_text_path(settings: Settings, tmp_path: Path) -> None:
    """When --text is provided, the in-skill OCR chain is NOT called."""
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    odoo = _make_odoo()
    extraction = _make_extraction()
    with patch("skill_odoo.receipt.Odoo", return_value=odoo), \
         patch("skill_odoo.receipt.classify_receipt_with_debug", return_value=(extraction, {"model": "m"})) as classify, \
         patch("skill_odoo.receipt.fx.convert", return_value=(12.50, 1.0)) as fxconv:
        result = process_receipt(
            settings, file_path=fp, provided_text="ACME\n$12.50"
        )
    assert result["ok"] is True
    assert result["ocr_source"] == "agent"
    assert result["extraction"]["vendor"] == "Acme Corp"
    assert result["fx"] == {"from": "SGD", "to": "SGD", "rate": 1.0, "converted": 12.50}
    assert result["move_id"] == 1000
    assert result["attachment_id"] == 99001
    assert result["totals"] == {"total_debit": 12.50, "credit": 12.50}
    # The local file is deleted on success.
    assert not fp.exists()
    classify.assert_called_once()


def test_process_receipt_non_sgd_with_live_fx(settings: Settings, tmp_path: Path) -> None:
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    odoo = _make_odoo()
    extraction = _make_extraction(currency="USD", amount=100.0, debit_account_code="625000")
    with patch("skill_odoo.receipt.Odoo", return_value=odoo), \
         patch("skill_odoo.receipt.classify_receipt_with_debug", return_value=(extraction, {"model": "m"})), \
         patch("skill_odoo.receipt.fx.convert", return_value=(135.0, 1.35)) as fxconv:
        result = process_receipt(
            settings, file_path=fp, provided_text="ACME US\n$100"
        )
    assert result["ok"] is True
    assert result["fx"] == {"from": "USD", "to": "SGD", "rate": 1.35, "converted": 135.0}
    assert result["totals"]["credit"] == 135.0


def test_process_receipt_keeps_local_file_when_setting_off(settings: Settings, tmp_path: Path) -> None:
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    settings2 = Settings(**{**settings.__dict__, "receipts_processed_delete": False})
    odoo = _make_odoo()
    extraction = _make_extraction()
    with patch("skill_odoo.receipt.Odoo", return_value=odoo), \
         patch("skill_odoo.receipt.classify_receipt_with_debug", return_value=(extraction, {"model": "m"})), \
         patch("skill_odoo.receipt.fx.convert", return_value=(12.50, 1.0)):
        result = process_receipt(
            settings2, file_path=fp, provided_text="x"
        )
    assert result["ok"] is True
    assert fp.exists()


def test_process_receipt_file_not_found(settings: Settings) -> None:
    result = process_receipt(
        settings, file_path=Path("/tmp/nope.pdf"), provided_text="x"
    )
    assert result["ok"] is False
    assert "file not found" in result["error"]


def test_process_receipt_unsupported_type(settings: Settings, tmp_path: Path) -> None:
    fp = tmp_path / "r.xyz"
    fp.write_text("x")
    result = process_receipt(settings, file_path=fp, provided_text="x")
    assert result["ok"] is False
    assert "unsupported file type" in result["error"]


def test_process_receipt_ocr_empty(settings: Settings, tmp_path: Path) -> None:
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    odoo = _make_odoo()
    with patch("skill_odoo.receipt.Odoo", return_value=odoo), \
         patch("skill_odoo.receipt.extract_receipt_text", return_value={"text": "", "source": "failed", "file_path": str(fp)}):
        result = process_receipt(settings, file_path=fp, provided_text=None)
    assert result["ok"] is False
    assert "OCR produced no text" in result["error"]


def test_process_receipt_classify_error(settings: Settings, tmp_path: Path) -> None:
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    odoo = _make_odoo()
    from skill_odoo.ai_automation import AIError
    with patch("skill_odoo.receipt.Odoo", return_value=odoo), \
         patch("skill_odoo.receipt.extract_receipt_text", return_value={"text": "ACME", "source": "agent", "file_path": str(fp)}), \
         patch("skill_odoo.receipt.classify_receipt_with_debug", side_effect=AIError("no account code")):
        result = process_receipt(settings, file_path=fp, provided_text=None)
    assert result["ok"] is False
    assert "no account code" in result["error"]


def test_process_receipt_unknown_account(settings: Settings, tmp_path: Path) -> None:
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    odoo = _make_odoo()
    extraction = _make_extraction(debit_account_code="999999")
    with patch("skill_odoo.receipt.Odoo", return_value=odoo), \
         patch("skill_odoo.receipt.extract_receipt_text", return_value={"text": "ACME", "source": "agent", "file_path": str(fp)}), \
         patch("skill_odoo.receipt.classify_receipt_with_debug", return_value=(extraction, {"model": "m"})), \
         patch("skill_odoo.receipt.fx.convert", return_value=(12.50, 1.0)):
        result = process_receipt(settings, file_path=fp, provided_text=None)
    assert result["ok"] is False
    assert "unknown account code" in result["error"]


def test_process_receipt_zero_amount(settings: Settings, tmp_path: Path) -> None:
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    odoo = _make_odoo()
    extraction = _make_extraction(amount=0.0)
    with patch("skill_odoo.receipt.Odoo", return_value=odoo), \
         patch("skill_odoo.receipt.extract_receipt_text", return_value={"text": "ACME", "source": "agent", "file_path": str(fp)}), \
         patch("skill_odoo.receipt.classify_receipt_with_debug", return_value=(extraction, {"model": "m"})), \
         patch("skill_odoo.receipt.fx.convert", return_value=(0.0, 1.0)):
        result = process_receipt(settings, file_path=fp, provided_text=None)
    assert result["ok"] is False
    assert "zero or unreadable" in result["error"]


def test_process_receipt_fx_error(settings: Settings, tmp_path: Path) -> None:
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    odoo = _make_odoo()
    extraction = _make_extraction()
    from skill_odoo.fx import FXError
    with patch("skill_odoo.receipt.Odoo", return_value=odoo), \
         patch("skill_odoo.receipt.extract_receipt_text", return_value={"text": "ACME", "source": "agent", "file_path": str(fp)}), \
         patch("skill_odoo.receipt.classify_receipt_with_debug", return_value=(extraction, {"model": "m"})), \
         patch("skill_odoo.receipt.fx.convert", side_effect=FXError("FX offline")):
        result = process_receipt(settings, file_path=fp, provided_text=None)
    assert result["ok"] is False
    assert "fx failed" in result["error"]
    assert "FX offline" in result["error"]


def test_process_receipt_audit_logged_no_secrets(settings: Settings, tmp_path: Path) -> None:
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    odoo = _make_odoo()
    extraction = _make_extraction()
    with patch("skill_odoo.receipt.Odoo", return_value=odoo), \
         patch("skill_odoo.receipt.classify_receipt_with_debug", return_value=(extraction, {"model": "m"})), \
         patch("skill_odoo.receipt.fx.convert", return_value=(12.50, 1.0)):
        process_receipt(settings, file_path=fp, provided_text="x")
    audit_files = list(settings.audit_log_dir.glob("*.jsonl"))
    assert len(audit_files) == 1
    raw = audit_files[0].read_text(encoding="utf-8")
    assert "***" not in raw
    assert "ODOO_API_KEY" not in raw
    assert "AI_SECRET" not in raw
    lines = [json.loads(l) for l in raw.splitlines() if l.strip()]
    stages = [l.get("stage") for l in lines]
    assert "ocr_done" in stages
    assert "ai_done" in stages
    assert "odoo_done" in stages


def test_process_receipt_failed_moves_to_failed_dir(settings: Settings, tmp_path: Path) -> None:
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    odoo = _make_odoo()
    extraction = _make_extraction(debit_account_code="999999")
    failed_dir = settings.receipts_inbox.parent / "failed_receipts"
    with patch("skill_odoo.receipt.Odoo", return_value=odoo), \
         patch("skill_odoo.receipt.extract_receipt_text", return_value={"text": "ACME", "source": "agent", "file_path": str(fp)}), \
         patch("skill_odoo.receipt.classify_receipt_with_debug", return_value=(extraction, {"model": "m"})), \
         patch("skill_odoo.receipt.fx.convert", return_value=(12.50, 1.0)):
        process_receipt(settings, file_path=fp, provided_text=None)
    assert not fp.exists()
    failed_files = list(failed_dir.glob("*"))
    assert len(failed_files) >= 1
    error_files = list(failed_dir.glob("*.error.txt"))
    assert len(error_files) == 1


# ── End-to-end CLI ────────────────────────────────────────────────────────


def test_bin_odoo_process_receipt_no_env(tmp_path: Path) -> None:
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "process-receipt", "--file-path", str(fp)],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_kind"] == "missing_env"


def test_bin_odoo_process_receipt_text_preferred(tmp_path: Path) -> None:
    """When --text is passed and the file is missing, the agent's text wins."""
    fp = tmp_path / "missing.pdf"
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "process-receipt", "--file-path", str(fp), "--text", "x"],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5  # still missing_env
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_kind"] == "missing_env"
