import json
from pathlib import Path

import pytest

from one_c_autoresearch.service import ApplicationService, backend_state


UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_backend_state_omits_null_optional_identities() -> None:
    assert backend_state({
        "status": "missing",
        "target_fingerprint": None,
        "embedding_identity": None,
        "reference_identity": None,
    }) == {"status": "missing"}


def _status() -> dict:
    return {
        "schema_version": "1",
        "state": "complete",
        "workflow_fingerprint": "sha256:workflow",
        "gates": [
            {"id": name, "state": "complete", "blockers": []}
            for name in (
                "project-configured",
                "sources-acquired",
                "diffs-built",
                "all-dif-classified",
                "mrq-consolidated",
                "source-evidence-complete",
                "decisions-approved",
                "published",
            )
        ],
    }


def _scope() -> dict:
    blocker = {
        "code": "extension_scope_required",
        "uuid": UUID,
        "roles": {"target_cf": {"present": True}},
        "action": "review_extension_scope",
    }
    return {"ready": False, "blockers": [blocker]}


def test_unreviewed_live_extension_blocks_current_readiness_but_not_old_generation_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("one_c_autoresearch.workflow.validate_workflow", lambda _repo: {})
    monkeypatch.setattr("one_c_autoresearch.workflow.status", lambda _repo, deep=True: _status())
    monkeypatch.setattr("one_c_autoresearch.workflow.state_fingerprint", lambda _repo: "sha256:workflow")
    monkeypatch.setattr("one_c_autoresearch.sources.extension_scope_status", lambda *_args: _scope())
    monkeypatch.setattr("one_c_autoresearch.workflow_migration.guard_mutation", lambda _repo: None)
    service = ApplicationService(tmp_path, rlm_executable="rlm", connections={"profile": {}})

    snapshot = service.snapshot()
    assert snapshot["gates"][1]["blockers"][0]["uuid"] == UUID
    assert snapshot["gates"][2]["blockers"][0]["code"] == "predecessor.blocked"
    assert service.next()["blocker"]["uuid"] == UUID

    with pytest.raises(RuntimeError) as error:
        service.apply("diff.build", {}, "sha256:workflow")
    assert json.loads(str(error.value))["blockers"][0]["uuid"] == UUID
