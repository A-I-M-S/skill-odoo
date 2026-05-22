"""Text extraction from receipts: plain text, PDF text, image OCR, PDF-image OCR."""
from __future__ import annotations

from pathlib import Path
from typing import Any


SUPPORTED_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def extract_text(document_path: Path) -> tuple[str, str]:
    """Return ``(text, source_kind)`` for a given document.

    ``source_kind`` is one of ``plain_text``, ``pdf_text``, ``ocr_pdf``, ``ocr_image``.
    """
    suffix = document_path.suffix.lower()

    if suffix == ".txt":
        return document_path.read_text(encoding="utf-8", errors="ignore"), "plain_text"

    if suffix == ".pdf":
        text = _extract_pdf_text(document_path)
        if text.strip():
            return text, "pdf_text"
        return _ocr_pdf(document_path), "ocr_pdf"

    if suffix in SUPPORTED_IMAGE:
        return _ocr_image(document_path), "ocr_image"

    raise ValueError(f"Unsupported file type: {suffix}")


def _extract_pdf_text(path: Path) -> str:
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return ""
    try:
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return ""


def _ocr_pdf(path: Path) -> str:
    try:
        from pdf2image import convert_from_path  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pdf2image + pytesseract required for scanned PDFs") from exc
    pages = convert_from_path(str(path), dpi=300)
    return "\n".join(_ocr_pil_image(p) for p in pages)


def _ocr_image(path: Path) -> str:
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pillow required for image OCR") from exc
    with Image.open(path) as image:
        return _ocr_pil_image(image)


def _ocr_pil_image(image: Any) -> str:
    try:
        from PIL import ImageEnhance, ImageOps  # type: ignore
        import pytesseract  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pytesseract + pillow required for OCR") from exc

    img = ImageOps.exif_transpose(image).convert("RGB")
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Sharpness(gray).enhance(2.0)
    scale = max(2, 1200 // max(gray.width, 1))
    if scale > 1:
        gray = gray.resize((gray.width * scale, gray.height * scale))
    return pytesseract.image_to_string(gray, config="--psm 6")
