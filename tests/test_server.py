"""HTTP contract tests without opening a socket."""

import io
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[1]
        / "app-assistant/rootfs/usr/lib/app-assistant"
    ),
)
import plans
import server


def request(monkeypatch, address="172.30.32.2", payload=None, csrf=None):
    handler = object.__new__(server.Handler)
    handler.client_address = (address, 1234)
    handler.path = "/api/migrate"
    body = json.dumps(payload).encode()
    handler.headers = {
        "Content-Length": str(len(body)),
        "Content-Type": "application/json",
        "X-App-CSRF": csrf or server.CSRF,
    }
    handler.rfile = io.BytesIO(body)
    handler.send = Mock()
    handler.close_connection = False
    return handler


def test_direct_access_rejected(monkeypatch):
    handler = request(monkeypatch, address="172.30.33.10")
    handler.do_POST()
    assert handler.send.call_args.args[0] == 403


def test_missing_csrf_rejected(monkeypatch):
    handler = request(monkeypatch, csrf="wrong")
    handler.do_POST()
    assert handler.send.call_args.args[0] == 403


@pytest.mark.parametrize(
    "payload",
    [[], None, {"plan": 1}, {"plan": "x", "resume": "false", "confirmed": True}],
)
def test_bad_payload_does_not_start(monkeypatch, payload):
    plan = plans.parse(
        {
            "source": {"slug": "local_old"},
            "target": {"slug": "local_new", "repository": "https://example.com/r"},
        },
        "user",
        "x",
    )
    monkeypatch.setattr(server.plans, "load_all", lambda: ([plan], []))
    start = Mock()
    monkeypatch.setattr(server.migrate, "start", start)
    handler = request(monkeypatch, payload=payload)
    handler.do_POST()
    assert handler.send.call_args.args[0] == 400
    start.assert_not_called()
