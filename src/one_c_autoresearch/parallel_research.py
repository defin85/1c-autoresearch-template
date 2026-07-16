from __future__ import annotations

import argparse
import contextlib
import copy
import concurrent.futures
import functools
import json
import os
import re
import shutil
import shlex
import signal
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .common import file_sha256, path_sha256, read_json, read_jsonl, read_toml, repo_path, sha256, utc_now_iso, write_json, write_jsonl
from .customization_registry import (
    ITEMS_JSONL as CUS_ITEMS,
    REGISTRY_DIR as CUS_ROOT,
    export_registry,
    load_registry_index,
    stable_customization_id,
    validate_registry,
    write_summary as write_cus_summary,
)
from .migration_requirements import LINKS as MRQ_LINKS, REQUIREMENTS as MRQ_REQUIREMENTS, ROOT as MRQ_ROOT, load_index as load_mrq_index, replace_graph as replace_mrq_graph, stable_requirement_id, validate_graph as validate_mrq_graph
from .queue import queue_lock
from .queue_seed import stable_task_id


ROOT = "analysis/parallel-research"
RUNS = f"{ROOT}/runs"
LEDGER = f"{ROOT}/apply-ledger.jsonl"
CUS_PROPOSALS = f"{ROOT}/cus-proposals.jsonl"
TRACE_ROOT = "analysis/cache/parallel-research-traces"
WORKSPACE_ROOT = "analysis/cache/parallel-research-workspaces"
DEFAULT_COMPARISON_REPO = "analysis/cache/noise/clean-rebase-v8unpack/repo"
# Backward-compatible import name; projects should configure comparison_repo.
V8UNPACK_REPO = DEFAULT_COMPARISON_REPO
V8UNPACK_PAIR_CACHE = "analysis/cache/parallel-research-v8unpack-pairs"
QUEUE = "analysis/queue/tasks.jsonl"
MRQ_BACKLOG = "analysis/migration-requirements/backlog-exceptions.jsonl"
COORDINATOR_LOCK = f"{ROOT}/.coordinator.lock"
APPLY_LOCK = f"{ROOT}/.apply.lock"
SCHEMA_VERSION = "parallel-research/v1"
RUNNER_VERSION = "14"
UNIT_STATES = {"planned", "running", "completed", "rejected", "applied", "follow_up", "rolled_back", "timeout"}
OUTCOMES = {"confirmed", "needs_followup", "needs_infobase_data"}
CONFIDENCES = {"high", "medium", "low"}
MAX_WORKERS = 20
DEFAULT_TIMEOUT = 1800
DEFAULT_MODEL = ""
FORBIDDEN_COMMANDS = re.compile(r"(?:^|\s)(?:apply_patch|git\s+(?:commit|reset|checkout)|rm\s+-rf|(?:tee|mv|cp)\s+|sed\s+-i|python\S*\s+-m\s+one_c_autoresearch\s+queue\s+set-status)", re.I)
FORBIDDEN_GLOBAL_READS = (
    "analysis/queue/tasks.jsonl",
    "analysis/customization-registry/customization-items.jsonl",
    "analysis/customization-registry/customization-evidence.jsonl",
    "analysis/migration-requirements/backlog-exceptions.jsonl",
    "analysis/migration-requirements/requirements.jsonl",
    "analysis/migration-requirements/requirement-links.jsonl",
)
KIND_FOLDERS = {
    "Catalog": "Catalogs", "Document": "Documents", "DataProcessor": "DataProcessors",
    "Report": "Reports", "InformationRegister": "InformationRegisters",
    "AccumulationRegister": "AccumulationRegisters", "AccountingRegister": "AccountingRegisters",
    "ChartOfAccounts": "ChartsOfAccounts", "Enum": "Enums", "CommonModule": "CommonModules",
    "CommonForm": "CommonForms", "CommonTemplate": "CommonTemplates", "CommonPicture": "CommonPictures",
    "ExchangePlan": "ExchangePlans", "ChartOfCharacteristicTypes": "ChartsOfCharacteristicTypes",
    "ChartOfCharacteristicType": "ChartsOfCharacteristicTypes",
    "BusinessProcess": "BusinessProcesses",
}
V8UNPACK_KINDS = {value.rstrip("s"): key for key, value in KIND_FOLDERS.items()}
V8UNPACK_KINDS.update({key: key for key in KIND_FOLDERS})
_ACTIVE_WORKERS: dict[int, subprocess.Popen[str]] = {}
_ACTIVE_WORKERS_LOCK = threading.Lock()


class ParallelResearchError(RuntimeError):
    pass


def _settings(root: Path) -> dict[str, Any]:
    manifest = read_toml(root / "project.toml") if (root / "project.toml").is_file() else {}
    value = manifest.get("parallel_research") or {}
    return value if isinstance(value, dict) else {}


def _source_profile(root: Path) -> dict[str, Any]:
    settings = _settings(root)
    return {
        "comparison_repo": str(settings.get("comparison_repo") or DEFAULT_COMPARISON_REPO),
        "vendor_ref": str(settings.get("vendor_ref") or "HEAD^"),
        "customer_ref": str(settings.get("customer_ref") or "HEAD"),
        "vendor_drift_markers": [str(value) for value in settings.get("vendor_drift_markers") or []],
    }


def _sha(payload: Any) -> str:
    return sha256(payload)


def _file_sha(path: Path) -> str:
    return file_sha256(path)


def _path_sha(path: Path) -> str:
    return path_sha256(path)


def _read_json(path: Path) -> dict[str, Any]:
    return read_json(path)


def _write_json(path: Path, payload: Any, mode: int | None = None) -> None:
    write_json(path, payload, mode)


def _rows(path: Path) -> list[dict[str, Any]]:
    return [row for _, row in read_jsonl(path)] if path.exists() else []


def _process_start(pid: int) -> str:
    path = Path(f"/proc/{pid}/stat")
    if not path.exists():
        return ""
    parts = path.read_text(encoding="utf-8").split()
    return parts[21] if len(parts) > 21 else ""


class ProcessLock:
    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"run_id": self.run_id, "pid": os.getpid(), "process_start": _process_start(os.getpid()), "created_at": utc_now_iso()}
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    current = _read_json(self.path)
                except (OSError, ValueError, json.JSONDecodeError):
                    current = {}
                pid = int(current.get("pid") or 0)
                if pid and current.get("process_start") == _process_start(pid):
                    raise ParallelResearchError(f"parallel-research lock is held: {self.path}")
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            return self

    def __exit__(self, *_: object) -> None:
        current = _read_json(self.path)
        if current.get("pid") == os.getpid() and current.get("run_id") == self.run_id:
            self.path.unlink(missing_ok=True)


def stable_unit_id(task_id: str, adapter: str, entities: list[str], fingerprints: dict[str, str]) -> str:
    return f"PRU-{_sha({'task_id': task_id, 'adapter': adapter, 'entities': sorted(entities), 'fingerprints': fingerprints})[:12].upper()}"


def allocate_canonical_id(kind: str, semantic_key: str) -> str:
    if kind == "requirement":
        return stable_requirement_id(semantic_key)
    if kind == "customization":
        return stable_customization_id(semantic_key)
    if kind == "task":
        return stable_task_id("Q-PR", semantic_key)
    if kind == "lineage":
        return f"LIN-{_sha(semantic_key)[:12].upper()}"
    raise ParallelResearchError(f"unsupported canonical id kind: {kind}")


def _task_hash(task: dict[str, Any]) -> str:
    return _sha(task)


def _adapter(task: dict[str, Any]) -> str:
    explicit = str(task.get("parallel_adapter") or "")
    if explicit in {"review_evidence", "uncovered_cus", "conflicting_cus"}:
        return explicit
    target = str(task.get("review_target_id") or "").lower()
    reasons = " ".join(str(item) for item in task.get("selection_reasons") or []).lower()
    if "uncovered" in target or "uncovered" in reasons:
        return "uncovered_cus"
    if "conflict" in target or "split candidate" in reasons:
        return "conflicting_cus"
    if task.get("type") == "review":
        return "review_evidence"
    return ""


def _source_fingerprints(root: Path, task: dict[str, Any], adapter: str) -> dict[str, str]:
    sources = {str(value).split(":", 1)[0] for value in task.get("source_artifacts") or [] if value}
    sources.add(QUEUE)
    if adapter in {"uncovered_cus", "conflicting_cus"}:
        sources.update({"analysis/customization-registry/customization-items.jsonl", "analysis/customization-registry/customization-evidence.jsonl", "analysis/migration-requirements/requirements.jsonl", "analysis/migration-requirements/requirement-links.jsonl"})
    return {relative: _path_sha(repo_path(root, relative)) for relative in sorted(sources)}


@functools.lru_cache(maxsize=4096)
def _git_tree_files(repo: str, ref: str, prefix: str) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", repo, "ls-tree", "-r", "--name-only", ref, "--", prefix],
        text=True,
        capture_output=True,
    )
    return tuple(line for line in result.stdout.splitlines() if line) if result.returncode == 0 else ()


def _materialize_git_blob(root: Path, ref: str, relative: str, role: str) -> str:
    repo = repo_path(root, _source_profile(root)["comparison_repo"])
    blob = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{ref}:{relative}"],
        text=True,
        capture_output=True,
    )
    if blob.returncode:
        return ""
    destination = repo_path(root, f"{V8UNPACK_PAIR_CACHE}/{role}/{blob.stdout.strip()}/{relative}")
    if not destination.is_file():
        content = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{relative}"], capture_output=True)
        if content.returncode:
            return ""
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content.stdout)
    return destination.relative_to(root).as_posix()


@functools.lru_cache(maxsize=4096)
def _v8unpack_usage_files(repo: str, term: str, ref: str = "HEAD") -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", repo, "grep", "-l", "-F", term, ref, "--", "*.bsl"],
        text=True,
        capture_output=True,
    )
    prefix = f"{ref}:"
    return tuple(line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix))[:20] if result.returncode in {0, 1} else ()


def _v8unpack_part_files(root: Path, evidence: dict[str, Any]) -> list[str]:
    metadata_object = str(evidence.get("metadata_object") or "")
    special = {
        "Configuration.БухгалтерияПредприятия": ["Configuration.json"],
        "ConfigDumpInfo.ConfigDumpInfo": ["Configuration.4.json"],
        "Ext.OrdinaryApplicationModule": ["Configuration.app.bsl"],
        "Ext.SessionModule": ["Configuration.seance.bsl"],
        "Ext.ExternalConnectionModule": ["Configuration.con.bsl"],
    }
    if metadata_object in special:
        return special[metadata_object]
    if "." not in metadata_object:
        return []
    kind, name = metadata_object.split(".", 1)
    folder = V8UNPACK_KINDS.get(kind, kind)
    prefix = f"{folder}/{name}"
    profile = _source_profile(root)
    repo = str(repo_path(root, profile["comparison_repo"]))
    files = set(_git_tree_files(repo, profile["vendor_ref"], prefix)) | set(_git_tree_files(repo, profile["customer_ref"], prefix))
    part_kind = str(evidence.get("part_kind") or "object").lower()
    part_name = str(evidence.get("part_name") or "")

    def relevant(relative: str) -> bool:
        tail = relative[len(prefix):].lstrip("/")
        if relative.endswith((".bin", ".id.json")):
            return False
        if part_kind == "form":
            return (kind == "CommonForm" or (part_name and tail.startswith(tuple(f"{candidate}/{part_name}/" for candidate in ("Form", f"{folder}Form", f"{kind}Form"))))) and relative.endswith((".json", ".bsl"))
        if part_kind == "template":
            return part_name and tail.startswith(f"Template/{part_name}/") and relative.endswith((".json", ".mxl", ".bsl"))
        if part_kind == "module":
            return "/" not in tail and relative.endswith(".bsl")
        return "/" not in tail and relative.endswith((".json", ".bsl"))

    return sorted(relative for relative in files if relevant(relative))


