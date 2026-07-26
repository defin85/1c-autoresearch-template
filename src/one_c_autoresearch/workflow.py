from __future__ import annotations

import csv
import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .contracts import MRQ_STATES, ROLES, canonical_json, reject_secrets, sha256
from . import __version__


GATES = (
    ("project-configured", "project.validate"),
    ("sources-acquired", "sources.validate"),
    ("diffs-built", "diff.validate"),
    ("diffs-classified", "mrq.ownership"),
    ("source-evidence-complete", "mrq.source-evidence"),
    ("decisions-approved", "mrq.approvals"),
    ("published", "workflow.verify"),
)
JOBS = (
    ("configure", (), (("validate-project", "project.validate", "1"),)),
    ("acquire-sources", ("configure",), (("acquire-sources", "sources.acquire", "1"),)),
    ("build-diffs", ("acquire-sources",), (("build-diffs", "diff.build", "1"),)),
    ("index-sources", ("build-diffs",), (("index-sources", "indexes.build", "1"),)),
    ("discover-mrq", ("index-sources",), (("discover-mrq", "mrq.discover-next", "2"),)),
    ("classify-mrq", ("discover-mrq",), (("classify-mrq", "mrq.classify-batches", "1"),)),
    ("decide-mrq", ("classify-mrq",), (("decide-mrq", "mrq.decide-next", "2"),)),
    ("publish", ("decide-mrq",), (("build-projections", "projections.build", "1"), ("verify-workflow", "workflow.verify", "1"))),
)
PARAMETERS = {
    "project.validate": {"timeout_seconds"},
    "sources.acquire": {"timeout_seconds"},
    "diff.build": {"timeout_seconds", "max_retries"},
    "indexes.build": {"timeout_seconds"},
    "mrq.discover-next": {"timeout_seconds", "agent_phases"},
    "mrq.classify-batches": {"timeout_seconds", "agent_phases"},
    "mrq.decide-next": {"timeout_seconds", "agent_phases"},
    "projections.build": {"timeout_seconds", "max_retries"},
    "workflow.verify": {"timeout_seconds"},
}
AGENT_PHASE_CATALOG = {
    "mrq.discover-next": (
        {"phase_id": "analyze-dif", "modes": ("sequential", "parallel-pool"), "roles": ("analyzer",)},
        {"phase_id": "form-mrq", "modes": ("coordinated-pool",), "roles": ("coordinator", "grouper")},
    ),
    "mrq.classify-batches": (
        {"phase_id": "classify-batches", "modes": ("sequential",), "roles": ("classifier",)},
    ),
    "mrq.decide-next": (
        {"phase_id": "research-target", "modes": ("sequential", "parallel-pool"), "roles": ("researcher",)},
    ),
}
_CATALOG = {
    "project.validate": {"executor": "application", "effect": "read", "paths": ["project.toml", "research/"], "artifacts": ["workflow-snapshot"], "validator": "doctor", "retryable": [], "approval_required": False},
    "sources.acquire": {"executor": "application", "effect": "write", "paths": ["sources/generations/", "research/active-source-generation.json"], "artifacts": ["source-generation"], "validator": "sources.validate", "retryable": [], "approval_required": True},
    "diff.build": {"executor": "application", "effect": "write", "paths": ["analysis/indexes/generations/", "research/active-diff-generation.json"], "artifacts": ["diff-generation"], "validator": "diff.validate", "retryable": ["transient_io"], "approval_required": False},
    "indexes.build": {"executor": "rlm-tools-bsl", "effect": "user-scope-write", "paths": ["sources/generations/"], "artifacts": ["source-index"], "validator": "indexes.validate", "retryable": [], "approval_required": False},
    "mrq.discover-next": {"executor": "agent", "effect": "proposal", "paths": ["analysis/migration-requirements/generations/", "research/active-generation.json"], "artifacts": ["mrq-generation"], "validator": "mrq.validate", "retryable": [], "approval_required": False},
    "mrq.classify-batches": {"executor": "agent", "effect": "proposal", "paths": ["analysis/migration-requirements/batch-generations/", "research/active-generation.json"], "artifacts": ["mrq-batch-generation"], "validator": "mrq-batches-ready", "retryable": [], "approval_required": False},
    "mrq.decide-next": {"executor": "agent", "effect": "proposal", "paths": ["analysis/migration-requirements/generations/", "research/active-generation.json"], "artifacts": ["mrq-generation"], "validator": "mrq.validate", "retryable": [], "approval_required": True},
    "projections.build": {"executor": "application", "effect": "write", "paths": ["outputs/"], "artifacts": ["projections"], "validator": "projections.validate", "retryable": ["transient_io"], "approval_required": False},
    "workflow.verify": {"executor": "application", "effect": "read", "paths": ["."], "artifacts": ["workflow-snapshot"], "validator": "doctor.strict", "retryable": [], "approval_required": False},
}
OPERATION_CATALOG = {
    operation: {
        "version": "1",
        "parameters": sorted(PARAMETERS[operation]),
        **entry,
        **({"fixed_inputs": {"mode": "ensure", "selector": "all"}, "run_inputs": {"mode": ["ensure", "rebuild"], "selector": "all|component_ids"}} if operation == "indexes.build" else {}),
    }
    for operation, entry in _CATALOG.items()
}
for _operation, _phases in AGENT_PHASE_CATALOG.items():
    OPERATION_CATALOG[_operation]["version"] = "1" if _operation == "mrq.classify-batches" else "2"
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
    "active_generation": "research/active-generation.json",
}


