#!/usr/bin/env python3
"""Fetch daily Hukamnama from GurbaniNow and deliver via Telegram, email, or ntfy."""

from __future__ import annotations

import json
import os
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

    raise RuntimeError(
        f"Unknown DELIVERY_METHOD '{method}'. Use telegram, email, or ntfy."
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
    dry_run = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}
    include_punjabi = os.environ.get("INCLUDE_PUNJABI", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    include_english = os.environ.get("INCLUDE_ENGLISH", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    cache_dir = Path(os.environ.get("SENT_CACHE_DIR", ".sent-cache"))

    data = fetch_hukamnama()
    date_key = (
        f"{data['date']['gregorian']['year']}-"
        f"{data['date']['gregorian']['monthno']:02d}-"
        f"{data['date']['gregorian']['date']:02d}"
    )

    if not dry_run and already_sent_today(cache_dir, date_key):
        print(f"Hukamnama for {date_key} already sent. Skipping.")
        return 0

    message = format_message(
        data,
        include_punjabi=include_punjabi,
        include_english=include_english,
    )
    channel = deliver(message, date_key, dry_run=dry_run)

    if not dry_run:
        mark_sent_today(cache_dir, date_key)
        print(f"Sent hukamnama for {date_key} via {channel} ({len(message)} chars).")
    else:
        print(f"Dry run complete for {date_key} via {channel} ({len(message)} chars).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())