"""Just enough Docker API to run a command inside the Supervisor container.

The Supervisor has no API to read or write the internal data folder of an app,
so the only way to move it is a command inside the Supervisor container, where
that folder lives. This needs ``docker_api`` and a disabled protection mode.
"""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any

SOCKET = "/run/docker.sock"
SUPERVISOR_CONTAINER = "hassio_supervisor"


class DockerError(Exception):
    """Raised when the Docker API is unreachable or refuses a call."""


class _UnixConnection(http.client.HTTPConnection):
    """HTTP connection that talks to the Docker socket."""

    def __init__(self, timeout: int) -> None:
        super().__init__("localhost", timeout=timeout)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET)


def _request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 900,
) -> tuple[int, bytes]:
    """Send a request to the Docker socket and return status and body."""
    connection = _UnixConnection(timeout)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    except (OSError, http.client.HTTPException) as err:
        raise DockerError(str(err)) from err
    finally:
        connection.close()


def available() -> bool:
    """Return whether the Docker socket answers."""
    try:
        status, _ = _request("GET", "/_ping", timeout=10)
    except DockerError:
        return False
    return status == 200


def _demultiplex(raw: bytes) -> str:
    """Turn a multiplexed Docker stream into plain text."""
    out: list[str] = []
    offset = 0
    while offset + 8 <= len(raw):
        if raw[offset] not in (0, 1, 2):
            # Not a multiplexed stream, the rest is plain output.
            break
        size = int.from_bytes(raw[offset + 4 : offset + 8], "big")
        out.append(raw[offset + 8 : offset + 8 + size].decode(errors="replace"))
        offset += 8 + size
    if offset == 0:
        return raw.decode(errors="replace")
    return "".join(out)


def exec_in_supervisor(command: str, timeout: int = 900) -> tuple[int, str]:
    """Run a shell command inside the Supervisor container.

    Returns the exit code and the combined output of the command.
    """
    status, raw = _request(
        "POST",
        f"/containers/{SUPERVISOR_CONTAINER}/exec",
        {
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
            "Cmd": ["/bin/sh", "-c", command],
        },
        timeout=60,
    )
    if status != 201:
        raise DockerError(
            f"Could not create the command in {SUPERVISOR_CONTAINER}: "
            f"HTTP {status} {raw.decode(errors='replace')[:200]}"
        )
    exec_id = json.loads(raw).get("Id")

    status, raw = _request(
        "POST",
        f"/exec/{exec_id}/start",
        {"Detach": False, "Tty": False},
        timeout=timeout,
    )
    if status != 200:
        raise DockerError(
            f"Could not run the command: HTTP {status} "
            f"{raw.decode(errors='replace')[:200]}"
        )
    output = _demultiplex(raw)

    status, raw = _request("GET", f"/exec/{exec_id}/json", timeout=60)
    if status != 200:
        raise DockerError(f"Could not read the command result: HTTP {status}")
    return int(json.loads(raw).get("ExitCode") or 0), output
