from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .autopilot import scaffold_autopilot
from .bootstrap import create_research_repo
from .checks import run_check, test_doctor, test_research_repo, test_template
from .detail_maps import build_command as detail_map_build_command
from .doctor import print_doctor, run_doctor
from .final_gate import build_command as final_gate_build_command
from .final_gate import status_command as final_gate_status_command
from .final_gate import verify_command as final_gate_verify_command
from .queue import claim_command, get_command, set_status_command
from .review_dashboard import build_command as review_dashboard_build_command
from .reverse_map import (
    claim_command as reverse_map_claim_command,
    next_command as reverse_map_next_command,
    scaffold_reverse_map,
    seed_command as reverse_map_seed_command,
    set_status_command as reverse_map_set_status_command,
    status_command as reverse_map_status_command,
)


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
    review_dashboard_build.set_defaults(func=review_dashboard_build_command)

    detail_map = sub.add_parser("detail-map")
    detail_map_sub = detail_map.add_subparsers(dest="detail_map_command", required=True)
    detail_map_build = detail_map_sub.add_parser("build")
    detail_map_build.add_argument("--repo-path", default=".")
    detail_map_build.add_argument("--force", action="store_true")
    detail_map_build.set_defaults(func=detail_map_build_command)

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
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
