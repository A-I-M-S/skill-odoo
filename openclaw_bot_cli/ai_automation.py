"""LLM-based receipt classifier.

Calls an OpenAI-compatible Chat Completions endpoint (OpenRouter by default),
optionally pinning the OpenRouter provider via ``provider.order``.  The model
returns a single JSON object mapping the receipt onto one debit account from a
slimmed Chart-of-Accounts.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .models import ReceiptExtraction


class AIError(RuntimeError):
    pass


SYSTEM_PROMPT = (
    "You are an accounting assistant for a Singapore private limited company. "
    "Given OCR text from one receipt or invoice and a short Chart of Accounts (COA), "
    "produce ONE JSON object with the fields: vendor (str), date (YYYY-MM-DD), "
    "currency (ISO-4217, e.g. SGD/USD/MYR), amount (number, gross total >0), "
    "debit_account_code (str, must be a code from the provided COA), description (str, <=80 chars), "
    "confidence (0..1 number), notes (str). "
    "Pick the SINGLE most appropriate expense or asset account. "
    "Never invent codes. Never include explanation or markdown. Output JSON only."
)


def classify_receipt(
    raw_text: str,
    coa_short: list[dict[str, Any]],
    *,
    chat_url: str,
    model: str,
    api_key: str,
    provider_order: str = "",
    default_currency: str = "SGD",
    timeout: int = 60,
) -> ReceiptExtraction:
    if not chat_url or not model or not api_key:
        raise AIError("AI_CHAT_URL, AI_MODEL, AI_SECRET must all be set")

    user_msg = (
        f"Default currency if unclear: {default_currency}.\n"
        f"Chart of Accounts (code | name | type):\n"
        + "\n".join(f"{a['code']} | {a['name']} | {a.get('account_type','')}" for a in coa_short)
        + "\n\nReceipt OCR text:\n"
        + raw_text.strip()
    )

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    if provider_order:
        payload["provider"] = {"order": [provider_order], "allow_fallbacks": False}

    req = urllib.request.Request(
        chat_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/A-I-M-S/skill-odoo",
            "X-Title": "skill-odoo",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        raise AIError(f"LLM HTTP {exc.code}: {exc.read().decode(errors='ignore')[:400]}") from exc
    except Exception as exc:
        raise AIError(f"LLM call failed: {exc}") from exc

    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _coerce_json(content)
    return _to_extraction(parsed, default_currency=default_currency, raw_text=raw_text)


def _coerce_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # last-resort: pick first { ... } block
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise AIError(f"LLM did not return JSON; got: {text[:200]}")


def _to_extraction(p: dict[str, Any], *, default_currency: str, raw_text: str) -> ReceiptExtraction:
    try:
        amount = float(p.get("amount", 0) or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return ReceiptExtraction(
        vendor=str(p.get("vendor", "Unknown")).strip() or "Unknown",
        tx_date=str(p.get("date", "")).strip(),
        currency=str(p.get("currency", default_currency)).strip().upper() or default_currency,
        amount=round(abs(amount), 2),
        debit_account_code=str(p.get("debit_account_code", "")).strip(),
        description=str(p.get("description", "")).strip()[:200],
        confidence=float(p.get("confidence", 0) or 0),
        notes=str(p.get("notes", "")).strip(),
        raw_text=raw_text,
    )


def shortlist_coa(coa: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim COA to plausible debit candidates for receipts/invoices.

    Keeps expense, current-asset (e.g. prepaid) and a couple of common cost accounts
    so the prompt stays compact.
    """
    keep_types = {
        "expense",
        "expense_depreciation",
        "expense_direct_cost",
        "asset_current",
        "asset_prepayments",
        "asset_fixed",
        "asset_non_current",
    }
    return [a for a in coa if a.get("account_type") in keep_types]
