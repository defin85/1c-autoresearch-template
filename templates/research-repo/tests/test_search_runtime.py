from __future__ import annotations

import time
from pathlib import Path

import pytest

from one_c_autoresearch import search_runtime
from one_c_autoresearch.search_runtime import (
    ADDRESS_SPACE_BYTES,
    Admission,
    BackendLifecycle,
    Session,
    broker_specs,
    create_serving_workspace,
    private_environment,
    process_identity,
    process_identity_alive,
    reconcile_restart,
    verify_promoted_unchanged,
)


def test_private_environment_and_fixed_required_broker_specs(tmp_path: Path) -> None:
    serving = tmp_path / "serving"
    runtime = tmp_path / "runtime"
    env = private_environment(serving, runtime, {
        "PATH": "/bin",
        "HOME": "/host",
        "HTTPS_PROXY": "http://proxy",
        "UPSTREAM_TOKEN": "secret",
        "EMBEDDING_URL": "http://127.0.0.1:1",
    })
    assert env["HOME"] == str(serving.resolve())
    assert env["XDG_RUNTIME_DIR"] == str(runtime.resolve())
    assert "HTTPS_PROXY" not in env
    assert "UPSTREAM_TOKEN" not in env
    assert env["EMBEDDING_URL"] == "http://127.0.0.1:1"
    specs = broker_specs(Path("/bin/bsl-analyzer"), tmp_path / "src", 42, env)
    assert specs["daemon"]["command"][-2:] == ["--mode", "daemon"]
    assert specs["daemon"]["address_space_bytes"] == ADDRESS_SPACE_BYTES
    assert specs["proxy"]["command"][-4:] == [
        "--mode",
        "broker-required",
        "--backend-pid",
        "42",
    ]
    assert specs["proxy"]["auto_launch"] is False
    assert specs["proxy"]["stdio_fallback"] is False


def test_idle_ttl_may_only_narrow(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid_broker_limits"):
        broker_specs(Path("/bin/tool"), tmp_path, 1, {}, idle_ttl_seconds=301)


def test_serving_workspace_is_private_copy_and_promoted_remains_immutable(
    tmp_path: Path,
) -> None:
    promoted = tmp_path / "instances" / "index"
    promoted.mkdir(parents=True)
    (promoted / "index.db").write_bytes(b"canonical")
    serving, digest = create_serving_workspace(promoted, tmp_path / "serving", "serve-1")
    (serving / "index.db").write_bytes(b"derived")
    assert (promoted / "index.db").read_bytes() == b"canonical"
    verify_promoted_unchanged(promoted, digest)
    assert serving.stat().st_mode & 0o077 == 0


def test_serving_workspace_rejects_symlinks(tmp_path: Path) -> None:
    promoted = tmp_path / "promoted"
    promoted.mkdir()
    (promoted / "link").symlink_to("/tmp")
    with pytest.raises(ValueError, match="symlink_forbidden"):
        create_serving_workspace(promoted, tmp_path / "serving", "serve")


def test_process_identity_binds_pid_group_start_and_boot() -> None:
    identity = process_identity(__import__("os").getpid())
    assert set(identity) == {"pid", "process_group", "start_time", "boot_id"}
    assert process_identity_alive(identity)
    stale = dict(identity)
    stale["start_time"] = "0"
    assert not process_identity_alive(stale)


def test_resident_reuse_is_target_and_project_bound() -> None:
    admission = Admission()
    deadline = time.monotonic() + 1
    with admission.resident("project-a", "target-a", deadline):
        with pytest.raises(ValueError, match="cross_project_reuse_forbidden"):
            with admission.resident("project-b", "target-a", deadline):
                pass


def test_call_queue_wait_consumes_original_deadline() -> None:
    admission = Admission()
    deadline = time.monotonic() + 1
    contexts = [admission.call("project", deadline) for _ in range(4)]
    for context in contexts:
        context.__enter__()
    try:
        with pytest.raises(TimeoutError, match="admission_deadline"):
            with admission.call("project", time.monotonic()):
                pass
    finally:
        for context in reversed(contexts):
            context.__exit__(None, None, None)


def test_backend_start_failure_releases_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    promoted = tmp_path / "instances" / "index" / "source"
    promoted.mkdir(parents=True)
    admission = Admission()
    monkeypatch.setattr(search_runtime, "_ADMISSION", admission)
    monkeypatch.setattr(
        search_runtime,
        "launch_backend",
        lambda _spec: (_ for _ in ()).throw(RuntimeError("failed")),
    )

    with pytest.raises(RuntimeError, match="failed"):
        with search_runtime.supervised_workspace_proxy(
            Path("/bin/bsl-analyzer"),
            promoted,
            {},
            timeout_seconds=1,
        ):
            pass

    assert admission._residents == {}
    assert admission._calls == {}


def test_project_shutdown_only_removes_matching_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped: list[int] = []
    monkeypatch.setattr(
        search_runtime,
        "terminate_process_group",
        lambda identity: stopped.append(identity["pid"]) or "terminated",
    )
    monkeypatch.setattr(search_runtime, "_BACKENDS", {
        "a": {
            "project": "project-a", "identity": {"pid": 1},
            "owned_resource": None, "serving_root": str(tmp_path / "a"),
        },
        "b": {
            "project": "project-b", "identity": {"pid": 2},
            "owned_resource": None, "serving_root": str(tmp_path / "b"),
        },
    })

    assert search_runtime.shutdown_project_backends("project-a") == 1
    assert stopped == [1]
    assert set(search_runtime._BACKENDS) == {"b"}


def test_cancelled_session_rejects_late_output_and_settles_once() -> None:
    first = Session()
    peer = Session()
    assert first.cancel()
    assert not first.settle("late")
    assert peer.settle("ok")
    assert not peer.settle("duplicate")
    assert peer.result == "ok"


def test_lifecycle_reports_idle_orphan_and_supersession() -> None:
    orphan = BackendLifecycle("target-a", started_at=0)
    assert orphan.observe(30) == "orphan_expired"
    assert orphan.diagnostic()["terminal_cause"] == "orphan_grace"

    backend = BackendLifecycle("target-b", started_at=0, idle_ttl_seconds=10)
    backend.traffic(1)
    assert backend.observe(2) == "idle"
    assert backend.observe(11) == "idle_expired"

    replaced = BackendLifecycle("target-c", started_at=0)
    replaced.supersede()
    assert replaced.diagnostic()["state"] == "superseded"
    with pytest.raises(RuntimeError, match="target_admission_closed"):
        replaced.session_opened()


def test_restart_quarantines_partial_state_and_replays_nothing(tmp_path: Path) -> None:
    (tmp_path / "staging" / "partial").mkdir(parents=True)
    (tmp_path / "serving" / "orphan").mkdir(parents=True)
    (tmp_path / "instances" / "current").mkdir(parents=True)
    result = reconcile_restart(tmp_path, None)
    assert len(result["quarantined"]) == 2
    assert result["replayed_requests"] == 0
    assert result["promoted_instances"] == 0
    assert (tmp_path / "instances" / "current").is_dir()
