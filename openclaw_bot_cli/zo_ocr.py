"""Zo inference OCR via /zo/ask.

Used for phone photos where Tesseract struggles with crumpled receipts,
non-Latin text, shadows, and perspective distortion.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path


class ZoOCRError(RuntimeError):
    pass


def ocr_with_zo(path: Path, *, token: str, model_name: str, timeout: int = 180) -> str:
    if not token:
        raise ZoOCRError("missing Zo API token")
    data = path.read_bytes()
    if len(data) > 15 * 1024 * 1024:
        raise ZoOCRError("file too large for Zo OCR (>15MB)")
    b64 = base64.b64encode(data).decode("ascii")
    prompt = (
        "You are doing OCR on a receipt image. Extract ALL visible receipt text verbatim. "
        "Keep original languages and numbers exactly. Preserve line breaks as much as possible. "
        "Do not summarize, translate, classify, or add commentary. Return only plain text.\n\n"
        f"Filename: {path.name}\n"
        f"Base64 image/file content:\n{b64}"
    )
    req = urllib.request.Request(
        "https://api.zo.computer/zo/ask",
        data=json.dumps({"input": prompt, "model_name": model_name}).encode("utf-8"),
        headers={"authorization": token, "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        raise ZoOCRError(f"Zo OCR request failed: {exc}") from exc
    out = body.get("output", "")
    if isinstance(out, dict):
        out = json.dumps(out, ensure_ascii=False)
    text = str(out).strip()
    if not text:
        raise ZoOCRError("Zo OCR returned empty text")
    return text
