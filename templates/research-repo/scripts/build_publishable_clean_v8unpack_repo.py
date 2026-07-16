#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


DEFAULT_SOURCE_REPO = "analysis/cache/noise/clean-rebase-v8unpack/repo"
DEFAULT_OUTPUT_REPO = "analysis/cache/noise/publishable-clean-v8unpack/repo"
DEFAULT_SUMMARY = "analysis/clean-comparison/publishable-clean-v8unpack-summary.json"


def run(args: list[str], cwd: Path | None = None, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(args, cwd=cwd, input=stdin, capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"Command failed: {' '.join(args)}\n"
            f"stdout:\n{result.stdout.decode(errors='replace')}\n"
            f"stderr:\n{result.stderr.decode(errors='replace')}"
        )
    return result


def git(repo: Path, args: list[str]) -> str:
    return run(["git", "-C", str(repo), "-c", "core.quotePath=false", *args]).stdout.decode().strip()


def clear_worktree(path: Path) -> None:
    for child in path.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def extract_ref(source_repo: Path, ref: str, output_repo: Path) -> None:
    clear_worktree(output_repo)
    archive = run(["git", "-C", str(source_repo), "archive", "--format=tar", ref]).stdout
    run(["tar", "-xf", "-", "-C", str(output_repo)], stdin=archive)


def commit_all(output_repo: Path, message: str) -> str:
    run(["git", "add", "-A"], cwd=output_repo)
    run(
        [
            "git",
            "-c",
            "user.name=one-c-autoresearch",
            "-c",
            "user.email=one-c-autoresearch@example.invalid",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=output_repo,
    )
    return git(output_repo, ["rev-parse", "HEAD"])


def count_commits(output_repo: Path) -> int:
    return int(git(output_repo, ["rev-list", "--count", "HEAD"]))


def build(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    source_repo = (root / args.source_repo).resolve()
    output_repo = (root / args.output_repo).resolve()
    summary_path = (root / args.summary).resolve()

    if not (source_repo / ".git").exists():
        raise SystemExit(f"Source Git repo not found: {source_repo}")
    if output_repo.exists():
        shutil.rmtree(output_repo)
    output_repo.mkdir(parents=True)

    run(["git", "init", "-q", "-b", "main"], cwd=output_repo)

    vendor_source_commit = git(source_repo, ["rev-parse", args.vendor_ref])
    customer_source_commit = git(source_repo, ["rev-parse", args.customer_ref])

    extract_ref(source_repo, args.vendor_ref, output_repo)
    vendor_snapshot_commit = commit_all(output_repo, "vendor-baseline")
    run(["git", "tag", "vendor-baseline"], cwd=output_repo)

    extract_ref(source_repo, args.customer_ref, output_repo)
    customer_snapshot_commit = commit_all(output_repo, "customer-clean-final")
    run(["git", "tag", "customer-clean-final"], cwd=output_repo)

    commits = count_commits(output_repo)
    if commits != 2:
        raise SystemExit(f"Snapshot repo must contain exactly 2 commits, got {commits}")

    diff_count = len(git(output_repo, ["diff", "--name-only", "vendor-baseline..customer-clean-final"]).splitlines())
    summary = {
        "schema_version": "publishable-clean-v8unpack/v1",
        "source_repo": args.source_repo,
        "output_repo": args.output_repo,
        "vendor_ref": args.vendor_ref,
        "customer_ref": args.customer_ref,
        "vendor_source_commit": vendor_source_commit,
        "customer_source_commit": customer_source_commit,
        "vendor_snapshot_commit": vendor_snapshot_commit,
        "customer_snapshot_commit": customer_snapshot_commit,
        "commit_count": commits,
        "diff_file_count": diff_count,
        "diff_command": f"git -C {args.output_repo} diff --name-status vendor-baseline..customer-clean-final",
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"output_repo: {output_repo}")
    print(f"commit_count: {commits}")
    print(f"diff_file_count: {diff_count}")
    print(f"summary: {summary_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", default=".")
    parser.add_argument("--source-repo", default=DEFAULT_SOURCE_REPO)
    parser.add_argument("--output-repo", default=DEFAULT_OUTPUT_REPO)
    parser.add_argument("--summary", default=DEFAULT_SUMMARY)
    parser.add_argument("--vendor-ref", default="HEAD^")
    parser.add_argument("--customer-ref", default="HEAD")
    return build(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
