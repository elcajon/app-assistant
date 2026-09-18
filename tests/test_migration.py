"""Regression tests exercise failure paths, real jq and real filesystem copies."""

import copy
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

LIB = Path(__file__).resolve().parents[1] / "app-assistant/rootfs/usr/lib/app-assistant"
sys.path.insert(0, str(LIB))
import migrate
import plans
import supervisor
import transfer


@pytest.fixture
def plan():
    return plans.parse(
        {
            "source": {"slug": "local_old"},
            "target": {"slug": "local_new", "repository": "https://example.com/repo"},
        },
        "user",
        "example",
    )


@pytest.fixture
def engine(monkeypatch, tmp_path, plan):
    monkeypatch.setattr(migrate, "JOURNAL", tmp_path / "journal")
    monkeypatch.setattr(migrate, "_active", None)
    state = {
        slug: dict(
            version="1.0.0",
            state="started",
            boot="auto",
            watchdog=True,
            auto_update=True,
            options={"secret": "hidden"},
            schema={"secret": "str"},
        )
        for slug in ("local_old", "local_new")
    }
    monkeypatch.setattr(
        supervisor, "addon_info", lambda slug: copy.deepcopy(state.get(slug))
    )
    monkeypatch.setattr(supervisor, "self_info", lambda: {"protected": False})
    monkeypatch.setattr(migrate.dockerapi, "available", lambda: True)
    monkeypatch.setattr(
        supervisor, "repositories", lambda: [{"source": plan.target.repository}]
    )
    monkeypatch.setattr(
        supervisor,
        "store_addon",
        lambda slug: dict(state[slug], version_latest="1.0.0"),
    )
    monkeypatch.setattr(
        supervisor, "set_options", lambda slug, payload: state[slug].update(payload)
    )
    monkeypatch.setattr(
        supervisor, "stop", lambda slug: state[slug].update(state="stopped")
    )
    monkeypatch.setattr(
        supervisor, "start", lambda slug: state[slug].update(state="started")
    )
    monkeypatch.setattr(supervisor, "validate_options", lambda *args: None)
    monkeypatch.setattr(
        supervisor, "partial_backup", lambda name, slug: {"job_id": slug}
    )
    monkeypatch.setattr(
        supervisor, "job", lambda slug: {"done": True, "reference": slug, "errors": []}
    )
    monkeypatch.setattr(
        supervisor, "backup_info", lambda slug: {"addons": [{"slug": slug}]}
    )
    monkeypatch.setattr(supervisor, "uninstall", lambda slug: state.pop(slug))
    record = dict(
        id="a" * 32,
        plan=plan.id,
        fingerprint=migrate.fingerprint(plan),
        state="running",
        completed=[],
        current=None,
        backups={},
        events=[],
        uncertain=None,
    )
    job = migrate.Job(plan, record)
    return state, job


def test_reject_shell_and_path_slugs(plan):
    for slug in [
        "local_x'; touch /tmp/evil; '",
        "local_../../root",
        "local_x/y",
        "local_",
    ]:
        with pytest.raises(plans.PlanError):
            plans.parse(
                {"source": {"slug": slug}, "target": vars(plan.target)},
                "user",
                "example",
            )


def test_jq_conversion_and_values_never_previewed(plan, engine):
    plan.options = {
        "jq": ".secret = (.secret | ascii_upcase) | .nested = {enabled: true}"
    }
    mapped, dropped = migrate.map_options({"secret": "hidden"}, plan, None)
    assert mapped == {"secret": "HIDDEN", "nested": {"enabled": True}}
    assert not dropped
    plan.options = {}
    assert "hidden" not in json.dumps(migrate.preview(plan))


@pytest.mark.parametrize("query", ["[]", "., .", 'error("secret value")'])
def test_invalid_jq_fails_without_values(plan, query):
    plan.options = {"jq": query}
    with pytest.raises(migrate.MigrationError) as error:
        migrate.map_options({"secret": "secret value"}, plan, None)
    assert "secret value" not in str(error.value)


def test_backup_missing_receipt_blocks(engine, monkeypatch):
    _, job = engine
    monkeypatch.setattr(supervisor, "partial_backup", lambda *args: {})
    with pytest.raises(migrate.MigrationError):
        job.backup("local_old")
    assert job.record["backups"]["local_old"] == {"pending": True}


