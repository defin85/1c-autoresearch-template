from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict

from .contracts import JsonValue, atomic_json, canonical_json, confined, parse_json_object, repository_lock, sha256
from .diffs import INVENTORY_HEADER, read_csv, validate_active as validate_diff


SCHEMA_VERSION = "1"
ROW_KEYS = {
    "schema_version", "stable_diff_id", "classification", "semantic_hints",
    "evidence", "rationale", "result_fingerprint", "evidence_fingerprint",
    "result_schema_fingerprint", "profile_fingerprint",
    "instruction_fingerprint", "context_fingerprint",
}
CLASSIFICATIONS = {"meaning", "noise_candidate"}
POINTER = "research/active-dif-classification-generation.json"


class Evidence(TypedDict):
    path: str
    fingerprint: str


class ClassificationRow(TypedDict):
    schema_version: str
    stable_diff_id: str
    classification: str
    semantic_hints: list[str]
    evidence: list[Evidence]
    rationale: str
    result_fingerprint: str
    evidence_fingerprint: str
    result_schema_fingerprint: str
    profile_fingerprint: str
    instruction_fingerprint: str
    context_fingerprint: str


class GenerationPointer(TypedDict):
    schema_version: str
    generation_id: str
    source_generation_id: str
    source_fingerprint: str
    diff_generation_id: str
    diff_fingerprint: str
    files: dict[str, str]
    row_counts: dict[str, int]


class Generation(TypedDict):
    pointer: GenerationPointer
    rows: list[ClassificationRow]


