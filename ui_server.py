#!/usr/bin/env python3
"""Local web UI to test and send daily hukamnama."""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import env_loader  # noqa: F401 — load .env files at startup

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
HOST = os.environ.get("UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("UI_PORT", "8765"))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes"}


def _load_groups() -> list[dict[str, str]]:
    groups_file = Path(os.environ.get("WHATSAPP_GROUPS_FILE", "whatsapp_groups.json"))
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

    return {
        "delivery_method": os.environ.get("DELIVERY_METHOD", "whatsapp"),
        "hukamnama_source": os.environ.get("HUKAMNAMA_SOURCE", "gurbaninow"),
        "send_audio": audio_on,
        "send_katha_audio": katha_on,
        "dry_run_env": _env_bool("DRY_RUN", False),
        "include_punjabi": _env_bool("INCLUDE_PUNJABI", True),
        "include_hindi": _env_bool("INCLUDE_HINDI", True),
        "include_english": _env_bool("INCLUDE_ENGLISH", True),
        "groups": _load_groups(),
    }


def _latest_preview_path() -> Path | None:
    image_dir = Path(os.environ.get("IMAGE_OUTPUT_DIR", ".generated-images"))
    if not image_dir.exists():
        return None
    images = sorted(image_dir.glob("hukamnama-*.png"), key=lambda path: path.stat().st_mtime)
    return images[-1] if images else None


def _run_hukamnama(*, dry_run: bool) -> dict:
    env = os.environ.copy()
    env["DRY_RUN"] = "1" if dry_run else "0"
    result = subprocess.run(
        [sys.executable, str(ROOT / "send_hukamnama.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    preview = _latest_preview_path()
    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "preview_path": str(preview) if preview else "",
        "preview_url": "/api/preview/latest" if preview else "",
    }


class HukamnamaUIHandler(BaseHTTPRequestHandler):
    server_version = "HukamnamaUI/1.0"

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

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/run":
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json_body()
            dry_run = bool(payload.get("dry_run", True))
            result = _run_hukamnama(dry_run=dry_run)
            self._send_json(result, HTTPStatus.OK if result["ok"] else HTTPStatus.BAD_REQUEST)
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON body"}, HTTPStatus.BAD_REQUEST)
        except subprocess.TimeoutExpired:
            self._send_json({"error": "Run timed out after 5 minutes"}, HTTPStatus.GATEWAY_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> int:
    if not (UI_DIR / "index.html").exists():
        print(f"Missing UI file: {UI_DIR / 'index.html'}", file=sys.stderr)
        return 1

    server = ThreadingHTTPServer((HOST, PORT), HukamnamaUIHandler)
    url = f"http://{HOST}:{PORT}"
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