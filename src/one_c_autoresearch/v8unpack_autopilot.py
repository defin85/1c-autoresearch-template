from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .autopilot import DIFF_INVENTORY_HEADER
from .common import read_jsonl, read_toml, repo_path, toml_enabled, toml_value, utc_now_iso
from .stable_diff_ids import DIFF_ID_MAP_PATH, read_csv_rows as read_stable_csv_rows, stable_diff_id_for_row, validate_diff_id_map_rows, write_diff_id_map
from .v8unpack_refinement import (
    NESTED_REPO,
    REFINEMENT_BRANCH,
    REFINEMENT_DIR,
    TARGET_TAG,
    VENDOR_TAG,
)


CANONICAL_SOURCE = "v8unpack-refinement"
TRACE_HEADER = (
    "diff_id,canonical_path,change_type,source_first_pass_group_id,refinement_item_id,"
    "object_group,object_kind,object_name,evidence_type,refinement_status,refinement_decision,"
    "risk,feature_hint,canonical_evidence_ref,xml_support_ref"
)
XML_SUPPORT_PATH = "analysis/indexes/xml-clean-rebase-support.csv"
TRACE_PATH = "analysis/indexes/v8unpack-diff-trace.csv"
METADATA_PATH = "analysis/indexes/source-alignment.json"
SUPERSEDED_PATH = "analysis/indexes/xml-clean-rebase-superseded.json"
FORM_MODEL_ORDINARY = "ordinary"
FEATURE_HINT_TO_ID = {
    "налоги и регламентированный учет": "BF-001",
    "бюджетирование и планирование": "BF-002",
    "логистика и транспорт": "BF-005",
    "корпоративные расширения": "BF-005",
    "интеграции и обмены": "BF-009",
    "кадры и зарплата": "BF-001",
    "прочие объекты": "BF-015",
}


def project_uses_ordinary_forms(root: Path) -> bool:
    manifest_path = repo_path(root, "project.toml")
    if not manifest_path.exists():
        return False
    manifest = read_toml(manifest_path)
    form_model = toml_value(manifest, "configuration", "form_model").strip().lower()
    if form_model == FORM_MODEL_ORDINARY:
        return True
    return toml_enabled(manifest, "configuration") or toml_value(manifest, "configuration", "ordinary_forms").strip().lower() == "true"


def _run_git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotePath=false", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_csv(path: Path, header: str, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = header.split(",")
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _json_list(value: str) -> list[str]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _name_status(repo: Path) -> list[tuple[str, str]]:
    result = _run_git(repo, ["diff", "--name-status", "--find-renames", f"{VENDOR_TAG}..{REFINEMENT_BRANCH}"])
    entries: list[tuple[str, str]] = []
    for raw in result.stdout.splitlines():
        parts = raw.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[2] if status.startswith("R") and len(parts) >= 3 else parts[1]
        entries.append((status, path))
    return entries


def _area_for(path: str, evidence_type: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".bsl"):
        return "bsl"
    if "form/" in lowered or "form." in lowered or "form/" in lowered.replace("\\", "/"):
        return "form"
    if "template" in lowered or "commontemplate" in lowered or evidence_type == "template-content":
        return "template"
    if lowered.endswith((".bin", ".c1brace")) or evidence_type in {"binary", "predefined-data"}:
        return "binary"
    if lowered.endswith((".json", ".xml")) or evidence_type in {"metadata-text", "mixed"}:
        return "metadata"
    return "other"


def _feature_id_for(row: dict[str, str], path: str, area: str) -> str:
    hint = row.get("feature_hint", "").strip()
    if hint == "формы, печать и отчеты":
        object_kind = row.get("object_kind", "").strip()
        if object_kind == "Report" or path.startswith("Report/"):
            return "BF-006"
        if "Template" in object_kind or area == "template":
            return "BF-007"
        if "Form" in object_kind or area == "form":
            return "BF-014"
        return "BF-014"
    return FEATURE_HINT_TO_ID.get(hint, "BF-015")


def _summary_for(row: dict[str, str], path: str) -> str:
    evidence_type = row.get("evidence_type", "") or "unknown"
    object_kind = row.get("object_kind", "") or "Object"
    object_name = row.get("object_name", "") or row.get("object_group", "")
    status = row.get("status", "") or "unknown"
    return (
        f"v8unpack retained candidate: {object_kind}.{object_name}; "
        f"evidence_type={evidence_type}; refinement_status={status}; path={path}"
    )