def _valid_agent_profile(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) is not None


def _safe_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 2**53 - 1


def validate_agent_phases(operation: str, phases: Any) -> list[dict[str, Any]]:
    catalog = AGENT_PHASE_CATALOG.get(operation)
    if catalog is None:
        raise ValueError(f"{operation} does not declare agent phases")
    if not isinstance(phases, list) or len(phases) != len(catalog):
        raise ValueError(f"{operation} requires its fixed agent phase catalog")
    for index, (phase, declared) in enumerate(zip(phases, catalog, strict=True)):
        if not isinstance(phase, dict) or set(phase) != {"phase_id", "mode", "max_concurrency", "roles"}:
            raise ValueError(f"agent phase[{index}] fields differ")
        if phase["phase_id"] != declared["phase_id"] or phase["mode"] not in declared["modes"]:
            raise ValueError(f"agent phase[{index}] identity, order, or mode differs")
        if not _safe_positive_int(phase["max_concurrency"]):
            raise ValueError(f"agent phase[{index}] max_concurrency is not a safe integer")
        roles = phase["roles"]
        if not isinstance(roles, list) or tuple(role.get("role_id") for role in roles if isinstance(role, dict)) != declared["roles"]:
            raise ValueError(f"agent phase[{index}] roles differ")
        for role_index, role in enumerate(roles):
            if set(role) != {"role_id", "agent_profile", "count", "instruction_supplement"}:
                raise ValueError(f"agent phase[{index}].role[{role_index}] fields differ")
            if not _valid_agent_profile(role["agent_profile"]) or not _safe_positive_int(role["count"]):
                raise ValueError(f"agent phase[{index}].role[{role_index}] profile or count is invalid")
            if not isinstance(role["instruction_supplement"], str) or len(role["instruction_supplement"]) > 4000:
                raise ValueError(f"agent phase[{index}].role[{role_index}] instruction supplement is invalid")
        counts = {role["role_id"]: role["count"] for role in roles}
        if phase["mode"] == "sequential" and (list(counts.values()) != [1] or phase["max_concurrency"] != 1):
            raise ValueError("sequential phase requires one role, count=1 and max_concurrency=1")
        worker = counts.get("grouper", next(iter(counts.values())))
        if phase["mode"] != "sequential" and phase["max_concurrency"] > worker:
            raise ValueError("agent phase max_concurrency exceeds worker count")
        if phase["mode"] == "coordinated-pool" and counts.get("coordinator") != 1:
            raise ValueError("coordinated phase requires one coordinator")
        reject_secrets(phase, f"agent phase {phase['phase_id']}")
    return phases


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


def read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        value = tomllib.load(stream)
    reject_secrets(value, str(path))
    return value


def workflow_fingerprint(repo: Path) -> str:
    return "sha256:" + sha256((repo / "research/workflow.toml").read_bytes())


def validate_project_contract(repo: Path) -> dict[str, Any]:
    project = read_toml(repo / "project.toml")
    unknown = sorted(set(project) - PROJECT_SECTIONS)
    if unknown:
        raise ValueError(f"unknown project.toml section [{unknown[0]}]; allowed sections are [project], [mcp], [web], [policy]")
    for section, values in project.items():
        if not isinstance(values, dict):
            raise ValueError(f"project.toml section [{section}] must be a table")
        for key, owner in RESEARCH_OWNED_PROJECT_KEYS.items():
            if key in values:
                raise ValueError(f"project.toml [{section}].{key} is owned by {owner}")
    values = project.get("project", {})
    missing = [key for key in ("id", "product", "baseline_version", "target_version", "next_vendor_version") if not str(values.get(key, "")).strip()]
    if missing:
        raise ValueError(f"empty project fields: {', '.join(missing)}")
    return project


def state_fingerprint(repo: Path) -> str:
    paths = ("project.toml", "research/workflow.toml", "research/infobases.toml", "research/external-artifacts.toml", "research/indexing.toml", "research/active-source-generation.json", "research/active-diff-generation.json", "research/active-generation.json", "outputs/projections.json")
    return "sha256:" + sha256(canonical_json({name: sha256((repo / name).read_bytes()) if (repo / name).is_file() else None for name in paths}))


