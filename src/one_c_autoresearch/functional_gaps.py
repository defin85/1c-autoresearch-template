from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .common import read_toml, repo_path, utc_now_iso
from .customization_registry import customization_trace_payload, customizations_for_subject, load_registry_index
from .migration_requirements import canonical_active, load_index as load_migration_requirement_index
from .functional_gap_probes import derive_behavior_probe_requests, evaluate_behavior_probe_requests, load_target_profile
from .stable_diff_ids import DIFF_ID_MAP_PATH, current_to_stable_diff_ids, read_csv_rows as read_stable_csv_rows
from .subject_cards import load_subject_card, read_csv_rows, split_refs, write_csv_rows


FUNCTIONAL_GAP_SCHEMA_VERSION = "functional-gap-card/v2"
FUNCTIONAL_GAP_HYPOTHESES_HEADER = "hypothesis_id,gap_type,status,confidence,summary,evidence_ref,next_check,decision"
FUNCTIONAL_GAP_CHECKS_HEADER = "check_id,check_type,status,source,question,result,blocking"
FUNCTIONAL_GAP_TARGET_FINDINGS_HEADER = "finding_id,finding_type,target_object,target_path,match_basis,confidence,evidence_ref,object_role,functional_relevance,notes"
FUNCTIONAL_GAP_OBJECT_MAPPING_HEADER = "mapping_id,source_object,source_path,target_object,target_path,mapping_type,object_role,scenario_id,coverage_status,is_gap_driver,confidence,decision,notes"
FUNCTIONAL_GAP_BEHAVIOR_PROBES_HEADER = "probe_id,capability_id,source_signal,source_ref,target_profile,profile_status,result,confidence,evidence_ref,finding_id,notes"
FUNCTIONAL_GAP_SCENARIO_HEADER = "scenario_id,scenario,status,standard_mechanism,target_object,evidence_ref,gap_or_limit,next_action,confidence,notes"
FUNCTIONAL_GAP_IMPLEMENTATION_SCOPE_HEADER = "scope_id,scenario_id,source_object,stable_source_key,linked_diff_ids,linked_stable_diff_ids,source_paths,target_objects,transfer_decision,verification,notes"
FUNCTIONAL_GAP_METADATA_REBASE_HEADER = "metadata_object,metadata_kind,top_level_change_type,structural_part_kinds,change_types,linked_final_diff_ids,scenario_votes,selected_scenario_id,confidence_bucket,current_subject_card_slugs,needs_bsl_review,notes"
FUNCTIONAL_GAP_METADATA_REBASE_EXCLUDED_HEADER = "metadata_object,metadata_kind,part_kind,part_name,change_type,final_diff_ids,subject_card_slugs,reason"
FUNCTIONAL_GAP_METADATA_REBASE_CARD_SUPPORT_HEADER = "subject_card_slug,source_objects_count,structural_matches_count,excluded_only_count,rebuild_action,structural_objects,excluded_only_objects,notes"
FUNCTIONAL_GAP_METADATA_REBASE_OBJECT_COVERAGE_HEADER = "metadata_object,metadata_kind,selected_scenario_id,confidence_bucket,current_subject_card_slugs,actual_subject_card_slugs,actual_refs,coverage_status,notes"
FUNCTIONAL_GAP_METADATA_REBASE_UNRESOLVED_HEADER = "metadata_object,metadata_kind,selected_scenario_id,confidence_bucket,current_subject_card_slugs,linked_final_diff_ids,notes"
FUNCTIONAL_GAP_SCENARIO_SOURCE_OBJECT_ISSUES_HEADER = "subject_card_slug,scenario_id,scenario,standard_mechanism,target_object,issue,notes"
STRUCTURAL_METADATA_PART_KINDS = {
    "object",
    "attribute",
    "dimension",
    "resource",
    "tabular_section",
    "tabularsection",
    "table_part",
    "requisite",
}
NONSTRUCTURAL_METADATA_PART_KINDS = {"form", "template", "module", "help", "unknown"}
FUNCTIONAL_GAP_BEHAVIOR_PROBE_RESULTS = {
    "standard_supported",
    "adaptation_candidate",
    "not_supported",
    "needs_profile",
    "needs_runtime_check",
}
FUNCTIONAL_GAP_BEHAVIOR_PROBE_CONFIDENCE = {"high", "medium", "low"}
BEHAVIOR_RESULT_FINDING_TYPES = {
    "standard_supported": "standard_mechanism",
    "adaptation_candidate": "removed_or_changed_mechanism",
    "not_supported": "removed_or_changed_mechanism",
    "needs_runtime_check": "needs_runtime_check",
}
FUNCTIONAL_GAP_INDEX_HEADER = "subject_card_slug,title,status,gap_readiness,hypotheses_count,open_checks_count,gap_card_path,selected_decision,review_notes"
FUNCTIONAL_GAP_COVERAGE_HEADER = "subject_card_slug,gap_card_status,gap_readiness,gap_card_path,selected_decision,open_checks_count,notes"
FUNCTIONAL_GAP_OPEN_QUESTIONS_HEADER = "question_id,subject_card_slug,check_id,question,needed_source,blocking,status,notes"
FUNCTIONAL_GAP_TYPES = {
    "replace_by_standard",
    "adapt",
    "preserve",
    "retire",
    "split",
    "data_migration",
    "business_decision",
}
FUNCTIONAL_GAP_HYPOTHESIS_STATUSES = {"candidate", "supported", "rejected", "selected"}
FUNCTIONAL_GAP_CHECK_TYPES = {
    "subject_card_readiness",
    "target_release_static",
    "target_behavior_static",
    "target_release_rlm",
    "infobase_data",
    "ui_check",
    "analyst_decision",
}
FUNCTIONAL_GAP_CHECK_STATUSES = {"open", "done", "blocked", "not_applicable"}
FUNCTIONAL_GAP_STATUSES = {
    "draft",
    "needs_target_analysis",
    "needs_runtime_check",
    "needs_analyst_decision",
    "needs_reclassification",
    "needs_manual_review",
    "ready_for_review",
    "reviewed",
    "blocked",
    "out_of_scope",
}
FUNCTIONAL_GAP_READINESS = {
    "needs_subject_card_readiness",
    "needs_target_release_source",
    "needs_target_release_check",
    "ready_for_gap_review",
    "reviewed",
}
GAP_TYPE_LABELS = {
    "replace_by_standard": "заменить типовым механизмом",
    "adapt": "адаптировать доработку",
    "preserve": "сохранить без изменений",
    "retire": "вывести из эксплуатации",
    "split": "разделить решение по частям",
    "data_migration": "проверить перенос данных",
    "business_decision": "решение аналитика",
    "undecided": "итоговое решение не выбрано",
}
CHECK_TYPE_LABELS = {
    "subject_card_readiness": "готовность предметной карточки",
    "target_release_static": "статическая проверка целевого релиза",
    "target_behavior_static": "статическая проверка поведения целевого релиза",
    "target_release_rlm": "поиск в исходниках целевого релиза",
    "infobase_data": "проверка данных ИБ",
    "ui_check": "проверка интерфейса 1С",
    "analyst_decision": "решение аналитика",
}
CHECK_STATUS_LABELS = {
    "open": "открыта",
    "done": "закрыта",
    "blocked": "заблокирована",
    "not_applicable": "не требуется",
}
OBJECT_ROLE_LABELS = {
    "core_source_object": "ядро разрыва",
    "supporting_standard_object": "типовая опорная часть",
    "standard_target_object": "типовой объект целевого релиза",
    "target_candidate_object": "кандидат целевого механизма",
    "noise_or_infrastructure": "технический след",
}
FUNCTIONAL_RELEVANCE_LABELS = {
    "direct_standard_support": "прямое типовое покрытие",
    "candidate_only": "только кандидат",
    "gap_driver": "формирует разрыв",
    "technical_noise": "технический шум",
}
TARGET_INSPECTION_CHECK_IDS = {"FGC-0001", "FGC-0007"}
MANUAL_REVIEW_HEADING = "## Ручные заметки аналитика"
DEFAULT_ANALYST_DECISION_TEXT = "Пока не зафиксировано. После проверок целевого релиза нужно выбрать: заменить типовым механизмом, адаптировать, сохранить или вывести из эксплуатации."
READINESS_LABELS = {
    "needs_subject_card_readiness": "нужно довести предметную карточку",
    "needs_target_release_source": "нужен источник целевого релиза",
    "needs_target_release_check": "нужна проверка целевого релиза",
    "ready_for_gap_review": "готово к ревью разрыва",
    "reviewed": "проверено аналитиком",
}


def functional_gap_root(root: Path) -> Path:
    return repo_path(root, "analysis/functional-gaps")


def functional_gap_card_dir(root: Path, slug: str) -> Path:
    return functional_gap_root(root) / "cards" / slug


def first_csv_line(path: Path) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return lines[0] if lines else ""


def non_empty_csv_rows(path: Path) -> list[dict[str, str]]:
    return [row for row in read_csv_rows(path) if any((value or "").strip() for value in row.values())]


def is_non_typical_metadata_row(row: dict[str, str]) -> bool:
    return row.get("status") != "unchanged" or row.get("change_type") not in {"", "unchanged"}


def is_structural_metadata_row(row: dict[str, str]) -> bool:
    if is_role_rights_metadata_row(row):
        return True
    return row.get("part_kind", "").strip().lower() in STRUCTURAL_METADATA_PART_KINDS


def is_role_rights_metadata_row(row: dict[str, str]) -> bool:
    if row.get("metadata_kind") != "Role":
        return False
    part_name = row.get("part_name", "").strip().lower()
    part_path = row.get("part_path", "").strip().lower()
    return part_name == "rights" or part_path.endswith("/ext/rights.xml")


def structural_metadata_part_kind(row: dict[str, str]) -> str:
    if is_role_rights_metadata_row(row):
        return "rights"
    return row.get("part_kind", "")


def is_metadata_rebase_driver_row(row: dict[str, str]) -> bool:
    if not is_non_typical_metadata_row(row) or not is_structural_metadata_row(row):
        return False
    if row.get("part_kind", "").strip().lower() == "object":
        return row.get("change_type") in {"added", "modified", "removed"}
    return True


def metadata_rebase_bucket(votes: dict[str, int]) -> tuple[str, str, bool]:
    if not votes:
        return "", "no_votes", True
    ordered = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
    selected, count = ordered[0]
    total = sum(votes.values())
    ratio = count / total if total else 0
    if len(votes) == 1 or ratio >= 0.7:
        return selected, "clear", False
    if ratio >= 0.5:
        return selected, "weak", True
    return selected, "conflict", True


def scenario_votes_text(votes: dict[str, int]) -> str:
    return ";".join(f"{scenario}:{count}" for scenario, count in sorted(votes.items(), key=lambda item: (-item[1], item[0])))


def current_functional_gap_slugs(root: Path) -> set[str]:
    slugs: set[str] = set()
    for row in non_empty_csv_rows(functional_gap_root(root) / "index.csv"):
        slug = row.get("subject_card_slug", "").strip()
        if slug:
            slugs.add(slug)
    cards_dir = functional_gap_root(root) / "cards"
    if cards_dir.exists():
        slugs.update(path.name for path in cards_dir.iterdir() if path.is_dir())
    return slugs


def excluded_metadata_rebase_rows(root: Path) -> list[dict[str, str]]:
    metadata_rows = non_empty_csv_rows(repo_path(root, "analysis/custom-metadata/index.csv"))
    result: list[dict[str, str]] = []
    for row in metadata_rows:
        if not is_non_typical_metadata_row(row) or is_metadata_rebase_driver_row(row):
            continue
        metadata_object = row.get("metadata_full_name") or ".".join(part for part in (row.get("metadata_kind"), row.get("metadata_name")) if part)
        if not metadata_object:
            continue
        part_kind = row.get("part_kind", "")
        reason = "nonstructural_part" if part_kind.lower() in NONSTRUCTURAL_METADATA_PART_KINDS else "not_structural_driver"
        result.append(
            {
                "metadata_object": metadata_object,
                "metadata_kind": row.get("metadata_kind", ""),
                "part_kind": part_kind,
                "part_name": row.get("part_name", "") or row.get("part_path", ""),
                "change_type": row.get("change_type", ""),
                "final_diff_ids": row.get("final_diff_ids", ""),
                "subject_card_slugs": row.get("subject_card_slugs", ""),
                "reason": reason,
            }
        )
    return result


def build_metadata_rebase_candidates(root: Path) -> list[dict[str, str]]:
    metadata_rows = non_empty_csv_rows(repo_path(root, "analysis/custom-metadata/index.csv"))
    final_diff_ids = {row.get("diff_id", "") for row in non_empty_csv_rows(repo_path(root, "analysis/indexes/final-diff-inventory.csv")) if row.get("diff_id")}
    decisions = [
        row
        for row in non_empty_csv_rows(repo_path(root, "analysis/reverse-map/decisions.csv"))
        if row.get("decision") == "confirmed_in_scenario" and row.get("diff_id") and row.get("scenario_id")
    ]
    scenario_by_diff = {row["diff_id"]: row["scenario_id"] for row in decisions}
    current_slugs = current_functional_gap_slugs(root)
    grouped: dict[str, dict[str, Any]] = {}
    for row in metadata_rows:
        if not is_metadata_rebase_driver_row(row):
            continue
        metadata_object = row.get("metadata_full_name") or ".".join(part for part in (row.get("metadata_kind"), row.get("metadata_name")) if part)
        if not metadata_object:
            continue
        group = grouped.setdefault(
            metadata_object,
            {
                "metadata_object": metadata_object,
                "metadata_kind": row.get("metadata_kind", ""),
                "top_level_change_type": "",
                "structural_part_kinds": set(),
                "change_types": set(),
                "linked_final_diff_ids": set(),
                "subject_card_slugs": set(),
                "unresolved_diff_ids": set(),
            },
        )
        if row.get("part_kind") == "object" and row.get("change_type") in {"added", "modified", "removed"}:
            group["top_level_change_type"] = row.get("change_type", "")
        structural_part_kind = structural_metadata_part_kind(row)
        if structural_part_kind:
            group["structural_part_kinds"].add(structural_part_kind)
        if row.get("change_type"):
            group["change_types"].add(row["change_type"])
        group["linked_final_diff_ids"].update(split_refs(row.get("final_diff_ids", "")))
        group["subject_card_slugs"].update(slug for slug in split_refs(row.get("subject_card_slugs", "")) if not current_slugs or slug in current_slugs)

    result: list[dict[str, str]] = []
    for metadata_object, group in sorted(grouped.items()):
        diff_ids = sorted(group["linked_final_diff_ids"])
        votes: dict[str, int] = {}
        unresolved: list[str] = []
        stale: list[str] = []
        for diff_id in diff_ids:
            if final_diff_ids and diff_id not in final_diff_ids:
                stale.append(diff_id)
            scenario = scenario_by_diff.get(diff_id)
            if scenario:
                votes[scenario] = votes.get(scenario, 0) + 1
            else:
                unresolved.append(diff_id)
        selected, bucket, needs_bsl = metadata_rebase_bucket(votes)
        notes: list[str] = []
        if not diff_ids:
            notes.append("parser_only_no_final_diff_ids")
        if unresolved:
            notes.append("unresolved_diff_ids=" + ";".join(unresolved))
        if stale:
            notes.append("stale_final_diff_ids=" + ";".join(stale))
        result.append(
            {
                "metadata_object": metadata_object,
                "metadata_kind": group["metadata_kind"],
                "top_level_change_type": group["top_level_change_type"],
                "structural_part_kinds": ";".join(sorted(group["structural_part_kinds"])),
                "change_types": ";".join(sorted(group["change_types"])),
                "linked_final_diff_ids": ";".join(diff_ids),
                "scenario_votes": scenario_votes_text(votes),
                "selected_scenario_id": selected,
                "confidence_bucket": bucket,
                "current_subject_card_slugs": ";".join(sorted(group["subject_card_slugs"])),
                "needs_bsl_review": "true" if needs_bsl else "false",
                "notes": "; ".join(notes),
            }
        )
    return result


