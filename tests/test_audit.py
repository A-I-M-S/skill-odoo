"""Audit log tests for skill-odoo.

Verifies:
- JSONL line is written per call, one event per line.
- Secret-shaped keys (ODOO_API_KEY, AI_SECRET, OCR_API_KEY, TELEGRAM_*) are never
  persisted in the audit log even if they appear in the event dict.
- Path values are coerced to strings.
- _json_safe handles nested dicts and lists.
- Round-trip: write_audit → read the line → parse JSON → assert structure.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from skill_odoo.audit import _json_safe, write_audit


SECRET_KEY_PATTERNS = [
    r"\bODOO_API_KEY\b",
    r"\bAI_SECRET\b",
    r"\bOCR_API_KEY\b",
    r"\bTELEGRAM_BOT_TOKEN\b",
    r"\bTELEGRAM_ALLOWED_USER_IDS\b",
    r"\bAPI_KEY\b",
    r"\bSECRET\b",
    r"\bPASSWORD\b",
    r"\bTOKEN\b",
]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_write_audit_creates_jsonl_with_one_event(tmp_path: Path) -> None:
    log_dir = tmp_path / "audit"
    write_audit(log_dir, {"event": "receipt_processing", "stage": "ocr_done", "file": "a.pdf"})
    assert log_dir.exists()
    files = list(log_dir.glob("*.jsonl"))
    assert len(files) == 1
    events = _read_jsonl(files[0])
    assert len(events) == 1
    assert events[0]["event"] == "receipt_processing"
    assert events[0]["stage"] == "ocr_done"
    assert events[0]["file"] == "a.pdf"
    assert "ts_utc" in events[0]


def test_write_audit_appends_per_call(tmp_path: Path) -> None:
    log_dir = tmp_path / "audit"
    write_audit(log_dir, {"event": "a"})
    write_audit(log_dir, {"event": "b"})
    write_audit(log_dir, {"event": "c"})
    files = list(log_dir.glob("*.jsonl"))
    assert len(files) == 1
    events = _read_jsonl(files[0])
    assert [e["event"] for e in events] == ["a", "b", "c"]


def test_write_audit_path_value_coerced_to_string(tmp_path: Path) -> None:
    log_dir = tmp_path / "audit"
    write_audit(log_dir, {"event": "x", "file_path": Path("/tmp/foo.pdf")})
    [event] = _read_jsonl(next(log_dir.glob("*.jsonl")))
    assert event["file_path"] == "/tmp/foo.pdf"
    assert isinstance(event["file_path"], str)


def test_write_audit_never_persists_secret_keys(tmp_path: Path) -> None:
    """Even if a caller accidentally puts a secret-shaped KEY in the event,
    it must not land in the audit log. The audit module enforces a key-name
    deny-list (case-insensitive match on the full key). Values inside lists
    are not filtered — callers must follow the convention of not passing
    secrets at all; this filter is a safety net, not a replacement.
    """
    log_dir = tmp_path / "audit"
    secret_value = "sk-live-1234567890abcdef"  # noqa: S105
    event = {
        "event": "receipt_processing",
        "stage": "ocr_done",
        "ODOO_API_KEY": secret_value,
        "AI_SECRET": secret_value,
        "OCR_API_KEY": secret_value,
        "TELEGRAM_BOT_TOKEN": secret_value,
        "nested": {"API_KEY": secret_value, "safe": "ok"},
        "tokens": ["fine", "also fine"],
    }
    write_audit(log_dir, event)
    raw = next(log_dir.glob("*.jsonl")).read_text(encoding="utf-8")
    # None of the well-known secret keys should appear in the log line.
    for pattern in SECRET_KEY_PATTERNS:
        assert not re.search(pattern, raw), (
            f"audit log contains secret-shaped key matching {pattern!r}\n{raw}"
        )
    # Non-secret siblings should still be there.
    assert '"safe": "ok"' in raw
    assert '"event": "receipt_processing"' in raw


def test_write_audit_keeps_safe_event_data(tmp_path: Path) -> None:
    log_dir = tmp_path / "audit"
    write_audit(log_dir, {"event": "x", "vendor": "Acme", "amount": 12.5, "currency": "SGD"})
    [event] = _read_jsonl(next(log_dir.glob("*.jsonl")))
    assert event["vendor"] == "Acme"
    assert event["amount"] == 12.5
    assert event["currency"] == "SGD"


def test_json_safe_handles_nested_dicts() -> None:
    out = _json_safe({"a": {"b": {"c": 1}}, "d": [1, 2, {"e": 3}]})
    assert out == {"a": {"b": {"c": 1}}, "d": [1, 2, {"e": 3}]}


def test_json_safe_coerces_paths() -> None:
    out = _json_safe({"p": Path("/x/y")})
    assert out == {"p": "/x/y"}


def test_json_safe_handles_tuples_as_lists() -> None:
    out = _json_safe((1, 2, 3))
    assert out == [1, 2, 3]


def test_json_safe_handles_non_str_dict_keys() -> None:
    out = _json_safe({1: "a", 2.0: "b"})
    assert out == {"1": "a", "2.0": "b"}


def test_json_safe_leaves_scalars_alone() -> None:
    for v in [1, 1.5, "x", True, None]:
        assert _json_safe(v) == v


def test_write_audit_serializes_unicode(tmp_path: Path) -> None:
    log_dir = tmp_path / "audit"
    write_audit(log_dir, {"event": "x", "vendor": "Café Olé"})
    raw = next(log_dir.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "Café Olé" in raw
