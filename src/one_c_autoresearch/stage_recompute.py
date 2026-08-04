from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterable, Mapping
from typing import Callable, NotRequired, TypedDict

from .contracts import JsonValue, SECRET_KEYS, atomic_json, canonical_json, json_array, json_object, owned_function, parse_json, parse_json_object, repository_lock, sha256
from .diffs import diff_pointer, source_pointer as diff_source_pointer
from .sources import ConnectionProfile, ExtensionInfo, source_pointer


BOUNDARY_STEPS = {
    "sources": ("sources.acquire", "diff.build", "dif.classification-reset", "indexes.build"),
    "diffs": ("diff.build", "dif.classification-reset"),
    "projections": ("projections.build", "workflow.verify"),
}
POINTERS = {
    "source": "active-source-generation.json",
    "diff": "active-diff-generation.json",
    "mrq": "active-consolidation-generation.json",
}
INTENT = ".stage-recompute-transaction.json"

JsonObject = dict[str, JsonValue]
Pointer = Mapping[str, object]
PointerSet = dict[str, Pointer | None]


class StageStep(TypedDict):
    step_id: str
    operation: str
    conditional: bool
    input_fingerprint: str
    status: NotRequired[str]
    result: NotRequired[JsonObject]
    output_fingerprint: NotRequired[str]
    next_input_fingerprint: NotRequired[str]


class StagePlan(TypedDict):
    schema_version: str
    boundary: str
    workflow_fingerprint: str
    active_pointers: PointerSet
    steps: list[StageStep]
    required_confirmations: list[str]
    possible_results: list[str]
    manual_stop: str | None
    source_inputs: JsonObject | None
    projection_fingerprint: str | None
    plan_fingerprint: NotRequired[str]


class StageRun(TypedDict, total=False):
    run_id: str
    idempotency_key_fingerprint: str
    request_fingerprint: str
    lease_token: str
    boundary: str
    status: str
    steps: list[StageStep]
    result: dict[str, object]
    plan: StagePlan
    predecessor_run_id: str | None
    created_at: str
    updated_at: str


def state_fingerprint(repo: Path) -> str:
    value = owned_function(".workflow", "state_fingerprint")(repo)
    if not isinstance(value, str):
        raise RuntimeError("workflow state fingerprint is invalid")
    return value


def _object(value: object) -> dict[str, JsonValue]:
    return json_object(value)


def _value(value: object) -> JsonValue:
    return parse_json(canonical_json(value).decode("utf-8"))


def _objects(value: object) -> list[dict[str, JsonValue]]:
    return [_object(item) for item in json_array(value)]


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _strings(value: object) -> list[str]:
    values = json_array(value)
    if not all(isinstance(item, str) for item in values):
        raise ValueError("expected string array")
    return [item for item in values if isinstance(item, str)]


def _profile_map(value: object) -> dict[str, Pointer]:
    return {name: dict(_object(profile)) for name, profile in _object(value).items()}


def _connection_profiles(value: object) -> dict[str, ConnectionProfile]:
    profiles: dict[str, ConnectionProfile] = {}
    string_fields = (
        "platform_path", "kind", "server", "reference", "path", "dbms", "db_server", "db_name",
        "db_user", "db_password", "infobase_user", "infobase_password", "profile_id",
        "tested_fingerprint", "client_connection",
    )
    for name, raw_value in _object(value).items():
        raw = _object(raw_value)
        profile: ConnectionProfile = {}
        for field in string_fields:
            if field in raw:
                profile[field] = _string(raw[field])
        if "tested" in raw:
            profile["tested"] = bool(raw["tested"])
        if "configuration" in raw:
            profile["configuration"] = {
                key: _string(item) for key, item in _object(raw["configuration"]).items()
            }
        if "tool_versions" in raw:
            profile["tool_versions"] = {
                key: _string(item) for key, item in _object(raw["tool_versions"]).items()
            }
        if "extensions" in raw:
            extensions: list[ExtensionInfo] = []
            for item in _objects(raw["extensions"]):
                extensions.append({
                    "uuid": _string(item.get("uuid", "")),
                    "name": _string(item.get("name", "")),
                    "version": _string(item.get("version", "")),
                    "active": bool(item.get("active", False)),
                })
            profile["extensions"] = extensions
        profiles[name] = profile
    return profiles


