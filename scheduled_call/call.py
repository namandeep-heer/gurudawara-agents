#!/usr/bin/env python3
"""Send scheduled Punjabi WhatsApp voice reminders on dates in schedule.json."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scheduled_call.paths import ensure_service_env, resolve_service_path

ensure_service_env()
import shared.env_loader  # noqa: F401 — load config.env + .env at startup

from scheduled_call.schedule import ScheduledCall, calls_for_date, find_call_by_id, load_schedule
from scheduled_call.translate import english_to_punjabi
from scheduled_call.tts import synthesize_punjabi_mp3
from scheduled_call.whatsapp_delivery import deliver_voice_messages


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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_contacts(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    contacts = data.get("contacts", [])
    if not isinstance(contacts, list):
        raise ValueError(f"{path}: 'contacts' must be a list")
    normalized: list[dict] = []
    for entry in contacts:
        if not isinstance(entry, dict):
            continue
        phone = str(entry.get("phone", "")).strip()
        if not phone:
            continue
        normalized.append(
            {
                "name": str(entry.get("name", "")).strip(),
                "phone": phone,
                "enabled": entry.get("enabled", True),
            }
        )
    return normalized


def _today_key(timezone_name: str) -> str:
    tz = ZoneInfo(timezone_name)
    return datetime.now(tz).strftime("%Y-%m-%d")


def sent_marker_path(cache_dir: Path, date_key: str, call_id: str) -> Path:
    safe_id = call_id.replace("/", "-")
    return cache_dir / f"sent-{date_key}-{safe_id}.marker"


def already_sent(cache_dir: Path, date_key: str, call_id: str) -> bool:
    return sent_marker_path(cache_dir, date_key, call_id).exists()


def mark_sent(cache_dir: Path, date_key: str, call_id: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    sent_marker_path(cache_dir, date_key, call_id).write_text(
        datetime.now(ZoneInfo("UTC")).isoformat(),
        encoding="utf-8",
    )


def _parse_cli_flags() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send scheduled WhatsApp voice reminders")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--skip-sent-check",
        action="store_true",
        help="Allow re-sending if this schedule entry already ran",
    )
    group.add_argument(
        "--enforce-sent-check",
        action="store_true",
        help="Block if this schedule entry already ran",
    )
    parser.add_argument(
        "--call-id",
        metavar="ID",
        help="Run a specific schedule entry by id (ignores date match)",
    )
    return parser.parse_args()


def _resolve_skip_sent_check(cli: argparse.Namespace) -> bool:
    if cli.skip_sent_check:
        return True
    if cli.enforce_sent_check:
        return False
    return _env_bool("SKIP_SENT_CHECK", False)


def _run_scheduled_reminder(
    *,
    call: ScheduledCall,
    date_key: str,
    dry_run: bool,
    skip_sent_check: bool,
    cache_dir: Path,
    audio_dir: Path,
    tts_lang: str,
    delay_seconds: int,
    send_text_intro: bool,
) -> int:
    if not call.enabled:
        _log(f"Skipping disabled entry {call.id!r}.")
        return 0

    if not dry_run and not skip_sent_check and already_sent(cache_dir, date_key, call.id):
        _log(f"Reminder {call.id!r} for {date_key} already sent. Skipping.")
        return 0

    contacts_file = resolve_service_path(call.contacts_file)
    message_file = resolve_service_path(call.message_file)
    contacts = _load_contacts(contacts_file)
    enabled_contacts = [contact for contact in contacts if contact.get("enabled", True)]
    if not enabled_contacts:
        _log(f"No enabled contacts in {contacts_file} for {call.id!r}.")
        return 1

    english_message = message_file.read_text(encoding="utf-8").strip()
    if not english_message:
        _log(f"Message file is empty: {message_file}")
        return 1

    label = call.note or call.id
    _log(f"[{label}] Translating message to Punjabi for {date_key}...")
    punjabi_message = english_to_punjabi(english_message)
    _log(f"Punjabi message: {punjabi_message}")

    audio_path = audio_dir / f"scheduled-reminder-{date_key}-{call.id}.mp3"
    _log(f"Generating Punjabi audio -> {audio_path.name}")
    synthesize_punjabi_mp3(punjabi_message, audio_path, lang=tts_lang)

    if dry_run:
        _log("DRY_RUN=1: skipping WhatsApp delivery.")

    results = deliver_voice_messages(
        contacts=enabled_contacts,
        audio_path=audio_path,
        punjabi_intro=punjabi_message,
        dry_run=dry_run,
        delay_seconds=delay_seconds,
        send_text_intro=send_text_intro,
    )

    if not dry_run:
        mark_sent(cache_dir, date_key, call.id)

    for line in results:
        _log(line)

    mode = "Dry run" if dry_run else "Completed"
    _log(
        f"{mode} WhatsApp reminder {call.id!r} for {date_key} "
        f"({len(enabled_contacts)} contact(s))."
    )
    return 0


def main() -> int:
    _configure_stdout()
    cli = _parse_cli_flags()

    dry_run = _env_bool("DRY_RUN", False)
    timezone_name = os.environ.get("CALL_TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata"
    schedule_file = resolve_service_path(os.environ.get("SCHEDULE_FILE", "schedule.json"))
    cache_dir = resolve_service_path(os.environ.get("SENT_CACHE_DIR", ".sent-cache"))
    audio_dir = resolve_service_path(os.environ.get("AUDIO_OUTPUT_DIR", ".generated-audio"))
    tts_lang = os.environ.get("TTS_LANG", "pa").strip() or "pa"
    delay_seconds = int(os.environ.get("MESSAGE_DELAY_SECONDS", "30"))
    send_text_intro = _env_bool("SEND_TEXT_INTRO", False)

    date_key = _today_key(timezone_name)
    schedule = load_schedule(schedule_file)
    skip_sent_check = _resolve_skip_sent_check(cli)

    if cli.call_id:
        selected = find_call_by_id(schedule, cli.call_id)
        if selected is None:
            _log(f"No schedule entry with id {cli.call_id!r} in {schedule_file}")
            return 1
        due_calls = [selected]
        _log(f"Running schedule entry {cli.call_id!r} (scheduled date {selected.date}).")
    else:
        due_calls = calls_for_date(schedule, date_key)
        if not due_calls:
            _log(f"Today ({date_key}) has no enabled reminders in {schedule_file}. Nothing to do.")
            return 0
        _log(f"Found {len(due_calls)} reminder(s) scheduled for {date_key}.")

    exit_code = 0
    for call in due_calls:
        result = _run_scheduled_reminder(
            call=call,
            date_key=call.date,
            dry_run=dry_run,
            skip_sent_check=skip_sent_check,
            cache_dir=cache_dir,
            audio_dir=audio_dir,
            tts_lang=tts_lang,
            delay_seconds=delay_seconds,
            send_text_intro=send_text_intro,
        )
        if result != 0:
            exit_code = result

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())