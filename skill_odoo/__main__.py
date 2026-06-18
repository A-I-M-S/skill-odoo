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
from pathlib import Path
from typing import Any, Callable

from skill_odoo.config import Settings

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


def _load_settings_or_fail() -> Settings | int:
    """Load Settings.from_env(); on missing env, return an exit code instead.

    Caller pattern: ``s = _load_settings_or_fail(); if isinstance(s, int): return s``
    """
    try:
        return Settings.from_env()
    except RuntimeError as exc:
        return fail(str(exc), code=5, error_kind="missing_env")


def cmd_probe(args: argparse.Namespace) -> int:          # issue #5
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.probe import run_probe
    try:
        payload = run_probe(settings, force_refresh=args.refresh_cache)
    except RuntimeError as exc:
        return fail(str(exc), code=3, error_kind="odoo_error")
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_chart_of_accounts(args: argparse.Namespace) -> int:   # issue #6
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.read_tools import run_chart_of_accounts
    try:
        payload = run_chart_of_accounts(
            settings,
            code_prefix=args.code_prefix,
            account_type=args.account_type,
            limit=args.limit,
        )
    except RuntimeError as exc:
        return fail(str(exc), code=3, error_kind="odoo_error")
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_get_move(args: argparse.Namespace) -> int:     # issue #6
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.read_tools import run_get_move
    try:
        payload = run_get_move(settings, move_id=args.id, ref=args.ref)
    except ValueError as exc:
        return fail(str(exc), code=2, error_kind="bad_args")
    except RuntimeError as exc:
        return fail(str(exc), code=3, error_kind="odoo_error")
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_list_drafts(args: argparse.Namespace) -> int:   # issue #6
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.read_tools import run_list_drafts
    try:
        payload = run_list_drafts(
            settings,
            ref=args.ref,
            date_from=args.date_from,
            date_to=args.date_to,
            journal_code=args.journal_code,
            limit=args.limit,
        )
    except RuntimeError as exc:
        return fail(str(exc), code=3, error_kind="odoo_error")
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_list_invoices(args: argparse.Namespace) -> int:  # issue #7
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.read_tools import run_list_invoices
    try:
        payload = run_list_invoices(
            settings,
            partner_id=args.partner_id,
            state=args.state,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=args.limit,
        )
    except RuntimeError as exc:
        return fail(str(exc), code=3, error_kind="odoo_error")
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_list_bills(args: argparse.Namespace) -> int:    # issue #7
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.read_tools import run_list_bills
    try:
        payload = run_list_bills(
            settings,
            partner_id=args.partner_id,
            state=args.state,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=args.limit,
        )
    except RuntimeError as exc:
        return fail(str(exc), code=3, error_kind="odoo_error")
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_list_partners(args: argparse.Namespace) -> int:  # issue #7
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.read_tools import run_list_partners
    try:
        payload = run_list_partners(
            settings,
            name_contains=args.name_contains,
            limit=args.limit,
        )
    except RuntimeError as exc:
        return fail(str(exc), code=3, error_kind="odoo_error")
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_search_read(args: argparse.Namespace) -> int:   # issue #7
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.read_tools import run_search_read
    try:
        payload = run_search_read(
            settings,
            model=args.model,
            domain=args.domain,
            fields=args.fields,
            limit=args.limit,
        )
    except RuntimeError as exc:
        return fail(str(exc), code=3, error_kind="odoo_error")
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_create_bill(args: argparse.Namespace) -> int:   # issue #8
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.write_tools import run_create_bill
    try:
        payload = run_create_bill(
            settings,
            partner_name=args.partner_name,
            invoice_date=args.invoice_date,
            lines=args.lines,
            ref=args.ref,
            currency=args.currency,
        )
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_post_move(args: argparse.Namespace) -> int:     # issue #8
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.write_tools import run_post_move
    try:
        payload = run_post_move(settings, move_id=args.id)
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_cancel_move(args: argparse.Namespace) -> int:   # issue #8
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.write_tools import run_cancel_move
    try:
        payload = run_cancel_move(settings, move_id=args.id)
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_attach_file(args: argparse.Namespace) -> int:   # issue #9
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.attach import run_attach_file
    try:
        payload = run_attach_file(
            settings,
            model=args.model,
            res_id=args.id,
            file_path=args.file_path,
            name=args.name,
        )
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_process_receipt(args: argparse.Namespace) -> int:  # issue #12
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.receipt import process_receipt
    from pathlib import Path
    try:
        payload = process_receipt(
            settings,
            file_path=Path(args.file_path),
            provided_text=args.text,
        )
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    # process-receipt returns a structured failure dict rather than raising,
    # so the exit code reflects `ok`. 0 on success, 3 on receipt-level failure
    # (Odoo / FX / classify error), 4 on missing file / unsupported type.
    if payload.get("ok"):
        return emit(payload)
    err = payload.get("error") or "receipt processing failed"
    code = 3
    if any(s in err.lower() for s in ("file not found", "unsupported file type")):
        code = 4
    return fail(err, code=code, error_kind="receipt_failed", **{k: v for k, v in payload.items() if k not in ("ok", "error")})


def cmd_cache(args: argparse.Namespace) -> int:          # issue #5
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.probe import run_cache_clear, run_cache_refresh, run_cache_show
    action = args.cache_action
    try:
        if action == "show":
            payload = run_cache_show(settings)
        elif action == "refresh":
            payload = run_cache_refresh(settings)
        elif action == "clear":
            payload = run_cache_clear(settings)
        else:
            return fail(f"unknown cache action: {action!r}", code=2)
    except RuntimeError as exc:
        return fail(str(exc), code=3, error_kind="odoo_error")
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


def cmd_ocr_test(args: argparse.Namespace) -> int:      # issue #10
    settings = _load_settings_or_fail()
    if isinstance(settings, int):
        return settings
    from skill_odoo.ocr_router import extract_receipt_text
    try:
        from pathlib import Path
        payload = extract_receipt_text(
            settings,
            Path(args.file_path),
            provided_text=args.text,
        )
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", code=1, error_kind="unexpected")
    return emit(payload)


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
    p.add_argument("--refresh-cache", action="store_true",
                   help="Force-refresh the Odoo lookup cache before returning")
    p.set_defaults(func=cmd_probe)

    # chart-of-accounts
    p = sub.add_parser("chart-of-accounts", help="List accounts (filter by code prefix or type)")
    _add_common(p)
    p.add_argument("--code-prefix", default=None,
                   help="Substring match on account code (e.g. '6' for expenses)")
    p.add_argument("--type", dest="account_type", default=None,
                   help="Exact match on account.account.account_type")
    p.add_argument("--limit", type=int, default=500, help="Max accounts to return (default 500)")
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
    p.add_argument("--date-from", default=None, help="YYYY-MM-DD (defaults to 1st of this month)")
    p.add_argument("--date-to", default=None, help="YYYY-MM-DD (defaults to last day of this month)")
    p.add_argument("--journal-code", default=None)
    p.add_argument("--limit", type=int, default=100)
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

    # Load .env from the path supplied via --env (default: .env in skill dir).
    # This is a no-op if the file is missing. Variables already in os.environ
    # are preserved (the loader only fills in missing keys), so a parent
    # process (e.g. bin/odoo) that pre-sourced the same file still wins.
    env_path = Path(args.env) if args.env else None
    if env_path and env_path.exists():
        from skill_odoo.config import load_env_file  # local import to keep cold start cheap
        load_env_file(env_path)

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
