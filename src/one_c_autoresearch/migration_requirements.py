from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .common import file_sha256, read_csv, read_json, read_jsonl, repo_path, sha256, utc_now_iso, write_json, write_jsonl
from .customization_registry import load_registry_index, validate_registry
from .queue import queue_lock


ROOT = "analysis/migration-requirements"
REQUIREMENTS = f"{ROOT}/requirements.jsonl"
LINKS = f"{ROOT}/requirement-links.jsonl"
LINEAGE = f"{ROOT}/requirement-lineage.jsonl"
MAPPING = f"{ROOT}/migration-mapping.jsonl"
BACKLOG = f"{ROOT}/backlog-exceptions.jsonl"
OWNERSHIP = f"{ROOT}/ownership-ledger.jsonl"
SUBJECT_VIEWS = f"{ROOT}/subject-card-views.jsonl"
METADATA = f"{ROOT}/build-metadata.json"
SUMMARY = f"{ROOT}/summary.md"
CARDS = f"{ROOT}/cards"
LOCK = f"{ROOT}.lock"
SCHEMA = "migration-requirements/v1"

ROLES = {"primary", "required", "supporting", "shared"}
STATUSES = {"draft", "ready_for_review", "approved", "excluded_by_customer", "superseded"}
AGREEMENT_STATES = {"not_requested", "pending", "approved", "rejected"}
LINEAGE_EVENTS = {"split", "merge", "supersede", "restore"}
ACTIVE = {"draft", "ready_for_review", "approved"}
PUBLISHABLE = {"ready_for_review", "approved"}
INTERNAL_MARKERS = re.compile(r"\b(?:MRQ|CUS|V8D|CMI|EXT)-[A-Z0-9-]+\b|(?<!\w)analysis[\\/][^\s;,]+", re.I)
INTERNAL_IDS = re.compile(r"\b(?:MRQ|CUS|V8D|CMI|EXT)-[A-Z0-9-]+\b", re.I)
INTERNAL_PATHS = re.compile(r"(?<!\w)analysis[\\/][^\s;,]+", re.I)


def _rows(path: Path) -> list[dict[str, Any]]:
    return [row for _, row in read_jsonl(path)] if path.exists() else []


def _json(path: Path) -> dict[str, Any]:
    return read_json(path)


def _write_json(path: Path, payload: Any) -> None:
    write_json(path, payload)


def _digest(payload: Any) -> str:
    return sha256(payload)


def _public_text(value: Any) -> str:
    return INTERNAL_PATHS.sub("внутреннему доказательству", INTERNAL_IDS.sub("связанному элементу", str(value or ""))).strip()


def stable_requirement_id(stable_key: str) -> str:
    return f"MRQ-{hashlib.sha1(stable_key.encode()).hexdigest()[:10].upper()}"


def _read_csv(path: Path) -> list[dict[str, str]]:
    return read_csv(path)


def _subject_payload(root: Path, slug: str) -> dict[str, Any]:
    path = repo_path(root, f"analysis/subject-cards/cards/{slug}/subject-card.json")
    return _json(path)


def _gap_payload(root: Path, slug: str) -> dict[str, Any]:
    return _json(repo_path(root, f"analysis/functional-gaps/cards/{slug}/gap-card.json"))


def _source_hash(root: Path, relative: str) -> str:
    return file_sha256(repo_path(root, relative))


