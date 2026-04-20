from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Entry:
    account_code: str
    debit: float
    credit: float


@dataclass
class AccountingResult:
    document_type: str
    vendor: str
    tx_date: str
    currency: str
    entries: list[Entry]
    confidence: float
    notes: str
    raw_text: str

    def as_json(self) -> dict[str, Any]:
        return {
            "document_type": self.document_type,
            "vendor": self.vendor,
            "date": self.tx_date,
            "currency": self.currency,
            "entries": [
                {
                    "account_code": entry.account_code,
                    "debit": round(entry.debit, 2),
                    "credit": round(entry.credit, 2),
                }
                for entry in self.entries
            ],
            "confidence": round(self.confidence, 2),
            "notes": self.notes,
            "raw_text": self.raw_text,
        }
