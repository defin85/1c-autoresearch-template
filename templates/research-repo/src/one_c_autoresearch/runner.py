from __future__ import annotations

import uuid
import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict

from .contracts import JsonValue, canonical_json, json_object, parse_json_object, sha256
from .events import EventStore, process_identity
from .workflow import OPERATION_CATALOG, next_work, status, step_configurations


AGENT_OPERATIONS = {"dif.classify-next", "mrq.consolidate", "mrq.classify-batches", "mrq.decide-next"}
AUTOMATABLE = {"sources.acquire", "diff.build", "indexes.build", "projections.build", "workflow.verify", *AGENT_OPERATIONS}
APPROVAL_REQUIRED = {"sources.acquire"}
LOCATION = {
    "project.validate": ("configure", "validate-project"), "sources.acquire": ("acquire-sources", "acquire-sources"),
    "diff.build": ("build-diffs", "build-diffs"), "indexes.build": ("index-sources", "index-sources"),
    "dif.classify-next": ("analyze-dif", "analyze-dif"),
    "mrq.consolidate": ("consolidate-mrq", "consolidate-mrq"),
    "mrq.classify-batches": ("classify-mrq", "classify-mrq"),
    "mrq.decide-next": ("decide-mrq", "decide-mrq"),
    "projections.build": ("publish", "build-projections"), "workflow.verify": ("publish", "verify-workflow"),
}

JsonObject = dict[str, JsonValue]


class Work(TypedDict):
    action: str
    gate_id: NotRequired[str | None]
    job_id: NotRequired[str]
    workflow_fingerprint: NotRequired[str]
    blocker: NotRequired[JsonObject]
    work_unit: NotRequired[JsonObject]


class StepConfiguration(TypedDict):
    id: str
    max_retries: int
    parameters: JsonObject


class Invoker(Protocol):
    def __call__(self, operation: str, payload: JsonObject, cancelled: Callable[[], bool]) -> JsonObject: ...


class Selector(Protocol):
    def __call__(self) -> object: ...


def _work(value: object) -> Work | None:
    if value is None:
        return None
    source = json_object(value)
    action = source.get("action")
    if not isinstance(action, str):
        raise ValueError("workflow work unit has no action")
    result: Work = {"action": action}
    gate_id = source.get("gate_id")
    job_id = source.get("job_id")
    workflow_fingerprint = source.get("workflow_fingerprint")
    if gate_id is None or isinstance(gate_id, str):
        result["gate_id"] = gate_id
    if isinstance(job_id, str):
        result["job_id"] = job_id
    if isinstance(workflow_fingerprint, str):
        result["workflow_fingerprint"] = workflow_fingerprint
    if "blocker" in source:
        result["blocker"] = json_object(source["blocker"])
    if "work_unit" in source:
        result["work_unit"] = json_object(source["work_unit"])
    return result


def _step_configuration(value: object) -> StepConfiguration:
    step = json_object(json_object(value).get("step"))
    step_id = step.get("id")
    retries = step.get("max_retries", 0)
    if not isinstance(step_id, str) or not isinstance(retries, int) or isinstance(retries, bool):
        raise ValueError("invalid workflow step configuration")
    return {"id": step_id, "max_retries": retries, "parameters": step}


def _work_payload(work: Work) -> JsonObject:
    result: JsonObject = {"action": work["action"]}
    gate_id = work.get("gate_id")
    job_id = work.get("job_id")
    workflow_fingerprint = work.get("workflow_fingerprint")
    blocker = work.get("blocker")
    work_unit = work.get("work_unit")
    if gate_id is not None:
        result["gate_id"] = gate_id
    if job_id is not None:
        result["job_id"] = job_id
    if workflow_fingerprint is not None:
        result["workflow_fingerprint"] = workflow_fingerprint
    if blocker is not None:
        result["blocker"] = blocker
    if work_unit is not None:
        result["work_unit"] = work_unit
    return result


def _string(value: JsonValue, message: str) -> str:
    if not isinstance(value, str):
        raise ValueError(message)
    return value


def _strings(value: JsonValue) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("expected string array")
    return [item for item in value if isinstance(item, str)]


