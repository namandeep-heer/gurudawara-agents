#!/usr/bin/env python3
"""Load config.env (shared) then .env (secrets + local overrides)."""

from __future__ import annotations

import os
from pathlib import Path

SHARED_ENV_FILE = "config.env"
LOCAL_ENV_FILES = (".env", ".env.local")


def _resolve_env_files() -> list[Path]:
    custom = os.environ.get("ENV_FILES", "").strip()
    if custom:
        return [Path(item.strip()) for item in custom.split(",") if item.strip()]

    single = os.environ.get("ENV_FILE", "").strip()
    if single:
        return [Path(single)]

    paths = [Path(SHARED_ENV_FILE)]
    paths.extend(Path(path) for path in LOCAL_ENV_FILES)
    return paths


def _strip_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _load_env_file(path: Path, *, overwrite: bool = False) -> None:
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
        value = _strip_env_value(value)
        if not key:
            continue
        if not overwrite and key in os.environ:
            continue

        os.environ[key] = value


def primary_env_file() -> Path:
    """File the local UI writes shared settings to."""
    return Path(SHARED_ENV_FILE)


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Merge key=value updates into an env file, preserving comments and order."""
    if not updates:
        return

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    remaining = dict(updates)
    new_lines: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        export_prefix = ""
        parsed = stripped
        if parsed.startswith("export "):
            export_prefix = "export "
            parsed = parsed[7:].strip()

        if "=" in parsed and not parsed.startswith("#"):
            key, _, _ = parsed.partition("=")
            key = key.strip()
            if key in remaining:
                new_lines.append(f"{export_prefix}{key}={remaining.pop(key)}")
                continue

        new_lines.append(raw_line)

    if remaining:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append("# Updated via local test UI")
        for key, value in remaining.items():
            new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def load_env_files(paths: list[str | Path] | None = None) -> None:
    env_paths = [Path(path) for path in paths] if paths else _resolve_env_files()
    if not env_paths:
        return
    _load_env_file(env_paths[0], overwrite=False)
    for env_path in env_paths[1:]:
        _load_env_file(env_path, overwrite=True)


def load_env_file(path: str | Path | None = None) -> None:
    if path is None:
        load_env_files()
        return
    _load_env_file(Path(path))


def reload_env_files(paths: list[str | Path] | None = None) -> None:
    env_paths = [Path(path) for path in paths] if paths else _resolve_env_files()
    for env_path in env_paths:
        _load_env_file(env_path, overwrite=True)


load_env_files()