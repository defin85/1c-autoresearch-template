from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

from .contracts import atomic_json, canonical_json, normalize_relative, reject_secrets
from .sources import _run_command


PROPOSAL_FIELDS = {
    "dif.classify-next": {"classification", "semantic_hints", "evidence", "rationale"},
    "mrq.discover-next": {"semantic_key", "title", "stable_diff_ids", "supporting_diff_ids", "evidence", "business_meaning", "scope", "confidence", "rationale"},
    "mrq.classify-batches": {"mrq_ids", "basis", "linkage_proven"},
    "mrq.decide-next": {"mrq_id", "decision", "target_evidence", "target_coverage", "residual_gap", "target_solution", "rationale", "acceptance_criteria", "risk", "open_questions"},
}
CONSOLIDATION_GROUP_FIELDS = {
    "semantic_key", "title", "stable_diff_ids", "supporting_diff_ids",
    "component_keys", "source_mrq_ids", "evidence", "business_meaning",
    "scope", "confidence", "rationale", "split_source_mrq_id",
}
STRING_ARRAY_FIELDS = {
    "semantic_hints", "stable_diff_ids", "supporting_diff_ids", "component_keys",
    "source_mrq_ids", "mrq_ids", "acceptance_criteria", "open_questions",
}
EVIDENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "fingerprint", "stable_diff_id"],
    "properties": {key: {"type": "string"} for key in ("path", "fingerprint", "stable_diff_id")},
}
TARGET_COVERAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["customer_diff_id", "target_diff_ids", "coverage_status", "evidence_ref", "notes"],
    "properties": {key: {"type": "string"} for key in ("customer_diff_id", "target_diff_ids", "coverage_status", "evidence_ref", "notes")},
}
CONTEXT_CONTRACT_VERSION = "context-envelope/v1"
CONTEXT_ESTIMATOR_VERSION = "utf8-v1"
STRUCTURED_RESPONSE_RESERVE_TOKENS = 4096
STRUCTURED_RESPONSE_RESERVE_BYTES = 8192
MAX_RESPONSE_STRING = 72
MAX_RESPONSE_ITEMS = 2
MAX_RESPONSE_GROUPS = 4
MAX_RESPONSE_PAGE_ITEMS = 16
CONTEXT_SELECTION_POLICIES = {
    ("analyze-dif", "analyzer"): "dif-analysis/v1",
    ("form-mrq", "grouper"): "mrq-consolidation-leaf/v1",
    ("form-mrq", "coordinator"): "mrq-consolidation-hierarchy/v1",
    ("classify-batches", "classifier"): "mrq-classification-window/v1",
    ("research-target", "researcher"): "target-research/v1",
}
CONSOLIDATION_LINK_FIELDS = {
    "left_candidate_id", "right_candidate_id", "decision", "rationale",
}
CONSOLIDATION_REDUCED_FIELDS = {
    "semantic_key", "title", "business_meaning", "scope", "confidence",
    "rationale", "split_source_mrq_id",
}
CONSOLIDATION_NOISE_FIELDS = {"stable_diff_id", "decision", "rationale"}
ENVIRONMENT_PRESET_VERSION = "local-read-only/v2"
ENVIRONMENT_KEYS = (
    "PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)
EXEC_ARGUMENTS = ("exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only")
INSTRUCTION_CATALOG = {
    "1": (
        "You are a bounded research worker. Read only the repository paths named in allowed_paths "
        "and the supplied work unit. Do not edit files, do not include private reasoning, and return "
        "only the JSON object required by the output schema."
    ),
}
_UNRESOLVED_PROBE = object()


def instruction_fingerprint(version: str) -> str:
    try:
        text = INSTRUCTION_CATALOG[version]
    except KeyError as exc:
        raise ValueError(f"unsupported agent instruction version: {version}") from exc
    return "sha256:" + sha256(text.encode()).hexdigest()


def _bounded_string() -> dict[str, Any]:
    return {"type": "string", "maxLength": MAX_RESPONSE_STRING}


def proposal_schema(operation: str, work_unit: dict[str, Any]) -> dict[str, Any]:
    """Builds the fixed bounded response schema before context budgeting."""

    if operation == "mrq.consolidate":
        kind = work_unit.get("kind")
        if kind == "consolidation-link-page":
            item = {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(CONSOLIDATION_LINK_FIELDS),
                "properties": {
                    "left_candidate_id": _bounded_string(),
                    "right_candidate_id": _bounded_string(),
                    "decision": {
                        **_bounded_string(),
                        "enum": ["merge", "keep_separate"],
                    },
                    "rationale": _bounded_string(),
                },
            }
            return {
                "type": "object",
                "additionalProperties": False,
                "required": ["links"],
                "properties": {
                    "links": {
                        "type": "array",
                        "maxItems": MAX_RESPONSE_PAGE_ITEMS,
                        "items": item,
                    }
                },
            }
        if kind == "consolidation-reduce-pair":
            return {
                "type": "object",
                "additionalProperties": False,
                "required": ["group"],
                "properties": {
                    "group": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": sorted(CONSOLIDATION_REDUCED_FIELDS),
                        "properties": {
                            key: _bounded_string()
                            for key in sorted(CONSOLIDATION_REDUCED_FIELDS)
                        },
                    }
                },
            }
        if kind == "consolidation-noise-page":
            item = {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(CONSOLIDATION_NOISE_FIELDS),
                "properties": {
                    "stable_diff_id": _bounded_string(),
                    "decision": {
                        **_bounded_string(),
                        "enum": ["approve", "reject"],
                    },
                    "rationale": _bounded_string(),
                },
            }
            return {
                "type": "object",
                "additionalProperties": False,
                "required": ["decisions"],
                "properties": {
                    "decisions": {
                        "type": "array",
                        "maxItems": MAX_RESPONSE_PAGE_ITEMS,
                        "items": item,
                    }
                },
            }
    fields = CONSOLIDATION_GROUP_FIELDS if operation == "mrq.consolidate" else PROPOSAL_FIELDS[operation]
    properties = {
        key: {
            "type": "array",
            "maxItems": MAX_RESPONSE_ITEMS,
            "items": _bounded_string(),
        } if key in STRING_ARRAY_FIELDS
        else {
            "type": "array",
            "maxItems": MAX_RESPONSE_ITEMS,
            "items": {
                **TARGET_COVERAGE_SCHEMA,
                "properties": {
                    name: _bounded_string()
                    for name in TARGET_COVERAGE_SCHEMA["properties"]
                },
            },
        } if key == "target_coverage"
        else {
            "type": "array",
            "maxItems": MAX_RESPONSE_ITEMS,
            "items": {
                **EVIDENCE_SCHEMA,
                "properties": {
                    name: _bounded_string()
                    for name in EVIDENCE_SCHEMA["properties"]
                },
            },
            **({"minItems": 1} if key == "evidence" else {}),
        } if key in {"evidence", "target_evidence"}
        else {"type": "boolean"} if key == "linkage_proven"
        else {**_bounded_string(), "enum": ["meaning", "noise"]} if key == "classification"
        else _bounded_string()
        for key in sorted(fields)
    }
    item_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(fields),
        "properties": properties,
    }
    grouped = work_unit.get("kind") == "coordinate-groups" or operation in {
        "mrq.classify-batches",
        "mrq.consolidate",
    }
    if not grouped:
        return item_schema
    schema_properties = {
        "groups": {
            "type": "array",
            "maxItems": MAX_RESPONSE_GROUPS,
            "items": item_schema,
        }
    }
    required = ["groups"]
    if operation == "mrq.consolidate" and work_unit.get("kind") == "consolidation-coordinate":
        schema_properties["approved_noise"] = {
            "type": "array",
            "maxItems": MAX_RESPONSE_ITEMS,
            "items": _bounded_string(),
        }
        required.append("approved_noise")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": schema_properties,
    }