def validate_workflow_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if set(manifest) != {"schema_version", "tool_version", "gates", "jobs"}:
        errors.append(f"workflow top-level fields differ: {sorted(set(manifest) ^ {'schema_version', 'tool_version', 'gates', 'jobs'})}")
    if manifest.get("schema_version") != "3":
        errors.append("unsupported workflow schema; use a compatible tool or recreate the repository")
    if manifest.get("tool_version") != __version__:
        errors.append(f"workflow requires tool version {manifest.get('tool_version')}; installed version is {__version__}")
    for index, item in enumerate(manifest.get("gates", [])):
        if set(item) != {"id", "validator"}:
            errors.append(f"gate[{index}] fields differ: {sorted(set(item) ^ {'id', 'validator'})}")
    actual_gates = tuple((item.get("id"), item.get("validator")) for item in manifest.get("gates", []))
    if actual_gates != GATES:
        errors.append(f"fixed seven-gate contract changed: {actual_gates!r}")
    actual_jobs = []
    for job_index, job in enumerate(manifest.get("jobs", [])):
        if set(job) != {"id", "needs", "steps"}:
            errors.append(f"fixed jobs: job[{job_index}] fields differ: {sorted(set(job) ^ {'id', 'needs', 'steps'})}")
        steps = []
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
            if not isinstance(timeout, int) or not 30 <= timeout <= 86400:
                errors.append(f"job[{job_index}].step[{step_index}] invalid timeout for {operation}")
            retries = step.get("max_retries", 0)
            if retries not in ({0, 1} if operation in {"diff.build", "projections.build"} else {0}):
                errors.append(f"job[{job_index}].step[{step_index}] invalid retry count for {operation}")
            if operation in AGENT_PHASE_CATALOG:
                try:
                    validate_agent_phases(operation, step.get("agent_phases"))
                except ValueError as exc:
                    errors.append(f"job[{job_index}].step[{step_index}] {exc}")
            try:
                reject_secrets(step, f"workflow step {step.get('id')}")
            except ValueError as exc:
                errors.append(str(exc))
            steps.append((step.get("id"), operation, step.get("operation_version")))
        actual_jobs.append((job.get("id"), tuple(job.get("needs", [])), tuple(steps)))
    if tuple(actual_jobs) != JOBS:
        errors.append(f"fixed eight-job/nine-operation graph changed: {tuple(actual_jobs)!r}")
    if errors:
        raise ValueError("; ".join(errors))
    return manifest


def validate_workflow(repo: Path) -> dict[str, Any]:
    return validate_workflow_manifest(read_toml(repo / "research/workflow.toml"))


def step_configurations(repo: Path) -> list[dict[str, Any]]:
    manifest = validate_workflow(repo)
    result = []
    for job in manifest["jobs"]:
        for step in job["steps"]:
            operation = step["operation"]
            result.append({"job_id": job["id"], "step": step, "catalog": OPERATION_CATALOG[operation]})
    return result


def preview_step_patch(
    repo: Path,
    step_id: str,
    parameters: dict[str, Any],
    expected_manifest_fingerprint: str,
    state_base: Path | None = None,
) -> dict[str, Any]:
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
    if operation in AGENT_PHASE_CATALOG:
        validate_agent_phases(operation, candidate.get("agent_phases"))
    reject_secrets(candidate, f"workflow step {step_id}")
    result = {"step_id": step_id, "operation": operation, "before": current["step"], "after": candidate, "catalog": current["catalog"], "manifest_fingerprint": expected_manifest_fingerprint}
    if operation in AGENT_PHASE_CATALOG:
        try:
            from .user_state import load_agent_profiles
            profiles = load_agent_profiles(repo, state_base)
        except (OSError, ValueError, KeyError):
            profiles = {}
        try:
            from .pipeline_graphs import _owned_diff_ids, _read_diff_inventory, select_dif_window
            discover_ready = len(select_dif_window(repo))
            discover_remaining = len(
                {
                    row["stable_diff_id"]
                    for row in _read_diff_inventory(repo)
                }
                - _owned_diff_ids(repo)
            )
        except (OSError, ValueError, KeyError, IndexError):
            discover_ready = discover_remaining = 0
        try:
            from .mrq import active
            from .mrq_batches import load_active
            mrq_rows = [
                row
                for row in active(repo)["mrq.jsonl"]
                if row.get("state") != "superseded"
                and not row.get("migration_decision", {}).get("decision")
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
            "form-mrq": discover_ready,
            "classify-batches": classification_ready,
            "research-target": target_ready,
        }
        maximum_calls = {
            "analyze-dif": discover_ready,
            "form-mrq": discover_ready + int(discover_ready > 0 and discover_ready == discover_remaining),
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
            for phase in candidate["agent_phases"]
        ]
    return result