def _pointer_set(value: object) -> PointerSet:
    return {
        kind: None if pointer is None else dict(_object(pointer))
        for kind, pointer in _object(value).items()
    }


def _present_pointers(values: PointerSet) -> dict[str, Pointer]:
    return {kind: pointer for kind, pointer in values.items() if pointer is not None}


def _stage_step(value: object) -> StageStep:
    raw = _object(value)
    step = StageStep(
        step_id=_string(raw.get("step_id", "")),
        operation=_string(raw.get("operation", "")),
        conditional=bool(raw.get("conditional", False)),
        input_fingerprint=_string(raw.get("input_fingerprint", "")),
    )
    if "status" in raw:
        step["status"] = _string(raw["status"])
    if "result" in raw:
        step["result"] = _object(raw["result"])
    for key in ("output_fingerprint", "next_input_fingerprint"):
        if key in raw:
            step[key] = _string(raw[key])
    return step


def _stage_steps(value: object) -> list[StageStep]:
    return [_stage_step(item) for item in json_array(value)]


def stage_plan(value: object) -> StagePlan:
    raw = _object(value)
    plan = StagePlan(
        schema_version=_string(raw.get("schema_version", "")),
        boundary=_string(raw.get("boundary", "")),
        workflow_fingerprint=_string(raw.get("workflow_fingerprint", "")),
        active_pointers=_pointer_set(raw.get("active_pointers", {})),
        steps=_stage_steps(raw.get("steps", [])),
        required_confirmations=_strings(raw.get("required_confirmations", [])),
        possible_results=_strings(raw.get("possible_results", [])),
        manual_stop=None if raw.get("manual_stop") is None else _string(raw["manual_stop"]),
        source_inputs=None if raw.get("source_inputs") is None else _object(raw["source_inputs"]),
        projection_fingerprint=None if raw.get("projection_fingerprint") is None else _string(raw["projection_fingerprint"]),
    )
    if "plan_fingerprint" in raw:
        plan["plan_fingerprint"] = _string(raw["plan_fingerprint"])
    return plan


def stage_run(value: object) -> StageRun:
    raw = _object(value)
    run = StageRun()
    for key in ("run_id", "idempotency_key_fingerprint", "request_fingerprint", "lease_token", "boundary", "status", "created_at", "updated_at"):
        if key in raw:
            run[key] = _string(raw[key])
    if "steps" in raw:
        run["steps"] = _stage_steps(raw["steps"])
    if "result" in raw:
        run["result"] = dict(_object(raw["result"]))
    if "plan" in raw:
        run["plan"] = stage_plan(raw["plan"])
    if "predecessor_run_id" in raw:
        run["predecessor_run_id"] = None if raw["predecessor_run_id"] is None else _string(raw["predecessor_run_id"])
    return run


def _plan_with_steps(plan: StagePlan, steps: list[StageStep]) -> StagePlan:
    result = StagePlan(
        schema_version=plan["schema_version"],
        boundary=plan["boundary"],
        workflow_fingerprint=plan["workflow_fingerprint"],
        active_pointers=plan["active_pointers"],
        steps=steps,
        required_confirmations=plan["required_confirmations"],
        possible_results=plan["possible_results"],
        manual_stop=plan["manual_stop"],
        source_inputs=plan["source_inputs"],
        projection_fingerprint=plan["projection_fingerprint"],
    )
    result["plan_fingerprint"] = fingerprint(result)
    return result


class StageExecutionError(RuntimeError):
    steps: list[StageStep]

    def __init__(self, message: str, steps: list[StageStep]) -> None:
        super().__init__(message)
        self.steps = steps


def exception_steps(exc: object) -> list[StageStep]:
    return exc.steps if isinstance(exc, StageExecutionError) else []


def fingerprint(value: object) -> str:
    return "sha256:" + sha256(canonical_json(value))


def safe_profile_fingerprint(profiles: Mapping[str, Mapping[str, object]]) -> str:
    def safe(value: JsonValue) -> JsonValue:
        if isinstance(value, Mapping):
            return {key: safe(item) for key, item in sorted(value.items()) if not SECRET_KEYS.search(key)}
        if isinstance(value, list):
            return [safe(item) for item in value]
        return value

    return fingerprint(safe(_value(profiles)))