def _configuration_source_pairs(root: Path, evidence: dict[str, Any]) -> list[dict[str, str]]:
    paths = read_toml(root / "project.toml").get("paths") or {}
    roots = {"customer": str(paths.get("target_cf") or "").rstrip("/"), "vendor": str(paths.get("vendor_baseline") or "").rstrip("/")}
    metadata_object = str(evidence.get("metadata_object") or "")
    raw = str(evidence.get("source_path") or evidence.get("part_path") or "").split(":", 1)[0]
    relative = raw
    for source_root in roots.values():
        if source_root and relative.startswith(source_root + "/"):
            relative = relative[len(source_root) + 1:]
    if relative.endswith("Предустановленные данные.bin") and "." in metadata_object:
        kind, name = metadata_object.split(".", 1)
        folder = KIND_FOLDERS.get(kind)
        relative = f"{folder}/{name}/Ext/Predefined.xml" if folder else ""
    elif relative.startswith("Modules/") and "." in metadata_object:
        kind, name = metadata_object.split(".", 1)
        folder = KIND_FOLDERS.get(kind)
        part_name = str(evidence.get("part_name") or "")
        relative = f"{folder}/{name}/Ext/{part_name}.bsl" if folder and part_name else ""
    elif not relative and "." in metadata_object:
        kind, name = metadata_object.split(".", 1)
        folder = KIND_FOLDERS.get(kind)
        relative = f"{folder}/{name}.xml" if folder else ""
    direct = {
        role: f"{source_root}/{relative}"
        for role, source_root in roots.items()
        if source_root and relative and repo_path(root, f"{source_root}/{relative}").is_file()
    }
    template_match = re.match(r"^([^/]+)/([^/]+)/Templates/([^/]+)/", relative)
    if direct and not template_match:
        return [direct]
    raw_source = str(evidence.get("source_path") or "")
    exact = raw_source.split("#", 1)[-1] if "#" in raw_source else raw_source
    profile = _source_profile(root)
    marker = f"{profile['comparison_repo']}/"
    if exact.startswith(marker):
        exact = exact[len(marker):]
    exact = exact.lstrip("/")
    files = [] if not exact or exact.endswith((".bin", ".id.json")) else [exact]
    files.extend(_v8unpack_part_files(root, evidence))
    if template_match:
        folder, owner, template = template_match.groups()
        kind = {value: key for key, value in KIND_FOLDERS.items()}.get(folder, folder.rstrip("s"))
        prefix = f"{kind}/{owner}/Template/{template}"
        repo = str(repo_path(root, profile["comparison_repo"]))
        files.extend(
            path for path in set(_git_tree_files(repo, profile["vendor_ref"], prefix)) | set(_git_tree_files(repo, profile["customer_ref"], prefix))
            if path.endswith((".json", ".mxl", ".bsl")) and not path.endswith(".id.json")
        )
    pairs = [direct] if direct else []
    for source in dict.fromkeys(files):
        pair = {
            role: path
            for role, path in (
                ("customer", _materialize_git_blob(root, profile["customer_ref"], source, "customer")),
                ("vendor", _materialize_git_blob(root, profile["vendor_ref"], source, "vendor")),
            )
            if path
        }
        if pair:
            pairs.append(pair)
    return pairs


def _xml_without_standard_attributes(path: Path) -> tuple[str, int]:
    root = ET.parse(path).getroot()
    removed = 0
    for parent in root.iter():
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] == "StandardAttributes":
                parent.remove(child)
                removed += 1
    return ET.canonicalize(ET.tostring(root, encoding="unicode"), strip_text=True), removed


def _technical_xml_serialization_difference(customer: Path, vendor: Path) -> bool:
    if customer.suffix.lower() != ".xml" or vendor.suffix.lower() != ".xml":
        return False
    try:
        customer_xml, customer_removed = _xml_without_standard_attributes(customer)
        vendor_xml, vendor_removed = _xml_without_standard_attributes(vendor)
    except ET.ParseError:
        return False
    if bool(customer_removed or vendor_removed) and customer_xml == vendor_xml:
        return True
    try:
        roots = [ET.parse(path).getroot() for path in (customer, vendor)]
    except ET.ParseError:
        return False
    for root in roots:
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == "Attribute":
                element.attrib.pop("uuid", None)
            children = list(element)
            if len(children) > 1 and all(child.tag.rsplit("}", 1)[-1] == "Attribute" for child in children):
                children.sort(key=lambda child: next((node.text or "" for node in child.iter() if node.tag.rsplit("}", 1)[-1] == "Name"), ""))
                element[:] = children
    normalized = [ET.canonicalize(ET.tostring(root, encoding="unicode"), strip_text=True) for root in roots]
    return normalized[0] == normalized[1]


