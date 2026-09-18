"""The migration engine.

Every step checks the current state before it acts, so a migration that failed
half way can simply be started again and ends in the same state.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import dockerapi
import plans as plans_module
import supervisor

DATA_ROOT = "/data/addons/data"

STEPS: list[tuple[str, str]] = [
    ("preflight", "Check the environment"),
    ("target_repository", "Add the repository of the new app"),
    ("target_install", "Install the new app"),
    ("target_backup", "Back up the new app"),
    ("source_stop", "Stop the old app"),
    ("source_backup", "Back up the old app"),
    ("options_copy", "Copy the configuration"),
    ("data_copy", "Copy the internal data"),
    ("source_disable", "Set the old app to manual start"),
    ("target_start", "Start the new app"),
    ("source_uninstall", "Uninstall the old app"),
]

OPTIONAL_STEPS = {
    "target_backup": True,
    "source_backup": True,
    "target_start": True,
    "source_uninstall": False,
}


class MigrationError(Exception):
    """Raised when a step cannot finish."""


def version_tuple(version: str) -> tuple[int, ...]:
    """Turn a version string into something comparable."""
    return tuple(int(part) for part in re.findall(r"\d+", version)[:4]) or (0,)


def data_dir(slug: str) -> str:
    """Return the path of an app's data folder inside the Supervisor."""
    return f"{DATA_ROOT}/{slug}"


def environment() -> dict[str, Any]:
    """Describe the environment the migration would run in."""
    info = supervisor.self_info() if supervisor.ping() else {}
    protected = bool(info.get("protected", True))
    docker = dockerapi.available()
    return {
        "supervisor": bool(info),
        "protected": protected,
        "docker": docker,
        "ready": bool(info) and not protected and docker,
    }


def describe(plan: plans_module.Plan) -> dict[str, Any]:
    """Return the plan plus the current state of both apps."""
    source = supervisor.addon_info(plan.source.slug)
    target = supervisor.addon_info(plan.target.slug)
    store_target = supervisor.store_addon(plan.target.slug)

    blocked = None
    if source is None:
        blocked = f"The old app {plan.source.slug} is not installed"
    elif plan.source.min_version and version_tuple(
        source.get("version", "0")
    ) < version_tuple(plan.source.min_version):
        blocked = (
            f"The old app has to be updated to at least "
            f"version {plan.source.min_version}"
        )
    elif plan.source.max_version and version_tuple(
        source.get("version", "0")
    ) > version_tuple(plan.source.max_version):
        blocked = (
            f"The old app is newer than version {plan.source.max_version}, "
            "which this plan does not know"
        )

    data = plan.as_dict()
    data["blocked"] = blocked
    data["source_state"] = {
        "installed": source is not None,
        "version": (source or {}).get("version", ""),
        "state": (source or {}).get("state", ""),
    }
    data["target_state"] = {
        "installed": target is not None,
        "version": (target or {}).get("version", ""),
        "state": (target or {}).get("state", ""),
        "available": store_target is not None,
    }
    return data


def map_options(
    options: dict[str, Any], plan: plans_module.Plan, schema: Any
) -> tuple[dict[str, Any], list[str]]:
    """Map the options of the old app onto the new one.

    Returns the mapped options and the list of options that were left out,
    because the new app does not know them.
    """
    rename: dict[str, str] = plan.options.get("rename", {})
    remove: list[str] = plan.options.get("remove", [])
    defaults: dict[str, Any] = plan.options.get("defaults", {})

    mapped = {
        rename.get(key, key): value
        for key, value in options.items()
        if key not in remove
    }
    for key, value in defaults.items():
        mapped.setdefault(key, value)

    if not isinstance(schema, dict):
        return mapped, []

    known = set(schema)
    dropped = sorted(key for key in mapped if key not in known)
    return {key: value for key, value in mapped.items() if key in known}, dropped


