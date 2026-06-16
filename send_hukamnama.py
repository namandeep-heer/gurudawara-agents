#!/usr/bin/env python3
"""Fetch daily Hukamnama from GurbaniNow and deliver via Telegram, WhatsApp, email, or ntfy."""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import smtplib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import env_loader  # noqa: F401 — load .env files at startup
from hukamnama_image import render_hukamnama_image

HUKAMNAMA_API = "https://api.gurbaninow.com/v2/hukamnama/today"
MAX_MESSAGE_LENGTH = 4000
USER_AGENT = "gur-agent/1.0"


def fetch_hukamnama(retries: int = 3, delay_seconds: int = 30) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                HUKAMNAMA_API,
                headers={"User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
            if data.get("error"):
                raise RuntimeError(f"Hukamnama API error: {data['error']}")
            if not data.get("hukamnama"):
                raise RuntimeError("Hukamnama API returned no verses")
            return data
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay_seconds)
    raise RuntimeError(f"Failed to fetch hukamnama after {retries} attempts: {last_error}")


def _line_text(line: dict, field: str, subfield: str = "default") -> str:
    try:
        value = line["line"]["translation"][field][subfield]
        if isinstance(value, dict):
            return value.get("unicode", "").strip()
        return str(value).strip()
    except (KeyError, TypeError):
        return ""


def format_message(
    data: dict,
    *,
    include_punjabi: bool = True,
    include_english: bool = False,
) -> str:
    gregorian = data["date"]["gregorian"]
    nanakshahi = data["date"]["nanakshahi"]["punjabi"]
    info = data["hukamnamainfo"]

    header = [
        "ਅੱਜ ਦਾ ਹੁਕਮਨਾਮਾ",
        "ਸ੍ਰੀ ਦਰਬਾਰ ਸਾਹਿਬ, ਅਮ੍ਰਿਤਸਰ",
        "",
        f"ਨਾਨਕਸ਼ਾਹੀ: {nanakshahi['month']} {nanakshahi['date']}, {nanakshahi['year']} ({nanakshahi['day']})",
        f"ਤਾਰੀਖ: {gregorian['date']} {gregorian['month']} {gregorian['year']}, {gregorian['day']}",
        "",
        f"ਰਾਗ: {info['raag']['unicode']}",
        f"ਰਚਨਾਕਾਰ: {info['writer']['unicode']}",
        f"ਅੰਗ: {info['pageno']}",
        "",
        "---",
    ]

    body: list[str] = []
    for entry in data["hukamnama"]:
        gurmukhi = entry["line"]["gurmukhi"]["unicode"].strip()
        if not gurmukhi:
            continue
        body.append(gurmukhi)
        if include_punjabi:
            punjabi = _line_text(entry, "punjabi")
            if punjabi:
                body.append(punjabi)
        if include_english:
            english = _line_text(entry, "english")
            if english:
                body.append(english)
        body.append("")

    footer = [
        "---",
        "ਸਰੋਤ: GurbaniNow API",
        "ਵਾਹਿਗੁਰੂ ਜੀ ਕਾ ਖਾਲਸਾ, ਵਾਹਿਗੁਰੂ ਜੀ ਕੀ ਫਤਹਿ",
    ]

    return "\n".join(header + body + footer).strip()


def split_message(message: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current = ""
    for paragraph in message.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= limit:
            current = paragraph
            continue
        for line in paragraph.splitlines():
            line_candidate = line if not current else f"{current}\n{line}"
            if len(line_candidate) <= limit:
                current = line_candidate
            else:
                if current:
                    chunks.append(current)
                current = line
    if current:
        chunks.append(current)
    return chunks


def _http_post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_post_multipart(
    url: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes, str]],
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

    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes"}


def _send_format(method: str) -> str:
    configured = os.environ.get("SEND_FORMAT", "auto").strip().lower()
    if configured in {"image", "text"}:
        return configured
    if method == "whatsapp":
        return "image"
    return "text"


def load_whatsapp_group_ids() -> list[str]:
    env_ids = os.environ.get("WHATSAPP_GROUP_IDS", "").strip()
    if env_ids:
        return [item.strip() for item in env_ids.split(",") if item.strip()]

    groups_file = Path(
        os.environ.get("WHATSAPP_GROUPS_FILE", "whatsapp_groups.json")
    )
    if not groups_file.exists():
        raise RuntimeError(
            "WhatsApp requires WHATSAPP_GROUP_IDS or an enabled whatsapp_groups.json file."
        )

    payload = json.loads(groups_file.read_text(encoding="utf-8"))
    chat_ids = [
        group["chat_id"].strip()
        for group in payload.get("groups", [])
        if group.get("enabled") and group.get("chat_id", "").strip()
    ]
    if not chat_ids:
        raise RuntimeError(
            "No enabled WhatsApp groups found. Set enabled: true and chat_id in "
            "whatsapp_groups.json or provide WHATSAPP_GROUP_IDS."
        )
    return chat_ids