def compatibility_fingerprint(
    mrq: Mapping[str, object],
    dispositions: Iterable[Mapping[str, object]],
    diff_facts: Mapping[str, Mapping[str, object]],
    coverage: Mapping[str, Mapping[str, object]],
    approvals: Iterable[Mapping[str, object]],
) -> str:
    def normalize(value: JsonValue) -> JsonValue:
        if isinstance(value, Mapping):
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
    diff_ids = sorted(str(item["stable_diff_id"]) for item in relations)
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
    return fingerprint(normalize(_value(closure)))


def active_pointers(repo: Path, *, recover: bool = True) -> PointerSet:
    if recover:
        with repository_lock(repo):
            if (repo / "research" / INTENT).is_file():
                _ = recover_publication(repo, validate=lambda values: _validate_pointer_set(repo, values))
            return active_pointers(repo, recover=False)
    result: PointerSet = {}
    for kind, name in POINTERS.items():
        path = repo / "research" / name
        result[kind] = dict(parse_json_object(path.read_text(encoding="utf-8"))) if path.is_file() else None
    return result


def active_state(repo: Path) -> tuple[PointerSet, str]:
    """Возвращает указатели и соответствующий им отпечаток под одной блокировкой."""

    with repository_lock(repo):
        if (repo / "research" / INTENT).is_file():
            _ = recover_publication(repo, validate=lambda values: _validate_pointer_set(repo, values))
        return active_pointers(repo, recover=False), state_fingerprint(repo)


def build_plan(
    repo: Path,
    boundary: str,
    expected_workflow_fingerprint: str,
    *,
    routing_preview: dict[str, object] | None = None,
    profiles: dict[str, Pointer] | None = None,
) -> StagePlan:
    if boundary not in BOUNDARY_STEPS:
        raise ValueError("unsupported recompute boundary")
    with repository_lock(repo):
        if (repo / "research" / INTENT).is_file():
            _ = recover_publication(repo, validate=lambda values: _validate_pointer_set(repo, values))
        current = state_fingerprint(repo)
        pointers = active_pointers(repo, recover=False)
    if expected_workflow_fingerprint != current:
        raise RuntimeError("stale workflow fingerprint")
    if boundary == "sources":
        required = {"routing_plan_fingerprint", "expires_at", "required_tools"}
        if routing_preview is None or not required <= routing_preview.keys() or profiles is None:
            raise ValueError("sources recompute requires routing preview and profile assignments")
        source_inputs: JsonObject | None = {
            "source_routing_preview_id": _value(routing_preview.get("preview_id", "")),
            "routing_plan_fingerprint": _value(routing_preview["routing_plan_fingerprint"]),
            "routing_expires_at": _value(routing_preview["expires_at"]),
            "required_tools": _value(routing_preview["required_tools"]),
            "profile_assignments_fingerprint": safe_profile_fingerprint(profiles),
        }
    else:
        if routing_preview is not None or profiles is not None:
            raise ValueError("routing inputs are only valid for sources")
        source_inputs = None
    if any(not value for value in pointers.values()):
        raise RuntimeError("stage recompute requires active source, diff and MRQ generations")
    plan = StagePlan({
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
    })
    plan["plan_fingerprint"] = fingerprint(plan)
    return plan


def preview(
    repo: Path,
    *,
    boundary: str,
    expected_workflow_fingerprint: str,
    source_routing_preview_id: str | None = None,
    operational_root: Path | None = None,
) -> StagePlan:
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
    route = parse_json_object(route_path.read_text(encoding="utf-8"))
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
        routing_preview=dict(route),
        profiles=_profile_map(parse_json_object(connection_path.read_text(encoding="utf-8"))),
    )


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_json(path: Path, value: object) -> None:
    atomic_json(path, value)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    _sync_directory(path.parent)


def _validate_pointer_set(repo: Path, values: dict[str, Pointer]) -> None:
    from .diffs import validate_active as validate_diff
    from .consolidation import validate_pointer as validate_consolidation
    from .sources import validate_active as validate_source

    source, diff, consolidation = (values[kind] for kind in ("source", "diff", "mrq"))
    source_candidate = source_pointer(_object(source))
    _ = validate_source(repo, deep=True, candidate=source_candidate)
    _ = validate_diff(
        repo,
        candidate=diff_pointer(_object(diff)),
        source_candidate=diff_source_pointer(_object(source)),
    )
    validate_consolidation(_object(consolidation))


