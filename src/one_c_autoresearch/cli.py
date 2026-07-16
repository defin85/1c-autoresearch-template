from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .autopilot import scaffold_autopilot
from .bootstrap import create_research_repo
from .checks import run_check, test_doctor, test_research_repo, test_template
from .clean_rebase import build_command as clean_rebase_build_command
from .configuration_source_parser import parse_command as configuration_source_parse_command
from .configuration_source_parser import validate_command as configuration_source_validate_command
from .configuration_source_parser import SOURCE_FORMAT_CHOICES
from .custom_metadata_inventory import COMPARISON_MODES
from .custom_metadata_inventory import build_command as custom_metadata_build_command
from .custom_metadata_inventory import export_command as custom_metadata_export_command
from .custom_metadata_inventory import validate_command as custom_metadata_validate_command
from .customization_registry import bootstrap_command as customization_registry_bootstrap_command
from .customization_registry import build_command as customization_registry_build_command
from .customization_registry import context_build_command as customization_registry_context_build_command
from .customization_registry import export_command as customization_registry_export_command
from .customization_registry import validate_command as customization_registry_validate_command
from .customer_register import build_command as customer_register_build_command
from .customer_register import validate_command as customer_register_validate_command
from .detailed_customer_register import build_command as detailed_customer_register_build_command
from .detailed_customer_register import validate_command as detailed_customer_register_validate_command
from .detailed_register_reverse_review import build_command as detailed_register_reverse_review_build_command
from .detailed_register_reverse_review import validate_command as detailed_register_reverse_review_validate_command
from .detail_maps import build_command as detail_map_build_command
from .doctor import print_doctor, run_doctor
from .external_processing import plan_command as external_processing_plan_command
from .external_processing import validate_command as external_processing_validate_command
from .final_gate import build_command as final_gate_build_command
from .final_gate import status_command as final_gate_status_command
from .final_gate import verify_command as final_gate_verify_command
from .functional_gaps import build_command as functional_gap_build_command
from .functional_gaps import inspect_target_command as functional_gap_inspect_target_command
from .functional_gaps import map_build_command as functional_gap_map_build_command
from .functional_gaps import metadata_rebase_command as functional_gap_metadata_rebase_command
from .functional_gaps import refresh_command as functional_gap_refresh_command
from .functional_gaps import status_command as functional_gap_status_command
from .functional_gaps import validate_command as functional_gap_validate_command
from .functional_gap_dashboard import build_command as functional_gap_dashboard_build_command
from .migration_requirements import (
    activate_command as migration_requirement_activate_command,
    bootstrap_command as migration_requirement_bootstrap_command,
    build_command as migration_requirement_build_command,
    context_command as migration_requirement_context_command,
    deactivate_command as migration_requirement_deactivate_command,
    link_command as migration_requirement_link_command,
    neighbors_command as migration_requirement_neighbors_command,
    outputs_command as migration_requirement_outputs_command,
    show_command as migration_requirement_show_command,
    unlink_command as migration_requirement_unlink_command,
    validate_command as migration_requirement_validate_command,
)
from .manual_cleanup import COMMAND_MODULES as MANUAL_CLEANUP_COMMANDS, command as manual_cleanup_command
from .queue import claim_command, get_command, set_status_command
from .queue_seed import seed_command as queue_seed_command
from .parallel_research import (
    MAX_WORKERS,
    apply_command as parallel_research_apply_command,
    cleanup_command as parallel_research_cleanup_command,
    compact_command as parallel_research_compact_command,
    context_command as parallel_research_context_command,
    exec_command as parallel_research_exec_command,
    inspect_command as parallel_research_inspect_command,
    plan_command as parallel_research_plan_command,
    run_command as parallel_research_run_command,
)
from .research_review import prepare_command as research_review_prepare_command
from .research_review import validate_command as research_review_validate_command
from .review_dashboard import build_command as review_dashboard_build_command
from .reverse_map import (
    claim_command as reverse_map_claim_command,
    next_command as reverse_map_next_command,
    scaffold_reverse_map,
    seed_command as reverse_map_seed_command,
    set_status_command as reverse_map_set_status_command,
    status_command as reverse_map_status_command,
)
from .subject_cards import (
    classify_command as subject_card_classify_command,
    compare_reference_command as subject_card_compare_reference_command,
    contour_draft_command as subject_card_contour_draft_command,
    contour_validate_command as subject_card_contour_validate_command,
    discover_command as subject_card_discover_command,
    refine_command as subject_card_refine_command,
    registry_build_command as subject_card_registry_build_command,
    seed_command as subject_card_seed_command,
    validate_command as subject_card_validate_command,
)
from .v8unpack_refinement import build_command as v8unpack_refinement_build_command
from .v8unpack_refinement import manual_cleanup_report_command as v8unpack_refinement_manual_cleanup_report_command
from .v8unpack_refinement import smoke_command as v8unpack_refinement_smoke_command
from .v8unpack_autopilot import build_command as v8unpack_autopilot_build_command
from .v8unpack_autopilot import smoke_command as v8unpack_autopilot_smoke_command


