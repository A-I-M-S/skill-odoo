from __future__ import annotations

import json
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from .ai_automation import classify_accounting_with_ai
from .models import AccountingResult, Entry


def load_chart_of_accounts(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "accounts" in payload:
        payload = payload["accounts"]
    if not isinstance(payload, list):
        raise ValueError("Chart of accounts JSON must be a list or include an 'accounts' list.")
    return payload


def refresh_chart_of_accounts(path: Path, source_cmd: str) -> None:
    process = subprocess.run(source_cmd, shell=True, check=False, capture_output=True, text=True)
    if process.returncode != 0:
        raise RuntimeError(f"COA refresh failed: {process.stderr.strip()}")
    path.write_text(process.stdout, encoding="utf-8")


def classify_accounting(raw_text: str, coa: list[dict[str, Any]], *, default_currency: str) -> AccountingResult:
    total = _extract_total(raw_text)
    vendor = _extract_vendor(raw_text)
    currency = _extract_currency(raw_text) or default_currency

    expense_account = _find_account(coa, ["expense", "operating", "cost"]) or _first_account_code(coa)
    payable_account = _find_account(coa, ["payable", "creditor", "liability"]) or _first_account_code(coa)

    confidence = 0.55
    notes = "Heuristic classification from extracted text"
    if total is not None:
        confidence += 0.25
    if vendor != "Unknown Vendor":
        confidence += 0.10

    amount = float(total or 0.0)
    entries = [
        Entry(account_code=expense_account, debit=amount, credit=0.0),
        Entry(account_code=payable_account, debit=0.0, credit=amount),
    ]

    return AccountingResult(
        document_type="receipt",
        vendor=vendor,
        tx_date=str(date.today()),
        currency=currency,
        entries=entries,
        confidence=min(confidence, 0.98),
        notes=notes,
        raw_text=raw_text,
    )




def classify_accounting_automated(raw_text: str, coa: list[dict[str, Any]], *, default_currency: str) -> AccountingResult:
    """AI-first classification that prompts a chat model with expense text and chart of accounts."""
    return classify_accounting_with_ai(raw_text, coa, default_currency=default_currency)

def validate_result(result: AccountingResult, coa: list[dict[str, Any]]) -> None:
    debit = sum(item.debit for item in result.entries)
    credit = sum(item.credit for item in result.entries)
    if round(debit, 2) != round(credit, 2):
        raise ValueError("Unbalanced journal entry: debits and credits do not match.")

    if debit == 0:
        raise ValueError("Total amount is missing; refusing to post zero-value entry.")

    allowed_codes = {str(account.get("code", "")).strip() for account in coa}
    for item in result.entries:
        if item.account_code not in allowed_codes:
            raise ValueError(f"Unknown account code in entry: {item.account_code}")


def post_to_odoo(result: AccountingResult, api_key: str, dry_run: bool = True) -> dict[str, Any]:
    if dry_run:
        return {
            "posted": False,
            "mode": "dry_run",
            "message": "Posting skipped. Pass --allow-post to enable real posting integration.",
        }

    return {
        "posted": False,
        "mode": "stub",
        "message": f"API key received (length={len(api_key)}). Implement Odoo API call here.",
    }


def _extract_total(text: str) -> float | None:
    patterns = [
        r"(?:total\s*[:\-]?\s*)([0-9]+(?:\.[0-9]{1,2})?)",
        r"(?:amount\s*due\s*[:\-]?\s*)([0-9]+(?:\.[0-9]{1,2})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    numbers = re.findall(r"\b([0-9]+\.[0-9]{2})\b", text)
    if numbers:
        return float(numbers[-1])
    return None


def _extract_vendor(text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first_line:
        return first_line[:120]
    return "Unknown Vendor"


def _extract_currency(text: str) -> str | None:
    for code in ["USD", "EUR", "GBP", "AED", "INR", "SAR"]:
        if re.search(rf"\b{code}\b", text):
            return code
    return None


def _find_account(coa: list[dict[str, Any]], keywords: list[str]) -> str | None:
    for account in coa:
        haystack = f"{account.get('name', '')} {account.get('type', '')}".lower()
        if any(word in haystack for word in keywords):
            code = str(account.get("code", "")).strip()
            if code:
                return code
    return None


def _first_account_code(coa: list[dict[str, Any]]) -> str:
    for account in coa:
        code = str(account.get("code", "")).strip()
        if code:
            return code
    raise ValueError("Chart of accounts is empty or missing account codes.")
