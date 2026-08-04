from __future__ import annotations

import csv
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, TypedDict

from .contracts import JsonValue, ROLES, canonical_json, json_array, json_object, owned_function, parse_json_object, reject_secrets, sha256
from . import __version__

if TYPE_CHECKING:
    from .sqlite_state import DispatcherStore


class AgentRole(TypedDict):
    role_id: str
    agent_profile: str
    count: int
    instruction_supplement: str


class AgentPhase(TypedDict):
    phase_id: str
    mode: str
    max_concurrency: int
    roles: list[AgentRole]


class WorkflowStep(TypedDict):
    id: str
    operation: str
    operation_version: str
    timeout_seconds: NotRequired[int]
    max_retries: NotRequired[int]
    agent_phases: NotRequired[list[AgentPhase]]


class WorkflowJob(TypedDict):
    id: str
    needs: list[str]
    steps: list[WorkflowStep]


class WorkflowGate(TypedDict):
    id: str
    validator: str


class WorkflowManifest(TypedDict):
    schema_version: str
    tool_version: str
    gates: list[WorkflowGate]
    jobs: list[WorkflowJob]


class StepConfiguration(TypedDict):
    job_id: str
    step: WorkflowStep
    catalog: dict[str, object]


class BlockerValue(TypedDict):
    code: str
    message: str
    action: str


class GateValue(TypedDict):
    id: str
    state: str
    blockers: list[BlockerValue]


class WorkflowSnapshot(TypedDict):
    schema_version: str
    tool_version: str
    operation_versions: dict[str, str]
    workflow_fingerprint: str
    manifest_fingerprint: str
    state: str
    gates: list[GateValue]


GATES = (
    ("project-configured", "project.validate"),
    ("sources-acquired", "sources.validate"),
    ("diffs-built", "diff.validate"),
    ("all-dif-classified", "dif.classification"),
    ("mrq-consolidated", "mrq.ownership"),
    ("source-evidence-complete", "mrq.source-evidence"),
    ("decisions-approved", "mrq.approvals"),
    ("published", "workflow.verify"),
)
JOBS = (
    ("configure", (), (("validate-project", "project.validate", "1"),)),
    ("acquire-sources", ("configure",), (("acquire-sources", "sources.acquire", "1"),)),
    ("build-diffs", ("acquire-sources",), (("build-diffs", "diff.build", "1"),)),
    ("index-sources", ("build-diffs",), (("index-sources", "indexes.build", "1"),)),
    ("analyze-dif", ("index-sources",), (("analyze-dif", "dif.classify-next", "1"),)),
    ("consolidate-mrq", ("analyze-dif",), (("consolidate-mrq", "mrq.consolidate", "1"),)),
    ("classify-mrq", ("consolidate-mrq",), (("classify-mrq", "mrq.classify-batches", "1"),)),
    ("decide-mrq", ("classify-mrq",), (("decide-mrq", "mrq.decide-next", "2"),)),
    ("publish", ("decide-mrq",), (("build-projections", "projections.build", "1"), ("verify-workflow", "workflow.verify", "1"))),
)
PARAMETERS = {
    "project.validate": {"timeout_seconds"},
    "sources.acquire": {"timeout_seconds"},
    "diff.build": {"timeout_seconds", "max_retries"},
    "indexes.build": {"timeout_seconds"},
    "dif.classify-next": {"timeout_seconds", "agent_phases"},
    "mrq.consolidate": {"timeout_seconds", "agent_phases"},
    "mrq.classify-batches": {"timeout_seconds", "agent_phases"},
    "mrq.decide-next": {"timeout_seconds", "agent_phases"},
    "projections.build": {"timeout_seconds", "max_retries"},
    "workflow.verify": {"timeout_seconds"},
}
AGENT_PHASE_CATALOG = {
    "dif.classify-next": (
        {"phase_id": "analyze-dif", "modes": ("sequential", "parallel-pool"), "roles": ("analyzer",)},
    ),
    "mrq.consolidate": (
        {"phase_id": "form-mrq", "modes": ("coordinated-pool",), "roles": ("coordinator", "grouper")},
    ),
    "mrq.classify-batches": (
        {"phase_id": "classify-batches", "modes": ("sequential",), "roles": ("classifier",)},
    ),
    "mrq.decide-next": (
        {"phase_id": "research-target", "modes": ("sequential", "parallel-pool"), "roles": ("researcher",)},
    ),
}
_CATALOG: dict[str, dict[str, JsonValue]] = {
    "project.validate": {"executor": "application", "effect": "read", "paths": ["project.toml", "research/"], "artifacts": ["workflow-snapshot"], "validator": "doctor", "retryable": [], "approval_required": False},
    "sources.acquire": {"executor": "application", "effect": "write", "paths": ["sources/generations/", "research/active-source-generation.json"], "artifacts": ["source-generation"], "validator": "sources.validate", "retryable": [], "approval_required": True},
    "diff.build": {"executor": "application", "effect": "write", "paths": ["analysis/indexes/generations/", "research/active-diff-generation.json"], "artifacts": ["diff-generation"], "validator": "diff.validate", "retryable": ["transient_io"], "approval_required": False},
    "indexes.build": {"executor": "source-index-adapters", "effect": "user-scope-write", "paths": [], "artifacts": ["operational-source-index"], "validator": "indexes.validate", "retryable": [], "approval_required": False},
    "dif.classify-next": {"executor": "agent", "effect": "proposal", "paths": ["analysis/dif-classifications/generations/", "research/active-dif-classification-generation.json"], "artifacts": ["dif-classification-generation"], "validator": "dif.classification", "retryable": [], "approval_required": False},
    "mrq.consolidate": {"executor": "agent", "effect": "proposal", "paths": ["analysis/migration-requirements/generations/", "research/active-consolidation-generation.json"], "artifacts": ["consolidation-plan"], "validator": "mrq.validate", "retryable": [], "approval_required": True},
    "mrq.classify-batches": {"executor": "agent", "effect": "proposal", "paths": ["analysis/migration-requirements/batch-generations/", "research/active-consolidation-generation.json"], "artifacts": ["mrq-batch-generation"], "validator": "mrq-batches-ready", "retryable": [], "approval_required": False},
    "mrq.decide-next": {"executor": "agent", "effect": "proposal", "paths": ["analysis/migration-requirements/decision-generations/", "research/active-consolidation-generation.json"], "artifacts": ["decision-generation"], "validator": "mrq.validate", "retryable": [], "approval_required": True},
    "projections.build": {"executor": "application", "effect": "write", "paths": ["outputs/"], "artifacts": ["projections"], "validator": "projections.validate", "retryable": ["transient_io"], "approval_required": False},
    "workflow.verify": {"executor": "application", "effect": "read", "paths": ["."], "artifacts": ["workflow-snapshot"], "validator": "doctor.strict", "retryable": [], "approval_required": False},
}
OPERATION_CATALOG: dict[str, dict[str, JsonValue]] = {
    operation: json_object({
        "version": "1",
        "parameters": sorted(PARAMETERS[operation]),
        **entry,
        **({"fixed_inputs": {"mode": "ensure", "selector": "all"}, "run_inputs": {"mode": ["ensure", "validate", "rebuild"], "selector": "all|component_ids|backend_ids"}} if operation == "indexes.build" else {}),
    })
    for operation, entry in _CATALOG.items()
}
for _operation, _phases in AGENT_PHASE_CATALOG.items():
    OPERATION_CATALOG[_operation]["version"] = "2" if _operation == "mrq.decide-next" else "1"
    OPERATION_CATALOG[_operation]["agent_phases"] = [dict(item) for item in _phases]
PROJECT_SECTIONS = {"project", "mcp", "web", "policy"}
RESEARCH_OWNED_PROJECT_KEYS = {
    "acquisition_profile": "research/infobases.toml",
    "roles": "research/infobases.toml",
    "source_roles": "research/infobases.toml",
    "external_artifacts": "research/external-artifacts.toml",
    "indexing": "research/indexing.toml",
    "workflow": "research/workflow.toml",
    "active_source_generation": "research/active-source-generation.json",
    "active_diff_generation": "research/active-diff-generation.json",
    "active_dif_classification_generation": "research/active-dif-classification-generation.json",
    "active_consolidation_generation": "research/active-consolidation-generation.json",
    "active_generation": "research/active-generation.json",
}


def _object(value: object) -> dict[str, JsonValue]:
    return json_object(value)


def _objects(value: object) -> list[dict[str, JsonValue]]:
    normalized = json_array(value)
    return [json_object(item) for item in normalized]


