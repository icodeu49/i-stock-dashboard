"""Telegram delivery with bounded retries and persisted per-message deduplication."""
import os
import time
from datetime import datetime, timezone

import requests

from storage import ALERT_STATE_FILE, read_json, write_json


def require_telegram_config():
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    return token, chat


def send_telegram(message, attempts=3):
    token, chat = require_telegram_config()
    for attempt in range(attempts):
        retry_delay = 2 ** attempt
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": message, "disable_web_page_preview": True},
                timeout=20,
            )
            data = response.json()
            if response.status_code == 200 and data.get("ok"):
                return
            code = response.status_code
            if code == 429:
                retry_delay = min(int(data.get("parameters", {}).get("retry_after", retry_delay)), 60)
            elif code < 500:
                raise RuntimeError(f"Telegram rejected the message (HTTP {code})")
        except (requests.RequestException, ValueError):
            # Network errors can include the bot token in their URL; do not print them.
            pass
        if attempt + 1 < attempts:
            time.sleep(retry_delay)
    raise RuntimeError("Telegram delivery failed after retries")


def send_records(records, state_path=ALERT_STATE_FILE):
    """Each record is (stable_id, text). Persist IDs only after confirmed delivery."""
    state = read_json(state_path, {"delivered": {}})
    delivered = state.setdefault("delivered", {})
    pending = [(key, text) for key, text in records if key not in delivered]
    batches, batch, units = [], [], 0
    for key, text in pending:
        size = len(text.encode("utf-16-le")) // 2 + 2
        if size > 3500:
            raise ValueError("Alert record exceeds Telegram message budget")
        if batch and units + size > 3500:
            batches.append(batch)
            batch, units = [], 0
        batch.append((key, text))
        units += size
    if batch:
        batches.append(batch)
    sent = 0
    for batch in batches:
        send_telegram("\n\n".join(text for _, text in batch))
        stamp = datetime.now(timezone.utc).isoformat()
        for key, _ in batch:
            delivered[key] = stamp
        # Bounded state retains far more than a year of normal signal traffic.
        state["delivered"] = dict(sorted(delivered.items(), key=lambda item: item[1])[-10000:])
        delivered = state["delivered"]
        write_json(state_path, state)
        sent += len(batch)
    print(f"Telegram: delivered {sent} new records; skipped {len(records) - len(pending)} duplicates.")
    return sent
