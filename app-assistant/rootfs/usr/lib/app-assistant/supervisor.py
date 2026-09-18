"""Minimal Supervisor API client, built on the standard library only."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

API = "http://supervisor"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")


class SupervisorError(Exception):
    """Raised when the Supervisor refuses a call."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def call(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 300,
) -> Any:
    """Call the Supervisor API and return the ``data`` part of the answer."""
    data = None
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{API}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as err:
        try:
            body = json.loads(err.read().decode() or "{}")
        except ValueError:
            body = {}
        raise SupervisorError(f"{method} {path}: HTTP {err.code}", err.code) from err
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        raise SupervisorError(
            f"{method} {path}: transport or invalid response"
        ) from err

    if not isinstance(body, dict):
        raise SupervisorError("Invalid Supervisor response")
    if body.get("result") == "error":
        raise SupervisorError(
            f"{method} {path}: Supervisor rejected the request; check its log"
        )
    return body.get("data", {})


def ping() -> bool:
    """Return whether the Supervisor answers."""
    try:
        call("GET", "/supervisor/ping", timeout=10)
    except SupervisorError:
        return False
    return True


def self_info() -> dict[str, Any]:
    """Return the info of this app."""
    return call("GET", "/addons/self/info", timeout=30)


def store() -> dict[str, Any]:
    """Return the full store contents."""
    return call("GET", "/store", timeout=60)


def store_addon(slug: str) -> dict[str, Any] | None:
    """Determine absence from a successful listing, never from a generic error."""
    entries = store().get("addons")
    if not isinstance(entries, list):
        raise SupervisorError("Invalid store listing")
    if not any(item.get("slug") == slug for item in entries):
        return None
    return call("GET", f"/store/addons/{slug}", timeout=30)


def addon_info(slug: str) -> dict[str, Any] | None:
    entries = call("GET", "/addons", timeout=30).get("addons")
    if not isinstance(entries, list):
        raise SupervisorError("Invalid installed-app listing")
    if not any(item.get("slug") == slug for item in entries):
        return None
    return call("GET", f"/addons/{slug}/info", timeout=30)


def repositories() -> list[dict[str, Any]]:
    """Return the configured store repositories."""
    return store().get("repositories", [])


def add_repository(url: str) -> None:
    """Add a store repository."""
    call("POST", "/store/repositories", {"repository": url})


def install(slug: str) -> None:
    """Install an app from the store."""
    call("POST", f"/store/addons/{slug}/install", timeout=1800)


def uninstall(slug: str) -> None:
    """Uninstall an app."""
    call("POST", f"/addons/{slug}/uninstall", timeout=600)


def start(slug: str) -> None:
    """Start an app."""
    call("POST", f"/addons/{slug}/start", timeout=600)


def stop(slug: str) -> None:
    """Stop an app."""
    call("POST", f"/addons/{slug}/stop", timeout=600)


def set_options(slug: str, payload: dict[str, Any]) -> None:
    """Write options, boot mode or watchdog of an app."""
    call("POST", f"/addons/{slug}/options", payload, timeout=120)


def partial_backup(name: str, slug: str) -> dict[str, Any]:
    """Start a partial backup of a single app and return the job id."""
    data = call(
        "POST",
        "/backups/new/partial",
        {
            "name": name,
            "addons": [slug],
            "homeassistant": False,
            "background": True,
        },
        timeout=120,
    )
    return data


def job(uuid: str) -> dict[str, Any]:
    """Return the state of a background job."""
    return call("GET", f"/jobs/{uuid}", timeout=30)


def update(slug: str) -> None:
    """Update only after the engine has confirmed a backup."""
    call("POST", f"/store/addons/{slug}/update", {"backup": False}, timeout=1800)


def validate_options(slug: str, options: dict) -> None:
    result = call("POST", f"/addons/{slug}/options/validate", options)
    if result.get("valid") is not True:
        raise SupervisorError(
            "Target options are invalid; review the plan and required values"
        )


def backup_info(slug: str) -> dict:
    return call("GET", f"/backups/{slug}/info", timeout=30)