def test_nested_backup_failure_blocks(engine, monkeypatch):
    _, job = engine
    monkeypatch.setattr(
        supervisor,
        "job",
        lambda _: {"done": True, "child_jobs": [{"errors": ["failed"]}]},
    )
    with pytest.raises(migrate.MigrationError):
        job.backup("local_old")


def test_wrong_backup_contents_blocks(engine, monkeypatch):
    _, job = engine
    monkeypatch.setattr(supervisor, "backup_info", lambda _: {"addons": []})
    with pytest.raises(migrate.MigrationError):
        job.backup("local_old")


def test_backup_timeout_resumes_same_job(engine, monkeypatch):
    _, job = engine
    start = Mock(return_value={"job_id": "local_old"})
    monkeypatch.setattr(supervisor, "partial_backup", start)
    monkeypatch.setattr(supervisor, "job", lambda _: {"done": False})
    ticks = iter([0, 1801])
    monkeypatch.setattr(migrate.time, "monotonic", lambda: next(ticks))
    with pytest.raises(migrate.MigrationError):
        job.backup("local_old")
    monkeypatch.setattr(migrate.time, "monotonic", lambda: 0)
    monkeypatch.setattr(
        supervisor, "job", lambda _: {"done": True, "reference": "local_old"}
    )
    job.backup("local_old")
    assert start.call_count == 1


def test_running_target_never_copied(engine, monkeypatch):
    _, job = engine
    execute = Mock()
    monkeypatch.setattr(migrate.dockerapi, "exec_in_supervisor", execute)
    with pytest.raises(migrate.MigrationError):
        job.data_copy()
    execute.assert_not_called()


def test_complete_workflow_and_resume_does_not_recopy(engine, monkeypatch, plan):
    state, job = engine
    calls = []

    def execute(command):
        assert all(
            i["state"] == "stopped" and not i["watchdog"] for i in state.values()
        )
        assert len(job.record["backups"]) == 2
        calls.append(command)
        return 0, "/retained"

    monkeypatch.setattr(migrate.dockerapi, "exec_in_supervisor", execute)
    job.run()
    assert job.record["state"] == "awaiting_confirmation"
    assert state["local_old"]["boot"] == "manual"
    assert state["local_new"]["state"] == "started"
    state["local_new"]["options"] = {"new": "data"}
    job.run()  # Journal checkpoints prevent re-copy even if worker is retried.
    assert len(calls) == 1 and state["local_new"]["options"] == {"new": "data"}
    with pytest.raises(migrate.MigrationError):
        migrate.start(plan)
    migrate.finish(plan)
    assert "local_old" in state
    assert migrate.read_job(plan.id)["state"] == "complete"


def test_update_is_after_verified_backup(engine, monkeypatch):
    state, job = engine
    state["local_new"]["update_available"] = True

    def update(slug):
        assert job.record["backups"][slug]["verified"]
        assert state[slug]["state"] == "stopped"
        state[slug].update(version="2.0.0", update_available=False)

    monkeypatch.setattr(supervisor, "update", update)
    monkeypatch.setattr(
        migrate.dockerapi, "exec_in_supervisor", lambda _: (0, "/retained")
    )
    job.run()
    assert job.record["target_version"] == "2.0.0"


def test_unexpected_error_persisted_as_failure(engine, monkeypatch):
    _, job = engine
    monkeypatch.setattr(job, "preflight", Mock(side_effect=ValueError("secret")))
    job.run()
    saved = migrate.read_job(job.plan.id)
    assert saved["state"] == "failed"
    assert "secret" not in json.dumps(saved)


def test_source_removal_requires_completion(engine, plan):
    _, job = engine
    job.save()
    with pytest.raises(migrate.MigrationError):
        migrate.finish(plan, True)


def test_listing_errors_not_absence(monkeypatch):
    monkeypatch.setattr(
        supervisor, "call", Mock(side_effect=supervisor.SupervisorError("denied", 403))
    )
    with pytest.raises(supervisor.SupervisorError):
        supervisor.addon_info("local_old")