def _requirement_from_subject(root: Path, row: dict[str, str], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    slug = row["slug"]
    subject = _subject_payload(root, slug)
    gap = _gap_payload(root, slug)
    stable_key = f"subject:{slug}"
    generated = {
        "schema_version": SCHEMA,
        "requirement_id": stable_requirement_id(stable_key),
        "stable_key": stable_key,
        "title": subject.get("title") or row.get("title") or slug,
        "status": "draft",
        "agreement_state": "not_requested",
        "agreement_reason": "",
        "subject_tags": [subject.get("subject_type") or row.get("subject_type") or "other"],
        "source_scenario": subject.get("summary") or subject.get("identification") or "",
        "migration_boundary": subject.get("migration_boundary") or "",
        "bp30_coverage": gap.get("selected_decision_summary") or "Покрытие целевого релиза требует проверки.",
        "residual_gap": gap.get("selected_decision_summary") or "Не определен до проверки целевого релиза.",
        "target_solution": gap.get("selected_decision") or "needs_customer_decision",
        "acceptance_criteria": [q.get("needed", "") for q in subject.get("sections", {}).get("open_questions", []) if q.get("needed")],
        "risk": subject.get("upgrade_risk") or "Требует оценки.",
        "open_questions": [q.get("question", "") for q in subject.get("sections", {}).get("open_questions", []) if q.get("question")],
        "specification_text": subject.get("key_conclusion") or subject.get("summary") or "",
        "source_provenance": {
            "subject_card_slug": slug,
            "accepted_contour_id": gap.get("source_contour", {}).get("accepted_contour_id") or row.get("owner_feature", ""),
            "subject_card_path": row.get("card_path", ""),
            "subject_card_hash": _source_hash(root, row.get("card_path", "")),
            "gap_card_path": f"analysis/functional-gaps/cards/{slug}/gap-card.json",
            "gap_card_hash": _source_hash(root, f"analysis/functional-gaps/cards/{slug}/gap-card.json"),
            "target_release": gap.get("target_release", ""),
            "evidence_refs": subject.get("source_artifacts") or [],
        },
    }
    if previous:
        for field in ("requirement_id", "status", "agreement_state", "agreement_reason", "title", "source_scenario", "migration_boundary", "bp30_coverage", "residual_gap", "target_solution", "acceptance_criteria", "risk", "open_questions", "specification_text", "subject_tags"):
            generated[field] = previous.get(field, generated[field])
    return generated


def _candidate_graph(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    registry = load_registry_index(root)
    previous = {row.get("stable_key"): row for row in _rows(repo_path(root, REQUIREMENTS))}
    previous_links = _rows(repo_path(root, LINKS))
    by_slug: dict[str, set[str]] = defaultdict(set)
    for link in registry["links"]:
        if link.get("target_type") == "subject_card" and link.get("target_id"):
            by_slug[str(link["target_id"])].add(str(link["customization_id"]))
    subject_rows = [row for row in _read_csv(repo_path(root, "analysis/subject-cards/registry.csv")) if row.get("status") in {"ready_for_review", "reviewed", "accepted"}]
    requirements = [_requirement_from_subject(root, row, previous.get(f"subject:{row['slug']}")) for row in subject_rows]
    generated_keys = {row["stable_key"] for row in requirements}
    requirements.extend(row for key, row in previous.items() if key not in generated_keys)
    ids = {row["stable_key"]: row["requirement_id"] for row in requirements}
    linked_cus: set[str] = set()
    links: list[dict[str, Any]] = []
    existing_by_pair = {(row.get("requirement_id"), row.get("customization_id")): row for row in previous_links}
    owners: set[str] = set()
    for row in sorted(subject_rows, key=lambda item: item["slug"]):
        requirement_id = ids[f"subject:{row['slug']}"]
        for customization_id in sorted(by_slug.get(row["slug"], set())):
            old = existing_by_pair.get((requirement_id, customization_id), {})
            role = old.get("role") or ("primary" if customization_id not in owners else "shared")
            if role == "primary":
                owners.add(customization_id)
            links.append({
                "schema_version": SCHEMA,
                "requirement_id": requirement_id,
                "customization_id": customization_id,
                "role": role,
                "rationale": old.get("rationale") or f"Связь подтверждена предметной карточкой {row['slug']}.",
                "required": bool(old.get("required", role in {"primary", "required", "shared"})),
                "acceptance_refs": old.get("acceptance_refs") or [],
                "effort_owner": bool(role == "primary"),
            })
            linked_cus.add(customization_id)
    generated_pairs = {(row["requirement_id"], row["customization_id"]) for row in links}
    requirement_ids = {row["requirement_id"] for row in requirements}
    customization_ids = set(registry["by_id"])
    for old in previous_links:
        pair = (str(old.get("requirement_id") or ""), str(old.get("customization_id") or ""))
        if pair not in generated_pairs and pair[0] in requirement_ids and pair[1] in customization_ids:
            links.append(old)
            linked_cus.add(pair[1])
    included = {str(row["customization_id"]) for row in registry["items"] if row.get("scope_status") == "included"}
    uncovered = sorted(included - linked_cus)
    decisions_by_cus: dict[str, set[str]] = defaultdict(set)
    req_by_id = {row["requirement_id"]: row for row in requirements}
    for link in links:
        decision = str(req_by_id[link["requirement_id"]].get("target_solution") or "")
        if decision not in {"", "needs_customer_decision", "business_decision"}:
            decisions_by_cus[link["customization_id"]].add(decision)
    conflict_ids = {cid for cid, decisions in decisions_by_cus.items() if len(decisions) > 1}
    conflicts_by_req = {link["requirement_id"] for link in links if link["customization_id"] in conflict_ids}
    mapping = [{
        "schema_version": SCHEMA,
        "source_slug": row["slug"],
        "source_card_path": row.get("card_path", ""),
        "source_card_hash": _source_hash(root, row.get("card_path", "")),
        "requirement_ids": [ids[f"subject:{row['slug']}"]],
        "migration_action": "split_candidate" if ids[f"subject:{row['slug']}"] in conflicts_by_req else "mapped",
    } for row in subject_rows]
    return sorted(requirements, key=lambda x: x["requirement_id"]), sorted(links, key=lambda x: (x["requirement_id"], x["customization_id"])), mapping, [{"customization_id": value, "reason": "uncovered", "blocks_activation": True} for value in uncovered]


def _validation(root: Path, requirements: list[dict[str, Any]] | None = None, links: list[dict[str, Any]] | None = None, lineage: list[dict[str, Any]] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    requirements = requirements if requirements is not None else _rows(repo_path(root, REQUIREMENTS))
    links = links if links is not None else _rows(repo_path(root, LINKS))
    lineage = lineage if lineage is not None else _rows(repo_path(root, LINEAGE))
    metadata = metadata if metadata is not None else _json(repo_path(root, METADATA))
    errors: list[str] = []
    warnings: list[str] = []
    registry_check = validate_registry(root)
    if registry_check["status"] != "ok":
        errors.append("Customization registry validation failed")
    registry = load_registry_index(root)
    cus_by_id = registry["by_id"]
    req_by_id: dict[str, dict[str, Any]] = {}
    keys_by_id: dict[str, str] = {}
    stable_keys: dict[str, str] = {}
    for req in requirements:
        rid = str(req.get("requirement_id") or "")
        key = str(req.get("stable_key") or "")
        if not rid.startswith("MRQ-") or not key:
            errors.append(f"Invalid requirement identity: {rid or '<empty>'}")
        if rid in req_by_id:
            errors.append(f"Stable id collision: {rid} maps both {keys_by_id[rid]} and {key}")
        if key in stable_keys and stable_keys[key] != rid:
            errors.append(f"Duplicate requirement stable key: {key}")
        if rid != stable_requirement_id(key):
            errors.append(f"Stable id mismatch: {rid}/{key}")
        req_by_id[rid] = req
        keys_by_id[rid] = key
        stable_keys[key] = rid
        for field in ("title", "source_scenario", "migration_boundary", "bp30_coverage", "residual_gap", "target_solution", "acceptance_criteria", "risk", "open_questions", "agreement_state", "specification_text"):
            if field not in req:
                errors.append(f"{rid} lacks required field: {field}")
        if req.get("status") not in STATUSES or req.get("agreement_state") not in AGREEMENT_STATES:
            errors.append(f"{rid} has invalid lifecycle state")
        if req.get("status") in PUBLISHABLE:
            for field in ("title", "source_scenario", "migration_boundary", "bp30_coverage", "residual_gap", "target_solution", "acceptance_criteria", "risk", "specification_text"):
                if not req.get(field):
                    errors.append(f"{rid} is publishable but {field} is empty")
        if req.get("status") == "approved" and req.get("agreement_state") != "approved":
            errors.append(f"{rid} is approved without approved agreement state")
        if req.get("status") == "excluded_by_customer" and not str(req.get("agreement_reason") or "").strip():
            errors.append(f"{rid} is excluded without customer agreement reason")
    primary: dict[str, list[str]] = defaultdict(list)
    pairs: set[tuple[str, str]] = set()
    linked: set[str] = set()
    for link in links:
        pair = (str(link.get("requirement_id") or ""), str(link.get("customization_id") or ""))
        if pair in pairs:
            errors.append(f"Duplicate requirement link: {pair[0]}/{pair[1]}")
        pairs.add(pair)
        if pair[0] not in req_by_id or pair[1] not in cus_by_id:
            errors.append(f"Broken requirement link: {pair[0]}/{pair[1]}")
            continue
        if link.get("role") not in ROLES or not str(link.get("rationale") or "").strip():
            errors.append(f"Invalid requirement link: {pair[0]}/{pair[1]}")
        if link.get("role") == "primary":
            primary[pair[1]].append(pair[0])
            if not link.get("effort_owner"):
                errors.append(f"Primary link does not own effort: {pair[0]}/{pair[1]}")
        elif link.get("effort_owner"):
            errors.append(f"Non-primary link owns effort: {pair[0]}/{pair[1]}")
        linked.add(pair[1])
    for cid, owners in primary.items():
        if len(owners) > 1:
            errors.append(f"Multiple primary owners for {cid}: {','.join(owners)}")
    links_by_requirement = Counter(str(link.get("requirement_id") or "") for link in links)
    for rid, requirement in req_by_id.items():
        if requirement.get("status") in PUBLISHABLE and not links_by_requirement[rid]:
            errors.append(f"Publishable requirement has no active customization: {rid}")
    decisions_by_cus: dict[str, set[str]] = defaultdict(set)
    for link in links:
        requirement = req_by_id.get(str(link.get("requirement_id") or ""), {})
        decision = str(requirement.get("target_solution") or "")
        if decision not in {"", "needs_customer_decision", "business_decision"}:
            decisions_by_cus[str(link.get("customization_id") or "")].add(decision)
    for cid, decisions in decisions_by_cus.items():
        if len(decisions) > 1:
            warnings.append(f"Conflicting requirement decisions require CUS split: {cid} ({','.join(sorted(decisions))})")
    for cid, cus in cus_by_id.items():
        if cus.get("scope_status") == "included" and cid not in linked:
            warnings.append(f"Uncovered included customization: {cid}")
        if cus.get("scope_status") == "included" and cus.get("migration_decision") in {"carry", "adapt"} and len(primary.get(cid, [])) != 1:
            errors.append(f"Implemented customization lacks unique primary owner: {cid}")
    active_ids = {rid for rid, req in req_by_id.items() if req.get("status") in ACTIVE}
    for event in lineage:
        if event.get("event_type") not in LINEAGE_EVENTS:
            errors.append(f"Invalid lineage event: {event.get('event_type')}")
        for rid in list(event.get("source_ids") or []) + list(event.get("target_ids") or []):
            if rid not in req_by_id:
                errors.append(f"Lineage references unknown requirement: {rid}")
        if event.get("event_type") == "supersede" and any(rid in active_ids for rid in event.get("source_ids") or []):
            errors.append("Superseded requirement remains active")
    canonical_payload = {"requirements": requirements, "links": links, "lineage": lineage}
    generation_id = _digest(canonical_payload)
    if metadata and metadata.get("generation_id") not in {None, "", generation_id}:
        errors.append("Build metadata refers to a mixed generation")
    if metadata.get("canonical_active") and (warnings or errors or not metadata.get("migration_comparison_accepted")):
        errors.append("Canonical activation gate is not satisfied")
    return {"status": "ok" if not errors else "fail", "errors": errors, "warnings": warnings, "counts": {"requirements": len(requirements), "links": len(links), "uncovered": sum(v.startswith("Uncovered") for v in warnings), "conflicts": sum(v.startswith("Conflicting") for v in warnings) + len(errors)}, "generation_id": generation_id}


def _card_hash(requirement: dict[str, Any], links: list[dict[str, Any]]) -> str:
    return _digest({"requirement": requirement, "links": sorted(links, key=lambda row: row["customization_id"])})


def _write_generation(root: Path, requirements: list[dict[str, Any]], links: list[dict[str, Any]], lineage: list[dict[str, Any]], mapping: list[dict[str, Any]], uncovered: list[dict[str, Any]], previous_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    validation = _validation(root, requirements, links, lineage, {})
    if validation["status"] != "ok":
        return validation
    if (previous_metadata or {}).get("canonical_active") and validation["warnings"]:
        return {**validation, "status": "fail", "errors": ["Active contour cannot publish a generation with coverage or decision conflicts"]}
    if (previous_metadata or {}).get("canonical_active") and validation["generation_id"] != (previous_metadata or {}).get("generation_id"):
        return {**validation, "status": "fail", "errors": ["Deactivate the canonical contour before changing its generation"]}
    target = repo_path(root, ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="migration-requirements-", dir=target.parent))
    try:
        write_jsonl(staging / "requirements.jsonl", requirements)
        write_jsonl(staging / "requirement-links.jsonl", links)
        write_jsonl(staging / "requirement-lineage.jsonl", lineage)
        write_jsonl(staging / "migration-mapping.jsonl", mapping)
        write_jsonl(staging / "backlog-exceptions.jsonl", uncovered)
        write_jsonl(staging / "ownership-ledger.jsonl", [{"customization_id": link["customization_id"], "owner_requirement_id": link["requirement_id"], "effort_count": 1} for link in links if link.get("effort_owner")])
        write_jsonl(staging / "subject-card-views.jsonl", [{"requirement_id": row["requirement_id"], "source_slug": row.get("source_provenance", {}).get("subject_card_slug", ""), "title": row["title"], "status": row["status"], "scenario": row["source_scenario"], "migration_boundary": row["migration_boundary"], "requirement_hash": _digest(row)} for row in requirements])
        by_req: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for link in links:
            by_req[link["requirement_id"]].append(link)
        for requirement in requirements:
            card_dir = staging / "cards" / requirement["requirement_id"]
            card_dir.mkdir(parents=True)
            card_links = by_req[requirement["requirement_id"]]
            card_hash = _card_hash(requirement, card_links)
            _write_json(card_dir / "requirement.json", {**requirement, "card_hash": card_hash})
            write_jsonl(card_dir / "evidence.jsonl", card_links)
            (card_dir / "review.md").write_text(f"# {requirement['title']}\n\n- Статус: {requirement['status']}\n- Решение: {requirement['target_solution']}\n- Связанных доработок: {len(card_links)}\n- Хэш: `{card_hash}`\n", encoding="utf-8")
        metadata = {
            "schema_version": SCHEMA,
            "generated_at": utc_now_iso(),
            "generation_id": validation["generation_id"],
            "canonical_active": bool((previous_metadata or {}).get("canonical_active", False)),
            "migration_comparison_accepted": bool((previous_metadata or {}).get("migration_comparison_accepted", False)),
            "migration_comparison_report": (previous_metadata or {}).get("migration_comparison_report", ""),
            "migration_comparison_report_hash": (previous_metadata or {}).get("migration_comparison_report_hash", ""),
            "input_fingerprints": {
                "customization_items": _source_hash(root, "analysis/customization-registry/customization-items.jsonl"),
                "customization_links": _source_hash(root, "analysis/customization-registry/customization-links.jsonl"),
                "subject_registry": _source_hash(root, "analysis/subject-cards/registry.csv"),
            },
            "validation": validation,
        }
        _write_json(staging / "build-metadata.json", metadata)
        counts = validation["counts"]
        (staging / "summary.md").write_text(f"# Migration Requirements\n\n- requirements: {counts['requirements']}\n- links: {counts['links']}\n- uncovered: {counts['uncovered']}\n- conflicts: {counts['conflicts']}\n- canonical active: {str(metadata['canonical_active']).lower()}\n", encoding="utf-8")
        readme = repo_path(root, f"{ROOT}/README.md")
        if readme.exists():
            shutil.copy2(readme, staging / "README.md")
        schemas = repo_path(root, f"{ROOT}/schemas")
        if schemas.exists():
            shutil.copytree(schemas, staging / "schemas")
        comparison = repo_path(root, f"{ROOT}/comparison.md")
        if comparison.exists():
            shutil.copy2(comparison, staging / "comparison.md")
        backup = target.with_name(f"{target.name}.previous")
        with queue_lock(repo_path(root, LOCK), 30):
            if backup.exists():
                shutil.rmtree(backup)
            if target.exists():
                target.replace(backup)
            try:
                staging.replace(target)
            except Exception:
                if backup.exists() and not target.exists():
                    backup.replace(target)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        return {**validation, "path": ROOT}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def bootstrap(root: Path, apply: bool = False) -> dict[str, Any]:
    root = root.resolve()
    registry_validation = validate_registry(root)
    if registry_validation["status"] != "ok":
        return {"status": "fail", "errors": ["Customization registry must validate before bootstrap"]}
    requirements, links, mapping, uncovered = _candidate_graph(root)
    link_counts = Counter(link["customization_id"] for link in links)
    registry = load_registry_index(root)
    result = {"status": "planned", "requirements": len(requirements), "links": len(links), "mapped": sum(row["migration_action"] == "mapped" for row in mapping), "split_candidates": sum(row["migration_action"] == "split_candidate" for row in mapping), "shared": sum(count > 1 for count in link_counts.values()), "uncovered": len(uncovered), "merged": sum(row["migration_action"] == "merged" for row in mapping), "excluded": sum(row.get("scope_status") == "excluded_by_customer" for row in registry["items"])}
    if not apply:
        return result
    lineage = _rows(repo_path(root, LINEAGE))
    written = _write_generation(root, requirements, links, lineage, mapping, uncovered, _json(repo_path(root, METADATA)))
    return {**result, **written, "status": written["status"]}


def build(root: Path) -> dict[str, Any]:
    root = root.resolve()
    requirements = _rows(repo_path(root, REQUIREMENTS))
    if not requirements:
        return {"status": "fail", "errors": ["Run migration-requirement bootstrap --apply first"]}
    metadata = _json(repo_path(root, METADATA))
    current_inputs = {"customization_items": _source_hash(root, "analysis/customization-registry/customization-items.jsonl"), "customization_links": _source_hash(root, "analysis/customization-registry/customization-links.jsonl"), "subject_registry": _source_hash(root, "analysis/subject-cards/registry.csv")}
    if metadata.get("input_fingerprints") != current_inputs:
        return {"status": "fail", "errors": ["Source registries changed; run migration-requirement bootstrap --apply"]}
    return _write_generation(root, requirements, _rows(repo_path(root, LINKS)), _rows(repo_path(root, LINEAGE)), _rows(repo_path(root, MAPPING)), _rows(repo_path(root, BACKLOG)), metadata)


def replace_graph(root: Path, requirements: list[dict[str, Any]], links: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate and atomically publish canonical requirement and relation rows."""
    root = root.resolve()
    linked = {str(row.get("customization_id") or "") for row in links}
    uncovered = [
        {"customization_id": row["customization_id"], "reason": "uncovered", "blocks_activation": True}
        for row in load_registry_index(root)["items"]
        if row.get("scope_status") == "included" and row["customization_id"] not in linked
    ]
    return _write_generation(
        root,
        sorted(requirements, key=lambda row: row["requirement_id"]),
        sorted(links, key=lambda row: (row["requirement_id"], row["customization_id"])),
        _rows(repo_path(root, LINEAGE)),
        _rows(repo_path(root, MAPPING)),
        uncovered,
        _json(repo_path(root, METADATA)),
    )


def validate_graph(root: Path, requirements: list[dict[str, Any]], links: list[dict[str, Any]]) -> dict[str, Any]:
    return _validation(root.resolve(), requirements, links, _rows(repo_path(root, LINEAGE)), {})


def validate(root: Path) -> dict[str, Any]:
    result = _validation(root)
    if result["status"] == "ok":
        by_req: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for link in _rows(repo_path(root, LINKS)):
            by_req[str(link.get("requirement_id"))].append(link)
        for req in _rows(repo_path(root, REQUIREMENTS)):
            card = _json(repo_path(root, f"{CARDS}/{req['requirement_id']}/requirement.json"))
            if card.get("card_hash") != _card_hash(req, by_req[req["requirement_id"]]):
                result["errors"].append(f"Stale derived card: {req['requirement_id']}")
        expected_owners = {(link["customization_id"], link["requirement_id"], 1) for link in _rows(repo_path(root, LINKS)) if link.get("effort_owner")}
        actual_owners = {(row.get("customization_id"), row.get("owner_requirement_id"), row.get("effort_count")) for row in _rows(repo_path(root, OWNERSHIP))}
        if expected_owners != actual_owners:
            result["errors"].append("Ownership ledger is stale or duplicates implementation effort")
        requirements = _rows(repo_path(root, REQUIREMENTS))
        expected_views = {(row["requirement_id"], _digest(row)) for row in requirements}
        actual_views = {(row.get("requirement_id"), row.get("requirement_hash")) for row in _rows(repo_path(root, SUBJECT_VIEWS))}
        if expected_views != actual_views:
            result["errors"].append("Subject-card compatibility views are stale")
        specification = _json(repo_path(root, "outputs/technical-specification-draft.json"))
        if specification:
            expected = {(row["requirement_id"], _digest(row)) for row in requirements if row.get("status") in ACTIVE}
            actual = {(row.get("requirement_id"), row.get("requirement_hash")) for row in specification.get("requirements", [])}
            if expected != actual:
                result["errors"].append("Technical specification draft is stale or lacks reverse trace")
        metadata = _json(repo_path(root, METADATA))
        current_inputs = {
            "customization_items": _source_hash(root, "analysis/customization-registry/customization-items.jsonl"),
            "customization_links": _source_hash(root, "analysis/customization-registry/customization-links.jsonl"),
            "subject_registry": _source_hash(root, "analysis/subject-cards/registry.csv"),
        }
        if metadata.get("input_fingerprints") != current_inputs:
            result["errors"].append("Migration-requirement graph is stale after source registry changes")
        if metadata.get("canonical_active"):
            comparison_report = str(metadata.get("migration_comparison_report") or "")
            if not comparison_report or _source_hash(root, comparison_report) != metadata.get("migration_comparison_report_hash"):
                result["errors"].append("Accepted migration comparison report is missing or changed")
            for requirement in requirements:
                provenance = requirement.get("source_provenance") or {}
                for path_field, hash_field in (("subject_card_path", "subject_card_hash"), ("gap_card_path", "gap_card_hash")):
                    relative = str(provenance.get(path_field) or "")
                    expected_hash = str(provenance.get(hash_field) or "")
                    if relative and expected_hash and _source_hash(root, relative) != expected_hash:
                        result["errors"].append(f"Legacy decision source drifted after cutover: {relative}")
        if result["errors"]:
            result["status"] = "fail"
    return result


def load_index(root: Path) -> dict[str, Any]:
    requirements = _rows(repo_path(root, REQUIREMENTS))
    links = _rows(repo_path(root, LINKS))
    by_req = {row["requirement_id"]: row for row in requirements}
    links_by_req: dict[str, list[dict[str, Any]]] = defaultdict(list)
    links_by_cus: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        links_by_req[link["requirement_id"]].append(link)
        links_by_cus[link["customization_id"]].append(link)
    return {"requirements": requirements, "by_req": by_req, "links": links, "links_by_req": links_by_req, "links_by_cus": links_by_cus, "metadata": _json(repo_path(root, METADATA))}


def canonical_active(root: Path) -> bool:
    return bool(_json(repo_path(root, METADATA)).get("canonical_active"))


def requirements_for_customizations(root: Path, customization_ids: list[str], index: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    index = index or load_index(root)
    result: dict[str, dict[str, Any]] = {}
    for customization_id in customization_ids:
        for link in index["links_by_cus"].get(customization_id, []):
            requirement = index["by_req"].get(link["requirement_id"])
            if requirement and requirement.get("status") in ACTIVE:
                entry = result.setdefault(requirement["requirement_id"], {"requirement_id": requirement["requirement_id"], "title": requirement["title"], "roles": set(), "requirement_hash": _digest(requirement)})
                entry["roles"].add(link["role"])
    return [{**result[key], "roles": sorted(result[key]["roles"]), "primary_owner": "primary" in result[key]["roles"]} for key in sorted(result)]


def show(root: Path, entity_id: str) -> dict[str, Any]:
    index = load_index(root)
    if entity_id.startswith("MRQ-") and entity_id in index["by_req"]:
        return {"entity": index["by_req"][entity_id], "links": index["links_by_req"][entity_id]}
    if entity_id.startswith("CUS-"):
        item = load_registry_index(root)["by_id"].get(entity_id)
        if item:
            return {"entity": item, "links": index["links_by_cus"][entity_id]}
    raise KeyError(f"Entity not found: {entity_id}")


def neighbors(root: Path, entity_id: str, limit: int = 100) -> dict[str, Any]:
    payload = show(root, entity_id)
    rows = payload["links"][:limit]
    return {"entity_id": entity_id, "neighbors": rows, "truncated": len(payload["links"]) > limit}


def _safe_source_path(root: Path, value: str) -> str:
    if not value:
        return ""
    path = repo_path(root, value).resolve()
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"Evidence path escapes registered roots: {value}") from exc


def context(root: Path, requirement_id: str, limit: int = 100) -> dict[str, Any]:
    index = load_index(root)
    if requirement_id not in index["by_req"]:
        raise KeyError(f"Requirement not found: {requirement_id}")
    registry = load_registry_index(root)
    links = index["links_by_req"][requirement_id]
    selected = links[:limit]
    customizations = []
    for link in selected:
        cid = link["customization_id"]
        item = registry["by_id"][cid]
        evidence = registry["evidence_by_item"].get(cid, [])
        customizations.append({
            "customization": item,
            "relation": link,
            "diff_ids": sorted({row.get("diff_id") for row in evidence if row.get("diff_id")}),
            "metadata_refs": sorted({row.get("item_id") for row in evidence if row.get("item_id")}),
            "source_paths": sorted({_safe_source_path(root, str(row.get("source_path") or row.get("part_path") or "")) for row in evidence if row.get("source_path") or row.get("part_path")}),
        })
    return {"requirement": index["by_req"][requirement_id], "customizations": customizations, "truncated": len(links) > limit, "total_customizations": len(links), "generation_id": index["metadata"].get("generation_id", "")}


def _mutate_link(root: Path, requirement_id: str, customization_id: str, remove: bool, role: str = "supporting", rationale: str = "") -> dict[str, Any]:
    requirements = _rows(repo_path(root, REQUIREMENTS))
    links = _rows(repo_path(root, LINKS))
    pair = (requirement_id, customization_id)
    links = [row for row in links if (row.get("requirement_id"), row.get("customization_id")) != pair]
    if not remove:
        links.append({"schema_version": SCHEMA, "requirement_id": requirement_id, "customization_id": customization_id, "role": role, "rationale": rationale, "required": role != "supporting", "acceptance_refs": [], "effort_owner": role == "primary"})
    return _write_generation(root, requirements, sorted(links, key=lambda row: (row["requirement_id"], row["customization_id"])), _rows(repo_path(root, LINEAGE)), _rows(repo_path(root, MAPPING)), _rows(repo_path(root, BACKLOG)), _json(repo_path(root, METADATA)))


def activate(root: Path, comparison_accepted: bool, comparison_report: str = "") -> dict[str, Any]:
    report_path = repo_path(root, comparison_report) if comparison_report else Path()
    if not comparison_accepted or not comparison_report or not report_path.is_file():
        return {"status": "fail", "errors": ["Activation requires an accepted migration comparison report"]}
    metadata = _json(repo_path(root, METADATA))
    metadata["migration_comparison_accepted"] = comparison_accepted
    metadata["migration_comparison_report"] = comparison_report
    metadata["migration_comparison_report_hash"] = _source_hash(root, comparison_report)
    check = validate(root)
    if not repo_path(root, "outputs/technical-specification-draft.json").is_file():
        check["errors"].append("Build migration-requirement outputs before activation")
        check["status"] = "fail"
    if check["status"] != "ok" or check["warnings"]:
        return {"status": "fail", "errors": check["errors"] + check["warnings"]}
    metadata["canonical_active"] = True
    _write_json(repo_path(root, METADATA), metadata)
    return {"status": "ok", "canonical_active": True}


def deactivate(root: Path) -> dict[str, Any]:
    metadata = _json(repo_path(root, METADATA))
    if not metadata:
        return {"status": "fail", "errors": ["Migration-requirement contour is not built"]}
    metadata["canonical_active"] = False
    metadata["deactivated_at"] = utc_now_iso()
    _write_json(repo_path(root, METADATA), metadata)
    return {"status": "ok", "canonical_active": False}


def build_outputs(root: Path) -> dict[str, Any]:
    index = load_index(root)
    active = [row for row in index["requirements"] if row.get("status") in ACTIVE]
    publishable = [row for row in active if row.get("status") in PUBLISHABLE]
    trace = [{"requirement_id": row["requirement_id"], "customization_ids": [link["customization_id"] for link in index["links_by_req"][row["requirement_id"]]], "requirement_hash": _digest(row)} for row in publishable]
    output = repo_path(root, "outputs")
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output / "migration-requirements.trace.jsonl", trace)
    public = [{"number": number, "title": _public_text(row["title"]), "business_area": _public_text("; ".join(row.get("subject_tags") or [])), "change_summary": _public_text(row["source_scenario"]), "transition_decision": _public_text(row["target_solution"]), "target_release_coverage": _public_text(row["bp30_coverage"]), "residual_gap": _public_text(row["residual_gap"]), "risk": _public_text(row["risk"]), "verification_status": _public_text(row["status"]), "recommendation": _public_text(row["specification_text"])} for number, row in enumerate(publishable, 1)]
    fields = list(public[0]) if public else ["number", "title", "business_area", "change_summary", "transition_decision", "target_release_coverage", "residual_gap", "risk", "verification_status", "recommendation"]
    with (output / "migration-requirements-register.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(public)
    (output / "migration-requirements-register.md").write_text("# Миграционные требования\n\n" + "\n".join(f"{row['number']}. **{row['title']}** — {row['transition_decision']}" for row in public) + "\n", encoding="utf-8")
    _write_json(output / "functional-gap-map.mrq.json", {"generation_id": index["metadata"].get("generation_id"), "requirements": active})
    _write_json(output / "technical-specification-draft.json", {"generation_id": index["metadata"].get("generation_id"), "requirements": [{"requirement_id": row["requirement_id"], "requirement_hash": _digest(row), "customization_ids": [link["customization_id"] for link in index["links_by_req"][row["requirement_id"]]]} for row in active]})
    specification_texts = [f"## {_public_text(row['title'])}\n\n{_public_text(row['specification_text'])}\n\nКритерии приемки: " + "; ".join(_public_text(value) for value in row.get("acceptance_criteria") or []) for row in active]
    (output / "technical-specification-draft.md").write_text("# Проект технического задания\n\n" + "\n\n".join(specification_texts) + "\n", encoding="utf-8")
    leaks = [f"register:{number}:{field}" for number, row in enumerate(public, 1) for field, value in row.items() if INTERNAL_MARKERS.search(str(value))]
    leaks.extend(f"specification:{number}" for number, text in enumerate(specification_texts, 1) if INTERNAL_MARKERS.search(text))
    return {"status": "fail" if leaks else "ok", "requirements": len(active), "leaks": leaks}


def _print(payload: Any) -> int:
    printable = dict(payload)
    if len(printable.get("warnings") or []) > 20:
        printable["warnings_total"] = len(printable["warnings"])
        printable["warnings"] = printable["warnings"][:20]
        printable["warnings_truncated"] = True
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if payload.get("status", "ok") in {"ok", "planned"} else 1


def bootstrap_command(args: argparse.Namespace) -> int:
    return _print(bootstrap(Path(args.repo_path), apply=args.apply))


def build_command(args: argparse.Namespace) -> int:
    return _print(build(Path(args.repo_path)))


def validate_command(args: argparse.Namespace) -> int:
    return _print(validate(Path(args.repo_path)))


def show_command(args: argparse.Namespace) -> int:
    try:
        return _print({"status": "ok", **show(Path(args.repo_path), args.entity_id)})
    except KeyError as exc:
        return _print({"status": "fail", "error": str(exc)})


def neighbors_command(args: argparse.Namespace) -> int:
    try:
        return _print({"status": "ok", **neighbors(Path(args.repo_path), args.entity_id, args.limit)})
    except KeyError as exc:
        return _print({"status": "fail", "error": str(exc)})


def context_command(args: argparse.Namespace) -> int:
    try:
        return _print({"status": "ok", **context(Path(args.repo_path), args.requirement_id, args.limit)})
    except (KeyError, ValueError) as exc:
        return _print({"status": "fail", "error": str(exc)})


def link_command(args: argparse.Namespace) -> int:
    return _print(_mutate_link(Path(args.repo_path), args.requirement_id, args.customization_id, False, args.role, args.rationale))


def unlink_command(args: argparse.Namespace) -> int:
    return _print(_mutate_link(Path(args.repo_path), args.requirement_id, args.customization_id, True))


def activate_command(args: argparse.Namespace) -> int:
    return _print(activate(Path(args.repo_path), args.comparison_accepted, args.comparison_report))


def deactivate_command(args: argparse.Namespace) -> int:
    return _print(deactivate(Path(args.repo_path)))


def outputs_command(args: argparse.Namespace) -> int:
    return _print(build_outputs(Path(args.repo_path)))