def _integer(value: JsonValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected integer")
    return value


def _strings(value: object) -> list[str]:
    normalized = json_array(value)
    if not all(isinstance(item, str) for item in normalized):
        raise ValueError("expected string array")
    return [item for item in normalized if isinstance(item, str)]


def _valid_agent_profile(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) is not None


def _safe_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 2**53 - 1


def validate_agent_phases(operation: str, phases: object) -> list[AgentPhase]:
    catalog = AGENT_PHASE_CATALOG.get(operation)
    if catalog is None:
        raise ValueError(f"{operation} does not declare agent phases")
    raw_phases = _objects(phases)
    if len(raw_phases) != len(catalog):
        raise ValueError(f"{operation} requires its fixed agent phase catalog")
    normalized: list[AgentPhase] = []
    for index, (raw_phase, declared) in enumerate(zip(raw_phases, catalog, strict=True)):
        raw_roles = _objects(raw_phase.get("roles"))
        phase = AgentPhase(
            phase_id=str(raw_phase.get("phase_id", "")),
            mode=str(raw_phase.get("mode", "")),
            max_concurrency=_integer(raw_phase.get("max_concurrency")),
            roles=[AgentRole(role_id=str(role.get("role_id", "")), agent_profile=str(role.get("agent_profile", "")), count=_integer(role.get("count")), instruction_supplement=str(role.get("instruction_supplement", ""))) for role in raw_roles],
        )
        if set(raw_phase) != {"phase_id", "mode", "max_concurrency", "roles"}:
            raise ValueError(f"agent phase[{index}] fields differ")
        if phase["phase_id"] != declared["phase_id"] or phase["mode"] not in declared["modes"]:
            raise ValueError(f"agent phase[{index}] identity, order, or mode differs")
        if not _safe_positive_int(phase["max_concurrency"]):
            raise ValueError(f"agent phase[{index}] max_concurrency is not a safe integer")
        roles = phase["roles"]
        if tuple(role["role_id"] for role in roles) != declared["roles"]:
            raise ValueError(f"agent phase[{index}] roles differ")
        for role_index, (role, raw_role) in enumerate(zip(roles, raw_roles, strict=True)):
            if set(raw_role) != {"role_id", "agent_profile", "count", "instruction_supplement"}:
                raise ValueError(f"agent phase[{index}].role[{role_index}] fields differ")
            if not _valid_agent_profile(role["agent_profile"]) or not _safe_positive_int(role["count"]):
                raise ValueError(f"agent phase[{index}].role[{role_index}] profile or count is invalid")
            if len(role["instruction_supplement"]) > 4000:
                raise ValueError(f"agent phase[{index}].role[{role_index}] instruction supplement is invalid")
        counts = {role["role_id"]: role["count"] for role in roles}
        if phase["mode"] == "sequential" and (list(counts.values()) != [1] or phase["max_concurrency"] != 1):
            raise ValueError("sequential phase requires one role, count=1 and max_concurrency=1")
        worker = counts.get("grouper", next(iter(counts.values())))
        if phase["mode"] != "sequential" and phase["max_concurrency"] > worker:
            raise ValueError("agent phase max_concurrency exceeds worker count")
        if phase["mode"] == "coordinated-pool" and counts.get("coordinator") != 1:
            raise ValueError("coordinated phase requires one coordinator")
        reject_secrets(json_object(phase), f"agent phase {phase['phase_id']}")
        normalized.append(phase)
    return normalized


@dataclass(frozen=True)
class Blocker:
    code: str
    message: str
    action: str


@dataclass(frozen=True)
class Gate:
    id: str
    state: str
    blockers: tuple[Blocker, ...]


def read_toml(path: Path) -> dict[str, JsonValue]:
    with path.open("rb") as stream:
        value = json_object(tomllib.load(stream))
    reject_secrets(value, str(path))
    return value


def _workflow_manifest(value: object) -> WorkflowManifest:
    root = _object(value)
    if set(root) != {"schema_version", "tool_version", "gates", "jobs"}:
        raise ValueError("workflow top-level fields differ")
    gates = [WorkflowGate(id=str(item.get("id", "")), validator=str(item.get("validator", ""))) for item in _objects(root.get("gates"))]
    jobs: list[WorkflowJob] = []
    for item in _objects(root.get("jobs")):
        if set(item) != {"id", "needs", "steps"}:
            raise ValueError("fixed jobs: fields differ")
        steps: list[WorkflowStep] = []
        for raw in _objects(item.get("steps")):
            operation = str(raw.get("operation", ""))
            allowed = PARAMETERS.get(operation, set())
            unknown = set(raw) - {"id", "operation", "operation_version"} - allowed
            if unknown:
                expected = OPERATION_CATALOG.get(operation, {}).get("version", "")
                raise ValueError(f"unsupported workflow step fields: {sorted(unknown)}; {operation} requires operation version {expected}")
            step = WorkflowStep(
                id=str(raw.get("id", "")),
                operation=operation,
                operation_version=str(raw.get("operation_version", "")),
            )
            timeout = raw.get("timeout_seconds")
            retries = raw.get("max_retries")
            if isinstance(timeout, int) and not isinstance(timeout, bool):
                step["timeout_seconds"] = timeout
            if isinstance(retries, int) and not isinstance(retries, bool):
                step["max_retries"] = retries
            if "agent_phases" in raw:
                step["agent_phases"] = validate_agent_phases(step["operation"], raw["agent_phases"])
            steps.append(step)
        jobs.append(WorkflowJob(id=str(item.get("id", "")), needs=_strings(item.get("needs")), steps=steps))
    return WorkflowManifest(
        schema_version=str(root.get("schema_version", "")),
        tool_version=str(root.get("tool_version", "")),
        gates=gates,
        jobs=jobs,
    )


def workflow_fingerprint(repo: Path) -> str:
    return "sha256:" + sha256((repo / "research/workflow.toml").read_bytes())


def validate_project_contract(repo: Path) -> dict[str, JsonValue]:
    project = read_toml(repo / "project.toml")
    unknown = sorted(set(project) - PROJECT_SECTIONS)
    if unknown:
        raise ValueError(f"unknown project.toml section [{unknown[0]}]; allowed sections are [project], [mcp], [web], [policy]")
    for section, section_value in project.items():
        values = _object(section_value)
        for key, owner in RESEARCH_OWNED_PROJECT_KEYS.items():
            if key in values:
                raise ValueError(f"project.toml [{section}].{key} is owned by {owner}")
    values = _object(project.get("project", {}))
    missing = [key for key in ("id", "product", "baseline_version", "target_version", "next_vendor_version") if not str(values.get(key, "")).strip()]
    if missing:
        raise ValueError(f"empty project fields: {', '.join(missing)}")
    return project


def state_fingerprint(repo: Path) -> str:
    paths = ("project.toml", "research/workflow.toml", "research/infobases.toml", "research/external-artifacts.toml", "research/indexing.toml", "research/active-source-generation.json", "research/active-diff-generation.json", "research/active-dif-classification-generation.json", "research/active-consolidation-generation.json", "outputs/projections.json")
    return "sha256:" + sha256(canonical_json({name: sha256((repo / name).read_bytes()) if (repo / name).is_file() else None for name in paths}))


def validate_workflow_manifest(value: object) -> WorkflowManifest:
    manifest = _workflow_manifest(value)
    errors: list[str] = []
    if set(manifest) != {"schema_version", "tool_version", "gates", "jobs"}:
        errors.append(f"workflow top-level fields differ: {sorted(set(manifest) ^ {'schema_version', 'tool_version', 'gates', 'jobs'})}")
    if manifest.get("schema_version") != "4":
        errors.append("unsupported workflow schema; use a compatible tool or recreate the repository")
    if manifest.get("tool_version") != __version__:
        errors.append(f"workflow requires tool version {manifest.get('tool_version')}; installed version is {__version__}")
    for index, item in enumerate(manifest.get("gates", [])):
        if set(item) != {"id", "validator"}:
            errors.append(f"gate[{index}] fields differ: {sorted(set(item) ^ {'id', 'validator'})}")
    actual_gates = tuple((item.get("id"), item.get("validator")) for item in manifest.get("gates", []))
    if actual_gates != GATES:
        errors.append(f"fixed eight-gate contract changed: {actual_gates!r}")
    actual_jobs: list[tuple[str, tuple[str, ...], tuple[tuple[str, str, str], ...]]] = []
    for job_index, job in enumerate(manifest.get("jobs", [])):
        if set(job) != {"id", "needs", "steps"}:
            errors.append(f"fixed jobs: job[{job_index}] fields differ: {sorted(set(job) ^ {'id', 'needs', 'steps'})}")
        steps: list[tuple[str, str, str]] = []
        for step_index, step in enumerate(job.get("steps", [])):
            operation = step.get("operation")
            allowed = PARAMETERS.get(operation)
            if allowed is None:
                errors.append(f"job[{job_index}].step[{step_index}] unknown operation: {operation}")
                steps.append((step.get("id"), operation, step.get("operation_version")))
                continue
            unknown = set(step) - {"id", "operation", "operation_version"} - allowed
            if unknown:
                errors.append(f"job[{job_index}].step[{step_index}] unsupported fields for {operation}: {sorted(unknown)}")
            if step.get("operation_version") != OPERATION_CATALOG[operation]["version"]:
                errors.append(f"job[{job_index}].step[{step_index}] {operation} requires operation version {OPERATION_CATALOG[operation]['version']}")
            timeout = step.get("timeout_seconds", 1800)
            if not 30 <= timeout <= 86400:
                errors.append(f"job[{job_index}].step[{step_index}] invalid timeout for {operation}")
            retries = step.get("max_retries", 0)
            if retries not in ({0, 1} if operation in {"diff.build", "projections.build"} else {0}):
                errors.append(f"job[{job_index}].step[{step_index}] invalid retry count for {operation}")
            if operation in AGENT_PHASE_CATALOG:
                try:
                    _ = validate_agent_phases(operation, step.get("agent_phases"))
                except ValueError as exc:
                    errors.append(f"job[{job_index}].step[{step_index}] {exc}")
            try:
                step_identifier = step.get("id")
                reject_secrets(json_object(step), f"workflow step {step_identifier}")
            except ValueError as exc:
                errors.append(str(exc))
            steps.append((step.get("id"), operation, step.get("operation_version")))
        actual_jobs.append((job.get("id"), tuple(job.get("needs", [])), tuple(steps)))
    if tuple(actual_jobs) != JOBS:
        errors.append(f"fixed nine-job/ten-operation graph changed: {tuple(actual_jobs)!r}")
    if errors:
        raise ValueError("; ".join(errors))
    return manifest


def validate_workflow(repo: Path) -> WorkflowManifest:
    return validate_workflow_manifest(_workflow_manifest(read_toml(repo / "research/workflow.toml")))


def step_configurations(repo: Path) -> list[StepConfiguration]:
    manifest = validate_workflow(repo)
    result: list[StepConfiguration] = []
    for job in manifest["jobs"]:
        for step in job["steps"]:
            operation = step["operation"]
            result.append({"job_id": job["id"], "step": step, "catalog": dict(OPERATION_CATALOG[operation])})
    return result


def read_diff_inventory(repo: Path) -> list[dict[str, str]]:
    pointer = _pointer(repo, "active-diff-generation.json")
    generation_id: object = pointer.get("generation_id", "")
    path = repo / "analysis/indexes/generations" / str(generation_id) / "diff-inventory.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        return [
            row for row in csv.DictReader(stream)
            if row.get("before_role") == "vendor_baseline" and row.get("after_role") == "target_cf"
        ]