def _context_provenance(
    work_unit: dict[str, Any],
    manifest: dict[str, Any],
) -> list[dict[str, str]]:
    identifier = str(work_unit.get("id", ""))
    kind = str(work_unit.get("kind", "work-unit"))
    if not identifier:
        raise ValueError("agent context subject has no stable item key")
    rows = [{
        "item_key": f"{kind}:{identifier}",
        "item_kind": "subject",
        "selection_reason": "primary_subject",
        "origin_kind": "work_unit",
        "origin_ref": identifier,
        "fingerprint": "sha256:" + sha256(canonical_json(work_unit)).hexdigest(),
    }]
    ignored = {"id", "kind", "allowed_paths", "allowed_path_fingerprints"}
    for key in sorted(set(work_unit) - ignored):
        rows.append({
            "item_key": f"{kind}:{identifier}:fact:{key}",
            "item_kind": "fact",
            "selection_reason": "stage_policy",
            "origin_kind": "work_unit",
            "origin_ref": identifier,
            "fingerprint": "sha256:" + sha256(canonical_json(work_unit[key])).hexdigest(),
        })
    for item in manifest["paths"]:
        rows.append({
            "item_key": f"path:{item['path']}",
            "item_kind": "evidence",
            "selection_reason": "selected_path",
            "origin_kind": "repository_path",
            "origin_ref": item["path"],
            "fingerprint": item["fingerprint"],
        })
    return rows


