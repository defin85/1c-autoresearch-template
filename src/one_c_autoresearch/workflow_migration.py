"""Fenced, restart-safe workflow-v3 to workflow-v4 migration."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .contracts import atomic_json, canonical_json, sha256
from .consolidation import fingerprint as consolidation_fingerprint, sentinel as consolidation_sentinel
from .dif_classifications import (
    POINTER as CLASSIFICATION_POINTER,
    coverage,
    make_row,
    publish_empty,
    publish_window,
)
from .sqlite_state import DispatcherStore
from .user_state import agent_profiles_path, state_root, workspace_id


SCHEMA_VERSION = "1"
PHASES = (
    "prepared",
    "legacy-interrupted",
    "classification-published",
    "catalog-switched",
    "committed",
)
POINTER_GLOB = "active-*-generation.json"


def _fingerprint(value: Any) -> str:
    return "sha256:" + sha256(canonical_json(value))


def operational_root(repo: Path, base: Path | None = None) -> Path:
    return state_root(base) / "projects" / workspace_id(repo)


def journal_path(repo: Path, base: Path | None = None) -> Path:
    return operational_root(repo, base) / "migrations/workflow-v3-v4.json"


def _encoded_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "sha256": None, "bytes_base64": None}
    payload = path.read_bytes()
    return {
        "exists": True,
        "sha256": sha256(payload),
        "bytes_base64": base64.b64encode(payload).decode("ascii"),
    }


def _restore_file(path: Path, backup: dict[str, Any]) -> None:
    if not backup["exists"]:
        path.unlink(missing_ok=True)
        return
    payload = base64.b64decode(backup["bytes_base64"], validate=True)
    if sha256(payload) != backup["sha256"]:
        raise ValueError(f"migration backup is corrupt: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _matches_file(path: Path, backup: dict[str, Any]) -> bool:
    return (
        (not backup["exists"] and not path.exists())
        or (
            backup["exists"]
            and path.is_file()
            and sha256(path.read_bytes()) == backup["sha256"]
        )
    )


def _database_backup(store: DispatcherStore, path: Path) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    destination = sqlite3.connect(temporary)
    try:
        store.conn.backup(destination)
    finally:
        destination.close()
    os.replace(temporary, path)
    path.chmod(0o600)
    return {"path": str(path), "sha256": sha256(path.read_bytes())}


def _seed(
    repo: Path,
    store: DispatcherStore,
    migration_id: str,
    repository_fingerprint: str,
    workflow_fingerprint: str,
    base: Path | None,
) -> dict[str, Any]:
    root = operational_root(repo, base)
    pointers = {
        path.relative_to(repo).as_posix(): _encoded_file(path)
        for path in sorted((repo / "research").glob(POINTER_GLOB))
    }
    # Known version-4 pointers may be absent in a version-3 repository.
    for relative in (
        CLASSIFICATION_POINTER,
        "research/active-consolidation-generation.json",
        "research/active-generation.json",
    ):
        pointers.setdefault(relative, _encoded_file(repo / relative))
    files = {
        "research/workflow.toml": _encoded_file(repo / "research/workflow.toml"),
        "agent-profiles.json": _encoded_file(agent_profiles_path(repo, base)),
    }
    database = _database_backup(
        store, root / "migrations/backups" / f"{migration_id}.sqlite"
    )
    return {
        "repository_fingerprint": repository_fingerprint,
        "workflow_fingerprint": workflow_fingerprint,
        "files": files,
        "pointers": pointers,
        "database_backup": database,
    }


def _validate_journal(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "migration_id", "phase", "lease_token", "thread_id",
        "repository_fingerprint", "workflow_fingerprint", "legacy_lease_preimage",
        "legacy_audit_ids", "backups", "effects", "result",
    }
    if (
        set(value) != required
        or value["schema_version"] != SCHEMA_VERSION
        or value["phase"] not in PHASES
        or not all(isinstance(value[key], str) and value[key] for key in (
            "migration_id", "lease_token", "thread_id",
            "repository_fingerprint", "workflow_fingerprint",
        ))
        or not isinstance(value["backups"], dict)
        or not isinstance(value["effects"], dict)
    ):
        raise ValueError("invalid workflow migration journal")
    return value


def reconstruct_journal(
    repo: Path,
    store: DispatcherStore,
    migration_id: str,
    *,
    base: Path | None = None,
) -> dict[str, Any]:
    """Recreates the first external artifact from the durable handoff row."""

    path = journal_path(repo, base)
    row = store.workflow_migration(migration_id)
    if row is None or row["status"] != "handoff_prepared":
        if path.is_file():
            return _validate_journal(json.loads(path.read_text(encoding="utf-8")))
        raise ValueError("workflow migration handoff is missing")
    if not store.owns_lease("workflow-migration", row["lease_token"], row["thread_id"]):
        raise RuntimeError("workflow migration lease was fenced")
    journal = {
        "schema_version": SCHEMA_VERSION,
        "migration_id": migration_id,
        "phase": "prepared",
        "lease_token": row["lease_token"],
        "thread_id": row["thread_id"],
        "repository_fingerprint": row["repository_fingerprint"],
        "workflow_fingerprint": row["workflow_fingerprint"],
        "legacy_lease_preimage": row["legacy_lease_preimage"],
        "legacy_audit_ids": row["legacy_audit_ids"],
        "backups": row["journal_seed"],
        "effects": {},
        "result": None,
    }
    if path.is_file():
        current = _validate_journal(json.loads(path.read_text(encoding="utf-8")))
        if current != journal:
            raise RuntimeError("workflow migration journal conflicts with handoff")
        return current
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_json(path, journal)
    path.chmod(0o600)
    return journal


def start_migration(
    repo: Path,
    *,
    repository_fingerprint: str,
    workflow_fingerprint: str,
    owner: str,
    process_identity: dict | None = None,
    base: Path | None = None,
) -> dict[str, Any]:
    migration_id = sha256(canonical_json({
        "repository": str(repo.resolve()),
        "repository_fingerprint": repository_fingerprint,
        "workflow_fingerprint": workflow_fingerprint,
        "target_schema_version": "4",
    }))
    with DispatcherStore(repo, base) as store:
        prior = store.workflow_migration(migration_id)
        if prior is not None:
            if prior["status"] == "committed":
                return prior["result"]
            return reconstruct_journal(repo, store, migration_id, base=base)
        seed = _seed(
            repo, store, migration_id, repository_fingerprint,
            workflow_fingerprint, base,
        )
        handoff = store.acquire_workflow_migration(
            migration_id,
            owner=owner,
            process_identity=process_identity,
            repository_fingerprint=repository_fingerprint,
            workflow_fingerprint=workflow_fingerprint,
            journal_seed=seed,
        )
        if handoff is None:
            raise RuntimeError("workflow migration conflicts with an active lease")
        return reconstruct_journal(repo, store, migration_id, base=base)


def load_journal(repo: Path, *, base: Path | None = None) -> dict[str, Any] | None:
    path = journal_path(repo, base)
    return _validate_journal(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else None


def guard_mutation(repo: Path, *, base: Path | None = None) -> None:
    journal = load_journal(repo, base=base)
    if journal is not None and journal["phase"] != "committed":
        raise RuntimeError("workflow migration is in progress")
    with DispatcherStore(repo, base) as store:
        lease = store.lease("workflow-migration")
        if journal is not None and journal["phase"] == "committed" and lease is not None:
            if not store.update_workflow_migration(
                journal["migration_id"], journal["lease_token"], "committed",
                journal["result"] or {"migration_id": journal["migration_id"], "phase": "committed"},
                release=True,
            ):
                raise RuntimeError("workflow migration commit recovery was fenced")
            lease = None
        if lease is not None:
            raise RuntimeError("workflow migration is in progress")


def advance(
    repo: Path,
    migration_id: str,
    lease_token: str,
    phase: str,
    *,
    effects: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    base: Path | None = None,
) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError("invalid workflow migration phase")
    path = journal_path(repo, base)
    with DispatcherStore(repo, base) as store:
        journal = load_journal(repo, base=base)
        if journal is None or journal["migration_id"] != migration_id:
            raise ValueError("workflow migration journal is missing")
        if not store.owns_lease("workflow-migration", lease_token, journal["thread_id"]):
            raise RuntimeError("workflow migration lease was fenced")
        current_index = PHASES.index(journal["phase"])
        target_index = PHASES.index(phase)
        if target_index < current_index:
            return journal
        if target_index > current_index + 1:
            raise ValueError("workflow migration phase cannot be skipped")
        updated = {
            **journal,
            "phase": phase,
            "effects": {**journal["effects"], **(effects or {})},
            "result": result if result is not None else journal["result"],
        }
        atomic_json(path, updated)
        final_result = updated["result"] or {
            "migration_id": migration_id,
            "phase": phase,
        }
        if not store.update_workflow_migration(
            migration_id, lease_token, phase, final_result,
            release=phase == "committed",
        ):
            raise RuntimeError("workflow migration lease was fenced")
        return updated


def import_compatible_analyzer_results(
    repo: Path,
    envelopes: Iterable[dict[str, Any]],
    expected: dict[str, dict[str, str]],
) -> dict[str, int]:
    """Publishes only exact analyzer envelopes; grouping envelopes are ignored."""

    rows = []
    rejected = 0
    for envelope in envelopes:
        result = envelope.get("result")
        identifier = result.get("stable_diff_id") if isinstance(result, dict) else None
        binding = expected.get(str(identifier))
        if (
            binding is None
            or envelope.get("compatibility_fingerprint") != binding.get("compatibility_fingerprint")
            or envelope.get("result_fingerprint") != _fingerprint(result)
            or result.get("kind") not in {"meaning", "noise"}
        ):
            rejected += 1
            continue
        try:
            rows.append(make_row(
                str(identifier), result,
                evidence_fingerprint=binding["evidence_fingerprint"],
                result_schema_fingerprint=binding["result_schema_fingerprint"],
                profile_fingerprint=binding["profile_fingerprint"],
                instruction_fingerprint=binding["instruction_fingerprint"],
                context_fingerprint=binding["context_fingerprint"],
            ))
        except (KeyError, TypeError, ValueError):
            rejected += 1
    pointer_path = repo / CLASSIFICATION_POINTER
    if not pointer_path.is_file():
        publish_empty(repo)
    active = json.loads(pointer_path.read_text(encoding="utf-8"))
    if rows:
        publish_window(
            repo, rows,
            expected_generation_id=active["generation_id"],
        )
    counts = coverage(repo)
    return {
        "imported": len(rows),
        "rejected": rejected,
        "remaining": int(counts["remaining"]),
    }


def discover_compatible_analyzer_results(
    repo: Path, *, base: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Reconstructs exact legacy analyzer expectations from durable run snapshots."""

    from .dif_classifications import physical_evidence_fingerprints
    root = operational_root(repo, base)
    evidence = physical_evidence_fingerprints(repo)
    envelopes: list[dict[str, Any]] = []
    expected: dict[str, dict[str, str]] = {}
    with DispatcherStore(repo, base) as store:
        candidates = [
            row for row in store.proposals()
            if row.get("job_id") == "discover-mrq"
            and row.get("kind") == "node-result"
            and str(row.get("payload", {}).get("name", "")).startswith("analyze-dif:")
        ]
    for candidate in candidates:
        envelope = candidate.get("payload", {}).get("envelope")
        result = envelope.get("result") if isinstance(envelope, dict) else None
        identifier = result.get("stable_diff_id") if isinstance(result, dict) else None
        run_path = root / "runs" / f"{envelope.get('source_run_id', '')}.json" if isinstance(envelope, dict) else None
        if not identifier or identifier not in evidence or not run_path or not run_path.is_file():
            continue
        run = json.loads(run_path.read_text(encoding="utf-8"))
        execution = run.get("execution_snapshot", {})
        work_unit = execution.get("work_unit", {})
        profile_name = next((
            role["agent_profile"]
            for phase in execution.get("agent_phases", [])
            if phase.get("phase_id") == "analyze-dif"
            for role in phase.get("roles", [])
            if role.get("role_id") == "analyzer"
        ), "")
        profile = execution.get("profiles", {}).get(profile_name, {})
        if work_unit.get("id") != identifier or not profile:
            continue
        compatibility = {
            "operation": "mrq.discover-next",
            "phase_id": "analyze-dif",
            "role_id": "analyzer",
            "work_unit": work_unit,
            "allowed_paths": sorted(work_unit.get("allowed_paths", [])),
            "base_instruction_version": str(profile.get("instructions_version", "")),
            "response_schema": "analyze-dif-analyzer/v1",
            "validator_version": "agent-phase-result/v1",
        }
        expected[str(identifier)] = {
            "compatibility_fingerprint": _fingerprint(compatibility),
            "evidence_fingerprint": evidence[str(identifier)],
            "result_schema_fingerprint": "sha256:" + sha256(b"analyze-dif-result/v1"),
            "profile_fingerprint": _fingerprint(profile),
            "instruction_fingerprint": "sha256:" + sha256(str(profile.get("instructions_version", "")).encode()),
            "context_fingerprint": _fingerprint(work_unit.get("allowed_path_fingerprints", [])),
        }
        envelopes.append(envelope)
    return envelopes, expected