def write_metadata_rebase_summary(root: Path, rows: list[dict[str, str]], output_dir: Path) -> None:
    bucket_counts: dict[str, int] = {}
    selected_counts: dict[str, int] = {}
    for row in rows:
        bucket_counts[row["confidence_bucket"]] = bucket_counts.get(row["confidence_bucket"], 0) + 1
        if row.get("selected_scenario_id"):
            selected_counts[row["selected_scenario_id"]] = selected_counts.get(row["selected_scenario_id"], 0) + 1
    lines = [
        "# Структурная metadata-first ревизия карты функциональных разрывов",
        "",
        f"- Сформировано: {utc_now_iso()}",
        f"- Структурных объектов метаданных: {len(rows)}",
        f"- Требуют BSL-проверки: {sum(1 for row in rows if row.get('needs_bsl_review') == 'true')}",
        "",
        "## Уверенность",
        "",
    ]
    for bucket in ("clear", "weak", "conflict", "no_votes"):
        lines.append(f"- `{bucket}`: {bucket_counts.get(bucket, 0)}")
    lines.extend(["", "## Ведущие сценарии", ""])
    if selected_counts:
        for scenario, count in sorted(selected_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- `{scenario}`: {count}")
    else:
        lines.append("Нет выбранных сценариев.")
    lines.extend(["", "## Следующий шаг", "", "Использовать `candidates.csv` как вход для ручного переоформления карточек по новым/измененным объектам и реквизитам; формы и макеты из `excluded-parts.csv` не считать самостоятельными драйверами карточек. `weak`, `conflict` и `no_votes` проверять по BSL/исходникам перед изменением карточек."])
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def functional_gap_card_source_objects(card_dir: Path) -> list[str]:
    objects: list[str] = []
    payload_path = card_dir / "gap-card.json"
    if payload_path.exists():
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        source_contour = payload.get("source_contour") if isinstance(payload.get("source_contour"), dict) else {}
        for key in ("core_source_objects", "primary_objects"):
            objects.extend(split_refs(source_contour.get(key, [])))
            objects.extend(split_refs(payload.get(key, [])))
    mapping_path = card_dir / "object-mapping.csv"
    if mapping_path.exists():
        objects.extend(row.get("source_object", "") for row in non_empty_csv_rows(mapping_path))
    return sorted({obj for obj in objects if obj})


def build_metadata_rebase_card_support(root: Path, candidates: list[dict[str, str]], excluded: list[dict[str, str]]) -> list[dict[str, str]]:
    structural_objects = {row.get("metadata_object", "") for row in candidates if row.get("metadata_object")}
    excluded_objects = {row.get("metadata_object", "") for row in excluded if row.get("metadata_object")}
    result: list[dict[str, str]] = []
    cards_dir = functional_gap_root(root) / "cards"
    if not cards_dir.exists():
        return result
    for card_dir in sorted(path for path in cards_dir.iterdir() if path.is_dir()):
        source_objects = functional_gap_card_source_objects(card_dir)
        matched = sorted(obj for obj in source_objects if obj in structural_objects)
        excluded_only = sorted(obj for obj in source_objects if obj in excluded_objects and obj not in structural_objects)
        if matched:
            action = "keep_rebuild_from_structural_candidates"
            notes = "Есть структурная опора; формы и макеты оставить только вторичным контекстом."
        elif excluded_only:
            action = "remove_or_merge_form_only"
            notes = "Самостоятельный разрыв не подтвержден структурными метаданными."
        else:
            action = "needs_manual_source_review"
            notes = "В карточке нет прямой связи с текущим структурным отчетом."
        result.append(
            {
                "subject_card_slug": card_dir.name,
                "source_objects_count": str(len(source_objects)),
                "structural_matches_count": str(len(matched)),
                "excluded_only_count": str(len(excluded_only)),
                "rebuild_action": action,
                "structural_objects": ";".join(matched),
                "excluded_only_objects": ";".join(excluded_only),
                "notes": notes,
            }
        )
    return result


def add_metadata_rebase_ref(refs: dict[str, set[str]], candidate_objects: set[str], metadata_object: str, ref: str) -> None:
    metadata_object = metadata_object.strip()
    if metadata_object and metadata_object in candidate_objects:
        refs.setdefault(metadata_object, set()).add(ref)


def metadata_rebase_card_object_refs(root: Path, candidate_objects: set[str]) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for relative, slug_field in (("analysis/subject-cards/contours.csv", "slug"), ("analysis/subject-cards/registry.csv", "slug")):
        path = repo_path(root, relative)
        if not path.exists():
            continue
        for row in non_empty_csv_rows(path):
            slug = row.get(slug_field, "").strip()
            for metadata_object in split_refs(row.get("primary_objects", "")):
                add_metadata_rebase_ref(refs, candidate_objects, metadata_object, f"{relative}:{slug}")
    cards_dir = functional_gap_root(root) / "cards"
    if not cards_dir.exists():
        return refs
    for card_dir in sorted(path for path in cards_dir.iterdir() if path.is_dir()):
        payload_path = card_dir / "gap-card.json"
        if payload_path.exists():
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            source_contour = payload.get("source_contour") if isinstance(payload.get("source_contour"), dict) else {}
            for key in ("primary_objects", "core_source_objects"):
                for metadata_object in split_refs(source_contour.get(key, [])):
                    add_metadata_rebase_ref(refs, candidate_objects, metadata_object, f"gap-card:{card_dir.name}:{key}")
                for metadata_object in split_refs(payload.get(key, [])):
                    add_metadata_rebase_ref(refs, candidate_objects, metadata_object, f"gap-card:{card_dir.name}:{key}")
        for filename, column in (("object-mapping.csv", "source_object"), ("functional-equivalence.csv", "standard_mechanism")):
            path = card_dir / filename
            if not path.exists():
                continue
            for row in non_empty_csv_rows(path):
                add_metadata_rebase_ref(refs, candidate_objects, row.get(column, ""), f"{filename}:{card_dir.name}")
    return refs


def metadata_rebase_ref_slug(ref: str) -> str:
    if ref.startswith("gap-card:"):
        parts = ref.split(":")
        return parts[1] if len(parts) > 1 else ""
    return ref.rsplit(":", 1)[-1] if ":" in ref else ""


def build_metadata_rebase_object_coverage(root: Path, candidates: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    candidate_objects = {row.get("metadata_object", "") for row in candidates if row.get("metadata_object")}
    refs_by_object = metadata_rebase_card_object_refs(root, candidate_objects)
    coverage_rows: list[dict[str, str]] = []
    unresolved_rows: list[dict[str, str]] = []
    for row in candidates:
        metadata_object = row.get("metadata_object", "")
        refs = sorted(refs_by_object.get(metadata_object, set()))
        actual_slugs = sorted({slug for slug in (metadata_rebase_ref_slug(ref) for ref in refs) if slug})
        coverage_status = "covered" if refs else "uncovered"
        notes = "" if refs else "Нет фактической ссылки в contours/registry/gap-card/object-mapping/functional-equivalence."
        coverage_row = {
            "metadata_object": metadata_object,
            "metadata_kind": row.get("metadata_kind", ""),
            "selected_scenario_id": row.get("selected_scenario_id", ""),
            "confidence_bucket": row.get("confidence_bucket", ""),
            "current_subject_card_slugs": row.get("current_subject_card_slugs", ""),
            "actual_subject_card_slugs": ";".join(actual_slugs),
            "actual_refs": ";".join(refs),
            "coverage_status": coverage_status,
            "notes": notes,
        }
        coverage_rows.append(coverage_row)
        if coverage_status == "uncovered":
            unresolved_rows.append(
                {
                    "metadata_object": metadata_object,
                    "metadata_kind": row.get("metadata_kind", ""),
                    "selected_scenario_id": row.get("selected_scenario_id", ""),
                    "confidence_bucket": row.get("confidence_bucket", ""),
                    "current_subject_card_slugs": row.get("current_subject_card_slugs", ""),
                    "linked_final_diff_ids": row.get("linked_final_diff_ids", ""),
                    "notes": notes or row.get("notes", ""),
                }
            )
    return coverage_rows, unresolved_rows


def build_metadata_rebase(root: Path) -> dict[str, Any]:
    output_dir = functional_gap_root(root) / "metadata-rebase"
    rows = build_metadata_rebase_candidates(root)
    excluded_rows = excluded_metadata_rebase_rows(root)
    card_support_rows = build_metadata_rebase_card_support(root, rows, excluded_rows)
    coverage_rows, unresolved_rows = build_metadata_rebase_object_coverage(root, rows)
    write_csv_rows(output_dir / "candidates.csv", FUNCTIONAL_GAP_METADATA_REBASE_HEADER, rows)
    write_csv_rows(output_dir / "excluded-parts.csv", FUNCTIONAL_GAP_METADATA_REBASE_EXCLUDED_HEADER, excluded_rows)
    write_csv_rows(output_dir / "card-support.csv", FUNCTIONAL_GAP_METADATA_REBASE_CARD_SUPPORT_HEADER, card_support_rows)
    write_csv_rows(output_dir / "object-coverage.csv", FUNCTIONAL_GAP_METADATA_REBASE_OBJECT_COVERAGE_HEADER, coverage_rows)
    write_csv_rows(output_dir / "unresolved-candidates.csv", FUNCTIONAL_GAP_METADATA_REBASE_UNRESOLVED_HEADER, unresolved_rows)
    write_metadata_rebase_summary(root, rows, output_dir)
    return {
        "status": "ok",
        "rows": len(rows),
        "excluded_rows": len(excluded_rows),
        "card_support_rows": len(card_support_rows),
        "covered_rows": sum(1 for row in coverage_rows if row.get("coverage_status") == "covered"),
        "unresolved_rows": len(unresolved_rows),
        "needs_bsl_review": sum(1 for row in rows if row.get("needs_bsl_review") == "true"),
        "path": "analysis/functional-gaps/metadata-rebase/candidates.csv",
    }


def metadata_supported_card_slugs(root: Path) -> set[str] | None:
    path = functional_gap_root(root) / "metadata-rebase/card-support.csv"
    if not path.exists():
        return None
    rows = read_csv_rows(path)
    return {
        row.get("subject_card_slug", "").strip()
        for row in rows
        if row.get("subject_card_slug", "").strip()
        and row.get("rebuild_action") == "keep_rebuild_from_structural_candidates"
    }


def load_manifest(root: Path) -> dict[str, Any]:
    path = repo_path(root, "project.toml")
    if not path.exists():
        return {}
    return read_toml(path)


def manifest_value(manifest: dict[str, Any], section: str, key: str) -> str:
    value = manifest.get(section, {}).get(key, "")
    return "" if value is None else str(value).strip()


def target_release_label(manifest: dict[str, Any]) -> str:
    value = manifest_value(manifest, "project", "next_vendor_version")
    if value:
        return value
    return "целевой релиз"


def default_target_profile_id(manifest: dict[str, Any]) -> str:
    configured = manifest_value(manifest, "functional_gap", "target_profile")
    if configured:
        return configured
    return ""


def target_sources(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        "next_vendor_path": manifest_value(manifest, "paths", "next_vendor"),
        "next_vendor_rlm": manifest_value(manifest, "rlm", "next_vendor"),
    }


def subject_card_relative(slug: str) -> str:
    return f"analysis/subject-cards/cards/{slug}/subject-card.json"


def file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_fingerprint(path: Path) -> str:
    if not path.exists():
        return ""
    if path.is_file():
        return file_sha256(path)
    digest = hashlib.sha256()
    file_count = 0
    total_size = 0
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        try:
            stat = child.stat()
        except OSError:
            continue
        file_count += 1
        total_size += stat.st_size
        digest.update(child.relative_to(path).as_posix().encode("utf-8", errors="replace"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(file_sha256(child).encode("ascii"))
    digest.update(f"{file_count}:{total_size}".encode("ascii"))
    return digest.hexdigest()


def target_source_hash(root: Path, sources: dict[str, str]) -> str:
    relative = sources.get("next_vendor_path", "")
    if not relative:
        return ""
    return tree_fingerprint(repo_path(root, relative))


def status_from_readiness(readiness: str) -> str:
    if readiness in {"needs_subject_card_readiness", "needs_target_release_source"}:
        return "blocked"
    if readiness == "ready_for_gap_review":
        return "ready_for_review"
    if readiness == "reviewed":
        return "reviewed"
    return "needs_target_analysis"


def source_ref(root: Path, slug: str, field: str) -> str:
    return f"{subject_card_relative(slug)}#{field}"


def row_by_slug(root: Path, relative: str, slug: str) -> dict[str, str]:
    for row in read_csv_rows(repo_path(root, relative)):
        if row.get("slug") == slug:
            return row
    return {}


def source_contour_snapshot(
    root: Path,
    slug: str,
    payload: dict[str, Any],
    evidence_rows: list[dict[str, str]],
) -> dict[str, Any]:
    registry = row_by_slug(root, "analysis/subject-cards/registry.csv", slug)
    contour = next(
        (
            row
            for row in read_csv_rows(repo_path(root, "analysis/subject-cards/contours.csv"))
            if row.get("slug") == slug and row.get("status") == "accepted"
        ),
        {},
    )
    subject_path = repo_path(root, subject_card_relative(slug))
    primary_objects = (
        split_refs(payload.get("primary_objects", []))
        or split_refs(contour.get("primary_objects", ""))
        or split_refs(registry.get("primary_objects", ""))
    )
    linked_detail_maps = (
        split_refs(payload.get("linked_detail_maps", []))
        or split_refs(contour.get("linked_detail_maps", ""))
        or split_refs(registry.get("linked_detail_maps", ""))
    )
    evidence_refs = split_refs(contour.get("evidence_refs", "")) or split_refs(payload.get("source_artifacts", []))
    evidence_refs.extend(row.get("source_path", "") for row in evidence_rows if row.get("source_path"))
    return {
        "slug": slug,
        "title": str(payload.get("title") or registry.get("title") or contour.get("title") or slug),
        "card_path": subject_card_relative(slug),
        "subject_card_hash": file_sha256(subject_path),
        "registry_title": registry.get("title", ""),
        "registry_status": registry.get("status", ""),
        "accepted_contour_id": contour.get("contour_id", ""),
        "accepted_contour_title": contour.get("title", ""),
        "scenario_summary": contour.get("scenario_summary", "") or str(payload.get("summary") or ""),
        "migration_boundary": contour.get("migration_boundary", "") or str(payload.get("migration_boundary") or payload.get("upgrade_risk") or ""),
        "primary_objects": primary_objects,
        "core_source_objects": primary_objects,
        "linked_detail_maps": linked_detail_maps,
        "evidence_refs": split_refs(";".join(evidence_refs)),
    }


def source_contour_problem(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "source_contour отсутствует"
    if not str(snapshot.get("scenario_summary") or "").strip():
        return "не заполнен scenario_summary"
    if not str(snapshot.get("migration_boundary") or "").strip():
        return "не заполнен migration_boundary"
    if not split_refs(snapshot.get("core_source_objects", [])):
        return "не определены core_source_objects"
    if not str(snapshot.get("accepted_contour_id") or "").strip():
        return "нет accepted contour"
    return ""


def has_open_blocking_subject_gaps(gaps: list[dict[str, str]]) -> bool:
    return any(
        row.get("blocking", "").strip().lower() == "true"
        and row.get("status", "").strip().lower() not in {"closed", "done", "resolved"}
        for row in gaps
    )


def subject_ready_for_gap_pass(payload: dict[str, Any], gaps: list[dict[str, str]]) -> bool:
    status = str(payload.get("status") or "").strip()
    return status in {"ready_for_review", "reviewed"} and not has_open_blocking_subject_gaps(gaps)


def gap_readiness(
    payload: dict[str, Any],
    gaps: list[dict[str, str]],
    sources: dict[str, str],
    contour_problem: str = "",
) -> str:
    if contour_problem:
        return "needs_subject_card_readiness"
    if not subject_ready_for_gap_pass(payload, gaps):
        return "needs_subject_card_readiness"
    if not sources.get("next_vendor_path") and not sources.get("next_vendor_rlm"):
        return "needs_target_release_source"
    return "needs_target_release_check"


def text_blob(payload: dict[str, Any]) -> str:
    parts = [
        payload.get("title", ""),
        payload.get("subject_type", ""),
        payload.get("summary", ""),
        payload.get("key_conclusion", ""),
        payload.get("upgrade_risk", ""),
        payload.get("runtime_data_needed", ""),
    ]
    return "\n".join(str(part or "") for part in parts).lower()


def build_hypotheses(
    root: Path,
    slug: str,
    payload: dict[str, Any],
    target_label: str,
) -> list[dict[str, str]]:
    title = str(payload.get("title") or slug).strip()
    confidence = str(payload.get("confidence") or "medium").strip() or "medium"
    hypotheses: list[dict[str, str]] = [
        {
            "hypothesis_id": "FGH-0001",
            "gap_type": "replace_by_standard",
            "status": "candidate",
            "confidence": "medium",
            "summary": f"Проверить, закрывает ли типовой {target_label} предметную доработку «{title}» без переноса клиентского кода.",
            "evidence_ref": source_ref(root, slug, "key_conclusion"),
            "next_check": "FGC-0001",
            "decision": "",
        },
        {
            "hypothesis_id": "FGH-0002",
            "gap_type": "adapt",
            "status": "candidate",
            "confidence": confidence,
            "summary": "Если типовой механизм целевого релиза не закрывает фактическое поведение, доработку нужно переносить или адаптировать.",
            "evidence_ref": source_ref(root, slug, "upgrade_risk"),
            "next_check": "FGC-0001",
            "decision": "",
        },
        {
            "hypothesis_id": "FGH-0003",
            "gap_type": "preserve",
            "status": "candidate",
            "confidence": "low",
            "summary": "Сохранение без изменения допустимо только после доказательства, что объектная модель и сценарии целевого релиза совместимы.",
            "evidence_ref": source_ref(root, slug, "primary_objects"),
            "next_check": "FGC-0001",
            "decision": "",
        },
        {
            "hypothesis_id": "FGH-0004",
            "gap_type": "business_decision",
            "status": "candidate",
            "confidence": "medium",
            "summary": "Аналитик должен выбрать целевое решение: заменить типовым механизмом, адаптировать, сохранить или вывести из эксплуатации.",
            "evidence_ref": source_ref(root, slug, "summary"),
            "next_check": "FGC-0004",
            "decision": "",
        },
    ]
    if str(payload.get("runtime_data_needed") or "").strip():
        hypotheses.append(
            {
                "hypothesis_id": "FGH-0005",
                "gap_type": "data_migration",
                "status": "candidate",
                "confidence": "medium",
                "summary": "Нужно проверить фактические данные ИБ: есть ли исторические объекты или настройки, которые влияют на перенос.",
                "evidence_ref": source_ref(root, slug, "runtime_data_needed"),
                "next_check": "FGC-0003",
                "decision": "",
            }
        )
    blob = text_blob(payload)
    if str(payload.get("subject_type") or "").strip() == "technical_support" or any(term in blob for term in ("однораз", "устар", "временно")):
        hypotheses.append(
            {
                "hypothesis_id": "FGH-0006",
                "gap_type": "retire",
                "status": "candidate",
                "confidence": "low",
                "summary": "Возможен вывод доработки из эксплуатации, если она была временной или служебной и не нужна в целевом релизе.",
                "evidence_ref": source_ref(root, slug, "summary"),
                "next_check": "FGC-0004",
                "decision": "",
            }
        )
    return hypotheses


def build_required_checks(
    slug: str,
    payload: dict[str, Any],
    gaps: list[dict[str, str]],
    sources: dict[str, str],
    readiness: str,
    target_label: str,
) -> list[dict[str, str]]:
    title = str(payload.get("title") or slug).strip()
    primary_objects = ";".join(split_refs(payload.get("primary_objects", [])))
    checks: list[dict[str, str]] = []
    if readiness == "needs_subject_card_readiness":
        checks.append(
            {
                "check_id": "FGC-0000",
                "check_type": "subject_card_readiness",
                "status": "open",
                "source": f"analysis/subject-cards/cards/{slug}/gaps.csv",
                "question": "Довести предметную карточку до ready_for_review и закрыть блокирующие gaps.",
                "result": "",
                "blocking": "true",
            }
        )
    checks.append(
        {
            "check_id": "FGC-0001",
            "check_type": "target_release_static",
            "status": "open" if sources.get("next_vendor_path") else "blocked",
            "source": sources.get("next_vendor_path") or "project.toml:[paths].next_vendor",
            "question": f"Сравнить объекты «{title}» с типовой конфигурацией {target_label}: {primary_objects or 'объекты указаны в предметной карточке'}.",
            "result": "",
            "blocking": "true",
        }
    )
    checks.append(
        {
            "check_id": "FGC-0002",
            "check_type": "target_release_rlm",
            "status": "open" if sources.get("next_vendor_rlm") else "blocked",
            "source": sources.get("next_vendor_rlm") or "project.toml:[rlm].next_vendor",
            "question": "Проверить через rlm-tools-bsl, есть ли в целевом релизе типовой сценарий или близкий механизм.",
            "result": "",
            "blocking": "true",
        }
    )
    if str(payload.get("runtime_data_needed") or "").strip():
        checks.append(
            {
                "check_id": "FGC-0003",
                "check_type": "infobase_data",
                "status": "open",
                "source": "тестовая ИБ",
                "question": str(payload.get("runtime_data_needed") or "").strip(),
                "result": "",
                "blocking": "false",
            }
        )
    checks.append(
        {
            "check_id": "FGC-0004",
            "check_type": "analyst_decision",
            "status": "open",
            "source": f"analysis/functional-gaps/cards/{slug}/review.md",
            "question": "Зафиксировать итоговое решение по функциональному разрыву после проверок целевого релиза.",
            "result": "",
            "blocking": "true",
        }
    )
    return checks


def ensure_scaffold(root: Path) -> None:
    base = functional_gap_root(root)
    (base / "_templates").mkdir(parents=True, exist_ok=True)
    (base / "cards").mkdir(parents=True, exist_ok=True)
    (base / "profiles").mkdir(parents=True, exist_ok=True)
    readme = base / "README.md"
    readme_text = (
        "# Карта функциональных разрывов\n\n"
        "Этот слой строится поверх `analysis/subject-cards` и обрабатывает одну предметную карточку за проход.\n\n"
        "Базовый цикл:\n\n"
        "```bash\n"
        "python -m one_c_autoresearch functional-gap build --card <slug>\n"
        "python -m one_c_autoresearch functional-gap refresh --card <slug>\n"
        "python -m one_c_autoresearch functional-gap inspect-target --card <slug>\n"
        "python -m one_c_autoresearch functional-gap validate --card <slug>\n"
        "python -m one_c_autoresearch functional-gap map-build\n"
        "```\n\n"
        "`build` создает черновик gap-карточки, `refresh` обновляет generated-поля без перезаписи ручных решений, "
        "`inspect-target` ищет аналоги в целевом релизе, а `validate` проверяет доказательность и открытые вопросы. "
        "Для `build`, `refresh` и `inspect-target` флаг `--force` разрешает принудительную регенерацию ручных или "
        "заблокированных аналитиком полей; без него зафиксированные решения и curated target-analysis не перетираются.\n\n"
        "Назначение слоя: для каждой предметной доработки отдельно зафиксировать гипотезы перехода на целевой релиз, обязательные проверки и решение аналитика.\n"
    )
    if not readme.exists() or "inspect-target" not in readme.read_text(encoding="utf-8"):
        readme.write_text(
            readme_text,
            encoding="utf-8",
            newline="\n",
        )
    template = base / "_templates/gap-card.json"
    template_payload = {
        "schema_version": FUNCTIONAL_GAP_SCHEMA_VERSION,
        "subject_card_slug": "example-subject",
        "title": "Пример функционального разрыва",
        "status": "draft",
        "gap_readiness": "needs_target_release_check",
        "target_release": "целевой релиз",
        "source_subject_card": "analysis/subject-cards/cards/example-subject/subject-card.json",
        "source_subject_card_hash": "",
        "target_source_hash": "",
        "selected_decision": "",
        "selected_decision_summary": "",
        "manual_decision_locked": False,
        "generated_at": "",
        "updated_at": "",
        "inputs": {
            "subject_card_path": "analysis/subject-cards/cards/example-subject/subject-card.json",
            "next_vendor_path": "sources/next_vendor",
            "next_vendor_rlm": "",
        },
        "counts": {
            "hypotheses": 0,
            "checks_open": 0,
            "target_findings": 0,
            "object_mappings": 0,
            "behavior_probes": 0,
        },
        "source_artifacts": [],
        "subject_summary": "",
        "subject_key_conclusion": "",
        "subject_upgrade_risk": "",
        "primary_objects": [],
        "linked_features": [],
        "linked_detail_maps": [],
        "target_sources": {"next_vendor_path": "sources/next_vendor", "next_vendor_rlm": ""},
        "hypotheses": [],
        "required_checks": [],
    }
    if not template.exists() or json.loads(template.read_text(encoding="utf-8")).get("schema_version") != FUNCTIONAL_GAP_SCHEMA_VERSION:
        template.write_text(
            json.dumps(template_payload, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    for relative, header in (
        ("index.csv", FUNCTIONAL_GAP_INDEX_HEADER),
        ("coverage.csv", FUNCTIONAL_GAP_COVERAGE_HEADER),
        ("open-questions.csv", FUNCTIONAL_GAP_OPEN_QUESTIONS_HEADER),
    ):
        path = base / relative
        if not path.exists() or first_csv_line(path) != header:
            write_csv_rows(path, header, [])
    profile_template = base / "_templates/target-profile.toml"
    if not profile_template.exists():
        profile_template.write_text(
            'profile_id = "target"\n'
            'profile_title = "Целевая конфигурация"\n'
            'profile_status = "experimental"\n\n'
            '[target_identity]\n'
            'configuration = ""\n'
            'major_version = ""\n\n'
            '[[capabilities.document_lifecycle_state.standard_evidence]]\n'
            'kind = "source_call"\n'
            'target = "ИмяПроцедурыЗаписиСостояния"\n'
            'context = "типовой механизм записи состояния объекта"\n\n'
            '[capabilities.document_lifecycle_state.confidence_rules]\n'
            'standard_supported = "medium"\n',
            encoding="utf-8",
            newline="\n",
        )


def build_gap_payload(
    root: Path,
    slug: str,
    payload: dict[str, Any],
    evidence_rows: list[dict[str, str]],
    gaps: list[dict[str, str]],
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    sources = target_sources(manifest)
    target_label = target_release_label(manifest)
    source_contour = source_contour_snapshot(root, slug, payload, evidence_rows)
    contour_problem = source_contour_problem(source_contour)
    readiness = gap_readiness(payload, gaps, sources, contour_problem)
    hypotheses = build_hypotheses(root, slug, payload, target_label)
    checks = build_required_checks(slug, payload, gaps, sources, readiness, target_label)
    subject_path = repo_path(root, subject_card_relative(slug))
    now = utc_now_iso()
    status = "needs_reclassification" if contour_problem else status_from_readiness(readiness)
    card_payload = {
        "schema_version": FUNCTIONAL_GAP_SCHEMA_VERSION,
        "subject_card_slug": slug,
        "title": str(payload.get("title") or slug),
        "status": status,
        "gap_readiness": readiness,
        "target_release": target_label,
        "source_subject_card": subject_card_relative(slug),
        "source_subject_card_hash": file_sha256(subject_path),
        "source_contour": source_contour,
        "source_contour_problem": contour_problem,
        "target_source_hash": target_source_hash(root, sources),
        "selected_decision": "",
        "selected_decision_summary": "",
        "manual_decision_locked": False,
        "generated_at": now,
        "updated_at": now,
        "inputs": {
            "subject_card_path": subject_card_relative(slug),
            "next_vendor_path": sources.get("next_vendor_path", ""),
            "next_vendor_rlm": sources.get("next_vendor_rlm", ""),
        },
        "source_artifacts": split_refs(payload.get("source_artifacts", [])),
        "subject_summary": str(payload.get("summary") or ""),
        "subject_key_conclusion": str(payload.get("key_conclusion") or ""),
        "subject_upgrade_risk": str(payload.get("upgrade_risk") or ""),
        "subject_status": str(payload.get("status") or ""),
        "subject_confidence": str(payload.get("confidence") or ""),
        "subject_type": str(payload.get("subject_type") or ""),
        "primary_objects": split_refs(payload.get("primary_objects", [])),
        "linked_features": split_refs(payload.get("linked_features", [])),
        "linked_detail_maps": split_refs(payload.get("linked_detail_maps", [])),
        "target_sources": sources,
        "hypotheses": hypotheses,
        "required_checks": checks,
        "analyst_decision": "",
        "counts": {
            "hypotheses": len(hypotheses),
            "checks_open": sum(1 for row in checks if row.get("status") in {"open", "blocked"}),
            "target_findings": 0,
            "object_mappings": 0,
            "behavior_probes": 0,
        },
    }
    return card_payload, hypotheses, checks


def extract_manual_review_notes(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if MANUAL_REVIEW_HEADING in text:
        return text.split(MANUAL_REVIEW_HEADING, 1)[1].strip()
    generated_markers = (
        "## Вывод предметной карточки",
        "## Гипотезы перехода",
        "## Проверки",
        "## Поведенческие проверки целевого релиза",
        "## Решение аналитика",
    )
    if not any(marker in text for marker in generated_markers):
        return text.strip()
    if "## Решение аналитика" in text:
        decision_text = text.split("## Решение аналитика", 1)[1]
        if "\n## " in decision_text:
            decision_text = decision_text.split("\n## ", 1)[0]
        decision_text = decision_text.strip()
        if decision_text and decision_text != DEFAULT_ANALYST_DECISION_TEXT:
            return decision_text
    return ""


def write_review(path: Path, payload: dict[str, Any], hypotheses: list[dict[str, str]], checks: list[dict[str, str]], manual_notes: str = "") -> None:
    open_checks = [row for row in checks if row.get("status") == "open"]
    blocked_checks = [row for row in checks if row.get("status") == "blocked"]
    readiness = str(payload.get("gap_readiness") or "")
    decision_summary = str(payload.get("selected_decision_summary") or "").strip()
    lines = [
        f"# {payload.get('title')}",
        "",
        f"- Предметная карточка: `{payload.get('source_subject_card')}`",
        f"- Целевой релиз: {payload.get('target_release')}",
        f"- Готовность: {READINESS_LABELS.get(readiness, readiness)}",
        f"- Гипотезы: {len(hypotheses)}",
        f"- Открытые проверки: {len(open_checks)}",
        f"- Заблокированные проверки: {len(blocked_checks)}",
        "",
        "## Вывод предметной карточки",
        "",
        str(payload.get("subject_key_conclusion") or payload.get("subject_summary") or "Не заполнено."),
        "",
        "## Гипотезы перехода",
        "",
    ]
    for row in hypotheses:
        gap_type = row.get("gap_type", "")
        lines.append(f"- `{row['hypothesis_id']}` {GAP_TYPE_LABELS.get(gap_type, gap_type)}: {row['summary']}")
    lines.extend(["", "## Проверки", ""])
    for row in checks:
        check_type = row.get("check_type", "")
        status = row.get("status", "")
        lines.append(f"- `{row['check_id']}` {CHECK_TYPE_LABELS.get(check_type, check_type)} / {CHECK_STATUS_LABELS.get(status, status)}: {row['question']}")
    behavior_probes = non_empty_csv_rows(path.parent / "behavior-probes.csv")
    lines.extend(["", "## Поведенческие проверки целевого релиза", ""])
    if behavior_probes:
        for row in behavior_probes:
            lines.append(
                "- "
                f"`{row.get('probe_id')}` `{row.get('capability_id')}`: {row.get('result')} / "
                f"уверенность: {row.get('confidence') or 'не указана'}; "
                f"профиль: {row.get('target_profile') or 'не указан'}; "
                f"evidence: {row.get('evidence_ref') or 'не найдено'}; "
                f"{row.get('notes') or ''}"
            )
    else:
        lines.append("Поведенческие проверки еще не выполнялись.")
    lines.extend(
        [
            "",
            "## Решение аналитика",
            "",
            decision_summary or DEFAULT_ANALYST_DECISION_TEXT,
            "",
        ]
    )
    if manual_notes.strip():
        lines.extend([MANUAL_REVIEW_HEADING, "", manual_notes.strip(), ""])
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def merge_hypotheses(generated: list[dict[str, str]], existing: list[dict[str, str]]) -> list[dict[str, str]]:
    existing_by_id = {row.get("hypothesis_id", ""): row for row in existing if row.get("hypothesis_id")}
    merged: list[dict[str, str]] = []
    for row in generated:
        current = dict(row)
        previous = existing_by_id.get(row.get("hypothesis_id", ""))
        if previous and (previous.get("status") in {"supported", "rejected", "selected"} or previous.get("decision")):
            for field in ("status", "confidence", "summary", "evidence_ref", "next_check", "decision"):
                if previous.get(field):
                    current[field] = previous[field]
        merged.append(current)
    known_ids = {row.get("hypothesis_id", "") for row in merged}
    for row in existing:
        if row.get("hypothesis_id") and row["hypothesis_id"] not in known_ids:
            merged.append(row)
    return merged


def merge_checks(generated: list[dict[str, str]], existing: list[dict[str, str]]) -> list[dict[str, str]]:
    existing_by_id = {row.get("check_id", ""): row for row in existing if row.get("check_id")}
    merged: list[dict[str, str]] = []
    for row in generated:
        current = dict(row)
        previous = existing_by_id.get(row.get("check_id", ""))
        if previous and (
            previous.get("status") in {"done", "not_applicable"}
            or previous.get("result")
            or previous.get("blocking", "").strip().lower() == "false"
        ):
            for field in ("status", "source", "question", "result", "blocking"):
                if previous.get(field):
                    current[field] = previous[field]
        merged.append(current)
    known_ids = {row.get("check_id", "") for row in merged}
    for row in existing:
        if row.get("check_id") and row["check_id"] not in known_ids:
            merged.append(row)
    return merged


def functional_relevance_for_role(role: str, fallback: str = "") -> str:
    if role == "standard_target_object":
        return "direct_standard_support"
    if role == "target_candidate_object":
        return "candidate_only"
    if role == "core_source_object":
        return "gap_driver"
    if role == "noise_or_infrastructure":
        return "technical_noise"
    return fallback


def normalize_gap_driver(row: dict[str, str]) -> dict[str, str]:
    result = dict(row)
    if result.get("object_role") != "core_source_object" and result.get("is_gap_driver") == "true":
        result["is_gap_driver"] = "false"
    return result


def merge_curated_object_mappings(
    generated: list[dict[str, str]],
    existing: list[dict[str, str]],
) -> list[dict[str, str]]:
    existing_by_source = {row.get("source_object", ""): row for row in existing if row.get("source_object")}
    merged: list[dict[str, str]] = []
    preserved_fields = ("object_role", "scenario_id", "coverage_status", "is_gap_driver", "confidence", "decision", "notes")
    for row in generated:
        current = dict(row)
        previous = existing_by_source.get(row.get("source_object", ""))
        if previous:
            for field in preserved_fields:
                if previous.get(field):
                    current[field] = previous[field]
        merged.append(normalize_gap_driver(current))
    generated_sources = {row.get("source_object", "") for row in generated}
    for row in existing:
        if row.get("source_object") and row["source_object"] not in generated_sources:
            merged.append(normalize_gap_driver(row))
    return merged


def merge_curated_target_findings(
    generated: list[dict[str, str]],
    existing: list[dict[str, str]],
) -> list[dict[str, str]]:
    existing_by_key = {
        (row.get("target_object", ""), row.get("match_basis", "")): row
        for row in existing
        if row.get("target_object") or row.get("match_basis")
    }
    merged: list[dict[str, str]] = []
    for row in generated:
        current = dict(row)
        previous = existing_by_key.get((row.get("target_object", ""), row.get("match_basis", "")))
        if previous:
            for field in ("object_role", "functional_relevance", "confidence", "notes"):
                if previous.get(field):
                    current[field] = previous[field]
        relevance = functional_relevance_for_role(current.get("object_role", ""), current.get("functional_relevance", ""))
        if relevance:
            current["functional_relevance"] = relevance
        merged.append(current)
    return merged


def preserve_manual_payload_fields(generated: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    if not existing:
        return generated
    result = dict(generated)
    for field in ("selected_decision", "selected_decision_summary", "manual_decision_locked", "analyst_decision"):
        if field in existing and existing.get(field) not in (None, ""):
            result[field] = existing[field]
    if existing.get("manual_decision_locked"):
        result["status"] = existing.get("status") or result["status"]
        result["gap_readiness"] = existing.get("gap_readiness") or result["gap_readiness"]
    result["generated_at"] = existing.get("generated_at") or result.get("generated_at")
    result["updated_at"] = utc_now_iso()
    return result


def update_counts(
    payload: dict[str, Any],
    hypotheses: list[dict[str, str]],
    checks: list[dict[str, str]],
    findings: list[dict[str, str]],
    mappings: list[dict[str, str]],
    behavior_probes: list[dict[str, str]] | None = None,
    scenarios: list[dict[str, str]] | None = None,
) -> None:
    behavior_probes = behavior_probes or []
    scenarios = scenarios or []
    payload["counts"] = {
        "hypotheses": len(hypotheses),
        "checks_open": sum(1 for row in checks if row.get("status") in {"open", "blocked"}),
        "target_findings": len(findings),
        "object_mappings": len(mappings),
        "behavior_probes": len(behavior_probes),
        "scenarios": len(scenarios),
    }


def _expand_diff_refs(value: str) -> list[str]:
    result: list[str] = []
    for part in split_refs(value):
        if ".." not in part:
            result.append(part)
            continue
        start, end = part.split("..", 1)
        prefix = start.rstrip("0123456789")
        if not prefix or not end.startswith(prefix):
            result.append(part)
            continue
        try:
            start_num = int(start[len(prefix) :])
            end_num = int(end[len(prefix) :])
        except ValueError:
            result.append(part)
            continue
        width = len(start) - len(prefix)
        step = 1 if end_num >= start_num else -1
        result.extend(f"{prefix}{num:0{width}d}" for num in range(start_num, end_num + step, step))
    return result


def _stable_source_key(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:12] if path else ""


def _diff_inventory_rows(root: Path) -> list[dict[str, str]]:
    rows = read_csv_rows(repo_path(root, "analysis/indexes/final-diff-inventory.csv"))
    if not rows:
        rows = read_csv_rows(repo_path(root, "analysis/indexes/diff-inventory.csv"))
    return rows


def _diff_inventory_by_id(root: Path) -> dict[str, dict[str, str]]:
    rows = _diff_inventory_rows(root)
    return {row.get("diff_id", ""): row for row in rows if row.get("diff_id")}


def _source_object_from_path(source_path: str) -> str:
    parts = Path(source_path).parts
    category_map = {
        "Catalog": "Catalog",
        "CommonModule": "CommonModule",
        "CommonTemplate": "CommonTemplate",
        "DataProcessor": "DataProcessor",
        "Document": "Document",
        "ExchangePlan": "ExchangePlan",
        "InformationRegister": "InformationRegister",
        "Report": "Report",
    }
    for index, part in enumerate(parts[:-1]):
        if part in category_map:
            return f"{category_map[part]}.{parts[index + 1]}"
    return ""


def _diff_rows_for_evidence(evidence: dict[str, str], diff_rows: list[dict[str, str]], diff_by_id: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    linked_rows = [diff_by_id[diff_id] for diff_id in _expand_diff_refs(evidence.get("linked_diff_id", "")) if diff_id in diff_by_id]
    source_path = evidence.get("source_path", "")
    source_object = _source_object_from_path(source_path)
    if source_object and linked_rows and all(row.get("object_name") == source_object for row in linked_rows):
        return linked_rows
    normalized_source = source_path.replace("/repo/", "/repo#")
    exact_rows = [row for row in diff_rows if row.get("evidence_ref") == normalized_source]
    if exact_rows:
        return exact_rows
    if source_object:
        return [row for row in diff_rows if row.get("object_name") == source_object]
    return linked_rows


def build_implementation_scope(root: Path, slug: str, mappings: list[dict[str, str]], scenarios: list[dict[str, str]]) -> list[dict[str, str]]:
    evidence_rows = read_csv_rows(repo_path(root, f"analysis/subject-cards/cards/{slug}/evidence.csv"))
    diff_rows = _diff_inventory_rows(root)
    diff_by_id = _diff_inventory_by_id(root)
    stable_by_current = current_to_stable_diff_ids(root)
    scenario_by_id = {row.get("scenario_id", ""): row for row in scenarios if row.get("scenario_id")}
    mapping_by_source = {row.get("source_object", ""): row for row in mappings if row.get("source_object")}
    rows: list[dict[str, str]] = []
    for evidence in evidence_rows:
        diff_matches = _diff_rows_for_evidence(evidence, diff_rows, diff_by_id)
        if not diff_matches:
            continue
        diff_ids = [row.get("diff_id", "") for row in diff_matches if row.get("diff_id")]
        stable_diff_ids = [stable_by_current[diff_id] for diff_id in diff_ids if diff_id in stable_by_current]
        source_objects = sorted({diff_by_id[diff_id].get("object_name", "") for diff_id in diff_ids if diff_by_id[diff_id].get("object_name")})
        source_paths = sorted({diff_by_id[diff_id].get("evidence_ref", "") for diff_id in diff_ids if diff_by_id[diff_id].get("evidence_ref")})
        scenario_id = ""
        target_objects: list[str] = []
        for source_object in source_objects:
            mapping = mapping_by_source.get(source_object)
            if mapping and not scenario_id:
                scenario_id = mapping.get("scenario_id", "")
            if mapping and mapping.get("target_object"):
                target_objects.append(mapping["target_object"])
        scenario = scenario_by_id.get(scenario_id, {})
        transfer_decision = scenario.get("status") or "needs_gap_decision"
        rows.append(
            {
                "scope_id": f"FGS-{len(rows) + 1:04d}",
                "scenario_id": scenario_id,
                "source_object": ";".join(source_objects),
                "stable_source_key": _stable_source_key(";".join(source_paths) or ";".join(diff_ids)),
                "linked_diff_ids": ";".join(diff_ids),
                "linked_stable_diff_ids": ";".join(stable_diff_ids),
                "source_paths": ";".join(source_paths),
                "target_objects": ";".join(sorted(set(target_objects))),
                "transfer_decision": transfer_decision,
                "verification": scenario.get("next_action", ""),
                "notes": f"subject_evidence={evidence.get('evidence_id', '')}; {evidence.get('claim', '')}",
            }
        )
    return rows


def write_functional_gap_bundle(
    root: Path,
    payload: dict[str, Any],
    hypotheses: list[dict[str, str]],
    checks: list[dict[str, str]],
    target_findings: list[dict[str, str]] | None = None,
    object_mappings: list[dict[str, str]] | None = None,
    behavior_probes: list[dict[str, str]] | None = None,
    scenarios: list[dict[str, str]] | None = None,
    preserve_review: bool = False,
) -> None:
    slug = str(payload["subject_card_slug"])
    card_dir = functional_gap_card_dir(root, slug)
    card_dir.mkdir(parents=True, exist_ok=True)
    target_findings = target_findings or []
    object_mappings = object_mappings or []
    behavior_probes = behavior_probes or []
    scenarios = scenarios if scenarios is not None else build_functional_equivalence_rows(object_mappings)
    payload["hypotheses"] = hypotheses
    payload["required_checks"] = checks
    registry = load_registry_index(root)
    payload["semantic_customizations"] = customization_trace_payload(
        root,
        [str(item.get("customization_id") or "") for item in customizations_for_subject(root, slug, registry)],
        registry,
    )
    update_counts(payload, hypotheses, checks, target_findings, object_mappings, behavior_probes, scenarios)
    (card_dir / "gap-card.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    write_csv_rows(card_dir / "hypotheses.csv", FUNCTIONAL_GAP_HYPOTHESES_HEADER, hypotheses)
    write_csv_rows(card_dir / "checks.csv", FUNCTIONAL_GAP_CHECKS_HEADER, checks)
    write_csv_rows(card_dir / "target-findings.csv", FUNCTIONAL_GAP_TARGET_FINDINGS_HEADER, target_findings)
    write_csv_rows(card_dir / "object-mapping.csv", FUNCTIONAL_GAP_OBJECT_MAPPING_HEADER, object_mappings)
    write_csv_rows(card_dir / "behavior-probes.csv", FUNCTIONAL_GAP_BEHAVIOR_PROBES_HEADER, behavior_probes)
    write_csv_rows(card_dir / "functional-equivalence.csv", FUNCTIONAL_GAP_SCENARIO_HEADER, scenarios)
    write_csv_rows(card_dir / "implementation-scope.csv", FUNCTIONAL_GAP_IMPLEMENTATION_SCOPE_HEADER, build_implementation_scope(root, slug, object_mappings, scenarios))
    review_path = card_dir / "review.md"
    manual_notes = extract_manual_review_notes(review_path) if preserve_review else ""
    write_review(review_path, payload, hypotheses, checks, manual_notes=manual_notes)


def refresh_index(root: Path) -> dict[str, Any]:
    ensure_scaffold(root)
    supported_slugs = metadata_supported_card_slugs(root)
    rows: list[dict[str, str]] = []
    coverage_rows: list[dict[str, str]] = []
    open_question_rows: list[dict[str, str]] = []
    for path in sorted((functional_gap_root(root) / "cards").glob("*/gap-card.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        card_dir = path.parent
        checks = non_empty_csv_rows(card_dir / "checks.csv")
        open_checks = [row for row in checks if row.get("status") in {"open", "blocked"}]
        slug = str(payload.get("subject_card_slug") or card_dir.name)
        if supported_slugs is not None and slug not in supported_slugs:
            continue
        gap_card_path = path.relative_to(root).as_posix()
        rows.append(
            {
                "subject_card_slug": slug,
                "title": str(payload.get("title") or slug),
                "status": str(payload.get("status") or ""),
                "gap_readiness": str(payload.get("gap_readiness") or ""),
                "hypotheses_count": str(len(payload.get("hypotheses") or [])),
                "open_checks_count": str(len(open_checks)),
                "gap_card_path": gap_card_path,
                "selected_decision": str(payload.get("selected_decision") or ""),
                "review_notes": "",
            }
        )
        coverage_rows.append(
            {
                "subject_card_slug": slug,
                "gap_card_status": str(payload.get("status") or ""),
                "gap_readiness": str(payload.get("gap_readiness") or ""),
                "gap_card_path": gap_card_path,
                "selected_decision": str(payload.get("selected_decision") or ""),
                "open_checks_count": str(len(open_checks)),
                "notes": "",
            }
        )
        for index, check in enumerate(open_checks, 1):
            open_question_rows.append(
                {
                    "question_id": f"FGQ-{len(open_question_rows) + 1:04d}",
                    "subject_card_slug": slug,
                    "check_id": check.get("check_id", ""),
                    "question": check.get("question", ""),
                    "needed_source": check.get("source", ""),
                    "blocking": check.get("blocking", ""),
                    "status": check.get("status", ""),
                    "notes": check.get("result", ""),
                }
            )
    write_csv_rows(functional_gap_root(root) / "index.csv", FUNCTIONAL_GAP_INDEX_HEADER, rows)
    write_csv_rows(functional_gap_root(root) / "coverage.csv", FUNCTIONAL_GAP_COVERAGE_HEADER, coverage_rows)
    write_csv_rows(functional_gap_root(root) / "open-questions.csv", FUNCTIONAL_GAP_OPEN_QUESTIONS_HEADER, open_question_rows)
    return {"cards": len(rows), "index_path": (functional_gap_root(root) / "index.csv").relative_to(root).as_posix()}


def existing_gap_payload(root: Path, card: str) -> dict[str, Any] | None:
    path = functional_gap_card_dir(root, card) / "gap-card.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def target_inspection_is_stale(existing: dict[str, Any] | None, generated: dict[str, Any]) -> bool:
    if not existing:
        return False
    existing_inputs = existing.get("inputs") if isinstance(existing.get("inputs"), dict) else {}
    generated_inputs = generated.get("inputs") if isinstance(generated.get("inputs"), dict) else {}
    return (
        str(existing.get("target_source_hash") or "") != str(generated.get("target_source_hash") or "")
        or str(existing_inputs.get("next_vendor_path") or "") != str(generated_inputs.get("next_vendor_path") or "")
    )


def build_or_refresh_functional_gap_card(root: Path, card: str, preserve_manual: bool = True) -> dict[str, Any]:
    root = root.resolve()
    if not card:
        raise ValueError("--card is required; functional-gap build processes exactly one subject card per pass")
    ensure_scaffold(root)
    card_dir = functional_gap_card_dir(root, card)
    existing_payload = existing_gap_payload(root, card)
    existing_hypotheses = non_empty_csv_rows(card_dir / "hypotheses.csv")
    existing_checks = non_empty_csv_rows(card_dir / "checks.csv")
    existing_findings = non_empty_csv_rows(card_dir / "target-findings.csv")
    existing_mappings = non_empty_csv_rows(card_dir / "object-mapping.csv")
    existing_behavior_probes = non_empty_csv_rows(card_dir / "behavior-probes.csv")
    payload, evidence_rows, gaps = load_subject_card(root, card)
    manifest = load_manifest(root)
    gap_payload, hypotheses, checks = build_gap_payload(root, card, payload, evidence_rows, gaps, manifest)
    target_inspection_stale = target_inspection_is_stale(existing_payload, gap_payload)
    if target_inspection_stale:
        existing_findings = []
        existing_mappings = []
        existing_behavior_probes = []
        existing_checks = [row for row in existing_checks if row.get("check_id") not in TARGET_INSPECTION_CHECK_IDS]
    if preserve_manual:
        gap_payload = preserve_manual_payload_fields(gap_payload, existing_payload)
        hypotheses = merge_hypotheses(hypotheses, existing_hypotheses)
        checks = merge_checks(checks, existing_checks)
    write_functional_gap_bundle(
        root,
        gap_payload,
        hypotheses,
        checks,
        target_findings=existing_findings,
        object_mappings=existing_mappings,
        behavior_probes=existing_behavior_probes,
        preserve_review=bool(preserve_manual and (card_dir / "review.md").exists()),
    )
    refresh_index(root)
    return {
        "status": "ok",
        "card": card,
        "gap_readiness": gap_payload["gap_readiness"],
        "hypotheses": len(hypotheses),
        "checks": len(checks),
        "path": (functional_gap_card_dir(root, card) / "gap-card.json").relative_to(root).as_posix(),
    }


def build_functional_gap_card(root: Path, card: str, force: bool = False) -> dict[str, Any]:
    return build_or_refresh_functional_gap_card(root, card, preserve_manual=not force)


def refresh_functional_gap_card(root: Path, card: str, force: bool = False) -> dict[str, Any]:
    return build_or_refresh_functional_gap_card(root, card, preserve_manual=not force)


OBJECT_REF_RE = re.compile(
    r"\b(?:Документ|Справочник|РегистрСведений|РегистрНакопления|РегистрБухгалтерии|РегистрРасчета|"
    r"БизнесПроцесс|Задача|ПланВидовХарактеристик|ПланСчетов|ПланВидовРасчета|ОбщийМодуль|Отчет|Обработка|"
    r"Catalog|Document|InformationRegister|AccumulationRegister|AccountingRegister|CalculationRegister|"
    r"BusinessProcess|Task|ChartOfCharacteristicTypes|ChartOfAccounts|ChartOfCalculationTypes|CommonModule|"
    r"Report|DataProcessor|Enum|Role|ScheduledJob)\.[A-Za-zА-Яа-яЁё0-9_]+"
)
TARGET_TYPE_DIRS = {
    "Документ": "Documents",
    "Document": "Documents",
    "Справочник": "Catalogs",
    "Catalog": "Catalogs",
    "РегистрСведений": "InformationRegisters",
    "InformationRegister": "InformationRegisters",
    "РегистрНакопления": "AccumulationRegisters",
    "AccumulationRegister": "AccumulationRegisters",
    "РегистрБухгалтерии": "AccountingRegisters",
    "AccountingRegister": "AccountingRegisters",
    "РегистрРасчета": "CalculationRegisters",
    "CalculationRegister": "CalculationRegisters",
    "БизнесПроцесс": "BusinessProcesses",
    "BusinessProcess": "BusinessProcesses",
    "Задача": "Tasks",
    "Task": "Tasks",
    "ОбщийМодуль": "CommonModules",
    "CommonModule": "CommonModules",
    "Отчет": "Reports",
    "Report": "Reports",
    "Обработка": "DataProcessors",
    "DataProcessor": "DataProcessors",
    "Enum": "Enums",
    "Role": "Roles",
    "ScheduledJob": "ScheduledJobs",
}
TARGET_SEARCH_EXTENSIONS = {".xml", ".bsl", ".txt", ".md", ".json"}


def detail_map_candidates(root: Path, ref: str) -> list[Path]:
    value = str(ref or "").strip()
    if not value:
        return []
    path = repo_path(root, value)
    candidates = [path] if path.suffix == ".json" else [path / "detail-map.json"]
    slug = Path(value).name
    candidates.extend(
        [
            repo_path(root, f"analysis/detail-maps/{value}/detail-map.json"),
            repo_path(root, f"analysis/detail-maps/generated/{value}/detail-map.json"),
            repo_path(root, f"analysis/detail-maps/cards/{value}/detail-map.json"),
            repo_path(root, f"analysis/detail-maps/{slug}/detail-map.json"),
            repo_path(root, f"analysis/detail-maps/generated/{slug}/detail-map.json"),
            repo_path(root, f"analysis/detail-maps/cards/{slug}/detail-map.json"),
        ]
    )
    seen: set[str] = set()
    result: list[Path] = []
    for candidate in candidates:
        key = candidate.as_posix()
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def load_linked_detail_maps(root: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    maps: list[dict[str, Any]] = []
    refs = split_refs(payload.get("linked_detail_maps", []))
    refs.extend(ref for ref in split_refs(payload.get("source_artifacts", [])) if "detail-map" in ref)
    seen_paths: set[str] = set()
    for ref in refs:
        for path in detail_map_candidates(root, ref):
            if not path.exists() or path.as_posix() in seen_paths:
                continue
            seen_paths.add(path.as_posix())
            try:
                maps.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            break
    return maps


def collect_subject_object_refs(root: Path, payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    refs.extend(split_refs(payload.get("primary_objects", [])))

    def visit(value: Any) -> None:
        if isinstance(value, str):
            refs.extend(match.group(0) for match in OBJECT_REF_RE.finditer(value))
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)

    visit(payload.get("sections", {}))
    visit(payload.get("source_artifacts", []))
    for detail_map in load_linked_detail_maps(root, payload):
        visit(detail_map)
    seen: set[str] = set()
    result: list[str] = []
    for ref in refs:
        ref = str(ref or "").strip()
        if ref and ref not in seen:
            seen.add(ref)
            result.append(ref)
    return result


def target_inspection_source_refs(
    gap_payload: dict[str, Any],
    subject_payload: dict[str, Any],
    root: Path | None = None,
) -> list[str]:
    source_contour = gap_payload.get("source_contour") if isinstance(gap_payload.get("source_contour"), dict) else {}
    refs = split_refs(source_contour.get("core_source_objects", [])) or split_refs(source_contour.get("primary_objects", []))
    if refs:
        return list(dict.fromkeys(refs))
    return collect_subject_object_refs(root or Path("."), subject_payload)


def object_tail(ref: str) -> str:
    return ref.rsplit(".", 1)[-1].strip()


def object_type(ref: str) -> str:
    return ref.split(".", 1)[0].strip() if "." in ref else ""


def object_role_for_mapping(source_object: str, mapping_type: str) -> str:
    tail = object_tail(source_object).lower()
    if tail in {"id", "json", "obj", "mgr", "html", "xml"}:
        return "noise_or_infrastructure"
    if mapping_type == "no_target_match":
        return "core_source_object"
    if mapping_type == "same_name":
        return "supporting_standard_object"
    return "target_candidate_object"


def coverage_status_for_role(role: str) -> str:
    if role == "supporting_standard_object":
        return "covered"
    if role == "core_source_object":
        return "not_covered"
    if role == "target_candidate_object":
        return "partially_covered"
    return "not_relevant"


def finding_role_and_relevance(finding_type: str, match_basis: str) -> tuple[str, str]:
    if match_basis.startswith("behavior_probe:"):
        if finding_type == "standard_mechanism":
            return "standard_target_object", "direct_standard_support"
        return "target_candidate_object", "candidate_only"
    if finding_type == "same_object":
        return "standard_target_object", "direct_standard_support"
    if finding_type == "no_match":
        return "core_source_object", "gap_driver"
    if finding_type in {"similar_object", "standard_mechanism", "removed_or_changed_mechanism", "needs_runtime_check"}:
        return "target_candidate_object", "candidate_only"
    return "noise_or_infrastructure", "technical_noise"


def scenario_title_for_object(source_object: str) -> str:
    tail = object_tail(source_object)
    if not tail:
        return "Неуказанный объект"
    return f"Проверить функциональное покрытие: {tail}"


def build_functional_equivalence_rows(mappings: list[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, mapping in enumerate(mappings, 1):
        role = mapping.get("object_role", "")
        if role == "noise_or_infrastructure":
            continue
        status = {
            "supporting_standard_object": "standard_setting",
            "core_source_object": "adaptation_required",
            "target_candidate_object": "needs_runtime_check",
        }.get(role, "needs_runtime_check")
        rows.append(
            {
                "scenario_id": f"FGE-{index:04d}",
                "scenario": scenario_title_for_object(mapping.get("source_object", "")),
                "status": status,
                "standard_mechanism": mapping.get("target_object", "") or "не найден",
                "target_object": mapping.get("target_object", ""),
                "evidence_ref": mapping.get("source_path", ""),
                "gap_or_limit": mapping.get("notes", ""),
                "next_action": "Зафиксировать решение аналитика" if status == "standard_setting" else "Проверить функциональный аналог в целевом релизе",
                "confidence": mapping.get("confidence", ""),
                "notes": OBJECT_ROLE_LABELS.get(role, role),
            }
        )
    return rows


def mappings_by_scenario_id(mappings: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    for row in mappings:
        scenario_id = row.get("scenario_id", "").strip()
        if scenario_id:
            result.setdefault(scenario_id, []).append(row)
    return result


def enrich_scenarios_with_mapping_sources(scenarios: list[dict[str, str]], mappings: list[dict[str, str]]) -> list[dict[str, str]]:
    by_scenario = mappings_by_scenario_id(mappings)
    result: list[dict[str, str]] = []
    for row in scenarios:
        enriched = dict(row)
        scenario_mappings = by_scenario.get(row.get("scenario_id", "").strip(), [])
        source_objects = sorted({mapping.get("source_object", "").strip() for mapping in scenario_mappings if mapping.get("source_object", "").strip()})
        source_paths = sorted({mapping.get("source_path", "").strip() for mapping in scenario_mappings if mapping.get("source_path", "").strip()})
        target_objects = sorted({target for mapping in scenario_mappings for target in split_refs(mapping.get("target_object", "")) if target})
        enriched["source_object"] = ";".join(source_objects)
        enriched["source_path"] = ";".join(source_paths)
        enriched["mapping_ids"] = ";".join(mapping.get("mapping_id", "").strip() for mapping in scenario_mappings if mapping.get("mapping_id", "").strip())
        if target_objects and not enriched.get("target_object", "").strip():
            enriched["target_object"] = ";".join(target_objects)
        result.append(enriched)
    return result


def build_scenario_source_object_issues(root: Path) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    cards_root = functional_gap_root(root) / "cards"
    if not cards_root.exists():
        return issues
    for card_dir in sorted(path for path in cards_root.iterdir() if path.is_dir()):
        scenarios = non_empty_csv_rows(card_dir / "functional-equivalence.csv")
        mappings = non_empty_csv_rows(card_dir / "object-mapping.csv")
        by_scenario = mappings_by_scenario_id(mappings)
        for row in scenarios:
            scenario_id = row.get("scenario_id", "").strip()
            matched = by_scenario.get(scenario_id, []) if scenario_id else []
            source_objects = [mapping.get("source_object", "").strip() for mapping in matched if mapping.get("source_object", "").strip()]
            issue = ""
            notes = ""
            if not scenario_id:
                issue = "missing_scenario_id"
                notes = "Сценарий нельзя сопоставить с object-mapping без scenario_id."
            elif not source_objects:
                issue = "missing_source_object_mapping"
                notes = "Для сценария нет source_object в object-mapping.csv."
            if issue:
                issues.append(
                    {
                        "subject_card_slug": card_dir.name,
                        "scenario_id": scenario_id,
                        "scenario": row.get("scenario", ""),
                        "standard_mechanism": row.get("standard_mechanism", ""),
                        "target_object": row.get("target_object", ""),
                        "issue": issue,
                        "notes": notes,
                    }
                )
    return issues


def write_scenario_source_object_issues(root: Path) -> list[dict[str, str]]:
    issues = build_scenario_source_object_issues(root)
    write_csv_rows(
        functional_gap_root(root) / "metadata-rebase/scenario-source-object-issues.csv",
        FUNCTIONAL_GAP_SCENARIO_SOURCE_OBJECT_ISSUES_HEADER,
        issues,
    )
    return issues


def text_file_candidates(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in TARGET_SEARCH_EXTENSIONS)


def find_target_object(target_root: Path, ref: str, files: list[Path]) -> tuple[str, Path | None, str, str]:
    type_dir = TARGET_TYPE_DIRS.get(object_type(ref), "")
    tail = object_tail(ref)
    tail_lower = tail.lower()
    if type_dir:
        expected_xml = f"{type_dir}/{tail}.xml".lower()
        expected_dir = f"{type_dir}/{tail}/".lower()
        for path in files:
            relative_lower = path.relative_to(target_root).as_posix().lower()
            if relative_lower == expected_xml or relative_lower.startswith(expected_dir):
                return "same_object", path, f"metadata:{type_dir}/{tail}", "high"
    for path in files:
        relative_lower = path.relative_to(target_root).as_posix().lower()
        if tail_lower and tail_lower in relative_lower:
            return "similar_object", path, f"path:{tail}", "medium"
    # ponytail: content-wide fallback is too expensive on full target-release exports; add an indexed lookup if path matching is not enough.
    return "no_match", None, "not_found", "low"


def update_check_after_target_inspection(checks: list[dict[str, str]], findings: list[dict[str, str]]) -> list[dict[str, str]]:
    matched = sum(1 for row in findings if row.get("finding_type") != "no_match")
    missing = sum(1 for row in findings if row.get("finding_type") == "no_match")
    updated: list[dict[str, str]] = []
    for row in checks:
        current = dict(row)
        if current.get("check_id") == "FGC-0001":
            if findings:
                current["status"] = "done"
                current["result"] = f"Статическая проверка целевого релиза выполнена: найдено совпадений {matched}, без совпадений {missing}."
            else:
                current["status"] = "open"
                current["result"] = "Не найдены объекты предметной карточки для статической проверки целевого релиза; нужно заполнить primary_objects или уточнить evidence."
        updated.append(current)
    return updated


def update_check_after_behavior_probe_inspection(
    checks: list[dict[str, str]],
    behavior_probes: list[dict[str, str]],
    slug: str,
) -> list[dict[str, str]]:
    existing = [row for row in checks if row.get("check_id") != "FGC-0007"]
    results = {row.get("result", "") for row in behavior_probes}
    if not behavior_probes:
        status = "not_applicable"
        result = "Поведенческие признаки в предметной карточке не найдены."
        blocking = "false"
    elif results <= {"standard_supported"}:
        status = "done"
        result = "Статические behavior probes выполнены; все найденные проверки покрыты типовым evidence профиля."
        blocking = "false"
    elif "needs_profile" in results:
        status = "blocked"
        result = "Есть поведенческие проверки без профиля целевой конфигурации."
        blocking = "true"
    else:
        status = "open"
        result = "Статические behavior probes выполнены; часть выводов требуют ревью или runtime-проверки."
        blocking = "false"
    existing.append(
        {
            "check_id": "FGC-0007",
            "check_type": "target_behavior_static",
            "status": status,
            "source": f"analysis/functional-gaps/cards/{slug}/behavior-probes.csv",
            "question": "Проверить функциональные возможности целевого релиза по профилю конфигурации.",
            "result": result,
            "blocking": blocking,
        }
    )
    return existing


def next_finding_id(used_ids: set[str]) -> str:
    index = 1
    while True:
        finding_id = f"FGF-{index:04d}"
        if finding_id not in used_ids:
            used_ids.add(finding_id)
            return finding_id
        index += 1


def append_behavior_findings(findings: list[dict[str, str]], behavior_probes: list[dict[str, str]]) -> list[dict[str, str]]:
    result = [dict(row) for row in findings if not row.get("match_basis", "").startswith("behavior_probe:")]
    used_ids = {row.get("finding_id", "") for row in result if row.get("finding_id")}
    for probe in behavior_probes:
        finding_type = BEHAVIOR_RESULT_FINDING_TYPES.get(probe.get("result", ""))
        if not finding_type:
            probe["finding_id"] = ""
            continue
        finding_id = next_finding_id(used_ids)
        probe["finding_id"] = finding_id
        object_role, functional_relevance = finding_role_and_relevance(finding_type, f"behavior_probe:{probe.get('capability_id', '')}")
        result.append(
            {
                "finding_id": finding_id,
                "finding_type": finding_type,
                "target_object": probe.get("target_profile", ""),
                "target_path": probe.get("evidence_ref", ""),
                "match_basis": f"behavior_probe:{probe.get('capability_id', '')}",
                "confidence": probe.get("confidence", ""),
                "evidence_ref": probe.get("source_ref", ""),
                "object_role": object_role,
                "functional_relevance": functional_relevance,
                "notes": probe.get("notes", ""),
            }
        )
    return result


def target_relative_from_gap_payload(payload: dict[str, Any]) -> str:
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    sources = payload.get("target_sources") if isinstance(payload.get("target_sources"), dict) else inputs
    return str(sources.get("next_vendor_path") or inputs.get("next_vendor_path") or "").strip()


def inspect_target_for_functional_gap(root: Path, card: str, force: bool = False, target_profile: str = "") -> dict[str, Any]:
    root = root.resolve()
    card_dir = functional_gap_card_dir(root, card)
    payload_path = card_dir / "gap-card.json"
    if payload_path.exists():
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        existing_findings = non_empty_csv_rows(card_dir / "target-findings.csv")
        existing_mappings = non_empty_csv_rows(card_dir / "object-mapping.csv")
        existing_behavior_probes = non_empty_csv_rows(card_dir / "behavior-probes.csv")
        if payload.get("manual_decision_locked") and existing_findings and existing_mappings and not force:
            if existing_behavior_probes and not target_profile.strip():
                refresh_index(root)
                return {
                    "status": "ok",
                    "card": card,
                    "target_findings": len(existing_findings),
                    "object_mappings": len(existing_mappings),
                    "behavior_probes": len(existing_behavior_probes),
                }
            manifest = load_manifest(root)
            target_relative = target_relative_from_gap_payload(payload)
            if not target_relative:
                raise ValueError("Не задан project.toml:[paths].next_vendor для inspect-target")
            target_root = repo_path(root, target_relative)
            if not target_root.exists():
                raise FileNotFoundError(f"Нет каталога целевого релиза: {target_relative}")
            subject_payload, evidence_rows, gaps = load_subject_card(root, card)
            files = text_file_candidates(target_root)
            profile_id = target_profile.strip() or default_target_profile_id(manifest)
            profile = load_target_profile(root, profile_id) if profile_id else None
            behavior_requests = derive_behavior_probe_requests(subject_payload, evidence_rows, gaps)
            behavior_probes = evaluate_behavior_probe_requests(root, behavior_requests, profile, files)
            checks = update_check_after_behavior_probe_inspection(non_empty_csv_rows(card_dir / "checks.csv"), behavior_probes, card)
            findings = append_behavior_findings(existing_findings, behavior_probes)
            mappings = merge_curated_object_mappings([], existing_mappings)
            hypotheses = non_empty_csv_rows(card_dir / "hypotheses.csv")
            payload["target_source_hash"] = target_source_hash(root, {"next_vendor_path": target_relative})
            payload["updated_at"] = utc_now_iso()
            write_functional_gap_bundle(
                root,
                payload,
                hypotheses,
                checks,
                target_findings=findings,
                object_mappings=mappings,
                behavior_probes=behavior_probes,
                preserve_review=True,
            )
            refresh_index(root)
            return {
                "status": "ok",
                "card": card,
                "target_findings": len(findings),
                "object_mappings": len(mappings),
                "behavior_probes": len(behavior_probes),
            }
    refresh_functional_gap_card(root, card)
    manifest = load_manifest(root)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    existing_findings = non_empty_csv_rows(card_dir / "target-findings.csv")
    existing_mappings = non_empty_csv_rows(card_dir / "object-mapping.csv")
    target_relative = target_relative_from_gap_payload(payload)
    if not target_relative:
        raise ValueError("Не задан project.toml:[paths].next_vendor для inspect-target")
    target_root = repo_path(root, target_relative)
    if not target_root.exists():
        raise FileNotFoundError(f"Нет каталога целевого релиза: {target_relative}")

    subject_payload, _evidence_rows, _gaps = load_subject_card(root, card)
    refs = target_inspection_source_refs(payload, subject_payload, root)
    files = text_file_candidates(target_root)
    findings: list[dict[str, str]] = []
    mappings: list[dict[str, str]] = []
    for index, ref in enumerate(refs, 1):
        finding_type, target_path, match_basis, confidence = find_target_object(target_root, ref, files)
        relative_target_path = target_path.relative_to(root).as_posix() if target_path else ""
        target_object = ref if finding_type == "same_object" else ""
        mapping_type = "same_name" if finding_type == "same_object" else ("shared_infrastructure" if target_path else "no_target_match")
        object_role = object_role_for_mapping(ref, mapping_type)
        coverage_status = coverage_status_for_role(object_role)
        finding_role, functional_relevance = finding_role_and_relevance(finding_type, match_basis)
        findings.append(
            {
                "finding_id": f"FGF-{index:04d}",
                "finding_type": finding_type,
                "target_object": target_object,
                "target_path": relative_target_path,
                "match_basis": match_basis,
                "confidence": confidence,
                "evidence_ref": source_ref(root, card, "primary_objects"),
                "object_role": finding_role,
                "functional_relevance": functional_relevance,
                "notes": "Совпадение найдено в исходниках целевого релиза." if target_path else "Совпадение в исходниках целевого релиза не найдено.",
            }
        )
        mappings.append(
            {
                "mapping_id": f"FGM-{index:04d}",
                "source_object": ref,
                "source_path": source_ref(root, card, "primary_objects"),
                "target_object": target_object,
                "target_path": relative_target_path,
                "mapping_type": mapping_type,
                "object_role": object_role,
                "scenario_id": f"FGE-{index:04d}" if object_role != "noise_or_infrastructure" else "",
                "coverage_status": coverage_status,
                "is_gap_driver": "true" if object_role == "core_source_object" else "false",
                "confidence": confidence,
                "decision": "",
                "notes": "Найден тот же объект в целевом релизе." if finding_type == "same_object" else ("Найден похожий механизм или инфраструктурная ссылка; это не доказанное соответствие объекта." if target_path else "Нужно проверить, не заменяется ли типовым механизмом с другим именем."),
            }
        )
    checks = update_check_after_target_inspection(non_empty_csv_rows(card_dir / "checks.csv"), findings)
    profile_id = target_profile.strip() or default_target_profile_id(manifest)
    profile = load_target_profile(root, profile_id) if profile_id else None
    behavior_requests = derive_behavior_probe_requests(subject_payload, _evidence_rows, _gaps)
    behavior_probes = evaluate_behavior_probe_requests(root, behavior_requests, profile, files)
    checks = update_check_after_behavior_probe_inspection(checks, behavior_probes, card)
    findings = append_behavior_findings(findings, behavior_probes)
    findings = merge_curated_target_findings(findings, existing_findings)
    mappings = merge_curated_object_mappings(mappings, existing_mappings)
    hypotheses = non_empty_csv_rows(card_dir / "hypotheses.csv")
    payload["target_source_hash"] = target_source_hash(root, {"next_vendor_path": target_relative})
    payload["updated_at"] = utc_now_iso()
    write_functional_gap_bundle(
        root,
        payload,
        hypotheses,
        checks,
        target_findings=findings,
        object_mappings=mappings,
        behavior_probes=behavior_probes,
        preserve_review=True,
    )
    refresh_index(root)
    return {
        "status": "ok",
        "card": card,
        "target_findings": len(findings),
        "object_mappings": len(mappings),
        "behavior_probes": len(behavior_probes),
    }


def validate_functional_gaps(root: Path, card: str = "") -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    base = functional_gap_root(root)
    manifest = load_manifest(root)
    sources = target_sources(manifest)
    expected_next_vendor = sources.get("next_vendor_path", "")
    expected_target_source_hash = target_source_hash(root, sources) if expected_next_vendor else ""
    if not base.exists():
        return {"status": "fail", "cards": 0, "errors": ["Нет analysis/functional-gaps; выполните functional-gap build --card <slug>."]}
    for relative, header in (
        ("index.csv", FUNCTIONAL_GAP_INDEX_HEADER),
        ("coverage.csv", FUNCTIONAL_GAP_COVERAGE_HEADER),
        ("open-questions.csv", FUNCTIONAL_GAP_OPEN_QUESTIONS_HEADER),
        ("_templates/gap-card.json", ""),
    ):
        path = base / relative
        if not path.exists():
            errors.append(f"Нет артефакта functional-gap: {path.relative_to(root).as_posix()}")
            continue
        if header and first_csv_line(path) != header:
            errors.append(f"{path.relative_to(root).as_posix()}: неверный заголовок CSV")
    card_dirs = [functional_gap_card_dir(root, card)] if card else sorted((base / "cards").glob("*"))
    card_dirs = [path for path in card_dirs if path.is_dir()]
    if not card_dirs:
        errors.append("Нет functional-gap карточек.")
    seen: set[str] = set()
    for card_dir in card_dirs:
        payload_path = card_dir / "gap-card.json"
        hypotheses_path = card_dir / "hypotheses.csv"
        checks_path = card_dir / "checks.csv"
        findings_path = card_dir / "target-findings.csv"
        mapping_path = card_dir / "object-mapping.csv"
        behavior_probes_path = card_dir / "behavior-probes.csv"
        scenario_path = card_dir / "functional-equivalence.csv"
        implementation_scope_path = card_dir / "implementation-scope.csv"
        review_path = card_dir / "review.md"
        for path in (payload_path, hypotheses_path, checks_path, findings_path, mapping_path, behavior_probes_path, scenario_path, implementation_scope_path, review_path):
            if not path.exists():
                errors.append(f"Нет артефакта functional-gap: {path.relative_to(root).as_posix()}")
        if first_csv_line(hypotheses_path) != FUNCTIONAL_GAP_HYPOTHESES_HEADER:
            errors.append(f"{hypotheses_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if first_csv_line(checks_path) != FUNCTIONAL_GAP_CHECKS_HEADER:
            errors.append(f"{checks_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if first_csv_line(findings_path) != FUNCTIONAL_GAP_TARGET_FINDINGS_HEADER:
            errors.append(f"{findings_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if first_csv_line(mapping_path) != FUNCTIONAL_GAP_OBJECT_MAPPING_HEADER:
            errors.append(f"{mapping_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if first_csv_line(behavior_probes_path) != FUNCTIONAL_GAP_BEHAVIOR_PROBES_HEADER:
            errors.append(f"{behavior_probes_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if first_csv_line(scenario_path) != FUNCTIONAL_GAP_SCENARIO_HEADER:
            errors.append(f"{scenario_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if first_csv_line(implementation_scope_path) != FUNCTIONAL_GAP_IMPLEMENTATION_SCOPE_HEADER:
            errors.append(f"{implementation_scope_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if not payload_path.exists():
            continue
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"Не удалось прочитать {payload_path.relative_to(root).as_posix()}: {exc}")
            continue
        if payload.get("schema_version") != FUNCTIONAL_GAP_SCHEMA_VERSION:
            errors.append(f"{payload_path.relative_to(root).as_posix()}: неверный schema_version")
        slug = str(payload.get("subject_card_slug") or "").strip()
        if not slug:
            errors.append(f"{payload_path.relative_to(root).as_posix()}: не заполнено subject_card_slug")
        elif slug in seen:
            errors.append(f"Дублируется functional-gap карточка для subject_card_slug={slug}")
        else:
            seen.add(slug)
        if card and slug and slug != card:
            errors.append(f"{payload_path.relative_to(root).as_posix()}: expected subject_card_slug={card}, got {slug}")
        if slug and not repo_path(root, f"analysis/subject-cards/cards/{slug}/subject-card.json").exists():
            errors.append(f"{slug}: нет исходной предметной карточки")
        readiness = str(payload.get("gap_readiness") or "").strip()
        status = str(payload.get("status") or "").strip()
        if status not in FUNCTIONAL_GAP_STATUSES:
            errors.append(f"{slug or card_dir.name}: недопустимый статус functional-gap: {status}")
        if readiness not in FUNCTIONAL_GAP_READINESS:
            errors.append(f"{slug or card_dir.name}: недопустимая готовность functional-gap: {readiness}")
        for field in ("title", "target_release", "source_subject_card", "subject_summary", "hypotheses", "required_checks", "inputs", "counts"):
            value = payload.get(field)
            if value in (None, "", []):
                errors.append(f"{slug or card_dir.name}: не заполнено поле {field}")
        if slug:
            expected_subject_hash = file_sha256(repo_path(root, subject_card_relative(slug)))
            if expected_subject_hash and payload.get("source_subject_card_hash") != expected_subject_hash:
                errors.append(f"{slug}: gap-card устарела относительно subject-card; выполните functional-gap refresh --card {slug}")
            source_contour = payload.get("source_contour") if isinstance(payload.get("source_contour"), dict) else {}
            if not source_contour:
                errors.append(f"{slug}: gap-card не содержит source_contour")
            else:
                expected_contour = source_contour_snapshot(root, slug, json.loads(repo_path(root, subject_card_relative(slug)).read_text(encoding="utf-8")), [],)
                if source_contour.get("subject_card_hash") != expected_subject_hash:
                    errors.append(f"{slug}: source_contour устарел относительно subject-card")
                required_contour_fields = ["slug", "card_path", "scenario_summary", "migration_boundary"]
                if expected_contour.get("accepted_contour_id"):
                    required_contour_fields.append("accepted_contour_id")
                for field in required_contour_fields:
                    if not str(source_contour.get(field) or "").strip():
                        errors.append(f"{slug}: source_contour не содержит {field}")
                if not split_refs(source_contour.get("core_source_objects", [])):
                    errors.append(f"{slug}: source_contour не содержит core_source_objects")
                if source_contour.get("accepted_contour_id") != expected_contour.get("accepted_contour_id"):
                    errors.append(f"{slug}: source_contour устарел относительно accepted contour")
                if split_refs(source_contour.get("core_source_objects", [])) != split_refs(expected_contour.get("core_source_objects", [])):
                    errors.append(f"{slug}: source_contour core_source_objects не совпадает с текущим контуром")
            if payload.get("status") in {"needs_reclassification", "needs_manual_review"} and str(payload.get("selected_decision") or "").strip():
                errors.append(f"{slug}: reclassification/manual-review карточка не должна содержать selected_decision")
        inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        if expected_next_vendor and inputs.get("next_vendor_path") != expected_next_vendor:
            errors.append(f"{slug or card_dir.name}: next_vendor_path устарел; выполните functional-gap refresh --card {slug or card_dir.name}")
        if expected_next_vendor and payload.get("target_source_hash") != expected_target_source_hash:
            errors.append(f"{slug or card_dir.name}: target_source_hash устарел; выполните functional-gap refresh --card {slug or card_dir.name}")
        hypotheses = non_empty_csv_rows(hypotheses_path)
        checks = non_empty_csv_rows(checks_path)
        findings = non_empty_csv_rows(findings_path)
        mappings = non_empty_csv_rows(mapping_path)
        behavior_probes = non_empty_csv_rows(behavior_probes_path)
        scenarios = non_empty_csv_rows(scenario_path)
        implementation_scope = non_empty_csv_rows(implementation_scope_path)
        registry = load_registry_index(root)
        semantic_rows = payload.get("semantic_customizations") if isinstance(payload.get("semantic_customizations"), list) else []
        registry_ids = set(registry["by_id"])
        evidence_by_item = registry["evidence_by_item"]
        if registry["exists"]:
            linked_ids = {item.get("customization_id") for item in customizations_for_subject(root, slug, registry)} if slug else set()
            payload_ids = {str(row.get("customization_id") or "") for row in semantic_rows}
            missing_payload_ids = sorted(linked_ids - payload_ids)
            if missing_payload_ids:
                errors.append(f"{slug or card_dir.name}: gap-card не содержит связанные CUS-* из реестра: {';'.join(missing_payload_ids[:10])}")
        for row in semantic_rows:
            customization_id = str(row.get("customization_id") or "")
            if customization_id not in registry_ids:
                errors.append(f"{slug or card_dir.name}: semantic_customizations ссылается на неизвестный customization_id: {customization_id}")
            elif not evidence_by_item.get(customization_id):
                errors.append(f"{slug or card_dir.name}: customization_id без evidence: {customization_id}")
        diff_ids = {row.get("diff_id", "") for row in read_csv_rows(repo_path(root, "analysis/indexes/final-diff-inventory.csv")) if row.get("diff_id")}
        if not diff_ids:
            diff_ids = {row.get("diff_id", "") for row in read_csv_rows(repo_path(root, "analysis/indexes/diff-inventory.csv")) if row.get("diff_id")}
        diff_ids |= {row.get("row_id", "") for row in read_csv_rows(repo_path(root, "analysis/detailed-register-reverse-review/markup.csv")) if row.get("row_id")}
        stable_rows = read_stable_csv_rows(repo_path(root, DIFF_ID_MAP_PATH))
        stable_by_id = {row.get("stable_diff_id", ""): row for row in stable_rows if row.get("stable_diff_id")}
        active_stable_ids = {sid for sid, row in stable_by_id.items() if row.get("active") == "true"}
        inactive_stable_ids = {sid for sid, row in stable_by_id.items() if row.get("active") == "false"}
        subject_evidence = read_csv_rows(repo_path(root, f"analysis/subject-cards/cards/{slug}/evidence.csv")) if slug else []
        subject_diff_refs = [
            diff_id
            for row in subject_evidence
            for diff_id in _expand_diff_refs(row.get("linked_diff_id", ""))
            if diff_id.startswith("V8D-") and diff_id in diff_ids
        ]
        if subject_diff_refs and not implementation_scope:
            errors.append(f"{slug or card_dir.name}: implementation-scope.csv пустой, хотя subject-card evidence содержит linked_diff_id")
        scope_ids = {row.get("scope_id", "") for row in implementation_scope if row.get("scope_id")}
        if len(scope_ids) != len(implementation_scope):
            errors.append(f"{slug or card_dir.name}: implementation-scope.csv содержит пустые или повторяющиеся scope_id")
        for row in implementation_scope:
            linked = split_refs(row.get("linked_diff_ids", ""))
            linked_stable = split_refs(row.get("linked_stable_diff_ids", ""))
            if not linked:
                errors.append(f"{slug or card_dir.name}: implementation-scope row {row.get('scope_id')} не содержит linked_diff_ids")
            if linked and any(diff_id.startswith("V8D-") for diff_id in linked) and not linked_stable:
                errors.append(
                    f"{slug or card_dir.name}: implementation-scope row {row.get('scope_id')} содержит только V8D-* без linked_stable_diff_ids; обновите карточку"
                )
            missing = [diff_id for diff_id in linked if diff_id not in diff_ids]
            if missing:
                errors.append(f"{slug or card_dir.name}: implementation-scope row {row.get('scope_id')} ссылается на неизвестные diff_id: {';'.join(missing[:10])}")
            missing_stable = [diff_id for diff_id in linked_stable if diff_id not in stable_by_id]
            if missing_stable:
                errors.append(
                    f"{slug or card_dir.name}: implementation-scope row {row.get('scope_id')} ссылается на неизвестные stable_diff_id: {';'.join(missing_stable[:10])}"
                )
            inactive = [diff_id for diff_id in linked_stable if diff_id in inactive_stable_ids]
            if inactive:
                errors.append(
                    f"{slug or card_dir.name}: implementation-scope row {row.get('scope_id')} ссылается на удаленные или переклассифицированные stable_diff_id: {';'.join(inactive[:10])}"
                )
            unresolved_active = [diff_id for diff_id in linked_stable if diff_id not in active_stable_ids and diff_id not in inactive_stable_ids]
            if unresolved_active:
                errors.append(
                    f"{slug or card_dir.name}: implementation-scope row {row.get('scope_id')} не может разрешить stable_diff_id: {';'.join(unresolved_active[:10])}"
                )
            for field in ("stable_source_key", "source_paths", "transfer_decision"):
                if not row.get(field, "").strip():
                    errors.append(f"{slug or card_dir.name}: implementation-scope row {row.get('scope_id')} не содержит {field}")
        hypothesis_ids = {row.get("hypothesis_id", "") for row in hypotheses if row.get("hypothesis_id")}
        check_ids = {row.get("check_id", "") for row in checks if row.get("check_id")}
        if len(hypothesis_ids) != len(hypotheses):
            errors.append(f"{slug or card_dir.name}: hypotheses.csv содержит пустые или повторяющиеся hypothesis_id")
        if len(check_ids) != len(checks):
            errors.append(f"{slug or card_dir.name}: checks.csv содержит пустые или повторяющиеся check_id")
        mapping_ids = {row.get("mapping_id", "") for row in mappings if row.get("mapping_id")}
        finding_ids = {row.get("finding_id", "") for row in findings if row.get("finding_id")}
        if len(finding_ids) != len(findings):
            errors.append(f"{slug or card_dir.name}: target-findings.csv содержит пустые или повторяющиеся finding_id")
        if len(mapping_ids) != len(mappings):
            errors.append(f"{slug or card_dir.name}: object-mapping.csv содержит пустые или повторяющиеся mapping_id")
        scenario_ids = {row.get("scenario_id", "") for row in scenarios if row.get("scenario_id")}
        if len(scenario_ids) != len(scenarios):
            errors.append(f"{slug or card_dir.name}: functional-equivalence.csv содержит пустые или повторяющиеся scenario_id")
        for row in mappings:
            if row.get("object_role") not in OBJECT_ROLE_LABELS:
                errors.append(f"{slug or card_dir.name}: object-mapping.csv содержит недопустимую object_role {row.get('object_role')}")
            if row.get("object_role") == "core_source_object" and row.get("is_gap_driver") != "true":
                errors.append(f"{slug or card_dir.name}: core_source_object должен быть is_gap_driver=true")
            if row.get("object_role") != "core_source_object" and row.get("is_gap_driver") == "true":
                errors.append(f"{slug or card_dir.name}: только core_source_object может быть is_gap_driver=true")
        for row in findings:
            if row.get("object_role") not in OBJECT_ROLE_LABELS:
                errors.append(f"{slug or card_dir.name}: target-findings.csv содержит недопустимую object_role {row.get('object_role')}")
            if row.get("functional_relevance") not in FUNCTIONAL_RELEVANCE_LABELS:
                errors.append(f"{slug or card_dir.name}: target-findings.csv содержит недопустимую functional_relevance {row.get('functional_relevance')}")
        probe_ids = {row.get("probe_id", "") for row in behavior_probes if row.get("probe_id")}
        if len(probe_ids) != len(behavior_probes):
            errors.append(f"{slug or card_dir.name}: behavior-probes.csv содержит пустые или повторяющиеся probe_id")
        for row in behavior_probes:
            result = row.get("result", "")
            if result not in FUNCTIONAL_GAP_BEHAVIOR_PROBE_RESULTS:
                errors.append(f"{slug or card_dir.name}: behavior-probes.csv содержит недопустимый result {result}")
            confidence = row.get("confidence", "")
            if confidence not in FUNCTIONAL_GAP_BEHAVIOR_PROBE_CONFIDENCE:
                errors.append(f"{slug or card_dir.name}: behavior-probes.csv содержит недопустимый confidence {confidence}")
        for row in hypotheses:
            gap_type = row.get("gap_type", "")
            if gap_type not in FUNCTIONAL_GAP_TYPES:
                errors.append(f"{slug or card_dir.name}: недопустимый gap_type {gap_type}")
            hypothesis_status = row.get("status", "")
            if hypothesis_status not in FUNCTIONAL_GAP_HYPOTHESIS_STATUSES:
                errors.append(f"{slug or card_dir.name}: недопустимый статус гипотезы {hypothesis_status}")
            if row.get("next_check") and row["next_check"] not in check_ids:
                errors.append(f"{slug or card_dir.name}: hypothesis {row.get('hypothesis_id')} ссылается на неизвестную проверку {row.get('next_check')}")
        for row in checks:
            check_type = row.get("check_type", "")
            if check_type not in FUNCTIONAL_GAP_CHECK_TYPES:
                errors.append(f"{slug or card_dir.name}: недопустимый check_type {check_type}")
            status = row.get("status", "")
            if status not in FUNCTIONAL_GAP_CHECK_STATUSES:
                errors.append(f"{slug or card_dir.name}: недопустимый статус проверки {status}")
        if payload.get("status") in {"ready_for_review", "reviewed"}:
            decision = str(payload.get("selected_decision") or "").strip()
            if decision not in FUNCTIONAL_GAP_TYPES:
                errors.append(f"{slug or card_dir.name}: для ready/reviewed нужен selected_decision из контролируемого списка")
            blocking_open = [
                row
                for row in checks
                if row.get("blocking", "").strip().lower() == "true" and row.get("status") in {"open", "blocked"}
            ]
            if blocking_open:
                errors.append(f"{slug or card_dir.name}: есть открытые блокирующие проверки")
            if review_path.exists() and "## Решение аналитика" not in review_path.read_text(encoding="utf-8"):
                errors.append(f"{slug or card_dir.name}: review.md должен содержать раздел решения аналитика")
            if not scenarios:
                errors.append(f"{slug or card_dir.name}: для ready/reviewed нужна functional-equivalence.csv со сценарной матрицей")
    return {"status": "ok" if not errors else "fail", "cards": len(card_dirs), "errors": errors}


def decision_bucket(decision: str) -> str:
    if not decision.strip():
        return "undecided"
    if decision in FUNCTIONAL_GAP_TYPES:
        return decision
    return "business_decision"


def load_gap_cards(root: Path) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    cards_root = functional_gap_root(root) / "cards"
    if not cards_root.exists():
        return cards
    supported_slugs = metadata_supported_card_slugs(root)
    for payload_path in sorted(cards_root.glob("*/gap-card.json")):
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Не удалось прочитать {payload_path.relative_to(root).as_posix()}: {exc}") from exc
        card_dir = payload_path.parent
        checks = non_empty_csv_rows(card_dir / "checks.csv")
        hypotheses = non_empty_csv_rows(card_dir / "hypotheses.csv")
        findings = non_empty_csv_rows(card_dir / "target-findings.csv")
        mappings = non_empty_csv_rows(card_dir / "object-mapping.csv")
        scenarios = enrich_scenarios_with_mapping_sources(non_empty_csv_rows(card_dir / "functional-equivalence.csv"), mappings)
        slug = str(payload.get("subject_card_slug") or card_dir.name)
        if supported_slugs is not None and slug not in supported_slugs:
            continue
        open_blocking_checks = [
            row
            for row in checks
            if row.get("blocking", "").strip().lower() == "true" and row.get("status") in {"open", "blocked"}
        ]
        cards.append(
            {
                "subject_card_slug": slug,
                "title": str(payload.get("title") or slug),
                "status": str(payload.get("status") or ""),
                "gap_readiness": str(payload.get("gap_readiness") or ""),
                "selected_decision": str(payload.get("selected_decision") or ""),
                "selected_decision_summary": str(payload.get("selected_decision_summary") or ""),
                "target_release": str(payload.get("target_release") or ""),
                "gap_card_path": payload_path.relative_to(root).as_posix(),
                "review_path": (card_dir / "review.md").relative_to(root).as_posix(),
                "hypotheses_count": len(hypotheses),
                "checks_count": len(checks),
                "open_checks_count": sum(1 for row in checks if row.get("status") in {"open", "blocked"}),
                "open_blocking_checks_count": len(open_blocking_checks),
                "target_findings_count": len(findings),
                "object_mappings_count": len(mappings),
                "target_findings": findings,
                "object_mappings": mappings,
                "scenarios": scenarios,
                "checks": checks,
                "hypotheses": hypotheses,
                "semantic_customizations": payload.get("semantic_customizations") or [],
            }
        )
    return cards


def build_functional_gap_map(root: Path) -> dict[str, Any]:
    root = root.resolve()
    ensure_scaffold(root)
    refresh_index(root)
    manifest = load_manifest(root)
    target_label = target_release_label(manifest)
    cards = load_gap_cards(root)
    if canonical_active(root):
        mrq = load_migration_requirement_index(root)
        by_slug = {str(row.get("source_provenance", {}).get("subject_card_slug") or ""): row for row in mrq["requirements"] if row.get("status") in {"ready_for_review", "approved"}}
        derived = []
        for card in cards:
            requirement = by_slug.get(card["subject_card_slug"])
            if not requirement:
                continue
            derived.append({
                **card,
                "title": requirement["title"],
                "status": requirement["status"],
                "selected_decision": requirement["target_solution"],
                "selected_decision_summary": requirement["residual_gap"],
                "migration_requirement_id": requirement["requirement_id"],
                "migration_requirement_hash": hashlib.sha256(json.dumps(requirement, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                "semantic_customizations": [link["customization_id"] for link in mrq["links_by_req"][requirement["requirement_id"]]],
            })
        cards = derived
    write_scenario_source_object_issues(root)
    output_dir = repo_path(root, "outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "cards_total": len(cards),
        "ready_for_review": sum(1 for card in cards if card.get("status") == "ready_for_review"),
        "reviewed": sum(1 for card in cards if card.get("status") == "reviewed"),
        "open_blocking_checks": sum(int(card.get("open_blocking_checks_count") or 0) for card in cards),
    }
    payload = {
        "target_release": target_label,
        "generated_at": utc_now_iso(),
        "summary": summary,
        "cards": cards,
    }
    (output_dir / "functional-gap-map.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    labels = {
        "undecided": "Доработки без итогового решения",
        "replace_by_standard": "Доработки к замене типовым механизмом",
        "adapt": "Доработки к адаптации",
        "preserve": "Доработки к переносу",
        "retire": "Доработки к исключению",
        "split": "Доработки со смешанным решением",
        "business_decision": "Доработки с бизнес-решением",
        "data_migration": "Доработки с проверкой переноса данных",
    }
    lines = [
        f"# Сводка по переходу на {target_label}",
        "",
        f"- Карточек функциональных разрывов: {summary['cards_total']}",
        f"- Готово к ревью: {summary['ready_for_review']}",
        f"- Отревьюировано: {summary['reviewed']}",
        f"- Открытых блокирующих проверок: {summary['open_blocking_checks']}",
        "",
    ]
    for bucket, title in labels.items():
        bucket_cards = [card for card in cards if decision_bucket(str(card.get("selected_decision") or "")) == bucket]
        lines.extend([f"## {title}", ""])
        if not bucket_cards:
            lines.extend(["Нет карточек.", ""])
            continue
        for card in bucket_cards:
            decision = card.get("selected_decision") or "undecided"
            lines.append(f"- `{card['subject_card_slug']}` {card['title']} - {GAP_TYPE_LABELS.get(str(decision), str(decision))}; статус: {card.get('status') or 'не указан'}; блокирующие проверки: {card.get('open_blocking_checks_count')}.")
        lines.append("")
    open_questions = non_empty_csv_rows(functional_gap_root(root) / "open-questions.csv")
    lines.extend(["## Открытые проверки", ""])
    if open_questions:
        for row in open_questions:
            lines.append(f"- `{row.get('subject_card_slug')}` / `{row.get('check_id')}`: {row.get('question')} ({row.get('status')})")
    else:
        lines.append("Открытых проверок нет.")
    lines.append("")
    (output_dir / "functional-gap-map.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {"status": "ok", "cards": len(cards), "path": "outputs/functional-gap-map.md"}


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = build_functional_gap_card(root, args.card, force=args.force)
    print(f"functional_gap_card: {result['card']}")
    print(f"gap_readiness: {result['gap_readiness']}")
    print(f"hypotheses: {result['hypotheses']}")
    print(f"checks: {result['checks']}")
    print(f"path: {result['path']}")
    return 0


def refresh_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = refresh_functional_gap_card(root, args.card, force=args.force)
    print(f"functional_gap_card: {result['card']}")
    print(f"gap_readiness: {result['gap_readiness']}")
    print(f"hypotheses: {result['hypotheses']}")
    print(f"checks: {result['checks']}")
    print(f"path: {result['path']}")
    return 0


def inspect_target_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = inspect_target_for_functional_gap(root, args.card, force=args.force, target_profile=args.target_profile)
    print(f"functional_gap_card: {result['card']}")
    print(f"target_findings: {result['target_findings']}")
    print(f"object_mappings: {result['object_mappings']}")
    print(f"behavior_probes: {result.get('behavior_probes', 0)}")
    return 0


def map_build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = build_functional_gap_map(root)
    print(f"functional_gap_map: {result['path']}")
    print(f"cards: {result['cards']}")
    return 0


def metadata_rebase_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = build_metadata_rebase(root)
    print(f"metadata_rebase: {result['path']}")
    print(f"rows: {result['rows']}")
    print(f"excluded_rows: {result['excluded_rows']}")
    print(f"card_support_rows: {result['card_support_rows']}")
    print(f"needs_bsl_review: {result['needs_bsl_review']}")
    return 0


def validate_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = validate_functional_gaps(root, card=args.card or "")
    print(f"functional_gap_status: {result['status']}")
    print(f"cards: {result.get('cards', 0)}")
    for error in result["errors"]:
        print(f"- {error}")
    return 0 if result["status"] == "ok" else 1


def status_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    index = refresh_index(root)
    rows = non_empty_csv_rows(functional_gap_root(root) / "index.csv")
    print(f"functional_gap_cards: {index['cards']}")
    for row in rows:
        print(f"- {row.get('subject_card_slug')}: {row.get('gap_readiness')} ({row.get('open_checks_count')} open checks)")
    return 0
