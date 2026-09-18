"""Executed with Python inside Supervisor. No shell or user-supplied paths."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path


def manifest(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        entry = [stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]
        if path.is_symlink():
            entry += ["link", os.readlink(path)]
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            entry += ["file", digest.hexdigest()]
        elif path.is_dir():
            entry += ["dir"]
        else:
            raise RuntimeError("Unsupported special file in app data")
        result[str(path.relative_to(root))] = entry
    return result


def preserve_owner(source, target):
    for path in [source, *source.rglob("*")]:
        destination = target / path.relative_to(source)
        info = path.lstat()
        os.chown(destination, info.st_uid, info.st_gid, follow_symlinks=False)
        if not path.is_symlink():
            os.chmod(destination, stat.S_IMODE(info.st_mode))


def _transfer(root: Path, source_slug: str, target_slug: str, job: str):
    for slug in (source_slug, target_slug):
        if not re.fullmatch(
            r"(?:[a-f0-9]{8}|local|core)_[a-z0-9][a-z0-9_-]{0,80}", slug
        ):
            raise ValueError("Invalid app slug")
    if source_slug == target_slug or not re.fullmatch(r"[a-f0-9]{32}", job):
        raise ValueError("Invalid transfer identity")
    source, target = root / source_slug, root / target_slug
    stage = root / f".assistant-{job}-stage"
    previous = root / f".assistant-{job}-previous"
    receipt = root / f".assistant-{job}.json"
    for path in (root, source, target, stage, previous, receipt):
        if path.is_symlink():
            raise RuntimeError("Refusing a symlink at a transfer boundary")
    if not source.is_dir():
        raise RuntimeError("Source data directory is missing")
    if previous.exists():
        if not receipt.is_file():
            raise RuntimeError("Missing transfer receipt; manual recovery required")
        expected = json.loads(receipt.read_text())
        if not target.exists() and stage.is_dir():
            if manifest(stage) != expected:
                raise RuntimeError("Staged data changed; manual recovery required")
            stage.rename(target)
            os.sync()
        if not target.is_dir() or stage.exists() or manifest(target) != expected:
            raise RuntimeError("Target changed after transfer; refusing to overwrite")
        return str(previous)
    if not target.is_dir():
        raise RuntimeError("Target data directory is missing")
    expected = manifest(source)
    required = sum(
        p.lstat().st_size
        for p in source.rglob("*")
        if p.is_file() and not p.is_symlink()
    )
    if shutil.disk_usage(root).free < required + 16 * 1024 * 1024:
        raise RuntimeError("Insufficient space for a staged data copy")
    # Only an incomplete staging directory belonging to this exact job is removed.
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(source, stage, symlinks=True)
    preserve_owner(source, stage)
    if manifest(stage) != expected or manifest(source) != expected:
        raise RuntimeError("Data verification failed; target remains untouched")
    with receipt.open("w") as stream:
        os.chmod(receipt, 0o600)
        json.dump(expected, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.sync()
    target.rename(previous)
    os.sync()
    stage.rename(target)
    os.sync()
    return str(previous)


def transfer(root: Path, source_slug: str, target_slug: str, job: str):
    # A timed-out Docker request may leave its process running in Supervisor.
    # A second request must not remove that process's staging directory.
    if not re.fullmatch(r"[a-f0-9]{32}", job) or root.is_symlink():
        raise ValueError("Invalid transfer identity or root")
    lock = root / f".assistant-{job}.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _transfer(root, source_slug, target_slug, job)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    candidates = [Path("/data/addons/data"), Path("/data/apps/data")]
    roots = [p for p in candidates if p.is_dir() and not p.is_symlink()]
    if len(roots) != 1:
        raise RuntimeError("Expected one unambiguous Supervisor data directory")
    print(transfer(roots[0], *sys.argv[1:]))