@pytest.fixture
def filesystem(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer.os, "sync", lambda: None)
    source, target = tmp_path / "local_old", tmp_path / "local_new"
    source.mkdir()
    target.mkdir()
    (source / "data").write_bytes(b"new state")
    (source / ".hidden").write_text("hidden")
    (source / "link").symlink_to("data")
    (target / "data").write_bytes(b"original state")
    return tmp_path, source, target


def test_staged_copy_verified_and_original_retained(filesystem):
    root, source, target = filesystem
    old = transfer.transfer(root, source.name, target.name, "a" * 32)
    assert transfer.manifest(source) == transfer.manifest(target)
    assert (Path(old) / "data").read_bytes() == b"original state"
    (target / "data").write_bytes(b"changed by app")
    with pytest.raises(RuntimeError):
        transfer.transfer(root, source.name, target.name, "a" * 32)
    assert (target / "data").read_bytes() == b"changed by app"


def test_failed_copy_preserves_target(filesystem, monkeypatch):
    root, source, target = filesystem
    monkeypatch.setattr(
        transfer.shutil, "copytree", Mock(side_effect=OSError("disk full"))
    )
    with pytest.raises(OSError):
        transfer.transfer(root, source.name, target.name, "a" * 32)
    assert (target / "data").read_bytes() == b"original state"


def test_resume_between_directory_renames(filesystem, monkeypatch):
    root, source, target = filesystem
    rename = Path.rename

    def interrupt(path, destination):
        if path.name.endswith("-stage"):
            raise OSError("power loss")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", interrupt)
    with pytest.raises(OSError):
        transfer.transfer(root, source.name, target.name, "a" * 32)
    assert not target.exists()
    monkeypatch.setattr(Path, "rename", rename)
    transfer.transfer(root, source.name, target.name, "a" * 32)
    assert transfer.manifest(source) == transfer.manifest(target)


def test_symlink_boundary_refused(filesystem):
    root, source, target = filesystem
    target.rename(root / "actual")
    target.symlink_to(root / "actual", target_is_directory=True)
    with pytest.raises(RuntimeError):
        transfer.transfer(root, source.name, target.name, "a" * 32)


def test_changed_plan_cannot_resume(engine, plan):
    _, job = engine
    job.record["state"] = "failed"
    job.save()
    plan.options = {"defaults": {"changed": True}}
    with pytest.raises(migrate.MigrationError, match="Plan changed"):
        migrate.start(plan, resume=True)


def test_running_journal_becomes_interrupted(engine):
    _, job = engine
    job.save()
    assert migrate.read_job(job.plan.id)["state"] == "interrupted"


def test_jq_does_not_receive_supervisor_token(plan, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "do-not-leak")
    plan.options = {"jq": "{token: env.SUPERVISOR_TOKEN}"}
    mapped, _ = migrate.map_options({}, plan, None)
    assert mapped["token"] is None


def test_unconfirmed_mutation_cannot_repeat(engine, monkeypatch):
    _, job = engine
    job.record["uncertain"] = "target_update"
    update = Mock()
    monkeypatch.setattr(supervisor, "update", update)
    job.run()
    assert job.record["state"] == "failed"
    update.assert_not_called()


def test_live_transfer_lock_blocks_second_copy(filesystem):
    import fcntl

    root, source, target = filesystem
    with (root / (".assistant-" + "a" * 32 + ".lock")).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            transfer.transfer(root, source.name, target.name, "a" * 32)
    assert (target / "data").read_bytes() == b"original state"


def test_absent_target_installed_before_backup(engine, monkeypatch):
    state, job = engine
    template = state.pop("local_new")

    def install(slug):
        state[slug] = dict(template, state="stopped")

    monkeypatch.setattr(supervisor, "install", install)
    monkeypatch.setattr(
        migrate.dockerapi, "exec_in_supervisor", lambda _: (0, "/retained")
    )
    job.run()
    assert job.record["state"] == "awaiting_confirmation"
    assert job.record["target_existed"] is False
    assert job.record["backups"]["local_new"]["verified"] is True


def test_numeric_version_bounds_and_prerelease_rejection():
    assert migrate.version_tuple("1.0") == migrate.version_tuple("v1.0.0")
    with pytest.raises(migrate.MigrationError):
        migrate.version_tuple("1.0.0-beta1")
