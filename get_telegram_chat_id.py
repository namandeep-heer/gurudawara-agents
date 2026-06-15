#!/usr/bin/env python3
"""Print your Telegram chat_id after you message your bot."""

from __future__ import annotations

import json
import os
import sys
import urllib.request

USER_AGENT = "gur-agent/1.0"


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN first.", file=sys.stderr)
        return 1

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))

    if not data.get("ok"):
        print(f"Telegram API error: {data}", file=sys.stderr)
        return 1

    updates = data.get("result", [])
    if not updates:
        print("No messages found.")
        print("1. Open Telegram and search for your bot")
        print("2. Tap Start and send any message (e.g. Hi)")
        print("3. Run this script again")
        return 1

    seen: set[str] = set()
    for update in reversed(updates):
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        chat_id_str = str(chat_id)
        if chat_id_str in seen:
            continue
        seen.add(chat_id_str)
        name = chat.get("first_name") or chat.get("title") or "unknown"
        print(f"chat_id={chat_id_str}  name={name}")

    print("\nAdd TELEGRAM_CHAT_ID to your GitHub secrets using the chat_id above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())