def _xml_key(row: dict[str, str]) -> str:
    object_name = row.get("object_name", "").strip()
    if object_name:
        return object_name
    kind = row.get("object_kind", "").strip()
    name = row.get("path", "").split("/", 2)[1] if "/" in row.get("path", "") else ""
    return f"{kind}.{name}" if kind and name else ""


def _xml_support_map(rows: list[dict[str, str]]) -> dict[str, str]:
    by_key: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        key = _xml_key(row)
        ref = row.get("evidence_ref", "").strip()
        if key and ref:
            by_key[key].append(ref)
    return {key: ";".join(refs[:5]) for key, refs in by_key.items()}


def _object_key(row: dict[str, str]) -> str:
    kind = row.get("object_kind", "").strip()
    name = row.get("object_name", "").strip()
    return f"{kind}.{name}" if kind and name else row.get("object_group", "").strip()


def _decision_by_item(root: Path) -> dict[str, dict[str, Any]]:
    decisions = [row for _, row in read_jsonl(repo_path(root, f"{REFINEMENT_DIR}/decisions.jsonl"))]
    return {str(row.get("item_id", "")): row for row in decisions if row.get("item_id")}


def _manual_cleanup_decisions_by_stable_id(root: Path) -> dict[str, dict[str, Any]]:
    path = repo_path(root, "analysis/detailed-register-reverse-review/manual-markup-queue.jsonl")
    if not path.exists():
        return {}
    decisions: dict[str, dict[str, Any]] = {}
    for _, row in read_jsonl(path):
        if row.get("status") != "done" or row.get("decision") != "keep_customization":
            continue
        for stable_id in row.get("stable_diff_ids") or []:
            decisions[str(stable_id)] = row
    return decisions


