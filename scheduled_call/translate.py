"""Translate English reminder text to Punjabi for voice delivery."""

from __future__ import annotations

from deep_translator import GoogleTranslator


def english_to_punjabi(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Message text is empty")
    return GoogleTranslator(source="en", target="pa").translate(cleaned)