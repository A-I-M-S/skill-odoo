from __future__ import annotations

from pathlib import Path
from typing import Any


def extract_text(document_path: Path) -> tuple[str, str]:
    suffix = document_path.suffix.lower()

    if suffix == ".txt":
        return document_path.read_text(encoding="utf-8"), "plain_text"

    if suffix == ".pdf":
        text = _extract_pdf_text(document_path)
        if text.strip():
            return text, "pdf_text"
        return _ocr_pdf(document_path), "ocr_pdf"

    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        return _ocr_image(document_path), "ocr_image"

    raise ValueError(f"Unsupported document format: {document_path.suffix}")


def _extract_pdf_text(path: Path) -> str:
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return ""

    try:
        pages: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
        return "\n".join(pages)
    except Exception:
        # Any PDF text extraction failure falls back to full-page OCR in extract_text().
        return ""
    pages: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _ocr_pdf(path: Path) -> str:
    try:
        from pdf2image import convert_from_path  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pdf2image is required for OCR fallback on PDFs.") from exc

    images = convert_from_path(str(path))
    text_chunks: list[str] = []
    for image in images:
        text_chunks.append(_ocr_pil_image(image))
    return "\n".join(text_chunks)


def _ocr_image(path: Path) -> str:
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pytesseract and pillow are required for image OCR.") from exc

    with Image.open(path) as image:
        return pytesseract.image_to_string(image)


def _ocr_pil_image(image: Any) -> str:
    try:
        import pytesseract  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pytesseract is required for OCR.") from exc
    return pytesseract.image_to_string(image)
