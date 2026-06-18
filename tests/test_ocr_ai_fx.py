"""Tests for ocr_router, ai_automation, fx (issues #10 + #11).

Strategy:
- ocr_router: patch the in-skill OCR chain (pdfplumber/pytesseract/openai) to
  return canned text. Verify the routing order.
- ai_automation: mock the HTTP response. Verify the LLM payload is well-formed
  and the receipt extraction is correct.
- fx: patch urllib.request.urlopen. Verify rates and error handling.
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

from skill_odoo.ai_automation import (
    AIError,
    SYSTEM_PROMPT,
    build_ai_payload,
    classify_receipt_with_debug,
    shortlist_coa,
)
from skill_odoo.config import Settings
from skill_odoo.fx import FXError, convert
from skill_odoo.ocr_router import extract_receipt_text


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


# ── ocr_router ─────────────────────────────────────────────────────────────


def test_ocr_router_agent_text_wins(settings: Settings, tmp_path: Path) -> None:
    """If provided_text is set, it's used and the in-skill chain is NOT called."""
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with patch("skill_odoo.extraction._extract_pdf_text") as plumber, \
         patch("skill_odoo.ocr_router.extract_text") as ocr:
        result = extract_receipt_text(settings, pdf, provided_text="ACME\n$10.00")
    assert result["source"] == "agent"
    assert result["text"] == "ACME\n$10.00"
    plumber.assert_not_called()
    ocr.assert_not_called()


def test_ocr_router_plumber_for_pdf_with_text(settings: Settings, tmp_path: Path) -> None:
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with patch("skill_odoo.extraction._extract_pdf_text", return_value="RECEIPT\n$50.00"):
        result = extract_receipt_text(settings, pdf)
    assert result["source"] == "plumber"
    assert result["text"] == "RECEIPT\n$50.00"


def test_ocr_router_plumber_empty_falls_through_to_openai(settings: Settings, tmp_path: Path) -> None:
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with patch("skill_odoo.extraction._extract_pdf_text", return_value=""), \
         patch("skill_odoo.ocr_router.extract_text", return_value=("VISION TEXT", "openai_ocr_pdf")):
        result = extract_receipt_text(settings, pdf)
    assert result["source"] == "openai"
    assert result["text"] == "VISION TEXT"


def test_ocr_router_tesseract_for_image(settings: Settings, tmp_path: Path) -> None:
    img = tmp_path / "r.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    with patch("skill_odoo.ocr_router.extract_text", return_value=("TESS TEXT", "ocr_image")):
        result = extract_receipt_text(settings, img)
    assert result["source"] == "tesseract"
    assert result["text"] == "TESS TEXT"


def test_ocr_router_failure_returns_empty_with_error(settings: Settings, tmp_path: Path) -> None:
    img = tmp_path / "r.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    with patch("skill_odoo.ocr_router.extract_text", side_effect=RuntimeError("boom")):
        result = extract_receipt_text(settings, img)
    assert result["source"] == "failed"
    assert result["text"] == ""
    assert "boom" in result["error"]


def test_ocr_router_file_not_found(settings: Settings, tmp_path: Path) -> None:
    result = extract_receipt_text(settings, tmp_path / "nope.pdf")
    assert result["source"] == "failed"
    assert "file not found" in result["error"]


# ── ai_automation ──────────────────────────────────────────────────────────


def test_build_ai_payload_includes_coa_and_text() -> None:
    coa = [
        {"code": "624000", "name": "Office", "account_type": "expense"},
        {"code": "625000", "name": "Travel", "account_type": "expense"},
    ]
    payload = build_ai_payload(
        "ACME\n$10.00",
        coa,
        model="m",
        provider_order="google-ai-studio",
        default_currency="SGD",
    )
    assert payload["model"] == "m"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["provider"]["order"] == ["google-ai-studio"]
    assert payload["provider"]["allow_fallbacks"] is False
    user_msg = payload["messages"][1]["content"]
    assert "624000" in user_msg
    assert "Office" in user_msg
    assert "ACME" in user_msg