def _source_comparisons(root: Path, evidence_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons = []
    for evidence in evidence_rows:
        for pair in _configuration_source_pairs(root, evidence):
            customer = pair.get("customer", "")
            vendor = pair.get("vendor", "")
            if customer and vendor:
                customer_path, vendor_path = repo_path(root, customer), repo_path(root, vendor)
                status = "identical" if _file_sha(customer_path) == _file_sha(vendor_path) else "technical_noise" if _technical_xml_serialization_difference(customer_path, vendor_path) else "changed"
            else:
                status = "customer_only" if customer else "vendor_only"
            comparisons.append({
                "item_id": evidence.get("item_id"),
                "metadata_object": evidence.get("metadata_object"),
                "part_kind": evidence.get("part_kind"),
                "part_name": evidence.get("part_name"),
                "declared_change_type": evidence.get("change_type"),
                "comparison_status": status,
                "customer_path": customer,
                "vendor_path": vendor,
            })
    identical_xml_objects = {
        str(row.get("metadata_object") or "")
        for row in comparisons
        if row.get("comparison_status") == "identical" and str(row.get("customer_path") or "").endswith(".xml")
    }
    for row in comparisons:
        if row.get("comparison_status") == "changed" and str(row.get("customer_path") or "").endswith(".json") and row.get("metadata_object") in identical_xml_objects:
            row["comparison_status"] = "technical_noise"
    return comparisons


def _vendor_drift_candidate(comparisons: list[dict[str, Any]], markers: list[str] | tuple[str, ...] = ()) -> bool:
    customer_only = [row for row in comparisons if row.get("comparison_status") == "customer_only"]
    return bool(customer_only) and any(
        marker in str(row.get("customer_path") or "")
        for row in customer_only
        for marker in markers
    )


def _allowed_sources(root: Path, task: dict[str, Any], adapter: str, entity_ids: list[str], registry: dict[str, Any] | None = None) -> list[str]:
    sources = {str(value).split(":", 1)[0] for value in task.get("source_artifacts") or [] if value}
    sources.difference_update(FORBIDDEN_GLOBAL_READS)
    for relative in sources:
        normalized = _safe_path(root, relative)
        if repo_path(root, normalized).is_dir() and any(
            forbidden.startswith(normalized.rstrip("/") + "/") for forbidden in FORBIDDEN_GLOBAL_READS
        ):
            raise ParallelResearchError(f"source directory contains a forbidden global registry: {relative}")
    if adapter in {"uncovered_cus", "conflicting_cus"}:
        registry = registry or load_registry_index(root)
        for customization_id in entity_ids:
            item = registry["by_id"].get(customization_id, {})
            fallback_object = next(iter(item.get("source_bp20_objects") or []), "")
            for evidence in registry["evidence_by_item"].get(customization_id, []):
                comparable = {**evidence, "metadata_object": evidence.get("metadata_object") or fallback_object}
                for pair in _configuration_source_pairs(root, comparable):
                    sources.update(pair.values())
                metadata_object = str(comparable.get("metadata_object") or "")
                if metadata_object.startswith("Template."):
                    term = metadata_object.split(".", 1)[1]
                    profile = _source_profile(root)
                    repo = str(repo_path(root, profile["comparison_repo"]))
                    sources.update(filter(None, (_materialize_git_blob(root, profile["customer_ref"], path, "customer") for path in _v8unpack_usage_files(repo, term, profile["customer_ref"]))))
                value = str(evidence.get("source_path") or evidence.get("part_path") or "")
                if value:
                    relative = value.split(":", 1)[0]
                    candidates = [relative, f"analysis/cache/noise/publishable-clean-v8unpack/repo/{relative}"]
                    resolved = next((item for item in candidates if repo_path(root, item).exists()), "")
                    if resolved:
                        sources.add(resolved)
    expanded = set()
    for value in sources:
        relative = _safe_path(root, value)
        path = repo_path(root, relative)
        if path.is_dir():
            expanded.update(item.relative_to(root).as_posix() for item in path.rglob("*") if item.is_file())
        elif path.is_file():
            expanded.add(relative)
    return sorted(expanded)


def _phase1_conflicting_cus(links: list[dict[str, Any]]) -> list[str]:
    owners: dict[str, set[str]] = {}
    for link in links:
        if link.get("role") == "primary" or link.get("effort_owner"):
            owners.setdefault(str(link.get("customization_id") or ""), set()).add(str(link.get("requirement_id") or ""))
    return sorted(cid for cid, values in owners.items() if cid and len(values) > 1)


def _phase1_goal_complete(coverage: dict[str, Any], counts: dict[str, int]) -> bool:
    return not coverage.get("errors") and not counts["uncovered"] and not counts["conflicts"]


def _conflicting_cus(root: Path) -> list[str]:
    return _phase1_conflicting_cus(load_mrq_index(root)["links"])


def _stable_follow_up_ids(root: Path) -> set[str]:
    latest = {}
    for row in _rows(repo_path(root, CUS_PROPOSALS)):
        if row.get("adapter") == "uncovered_cus" and row.get("customization_id"):
            latest[str(row["customization_id"])] = row
    needed = {str(row.get("unit_id")) for row in latest.values() if (row.get("proposal") or {}).get("mutation_type") == "follow_up"}
    units = {}
    runs = repo_path(root, RUNS)
    for run_dir in runs.iterdir() if runs.exists() else []:
        manifest = _read_json(run_dir / "manifest.json")
        for unit in manifest.get("units") or []:
            if unit.get("unit_id") in needed:
                units[str(unit["unit_id"])] = unit
    mutable = {QUEUE, MRQ_BACKLOG, MRQ_LINKS, MRQ_REQUIREMENTS, CUS_ITEMS}
    stable = set()
    hashes = {}
    for cid, row in latest.items():
        if (row.get("proposal") or {}).get("mutation_type") != "follow_up":
            continue
        unit = units.get(str(row.get("unit_id")))
        for relative in unit.get("source_fingerprints", {}) if unit else []:
            if relative not in mutable and relative not in hashes:
                hashes[relative] = _path_sha(repo_path(root, relative))
        if unit and unit.get("runner_version") == RUNNER_VERSION and all(relative in mutable or hashes[relative] == expected for relative, expected in unit.get("source_fingerprints", {}).items()):
            stable.add(cid)
    return stable


def _entities(root: Path, task: dict[str, Any], adapter: str) -> list[str]:
    explicit = [str(value) for value in task.get("parallel_entities") or [] if value]
    if adapter == "review_evidence":
        return sorted(set(explicit)) if explicit else [str(task["id"])]
    if adapter == "uncovered_cus":
        candidates = explicit or [str(row.get("customization_id")) for row in _rows(repo_path(root, MRQ_BACKLOG)) if row.get("customization_id")]
    elif adapter == "conflicting_cus":
        candidates = explicit or _conflicting_cus(root)
    else:
        return []
    registry = load_registry_index(root)
    stable_follow_up = _stable_follow_up_ids(root) if adapter == "uncovered_cus" else set()
    return sorted({cid for cid in candidates if cid not in stable_follow_up and registry["by_id"].get(cid, {}).get("scope_status") == "included"})


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def plan_run(root: Path, task_ids: list[str] | None = None, unit_size: int = 3, limit: int = 0, write: bool = False) -> dict[str, Any]:
    root = root.resolve()
    unit_size = unit_size or int(_settings(root).get("unit_size") or 3)
    if unit_size < 1:
        raise ParallelResearchError("unit_size must be positive")
    tasks = _rows(repo_path(root, QUEUE))
    selected_ids = set(task_ids or [])
    candidates = [task for task in tasks if task.get("status") == "pending" and (not selected_ids or str(task.get("id")) in selected_ids)]
    unsupported = []
    units = []
    ownership: set[str] = set()
    output_ownership: dict[str, str] = {}
    registry_index: dict[str, Any] | None = None
    aggregate_last: dict[str, str] = {}
    for task in candidates:
        adapter = _adapter(task)
        if not adapter:
            unsupported.append({"task_id": task.get("id"), "reason": "unsupported_adapter"})
            continue
        entities = _entities(root, task, adapter)
        if not entities:
            unsupported.append({"task_id": task.get("id"), "reason": "no_explicit_entities"})
            continue
        base_fingerprints = _source_fingerprints(root, task, adapter)
        if adapter in {"uncovered_cus", "conflicting_cus"} and registry_index is None:
            registry_index = load_registry_index(root)
        for output in task.get("expected_outputs") or []:
            owner = output_ownership.get(str(output))
            if owner and owner != str(task["id"]):
                raise ParallelResearchError(f"expected output overlap: {output} ({owner}, {task['id']})")
            output_ownership[str(output)] = str(task["id"])
        for chunk in _chunks(entities, 1 if adapter == "review_evidence" else unit_size):
            duplicate = sorted(set(chunk) & ownership)
            if duplicate:
                raise ParallelResearchError(f"duplicate entity ownership: {duplicate}")
            ownership.update(chunk)
            allowed_sources = _allowed_sources(root, task, adapter, chunk, registry_index)
            fingerprints = {**base_fingerprints, **{path: _path_sha(repo_path(root, path)) for path in allowed_sources}}
            unit_id = stable_unit_id(str(task["id"]), adapter, chunk, fingerprints)
            aggregate = QUEUE if adapter == "review_evidence" else MRQ_LINKS
            dependency = aggregate_last.get(aggregate, "") if adapter != "review_evidence" else ""
            unit = {
                "schema_version": SCHEMA_VERSION,
                "runner_version": RUNNER_VERSION,
                "unit_id": unit_id,
                "task_id": task["id"],
                "expected_task_status": task["status"],
                "task_hash": _task_hash(task),
                "adapter": adapter,
                "entity_ids": chunk,
                "ownership_keys": [f"entity:{value}" for value in chunk],
                "conflict_keys": [f"task:{task['id']}", f"aggregate:{aggregate}"],
                "apply_after": [dependency] if dependency else [],
                "allowed_sources": allowed_sources,
                "expected_outputs": list(task.get("expected_outputs") or []),
                "source_fingerprints": fingerprints,
                "state": "planned",
            }
            units.append(unit)
            if adapter != "review_evidence":
                aggregate_last[aggregate] = unit_id
    truncated = bool(limit and len(units) > limit)
    if limit:
        units = units[:limit]
    plan_core = {"schema_version": SCHEMA_VERSION, "runner_version": RUNNER_VERSION, "units": units, "unsupported": unsupported, "truncated": truncated}
    run_id = f"PR-{_sha(plan_core)[:12].upper()}"
    manifest = {**plan_core, "run_id": run_id, "created_at": utc_now_iso(), "unit_size": unit_size}
    if write:
        run_dir = repo_path(root, f"{RUNS}/{run_id}")
        if run_dir.exists():
            existing = _read_json(run_dir / "manifest.json")
            if _sha({k: existing.get(k) for k in plan_core}) != _sha(plan_core):
                raise ParallelResearchError("existing run manifest differs; resume it or choose different inputs")
        else:
            _write_json(run_dir / "manifest.json", manifest)
            for unit in units:
                _write_json(run_dir / "states" / f"{unit['unit_id']}.json", {"unit_id": unit["unit_id"], "state": "planned", "updated_at": utc_now_iso()})
    return {"status": "ok", **manifest, "written": write}


def _load_manifest(root: Path, run_id: str) -> dict[str, Any]:
    manifest = _read_json(repo_path(root, f"{RUNS}/{run_id}/manifest.json"))
    if not isinstance(manifest, dict) or not manifest:
        raise ParallelResearchError(f"run not found: {run_id}")
    plan_core = {key: manifest.get(key) for key in ("schema_version", "runner_version", "units", "unsupported", "truncated")}
    expected_run_id = f"PR-{_sha(plan_core)[:12].upper()}"
    if run_id != expected_run_id or manifest.get("run_id") != expected_run_id:
        raise ParallelResearchError(f"run manifest integrity check failed: {run_id}")
    return manifest


def _ensure_mutable_run(root: Path, run_id: str) -> None:
    if repo_path(root, f"{RUNS}/{run_id}/compaction.json").exists():
        raise ParallelResearchError(f"compacted run is read-only: {run_id}")


def load_unit(root: Path, run_id: str, unit_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _load_manifest(root, run_id)
    unit = next((row for row in manifest.get("units", []) if row.get("unit_id") == unit_id), None)
    if unit is None:
        raise ParallelResearchError(f"unit not found: {unit_id}")
    return manifest, unit


def _safe_path(root: Path, relative: str) -> str:
    path = repo_path(root, relative).resolve()
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ParallelResearchError(f"path escapes repository: {relative}") from exc


def task_context(root: Path, run_id: str, unit_id: str, max_evidence: int = 20, shared: dict[str, Any] | None = None) -> dict[str, Any]:
    shared = shared or {}
    manifest = shared.get("manifest")
    units = shared.get("units")
    if manifest is None or units is None:
        manifest, unit = load_unit(root, run_id, unit_id)
    else:
        unit = units.get(unit_id)
        if unit is None:
            raise ParallelResearchError(f"unit not found: {unit_id}")
    tasks = shared.get("tasks") or {str(row.get("id")): row for row in _rows(repo_path(root, QUEUE))}
    task = tasks.get(str(unit["task_id"]), {})
    context: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "unit_id": unit_id,
        "adapter": unit["adapter"],
        "entity_ids": unit["entity_ids"],
        "task": {key: task.get(key) for key in ("id", "type", "title", "feature_id", "scope", "quality_gates", "expected_outputs", "evidence_sources", "selection_reasons", "open_questions")},
        "allowed_sources": [_safe_path(root, value) for value in unit["allowed_sources"]],
        "policy": {"read_only": True, "mcp_allowed": False, "web_allowed": False, "static_sources_first": True},
        "entities": [],
        "truncated": False,
    }
    if unit["adapter"] == "review_evidence":
        context["entities"] = [{"entity_id": unit["entity_ids"][0], "task_title": task.get("title", ""), "selection_reasons": task.get("selection_reasons") or []}]
    else:
        registry = shared.get("registry") or load_registry_index(root)
        mrq = shared.get("mrq") or load_mrq_index(root)
        for cid in unit["entity_ids"]:
            item = registry["by_id"].get(cid, {})
            evidence = registry["evidence_by_item"].get(cid, [])
            selected = evidence[:max_evidence]
            fallback_object = next(iter(item.get("source_bp20_objects") or []), "")
            comparisons = _source_comparisons(root, [{**row, "metadata_object": row.get("metadata_object") or fallback_object} for row in selected])
            statuses = {row["comparison_status"] for row in comparisons}
            assessment = "semantic_candidate" if statuses & {"changed", "customer_only"} else "no_semantic_diff" if statuses and statuses <= {"identical", "technical_noise"} else "insufficient"
            metadata_names = {str(row.get("metadata_object") or "").split(".", 1)[-1].lower() for row in selected}
            context["truncated"] = context["truncated"] or len(evidence) > len(selected)
            context["entities"].append({
                "entity_id": cid,
                "customization": item,
                "current_relations": mrq["links_by_cus"].get(cid, []),
                "evidence": selected,
                "evidence_total": len(evidence),
                "source_comparisons": comparisons,
                "comparison_assessment": assessment,
                "technical_object": any(name.startswith("mcp_") for name in metadata_names),
                "vendor_drift_candidate": _vendor_drift_candidate(comparisons, _source_profile(root)["vendor_drift_markers"]),
            })
    context["context_hash"] = _sha(context)
    context["manifest_hash"] = shared.get("manifest_hash") or _sha(manifest)
    return context


DECISION_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "run_id", "unit_id", "adapter", "context_hash", "entities"],
    "properties": {
        "schema_version": {"type": "string"},
        "run_id": {"type": "string"},
        "unit_id": {"type": "string"},
        "adapter": {"type": "string"},
        "context_hash": {"type": "string"},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["entity_id", "outcome", "confidence", "conclusion", "evidence", "proposal"],
                "properties": {
                    "entity_id": {"type": "string"},
                    "outcome": {"type": "string", "enum": sorted(OUTCOMES)},
                    "confidence": {"type": "string", "enum": sorted(CONFIDENCES)},
                    "conclusion": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["path", "line_start", "line_end", "summary"],
                            "properties": {"path": {"type": "string"}, "line_start": {"type": "integer"}, "line_end": {"type": "integer"}, "summary": {"type": "string"}},
                            "additionalProperties": False,
                        },
                    },
                    "proposal": {
                        "type": "object",
                        "properties": {
                            "mutation_type": {"type": "string"},
                            "requirement_id": {"type": ["string", "null"]},
                            "semantic_key": {"type": ["string", "null"]},
                            "title": {"type": ["string", "null"]},
                            "source_scenario": {"type": ["string", "null"]},
                            "migration_boundary": {"type": ["string", "null"]},
                            "target_solution": {"type": ["string", "null"]},
                            "acceptance_criteria": {"type": ["string", "null"]},
                            "risk": {"type": ["string", "null"]},
                            "specification_text": {"type": ["string", "null"]},
                            "role": {"type": ["string", "null"]},
                            "rationale": {"type": ["string", "null"]},
                            "acceptance_refs": {"type": ["array", "null"], "items": {"type": "string"}},
                            "subject_tags": {"type": ["array", "null"], "items": {"type": "string"}},
                            "bp30_coverage": {"type": ["string", "null"]},
                            "residual_gap": {"type": ["string", "null"]},
                            "open_questions": {"type": ["array", "null"], "items": {"type": "string"}},
                        },
                        "required": ["mutation_type", "requirement_id", "semantic_key", "title", "source_scenario", "migration_boundary", "target_solution", "acceptance_criteria", "risk", "specification_text", "role", "rationale", "acceptance_refs", "subject_tags", "bp30_coverage", "residual_gap", "open_questions"],
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def decision_schema(adapter: str, entity_count: int | None = None) -> dict[str, Any]:
    schema = copy.deepcopy(DECISION_SCHEMA)
    allowed = {
        "review_evidence": ["record_evidence", "follow_up"],
        "uncovered_cus": ["link_existing_requirement", "new_requirement_proposal", "exclude_false_customization", "follow_up"],
        "conflicting_cus": ["set_relation", "split_customization_proposal", "exclude_false_customization", "follow_up"],
    }[adapter]
    schema["properties"]["entities"]["items"]["properties"]["proposal"]["properties"]["mutation_type"]["enum"] = allowed
    if entity_count is not None:
        schema["properties"]["entities"].update({"minItems": entity_count, "maxItems": entity_count})
    return schema


def validate_decision(root: Path, run_id: str, unit_id: str, decision: dict[str, Any], context: dict[str, Any] | None = None) -> list[str]:
    _, unit = load_unit(root, run_id, unit_id)
    context = context or task_context(root, run_id, unit_id)
    errors = []
    if not isinstance(decision, dict):
        return ["decision is not an object"]
    required_envelope = set(DECISION_SCHEMA["required"])
    if set(decision) != set(DECISION_SCHEMA["properties"]) or not required_envelope <= set(decision):
        errors.append("decision envelope does not match schema")
    if decision.get("schema_version") != SCHEMA_VERSION or decision.get("run_id") != run_id or decision.get("unit_id") != unit_id or decision.get("adapter") != unit["adapter"] or decision.get("context_hash") != context["context_hash"]:
        errors.append("decision envelope does not match unit/context")
    rows_value = decision.get("entities")
    rows = rows_value if isinstance(rows_value, list) else []
    if not isinstance(rows_value, list):
        errors.append("decision entities is not an array")
    actual = [str(row.get("entity_id") or "") for row in rows if isinstance(row, dict)]
    context_entities = {str(row.get("entity_id")): row for row in context.get("entities") or []}
    if sorted(actual) != sorted(unit["entity_ids"]) or len(actual) != len(set(actual)):
        errors.append("decision entity ids do not match assigned entities")
    for row in rows:
        if not isinstance(row, dict):
            errors.append("decision entity is not an object")
            continue
        row_schema = DECISION_SCHEMA["properties"]["entities"]["items"]
        if set(row) != set(row_schema["properties"]) or not set(row_schema["required"]) <= set(row):
            errors.append(f"decision row does not match schema for {row.get('entity_id')}")
        if row.get("outcome") not in OUTCOMES or row.get("confidence") not in CONFIDENCES or not str(row.get("conclusion") or "").strip():
            errors.append(f"invalid conclusion fields for {row.get('entity_id')}")
        evidence_rows = row.get("evidence")
        if not isinstance(evidence_rows, list):
            errors.append(f"evidence is not an array for {row.get('entity_id')}")
            evidence_rows = []
        for evidence in evidence_rows:
            evidence_schema = row_schema["properties"]["evidence"]["items"]
            if not isinstance(evidence, dict) or set(evidence) != set(evidence_schema["properties"]):
                errors.append(f"evidence does not match schema for {row.get('entity_id')}")
                continue
            relative = str(evidence.get("path") or "")
            if not relative or not str(evidence.get("summary") or "").strip():
                errors.append(f"invalid evidence for {row.get('entity_id')}")
                continue
            try:
                _safe_path(root, relative)
            except ParallelResearchError as exc:
                errors.append(str(exc))
            allowed = relative in unit["allowed_sources"] or any(
                repo_path(root, value).is_dir() and relative.startswith(value.rstrip("/") + "/")
                for value in unit["allowed_sources"]
            )
            if not allowed:
                errors.append(f"evidence path is outside unit sources: {relative}")
                continue
            evidence_path = repo_path(root, relative)
            line_start = evidence.get("line_start")
            line_end = evidence.get("line_end")
            if not evidence_path.is_file():
                errors.append(f"evidence file does not exist: {relative}")
            elif not isinstance(line_start, int) or not isinstance(line_end, int) or line_start < 1 or line_end < line_start:
                errors.append(f"invalid evidence line range for {row.get('entity_id')}: {relative}")
            else:
                line_count = sum(1 for _ in evidence_path.open(encoding="utf-8", errors="replace"))
                if line_end > max(1, line_count):
                    errors.append(f"evidence line range exceeds file for {row.get('entity_id')}: {relative}")
        proposal_value = row.get("proposal")
        proposal = proposal_value if isinstance(proposal_value, dict) else {}
        proposal_schema = row_schema["properties"]["proposal"]
        if not isinstance(proposal_value, dict) or set(proposal) != set(proposal_schema["properties"]):
            errors.append(f"proposal does not match schema for {row.get('entity_id')}")
        if any(key.endswith("_id") and str(value).startswith(("MRQ-", "CUS-", "Q-")) for key, value in proposal.items() if key not in {"requirement_id"}):
            errors.append(f"model allocated canonical id for {row.get('entity_id')}")
        allowed = {"review_evidence": {"record_evidence", "follow_up"}, "uncovered_cus": {"link_existing_requirement", "new_requirement_proposal", "exclude_false_customization", "follow_up"}, "conflicting_cus": {"set_relation", "split_customization_proposal", "exclude_false_customization", "follow_up"}}[unit["adapter"]]
        if proposal.get("mutation_type") not in allowed:
            errors.append(f"unsupported mutation type for {row.get('entity_id')}: {proposal.get('mutation_type')}")
        if proposal.get("mutation_type") in {"link_existing_requirement", "set_relation"} and not proposal.get("requirement_id"):
            errors.append(f"requirement_id is required for relation mutation for {row.get('entity_id')}")
        if proposal.get("mutation_type") in {"link_existing_requirement", "set_relation", "new_requirement_proposal"} and proposal.get("role") not in {"primary", "required", "supporting", "shared"}:
            errors.append(f"invalid requirement relation role for {row.get('entity_id')}: {proposal.get('role')}")
        entity_context = context_entities.get(str(row.get("entity_id")), {})
        if proposal.get("mutation_type") == "exclude_false_customization":
            comparisons = entity_context.get("source_comparisons") or []
            identical = bool(comparisons) and all(item.get("comparison_status") in {"identical", "technical_noise"} for item in comparisons)
            eligible = bool(entity_context.get("technical_object")) or (
                entity_context.get("comparison_assessment") == "no_semantic_diff" and identical
            )
            if row.get("outcome") != "confirmed" or row.get("confidence") != "high":
                errors.append(f"false customization exclusion requires confirmed/high outcome for {row.get('entity_id')}")
            if not eligible:
                errors.append(f"false customization exclusion lacks identical or technical evidence for {row.get('entity_id')}")
            cited = {str(item.get("path") or "") for item in evidence_rows}
            cited_pairs = [
                {comparison.get("customer_path"), comparison.get("vendor_path")} - {"", None}
                for comparison in comparisons
            ]
            if identical and not any(pair and pair <= cited for pair in cited_pairs):
                errors.append(f"no identical source pair is fully cited for {row.get('entity_id')}")
        if unit["adapter"] == "uncovered_cus" and proposal.get("mutation_type") not in {"follow_up", "exclude_false_customization"}:
            if row.get("outcome") != "confirmed":
                errors.append(f"migration proposal requires confirmed outcome for {row.get('entity_id')}")
            if entity_context.get("technical_object"):
                errors.append(f"technical object cannot create migration proposal for {row.get('entity_id')}")
            if entity_context.get("vendor_drift_candidate"):
                errors.append(f"vendor release drift candidate cannot create migration proposal for {row.get('entity_id')}")
            external_processing = "external_processing" in (
                entity_context.get("customization", {}).get("source_kinds") or []
            )
            if entity_context.get("comparison_assessment") != "semantic_candidate" and not external_processing:
                errors.append(f"migration proposal lacks comparative semantic evidence for {row.get('entity_id')}")
            cited = {str(item.get("path") or "") for item in evidence_rows}
            changed_pairs = [
                {comparison.get("customer_path"), comparison.get("vendor_path")} - {"", None}
                for comparison in entity_context.get("source_comparisons") or []
                if comparison.get("comparison_status") == "changed"
            ]
            if changed_pairs and not any(pair <= cited for pair in changed_pairs):
                errors.append(f"changed source pair is not fully cited for {row.get('entity_id')}")
        if proposal.get("mutation_type") in {"new_requirement_proposal", "split_customization_proposal"} and not proposal.get("semantic_key"):
            errors.append(f"semantic_key is required for {row.get('entity_id')}")
        if proposal.get("mutation_type") == "new_requirement_proposal":
            required = {"semantic_key", "title", "source_scenario", "migration_boundary", "acceptance_criteria", "risk", "specification_text"}
            missing = sorted(field for field in required if not proposal.get(field))
            if missing:
                errors.append(f"new requirement proposal lacks fields for {row.get('entity_id')}: {missing}")
    return errors


def summarize_trace(trace_path: Path, decision_path: Path, unit: dict[str, Any]) -> dict[str, Any]:
    events = []
    invalid = 0
    for raw in trace_path.read_text(encoding="utf-8").split("\n") if trace_path.exists() else []:
        if not raw:
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            invalid += 1
    commands = []
    for event in events:
        item = event.get("item") if isinstance(event, dict) else None
        command = item.get("command") if isinstance(item, dict) and item.get("type") == "command_execution" else event.get("command") if isinstance(event, dict) else None
        if isinstance(command, str):
            commands.append(command)
    commands = list(dict.fromkeys(commands))
    allowed = set(unit["allowed_sources"]) | set(unit.get("expected_outputs") or []) | {
        ".agents/skills/1c-autoresearch-parallel-research-goal/SKILL.md",
        "docs/method/parallel-research-goal.md",
        f"{RUNS}/",
    }
    mentioned_paths = [match for text in commands for match in re.findall(r"(?:analysis|src|docs|outputs)/[A-Za-zА-Яа-яЁё0-9_./-]+", text)]
    out_of_scope = [path for path in mentioned_paths if path not in allowed and not any(path.startswith(str(Path(value).parent) + "/") for value in allowed)]
    forbidden = [text for text in commands if FORBIDDEN_COMMANDS.search(text) or any(path in text for path in FORBIDDEN_GLOBAL_READS)]
    context_seen = any("research_task_context.py" in text and unit["unit_id"] in text for text in commands)
    input_tokens = sum(int(event.get("input_tokens") or event.get("usage", {}).get("input_tokens") or 0) for event in events if isinstance(event, dict))
    output_tokens = sum(int(event.get("output_tokens") or event.get("usage", {}).get("output_tokens") or 0) for event in events if isinstance(event, dict))
    scope_errors = ["out_of_scope_read"] if out_of_scope and unit.get("runner_version", "1") == "1" else []
    return {"events": len(events), "invalid_events": invalid, "commands": commands, "tool_calls": len(commands), "input_tokens": input_tokens, "output_tokens": output_tokens, "forbidden_commands": forbidden, "out_of_scope_paths": sorted(set(out_of_scope)), "context_seen": context_seen, "decision_sha256": _file_sha(decision_path), "errors": (["invalid_trace_json"] if invalid else []) + (["context_wrapper_missing"] if not context_seen else []) + (["forbidden_command"] if forbidden else []) + scope_errors}


def _codex_failure_reason(trace_path: Path, returncode: int) -> str:
    text = trace_path.read_text(encoding="utf-8", errors="replace").lower() if trace_path.exists() else ""
    if "usage limit" in text:
        return "usage_limit"
    if "model is at capacity" in text:
        return "model_capacity"
    return f"codex_exit_{returncode}"


def _state_path(root: Path, run_id: str, unit_id: str) -> Path:
    return repo_path(root, f"{RUNS}/{run_id}/states/{unit_id}.json")


def _set_state(root: Path, run_id: str, unit_id: str, state: str, **payload: Any) -> None:
    if state not in UNIT_STATES:
        raise ParallelResearchError(f"invalid unit state: {state}")
    _write_json(_state_path(root, run_id, unit_id), {"unit_id": unit_id, "state": state, "updated_at": utc_now_iso(), **payload})


def _fake_decision(context: dict[str, Any]) -> dict[str, Any]:
    entities = []
    for entity in context["entities"]:
        evidence_path = context["allowed_sources"][0] if context["allowed_sources"] else "project.toml"
        adapter = context["adapter"]
        mutation = "record_evidence" if adapter == "review_evidence" else "follow_up"
        proposal = {key: None for key in DECISION_SCHEMA["properties"]["entities"]["items"]["properties"]["proposal"]["properties"]}
        proposal.update({"mutation_type": mutation, "acceptance_refs": [], "subject_tags": [], "open_questions": []})
        entities.append({"entity_id": entity["entity_id"], "outcome": "needs_followup" if adapter != "review_evidence" else "confirmed", "confidence": "medium", "conclusion": "Проверочный структурированный результат.", "evidence": [{"path": evidence_path, "line_start": 1, "line_end": 1, "summary": "Проверочный источник."}], "proposal": proposal})
    return {"schema_version": SCHEMA_VERSION, "run_id": context["run_id"], "unit_id": context["unit_id"], "adapter": context["adapter"], "context_hash": context["context_hash"], "entities": entities}


def _worker_prompt(context: dict[str, Any]) -> str:
    return f"""Это read-only рабочая единица параллельного исследования.
Сначала обязательно вызови: python3 scripts/research_task_context.py --run-id {context['run_id']} --unit-id {context['unit_id']}
Не читай analysis/queue/tasks.jsonl и глобальные JSONL реестров напрямую. Не редактируй файлы, не меняй очередь и не создавай коммиты.
Работай только по entity_ids и allowed_sources из контекста. Если доказательств доработанного источника недостаточно, верни needs_followup или needs_infobase_data.
Это только проход по исходной доработанной системе. Не проверяй целевой релиз и не требуй доказательств из него. Отсутствие проверки целевого релиза не является причиной для follow_up. Поля покрытия, residual_gap и окончательный target_solution оставляй неподтвержденными до отдельной цели /goal Карта разрывов.
Для CUS сначала используй source_comparisons. Если все пары identical/technical_noise или technical_object=true, верни outcome=confirmed, confidence=high и exclude_false_customization с точным обоснованием: это ложная запись реестра, а не миграционное требование. Для identical/technical_noise приведи в evidence хотя бы одну полную пару customer_path + vendor_path; перечислять все одинаковые пары не требуется. customer_only подтверждает только техническое отличие, но само по себе не доказывает пользовательскую бизнес-доработку. Для внешней обработки или отчета из source_kinds=external_processing типовая пара не ожидается: подтвержденная исходным кодом самостоятельная бизнес-функция является достаточным доказательством исходной системы для связи или нового MRQ. Если vendor_drift_candidate=true, это вероятное расхождение версий типовой поставки: верни follow_up и не создавай миграционное требование без отдельного доказательства пользовательского поведения. Для подтвержденного смыслового изменения выбери подходящий существующий MRQ из контекста через link_existing_requirement/set_relation либо верни new_requirement_proposal. new_requirement_proposal разрешен только при outcome=confirmed и доказанном бизнес-смысле. Не определяй target_solution на этом проходе: передай null, единственный процесс записи сохранит needs_customer_decision. follow_up допустим только при точном недостатке доказательств исходной системы.
Поле proposal.role означает только роль связи с MRQ: primary, required, supporting или shared; не записывай туда должность или бизнес-роль пользователя.
В evidence указывай только конкретный файл из allowed_sources и реальные строки этого файла; каталог не является допустимым evidence path.
Читай только точные пути allowed_sources: не запускай find, rg, ls или обход родительских каталогов для поиска дополнительных файлов.
Не назначай новые MRQ/CUS/task идентификаторы: для новой сущности предложи semantic_key.
Верни только JSON по схеме. Все факты рабочей единицы получи из указанного враппера."""


def _retry_prompt(prompt: str, errors: list[str]) -> str:
    if not errors:
        return prompt
    return prompt + "\n\nПредыдущее решение отклонено валидатором:\n- " + "\n- ".join(errors) + """
Исправь именно эти ошибки. exclude_false_customization разрешен только для identical/technical_noise-пары с обоими файлами в evidence или при technical_object=true; не признавай UUID или changed техническим шумом самостоятельно. Для link_existing_requirement, set_relation и new_requirement_proposal поле role обязательно. Для new_requirement_proposal обязательны непустые semantic_key, title, source_scenario, migration_boundary, acceptance_criteria, risk и specification_text."""


def _prepare_worker_workspace(root: Path, run_id: str, unit_id: str, context: dict[str, Any], schema: dict[str, Any]) -> Path:
    workspace = repo_path(root, f"{WORKSPACE_ROOT}/{run_id}/{unit_id}")
    shutil.rmtree(workspace, ignore_errors=True)
    for relative in context["allowed_sources"]:
        source = repo_path(root, relative)
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, symlinks=True)
        elif source.is_file():
            shutil.copy2(source, destination)
    _write_json(workspace / "context.json", context)
    _write_json(workspace / "schema.json", schema)
    (workspace / "AGENTS.md").write_text(
        "# Изолированная рабочая единица\n\n"
        "Выполни только назначение из context.json. Читай только перечисленные там allowed_sources. "
        "Не ищи другие файлы и не изменяй данные.\n",
        encoding="utf-8",
    )
    wrapper = workspace / "scripts/research_task_context.py"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p=argparse.ArgumentParser(); p.add_argument('--run-id', required=True); p.add_argument('--unit-id', required=True); a=p.parse_args()\n"
        f"assert a.run_id == {run_id!r} and a.unit_id == {unit_id!r}, 'assignment mismatch'\n"
        "print(Path('context.json').read_text(encoding='utf-8'))\n",
        encoding="utf-8",
    )
    return workspace


