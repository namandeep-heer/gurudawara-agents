#!/usr/bin/env python3
"""List WhatsApp group chat IDs from your linked Green API account."""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

import env_loader  # noqa: F401 — load .env files at startup

USER_AGENT = "gur-agent/1.0"


def main() -> int:
    id_instance = os.environ.get("WHATSAPP_ID_INSTANCE", "").strip()
    api_token = os.environ.get("WHATSAPP_API_TOKEN", "").strip()
    api_url = os.environ.get("WHATSAPP_API_URL", "https://api.green-api.com").strip().rstrip("/")

    if not id_instance or not api_token:
        print(
            "Set WHATSAPP_ID_INSTANCE and WHATSAPP_API_TOKEN first.",
            file=sys.stderr,
        )
        return 1

    query = urllib.parse.urlencode({"group": "true"})
    url = f"{api_url}/waInstance{id_instance}/getContacts/{api_token}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        contacts = json.loads(response.read().decode("utf-8"))

    if not contacts:
        print("No groups found.")
        print("1. Sign up at https://console.green-api.com/")
        print("2. Scan the QR code with WhatsApp on your phone")
        print("3. Make sure your account is in the target groups")
        print("4. Run this script again")
        return 1

    print("WhatsApp groups:")
    for contact in contacts:
        if contact.get("type") != "group":
            continue
        chat_id = contact.get("id", "")
        name = contact.get("name") or contact.get("contactName") or "unknown"
        print(f"chat_id={chat_id}  name={name}")

    print("\nCopy chat_id values into whatsapp_groups.json (set enabled: true)")
    print("or add them to the WHATSAPP_GROUP_IDS GitHub secret (comma-separated).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())