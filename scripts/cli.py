"""Command-line entrypoint for skill-odoo."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Settings, load_env_file
from .odoo_client import Odoo
from .processor import process_inbox


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--env", type=Path, default=Path(".env"), help="Path to .env file")
    p.add_argument("--verbose", "-v", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skill-odoo", description="skill-odoo receipt → Odoo pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="Authenticate + describe target Odoo (read-only)")
    _add_common(p_probe)

    p_run = sub.add_parser("run", help="Process all receipts in the inbox folder")
    _add_common(p_run)
    p_run.add_argument("--dry-run", action="store_true", help="Don't write anything to Odoo")
    p_run.add_argument(
        "--refresh-cache", "--refresh-coa",
        dest="refresh_cache", action="store_true",
        help="Force-refresh all cached Odoo lookups (user, currency, journal, account, COA)",
    )
    p_run.add_argument("--output", type=Path, default=None, help="Write JSON result to this file")

    p_coa = sub.add_parser("coa", help="Inspect / refresh the cached Chart of Accounts")
    _add_common(p_coa)
    p_coa.add_argument("action", choices=["show", "refresh"], help="show cached COA, or force-refresh from Odoo")
    p_coa.add_argument("--full", action="store_true", help="Show all COA entries instead of the AI shortlist")

    p_cache = sub.add_parser("cache", help="Inspect / refresh / clear the Odoo lookup cache")
    _add_common(p_cache)
    p_cache.add_argument("action", choices=["show", "refresh", "clear"],
                          help="show entries, refresh all, or delete cache file")

    p_bot = sub.add_parser("telegram-bot", help="Run Telegram receipt ingestion bot")
    _add_common(p_bot)

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    load_env_file(args.env)
    settings = Settings.from_env()

    if args.cmd == "probe":
        odoo = Odoo(url=settings.odoo_url, db=settings.odoo_db, login=settings.odoo_login, api_key=settings.odoo_api_key)
        user = odoo.user_info()
        journal = odoo.find_journal(settings.journal_code, settings.journal_type)
        sh = odoo.find_account(settings.shareholder_account_code)
        result = {
            "uid": odoo.uid,
            "user": {"name": user["name"], "login": user["login"], "tz": user.get("tz")},
            "company": user["company_id"],
            "journal": journal,
            "shareholder_account": sh,
        }
        print(json.dumps(result, indent=2, default=str))
        return 0

    if args.cmd == "run":
        result = process_inbox(settings, dry_run=args.dry_run, force_refresh_coa=args.refresh_cache)
        out = json.dumps(result, indent=2, default=str)
        print(out)
        if args.output:
            args.output.write_text(out, encoding="utf-8")
        return 0

    if args.cmd == "coa":
        from .ai_automation import shortlist_coa
        from .odoo_cache import get_chart_of_accounts

        odoo = Odoo(url=settings.odoo_url, db=settings.odoo_db, login=settings.odoo_login, api_key=settings.odoo_api_key)
        items, source = get_chart_of_accounts(odoo, settings, force_refresh=(args.action == "refresh"))
        rows = items if args.full else shortlist_coa(items)
        print(json.dumps({
            "source": source,
            "full_count": len(items),
            "short_count": len(shortlist_coa(items)),
            "shown": "full" if args.full else "short",
            "items": [{"code": a.get("code"), "name": a.get("name"), "account_type": a.get("account_type")} for a in rows],
        }, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "cache":
        from .odoo_cache import clear_cache, read_cache
        from .ai_automation import shortlist_coa
        from .odoo_cache import (
            get_account, get_chart_of_accounts, get_company_currency,
            get_journal, get_user_info,
        )

        if args.action == "clear":
            clear_cache(settings)
            print(json.dumps({"cleared": True}))
            return 0

        if args.action == "refresh":
            odoo = Odoo(url=settings.odoo_url, db=settings.odoo_db, login=settings.odoo_login, api_key=settings.odoo_api_key)
            user, _ = get_user_info(odoo, settings, force_refresh=True)
            co_id = user["company_id_int"]
            get_company_currency(odoo, co_id, settings, force_refresh=True)
            get_journal(odoo, settings.journal_code, settings.journal_type, settings, force_refresh=True)
            get_account(odoo, settings.shareholder_account_code, settings, force_refresh=True)
            get_chart_of_accounts(odoo, settings, force_refresh=True)

        raw = read_cache(settings)
        summary = {
            "meta": raw.get("meta", {}),
            "entries": {
                k: {
                    "fetched_at": v.get("fetched_at"),
                    "size": (len(v["value"]) if isinstance(v.get("value"), list) else 1),
                }
                for k, v in raw.get("entries", {}).items()
            },
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "telegram-bot":
        from .telegram_bot import TelegramReceiptBot

        TelegramReceiptBot(env_path=args.env).run_forever()
        return 0

    parser.error(f"Unknown command {args.cmd!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
