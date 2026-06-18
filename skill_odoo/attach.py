"""attach-file: upload an ``ir.attachment`` to any Odoo record.

Issue A-I-M-S/skill-odoo#9. The dispatch lives here.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

from .audit import write_audit
from .config import Settings
from .odoo_client import Odoo

LOG = logging.getLogger("skill-odoo.attach")

MAX_ATTACH_BYTES = 25 * 1024 * 1024  # 25 MB


def run_attach_file(
    settings: Settings,
    *,
    model: str,
    res_id: int,
    file_path: str | Path,
    name: str | None = None,
) -> dict[str, Any]:
    """Upload a local file as ``ir.attachment`` attached to an Odoo record.

    Validates:
    - the file exists
    - the file is non-empty
    - the file is <= 25 MB (matches Odoo's default ``attachment.size`` limit)

    Returns the attachment id, name, file_size, mimetype, and SHA-256
    checksum so the agent can verify the upload succeeded.
    """
    path = Path(file_path)
    if not path.exists():
        return {
            "ok": False,
            "error": f"file not found: {file_path}",
            "code": 4,
            "error_kind": "not_found",
        }
    if not path.is_file():
        return {
            "ok": False,
            "error": f"not a file: {file_path}",
            "code": 2,
            "error_kind": "bad_args",
        }
    size = path.stat().st_size
    if size == 0:
        return {
            "ok": False,
            "error": f"file is empty: {file_path}",
            "code": 2,
            "error_kind": "bad_args",
        }
    if size > MAX_ATTACH_BYTES:
        return {
            "ok": False,
            "error": f"file too large: {size} bytes (limit {MAX_ATTACH_BYTES})",
            "code": 2,
            "error_kind": "file_too_large",
            "size_bytes": size,
            "limit_bytes": MAX_ATTACH_BYTES,
        }
    checksum = _sha256(path)
    display_name = name or path.name
    mimetype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    odoo = Odoo(
        url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    try:
        attachment_id = odoo.attach_file(
            res_model=model,
            res_id=res_id,
            file_path=path,
            mimetype=mimetype,
            display_name=display_name,
        )
    except RuntimeError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "code": 3,
            "error_kind": "odoo_error",
        }
    try:
        write_audit(
            settings.audit_log_dir,
            {
                "event": "attach_file",
                "ok": True,
                "res_model": model,
                "res_id": res_id,
                "filename": display_name,
                "size_bytes": size,
                "mimetype": mimetype,
                "attachment_id": attachment_id,
            },
        )
    except Exception:
        pass
    return {
        "ok": True,
        "attachment": {
            "id": attachment_id,
            "name": display_name,
            "file_size": size,
            "mimetype": mimetype,
            "checksum_sha256": checksum,
            "res_model": model,
            "res_id": res_id,
        },
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
