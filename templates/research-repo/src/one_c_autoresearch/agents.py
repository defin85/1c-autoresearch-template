from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .contracts import atomic_json, canonical_json, reject_secrets
from .sources import _run_command


PROPOSAL_FIELDS = {
    "mrq.discover-next": {"semantic_key", "title", "stable_diff_ids", "supporting_diff_ids", "evidence", "business_meaning", "scope", "confidence", "rationale"},
    "mrq.decide-next": {"mrq_id", "decision", "target_evidence", "target_coverage", "residual_gap", "target_solution", "rationale", "acceptance_criteria", "risk", "open_questions"},
}


def validate_proposal(operation: str, payload: dict[str, Any], work_unit: dict[str, Any]) -> dict[str, Any]:
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


def execute(repo: Path, proposal_dir: Path, profile: dict[str, Any], operation: str, work_unit: dict[str, Any], supplement: str, timeout_seconds: int, cancelled: callable) -> dict[str, Any]:
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("codex executable is unavailable for the local agent profile")
    proposal_dir.mkdir(parents=True, exist_ok=False)
    fields = PROPOSAL_FIELDS[operation]
    schema = {"type": "object", "additionalProperties": False, "required": sorted(fields), "properties": {key: {} for key in sorted(fields)}}
    schema_path = proposal_dir / "output-schema.json"
    output_path = proposal_dir / "proposal.json"
    atomic_json(schema_path, schema)
    prompt = (
        "You are a bounded research worker. Read only the repository paths named in allowed_paths and the supplied work unit. "
        "Do not edit files, do not include private reasoning, and return only the JSON object required by the output schema. "
        f"Operation: {operation}. Instruction version: {profile['instructions_version']}. "
        f"Work unit: {canonical_json(work_unit).decode('utf-8')}. Supplement: {supplement}"
    )
    command = [executable, "exec", "--ephemeral", "--ignore-user-config", "--color", "never", "--sandbox", "read-only", "--cd", str(repo), "--model", profile["model"], "--config", f'model_reasoning_effort="{profile["reasoning_effort"]}"', "--output-schema", str(schema_path), "--output-last-message", str(output_path), "-"]
    environment = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}}
    result = _run_command(subprocess.run, command, cancelled=cancelled, input=prompt, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=timeout_seconds, env=environment)
    if result.returncode:
        from .events import redact
        detail = str(redact((result.stderr or result.stdout or "").strip()))[-1000:]
        raise RuntimeError(f"local agent failed with exit {result.returncode}: {detail}")
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("local agent did not return a valid proposal") from exc
    return validate_proposal(operation, payload, work_unit)
