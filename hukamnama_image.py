#!/usr/bin/env python3
"""Render daily Hukamnama as a shareable image."""

from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import env_loader  # noqa: F401 — load .env files at startup
from PIL import Image, ImageDraw, ImageFont

USER_AGENT = "gur-agent/1.0"
IMAGE_WIDTH = 1080
HORIZONTAL_PADDING = 56
VERTICAL_PADDING = 48
LINE_GAP = 10
SECTION_GAP = 28
VERSE_GAP = 22

COLOR_BG = "#FFF8F0"
COLOR_HEADER = "#E65100"
COLOR_HEADER_TEXT = "#FFFFFF"
COLOR_TITLE = "#1A237E"
COLOR_GURMUKHI = "#1B1B1B"
COLOR_TRANSLATION = "#4A4A4A"
COLOR_MUTED = "#6B6B6B"
COLOR_DIVIDER = "#E0C9A6"

MIN_FONT_BYTES = 10_000

FONT_SOURCES = {
    "gurmukhi": (
        "NotoSansGurmukhi-Regular.ttf",
        [
            "https://fonts.gstatic.com/s/notosansgurmukhi/v29/w8g9H3EvQP81sInb43inmyN9zZ7hb7ATbSWo4q8dJ74a3cVrYFQ_bogT0-gPeG1Oenbx.ttf",
            "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansgurmukhi/NotoSansGurmukhi%5Bwdth%2Cwght%5D.ttf",
        ],
    ),
    "devanagari": (
        "NotoSansDevanagari-Regular.ttf",
        [
            "https://fonts.gstatic.com/s/notosansdevanagari/v30/TuGoUUFzXI5FBtUq5a8bjKYTZjtRU6Sgv3NaV_SNmI0b8QQCQmHn6B2OHjbL_08AlXQly-A.ttf",
            "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth%2Cwght%5D.ttf",
        ],
    ),
    "latin": (
        "NotoSans-Regular.ttf",
        [
            "https://fonts.gstatic.com/s/notosans/v42/o-0mIpQlx3QUlC5A4PNB6Ryti20_6n1iPHjcz6L1SoM-jCpoiyD9A99d.ttf",
            "https://raw.githubusercontent.com/google/fonts/main/ofl/notosans/NotoSans%5Bwdth%2Cwght%5D.ttf",
        ],
    ),
}


@dataclass(frozen=True)
class TextLine:
    text: str
    font_key: str
    size: int
    color: str
    gap_after: int = LINE_GAP


@dataclass(frozen=True)
class LayoutPart:
    key: str
    title: str
    lines: list[TextLine]


def _fonts_dir() -> Path:
    return Path(os.environ.get("FONTS_DIR", ".fonts"))


