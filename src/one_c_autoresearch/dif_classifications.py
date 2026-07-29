from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .contracts import atomic_json, canonical_json, confined, repository_lock, sha256
from .diffs import INVENTORY_HEADER, _read_csv, validate_active as validate_diff


SCHEMA_VERSION = "1"
ROW_KEYS = {
    "schema_version", "stable_diff_id", "classification", "semantic_hints",
    "evidence", "rationale", "result_fingerprint", "evidence_fingerprint",
    "result_schema_fingerprint", "profile_fingerprint",
    "instruction_fingerprint", "context_fingerprint",
}
CLASSIFICATIONS = {"meaning", "noise_candidate"}
POINTER = "research/active-dif-classification-generation.json"


def _fingerprint(value: Any) -> str:
    return "sha256:" + sha256(canonical_json(value))


def _whole_component(row: dict[str, Any]) -> bool:
    hints = row.get("semantic_hints", [])
    return (
        row.get("classification") == "meaning"
        and any(str(item).startswith("component:") for item in hints)
        and any(str(item).startswith("whole-component:") for item in hints)
    )


def _bindings(repo: Path) -> dict[str, str]:
    source = json.loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    diff = validate_diff(repo)
    return {
        "source_generation_id": str(source["generation_id"]),
        "source_fingerprint": _fingerprint(source),
        "diff_generation_id": str(diff["generation_id"]),
        "diff_fingerprint": _fingerprint(diff),
    }


def inventory(repo: Path) -> list[dict[str, str]]:
    pointer = validate_diff(repo)
    rows = _read_csv(
        repo / "analysis/indexes/generations" / pointer["generation_id"] / "diff-inventory.csv",
        INVENTORY_HEADER,
    )
    return [row for row in rows if row["after_role"] == "target_cf"]


