from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .contracts import SECRET_KEYS, atomic_json, canonical_json, repository_lock, sha256
from .workflow import state_fingerprint


BOUNDARY_STEPS = {
    "sources": ("sources.acquire", "diff.build", "mrq.revalidate-unchanged", "indexes.build"),
    "diffs": ("diff.build", "mrq.revalidate-unchanged"),
    "projections": ("projections.build", "workflow.verify"),
}
POINTERS = {
    "source": "active-source-generation.json",
    "diff": "active-diff-generation.json",
    "mrq": "active-generation.json",
}
INTENT = ".stage-recompute-transaction.json"


class StageExecutionError(RuntimeError):
    def __init__(self, message: str, steps: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.steps = steps


def fingerprint(value: Any) -> str:
    return "sha256:" + sha256(canonical_json(value))


def safe_profile_fingerprint(profiles: dict[str, dict[str, Any]]) -> str:
    def safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: safe(item) for key, item in sorted(value.items()) if not SECRET_KEYS.search(key)}
        if isinstance(value, list):
            return [safe(item) for item in value]
        return value

    return fingerprint(safe(profiles))


def compatibility_fingerprint(
    mrq: dict[str, Any],
    dispositions: Iterable[dict[str, Any]],
    diff_facts: dict[str, dict[str, Any]],
    coverage: dict[str, dict[str, Any]],
    approvals: Iterable[dict[str, Any]],
) -> str:
    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "<generation>"
                if key in {"source_generation_id", "diff_generation_id"}
                else normalize(item)
                for key, item in sorted(value.items())
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    relations = sorted(
        (item for item in dispositions if item.get("mrq_id") == mrq.get("mrq_id")),
        key=canonical_json,
    )
    diff_ids = sorted({item["stable_diff_id"] for item in relations})
    closure = {
        "mrq": mrq,
        "dispositions": relations,
        "diff_facts": {identifier: diff_facts.get(identifier) for identifier in diff_ids},
        "coverage": {identifier: coverage.get(identifier) for identifier in diff_ids},
        "approvals": sorted(
            (
                item
                for item in approvals
                if item.get("target_id") in {mrq.get("mrq_id"), *diff_ids}
            ),
            key=canonical_json,
        ),
    }
    return fingerprint(normalize(closure))


def active_pointers(repo: Path, *, recover: bool = True) -> dict[str, dict[str, Any] | None]:
    if recover:
        with repository_lock(repo):
            if (repo / "research" / INTENT).is_file():
                recover_publication(repo, validate=lambda values: _validate_pointer_set(repo, values))
            return active_pointers(repo, recover=False)
    result: dict[str, dict[str, Any] | None] = {}
    for kind, name in POINTERS.items():
        path = repo / "research" / name
        result[kind] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    return result


def active_state(repo: Path) -> tuple[dict[str, dict[str, Any] | None], str]:
    """Возвращает указатели и соответствующий им отпечаток под одной блокировкой."""

    with repository_lock(repo):
        if (repo / "research" / INTENT).is_file():
            recover_publication(repo, validate=lambda values: _validate_pointer_set(repo, values))
        return active_pointers(repo, recover=False), state_fingerprint(repo)