def _codex_command(workspace: Path, model: str, output_path: Path, prompt: str) -> list[str]:
    home = Path.home()
    command = [
        "bwrap", "--die-with-parent",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--ro-bind", "/etc", "/etc", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--dir", "/home", "--dir", str(home), "--tmpfs", str(home / ".codex"),
        "--ro-bind", str(home / ".codex/auth.json"), str(home / ".codex/auth.json"),
        "--ro-bind", str(home / ".codex/models_cache.json"), str(home / ".codex/models_cache.json"),
        "--ro-bind", str(workspace), "/workspace",
        "--bind", str(output_path.parent), "/output",
        "--chdir", "/workspace", "--setenv", "HOME", str(home), "--setenv", "CODEX_HOME", str(home / ".codex"),
        "codex", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
        *(["-m", model] if model else []), "-C", "/workspace", "--dangerously-bypass-approvals-and-sandbox",
    ]
    for feature in ("apps", "browser_use", "computer_use", "image_generation", "standalone_web_search"):
        command.extend(["--disable", feature])
    command.extend(["--json", "--output-schema", "/workspace/schema.json", "-o", f"/output/{output_path.name}", prompt])
    return command


def terminate_process_group(process: subprocess.Popen[str], grace_seconds: float = 5) -> tuple[str, str]:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return "", ""
    try:
        stdout, stderr = process.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
    return stdout or "", stderr or ""


