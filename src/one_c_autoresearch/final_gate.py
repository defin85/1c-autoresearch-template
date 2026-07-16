from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .autopilot import (
    DIFF_INVENTORY_HEADER,
    FEATURE_MAP_HEADER,
    FINAL_DIFF_INVENTORY_HEADER,
    INFOBASE_QUESTIONS_HEADER,
    OPEN_QUESTIONS_HEADER,
)
from .common import repo_path
from .reverse_map import REVERSE_MAP_COVERAGE_HEADER, REVERSE_MAP_INFOBASE_CHECKS_HEADER, REVERSE_MAP_UNRESOLVED_HEADER
from .stable_diff_ids import write_diff_id_map
from .v8unpack_autopilot import assert_source_alignment
from .v8unpack_refinement import NESTED_REPO, REFINEMENT_BRANCH, VENDOR_TAG


FINAL_FEATURE_MAP_HEADER = FEATURE_MAP_HEADER

PASS_STATUSES = {"confirmed_in_scenario", "supporting_shared"}
BLOCKING_STATUS_TO_FEATURE_STATUS = {
    "assigned": "requires_1c_review",
    "unreviewed": "requires_1c_review",
    "needs_manual_review": "requires_1c_review",
    "needs_infobase_data": "blocked_by_infobase_data",
    "needs_runtime_verification": "requires_runtime_verification",
    "needs_reclassification": "needs_reclassification",
    "cross_scenario_reclassification": "needs_reclassification",
}
ROW_EXCLUDE_STATUSES = {"technical_noise", "out_of_scope"}
ROW_SUPPORTING_ONLY_STATUSES = {"technical_platform"}
ROW_MOVE_STATUSES = {"belongs_to_other_scenario"}


@dataclass(frozen=True)
class FinalGateResult:
    diff_rows: list[dict[str, str]]
    feature_rows: list[dict[str, str]]
    open_questions: list[dict[str, str]]
    blocking_rows: list[dict[str, str]]
    reassigned_rows: list[dict[str, str]]
    excluded_rows: list[dict[str, str]]
    supporting_only_rows: list[dict[str, str]]

    @property
    def is_clean(self) -> bool:
        return not self.blocking_rows and not self.open_questions

    def summary(self) -> dict[str, int]:
        return {
            "final_diff_rows": len(self.diff_rows),
            "final_feature_rows": len(self.feature_rows),
            "open_questions": len(self.open_questions),
            "blocking_rows": len(self.blocking_rows),
            "reassigned_rows": len(self.reassigned_rows),
            "excluded_rows": len(self.excluded_rows),
            "supporting_only_rows": len(self.supporting_only_rows),
        }