@dataclass
class Job:
    """A single migration run."""

    plan: plans_module.Plan
    choices: dict[str, bool]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    state: str = "running"
    events: list[dict[str, Any]] = field(default_factory=list)
    _condition: threading.Condition = field(
        default_factory=threading.Condition, repr=False
    )

    # -- event plumbing ----------------------------------------------------

    def emit(self, **event: Any) -> None:
        """Append an event and wake up everybody waiting for it."""
        with self._condition:
            event["seq"] = len(self.events)
            event["time"] = time.time()
            self.events.append(event)
            self._condition.notify_all()

    def log(self, message: str, level: str = "info") -> None:
        """Write a line to the migration log."""
        self.emit(type="log", level=level, message=message)

    def follow(self, start: int, timeout: float = 20.0):
        """Yield events from ``start`` on, blocking until there are new ones."""
        index = start
        while True:
            with self._condition:
                if index >= len(self.events):
                    if self.state != "running":
                        return
                    self._condition.wait(timeout)
                    if index >= len(self.events):
                        yield {"type": "ping", "seq": index}
                        continue
                event = self.events[index]
            index += 1
            yield event

    # -- step plumbing -----------------------------------------------------

    def enabled(self, step: str) -> bool:
        """Return whether an optional step was chosen by the user."""
        if step not in OPTIONAL_STEPS:
            return True
        return bool(self.choices.get(step, OPTIONAL_STEPS[step]))

    def run(self) -> None:
        """Run all steps in order."""
        self.emit(type="state", state="running")
        self.log(f"Migrating {self.plan.source.slug} to {self.plan.target.slug}")

        for step, label in STEPS:
            if not self.enabled(step):
                self.emit(
                    type="step", step=step, status="skipped", message="Not selected"
                )
                continue

            self.emit(type="step", step=step, status="running", message=label)
            try:
                note = getattr(self, f"_step_{step}")()
            except (
                MigrationError,
                supervisor.SupervisorError,
                dockerapi.DockerError,
            ) as err:
                self.emit(type="step", step=step, status="failed", message=str(err))
                self.log(str(err), level="error")
                self.state = "failed"
                self.emit(type="state", state="failed")
                self.log(
                    "The migration stopped. Fix the problem and start it again, "
                    "the steps that already succeeded are skipped."
                )
                return
            if note == "skipped":
                self.emit(
                    type="step", step=step, status="skipped", message="Already done"
                )
            else:
                self.emit(type="step", step=step, status="done", message=note or label)

        self.state = "done"
        self.emit(type="state", state="done")
        self.log("The migration finished.")

    # -- the steps themselves ----------------------------------------------

    def _step_preflight(self) -> str:
        env = environment()
        if not env["supervisor"]:
            raise MigrationError("The Supervisor is not reachable")
        if env["protected"]:
            raise MigrationError(
                "Protection mode is enabled. Turn it off in the app info panel "
                "and restart this app."
            )
        if not env["docker"]:
            raise MigrationError("The Docker socket is not reachable")

        source = supervisor.addon_info(self.plan.source.slug)
        if source is None:
            raise MigrationError(f"{self.plan.source.slug} is not installed")

        version = source.get("version", "0")
        if self.plan.source.min_version and version_tuple(version) < version_tuple(
            self.plan.source.min_version
        ):
            raise MigrationError(
                f"{self.plan.source.slug} {version} is older than the supported "
                f"version {self.plan.source.min_version}"
            )
        if self.plan.source.max_version and version_tuple(version) > version_tuple(
            self.plan.source.max_version
        ):
            raise MigrationError(
                f"{self.plan.source.slug} {version} is newer than the supported "
                f"version {self.plan.source.max_version}"
            )

        code, output = dockerapi.exec_in_supervisor(f"test -d '{DATA_ROOT}'")
        if code != 0:
            raise MigrationError(
                f"The Supervisor has no {DATA_ROOT} folder, the internal data "
                f"of an app cannot be copied. {output.strip()}"
            )
        return f"{self.plan.source.slug} {version} can be migrated"

    def _step_target_repository(self) -> str:
        url = self.plan.target.repository.rstrip("/")
        known = {
            str(repository.get("source", "")).rstrip("/")
            for repository in supervisor.repositories()
        }
        if url in known:
            return "skipped"
        supervisor.add_repository(url)
        self.log(f"Added the repository {url}")
        return f"Added {url}"

    def _step_target_install(self) -> str:
        if supervisor.addon_info(self.plan.target.slug) is not None:
            return "skipped"
        if supervisor.store_addon(self.plan.target.slug) is None:
            raise MigrationError(
                f"{self.plan.target.slug} is not in the store, check the "
                "repository of the plan"
            )
        self.log(f"Installing {self.plan.target.slug}, this can take a while...")
        supervisor.install(self.plan.target.slug)
        return f"Installed {self.plan.target.slug}"

    def _step_target_backup(self) -> str:
        info = supervisor.addon_info(self.plan.target.slug)
        if info is None:
            return "skipped"
        return self._backup(self.plan.target.slug)

    def _step_source_stop(self) -> str:
        info = supervisor.addon_info(self.plan.source.slug)
        if info is None:
            raise MigrationError(f"{self.plan.source.slug} is not installed")
        if info.get("state") != "started":
            return "skipped"
        supervisor.stop(self.plan.source.slug)
        return f"Stopped {self.plan.source.slug}"

    def _step_source_backup(self) -> str:
        return self._backup(self.plan.source.slug)

    def _step_options_copy(self) -> str:
        source = supervisor.addon_info(self.plan.source.slug)
        if source is None:
            raise MigrationError(f"{self.plan.source.slug} is not installed")

        store_target = supervisor.store_addon(self.plan.target.slug) or {}
        mapped, dropped = map_options(
            source.get("options") or {}, self.plan, store_target.get("schema")
        )
        for key in dropped:
            self.log(
                f"The option '{key}' is unknown to {self.plan.target.slug} "
                "and was not copied",
                level="warning",
            )
        supervisor.set_options(self.plan.target.slug, {"options": mapped})
        return f"Copied {len(mapped)} option(s)"

    def _step_data_copy(self) -> str:
        source = data_dir(self.plan.source.slug)
        target = data_dir(self.plan.target.slug)
        command = (
            f"set -e; "
            f"test -d '{source}'; "
            f"mkdir -p '{target}'; "
            f"rm -rf '{target}'/* '{target}'/.[!.]* '{target}'/..?* 2>/dev/null || true; "
            f"cp -a '{source}'/. '{target}'/"
        )
        code, output = dockerapi.exec_in_supervisor(command)
        if code != 0:
            raise MigrationError(
                f"Copying {source} to {target} failed: {output.strip() or code}"
            )
        return "Copied the internal data of the app"

    def _step_source_disable(self) -> str:
        info = supervisor.addon_info(self.plan.source.slug)
        if info is None:
            return "skipped"
        if info.get("boot") == "manual" and info.get("watchdog") is False:
            return "skipped"
        supervisor.set_options(
            self.plan.source.slug, {"boot": "manual", "watchdog": False}
        )
        return f"{self.plan.source.slug} will no longer start on its own"

    def _step_target_start(self) -> str:
        info = supervisor.addon_info(self.plan.target.slug)
        if info is None:
            raise MigrationError(f"{self.plan.target.slug} is not installed")
        if info.get("state") == "started":
            return "skipped"
        supervisor.start(self.plan.target.slug)
        self.log(
            f"Check the log of {self.plan.target.slug} before you remove the old app."
        )
        return f"Started {self.plan.target.slug}"

    def _step_source_uninstall(self) -> str:
        if supervisor.addon_info(self.plan.source.slug) is None:
            return "skipped"
        supervisor.uninstall(self.plan.source.slug)
        return f"Uninstalled {self.plan.source.slug}"

    # -- helpers -----------------------------------------------------------

    def _backup(self, slug: str) -> str:
        name = f"Before migration: {slug}"
        self.log(f"Backing up {slug}, this can take a while...")
        job_id = supervisor.partial_backup(name, slug)
        if not job_id:
            return f"Backed up {slug}"

        while True:
            state = supervisor.job(job_id)
            if state.get("done"):
                errors = state.get("errors") or []
                if errors:
                    messages = ", ".join(
                        str(error.get("message", error)) for error in errors
                    )
                    raise MigrationError(f"The backup of {slug} failed: {messages}")
                return f"Backed up {slug}"
            time.sleep(2)


_current: Job | None = None
_lock = threading.Lock()


def current() -> Job | None:
    """Return the running or last finished job."""
    return _current


def start(plan: plans_module.Plan, choices: dict[str, bool]) -> Job:
    """Start a migration, unless one is already running."""
    global _current  # noqa: PLW0603
    with _lock:
        if _current is not None and _current.state == "running":
            raise MigrationError("A migration is already running")
        job = Job(plan=plan, choices=choices)
        _current = job

    thread = threading.Thread(target=job.run, name=f"migration-{job.id}", daemon=True)
    thread.start()
    return job
