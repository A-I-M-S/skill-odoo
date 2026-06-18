"""OCR router: agent-first, in-skill fallback chain.

Per Aloy's decision (issue #1): the OpenClaw agent's own OCR is the primary
path. If the agent can read the receipt itself, it should pass the extracted
text via \`--text\` to \`process-receipt\` and the in-skill chain is skipped.

When \`--text\` is absent, the in-skill chain runs in this order:

1. \`provided_text\` (if non-None, returned as-is) — agent's authoritative OCR
2. \`pdfplumber\` text extraction (PDF text layer)
3. \`pytesseract\` local OCR
4. \`openai_ocr\` — Gemma via OpenRouter (vision LLM)

The winning path is returned in the \`source\` field of the result.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .config import Settings
from .extraction import extract_text

LOG = logging.getLogger("skill-odoo.ocr_router")


def extract_receipt_text(
    settings: Settings,
    file_path: Path,
    *,
    provided_text: str | None = None,
) -> dict[str, Any]:
    """Run the OCR routing policy and return the extracted text.

    Returns a dict with:
    - ``text`` (str): the extracted text (may be empty if all paths fail)
    - ``source`` (str): which path won — ``agent``, ``plumber``, ``tesseract``,
      ``openai``, ``empty``, or ``failed``
    - ``file_path`` (str): the input path
    - ``file_size`` (int): the input file size
    """
    if not file_path.exists():
        return {
            "text": "",
            "source": "failed",
            "file_path": str(file_path),
            "error": f"file not found: {file_path}",
        }

    if provided_text:
        return {
            "text": provided_text,
            "source": "agent",
            "file_path": str(file_path),
            "file_size": file_path.stat().st_size,
        }

    suffix = file_path.suffix.lower()
    # Try pdfplumber for PDFs first; it's free and fast.
    if suffix == ".pdf":
        from .extraction import _extract_pdf_text
        text = _extract_pdf_text(file_path)
        if text.strip():
            return {
                "text": text,
                "source": "plumber",
                "file_path": str(file_path),
                "file_size": file_path.stat().st_size,
            }
    # Then the openai_ocr (vision) chain.
    try:
        text, source = extract_text(
            file_path,
            ocr_provider=settings.ocr_provider,
            ocr_base_url=settings.ocr_base_url,
            ocr_api_key=settings.ocr_api_key,
            ocr_model=settings.ocr_model,
        )
        # Map the openai_ocr_* sources to a uniform label.
        if source.startswith("openai_ocr"):
            label = "openai"
        elif source.startswith("ocr_"):
            label = "tesseract"
        elif source == "plain_text":
            label = "plumber"
        elif source == "pdf_text":
            label = "plumber"
        else:
            label = source
        return {
            "text": text,
            "source": label,
            "file_path": str(file_path),
            "file_size": file_path.stat().st_size,
        }
    except Exception as exc:
        LOG.warning("OCR router: in-skill OCR failed for %s: %s", file_path, exc)
        return {
            "text": "",
            "source": "failed",
            "file_path": str(file_path),
            "error": f"{type(exc).__name__}: {exc}",
        }