def _render_prepared_input(
    profile: dict[str, Any],
    operation: str,
    provider_context: dict[str, Any],
    supplement: str,
) -> str:
    instruction_version = str(profile["instructions_version"])
    return (
        f"{INSTRUCTION_CATALOG[instruction_version]} "
        f"Operation: {operation}. Instruction version: {instruction_version}. "
        f"Context envelope: {canonical_json(provider_context).decode('utf-8')}. "
        f"Supplement: {supplement}"
    )


def prepare_context_envelope(
    repo: Path,
    profile: dict[str, Any],
    operation: str,
    phase_id: str,
    role_id: str,
    work_unit: dict[str, Any],
    supplement: str,
    execution_snapshot: dict[str, Any],
    context_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Freezes, budgets, and fingerprints one provider-bound context."""

    manifest = context_manifest or build_context_manifest(repo, work_unit)
    verify_context_manifest(repo, work_unit, manifest)
    try:
        policy_version = CONTEXT_SELECTION_POLICIES[(phase_id, role_id)]
    except KeyError as exc:
        raise ValueError("agent context selection policy is unsupported") from exc
    provenance = _context_provenance(work_unit, manifest)
    provider_context = {
        "contract_version": CONTEXT_CONTRACT_VERSION,
        "stage": phase_id,
        "role": role_id,
        "work_unit": {
            "id": str(work_unit.get("id", "")),
            "kind": str(work_unit.get("kind", "")),
        },
        "bindings": {
            **{
                key: str(value or "")
                for key, value in sorted(
                    (execution_snapshot.get("subject_bindings") or {}).items()
                )
            },
            "context_manifest_fingerprint": "sha256:"
            + sha256(canonical_json(manifest)).hexdigest(),
            "profile_capability_fingerprint": str(
                profile.get("capability_fingerprint", "")
            ),
            "instruction_fingerprint": instruction_fingerprint(
                str(profile["instructions_version"])
            ),
        },
        "payload": work_unit,
        "selection": {
            "policy_version": policy_version,
            "ordering": "canonical-json",
            "included_count": len(provenance),
            "candidate_count": len(provenance),
            "excluded": [],
            "budget_truncation_count": 0,
        },
        "provenance": provenance,
        "allowed_paths": manifest["paths"],
    }
    schema = proposal_schema(operation, work_unit)
    prompt = _render_prepared_input(
        profile, operation, provider_context, supplement
    )
    prepared_bytes = canonical_json({
        "adapter": "structured-output/v1",
        "prompt": prompt,
        "response_schema": schema,
    })
    from .source_search import dynamic_reserve_bytes
    search_policy = (execution_snapshot.get("source_search_policies") or {}).get(
        f"{phase_id}:{role_id}"
    )
    dynamic_bytes = dynamic_reserve_bytes(search_policy) if search_policy else 0
    if profile.get("context_estimator_version") != CONTEXT_ESTIMATOR_VERSION:
        raise ValueError("agent context estimator is unsupported")
    capacity = profile.get("input_context_tokens")
    if not isinstance(capacity, int) or capacity <= STRUCTURED_RESPONSE_RESERVE_TOKENS:
        raise ValueError("agent context capacity is unavailable")
    if not str(profile.get("capability_fingerprint", "")).startswith("sha256:"):
        raise ValueError("agent profile capability fingerprint is unavailable")
    allowance = (capacity - STRUCTURED_RESPONSE_RESERVE_TOKENS) * 2
    headroom = allowance - len(prepared_bytes) - dynamic_bytes
    if headroom < 0:
        raise ValueError("agent.context_capacity")
    budget = {
        "estimator_version": CONTEXT_ESTIMATOR_VERSION,
        "context_window_tokens": capacity,
        "structured_response_reserve_tokens": STRUCTURED_RESPONSE_RESERVE_TOKENS,
        "structured_response_reserve_bytes": STRUCTURED_RESPONSE_RESERVE_BYTES,
        "prepared_input_bytes": len(prepared_bytes),
        "dynamic_search_reserve_bytes": dynamic_bytes,
        "input_allowance_bytes": allowance,
        "headroom_bytes": headroom,
    }
    prepared_input_fingerprint = "sha256:" + sha256(prepared_bytes).hexdigest()
    envelope = {
        **provider_context,
        "budget": budget,
        "prepared_input_fingerprint": prepared_input_fingerprint,
    }
    envelope["envelope_fingerprint"] = "sha256:" + sha256(
        canonical_json(envelope)
    ).hexdigest()
    diagnostics = {
        "contract_version": CONTEXT_CONTRACT_VERSION,
        "selection_policy_version": policy_version,
        **budget,
        "included_count": len(provenance),
        "origin_counts": {
            origin: sum(row["origin_kind"] == origin for row in provenance)
            for origin in sorted({row["origin_kind"] for row in provenance})
        },
        "policy_exclusions": [],
        "budget_truncation_count": 0,
    }
    return {
        "envelope": envelope,
        "provider_context": provider_context,
        "prompt": prompt,
        "response_schema": schema,
        "provenance": provenance,
        "diagnostics": diagnostics,
    }


def _fingerprint_path(root: Path, path: Path) -> str:
    if path.is_file():
        return "sha256:" + sha256(path.read_bytes()).hexdigest()
    if path.is_dir():
        rows = []
        for item in sorted(path.rglob("*")):
            if not item.is_file():
                continue
            resolved = item.resolve(strict=True)
            if root not in resolved.parents:
                raise ValueError("agent context path escapes the repository")
            rows.append((item.relative_to(path).as_posix(), "sha256:" + sha256(resolved.read_bytes()).hexdigest()))
        return "sha256:" + sha256(canonical_json(rows)).hexdigest()
    raise ValueError("agent context path is not a file or directory")


def build_context_manifest(repo: Path, work_unit: dict[str, Any]) -> dict[str, Any]:
    root = repo.resolve()
    generation_id = str(work_unit.get("source_generation_id", ""))
    pointer = root / "research/active-source-generation.json"
    if not generation_id and pointer.is_file():
        generation_id = str(json.loads(pointer.read_text(encoding="utf-8"))["generation_id"])
    source_root = root / "sources/generations" / normalize_relative(generation_id) if generation_id else root
    allowed = work_unit.get("allowed_paths", [])
    if not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed):
        raise ValueError("agent context allowed_paths must be a list of repository paths")
    paths = []
    for raw in allowed:
        relative = normalize_relative(raw)
        candidates = (source_root / relative, source_root / "target_cf" / relative)
        selected = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
        path = selected.resolve(strict=True)
        if path != root and root not in path.parents:
            raise ValueError("agent context path escapes the repository")
        paths.append({"path": relative, "fingerprint": _fingerprint_path(root, path)})
    return {
        "schema_version": "1",
        "work_unit_id": str(work_unit.get("id", "")),
        "work_unit_fingerprint": "sha256:" + sha256(canonical_json(work_unit)).hexdigest(),
        "paths": paths,
    }


def verify_context_manifest(repo: Path, work_unit: dict[str, Any], manifest: dict[str, Any]) -> None:
    if build_context_manifest(repo, work_unit) != manifest:
        raise RuntimeError("agent context manifest is stale")


def _verify_executable(environment: dict[str, Any]) -> str:
    if set(environment) != {"preset", "preset_version", "executable", "executable_fingerprint", "inherited_environment_keys", "arguments"}:
        raise ValueError("agent execution environment does not match the closed preset")
    if environment["preset"] != "local-read-only" or environment["preset_version"] != ENVIRONMENT_PRESET_VERSION:
        raise ValueError("unsupported agent environment preset")
    if tuple(environment["arguments"]) != EXEC_ARGUMENTS or tuple(environment["inherited_environment_keys"]) != ENVIRONMENT_KEYS:
        raise ValueError("agent execution environment differs from the closed preset")
    executable = Path(str(environment["executable"])).resolve(strict=True)
    if not executable.is_file() or "sha256:" + sha256(executable.read_bytes()).hexdigest() != environment["executable_fingerprint"]:
        raise RuntimeError("codex executable changed after the execution snapshot was created")
    return str(executable)


def probe_codex_environment() -> dict[str, str] | None:
    """Возвращает текущую идентичность Codex одним запуском процесса."""

    executable = shutil.which("codex")
    if not executable:
        return None
    path = Path(executable).resolve()
    try:
        fingerprint = "sha256:" + sha256(path.read_bytes()).hexdigest()
        version = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if version.returncode:
        return None
    return {
        "executable": str(path),
        "executable_fingerprint": fingerprint,
        "codex_version": " ".join((version.stdout or version.stderr).split())[:200],
    }


def verify_execution_environment(
    snapshot: dict[str, Any],
    probe: dict[str, str] | None | object = _UNRESOLVED_PROBE,
) -> None:
    """Проверяет исполняемый файл и зафиксированную версию Codex."""

    executable = _verify_executable(snapshot["environment"])
    current = probe_codex_environment() if probe is _UNRESOLVED_PROBE else probe
    if current is None:
        raise RuntimeError("codex executable version is unavailable")
    if (
        current["executable"] != executable
        or current["executable_fingerprint"] != snapshot["environment"]["executable_fingerprint"]
        or current["codex_version"] != snapshot.get("codex_version")
    ):
        raise RuntimeError("codex version changed after the execution snapshot was created")


def codex_environment_available() -> bool:
    """Проверяет, что локальный Codex найден и действительно запускается."""

    return probe_codex_environment() is not None


def validate_execution_snapshot(repo: Path, snapshot: dict[str, Any]) -> None:
    """Проверяет, что сохранённый снимок всё ещё воспроизводим локально."""

    if snapshot.get("schema_version") not in {"1", "2"} or snapshot.get("environment", {}).get("preset") != "local-read-only":
        raise RuntimeError("agent execution snapshot is unsupported")
    if snapshot.get("schema_version") == "2" and (
        snapshot.get("context_contract_version") != CONTEXT_CONTRACT_VERSION
        or snapshot.get("context_estimator_version") != CONTEXT_ESTIMATOR_VERSION
    ):
        raise RuntimeError("agent context contract in the execution snapshot is unsupported")
    verify_execution_environment(snapshot)
    instructions = snapshot.get("instructions")
    if not isinstance(instructions, dict):
        raise RuntimeError("agent execution snapshot has no instruction catalog")
    expected_instructions = {
        version: instruction_fingerprint(version)
        for version in {
            str(profile.get("instructions_version", ""))
            for profile in snapshot.get("profiles", {}).values()
            if isinstance(profile, dict)
        }
    }
    if instructions != expected_instructions:
        raise RuntimeError("agent instruction changed after the execution snapshot was created")
    from .source_search import POLICY_VERSION
    policies = snapshot.get("source_search_policies", {})
    if not isinstance(policies, dict) or any(
        not isinstance(policy, dict)
        or policy.get("schema_version") != POLICY_VERSION
        or not str(policy.get("policy_fingerprint", "")).startswith("sha256:")
        for policy in policies.values()
    ):
        raise RuntimeError("agent source-search policy in the execution snapshot is unsupported")
    work_unit = snapshot.get("work_unit")
    manifest = snapshot.get("context_manifest")
    if not isinstance(work_unit, dict) or not isinstance(manifest, dict):
        raise RuntimeError("agent execution snapshot has no context manifest")
    verify_context_manifest(repo, work_unit, manifest)


def resolve_execution_snapshot(
    repo: Path,
    run_id: str,
    operation: str,
    step: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
    work_unit: dict[str, Any],
) -> dict[str, Any]:
    """Разрешает только безопасную серверную конфигурацию до захвата аренды."""

    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("codex executable is unavailable for the local agent profile")
    executable_path = Path(executable).resolve()
    try:
        executable_fingerprint = "sha256:" + sha256(executable_path.read_bytes()).hexdigest()
        version = subprocess.run([str(executable_path), "--version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("codex executable cannot be fingerprinted") from exc
    if version.returncode:
        raise RuntimeError("codex executable version is unavailable")
    phases = step.get("agent_phases", [])
    try:
        from .stage_recompute import active_state
        pointers, workflow_fingerprint = active_state(repo)
    except (OSError, ValueError, KeyError):
        # Узкие модульные вызовы без проектного контракта всё равно получают
        # закрытый снимок; рабочий API всегда проходит validate_project_contract.
        pointers, workflow_fingerprint = {}, "sha256:" + sha256(canonical_json(step)).hexdigest()
    safe_profiles: dict[str, dict[str, Any]] = {}
    source_search_policies: dict[str, dict[str, Any]] = {}
    from .source_search import resolve_policy
    for phase in phases:
        for role in phase["roles"]:
            name = role["agent_profile"]
            profile = profiles.get(name)
            if profile is None:
                raise RuntimeError(f"user-scope agent profile is unavailable: {name}")
            if profile.get("environment_preset") != "local-read-only":
                raise ValueError("unsupported agent environment preset")
            safe_profiles[name] = dict(profile)
            policy = resolve_policy(repo, profile, str(role["role_id"]), work_unit)
            if policy is not None:
                source_search_policies[f'{phase["phase_id"]}:{role["role_id"]}'] = policy
    snapshot = {
        "schema_version": "2",
        "context_contract_version": CONTEXT_CONTRACT_VERSION,
        "context_estimator_version": CONTEXT_ESTIMATOR_VERSION,
        "run_id": run_id,
        "operation": operation,
        "operation_version": step["operation_version"],
        "workflow_fingerprint": workflow_fingerprint,
        "timeout_seconds": step["timeout_seconds"],
        "agent_phases": phases,
        "profiles": safe_profiles,
        "source_search_policies": source_search_policies,
        "instructions": {
            version: instruction_fingerprint(version)
            for version in sorted({str(profile["instructions_version"]) for profile in safe_profiles.values()})
        },
        "environment": {
            "preset": "local-read-only",
            "preset_version": ENVIRONMENT_PRESET_VERSION,
            "executable": str(executable_path),
            "executable_fingerprint": executable_fingerprint,
            "inherited_environment_keys": list(ENVIRONMENT_KEYS),
            "arguments": list(EXEC_ARGUMENTS),
        },
        "codex_version": " ".join((version.stdout or version.stderr).split())[:200],
        "application_version": "one-c-autoresearch/0.2",
        "subject_bindings": {
            "source_generation_id": str((pointers.get("source") or {}).get("generation_id", "")),
            "diff_generation_id": str((pointers.get("diff") or {}).get("generation_id", "")),
            "canonical_generation_id": str(
                json.loads(
                    (repo / "research/active-consolidation-generation.json").read_text(encoding="utf-8")
                ).get("mrq_generation_id", "")
            ),
        },
        "policy_source": "current-policy",
        "work_unit": work_unit,
        "context_manifest": build_context_manifest(repo, work_unit),
    }
    reject_secrets(snapshot, "execution snapshot")
    return snapshot


def validate_proposal(operation: str, payload: dict[str, Any], work_unit: dict[str, Any]) -> dict[str, Any]:
    if operation == "mrq.consolidate":
        kind = work_unit.get("kind")
        if kind == "consolidation-link-page":
            links = payload.get("links")
            if (
                set(payload) != {"links"}
                or not isinstance(links, list)
                or any(
                    not isinstance(link, dict)
                    or set(link) != CONSOLIDATION_LINK_FIELDS
                    or link.get("decision") not in {"merge", "keep_separate"}
                    for link in links
                )
            ):
                raise ValueError("consolidation link response does not match the fixed schema")
            reject_secrets(payload, "agent proposal")
            return payload
        if kind == "consolidation-reduce-pair":
            group = payload.get("group")
            if (
                set(payload) != {"group"}
                or not isinstance(group, dict)
                or set(group) != CONSOLIDATION_REDUCED_FIELDS
            ):
                raise ValueError("consolidation reduction response does not match the fixed schema")
            reject_secrets(payload, "agent proposal")
            return payload
        if kind == "consolidation-noise-page":
            decisions = payload.get("decisions")
            if (
                set(payload) != {"decisions"}
                or not isinstance(decisions, list)
                or any(
                    not isinstance(decision, dict)
                    or set(decision) != CONSOLIDATION_NOISE_FIELDS
                    or decision.get("decision") not in {"approve", "reject"}
                    for decision in decisions
                )
            ):
                raise ValueError("consolidation noise response does not match the fixed schema")
            reject_secrets(payload, "agent proposal")
            return payload
        coordinator = work_unit.get("kind") == "consolidation-coordinate"
        expected = {"groups", "approved_noise"} if coordinator else {"groups"}
        groups = payload.get("groups")
        if (
            set(payload) != expected
            or not isinstance(groups, list)
            or any(not isinstance(group, dict) or set(group) != CONSOLIDATION_GROUP_FIELDS for group in groups)
            or (coordinator and (
                not isinstance(payload["approved_noise"], list)
                or any(not isinstance(identifier, str) for identifier in payload["approved_noise"])
                or payload["approved_noise"] != sorted(set(payload["approved_noise"]))
            ))
        ):
            raise ValueError("consolidation proposal does not match the fixed response schema")
        reject_secrets(payload, "agent proposal")
        return payload
    if operation == "mrq.classify-batches":
        fields = PROPOSAL_FIELDS[operation]
        groups = payload.get("groups")
        if set(payload) != {"groups"} or not isinstance(groups, list) or any(not isinstance(group, dict) or set(group) != fields for group in groups):
            raise ValueError("classifier proposal does not match the fixed response schema")
        reject_secrets(payload, "agent proposal")
        return payload
    if work_unit.get("kind") == "coordinate-groups":
        if set(payload) != {"groups"} or not isinstance(payload["groups"], list):
            raise ValueError("coordinator proposal does not match the fixed response schema")
        fields = PROPOSAL_FIELDS["mrq.discover-next"]
        if any(not isinstance(group, dict) or set(group) != fields for group in payload["groups"]):
            raise ValueError("coordinator group does not match the fixed response schema")
        reject_secrets(payload, "agent proposal")
        return payload
    required = PROPOSAL_FIELDS.get(operation)
    if required is None or set(payload) != required:
        raise ValueError("agent proposal does not match the fixed operation schema")
    work_id = str(work_unit.get("id", ""))
    if operation == "dif.classify-next" and payload["classification"] not in {"meaning", "noise"}:
        raise ValueError("DIF classification must be meaning or noise")
    if operation == "mrq.discover-next" and work_id not in payload["stable_diff_ids"]:
        raise ValueError("discovery proposal does not own its selected DIF work unit")
    if operation == "mrq.decide-next" and payload["mrq_id"] != work_id:
        raise ValueError("decision proposal targets a different MRQ work unit")
    reject_secrets(payload, "agent proposal")
    return payload


def execute(
    repo: Path,
    proposal_dir: Path,
    profile: dict[str, Any],
    operation: str,
    work_unit: dict[str, Any],
    supplement: str,
    timeout_seconds: int,
    cancelled: callable,
    execution_snapshot: dict[str, Any] | None = None,
    context_manifest: dict[str, Any] | None = None,
    prepared_context: dict[str, Any] | None = None,
    invocation_id: str = "",
    source_search_capability: str = "",
    operational_state_root: Path | None = None,
) -> dict[str, Any]:
    if execution_snapshot is None:
        execution_snapshot = resolve_execution_snapshot(repo, "standalone", operation, {"operation_version": "2", "timeout_seconds": timeout_seconds, "agent_phases": [{"roles": [{"agent_profile": "selected"}]}]}, {"selected": profile}, work_unit)
    if execution_snapshot.get("operation") != operation:
        raise RuntimeError("agent invocation differs from its execution snapshot")
    if profile not in execution_snapshot.get("profiles", {}).values():
        raise RuntimeError("agent profile differs from its execution snapshot")
    validate_execution_snapshot(repo, execution_snapshot)
    manifest = context_manifest or execution_snapshot["context_manifest"]
    verify_context_manifest(repo, work_unit, manifest)
    executable = str(execution_snapshot["environment"]["executable"])
    proposal_dir.mkdir(parents=True, exist_ok=False)
    schema = proposal_schema(operation, work_unit)
    schema_path = proposal_dir / "output-schema.json"
    output_path = proposal_dir / "proposal.json"
    atomic_json(schema_path, schema)
    if prepared_context is None:
        phase_id, role_id = (
            ("research-target", "researcher")
            if operation == "mrq.decide-next"
            else ("classify-batches", "classifier")
            if operation == "mrq.classify-batches"
            else ("form-mrq", "coordinator")
            if operation == "mrq.consolidate"
            and work_unit.get("kind") == "consolidation-coordinate"
            else ("form-mrq", "grouper")
            if operation == "mrq.consolidate"
            else ("analyze-dif", "analyzer")
        )
        prepared_context = prepare_context_envelope(
            repo,
            profile,
            operation,
            phase_id,
            role_id,
            work_unit,
            supplement,
            execution_snapshot,
            manifest,
        )
    if prepared_context["response_schema"] != schema:
        raise RuntimeError("agent response schema changed after context preflight")
    prompt = str(prepared_context["prompt"])
    if profile.get("environment_preset") != "local-read-only":
        raise ValueError("unsupported agent environment preset")
    command = [executable, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only", "--color", "never", "--cd", str(repo), "--model", profile["model"], "--config", f'model_reasoning_effort="{profile["reasoning_effort"]}"', "--output-schema", str(schema_path), "--output-last-message", str(output_path), "-"]
    environment = {key: value for key, value in os.environ.items() if key in ENVIRONMENT_KEYS}
    if source_search_capability:
        if not invocation_id:
            raise RuntimeError("source-search invocation identity is unavailable")
        bridge_environment = {
            "ONE_C_AUTORESEARCH_REPO": str(repo),
            "ONE_C_AUTORESEARCH_INVOCATION_ID": invocation_id,
            "ONE_C_AUTORESEARCH_SOURCE_SEARCH_CAPABILITY": source_search_capability,
        }
        if operational_state_root is not None:
            bridge_environment["ONE_C_AUTORESEARCH_STATE_ROOT"] = str(operational_state_root)
        environment.update(bridge_environment)
        command[-1:-1] = [
            "--config", f'mcp_servers.source_search.command={json.dumps(sys.executable)}',
            "--config", 'mcp_servers.source_search.args=["-m","one_c_autoresearch.source_search_bridge"]',
            "--config", "mcp_servers.source_search.enabled=true",
        ]
    result = _run_command(subprocess.run, command, cancelled=cancelled, input=prompt, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=timeout_seconds, env=environment)
    if result.returncode:
        from .events import redact
        detail = str(redact((result.stderr or result.stdout or "").strip()))[-1000:]
        raise RuntimeError(f"local agent failed with exit {result.returncode}: {detail}")
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("local agent did not return a valid proposal") from exc
    validate_execution_snapshot(repo, execution_snapshot)
    verify_context_manifest(repo, work_unit, manifest)
    if len(canonical_json(payload)) > STRUCTURED_RESPONSE_RESERVE_BYTES:
        raise ValueError("agent structured response exceeds reserved capacity")
    return validate_proposal(operation, payload, work_unit)