def build_plan(
    repo: Path,
    boundary: str,
    expected_workflow_fingerprint: str,
    *,
    routing_preview: dict[str, Any] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if boundary not in BOUNDARY_STEPS:
        raise ValueError("unsupported recompute boundary")
    with repository_lock(repo):
        if (repo / "research" / INTENT).is_file():
            recover_publication(repo, validate=lambda values: _validate_pointer_set(repo, values))
        current = state_fingerprint(repo)
        pointers = active_pointers(repo, recover=False)
    if expected_workflow_fingerprint != current:
        raise RuntimeError("stale workflow fingerprint")
    if boundary == "sources":
        required = {"routing_plan_fingerprint", "expires_at", "required_tools"}
        if routing_preview is None or not required <= routing_preview.keys() or profiles is None:
            raise ValueError("sources recompute requires routing preview and profile assignments")
        source_inputs = {
            "source_routing_preview_id": routing_preview.get("preview_id", ""),
            "routing_plan_fingerprint": routing_preview["routing_plan_fingerprint"],
            "routing_expires_at": routing_preview["expires_at"],
            "required_tools": routing_preview["required_tools"],
            "profile_assignments_fingerprint": safe_profile_fingerprint(profiles),
        }
    else:
        if routing_preview is not None or profiles is not None:
            raise ValueError("routing inputs are only valid for sources")
        source_inputs = None
    if any(not value for value in pointers.values()):
        raise RuntimeError("stage recompute requires active source, diff and MRQ generations")
    canonical_id = pointers["mrq"].get("canonical_generation_id")
    manifest_path = repo / "research" / "generations" / str(canonical_id) / "manifest.json"
    if manifest_path.is_file():
        from .mrq import comparison_epoch_fingerprint

        analyzer_path = repo / "analysis" / "indexes" / "generations" / str(pointers["diff"].get("generation_id")) / "extension-analyzer-manifest.json"
        analyzer = json.loads(analyzer_path.read_text(encoding="utf-8")) if analyzer_path.is_file() else None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("comparison_epoch_fingerprint") != comparison_epoch_fingerprint(pointers["source"], analyzer):
            raise RuntimeError("comparison epoch changed; use sources.configure")
    plan = {
        "schema_version": "1",
        "boundary": boundary,
        "workflow_fingerprint": current,
        "active_pointers": pointers,
        "steps": [
            {
                "step_id": f"{position}:{operation}",
                "operation": operation,
                "conditional": operation == "indexes.build",
                "input_fingerprint": fingerprint({"operation": operation, "pointers": pointers, "source_inputs": source_inputs}),
            }
            for position, operation in enumerate(BOUNDARY_STEPS[boundary], 1)
        ],
        "required_confirmations": ["confirm_recompute", *(["confirm_sources_acquire"] if boundary == "sources" else [])],
        "possible_results": ["changed", "unchanged", "awaiting_manual_work"],
        "manual_stop": None if boundary == "projections" else "mrq.next",
        "source_inputs": source_inputs,
        "projection_fingerprint": (
            "sha256:" + sha256((repo / "outputs" / "projections.json").read_bytes())
            if (repo / "outputs" / "projections.json").is_file()
            else None
        ),
    }
    return {**plan, "plan_fingerprint": fingerprint(plan)}


def preview(
    repo: Path,
    *,
    boundary: str,
    expected_workflow_fingerprint: str,
    source_routing_preview_id: str | None = None,
    operational_root: Path | None = None,
) -> dict[str, Any]:
    if boundary != "sources":
        return build_plan(repo, boundary, expected_workflow_fingerprint)
    if operational_root is None or not source_routing_preview_id or Path(source_routing_preview_id).name != source_routing_preview_id:
        raise ValueError("sources recompute requires a safe routing preview id")
    from .user_state import workspace_id

    project_root = operational_root / "projects" / workspace_id(repo)
    route_path = project_root / "source-routing-previews" / f"{source_routing_preview_id}.json"
    connection_path = project_root / "connections.json"
    if not route_path.is_file() or not connection_path.is_file():
        raise RuntimeError("routing_preview_stale")
    route = json.loads(route_path.read_text(encoding="utf-8"))
    if route.get("status") != "ready" or route.get("preview_id") != source_routing_preview_id:
        raise RuntimeError("routing_preview_stale")
    expires = str(route.get("expires_at", "")).replace("Z", "+00:00")
    try:
        if datetime.fromisoformat(expires) <= datetime.now(timezone.utc):
            raise RuntimeError("routing_preview_stale")
    except ValueError as exc:
        raise RuntimeError("routing_preview_stale") from exc
    return build_plan(
        repo,
        boundary,
        expected_workflow_fingerprint,
        routing_preview=route,
        profiles=json.loads(connection_path.read_text(encoding="utf-8")),
    )


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_json(path: Path, value: Any) -> None:
    atomic_json(path, value)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    _sync_directory(path.parent)


def _validate_pointer_set(repo: Path, values: dict[str, dict[str, Any]]) -> None:
    from .diffs import validate_active as validate_diff
    from .mrq import active as validate_mrq
    from .sources import validate_active as validate_source

    source, diff, canonical = (values[kind] for kind in ("source", "diff", "mrq"))
    validate_source(repo, deep=True, candidate=source)
    validate_diff(repo, candidate=diff, source_candidate=source)
    validate_mrq(repo, pointer_candidate=canonical, source_candidate=source, diff_candidate=diff)


def recover_active_publication(repo: Path) -> str | None:
    if not (repo / "research" / INTENT).is_file():
        return None
    with repository_lock(repo):
        return recover_publication(repo, validate=lambda values: _validate_pointer_set(repo, values))


def recover_publication(
    repo: Path,
    *,
    validate: Callable[[dict[str, dict[str, Any]]], None] | None = None,
) -> str | None:
    intent_path = repo / "research" / INTENT
    if not intent_path.is_file():
        return None
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    if intent.get("phase") not in {"prepared", "committing"}:
        raise ValueError("invalid publication intent")
    required = {"schema_version", "phase", "kinds", "old", "new", "old_fingerprint", "new_fingerprint"}
    if set(intent) != required or fingerprint(intent["old"]) != intent["old_fingerprint"] or fingerprint(intent["new"]) != intent["new_fingerprint"]:
        raise RuntimeError("critical corrupted stage recompute transaction")
    selected = intent["old"] if intent["phase"] == "prepared" else intent["new"]
    if intent["phase"] == "committing":
        if validate is None:
            raise RuntimeError("critical committing transaction requires candidate validation")
        try:
            validate({kind: value for kind, value in selected.items() if value is not None})
        except Exception as exc:
            current = active_pointers(repo, recover=False)
            if all(current[kind] == intent["old"][kind] for kind in intent["kinds"]):
                selected = intent["old"]
                intent["phase"] = "prepared"
            else:
                raise RuntimeError("critical invalid committing stage recompute transaction") from exc
    for kind in intent["kinds"]:
        _durable_json(repo / "research" / POINTERS[kind], selected[kind])
    intent_path.unlink()
    _sync_directory(intent_path.parent)
    return "rolled_back" if intent["phase"] == "prepared" else "committed"


def publish_pointers(
    repo: Path,
    expected: dict[str, dict[str, Any] | None],
    candidates: dict[str, dict[str, Any]],
    *,
    lease_check: Callable[[], bool],
    validate: Callable[[dict[str, dict[str, Any]]], None],
    expected_workflow_fingerprint: str | None = None,
    before_write: Callable[[str], None] | None = None,
) -> dict[str, dict[str, Any]]:
    kinds = tuple(candidates)
    if not kinds or any(kind not in POINTERS for kind in kinds):
        raise ValueError("invalid pointer publication set")
    with repository_lock(repo):
        recover_publication(repo, validate=validate)
        if not lease_check():
            raise RuntimeError("stage recompute lease lost")
        current = active_pointers(repo, recover=False)
        if expected_workflow_fingerprint is not None and state_fingerprint(repo) != expected_workflow_fingerprint:
            raise RuntimeError("stale complete workflow state")
        if any(current[kind] != expected.get(kind) for kind in POINTERS):
            raise RuntimeError("stale complete pointer state")
        merged = {**current, **candidates}
        validate({kind: value for kind, value in merged.items() if value is not None})
        intent_path = repo / "research" / INTENT
        intent = {
            "schema_version": "1",
            "phase": "prepared",
            "kinds": list(kinds),
            "old": current,
            "new": merged,
            "old_fingerprint": fingerprint(current),
            "new_fingerprint": fingerprint(merged),
        }
        _durable_json(intent_path, intent)
        if before_write:
            before_write("prepared")
        if not lease_check():
            raise RuntimeError("stage recompute lease lost")
        intent["phase"] = "committing"
        _durable_json(intent_path, intent)
        if before_write:
            before_write("committing")
        for kind in kinds:
            if before_write:
                before_write(kind)
            _durable_json(repo / "research" / POINTERS[kind], candidates[kind])
        if before_write:
            before_write("delete_intent")
        intent_path.unlink()
        _sync_directory(intent_path.parent)
        return {kind: value for kind, value in merged.items() if value is not None}


def execute_plan(
    plan: dict[str, Any],
    apply: Callable[[str, dict[str, Any], str, Callable[[], bool]], dict[str, Any]],
    *,
    lease_check: Callable[[], bool],
    payloads: dict[str, dict[str, Any]] | None = None,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    unsigned = {key: value for key, value in plan.items() if key != "plan_fingerprint"}
    if plan.get("plan_fingerprint") != fingerprint(unsigned):
        raise RuntimeError("stale recompute plan")
    payloads = payloads or {}
    results = []
    expected = plan["workflow_fingerprint"]
    for step in plan["steps"]:
        if not lease_check():
            raise RuntimeError("stage recompute lease lost")
        operation = step["operation"]
        if step.get("conditional") and not payloads.get(operation, {}).get("component_ids"):
            results.append({**step, "status": "skipped", "result": {"status": "skipped"}, "output_fingerprint": fingerprint({"status": "skipped"})})
            continue
        if emit:
            emit("step.started", step)
        try:
            result = apply(operation, payloads.get(operation, {}), expected, lambda: not lease_check())
        except Exception as exc:
            raise StageExecutionError(str(exc), results) from exc
        expected = result.get("workflow_fingerprint", expected)
        stop = bool(result.pop("_stop", False))
        output = {**step, "result": result, "output_fingerprint": fingerprint(result)}
        if len(results) + 1 < len(plan["steps"]):
            output["next_input_fingerprint"] = plan["steps"][len(results) + 1]["input_fingerprint"]
        if not lease_check():
            raise StageExecutionError("stage recompute lease lost", [*results, output])
        results.append(output)
        if emit:
            emit("step.finished", output)
        if stop:
            break
    return results


def prepare_resume(
    plan: dict[str, Any],
    predecessor_run: dict[str, Any],
    current_artifacts: dict[str, str],
) -> list[dict[str, Any]]:
    prior = predecessor_run.get("steps")
    if predecessor_run.get("boundary") != plan.get("boundary") or predecessor_run.get("status") not in {"failed", "cancelled", "resumable"} or not isinstance(prior, list):
        raise RuntimeError("incompatible predecessor run")
    skipped: list[dict[str, Any]] = []
    for position, step in enumerate(plan["steps"]):
        if position >= len(prior):
            break
        previous = prior[position]
        if step["operation"] == "sources.acquire":
            break
        if previous.get("operation") != step["operation"] or previous.get("input_fingerprint") != step.get("input_fingerprint"):
            raise RuntimeError("incompatible predecessor step")
        output = previous.get("output_fingerprint")
        if not output or current_artifacts.get(step["operation"]) != output:
            break
        if position + 1 < len(plan["steps"]) and previous.get("next_input_fingerprint") != plan["steps"][position + 1].get("input_fingerprint"):
            raise RuntimeError("predecessor output is not the next step input")
        skipped.append(step)
    return skipped


def execute(
    repo: Path,
    plan: dict[str, Any],
    *,
    lease_token: str,
    cancelled: Callable[[], bool],
    emit: Callable[[str, dict[str, Any]], None] | None,
    operational_root: Path | None = None,
    predecessor_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not lease_token:
        raise ValueError("lease token is required")
    from .service import ApplicationService
    from .user_state import load_connections, workspace_id

    project_root = (operational_root / "projects" / workspace_id(repo)) if operational_root else None
    service = ApplicationService(
        repo,
        connections=load_connections(repo, operational_root) if operational_root else None,
        upload_drafts=project_root / "upload-drafts" if project_root else None,
        routing_previews=project_root / "source-routing-previews" if project_root else None,
    )
    if plan["boundary"] == "sources":
        expires = str((plan.get("source_inputs") or {}).get("routing_expires_at", "")).replace("Z", "+00:00")
        if not expires or datetime.fromisoformat(expires) <= datetime.now(timezone.utc):
            raise RuntimeError("routing_preview_stale")
        if safe_profile_fingerprint(service.connections or {}) != (plan.get("source_inputs") or {}).get("profile_assignments_fingerprint"):
            raise RuntimeError("source profile assignments changed")
    skipped: list[dict[str, Any]] = []
    changed_components: list[str] = []
    if predecessor_run is not None:
        predecessor_record = predecessor_run
        predecessor_result = predecessor_run.get("result") or {}
        predecessor_run = {
            **predecessor_result,
            "boundary": predecessor_run.get("boundary"),
            "status": predecessor_run.get("status"),
        }
        current = active_pointers(repo)
        prior_steps = predecessor_run.get("steps", [])
        if plan["boundary"] == "sources" and len(prior_steps) >= 3:
            prior_plan_steps = (predecessor_record.get("plan") or {}).get("steps", [])
            source_result, diff_result, mrq_result = (prior_steps[position].get("result", {}) for position in range(3))
            published = (
                len(prior_plan_steps) == len(BOUNDARY_STEPS["sources"])
                and all(
                    prior_steps[position].get("operation") == BOUNDARY_STEPS["sources"][position]
                    and prior_steps[position].get("input_fingerprint") == prior_plan_steps[position].get("input_fingerprint")
                    and prior_steps[position].get("output_fingerprint") == fingerprint(prior_steps[position].get("result", {}))
                    for position in range(3)
                )
                and prior_steps[2].get("next_input_fingerprint") == prior_plan_steps[3].get("input_fingerprint")
                and source_result.get("generation_id") == (current["source"] or {}).get("generation_id")
                and diff_result.get("generation_id") == (current["diff"] or {}).get("generation_id")
                and mrq_result.get("canonical_generation_id") == (current["mrq"] or {}).get("canonical_generation_id")
            )
            if published:
                old_components = {
                    item["component_id"]: item.get("fingerprint")
                    for item in ((predecessor_record.get("plan") or {}).get("active_pointers", {}).get("source") or {}).get("components", [])
                }
                changed_components = [
                    item["component_id"]
                    for item in (current["source"] or {}).get("components", [])
                    if old_components.get(item["component_id"]) != item.get("fingerprint")
                ]
                skipped = plan["steps"][:3]
        artifacts: dict[str, str] = {}
        for step in predecessor_run.get("steps", []):
            result = step.get("result", {})
            operation = step.get("operation")
            pointer = current["diff"] if operation == "diff.build" else current["mrq"] if operation == "mrq.revalidate-unchanged" else None
            identifier = result.get("generation_id") or result.get("canonical_generation_id")
            active_identifier = pointer.get("generation_id") if operation == "diff.build" and pointer else pointer.get("canonical_generation_id") if pointer else None
            if identifier and identifier == active_identifier:
                artifacts[operation] = step.get("output_fingerprint", "")
        if not skipped:
            skipped = prepare_resume(plan, predecessor_run, artifacts)
    source_inputs = plan.get("source_inputs") or {}
    payloads = {
        "sources.acquire": {
            "source_routing_preview_id": source_inputs.get("source_routing_preview_id", ""),
            "routing_plan_fingerprint": source_inputs.get("routing_plan_fingerprint", ""),
        },
        "mrq.revalidate-unchanged": {
            "actor": "stage-recompute",
            "rationale": f"controlled recompute from {plan['boundary']}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "indexes.build": {"component_ids": [], "mode": "ensure", "confirmed": False},
    }
    staged: dict[str, dict[str, Any]] = {}
    old_pointers = active_pointers(repo)
    payloads["indexes.build"]["component_ids"] = changed_components

    def validate_candidates(values: dict[str, dict[str, Any]]) -> None:
        _validate_pointer_set(repo, values)

    def apply(operation: str, payload: dict[str, Any], expected: str, signal: Callable[[], bool]) -> dict[str, Any]:
        nonlocal changed_components
        candidate_mode = operation in {"sources.acquire", "diff.build", "mrq.revalidate-unchanged"}
        result = service.apply(operation, payload, expected, signal, staged=staged if candidate_mode else None)
        if operation == "sources.acquire":
            previous = {item["component_id"]: item.get("fingerprint") for item in (old_pointers["source"] or {}).get("components", [])}
            changed_components = [
                item["component_id"]
                for item in staged["source"].get("components", [])
                if previous.get(item["component_id"]) != item.get("fingerprint")
            ]
            payloads["indexes.build"]["component_ids"] = changed_components
        if operation == "diff.build":
            source_same = staged.get("source", old_pointers["source"]).get("generation_id") == (old_pointers["source"] or {}).get("generation_id")
            diff_same = staged["diff"].get("generation_id") == (old_pointers["diff"] or {}).get("generation_id")
            if diff_same and (plan["boundary"] == "diffs" or source_same):
                return {**result, "_stop": True}
        next_fingerprint = expected
        if operation == "mrq.revalidate-unchanged":
            candidates = {kind: staged[kind] for kind in ("source", "diff", "mrq") if kind in staged}
            publish_pointers(
                repo,
                old_pointers,
                candidates,
                lease_check=lambda: not cancelled(),
                validate=validate_candidates,
                expected_workflow_fingerprint=plan["workflow_fingerprint"],
            )
            next_fingerprint = service.snapshot(deep=False)["workflow_fingerprint"]
        elif not candidate_mode:
            next_fingerprint = result.get("workflow_fingerprint", expected)
        return {**result, "workflow_fingerprint": next_fingerprint}

    execution_plan = plan
    if skipped:
        unsigned = {key: value for key, value in plan.items() if key != "plan_fingerprint"}
        unsigned["steps"] = plan["steps"][len(skipped):]
        execution_plan = {**unsigned, "plan_fingerprint": fingerprint(unsigned)}
    results = execute_plan(execution_plan, apply, lease_check=lambda: not cancelled(), payloads=payloads, emit=emit)
    final = service.snapshot(deep=False)
    changed = bool(skipped and plan["boundary"] == "sources") or active_pointers(repo) != plan["active_pointers"]
    if plan["boundary"] == "projections":
        projection = repo / "outputs" / "projections.json"
        changed = (
            ("sha256:" + sha256(projection.read_bytes()) if projection.is_file() else None)
            != plan.get("projection_fingerprint")
        )
    return {
        "status": "unchanged" if not changed else "awaiting_manual_work" if final.get("state") != "complete" and plan["boundary"] != "projections" else "changed",
        "steps": [{**step, "status": "skipped"} for step in skipped] + results,
        "workflow_fingerprint": final["workflow_fingerprint"],
        "next": service.next(),
    }