def execute(
    repo: Path,
    migration_id: str,
    *,
    workflow_v4: bytes,
    analyzer_envelopes: Iterable[dict[str, Any]] = (),
    expected_analyzers: dict[str, dict[str, str]] | None = None,
    base: Path | None = None,
) -> dict[str, Any]:
    """Completes the guarded v3->v4 cutover under the durable migration fence."""

    journal = load_journal(repo, base=base)
    if journal is None or journal["migration_id"] != migration_id:
        raise ValueError("workflow migration journal is missing")
    token = journal["lease_token"]
    if journal["phase"] == "prepared":
        journal = advance(
            repo, migration_id, token, "legacy-interrupted",
            effects={"legacy_interrupted": True}, base=base,
        )
    if journal["phase"] == "legacy-interrupted":
        if expected_analyzers is None:
            discovered, expected_analyzers = discover_compatible_analyzer_results(repo, base=base)
            analyzer_envelopes = discovered
        imported = import_compatible_analyzer_results(
            repo, analyzer_envelopes, expected_analyzers or {},
        )
        journal = advance(
            repo, migration_id, token, "classification-published",
            effects={"classification": imported}, base=base,
        )
    if journal["phase"] == "classification-published":
        try:
            import tomllib
            candidate = tomllib.loads(workflow_v4.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("invalid workflow-v4 bytes") from exc
        from .workflow import validate_workflow_manifest
        validate_workflow_manifest(candidate)
        classification = json.loads((repo / CLASSIFICATION_POINTER).read_text(encoding="utf-8"))
        atomic_json(repo / "research/active-consolidation-generation.json", consolidation_sentinel())
        workflow_path = repo / "research/workflow.toml"
        fd, temporary = tempfile.mkstemp(prefix=".workflow.toml.", dir=workflow_path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(workflow_v4)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, workflow_path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        journal = advance(
            repo, migration_id, token, "catalog-switched",
            effects={"workflow_sha256": sha256(workflow_v4)}, base=base,
        )
    if journal["phase"] == "catalog-switched":
        from .workflow import validate_workflow
        validate_workflow(repo)
        result = {
            "migration_id": migration_id,
            "phase": "committed",
            "classification": journal["effects"].get("classification", {}),
        }
        journal = advance(
            repo, migration_id, token, "committed", result=result, base=base,
        )
    return journal["result"] or {"migration_id": migration_id, "phase": journal["phase"]}


def recover(
    repo: Path,
    migration_id: str,
    *,
    base: Path | None = None,
) -> dict[str, Any]:
    should_rollback = False
    with DispatcherStore(repo, base) as store:
        journal = load_journal(repo, base=base)
        if journal is None:
            return reconstruct_journal(repo, store, migration_id, base=base)
        if journal["migration_id"] != migration_id:
            raise RuntimeError("another workflow migration journal exists")
        if journal["phase"] == "committed":
            row = store.workflow_migration(migration_id)
            if row and row["status"] != "committed":
                if not store.update_workflow_migration(
                    migration_id,
                    journal["lease_token"],
                    "committed",
                    journal["result"] or {"migration_id": migration_id, "phase": "committed"},
                    release=True,
                ):
                    raise RuntimeError("workflow migration commit recovery was fenced")
            return journal
        if not store.owns_lease(
            "workflow-migration", journal["lease_token"], journal["thread_id"]
        ):
            raise RuntimeError("workflow migration lease was fenced")
        backups = journal["backups"]
        database = backups["database_backup"]
        backup_path = Path(database["path"])
        valid = (
            backup_path.is_file()
            and sha256(backup_path.read_bytes()) == database["sha256"]
        )
        if journal["phase"] in {"prepared", "legacy-interrupted"}:
            valid = valid and all(
                _matches_file(
                    agent_profiles_path(repo, base)
                    if relative == "agent-profiles.json"
                    else repo / relative,
                    backup,
                )
                for relative, backup in backups["files"].items()
            ) and all(
                _matches_file(repo / relative, backup)
                for relative, backup in backups["pointers"].items()
            )
        elif journal["phase"] == "classification-published":
            valid = valid and all(
                _matches_file(
                    agent_profiles_path(repo, base)
                    if relative == "agent-profiles.json"
                    else repo / relative,
                    backup,
                )
                for relative, backup in backups["files"].items()
            ) and (repo / CLASSIFICATION_POINTER).is_file() and all(
                relative == CLASSIFICATION_POINTER or _matches_file(repo / relative, backup)
                for relative, backup in backups["pointers"].items()
            )
        elif journal["phase"] == "catalog-switched":
            valid = (
                valid
                and (repo / "research/workflow.toml").is_file()
                and (repo / CLASSIFICATION_POINTER).is_file()
            )
        should_rollback = not valid
    if should_rollback:
        return rollback(repo, migration_id, base=base)
    return {**journal, "recovery_action": "resume"}


def rollback(
    repo: Path,
    migration_id: str,
    *,
    base: Path | None = None,
) -> dict[str, Any]:
    with DispatcherStore(repo, base) as store:
        journal = load_journal(repo, base=base)
        if journal is None or journal["migration_id"] != migration_id:
            raise ValueError("workflow migration journal is missing")
        token = journal["lease_token"]
        if not store.owns_lease("workflow-migration", token, journal["thread_id"]):
            raise RuntimeError("workflow migration lease was fenced")
        backups = journal["backups"]
        for relative, backup in backups["files"].items():
            path = agent_profiles_path(repo, base) if relative == "agent-profiles.json" else repo / relative
            _restore_file(path, backup)
        for relative, backup in backups["pointers"].items():
            _restore_file(repo / relative, backup)
        database = backups["database_backup"]
        backup_path = Path(database["path"])
        if not backup_path.is_file() or sha256(backup_path.read_bytes()) != database["sha256"]:
            raise ValueError("workflow migration database backup is corrupt")
        result = {"migration_id": migration_id, "phase": "rolled_back"}
        if not store.rollback_workflow_migration(
            migration_id, token, backup_path, result
        ):
            raise RuntimeError("workflow migration lease was fenced")
        journal_path(repo, base).unlink(missing_ok=True)
        return result
