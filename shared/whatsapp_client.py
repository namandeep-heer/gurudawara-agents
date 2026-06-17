"""Shared Green API WhatsApp helpers."""

from __future__ import annotations

import json
import mimetypes
import secrets
import urllib.request
from pathlib import Path

USER_AGENT = "gur-agent/1.0"


def phone_to_chat_id(phone: str) -> str:
    """Convert an India mobile number to a WhatsApp personal chat id."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 10:
        digits = f"91{digits}"
    elif digits.startswith("91") and len(digits) == 12:
        pass
    else:
        raise ValueError(f"Expected a 10-digit India mobile or +91 number, got: {phone!r}")
    return f"{digits}@c.us"


def _transfer_timeout_seconds(size_bytes: int, *, minimum: int = 120) -> int:
    return min(1800, max(minimum, size_bytes // (200 * 1024) + 120))


def _http_post_multipart(
    url: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes, str]],
    *,
    timeout: int | None = None,
) -> dict:
    boundary = f"----guragent{secrets.token_hex(16)}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    for name, (filename, content, content_type) in files.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n'
            ).encode()
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    if timeout is None:
        timeout = _transfer_timeout_seconds(
            max(len(content) for _, (_, content, _) in files.items()),
        )

    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def send_whatsapp_file(
    *,
    id_instance: str,
    api_token: str,
    chat_id: str,
    file_path: Path,
    caption: str = "",
    media_url: str = "https://media.green-api.com",
    typing_type: str = "",
    dry_run: bool = False,
) -> str:
    if dry_run:
        return f"dry-run message to {chat_id}"

    file_bytes = file_path.read_bytes()
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    url = (
        f"{media_url.rstrip('/')}/waInstance{id_instance}/"
        f"sendFileByUpload/{api_token}"
    )
    fields = {"chatId": chat_id, "fileName": file_path.name}
    if caption:
        fields["caption"] = caption
    if typing_type:
        fields["typingType"] = typing_type

    result = _http_post_multipart(
        url,
        fields,
        {"file": (file_path.name, file_bytes, content_type)},
        timeout=_transfer_timeout_seconds(len(file_bytes)),
    )
    if not result.get("idMessage"):
        raise RuntimeError(f"WhatsApp error for {chat_id}: {result}")
    return str(result["idMessage"])


def send_whatsapp_text(
    *,
    id_instance: str,
    api_token: str,
    chat_id: str,
    message: str,
    api_url: str = "https://api.green-api.com",
    dry_run: bool = False,
) -> str:
    if dry_run:
        return f"dry-run text to {chat_id}"

    url = (
        f"{api_url.rstrip('/')}/waInstance{id_instance}/"
        f"sendMessage/{api_token}"
    )
    payload = json.dumps({"chatId": chat_id, "message": message}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("idMessage"):
        raise RuntimeError(f"WhatsApp text error for {chat_id}: {result}")
    return str(result["idMessage"])