def decision_mrqs(repo: Path) -> list[dict[str, JsonValue]]:
    if not (repo / "research/active-consolidation-generation.json").is_file():
        from .mrq import active

        return [json_object(row) for row in active(repo)["mrq.jsonl"]]
    from .consolidation import load_active

    raw_state: object = load_active(repo)
    state = _object(raw_state)
    pointer = _object(state["pointer"])
    mrq = _object(state["mrq"])
    mrq_rows = _objects(mrq["mrq.jsonl"])
    decisions: dict[str, dict[str, JsonValue]] = {}
    decision_generation_id = pointer.get("decision_generation_id")
    if isinstance(decision_generation_id, str):
        from .decision_generations import validate_generation

        generation = validate_generation(
            repo,
            decision_generation_id,
            allowed_mrq_ids={str(row["mrq_id"]) for row in mrq_rows},
        )
        decisions = {
            str(row["mrq_id"]): _object(row["decision"])
            for row in _objects(generation["decisions.jsonl"])
        }
    evidence: dict[str, list[dict[str, JsonValue]]] = {}
    for row in _objects(mrq["evidence.jsonl"]):
        evidence.setdefault(str(row["mrq_id"]), []).append({
            "stable_diff_id": row["stable_diff_id"],
            **_object(row.get("payload", {})),
        })
    return [
        {
            **row,
            "migration_decision": decisions.get(str(row["mrq_id"]), {}),
            "source_customization": {
                "business_meaning": row.get("business_meaning", ""),
                "scope": row.get("scope", ""),
                "evidence": evidence.get(str(row["mrq_id"]), []),
            },
        }
        for row in mrq_rows
    ]


def preview_step_patch(
    repo: Path,
    step_id: str,
    parameters: dict[str, object],
    expected_manifest_fingerprint: str,
    state_base: Path | None = None,
) -> dict[str, object]:
    if expected_manifest_fingerprint != workflow_fingerprint(repo):
        raise RuntimeError("stale workflow manifest fingerprint")
    current = next((item for item in step_configurations(repo) if item["step"]["id"] == step_id), None)
    if current is None:
        raise ValueError("workflow step not found")
    operation = current["step"]["operation"]
    if set(parameters) - PARAMETERS[operation]:
        raise ValueError("unsupported workflow step parameter")
    candidate = dict(current["step"]); candidate.update(parameters)
    # Reuse the complete validator by validating the parameter rules directly.
    timeout = candidate.get("timeout_seconds", 1800)
    if not isinstance(timeout, int) or not 30 <= timeout <= 86400:
        raise ValueError(f"invalid timeout for {operation}")
    retries = candidate.get("max_retries", 0)
    if retries not in ({0, 1} if operation in {"diff.build", "projections.build"} else {0}):
        raise ValueError(f"invalid retry count for {operation}")
    agent_phases: list[AgentPhase] = []
    if operation in AGENT_PHASE_CATALOG:
        agent_phases = validate_agent_phases(operation, candidate.get("agent_phases"))
    reject_secrets(json_object(candidate), f"workflow step {step_id}")
    result: dict[str, object] = {"step_id": step_id, "operation": operation, "before": current["step"], "after": candidate, "catalog": current["catalog"], "manifest_fingerprint": expected_manifest_fingerprint}
    if operation in AGENT_PHASE_CATALOG:
        try:
            from .user_state import load_agent_profiles
            profiles = load_agent_profiles(repo, state_base)
        except (OSError, ValueError, KeyError):
            profiles = {}
        try:
            from .dif_classifications import coverage
            classification = coverage(repo)
            discover_ready = min(int(classification["remaining"]), 32)
            discover_remaining = int(classification["remaining"])
        except (OSError, ValueError, KeyError, IndexError):
            discover_ready = discover_remaining = 0
        try:
            from .mrq_batches import load_active
            mrq_rows = [
                row
                for row in decision_mrqs(repo)
                if row.get("state") != "superseded"
                and not _object(row.get("migration_decision", {})).get("decision")
            ]
            batches = load_active(repo)
            first_pending = mrq_rows[0]["mrq_id"] if mrq_rows else ""
            target_ready = len(next((batch.mrq_ids for batch in batches if first_pending in batch.mrq_ids), ()))
        except (OSError, ValueError, KeyError, IndexError):
            target_ready = 0
        try:
            from .mrq_batches import source_mrq_payload, stable_windows
            classification_ready = len(stable_windows(source_mrq_payload(repo)[2]))
        except (OSError, ValueError, KeyError, IndexError):
            classification_ready = 0
        ready_work = {
            "analyze-dif": discover_ready,
            "form-mrq": int(discover_remaining == 0),
            "classify-batches": classification_ready,
            "research-target": target_ready,
        }
        maximum_calls = {
            "analyze-dif": discover_ready,
            "form-mrq": int(discover_remaining == 0),
            "classify-batches": classification_ready,
            "research-target": target_ready,
        }
        result["agent_phase_preview"] = [
            {
                "phase_id": phase["phase_id"],
                "mode": phase["mode"],
                "effective_max_concurrency": min(
                    phase["max_concurrency"],
                    sum(role["count"] for role in phase["roles"] if role["role_id"] != "coordinator") or 1,
                    ready_work[phase["phase_id"]],
                ),
                "maximum_calls_in_current_window": maximum_calls[phase["phase_id"]],
                "roles": [
                    {
                        "role_id": role["role_id"],
                        "agent_profile": role["agent_profile"],
                        "profile": {
                            key: profiles.get(role["agent_profile"], {}).get(key, "")
                            for key in ("model", "reasoning_effort", "instructions_version", "environment_preset")
                        },
                    }
                    for role in phase["roles"]
                ],
                "sandbox": "read-only",
                "allowed_paths": "subject paths select context; they do not narrow filesystem read access",
            }
            for phase in agent_phases
        ]
    return result


