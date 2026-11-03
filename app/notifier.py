"""Notifiche: ntfy, Telegram, webhook generico."""
from __future__ import annotations

import logging

import requests

from .config import NotifyCfg

log = logging.getLogger("fleetbuddy.notify")


def send(cfg: NotifyCfg, event: str, title: str, message: str, printer: str = "", image: bytes | None = None):
    if cfg.ntfy_url:
        try:
            headers = {"Title": title.encode("utf-8"), "Tags": {"finished": "white_check_mark",
                       "failed": "x", "error": "warning"}.get(event, "printer")}
            if image:
                headers["Filename"] = "snapshot.jpg"
                headers["Message"] = message.encode("utf-8")
                requests.put(cfg.ntfy_url, data=image, headers=headers, timeout=15)
            else:
                requests.post(cfg.ntfy_url, data=message.encode("utf-8"), headers=headers, timeout=15)
        except requests.RequestException as e:
            log.warning("ntfy: %s", e)
    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        base = f"https://api.telegram.org/bot{cfg.telegram_bot_token}"
        text = f"*{title}*\n{message}"
        try:
            if image:
                requests.post(f"{base}/sendPhoto", data={"chat_id": cfg.telegram_chat_id, "caption": text,
                              "parse_mode": "Markdown"}, files={"photo": ("s.jpg", image)}, timeout=20)
            else:
                requests.post(f"{base}/sendMessage", json={"chat_id": cfg.telegram_chat_id, "text": text,
                              "parse_mode": "Markdown"}, timeout=15)
        except requests.RequestException as e:
            log.warning("telegram: %s", e)
    if cfg.webhook_url:
        try:
            requests.post(cfg.webhook_url, json={"event": event, "title": title, "message": message,
                                                 "printer": printer}, timeout=15)
        except requests.RequestException as e:
            log.warning("webhook: %s", e)