def _track_worker(process: subprocess.Popen[str]) -> None:
    with _ACTIVE_WORKERS_LOCK:
        _ACTIVE_WORKERS[process.pid] = process


def _untrack_worker(process: subprocess.Popen[str]) -> None:
    with _ACTIVE_WORKERS_LOCK:
        _ACTIVE_WORKERS.pop(process.pid, None)


def terminate_active_workers() -> list[int]:
    with _ACTIVE_WORKERS_LOCK:
        workers = list(_ACTIVE_WORKERS.values())
    terminated = []
    for process in workers:
        if process.poll() is None:
            terminate_process_group(process)
            terminated.append(process.pid)
        _untrack_worker(process)
    return terminated


@contextlib.contextmanager
def coordinator_signal_guard():
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

    def stop(signum: int, _: Any) -> None:
        terminate_active_workers()
        raise ParallelResearchError(f"coordinator interrupted by signal {signum}")

    try:
        for signum in previous:
            signal.signal(signum, stop)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _run_unit(root: Path, run_id: str, unit_id: str, model: str, timeout: int, fake: bool = False, shared: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_mutable_run(root, run_id)
    saved = _read_json(_state_path(root, run_id, unit_id))
    saved_state = saved.get("state")
    if shared:
        manifest = shared["manifest"]
        unit = shared["units"].get(unit_id)
        if unit is None:
            raise ParallelResearchError(f"unit not found: {unit_id}")
    else:
        manifest, unit = load_unit(root, run_id, unit_id)
    context = task_context(root, run_id, unit_id, shared=shared)
    base_prompt = _worker_prompt(context)
    base_prompt_hash = _sha(base_prompt)
    retry_errors = list(saved.get("errors") or []) if saved_state == "rejected" else []
    if saved_state == "timeout":
        retry_errors = ["Предыдущий проход превысил лимит времени. Не читай большие identical-пары целиком: проверь их cmp и прямо используй source_comparisons."]
    prompt = _retry_prompt(base_prompt, retry_errors)
    prompt_hash = _sha(prompt)
    run_dir = repo_path(root, f"{RUNS}/{run_id}")
    trace_dir = repo_path(root, f"{TRACE_ROOT}/{run_id}")
    trace_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    decision_path = run_dir / "decisions" / f"{unit_id}.json"
    schema_path = run_dir / "schemas" / f"{unit_id}.json"
    trace_path = trace_dir / f"{unit_id}.jsonl"
    stderr_path = trace_dir / f"{unit_id}.stderr.txt"
    summary_path = trace_dir / f"{unit_id}.summary.json"
    if decision_path.exists():
        decision = _read_json(decision_path)
        summary = _read_json(summary_path)
        compatible = isinstance(decision, dict) and isinstance(summary, dict) and decision.get("schema_version") == SCHEMA_VERSION and unit.get("runner_version") == RUNNER_VERSION and decision.get("context_hash") == context["context_hash"] and summary.get("decision_sha256") == _file_sha(decision_path) and summary.get("runner_version") == RUNNER_VERSION and summary.get("model") == model and summary.get("base_prompt_hash", summary.get("prompt_hash")) == base_prompt_hash and summary.get("source_fingerprints") == unit["source_fingerprints"]
        if compatible and not validate_decision(root, run_id, unit_id, decision, context) and not summary.get("errors"):
            state = "follow_up" if any(row["outcome"] != "confirmed" for row in decision["entities"]) else "completed"
            _set_state(root, run_id, unit_id, state, decision_sha256=_file_sha(decision_path), reused=True, model=model, timeout=timeout, prompt_hash=prompt_hash, context_hash=context["context_hash"], source_fingerprints=unit["source_fingerprints"])
            return {"status": "reused", "unit_id": unit_id, "decision": str(decision_path)}
        if saved_state != "rejected":
            raise ParallelResearchError(f"saved decision is stale or incompatible: {unit_id}")
        decision_path.unlink()
    for path in (trace_path, stderr_path, summary_path):
        path.unlink(missing_ok=True)
    _set_state(root, run_id, unit_id, "running", model=model, timeout=timeout)
    schema = decision_schema(unit["adapter"], len(unit["entity_ids"]))
    for field, value in {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "unit_id": unit_id,
        "adapter": unit["adapter"],
        "context_hash": context["context_hash"],
    }.items():
        schema["properties"][field]["const"] = value
    _write_json(schema_path, schema)
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if fake:
        decision = _fake_decision(context)
        _write_json(decision_path, decision)
        trace_path.write_text(json.dumps({"type": "command", "command": f"python3 scripts/research_task_context.py --run-id {run_id} --unit-id {unit_id}"}) + "\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
    else:
        workspace = _prepare_worker_workspace(root, run_id, unit_id, context, schema)
        output_dir = trace_dir / "worker-output" / unit_id
        shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, mode=0o700)
        worker_decision_path = output_dir / "decision.json"
        cmd = _codex_command(workspace, model, worker_decision_path, prompt)
        fd = os.open(trace_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as trace:
                process = subprocess.Popen(cmd, stdout=trace, stderr=subprocess.PIPE, text=True, start_new_session=True)
                _track_worker(process)
                try:
                    _, stderr = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _, stderr = terminate_process_group(process)
                    stderr_path.write_text(stderr or "", encoding="utf-8")
                    shutil.rmtree(output_dir, ignore_errors=True)
                    _set_state(root, run_id, unit_id, "timeout", model=model, timeout=timeout, prompt_hash=prompt_hash, context_hash=context["context_hash"], elapsed_seconds=round(time.monotonic() - started, 3))
                    return {"status": "timeout", "unit_id": unit_id}
                finally:
                    _untrack_worker(process)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
        stderr_path.write_text(stderr or "", encoding="utf-8")
        if process.returncode:
            reason = _codex_failure_reason(trace_path, process.returncode)
            shutil.rmtree(output_dir, ignore_errors=True)
            _set_state(root, run_id, unit_id, "rejected", reason=reason, model=model, timeout=timeout, prompt_hash=prompt_hash, context_hash=context["context_hash"])
            return {"status": "rejected", "unit_id": unit_id, "reason": reason}
        if not worker_decision_path.is_file():
            shutil.rmtree(output_dir, ignore_errors=True)
            _set_state(root, run_id, unit_id, "rejected", reason="decision_missing", model=model, timeout=timeout, prompt_hash=prompt_hash, context_hash=context["context_hash"])
            return {"status": "rejected", "unit_id": unit_id, "reason": "decision_missing"}
        worker_decision_path.replace(decision_path)
        shutil.rmtree(output_dir, ignore_errors=True)
    for path in (trace_path, stderr_path):
        path.chmod(0o600)
    decision = _read_json(decision_path)
    errors = validate_decision(root, run_id, unit_id, decision, context)
    summary = summarize_trace(trace_path, decision_path, unit)
    summary.update({"schema_version": SCHEMA_VERSION, "runner_version": RUNNER_VERSION, "model": model, "timeout": timeout, "context_hash": context["context_hash"], "prompt_hash": prompt_hash, "base_prompt_hash": base_prompt_hash, "retry_errors": retry_errors, "elapsed_seconds": round(time.monotonic() - started, 3), "source_fingerprints": unit["source_fingerprints"]})
    summary["errors"].extend(errors)
    _write_json(summary_path, summary, mode=0o600)
    if summary["errors"]:
        _set_state(root, run_id, unit_id, "rejected", errors=summary["errors"], model=model, timeout=timeout, prompt_hash=prompt_hash, context_hash=context["context_hash"])
        return {"status": "rejected", "unit_id": unit_id, "errors": summary["errors"]}
    state = "follow_up" if any(row["outcome"] != "confirmed" for row in decision["entities"]) else "completed"
    _set_state(root, run_id, unit_id, state, decision_sha256=_file_sha(decision_path), elapsed_seconds=summary["elapsed_seconds"], model=model, timeout=timeout, prompt_hash=prompt_hash, context_hash=context["context_hash"], source_fingerprints=unit["source_fingerprints"])
    return {"status": state, "unit_id": unit_id, "decision": str(decision_path)}


def _saved_decision_reusable(root: Path, run_id: str, unit: dict[str, Any], model: str, tasks: dict[str, dict[str, Any]], hashes: dict[str, str]) -> bool:
    unit_id = unit["unit_id"]
    run_dir = repo_path(root, f"{RUNS}/{run_id}")
    decision_path = run_dir / "decisions" / f"{unit_id}.json"
    summary = _read_json(repo_path(root, f"{TRACE_ROOT}/{run_id}/{unit_id}.summary.json"))
    decision = _read_json(decision_path)
    task = tasks.get(str(unit["task_id"]), {})
    prompt_hash = _sha(_worker_prompt({"run_id": run_id, "unit_id": unit_id}))
    if not decision_path.is_file() or task.get("status") != unit["expected_task_status"] or _task_hash(task) != unit["task_hash"]:
        return False
    for relative, expected in unit["source_fingerprints"].items():
        if relative == QUEUE:
            continue
        if relative not in hashes:
            hashes[relative] = _path_sha(repo_path(root, relative))
        if hashes[relative] != expected:
            return False
    return (
        decision.get("schema_version") == SCHEMA_VERSION
        and decision.get("run_id") == run_id
        and decision.get("unit_id") == unit_id
        and decision.get("adapter") == unit["adapter"]
        and decision.get("context_hash") == summary.get("context_hash")
        and unit.get("runner_version") == RUNNER_VERSION
        and summary.get("runner_version") == RUNNER_VERSION
        and summary.get("model") == model
        and summary.get("base_prompt_hash", summary.get("prompt_hash")) == prompt_hash
        and summary.get("source_fingerprints") == unit["source_fingerprints"]
        and summary.get("decision_sha256") == _file_sha(decision_path)
        and not summary.get("errors")
    )


def run_unit(root: Path, run_id: str, unit_id: str, model: str, timeout: int, fake: bool = False) -> dict[str, Any]:
    root = root.resolve()
    with ProcessLock(repo_path(root, COORDINATOR_LOCK), run_id):
        return _run_unit(root, run_id, unit_id, model, timeout, fake)


def _snapshot(paths: list[Path]) -> tuple[Path, dict[str, str]]:
    temp = Path(tempfile.mkdtemp(prefix="parallel-research-snapshot-"))
    state = {}
    for index, path in enumerate(paths):
        key = str(index)
        if path.is_file():
            shutil.copy2(path, temp / key)
            state[str(path)] = f"file:{key}"
        elif path.is_dir():
            shutil.copytree(path, temp / key)
            state[str(path)] = f"dir:{key}"
        else:
            state[str(path)] = "missing"
    return temp, state


def _restore(snapshot: Path, state: dict[str, str]) -> None:
    for raw, marker in state.items():
        path = Path(raw)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        if marker == "missing":
            continue
        kind, key = marker.split(":", 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "file":
            shutil.copy2(snapshot / key, path)
        else:
            shutil.copytree(snapshot / key, path)


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    current = _rows(path)
    write_jsonl(path, current + rows)


def _current_task(root: Path, task_id: str) -> dict[str, Any]:
    return next((row for row in _rows(repo_path(root, QUEUE)) if str(row.get("id")) == task_id), {})


def _declared_paths(root: Path, manifest: dict[str, Any]) -> list[Path]:
    paths = [repo_path(root, QUEUE), repo_path(root, MRQ_ROOT), repo_path(root, CUS_PROPOSALS), repo_path(root, CUS_ROOT)]
    for unit in manifest["units"]:
        if unit["adapter"] != "review_evidence":
            continue
        task = _current_task(root, str(unit["task_id"]))
        feature = str(task.get("feature_id") or task.get("id") or "parallel-research")
        paths.append(repo_path(root, f"analysis/features/{feature}/artifacts/parallel-research-{unit['unit_id']}.json"))
    return list(dict.fromkeys(paths))


def _verify_fingerprints(root: Path, unit: dict[str, Any], applied_in_run: set[str]) -> bool:
    dependencies_applied = set(unit.get("apply_after") or []) <= applied_in_run
    return all(
        _path_sha(repo_path(root, relative)) == expected
        or relative == QUEUE
        or (relative in {MRQ_LINKS, MRQ_REQUIREMENTS, CUS_ITEMS} and dependencies_applied)
        for relative, expected in unit["source_fingerprints"].items()
    )


def _apply_review(root: Path, unit: dict[str, Any], decision: dict[str, Any]) -> list[str]:
    task = _current_task(root, str(unit["task_id"]))
    feature = str(task.get("feature_id") or task.get("id") or "parallel-research")
    relative = f"analysis/features/{feature}/artifacts/parallel-research-{unit['unit_id']}.json"
    _write_json(repo_path(root, relative), decision)
    return [relative]


def _apply_cus(root: Path, unit: dict[str, Any], decision: dict[str, Any], requirements: list[dict[str, Any]], links: list[dict[str, Any]], registry_items: list[dict[str, Any]] | None = None, proposal_sink: list[dict[str, Any]] | None = None) -> tuple[list[str], bool, list[dict[str, Any]], list[dict[str, Any]]]:
    proposals = []
    changed = False
    for row in decision["entities"]:
        proposal = row["proposal"]
        kind = proposal.get("mutation_type")
        cid = row["entity_id"]
        if kind in {"link_existing_requirement", "set_relation"}:
            rid = str(proposal.get("requirement_id") or "")
            if rid not in {item["requirement_id"] for item in requirements}:
                raise ParallelResearchError(f"unknown requirement_id: {rid}")
            role = str(proposal.get("role") or "supporting")
            if role not in {"primary", "required", "supporting", "shared"}:
                raise ParallelResearchError(f"invalid role: {role}")
            if role == "primary":
                links = [link for link in links if not (link.get("customization_id") == cid and link.get("role") == "primary")]
            links = [link for link in links if (link.get("requirement_id"), link.get("customization_id")) != (rid, cid)]
            links.append({"schema_version": "migration-requirements/v1", "requirement_id": rid, "customization_id": cid, "role": role, "rationale": proposal.get("rationale") or row["conclusion"], "required": role != "supporting", "acceptance_refs": proposal.get("acceptance_refs") or [], "effort_owner": role == "primary"})
            changed = True
        elif kind == "new_requirement_proposal":
            semantic_key = str(proposal["semantic_key"])
            rid = allocate_canonical_id("requirement", semantic_key)
            same_key = [item for item in requirements if item.get("stable_key") == semantic_key]
            if same_key and same_key[0].get("requirement_id") != rid:
                raise ParallelResearchError(f"stable-key collision: {semantic_key}")
            if not same_key:
                requirements.append({"schema_version": "migration-requirements/v1", "requirement_id": rid, "stable_key": semantic_key, "title": proposal["title"], "status": "draft", "agreement_state": "not_requested", "agreement_reason": "", "subject_tags": proposal.get("subject_tags") or ["other"], "source_scenario": proposal["source_scenario"], "migration_boundary": proposal["migration_boundary"], "bp30_coverage": proposal.get("bp30_coverage") or "Требует проверки", "residual_gap": proposal.get("residual_gap") or "Требует проверки", "target_solution": proposal.get("target_solution") or "needs_customer_decision", "acceptance_criteria": proposal["acceptance_criteria"], "risk": proposal["risk"], "open_questions": proposal.get("open_questions") or [], "specification_text": proposal["specification_text"], "source_provenance": {"parallel_research_unit_id": unit["unit_id"]}})
            links.append({"schema_version": "migration-requirements/v1", "requirement_id": rid, "customization_id": cid, "role": proposal.get("role") or "primary", "rationale": proposal.get("rationale") or row["conclusion"], "required": True, "acceptance_refs": [], "effort_owner": (proposal.get("role") or "primary") == "primary"})
            changed = True
        elif kind == "exclude_false_customization":
            items = registry_items if registry_items is not None else _rows(repo_path(root, CUS_ITEMS))
            item = next((item for item in items if item.get("customization_id") == cid), None)
            if item is None:
                raise ParallelResearchError(f"unknown customization_id: {cid}")
            item.update({
                "scope_status": "excluded_false_positive",
                "exclusion_reason": proposal.get("rationale") or row["conclusion"],
                "status": "technical_noise_removed",
                "migration_decision": "drop",
            })
            if registry_items is None:
                write_jsonl(repo_path(root, CUS_ITEMS), items)
                registry = load_registry_index(root)
                write_cus_summary(root, registry["items"], registry["evidence"], registry["links"])
                export_registry(root)
                registry_check = validate_registry(root)
                if registry_check.get("status") != "ok":
                    raise ParallelResearchError(f"customization registry candidate failed: {registry_check.get('errors')}")
            links = [link for link in links if link.get("customization_id") != cid]
            changed = True
        elif kind in {"split_customization_proposal", "follow_up"}:
            proposals.append({"unit_id": unit["unit_id"], "task_id": unit["task_id"], "customization_id": cid, "adapter": unit["adapter"], "proposal": proposal, "conclusion": row["conclusion"], "created_at": utc_now_iso()})
    if changed and proposal_sink is None:
        result = validate_mrq_graph(root, requirements, links)
        if result.get("status") != "ok":
            raise ParallelResearchError(f"migration-requirement candidate failed: {result.get('errors')}")
    if proposals:
        if proposal_sink is not None:
            proposal_sink.extend(proposals)
        else:
            proposal_path = repo_path(root, CUS_PROPOSALS)
            existing_keys = {(str(item.get("unit_id")), str(item.get("customization_id"))) for item in _rows(proposal_path)}
            _append_jsonl(proposal_path, [item for item in proposals if (str(item["unit_id"]), str(item["customization_id"])) not in existing_keys])
    registry_changed = any(row["proposal"].get("mutation_type") == "exclude_false_customization" for row in decision["entities"])
    return ([MRQ_ROOT] if changed else []) + ([CUS_ROOT] if registry_changed else []) + ([CUS_PROPOSALS] if proposals else []), changed, requirements, links


def _validate_affected(root: Path, affected: list[str]) -> None:
    missing = [relative for relative in affected if not repo_path(root, relative).exists()]
    if missing:
        raise ParallelResearchError(f"affected artifacts are missing: {missing}")
    for relative in affected:
        path = repo_path(root, relative)
        if path.suffix == ".json":
            _read_json(path)
        elif path.suffix == ".jsonl":
            _rows(path)


def _update_tasks(root: Path, manifest: dict[str, Any], applied_units: set[str]) -> None:
    units_by_task: dict[str, list[dict[str, Any]]] = {}
    for unit in manifest["units"]:
        units_by_task.setdefault(str(unit["task_id"]), []).append(unit)
    tasks = _rows(repo_path(root, QUEUE))
    for task in tasks:
        task_units = units_by_task.get(str(task.get("id")), [])
        if not task_units or not all(unit["unit_id"] in applied_units or _read_json(_state_path(root, manifest["run_id"], unit["unit_id"])).get("state") == "follow_up" for unit in task_units):
            continue
        states = [_read_json(_state_path(root, manifest["run_id"], unit["unit_id"])) for unit in task_units]
        # Unresolved CUS work must be selected by the next full plan, not parked as completed.
        task["status"] = "pending" if any(state.get("state") == "follow_up" or state.get("result_state") == "follow_up" for state in states) else "needs_review"
        task["updated_at"] = utc_now_iso()
        task["parallel_research_run_id"] = manifest["run_id"]
    write_jsonl(repo_path(root, QUEUE), tasks)


def apply_ready(root: Path, run_id: str, heavy_checks: list[list[str]] | None = None) -> dict[str, Any]:
    root = root.resolve()
    _ensure_mutable_run(root, run_id)
    manifest = _load_manifest(root, run_id)
    if manifest.get("truncated"):
        raise ParallelResearchError("truncated diagnostic run cannot be applied; create a full plan without --limit")
    ledger_path = repo_path(root, LEDGER)
    latest_events: dict[str, str] = {}
    for row in _rows(ledger_path):
        if row.get("run_id") == run_id:
            latest_events[str(row.get("unit_id"))] = str(row.get("event"))
    applied_before = {unit_id for unit_id, event in latest_events.items() if event == "applied"}
    declared = _declared_paths(root, manifest)
    applied_now = []
    rejected = []
    requirements = _rows(repo_path(root, MRQ_REQUIREMENTS))
    links = _rows(repo_path(root, MRQ_LINKS))
    registry_items = _rows(repo_path(root, CUS_ITEMS))
    proposal_sink: list[dict[str, Any]] = []
    tasks = {str(row.get("id")): row for row in _rows(repo_path(root, QUEUE))}
    shared = {
        "manifest": manifest,
        "manifest_hash": _sha(manifest),
        "units": {unit["unit_id"]: unit for unit in manifest["units"]},
        "tasks": tasks,
        "registry": load_registry_index(root),
        "mrq": load_mrq_index(root),
    }
    mrq_changed = False
    affected_by_unit: dict[str, list[str]] = {}
    started = time.monotonic()
    gate_results: list[dict[str, Any]] = []
    with ProcessLock(repo_path(root, APPLY_LOCK), run_id), queue_lock(repo_path(root, QUEUE), 30):
        _ensure_mutable_run(root, run_id)
        batch_snapshot, batch_state = _snapshot(declared)
        try:
            for unit in manifest["units"]:
                uid = unit["unit_id"]
                if uid in applied_before:
                    continue
                state = _read_json(_state_path(root, run_id, uid))
                if state.get("state") not in {"completed", "follow_up"}:
                    continue
                result_state = state.get("state")
                decision_path = repo_path(root, f"{RUNS}/{run_id}/decisions/{uid}.json")
                decision = _read_json(decision_path)
                summary = _read_json(repo_path(root, f"{TRACE_ROOT}/{run_id}/{uid}.summary.json"))
                task = tasks.get(str(unit["task_id"]), {})
                reason = ""
                if not set(unit.get("apply_after") or []) <= applied_before | set(applied_now):
                    reason = "dependency_not_applied"
                elif task.get("status") != unit["expected_task_status"] or _task_hash(task) != unit["task_hash"]:
                    reason = "task_changed"
                elif not _verify_fingerprints(root, unit, applied_before | set(applied_now)):
                    reason = "source_changed"
                elif summary.get("decision_sha256") != _file_sha(decision_path) or summary.get("errors"):
                    reason = "trace_invalid"
                else:
                    errors = validate_decision(root, run_id, uid, decision, task_context(root, run_id, uid, shared=shared))
                    reason = "decision_invalid" if errors else ""
                if reason:
                    rejected.append({"unit_id": uid, "reason": reason})
                    if reason != "dependency_not_applied":
                        _set_state(root, run_id, uid, "rejected", reason=reason)
                    continue
                if unit["adapter"] == "review_evidence":
                    affected = _apply_review(root, unit, decision)
                    _validate_affected(root, affected)
                else:
                    affected, changed, requirements, links = _apply_cus(root, unit, decision, requirements, links, registry_items, proposal_sink)
                    mrq_changed = mrq_changed or changed
                    _validate_affected(root, [path for path in affected if path != MRQ_ROOT])
                applied_now.append(uid)
                affected_by_unit[uid] = affected
                _set_state(root, run_id, uid, "applied", result_state=result_state, affected_artifacts=affected)
            if mrq_changed:
                result = replace_mrq_graph(root, requirements, links)
                if result.get("status") != "ok":
                    raise ParallelResearchError(f"migration-requirement publication failed: {result.get('errors')}")
                _validate_affected(root, [MRQ_ROOT])
            if any(CUS_ROOT in paths for paths in affected_by_unit.values()):
                write_jsonl(repo_path(root, CUS_ITEMS), registry_items)
                registry = load_registry_index(root)
                write_cus_summary(root, registry["items"], registry["evidence"], registry["links"])
                export_registry(root)
                registry_check = validate_registry(root)
                if registry_check.get("status") != "ok":
                    raise ParallelResearchError(f"customization registry candidate failed: {registry_check.get('errors')}")
            if proposal_sink:
                proposal_path = repo_path(root, CUS_PROPOSALS)
                existing_keys = {(str(item.get("unit_id")), str(item.get("customization_id"))) for item in _rows(proposal_path)}
                _append_jsonl(proposal_path, [item for item in proposal_sink if (str(item["unit_id"]), str(item["customization_id"])) not in existing_keys])
            ledger_rows = []
            units_by_id = {unit["unit_id"]: unit for unit in manifest["units"]}
            for applied_uid in applied_now:
                affected = affected_by_unit[applied_uid]
                ledger_rows.append({"schema_version": SCHEMA_VERSION, "event": "applied", "run_id": run_id, "unit_id": applied_uid, "adapter": units_by_id[applied_uid]["adapter"], "affected_artifacts": affected, "resulting_hashes": {path: _path_sha(repo_path(root, path)) for path in affected}, "decision_sha256": _file_sha(repo_path(root, f"{RUNS}/{run_id}/decisions/{applied_uid}.json")), "created_at": utc_now_iso()})
            _append_jsonl(ledger_path, ledger_rows)
            _update_tasks(root, manifest, applied_before | set(applied_now))
            for command in heavy_checks or []:
                gate_started = time.monotonic()
                result = subprocess.run(command, cwd=root, text=True, capture_output=True)
                gate_results.append({"command": command, "returncode": result.returncode, "elapsed_seconds": round(time.monotonic() - gate_started, 3)})
                if result.returncode:
                    raise ParallelResearchError(f"heavy check failed: {' '.join(command)}: {result.stderr or result.stdout}")
        except Exception as exc:
            _restore(batch_snapshot, batch_state)
            _append_jsonl(ledger_path, [{"schema_version": SCHEMA_VERSION, "event": "rolled_back", "run_id": run_id, "unit_id": uid, "reason": str(exc), "created_at": utc_now_iso()} for uid in applied_now])
            for uid in applied_now:
                _set_state(root, run_id, uid, "rolled_back", reason=str(exc))
            payload = {"status": "fail", "applied": [], "rolled_back": applied_now, "rejected": rejected, "gate_results": gate_results, "elapsed_seconds": round(time.monotonic() - started, 3), "error": str(exc)}
            _write_json(repo_path(root, f"{RUNS}/{run_id}/apply-summary.json"), payload)
            return payload
        finally:
            shutil.rmtree(batch_snapshot, ignore_errors=True)
        coverage = validate_mrq_graph(root, requirements, links)
        coverage_warnings = coverage.get("warnings") or []
        coverage_counts = {
            "uncovered": sum(str(item).startswith("Uncovered") for item in coverage_warnings),
            "conflicts": len(_phase1_conflicting_cus(links)),
        }
        payload = {
            "status": "ok",
            "goal_complete": _phase1_goal_complete(coverage, coverage_counts),
            "coverage": {"uncovered": coverage_counts.get("uncovered", 0), "conflicts": coverage_counts.get("conflicts", 0)},
            "applied": applied_now,
            "rejected": rejected,
            "follow_up": sum(_read_json(_state_path(root, run_id, uid)).get("result_state") == "follow_up" for uid in applied_now),
            "gate_results": gate_results,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        summary_path = repo_path(root, f"{RUNS}/{run_id}/apply-summary.json")
        existing_summary = _read_json(summary_path)
        if applied_now or rejected or gate_results or existing_summary.get("goal_complete") != payload["goal_complete"] or existing_summary.get("coverage") != payload["coverage"]:
            _write_json(summary_path, payload)
        return payload


def run_coordinator(root: Path, run_id: str, workers: int = MAX_WORKERS, model: str = "", timeout: int = DEFAULT_TIMEOUT, fake: bool = False, apply: bool = False) -> dict[str, Any]:
    root = root.resolve()
    settings = _settings(root)
    workers = workers or int(settings.get("workers") or 3)
    timeout = timeout or int(settings.get("timeout_seconds") or DEFAULT_TIMEOUT)
    if workers < 1 or workers > MAX_WORKERS:
        raise ParallelResearchError(f"workers must be in range 1..{MAX_WORKERS}")
    model = model or ("fake-model" if fake else os.environ.get("CODEX_RESEARCH_MODEL", str(settings.get("model") or DEFAULT_MODEL)))
    _ensure_mutable_run(root, run_id)
    manifest = _load_manifest(root, run_id)
    shared = {
        "manifest": manifest,
        "manifest_hash": _sha(manifest),
        "units": {unit["unit_id"]: unit for unit in manifest["units"]},
        "tasks": {str(row.get("id")): row for row in _rows(repo_path(root, QUEUE))},
    }
    if any(unit["adapter"] != "review_evidence" for unit in manifest["units"]):
        shared["registry"] = load_registry_index(root)
        shared["mrq"] = load_mrq_index(root)
    started = time.monotonic()
    with coordinator_signal_guard(), ProcessLock(repo_path(root, COORDINATOR_LOCK), run_id):
        _ensure_mutable_run(root, run_id)
        pending = []
        reused = []
        results = []
        current_hashes: dict[str, str] = {}
        for unit in manifest["units"]:
            state_path = _state_path(root, run_id, unit["unit_id"])
            state = _read_json(state_path).get("state")
            if state == "running":
                _set_state(root, run_id, unit["unit_id"], "planned", reason="stale_running_state")
                state = "planned"
            if state == "applied":
                reused.append(unit["unit_id"])
            elif state in {"completed", "follow_up"} and _saved_decision_reusable(root, run_id, unit, model, shared["tasks"], current_hashes):
                results.append({"status": "reused", "unit_id": unit["unit_id"]})
            elif state in {"planned", "rejected", "timeout", "running", "rolled_back", "follow_up", "completed"}:
                pending.append(unit)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        active: set[concurrent.futures.Future[dict[str, Any]]] = set()
        pending_iter = iter(pending)
        halt_reason = None
        try:
            for unit in pending_iter:
                active.add(pool.submit(_run_unit, root, run_id, unit["unit_id"], model, timeout, fake, shared))
                if len(active) == workers:
                    break
            while active:
                completed, active = concurrent.futures.wait(active, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in completed:
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {"status": "rejected", "error": str(exc)}
                    results.append(result)
                    if result.get("reason") == "usage_limit":
                        halt_reason = "usage_limit"
                if halt_reason:
                    terminate_active_workers()
                    continue
                for _ in completed:
                    unit = next(pending_iter, None)
                    if unit is not None:
                        active.add(pool.submit(_run_unit, root, run_id, unit["unit_id"], model, timeout, fake, shared))
        except BaseException:
            for future in active:
                future.cancel()
            terminate_active_workers()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        apply_result = apply_ready(root, run_id) if apply else {"status": "not_requested"}
        counts: dict[str, int] = {}
        for result in results:
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        trace_summaries = [_read_json(repo_path(root, f"{TRACE_ROOT}/{run_id}/{unit['unit_id']}.summary.json")) for unit in manifest["units"]]
        elapsed = [float(item["elapsed_seconds"]) for item in trace_summaries if item.get("elapsed_seconds") is not None]
        current_size = int(manifest.get("unit_size") or 1)
        failed = counts.get("rejected", 0) + counts.get("timeout", 0)
        if failed or (elapsed and max(elapsed) > 300):
            next_size, reason = max(1, current_size - 1), "уменьшить после отказа или долгой единицы"
        elif elapsed and max(elapsed) < 90:
            next_size, reason = min(10, current_size + 2), "увеличить после устойчивого быстрого пилота"
        else:
            next_size, reason = current_size, "сохранить текущий размер"
        feedback = {"current_unit_size": current_size, "recommended_unit_size": next_size, "reason": reason, "max_unit_seconds": round(max(elapsed), 3) if elapsed else None, "failed_units": failed}
        summary = {"schema_version": SCHEMA_VERSION, "runner_version": RUNNER_VERSION, "run_id": run_id, "model": model, "timeout": timeout, "worker_limit": workers, "peak_concurrency": min(workers, len(pending)), "elapsed_seconds": round(time.monotonic() - started, 3), "input_tokens": sum(int(item.get("input_tokens") or 0) for item in trace_summaries), "output_tokens": sum(int(item.get("output_tokens") or 0) for item in trace_summaries), "tool_calls": sum(int(item.get("tool_calls") or 0) for item in trace_summaries), "prompt_hashes": sorted({str(item.get("prompt_hash")) for item in trace_summaries if item.get("prompt_hash")}), "unit_counts": counts, "batch_feedback": feedback, "reused": len(reused) + counts.get("reused", 0), "halt_reason": halt_reason, "apply": apply_result}
        _write_json(repo_path(root, f"{RUNS}/{run_id}/summary.json"), summary)
        return {"status": "ok" if all(result.get("status") not in {"rejected", "timeout"} for result in results) and apply_result.get("status") != "fail" else "fail", **summary, "results": results}


def cleanup_traces(root: Path, older_than_days: int, active_run_ids: set[str] | None = None) -> dict[str, Any]:
    cutoff = time.time() - older_than_days * 86400
    active_run_ids = set(active_run_ids or set())
    try:
        lock = _read_json(repo_path(root, COORDINATOR_LOCK))
    except (OSError, ValueError, json.JSONDecodeError):
        lock = {}
    pid = int(lock.get("pid") or 0)
    if pid and lock.get("process_start") == _process_start(pid) and lock.get("run_id"):
        active_run_ids.add(str(lock["run_id"]))
    deleted = []
    for path in repo_path(root, TRACE_ROOT).rglob("*") if repo_path(root, TRACE_ROOT).exists() else []:
        if not path.is_file() or any(run_id in path.parts for run_id in active_run_ids) or path.stat().st_mtime >= cutoff:
            continue
        path.unlink()
        deleted.append(path.relative_to(root).as_posix())
    return {"status": "ok", "deleted": deleted}


def _compacted_audit(run_dir: Path, manifest: dict[str, Any], marker: dict[str, Any]) -> dict[str, Any]:
    units = [str(unit["unit_id"]) for unit in manifest.get("units", [])]
    events = _rows(run_dir / "apply-events.jsonl")
    latest_events = {str(row.get("unit_id")): row for row in events}
    decision_hashes = {unit_id: _file_sha(run_dir / "decisions" / f"{unit_id}.json") for unit_id in units}
    apply_summary = _read_json(run_dir / "apply-summary.json")
    summary = _read_json(run_dir / "summary.json")
    counters = {
        "units": len(units),
        "applied_events": sum(row.get("event") == "applied" for row in events),
        "unit_counts": summary.get("unit_counts", {}),
        "follow_up": apply_summary.get("follow_up", 0),
        "coverage": apply_summary.get("coverage", {}),
        "goal_complete": apply_summary.get("goal_complete"),
    }
    errors = []
    actual_paths = {path.relative_to(run_dir).as_posix() for path in run_dir.rglob("*") if path.is_file() and path.name != "compaction.json"}
    expected_paths = set(marker.get("preserved") or [])
    hashes = marker.get("preserved_hashes") or {}
    if actual_paths != expected_paths or actual_paths != set(hashes):
        errors.append("preserved_paths_mismatch")
    errors.extend(f"preserved_hash_mismatch:{relative}" for relative, digest in hashes.items() if _file_sha(run_dir / relative) != digest)
    if marker.get("unit_count") != len(units):
        errors.append("unit_count_mismatch")
    if marker.get("apply_event_count") != len(events):
        errors.append("apply_event_count_mismatch")
    if marker.get("decision_hashes") != decision_hashes:
        errors.append("decision_hashes_mismatch")
    if marker.get("counters") != counters:
        errors.append("counters_mismatch")
    for unit_id, digest in decision_hashes.items():
        event = latest_events.get(unit_id, {})
        if not digest or event.get("event") != "applied" or event.get("decision_sha256") != digest:
            errors.append(f"unit_integrity_mismatch:{unit_id}")
    if apply_summary.get("status") != "ok" or not summary:
        errors.append("summary_integrity_mismatch")
    return {
        "status": "fail" if errors else "ok",
        "run_id": str(manifest.get("run_id")),
        "compacted": True,
        "integrity_errors": errors,
        "unit_count": len(units),
        "apply_event_count": len(events),
        "decision_hashes": decision_hashes,
        "counters": counters,
        "compaction": marker,
    }


def inspect_run(root: Path, run_id: str, ignore_active: bool = False) -> dict[str, Any]:
    root = root.resolve()
    manifest = _load_manifest(root, run_id)
    run_dir = repo_path(root, f"{RUNS}/{run_id}")
    marker = _read_json(run_dir / "compaction.json")
    if marker:
        return _compacted_audit(run_dir, manifest, marker)

    units = [str(unit["unit_id"]) for unit in manifest.get("units", [])]
    latest_events: dict[str, dict[str, Any]] = {}
    events = []
    for row in _rows(repo_path(root, LEDGER)):
        if row.get("run_id") == run_id:
            events.append(row)
            latest_events[str(row.get("unit_id"))] = row
    reasons = []
    if not ignore_active:
        for lock_path in (COORDINATOR_LOCK, APPLY_LOCK):
            lock = _read_json(repo_path(root, lock_path))
            lock_pid = int(lock.get("pid") or 0)
            if lock.get("run_id") == run_id and lock_pid and lock.get("process_start") == _process_start(lock_pid):
                reasons.append("run_is_active")
                break
    decision_hashes = {}
    for unit_id in units:
        state = _read_json(_state_path(root, run_id, unit_id)).get("state")
        event = latest_events.get(unit_id, {})
        decision = run_dir / "decisions" / f"{unit_id}.json"
        digest = _file_sha(decision)
        if state != "applied":
            reasons.append(f"{unit_id}:state={state or 'missing'}")
        if event.get("event") != "applied":
            reasons.append(f"{unit_id}:not_applied")
        if not digest or event.get("decision_sha256") != digest:
            reasons.append(f"{unit_id}:decision_hash_mismatch")
        decision_hashes[unit_id] = digest
    apply_summary = _read_json(run_dir / "apply-summary.json")
    if apply_summary.get("status") != "ok":
        reasons.append("apply_summary_not_ok")
    if not (run_dir / "summary.json").is_file():
        reasons.append("run_summary_missing")
    files = [path for path in run_dir.rglob("*") if path.is_file()]
    removable = [path for path in files if path.relative_to(run_dir).parts[0] in {"contexts", "schemas", "states"}]
    preserved = [
        path for path in files
        if path.relative_to(run_dir).as_posix() in {"manifest.json", "apply-summary.json", "summary.json"}
        or path.relative_to(run_dir).parts[0] == "decisions"
    ]
    unknown = [path for path in files if path not in removable and path not in preserved]
    if unknown:
        reasons.append("unknown_files:" + ",".join(path.relative_to(run_dir).as_posix() for path in unknown[:10]))
    return {
        "status": "ok",
        "run_id": run_id,
        "compacted": False,
        "eligible": not reasons,
        "reasons": reasons,
        "unit_count": len(units),
        "apply_event_count": len(events),
        "decision_hashes": decision_hashes,
        "before": {"files": len(files), "bytes": sum(path.stat().st_size for path in files)},
        "removable": {"files": len(removable), "bytes": sum(path.stat().st_size for path in removable)},
        "removable_paths": [path.relative_to(run_dir).as_posix() for path in removable],
        "preserved_paths": [path.relative_to(run_dir).as_posix() for path in preserved],
        "unknown_paths": [path.relative_to(run_dir).as_posix() for path in unknown],
        "apply_events": events,
        "counters": {
            "units": len(units),
            "applied_events": sum(row.get("event") == "applied" for row in events),
            "unit_counts": _read_json(run_dir / "summary.json").get("unit_counts", {}),
            "follow_up": apply_summary.get("follow_up", 0),
            "coverage": apply_summary.get("coverage", {}),
            "goal_complete": apply_summary.get("goal_complete"),
        },
    }


def compact_run(root: Path, run_id: str, write: bool = False) -> dict[str, Any]:
    root = root.resolve()
    report = inspect_run(root, run_id)
    if report.get("compacted"):
        return report
    if not report.get("eligible"):
        raise ParallelResearchError("run is not eligible for compaction: " + ", ".join(report["reasons"][:10]))
    if not write:
        return {**report, "written": False, "history_note": "Git history is unchanged"}

    with ProcessLock(repo_path(root, COORDINATOR_LOCK), run_id), ProcessLock(repo_path(root, APPLY_LOCK), run_id):
        report = inspect_run(root, run_id, ignore_active=True)
        if report.get("compacted"):
            return report
        if not report.get("eligible"):
            raise ParallelResearchError("run is not eligible for compaction: " + ", ".join(report["reasons"][:10]))
        run_dir = repo_path(root, f"{RUNS}/{run_id}")
        write_jsonl(run_dir / "apply-events.jsonl", report.pop("apply_events"))
        deleted = report.pop("removable_paths")
        for relative in deleted:
            (run_dir / relative).unlink()
        for folder in ("contexts", "schemas", "states"):
            shutil.rmtree(run_dir / folder, ignore_errors=True)
        remaining = [path for path in run_dir.rglob("*") if path.is_file()]
        marker = {
            "schema_version": "parallel-research-compaction/v1",
            "run_id": run_id,
            "created_at": utc_now_iso(),
            "unit_count": report["unit_count"],
            "apply_event_count": report["apply_event_count"],
            "counters": report["counters"],
            "decision_hashes": report["decision_hashes"],
            "before": report["before"],
            "after": {"files": len(remaining) + 1, "bytes": 0},
            "deleted": deleted,
            "preserved": [path.relative_to(run_dir).as_posix() for path in remaining],
            "preserved_hashes": {path.relative_to(run_dir).as_posix(): _file_sha(path) for path in remaining},
            "history_note": "Git history is unchanged",
        }
        _write_json(run_dir / "compaction.json", marker)
        for _ in range(3):
            final_files = [path for path in run_dir.rglob("*") if path.is_file()]
            after = {"files": len(final_files), "bytes": sum(path.stat().st_size for path in final_files)}
            if marker["after"] == after:
                break
            marker["after"] = after
            _write_json(run_dir / "compaction.json", marker)
    return {"status": "ok", "run_id": run_id, "compacted": True, "written": True, "compaction": marker}


def _print(result: dict[str, Any]) -> int:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ok", "completed", "follow_up", "reused"} else 1


def _command_result(call: Any) -> int:
    try:
        return _print(call())
    except ParallelResearchError as exc:
        return _print({"status": "fail", "error": str(exc)})


def plan_command(args: argparse.Namespace) -> int:
    return _command_result(lambda: plan_run(Path(args.repo_path), args.task_id, args.unit_size, args.limit, args.write))


def context_command(args: argparse.Namespace) -> int:
    return _command_result(lambda: {"status": "ok", **task_context(Path(args.repo_path), args.run_id, args.unit_id, args.max_evidence)})


def exec_command(args: argparse.Namespace) -> int:
    return _command_result(lambda: run_unit(Path(args.repo_path), args.run_id, args.unit_id, args.model, args.timeout, args.fake_exec))


def apply_command(args: argparse.Namespace) -> int:
    checks = [shlex.split(value) for value in args.heavy_check]
    return _command_result(lambda: apply_ready(Path(args.repo_path), args.run_id, checks))


def run_command(args: argparse.Namespace) -> int:
    return _command_result(lambda: run_coordinator(Path(args.repo_path), args.run_id, args.workers, args.model, args.timeout, args.fake_exec, args.apply))


def cleanup_command(args: argparse.Namespace) -> int:
    return _command_result(lambda: cleanup_traces(Path(args.repo_path), args.older_than_days))


def inspect_command(args: argparse.Namespace) -> int:
    return _command_result(lambda: inspect_run(Path(args.repo_path), args.run_id))


def compact_command(args: argparse.Namespace) -> int:
    return _command_result(lambda: compact_run(Path(args.repo_path), args.run_id, args.write))
