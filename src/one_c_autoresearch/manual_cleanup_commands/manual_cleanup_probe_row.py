#!/usr/bin/env python3
"""Probe one manual-cleanup row and return source-backed JSON facts."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from one_c_autoresearch.common import run_git as common_run_git
from one_c_autoresearch.manual_cleanup_commands.config import path as configured_path, ref as configured_ref


QUEUE = Path("analysis/detailed-register-reverse-review/manual-markup-queue.jsonl")
NESTED_REPO = configured_path("comparison_repo", "analysis/cache/noise/clean-rebase-v8unpack/repo")
CLEAN_REPO = configured_path("normalized_repo", "analysis/cache/clean-rebase/repo")
VENDOR_REF = configured_ref("vendor_ref", "HEAD^")
CUSTOMER_REF = configured_ref("customer_ref", "HEAD")
BINARY_SUFFIXES = {".bin", ".c1brace", ".c1b64", ".mxl"}
READABLE_SUFFIXES = {".json", ".xml", ".html", ".txt", ".bsl", ".os"}


KIND_DIRS = {
    "Catalog": "Catalogs",
    "Document": "Documents",
    "Report": "Reports",
    "DataProcessor": "DataProcessors",
    "CommonModule": "CommonModules",
    "InformationRegister": "InformationRegisters",
    "AccumulationRegister": "AccumulationRegisters",
    "AccountingRegister": "AccountingRegisters",
    "ChartOfAccounts": "ChartsOfAccounts",
    "ChartOfCharacteristicType": "ChartsOfCharacteristicTypes",
    "ChartOfCalculationTypes": "ChartsOfCalculationTypes",
    "BusinessProcess": "BusinessProcesses",
    "Task": "Tasks",
    "ExchangePlan": "ExchangePlans",
    "DocumentJournal": "DocumentJournals",
    "CommandGroup": "CommandGroups",
    "CommonAttribute": "CommonAttributes",
    "CommonCommand": "CommonCommands",
    "CommonForm": "CommonForms",
    "CommonPicture": "CommonPictures",
    "CommonTemplate": "CommonTemplates",
    "Constant": "Constants",
    "DocumentNumerators": "DocumentNumerators",
    "Enum": "Enums",
    "EventSubscription": "EventSubscriptions",
    "FilterCriterion": "FilterCriteria",
    "FunctionalOption": "FunctionalOptions",
    "Interface": "Interfaces",
    "Language": "Languages",
    "Role": "Roles",
    "ScheduledJob": "ScheduledJobs",
    "Sequences": "Sequences",
    "SessionParameter": "SessionParameters",
    "Style": "Styles",
    "StyleItem": "StyleItems",
    "Subsystem": "Subsystems",
    "WebService": "WebServices",
}


def detect_strategy(paths: list[str]) -> str:
    if paths and all("/Template/" in path and path.endswith("/Template.mxl") for path in paths):
        return "template_mxl"
    if paths and all(path.startswith("CommonTemplate/") for path in paths):
        return "common_template"
    if paths and all("/Form/" in path or "/ReportForm/" in path or "Form." in path for path in paths):
        return "form"
    if paths and all(path.endswith((".bsl", ".os")) for path in paths):
        return "bsl"
    if paths and all(path.endswith((".bin", ".c1brace", ".c1b64")) for path in paths):
        return "binary_payload"
    if paths and all(path.endswith((".wsdl", ".xsd")) or path.startswith(("WSReference/", "XDTOPackage/")) for path in paths):
        return "schema_contract"
    if paths and all(path.endswith((".json", ".xml", ".html")) for path in paths):
        return "metadata"
    return "unknown"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        rows.append(json.loads(obj) if isinstance(obj, str) else obj)
    return rows


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return common_run_git(repo, args)


def diff_name_only(repo: Path, paths: list[str]) -> list[str]:
    result = run_git(repo, ["diff", "--name-only", f"{VENDOR_REF}..{CUSTOMER_REF}", "--", *paths])
    return [line for line in result.stdout.splitlines() if line]


def ls_tree_paths(repo: Path, ref: str, prefix: str) -> list[str]:
    result = run_git(repo, ["ls-tree", "-r", "--name-only", ref, prefix])
    return [line for line in result.stdout.splitlines() if line]


def readable_peer_paths(paths: list[str]) -> list[str]:
    peers: set[str] = set()
    path_set = set(paths)
    for path in paths:
        if Path(path).suffix.lower() not in BINARY_SUFFIXES:
            continue
        parent = str(Path(path).parent)
        if parent == ".":
            continue
        for ref in (VENDOR_REF, CUSTOMER_REF):
            for candidate in ls_tree_paths(NESTED_REPO, ref, parent):
                if candidate in path_set:
                    continue
                if Path(candidate).suffix.lower() in READABLE_SUFFIXES:
                    peers.add(candidate)
    return sorted(peers)


def template_mxl_probe(paths: list[str]) -> dict:
    candidates = [template_mxl_candidates(path) for path in paths]
    semantic_paths = sorted({path for candidate in candidates for path in candidate["semantic"]})
    context_paths = sorted({path for candidate in candidates for path in candidate["context"]})
    clean_paths = sorted(set(semantic_paths + context_paths))
    semantic_diff_paths = diff_name_only(CLEAN_REPO, semantic_paths) if semantic_paths else []
    context_diff_paths = diff_name_only(CLEAN_REPO, context_paths) if context_paths else []
    return {
        "clean_candidate_paths": clean_paths,
        "semantic_candidate_paths": semantic_paths,
        "semantic_diff_paths": semantic_diff_paths,
        "context_candidate_paths": context_paths,
        "context_diff_paths": context_diff_paths,
        "clean_diff_paths": sorted(set(semantic_diff_paths + context_diff_paths)),
        "has_semantic_diff": bool(semantic_diff_paths),
        "has_context_diff": bool(context_diff_paths),
    }


def simple_clean_probe(paths: list[str]) -> dict:
    candidates = [simple_clean_candidates(path) for path in paths]
    semantic_paths = sorted({path for candidate in candidates for path in candidate["semantic"]})
    context_paths = sorted({path for candidate in candidates for path in candidate["context"]})
    clean_paths = sorted(set(semantic_paths + context_paths))
    semantic_diff_paths = diff_name_only(CLEAN_REPO, semantic_paths) if semantic_paths else []
    context_diff_paths = diff_name_only(CLEAN_REPO, context_paths) if context_paths else []
    return {
        "clean_candidate_paths": clean_paths,
        "semantic_candidate_paths": semantic_paths,
        "semantic_diff_paths": semantic_diff_paths,
        "context_candidate_paths": context_paths,
        "context_diff_paths": context_diff_paths,
        "clean_diff_paths": sorted(set(semantic_diff_paths + context_diff_paths)),
        "has_semantic_diff": bool(semantic_diff_paths),
        "has_context_diff": bool(context_diff_paths),
    }


def simple_clean_candidates(v8_path: str) -> dict[str, list[str]]:
    parts = v8_path.split("/")
    if len(parts) < 2:
        return {"semantic": [], "context": []}
    kind, owner = parts[0], parts[1]
    if kind == "XDTOPackage":
        return {"semantic": [f"XDTOPackages/{owner}.xml"], "context": []}
    if kind == "WSReference":
        return {"semantic": [f"WSReferences/{owner}.xml"], "context": []}
    root = KIND_DIRS.get(kind)
    if not root:
        return {"semantic": [], "context": []}

    context = [f"{root}/{owner}.xml"]
    semantic: list[str] = []
    name = Path(v8_path).name
    suffix = Path(v8_path).suffix
    if kind == "CommonModule" and name.endswith((".bsl", ".os")):
        semantic.append(f"{root}/{owner}/Ext/Module.bsl")
    elif kind == "CommonCommand" and name.endswith((".bsl", ".os")):
        semantic.append(f"{root}/{owner}/Ext/CommandModule.bsl")
    elif kind == "CommonForm":
        semantic.extend([f"{root}/{owner}.xml", f"{root}/{owner}/Ext/Form.xml", f"{root}/{owner}/Ext/Form/Module.bsl"])
    elif kind == "CommonTemplate":
        semantic.extend([f"{root}/{owner}.xml", f"{root}/{owner}/Ext/Template.xml"])
    elif kind == "CommonPicture":
        semantic.extend([f"{root}/{owner}.xml", f"{root}/{owner}/Ext/Picture.xml"])
    elif name.endswith(".obj.bsl"):
        semantic.append(f"{root}/{owner}/Ext/ObjectModule.bsl")
    elif name.endswith(".mgr.bsl"):
        semantic.append(f"{root}/{owner}/Ext/ManagerModule.bsl")
    elif any(part.endswith("Form") for part in parts[2:3]) and len(parts) >= 4:
        form_name = parts[3]
        semantic.append(f"{root}/{owner}/Forms/{form_name}.xml")
    elif suffix in {".json", ".xml", ".html"}:
        semantic.append(f"{root}/{owner}.xml")
    return {"semantic": semantic, "context": context}


def template_mxl_candidates(v8_path: str) -> dict[str, list[str]]:
    parts = v8_path.split("/")
    if len(parts) < 2:
        return {"semantic": [], "context": []}
    root = KIND_DIRS.get(parts[0])
    if not root:
        return {"semantic": [], "context": []}
    owner = parts[1]
    context = [f"{root}/{owner}.xml"]
    semantic = []
    if len(parts) >= 5 and parts[2] == "Template":
        template_name = parts[3]
        semantic.extend(
            [
                f"{root}/{owner}/Templates/{template_name}.xml",
                f"{root}/{owner}/Templates/{template_name}/Ext/Template.xml",
            ]
        )
    context.extend(
        [
            f"{root}/{owner}/Ext/ObjectModule.bsl",
            f"{root}/{owner}/Ext/ManagerModule.bsl",
        ]
    )
    return {"semantic": semantic, "context": context}


def generic_probe(paths: list[str]) -> dict:
    return {
        "clean_candidate_paths": [],
        "semantic_candidate_paths": [],
        "semantic_diff_paths": [],
        "context_candidate_paths": [],
        "context_diff_paths": [],
        "clean_diff_paths": [],
        "has_semantic_diff": False,
        "has_context_diff": False,
    }


def find_row(row_id: str) -> dict:
    for row in read_jsonl(QUEUE):
        if row.get("row_id") == row_id:
            return row
    raise SystemExit(f"row not found: {row_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--row-id")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def self_test() -> None:
    assert detect_strategy(["Catalog/X/Template/Y/Template.mxl"]) == "template_mxl"
    assert detect_strategy(["Report/X/Template/Y/Template.bin"]) == "binary_payload"
    assert Path("Catalog/X/Template/Y/Template.mxl").suffix.lower() in BINARY_SUFFIXES
    assert Path("Catalog/X/Template/Y/Template.json").suffix.lower() in READABLE_SUFFIXES


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        print("self-test: ok")
        return 0
    if not args.row_id:
        raise SystemExit("--row-id is required unless --self-test is used")
    row = find_row(args.row_id)
    paths = row.get("paths", [])
    strategy = detect_strategy(paths)
    nested_diff_paths = diff_name_only(NESTED_REPO, paths)
    strategy_result = template_mxl_probe(paths) if strategy == "template_mxl" else simple_clean_probe(paths)
    semantic_diff_paths = strategy_result["semantic_diff_paths"]
    context_diff_paths = strategy_result["context_diff_paths"]
    clean_diff_paths = strategy_result["clean_diff_paths"]
    readable_peers = readable_peer_paths(paths)
    if not nested_diff_paths:
        suggested_decision = "manual_review"
        decision_reason = "no nested diff remains"
    elif not strategy_result["clean_candidate_paths"]:
        suggested_decision = "manual_review"
        decision_reason = f"no normalized candidate paths for strategy: {strategy}"
    elif strategy != "template_mxl":
        suggested_decision = "manual_review"
        decision_reason = f"non-template strategy requires source review: {strategy}"
    elif semantic_diff_paths:
        suggested_decision = "keep_customization"
        decision_reason = "strategy semantic representation differs"
    else:
        suggested_decision = "remove_noise"
        decision_reason = "template semantic representation is unchanged; binary/context diff is ignored"
    result = {
        "row_id": args.row_id,
        "task_id": row.get("central_task_id") or row.get("task_id"),
        "strategy": strategy,
        "stable_diff_ids": row.get("stable_diff_ids", []),
        "old_diff_ids": row.get("old_diff_ids", []),
        "paths": paths,
        "readable_peer_paths": readable_peers,
        "has_readable_peer": bool(readable_peers),
        "key_objects": row.get("key_objects", ""),
        "nested_diff_paths": nested_diff_paths,
        "clean_candidate_paths": strategy_result["clean_candidate_paths"],
        "semantic_candidate_paths": strategy_result["semantic_candidate_paths"],
        "semantic_diff_paths": semantic_diff_paths,
        "context_candidate_paths": strategy_result["context_candidate_paths"],
        "context_diff_paths": context_diff_paths,
        "clean_diff_paths": clean_diff_paths,
        "has_nested_diff": bool(nested_diff_paths),
        "has_normalized_diff": bool(clean_diff_paths),
        "has_semantic_diff": bool(semantic_diff_paths),
        "has_context_diff": bool(context_diff_paths),
        "suggested_decision": suggested_decision,
        "decision_reason": decision_reason,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