def read_csv_rows(path: Path, expected_header: str | None = None) -> list[dict[str, str]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if expected_header is not None and (not lines or lines[0] != expected_header):
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv_rows(path: Path, header: str, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = header.split(",")
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def final_gate_paths(root: Path) -> dict[str, Path]:
    return {
        "diff_inventory": repo_path(root, "analysis/indexes/diff-inventory.csv"),
        "feature_map": repo_path(root, "analysis/indexes/feature-map.csv"),
        "coverage": repo_path(root, "analysis/reverse-map/coverage.csv"),
        "unresolved": repo_path(root, "analysis/reverse-map/unresolved.csv"),
        "final_diff_inventory": repo_path(root, "analysis/indexes/final-diff-inventory.csv"),
        "final_feature_map": repo_path(root, "analysis/indexes/final-feature-map.csv"),
        "open_questions": repo_path(root, "outputs/open-questions.csv"),
        "infobase_questions": repo_path(root, "outputs/infobase-questions.csv"),
        "infobase_checks": repo_path(root, "analysis/reverse-map/infobase-checks.csv"),
    }


def final_status_for_reverse_status(reverse_status: str, primary_status: str) -> tuple[str, str]:
    if reverse_status in PASS_STATUSES:
        return primary_status or "mapped_to_feature", "accept"
    if reverse_status in BLOCKING_STATUS_TO_FEATURE_STATUS:
        return BLOCKING_STATUS_TO_FEATURE_STATUS[reverse_status], "block"
    if reverse_status in ROW_EXCLUDE_STATUSES:
        return "technical_noise_removed", "exclude"
    if reverse_status in ROW_SUPPORTING_ONLY_STATUSES:
        return "technical_platform", "supporting_only"
    if reverse_status in ROW_MOVE_STATUSES:
        return "belongs_to_other_scenario", "reassign"
    if not reverse_status:
        return "requires_1c_review", "block"
    return "requires_1c_review", "block"


def feature_status_from_rows(primary_status: str, rows: list[dict[str, str]]) -> str:
    statuses = {row.get("final_status", "").strip() for row in rows}
    if "blocked_by_infobase_data" in statuses:
        return "blocked_by_infobase_data"
    if "requires_runtime_verification" in statuses:
        return "requires_runtime_verification"
    if "needs_reclassification" in statuses:
        return "needs_reclassification"
    if "requires_1c_review" in statuses:
        return "requires_1c_review"
    return primary_status or "complete"


def open_question_status(unresolved_status: str) -> str:
    if unresolved_status == "needs_infobase_data":
        return "blocked_by_infobase_data"
    return "open_question"


def covered_infobase_question_ids(infobase_check_rows: list[dict[str, str]]) -> set[str]:
    covered: set[str] = set()
    for row in infobase_check_rows:
        if row.get("status_after_pass", "").strip() != "closed":
            continue
        item_id = row.get("item_id", "").strip()
        if item_id:
            covered.add(item_id)
        question_ref = row.get("question_ref", "").strip()
        if "#" in question_ref:
            covered.add(question_ref.rsplit("#", 1)[-1])
    return covered


def open_question_refs(open_questions: list[dict[str, str]]) -> set[str]:
    refs: set[str] = set()
    for row in open_questions:
        question_id = row.get("question_id", "").strip()
        if question_id:
            refs.add(question_id)
        source_ref = row.get("source_ref", "").strip()
        if "#" in source_ref:
            refs.add(source_ref.rsplit("#", 1)[-1])
    return refs


def build_final_gate_rows(root: Path) -> FinalGateResult:
    paths = final_gate_paths(root)
    diff_rows = read_csv_rows(paths["diff_inventory"], DIFF_INVENTORY_HEADER)
    feature_rows = read_csv_rows(paths["feature_map"], FEATURE_MAP_HEADER)
    coverage_rows = read_csv_rows(paths["coverage"], REVERSE_MAP_COVERAGE_HEADER)
    unresolved_rows = read_csv_rows(paths["unresolved"], REVERSE_MAP_UNRESOLVED_HEADER)
    infobase_question_rows = read_csv_rows(paths["infobase_questions"], INFOBASE_QUESTIONS_HEADER)
    infobase_check_rows = read_csv_rows(paths["infobase_checks"], REVERSE_MAP_INFOBASE_CHECKS_HEADER)

    coverage_by_diff = {row.get("diff_id", "").strip(): row for row in coverage_rows if row.get("diff_id", "").strip()}
    final_diff_rows: list[dict[str, str]] = []
    by_source_feature: dict[str, list[dict[str, str]]] = defaultdict(list)
    blocking_rows: list[dict[str, str]] = []
    reassigned_rows: list[dict[str, str]] = []
    excluded_rows: list[dict[str, str]] = []
    supporting_only_rows: list[dict[str, str]] = []

    for row in diff_rows:
        diff_id = row.get("diff_id", "").strip()
        coverage = coverage_by_diff.get(diff_id, {})
        reverse_status = coverage.get("status", "").strip()
        final_status, final_action = final_status_for_reverse_status(reverse_status, row.get("status", "").strip())
        source_feature = row.get("feature_id", "").strip()
        reverse_scenario = coverage.get("scenario_id", "").strip()
        final_feature = reverse_scenario if final_action == "reassign" and reverse_scenario else source_feature
        reason = coverage.get("notes", "").strip() or coverage.get("decision_ref", "").strip()
        final_row = {
            **row,
            "reverse_status": reverse_status,
            "reverse_confidence": coverage.get("confidence", "").strip(),
            "reverse_scenario_id": reverse_scenario,
            "final_feature_id": final_feature,
            "final_status": final_status,
            "final_action": final_action,
            "blocking_reason": reason,
        }
        final_diff_rows.append(final_row)
        if source_feature:
            by_source_feature[source_feature].append(final_row)
        if final_action == "block":
            blocking_rows.append(final_row)
        elif final_action == "reassign":
            reassigned_rows.append(final_row)
        elif final_action == "exclude":
            excluded_rows.append(final_row)
        elif final_action == "supporting_only":
            supporting_only_rows.append(final_row)

    feature_counts = Counter(
        row.get("final_feature_id", "").strip()
        for row in final_diff_rows
        if row.get("final_action") in {"accept", "supporting_only"} and row.get("final_feature_id", "").strip()
    )
    final_feature_rows: list[dict[str, str]] = []
    for row in feature_rows:
        feature_id = row.get("feature_id", "").strip()
        final_row = dict(row)
        scoped_rows = by_source_feature.get(feature_id, [])
        final_row["status"] = feature_status_from_rows(row.get("status", "").strip(), scoped_rows)
        final_row["notes"] = (
            f"Final-gate rows: accepted_or_supporting={feature_counts.get(feature_id, 0)}; "
            f"blocked={sum(1 for item in scoped_rows if item.get('final_action') == 'block')}; "
            f"reassigned={sum(1 for item in scoped_rows if item.get('final_action') == 'reassign')}; "
            f"excluded={sum(1 for item in scoped_rows if item.get('final_action') == 'exclude')}."
        )
        final_feature_rows.append(final_row)

    open_questions: list[dict[str, str]] = []
    for index, row in enumerate(unresolved_rows, 1):
        if not any((value or "").strip() for value in row.values()):
            continue
        item_id = row.get("item_id", "").strip() or f"OQ-{index:04d}"
        open_questions.append(
            {
                "question_id": item_id,
                "feature_id": row.get("scenario_id", "").strip(),
                "status": open_question_status(row.get("status", "").strip()),
                "reason": row.get("reason", "").strip() or row.get("status", "").strip(),
                "closure_method": row.get("needed_input", "").strip() or "Resolve reverse-map unresolved item.",
                "impact": row.get("impact", "").strip() or "Final claim cannot be accepted without this input.",
                "source_ref": f"analysis/reverse-map/unresolved.csv#{item_id}",
                "owner": row.get("owner", "").strip() or "business/1C review",
                "notes": row.get("notes", "").strip(),
            }
        )

    covered_infobase_questions = covered_infobase_question_ids(infobase_check_rows)
    for index, row in enumerate(infobase_question_rows, 1):
        if not any((value or "").strip() for value in row.values()):
            continue
        question_id = row.get("question_id", "").strip() or f"IBQ-{index:04d}"
        if row.get("status", "").strip() == "closed" or question_id in covered_infobase_questions:
            continue
        open_questions.append(
            {
                "question_id": question_id,
                "feature_id": row.get("feature_id", "").strip(),
                "status": "blocked_by_infobase_data",
                "reason": row.get("reason", "").strip() or "Нужна проверка данных ИБ.",
                "closure_method": row.get("closing_result", "").strip() or row.get("check_target", "").strip() or "Закрыть проверкой ИБ и записать результат в analysis/reverse-map/infobase-checks.csv.",
                "impact": row.get("risk_if_open", "").strip() or "Нельзя подтвердить финальный вывод без данных ИБ.",
                "source_ref": f"outputs/infobase-questions.csv#{question_id}",
                "owner": "business/1C review",
                "notes": row.get("object_or_setting", "").strip(),
            }
        )

    return FinalGateResult(
        diff_rows=final_diff_rows,
        feature_rows=final_feature_rows,
        open_questions=open_questions,
        blocking_rows=blocking_rows,
        reassigned_rows=reassigned_rows,
        excluded_rows=excluded_rows,
        supporting_only_rows=supporting_only_rows,
    )


def build_final_gate(root: Path) -> FinalGateResult:
    metadata = assert_source_alignment(root)
    refinement_ref = metadata.get("branch_commits", {}).get("v8unpack_refinement", "")
    result = build_final_gate_rows(root)
    paths = final_gate_paths(root)
    write_csv_rows(paths["final_diff_inventory"], FINAL_DIFF_INVENTORY_HEADER, result.diff_rows)
    write_csv_rows(paths["final_feature_map"], FINAL_FEATURE_MAP_HEADER, result.feature_rows)
    write_csv_rows(paths["open_questions"], OPEN_QUESTIONS_HEADER, result.open_questions)
    write_diff_id_map(
        root,
        result.diff_rows,
        nested_repo=repo_path(root, NESTED_REPO),
        vendor_ref=VENDOR_TAG,
        target_ref=REFINEMENT_BRANCH,
        first_seen_ref=refinement_ref,
        last_seen_ref=refinement_ref,
    )
    return result


def print_summary(result: FinalGateResult) -> None:
    for key, value in result.summary().items():
        print(f"{key}: {value}")
    if result.is_clean:
        print("final_gate_status: clean")
    else:
        print("final_gate_status: blocked")


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    assert_source_alignment(root)
    result = build_final_gate(root)
    print_summary(result)
    if args.strict and not result.is_clean:
        return 1
    return 0


def status_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    assert_source_alignment(root)
    print_summary(build_final_gate_rows(root))
    return 0


def verify_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    assert_source_alignment(root)
    result = build_final_gate_rows(root)
    print_summary(result)
    return 0 if result.is_clean else 1
