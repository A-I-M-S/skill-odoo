"""Thin XML-RPC wrapper around Odoo's external API for the receipt pipeline."""
from __future__ import annotations

import base64
import xmlrpc.client
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass
class Odoo:
    url: str
    db: str
    login: str
    api_key: str

    def __post_init__(self) -> None:
        self._common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        uid = self._common.authenticate(self.db, self.login, self.api_key, {})
        if not uid:
            raise RuntimeError("Odoo authentication failed")
        self.uid: int = uid

    # --- low-level RPC ----------------------------------------------------
    def rpc(self, model: str, method: str, args: list | None = None, kw: dict | None = None) -> Any:
        return self._models.execute_kw(self.db, self.uid, self.api_key, model, method, args or [], kw or {})

    # --- references -------------------------------------------------------
    def user_info(self) -> dict[str, Any]:
        rec = self.rpc("res.users", "read", [[self.uid]], {"fields": ["name", "login", "company_id", "tz"]})[0]
        rec["company_id_int"] = rec["company_id"][0]
        return rec

    def company_currency(self, company_id: int) -> tuple[int, str]:
        rec = self.rpc("res.company", "read", [[company_id]], {"fields": ["currency_id"]})[0]
        return rec["currency_id"][0], rec["currency_id"][1]

    def find_journal(self, code: str, type_: str = "general") -> dict[str, Any]:
        ids = self.rpc("account.journal", "search", [[["code", "=", code]]], {"limit": 1})
        if not ids:
            ids = self.rpc("account.journal", "search", [[["type", "=", type_]]], {"limit": 1})
        if not ids:
            raise RuntimeError(f"No journal found for code={code!r} or type={type_!r}")
        return self.rpc("account.journal", "read", [ids], {"fields": ["name", "code", "type"]})[0]

    def find_account(self, code: str) -> dict[str, Any]:
        ids = self.rpc("account.account", "search", [[["code", "=", code]]], {"limit": 1})
        if not ids:
            raise RuntimeError(f"Account code {code!r} not found")
        return self.rpc("account.account", "read", [ids], {"fields": ["code", "name", "account_type", "company_ids"]})[0]

    def chart_of_accounts(self) -> list[dict[str, Any]]:
        ids = self.rpc(
            "account.account",
            "search",
            [[]],
            {"limit": 0, "order": "code"},
        )
        return self.rpc("account.account", "read", [ids], {"fields": ["code", "name", "account_type"]})

    # --- journal entries --------------------------------------------------
    def find_month_draft(self, *, journal_id: int, ref: str, today: date) -> int | None:
        """Locate the draft account.move we should append to for this month.

        Preference:
        1. A draft move in this journal whose ref/name already equals ``ref``.
        2. Any draft move in this journal dated within this month (we'll adopt it).
        """
        start = today.replace(day=1)
        # next-month first day
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        ids = self.rpc(
            "account.move",
            "search",
            [[
                ["journal_id", "=", journal_id],
                ["state", "=", "draft"],
                "|", ["ref", "=", ref], ["name", "=", ref],
            ]],
            {"limit": 1},
        )
        if ids:
            return ids[0]
        ids = self.rpc(
            "account.move",
            "search",
            [[
                ["journal_id", "=", journal_id],
                ["state", "=", "draft"],
                ["date", ">=", start.isoformat()],
                ["date", "<", end.isoformat()],
                ["move_type", "=", "entry"],
            ]],
            {"limit": 1, "order": "id desc"},
        )
        return ids[0] if ids else None

    def read_move(self, move_id: int) -> dict[str, Any]:
        return self.rpc(
            "account.move",
            "read",
            [[move_id]],
            {"fields": ["name", "ref", "state", "date", "journal_id", "line_ids", "amount_total"]},
        )[0]

    def read_lines(self, line_ids: list[int]) -> list[dict[str, Any]]:
        if not line_ids:
            return []
        return self.rpc(
            "account.move.line",
            "read",
            [line_ids],
            {"fields": ["id", "name", "account_id", "debit", "credit"]},
        )

    def create_move(self, *, journal_id: int, ref: str, name: str | None, on: date, lines: list[dict[str, Any]]) -> int:
        vals = {
            "journal_id": journal_id,
            "date": on.isoformat(),
            "ref": ref,
            "move_type": "entry",
            "line_ids": [(0, 0, line) for line in lines],
        }
        if name:
            vals["name"] = name
        return self.rpc("account.move", "create", [vals])

    def write_move(self, move_id: int, vals: dict[str, Any]) -> None:
        self.rpc("account.move", "write", [[move_id], vals])

    def replace_move_lines(self, move_id: int, lines: list[dict[str, Any]]) -> None:
        # 5 = remove all, then 0 = create new
        ops: list[Any] = [(5, 0, 0)] + [(0, 0, line) for line in lines]
        self.write_move(move_id, {"line_ids": ops})

    # --- attachments ------------------------------------------------------
    def attach_file(self, *, move_id: int, file_path: Path, mimetype: str | None = None) -> int:
        data = base64.b64encode(file_path.read_bytes()).decode()
        vals = {
            "name": file_path.name,
            "datas": data,
            "res_model": "account.move",
            "res_id": move_id,
            "type": "binary",
        }
        if mimetype:
            vals["mimetype"] = mimetype
        return self.rpc("ir.attachment", "create", [vals])
