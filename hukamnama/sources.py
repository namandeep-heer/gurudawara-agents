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
BANIDB_API = "https://api.banidb.com/v2/hukamnamas/today"
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
        if re.fullmatch(r"[੦-੯0-9]+", line) or line == "ਰਹਾਉ":
            if verses:
                verses[-1] = f"{verses[-1].rstrip(' ॥')} ॥{line} ॥"
            continue
        if not line.endswith("॥"):
            line = f"{line} ॥"
        verses.append(line)
    return verses


def _is_marker_only_verse(text: str) -> bool:
    normalized = _normalize_gurmukhi(text).replace("॥", "")
    return not normalized or normalized in {"ਰਹਾਉ"} or normalized.isdigit()


def _make_line(
    gurmukhi: str,
    *,
    punjabi: str = "",
    english: str = "",
    hindi_translit: str = "",
    hindi_meaning: str = "",
) -> dict:
    punjabi_value: dict | str = {"unicode": punjabi} if punjabi else ""
    return {
        "line": {
            "gurmukhi": {"unicode": gurmukhi},
            "translation": {
                "punjabi": {"default": punjabi_value},
                "english": {"default": english},
                "hindi": {"default": hindi_meaning},
            },
            "transliteration": {
                "devanagari": {"text": hindi_translit},
            },
        }
    }


def _normalize_gurmukhi(text: str) -> str:
    cleaned = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned.strip()


def _gurmukhi_lookup_keys(text: str) -> list[str]:
    normalized = _normalize_gurmukhi(text)
    if not normalized or _is_marker_only_verse(text):
        return []

    keys = [normalized]
    without_numbers = re.sub(r"॥[੦-੯0-9]+॥?", "॥", normalized)
    without_numbers = re.sub(r"[੦-੯0-9]+॥?$", "॥", without_numbers)
    if without_numbers and without_numbers not in keys:
        keys.append(without_numbers)
    return keys


def _find_banidb_verse(lookup: dict[str, dict], gurmukhi: str) -> dict | None:
    for key in _gurmukhi_lookup_keys(gurmukhi):
        verse = lookup.get(key)
        if verse:
            return verse

    target = _normalize_gurmukhi(gurmukhi)
    if len(target) < 8:
        return None

    for lookup_key, verse in lookup.items():
        if target in lookup_key or lookup_key in target:
            return verse
    return None


def _entry_gurmukhi(entry: dict) -> str:
    try:
        return entry["line"]["gurmukhi"]["unicode"].strip()
    except (KeyError, TypeError):
        return ""


def _entry_devanagari(entry: dict) -> str:
    try:
        return entry["line"]["transliteration"]["devanagari"]["text"].strip()
    except (KeyError, TypeError):
        return ""


def _entry_hindi_meaning(entry: dict) -> str:
    try:
        return entry["line"]["translation"]["hindi"]["default"].strip()
    except (KeyError, TypeError):
        return ""


def _banidb_hindi_text(verse: dict) -> str:
    translation = verse.get("translation", {}).get("hi", {})
    for key in ("ss", "sts"):
        value = translation.get(key, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _banidb_devanagari(verse: dict) -> str:
    transliteration = verse.get("transliteration", {})
    for key in ("hindi", "hi"):
        value = transliteration.get(key, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    translation = verse.get("translation", {}).get("hi", {})
    sts = translation.get("sts", "")
    if isinstance(sts, str) and sts.strip():
        return sts.strip()
    return ""


def _banidb_verse_lookup(banidb_data: dict) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    shabads = banidb_data.get("shabads", [])
    if not shabads:
        return lookup
    for verse in shabads[0].get("verses", []):
        gurmukhi = verse.get("verse", {}).get("unicode", "").strip()
        if not gurmukhi:
            continue
        for key in _gurmukhi_lookup_keys(gurmukhi):
            lookup.setdefault(key, verse)
    return lookup


def fetch_banidb_hukamnama(retries: int = 3, delay_seconds: int = 15) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            data = _http_get_json(BANIDB_API)
            if not data.get("shabads"):
                raise RuntimeError("BaniDB API returned no shabads")
            return data
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay_seconds)
    raise RuntimeError(f"Failed to fetch BaniDB hukamnama: {last_error}")


def enrich_with_hindi(data: dict) -> dict:
    """Add Hindi transliteration and viakhya from BaniDB for any source."""
    try:
        banidb = fetch_banidb_hukamnama()
    except RuntimeError:
        return data

    lookup = _banidb_verse_lookup(banidb)
    if not lookup:
        return data

    hindi_hukamnama_parts: list[str] = []
    hindi_viakhya_parts: list[str] = []

    for entry in data.get("hukamnama", []):
        gurmukhi = _entry_gurmukhi(entry)
        if not gurmukhi:
            continue

        verse = _find_banidb_verse(lookup, gurmukhi)
        if not verse:
            continue

        line = entry.setdefault("line", {})
        devanagari = _banidb_devanagari(verse)
        if devanagari:
            line.setdefault("transliteration", {}).setdefault("devanagari", {})["text"] = devanagari
            hindi_hukamnama_parts.append(devanagari)

        hindi_meaning = _banidb_hindi_text(verse)
        if hindi_meaning:
            line.setdefault("translation", {}).setdefault("hindi", {})["default"] = hindi_meaning
            hindi_viakhya_parts.append(hindi_meaning)

    blocks = data.setdefault("blocks", {})
    if hindi_hukamnama_parts and not blocks.get("hindi"):
        blocks["hindi"] = " ".join(hindi_hukamnama_parts)
    if hindi_viakhya_parts and not blocks.get("hindi_viakhya"):
        blocks["hindi_viakhya"] = " ".join(hindi_viakhya_parts)

    return data


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
    enrich_hindi: bool = True,
) -> dict:
    selected = (source or os.environ.get("HUKAMNAMA_SOURCE", "gurbaninow")).strip().lower()
    if selected == "gurbaninow":
        data = fetch_gurbaninow()
    elif selected == "sgpc":
        data = fetch_sgpc(url=sgpc_url or os.environ.get("SGPC_URL", SGPC_DEFAULT_URL))
    else:
        raise RuntimeError(
            f"Unknown HUKAMNAMA_SOURCE '{selected}'. Use 'gurbaninow' or 'sgpc'."
        )

    if enrich_hindi:
        data = enrich_with_hindi(data)
    return data


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