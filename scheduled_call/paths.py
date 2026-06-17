"""Resolve service-local paths for scheduled call data and config."""

from __future__ import annotations

import os
from pathlib import Path

from scheduled_call import REPO_ROOT, SERVICE_ROOT


def resolve_service_path(raw: str, *, default: str = "") -> Path:
    value = (raw or default).strip()
    if not value:
        return SERVICE_ROOT
    path = Path(value)
    if path.is_absolute():
        return path
    return SERVICE_ROOT / path


def ensure_service_env() -> None:
    os.environ.setdefault("SERVICE_DIR", SERVICE_ROOT.name)