def _pointer(repo: Path, name: str) -> dict[str, Any]:
    try:
        return json.loads((repo / "research" / name).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid pointer research/{name}") from exc


def _project_blockers(repo: Path) -> list[Blocker]:
    try:
        validate_project_contract(repo)
        return []
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        return [Blocker("project.invalid", str(exc), "project.configure")]


def _source_blockers(repo: Path, *, deep: bool = True) -> list[Blocker]:
    from .sources import validate_active
    try:
        validate_active(repo, deep=deep)
        return []
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return [Blocker("sources.invalid", str(exc), "sources.acquire")]


def _diff_blockers(repo: Path) -> list[Blocker]:
    from .diffs import validate_active
    try:
        validate_active(repo); return []
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return [Blocker("diff.invalid", str(exc), "diff.build")]


def _active_rows(repo: Path) -> tuple[list[dict[str, str]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from .stage_recompute import active_pointers
    pointers = active_pointers(repo)
    diff_pointer = pointers["diff"]
    diff_root = repo / "analysis/indexes/generations" / str(diff_pointer.get("generation_id"))
    with (diff_root / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
        diffs = [row for row in csv.DictReader(stream) if row.get("comparison_id", "").startswith("CMP-")]
    pointer = pointers["mrq"]
    generation = pointer.get("canonical_generation_id")
    if not generation:
        return diffs, [], [], []
    from .mrq import active
    state = active(
        repo,
        pointer_candidate=pointer,
        source_candidate=pointers["source"],
        diff_candidate=diff_pointer,
    )
    return diffs, state["mrq.jsonl"], state["dispositions.jsonl"], state["approvals.jsonl"]


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def semantic_diff_context(repo: Path, stable_diff_id: str, fact: dict[str, Any] | None = None) -> dict[str, Any]:
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
    context: dict[str, Any] = {"diff": fact, "allowed_paths": [fact["path"]]}
    if pointer.get("schema_version") != "2" or fact.get("object_kind") != "extension_intervention":
        return context
    details = next((row for row in _jsonl(root / "extension-diff.jsonl") if row.get("stable_diff_id") == stable_diff_id), None)
    if details is None:
        raise ValueError(f"semantic extension DIF details are missing: {stable_diff_id}")
    dependencies = [row for row in _jsonl(root / "extension-dependencies.jsonl") if row.get("stable_diff_id") == stable_diff_id]
    with (root / "target-coverage.csv").open(encoding="utf-8", newline="") as stream:
        coverage = next((row for row in csv.DictReader(stream) if row.get("customer_diff_id") == stable_diff_id), None)
    context.update({
        "extension": details,
        "dependencies": dependencies,
        "target_coverage": coverage,
        "allowed_paths": sorted({f"{item['role']}/extensions/{details['extension_uuid']}/{item['path']}" for item in details["evidence"]}),
        "compatibility_summary": {
            "dependency_outcomes": sorted({item["outcome"] for item in dependencies}),
            "diagnostic_codes": details["diagnostic_codes"],
            "target_coverage_status": coverage.get("coverage_status", "") if coverage else "",
        },
    })
    return context


def status(repo: Path, *, deep: bool = True) -> dict[str, Any]:
    repo = repo.resolve()
    from .stage_recompute import recover_active_publication

    recover_active_publication(repo)
    validate_workflow(repo)
    checks = [_project_blockers(repo), _source_blockers(repo, deep=deep), _diff_blockers(repo)]
    if not any(checks):
        diffs, mrqs, dispositions, approvals = _active_rows(repo)
        customer = {row["stable_diff_id"] for row in diffs if row.get("before_role") == "vendor_baseline" and row.get("after_role") == "target_cf"}
        owners: dict[str, int] = {key: 0 for key in customer}
        mrq_ids = {item.get("mrq_id") for item in mrqs}
        approved_noise = {(event.get("target_id"), event.get("fingerprint")) for event in approvals if event.get("event") == "approve"}
        for item in dispositions:
            noise = item.get("approved_noise")
            valid_noise = isinstance(noise, dict) and (item.get("stable_diff_id"), sha256(canonical_json(noise))) in approved_noise
            if item.get("primary") and item.get("stable_diff_id") in owners and (item.get("mrq_id") in mrq_ids or valid_noise):
                owners[item["stable_diff_id"]] += 1
        checks.append([] if all(value == 1 for value in owners.values()) else [Blocker("mrq.ownership", "each customer DIF needs exactly one primary MRQ or approved noise", "mrq.discover-next")])
        active = [item for item in mrqs if item.get("state") != "superseded"]
        checks.append([] if all(item.get("source_customization", {}).get("evidence") for item in active) else [Blocker("mrq.source_evidence", "active MRQ source evidence is incomplete", "mrq.discover-next")])
        checks.append([] if all(item.get("state") == "approved" and item.get("migration_decision", {}).get("decision") for item in active) else [Blocker("mrq.approvals", "active MRQ decisions are incomplete or unapproved", "mrq.decide-next")])
        checks.append(_publication_blockers(repo, _pointer(repo, "active-generation.json").get("canonical_generation_id"), deep=deep))
    while len(checks) < len(GATES):
        checks.append([Blocker("predecessor.blocked", "a predecessor gate is incomplete", "")])
    gates: list[Gate] = []
    predecessor_complete = True
    for (gate_id, _), blockers in zip(GATES, checks, strict=True):
        effective = blockers if predecessor_complete else [Blocker("predecessor.blocked", "a predecessor gate is incomplete", "")]
        state = "complete" if not effective else ("ready" if predecessor_complete and effective[0].action else "blocked")
        gates.append(Gate(gate_id, state, tuple(effective)))
        predecessor_complete &= state == "complete"
    return {"schema_version": "1", "tool_version": __version__, "operation_versions": {operation: item["version"] for operation, item in OPERATION_CATALOG.items()}, "workflow_fingerprint": state_fingerprint(repo), "manifest_fingerprint": workflow_fingerprint(repo), "state": "complete" if all(g.state == "complete" for g in gates) else "ready" if any(g.state == "ready" for g in gates) else "blocked", "gates": [asdict(g) for g in gates]}


def _projection_blockers(repo: Path, generation: str | None) -> list[Blocker]:
    if not generation:
        return [Blocker("projection.missing", "canonical generation is absent", "projections.build")]
    manifest = repo / "outputs/projections.json"
    if not manifest.is_file():
        return [Blocker("projection.missing", "projections are absent", "projections.build")]
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        value = {}
    from .mrq import active
    return [] if value == projection_value(active(repo)) else [Blocker("projection.stale", "projections are stale or independently edited", "projections.build")]


def projection_value(state: dict[str, Any]) -> dict[str, Any]:
    generation = state["pointer"].get("canonical_generation_id")
    mrqs = state["mrq.jsonl"]
    dispositions = state["dispositions.jsonl"]
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
    from .mrq import active as active_mrq
    from .contracts import require_tracked_clean
    try:
        pointer = _pointer(repo, "active-generation.json")
        if pointer.get("batch_generation"):
            from .mrq_batches import load_active
            load_active(repo)
        validate_active(repo, deep=True, require_tracked_clean=True)
        validate_active_diffs(repo, require_tracked_clean_state=True)
        active_mrq(repo, require_tracked_clean_state=True)
        require_tracked_clean(repo, [repo / "outputs/projections.json"])
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        return [Blocker("publication.strict", str(exc), "workflow.verify")]
    return []


def next_work(repo: Path) -> dict[str, Any] | None:
    snapshot = status(repo)
    for gate in snapshot["gates"]:
        if gate["state"] == "ready":
            blocker = gate["blockers"][0]
            result: dict[str, Any] = {"gate_id": gate["id"], "action": blocker["action"], "blocker": blocker, "workflow_fingerprint": snapshot["workflow_fingerprint"]}
            if blocker["action"] == "mrq.discover-next":
                diffs, mrqs, dispositions, _approvals = _active_rows(repo)
                owned = {item["stable_diff_id"] for item in dispositions if item.get("primary")}
                pending = sorted((item for item in diffs if item.get("before_role") == "vendor_baseline" and item.get("after_role") == "target_cf" and item["stable_diff_id"] not in owned), key=lambda item: item["stable_diff_id"])
                if pending:
                    target_pointer = _pointer(repo, "active-diff-generation.json")
                    if pending[0].get("object_kind") == "extension_intervention":
                        context = semantic_diff_context(repo, pending[0]["stable_diff_id"], pending[0])
                    else:
                        coverage_path = repo / "analysis/indexes/generations" / target_pointer["generation_id"] / "target-coverage.csv"
                        with coverage_path.open(encoding="utf-8", newline="") as stream:
                            coverage = next((item for item in csv.DictReader(stream) if item["customer_diff_id"] == pending[0]["stable_diff_id"]), None)
                        context = {"diff": pending[0], "target_coverage": coverage, "allowed_paths": [pending[0]["path"]]}
                    result["work_unit"] = {"id": pending[0]["stable_diff_id"], "kind": "uncovered-diff", **context, "source_generation_id": _pointer(repo, "active-source-generation.json")["generation_id"], "diff_generation_id": target_pointer["generation_id"]}
            elif blocker["action"] == "mrq.decide-next":
                try:
                    from .mrq_batches import load_active
                    load_active(repo)
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
                _diffs, mrqs, dispositions, _approvals = _active_rows(repo)
                pending = sorted((item for item in mrqs if item.get("state") != "superseded" and item.get("state") != "approved"), key=lambda item: item["mrq_id"])
                if pending:
                    item = pending[0]
                    owned = {relation["stable_diff_id"] for relation in dispositions if relation.get("mrq_id") == item["mrq_id"] and relation.get("primary")}
                    paths = sorted({evidence["path"] for evidence in item.get("source_customization", {}).get("evidence", []) if evidence.get("stable_diff_id") in owned})
                    semantic = [semantic_diff_context(repo, identifier) for identifier in sorted(owned) if next((row for row in diffs if row["stable_diff_id"] == identifier), {}).get("object_kind") == "extension_intervention"]
                    result["work_unit"] = {"id": item["mrq_id"], "kind": "migration-decision" if not item.get("migration_decision", {}).get("decision") else "approval", "mrq": item, "semantic_extension_context": semantic, "source_generation_id": item["source_generation_id"], "diff_generation_id": item["diff_generation_id"], "allowed_paths": sorted(set(paths) | {path for context in semantic for path in context["allowed_paths"]})}
            return result
    return None


def attach_dispatcher(snapshot: dict[str, Any], repo: Path, base: Path | None = None) -> dict[str, Any]:
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
        return snapshot
    try:
        store = DispatcherStore(repo, base)
        store.open()
        try:
            projection = _dispatcher_projection(snapshot, repo, store)
        finally:
            store.close()
    except Exception:
        # отсутствие/повреждение SQLite-базы не должно ронять канонический снимок
        projection = {"schema_version": "2", "revision": 0, "fresh_at": "", "circuits": [], "jobs": {}, "error": "dispatcher_unavailable"}
    return {**snapshot, "dispatcher": projection}


def _zone_state(gate: dict[str, Any] | None) -> str:
    return {"complete": "complete", "ready": "waiting"}.get(
        str((gate or {}).get("state", "")),
        "unknown",
    )


def _prepare_zones(
    repo: Path,
    state_base: Path | None,
    gates: dict[str, dict[str, Any]],
    runs_root: Path,
    workflow_fingerprint: str,
) -> list[dict[str, Any]]:
    from .sources import current_profile_test, load_contract
    from .user_state import load_connections, state_root

    infobases, _artifacts = load_contract(repo)
    connections = load_connections(repo, state_base)
    profile_id = str(infobases.get("acquisition_profile", ""))
    zones = []
    for role in ROLES:
        profile = connections.get(
            str(infobases.get("roles", {}).get(role, {}).get("connection_profile", "")),
        )
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
        elif gates.get("diffs-built", {}).get("state") == "complete":
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
            run = json.loads(path.read_text(encoding="utf-8"))
            execution = run["execution_snapshot"]
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
            if status == "running" and process_identity_alive(run.get("process_identity")):
                state = "active"
            elif status == "completed":
                state = "complete"
            elif status == "failed":
                state = "error"
            else:
                continue
            updated_at = str((run.get("events") or [{}])[-1].get("timestamp", ""))
            return {"state": state, **({"updated_at": updated_at} if updated_at else {})}
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _dispatcher_projection(snapshot: dict[str, Any], repo: Path, store: Any) -> dict[str, Any]:
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
    phases: list[dict[str, Any]] = []
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
        profile_definitions = execution_snapshot.get("profiles", {}) if isinstance(execution_snapshot, dict) else safe_profiles
        snapshot_environment_available = False
        if isinstance(execution_snapshot, dict):
            try:
                from .agents import verify_execution_environment
                verify_execution_environment(execution_snapshot, codex_probe)
                snapshot_environment_available = True
            except (OSError, RuntimeError, ValueError):
                pass
        for phase in phase_definitions:
            roles = []
            for role in phase["roles"]:
                rows = store.invocations(
                    active_run_id,
                    limit=16,
                    phase_id=phase["phase_id"],
                    role_id=role["role_id"],
                ) if active_run_id else []
                invocation_total = store.invocation_count(
                    active_run_id,
                    phase["phase_id"],
                    role["role_id"],
                ) if active_run_id else 0
                units = [item for item in work_rows if item["phase_id"] == phase["phase_id"] and item["role_id"] == role["role_id"]]
                counts = {state: sum(item["status"] == state for item in units) for state in ("running", "queued", "completed", "failed", "cancelled", "interrupted")}
                profile = profile_definitions.get(role["agent_profile"], {})
                roles.append({
                    "role_id": role["role_id"],
                    "agent_profile": role["agent_profile"],
                    "model": profile.get("model", ""),
                    "reasoning_effort": profile.get("reasoning_effort", ""),
                    "environment_preset": profile.get("environment_preset", ""),
                    "environment_status": (
                        "available"
                        if snapshot_environment_available
                        or (
                            not active_run_id
                            and profile.get("environment_preset") == "local-read-only"
                            and codex_probe is not None
                        )
                        else "unavailable"
                    ),
                    "configured_slots": role["count"],
                    "requested": len(units),
                    **counts,
                    "invocation_total": invocation_total,
                    "invocation_omitted": max(invocation_total - len(rows), 0),
                    "invocations": rows,
                })
            phases.append({"job_id": configured["job_id"], "phase_id": phase["phase_id"], "mode": phase["mode"], "max_concurrency": phase["max_concurrency"], "roles": roles})
    # контуры выводятся из канонического снимка
    gates = {gate["id"]: gate for gate in snapshot.get("gates", [])}
    classify_aggregates = _classify_aggregates(repo)
    circuits = [
        {"id": "prepare-diffs", "state": _circuit_state(gates.get("sources-acquired"), gates.get("diffs-built")), "aggregates": _prepare_aggregates(repo), "zones": _prepare_zones(repo, store.base, gates, store.path.parent / "runs", str(snapshot.get("workflow_fingerprint", "")))},
        {"id": "analyze-dif", "state": _circuit_state(gates.get("diffs-classified")), "leases": [lease for lease in leases if lease["job_id"] == "discover-mrq"], "aggregates": _analyze_aggregates(repo)},
        {"id": "form-mrq", "state": _circuit_state(gates.get("source-evidence-complete")), "aggregates": _form_mrq_aggregates(repo), "publication": {"id": "publication", "state": _zone_state(gates.get("source-evidence-complete"))}},
        {"id": "classify-mrq", "state": classify_aggregates["state"], "leases": [lease for lease in leases if lease["job_id"] == "classify-mrq"], "aggregates": classify_aggregates},
        {"id": "decide-target", "state": _circuit_state(gates.get("decisions-approved")), "leases": [lease for lease in leases if lease["job_id"] == "decide-mrq"], "aggregates": _decide_aggregates(repo)},
    ]
    from datetime import datetime, timezone
    retry_candidates = []
    runs_root = store.path.parent / "runs"
    for path in sorted(
        runs_root.glob("*.json") if runs_root.is_dir() else [],
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    ):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
            execution = run["execution_snapshot"]
            if (
                run.get("execution_snapshot_fingerprint")
                != "sha256:" + sha256(canonical_json(execution))
            ):
                continue
            operation = execution.get("operation")
            job_id = {
                "mrq.discover-next": "discover-mrq",
                "mrq.classify-batches": "classify-mrq",
                "mrq.decide-next": "decide-mrq",
            }.get(operation)
            from .events import RETRYABLE_RUN_STATUSES
            if not job_id or run.get("status") not in RETRYABLE_RUN_STATUSES:
                continue
            bindings = execution.get("subject_bindings", {})
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
                        "work_unit_id": str(execution.get("work_unit", {}).get("id", "")),
                    },
                }
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if len(retry_candidates) == 20:
            break
    return {
        "schema_version": "2",
        "revision": revision,
        "fresh_at": updated_at or datetime.now(timezone.utc).isoformat(),
        "circuits": circuits,
        "jobs": {lease["job_id"]: lease for lease in leases},
        "stage_recompute": next((lease for lease in leases if lease["job_id"] == "stage-recompute"), None),
        "stage_recompute_run": store.latest_stage_recompute(),
        "items": _dispatcher_items(repo, store),
        "agent_phases": phases,
        "retry_candidates": retry_candidates,
    }


def _dispatcher_items(repo: Path, store: Any) -> dict[str, Any]:
    """Компактные карточки для экрана без второго предметного хранилища."""

    try:
        diffs, mrqs, dispositions, approvals = _active_rows(repo)
        customer = sorted(
            (row for row in diffs if row.get("before_role") == "vendor_baseline" and row.get("after_role") == "target_cf"),
            key=lambda row: row["stable_diff_id"],
        )
        owners = {row.get("stable_diff_id") for row in dispositions if row.get("primary")}
        noise = {row.get("stable_diff_id") for row in dispositions if row.get("approved_noise")}
        active_mrqs = sorted((row for row in mrqs if row.get("state") != "superseded"), key=lambda row: row["mrq_id"])
        pointer = _pointer(repo, "active-diff-generation.json")
        diff_root = repo / "analysis/indexes/generations" / str(pointer.get("generation_id"))
        extension_rows = _jsonl(diff_root / "extension-diff.jsonl") if pointer.get("schema_version") == "2" else []
        dependency_rows = _jsonl(diff_root / "extension-dependencies.jsonl") if pointer.get("schema_version") == "2" else []
        coverage_rows = _csv(diff_root / "target-coverage.csv")
        extension_by_id = {row["stable_diff_id"]: row for row in extension_rows}
        dependencies_by_id: dict[str, list[dict[str, Any]]] = {}
        for dependency in dependency_rows:
            dependencies_by_id.setdefault(dependency["stable_diff_id"], []).append(dependency)
        coverage_by_id = {row["customer_diff_id"]: row for row in coverage_rows}
        card = lambda row, state: _dif_card(
            row,
            state,
            extension_by_id.get(row["stable_diff_id"]),
            coverage_by_id.get(row["stable_diff_id"]),
            dependencies_by_id.get(row["stable_diff_id"], []),
        )
        from .mrq_batches import load_active
        batches = load_active(repo)
        return {
            "dif_queue": [card(row, "queued") for row in customer if row["stable_diff_id"] not in owners][:32],
            "meaning_diffs": [card(row, "meaning") for row in customer if row["stable_diff_id"] in owners and row["stable_diff_id"] not in noise][:16],
            "noise_diffs": [card(row, "noise") for row in customer if row["stable_diff_id"] in noise][:16],
            "proposals": [_proposal_card(row) for row in store.proposals() if row.get("kind") == "approval" and row.get("consumed_at") is None][:16],
            "mrqs": [_mrq_card(row, dispositions) for row in active_mrqs[:32]],
            "batches": [{"id": batch.batch_id, "mrq_ids": list(batch.mrq_ids), "reason": batch.basis} for batch in batches[:16]],
            "decisions": [_decision_card(row) for row in active_mrqs if row.get("migration_decision", {}).get("decision")][:32],
            "approval_count": len(approvals),
        }
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return {"dif_queue": [], "meaning_diffs": [], "noise_diffs": [], "proposals": [], "mrqs": [], "batches": [], "decisions": [], "approval_count": 0}


def _dif_card(
    row: dict[str, Any],
    state: str,
    extension: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    dependencies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    card = {"id": row.get("stable_diff_id", ""), "path": row.get("path", ""), "kind": row.get("object_kind", ""), "state": state}
    if extension:
        card.update({key: extension.get(key, "") for key in ("extension_uuid", "intervention_kind", "object_scope", "affected_base_identity")})
        role = extension.get("before_role") if row.get("change_type") == "deleted" else extension.get("after_role")
        card["component_id"] = f"{role}:extension:{extension.get('extension_uuid', '')}"
        card["evidence_count"] = len(extension.get("evidence", []))
        values = dependencies or []
        card["dependency_count"] = len(values)
        card["compatibility_summary"] = {
            outcome: sum(item.get("outcome") == outcome for item in values)
            for outcome in ("present_compatible", "present_changed", "missing", "unresolved")
        }
        card["target_coverage"] = (target or {}).get("coverage_status", "")
        card["blocker_codes"] = sorted(
            set(extension.get("diagnostic_codes", []))
            | {item["diagnostic_code"] for item in values if item.get("diagnostic_code")}
        )
    return card


def _proposal_card(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload", {})
    return {
        "id": row.get("key", ""),
        "job_id": row.get("job_id", ""),
        "kind": row.get("kind", ""),
        "approval_stage": payload.get("approval_stage", ""),
        "semantic_key": payload.get("semantic_key", ""),
        "dif_ids": sorted(payload.get("stable_diff_ids", [])),
        "evidence_count": len(payload.get("evidence", [])),
        "noise_count": len(payload.get("noise_proposals", payload.get("approved_noise_ids", []))),
        "mrq_id": (payload.get("decision_proposal") or {}).get("mrq_id", ""),
        "created_at": row.get("created_at", ""),
    }


def _mrq_card(row: dict[str, Any], dispositions: list[dict[str, Any]]) -> dict[str, Any]:
    mrq_id = row.get("mrq_id", "")
    diff_ids = sorted(item.get("stable_diff_id", "") for item in dispositions if item.get("mrq_id") == mrq_id and item.get("primary"))
    source = row.get("source_customization", {})
    return {
        "id": mrq_id,
        "title": row.get("title", ""),
        "semantic_key": row.get("semantic_key", ""),
        "state": row.get("state", ""),
        "dif_ids": diff_ids,
        "evidence_count": len(source.get("evidence", [])),
    }


def _decision_card(row: dict[str, Any]) -> dict[str, Any]:
    decision = row.get("migration_decision", {})
    return {
        "id": row.get("mrq_id", ""),
        "title": row.get("title", ""),
        "decision": decision.get("decision", ""),
        "target_solution": decision.get("target_solution", ""),
        "evidence_count": len(decision.get("target_evidence", [])),
        "gap": decision.get("decision") == "adapt",
    }


def _circuit_state(*gates: dict[str, Any]) -> str:
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


def _prepare_aggregates(repo: Path) -> dict[str, Any]:
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


def _analyze_aggregates(repo: Path) -> dict[str, Any]:
    try:
        from .pipeline_graphs import select_dif_window, _read_diff_inventory
        customer = _read_diff_inventory(repo)
        window = select_dif_window(repo)
        semantic = sum(row.get("object_kind") == "extension_intervention" for row in customer)
        pointer = _pointer(repo, "active-diff-generation.json")
        root = repo / "analysis/indexes/generations" / str(pointer.get("generation_id"))
        raw_count = len(_csv(root / "extension-physical-diff.csv"))
        return {"customer_diff_count": len(customer), "semantic_extension_diff_count": semantic, "raw_extension_diff_count": raw_count, "window_size": len(window), "max_window": 32, "max_parallel": 4}
    except Exception:
        return {"customer_diff_count": 0, "semantic_extension_diff_count": 0, "window_size": 0, "max_window": 32, "max_parallel": 4}


def _form_mrq_aggregates(repo: Path) -> dict[str, Any]:
    try:
        from .mrq import active
        state = active(repo)
        active_mrqs = [item for item in state["mrq.jsonl"] if item.get("state") != "superseded"]
        return {"active_mrq_count": len(active_mrqs), "disposition_count": len(state["dispositions.jsonl"])}
    except Exception:
        return {"active_mrq_count": 0, "disposition_count": 0}


def _classify_aggregates(repo: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    windows: list[list[dict[str, Any]]] = []
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


def _decide_aggregates(repo: Path) -> dict[str, Any]:
    try:
        from .mrq import active
        state = active(repo)
        decisions = [item for item in state["mrq.jsonl"] if item.get("migration_decision", {}).get("decision")]
        adapt_count = sum(1 for item in decisions if item.get("migration_decision", {}).get("decision") == "adapt")
        return {"decision_count": len(decisions), "adapt_count": adapt_count, "pending_count": sum(1 for item in state["mrq.jsonl"] if item.get("state") != "superseded" and not item.get("migration_decision", {}).get("decision"))}
    except Exception:
        return {"decision_count": 0, "adapt_count": 0, "pending_count": 0}
