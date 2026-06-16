#!/usr/bin/env python3
"""Compress large MP3 files for reliable WhatsApp delivery."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

DEFAULT_MAX_BYTES = 12 * 1024 * 1024
_MIN_BITRATE = 24_000
_MAX_BITRATE = 96_000


def _max_upload_bytes() -> int:
    raw = os.environ.get("KATHA_MAX_UPLOAD_MB", "12").strip()
    try:
        megabytes = max(4, min(int(raw), 64))
    except ValueError:
        megabytes = 12
    return megabytes * 1024 * 1024


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError(
            "Katha compression requires imageio-ffmpeg. "
            "Install dependencies with: pip install -r requirements.txt"
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def _probe_duration_seconds(ffmpeg: str, path: Path) -> float:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
        proc.stderr or "",
    )
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def compress_mp3_for_whatsapp(
    path: Path,
    *,
    max_bytes: int | None = None,
    log=print,
) -> Path:
    """Return a WhatsApp-sized MP3, re-encoding katha when needed."""
    source = path.resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    limit = max_bytes if max_bytes is not None else _max_upload_bytes()
    original_size = source.stat().st_size
    if original_size <= limit:
        return source

    ffmpeg = _ffmpeg_exe()
    duration = _probe_duration_seconds(ffmpeg, source)
    if duration <= 0:
        raise RuntimeError(f"Could not determine audio duration for {source.name}")

    target_bps = int((limit * 8 * 0.9) / duration)
    target_bps = max(_MIN_BITRATE, min(target_bps, _MAX_BITRATE))
    output = source.with_name(f"{source.stem}-whatsapp.mp3")

    log(
        f"Compressing {source.name} "
        f"({original_size / (1024 * 1024):.1f} MB) "
        f"to <= {limit / (1024 * 1024):.0f} MB "
        f"at {target_bps // 1000} kbps mono..."
    )

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "22050",
        "-c:a",
        "libmp3lame",
        "-b:a",
        f"{target_bps}",
        str(output),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0 or not output.exists():
        detail = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
        raise RuntimeError(f"Katha compression failed for {source.name}: {detail}")

    compressed_size = output.stat().st_size
    if compressed_size > limit and target_bps > _MIN_BITRATE:
        output.unlink(missing_ok=True)
        lower_bps = max(_MIN_BITRATE, int(target_bps * 0.75))
        log(f"Compressed file still too large; retrying at {lower_bps // 1000} kbps...")
        cmd[-1] = f"{lower_bps}"
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0 or not output.exists():
            detail = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
            raise RuntimeError(f"Katha compression failed for {source.name}: {detail}")
        compressed_size = output.stat().st_size

    log(
        f"Compressed {source.name}: "
        f"{original_size / (1024 * 1024):.1f} MB -> "
        f"{compressed_size / (1024 * 1024):.1f} MB"
    )
    return output