def recover_active_publication(repo: Path) -> str | None:
    if not (repo / "research" / INTENT).is_file():
        return None
    with repository_lock(repo):
        return recover_publication(repo, validate=lambda values: _validate_pointer_set(repo, values))


def recover_publication(
    repo: Path,
    *,
    validate: Callable[[dict[str, Pointer]], None] | None = None,
) -> str | None:
    intent_path = repo / "research" / INTENT
    if not intent_path.is_file():
        return None
    intent = parse_json_object(intent_path.read_text(encoding="utf-8"))
    if intent.get("phase") not in {"prepared", "committing"}:
        raise ValueError("invalid publication intent")
    required = {"schema_version", "phase", "kinds", "old", "new", "old_fingerprint", "new_fingerprint"}
    if set(intent) != required or fingerprint(intent["old"]) != intent["old_fingerprint"] or fingerprint(intent["new"]) != intent["new_fingerprint"]:
        raise RuntimeError("critical corrupted stage recompute transaction")
    old = _pointer_set(intent["old"])
    new = _pointer_set(intent["new"])
    kinds = _strings(intent["kinds"])
    selected = old if intent["phase"] == "prepared" else new
    if intent["phase"] == "committing":
        if validate is None:
            raise RuntimeError("critical committing transaction requires candidate validation")
        try:
            validate(_present_pointers(selected))
        except Exception as exc:
            current = active_pointers(repo, recover=False)
            if all(current[kind] == old[kind] for kind in kinds):
                selected = old
                intent["phase"] = "prepared"
            else:
                raise RuntimeError("critical invalid committing stage recompute transaction") from exc
    for kind in kinds:
        pointer = selected[kind]
        if pointer is None:
            raise RuntimeError("critical publication pointer is missing")
        _durable_json(repo / "research" / POINTERS[kind], pointer)
    intent_path.unlink()
    _sync_directory(intent_path.parent)
    return "rolled_back" if intent["phase"] == "prepared" else "committed"


def publish_pointers(
    repo: Path,
    expected: PointerSet,
    candidates: dict[str, Pointer],
    *,
    lease_check: Callable[[], bool],
    validate: Callable[[dict[str, Pointer]], None],
    expected_workflow_fingerprint: str | None = None,
    before_write: Callable[[str], None] | None = None,
) -> dict[str, Pointer]:
    kinds = tuple(candidates)
    if not kinds or any(kind not in POINTERS for kind in kinds):
        raise ValueError("invalid pointer publication set")
    with repository_lock(repo):
        _ = recover_publication(repo, validate=validate)
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
    plan: StagePlan,
    apply: Callable[[str, JsonObject, str, Callable[[], bool]], JsonObject],
    *,
    lease_check: Callable[[], bool],
    payloads: dict[str, JsonObject] | None = None,
    emit: Callable[[str, JsonObject], None] | None = None,
) -> list[StageStep]:
    unsigned = {key: value for key, value in plan.items() if key != "plan_fingerprint"}
    if plan.get("plan_fingerprint") != fingerprint(unsigned):
        raise RuntimeError("stale recompute plan")
    payloads = payloads or {}
    results: list[StageStep] = []
    expected = plan["workflow_fingerprint"]
    for step in plan["steps"]:
        if not lease_check():
            raise RuntimeError("stage recompute lease lost")
        operation = step["operation"]
        if step.get("conditional") and not payloads.get(operation, {}).get("component_ids"):
            results.append({**step, "status": "skipped", "result": {"status": "skipped"}, "output_fingerprint": fingerprint({"status": "skipped"})})
            continue
        if emit:
            emit("step.started", _object(step))
        try:
            result = apply(operation, payloads.get(operation, {}), expected, lambda: not lease_check())
        except Exception as exc:
            raise StageExecutionError(str(exc), results) from exc
        expected = _string(result.get("workflow_fingerprint", expected))
        stop = bool(result.pop("_stop", False))
        output: StageStep = {**step, "result": result, "output_fingerprint": fingerprint(result)}
        if len(results) + 1 < len(plan["steps"]):
            output["next_input_fingerprint"] = plan["steps"][len(results) + 1]["input_fingerprint"]
        if not lease_check():
            raise StageExecutionError("stage recompute lease lost", [*results, output])
        results.append(output)
        if emit:
            emit("step.finished", _object(output))
        if stop:
            break
    return results