def _validate_inputs(root: Path) -> tuple[Path, dict[str, Any], list[dict[str, str]], dict[str, dict[str, Any]], list[tuple[str, str]]]:
    refinement_dir = repo_path(root, REFINEMENT_DIR)
    summary_path = refinement_dir / "summary.json"
    queue_path = refinement_dir / "refinement-queue.csv"
    decisions_path = refinement_dir / "decisions.jsonl"
    if not summary_path.exists() or not queue_path.exists() or not decisions_path.exists():
        raise FileNotFoundError("Missing v8unpack refinement summary, queue, or decisions")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    queue_rows = _read_csv(queue_path)
    decisions_by_item = _decision_by_item(root)
    nested_repo = repo_path(root, NESTED_REPO)
    top = Path(_run_git(nested_repo, ["rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
    if top != nested_repo.resolve():
        raise RuntimeError(f"Nested git top-level mismatch: {top}")
    entries = _name_status(nested_repo)
    expected = int(summary.get("diff", {}).get("refined_diff_paths") or 0)
    if expected and len(entries) != expected:
        raise RuntimeError(f"Refined diff path count mismatch: git={len(entries)} summary={expected}")
    return nested_repo, summary, queue_rows, decisions_by_item, entries


def build_realigned_inventory(root: Path) -> dict[str, Any]:
    nested_repo, summary, queue_rows, decisions_by_item, entries = _validate_inputs(root)
    manual_keep_by_stable_id = _manual_cleanup_decisions_by_stable_id(root)
    status_by_path = {path: status for status, path in entries}
    xml_inventory_path = repo_path(root, "analysis/indexes/diff-inventory.csv")
    previous_rows = _read_csv(xml_inventory_path)
    xml_rows = [row for row in previous_rows if row.get("source") == "clean-rebase"]
    if xml_rows:
        _write_csv(repo_path(root, XML_SUPPORT_PATH), DIFF_INVENTORY_HEADER, xml_rows)
    elif not repo_path(root, XML_SUPPORT_PATH).exists():
        _write_csv(repo_path(root, XML_SUPPORT_PATH), DIFF_INVENTORY_HEADER, [])
    xml_support = _xml_support_map(xml_rows or _read_csv(repo_path(root, XML_SUPPORT_PATH)))

    path_to_refinement: dict[str, dict[str, str]] = {}
    for row in queue_rows:
        for path in _json_list(row.get("current_diff_paths", "[]")):
            path_to_refinement[path] = row

    inventory_rows: list[dict[str, str]] = []
    trace_rows: list[dict[str, str]] = []
    missing_queue_paths: list[str] = []
    for index, (change_type, path) in enumerate(entries, 1):
        refinement = path_to_refinement.get(path)
        if not refinement:
            missing_queue_paths.append(path)
            refinement = {
                "item_id": "",
                "source_first_pass_group_id": "",
                "object_group": path.split("/", 1)[0],
                "object_kind": "Unknown",
                "object_name": "",
                "evidence_type": "unknown",
                "status": "manual_review",
                "decision": "needs_manual_review",
                "risk": "high",
                "feature_hint": "",
            }
        item_id = refinement.get("item_id", "")
        decision = decisions_by_item.get(item_id, {})
        evidence_type = str(decision.get("evidence_type") or refinement.get("evidence_type") or "unknown")
        refinement_status = str(decision.get("status") or refinement.get("status") or "manual_review")
        refinement_decision = str(decision.get("decision") or refinement.get("decision") or "needs_manual_review")
        object_key = _object_key(refinement)
        xml_ref = xml_support.get(object_key, "")
        diff_id = f"V8D-{index:05d}"
        area = _area_for(path, evidence_type)
        feature_id = _feature_id_for(refinement, path, area)
        classification = "retained v8unpack candidate"
        confidence = "low" if refinement_status == "manual_review" or evidence_type in {"binary", "unknown"} else "medium"
        row_status = "requires_1c_review" if refinement_status == "manual_review" else "retained_candidate"
        stable_id = stable_diff_id_for_row({"source": CANONICAL_SOURCE, "change_type": change_type, "path": path})
        manual_keep = manual_keep_by_stable_id.get(stable_id) if refinement_status == "manual_review" else None
        if manual_keep:
            row_status = "retained_candidate"
            confidence = "medium"
        evidence_ref = f"{NESTED_REPO}#{path}"
        notes = "; ".join(
            item
            for item in [
                f"source_first_pass_group_id={refinement.get('source_first_pass_group_id', '')}",
                f"refinement_item_id={item_id}",
                f"object_group={refinement.get('object_group', '')}",
                f"evidence_type={evidence_type}",
                f"refinement_status={refinement_status}",
                f"refinement_decision={refinement_decision}",
                "canonical_source=v8unpack-refinement",
                f"xml_support_ref={xml_ref}" if xml_ref else "",
                f"manual_cleanup_decision={manual_keep.get('row_id', '')}:keep_customization" if manual_keep else "",
            ]
            if item
        )
        inventory_rows.append(
            {
                "diff_id": diff_id,
                "source": CANONICAL_SOURCE,
                "change_type": change_type,
                "path": path,
                "object_kind": refinement.get("object_kind", ""),
                "object_name": object_key,
                "area": area,
                "feature_id": feature_id,
                "classification": classification,
                "confidence": confidence,
                "status": row_status,
                "summary": _summary_for(refinement, path),
                "evidence_ref": evidence_ref,
                "notes": notes,
            }
        )
        trace_rows.append(
            {
                "diff_id": diff_id,
                "canonical_path": path,
                "change_type": change_type,
                "source_first_pass_group_id": refinement.get("source_first_pass_group_id", ""),
                "refinement_item_id": item_id,
                "object_group": refinement.get("object_group", ""),
                "object_kind": refinement.get("object_kind", ""),
                "object_name": refinement.get("object_name", ""),
                "evidence_type": evidence_type,
                "refinement_status": refinement_status,
                "refinement_decision": refinement_decision,
                "risk": str(decision.get("risk") or refinement.get("risk") or ""),
                "feature_hint": str(decision.get("feature_hint") or refinement.get("feature_hint") or ""),
                "canonical_evidence_ref": evidence_ref,
                "xml_support_ref": xml_ref,
            }
        )

    _write_csv(xml_inventory_path, DIFF_INVENTORY_HEADER, inventory_rows)
    _write_csv(repo_path(root, TRACE_PATH), TRACE_HEADER, trace_rows)

    commits = {
        "vendor_baseline": _run_git(nested_repo, ["rev-parse", VENDOR_TAG]).stdout.strip(),
        "target_cf": _run_git(nested_repo, ["rev-parse", TARGET_TAG]).stdout.strip(),
        "manual_cleanup": _run_git(nested_repo, ["rev-parse", "manual-cleanup"]).stdout.strip(),
        "v8unpack_refinement": _run_git(nested_repo, ["rev-parse", REFINEMENT_BRANCH]).stdout.strip(),
    }
    evidence_counts = Counter(row["evidence_type"] for row in trace_rows)
    status_counts = Counter(row["refinement_status"] for row in trace_rows)
    decision_counts = Counter(row["refinement_decision"] for row in trace_rows)
    form_template_mixed = sum(
        1
        for row in trace_rows
        if row["evidence_type"] in {"form", "template-content", "mixed", "binary", "predefined-data"}
        or _area_for(row["canonical_path"], row["evidence_type"]) in {"form", "template", "binary"}
    )
    xml_support_rows = sum(1 for row in trace_rows if row.get("xml_support_ref"))
    metadata = {
        "schema_version": "autopilot-source-alignment/v1",
        "generated_at": utc_now_iso(),
        "canonical_source": CANONICAL_SOURCE,
        "configuration_profile": {
            "form_model": FORM_MODEL_ORDINARY if project_uses_ordinary_forms(root) else "",
            "ordinary_forms": project_uses_ordinary_forms(root),
        },
        "inventory_policy": "analysis/indexes/diff-inventory.csv is regenerated directly from v8unpack-refinement; XML clean-rebase is retained only as support.",
        "inventory_path": "analysis/indexes/diff-inventory.csv",
        "trace_path": TRACE_PATH,
        "xml_support_path": XML_SUPPORT_PATH,
        "refinement_summary_path": f"{REFINEMENT_DIR}/summary.json",
        "nested_repo": NESTED_REPO,
        "raw_tag_commits": {
            "vendor_baseline": commits["vendor_baseline"],
            "target_cf": commits["target_cf"],
        },
        "branch_commits": {
            "manual_cleanup": commits["manual_cleanup"],
            "v8unpack_refinement": commits["v8unpack_refinement"],
        },
        "diff_command": f"git -C {NESTED_REPO} diff --name-status --find-renames {VENDOR_TAG}..{REFINEMENT_BRANCH}",
        "counts": {
            "canonical_v8unpack_rows": len(inventory_rows),
            "trace_rows": len(trace_rows),
            "git_name_status_rows": len(entries),
            "refinement_queue_rows": len(queue_rows),
            "refinement_decisions": len(decisions_by_item),
            "xml_clean_rebase_rows": len(xml_rows or _read_csv(repo_path(root, XML_SUPPORT_PATH))),
            "xml_support_rows": xml_support_rows,
            "form_template_mixed_rows": form_template_mixed,
            "manual_review_rows": status_counts.get("manual_review", 0),
            "missing_queue_paths": len(missing_queue_paths),
        },
        "counts_by_evidence_type": dict(sorted(evidence_counts.items())),
        "counts_by_refinement_status": dict(sorted(status_counts.items())),
        "counts_by_refinement_decision": dict(sorted(decision_counts.items())),
        "source_artifacts": {
            "refinement_queue": f"{REFINEMENT_DIR}/refinement-queue.csv",
            "refinement_decisions": f"{REFINEMENT_DIR}/decisions.jsonl",
            "refinement_summary": f"{REFINEMENT_DIR}/summary.json",
        },
    }
    diff_id_map_rows = write_diff_id_map(
        root,
        inventory_rows,
        nested_repo=nested_repo,
        vendor_ref=VENDOR_TAG,
        target_ref=REFINEMENT_BRANCH,
        first_seen_ref=commits["v8unpack_refinement"],
        last_seen_ref=commits["v8unpack_refinement"],
    )
    metadata["diff_id_map_path"] = DIFF_ID_MAP_PATH
    metadata["counts"]["diff_id_map_active_rows"] = sum(1 for row in diff_id_map_rows if row.get("active") == "true")
    metadata["counts"]["diff_id_map_inactive_rows"] = sum(1 for row in diff_id_map_rows if row.get("active") == "false")
    _write_json(repo_path(root, METADATA_PATH), metadata)
    _write_json(
        repo_path(root, SUPERSEDED_PATH),
        {
            "schema_version": "autopilot-superseded-source/v1",
            "generated_at": metadata["generated_at"],
            "superseded_source": "clean-rebase",
            "superseded_inventory_rows": metadata["counts"]["xml_clean_rebase_rows"],
            "superseded_by": CANONICAL_SOURCE,
            "canonical_inventory": "analysis/indexes/diff-inventory.csv",
            "support_copy": XML_SUPPORT_PATH,
            "reason": "XML/ibcmd clean-rebase does not expose ordinary form internals with enough fidelity for publishable autopilot evidence.",
        },
    )
    return metadata


def validate_source_alignment(root: Path) -> list[str]:
    errors: list[str] = []
    refinement_summary = repo_path(root, f"{REFINEMENT_DIR}/summary.json")
    ordinary_forms = project_uses_ordinary_forms(root)
    if not refinement_summary.exists() and not ordinary_forms:
        return errors
    if ordinary_forms and not refinement_summary.exists():
        return ["project.toml declares ordinary forms, but analysis/v8unpack-refinement/summary.json is missing"]
    if refinement_summary.exists() and not ordinary_forms:
        errors.append("analysis/v8unpack-refinement exists, but project.toml lacks [configuration] form_model = \"ordinary\" / ordinary_forms = true")
    metadata_path = repo_path(root, METADATA_PATH)
    if not metadata_path.exists():
        errors.append(f"Missing source alignment metadata: {METADATA_PATH}")
        return errors
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("canonical_source") != CANONICAL_SOURCE:
        errors.append(f"{METADATA_PATH} canonical_source is not {CANONICAL_SOURCE}")
    if ordinary_forms and not metadata.get("configuration_profile", {}).get("ordinary_forms"):
        errors.append(f"{METADATA_PATH} does not record ordinary_forms=true")
    counts = metadata.get("counts", {})
    inventory_rows = _read_csv(repo_path(root, "analysis/indexes/diff-inventory.csv"))
    trace_rows = _read_csv(repo_path(root, TRACE_PATH))
    if not inventory_rows:
        errors.append("Realigned diff inventory is empty")
    if {row.get("source") for row in inventory_rows} != {CANONICAL_SOURCE}:
        errors.append("analysis/indexes/diff-inventory.csv is not exclusively v8unpack-refinement sourced")
    if len(inventory_rows) != int(counts.get("canonical_v8unpack_rows") or -1):
        errors.append("Source alignment canonical row count does not match diff inventory")
    if len(trace_rows) != len(inventory_rows):
        errors.append("v8unpack trace row count does not match diff inventory")
    diff_id_map_rows = read_stable_csv_rows(repo_path(root, DIFF_ID_MAP_PATH))
    if not diff_id_map_rows:
        errors.append(f"Missing stable diff id map: {DIFF_ID_MAP_PATH}")
    else:
        errors.extend(validate_diff_id_map_rows(diff_id_map_rows))
        active_map = {row.get("stable_diff_id", ""): row for row in diff_id_map_rows if row.get("active") == "true"}
        expected_ids = {stable_diff_id_for_row(row) for row in inventory_rows}
        if set(active_map) != expected_ids:
            missing = sorted(expected_ids - set(active_map))
            extra = sorted(set(active_map) - expected_ids)
            errors.append(f"Stable diff id map does not match inventory: missing={missing[:5]} extra={extra[:5]}")
        if len(active_map) != len(inventory_rows):
            errors.append("Stable diff id map active row count does not match diff inventory")
    for required in ("canonical_v8unpack_rows", "xml_support_rows", "form_template_mixed_rows", "manual_review_rows"):
        if required not in counts:
            errors.append(f"Source alignment metadata lacks count: {required}")
    summary = json.loads(refinement_summary.read_text(encoding="utf-8"))
    expected = int(summary.get("diff", {}).get("refined_diff_paths") or 0)
    if expected and len(inventory_rows) != expected:
        errors.append(f"Realigned inventory rows {len(inventory_rows)} do not match refined diff paths {expected}")
    current_commits = {}
    nested_repo = repo_path(root, NESTED_REPO)
    if nested_repo.exists():
        current_commits = {
            "vendor_baseline": _run_git(nested_repo, ["rev-parse", VENDOR_TAG]).stdout.strip(),
            "target_cf": _run_git(nested_repo, ["rev-parse", TARGET_TAG]).stdout.strip(),
        }
    for key, value in current_commits.items():
        if metadata.get("raw_tag_commits", {}).get(key) != value:
            errors.append(f"Raw tag commit drift for {key}: metadata={metadata.get('raw_tag_commits', {}).get(key)} current={value}")
    return errors


def assert_source_alignment(root: Path) -> dict[str, Any]:
    errors = validate_source_alignment(root)
    if errors:
        raise RuntimeError("\n".join(errors))
    return json.loads(repo_path(root, METADATA_PATH).read_text(encoding="utf-8"))


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    metadata = build_realigned_inventory(root)
    print(f"canonical_source: {metadata['canonical_source']}")
    for key, value in metadata["counts"].items():
        print(f"{key}: {value}")
    return 0


def smoke_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    metadata = assert_source_alignment(root)
    print(f"source_alignment: ok")
    for key in ("canonical_v8unpack_rows", "xml_support_rows", "form_template_mixed_rows", "manual_review_rows"):
        print(f"{key}: {metadata['counts'].get(key, 0)}")
    return 0


def copy_review_source_alignment(root: Path, output_dir: Path) -> None:
    source = repo_path(root, METADATA_PATH)
    if not source.exists():
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output_dir / "source-alignment.json")
