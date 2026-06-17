"""Load and match editable call schedules from schedule.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ScheduledCall:
    id: str
    date: str
    enabled: bool
    message_file: str
    contacts_file: str
    note: str


def _validate_date(value: str, *, context: str) -> str:
    cleaned = value.strip()
    if not _DATE_RE.match(cleaned):
        raise ValueError(f"{context}: date must be YYYY-MM-DD, got {value!r}")
    return cleaned


def _normalize_call(entry: dict, *, index: int, source: Path) -> ScheduledCall | None:
    if not isinstance(entry, dict):
        return None

    raw_date = str(entry.get("date", "")).strip()
    if not raw_date:
        return None

    call_id = str(entry.get("id", "")).strip() or f"call-{raw_date}-{index + 1}"
    return ScheduledCall(
        id=call_id,
        date=_validate_date(raw_date, context=f"{source} entry {call_id!r}"),
        enabled=bool(entry.get("enabled", True)),
        message_file=str(entry.get("message_file", "message.txt")).strip() or "message.txt",
        contacts_file=str(entry.get("contacts_file", "contacts.json")).strip() or "contacts.json",
        note=str(entry.get("note", "")).strip(),
    )


def load_schedule(path: Path) -> list[ScheduledCall]:
    data = json.loads(path.read_text(encoding="utf-8"))

    calls_raw = data.get("calls")
    if calls_raw is not None:
        if not isinstance(calls_raw, list):
            raise ValueError(f"{path}: 'calls' must be a list")
        calls: list[ScheduledCall] = []
        for index, entry in enumerate(calls_raw):
            normalized = _normalize_call(entry, index=index, source=path)
            if normalized is not None:
                calls.append(normalized)
        return calls

    # Legacy: { "dates": ["2026-07-01", ...] }
    dates = data.get("dates", [])
    if not isinstance(dates, list):
        raise ValueError(f"{path}: expected 'calls' list or legacy 'dates' list")
    legacy_calls: list[ScheduledCall] = []
    for index, raw_date in enumerate(dates):
        date_key = _validate_date(str(raw_date), context=f"{path} dates[{index}]")
        legacy_calls.append(
            ScheduledCall(
                id=f"call-{date_key}",
                date=date_key,
                enabled=True,
                message_file="message.txt",
                contacts_file="contacts.json",
                note="",
            )
        )
    return legacy_calls


def calls_for_date(schedule: list[ScheduledCall], date_key: str) -> list[ScheduledCall]:
    return [call for call in schedule if call.enabled and call.date == date_key]


def find_call_by_id(schedule: list[ScheduledCall], call_id: str) -> ScheduledCall | None:
    for call in schedule:
        if call.id == call_id:
            return call
    return None