def _objects(value: JsonValue) -> list[JsonObject]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("expected object array")
    return [item for item in value if isinstance(item, dict)]


def run_next(
    repo: Path,
    invoke: Invoker,
    store: EventStore,
    actor: str = "local-user",
    select: Selector | None = None,
    approved_operations: set[str] | None = None,
    agent_profiles: dict[str, JsonObject] | None = None,
    agent_executor: object | None = None,
    source_routing_preview: dict[str, str] | None = None,
    run_id: str | None = None,
    precreated: bool = False,
) -> JsonObject:
    _ = agent_profiles, agent_executor
    run_started = time.monotonic()
    elapsed: Callable[[], float] = lambda: time.monotonic() - run_started
    _ = store.reconcile()
    before = json_object(status(repo))
    work = _work(select() if select is not None else next_work(repo))
    if work is not None and work["action"] in AGENT_OPERATIONS:
        work_payload = _work_payload(work)
        return {
            "run_id": "",
            "result": "blocked",
            "work": work_payload,
            "blocker": {
                "code": "dispatcher.required",
                "message": "agent operations require the snapshot-backed dispatcher",
                "action": work["action"],
            },
        }
    run_id = run_id or str(uuid.uuid4())
    if not precreated:
        _ = store.emit("run.created", run_id, {"status": "running", "actor": actor, "process_identity": process_identity(), "workflow_fingerprint": before["workflow_fingerprint"], "operation": work.get("action") if work else "none", "work_unit": _work_payload(work) if work else None})
    if work is None:
        _ = store.emit("run.finished", run_id, {"status": "completed", "result": "already_complete", "duration_seconds": elapsed()})
        return {"run_id": run_id, "result": "already_complete", "snapshot": before}
    operation = work["action"]
    work_payload = _work_payload(work)
    job_id, step_id = LOCATION.get(operation, (work.get("job_id", "unknown"), operation.replace(".", "-")))
    configurations: Iterable[object] = step_configurations(repo)
    configured = next(item for item in map(_step_configuration, configurations) if item["id"] == step_id)
    catalog_source: object = OPERATION_CATALOG[operation]
    catalog = json_object(catalog_source)
    executor_name = _string(catalog.get("executor"), "operation catalog has no executor")
    operation_version = _string(catalog.get("version"), "operation catalog has no version")
    try:
        pointers = {
            name: parse_json_object((repo / "research" / name).read_text(encoding="utf-8"))
            for name in ("active-source-generation.json", "active-diff-generation.json", "active-consolidation-generation.json")
        }
    except (OSError, ValueError, json.JSONDecodeError):
        pointers = {}
    work_unit = work.get("work_unit", {})
    context: JsonObject = {"actor": actor, "executor": executor_name, "operation": operation, "operation_version": operation_version, "gate_id": work.get("gate_id"), "work_unit": work_unit, "work_unit_id": work_unit.get("id"), "source_generation_id": pointers.get("active-source-generation.json", {}).get("generation_id"), "diff_generation_id": pointers.get("active-diff-generation.json", {}).get("generation_id"), "canonical_generation_id": pointers.get("active-consolidation-generation.json", {}).get("mrq_generation_id"), "index_key": work_unit.get("index_key"), "index_keys": work_unit.get("index_keys")}
    runtime_payload: JsonObject = {"mode": "ensure"} if operation == "indexes.build" else ({**(source_routing_preview or {})} if operation == "sources.acquire" else {})
    idempotency_key = sha256(canonical_json({"manifest_fingerprint": before["manifest_fingerprint"], "job_id": job_id, "step_id": step_id, "work_unit_id": work_unit.get("id"), "source_generation_id": context["source_generation_id"], "diff_generation_id": context["diff_generation_id"], "canonical_generation_id": context["canonical_generation_id"], "parameters": configured["parameters"], "runtime_payload": runtime_payload}))
    accepted = store.accepted(idempotency_key)
    if accepted:
        current = json_object(status(repo))
        current_work = _work(select() if select is not None else next_work(repo))
        if current_work != work:
            _ = store.emit("run.finished", run_id, {**context, "status": "completed", "result": "reused", "idempotency_key": idempotency_key, "duration_seconds": elapsed()})
            return {"run_id": run_id, "result": "reused", "accepted_result": accepted["result"], "snapshot": current}
    if operation in APPROVAL_REQUIRED and operation not in (approved_operations or set()):
        work_blocker = work.get("blocker", {})
        _ = store.emit("job.started", run_id, {**context, "status": "running"}, job_id=job_id)
        _ = store.emit("step.started", run_id, {**context, "status": "running", "inputs": runtime_payload, "input_fingerprint": idempotency_key}, job_id=job_id, step_id=step_id, attempt=1)
        _ = store.emit("approval.required", run_id, {**context, "status": "blocked", "blocker": work_blocker}, job_id=job_id, step_id=step_id, attempt=1)
        _ = store.emit("step.finished", run_id, {**context, "status": "blocked", "blocker": work_blocker, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=1)
        _ = store.emit("job.finished", run_id, {**context, "status": "blocked", "blocker": work_blocker, "duration_seconds": elapsed()}, job_id=job_id)
        _ = store.emit("run.finished", run_id, {**context, "status": "blocked", "blocker": work_blocker, "duration_seconds": elapsed()})
        return {"run_id": run_id, "result": "blocked", "work": work_payload}
    if operation not in AUTOMATABLE:
        blocker: JsonObject = {"code": "executor.agent_profile_missing", "message": "user-scope agent profile is unavailable: None", "action": operation}
        _ = store.emit("job.started", run_id, {**context, "status": "running"}, job_id=job_id)
        _ = store.emit("step.started", run_id, {**context, "status": "running", "inputs": runtime_payload, "input_fingerprint": idempotency_key}, job_id=job_id, step_id=step_id, attempt=1)
        _ = store.emit("step.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=1)
        _ = store.emit("job.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id)
        _ = store.emit("run.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()})
        return {"run_id": run_id, "result": "blocked", "work": work_payload, "blocker": blocker}
    _ = store.emit("job.started", run_id, {**context, "status": "running"}, job_id=job_id)
    max_attempts = configured["max_retries"] + 1
    for attempt in range(1, max_attempts + 1):
      if store.cancellation(run_id):
        blocker = {"code": "run.cancelled", "message": "run cancelled by local user", "action": operation}
        _ = store.emit("step.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=attempt)
        _ = store.emit("job.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id)
        _ = store.emit("run.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()})
        return {"run_id": run_id, "result": "cancelled", "work": work_payload, "blocker": blocker}
      _ = store.emit("step.started", run_id, {**context, "status": "running", "inputs": runtime_payload, "input_fingerprint": idempotency_key}, job_id=job_id, step_id=step_id, attempt=attempt)
      try:
        _ = store.emit("step.action", run_id, {**context, "status": "running", "effective_action": runtime_payload}, job_id=job_id, step_id=step_id, attempt=attempt)
        output: JsonObject = {}
        component_ids = sorted(_strings(work_unit.get("component_ids", []))) if operation == "indexes.build" else []
        if component_ids:
            output_components: list[JsonValue] = []
            output = {"components": output_components}
            for index, component_id in enumerate(component_ids, 1):
                if store.cancellation(run_id):
                    raise InterruptedError("index build cancelled")
                current = invoke(operation, {"mode": "ensure", "component_ids": [component_id], "confirmed": False}, lambda: bool(store.cancellation(run_id)))
                output_components.extend(_objects(current.get("components")))
                index_keys = json_object(work_unit.get("index_keys", {}))
                index_key = index_keys.get(component_id)
                _ = store.emit("step.progress", run_id, {**context, "index_key": index_key, "status": "running", "progress": {"current": index, "total": len(component_ids), "component_id": component_id}}, job_id=job_id, step_id=step_id, attempt=attempt)
                log = store.append_log(run_id, attempt, canonical_json({"component_id": component_id, "result": current}) + b"\n")
                _ = store.emit("log.append", run_id, {**context, "index_key": index_key, "status": "running", "log": log}, job_id=job_id, step_id=step_id, attempt=attempt)
        else:
            output = invoke(operation, runtime_payload, lambda: bool(store.cancellation(run_id)))
        if operation == "sources.acquire":
            components = _objects(output.get("components", []))
            for index, component in enumerate(components, 1):
                _ = store.emit("step.progress", run_id, {**context, "status": "running", "progress": {"current": index, "total": len(components), "component_id": component["component_id"]}}, job_id=job_id, step_id=step_id, attempt=attempt)
        _ = store.emit("step.output", run_id, {**context, "status": "completed", "outputs": output, "output_fingerprint": sha256(canonical_json(output))}, job_id=job_id, step_id=step_id, attempt=attempt)
        after = json_object(status(repo))
        after_work = _work(select() if select is not None else next_work(repo))
        if after["workflow_fingerprint"] == before["workflow_fingerprint"] and after == before and after_work == work:
            if operation == "workflow.verify":
                blocker = {"code": "verification.no_progress", "message": "strict verification is still blocked by repository state", "action": "workflow.verify"}
                _ = store.emit("step.validation", run_id, {**context, "status": "blocked", "validation": after, "blocker": blocker}, job_id=job_id, step_id=step_id, attempt=attempt)
                _ = store.emit("step.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=attempt)
                _ = store.emit("job.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id)
                _ = store.emit("run.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()})
                return {"run_id": run_id, "result": "blocked", "work": work_payload, "blocker": blocker, "snapshot": after}
            raise RuntimeError("accepted operation made no observable progress")
        _ = store.emit("step.validation", run_id, {**context, "status": "completed", "validation": after}, job_id=job_id, step_id=step_id, attempt=attempt)
        _ = store.emit("step.finished", run_id, {**context, "status": "completed", "exit": {"status": "completed"}, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=attempt)
        _ = store.emit("job.finished", run_id, {**context, "status": "completed", "duration_seconds": elapsed()}, job_id=job_id)
        result: JsonObject = {"run_id": run_id, "result": "progressed", "snapshot": after}
        store.accept(idempotency_key, result)
        _ = store.emit("run.finished", run_id, {**context, "status": "completed", "result": "progressed", "idempotency_key": idempotency_key, "duration_seconds": elapsed()})
        return result
      except InterruptedError as exc:
        blocker = {"code": "run.cancelled", "message": str(exc), "action": operation}
        _ = store.emit("step.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=attempt)
        _ = store.emit("job.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id)
        _ = store.emit("run.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()})
        return {"run_id": run_id, "result": "cancelled", "work": work_payload, "blocker": blocker}
      except Exception as exc:
        retryable = isinstance(exc, OSError) and "transient_io" in _strings(catalog.get("retryable", [])) and attempt < max_attempts and status(repo)["workflow_fingerprint"] == before["workflow_fingerprint"]
        _ = store.emit("step.finished", run_id, {**context, "status": "interrupted" if retryable else "failed", "error_class": type(exc).__name__, "exit": {"message": str(exc)}, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=attempt)
        if retryable:
            continue
        _ = store.emit("job.finished", run_id, {**context, "status": "failed", "error_class": type(exc).__name__, "message": str(exc), "duration_seconds": elapsed()}, job_id=job_id)
        _ = store.emit("run.finished", run_id, {**context, "status": "failed", "error_class": type(exc).__name__, "message": str(exc), "duration_seconds": elapsed()})
        raise
    raise RuntimeError("runner exhausted attempts without a result")


def run_until_blocked(
    repo: Path,
    invoke: Invoker,
    store: EventStore,
    *,
    max_units: int = 100,
    actor: str = "local-user",
    select: Selector | None = None,
    approved_operations: set[str] | None = None,
    agent_profiles: dict[str, JsonObject] | None = None,
    agent_executor: object | None = None,
    source_routing_preview: dict[str, str] | None = None,
) -> JsonObject:
    if not 1 <= max_units <= 1000:
        raise ValueError("max_units must be 1..1000")
    runs: list[JsonObject] = []
    for _ in range(max_units):
        result = run_next(repo, invoke, store, actor, select, approved_operations, agent_profiles, agent_executor, source_routing_preview)
        runs.append(result)
        if result["result"] != "progressed":
            break
    else:
        return {"result": "bounded", "runs": runs, "snapshot": json_object(status(repo))}
    return {"result": runs[-1]["result"], "runs": runs, "snapshot": json_object(status(repo))}
