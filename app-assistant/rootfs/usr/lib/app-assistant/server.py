"""Ingress-only UI with bounded JSON requests and durable job polling."""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import migrate
import plans

WWW = Path("/usr/share/app-assistant/www")
PORT = 8099
CSRF = secrets.token_urlsafe(32)
MAX_BODY = 16384


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        logging.debug(fmt, *args)

    def send(self, status, body, kind="application/json"):
        if kind == "application/json":
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; base-uri 'self'; object-src 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        # Never trust a forwarded address supplied by another app.
        if self.client_address[0] != "172.30.32.2":
            self.send(
                403, {"error": "Open App Assistant through Home Assistant ingress"}
            )
            return False
        return True

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/healthz" and self.client_address[0] in (
            "127.0.0.1",
            "172.30.32.2",
        ):
            self.send(200, b"ok", "text/plain")
            return
        if not self.allowed():
            return
        try:
            if route == "/api/state":
                loaded, errors = plans.load_all()
                self.send(
                    200,
                    {
                        "csrf": CSRF,
                        "environment": migrate.environment(),
                        "plans": [migrate.describe(p) for p in loaded],
                        "errors": errors,
                        "jobs": migrate.all_jobs(),
                    },
                )
            elif route == "/" or route == "/index.html" or route.startswith("/static/"):
                name = "index.html" if route in ("/", "/index.html") else route[8:]
                path = (WWW / name).resolve()
                if WWW.resolve() not in path.parents or not path.is_file():
                    self.send(404, {"error": "Not found"})
                    return
                kind = {
                    ".html": "text/html; charset=utf-8",
                    ".js": "text/javascript",
                    ".css": "text/css",
                }.get(path.suffix, "application/octet-stream")
                self.send(200, path.read_bytes(), kind)
            else:
                self.send(404, {"error": "Not found"})
        except Exception:
            self.send(
                503,
                {
                    "error": "Cannot read Supervisor or migration journal; retry without starting a new migration"
                },
            )

    def do_POST(self):
        if not self.allowed():
            return
        if not hmac.compare_digest(self.headers.get("X-App-CSRF", ""), CSRF):
            self.send(403, {"error": "Reload the page before continuing"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (
                not 0 < length <= MAX_BODY
                or self.headers.get("Content-Type") != "application/json"
            ):
                raise ValueError()
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or not isinstance(
                payload.get("plan"), str
            ):
                raise ValueError()
            loaded, _ = plans.load_all()
            plan = next((p for p in loaded if p.id == payload["plan"]), None)
            if plan is None:
                self.send(404, {"error": "Unknown migration plan"})
                return
            route = urlparse(self.path).path
            if route == "/api/migrate":
                if (
                    payload.get("confirmed") is not True
                    or type(payload.get("resume", False)) is not bool
                ):
                    raise ValueError()
                result = migrate.start(plan, payload.get("resume", False))
            elif route == "/api/finish":
                if (
                    payload.get("confirmed") is not True
                    or type(payload.get("remove", False)) is not bool
                ):
                    raise ValueError()
                result = migrate.finish(plan, payload.get("remove", False))
            else:
                self.send(404, {"error": "Not found"})
                return
            self.send(200, {"job": result})
        except (ValueError, TypeError):
            self.close_connection = True
            self.send(400, {"error": "Invalid request"})
        except migrate.MigrationError as error:
            self.send(409, {"error": str(error)})
        except Exception:
            self.send(
                503,
                {
                    "error": "Operation could not be confirmed. Reload and inspect the existing migration."
                },
            )


def main():
    levels = {
        "trace": logging.DEBUG,
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "notice": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "fatal": logging.CRITICAL,
    }
    logging.basicConfig(
        level=levels.get(os.environ.get("LOG_LEVEL", "info"), logging.INFO)
    )
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
