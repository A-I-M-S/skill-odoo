"""Telegram long-polling bot for receipt ingestion."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from .config import Settings, load_env_file
from .processor import process_inbox

LOG = logging.getLogger("skill-odoo.telegram")

SUPPORTED_DOC_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".txt"}


def _csv_set(value: str) -> set[str]:
    return {x.strip() for x in value.split(",") if x.strip()}


class TelegramReceiptBot:
    def __init__(self, *, env_path: Path = Path(".env")) -> None:
        load_env_file(env_path)
        self.settings = Settings.from_env()
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")
        self.allowed_ids = _csv_set(os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""))
        self.allowed_usernames = {u.lstrip("@").lower() for u in _csv_set(os.getenv("TELEGRAM_ALLOWED_USERNAMES", ""))}
        self.api = f"https://api.telegram.org/bot{self.token}"
        self.offset_path = Path(os.getenv("TELEGRAM_OFFSET_FILE", ".telegram_offset"))
        self.timeout = int(os.getenv("TELEGRAM_POLL_TIMEOUT", "50"))

    def run_forever(self) -> None:
        LOG.info("Telegram receipt bot started")
        self.settings.receipts_inbox.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                for update in self._get_updates():
                    self._handle_update(update)
                    self._save_offset(update["update_id"] + 1)
            except KeyboardInterrupt:
                raise
            except Exception:
                LOG.exception("bot loop error")
                time.sleep(5)

    def _request(self, method: str, **kwargs: Any) -> dict[str, Any]:
        resp = requests.post(f"{self.api}/{method}", timeout=max(self.timeout + 10, 30), **kwargs)
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"Telegram {method} returned non-JSON HTTP {resp.status_code}") from exc
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data}")
        return data

    def _get_updates(self) -> list[dict[str, Any]]:
        payload = {"timeout": self.timeout, "allowed_updates": json.dumps(["message"])}
        offset = self._load_offset()
        if offset is not None:
            payload["offset"] = offset
        return self._request("getUpdates", data=payload).get("result", [])

    def _load_offset(self) -> int | None:
        try:
            text = self.offset_path.read_text().strip()
            return int(text) if text else None
        except FileNotFoundError:
            return None

    def _save_offset(self, offset: int) -> None:
        self.offset_path.write_text(str(offset))

    def _handle_update(self, update: dict[str, Any]) -> None:
        msg = update.get("message") or {}
        chat_id = msg.get("chat", {}).get("id")
        if chat_id is None:
            return
        user = msg.get("from") or {}
        user_id = str(user.get("id", ""))
        username = str(user.get("username", "")).lower()

        if not self._allowed(user_id, username):
            self._send_message(
                chat_id,
                "Not authorised yet. Your Telegram user ID is "
                f"`{user_id}` and username is `@{username or 'unknown'}`. "
                "Add it to TELEGRAM_ALLOWED_USER_IDS or TELEGRAM_ALLOWED_USERNAMES in .env.",
            )
            return

        text = (msg.get("text") or "").strip()
        if text.startswith("/start") or text.startswith("/help"):
            self._send_message(chat_id, self._help_text(user_id, username))
            return
        if text.startswith("/status"):
            self._send_message(chat_id, self._status_text())
            return
        if text.startswith("/process"):
            self._process_and_reply(chat_id, trigger="manual /process")
            return

        file_info = self._extract_file_info(msg)
        if not file_info:
            self._send_message(chat_id, "Send me a receipt photo, image file, or PDF. I'll process it into the monthly Odoo draft.")
            return

        self._send_message(chat_id, "Received. Uploading to the receipt inbox and processing now…")
        path = self._download_to_inbox(file_info)
        LOG.info("saved receipt %s", path)
        self._process_and_reply(chat_id, trigger=path.name)

    def _allowed(self, user_id: str, username: str) -> bool:
        if not self.allowed_ids and not self.allowed_usernames:
            return False
        if user_id and user_id in self.allowed_ids:
            return True
        return bool(username and username in self.allowed_usernames)

    def _send_message(self, chat_id: int | str, text: str) -> None:
        self._request("sendMessage", data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

    def _extract_file_info(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        if msg.get("photo"):
            photo = msg["photo"][-1]
            return {"file_id": photo["file_id"], "filename": f"telegram_photo_{photo['file_unique_id']}.jpg"}
        doc = msg.get("document")
        if doc:
            name = doc.get("file_name") or f"telegram_document_{doc['file_unique_id']}"
            ext = Path(name).suffix.lower()
            mime = (doc.get("mime_type") or "").lower()
            if ext in SUPPORTED_DOC_EXTS or mime in {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/tiff"}:
                return {"file_id": doc["file_id"], "filename": name}
        return None

    def _download_to_inbox(self, file_info: dict[str, Any]) -> Path:
        data = self._request("getFile", data={"file_id": file_info["file_id"]})
        file_path = data["result"]["file_path"]
        dl = requests.get(f"https://api.telegram.org/file/bot{self.token}/{file_path}", timeout=120)
        dl.raise_for_status()
        safe_name = _safe_filename(file_info["filename"])
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.settings.receipts_inbox / f"{stamp}-{safe_name}"
        target.write_bytes(dl.content)
        return target

    def _process_and_reply(self, chat_id: int | str, *, trigger: str) -> None:
        try:
            summary = process_inbox(self.settings, dry_run=False)
        except Exception as exc:
            LOG.exception("processing failed")
            self._send_message(chat_id, f"❌ Processing failed for `{trigger}`:\n`{exc}`")
            return

        results = summary.get("results", [])
        processed = [r for r in results if r.get("ok")]
        failed = [r for r in results if not r.get("ok")]
        totals = summary.get("totals", {})
        ref = summary.get("ref", "current month")
        lines = [f"✅ Processed into Odoo draft `{ref}`."]
        if totals:
            lines.append(f"Debits: SGD {float(totals.get('total_debit', 0) or 0):.2f}")
            lines.append(f"Credit: SGD {float(totals.get('credit', 0) or 0):.2f} → `{self.settings.shareholder_account_code}`")
        for item in processed[-5:]:
            ex = item.get("extraction", {})
            acc = ex.get("debit_account_code", "?")
            lines.append(
                f"• {ex.get('vendor', 'Unknown')} — {ex.get('currency', '')} {float(ex.get('amount', 0) or 0):.2f} "
                f"→ `{acc}`; attachment `{item.get('attachment_id')}`"
            )
        for item in failed[-3:]:
            lines.append(f"⚠️ `{item.get('file', 'receipt')}` failed: {item.get('error', 'unknown error')}")
            if item.get("failed_path"):
                lines.append(f"Kept for review: `{item['failed_path']}`")
        if not processed and not failed:
            lines.append("No supported files were waiting in the inbox.")
        self._send_message(chat_id, "\n".join(lines))

    def _help_text(self, user_id: str, username: str) -> str:
        return (
            "Send a receipt photo, image file, or PDF and I'll add it to the current monthly Odoo draft.\n\n"
            "Commands:\n"
            "• /process — process anything already in the inbox\n"
            "• /status — show inbox/status\n\n"
            f"Your user ID: `{user_id}`\nUsername: `@{username or 'unknown'}`"
        )

    def _status_text(self) -> str:
        files = sorted(p for p in self.settings.receipts_inbox.glob("*") if p.is_file())
        return (
            f"Inbox: `{self.settings.receipts_inbox}`\n"
            f"Waiting files: {len(files)}\n"
            f"Odoo draft ref format: `{self.settings.move_ref_format}`\n"
            f"Shareholder account: `{self.settings.shareholder_account_code}`"
        )


def _safe_filename(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or "receipt"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    TelegramReceiptBot().run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
