"""The web server behind the Home Assistant ingress panel."""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import migrate
import plans as plans_module

WWW = Path("/usr/share/migration-assistant/www")
PORT = 8099

CONTENT_TYPES = {
    ".css": "text/css",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript",
    ".svg": "image/svg+xml",
}

LOG_LEVELS = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
}

_log = logging.getLogger("migration-assistant")


def collect_state() -> dict[str, Any]:
    """Return everything the frontend needs to draw itself."""
    loaded, errors = plans_module.load_all()
    environment = migrate.environment()

    described = []
    for plan in loaded:
        try:
            described.append(migrate.describe(plan))
        except Exception as err:  # noqa: BLE001 - a broken plan must not kill the page
            errors.append(f"{plan.id}: {err}")

    job = migrate.current()
    return {
        "environment": environment,
        "plans": described,
        "errors": errors,
        "steps": [{"id": step, "label": label} for step, label in migrate.STEPS],
        "optional": migrate.OPTIONAL_STEPS,
        "job": (
            {
                "id": job.id,
                "plan": job.plan.id,
                "state": job.state,
                "events": job.events,
            }
            if job
            else None
        ),
    }


class Handler(BaseHTTPRequestHandler):
    """Serves the panel and its small API."""

    server_version = "MigrationAssistant"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        """Send request logs to the app log instead of stderr."""
        _log.debug("%s - %s", self.address_string(), fmt % args)

    # -- answers -----------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _send_file(self, name: str) -> None:
        path = (WWW / name).resolve()
        if not path.is_file() or WWW not in path.parents:
            self._send(404, b"Not found", "text/plain")
            return
        content_type = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self._send(200, path.read_bytes(), content_type)

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - name comes from the base class
        """Serve the panel, the state and the event stream."""
        url = urlparse(self.path)
        route = url.path.rstrip("/") or "/"

        if route in ("/", "/index.html"):
            self._send_file("index.html")
        elif route == "/healthz":
            self._send(200, b"ok", "text/plain")
        elif route.startswith("/static/"):
            self._send_file(route[len("/static/") :])
        elif route == "/api/state":
            self._send_json(200, collect_state())
        elif route == "/api/events":
            self._stream_events(parse_qs(url.query))
        else:
            self._send(404, b"Not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802 - name comes from the base class
        """Start a migration."""
        if urlparse(self.path).path.rstrip("/") != "/api/migrate":
            self._send(404, b"Not found", "text/plain")
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send_json(400, {"error": "The request is not valid JSON"})
            return

        loaded, _ = plans_module.load_all()
        plan = next((item for item in loaded if item.id == payload.get("plan")), None)
        if plan is None:
            self._send_json(404, {"error": "Unknown migration plan"})
            return

        choices = {
            key: bool(value)
            for key, value in (payload.get("choices") or {}).items()
            if key in migrate.OPTIONAL_STEPS
        }

        try:
            job = migrate.start(plan, choices)
        except migrate.MigrationError as err:
            self._send_json(409, {"error": str(err)})
            return

        _log.info("Started migration %s for plan %s", job.id, plan.id)
        self._send_json(200, {"job": job.id})

    # -- event stream ------------------------------------------------------

    def _stream_events(self, query: dict[str, list[str]]) -> None:
        job = migrate.current()
        if job is None:
            self._send_json(404, {"error": "No migration has been started"})
            return

        start = int((query.get("from") or ["0"])[0])

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        try:
            for event in job.follow(start):
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            _log.debug("The event stream was closed by the browser")

    def do_HEAD(self) -> None:  # noqa: N802 - name comes from the base class
        """Answer health checks without a body."""
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> None:
    """Run the web server until the app is stopped."""
    logging.basicConfig(
        level=LOG_LEVELS.get(os.environ.get("LOG_LEVEL", "info"), logging.INFO),
        format="[%(levelname)s] %(message)s",
    )
    _log.info("Listening on port %s", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()
