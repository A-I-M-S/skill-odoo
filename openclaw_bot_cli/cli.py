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
    parser = argparse.ArgumentParser(prog="openclaw_bot_cli", description="OpenClaw skill-odoo")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="Authenticate + describe target Odoo (read-only)")
    _add_common(p_probe)

    p_run = sub.add_parser("run", help="Process all receipts in the inbox folder")
    _add_common(p_run)
    p_run.add_argument("--dry-run", action="store_true", help="Don't write anything to Odoo")
    p_run.add_argument("--output", type=Path, default=None, help="Write JSON result to this file")

    p_bot = sub.add_parser("telegram-bot", help="Run Telegram receipt ingestion bot")
    _add_common(p_bot)

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
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
        result = process_inbox(settings, dry_run=args.dry_run)
        out = json.dumps(result, indent=2, default=str)
        print(out)
        if args.output:
            args.output.write_text(out, encoding="utf-8")
        return 0

    if args.cmd == "telegram-bot":
        from .telegram_bot import TelegramReceiptBot

        TelegramReceiptBot(env_path=args.env).run_forever()
        return 0

    parser.error(f"Unknown command {args.cmd!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
