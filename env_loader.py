#!/usr/bin/env python3
"""Load variables from local .env files into os.environ at import time."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_FILES = (".env", ".env.local")


def _resolve_env_files() -> list[Path]:
    custom = os.environ.get("ENV_FILES", "").strip()
    if custom:
        return [Path(item.strip()) for item in custom.split(",") if item.strip()]

    single = os.environ.get("ENV_FILE", "").strip()
    if single:
        return [Path(single)]

    return [Path(path) for path in DEFAULT_ENV_FILES]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        os.environ[key] = value


def load_env_files(paths: list[str | Path] | None = None) -> None:
    env_paths = [Path(path) for path in paths] if paths else _resolve_env_files()
    for env_path in env_paths:
        _load_env_file(env_path)


def load_env_file(path: str | Path | None = None) -> None:
    if path is None:
        load_env_files()
        return
    _load_env_file(Path(path))


load_env_files()