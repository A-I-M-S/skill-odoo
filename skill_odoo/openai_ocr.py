"""OpenAI-compatible vision OCR.

Sends the receipt image (or first page of a PDF) to any OpenAI-compatible
chat-completions endpoint (OpenRouter, AgentRouter, MiniMax direct, OpenAI,
local llama.cpp, etc.) using the standard vision content payload, so the
model receives the image natively rather than as base64 inside a text prompt.

Defaults are wired for OpenRouter + ``minimax/minimax-01`` (multimodal).
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import time
import urllib.request
from pathlib import Path

LOG = logging.getLogger("skill-odoo.ocr")


class OpenAIOCRError(RuntimeError):
    pass


_OCR_PROMPT = (
    "You are doing OCR on a receipt image. Extract ALL visible receipt text "
    "verbatim. Keep original languages and numbers exactly. Preserve line "
    "breaks as much as possible. Do not summarize, translate, classify, or "
    "add commentary. Return only plain text."
)

_PDF_TO_IMAGE_DPI = 200


def _image_to_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pdf2image import convert_from_path  # type: ignore
        except ImportError as exc:
            raise OpenAIOCRError(
                "pdf2image required to OCR PDFs with vision model"
            ) from exc
        import io

        pages = convert_from_path(str(path), dpi=_PDF_TO_IMAGE_DPI, first_page=1, last_page=1)
        if not pages:
            raise OpenAIOCRError("PDF has no pages")
        buf = io.BytesIO()
        pages[0].save(buf, format="PNG")
        data = buf.getvalue()
        mime = "image/png"
    else:
        data = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    if len(data) > 20 * 1024 * 1024:
        raise OpenAIOCRError("file too large for vision OCR (>20MB)")
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def ocr_with_openai(
    path: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: int = 180,
    extra_headers: dict[str, str] | None = None,
) -> str:
    """OCR ``path`` via an OpenAI-compatible chat-completions endpoint.

    ``base_url`` is the API root (e.g. ``https://openrouter.ai/api/v1``); the
    ``/chat/completions`` suffix is added automatically. The full URL form is
    also accepted.
    """
    if not api_key:
        raise OpenAIOCRError("missing OCR API key")
    if not base_url:
        raise OpenAIOCRError("missing OCR base URL")
    if not model:
        raise OpenAIOCRError("missing OCR model")

    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"

    data_url = _image_to_data_url(path)

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 4096,
        "stream": False,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/A-I-M-S/skill-odoo",
        "X-Title": "skill-odoo",
    }
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    last_error: OpenAIOCRError | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode(errors="ignore")[:500]
            last_error = OpenAIOCRError(f"OCR HTTP {exc.code}: {err_body[:400]}")
            LOG.warning("OCR attempt %d/3 HTTP %d: %s", attempt + 1, exc.code, err_body[:200])
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise last_error from exc
        except Exception as exc:
            last_error = OpenAIOCRError(f"OCR request failed: {exc}")
            LOG.warning("OCR attempt %d/3 failed: %s", attempt + 1, exc)
            if attempt == 2:
                raise last_error from exc
        time.sleep([2, 5][attempt])
    else:
        raise last_error or OpenAIOCRError("OCR request failed after 3 attempts")

    if "error" in body:
        err = body["error"]
        err_msg = err.get("message", str(err))
        raise OpenAIOCRError(f"OpenRouter API error: {err_msg}")

    try:
        choices = body.get("choices") or []
        message = choices[0]["message"]
        content = message.get("content", "")
    except Exception as exc:
        raise OpenAIOCRError(f"unexpected OCR response shape: {body!r}") from exc

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                txt = item.get("text") or item.get("content") or ""
                if txt:
                    parts.append(str(txt))
            elif isinstance(item, str):
                parts.append(item)
        content = "\n".join(parts)

    text = str(content or "").strip()
    if not text:
        raise OpenAIOCRError("OCR returned empty text")
    LOG.info("OCR success model=%s text_len=%d", model, len(text))
    return text
