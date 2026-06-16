#!/usr/bin/env python3
"""Fetch daily Hukamnama from GurbaniNow or SGPC (Sri Harmandir Sahib)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from html import unescape

USER_AGENT = "gur-agent/1.0"
GURBANINOW_API = "https://api.gurbaninow.com/v2/hukamnama/today"
SGPC_DEFAULT_URL = "https://hs.sgpc.net/"


def _http_get(url: str, timeout: int = 60) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _http_get_json(url: str, timeout: int = 60) -> dict:
    return json.loads(_http_get(url, timeout=timeout))


def _clean_html_text(raw: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()


def _split_gurmukhi_verses(text: str) -> list[str]:
    parts = re.split(r"\s*॥\s*", text)
    verses: list[str] = []
    for part in parts:
        line = part.strip()
        if not line:
            continue
        if not line.endswith("॥"):
            line = f"{line} ॥"
        verses.append(line)
    return verses


def _make_line(gurmukhi: str, *, punjabi: str = "", english: str = "", hindi: str = "") -> dict:
    punjabi_value: dict | str = {"unicode": punjabi} if punjabi else ""
    return {
        "line": {
            "gurmukhi": {"unicode": gurmukhi},
            "translation": {
                "punjabi": {"default": punjabi_value},
                "english": {"default": english},
            },
            "transliteration": {
                "devanagari": {"text": hindi},
            },
        }
    }


def _attach_meta(data: dict, *, source: str, source_label: str, audio: dict[str, str]) -> dict:
    data["meta"] = {
        "source": source,
        "source_label": source_label,
        "audio": audio,
    }
    return data


def sgpc_audio_urls_from_date(gregorian: dict) -> dict[str, str]:
    day = int(gregorian["date"])
    month = int(gregorian["monthno"])
    year_suffix = str(int(gregorian["year"]))[-2:]
    stamp = f"{day:02d}{month:02d}{year_suffix}"
    return {
        "hukamnama": f"https://hs.sgpc.net/hukamnamaaudio/SGPCNET{stamp}.mp3",
        "katha": f"https://hs.sgpc.net/kathaaudio/katha{stamp}.mp3",
    }


def _parse_sgpc_html(html: str) -> dict:
    hukam_audio = re.search(
        r'src="(https://hs\.sgpc\.net/hukamnamaaudio/[^"]+\.mp3)"',
        html,
    )
    katha_audio = re.search(
        r'src="(https://hs\.sgpc\.net/kathaaudio/[^"]+\.mp3)"',
        html,
    )
    audio = {
        "hukamnama": hukam_audio.group(1) if hukam_audio else "",
        "katha": katha_audio.group(1) if katha_audio else "",
    }

    date_match = re.search(
        r'<p class="fs-5 customDate"><strong>(\d{2})-(\d{2})-(\d{4})</strong></p>',
        html,
    )
    if not date_match:
        raise RuntimeError("SGPC page did not include a recognizable date.")
    day, month, year = date_match.groups()
    gregorian = {
        "monthno": int(month),
        "date": int(day),
        "year": int(year),
        "month": datetime_month_name(int(month)),
        "day": weekday_name(int(day), int(month), int(year)),
    }

    gurmukhi_match = re.search(
        r'<div class="hukamnama-card mt-4">.*?<p class="hukamnama-text">(.*?)</p>',
        html,
        re.DOTALL,
    )
    if not gurmukhi_match:
        raise RuntimeError("SGPC page did not include Gurmukhi hukamnama text.")
    gurmukhi_text = _clean_html_text(gurmukhi_match.group(1))

    raag_match = re.search(
        r'<div class="hukamnama-card mt-4">.*?<h4 class="hukamnama-title">(.*?)</h4>',
        html,
        re.DOTALL,
    )
    raag_unicode = _clean_html_text(raag_match.group(1)) if raag_match else ""

    nanakshahi_match = re.search(
        r'<div class="hukamnama-card mt-4">.*?<p class="customDate"><strong>(.*?)</strong></p>',
        html,
        re.DOTALL,
    )
    nanakshahi_line = _clean_html_text(nanakshahi_match.group(1)) if nanakshahi_match else ""

    ang_match = re.search(r"\(ਅੰਗ:\s*([^)]+)\)", html)
    if not ang_match:
        ang_match = re.search(r"\(Page:\s*(\d+)\)", html, re.IGNORECASE)
    pageno = int(re.sub(r"\D", "", ang_match.group(1))) if ang_match else 0

    punjabi_match = re.search(
        r'<div class="hukamnama-card2">\s*<h4 class="hukamnama-title">ਪੰਜਾਬੀ ਵਿਆਖਿਆ</h4>\s*'
        r'<p class="hukamnama-text">(.*?)</p>',
        html,
        re.DOTALL,
    )
    punjabi_block = _clean_html_text(punjabi_match.group(1)) if punjabi_match else ""

    english_match = re.search(
        r"English Translation.*?<p class=\"hukamnama-text\"[^>]*>(.*?)</p>",
        html,
        re.DOTALL | re.IGNORECASE,
    )
    english_block = _clean_html_text(english_match.group(1)) if english_match else ""

    verses = _split_gurmukhi_verses(gurmukhi_text)
    if not verses:
        raise RuntimeError("SGPC Gurmukhi text could not be parsed into verses.")

    data = {
        "date": {
            "gregorian": gregorian,
            "nanakshahi": {
                "punjabi": {
                    "display": nanakshahi_line,
                    "month": "",
                    "date": "",
                    "year": "",
                    "day": "",
                }
            },
        },
        "hukamnamainfo": {
            "raag": {"unicode": raag_unicode},
            "writer": {"unicode": ""},
            "pageno": pageno,
        },
        "hukamnama": [_make_line(verse) for verse in verses],
        "blocks": {
            "punjabi": punjabi_block,
            "english": english_block,
        },
    }
    if not audio["hukamnama"]:
        audio = sgpc_audio_urls_from_date(gregorian)
    return _attach_meta(
        data,
        source="sgpc",
        source_label="SGPC / Sri Harmandir Sahib",
        audio=audio,
    )


def datetime_month_name(monthno: int) -> str:
    names = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    return names[monthno - 1]


def weekday_name(day: int, month: int, year: int) -> str:
    from datetime import date

    return date(year, month, day).strftime("%A")


def fetch_gurbaninow(retries: int = 3, delay_seconds: int = 30) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            data = _http_get_json(GURBANINOW_API)
            if data.get("error"):
                raise RuntimeError(f"Hukamnama API error: {data['error']}")
            if not data.get("hukamnama"):
                raise RuntimeError("Hukamnama API returned no verses")
            audio = sgpc_audio_urls_from_date(data["date"]["gregorian"])
            return _attach_meta(
                data,
                source="gurbaninow",
                source_label="GurbaniNow / Sri Darbar Sahib",
                audio=audio,
            )
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay_seconds)
    raise RuntimeError(f"Failed to fetch GurbaniNow hukamnama: {last_error}")


def fetch_sgpc(
    *,
    url: str = SGPC_DEFAULT_URL,
    retries: int = 3,
    delay_seconds: int = 30,
) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            html = _http_get(url)
            return _parse_sgpc_html(html)
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay_seconds)
    raise RuntimeError(f"Failed to fetch SGPC hukamnama: {last_error}")


def fetch_hukamnama(
    source: str | None = None,
    *,
    sgpc_url: str | None = None,
) -> dict:
    selected = (source or os.environ.get("HUKAMNAMA_SOURCE", "gurbaninow")).strip().lower()
    if selected == "gurbaninow":
        return fetch_gurbaninow()
    if selected == "sgpc":
        return fetch_sgpc(url=sgpc_url or os.environ.get("SGPC_URL", SGPC_DEFAULT_URL))
    raise RuntimeError(
        f"Unknown HUKAMNAMA_SOURCE '{selected}'. Use 'gurbaninow' or 'sgpc'."
    )


def resolve_audio_files(
    data: dict,
    *,
    include_hukamnama_audio: bool,
    include_katha_audio: bool,
) -> list[tuple[str, str]]:
    audio = data.get("meta", {}).get("audio", {})
    files: list[tuple[str, str]] = []
    if include_hukamnama_audio and audio.get("hukamnama"):
        files.append((audio["hukamnama"], "hukamnama.mp3"))
    if include_katha_audio and audio.get("katha"):
        files.append((audio["katha"], "katha.mp3"))
    return files