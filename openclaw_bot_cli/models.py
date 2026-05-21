from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReceiptExtraction:
    """Single-receipt extraction result.

    ``amount`` is the gross transaction amount in ``currency``.
    ``debit_account_code`` is the expense / asset code the AI chose against the COA.
    """

    vendor: str
    tx_date: str            # ISO YYYY-MM-DD
    currency: str           # ISO 4217 (e.g. SGD)
    amount: float           # gross, positive
    debit_account_code: str
    description: str
    confidence: float = 0.0
    notes: str = ""
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor": self.vendor,
            "date": self.tx_date,
            "currency": self.currency,
            "amount": round(self.amount, 2),
            "debit_account_code": self.debit_account_code,
            "description": self.description,
            "confidence": round(self.confidence, 2),
            "notes": self.notes,
        }


@dataclass
class JournalLineDraft:
    """One side of a journal entry to be posted to Odoo."""

    account_id: int
    name: str
    debit: float = 0.0
    credit: float = 0.0
    partner_id: int | None = None

    def to_odoo(self) -> dict[str, Any]:
        line: dict[str, Any] = {
            "account_id": self.account_id,
            "name": self.name,
            "debit": round(self.debit, 2),
            "credit": round(self.credit, 2),
        }
        if self.partner_id:
            line["partner_id"] = self.partner_id
        return line
