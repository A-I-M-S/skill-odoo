"""Smoke test for the skill-odoo bootstrap.

Verifies that:
- bin/odoo --help exits 0 and lists all 14 subcommands
- bin/odoo <subcommand> exits 0 with a structured "not implemented" JSON
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
BIN_ODOO = SKILL_ROOT / "bin" / "odoo"

EXPECTED_SUBCOMMANDS = {
    "probe",
    "chart-of-accounts",
    "get-move",
    "list-drafts",
    "list-invoices",
    "list-bills",
    "list-partners",
    "search-read",
    "create-bill",
    "post-move",
    "cancel-move",
    "attach-file",
    "process-receipt",
    "cache",
    "_ocr-test",
}


def _run(args: list[str], *, expect_exit: int | None = None) -> subprocess.CompletedProcess:
    """Run bin/odoo from the skill root."""
    env = os.environ.copy()
    # Make sure no real Odoo env is picked up during smoke tests.
    env.pop("ODOO_URL", None)
    env.pop("ODOO_DB", None)
    env.pop("ODOO_LOGIN", None)
    env.pop("ODOO_API_KEY", None)
    proc = subprocess.run(
        [str(BIN_ODOO), *args],
        cwd=str(SKILL_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    if expect_exit is not None:
        assert proc.returncode == expect_exit, (
            f"bin/odoo {' '.join(args)} exited {proc.returncode}, expected {expect_exit}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc


def test_bin_odoo_exists_and_is_executable() -> None:
    assert BIN_ODOO.exists(), f"missing {BIN_ODOO}"
    assert os.access(BIN_ODOO, os.X_OK), f"{BIN_ODOO} is not executable"


def test_help_lists_every_subcommand() -> None:
    proc = _run(["--help"], expect_exit=0)
    help_text = proc.stdout
    missing = sorted(EXPECTED_SUBCOMMANDS - set(help_text.split()))
    assert not missing, f"--help missing subcommands: {missing}"


def test_probe_returns_not_implemented_json() -> None:
    proc = _run(["probe"], expect_exit=0)
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": False,
        "error": "not implemented",
        "code": 501,
        "subcommand": "probe",
        "hint": "see A-I-M-S/skill-odoo issues #4+ for the real implementation",
    }


@pytest.mark.parametrize("subcommand,args", [
    ("chart-of-accounts", []),
    ("get-move", ["--id", "1"]),
    ("list-drafts", []),
    ("list-invoices", []),
    ("list-bills", []),
    ("list-partners", []),
    ("search-read", ["--model", "res.partner", "--domain", "[]"]),
    ("create-bill", [
        "--partner-name", "x",
        "--invoice-date", "2026-06-15",
        "--lines", "[]",
    ]),
    ("post-move", ["--id", "1"]),
    ("cancel-move", ["--id", "1"]),
    ("attach-file", ["--model", "account.move", "--id", "1", "--file-path", "/tmp/none"]),
    ("process-receipt", ["--file-path", "/tmp/none"]),
    ("cache", ["show"]),
])
def test_every_public_subcommand_returns_not_implemented(subcommand: str, args: list[str]) -> None:
    proc = _run([subcommand, *args], expect_exit=0)
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["code"] == 501
    assert payload["error"] == "not implemented"
    assert payload["subcommand"] == subcommand


def test_bad_args_exit_2() -> None:
    """argparse usage errors should exit 2 (SystemExit), not 0."""
    proc = _run(["get-move"], expect_exit=2)
    # argparse writes the usage error to stderr
    assert "must provide" in proc.stderr.lower() or "required" in proc.stderr.lower()
