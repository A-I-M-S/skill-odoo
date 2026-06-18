"""skill-odoo CLI entrypoint.

Every subcommand prints JSON to stdout. The bootstrap issue (A-I-M-S/skill-odoo#3)
ships a uniform 'not implemented' stub for each; subsequent issues replace
the stub with the real implementation.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Callable

__all__ = ["main", "build_parser", "not_implemented", "emit", "fail"]


LOG = logging.getLogger("skill-odoo")


# ── Output contract helpers ──────────────────────────────────────────────────


def emit(payload: dict[str, Any]) -> int:
    """Print JSON to stdout and return 0."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
    return 0


def fail(message: str, *, code: int = 1, **extra: Any) -> int:
    """Print a structured error JSON to stdout and return ``code``."""
    payload: dict[str, Any] = {"ok": False, "error": message, "code": code}
    payload.update(extra)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
    return code


def not_implemented(subcommand: str) -> int:
    """Bootstrap stub. Every subcommand dispatches here until later issues land.

    Exit code is 0 by design: the agent sees a structured 'not yet' response
    and can route around it. If something is genuinely broken, callers should
    use ``fail()`` instead.
    """
    return emit({
        "ok": False,
        "error": "not implemented",
        "code": 501,
        "subcommand": subcommand,
        "hint": "see A-I-M-S/skill-odoo issues #4+ for the real implementation",
    })


# ── Subcommand stubs (replaced in later issues) ──────────────────────────────


def cmd_probe(_args: argparse.Namespace) -> int:        # issue #5
    return not_implemented("probe")


def cmd_chart_of_accounts(_args: argparse.Namespace) -> int:   # issue #6
    return not_implemented("chart-of-accounts")


def cmd_get_move(_args: argparse.Namespace) -> int:     # issue #6
    return not_implemented("get-move")


def cmd_list_drafts(_args: argparse.Namespace) -> int:   # issue #6
    return not_implemented("list-drafts")


def cmd_list_invoices(_args: argparse.Namespace) -> int:  # issue #7
    return not_implemented("list-invoices")


def cmd_list_bills(_args: argparse.Namespace) -> int:    # issue #7
    return not_implemented("list-bills")


def cmd_list_partners(_args: argparse.Namespace) -> int:  # issue #7
    return not_implemented("list-partners")


def cmd_search_read(_args: argparse.Namespace) -> int:   # issue #7
    return not_implemented("search-read")


def cmd_create_bill(_args: argparse.Namespace) -> int:   # issue #8
    return not_implemented("create-bill")


def cmd_post_move(_args: argparse.Namespace) -> int:     # issue #8
    return not_implemented("post-move")


def cmd_cancel_move(_args: argparse.Namespace) -> int:   # issue #8
    return not_implemented("cancel-move")


def cmd_attach_file(_args: argparse.Namespace) -> int:   # issue #9
    return not_implemented("attach-file")


def cmd_process_receipt(_args: argparse.Namespace) -> int:  # issue #12
    return not_implemented("process-receipt")


def cmd_cache(args: argparse.Namespace) -> int:         # issue #5
    payload = {
        "ok": False,
        "error": "not implemented",
        "code": 501,
        "subcommand": "cache",
        "cache_action": getattr(args, "cache_action", "?"),
        "hint": "see A-I-M-S/skill-odoo issues #4+ for the real implementation",
    }
    return emit(payload)


def cmd_ocr_test(_args: argparse.Namespace) -> int:      # issue #10
    return not_implemented("_ocr-test")