def send_telegram_photo(
    bot_token: str,
    chat_id: str,
    image_path: Path,
    caption: str = "",
    dry_run: bool = False,
) -> None:
    if dry_run:
        print(f"[telegram photo] {image_path} caption={caption!r}\n{'-' * 40}")
        return

    image_bytes = image_path.read_bytes()
    content_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    fields = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption
    result = _http_post_multipart(
        url,
        fields,
        {"photo": (image_path.name, image_bytes, content_type)},
    )
    if not result.get("ok"):
        raise RuntimeError(f"Telegram photo error: {result}")


def send_telegram(bot_token: str, chat_id: str, message: str, dry_run: bool = False) -> None:
    chunks = split_message(message, limit=3900)
    for index, chunk in enumerate(chunks, start=1):
        prefix = f"({index}/{len(chunks)})\n" if len(chunks) > 1 else ""
        payload_text = prefix + chunk
        if dry_run:
            print(f"[telegram]\n{payload_text}\n{'-' * 40}")
            continue

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        result = _http_post_json(
            url,
            {"chat_id": chat_id, "text": payload_text},
        )
        if not result.get("ok"):
            raise RuntimeError(f"Telegram error: {result}")


def send_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    email_to: str,
    subject: str,
    message: str,
    dry_run: bool = False,
) -> None:
    if dry_run:
        print(f"[email to {email_to}]\nSubject: {subject}\n\n{message}\n{'-' * 40}")
        return

    email = EmailMessage()
    email["Subject"] = subject
    email["From"] = smtp_user
    email["To"] = email_to
    email.set_content(message, charset="utf-8")

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as server:
        server.starttls(context=context)
        server.login(smtp_user, smtp_password)
        server.send_message(email)