def _download_font(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read()
    if len(payload) < MIN_FONT_BYTES:
        raise RuntimeError(f"Downloaded font from {url} looks too small ({len(payload)} bytes).")
    return payload


def ensure_fonts() -> dict[str, Path]:
    directory = _fonts_dir()
    directory.mkdir(parents=True, exist_ok=True)
    resolved: dict[str, Path] = {}
    for key, (filename, urls) in FONT_SOURCES.items():
        path = directory / filename
        if path.exists() and path.stat().st_size < MIN_FONT_BYTES:
            path.unlink()

        if not path.exists():
            last_error: Exception | None = None
            for url in urls:
                try:
                    path.write_bytes(_download_font(url))
                    break
                except Exception as exc:  # noqa: BLE001 - try each mirror
                    last_error = exc
            else:
                raise RuntimeError(
                    f"Failed to download {filename} for {key}. Tried: {urls}. Last error: {last_error}"
                ) from last_error

        resolved[key] = path
    return resolved


def _load_fonts(font_paths: dict[str, Path]) -> dict[tuple[str, int], ImageFont.FreeTypeFont]:
    cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
    for key, path in font_paths.items():
        for size in (20, 22, 24, 28, 32, 36, 40):
            cache[(key, size)] = ImageFont.truetype(str(path), size=size)
    return cache


def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _wrap_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    if not text:
        return []

    words = text.split()
    if not words:
        return [text]

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        width, _ = _measure(draw, candidate, font)
        if width <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        if _measure(draw, word, font)[0] <= max_width:
            current = word
            continue
        chunk = ""
        for char in word:
            candidate = f"{chunk}{char}"
            if _measure(draw, candidate, font)[0] <= max_width:
                chunk = candidate
            else:
                if chunk:
                    lines.append(chunk)
                chunk = char
        current = chunk
    if current:
        lines.append(current)
    return lines


def _line_text(line: dict, field: str, subfield: str = "default") -> str:
    try:
        value = line["line"]["translation"][field][subfield]
        if isinstance(value, dict):
            return value.get("unicode", "").strip()
        return str(value).strip()
    except (KeyError, TypeError):
        return ""


def _hindi_text(line: dict) -> str:
    try:
        return line["line"]["transliteration"]["devanagari"]["text"].strip()
    except (KeyError, TypeError):
        return ""


def _hindi_meaning_text(line: dict) -> str:
    try:
        return line["line"]["translation"]["hindi"]["default"].strip()
    except (KeyError, TypeError):
        return ""


def _meta_lines(data: dict) -> list[TextLine]:
    gregorian = data["date"]["gregorian"]
    nanakshahi = data["date"]["nanakshahi"]["punjabi"]
    info = data["hukamnamainfo"]
    if nanakshahi.get("display"):
        nanakshahi_text = f"ਨਾਨਕਸ਼ਾਹੀ: {nanakshahi['display']}"
    else:
        nanakshahi_text = (
            f"ਨਾਨਕਸ਼ਾਹੀ: {nanakshahi['month']} {nanakshahi['date']}, "
            f"{nanakshahi['year']} ({nanakshahi['day']})"
        )

    return [
        TextLine("ਸ੍ਰੀ ਦਰਬਾਰ ਸਾਹਿਬ, ਅਮ੍ਰਿਤਸਰ", "gurmukhi", 28, COLOR_TITLE, LINE_GAP),
        TextLine(nanakshahi_text, "gurmukhi", 22, COLOR_MUTED, LINE_GAP),
        TextLine(
            f"Date: {gregorian['date']} {gregorian['month']} {gregorian['year']}, {gregorian['day']}",
            "latin",
            22,
            COLOR_MUTED,
            LINE_GAP,
        ),
        TextLine(
            f"ਰਾਗ: {info['raag']['unicode']}  |  ਰਚਨਾਕਾਰ: {info['writer']['unicode']}  |  ਅੰਗ: {info['pageno']}",
            "gurmukhi",
            22,
            COLOR_MUTED,
            SECTION_GAP,
        ),
    ]


def _compact_meta_line(data: dict) -> TextLine:
    gregorian = data["date"]["gregorian"]
    text = f"{gregorian['date']} {gregorian['month']} {gregorian['year']}"
    return TextLine(text, "latin", 20, COLOR_MUTED, SECTION_GAP)


def _footer_lines(data: dict) -> list[TextLine]:
    source_label = data.get("meta", {}).get("source_label", "GurbaniNow API")
    return [
        TextLine(f"ਸਰੋਤ: {source_label}", "gurmukhi", 20, COLOR_MUTED, LINE_GAP),
        TextLine(
            "ਵਾਹਿਗੁਰੂ ਜੀ ਕਾ ਖਾਲਸਾ, ਵਾਹਿਗੁਰੂ ਜੀ ਕੀ ਫਤਹਿ",
            "gurmukhi",
            24,
            COLOR_TITLE,
            0,
        ),
    ]


def _verse_lines(
    data: dict,
    *,
    include_punjabi: bool,
    include_hindi: bool,
    include_english: bool,
) -> list[TextLine]:
    layout: list[TextLine] = []
    for entry in data["hukamnama"]:
        gurmukhi = entry["line"]["gurmukhi"]["unicode"].strip()
        if not gurmukhi:
            continue
        compact = gurmukhi.replace(" ", "")
        if compact in {"॥", "੧॥", "੨॥", "੩॥", "੪॥", "ਰਹਾਉ॥"}:
            continue

        layout.append(TextLine(gurmukhi, "gurmukhi", 32, COLOR_GURMUKHI, LINE_GAP))
        if include_punjabi:
            punjabi = _line_text(entry, "punjabi")
            if punjabi:
                layout.append(TextLine(punjabi, "gurmukhi", 22, COLOR_TRANSLATION, LINE_GAP))
        if include_hindi:
            hindi = _hindi_text(entry)
            if hindi:
                layout.append(TextLine(hindi, "devanagari", 24, COLOR_GURMUKHI, LINE_GAP))
        if include_english:
            english = _line_text(entry, "english")
            if english:
                layout.append(TextLine(english, "latin", 22, COLOR_TRANSLATION, LINE_GAP))
        layout.append(TextLine("", "latin", 1, COLOR_TRANSLATION, VERSE_GAP))
    return layout


def build_layout_parts(
    data: dict,
    *,
    include_punjabi: bool = True,
    include_hindi: bool = True,
    include_english: bool = True,
) -> list[LayoutPart]:
    """Split content into phone-friendly parts: hukamnama + 3 viakhya sections."""
    blocks = data.get("blocks", {})
    parts: list[LayoutPart] = []

    hukamnama_lines = _meta_lines(data) + _verse_lines(
        data,
        include_punjabi=include_punjabi,
        include_hindi=include_hindi,
        include_english=include_english,
    ) + _footer_lines(data)
    parts.append(LayoutPart("hukamnama", "ਅੱਜ ਦਾ ਹੁਕਮਨਾਮਾ", hukamnama_lines))

    if blocks.get("punjabi") and include_punjabi:
        parts.append(
            LayoutPart(
                "punjabi-viakhya",
                "ਪੰਜਾਬੀ ਵਿਆਖਿਆ",
                [
                    _compact_meta_line(data),
                    TextLine(blocks["punjabi"], "gurmukhi", 22, COLOR_TRANSLATION, SECTION_GAP),
                    *_footer_lines(data),
                ],
            )
        )

    if blocks.get("hindi_viakhya") and include_hindi:
        parts.append(
            LayoutPart(
                "hindi-viakhya",
                "हिन्दी व्याख्या",
                [
                    _compact_meta_line(data),
                    TextLine(blocks["hindi_viakhya"], "devanagari", 22, COLOR_TRANSLATION, SECTION_GAP),
                    *_footer_lines(data),
                ],
            )
        )

    if blocks.get("english") and include_english:
        parts.append(
            LayoutPart(
                "english",
                "English Translation",
                [
                    _compact_meta_line(data),
                    TextLine(blocks["english"], "latin", 22, COLOR_TRANSLATION, SECTION_GAP),
                    *_footer_lines(data),
                ],
            )
        )

    return parts


def build_layout(
    data: dict,
    *,
    include_punjabi: bool = True,
    include_hindi: bool = True,
    include_english: bool = True,
) -> list[TextLine]:
    return [
        line
        for part in build_layout_parts(
            data,
            include_punjabi=include_punjabi,
            include_hindi=include_hindi,
            include_english=include_english,
        )
        for line in part.lines
    ]


def _render_lines(
    draw: ImageDraw.ImageDraw,
    layout: list[TextLine],
    fonts: dict[tuple[str, int], ImageFont.FreeTypeFont],
    start_y: int,
) -> int:
    max_width = IMAGE_WIDTH - (2 * HORIZONTAL_PADDING)
    y = start_y
    for item in layout:
        if not item.text:
            y += item.gap_after
            continue
        font = fonts[(item.font_key, item.size)]
        for wrapped in _wrap_line(draw, item.text, font, max_width):
            draw.text((HORIZONTAL_PADDING, y), wrapped, font=font, fill=item.color)
            _, height = _measure(draw, wrapped, font)
            y += height + LINE_GAP
        y += max(0, item.gap_after - LINE_GAP)
    return y


def _estimate_height(
    draw: ImageDraw.ImageDraw,
    layout: list[TextLine],
    fonts: dict[tuple[str, int], ImageFont.FreeTypeFont],
) -> int:
    max_width = IMAGE_WIDTH - (2 * HORIZONTAL_PADDING)
    height = VERTICAL_PADDING
    for item in layout:
        if not item.text:
            height += item.gap_after
            continue
        font = fonts[(item.font_key, item.size)]
        for wrapped in _wrap_line(draw, item.text, font, max_width):
            _, line_height = _measure(draw, wrapped, font)
            height += line_height + LINE_GAP
        height += max(0, item.gap_after - LINE_GAP)
    return height + VERTICAL_PADDING


def _render_part_image(
    *,
    layout: list[TextLine],
    fonts: dict[tuple[str, int], ImageFont.FreeTypeFont],
    output_path: Path,
    title: str,
    part_label: str,
) -> Path:
    probe = Image.new("RGB", (IMAGE_WIDTH, 200), COLOR_BG)
    probe_draw = ImageDraw.Draw(probe)
    content_height = _estimate_height(probe_draw, layout, fonts)
    header_height = 112
    image_height = header_height + content_height

    image = Image.new("RGB", (IMAGE_WIDTH, image_height), COLOR_BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, IMAGE_WIDTH, header_height), fill=COLOR_HEADER)

    if title == "English Translation":
        title_font = fonts[("latin", 32)]
    elif title == "हिन्दी व्याख्या":
        title_font = fonts[("devanagari", 32)]
    elif any(ord(c) > 127 for c in title):
        title_font = fonts[("gurmukhi", 32)]
    else:
        title_font = fonts[("latin", 32)]

    part_font = fonts[("latin", 20)]
    title_width, title_height = _measure(draw, title, title_font)
    part_width, part_height = _measure(draw, part_label, part_font)
    title_y = 24
    draw.text(((IMAGE_WIDTH - title_width) // 2, title_y), title, font=title_font, fill=COLOR_HEADER_TEXT)
    draw.text(
        ((IMAGE_WIDTH - part_width) // 2, title_y + title_height + 8),
        part_label,
        font=part_font,
        fill=COLOR_HEADER_TEXT,
    )
    draw.line(
        (HORIZONTAL_PADDING, header_height + 12, IMAGE_WIDTH - HORIZONTAL_PADDING, header_height + 12),
        fill=COLOR_DIVIDER,
        width=2,
    )
    _render_lines(draw, layout, fonts, header_height + 28)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path


def render_hukamnama_images(
    data: dict,
    output_dir: Path,
    date_key: str,
    *,
    include_punjabi: bool = True,
    include_hindi: bool = True,
    include_english: bool = True,
) -> list[Path]:
    font_paths = ensure_fonts()
    fonts = _load_fonts(font_paths)
    parts = build_layout_parts(
        data,
        include_punjabi=include_punjabi,
        include_hindi=include_hindi,
        include_english=include_english,
    )
    total = len(parts)
    rendered: list[Path] = []
    for index, part in enumerate(parts, start=1):
        output_path = output_dir / f"hukamnama-{date_key}-{index:02d}-{part.key}.png"
        part_label = f"Part {index} of {total}"
        _render_part_image(
            layout=part.lines,
            fonts=fonts,
            output_path=output_path,
            title=part.title,
            part_label=part_label,
        )
        rendered.append(output_path)
    return rendered


def render_hukamnama_image(
    data: dict,
    output_path: Path,
    *,
    include_punjabi: bool = True,
    include_hindi: bool = True,
    include_english: bool = True,
) -> Path:
    """Render all split parts; returns the first image path for compatibility."""
    date_key = output_path.stem.removeprefix("hukamnama-")
    if date_key.endswith(".png"):
        date_key = date_key[:-4]
    if "-" in date_key and len(date_key.split("-")) > 3:
        date_key = "-".join(date_key.split("-")[:3])

    images = render_hukamnama_images(
        data,
        output_path.parent,
        date_key,
        include_punjabi=include_punjabi,
        include_hindi=include_hindi,
        include_english=include_english,
    )
    return images[0]