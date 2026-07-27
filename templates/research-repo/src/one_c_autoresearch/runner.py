from __future__ import annotations

import uuid
import json
import time
from pathlib import Path
from typing import Any, Callable

from .contracts import canonical_json, sha256
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


def run_next(repo: Path, invoke: Callable[[str, dict[str, Any], Callable[[], bool]], dict[str, Any]], store: EventStore, actor: str = "local-user", select: Callable[[], dict[str, Any] | None] | None = None, approved_operations: set[str] | None = None, agent_profiles: dict[str, dict[str, Any]] | None = None, agent_executor: Callable[..., dict[str, Any]] | None = None, source_routing_preview: dict[str, str] | None = None, run_id: str | None = None, precreated: bool = False) -> dict[str, Any]:
    run_started = time.monotonic()
    elapsed = lambda: time.monotonic() - run_started
    store.reconcile()
    before = status(repo); work = (select or (lambda: next_work(repo)))()
    if work is not None and work.get("action") in AGENT_OPERATIONS:
        return {
            "run_id": "",
            "result": "blocked",
            "work": work,
            "blocker": {
                "code": "dispatcher.required",
                "message": "agent operations require the snapshot-backed dispatcher",
                "action": work["action"],
            },
        }
    run_id = run_id or str(uuid.uuid4())
    if not precreated:
        store.emit("run.created", run_id, {"status": "running", "actor": actor, "process_identity": process_identity(), "workflow_fingerprint": before["workflow_fingerprint"], "operation": work.get("action") if work else "none", "work_unit": work})
    if work is None:
        store.emit("run.finished", run_id, {"status": "completed", "result": "already_complete", "duration_seconds": elapsed()})
        return {"run_id": run_id, "result": "already_complete", "snapshot": before}
    operation = work["action"]
    job_id, step_id = LOCATION.get(operation, (str(work.get("job_id", "unknown")), operation.replace(".", "-")))
    configured = next(item for item in step_configurations(repo) if item["step"]["id"] == step_id)
    catalog = OPERATION_CATALOG[operation]
    primary_role = {"dif.classify-next": "analyzer", "mrq.consolidate": "grouper", "mrq.classify-batches": "classifier", "mrq.decide-next": "researcher"}.get(operation, "")
    role_policy = next((role for phase in configured["step"].get("agent_phases", []) for role in phase["roles"] if role["role_id"] == primary_role), None) if operation in AGENT_OPERATIONS else None
    profile_name = role_policy.get("agent_profile") if role_policy else None
    profile = (agent_profiles or {}).get(profile_name) if profile_name else None
    try:
        pointers = {name: json.loads((repo / "research" / name).read_text(encoding="utf-8")) for name in ("active-source-generation.json", "active-diff-generation.json", "active-consolidation-generation.json")}
    except (OSError, json.JSONDecodeError):
        pointers = {}
    context = {"actor": actor, "executor": catalog["executor"], "operation": operation, "operation_version": catalog["version"], "gate_id": work.get("gate_id"), "work_unit": work.get("work_unit"), "work_unit_id": (work.get("work_unit") or {}).get("id"), "source_generation_id": pointers.get("active-source-generation.json", {}).get("generation_id"), "diff_generation_id": pointers.get("active-diff-generation.json", {}).get("generation_id"), "canonical_generation_id": pointers.get("active-consolidation-generation.json", {}).get("mrq_generation_id"), "index_key": (work.get("work_unit") or {}).get("index_key"), "index_keys": (work.get("work_unit") or {}).get("index_keys")}
    runtime_payload = {"mode": "ensure"} if operation == "indexes.build" else ({"agent_profile": profile_name, "agent_profile_fingerprint": sha256(canonical_json(profile)) if profile else None, "model": profile.get("model") if profile else None, "instructions_version": profile.get("instructions_version") if profile else None, "environment_preset": profile.get("environment_preset") if profile else None, "instruction_supplement": role_policy.get("instruction_supplement", "") if role_policy else "", "agent_phases": configured["step"].get("agent_phases", []), "allowed_paths": (work.get("work_unit") or {}).get("allowed_paths", []), "tool_calls": [{"tool": "codex-cli", "sandbox": "read-only"}], "private_reasoning": "unavailable"} if operation in AGENT_OPERATIONS else ({**(source_routing_preview or {})} if operation == "sources.acquire" else {}))
    idempotency_key = sha256(canonical_json({"manifest_fingerprint": before["manifest_fingerprint"], "job_id": job_id, "step_id": step_id, "work_unit_id": (work.get("work_unit") or {}).get("id"), "source_generation_id": context["source_generation_id"], "diff_generation_id": context["diff_generation_id"], "canonical_generation_id": context["canonical_generation_id"], "parameters": configured["step"], "runtime_payload": runtime_payload}))
    accepted = store.accepted(idempotency_key)
    if accepted:
        current = status(repo)
        current_work = (select or (lambda: next_work(repo)))()
        if current_work != work:
            store.emit("run.finished", run_id, {**context, "status": "completed", "result": "reused", "idempotency_key": idempotency_key, "duration_seconds": elapsed()})
            return {"run_id": run_id, "result": "reused", "accepted_result": accepted["result"], "snapshot": current}
    if operation in APPROVAL_REQUIRED and operation not in (approved_operations or set()):
        store.emit("job.started", run_id, {**context, "status": "running"}, job_id=job_id)
        store.emit("step.started", run_id, {**context, "status": "running", "inputs": runtime_payload, "input_fingerprint": idempotency_key}, job_id=job_id, step_id=step_id, attempt=1)
        store.emit("approval.required", run_id, {**context, "status": "blocked", "blocker": work["blocker"]}, job_id=job_id, step_id=step_id, attempt=1)
        store.emit("step.finished", run_id, {**context, "status": "blocked", "blocker": work["blocker"], "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=1)
        store.emit("job.finished", run_id, {**context, "status": "blocked", "blocker": work["blocker"], "duration_seconds": elapsed()}, job_id=job_id)
        store.emit("run.finished", run_id, {**context, "status": "blocked", "blocker": work["blocker"], "duration_seconds": elapsed()})
        return {"run_id": run_id, "result": "blocked", "work": work}
    if operation not in AUTOMATABLE or operation in AGENT_OPERATIONS and profile is None:
        blocker = {"code": "executor.agent_profile_missing", "message": f"user-scope agent profile is unavailable: {profile_name}", "action": operation}
        store.emit("job.started", run_id, {**context, "status": "running"}, job_id=job_id)
        store.emit("step.started", run_id, {**context, "status": "running", "inputs": runtime_payload, "input_fingerprint": idempotency_key}, job_id=job_id, step_id=step_id, attempt=1)
        store.emit("step.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=1)
        store.emit("job.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id)
        store.emit("run.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()})
        return {"run_id": run_id, "result": "blocked", "work": work, "blocker": blocker}
    store.emit("job.started", run_id, {**context, "status": "running"}, job_id=job_id)
    max_attempts = int(configured["step"].get("max_retries", 0)) + 1
    for attempt in range(1, max_attempts + 1):
      if store.cancellation(run_id):
        blocker = {"code": "run.cancelled", "message": "run cancelled by local user", "action": operation}
        store.emit("step.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=attempt)
        store.emit("job.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id)
        store.emit("run.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()})
        return {"run_id": run_id, "result": "cancelled", "work": work, "blocker": blocker}
      store.emit("step.started", run_id, {**context, "status": "running", "inputs": runtime_payload, "input_fingerprint": idempotency_key}, job_id=job_id, step_id=step_id, attempt=attempt)
      try:
        store.emit("step.action", run_id, {**context, "status": "running", "effective_action": runtime_payload}, job_id=job_id, step_id=step_id, attempt=attempt)
        if operation == "mrq.classify-batches":
            from .mrq_batches import load_active, publish as publish_batches, source_mrq_payload, stable_windows
            from .pipeline_graphs import _validated_window_batches
            try:
                batches = load_active(repo)
                output = {"status": "completed", "result": "reused", "batch_ids": [batch.batch_id for batch in batches]}
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                executor = agent_executor
                if executor is None:
                    from .agents import execute as executor
                _generation_id, fingerprint, records = source_mrq_payload(repo)
                batches = []
                for index, window in enumerate(stable_windows(records)):
                    if source_mrq_payload(repo)[1] != fingerprint:
                        raise RuntimeError("source_mrq_fingerprint_stale")
                    unit = {"id": f"window:{index}", "kind": "mrq-batch-window", "source_mrq_fingerprint": fingerprint, "mrqs": window, "allowed_paths": sorted({evidence["path"] for record in window for evidence in record["source_evidence"]})}
                    proposal_payload = executor(repo, store.root / "proposal-work" / sha256(f"{run_id}:{index}".encode()), (agent_profiles or {})[profile_name], operation, unit, role_policy.get("instruction_supplement", "") if role_policy else "", int(configured["step"].get("timeout_seconds", 1800)), lambda: bool(store.cancellation(run_id)))
                    batches.extend(_validated_window_batches(window, proposal_payload))
                if source_mrq_payload(repo)[1] != fingerprint:
                    raise RuntimeError("source_mrq_fingerprint_stale")
                binding = publish_batches(repo, batches, fingerprint)
                output = {"status": "completed", "batch_generation": binding, "batch_ids": [batch.batch_id for batch in batches]}
        elif operation in AGENT_OPERATIONS:
            proposal = store.proposal(idempotency_key)
            new_proposal = proposal is None
            if proposal is None:
                executor = agent_executor
                if executor is None:
                    from .agents import execute as executor
                payload = executor(repo, store.root / "proposal-work" / sha256(run_id.encode()), (agent_profiles or {})[profile_name], operation, work.get("work_unit") or {}, role_policy.get("instruction_supplement", "") if role_policy else "", int(configured["step"].get("timeout_seconds", 1800)), lambda: bool(store.cancellation(run_id)))
                from .agents import validate_proposal
                validate_proposal(operation, payload, work.get("work_unit") or {})
                proposal = store.save_proposal(idempotency_key, operation, profile_name, payload)
            if new_proposal or operation not in (approved_operations or set()):
                output = {"proposal": proposal, "requires_approval": True}
                store.emit("step.output", run_id, {**context, "status": "blocked", "outputs": output, "output_fingerprint": sha256(canonical_json(output))}, job_id=job_id, step_id=step_id, attempt=attempt)
                blocker = {"code": "approval.agent_proposal", "message": "agent proposal requires explicit local-user approval", "action": operation}
                store.emit("approval.required", run_id, {**context, "status": "blocked", "actor": actor, "proposal": proposal, "blocker": blocker}, job_id=job_id, step_id=step_id, attempt=attempt)
                store.emit("step.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=attempt)
                store.emit("job.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id)
                store.emit("run.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()})
                return {"run_id": run_id, "result": "blocked", "work": work, "proposal": proposal, "blocker": blocker}
            canonical_operation = "mrq.propose" if operation == "mrq.consolidate" else "mrq.decide"
            output = invoke(canonical_operation, proposal["payload"], lambda: bool(store.cancellation(run_id)))
        else:
            output = None
        component_ids = sorted((work.get("work_unit") or {}).get("component_ids", [])) if operation == "indexes.build" else []
        if operation in AGENT_OPERATIONS:
            pass
        elif component_ids:
            output = {"components": []}
            for index, component_id in enumerate(component_ids, 1):
                if store.cancellation(run_id):
                    raise InterruptedError("index build cancelled")
                current = invoke(operation, {"mode": "ensure", "component_ids": [component_id], "confirmed": False}, lambda: bool(store.cancellation(run_id)))
                output["components"].extend(current["components"])
                index_key = (work.get("work_unit") or {}).get("index_keys", {}).get(component_id)
                store.emit("step.progress", run_id, {**context, "index_key": index_key, "status": "running", "progress": {"current": index, "total": len(component_ids), "component_id": component_id}}, job_id=job_id, step_id=step_id, attempt=attempt)
                log = store.append_log(run_id, attempt, canonical_json({"component_id": component_id, "result": current}) + b"\n")
                store.emit("log.append", run_id, {**context, "index_key": index_key, "status": "running", "log": log}, job_id=job_id, step_id=step_id, attempt=attempt)
        else:
            output = invoke(operation, runtime_payload, lambda: bool(store.cancellation(run_id)))
        if operation == "sources.acquire":
            components = output.get("components", [])
            for index, component in enumerate(components, 1):
                store.emit("step.progress", run_id, {**context, "status": "running", "progress": {"current": index, "total": len(components), "component_id": component["component_id"]}}, job_id=job_id, step_id=step_id, attempt=attempt)
        store.emit("step.output", run_id, {**context, "status": "completed", "outputs": output, "output_fingerprint": sha256(canonical_json(output))}, job_id=job_id, step_id=step_id, attempt=attempt)
        after = status(repo)
        after_work = (select or (lambda: next_work(repo)))()
        if after["workflow_fingerprint"] == before["workflow_fingerprint"] and after == before and after_work == work:
            if operation == "workflow.verify":
                blocker = {"code": "verification.no_progress", "message": "strict verification is still blocked by repository state", "action": "workflow.verify"}
                store.emit("step.validation", run_id, {**context, "status": "blocked", "validation": after, "blocker": blocker}, job_id=job_id, step_id=step_id, attempt=attempt)
                store.emit("step.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=attempt)
                store.emit("job.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id)
                store.emit("run.finished", run_id, {**context, "status": "blocked", "blocker": blocker, "duration_seconds": elapsed()})
                return {"run_id": run_id, "result": "blocked", "work": work, "blocker": blocker, "snapshot": after}
            raise RuntimeError("accepted operation made no observable progress")
        store.emit("step.validation", run_id, {**context, "status": "completed", "validation": after}, job_id=job_id, step_id=step_id, attempt=attempt)
        store.emit("step.finished", run_id, {**context, "status": "completed", "exit": {"status": "completed"}, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=attempt)
        store.emit("job.finished", run_id, {**context, "status": "completed", "duration_seconds": elapsed()}, job_id=job_id)
        result = {"run_id": run_id, "result": "progressed", "snapshot": after}
        store.accept(idempotency_key, result)
        store.emit("run.finished", run_id, {**context, "status": "completed", "result": "progressed", "idempotency_key": idempotency_key, "duration_seconds": elapsed()})
        return result
      except InterruptedError as exc:
        blocker = {"code": "run.cancelled", "message": str(exc), "action": operation}
        store.emit("step.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=attempt)
        store.emit("job.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()}, job_id=job_id)
        store.emit("run.finished", run_id, {**context, "status": "cancelled", "blocker": blocker, "duration_seconds": elapsed()})
        return {"run_id": run_id, "result": "cancelled", "work": work, "blocker": blocker}
      except Exception as exc:
        retryable = isinstance(exc, OSError) and "transient_io" in catalog["retryable"] and attempt < max_attempts and status(repo)["workflow_fingerprint"] == before["workflow_fingerprint"]
        store.emit("step.finished", run_id, {**context, "status": "interrupted" if retryable else "failed", "error_class": type(exc).__name__, "exit": {"message": str(exc)}, "duration_seconds": elapsed()}, job_id=job_id, step_id=step_id, attempt=attempt)
        if retryable:
            continue
        store.emit("job.finished", run_id, {**context, "status": "failed", "error_class": type(exc).__name__, "message": str(exc), "duration_seconds": elapsed()}, job_id=job_id)
        store.emit("run.finished", run_id, {**context, "status": "failed", "error_class": type(exc).__name__, "message": str(exc), "duration_seconds": elapsed()})
        raise


def run_until_blocked(repo: Path, invoke: Callable[[str, dict[str, Any], Callable[[], bool]], dict[str, Any]], store: EventStore, *, max_units: int = 100, actor: str = "local-user", select: Callable[[], dict[str, Any] | None] | None = None, approved_operations: set[str] | None = None, agent_profiles: dict[str, dict[str, Any]] | None = None, agent_executor: Callable[..., dict[str, Any]] | None = None, source_routing_preview: dict[str, str] | None = None) -> dict[str, Any]:
    if not 1 <= max_units <= 1000:
        raise ValueError("max_units must be 1..1000")
    runs = []
    for _ in range(max_units):
        result = run_next(repo, invoke, store, actor, select, approved_operations, agent_profiles, agent_executor, source_routing_preview)
        runs.append(result)
        if result["result"] != "progressed":
            break
    else:
        return {"result": "bounded", "runs": runs, "snapshot": status(repo)}
    return {"result": runs[-1]["result"], "runs": runs, "snapshot": status(repo)}
