"""Legacy module retained as a thin shim.

The previous monolithic ``accounting`` module has been split into
``odoo_client``, ``monthly_journal``, ``fx`` and ``processor``.  This shim
exists only so external imports of ``classify_accounting`` / ``post_to_odoo``
fail loudly with guidance.
"""
from __future__ import annotations


def __getattr__(name: str):
    raise ImportError(
        f"openclaw_bot_cli.accounting.{name} was removed. "
        "Use openclaw_bot_cli.processor.process_inbox instead."
    )
