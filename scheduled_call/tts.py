"""Generate Punjabi speech audio from text."""

from __future__ import annotations

from pathlib import Path

from gtts import gTTS


def synthesize_punjabi_mp3(text: str, output_path: Path, *, lang: str = "pa") -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gTTS(text=text, lang=lang).save(str(output_path))
    return output_path