from __future__ import annotations

import json
import os
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any

from .contracts import atomic_json, canonical_json, normalize_relative, reject_secrets
from .sources import _run_command


PROPOSAL_FIELDS = {
    "mrq.discover-next": {"semantic_key", "title", "stable_diff_ids", "supporting_diff_ids", "evidence", "business_meaning", "scope", "confidence", "rationale"},
    "mrq.classify-batches": {"mrq_ids", "basis", "linkage_proven"},
    "mrq.decide-next": {"mrq_id", "decision", "target_evidence", "target_coverage", "residual_gap", "target_solution", "rationale", "acceptance_criteria", "risk", "open_questions"},
}
ENVIRONMENT_PRESET_VERSION = "local-read-only/v1"
ENVIRONMENT_KEYS = ("PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_DATA_HOME")
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
    allowed = work_unit.get("allowed_paths", [])
    if not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed):
        raise ValueError("agent context allowed_paths must be a list of repository paths")
    paths = []
    for raw in allowed:
        relative = normalize_relative(raw)
        path = (root / relative).resolve(strict=True)
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

    if snapshot.get("schema_version") != "1" or snapshot.get("environment", {}).get("preset") != "local-read-only":
        raise RuntimeError("agent execution snapshot is unsupported")
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
    for phase in phases:
        for role in phase["roles"]:
            name = role["agent_profile"]
            profile = profiles.get(name)
            if profile is None:
                raise RuntimeError(f"user-scope agent profile is unavailable: {name}")
            if profile.get("environment_preset") != "local-read-only":
                raise ValueError("unsupported agent environment preset")
            safe_profiles[name] = dict(profile)
    snapshot = {
        "schema_version": "1",
        "run_id": run_id,
        "operation": operation,
        "operation_version": step["operation_version"],
        "workflow_fingerprint": workflow_fingerprint,
        "timeout_seconds": step["timeout_seconds"],
        "agent_phases": phases,
        "profiles": safe_profiles,
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
            "canonical_generation_id": str((pointers.get("mrq") or {}).get("canonical_generation_id", "")),
        },
        "policy_source": "current-policy",
        "work_unit": work_unit,
        "context_manifest": build_context_manifest(repo, work_unit),
    }
    reject_secrets(snapshot, "execution snapshot")
    return snapshot


def validate_proposal(operation: str, payload: dict[str, Any], work_unit: dict[str, Any]) -> dict[str, Any]:
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
    fields = PROPOSAL_FIELDS[operation]
    item_schema = {"type": "object", "additionalProperties": False, "required": sorted(fields), "properties": {key: {} for key in sorted(fields)}}
    grouped = work_unit.get("kind") == "coordinate-groups" or operation == "mrq.classify-batches"
    schema = {"type": "object", "additionalProperties": False, "required": ["groups"], "properties": {"groups": {"type": "array", "items": item_schema}}} if grouped else item_schema
    schema_path = proposal_dir / "output-schema.json"
    output_path = proposal_dir / "proposal.json"
    atomic_json(schema_path, schema)
    instruction_version = str(profile["instructions_version"])
    prompt = (
        f"{INSTRUCTION_CATALOG[instruction_version]} "
        f"Operation: {operation}. Instruction version: {instruction_version}. "
        f"Work unit: {canonical_json(work_unit).decode('utf-8')}. Supplement: {supplement}"
    )
    if profile.get("environment_preset") != "local-read-only":
        raise ValueError("unsupported agent environment preset")
    command = [executable, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only", "--color", "never", "--cd", str(repo), "--model", profile["model"], "--config", f'model_reasoning_effort="{profile["reasoning_effort"]}"', "--output-schema", str(schema_path), "--output-last-message", str(output_path), "-"]
    environment = {key: value for key, value in os.environ.items() if key in ENVIRONMENT_KEYS}
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
    return validate_proposal(operation, payload, work_unit)