# ── Parser construction ──────────────────────────────────────────────────────


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--env", type=str, default=".env",
                   help="Path to the .env file (default: .env in skill dir)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Verbose logging to stderr")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="odoo",
        description="skill-odoo: Odoo bridge + receipt pipeline (OpenClaw skill)",
    )
    _add_common(parser)
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<subcommand>")

    # probe
    p = sub.add_parser("probe", help="Verify Odoo credentials and return user/company/cache status")
    _add_common(p)
    p.set_defaults(func=cmd_probe)

    # chart-of-accounts
    p = sub.add_parser("chart-of-accounts", help="List accounts (filter by code prefix or type)")
    _add_common(p)
    p.add_argument("--code-prefix", default=None,
                   help="Substring match on account code (e.g. '6' for expenses)")
    p.add_argument("--type", dest="account_type", default=None,
                   help="Exact match on account.account.account_type")
    p.set_defaults(func=cmd_chart_of_accounts)

    # get-move
    p = sub.add_parser("get-move", help="Fetch a single account.move by id or ref")
    _add_common(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", type=int, default=None, help="account.move id")
    g.add_argument("--ref", type=str, default=None, help="account.move ref/name (e.g. '26May')")
    p.set_defaults(func=cmd_get_move)

    # list-drafts
    p = sub.add_parser("list-drafts", help="List draft account.moves (this month, by ref, by date range)")
    _add_common(p)
    p.add_argument("--ref", default=None)
    p.add_argument("--date-from", default=None, help="YYYY-MM-DD")
    p.add_argument("--date-to", default=None, help="YYYY-MM-DD")
    p.add_argument("--journal-code", default=None)
    p.set_defaults(func=cmd_list_drafts)

    # list-invoices
    p = sub.add_parser("list-invoices", help="List customer invoices")
    _add_common(p)
    p.add_argument("--partner-id", type=int, default=None)
    p.add_argument("--state", default=None)
    p.add_argument("--date-from", default=None)
    p.add_argument("--date-to", default=None)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_list_invoices)

    # list-bills
    p = sub.add_parser("list-bills", help="List vendor bills")
    _add_common(p)
    p.add_argument("--partner-id", type=int, default=None)
    p.add_argument("--state", default=None)
    p.add_argument("--date-from", default=None)
    p.add_argument("--date-to", default=None)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_list_bills)

    # list-partners
    p = sub.add_parser("list-partners", help="Search partners by name")
    _add_common(p)
    p.add_argument("--name-contains", default=None, help="Substring match on partner name")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_list_partners)

    # search-read
    p = sub.add_parser("search-read", help="Generic Odoo model query (escape hatch, audit-logged)")
    _add_common(p)
    p.add_argument("--model", required=True, help="e.g. res.partner, account.move.line")
    p.add_argument("--domain", required=True, help="JSON-encoded domain list")
    p.add_argument("--fields", default=None, help="Comma-separated field list")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_search_read)

    # create-bill
    p = sub.add_parser("create-bill", help="Create a vendor bill (one or more lines)")
    _add_common(p)
    p.add_argument("--partner-name", required=True, help="Exact-match partner name")
    p.add_argument("--invoice-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--lines", required=True, help="JSON list of {account_code, name, quantity, price_unit, tax_ids?}")
    p.add_argument("--ref", default=None)
    p.add_argument("--currency", default=None, help="Default: company currency")
    p.set_defaults(func=cmd_create_bill)

    # post-move
    p = sub.add_parser("post-move", help="Confirm (post) a draft account.move")
    _add_common(p)
    p.add_argument("--id", type=int, required=True, help="account.move id")
    p.set_defaults(func=cmd_post_move)

    # cancel-move
    p = sub.add_parser("cancel-move", help="Reset a posted account.move back to draft")
    _add_common(p)
    p.add_argument("--id", type=int, required=True, help="account.move id")
    p.set_defaults(func=cmd_cancel_move)

    # attach-file
    p = sub.add_parser("attach-file", help="Upload an ir.attachment to any Odoo record")
    _add_common(p)
    p.add_argument("--model", required=True)
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--file-path", required=True)
    p.add_argument("--name", default=None, help="Display name (default: basename of file)")
    p.set_defaults(func=cmd_attach_file)

    # process-receipt
    p = sub.add_parser("process-receipt", help="OCR → classify → FX → monthly draft → attach → delete")
    _add_common(p)
    p.add_argument("--file-path", required=True, help="Receipt file (PDF or image)")
    p.add_argument("--text", default=None, help="Pre-extracted text (agent's OCR). Wins over in-skill OCR if set.")
    p.set_defaults(func=cmd_process_receipt)

    # cache (show | refresh | clear)
    p = sub.add_parser("cache", help="Inspect / rebuild the Odoo lookup cache")
    _add_common(p)
    p.add_argument("cache_action", choices=["show", "refresh", "clear"], metavar="ACTION")
    p.set_defaults(func=cmd_cache)

    # internal: _ocr-test (issue #10)
    p = sub.add_parser("_ocr-test", help="Internal: run the OCR router on a file (issue #10)")
    _add_common(p)
    p.add_argument("file_path")
    p.add_argument("--text", default=None)
    p.set_defaults(func=cmd_ocr_test)

    return parser


# ── Main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    func: Callable[[argparse.Namespace], int] = args.func
    try:
        return func(args)
    except KeyboardInterrupt:
        return fail("interrupted", code=130)
    except SystemExit as e:
        # argparse uses SystemExit for usage errors
        return int(e.code) if isinstance(e.code, int) else 2


if __name__ == "__main__":
    raise SystemExit(main())