def physical_evidence_fingerprints(repo: Path) -> dict[str, str]:
    return {
        row["stable_diff_id"]: _fingerprint({
            "path": row["path"],
            "kind": row["object_kind"],
            "before": row["before_fingerprint"],
            "after": row["after_fingerprint"],
        })
        for row in inventory(repo)
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            for row in rows:
                stream.write(canonical_json(row) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _validate_rows(rows: list[dict[str, Any]], allowed_ids: set[str]) -> None:
    ids: set[str] = set()
    for row in rows:
        if set(row) != ROW_KEYS or row["schema_version"] != SCHEMA_VERSION:
            raise ValueError("invalid DIF classification row schema")
        identifier = row["stable_diff_id"]
        if identifier not in allowed_ids or identifier in ids:
            raise ValueError("unknown or duplicate classified DIF")
        if row["classification"] not in CLASSIFICATIONS:
            raise ValueError("invalid DIF classification")
        if not isinstance(row["semantic_hints"], list) or row["semantic_hints"] != sorted(set(row["semantic_hints"])):
            raise ValueError("DIF semantic hints must be unique and sorted")
        if not isinstance(row["evidence"], list) or not row["evidence"]:
            raise ValueError("DIF classification requires evidence")
        if any(
            not isinstance(item, dict)
            or not str(item.get("path", "")).strip()
            or not str(item.get("fingerprint", "")).startswith("sha256:")
            for item in row["evidence"]
        ):
            raise ValueError("DIF classification evidence requires path and fingerprint")
        if not str(row["rationale"]).strip():
            raise ValueError("DIF classification rationale is required")
        for key in (
            "result_fingerprint", "evidence_fingerprint", "result_schema_fingerprint",
            "profile_fingerprint", "instruction_fingerprint", "context_fingerprint",
        ):
            if not isinstance(row[key], str) or not row[key].startswith("sha256:") or len(row[key]) != 71:
                raise ValueError(f"invalid DIF classification {key}")
        if row["result_fingerprint"] != _fingerprint({key: row[key] for key in sorted(ROW_KEYS - {"result_fingerprint"})}):
            raise ValueError("invalid DIF classification result fingerprint")
        ids.add(identifier)


def make_row(
    stable_diff_id: str,
    result: dict[str, Any],
    *,
    evidence_fingerprint: str,
    result_schema_fingerprint: str,
    profile_fingerprint: str,
    instruction_fingerprint: str,
    context_fingerprint: str,
) -> dict[str, Any]:
    kind = result.get("kind")
    row = {
        "schema_version": SCHEMA_VERSION,
        "stable_diff_id": stable_diff_id,
        "classification": "noise_candidate" if kind == "noise" else kind,
        "semantic_hints": sorted(set(result.get("semantic_hints", []))),
        "evidence": result.get("evidence", []),
        "rationale": str(result.get("rationale", "")),
        "evidence_fingerprint": evidence_fingerprint,
        "result_schema_fingerprint": result_schema_fingerprint,
        "profile_fingerprint": profile_fingerprint,
        "instruction_fingerprint": instruction_fingerprint,
        "context_fingerprint": context_fingerprint,
    }
    row["result_fingerprint"] = _fingerprint(row)
    _validate_rows([row], {stable_diff_id})
    return row


def _generation_id(manifest_preimage: dict[str, Any]) -> str:
    return sha256(canonical_json(manifest_preimage))


def validate_generation(repo: Path, pointer: dict[str, Any], *, current: bool = True) -> dict[str, Any]:
    required = {
        "schema_version", "generation_id", "source_generation_id", "source_fingerprint",
        "diff_generation_id", "diff_fingerprint", "files", "row_counts",
    }
    if set(pointer) != required or pointer["schema_version"] != SCHEMA_VERSION:
        raise ValueError("invalid DIF classification pointer")
    if current and {key: pointer[key] for key in _bindings(repo)} != _bindings(repo):
        raise ValueError("stale DIF classification bindings")
    root = confined(repo / "analysis/dif-classifications/generations", pointer["generation_id"])
    payload = root / "classifications.jsonl"
    manifest_path = root / "manifest.json"
    if not payload.is_file() or not manifest_path.is_file():
        raise ValueError("incomplete DIF classification generation")
    rows = [json.loads(line) for line in payload.read_text(encoding="utf-8").splitlines()]
    if payload.read_bytes() != b"".join(canonical_json(row) + b"\n" for row in rows):
        raise ValueError("non-canonical DIF classification payload")
    if rows != sorted(rows, key=lambda row: row["stable_diff_id"]):
        raise ValueError("unsorted DIF classification payload")
    allowed = {row["stable_diff_id"] for row in inventory(repo)} if current else {row["stable_diff_id"] for row in rows}
    _validate_rows(rows, allowed)
    files = {"classifications.jsonl": sha256(payload.read_bytes())}
    if pointer["files"] != files or pointer["row_counts"] != {"classifications.jsonl": len(rows)}:
        raise ValueError("DIF classification pointer metadata mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    preimage = {key: pointer[key] for key in required - {"generation_id"}}
    if manifest != {**preimage, "generation_id": pointer["generation_id"]}:
        raise ValueError("DIF classification manifest mismatch")
    if pointer["generation_id"] != _generation_id(preimage):
        raise ValueError("DIF classification generation identity mismatch")
    return {"pointer": pointer, "rows": rows}


def load_active(repo: Path) -> dict[str, Any]:
    path = repo / POINTER
    if not path.is_file():
        raise ValueError("active DIF classification generation is missing")
    return validate_generation(repo, json.loads(path.read_text(encoding="utf-8")))


def coverage(repo: Path) -> dict[str, int | bool]:
    total_ids = {row["stable_diff_id"] for row in inventory(repo)}
    try:
        rows = load_active(repo)["rows"]
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        rows = []
    current_rows = _current_rows(repo, rows)
    classified = set(current_rows)
    return {
        "total": len(total_ids),
        "classified": len(classified & total_ids),
        "remaining": len(total_ids - classified),
        "meaning": sum(row["classification"] == "meaning" for row in current_rows.values()),
        "noise_candidate": sum(row["classification"] == "noise_candidate" for row in current_rows.values()),
        "all_dif_classified": classified == total_ids,
    }


def publish_window(
    repo: Path,
    rows: Iterable[dict[str, Any]],
    *,
    expected_generation_id: str | None,
    reset: bool = False,
) -> dict[str, Any]:
    incoming = list(rows)
    with repository_lock(repo):
        bindings = _bindings(repo)
        path = repo / POINTER
        active = validate_generation(repo, json.loads(path.read_text(encoding="utf-8")), current=not reset) if path.is_file() else {"pointer": {}, "rows": []}
        actual = active["pointer"].get("generation_id")
        if actual != expected_generation_id:
            raise RuntimeError("stale DIF classification generation")
        merged = {} if reset else {row["stable_diff_id"]: row for row in active["rows"]}
        if set(merged) & {row["stable_diff_id"] for row in incoming}:
            for row in incoming:
                prior = merged.get(row["stable_diff_id"])
                if prior is not None and prior != row and not (_whole_component(prior) and _whole_component(row)):
                    raise ValueError("classified DIF cannot be replaced")
        merged.update((row["stable_diff_id"], row) for row in incoming)
        accumulated = sorted(merged.values(), key=lambda row: row["stable_diff_id"])
        _validate_rows(accumulated, {row["stable_diff_id"] for row in inventory(repo)})
        staging = repo / "analysis/dif-classifications/.staging"
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=staging) as temporary:
            payload = Path(temporary) / "classifications.jsonl"
            _write_jsonl(payload, accumulated)
            files = {"classifications.jsonl": sha256(payload.read_bytes())}
            preimage = {
                "schema_version": SCHEMA_VERSION,
                **bindings,
                "files": files,
                "row_counts": {"classifications.jsonl": len(accumulated)},
            }
            generation_id = _generation_id(preimage)
            pointer = {**preimage, "generation_id": generation_id}
            root = repo / "analysis/dif-classifications/generations" / generation_id
            if not root.exists():
                root.parent.mkdir(parents=True, exist_ok=True)
                _write_jsonl(Path(temporary) / "classifications.jsonl", accumulated)
                atomic_json(Path(temporary) / "manifest.json", pointer)
                os.replace(temporary, root)
            validate_generation(repo, pointer)
            atomic_json(path, pointer)
        return pointer


def publish_empty(repo: Path, *, expected_generation_id: str | None = None, reset: bool = False) -> dict[str, Any]:
    return publish_window(repo, [], expected_generation_id=expected_generation_id, reset=reset)


def ensure_current(repo: Path) -> dict[str, Any]:
    path = repo / POINTER
    if not path.is_file():
        return publish_empty(repo)
    try:
        return load_active(repo)["pointer"]
    except ValueError as exc:
        if str(exc) != "stale DIF classification bindings":
            raise
    pointer = json.loads(path.read_text(encoding="utf-8"))
    return publish_empty(repo, expected_generation_id=pointer.get("generation_id"), reset=True)


def remaining_ids(repo: Path, *, limit: int = 32) -> list[str]:
    active = load_active(repo)["rows"] if (repo / POINTER).is_file() else []
    classified = _current_rows(repo, active)
    return sorted({row["stable_diff_id"] for row in inventory(repo)} - set(classified))[:limit]


def _current_rows(repo: Path, rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    classified = {row["stable_diff_id"]: row for row in rows}
    source_path = repo / "research/active-source-generation.json"
    if not source_path.is_file():
        return classified
    from .component_groups import deterministic_results
    source = json.loads(source_path.read_text(encoding="utf-8"))
    expected = deterministic_results(repo, classified) if source.get("routing_manifest_path") else {}
    for identifier, value in expected.items():
        row = make_row(identifier, value["result"], **{
            key: value[key]
            for key in (
                "evidence_fingerprint", "result_schema_fingerprint", "profile_fingerprint",
                "instruction_fingerprint", "context_fingerprint",
            )
        })
        if classified.get(identifier) != row:
            classified.pop(identifier, None)
    return classified


def reusable_rows(
    prior_rows: Iterable[dict[str, Any]],
    current_fingerprints: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    keys = {
        "evidence_fingerprint", "result_schema_fingerprint", "profile_fingerprint",
        "instruction_fingerprint", "context_fingerprint",
    }
    return [
        row for row in prior_rows
        if row["stable_diff_id"] in current_fingerprints
        and all(row[key] == current_fingerprints[row["stable_diff_id"]].get(key) for key in keys)
    ]
