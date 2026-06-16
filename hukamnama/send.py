#!/usr/bin/env python3
"""Fetch daily Hukamnama from GurbaniNow and deliver via WhatsApp, email, or ntfy."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from hukamnama.paths import ensure_service_env, resolve_service_path

ensure_service_env()
import shared.env_loader  # noqa: F401 — load config.env + .env at startup

from hukamnama.audio_compress import compress_mp3_for_whatsapp
from hukamnama.image import render_hukamnama_images
from hukamnama.sources import fetch_hukamnama, resolve_audio_files

MAX_MESSAGE_LENGTH = 4000
USER_AGENT = "gur-agent/1.0"


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (LookupError, ValueError, OSError):
            pass


def _log(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        encoded = message.encode(sys.stdout.encoding or "utf-8", errors="replace")
        sys.stdout.buffer.write(encoded + b"\n")


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

    source_label = data.get("meta", {}).get("source_label", "GurbaniNow API")
    footer = [
        "---",
        f"ਸਰੋਤ: {source_label}",
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


def _transfer_timeout_seconds(size_bytes: int, *, minimum: int = 120) -> int:
    # ~200 KiB/s effective throughput plus a 2-minute buffer, capped at 30 minutes.
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes"}


def _send_audio_enabled() -> bool:
    if os.environ.get("SEND_AUDIO", "").strip():
        return _env_bool("SEND_AUDIO", False)
    return _env_bool(
        "INCLUDE_HUKAMNAMA_AUDIO",
        _env_bool("INCLUDE_AUDIO", False),
    )


def _send_katha_audio_enabled() -> bool:
    if os.environ.get("SEND_KATHA_AUDIO", "").strip():
        return _env_bool("SEND_KATHA_AUDIO", False)
    return _env_bool("INCLUDE_KATHA_AUDIO", False)


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

    groups_file = resolve_service_path(
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
        _log(f"[email to {email_to}]\nSubject: {subject}\n\n{message}\n{'-' * 40}")
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
            _log(f"[ntfy:{topic}] {chunk_title}\n{chunk}\n{'-' * 40}")
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


def _download_file(url: str, output_path: Path) -> Path:
    expected = 0
    try:
        head_request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT},
            method="HEAD",
        )
        with urllib.request.urlopen(head_request, timeout=30) as head_response:
            expected = int(head_response.headers.get("Content-Length", "0") or 0)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        expected = 0

    timeout = _transfer_timeout_seconds(expected) if expected else 300
    if "kathaaudio" in url:
        timeout = max(timeout, 900)
    if expected >= 10 * 1024 * 1024:
        _log(
            f"Downloading large audio ({expected / (1024 * 1024):.1f} MB) "
            f"from {url}..."
        )

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    if expected and len(payload) < expected:
        raise RuntimeError(
            f"Downloaded file from {url} is incomplete "
            f"({len(payload)} of {expected} bytes)."
        )
    if len(payload) < 1024:
        raise RuntimeError(f"Downloaded file from {url} looks too small.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    return output_path


def send_whatsapp_file(
    *,
    id_instance: str,
    api_token: str,
    chat_id: str,
    file_path: Path,
    caption: str = "",
    media_url: str = "https://media.green-api.com",
    dry_run: bool = False,
) -> None:
    if dry_run:
        _log(f"[whatsapp:{chat_id}] {file_path} caption={caption!r}\n{'-' * 40}")
        return

    file_bytes = file_path.read_bytes()
    if len(file_bytes) >= 10 * 1024 * 1024:
        _log(
            f"Uploading large file to WhatsApp ({len(file_bytes) / (1024 * 1024):.1f} MB): "
            f"{file_path.name}"
        )
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    url = (
        f"{media_url.rstrip('/')}/waInstance{id_instance}/"
        f"sendFileByUpload/{api_token}"
    )
    fields = {"chatId": chat_id, "fileName": file_path.name}
    if caption:
        fields["caption"] = caption
    result = _http_post_multipart(
        url,
        fields,
        {"file": (file_path.name, file_bytes, content_type)},
        timeout=_transfer_timeout_seconds(len(file_bytes)),
    )
    if not result.get("idMessage"):
        raise RuntimeError(f"WhatsApp error for {chat_id}: {result}")


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
    send_whatsapp_file(
        id_instance=id_instance,
        api_token=api_token,
        chat_id=chat_id,
        file_path=image_path,
        caption=caption,
        media_url=media_url,
        dry_run=dry_run,
    )


def deliver(message: str, date_key: str, *, dry_run: bool = False) -> str:
    method = os.environ.get("DELIVERY_METHOD", "whatsapp").strip().lower()

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
        f"Unknown DELIVERY_METHOD '{method}'. Use whatsapp, email, or ntfy."
    )


def deliver_image(
    image_path: Path,
    *,
    caption: str = "",
    dry_run: bool = False,
) -> str:
    return deliver_images([image_path], caption=caption, dry_run=dry_run)


def deliver_images(
    image_paths: list[Path],
    *,
    caption: str = "",
    dry_run: bool = False,
) -> str:
    method = os.environ.get("DELIVERY_METHOD", "whatsapp").strip().lower()
    if method != "whatsapp":
        raise RuntimeError(
            f"Image delivery is only supported for DELIVERY_METHOD 'whatsapp', not '{method}'."
        )

    if not image_paths:
        raise RuntimeError("No images to deliver.")

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
    total = len(image_paths)
    for index, image_path in enumerate(image_paths, start=1):
        part_caption = caption
        if total > 1:
            part_caption = f"{caption} ({index}/{total})".strip()
        for chat_id in group_ids:
            send_whatsapp_image(
                id_instance=id_instance,
                api_token=api_token,
                chat_id=chat_id,
                image_path=image_path,
                caption=part_caption,
                media_url=media_url,
                dry_run=dry_run,
            )
    return f"whatsapp ({len(group_ids)} groups, {total} images)"


def deliver_audio(
    data: dict,
    date_key: str,
    *,
    audio_dir: Path,
    dry_run: bool = False,
) -> str:
    method = os.environ.get("DELIVERY_METHOD", "whatsapp").strip().lower()
    if method != "whatsapp":
        return "audio skipped (not whatsapp)"

    include_hukamnama_audio = _send_audio_enabled()
    include_katha_audio = _send_katha_audio_enabled()
    audio_files = resolve_audio_files(
        data,
        include_hukamnama_audio=include_hukamnama_audio,
        include_katha_audio=include_katha_audio,
    )
    if not audio_files:
        return "no audio configured"

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

    sent_labels: list[str] = []
    for audio_url, filename in audio_files:
        local_path = audio_dir / f"{date_key}-{filename}"
        label = filename.replace(".mp3", "")
        _log(f"Preparing {label} audio...")
        _download_file(audio_url, local_path)
        if label == "katha":
            local_path = compress_mp3_for_whatsapp(local_path, log=_log)
        if dry_run:
            _log(f"[dry run] audio saved: {local_path} ({audio_url})")
        caption = f"ਅੱਜ ਦਾ ਹੁਕਮਨਾਮਾ — {label} — {date_key}"
        for chat_id in group_ids:
            send_whatsapp_file(
                id_instance=id_instance,
                api_token=api_token,
                chat_id=chat_id,
                file_path=local_path,
                caption=caption,
                media_url=media_url,
                dry_run=dry_run,
            )
        sent_labels.append(label)

    return f"audio ({', '.join(sent_labels)})"


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


def _parse_cli_flags() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send daily hukamnama")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--skip-sent-check",
        action="store_true",
        help="Allow re-sending if already sent today",
    )
    group.add_argument(
        "--enforce-sent-check",
        action="store_true",
        help="Block if already sent today",
    )
    return parser.parse_args()


def _resolve_skip_sent_check(cli: argparse.Namespace) -> bool:
    if cli.skip_sent_check:
        return True
    if cli.enforce_sent_check:
        return False
    return _env_bool("SKIP_SENT_CHECK", False)


def main() -> int:
    _configure_stdout()
    dry_run = _env_bool("DRY_RUN", False)
    method = os.environ.get("DELIVERY_METHOD", "whatsapp").strip().lower()
    send_format = _send_format(method)
    include_punjabi = _env_bool("INCLUDE_PUNJABI", True)
    include_hindi = _env_bool("INCLUDE_HINDI", method == "whatsapp")
    include_english = _env_bool("INCLUDE_ENGLISH", method == "whatsapp")
    cache_dir = resolve_service_path(os.environ.get("SENT_CACHE_DIR", ".sent-cache"))
    image_dir = resolve_service_path(os.environ.get("IMAGE_OUTPUT_DIR", ".generated-images"))
    audio_dir = resolve_service_path(os.environ.get("AUDIO_OUTPUT_DIR", ".generated-audio"))
    source = os.environ.get("HUKAMNAMA_SOURCE", "gurbaninow").strip().lower()

    data = fetch_hukamnama(source)
    date_key = (
        f"{data['date']['gregorian']['year']}-"
        f"{data['date']['gregorian']['monthno']:02d}-"
        f"{data['date']['gregorian']['date']:02d}"
    )

    cli = _parse_cli_flags()
    skip_sent_check = _resolve_skip_sent_check(cli)
    if not dry_run and not skip_sent_check and already_sent_today(cache_dir, date_key):
        _log(f"Hukamnama for {date_key} already sent. Skipping.")
        return 0

    if send_format == "image":
        image_paths = render_hukamnama_images(
            data,
            image_dir,
            date_key,
            include_punjabi=include_punjabi,
            include_hindi=include_hindi,
            include_english=include_english,
        )
        caption = f"ਅੱਜ ਦਾ ਹੁਕਮਨਾਮਾ — {date_key}"
        channel = deliver_images(image_paths, caption=caption, dry_run=dry_run)
        audio_channel = deliver_audio(
            data,
            date_key,
            audio_dir=audio_dir,
            dry_run=dry_run,
        )
        detail = f"images {', '.join(path.name for path in image_paths)}, {audio_channel}"
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
        _log(f"Sent hukamnama for {date_key} via {channel} ({detail}).")
    else:
        _log(f"Dry run complete for {date_key} via {channel} ({detail}).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())