def prepare_resume(
    plan: StagePlan,
    predecessor_run: StageRun,
    current_artifacts: dict[str, str],
) -> list[StageStep]:
    prior = predecessor_run.get("steps")
    if predecessor_run.get("boundary") != plan.get("boundary") or predecessor_run.get("status") not in {"failed", "cancelled", "resumable"} or not isinstance(prior, list):
        raise RuntimeError("incompatible predecessor run")
    skipped: list[StageStep] = []
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
    plan: StagePlan,
    *,
    lease_token: str,
    cancelled: Callable[[], bool],
    emit: Callable[[str, JsonObject], None] | None,
    operational_root: Path | None = None,
    predecessor_run: StageRun | None = None,
) -> dict[str, object]:
    if not lease_token:
        raise ValueError("lease token is required")
    from .service import ApplicationService, Staged
    from .user_state import load_connections, workspace_id

    project_root = (operational_root / "projects" / workspace_id(repo)) if operational_root else None
    connections = _connection_profiles(load_connections(repo, operational_root)) if operational_root else None
    service = ApplicationService(
        repo,
        connections=connections,
        upload_drafts=project_root / "upload-drafts" if project_root else None,
        routing_previews=project_root / "source-routing-previews" if project_root else None,
    )
    if plan["boundary"] == "sources":
        expires = str((plan.get("source_inputs") or {}).get("routing_expires_at", "")).replace("Z", "+00:00")
        if not expires or datetime.fromisoformat(expires) <= datetime.now(timezone.utc):
            raise RuntimeError("routing_preview_stale")
        if safe_profile_fingerprint(service.connections or {}) != (plan.get("source_inputs") or {}).get("profile_assignments_fingerprint"):
            raise RuntimeError("source profile assignments changed")
    skipped: list[StageStep] = []
    changed_components: list[str] = []
    if predecessor_run is not None:
        predecessor_record = predecessor_run
        predecessor_result = predecessor_run.get("result") or {}
        resume_run = StageRun(
            boundary=predecessor_run.get("boundary", ""),
            status=predecessor_run.get("status", ""),
            steps=_stage_steps(predecessor_result.get("steps", [])),
        )
        current = active_pointers(repo)
        prior_steps = resume_run.get("steps", [])
        if plan["boundary"] == "sources" and len(prior_steps) >= 3:
            prior_plan = predecessor_record.get("plan")
            prior_plan_steps = prior_plan.get("steps", []) if prior_plan else []
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
                and mrq_result.get("transaction_id") == (current["mrq"] or {}).get("transaction_id")
            )
            if published and prior_plan is not None:
                old_components = {
                    item["component_id"]: item.get("fingerprint")
                    for item in _objects(((prior_plan.get("active_pointers") or {}).get("source") or {}).get("components", []))
                }
                changed_components = [
                    _string(item["component_id"])
                    for item in _objects((current["source"] or {}).get("components", []))
                    if old_components.get(item["component_id"]) != item.get("fingerprint")
                ]
                skipped = plan["steps"][:3]
        artifacts: dict[str, str] = {}
        for step in resume_run.get("steps", []):
            result = step.get("result", {})
            operation = step.get("operation")
            pointer = current["diff"] if operation == "diff.build" else current["mrq"] if operation == "dif.classification-reset" else None
            identifier = result.get("generation_id") or result.get("transaction_id")
            active_identifier = pointer.get("generation_id") if operation == "diff.build" and pointer else pointer.get("transaction_id") if pointer else None
            if identifier and identifier == active_identifier:
                artifacts[operation] = step.get("output_fingerprint", "")
        if not skipped:
            skipped = prepare_resume(plan, resume_run, artifacts)
    source_inputs = plan.get("source_inputs") or {}
    payloads: dict[str, JsonObject] = {
        "sources.acquire": {
            "source_routing_preview_id": source_inputs.get("source_routing_preview_id", ""),
            "routing_plan_fingerprint": source_inputs.get("routing_plan_fingerprint", ""),
        },
        "dif.classification-reset": {
            "actor": "stage-recompute",
            "rationale": f"controlled recompute from {plan['boundary']}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "indexes.build": {"component_ids": [], "mode": "ensure", "confirmed": False},
    }
    staged: Staged = {}
    staged_mrq: Pointer | None = None
    old_pointers = active_pointers(repo)
    try:
        from .dif_classifications import load_active as load_classifications
        prior_classification_rows = load_classifications(repo)["rows"]
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        prior_classification_rows = []
    payloads["indexes.build"]["component_ids"] = changed_components

    def validate_candidates(values: dict[str, Pointer]) -> None:
        _validate_pointer_set(repo, values)

    def apply(operation: str, payload: JsonObject, expected: str, signal: Callable[[], bool]) -> JsonObject:
        nonlocal changed_components, staged_mrq
        candidate_mode = operation in {"sources.acquire", "diff.build", "dif.classification-reset"}
        if operation == "dif.classification-reset":
            from .consolidation import sentinel
            staged_mrq = sentinel()
            result = _object(staged_mrq)
        else:
            result = service.apply(operation, payload, expected, signal, staged=staged if candidate_mode else None)
        if operation == "sources.acquire":
            previous = {
                _string(item["component_id"]): item.get("fingerprint")
                for item in _objects((old_pointers["source"] or {}).get("components", []))
            }
            source_candidate = staged.get("source")
            if source_candidate is None:
                raise RuntimeError("source acquisition did not stage a source pointer")
            changed_components = []
            for item in _objects(source_candidate.get("components", [])):
                component_id = _string(item["component_id"])
                if previous.get(component_id) != item.get("fingerprint"):
                    changed_components.append(component_id)
            payloads["indexes.build"]["component_ids"] = changed_components
        if operation == "diff.build":
            source_candidate = staged.get("source") or old_pointers["source"]
            diff_candidate = staged.get("diff")
            if source_candidate is None or diff_candidate is None:
                raise RuntimeError("diff build did not stage the required pointers")
            source_same = source_candidate.get("generation_id") == (old_pointers["source"] or {}).get("generation_id")
            diff_same = diff_candidate.get("generation_id") == (old_pointers["diff"] or {}).get("generation_id")
            if diff_same and (plan["boundary"] == "diffs" or source_same):
                return {**result, "_stop": True}
        next_fingerprint = expected
        if operation == "dif.classification-reset":
            candidates: dict[str, Pointer] = {}
            if "source" in staged:
                candidates["source"] = staged["source"]
            if "diff" in staged:
                candidates["diff"] = staged["diff"]
            if staged_mrq is not None:
                candidates["mrq"] = staged_mrq
            _ = publish_pointers(
                repo,
                old_pointers,
                candidates,
                lease_check=lambda: not cancelled(),
                validate=validate_candidates,
                expected_workflow_fingerprint=plan["workflow_fingerprint"],
            )
            from .dif_classifications import physical_evidence_fingerprints, publish_empty, publish_window, reusable_rows
            classification_path = repo / "research/active-dif-classification-generation.json"
            current_classification = parse_json_object(classification_path.read_text(encoding="utf-8")) if classification_path.is_file() else {}
            classification = publish_empty(
                repo,
                expected_generation_id=(
                    _string(current_classification["generation_id"])
                    if "generation_id" in current_classification
                    else None
                ),
            )
            current_evidence = physical_evidence_fingerprints(repo)
            expected_reuse = {
                row["stable_diff_id"]: {
                    "evidence_fingerprint": current_evidence.get(row["stable_diff_id"], ""),
                    "result_schema_fingerprint": row["result_schema_fingerprint"],
                    "profile_fingerprint": row["profile_fingerprint"],
                    "instruction_fingerprint": row["instruction_fingerprint"],
                    "context_fingerprint": row["context_fingerprint"],
                }
                for row in prior_classification_rows
                if row["stable_diff_id"] in current_evidence
            }
            compatible = reusable_rows(prior_classification_rows, expected_reuse)
            if compatible:
                classification = publish_window(
                    repo, compatible,
                    expected_generation_id=classification["generation_id"],
                )
            result = {**result, "classification_generation_id": classification["generation_id"]}
            next_fingerprint = _string(service.snapshot(deep=False)["workflow_fingerprint"])
        elif not candidate_mode:
            next_fingerprint = _string(result.get("workflow_fingerprint", expected))
        return {**result, "workflow_fingerprint": next_fingerprint}

    execution_plan = _plan_with_steps(plan, plan["steps"][len(skipped):]) if skipped else plan
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
