"""Durable, fail-closed migration workflow with explicit final confirmation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

import dockerapi
import supervisor

JOURNAL = Path("/data/migrations")
STEPS = [
    ("preflight", "Check environment and versions"),
    ("target_repository", "Add target repository"),
    ("target_install", "Install target if missing"),
    ("target_quiesce", "Disable automatic starts and stop target"),
    ("target_backup", "Verify target backup"),
    ("target_update", "Update target if necessary"),
    ("options_validate", "Validate transformed options"),
    ("source_quiesce", "Disable automatic starts and stop source"),
    ("source_backup", "Verify source backup"),
    ("options_copy", "Copy configuration"),
    ("data_copy", "Stage, verify and replace internal data"),
    ("target_start", "Start target"),
]
_lock = threading.RLock()
_active = None


class MigrationError(Exception):
    pass


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+(?:\.\d+){0,3}", value):
        raise MigrationError("Unrecognised version; use a stable supported app release")
    return tuple(int(x) for x in value.lstrip("v").split(".")) + (0,) * (
        4 - len(value.lstrip("v").split("."))
    )


def check_version(value, endpoint):
    if endpoint.min_version and version_tuple(value) < version_tuple(
        endpoint.min_version
    ):
        raise MigrationError("Installed version is below the plan minimum")
    if endpoint.max_version and version_tuple(value) > version_tuple(
        endpoint.max_version
    ):
        raise MigrationError("Installed version is above the plan maximum")


def fingerprint(plan):
    return hashlib.sha256(json.dumps(asdict(plan), sort_keys=True).encode()).hexdigest()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def environment():
    info = supervisor.self_info()
    docker = dockerapi.available()
    return dict(
        supervisor=True,
        protected=info.get("protected", True),
        docker=docker,
        ready=not info.get("protected", True) and docker,
    )


def map_options(options, plan, schema):
    mapped = {
        plan.options.get("rename", {}).get(k, k): v
        for k, v in options.items()
        if k not in plan.options.get("remove", [])
    }
    remaining = [k for k in options if k not in plan.options.get("remove", [])]
    if len(mapped) != len(remaining):
        raise MigrationError(
            "Option renames collide; resolve the plan before migrating"
        )
    for key, value in plan.options.get("defaults", {}).items():
        mapped.setdefault(key, value)
    if "jq" in plan.options:
        # No shell; output goes to a bounded file, errors never expose option values.
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("jq_runner.py")),
                    plan.options["jq"],
                ],
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            )
            try:
                process.communicate(json.dumps(mapped).encode(), timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise MigrationError(
                    "Option transformation exceeded five seconds"
                ) from None
            if process.returncode or output.tell() > 1024 * 1024:
                raise MigrationError(
                    "Option transformation failed or produced too much output"
                )
            output.seek(0)
            try:
                mapped = json.load(output)
            except ValueError:
                raise MigrationError("jq must return exactly one JSON object") from None
        if not isinstance(mapped, dict):
            raise MigrationError("jq must return exactly one JSON object")
    if schema is None:
        return mapped, []
    if not isinstance(schema, dict):
        raise MigrationError("Unexpected target schema")
    dropped = sorted(set(mapped) - set(schema))
    return {k: v for k, v in mapped.items() if k in schema}, dropped


def required_missing(options, schema, prefix=""):
    missing = []
    for key, spec in (schema or {}).items():
        name = f"{prefix}{key}"
        if key not in options:
            if not isinstance(spec, str) or not spec.endswith("?"):
                missing.append(name)
        elif isinstance(spec, dict) and isinstance(options[key], dict):
            missing.extend(required_missing(options[key], spec, name + "."))
    return missing


def preview(plan):
    source = supervisor.addon_info(plan.source.slug)
    if source is None:
        raise MigrationError("Source is not installed")
    check_version(source.get("version"), plan.source)
    target = supervisor.addon_info(plan.target.slug)
    store = supervisor.store_addon(plan.target.slug)
    schema = (store or target or {}).get("schema")
    mapped, dropped = map_options(source.get("options", {}), plan, schema)
    if target and store is None:
        raise MigrationError("Target store metadata is unavailable")
    missing = required_missing(mapped, schema) if schema is not None else []
    result = plan.as_dict()
    result.update(
        source_state={k: source.get(k) for k in ("version", "state")},
        target_state={k: (target or {}).get(k) for k in ("version", "state")},
        target_exists=target is not None,
        update_available=bool((target or {}).get("update_available")),
        target_version=(store or {}).get("version_latest")
        or (store or {}).get("version"),
        schema_known=store is not None or target is not None,
        changed=sorted(
            k for k in mapped if source.get("options", {}).get(k) != mapped[k]
        ),
        removed=sorted(set(source.get("options", {})) - set(mapped)),
        dropped=dropped,
        missing=missing,
        steps=[
            {"id": k, "label": v}
            for k, v in STEPS
            if not (k == "target_install" and target)
        ],
        job=read_job(plan.id),
    )
    result["steps"] = [{"id": k, "label": v} for k, v in STEPS]
    return result


def describe(plan):
    try:
        return dict(preview(plan), blocked=None)
    except (MigrationError, supervisor.SupervisorError) as error:
        return dict(
            plan.as_dict(),
            blocked=str(error),
            job=read_job(plan.id),
            steps=[{"id": k, "label": v} for k, v in STEPS],
        )


def read_job(plan_id):
    path = JOURNAL / f"{plan_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data["state"] == "running" and (_active is None or _active.id != data["id"]):
        data["state"] = "interrupted"
    return data


def all_jobs():
    return [read_job(p.stem) for p in JOURNAL.glob("*.json")]


def job_errors(job):
    return bool(job.get("errors")) or any(
        job_errors(child) for child in job.get("child_jobs", [])
    )


class Job:
    def __init__(self, plan, record):
        self.plan, self.record = plan, record
        self.id = record["id"]

    def save(self):
        atomic_write(JOURNAL / f"{self.plan.id}.json", self.record)

    def log(self, message):
        self.record["events"].append({"time": time.time(), "message": message})
        self.save()

    def info(self, slug):
        result = supervisor.addon_info(slug)
        if result is None:
            raise MigrationError("Required app is no longer installed")
        return result

    def quiesce(self, slug):
        supervisor.set_options(
            slug, {"boot": "manual", "watchdog": False, "auto_update": False}
        )
        if self.info(slug).get("state") != "stopped":
            supervisor.stop(slug)
        if self.info(slug).get("state") != "stopped":
            raise MigrationError("App did not stop; no data will be copied")

    def assert_stopped(self):
        for slug in (self.plan.source.slug, self.plan.target.slug):
            info = self.info(slug)
            if (
                info.get("state") != "stopped"
                or info.get("watchdog")
                or info.get("auto_update")
                or info.get("boot") != "manual"
            ):
                raise MigrationError(
                    "Both apps must remain stopped with automatic starts disabled"
                )

    def backup(self, slug):
        receipt = self.record["backups"].get(slug)
        if receipt is None:
            # Save intent first: an interrupted request must never create a second backup blindly.
            self.record["backups"][slug] = {"pending": True}
            self.save()
            receipt = supervisor.partial_backup(
                f"App Assistant {self.id}: {slug}", slug
            )
            if not isinstance(receipt, dict) or not (
                receipt.get("job_id") or receipt.get("slug")
            ):
                raise MigrationError(
                    "Backup was not confirmed; inspect Supervisor before recovery"
                )
            self.record["backups"][slug] = receipt
            self.save()
        if receipt.get("pending"):
            raise MigrationError(
                "Backup request outcome is unknown; manual recovery required"
            )
        backup_slug = receipt.get("slug")
        if receipt.get("job_id"):
            deadline = time.monotonic() + 1800
            while True:
                status = supervisor.job(receipt["job_id"])
                if job_errors(status):
                    raise MigrationError(
                        "Backup job failed; inspect Supervisor backup logs"
                    )
                if status.get("done") is True:
                    backup_slug = backup_slug or status.get("reference")
                    break
                if time.monotonic() >= deadline:
                    raise MigrationError(
                        "Backup timeout; resume will check the same job"
                    )
                time.sleep(2)
        if not isinstance(backup_slug, str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]+", backup_slug
        ):
            raise MigrationError("Backup finished without an identifiable backup")
        info = supervisor.backup_info(backup_slug)
        apps = info.get("addons", [])
        if not any(
            (item.get("slug") if isinstance(item, dict) else item) == slug
            for item in apps
        ):
            raise MigrationError("Verified backup does not contain the expected app")
        receipt["slug"] = backup_slug
        receipt["verified"] = True
        self.save()

    def run(self):
        global _active
        try:
            if not environment()["ready"]:
                raise MigrationError("Supervisor/Docker access is required")
            if self.record.get("uncertain"):
                raise MigrationError(
                    "Previous mutation outcome is unknown; inspect Supervisor and recover manually"
                )
            for step, label in STEPS:
                if step in self.record["completed"]:
                    continue
                self.record["current"] = step
                self.save()
                getattr(self, step)()
                self.record["completed"].append(step)
                self.record["current"] = None
                self.log(label + ": complete")
            self.record["state"] = "awaiting_confirmation"
        except Exception as error:
            # Do not log exception values: malformed options can contain credentials.
            self.record["state"] = "failed"
            self.record["error"] = (
                str(error)
                if isinstance(error, MigrationError)
                else f"{type(error).__name__}: operation failed; inspect Supervisor logs"
            )
            self.record["events"].append(
                {"time": time.time(), "message": self.record["error"]}
            )
        finally:
            try:
                self.save()
            finally:
                with _lock:
                    _active = None

    def preflight(self):
        source = self.info(self.plan.source.slug)
        check_version(source.get("version"), self.plan.source)
        target = supervisor.addon_info(self.plan.target.slug)
        self.record["original"] = {
            name: {
                k: (info or {}).get(k)
                for k in ("state", "boot", "watchdog", "auto_update", "version")
            }
            for name, info in [("source", source), ("target", target)]
        }
        self.record["target_existed"] = target is not None
        self.save()

    def mutate(self, callback):
        self.record["uncertain"] = self.record["current"]
        self.save()
        callback()
        self.record["uncertain"] = None
        self.save()

    def target_repository(self):
        url = self.plan.target.repository.rstrip("/")
        if url not in {
            str(r.get("source", "")).rstrip("/") for r in supervisor.repositories()
        }:
            self.mutate(lambda: supervisor.add_repository(url))

    def target_install(self):
        if supervisor.addon_info(self.plan.target.slug) is None:
            self.mutate(lambda: supervisor.install(self.plan.target.slug))
        self.info(self.plan.target.slug)

    def target_quiesce(self):
        self.quiesce(self.plan.target.slug)

    def target_backup(self):
        self.backup(self.plan.target.slug)

    def target_update(self):
        info = self.info(self.plan.target.slug)
        if info.get("update_available"):
            self.mutate(lambda: supervisor.update(self.plan.target.slug))
        self.quiesce(self.plan.target.slug)
        info = self.info(self.plan.target.slug)
        check_version(info.get("version"), self.plan.target)
        self.record["target_version"] = info.get("version")
        self.save()

    def transformed(self):
        source, target = (
            self.info(self.plan.source.slug),
            self.info(self.plan.target.slug),
        )
        if target.get("version") != self.record.get("target_version"):
            raise MigrationError("Target version changed during migration")
        options, dropped = map_options(
            source.get("options", {}), self.plan, target.get("schema")
        )
        if dropped:
            raise MigrationError(
                "Unknown target options: amend the plan with explicit remove/rename rules"
            )
        supervisor.validate_options(self.plan.target.slug, options)
        return options

    def options_validate(self):
        self.transformed()

    def source_quiesce(self):
        self.quiesce(self.plan.source.slug)

    def source_backup(self):
        self.backup(self.plan.source.slug)

    def options_copy(self):
        self.assert_stopped()
        supervisor.set_options(self.plan.target.slug, {"options": self.transformed()})

    def data_copy(self):
        self.assert_stopped()
        if any(
            not self.record["backups"].get(slug, {}).get("verified")
            for slug in (self.plan.source.slug, self.plan.target.slug)
        ):
            raise MigrationError("Both backups must be verified")
        script = Path(__file__).with_name("transfer.py").read_text()
        code, output = dockerapi.exec_in_supervisor(
            [
                "python3",
                "-c",
                script,
                self.plan.source.slug,
                self.plan.target.slug,
                self.id,
            ]
        )
        if code:
            raise MigrationError(
                "Data transfer was not confirmed; original target is retained. Inspect Supervisor."
            )
        self.record["retained_data"] = output.strip()
        self.save()

    def target_start(self):
        # Resume must never stop a target after it has begun using migrated data.
        source = self.info(self.plan.source.slug)
        if (
            source.get("state") != "stopped"
            or source.get("boot") != "manual"
            or source.get("watchdog")
        ):
            raise MigrationError("Source must remain disabled before target starts")
        if self.info(self.plan.target.slug).get("state") != "started":
            supervisor.start(self.plan.target.slug)
        if self.info(self.plan.target.slug).get("state") != "started":
            raise MigrationError("Target did not reach started state")


def start(plan, resume=False):
    global _active
    with _lock:
        if _active is not None:
            raise MigrationError("Another operation is running")
        record = read_job(plan.id)
        if any(j["plan"] != plan.id and j["state"] != "complete" for j in all_jobs()):
            raise MigrationError(
                "Resolve the existing migration before starting another"
            )
        if record:
            if not resume or record["state"] not in ("failed", "interrupted"):
                raise MigrationError(
                    "Existing migration cannot be overwritten or repeated"
                )
            if record["fingerprint"] != fingerprint(plan):
                raise MigrationError(
                    "Plan changed; restore the original plan before resuming"
                )
        else:
            if resume:
                raise MigrationError("No interrupted migration exists")
            check = preview(plan)
            if check["dropped"] or check["missing"]:
                raise MigrationError(
                    "Resolve unknown or missing options before migration"
                )
            record = dict(
                id=uuid.uuid4().hex,
                plan=plan.id,
                fingerprint=fingerprint(plan),
                completed=[],
                current=None,
                backups={},
                events=[],
                uncertain=None,
            )
        record["state"], record["error"] = "running", None
        job = Job(plan, record)
        job.save()
        _active = job
        threading.Thread(target=job.run, daemon=True).start()
        return record


def finish(plan, remove=False):
    with _lock:
        if _active is not None:
            raise MigrationError("An operation is running")
        record = read_job(plan.id)
        if not record or record["state"] != "awaiting_confirmation":
            raise MigrationError("Migration is not ready for confirmation")
        if record["fingerprint"] != fingerprint(plan):
            raise MigrationError(
                "Plan changed; restore the original plan before confirming"
            )
        job = Job(plan, record)
        if job.info(plan.target.slug).get("state") != "started":
            raise MigrationError("Target is not running")
        # Completion does not silently enable watchdog or updates.
        source = record["original"]["source"]
        target = job.info(plan.target.slug)
        if source.get("boot") == "auto" and target.get("boot_config") != "manual_only":
            supervisor.set_options(plan.target.slug, {"boot": "auto"})
        if remove and supervisor.addon_info(plan.source.slug) is not None:
            supervisor.uninstall(plan.source.slug)
        record["state"] = "complete"
        record["source_removed"] = remove
        job.log("User confirmed target functionality. Migration complete.")
        return record