def test_classify_receipt_success(settings: Settings) -> None:
    """Mock the LLM response and verify extraction + debug payload."""
    fake_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "vendor": "Acme Corp",
                        "date": "2026-06-15",
                        "currency": "USD",
                        "amount": 12.50,
                        "debit_account_code": "624000",
                        "description": "Pens and paper",
                        "confidence": 0.92,
                        "notes": "",
                    }),
                },
            },
        ],
    }
    with patch("skill_odoo.ai_automation.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_response).encode()
        extraction, debug = classify_receipt_with_debug(
            "ACME\n$12.50",
            shortlist_coa([{"code": "624000", "name": "Office", "account_type": "expense"}]),
            chat_url=settings.ai_chat_url,
            model=settings.ai_model,
            api_key=settings.ai_secret,
            default_currency=settings.default_currency,
        )
    assert extraction.vendor == "Acme Corp"
    assert extraction.amount == 12.50
    assert extraction.currency == "USD"
    assert extraction.debit_account_code == "624000"
    assert extraction.confidence == pytest.approx(0.92)
    assert debug["parsed_json"]["vendor"] == "Acme Corp"
    assert debug["attempts"][-1]["ok"] is True


def test_classify_receipt_handles_markdown_fence() -> None:
    """Some LLMs wrap the JSON in ```json ... ```. The classifier should handle that."""
    fake_response = {
        "choices": [
            {
                "message": {
                    "content": "```json\n" + json.dumps({
                        "vendor": "Acme",
                        "date": "2026-06-15",
                        "currency": "SGD",
                        "amount": 50.0,
                        "debit_account_code": "624000",
                        "description": "X",
                        "confidence": 0.8,
                    }) + "\n```",
                },
            },
        ],
    }
    with patch("skill_odoo.ai_automation.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_response).encode()
        extraction, _ = classify_receipt_with_debug(
            "ACME\n$50",
            shortlist_coa([{"code": "624000", "name": "Office", "account_type": "expense"}]),
            chat_url="https://example.com/chat",
            model="m",
            api_key="k",
        )
    assert extraction.vendor == "Acme"


def test_classify_receipt_rejects_zero_amount(settings: Settings) -> None:
    fake_response = {
        "choices": [
            {"message": {"content": json.dumps({
                "vendor": "Acme", "date": "2026-06-15", "currency": "SGD",
                "amount": 0, "debit_account_code": "624000", "description": "X",
                "confidence": 0.5,
            })}},
        ],
    }
    with patch("skill_odoo.ai_automation.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_response).encode()
        with pytest.raises(AIError, match="positive final paid total"):
            classify_receipt_with_debug(
                "garbled",
                shortlist_coa([{"code": "624000", "name": "Office", "account_type": "expense"}]),
                chat_url="https://example.com/chat", model="m", api_key="k",
            )


def test_classify_receipt_rejects_missing_account_code(settings: Settings) -> None:
    fake_response = {
        "choices": [
            {"message": {"content": json.dumps({
                "vendor": "Acme", "date": "2026-06-15", "currency": "SGD",
                "amount": 50.0, "debit_account_code": "", "description": "X",
                "confidence": 0.5,
            })}},
        ],
    }
    with patch("skill_odoo.ai_automation.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_response).encode()
        with pytest.raises(AIError, match="debit account code"):
            classify_receipt_with_debug(
                "x",
                shortlist_coa([{"code": "624000", "name": "Office", "account_type": "expense"}]),
                chat_url="https://example.com/chat", model="m", api_key="k",
            )


def test_classify_receipt_requires_credentials() -> None:
    with pytest.raises(AIError, match="must all be set"):
        classify_receipt_with_debug("x", [], chat_url="", model="m", api_key="k")


def test_shortlist_coa_keeps_expense_and_asset_types() -> None:
    coa = [
        {"code": "1", "name": "Cash", "account_type": "asset_cash"},
        {"code": "2", "name": "Office", "account_type": "expense"},
        {"code": "3", "name": "Travel", "account_type": "expense_depreciation"},
        {"code": "4", "name": "Prepaid", "account_type": "asset_prepayments"},
        {"code": "5", "name": "Other", "account_type": "off_balance"},
    ]
    short = shortlist_coa(coa)
    codes = [a["code"] for a in short]
    assert codes == ["2", "3", "4"]


# ── fx ─────────────────────────────────────────────────────────────────────


def test_fx_same_currency_short_circuits() -> None:
    converted, rate = convert(100.0, from_ccy="SGD", to_ccy="SGD", on=date(2026, 6, 15))
    assert converted == 100.0
    assert rate == 1.0


def test_fx_calls_frankfurter() -> None:
    body = {"rates": {"SGD": 1.35}}
    with patch("skill_odoo.fx.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(body).encode()
        converted, rate = convert(100.0, from_ccy="USD", to_ccy="SGD", on=date(2026, 6, 15))
    assert converted == 135.0
    assert rate == pytest.approx(1.35)
    # Verify the URL hit frankfurter
    url = urlopen.call_args.args[0]
    assert "frankfurter.dev" in url
    assert "base=USD" in url
    assert "symbols=SGD" in url


def test_fx_unsupported_provider() -> None:
    with pytest.raises(FXError, match="Unsupported FX provider"):
        convert(100.0, from_ccy="USD", to_ccy="SGD", provider="oanda")


def test_fx_rate_not_returned() -> None:
    body = {"rates": {}}
    with patch("skill_odoo.fx.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(body).encode()
        with pytest.raises(FXError, match="not returned"):
            convert(100.0, from_ccy="USD", to_ccy="SGD")


# ── End-to-end CLI ────────────────────────────────────────────────────────


def test_bin_odoo_ocr_test_no_env(tmp_path: Path) -> None:
    fp = tmp_path / "r.pdf"
    fp.write_bytes(b"%PDF-1.4\n")
    env = os.environ.copy()
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run(
        [str(BIN_ODOO), "_ocr-test", str(fp)],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 5
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_kind"] == "missing_env"
