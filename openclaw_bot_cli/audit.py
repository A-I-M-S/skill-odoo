"""Structured JSONL audit logging for receipt processing.

Logs are local only. They intentionally exclude API keys and raw binary files.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value
