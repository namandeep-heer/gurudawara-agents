"""Deliver scheduled Punjabi voice messages via WhatsApp (Green API)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from shared.whatsapp_client import phone_to_chat_id, send_whatsapp_file, send_whatsapp_text


def deliver_voice_messages(
    *,
    contacts: list[dict],
    audio_path: Path,
    punjabi_intro: str,
    dry_run: bool = False,
    delay_seconds: int = 30,
    send_text_intro: bool = True,
) -> list[str]:
    id_instance = os.environ.get("WHATSAPP_ID_INSTANCE", "").strip()
    api_token = os.environ.get("WHATSAPP_API_TOKEN", "").strip()
    if not dry_run and (not id_instance or not api_token):
        raise RuntimeError(
            "WhatsApp requires WHATSAPP_ID_INSTANCE and WHATSAPP_API_TOKEN secrets."
        )

    media_url = os.environ.get("WHATSAPP_MEDIA_URL", "https://media.green-api.com").strip()
    api_url = os.environ.get("WHATSAPP_API_URL", "https://api.green-api.com").strip()

    enabled = [contact for contact in contacts if contact.get("enabled", True)]
    results: list[str] = []
    for index, contact in enumerate(enabled):
        name = contact.get("name") or contact.get("phone") or "contact"
        chat_id = phone_to_chat_id(contact["phone"])

        if send_text_intro and punjabi_intro:
            text_id = send_whatsapp_text(
                id_instance=id_instance,
                api_token=api_token,
                chat_id=chat_id,
                message=punjabi_intro,
                api_url=api_url,
                dry_run=dry_run,
            )
            results.append(f"{name}: text {text_id}")

        message_id = send_whatsapp_file(
            id_instance=id_instance,
            api_token=api_token,
            chat_id=chat_id,
            file_path=audio_path,
            media_url=media_url,
            typing_type="recording",
            dry_run=dry_run,
        )
        results.append(f"{name}: voice {message_id}")

        if not dry_run and index < len(enabled) - 1 and delay_seconds > 0:
            time.sleep(delay_seconds)

    return results