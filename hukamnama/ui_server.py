#!/usr/bin/env python3
"""Local web UI to test and send daily hukamnama."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from hukamnama import REPO_ROOT
from hukamnama.paths import ensure_service_env, resolve_service_path

ensure_service_env()
from shared.env_loader import primary_env_file, reload_env_files, update_env_file
import shared.env_loader  # noqa: F401 — load config.env + .env at startup

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
HOST = os.environ.get("UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("UI_PORT", "8765"))
API_VERSION = 2

EDITABLE_ENV_KEYS = {
    "hukamnama_source": "HUKAMNAMA_SOURCE",
    "send_audio": "SEND_AUDIO",
    "send_katha_audio": "SEND_KATHA_AUDIO",
    "dry_run_env": "DRY_RUN",
    "skip_sent_check": "SKIP_SENT_CHECK",
    "include_punjabi": "INCLUDE_PUNJABI",
    "include_hindi": "INCLUDE_HINDI",
    "include_english": "INCLUDE_ENGLISH",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes"}


def _load_groups() -> list[dict[str, str]]:
    groups_file = resolve_service_path(os.environ.get("WHATSAPP_GROUPS_FILE", "whatsapp_groups.json"))
    if not groups_file.exists():
        return []
    payload = json.loads(groups_file.read_text(encoding="utf-8"))
    return [
        {
            "name": group.get("name", "unknown"),
            "chat_id": group.get("chat_id", ""),
        }
        for group in payload.get("groups", [])
        if group.get("enabled") and group.get("chat_id")
    ]


def _config_payload() -> dict:
    send_audio = os.environ.get("SEND_AUDIO", "").strip()
    if send_audio:
        audio_on = _env_bool("SEND_AUDIO", False)
    else:
        audio_on = _env_bool(
            "INCLUDE_HUKAMNAMA_AUDIO",
            _env_bool("INCLUDE_AUDIO", False),
        )

    katha_raw = os.environ.get("SEND_KATHA_AUDIO", "").strip()
    if katha_raw:
        katha_on = _env_bool("SEND_KATHA_AUDIO", False)
    else:
        katha_on = _env_bool("INCLUDE_KATHA_AUDIO", False)

    env_file = primary_env_file()
    try:
        env_file_display = str(env_file.relative_to(REPO_ROOT))
    except ValueError:
        env_file_display = str(env_file)
    return {
        "delivery_method": os.environ.get("DELIVERY_METHOD", "whatsapp"),
        "hukamnama_source": os.environ.get("HUKAMNAMA_SOURCE", "gurbaninow"),
        "send_audio": audio_on,
        "send_katha_audio": katha_on,
        "dry_run_env": _env_bool("DRY_RUN", False),
        "skip_sent_check": _env_bool("SKIP_SENT_CHECK", True),
        "include_punjabi": _env_bool("INCLUDE_PUNJABI", True),
        "include_hindi": _env_bool("INCLUDE_HINDI", True),
        "include_english": _env_bool("INCLUDE_ENGLISH", True),
        "groups": _load_groups(),
        "env_file": env_file_display,
        "editable_keys": list(EDITABLE_ENV_KEYS.keys()),
        "api_version": API_VERSION,
        "supports_save": True,
    }


def _bool_to_env(name: str, value: bool) -> str:
    if name in {"DRY_RUN", "SKIP_SENT_CHECK"}:
        return "1" if value else "0"
    return "true" if value else "false"


def _save_config(payload: dict) -> dict:
    source = str(payload.get("hukamnama_source", "gurbaninow")).strip().lower()
    if source not in {"gurbaninow", "sgpc"}:
        raise ValueError("hukamnama_source must be 'gurbaninow' or 'sgpc'")

    updates: dict[str, str] = {
        "HUKAMNAMA_SOURCE": source,
        "SEND_AUDIO": _bool_to_env("SEND_AUDIO", bool(payload.get("send_audio", False))),
        "SEND_KATHA_AUDIO": _bool_to_env(
            "SEND_KATHA_AUDIO",
            bool(payload.get("send_katha_audio", False)),
        ),
        "DRY_RUN": _bool_to_env("DRY_RUN", bool(payload.get("dry_run_env", False))),
        "SKIP_SENT_CHECK": _bool_to_env(
            "SKIP_SENT_CHECK",
            bool(payload.get("skip_sent_check", True)),
        ),
        "INCLUDE_PUNJABI": _bool_to_env(
            "INCLUDE_PUNJABI",
            bool(payload.get("include_punjabi", True)),
        ),
        "INCLUDE_HINDI": _bool_to_env(
            "INCLUDE_HINDI",
            bool(payload.get("include_hindi", True)),
        ),
        "INCLUDE_ENGLISH": _bool_to_env(
            "INCLUDE_ENGLISH",
            bool(payload.get("include_english", True)),
        ),
    }

    env_path = primary_env_file()
    update_env_file(env_path, updates)
    reload_env_files()
    return _config_payload()


def _image_dir() -> Path:
    return resolve_service_path(os.environ.get("IMAGE_OUTPUT_DIR", ".generated-images"))


def _latest_preview_images() -> list[dict[str, str]]:
    image_dir = _image_dir()
    if not image_dir.exists():
        return []

    images = sorted(image_dir.glob("hukamnama-*.png"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not images:
        return []

    latest_mtime = images[0].stat().st_mtime
    batch = [path for path in images if latest_mtime - path.stat().st_mtime < 10]
    split_batch = [
        path
        for path in batch
        if re.match(r"hukamnama-\d{4}-\d{2}-\d{2}-\d{2}-", path.name)
    ]
    if split_batch:
        batch = split_batch
    batch.sort(key=lambda path: path.name)

    labels = {
        "hukamnama": "Hukamnama",
        "punjabi-viakhya": "Punjabi Viakhya",
        "hindi-viakhya": "Hindi Viakhya",
        "english": "English",
    }
    items: list[dict[str, str]] = []
    total = len(batch)
    for path in batch:
        match = re.match(r"hukamnama-(\d{4}-\d{2}-\d{2})-(\d{2})-(.+)", path.stem)
        if match:
            part_no = int(match.group(2))
            part_key = match.group(3)
            title = labels.get(part_key, part_key.replace("-", " ").title())
            label = f"Part {part_no}/{total} — {title}"
        else:
            label = path.stem.replace("hukamnama-", "").replace("-", " ").title()
        items.append(
            {
                "name": path.name,
                "label": label,
                "url": f"/api/preview/image/{path.name}",
            }
        )
    return items


def _latest_preview_path() -> Path | None:
    images = _latest_preview_images()
    if not images:
        return None
    return _image_dir() / images[0]["name"]


def _resolve_image_file(filename: str) -> Path | None:
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return None
    if not filename.lower().endswith(".png"):
        return None

    path = (_image_dir() / filename).resolve()
    image_root = _image_dir().resolve()
    if image_root not in path.parents:
        return None
    if path.exists() and path.is_file():
        return path
    return None


def _audio_dir() -> Path:
    return resolve_service_path(os.environ.get("AUDIO_OUTPUT_DIR", ".generated-audio"))


def _latest_audio_files() -> list[dict[str, str]]:
    audio_dir = _audio_dir()
    if not audio_dir.exists():
        return []

    files = sorted(audio_dir.glob("*.mp3"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        return []

    latest_mtime = files[0].stat().st_mtime
    batch = [path for path in files if latest_mtime - path.stat().st_mtime < 10]
    batch.sort(key=lambda path: path.name)

    items: list[dict[str, str]] = []
    for path in batch:
        label = "Katha audio" if "katha" in path.name.lower() else "Hukamnama audio"
        items.append(
            {
                "name": path.name,
                "label": label,
                "url": f"/api/preview/audio/{path.name}",
            }
        )
    return items


def _resolve_audio_file(filename: str) -> Path | None:
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return None
    if not filename.lower().endswith(".mp3"):
        return None

    path = (_audio_dir() / filename).resolve()
    audio_root = _audio_dir().resolve()
    if audio_root not in path.parents:
        return None
    if path.exists() and path.is_file():
        return path
    return None


def _run_timeout_seconds(*, dry_run: bool, send_katha_audio: bool) -> int:
    if dry_run:
        return 300
    if send_katha_audio:
        return 1800
    return 600


def _run_hukamnama(
    *,
    dry_run: bool,
    skip_sent_check: bool,
    send_audio: bool,
    send_katha_audio: bool,
) -> dict:
    env = os.environ.copy()
    env["DRY_RUN"] = "1" if dry_run else "0"
    env["SKIP_SENT_CHECK"] = _bool_to_env("SKIP_SENT_CHECK", skip_sent_check)
    env["SEND_AUDIO"] = _bool_to_env("SEND_AUDIO", send_audio)
    env["SEND_KATHA_AUDIO"] = _bool_to_env("SEND_KATHA_AUDIO", send_katha_audio)
    env["SERVICE_DIR"] = ROOT.name
    cmd = [sys.executable, "-m", "hukamnama.send"]
    cmd.append("--skip-sent-check" if skip_sent_check else "--enforce-sent-check")
    run_timeout = _run_timeout_seconds(dry_run=dry_run, send_katha_audio=send_katha_audio)
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=run_timeout,
        check=False,
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    skipped_already_sent = "already sent. Skipping." in stdout
    ok = result.returncode == 0 and not skipped_already_sent
    preview_images = _latest_preview_images() if ok else []
    preview = _latest_preview_path()
    audio_files = _latest_audio_files() if ok else []
    return {
        "ok": ok,
        "stdout": stdout,
        "stderr": stderr,
        "skipped_already_sent": skipped_already_sent,
        "run_meta": {
            "dry_run": dry_run,
            "skip_sent_check": skip_sent_check,
            "send_audio": send_audio,
            "send_katha_audio": send_katha_audio,
            "timeout_seconds": run_timeout,
            "cmd": cmd,
        },
        "api_version": API_VERSION,
        "preview_path": str(preview) if preview else "",
        "preview_url": preview_images[0]["url"] if preview_images else "",
        "preview_images": preview_images,
        "audio_files": audio_files,
    }


class HukamnamaUIHandler(BaseHTTPRequestHandler):
    server_version = f"HukamnamaUI/{API_VERSION}"

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        sys.stdout.write("%s - %s\n" % (self.address_string(), format % args))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path in {"/", "/index.html"}:
            html = (UI_DIR / "index.html").read_bytes()
            self._send_bytes(html, content_type="text/html; charset=utf-8")
            return

        if path == "/api/config":
            self._send_json(_config_payload())
            return

        if path == "/api/preview/latest":
            preview = _latest_preview_path()
            if not preview or not preview.exists():
                self._send_json({"error": "No preview image found."}, HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(preview.name)[0] or "image/png"
            self._send_bytes(preview.read_bytes(), content_type=content_type)
            return

        if path.startswith("/api/preview/image/"):
            filename = path.removeprefix("/api/preview/image/").strip()
            image_path = _resolve_image_file(filename)
            if not image_path:
                self._send_json({"error": "Image file not found."}, HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
            self._send_bytes(image_path.read_bytes(), content_type=content_type)
            return

        if path.startswith("/api/preview/audio/"):
            filename = path.removeprefix("/api/preview/audio/").strip()
            audio_path = _resolve_audio_file(filename)
            if not audio_path:
                self._send_json({"error": "Audio file not found."}, HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(audio_path.name)[0] or "audio/mpeg"
            self._send_bytes(audio_path.read_bytes(), content_type=content_type)
            return

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON body"}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/config":
            try:
                config = _save_config(payload)
                self._send_json({"ok": True, "config": config})
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path == "/api/run":
            try:
                dry_run = bool(payload.get("dry_run", True))
                skip_sent_check = bool(payload.get("skip_sent_check", True))
                send_audio = bool(payload.get("send_audio", True))
                send_katha_audio = bool(payload.get("send_katha_audio", False))
                result = _run_hukamnama(
                    dry_run=dry_run,
                    skip_sent_check=skip_sent_check,
                    send_audio=send_audio,
                    send_katha_audio=send_katha_audio,
                )
                self._send_json(result, HTTPStatus.OK if result["ok"] else HTTPStatus.BAD_REQUEST)
            except subprocess.TimeoutExpired:
                timeout_seconds = _run_timeout_seconds(
                    dry_run=bool(payload.get("dry_run", True)),
                    send_katha_audio=bool(payload.get("send_katha_audio", False)),
                )
                minutes = max(1, timeout_seconds // 60)
                self._send_json(
                    {"error": f"Run timed out after {minutes} minutes"},
                    HTTPStatus.GATEWAY_TIMEOUT,
                )
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)


def main() -> int:
    if not (UI_DIR / "index.html").exists():
        print(f"Missing UI file: {UI_DIR / 'index.html'}", file=sys.stderr)
        return 1

    url = f"http://{HOST}:{PORT}"
    try:
        server = ThreadingHTTPServer((HOST, PORT), HukamnamaUIHandler)
    except OSError as exc:
        if exc.errno in {48, 98, 10048}:  # macOS, Linux, Windows: address already in use
            print(
                f"Port {PORT} is already in use — another hukamnama UI server is probably running.\n"
                f"Open {url} in your browser, or stop the other process first (Ctrl+C in its terminal).",
                file=sys.stderr,
            )
        else:
            print(f"Failed to start server on {url}: {exc}", file=sys.stderr)
        return 1

    print(f"Hukamnama test UI running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())