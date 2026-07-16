from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
from pathlib import Path

from .autopilot import DIFF_INVENTORY_HEADER
from .common import read_toml, repo_path, toml_value


DEFAULT_COMPARE_REPO = "analysis/cache/clean-rebase/repo"
DEFAULT_REPORT_DIR = "analysis/cache/clean-rebase/reports"

EXCLUDED_NAMES = {
    ".git",
    "__pycache__",
    "ConfigDumpInfo.xml",
    "stdout.log",
    "stderr.log",
    "summary.json",
}
EXCLUDED_SUFFIXES = {".bin", ".pyc"}

KIND_BY_FOLDER = {
    "AccountingRegisters": "AccountingRegister",
    "AccumulationRegisters": "AccumulationRegister",
    "BusinessProcesses": "BusinessProcess",
    "Catalogs": "Catalog",
    "ChartsOfAccounts": "ChartOfAccounts",
    "ChartsOfCalculationTypes": "ChartOfCalculationTypes",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
    "CommandGroups": "CommandGroup",
    "CommonAttributes": "CommonAttribute",
    "CommonCommands": "CommonCommand",
    "CommonForms": "CommonForm",
    "CommonModules": "CommonModule",
    "CommonPictures": "CommonPicture",
    "CommonTemplates": "CommonTemplate",
    "Constants": "Constant",
    "DataProcessors": "DataProcessor",
    "DefinedTypes": "DefinedType",
    "DocumentJournals": "DocumentJournal",
    "DocumentNumerators": "DocumentNumerator",
    "Documents": "Document",
    "Enums": "Enum",
    "EventSubscriptions": "EventSubscription",
    "ExchangePlans": "ExchangePlan",
    "FilterCriteria": "FilterCriterion",
    "FunctionalOptions": "FunctionalOption",
    "HTTPServices": "HTTPService",
    "InformationRegisters": "InformationRegister",
    "Interfaces": "Interface",
    "Languages": "Language",
    "Reports": "Report",
    "Roles": "Role",
    "ScheduledJobs": "ScheduledJob",
    "Sequences": "Sequence",
    "SessionParameters": "SessionParameter",
    "SettingsStorages": "SettingsStorage",
    "StyleItems": "StyleItem",
    "Tasks": "Task",
    "WebServices": "WebService",
    "WSReferences": "WSReference",
    "XDTOPackages": "XDTOPackage",
}


def _git_env(cwd: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_CEILING_DIRECTORIES"] = str(cwd.resolve().parent)
    return env


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=_git_env(cwd), text=True, capture_output=True, check=False)


def _is_excluded(path: Path) -> bool:
    return path.name in EXCLUDED_NAMES or path.suffix in EXCLUDED_SUFFIXES


def _clear_worktree(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in target.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _copy_normalized_tree(source: Path, target: Path) -> tuple[int, int]:
    if not source.exists():
        raise FileNotFoundError(f"Source path does not exist: {source}")
    _clear_worktree(target)
    copied_files = 0
    skipped_files = 0
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if any(_is_excluded(parent) for parent in relative.parents if parent.name):
            continue
        if _is_excluded(item):
            if item.is_file():
                skipped_files += 1
            continue
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        copied_files += 1
    return copied_files, skipped_files


def _git_commit_all(compare_repo: Path, message: str) -> None:
    commands = [
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.name=one-c-autoresearch",
            "-c",
            "user.email=one-c-autoresearch@example.invalid",
            "commit",
            "-m",
            message,
        ],
    ]
    for command in commands:
        result = _run(command, compare_repo)
        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed: {' '.join(command)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )


