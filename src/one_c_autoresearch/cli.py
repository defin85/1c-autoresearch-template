from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .autopilot import scaffold_autopilot
from .bootstrap import create_research_repo
from .checks import run_check, test_doctor, test_research_repo, test_template
from .doctor import print_doctor, run_doctor
from .queue import claim_command, get_command, set_status_command


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
