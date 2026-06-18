"""Structured JSONL audit logging for receipt processing.

Logs are local only. They intentionally exclude API keys and raw binary files.

Secret filtering is enforced: any key whose name matches the deny-list below
is dropped from the event before it lands in the log, even if a caller
accidentally puts the key in the event dict. The deny-list is intentionally
narrow — it's a safety net, not a replacement for the convention of not
passing secrets to ``write_audit``.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Keys that must never land in the audit log, even if a caller puts them there.
# Case-insensitive match on the full key name; we also catch *_API_KEY, *_SECRET,
# *_TOKEN, *_PASSWORD, TELEGRAM_*, ODOO_API_KEY, etc.
_DENY_KEYS = {
    "ODOO_API_KEY",
    "ODOO_LOGIN",
    "OPENROUTER_API_KEY",
    "AI_SECRET",
    "OCR_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USER_IDS",
    "TELEGRAM_ALLOWED_USERNAMES",
    "PASSWORD",
    "API_KEY",
    "SECRET",
    "TOKEN",
}
_DENY_PATTERN = re.compile(
    r"(?i)\b("
    r"ODOO_API_KEY|OPENROUTER_API_KEY|AI_SECRET|OCR_API_KEY|"
    r"TELEGRAM_BOT_TOKEN|TELEGRAM_ALLOWED_"
    r"|.*_API_KEY|.*_SECRET|.*_TOKEN|.*_PASSWORD"
    r")\b"
)


def _is_secret_key(name: Any) -> bool:
    upper = str(name).upper()
    if upper in _DENY_KEYS:
        return True
    return bool(_DENY_PATTERN.fullmatch(str(name)))


def write_audit(log_dir: Path, event: dict[str, Any]) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = log_dir / f"{now.strftime('%Y-%m')}.jsonl"
    payload = {"ts_utc": now.isoformat(), **_json_safe(event)}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return path


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _json_safe(v)
            for k, v in value.items()
            if not _is_secret_key(k)
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value