def _pointer(repo: Path, name: str) -> dict[str, JsonValue]:
    try:
        return parse_json_object((repo / "research" / name).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid pointer research/{name}") from exc


def _project_blockers(repo: Path) -> list[Blocker]:
    try:
        _ = validate_project_contract(repo)
        return []
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        return [Blocker("project.invalid", str(exc), "project.configure")]


def _source_blockers(repo: Path, *, deep: bool = True) -> list[Blocker]:
    from .sources import validate_active
    try:
        _ = validate_active(repo, deep=deep)
        return []
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return [Blocker("sources.invalid", str(exc), "sources.acquire")]


def _diff_blockers(repo: Path) -> list[Blocker]:
    from .diffs import validate_active
    try:
        _ = validate_active(repo); return []
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return [Blocker("diff.invalid", str(exc), "diff.build")]


def _active_rows(repo: Path) -> tuple[list[dict[str, str]], list[dict[str, JsonValue]], list[dict[str, JsonValue]], list[dict[str, JsonValue]]]:
    pointers = json_object(owned_function(".stage_recompute", "active_pointers")(repo))
    diff_pointer = _object(pointers["diff"])
    diff_root = repo / "analysis/indexes/generations" / str(diff_pointer.get("generation_id"))
    with (diff_root / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
        diffs = [row for row in csv.DictReader(stream) if row.get("comparison_id", "").startswith("CMP-")]
    from .consolidation import load_active as load_consolidation
    raw_state: object = load_consolidation(repo)
    state = _object(raw_state)
    pointer = _object(state["pointer"])
    mrq = _object(state["mrq"])
    if pointer["state"] != "active":
        return diffs, [], [], []
    dispositions = mrq["dispositions.jsonl"]
    decisions: object = []
    if pointer.get("decision_generation_id"):
        from .decision_generations import validate_generation as validate_decisions
        raw_decisions: object = validate_decisions(repo, str(pointer["decision_generation_id"]))
        decisions = _object(raw_decisions)["decisions.jsonl"]
    return (
        diffs,
        _objects(mrq["mrq.jsonl"]),
        _objects(dispositions),
        _objects(decisions),
    )


def _jsonl(path: Path) -> list[dict[str, JsonValue]]:
    if not path.is_file():
        return []
    return [parse_json_object(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def semantic_diff_context(repo: Path, stable_diff_id: str, fact: dict[str, JsonValue] | None = None) -> dict[str, JsonValue]:
    pointer = _pointer(repo, "active-diff-generation.json")
    root = repo / "analysis/indexes/generations" / str(pointer.get("generation_id"))
    if fact is None:
        with (root / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
            fact = next((row for row in csv.DictReader(stream) if row.get("stable_diff_id") == stable_diff_id), None)
    if fact is None:
        physical = root / "extension-physical-diff.csv"
        if physical.is_file():
            with physical.open(encoding="utf-8", newline="") as stream:
                if any(row.get("stable_diff_id") == stable_diff_id for row in csv.DictReader(stream)):
                    raise ValueError(f"raw extension audit DIF is not a workflow work unit: {stable_diff_id}")
        raise ValueError(f"unknown active DIF: {stable_diff_id}")
    context: dict[str, JsonValue] = {"diff": fact, "allowed_paths": [fact["path"]]}
    if pointer.get("schema_version") != "2" or fact.get("object_kind") != "extension_intervention":
        return context
    details = next((row for row in _jsonl(root / "extension-diff.jsonl") if row.get("stable_diff_id") == stable_diff_id), None)
    if details is None:
        raise ValueError(f"semantic extension DIF details are missing: {stable_diff_id}")
    dependencies = [row for row in _jsonl(root / "extension-dependencies.jsonl") if row.get("stable_diff_id") == stable_diff_id]
    evidence = _objects(details["evidence"])
    with (root / "target-coverage.csv").open(encoding="utf-8", newline="") as stream:
        coverage = next((row for row in csv.DictReader(stream) if row.get("customer_diff_id") == stable_diff_id), None)
    context.update({
        "extension": details,
        "dependencies": dependencies,
        "target_coverage": coverage,
        "allowed_paths": sorted({f"{item['role']}/extensions/{details['extension_uuid']}/{item['path']}" for item in evidence}),
        "compatibility_summary": {
            "dependency_outcomes": sorted({str(item["outcome"]) for item in dependencies}),
            "diagnostic_codes": details["diagnostic_codes"],
            "target_coverage_status": coverage.get("coverage_status", "") if coverage else "",
        },
    })
    return context


def status(repo: Path, *, deep: bool = True) -> WorkflowSnapshot:
    repo = repo.resolve()
    _ = owned_function(".stage_recompute", "recover_active_publication")(repo)
    _ = validate_workflow(repo)
    checks = [_project_blockers(repo), _source_blockers(repo, deep=deep), _diff_blockers(repo)]
    if not any(checks):
        from .dif_classifications import coverage as classification_coverage
        classification = classification_coverage(repo)
        checks.append(
            [] if classification["all_dif_classified"] else [
                Blocker(
                    "dif.classification",
                    f"{classification['remaining']} customer DIF remain unclassified",
                    "dif.classify-next",
                )
            ]
        )
    if not any(checks):
        try:
            from .consolidation import load_active as load_consolidation
            raw_consolidation: object = load_consolidation(repo)
            consolidation = _object(raw_consolidation)
            consolidated = _object(consolidation["pointer"])["state"] == "active"
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            consolidated = False
        checks.append(
            [] if consolidated else [
                Blocker("mrq.ownership", "complete MRQ consolidation is not published", "mrq.consolidate")
            ]
        )
    if not any(checks):
        from .consolidation import load_active as load_consolidation
        raw_consolidated: object = load_consolidation(repo)
        consolidated = _object(raw_consolidated)
        pointer = _object(consolidated["pointer"])
        mrq = _object(consolidated["mrq"])
        mrq_rows = _objects(mrq["mrq.jsonl"])
        mrq_ids = {str(row["mrq_id"]) for row in mrq_rows}
        evidenced = {str(row["mrq_id"]) for row in _objects(mrq["evidence.jsonl"])}
        checks.append([] if evidenced == mrq_ids else [Blocker("mrq.source_evidence", "active MRQ source evidence is incomplete", "mrq.consolidate")])
        decisions_complete = False
        try:
            from .decision_generations import consolidation_input_fingerprint, validate_generation as validate_decisions
            generation = validate_decisions(repo, str(pointer["decision_generation_id"]))
            decisions_complete = (
                pointer["decision_input_fingerprint"] == consolidation_input_fingerprint(pointer)
                and {str(row["mrq_id"]) for row in _objects(generation["decisions.jsonl"])} == mrq_ids
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            decisions_complete = False
        checks.append([] if decisions_complete else [Blocker("mrq.approvals", "active MRQ decisions are incomplete or unapproved", "mrq.decide-next")])
        generation_id = pointer.get("mrq_generation_id")
        checks.append(_publication_blockers(repo, generation_id if isinstance(generation_id, str) else None, deep=deep))
    while len(checks) < len(GATES):
        checks.append([Blocker("predecessor.blocked", "a predecessor gate is incomplete", "")])
    gates: list[Gate] = []
    predecessor_complete = True
    for (gate_id, _), blockers in zip(GATES, checks, strict=True):
        effective = blockers if predecessor_complete else [Blocker("predecessor.blocked", "a predecessor gate is incomplete", "")]
        state = "complete" if not effective else ("ready" if predecessor_complete and effective[0].action else "blocked")
        gates.append(Gate(gate_id, state, tuple(effective)))
        predecessor_complete &= state == "complete"
    return {
        "schema_version": "1",
        "tool_version": __version__,
        "operation_versions": {operation: str(item["version"]) for operation, item in OPERATION_CATALOG.items()},
        "workflow_fingerprint": state_fingerprint(repo),
        "manifest_fingerprint": workflow_fingerprint(repo),
        "state": "complete" if all(g.state == "complete" for g in gates) else "ready" if any(g.state == "ready" for g in gates) else "blocked",
        "gates": [
            {
                "id": gate.id,
                "state": gate.state,
                "blockers": [
                    {"code": blocker.code, "message": blocker.message, "action": blocker.action}
                    for blocker in gate.blockers
                ],
            }
            for gate in gates
        ],
    }


def _projection_blockers(repo: Path, generation: str | None) -> list[Blocker]:
    if not generation:
        return [Blocker("projection.missing", "canonical generation is absent", "projections.build")]
    manifest = repo / "outputs/projections.json"
    if not manifest.is_file():
        return [Blocker("projection.missing", "projections are absent", "projections.build")]
    try:
        value = parse_json_object(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        value = {}
    from .consolidation import load_active
    return [] if value == projection_value(load_active(repo)) else [Blocker("projection.stale", "projections are stale or independently edited", "projections.build")]


def projection_value(raw_state: object) -> dict[str, JsonValue]:
    state = _object(raw_state)
    pointer = _object(state["pointer"])
    if pointer.get("schema_version") == "2":
        mrq = _object(state["mrq"])
        mrqs = _objects(mrq["mrq.jsonl"])
        dispositions = _objects(mrq["dispositions.jsonl"])
        return {
            "schema_version": "3",
            "consolidation_transaction_id": pointer["transaction_id"],
            "decision_generation_id": pointer.get("decision_generation_id"),
            "input_consolidation_fingerprint": pointer["plan_fingerprint"],
            "input_decision_fingerprint": pointer.get("decision_input_fingerprint"),
            "input_fingerprint": sha256(canonical_json({"mrq": mrqs, "dispositions": dispositions})),
            "views": {
                "subject_cards": mrqs,
                "functional_gaps": mrqs,
                "dashboard": {"active_mrq": len(mrqs), "dif_memberships": len(dispositions)},
                "customer_register": dispositions,
                "specifications": [],
            },
        }
    generation = pointer.get("canonical_generation_id")
    mrqs = _objects(state["mrq.jsonl"])
    dispositions = _objects(state["dispositions.jsonl"])
    return {
        "schema_version": "1",
        "canonical_generation_id": generation,
        "input_fingerprint": sha256(canonical_json({name: state[name] for name in ("mrq.jsonl", "dispositions.jsonl", "evidence.jsonl", "lineage.jsonl", "approvals.jsonl")})),
        "views": {
            "subject_cards": mrqs,
            "functional_gaps": mrqs,
            "dashboard": {"active_mrq": sum(item.get("state") != "superseded" for item in mrqs), "approved_mrq": sum(item.get("state") == "approved" for item in mrqs), "dispositions": len(dispositions)},
            "customer_register": dispositions,
            "specifications": [item for item in mrqs if item.get("state") == "approved"],
        },
    }


def _publication_blockers(repo: Path, generation: str | None, *, deep: bool = True) -> list[Blocker]:
    blockers = _projection_blockers(repo, generation)
    if blockers or not deep:
        return blockers
    from .sources import validate_active
    from .diffs import validate_active as validate_active_diffs
    from .consolidation import load_active as active_consolidation
    from .contracts import require_tracked_clean
    try:
        raw_consolidation: object = active_consolidation(repo)
        consolidation = _object(raw_consolidation)
        if _object(consolidation["pointer"]).get("batch_generation_id"):
            from .mrq_batches import load_active
            _ = load_active(repo)
        _ = validate_active(repo, deep=True, require_tracked_clean=True)
        _ = validate_active_diffs(repo, require_tracked_clean_state=True)
        require_tracked_clean(repo, [repo / "outputs/projections.json"])
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        return [Blocker("publication.strict", str(exc), "workflow.verify")]
    return []


def next_work(repo: Path) -> dict[str, JsonValue] | None:
    snapshot = status(repo)
    for gate in snapshot["gates"]:
        if gate["state"] == "ready":
            blocker = gate["blockers"][0]
            result: dict[str, JsonValue] = {"gate_id": gate["id"], "action": blocker["action"], "blocker": json_object(blocker), "workflow_fingerprint": snapshot["workflow_fingerprint"]}
            if blocker["action"] == "mrq.discover-next":
                diffs, mrqs, dispositions, _approvals = _active_rows(repo)
                owned = {str(item["stable_diff_id"]) for item in dispositions if item.get("primary")}
                pending = sorted((item for item in diffs if item.get("before_role") == "vendor_baseline" and item.get("after_role") == "target_cf" and item["stable_diff_id"] not in owned), key=lambda item: item["stable_diff_id"])
                if pending:
                    target_pointer = _pointer(repo, "active-diff-generation.json")
                    if pending[0].get("object_kind") == "extension_intervention":
                        context = semantic_diff_context(repo, pending[0]["stable_diff_id"], json_object(pending[0]))
                    else:
                        coverage_path = repo / "analysis/indexes/generations" / str(target_pointer["generation_id"]) / "target-coverage.csv"
                        with coverage_path.open(encoding="utf-8", newline="") as stream:
                            coverage = next((item for item in csv.DictReader(stream) if item["customer_diff_id"] == pending[0]["stable_diff_id"]), None)
                        context = {"diff": pending[0], "target_coverage": coverage, "allowed_paths": [pending[0]["path"]]}
                    result["work_unit"] = {"id": pending[0]["stable_diff_id"], "kind": "uncovered-diff", **context, "source_generation_id": _pointer(repo, "active-source-generation.json")["generation_id"], "diff_generation_id": target_pointer["generation_id"]}
            elif blocker["action"] == "mrq.decide-next":
                try:
                    from .mrq_batches import load_active
                    _ = load_active(repo)
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    from .mrq_batches import source_mrq_payload, stable_windows
                    generation_id, fingerprint, records = source_mrq_payload(repo)
                    return {
                        "gate_id": gate["id"],
                        "action": "mrq.classify-batches",
                        "blocker": {
                            "code": "mrq-batches-ready",
                            "message": "active MRQ batch generation is missing, stale, or invalid",
                            "action": "mrq.classify-batches",
                        },
                        "workflow_fingerprint": snapshot["workflow_fingerprint"],
                        "work_unit": {
                            "id": f"classify:{fingerprint}",
                            "kind": "mrq-batch-classification",
                            "canonical_generation_id": generation_id,
                            "source_mrq_fingerprint": fingerprint,
                            "window_count": len(stable_windows(records)),
                        },
                    }
                diffs, mrqs, dispositions, _approvals = _active_rows(repo)
                pending = sorted((item for item in mrqs if item.get("state") != "superseded" and item.get("state") != "approved"), key=lambda item: str(item["mrq_id"]))
                if pending:
                    item = pending[0]
                    owned = {str(relation["stable_diff_id"]) for relation in dispositions if relation.get("mrq_id") == item["mrq_id"] and relation.get("primary")}
                    customization = _object(item.get("source_customization", {}))
                    migration_decision = _object(item.get("migration_decision", {}))
                    paths = sorted(str(evidence["path"]) for evidence in _objects(customization.get("evidence", [])) if str(evidence.get("stable_diff_id", "")) in owned)
                    extension_ids = {row["stable_diff_id"] for row in diffs if row.get("object_kind") == "extension_intervention"}
                    semantic = [semantic_diff_context(repo, identifier) for identifier in sorted(owned) if identifier in extension_ids]
                    result["work_unit"] = {"id": item["mrq_id"], "kind": "migration-decision" if not migration_decision.get("decision") else "approval", "mrq": item, "semantic_extension_context": semantic, "source_generation_id": item["source_generation_id"], "diff_generation_id": item["diff_generation_id"], "allowed_paths": sorted(set(paths) | {path for context in semantic for path in _strings(_object(context)["allowed_paths"])})}
            return result
    return None


def attach_dispatcher(snapshot: WorkflowSnapshot, repo: Path, base: Path | None = None) -> dict[str, object]:
    """Присоединяет к снимку необязательную операционную секцию ``dispatcher``.

    Секция собирается из канонического снимка и пользовательского состояния
    SQLite; она **не** сохраняется в канонических файлах и **не** участвует в
    ``state_fingerprint``, отпечатке идемпотентности, выводе ворот, CLI
    ``status`` и ``doctor``. Удаление пользовательского состояния делает секцию
    пустой, не меняя канонический снимок.
    """

    try:
        from .sqlite_state import DispatcherStore
    except ImportError:
        return dict(snapshot)
    try:
        store = DispatcherStore(repo, base)
        store.open()
        try:
            projection: dict[str, object] = _dispatcher_projection(snapshot, repo, store)
        finally:
            store.close()
    except Exception:
        # отсутствие/повреждение SQLite-базы не должно ронять канонический снимок
        projection = {"schema_version": "2", "revision": 0, "fresh_at": "", "circuits": [], "jobs": {}, "error": "dispatcher_unavailable"}
    return {**snapshot, "dispatcher": projection}


def _zone_state(gate: GateValue | None) -> str:
    return {"complete": "complete", "ready": "waiting"}.get(
        str((gate or {}).get("state", "")),
        "unknown",
    )


def _prepare_zones(
    repo: Path,
    state_base: Path | None,
    gates: dict[str, GateValue],
    runs_root: Path,
    workflow_fingerprint: str,
) -> list[dict[str, JsonValue]]:
    from .sources import current_profile_test, load_contract
    from .user_state import load_connections, state_root

    infobases, _artifacts = load_contract(repo)
    connections = load_connections(repo, state_base)
    profile_id = str(infobases.get("acquisition_profile", ""))
    zones: list[dict[str, JsonValue]] = []
    for role in ROLES:
        roles = _object(infobases.get("roles", {}))
        role_definition = _object(roles.get(role, {}))
        profile = connections.get(str(role_definition.get("connection_profile", "")))
        state = (
            "complete"
            if profile and current_profile_test(profile, profile_id)
            else "waiting"
            if profile
            else "unknown"
        )
        zones.append({"id": role.replace("_", "-"), "state": state})

    zones.extend((
        {
            "id": "sources-acquire",
            **(_latest_operation_zone(runs_root, "sources.acquire", workflow_fingerprint) or {"state": _zone_state(gates.get("sources-acquired"))}),
        },
        {
            "id": "diffs-build",
            **(_latest_operation_zone(runs_root, "diff.build", workflow_fingerprint) or {"state": _zone_state(gates.get("diffs-built"))}),
        },
    ))
    index_run = _latest_operation_zone(runs_root, "indexes.build", workflow_fingerprint)
    try:
        from .indexes import statuses

        index_rows = statuses(repo, state_root(state_base) / "indexes")
        if index_run:
            index_state = index_run["state"]
        elif index_rows and all(item["status"] in {"ready", "not_indexable"} for item in index_rows):
            index_state = "complete"
        elif any(item["status"] == "failed" for item in index_rows):
            index_state = "error"
        elif any(item["status"] == "building" for item in index_rows):
            index_state = "active"
        elif (diffs_gate := gates.get("diffs-built")) is not None and diffs_gate["state"] == "complete":
            index_state = "waiting"
        else:
            index_state = "unknown"
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        index_state = "unknown"
    zones.append({"id": "indexes-build", "state": index_state, **({"updated_at": index_run["updated_at"]} if index_run and index_run.get("updated_at") else {})})
    return zones


def _latest_operation_zone(
    runs_root: Path,
    operation: str,
    workflow_fingerprint: str | None = None,
) -> dict[str, str] | None:
    from .events import process_identity_alive

    for path in sorted(
        runs_root.glob("*.json") if runs_root.is_dir() else [],
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    ):
        try:
            run = parse_json_object(path.read_text(encoding="utf-8"))
            execution = _object(run["execution_snapshot"])
            if (
                execution.get("operation") != operation
                or (
                    workflow_fingerprint is not None
                    and execution.get("workflow_fingerprint") != workflow_fingerprint
                )
                or run.get("execution_snapshot_fingerprint")
                != "sha256:" + sha256(canonical_json(execution))
            ):
                continue
            status = str(run.get("status", ""))
            identity_value = run.get("process_identity")
            identity = _object(identity_value) if identity_value is not None else None
            if status == "running" and process_identity_alive(identity):
                state = "active"
            elif status == "completed":
                state = "complete"
            elif status == "failed":
                state = "error"
            else:
                continue
            events = _objects(run.get("events", []))
            updated_at = str(events[-1].get("timestamp", "")) if events else ""
            return {"state": state, **({"updated_at": updated_at} if updated_at else {})}
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _dispatcher_projection(snapshot: WorkflowSnapshot, repo: Path, store: DispatcherStore) -> dict[str, object]:
    """Собирает операционную проекцию пяти контуров.

    Структура стабильна и предназначена для React Flow-схемы; координаты узлов
    остаются константами клиента.
    """

    from .agents import probe_codex_environment

    revision, updated_at = store.revision()
    leases = store.leases()
    lease_by_job = {lease["job_id"]: lease for lease in leases}
    try:
        from .user_state import load_agent_profiles
        safe_profiles = load_agent_profiles(repo, store.base)
    except (OSError, ValueError, KeyError):
        safe_profiles = {}
    codex_probe = probe_codex_environment()
    phases: list[dict[str, object]] = []
    transient_meaning_ids: set[str] = set()
    for configured in step_configurations(repo):
        active_run_id = str(
            (lease_by_job.get(configured["job_id"]) or {}).get("run_id", "")
            or store.latest_phase_run(configured["job_id"])
        )
        work_rows = store.phase_work(active_run_id) if active_run_id else []
        execution_snapshot = None
        if active_run_id:
            from .events import EventStore
            run = EventStore(store.path.parent.parent, store.path.parent.name).run_snapshot(active_run_id)
            execution_snapshot = (run or {}).get("execution_snapshot")
        phase_definitions = execution_snapshot.get("agent_phases", []) if isinstance(execution_snapshot, dict) else configured["step"].get("agent_phases", [])
        profile_definitions = _object(execution_snapshot.get("profiles", {})) if isinstance(execution_snapshot, dict) else safe_profiles
        snapshot_environment_available = False
        if isinstance(execution_snapshot, dict):
            try:
                from .agents import verify_execution_environment
                verify_execution_environment(execution_snapshot, codex_probe)
                snapshot_environment_available = True
            except (OSError, RuntimeError, ValueError):
                pass
        for phase_index, phase in enumerate(_objects(phase_definitions)):
            roles: list[dict[str, object]] = []
            for role in _objects(phase["roles"]):
                phase_id = str(phase["phase_id"])
                role_id = str(role["role_id"])
                rows = store.invocations(
                    active_run_id,
                    limit=16,
                    phase_id=phase_id,
                    role_id=role_id,
                ) if active_run_id else []
                invocation_total = store.invocation_count(
                    active_run_id,
                    phase_id,
                    role_id,
                ) if active_run_id else 0
                units = [item for item in work_rows if item["phase_id"] == phase_id and item["role_id"] == role_id]
                if phase_id == "form-mrq" and role_id == "grouper":
                    transient_meaning_ids.update(str(item["work_unit_id"]) for item in units)
                counts = {state: sum(item["status"] == state for item in units) for state in ("running", "queued", "completed", "failed", "cancelled", "interrupted")}
                profile = _object(profile_definitions.get(str(role["agent_profile"]), {}))
                environment_status = (
                    "available"
                    if snapshot_environment_available
                    or (
                        not active_run_id
                        and profile.get("environment_preset") == "local-read-only"
                        and codex_probe is not None
                    )
                    else "unavailable"
                )
                assignments = (
                    store.slot_assignments(
                        active_run_id, phase_id, role_id
                    )
                    if active_run_id
                    else {}
                )
                slots: list[dict[str, object]] = []
                for ordinal in range(1, _integer(role["count"]) + 1):
                    slot_id = f"{phase_id}:{role_id}:{ordinal}"
                    assignment = assignments.get(
                        slot_id, {"current": None, "latest": None}
                    )
                    latest = assignment["latest"]
                    current = assignment["current"]
                    if current:
                        reason = "none"
                    elif not active_run_id:
                        reason = "work_not_requested"
                    elif environment_status == "unavailable":
                        reason = "environment_unavailable"
                    elif counts["queued"]:
                        reason = "waiting_for_dispatch"
                    elif units and counts["running"] == 0:
                        reason = "phase_complete"
                    elif not units:
                        reason = "waiting_for_prerequisite" if phase_index else "work_not_requested"
                    else:
                        reason = "queue_empty"
                    slots.append({
                        "slot_id": slot_id,
                        "display_label": str(ordinal),
                        "state": "running" if current else "idle",
                        "idle_reason_code": reason,
                        "run_id": active_run_id or None,
                        "current_invocation_id": current["invocation_id"] if current else None,
                        "latest_invocation_id": latest["invocation_id"] if latest else None,
                    })
                roles.append({
                    "role_id": role_id,
                    "agent_profile": role["agent_profile"],
                    "model": profile.get("model", ""),
                    "reasoning_effort": profile.get("reasoning_effort", ""),
                    "environment_preset": profile.get("environment_preset", ""),
                    "environment_status": environment_status,
                    "configured_slots": role["count"],
                    "requested": len(units),
                    **counts,
                    "invocation_total": invocation_total,
                    "invocation_omitted": max(invocation_total - len(rows), 0),
                    "invocations": rows,
                    "slots": slots,
                })
            phases.append({"job_id": configured["job_id"], "phase_id": str(phase["phase_id"]), "mode": str(phase["mode"]), "max_concurrency": _integer(phase["max_concurrency"]), "roles": roles})
    # контуры выводятся из канонического снимка
    gates = {gate["id"]: gate for gate in snapshot.get("gates", [])}
    classify_aggregates = _classify_aggregates(repo)
    active_jobs = {str(lease["job_id"]) for lease in leases if lease["state"] == "running"}
    circuits: list[dict[str, object]] = [
        {"id": "prepare-diffs", "state": _circuit_state(gates.get("sources-acquired"), gates.get("diffs-built")), "aggregates": _prepare_aggregates(repo), "zones": _prepare_zones(repo, store.base, gates, store.path.parent / "runs", str(snapshot.get("workflow_fingerprint", "")))},
        {"id": "analyze-dif", "state": _agent_circuit_state("analyze-dif", active_jobs, gates.get("all-dif-classified")), "leases": [lease for lease in leases if lease["job_id"] == "analyze-dif"], "aggregates": _analyze_aggregates(repo, store)},
        {"id": "form-mrq", "state": _agent_circuit_state("consolidate-mrq", active_jobs, gates.get("mrq-consolidated"), gates.get("source-evidence-complete")), "leases": [lease for lease in leases if lease["job_id"] == "consolidate-mrq"], "aggregates": _form_mrq_aggregates(repo, store), "publication": {"id": "publication", "state": _zone_state(gates.get("source-evidence-complete"))}},
        {"id": "classify-mrq", "state": "active" if "classify-mrq" in active_jobs else classify_aggregates["state"], "leases": [lease for lease in leases if lease["job_id"] == "classify-mrq"], "aggregates": classify_aggregates},
        {"id": "decide-target", "state": _agent_circuit_state("decide-mrq", active_jobs, gates.get("decisions-approved")), "leases": [lease for lease in leases if lease["job_id"] == "decide-mrq"], "aggregates": _decide_aggregates(repo)},
    ]
    from datetime import datetime, timezone
    retry_candidates: list[dict[str, object]] = []
    runs_root = store.path.parent / "runs"
    for path in sorted(
        runs_root.glob("*.json") if runs_root.is_dir() else [],
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    ):
        try:
            run = parse_json_object(path.read_text(encoding="utf-8"))
            execution = _object(run["execution_snapshot"])
            if (
                run.get("execution_snapshot_fingerprint")
                != "sha256:" + sha256(canonical_json(execution))
            ):
                continue
            operation_value = execution.get("operation")
            operation = operation_value if isinstance(operation_value, str) else ""
            job_id = {
                "dif.classify-next": "analyze-dif",
                "mrq.consolidate": "consolidate-mrq",
                "mrq.classify-batches": "classify-mrq",
                "mrq.decide-next": "decide-mrq",
            }.get(operation)
            from .events import RETRYABLE_RUN_STATUSES
            if not job_id or run.get("status") not in RETRYABLE_RUN_STATUSES:
                continue
            bindings = _object(execution.get("subject_bindings", {}))
            work_unit = _object(execution.get("work_unit", {}))
            retry_candidates.append(
                {
                    "run_id": run["run_id"],
                    "job_id": job_id,
                    "status": run.get("status", ""),
                    "execution_snapshot_fingerprint": run["execution_snapshot_fingerprint"],
                    "policy_source": execution.get("policy_source", "current-policy"),
                    "workflow_fingerprint": execution.get("workflow_fingerprint", ""),
                    "input_fingerprints": {
                        "source_generation_id": str(bindings.get("source_generation_id", "")),
                        "diff_generation_id": str(bindings.get("diff_generation_id", "")),
                        "canonical_generation_id": str(bindings.get("canonical_generation_id", "")),
                        "work_unit_id": str(work_unit.get("id", "")),
                    },
                }
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if len(retry_candidates) == 20:
            break
    items = _dispatcher_items(repo, store, transient_meaning_ids)
    queue_aggregates = items.pop("_queue_aggregates")
    return {
        "schema_version": "2",
        "revision": revision,
        "fresh_at": updated_at or datetime.now(timezone.utc).isoformat(),
        "circuits": circuits,
        "jobs": {lease["job_id"]: lease for lease in leases},
        "stage_recompute": next((lease for lease in leases if lease["job_id"] == "stage-recompute"), None),
        "stage_recompute_run": store.latest_stage_recompute(),
        "items": items,
        "queue_aggregates": queue_aggregates,
        "agent_phases": phases,
        "retry_candidates": retry_candidates,
    }


def _dispatcher_items(
    repo: Path,
    store: DispatcherStore,
    transient_meaning_ids: set[str] | None = None,
) -> dict[str, JsonValue]:
    """Компактные карточки для экрана без второго предметного хранилища."""

    try:
        diffs, mrqs, dispositions, approvals = _active_rows(repo)
        operational = store.proposals()
        transient = {identifier: "meaning" for identifier in transient_meaning_ids or ()}
        lease = store.lease("analyze-dif") if hasattr(store, "lease") else None
        transient.update(_transient_analyze_results(
            operational,
            str(lease.get("thread_id", "")) if lease else None,
        ))
        customer = sorted(
            (row for row in diffs if row.get("before_role") == "vendor_baseline" and row.get("after_role") == "target_cf"),
            key=lambda row: row["stable_diff_id"],
        )
        try:
            from .dif_classifications import load_active as load_classifications
            classification_rows = load_classifications(repo)["rows"]
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            classification_rows = []
        classifications = {
            row["stable_diff_id"]: row["classification"]
            for row in classification_rows
        }
        active_mrqs = sorted((row for row in mrqs if row.get("state") != "superseded"), key=lambda row: str(row["mrq_id"]))
        pointer = _pointer(repo, "active-diff-generation.json")
        diff_root = repo / "analysis/indexes/generations" / str(pointer.get("generation_id"))
        extension_rows = _jsonl(diff_root / "extension-diff.jsonl") if pointer.get("schema_version") == "2" else []
        dependency_rows = _jsonl(diff_root / "extension-dependencies.jsonl") if pointer.get("schema_version") == "2" else []
        coverage_rows = _csv(diff_root / "target-coverage.csv")
        extension_by_id = {row["stable_diff_id"]: row for row in extension_rows}
        dependencies_by_id: dict[str, list[dict[str, JsonValue]]] = {}
        for dependency in dependency_rows:
            dependencies_by_id.setdefault(str(dependency["stable_diff_id"]), []).append(dependency)
        coverage_by_id = {row["customer_diff_id"]: row for row in coverage_rows}
        def card(row: dict[str, str], state: str):
            identifier = row["stable_diff_id"]
            return _dif_card(
                json_object(row),
                state,
                extension_by_id.get(identifier),
                json_object(coverage_by_id[identifier]) if identifier in coverage_by_id else None,
                dependencies_by_id.get(identifier, []),
            )
        from .mrq_batches import load_active
        from .consolidation import load_active as load_consolidation
        raw_consolidation: object = load_consolidation(repo)
        consolidation = _object(raw_consolidation)
        consolidation_pointer = _object(consolidation["pointer"])
        batches = load_active(repo) if consolidation_pointer.get("batch_generation_id") else []
        previously_owned = {
            str(row["stable_diff_id"]) for row in dispositions if row.get("primary")
        }
        pending_difs = [
            row for row in customer
            if row["stable_diff_id"] not in classifications
            and row["stable_diff_id"] not in transient
            and row["stable_diff_id"] not in previously_owned
        ]
        meaning_difs = [row for row in customer if classifications.get(row["stable_diff_id"]) == "meaning" or transient.get(row["stable_diff_id"]) == "meaning"]
        unassigned_meaning_difs = [
            row for row in meaning_difs
            if row["stable_diff_id"] not in previously_owned
        ]
        noise_difs = [row for row in customer if classifications.get(row["stable_diff_id"]) == "noise_candidate" or transient.get(row["stable_diff_id"]) == "noise"]
        proposals = [row for row in operational if row.get("kind") == "approval" and row.get("consumed_at") is None]
        decision_rows: list[dict[str, JsonValue]] = []
        decision_generation_id = consolidation_pointer.get("decision_generation_id")
        if isinstance(decision_generation_id, str):
            from .decision_generations import validate_generation as validate_decisions
            decision_rows = _objects(validate_decisions(
                repo,
                decision_generation_id,
                allowed_mrq_ids={str(row["mrq_id"]) for row in active_mrqs},
            )["decisions.jsonl"])
        decision_by_mrq = {str(row["mrq_id"]): row["decision"] for row in decision_rows}
        pending_mrqs = [
            row for row in active_mrqs
            if str(row["mrq_id"]) not in decision_by_mrq
        ]
        decisions = [
            {**row, "migration_decision": decision_by_mrq[str(row["mrq_id"])]}
            for row in active_mrqs if str(row["mrq_id"]) in decision_by_mrq
        ]
        plan: dict[str, JsonValue] = {}
        proposal = next((
            row for row in reversed(operational)
            if row.get("job_id") == "consolidate-mrq"
            and row.get("kind") == "approval"
            and row.get("consumed_at") is None
        ), None)
        if proposal:
            payload = _object(proposal.get("payload", {}))
            relative = payload.get("plan_path")
            path = store.path.parent / relative if isinstance(relative, str) and relative else None
            if path and path.is_file():
                plan = parse_json_object(path.read_text(encoding="utf-8"))
        elif isinstance(consolidation_pointer.get("plan_fingerprint"), str) and consolidation_pointer["plan_fingerprint"]:
            plan_fingerprint = str(consolidation_pointer["plan_fingerprint"])
            path = (
                store.path.parent
                / "consolidation-plans"
                / f"{plan_fingerprint.removeprefix('sha256:')}.json"
            )
            if path.is_file():
                plan = parse_json_object(path.read_text(encoding="utf-8"))
        outcomes = _object(plan.get("outcomes", {}))
        def outcome_cards() -> list[dict[str, JsonValue]]:
            lineage = [
                row for row in _objects(plan.get("lineage", []))
                if row.get("domain") == "mrq"
            ]
            cards: list[dict[str, JsonValue]] = []
            for kind in ("retained", "new", "merged", "split", "superseded", "revalidated"):
                for identifier in _strings(outcomes.get(kind, [])):
                    related = [
                        row for row in lineage
                        if identifier in _strings(row.get("source_ids", []))
                        or identifier in _strings(row.get("target_ids", []))
                    ]
                    source_ids = _strings(related[0].get("source_ids", [])) if related else ([identifier] if kind in {"retained", "revalidated"} else [])
                    target_ids = _strings(related[0].get("target_ids", [])) if related else ([identifier] if kind in {"retained", "revalidated", "new"} else [])
                    cards.append({
                        "id": f"mrq:{kind}:{identifier}",
                        "title": identifier,
                        "state": kind,
                        "evidence_count": len(related),
                        "source_ids": source_ids,
                        "target_ids": target_ids,
                    })
            return cards
        mrq_outcomes = outcome_cards()
        return {
            "dif_queue": [card(row, "queued") for row in pending_difs[:32]],
            "meaning_diffs": [card(row, "meaning") for row in meaning_difs[:16]],
            "unassigned_meaning_diffs": [card(row, "meaning") for row in unassigned_meaning_difs[:16]],
            "noise_diffs": [card(row, "noise") for row in noise_difs[:16]],
            "proposals": [_proposal_card(row) for row in proposals[:16]],
            "mrq_outcomes": mrq_outcomes[:32],
            "mrqs": [_mrq_card(row, dispositions) for row in pending_mrqs[:32]],
            "all_mrqs": [_mrq_card(row, dispositions) for row in active_mrqs[:32]],
            "batches": [{"id": batch.batch_id, "mrq_ids": list(batch.mrq_ids), "reason": batch.basis} for batch in batches[:16]],
            "decisions": [_decision_card(row) for row in decisions[:32]],
            "approval_count": len(approvals),
            "_queue_aggregates": {
                "dif-queue": {
                    "total": len(pending_difs),
                    "visible": min(len(pending_difs), 32),
                    "omitted": max(len(pending_difs) - 32, 0),
                },
                "mrq-queue": {
                    "total": len(pending_mrqs),
                    "visible": min(len(pending_mrqs), 32),
                    "omitted": max(len(pending_mrqs) - 32, 0),
                },
                "meaning-diffs": {"total": len(meaning_difs), "visible": min(len(meaning_difs), 16), "omitted": max(len(meaning_difs) - 16, 0)},
                "unassigned-meaning-diffs": {"total": len(unassigned_meaning_difs), "visible": min(len(unassigned_meaning_difs), 16), "omitted": max(len(unassigned_meaning_difs) - 16, 0)},
                "noise-diffs": {"total": len(noise_difs), "visible": min(len(noise_difs), 16), "omitted": max(len(noise_difs) - 16, 0)},
                "proposals": {"total": len(proposals), "visible": min(len(proposals), 16), "omitted": max(len(proposals) - 16, 0)},
                "mrq-outcomes": {"total": len(mrq_outcomes), "visible": min(len(mrq_outcomes), 32), "omitted": max(len(mrq_outcomes) - 32, 0)},
                "all-mrqs": {"total": len(active_mrqs), "visible": min(len(active_mrqs), 32), "omitted": max(len(active_mrqs) - 32, 0)},
                "batches": {"total": len(batches), "visible": min(len(batches), 16), "omitted": max(len(batches) - 16, 0)},
                "decisions": {"total": len(decisions), "visible": min(len(decisions), 32), "omitted": max(len(decisions) - 32, 0)},
            },
        }
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return {"dif_queue": [], "meaning_diffs": [], "noise_diffs": [], "proposals": [], "mrq_outcomes": [], "mrqs": [], "batches": [], "decisions": [], "approval_count": 0, "_queue_aggregates": {"dif-queue": {"total": 0, "visible": 0, "omitted": 0}, "mrq-queue": {"total": 0, "visible": 0, "omitted": 0}}}


def _dif_card(
    row: dict[str, JsonValue],
    state: str,
    extension: dict[str, JsonValue] | None = None,
    target: dict[str, JsonValue] | None = None,
    dependencies: list[dict[str, JsonValue]] | None = None,
) -> dict[str, JsonValue]:
    card: dict[str, JsonValue] = {"id": row.get("stable_diff_id", ""), "path": row.get("path", ""), "kind": row.get("object_kind", ""), "state": state}
    if extension:
        card.update({key: extension.get(key, "") for key in ("extension_uuid", "intervention_kind", "object_scope", "affected_base_identity")})
        role = extension.get("before_role") if row.get("change_type") == "deleted" else extension.get("after_role")
        card["component_id"] = f"{role}:extension:{extension.get('extension_uuid', '')}"
        card["evidence_count"] = len(_objects(extension.get("evidence", [])))
        values = dependencies or []
        card["dependency_count"] = len(values)
        card["compatibility_summary"] = {
            outcome: sum(item.get("outcome") == outcome for item in values)
            for outcome in ("present_compatible", "present_changed", "missing", "unresolved")
        }
        card["target_coverage"] = target.get("coverage_status", "") if target else ""
        card["blocker_codes"] = sorted(
            set(_strings(extension.get("diagnostic_codes", [])))
            | {str(item["diagnostic_code"]) for item in values if item.get("diagnostic_code")}
        )
    return card


def _proposal_card(row: dict[str, JsonValue]) -> dict[str, JsonValue]:
    payload = _object(row.get("payload", {}))
    decision_proposal = _object(payload.get("decision_proposal", {}))
    return {
        "id": row.get("key", ""),
        "job_id": row.get("job_id", ""),
        "kind": row.get("kind", ""),
        "approval_stage": payload.get("approval_stage", ""),
        "semantic_key": payload.get("semantic_key", ""),
        "dif_ids": sorted(_strings(payload.get("stable_diff_ids", []))),
        "evidence_count": len(_objects(payload.get("evidence", []))),
        "noise_count": len(json_array(payload.get("noise_proposals", payload.get("approved_noise_ids", [])))),
        "mrq_id": decision_proposal.get("mrq_id", ""),
        "created_at": row.get("created_at", ""),
    }


def _mrq_card(row: dict[str, JsonValue], dispositions: list[dict[str, JsonValue]]) -> dict[str, JsonValue]:
    mrq_id = row.get("mrq_id", "")
    diff_ids = sorted(str(item.get("stable_diff_id", "")) for item in dispositions if item.get("mrq_id") == mrq_id and item.get("primary"))
    source = _object(row.get("source_customization", {}))
    return {
        "id": mrq_id,
        "title": row.get("title", ""),
        "semantic_key": row.get("semantic_key", ""),
        "state": row.get("state", ""),
        "dif_ids": diff_ids,
        "evidence_count": len(_objects(source.get("evidence", []))),
    }


def _decision_card(row: dict[str, JsonValue]) -> dict[str, JsonValue]:
    decision = _object(row.get("migration_decision", {}))
    return {
        "id": row.get("mrq_id", ""),
        "title": row.get("title", ""),
        "decision": decision.get("decision", ""),
        "target_solution": decision.get("target_solution", ""),
        "evidence_count": len(_objects(decision.get("target_evidence", []))),
        "gap": decision.get("decision") == "adapt",
    }


def _circuit_state(*gates: GateValue | None) -> str:
    if not gates:
        return "unknown"
    states = [gate.get("state", "blocked") for gate in gates if gate]
    if not states:
        return "unknown"
    if all(state == "complete" for state in states):
        return "complete"
    if any(state == "ready" for state in states):
        return "ready"
    return "blocked"


def _agent_circuit_state(job_id: str, active_jobs: set[str], *gates: GateValue | None) -> str:
    return "active" if job_id in active_jobs else _circuit_state(*gates)


def _prepare_aggregates(repo: Path) -> dict[str, JsonValue]:
    import csv
    pointer_path = repo / "research/active-diff-generation.json"
    if not pointer_path.is_file():
        return {"diff_count": 0, "target_coverage_count": 0}
    try:
        pointer = _pointer(repo, "active-diff-generation.json")
        diff_root = repo / "analysis/indexes/generations" / str(pointer.get("generation_id"))
        inventory_path = diff_root / "diff-inventory.csv"
        coverage_path = diff_root / "target-coverage.csv"
        diff_count = 0
        if inventory_path.is_file():
            with inventory_path.open(encoding="utf-8", newline="") as stream:
                diff_count = sum(1 for _ in csv.DictReader(stream))
        coverage_count = 0
        if coverage_path.is_file():
            with coverage_path.open(encoding="utf-8", newline="") as stream:
                coverage_count = sum(1 for _ in csv.DictReader(stream))
        extension_count = len(_jsonl(diff_root / "extension-diff.jsonl"))
        return {"diff_count": diff_count, "target_coverage_count": coverage_count, "semantic_extension_diff_count": extension_count}
    except (OSError, ValueError, KeyError):
        return {"diff_count": 0, "target_coverage_count": 0, "semantic_extension_diff_count": 0}


def _analyze_aggregates(repo: Path, store: DispatcherStore | None = None) -> dict[str, JsonValue]:
    try:
        from .dif_classifications import coverage
        customer = read_diff_inventory(repo)
        counts = coverage(repo)
        semantic = sum(row.get("object_kind") == "extension_intervention" for row in customer)
        pointer = _pointer(repo, "active-diff-generation.json")
        root = repo / "analysis/indexes/generations" / str(pointer.get("generation_id"))
        raw_count = len(_csv(root / "extension-physical-diff.csv"))
        window_total = min(int(counts["remaining"]), 32)
        completed = reusable = failed = 0
        lease = store.lease("analyze-dif") if store else None
        if store and lease:
            try:
                from .dif_classifications import load_active
                published: set[str] = {row["stable_diff_id"] for row in load_active(repo)["rows"]}
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                published = set()
            completed = len(set(_transient_analyze_results(
                store.proposals(), str(lease.get("thread_id", "")),
            )) - published)
        return {
            **counts,
            "customer_diff_count": len(customer),
            "semantic_extension_diff_count": semantic,
            "raw_extension_diff_count": raw_count,
            "window_size": window_total,
            "current_window_total": window_total,
            "current_window_completed": completed,
            "failed": failed,
            "reusable_completed": reusable,
            "window_published": not lease,
            "max_window": 32,
            "max_parallel": 4,
        }
    except Exception:
        return {"customer_diff_count": 0, "semantic_extension_diff_count": 0, "window_size": 0, "max_window": 32, "max_parallel": 4}


def _transient_analyze_results(
    operational: list[dict[str, JsonValue]],
    thread_id: str | None = None,
) -> dict[str, str]:
    rows = [
        row for row in operational
        if row.get("job_id") == "analyze-dif"
        and row.get("kind") == "node-result"
        and str(_object(row.get("payload", {})).get("name", "")).startswith("analyze-dif:")
    ]
    selected_thread = thread_id or (rows[-1].get("thread_id") if rows else None)
    results: dict[str, str] = {}
    for row in rows:
        if row.get("thread_id") != selected_thread:
            continue
        payload = _object(row.get("payload", {}))
        envelope = _object(payload.get("envelope", {}))
        result = _object(envelope.get("result", {}))
        if result.get("kind") in {"meaning", "noise"} and result.get("stable_diff_id"):
            results[str(result["stable_diff_id"])] = str(result["kind"])
    return results


def _form_mrq_aggregates(repo: Path, store: DispatcherStore | None = None) -> dict[str, JsonValue]:
    try:
        from .consolidation import load_active
        raw_state: object = load_active(repo)
        state = _object(raw_state)
        pointer = _object(state["pointer"])
        mrq = _object(state["mrq"])
        result: dict[str, JsonValue] = {
            "active_mrq_count": len(_objects(mrq.get("mrq.jsonl", []))),
            "plan_fingerprint": pointer.get("plan_fingerprint"),
            "publication_state": pointer["state"],
            "published": int(pointer["state"] == "active"),
            "approval_pending": 0,
            "already_applied": False,
        }
        if store:
            try:
                from .consolidation import input_snapshot, normalized_records, partition_manifest
                from .user_state import load_agent_profiles
                profiles = load_agent_profiles(repo, store.base)
                configured = next(item for item in step_configurations(repo) if item["step"]["id"] == "consolidate-mrq")
                coordinator_name = next(
                    role["agent_profile"]
                    for phase in configured["step"].get("agent_phases", [])
                    for role in phase["roles"]
                    if role["role_id"] == "coordinator"
                )
                profile = profiles[coordinator_name]
                snapshot = input_snapshot(repo)
                input_context_tokens = profile.get("input_context_tokens")
                if not isinstance(input_context_tokens, int) or isinstance(input_context_tokens, bool):
                    raise ValueError("agent profile input_context_tokens must be an integer")
                manifest = partition_manifest(
                    normalized_records(snapshot),
                    input_context_tokens,
                    estimator_version=str(profile.get("context_estimator_version", "utf8-v1")),
                )
                partitions = manifest["partitions"]
                pairs = manifest["pairs"]
                result.update({
                    "input_context_tokens": manifest["input_context_tokens"],
                    "context_estimator_version": manifest["estimator_version"],
                    "partition_count": len(partitions),
                    "pair_count": len(pairs),
                    "planned_invocation_count": manifest["planned_invocation_count"],
                    "plan_status": "ready",
                })
            except (OSError, ValueError, KeyError, StopIteration, TypeError):
                result.update({
                    "plan_status": "blocked",
                    "blocker_code": "consolidation.context_capacity",
                    "blocker_message": "verified positive model input context capacity is required",
                })
            lease = store.lease("consolidate-mrq")
            if lease:
                result.update(_object(lease.get("summary", {})))
            proposal = next((
                row for row in reversed(store.proposals())
                if row.get("job_id") == "consolidate-mrq"
                and row.get("kind") == "approval"
                and row.get("consumed_at") is None
            ), None)
            if proposal:
                payload = _object(proposal["payload"])
                result.update(_object(payload.get("aggregates", {})))
                result["approval_pending"] = 1
                relative = payload.get("plan_path")
                plan_path = store.path.parent / relative if isinstance(relative, str) and relative else None
                if plan_path and plan_path.is_file():
                    plan = parse_json_object(plan_path.read_text(encoding="utf-8"))
                    manifest = _object(plan["partition_manifest"])
                    result.update({
                        "input_context_tokens": manifest["input_context_tokens"],
                        "context_estimator_version": manifest["estimator_version"],
                        "partition_count": len(json_array(manifest["partitions"])),
                        "pair_count": len(json_array(manifest["pairs"])),
                        "planned_invocation_count": manifest["planned_invocation_count"],
                    })
            elif isinstance(pointer.get("plan_fingerprint"), str):
                plan_fingerprint = str(pointer["plan_fingerprint"])
                plan_path = (
                    store.path.parent
                    / "consolidation-plans"
                    / f"{plan_fingerprint.removeprefix('sha256:')}.json"
                )
                if plan_path.is_file():
                    plan = parse_json_object(plan_path.read_text(encoding="utf-8"))
                    carryover = _object(plan.get("decision_carryover", {}))
                    result["legacy_decisions_stale"] = len(
                        json_array(carryover.get("stale", []))
                    )
        return result
    except Exception:
        return {"active_mrq_count": 0, "publication_state": "unpublished"}


def _classify_aggregates(repo: Path) -> dict[str, JsonValue]:
    from .mrq_batches import MRQRecord
    records: list[MRQRecord] = []
    windows: list[list[MRQRecord]] = []
    try:
        from .mrq_batches import load_active, source_mrq_payload, stable_windows
        _generation_id, _fingerprint, records = source_mrq_payload(repo)
        windows = stable_windows(records)
        batches = load_active(repo)
        return {
            "state": "complete",
            "input_mrq_count": len(records),
            "window_count": len(windows),
            "batch_count": len(batches),
            "validation_state": "valid",
        }
    except Exception as exc:
        return {
            "state": "waiting",
            "input_mrq_count": len(records),
            "window_count": len(windows),
            "batch_count": 0,
            "validation_state": "missing_or_invalid",
            "validation_error": str(exc),
        }


def _decide_aggregates(repo: Path) -> dict[str, JsonValue]:
    try:
        from .consolidation import load_active
        raw_state: object = load_active(repo)
        state = _object(raw_state)
        pointer = _object(state["pointer"])
        mrq = _object(state["mrq"])
        mrq_rows = _objects(mrq["mrq.jsonl"])
        rows: list[dict[str, JsonValue]] = []
        decision_generation_id = pointer.get("decision_generation_id")
        if isinstance(decision_generation_id, str):
            from .decision_generations import validate_generation
            rows = _objects(validate_generation(
                repo,
                decision_generation_id,
                allowed_mrq_ids={str(row["mrq_id"]) for row in mrq_rows},
            )["decisions.jsonl"])
        return {
            "decision_count": len(rows),
            "adapt_count": sum(_object(row["decision"])["decision"] == "adapt" for row in rows),
            "pending_count": len(mrq_rows) - len(rows),
        }
    except Exception:
        return {"decision_count": 0, "adapt_count": 0, "pending_count": 0}
