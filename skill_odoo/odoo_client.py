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

    # --- chart of accounts (filtered) -------------------------------------
    def search_accounts(
        self,
        *,
        code_prefix: str | None = None,
        account_type: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Filtered account.account list.

        ``code_prefix`` is a substring match on the account code (e.g. ``"6"``
        for expenses). ``account_type`` is an exact match on
        ``account.account.account_type`` (e.g. ``"expense"``,
        ``"liability_payable"``). Both filters combine with AND. An empty /
        no-filter call returns the full COA up to ``limit``.
        """
        domain: list[Any] = []
        if code_prefix:
            domain.append(["code", "=ilike", f"{code_prefix}%"])
        if account_type:
            domain.append(["account_type", "=", account_type])
        ids = self.rpc("account.account", "search", domain, {"limit": limit, "order": "code"})
        if not ids:
            return []
        return self.rpc(
            "account.account",
            "read",
            [ids],
            {"fields": ["code", "name", "account_type"]},
        )

    # --- moves ------------------------------------------------------------
    def get_move_by_id(self, move_id: int) -> dict[str, Any] | None:
        """Read a single account.move by id, with its lines. None if not found."""
        ids = self.rpc("account.move", "search", [["id", "=", move_id]], {"limit": 1})
        if not ids:
            return None
        return self._read_move_with_lines(ids[0])

    def get_move_by_ref(self, ref: str) -> dict[str, Any] | None:
        """Read a single account.move by ref OR name, with its lines.

        Returns the most recent match (by id desc) if multiple. None if none.
        """
        ids = self.rpc(
            "account.move",
            "search",
            ["|", ["ref", "=", ref], ["name", "=", ref]],
            {"limit": 1, "order": "id desc"},
        )
        if not ids:
            return None
        return self._read_move_with_lines(ids[0])

    def _read_move_with_lines(self, move_id: int) -> dict[str, Any]:
        move = self.read_move(move_id)
        line_ids = move.pop("line_ids", []) or []
        lines = self.read_lines(line_ids)
        # Flatten the (id, name) tuple from the many2one field.
        for line in lines:
            if isinstance(line.get("account_id"), list) and len(line["account_id"]) == 2:
                line["account_id"] = {"id": line["account_id"][0], "name": line["account_id"][1]}
        return {
            "id": move_id,
            "name": move.get("name"),
            "ref": move.get("ref"),
            "state": move.get("state"),
            "date": move.get("date"),
            "journal_id": (
                {"id": move["journal_id"][0], "name": move["journal_id"][1]}
                if isinstance(move.get("journal_id"), list) and len(move["journal_id"]) == 2
                else move.get("journal_id")
            ),
            "amount_total": move.get("amount_total"),
            "lines": lines,
        }

    def list_drafts(
        self,
        *,
        ref: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        journal_id: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List draft account.moves (state='draft', move_type='entry').

        All non-None filters combine with AND. Date strings are ISO YYYY-MM-DD.
        When no date filter is given, this function does NOT default to this
        month — callers that want "this month" should pass the dates in
        explicitly (the dispatch wrapper does that).
        """
        domain: list[Any] = [
            ["state", "=", "draft"],
            ["move_type", "=", "entry"],
        ]
        if ref is not None:
            # Odoo OR: prefix with "|" then the two clauses.
            domain.append("|")
            domain.append(["ref", "=", ref])
            domain.append(["name", "=", ref])
        if date_from is not None:
            domain.append(["date", ">=", date_from])
        if date_to is not None:
            domain.append(["date", "<=", date_to])
        if journal_id is not None:
            domain.append(["journal_id", "=", journal_id])
        ids = self.rpc("account.move", "search", domain, {"limit": limit, "order": "date desc, id desc"})
        if not ids:
            return []
        moves = self.rpc(
            "account.move",
            "read",
            [ids],
            {"fields": ["id", "name", "ref", "state", "date", "journal_id", "amount_total"]},
        )
        for m in moves:
            if isinstance(m.get("journal_id"), list) and len(m["journal_id"]) == 2:
                m["journal_id"] = {"id": m["journal_id"][0], "name": m["journal_id"][1]}
        return moves

    def resolve_journal_id(self, code: str, type_: str = "general") -> int | None:
        """Return the journal id for the given code (or type fallback), or None."""
        try:
            j = self.find_journal(code, type_)
            return j.get("id")
        except RuntimeError:
            return None

    # --- generic move / partner / search_read ----------------------------
    def list_moves(
        self,
        *,
        move_type: str,
        partner_id: int | None = None,
        state: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Generic account.move list with the given ``move_type`` filter.

        ``move_type`` must be one of: ``"out_invoice"``, ``"in_invoice"``,
        ``"out_refund"``, ``"in_refund"``, ``"entry"``. The read returns
        id, name, ref, state, date, journal_id, partner_id, amount_total,
        invoice_date, invoice_date_due.
        """
        domain: list[Any] = [["move_type", "=", move_type]]
        if partner_id is not None:
            domain.append(["partner_id", "=", partner_id])
        if state is not None:
            domain.append(["state", "=", state])
        if date_from is not None:
            domain.append(["invoice_date", ">=", date_from])
        if date_to is not None:
            domain.append(["invoice_date", "<=", date_to])
        ids = self.rpc(
            "account.move",
            "search",
            domain,
            {"limit": limit, "order": "invoice_date desc, id desc"},
        )
        if not ids:
            return []
        moves = self.rpc(
            "account.move",
            "read",
            [ids],
            {
                "fields": [
                    "id", "name", "ref", "state", "date", "invoice_date",
                    "invoice_date_due", "journal_id", "partner_id", "amount_total",
                    "amount_residual", "currency_id",
                ],
            },
        )
        for m in moves:
            for k in ("journal_id", "partner_id", "currency_id"):
                v = m.get(k)
                if isinstance(v, list) and len(v) == 2:
                    m[k] = {"id": v[0], "name": v[1]}
        return moves

    def search_partners(
        self,
        *,
        name_contains: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Search res.partner by name (substring, case-insensitive).

        With no ``name_contains`` filter, returns the top-N partners ordered
        by name. ``is_company`` is included so the agent can filter visually.
        """
        domain: list[Any] = []
        if name_contains:
            domain.append(["name", "=ilike", f"%{name_contains}%"])
        ids = self.rpc(
            "res.partner",
            "search",
            domain,
            {"limit": limit, "order": "name"},
        )
        if not ids:
            return []
        return self.rpc(
            "res.partner",
            "read",
            [ids],
            {"fields": ["id", "name", "email", "is_company", "country_id", "vat"]},
        )

    def search_read(
        self,
        *,
        model: str,
        domain: list[Any] | str,
        fields: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Generic escape-hatch read against any Odoo model.

        ``domain`` may be a Python list (preferred) or a JSON string (the
        CLI passes a string for shell-friendliness). ``fields`` defaults to
        ``["id", "display_name"]``. ``limit`` is hard-capped at 1000.

        NOTE: per Aloy's decision (issue #5), this method is **unrestricted**.
        The skill's caller (the agent) is responsible for not reading
        sensitive models unless intentional. Every call is audit-logged by
        the dispatch wrapper in ``read_tools.py``.
        """
        # Hard cap.
        if limit > 1000:
            limit = 1000
        if isinstance(domain, str):
            import json as _json
            domain = _json.loads(domain)
        if not isinstance(domain, list):
            raise ValueError("domain must be a JSON array of clauses")
        if fields is None:
            fields = ["id", "display_name"]
        return self.rpc(
            model,
            "search_read",
            [domain],
            {"fields": fields, "limit": limit},
        )

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
