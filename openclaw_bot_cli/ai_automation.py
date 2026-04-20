from __future__ import annotations

import json
import os
from typing import Any
from urllib import request

from .models import AccountingResult, Entry


def classify_accounting_with_ai(raw_text: str, coa: list[dict[str, Any]], *, default_currency: str) -> AccountingResult:
    """Ask a chat model to map extracted expense text into accounting JSON and convert it to AccountingResult."""
    chat_url = os.getenv("AI_CHAT_URL", "").strip()
    model = os.getenv("AI_MODEL", "").strip()
    secret = os.getenv("AI_SECRET", "").strip()

    if not chat_url or not model or not secret:
        raise ValueError("Missing AI config. Set AI_CHAT_URL, AI_MODEL, and AI_SECRET in .env.")

    prompt = _build_prompt(raw_text=raw_text, coa=coa, default_currency=default_currency)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an accounting assistant that returns strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }

    req = request.Request(
        chat_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
        method="POST",
    )

    with request.urlopen(req, timeout=60) as response:
        body = response.read().decode("utf-8")

    message = _extract_model_message(body)
    model_json = _extract_json_block(message)

    return _to_accounting_result(model_json, raw_text=raw_text, default_currency=default_currency)


def _build_prompt(*, raw_text: str, coa: list[dict[str, Any]], default_currency: str) -> str:
    return (
        "You are given expense document text and a chart of accounts. "
        "Choose correct double-entry accounts and return ONLY JSON with this schema: "
        "{document_type, vendor, date, currency, entries:[{account_code,debit,credit}], confidence, notes}. "
        "Entries must be balanced and account_code must exist in chart of accounts. "
        f"If currency missing use {default_currency}.\n\n"
        f"Expense Text:\n{raw_text}\n\n"
        f"Chart of Accounts JSON:\n{json.dumps(coa, ensure_ascii=False)}"
    )


def _extract_model_message(response_body: str) -> str:
    payload = json.loads(response_body)

    if isinstance(payload, dict):
        # OpenAI-style response
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message", {})
                if isinstance(message, dict) and "content" in message:
                    return str(message["content"])
                if "text" in first:
                    return str(first["text"])

        # Generic fallback
        if "content" in payload:
            return str(payload["content"])

    raise ValueError("Unsupported AI response format; expected chat-completions-like payload.")


def _extract_json_block(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("AI response did not include JSON object.")

    return json.loads(cleaned[start : end + 1])


def _to_accounting_result(payload: dict[str, Any], *, raw_text: str, default_currency: str) -> AccountingResult:
    entries_payload = payload.get("entries", [])
    if not isinstance(entries_payload, list) or not entries_payload:
        raise ValueError("AI response missing 'entries' list.")

    entries: list[Entry] = []
    for item in entries_payload:
        entries.append(
            Entry(
                account_code=str(item.get("account_code", "")).strip(),
                debit=float(item.get("debit", 0.0) or 0.0),
                credit=float(item.get("credit", 0.0) or 0.0),
            )
        )

    return AccountingResult(
        document_type=str(payload.get("document_type", "receipt")),
        vendor=str(payload.get("vendor", "Unknown Vendor")),
        tx_date=str(payload.get("date", "")),
        currency=str(payload.get("currency", default_currency)),
        entries=entries,
        confidence=float(payload.get("confidence", 0.5) or 0.5),
        notes=str(payload.get("notes", "AI classification")),
        raw_text=raw_text,
    )