def add_new_repo_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target-path", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--product", default="1C")
    parser.add_argument("--baseline-version", default="")
    parser.add_argument("--target-version", default="")
    parser.add_argument("--next-vendor-version", default="")
    parser.add_argument("--vendor-baseline", default="")
    parser.add_argument("--target-cf", default="")
    parser.add_argument("--target-cfe", default="")
    parser.add_argument("--next-vendor", default="")
    parser.add_argument("--rlm-vendor-baseline", default="")
    parser.add_argument("--rlm-target-cf", default="")
    parser.add_argument("--rlm-target-cfe", default="")
    parser.add_argument("--rlm-next-vendor", default="")
    parser.add_argument("--template-root", default="")
    parser.add_argument("--init-git", action="store_true")
    parser.add_argument("--force", action="store_true")


def doctor_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = run_doctor(root, mode=args.mode, deep=args.deep, stale_claim_hours=args.stale_claim_hours)
    print_doctor(result, as_json=args.json)
    if result["summary"]["fail"] > 0:
        return 1
    if args.strict and result["summary"]["warn"] > 0:
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="one_c_autoresearch")
    sub = parser.add_subparsers(dest="command", required=True)

    parallel_research = sub.add_parser("parallel-research")
    parallel_sub = parallel_research.add_subparsers(dest="parallel_research_command", required=True)
    parallel_plan = parallel_sub.add_parser("plan")
    parallel_plan.add_argument("--repo-path", default=".")
    parallel_plan.add_argument("--task-id", action="append", default=[])
    parallel_plan.add_argument("--unit-size", type=int, default=0, help="0 uses project.toml parallel_research.unit_size")
    parallel_plan.add_argument("--limit", type=int, default=0)
    parallel_plan.add_argument("--write", action="store_true")
    parallel_plan.set_defaults(func=parallel_research_plan_command)
    parallel_run = parallel_sub.add_parser("run")
    parallel_run.add_argument("--repo-path", default=".")
    parallel_run.add_argument("--run-id", required=True)
    parallel_run.add_argument("--workers", type=int, default=0, help="0 uses project.toml parallel_research.workers")
    parallel_run.add_argument("--model", default="")
    parallel_run.add_argument("--timeout", type=int, default=0, help="0 uses project.toml parallel_research.timeout_seconds")
    parallel_run.add_argument("--fake-exec", action="store_true")
    parallel_run.add_argument("--apply", action="store_true")
    parallel_run.set_defaults(func=parallel_research_run_command)
    parallel_exec = parallel_sub.add_parser("run-unit")
    parallel_exec.add_argument("--repo-path", default=".")
    parallel_exec.add_argument("--run-id", required=True)
    parallel_exec.add_argument("--unit-id", required=True)
    parallel_exec.add_argument("--model", required=True)
    parallel_exec.add_argument("--timeout", type=int, default=1800)
    parallel_exec.add_argument("--fake-exec", action="store_true")
    parallel_exec.set_defaults(func=parallel_research_exec_command)
    parallel_context = parallel_sub.add_parser("context")
    parallel_context.add_argument("--repo-path", default=".")
    parallel_context.add_argument("--run-id", required=True)
    parallel_context.add_argument("--unit-id", required=True)
    parallel_context.add_argument("--max-evidence", type=int, default=20)
    parallel_context.set_defaults(func=parallel_research_context_command)
    parallel_apply = parallel_sub.add_parser("apply")
    parallel_apply.add_argument("--repo-path", default=".")
    parallel_apply.add_argument("--run-id", required=True)
    parallel_apply.add_argument("--heavy-check", action="append", default=[])
    parallel_apply.set_defaults(func=parallel_research_apply_command)
    parallel_cleanup = parallel_sub.add_parser("cleanup-traces")
    parallel_cleanup.add_argument("--repo-path", default=".")
    parallel_cleanup.add_argument("--older-than-days", type=int, default=14)
    parallel_cleanup.set_defaults(func=parallel_research_cleanup_command)
    for name, command in (("inspect", parallel_research_inspect_command), ("compact", parallel_research_compact_command)):
        item = parallel_sub.add_parser(name)
        item.add_argument("--repo-path", default=".")
        item.add_argument("--run-id", required=True)
        if name == "compact":
            item.add_argument("--write", action="store_true")
        item.set_defaults(func=command)

    manual_cleanup = sub.add_parser("manual-cleanup")
    manual_sub = manual_cleanup.add_subparsers(dest="manual_cleanup_command", required=True)
    for name in MANUAL_CLEANUP_COMMANDS:
        item = manual_sub.add_parser(name, add_help=False)
        item.add_argument("command_args", nargs=argparse.REMAINDER)
        item.set_defaults(func=manual_cleanup_command)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--repo-path", default="")
    doctor.add_argument("--mode", choices=["auto", "template", "research"], default="auto")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--deep", action="store_true")
    doctor.add_argument("--strict", action="store_true")
    doctor.add_argument("--stale-claim-hours", type=int, default=12)
    doctor.set_defaults(func=doctor_command)

    new_repo = sub.add_parser("new-repo")
    add_new_repo_args(new_repo)
    new_repo.set_defaults(func=create_research_repo)

    autopilot = sub.add_parser("autopilot")
    autopilot_sub = autopilot.add_subparsers(dest="autopilot_command", required=True)
    scaffold = autopilot_sub.add_parser("scaffold")
    scaffold.add_argument("--repo-path", default=".")
    scaffold.add_argument("--force", action="store_true")
    scaffold.add_argument("--enable-gate", action="store_true")
    scaffold.set_defaults(func=scaffold_autopilot)

    clean_rebase = sub.add_parser("clean-rebase")
    clean_rebase_sub = clean_rebase.add_subparsers(dest="clean_rebase_command", required=True)
    clean_rebase_build = clean_rebase_sub.add_parser("build")
    clean_rebase_build.add_argument("--repo-path", default=".")
    clean_rebase_build.add_argument("--compare-repo", default="")
    clean_rebase_build.add_argument("--report-dir", default="")
    clean_rebase_build.set_defaults(func=clean_rebase_build_command)

    configuration_source = sub.add_parser("configuration-source")
    configuration_source_sub = configuration_source.add_subparsers(dest="configuration_source_command", required=True)
    configuration_source_parse = configuration_source_sub.add_parser("parse")
    configuration_source_parse.add_argument("--repo-path", default=".")
    configuration_source_parse.add_argument("--source-root", required=True)
    configuration_source_parse.add_argument("--source-format", choices=SOURCE_FORMAT_CHOICES, default="auto")
    configuration_source_parse.add_argument("--output", default="")
    configuration_source_parse.add_argument("--parse-only", action="store_true")
    configuration_source_parse.set_defaults(func=configuration_source_parse_command)
    configuration_source_validate = configuration_source_sub.add_parser("validate")
    configuration_source_validate.add_argument("--repo-path", default=".")
    configuration_source_validate.add_argument("--snapshot", required=True)
    configuration_source_validate.add_argument("--strict", action="store_true")
    configuration_source_validate.set_defaults(func=configuration_source_validate_command)

    custom_metadata = sub.add_parser("custom-metadata")
    custom_metadata_sub = custom_metadata.add_subparsers(dest="custom_metadata_command", required=True)
    custom_metadata_build = custom_metadata_sub.add_parser("build")
    custom_metadata_build.add_argument("--repo-path", default=".")
    custom_metadata_build.add_argument("--typical-snapshot", default="")
    custom_metadata_build.add_argument("--customer-snapshot", default="")
    custom_metadata_build.add_argument("--typical-source-root", default="")
    custom_metadata_build.add_argument("--customer-source-root", default="")
    custom_metadata_build.add_argument("--source-format", choices=SOURCE_FORMAT_CHOICES, default="auto")
    custom_metadata_build.add_argument("--comparison-mode", choices=sorted(COMPARISON_MODES), default="full")
    custom_metadata_build.add_argument("--output-dir", default="")
    custom_metadata_build.add_argument("--strict-reconciliation", action="store_true")
    custom_metadata_build.set_defaults(func=custom_metadata_build_command)
    custom_metadata_validate = custom_metadata_sub.add_parser("validate")
    custom_metadata_validate.add_argument("--repo-path", default=".")
    custom_metadata_validate.add_argument("--output-dir", default="")
    custom_metadata_validate.add_argument("--strict-reconciliation", action="store_true")
    custom_metadata_validate.set_defaults(func=custom_metadata_validate_command)
    custom_metadata_export = custom_metadata_sub.add_parser("export")
    custom_metadata_export.add_argument("--repo-path", default=".")
    custom_metadata_export.add_argument("--output-dir", default="")
    custom_metadata_export.add_argument("--format", choices=["markdown", "spreadsheet"], required=True)
    custom_metadata_export.set_defaults(func=custom_metadata_export_command)

    customization_registry = sub.add_parser("customization-registry")
    customization_registry_sub = customization_registry.add_subparsers(dest="customization_registry_command", required=True)
    customization_registry_bootstrap = customization_registry_sub.add_parser("bootstrap")
    customization_registry_bootstrap.add_argument("--repo-path", default=".")
    customization_registry_bootstrap.add_argument("--designer-report", default="")
    customization_registry_bootstrap.add_argument("--v8unpack-root", default="")
    customization_registry_bootstrap.set_defaults(func=customization_registry_bootstrap_command)
    customization_registry_context_build = customization_registry_sub.add_parser("context-build")
    customization_registry_context_build.add_argument("--repo-path", default=".")
    customization_registry_context_build.add_argument("--designer-report", default="")
    customization_registry_context_build.add_argument("--v8unpack-root", default="")
    customization_registry_context_build.set_defaults(func=customization_registry_context_build_command)
    customization_registry_build = customization_registry_sub.add_parser("build")
    customization_registry_build.add_argument("--repo-path", default=".")
    customization_registry_build.set_defaults(func=customization_registry_build_command)
    customization_registry_validate = customization_registry_sub.add_parser("validate")
    customization_registry_validate.add_argument("--repo-path", default=".")
    customization_registry_validate.set_defaults(func=customization_registry_validate_command)
    customization_registry_export = customization_registry_sub.add_parser("export")
    customization_registry_export.add_argument("--repo-path", default=".")
    customization_registry_export.set_defaults(func=customization_registry_export_command)

    migration_requirement = sub.add_parser("migration-requirement")
    migration_requirement_sub = migration_requirement.add_subparsers(dest="migration_requirement_command", required=True)
    migration_requirement_bootstrap = migration_requirement_sub.add_parser("bootstrap")
    migration_requirement_bootstrap.add_argument("--repo-path", default=".")
    migration_requirement_bootstrap.add_argument("--apply", action="store_true")
    migration_requirement_bootstrap.set_defaults(func=migration_requirement_bootstrap_command)
    migration_requirement_build = migration_requirement_sub.add_parser("build")
    migration_requirement_build.add_argument("--repo-path", default=".")
    migration_requirement_build.set_defaults(func=migration_requirement_build_command)
    migration_requirement_validate = migration_requirement_sub.add_parser("validate")
    migration_requirement_validate.add_argument("--repo-path", default=".")
    migration_requirement_validate.set_defaults(func=migration_requirement_validate_command)
    for name, command in (("show", migration_requirement_show_command), ("neighbors", migration_requirement_neighbors_command)):
        item = migration_requirement_sub.add_parser(name)
        item.add_argument("entity_id")
        item.add_argument("--repo-path", default=".")
        item.add_argument("--limit", type=int, default=100) if name == "neighbors" else None
        item.set_defaults(func=command)
    migration_requirement_context = migration_requirement_sub.add_parser("context")
    migration_requirement_context.add_argument("requirement_id")
    migration_requirement_context.add_argument("--repo-path", default=".")
    migration_requirement_context.add_argument("--limit", type=int, default=100)
    migration_requirement_context.set_defaults(func=migration_requirement_context_command)
    for name, command in (("link", migration_requirement_link_command), ("unlink", migration_requirement_unlink_command)):
        item = migration_requirement_sub.add_parser(name)
        item.add_argument("requirement_id")
        item.add_argument("customization_id")
        item.add_argument("--repo-path", default=".")
        if name == "link":
            item.add_argument("--role", choices=["primary", "required", "supporting", "shared"], default="supporting")
            item.add_argument("--rationale", required=True)
        item.set_defaults(func=command)
    migration_requirement_activate = migration_requirement_sub.add_parser("activate")
    migration_requirement_activate.add_argument("--repo-path", default=".")
    migration_requirement_activate.add_argument("--comparison-accepted", action="store_true")
    migration_requirement_activate.add_argument("--comparison-report", default="")
    migration_requirement_activate.set_defaults(func=migration_requirement_activate_command)
    migration_requirement_deactivate = migration_requirement_sub.add_parser("deactivate")
    migration_requirement_deactivate.add_argument("--repo-path", default=".")
    migration_requirement_deactivate.set_defaults(func=migration_requirement_deactivate_command)
    migration_requirement_outputs = migration_requirement_sub.add_parser("outputs")
    migration_requirement_outputs.add_argument("--repo-path", default=".")
    migration_requirement_outputs.set_defaults(func=migration_requirement_outputs_command)

    v8unpack_refinement = sub.add_parser("v8unpack-refinement")
    v8unpack_refinement_sub = v8unpack_refinement.add_subparsers(dest="v8unpack_refinement_command", required=True)
    v8unpack_refinement_build = v8unpack_refinement_sub.add_parser("build")
    v8unpack_refinement_build.add_argument("--repo-path", default=".")
    v8unpack_refinement_build.add_argument("--force", action="store_true")
    v8unpack_refinement_build.set_defaults(func=v8unpack_refinement_build_command)
    v8unpack_refinement_smoke = v8unpack_refinement_sub.add_parser("smoke")
    v8unpack_refinement_smoke.add_argument("--repo-path", default=".")
    v8unpack_refinement_smoke.set_defaults(func=v8unpack_refinement_smoke_command)
    v8unpack_refinement_manual_cleanup_report = v8unpack_refinement_sub.add_parser("manual-cleanup-report")
    v8unpack_refinement_manual_cleanup_report.add_argument("--repo-path", default=".")
    v8unpack_refinement_manual_cleanup_report.set_defaults(func=v8unpack_refinement_manual_cleanup_report_command)

    v8unpack_autopilot = sub.add_parser("v8unpack-autopilot-realign")
    v8unpack_autopilot_sub = v8unpack_autopilot.add_subparsers(dest="v8unpack_autopilot_command", required=True)
    v8unpack_autopilot_build = v8unpack_autopilot_sub.add_parser("build")
    v8unpack_autopilot_build.add_argument("--repo-path", default=".")
    v8unpack_autopilot_build.set_defaults(func=v8unpack_autopilot_build_command)
    v8unpack_autopilot_smoke = v8unpack_autopilot_sub.add_parser("smoke")
    v8unpack_autopilot_smoke.add_argument("--repo-path", default=".")
    v8unpack_autopilot_smoke.set_defaults(func=v8unpack_autopilot_smoke_command)

    final_gate = sub.add_parser("final-gate")
    final_gate_sub = final_gate.add_subparsers(dest="final_gate_command", required=True)
    final_gate_build = final_gate_sub.add_parser("build")
    final_gate_build.add_argument("--repo-path", default=".")
    final_gate_build.add_argument("--strict", action="store_true")
    final_gate_build.set_defaults(func=final_gate_build_command)
    final_gate_status = final_gate_sub.add_parser("status")
    final_gate_status.add_argument("--repo-path", default=".")
    final_gate_status.set_defaults(func=final_gate_status_command)
    final_gate_verify = final_gate_sub.add_parser("verify")
    final_gate_verify.add_argument("--repo-path", default=".")
    final_gate_verify.set_defaults(func=final_gate_verify_command)

    review_dashboard = sub.add_parser("review-dashboard")
    review_dashboard_sub = review_dashboard.add_subparsers(dest="review_dashboard_command", required=True)
    review_dashboard_build = review_dashboard_sub.add_parser("build")
    review_dashboard_build.add_argument("--repo-path", default=".")
    review_dashboard_build.add_argument("--output-dir", default="")
    review_dashboard_build.add_argument("--subject-card", default="")
    review_dashboard_build.set_defaults(func=review_dashboard_build_command)

    research_review = sub.add_parser("research-review")
    research_review_sub = research_review.add_subparsers(dest="research_review_command", required=True)
    research_review_prepare = research_review_sub.add_parser("prepare")
    research_review_prepare.add_argument("--repo-path", default=".")
    research_review_prepare.add_argument("--queue-path", default="analysis/queue/tasks.jsonl")
    research_review_prepare.add_argument("--plan", action="store_true")
    research_review_prepare.add_argument("--apply", action="store_true")
    research_review_prepare.set_defaults(func=research_review_prepare_command)
    research_review_validate = research_review_sub.add_parser("validate")
    research_review_validate.add_argument("--repo-path", default=".")
    research_review_validate.add_argument("--queue-path", default="analysis/queue/tasks.jsonl")
    research_review_validate.set_defaults(func=research_review_validate_command)

    detail_map = sub.add_parser("detail-map")
    detail_map_sub = detail_map.add_subparsers(dest="detail_map_command", required=True)
    detail_map_build = detail_map_sub.add_parser("build")
    detail_map_build.add_argument("--repo-path", default=".")
    detail_map_build.add_argument("--force", action="store_true")
    detail_map_build.set_defaults(func=detail_map_build_command)

    subject_card = sub.add_parser("subject-card")
    subject_card_sub = subject_card.add_subparsers(dest="subject_card_command", required=True)
    subject_discover = subject_card_sub.add_parser("discover")
    subject_discover.add_argument("--repo-path", default=".")
    subject_discover.set_defaults(func=subject_card_discover_command)
    subject_contour_draft = subject_card_sub.add_parser("contour-draft")
    subject_contour_draft.add_argument("--repo-path", default=".")
    subject_contour_draft.set_defaults(func=subject_card_contour_draft_command)
    subject_contour_validate = subject_card_sub.add_parser("contour-validate")
    subject_contour_validate.add_argument("--repo-path", default=".")
    subject_contour_validate.set_defaults(func=subject_card_contour_validate_command)
    subject_classify = subject_card_sub.add_parser("classify")
    subject_classify.add_argument("--repo-path", default=".")
    subject_classify.set_defaults(func=subject_card_classify_command)
    subject_registry_build = subject_card_sub.add_parser("registry-build")
    subject_registry_build.add_argument("--repo-path", default=".")
    subject_registry_build.set_defaults(func=subject_card_registry_build_command)
    subject_seed = subject_card_sub.add_parser("seed")
    subject_seed.add_argument("--repo-path", default=".")
    subject_seed.add_argument("--card", default="")
    subject_seed.add_argument("--all", action="store_true")
    subject_seed.add_argument("--from-registry", action="store_true")
    subject_seed.set_defaults(func=subject_card_seed_command)
    subject_refine = subject_card_sub.add_parser("refine")
    subject_refine.add_argument("--repo-path", default=".")
    subject_refine.add_argument("--card", required=True)
    subject_refine.set_defaults(func=subject_card_refine_command)
    subject_validate = subject_card_sub.add_parser("validate")
    subject_validate.add_argument("--repo-path", default=".")
    subject_validate.add_argument("--card", default="")
    subject_validate.set_defaults(func=subject_card_validate_command)
    subject_compare = subject_card_sub.add_parser("compare-reference")
    subject_compare.add_argument("--repo-path", default=".")
    subject_compare.add_argument("--card", required=True)
    subject_compare.add_argument("--workbook", required=True)
    subject_compare.set_defaults(func=subject_card_compare_reference_command)

    functional_gap = sub.add_parser("functional-gap")
    functional_gap_sub = functional_gap.add_subparsers(dest="functional_gap_command", required=True)
    functional_gap_build = functional_gap_sub.add_parser("build")
    functional_gap_build.add_argument("--repo-path", default=".")
    functional_gap_build.add_argument("--card", required=True)
    functional_gap_build.add_argument("--force", action="store_true")
    functional_gap_build.set_defaults(func=functional_gap_build_command)
    functional_gap_refresh = functional_gap_sub.add_parser("refresh")
    functional_gap_refresh.add_argument("--repo-path", default=".")
    functional_gap_refresh.add_argument("--card", required=True)
    functional_gap_refresh.add_argument("--force", action="store_true")
    functional_gap_refresh.set_defaults(func=functional_gap_refresh_command)
    functional_gap_inspect_target = functional_gap_sub.add_parser("inspect-target")
    functional_gap_inspect_target.add_argument("--repo-path", default=".")
    functional_gap_inspect_target.add_argument("--card", required=True)
    functional_gap_inspect_target.add_argument("--force", action="store_true")
    functional_gap_inspect_target.add_argument("--target-profile", default="")
    functional_gap_inspect_target.set_defaults(func=functional_gap_inspect_target_command)
    functional_gap_map_build = functional_gap_sub.add_parser("map-build")
    functional_gap_map_build.add_argument("--repo-path", default=".")
    functional_gap_map_build.set_defaults(func=functional_gap_map_build_command)
    functional_gap_metadata_rebase = functional_gap_sub.add_parser("metadata-rebase")
    functional_gap_metadata_rebase.add_argument("--repo-path", default=".")
    functional_gap_metadata_rebase.set_defaults(func=functional_gap_metadata_rebase_command)
    functional_gap_dashboard_build = functional_gap_sub.add_parser("dashboard-build")
    functional_gap_dashboard_build.add_argument("--repo-path", default=".")
    functional_gap_dashboard_build.add_argument("--output-dir", default="")
    functional_gap_dashboard_build.set_defaults(func=functional_gap_dashboard_build_command)
    functional_gap_validate = functional_gap_sub.add_parser("validate")
    functional_gap_validate.add_argument("--repo-path", default=".")
    functional_gap_validate.add_argument("--card", default="")
    functional_gap_validate.set_defaults(func=functional_gap_validate_command)
    functional_gap_status = functional_gap_sub.add_parser("status")
    functional_gap_status.add_argument("--repo-path", default=".")
    functional_gap_status.set_defaults(func=functional_gap_status_command)

    customer_register = sub.add_parser("customer-register")
    customer_register_sub = customer_register.add_subparsers(dest="customer_register_command", required=True)
    customer_register_build = customer_register_sub.add_parser("build")
    customer_register_build.add_argument("--repo-path", default=".")
    customer_register_build.set_defaults(func=customer_register_build_command)
    customer_register_validate = customer_register_sub.add_parser("validate")
    customer_register_validate.add_argument("--repo-path", default=".")
    customer_register_validate.set_defaults(func=customer_register_validate_command)

    detailed_customer_register = sub.add_parser("detailed-customer-register")
    detailed_customer_register_sub = detailed_customer_register.add_subparsers(dest="detailed_customer_register_command", required=True)
    detailed_customer_register_build = detailed_customer_register_sub.add_parser("build")
    detailed_customer_register_build.add_argument("--repo-path", default=".")
    detailed_customer_register_build.add_argument("--workbook", default="")
    detailed_customer_register_build.set_defaults(func=detailed_customer_register_build_command)
    detailed_customer_register_validate = detailed_customer_register_sub.add_parser("validate")
    detailed_customer_register_validate.add_argument("--repo-path", default=".")
    detailed_customer_register_validate.add_argument("--workbook", default="")
    detailed_customer_register_validate.set_defaults(func=detailed_customer_register_validate_command)

    detailed_register_reverse_review = sub.add_parser("detailed-register-reverse-review")
    detailed_register_reverse_review_sub = detailed_register_reverse_review.add_subparsers(dest="detailed_register_reverse_review_command", required=True)
    detailed_register_reverse_review_build = detailed_register_reverse_review_sub.add_parser("build")
    detailed_register_reverse_review_build.add_argument("--repo-path", default=".")
    detailed_register_reverse_review_build.set_defaults(func=detailed_register_reverse_review_build_command)
    detailed_register_reverse_review_validate = detailed_register_reverse_review_sub.add_parser("validate")
    detailed_register_reverse_review_validate.add_argument("--repo-path", default=".")
    detailed_register_reverse_review_validate.add_argument("--mode", choices=("draft", "final"), default="draft")
    detailed_register_reverse_review_validate.set_defaults(func=detailed_register_reverse_review_validate_command)

    external_processing = sub.add_parser("external-processing")
    external_processing_sub = external_processing.add_subparsers(dest="external_processing_command", required=True)
    external_processing_plan = external_processing_sub.add_parser("plan")
    external_processing_plan.add_argument("--repo-path", default=".")
    external_processing_plan.add_argument("--queue-path", default="analysis/queue/tasks.jsonl")
    external_processing_plan.add_argument("--plan", action="store_true")
    external_processing_plan.add_argument("--apply", action="store_true")
    external_processing_plan.set_defaults(func=external_processing_plan_command)
    external_processing_validate = external_processing_sub.add_parser("validate")
    external_processing_validate.add_argument("--repo-path", default=".")
    external_processing_validate.add_argument("--queue-path", default="analysis/queue/tasks.jsonl")
    external_processing_validate.set_defaults(func=external_processing_validate_command)

    reverse_map = sub.add_parser("reverse-map")
    reverse_map_sub = reverse_map.add_subparsers(dest="reverse_map_command", required=True)
    reverse_scaffold = reverse_map_sub.add_parser("scaffold")
    reverse_scaffold.add_argument("--repo-path", default=".")
    reverse_scaffold.add_argument("--force", action="store_true")
    reverse_scaffold.set_defaults(func=scaffold_reverse_map)

    reverse_seed = reverse_map_sub.add_parser("seed")
    reverse_seed.add_argument("--repo-path", default=".")
    reverse_seed.set_defaults(func=reverse_map_seed_command)

    reverse_next = reverse_map_sub.add_parser("next")
    reverse_next.add_argument("--repo-path", default=".")
    reverse_next.set_defaults(func=reverse_map_next_command)

    reverse_claim = reverse_map_sub.add_parser("claim")
    reverse_claim.add_argument("--repo-path", default=".")
    reverse_claim.add_argument("--claimed-by", default="codex")
    reverse_claim.add_argument("--lock-timeout-seconds", type=int, default=10)
    reverse_claim.set_defaults(func=reverse_map_claim_command)

    reverse_set_status = reverse_map_sub.add_parser("set-status")
    reverse_set_status.add_argument("--repo-path", default=".")
    reverse_set_status.add_argument("--id", required=True)
    reverse_set_status.add_argument("--status", required=True)
    reverse_set_status.add_argument("--result-summary", default=None)
    reverse_set_status.add_argument("--expected-status", default=None)
    reverse_set_status.add_argument("--lock-timeout-seconds", type=int, default=10)
    reverse_set_status.set_defaults(func=reverse_map_set_status_command)

    reverse_status = reverse_map_sub.add_parser("status")
    reverse_status.add_argument("--repo-path", default=".")
    reverse_status.set_defaults(func=reverse_map_status_command)

    queue = sub.add_parser("queue")
    queue_sub = queue.add_subparsers(dest="queue_command", required=True)
    get = queue_sub.add_parser("get")
    get.add_argument("--queue-path", default="analysis/queue/tasks.jsonl")
    get.add_argument("--status", default="pending")
    get.add_argument("--type", default=None)
    get.add_argument("--all", action="store_true")
    get.add_argument("--include-blocked-by-dependencies", action="store_true")
    get.set_defaults(func=get_command)

    claim = queue_sub.add_parser("claim")
    claim.add_argument("--queue-path", default="analysis/queue/tasks.jsonl")
    claim.add_argument("--status", default="pending")
    claim.add_argument("--type", default=None)
    claim.add_argument("--claimed-by", default="codex")
    claim.add_argument("--lock-timeout-seconds", type=int, default=10)
    claim.set_defaults(func=claim_command)

    set_status = queue_sub.add_parser("set-status")
    set_status.add_argument("--queue-path", default="analysis/queue/tasks.jsonl")
    set_status.add_argument("--id", required=True)
    set_status.add_argument("--status", required=True)
    set_status.add_argument("--claimed-by", default="codex")
    set_status.add_argument("--result-summary", default=None)
    set_status.add_argument("--expected-status", default=None)
    set_status.add_argument("--lock-timeout-seconds", type=int, default=10)
    set_status.set_defaults(func=set_status_command)

    seed = queue_sub.add_parser("seed")
    seed.add_argument("--repo-path", default=".")
    seed.add_argument("--queue-path", default="analysis/queue/tasks.jsonl")
    seed.add_argument("--override-path", default="analysis/queue/seeding-overrides.csv")
    seed.add_argument("--profile", required=True)
    seed.add_argument("--plan", action="store_true")
    seed.add_argument("--apply", action="store_true")
    seed.set_defaults(func=queue_seed_command)

    checks = sub.add_parser("checks")
    checks_sub = checks.add_subparsers(dest="check_command", required=True)
    template = checks_sub.add_parser("template")
    template.add_argument("--repo-path", default=".")
    template.set_defaults(func=lambda args: run_check(test_template, args))
    doctor_check = checks_sub.add_parser("doctor")
    doctor_check.add_argument("--repo-path", default=".")
    doctor_check.set_defaults(func=lambda args: run_check(test_doctor, args))
    research = checks_sub.add_parser("research")
    research.add_argument("--repo-path", default=".")
    research.set_defaults(func=lambda args: run_check(test_research_repo, args))
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if len(raw) >= 2 and raw[0] == "manual-cleanup" and raw[1] in MANUAL_CLEANUP_COMMANDS:
        return manual_cleanup_command(argparse.Namespace(manual_cleanup_command=raw[1], command_args=raw[2:]))
    parser = build_parser()
    args = parser.parse_args(raw)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
