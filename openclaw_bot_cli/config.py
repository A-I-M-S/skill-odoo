"""Environment loading for skill-odoo.

A `.env` file (KEY=VALUE per line, # comments, optional quoted values) is loaded
into ``os.environ`` without overriding already-set values.  A small typed
``Settings`` view is exposed for the rest of the package.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _b(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass
class Settings:
    # Odoo
    odoo_url: str
    odoo_db: str
    odoo_login: str
    odoo_api_key: str
    # Accounting
    shareholder_account_code: str
    journal_code: str
    journal_type: str
    monthly_consolidate: bool
    move_ref_format: str
    move_name_from_ref: bool
    auto_post: bool
    default_currency: str
    # FX
    fx_provider: str
    fx_base_currency: str
    # OCR
    ocr_provider: str
    zo_ocr_token: str
    zo_ocr_model: str
    ocr_base_url: str
    ocr_api_key: str
    ocr_model: str
    # Inbox
    receipts_inbox: Path
    receipts_processed_delete: bool
    # AI
    ai_chat_url: str
    ai_model: str
    ai_secret: str
    ai_provider_order: str
    audit_log_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        missing = [k for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY") if not os.getenv(k)]
        if missing:
            raise RuntimeError(f"Missing required env: {', '.join(missing)}")
        return cls(
            odoo_url=os.environ["ODOO_URL"].rstrip("/"),
            odoo_db=os.environ["ODOO_DB"],
            odoo_login=os.environ["ODOO_LOGIN"],
            odoo_api_key=os.environ["ODOO_API_KEY"],
            shareholder_account_code=os.getenv("SHAREHOLDER_ACCOUNT_CODE", "202040"),
            journal_code=os.getenv("JOURNAL_CODE", "MISC"),
            journal_type=os.getenv("JOURNAL_TYPE", "general"),
            monthly_consolidate=_b("MONTHLY_CONSOLIDATE", True),
            move_ref_format=os.getenv("MOVE_REF_FORMAT", "%y%b"),
            move_name_from_ref=_b("MOVE_NAME_FROM_REF", True),
            auto_post=_b("AUTO_POST", False),
            default_currency=os.getenv("DEFAULT_CURRENCY", "SGD").upper(),
            fx_provider=os.getenv("FX_PROVIDER", "frankfurter").lower(),
            fx_base_currency=os.getenv("FX_BASE_CURRENCY", "SGD").upper(),
            ocr_provider=os.getenv("OCR_PROVIDER", "openai").strip().lower(),
            zo_ocr_token=os.getenv("ZO_OCR_TOKEN", os.getenv("ZO_API_KEY", os.getenv("ZO_CLIENT_IDENTITY_TOKEN", ""))).strip(),
            zo_ocr_model=os.getenv("ZO_OCR_MODEL", "openai:gpt-5.5-2026-04-23").strip(),
            ocr_base_url=os.getenv("OCR_BASE_URL", "https://openrouter.ai/api/v1").strip(),
            ocr_api_key=os.getenv(
                "OCR_API_KEY",
                os.getenv("OPENROUTER_AK", os.getenv("OPENROUTER_API_KEY", os.getenv("AI_SECRET", ""))),
            ).strip(),
            ocr_model=os.getenv("OCR_MODEL", "minimax/minimax-01").strip(),
            receipts_inbox=Path(os.getenv("RECEIPTS_INBOX", "./incoming_receipts")),
            receipts_processed_delete=_b("RECEIPTS_PROCESSED_DELETE", True),
            ai_chat_url=os.getenv("AI_CHAT_URL", "").strip(),
            ai_model=os.getenv("AI_MODEL", "").strip(),
            ai_secret=os.getenv("AI_SECRET", "").strip(),
            ai_provider_order=os.getenv("AI_PROVIDER_ORDER", "").strip(),
            audit_log_dir=Path(os.getenv("AUDIT_LOG_DIR", "./audit_logs")),
        )
