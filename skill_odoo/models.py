from __future__ import annotations

from dataclasses import dataclass
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