def _diff_entries(compare_repo: Path) -> list[tuple[str, str]]:
    result = _run(
        ["git", "-c", "core.quotePath=false", "diff", "--name-status", "--find-renames", "vendor-baseline", "target-cf"],
        compare_repo,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    entries: list[tuple[str, str]] = []
    for raw in result.stdout.splitlines():
        parts = raw.split("\t")
        if not parts:
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            entries.append(("D", parts[1]))
            entries.append(("A", parts[2]))
        elif len(parts) >= 2:
            entries.append((status[0], parts[1]))
    return entries


def _infer_object(relative_path: str) -> tuple[str, str, str]:
    path = Path(relative_path)
    parts = path.parts
    if not parts:
        return "", "", ""
    if parts[0] == "Configuration.xml":
        return "Configuration", "Configuration", "metadata"
    if parts[0] == "Ext":
        area = "bsl" if path.suffix.lower() == ".bsl" else "metadata"
        return "Configuration", "Configuration", area
    object_kind = KIND_BY_FOLDER.get(parts[0], parts[0])
    object_name = parts[1] if len(parts) > 1 else path.stem
    if len(parts) > 1:
        object_name = f"{object_kind}.{object_name}"
    suffix = path.suffix.lower()
    if suffix == ".bsl":
        area = "bsl"
    elif "Forms" in parts:
        area = "form"
    elif "Templates" in parts:
        area = "template"
    elif "Help" in parts or suffix == ".html":
        area = "help"
    elif "Predefined.xml" in parts:
        area = "predefined"
    elif suffix == ".xml":
        area = "metadata"
    else:
        area = "other"
    return object_kind, object_name, area


def _summary_for(change_type: str, object_name: str, area: str) -> str:
    action = {"A": "добавлен", "M": "изменен", "D": "удален"}.get(change_type, "изменен")
    subject = object_name or "файл"
    return f"{action} {subject}; область: {area or 'unknown'}"


def _write_diff_inventory(root: Path, entries: list[tuple[str, str]]) -> None:
    path = repo_path(root, "analysis/indexes/diff-inventory.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    header = DIFF_INVENTORY_HEADER.split(",")
    rows: list[dict[str, str]] = []
    for index, (change_type, relative_path) in enumerate(entries, 1):
        object_kind, object_name, area = _infer_object(relative_path)
        rows.append(
            {
                "diff_id": f"D-{index:06d}",
                "source": "clean-rebase",
                "change_type": change_type,
                "path": relative_path,
                "object_kind": object_kind,
                "object_name": object_name,
                "area": area,
                "feature_id": "",
                "classification": "requires_analysis",
                "confidence": "medium",
                "status": "requires_1c_review",
                "summary": _summary_for(change_type, object_name, area),
                "evidence_ref": f"{DEFAULT_COMPARE_REPO}#{relative_path}",
                "notes": "",
            }
        )
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_report(report_dir: Path, summary: dict[str, object]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Clean Rebase Summary",
        "",
        f"- compare_repo: `{summary['compare_repo']}`",
        f"- vendor_baseline: `{summary['vendor_baseline']}`",
        f"- target_cf: `{summary['target_cf']}`",
        f"- vendor files copied: {summary['vendor_files_copied']}",
        f"- target files copied: {summary['target_files_copied']}",
        f"- skipped vendor files: {summary['vendor_files_skipped']}",
        f"- skipped target files: {summary['target_files_skipped']}",
        f"- clean diff entries: {summary['diff_entries']}",
        "",
        "Excluded noise:",
        "",
    ]
    lines.extend(f"- `{name}`" for name in sorted(EXCLUDED_NAMES))
    lines.extend(f"- `*{suffix}`" for suffix in sorted(EXCLUDED_SUFFIXES))
    (report_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    manifest = read_toml(repo_path(root, "project.toml"))
    vendor_relative = toml_value(manifest, "paths", "vendor_baseline")
    target_relative = toml_value(manifest, "paths", "target_cf")
    if not vendor_relative or not target_relative:
        raise ValueError("project.toml must define paths.vendor_baseline and paths.target_cf")

    vendor = repo_path(root, vendor_relative)
    target = repo_path(root, target_relative)
    compare_relative = args.compare_repo or toml_value(manifest, "paths", "compare_repo") or DEFAULT_COMPARE_REPO
    if compare_relative == ".":
        compare_relative = DEFAULT_COMPARE_REPO
    compare_repo = repo_path(root, compare_relative)
    report_dir = repo_path(root, args.report_dir or DEFAULT_REPORT_DIR)

    shutil.rmtree(compare_repo, ignore_errors=True)
    compare_repo.mkdir(parents=True, exist_ok=True)
    init = _run(["git", "init", "-q"], compare_repo)
    if init.returncode != 0:
        raise RuntimeError(f"git init failed\nstdout:\n{init.stdout}\nstderr:\n{init.stderr}")
    top = _run(["git", "rev-parse", "--show-toplevel"], compare_repo)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != compare_repo.resolve():
        raise RuntimeError(f"Clean-rebase git repository is not isolated: {top.stdout.strip()}")

    vendor_files, vendor_skipped = _copy_normalized_tree(vendor, compare_repo)
    _git_commit_all(compare_repo, "vendor baseline")
    tag_vendor = _run(["git", "tag", "vendor-baseline"], compare_repo)
    if tag_vendor.returncode != 0:
        raise RuntimeError(f"git tag vendor-baseline failed\nstdout:\n{tag_vendor.stdout}\nstderr:\n{tag_vendor.stderr}")

    target_files, target_skipped = _copy_normalized_tree(target, compare_repo)
    _git_commit_all(compare_repo, "target cf")
    tag_target = _run(["git", "tag", "target-cf"], compare_repo)
    if tag_target.returncode != 0:
        raise RuntimeError(f"git tag target-cf failed\nstdout:\n{tag_target.stdout}\nstderr:\n{tag_target.stderr}")

    entries = _diff_entries(compare_repo)
    _write_diff_inventory(root, entries)
    summary = {
        "compare_repo": compare_relative,
        "vendor_baseline": vendor_relative,
        "target_cf": target_relative,
        "vendor_files_copied": vendor_files,
        "target_files_copied": target_files,
        "vendor_files_skipped": vendor_skipped,
        "target_files_skipped": target_skipped,
        "diff_entries": len(entries),
        "diff_inventory": "analysis/indexes/diff-inventory.csv",
        "excluded_names": sorted(EXCLUDED_NAMES),
        "excluded_suffixes": sorted(EXCLUDED_SUFFIXES),
    }
    _write_report(report_dir, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0