def _string(value: JsonValue, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid {field}")
    return value


def _strings(value: JsonValue, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"invalid {field}")
    return [item for item in value if isinstance(item, str)]


def _evidence(value: JsonValue) -> list[Evidence]:
    if not isinstance(value, list):
        raise ValueError("invalid evidence")
    result: list[Evidence] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid evidence")
        result.append({
            "path": _string(item.get("path"), "evidence path"),
            "fingerprint": _string(item.get("fingerprint"), "evidence fingerprint"),
        })
    return result


def _classification_row(value: dict[str, JsonValue]) -> ClassificationRow:
    return {
        "schema_version": _string(value.get("schema_version"), "schema version"),
        "stable_diff_id": _string(value.get("stable_diff_id"), "stable DIF ID"),
        "classification": _string(value.get("classification"), "classification"),
        "semantic_hints": _strings(value.get("semantic_hints"), "semantic hints"),
        "evidence": _evidence(value.get("evidence")),
        "rationale": _string(value.get("rationale"), "rationale"),
        "result_fingerprint": _string(value.get("result_fingerprint"), "result fingerprint"),
        "evidence_fingerprint": _string(value.get("evidence_fingerprint"), "evidence fingerprint"),
        "result_schema_fingerprint": _string(value.get("result_schema_fingerprint"), "result schema fingerprint"),
        "profile_fingerprint": _string(value.get("profile_fingerprint"), "profile fingerprint"),
        "instruction_fingerprint": _string(value.get("instruction_fingerprint"), "instruction fingerprint"),
        "context_fingerprint": _string(value.get("context_fingerprint"), "context fingerprint"),
    }


def _generation_pointer(value: dict[str, JsonValue]) -> GenerationPointer:
    files = value.get("files")
    row_counts = value.get("row_counts")
    if not isinstance(files, dict) or not all(isinstance(item, str) for item in files.values()):
        raise ValueError("invalid generation files")
    if not isinstance(row_counts, dict) or not all(isinstance(item, int) and not isinstance(item, bool) for item in row_counts.values()):
        raise ValueError("invalid generation row counts")
    return {
        "schema_version": _string(value.get("schema_version"), "schema version"),
        "generation_id": _string(value.get("generation_id"), "generation ID"),
        "source_generation_id": _string(value.get("source_generation_id"), "source generation ID"),
        "source_fingerprint": _string(value.get("source_fingerprint"), "source fingerprint"),
        "diff_generation_id": _string(value.get("diff_generation_id"), "diff generation ID"),
        "diff_fingerprint": _string(value.get("diff_fingerprint"), "diff fingerprint"),
        "files": {key: item for key, item in files.items() if isinstance(item, str)},
        "row_counts": {key: item for key, item in row_counts.items() if isinstance(item, int) and not isinstance(item, bool)},
    }


def _fingerprint(value: object) -> str:
    return "sha256:" + sha256(canonical_json(value))


def _row_fingerprint(row: ClassificationRow) -> str:
    preimage = dict(row)
    _ = preimage.pop("result_fingerprint")
    return _fingerprint(preimage)


def _whole_component(row: ClassificationRow) -> bool:
    hints = row.get("semantic_hints", [])
    return (
        row.get("classification") == "meaning"
        and any(str(item).startswith("component:") for item in hints)
        and any(str(item).startswith("whole-component:") for item in hints)
    )


def _bindings(repo: Path) -> dict[str, str]:
    source = parse_json_object((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    diff = parse_json_object(canonical_json(validate_diff(repo)).decode())
    return {
        "source_generation_id": str(source["generation_id"]),
        "source_fingerprint": _fingerprint(source),
        "diff_generation_id": str(diff["generation_id"]),
        "diff_fingerprint": _fingerprint(diff),
    }


def inventory(repo: Path) -> list[dict[str, str]]:
    pointer = parse_json_object(canonical_json(validate_diff(repo)).decode())
    rows = read_csv(
        repo / "analysis/indexes/generations" / _string(pointer.get("generation_id"), "generation ID") / "diff-inventory.csv",
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


def _write_jsonl(path: Path, rows: list[ClassificationRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            for row in rows:
                _ = stream.write(canonical_json(row) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _validate_rows(rows: list[ClassificationRow], allowed_ids: set[str]) -> None:
    ids: set[str] = set()
    for row in rows:
        if set(row) != ROW_KEYS or row["schema_version"] != SCHEMA_VERSION:
            raise ValueError("invalid DIF classification row schema")
        identifier = row["stable_diff_id"]
        if identifier not in allowed_ids or identifier in ids:
            raise ValueError("unknown or duplicate classified DIF")
        if row["classification"] not in CLASSIFICATIONS:
            raise ValueError("invalid DIF classification")
        if row["semantic_hints"] != sorted(set(row["semantic_hints"])):
            raise ValueError("DIF semantic hints must be unique and sorted")
        if not row["evidence"]:
            raise ValueError("DIF classification requires evidence")
        if any(
            not str(item.get("path", "")).strip()
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
            if not row[key].startswith("sha256:") or len(row[key]) != 71:
                raise ValueError(f"invalid DIF classification {key}")
        if row["result_fingerprint"] != _row_fingerprint(row):
            raise ValueError("invalid DIF classification result fingerprint")
        ids.add(identifier)


def make_row(
    stable_diff_id: str,
    result: dict[str, JsonValue],
    *,
    evidence_fingerprint: str,
    result_schema_fingerprint: str,
    profile_fingerprint: str,
    instruction_fingerprint: str,
    context_fingerprint: str,
) -> ClassificationRow:
    kind = _string(result.get("kind"), "classification kind")
    row: ClassificationRow = {
        "schema_version": SCHEMA_VERSION,
        "stable_diff_id": stable_diff_id,
        "classification": "noise_candidate" if kind == "noise" else kind,
        "semantic_hints": sorted(set(_strings(result.get("semantic_hints", []), "semantic hints"))),
        "evidence": _evidence(result.get("evidence", [])),
        "rationale": str(result.get("rationale", "")),
        "evidence_fingerprint": evidence_fingerprint,
        "result_schema_fingerprint": result_schema_fingerprint,
        "profile_fingerprint": profile_fingerprint,
        "instruction_fingerprint": instruction_fingerprint,
        "context_fingerprint": context_fingerprint,
        "result_fingerprint": "",
    }
    row["result_fingerprint"] = _row_fingerprint(row)
    _validate_rows([row], {stable_diff_id})
    return row


def _generation_id(manifest_preimage: object) -> str:
    return sha256(canonical_json(manifest_preimage))


def validate_generation(repo: Path, pointer: GenerationPointer, *, current: bool = True) -> Generation:
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
    rows = [_classification_row(parse_json_object(line)) for line in payload.read_text(encoding="utf-8").splitlines()]
    if payload.read_bytes() != b"".join(canonical_json(row) + b"\n" for row in rows):
        raise ValueError("non-canonical DIF classification payload")
    if rows != sorted(rows, key=lambda row: row["stable_diff_id"]):
        raise ValueError("unsorted DIF classification payload")
    allowed = {row["stable_diff_id"] for row in inventory(repo)} if current else {row["stable_diff_id"] for row in rows}
    _validate_rows(rows, allowed)
    files = {"classifications.jsonl": sha256(payload.read_bytes())}
    if pointer["files"] != files or pointer["row_counts"] != {"classifications.jsonl": len(rows)}:
        raise ValueError("DIF classification pointer metadata mismatch")
    manifest = parse_json_object(manifest_path.read_text(encoding="utf-8"))
    preimage = {
        "schema_version": pointer["schema_version"],
        "source_generation_id": pointer["source_generation_id"],
        "source_fingerprint": pointer["source_fingerprint"],
        "diff_generation_id": pointer["diff_generation_id"],
        "diff_fingerprint": pointer["diff_fingerprint"],
        "files": pointer["files"],
        "row_counts": pointer["row_counts"],
    }
    if manifest != {**preimage, "generation_id": pointer["generation_id"]}:
        raise ValueError("DIF classification manifest mismatch")
    if pointer["generation_id"] != _generation_id(preimage):
        raise ValueError("DIF classification generation identity mismatch")
    return {"pointer": pointer, "rows": rows}


def load_active(repo: Path) -> Generation:
    path = repo / POINTER
    if not path.is_file():
        raise ValueError("active DIF classification generation is missing")
    return validate_generation(repo, _generation_pointer(parse_json_object(path.read_text(encoding="utf-8"))))


def coverage(repo: Path) -> dict[str, int | bool]:
    total_ids = {row["stable_diff_id"] for row in inventory(repo)}
    try:
        rows = load_active(repo)["rows"]
    except (OSError, ValueError, KeyError):
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
    rows: Iterable[ClassificationRow],
    *,
    expected_generation_id: str | None,
    reset: bool = False,
) -> GenerationPointer:
    incoming = list(rows)
    with repository_lock(repo):
        bindings = _bindings(repo)
        path = repo / POINTER
        active: Generation | None = validate_generation(repo, _generation_pointer(parse_json_object(path.read_text(encoding="utf-8"))), current=not reset) if path.is_file() else None
        active_pointer = active["pointer"] if active else None
        active_rows = active["rows"] if active else []
        actual = active_pointer["generation_id"] if active_pointer else None
        if actual != expected_generation_id:
            raise RuntimeError("stale DIF classification generation")
        merged: dict[str, ClassificationRow] = {} if reset else {row["stable_diff_id"]: row for row in active_rows}
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
            pointer: GenerationPointer = {
                "schema_version": SCHEMA_VERSION,
                "generation_id": generation_id,
                "source_generation_id": bindings["source_generation_id"],
                "source_fingerprint": bindings["source_fingerprint"],
                "diff_generation_id": bindings["diff_generation_id"],
                "diff_fingerprint": bindings["diff_fingerprint"],
                "files": files,
                "row_counts": {"classifications.jsonl": len(accumulated)},
            }
            root = repo / "analysis/dif-classifications/generations" / generation_id
            if not root.exists():
                root.parent.mkdir(parents=True, exist_ok=True)
                _write_jsonl(Path(temporary) / "classifications.jsonl", accumulated)
                atomic_json(Path(temporary) / "manifest.json", pointer)
                os.replace(temporary, root)
            _ = validate_generation(repo, pointer)
            atomic_json(path, pointer)
        return pointer


def publish_empty(repo: Path, *, expected_generation_id: str | None = None, reset: bool = False) -> GenerationPointer:
    return publish_window(repo, [], expected_generation_id=expected_generation_id, reset=reset)


def ensure_current(repo: Path) -> GenerationPointer:
    path = repo / POINTER
    if not path.is_file():
        return publish_empty(repo)
    try:
        return load_active(repo)["pointer"]
    except ValueError as exc:
        if str(exc) != "stale DIF classification bindings":
            raise
    pointer = _generation_pointer(parse_json_object(path.read_text(encoding="utf-8")))
    return publish_empty(repo, expected_generation_id=pointer["generation_id"], reset=True)


def remaining_ids(repo: Path, *, limit: int = 32) -> list[str]:
    active = load_active(repo)["rows"] if (repo / POINTER).is_file() else []
    classified = _current_rows(repo, active)
    return sorted({row["stable_diff_id"] for row in inventory(repo)} - set(classified))[:limit]


def _current_rows(repo: Path, rows: Iterable[ClassificationRow]) -> dict[str, ClassificationRow]:
    classified = {row["stable_diff_id"]: row for row in rows}
    source_path = repo / "research/active-source-generation.json"
    if not source_path.is_file():
        return classified
    from .component_groups import deterministic_results
    source = parse_json_object(source_path.read_text(encoding="utf-8"))
    expected = parse_json_object(canonical_json(deterministic_results(repo, classified)).decode()) if source.get("routing_manifest_path") else {}
    for identifier, raw_value in expected.items():
        if not isinstance(raw_value, dict):
            raise ValueError("invalid deterministic classification result")
        value = raw_value
        result = value.get("result")
        if not isinstance(result, dict):
            raise ValueError("invalid deterministic classification result")
        row = make_row(
            identifier,
            result,
            evidence_fingerprint=_string(value.get("evidence_fingerprint"), "evidence fingerprint"),
            result_schema_fingerprint=_string(value.get("result_schema_fingerprint"), "result schema fingerprint"),
            profile_fingerprint=_string(value.get("profile_fingerprint"), "profile fingerprint"),
            instruction_fingerprint=_string(value.get("instruction_fingerprint"), "instruction fingerprint"),
            context_fingerprint=_string(value.get("context_fingerprint"), "context fingerprint"),
        )
        if classified.get(identifier) != row:
            _ = classified.pop(identifier, None)
    return classified


def reusable_rows(
    prior_rows: Iterable[ClassificationRow],
    current_fingerprints: dict[str, dict[str, str]],
) -> list[ClassificationRow]:
    return [
        row for row in prior_rows
        if row["stable_diff_id"] in current_fingerprints
        and row["evidence_fingerprint"] == current_fingerprints[row["stable_diff_id"]].get("evidence_fingerprint")
        and row["result_schema_fingerprint"] == current_fingerprints[row["stable_diff_id"]].get("result_schema_fingerprint")
        and row["profile_fingerprint"] == current_fingerprints[row["stable_diff_id"]].get("profile_fingerprint")
        and row["instruction_fingerprint"] == current_fingerprints[row["stable_diff_id"]].get("instruction_fingerprint")
        and row["context_fingerprint"] == current_fingerprints[row["stable_diff_id"]].get("context_fingerprint")
    ]