def send_ntfy(
    *,
    topic: str,
    message: str,
    server: str = "https://ntfy.sh",
    token: str = "",
    dry_run: bool = False,
) -> None:
    title = "ਅੱਜ ਦਾ ਹੁਕਮਨਾਮਾ"
    chunks = split_message(message, limit=3900)
    for index, chunk in enumerate(chunks, start=1):
        chunk_title = f"{title} ({index}/{len(chunks)})" if len(chunks) > 1 else title
        if dry_run:
            print(f"[ntfy:{topic}] {chunk_title}\n{chunk}\n{'-' * 40}")
            continue

        url = f"{server.rstrip('/')}/{urllib.parse.quote(topic, safe='')}"
        request = urllib.request.Request(
            url,
            data=chunk.encode("utf-8"),
            headers={
                "Title": chunk_title,
                "Priority": "default",
                "Tags": "pray",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status >= 400:
                raise RuntimeError(f"ntfy error: HTTP {response.status}")


def send_whatsapp_image(
    *,
    id_instance: str,
    api_token: str,
    chat_id: str,
    image_path: Path,
    caption: str = "",
    media_url: str = "https://media.green-api.com",
    dry_run: bool = False,
) -> None:
    if dry_run:
        print(f"[whatsapp:{chat_id}] {image_path} caption={caption!r}\n{'-' * 40}")
        return

    image_bytes = image_path.read_bytes()
    content_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    url = (
        f"{media_url.rstrip('/')}/waInstance{id_instance}/"
        f"sendFileByUpload/{api_token}"
    )
    fields = {"chatId": chat_id, "fileName": image_path.name}
    if caption:
        fields["caption"] = caption
    result = _http_post_multipart(
        url,
        fields,
        {"file": (image_path.name, image_bytes, content_type)},
    )
    if not result.get("idMessage"):
        raise RuntimeError(f"WhatsApp error for {chat_id}: {result}")


def deliver(message: str, date_key: str, *, dry_run: bool = False) -> str:
    method = os.environ.get("DELIVERY_METHOD", "telegram").strip().lower()

    if method == "telegram":
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not bot_token or not chat_id:
            raise RuntimeError(
                "Telegram requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets."
            )
        send_telegram(bot_token, chat_id, message, dry_run=dry_run)
        return "telegram"

    if method == "email":
        smtp_user = os.environ.get("SMTP_USER", "").strip()
        smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()
        email_to = os.environ.get("EMAIL_TO", "").strip()
        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        if not smtp_user or not smtp_password or not email_to:
            raise RuntimeError(
                "Email requires SMTP_USER, SMTP_PASSWORD, and EMAIL_TO secrets."
            )
        send_email(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            email_to=email_to,
            subject=f"ਅੱਜ ਦਾ ਹੁਕਮਨਾਮਾ — {date_key}",
            message=message,
            dry_run=dry_run,
        )
        return "email"

    if method == "ntfy":
        topic = os.environ.get("NTFY_TOPIC", "").strip()
        if not topic:
            raise RuntimeError("ntfy requires NTFY_TOPIC secret.")
        send_ntfy(
            topic=topic,
            message=message,
            server=os.environ.get("NTFY_SERVER", "https://ntfy.sh").strip(),
            token=os.environ.get("NTFY_TOKEN", "").strip(),
            dry_run=dry_run,
        )
        return "ntfy"

    if method == "whatsapp":
        raise RuntimeError("Use deliver_image() for WhatsApp delivery.")

    raise RuntimeError(
        f"Unknown DELIVERY_METHOD '{method}'. Use telegram, whatsapp, email, or ntfy."
    )


def deliver_image(
    image_path: Path,
    date_key: str,
    *,
    caption: str = "",
    dry_run: bool = False,
) -> str:
    method = os.environ.get("DELIVERY_METHOD", "telegram").strip().lower()

    if method == "whatsapp":
        id_instance = os.environ.get("WHATSAPP_ID_INSTANCE", "").strip()
        api_token = os.environ.get("WHATSAPP_API_TOKEN", "").strip()
        if not id_instance or not api_token:
            raise RuntimeError(
                "WhatsApp requires WHATSAPP_ID_INSTANCE and WHATSAPP_API_TOKEN secrets."
            )
        media_url = os.environ.get(
            "WHATSAPP_MEDIA_URL", "https://media.green-api.com"
        ).strip()
        group_ids = load_whatsapp_group_ids()
        for chat_id in group_ids:
            send_whatsapp_image(
                id_instance=id_instance,
                api_token=api_token,
                chat_id=chat_id,
                image_path=image_path,
                caption=caption,
                media_url=media_url,
                dry_run=dry_run,
            )
        return f"whatsapp ({len(group_ids)} groups)"

    if method == "telegram":
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not bot_token or not chat_id:
            raise RuntimeError(
                "Telegram requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets."
            )
        send_telegram_photo(
            bot_token,
            chat_id,
            image_path,
            caption=caption,
            dry_run=dry_run,
        )
        return "telegram (image)"

    raise RuntimeError(
        f"Image delivery is not supported for DELIVERY_METHOD '{method}'."
    )


def sent_marker_path(cache_dir: Path, date_key: str) -> Path:
    return cache_dir / f"sent-{date_key}.marker"


def already_sent_today(cache_dir: Path, date_key: str) -> bool:
    return sent_marker_path(cache_dir, date_key).exists()


def mark_sent_today(cache_dir: Path, date_key: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    sent_marker_path(cache_dir, date_key).write_text(
        datetime.now(timezone.utc).isoformat(),
        encoding="utf-8",
    )


def main() -> int:
    dry_run = _env_bool("DRY_RUN", False)
    method = os.environ.get("DELIVERY_METHOD", "telegram").strip().lower()
    send_format = _send_format(method)
    include_punjabi = _env_bool("INCLUDE_PUNJABI", True)
    include_hindi = _env_bool("INCLUDE_HINDI", method == "whatsapp")
    include_english = _env_bool("INCLUDE_ENGLISH", method == "whatsapp")
    cache_dir = Path(os.environ.get("SENT_CACHE_DIR", ".sent-cache"))
    image_dir = Path(os.environ.get("IMAGE_OUTPUT_DIR", ".generated-images"))

    data = fetch_hukamnama()
    date_key = (
        f"{data['date']['gregorian']['year']}-"
        f"{data['date']['gregorian']['monthno']:02d}-"
        f"{data['date']['gregorian']['date']:02d}"
    )

    if not dry_run and already_sent_today(cache_dir, date_key):
        print(f"Hukamnama for {date_key} already sent. Skipping.")
        return 0

    if send_format == "image":
        image_path = image_dir / f"hukamnama-{date_key}.png"
        render_hukamnama_image(
            data,
            image_path,
            include_punjabi=include_punjabi,
            include_hindi=include_hindi,
            include_english=include_english,
        )
        caption = f"ਅੱਜ ਦਾ ਹੁਕਮਨਾਮਾ — {date_key}"
        channel = deliver_image(image_path, date_key, caption=caption, dry_run=dry_run)
        detail = f"image {image_path}"
    else:
        message = format_message(
            data,
            include_punjabi=include_punjabi,
            include_english=include_english,
        )
        channel = deliver(message, date_key, dry_run=dry_run)
        detail = f"{len(message)} chars"

    if not dry_run:
        mark_sent_today(cache_dir, date_key)
        print(f"Sent hukamnama for {date_key} via {channel} ({detail}).")
    else:
        print(f"Dry run complete for {date_key} via {channel} ({detail}).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())