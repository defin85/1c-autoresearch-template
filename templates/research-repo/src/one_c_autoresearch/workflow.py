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
    ("discover-mrq", ("index-sources",), (("discover-mrq", "mrq.discover-next", "1"),)),
    ("decide-mrq", ("discover-mrq",), (("decide-mrq", "mrq.decide-next", "1"),)),
    ("publish", ("decide-mrq",), (("build-projections", "projections.build", "1"), ("verify-workflow", "workflow.verify", "1"))),
)
PARAMETERS = {
    "project.validate": {"timeout_seconds"},
    "sources.acquire": {"timeout_seconds"},
    "diff.build": {"timeout_seconds", "max_retries"},
    "indexes.build": {"timeout_seconds"},
    "mrq.discover-next": {"timeout_seconds", "agent_profile", "instruction_supplement"},
    "mrq.decide-next": {"timeout_seconds", "agent_profile", "instruction_supplement"},
    "projections.build": {"timeout_seconds", "max_retries"},
    "workflow.verify": {"timeout_seconds"},
}
_CATALOG = {
    "project.validate": {"executor": "application", "effect": "read", "paths": ["project.toml", "research/"], "artifacts": ["workflow-snapshot"], "validator": "doctor", "retryable": [], "approval_required": False},
    "sources.acquire": {"executor": "application", "effect": "write", "paths": ["sources/generations/", "research/active-source-generation.json"], "artifacts": ["source-generation"], "validator": "sources.validate", "retryable": [], "approval_required": True},
    "diff.build": {"executor": "application", "effect": "write", "paths": ["analysis/indexes/generations/", "research/active-diff-generation.json"], "artifacts": ["diff-generation"], "validator": "diff.validate", "retryable": ["transient_io"], "approval_required": False},
    "indexes.build": {"executor": "rlm-tools-bsl", "effect": "user-scope-write", "paths": ["sources/generations/"], "artifacts": ["source-index"], "validator": "indexes.validate", "retryable": [], "approval_required": False},
    "mrq.discover-next": {"executor": "agent", "effect": "proposal", "paths": ["analysis/migration-requirements/generations/", "research/active-generation.json"], "artifacts": ["mrq-generation"], "validator": "mrq.validate", "retryable": [], "approval_required": False},
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
    if manifest.get("schema_version") != "1":
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
            supplement = step.get("instruction_supplement", "")
            if not isinstance(supplement, str) or len(supplement) > 4000:
                errors.append(f"job[{job_index}].step[{step_index}] invalid instruction supplement for {operation}")
            if operation in {"mrq.discover-next", "mrq.decide-next"} and not _valid_agent_profile(step.get("agent_profile")):
                errors.append(f"job[{job_index}].step[{step_index}] invalid or missing agent profile for {operation}")
            try:
                reject_secrets(step, f"workflow step {step.get('id')}")
            except ValueError as exc:
                errors.append(str(exc))
            steps.append((step.get("id"), operation, step.get("operation_version")))
        actual_jobs.append((job.get("id"), tuple(job.get("needs", [])), tuple(steps)))
    if tuple(actual_jobs) != JOBS:
        errors.append(f"fixed seven-job/eight-operation graph changed: {tuple(actual_jobs)!r}")
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


def preview_step_patch(repo: Path, step_id: str, parameters: dict[str, Any], expected_manifest_fingerprint: str) -> dict[str, Any]:
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
    supplement = candidate.get("instruction_supplement", "")
    if not isinstance(supplement, str) or len(supplement) > 4000:
        raise ValueError(f"invalid instruction supplement for {operation}")
    if "agent_profile" in candidate and not _valid_agent_profile(candidate["agent_profile"]):
        raise ValueError(f"invalid agent profile for {operation}")
    reject_secrets(candidate, f"workflow step {step_id}")
    return {"step_id": step_id, "operation": operation, "before": current["step"], "after": candidate, "catalog": current["catalog"], "manifest_fingerprint": expected_manifest_fingerprint}


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
    diff_pointer = _pointer(repo, "active-diff-generation.json")
    diff_root = repo / "analysis/indexes/generations" / str(diff_pointer.get("generation_id"))
    with (diff_root / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
        diffs = [row for row in csv.DictReader(stream) if row.get("comparison_id", "").startswith("CMP-")]
    pointer = _pointer(repo, "active-generation.json")
    generation = pointer.get("canonical_generation_id")
    if not generation:
        return diffs, [], [], []
    from .mrq import active
    state = active(repo)
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
        projection = {"schema_version": "1", "revision": 0, "fresh_at": "", "circuits": [], "jobs": {}, "error": "dispatcher_unavailable"}
    return {**snapshot, "dispatcher": projection}


def _dispatcher_projection(snapshot: dict[str, Any], repo: Path, store: Any) -> dict[str, Any]:
    """Собирает операционную проекцию четырёх контуров.

    Структура стабильна и предназначена для React Flow-схемы; координаты узлов
    остаются константами клиента.
    """

    revision, updated_at = store.revision()
    leases = store.leases()
    # контуры выводятся из канонического снимка
    gates = {gate["id"]: gate for gate in snapshot.get("gates", [])}
    circuits = [
        {"id": "prepare-diffs", "state": _circuit_state(gates.get("sources-acquired"), gates.get("diffs-built")), "aggregates": _prepare_aggregates(repo)},
        {"id": "analyze-dif", "state": _circuit_state(gates.get("diffs-classified")), "leases": [lease for lease in leases if lease["job_id"] == "discover-mrq"], "aggregates": _analyze_aggregates(repo)},
        {"id": "form-mrq", "state": _circuit_state(gates.get("source-evidence-complete")), "aggregates": _form_mrq_aggregates(repo)},
        {"id": "decide-target", "state": _circuit_state(gates.get("decisions-approved")), "leases": [lease for lease in leases if lease["job_id"] == "decide-mrq"], "aggregates": _decide_aggregates(repo)},
    ]
    from datetime import datetime, timezone
    return {
        "schema_version": "1",
        "revision": revision,
        "fresh_at": updated_at or datetime.now(timezone.utc).isoformat(),
        "circuits": circuits,
        "jobs": {lease["job_id"]: lease for lease in leases},
        "items": _dispatcher_items(repo, store),
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
        from .mrq_batches import classify
        batches = classify(active_mrqs)
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
        "semantic_key": payload.get("semantic_key", ""),
        "dif_ids": sorted(payload.get("stable_diff_ids", [])),
        "evidence_count": len(payload.get("evidence", [])),
        "noise_count": len(payload.get("approved_noise_ids", [])),
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
        from .mrq_batches import classify
        batches = classify(state["mrq.jsonl"])
        return {"active_mrq_count": len(active_mrqs), "target_batch_count": len(batches), "disposition_count": len(state["dispositions.jsonl"])}
    except Exception:
        return {"active_mrq_count": 0, "target_batch_count": 0, "disposition_count": 0}


def _decide_aggregates(repo: Path) -> dict[str, Any]:
    try:
        from .mrq import active
        state = active(repo)
        decisions = [item for item in state["mrq.jsonl"] if item.get("migration_decision", {}).get("decision")]
        adapt_count = sum(1 for item in decisions if item.get("migration_decision", {}).get("decision") == "adapt")
        return {"decision_count": len(decisions), "adapt_count": adapt_count, "pending_count": sum(1 for item in state["mrq.jsonl"] if item.get("state") != "superseded" and not item.get("migration_decision", {}).get("decision"))}
    except Exception:
        return {"decision_count": 0, "adapt_count": 0, "pending_count": 0}
