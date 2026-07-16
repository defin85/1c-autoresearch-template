from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .analysis_work_planner import plan_second_pass_work
from .common import read_jsonl, read_toml, repo_path, utc_now_iso, write_jsonl
from .queue import load_tasks, queue_lock

PROFILE_SCHEMA_VERSION = "queue-seeding-profile/v1"
REPORT_SCHEMA_VERSION = "queue-seed-report/v1"
SEED_GENERATION = "queue-seed/v1"
OVERRIDE_HEADER = "enabled,action,candidate_key,group_key,priority,type,title,scope_note,reason"
VALID_TYPES = {"discovery", "deep_dive", "review", "manual_markup", "migration_map", "packaging", "needs_infobase_data"}
PROTECTED_STATUSES = {"claimed", "evidence_pack", "drafted", "needs_review", "blocked", "done", "skipped"}
REFRESHABLE_STATUSES = {"pending", "needs_followup"}


class QueueSeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeedProfile:
    name: str
    generator: str
    task_id_prefix: str
    default_type: str
    default_priority: int
    default_quality_gates: list[str]
    required_inputs: list[str]
    optional_inputs: list[str]


@dataclass
class SeedCandidate:
    candidate_key: str
    task_id: str
    title: str
    task_type: str
    priority: int
    feature_id: str
    scope: list[str] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    quality_gates: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    evidence_sources: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    selection_reasons: list[str] = field(default_factory=list)
    source_artifacts: list[str] = field(default_factory=list)
    selector: str = "manual"

    def task_payload(self, profile: SeedProfile, input_fingerprint: str, *, now: str | None = None) -> dict[str, Any]:
        timestamp = now or utc_now_iso()
        return {
            "id": self.task_id,
            "type": self.task_type,
            "status": "pending",
            "priority": self.priority,
            "title": self.title,
            "feature_id": self.feature_id,
            "dependencies": [],
            "scope": self.scope,
            "markers": self.markers,
            "search_terms": self.search_terms,
            "quality_gates": self.quality_gates,
            "expected_outputs": self.expected_outputs,
            "evidence_sources": self.evidence_sources,
            "open_questions": self.open_questions,
            "source_artifacts": self.source_artifacts,
            "selection_reasons": self.selection_reasons,
            "seed_profile": profile.name,
            "seed_candidate_key": self.candidate_key,
            "seed_generation": SEED_GENERATION,
            "seed_input_fingerprint": input_fingerprint,
            "created_at": timestamp,
            "updated_at": timestamp,
        }


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh) if any((value or "").strip() for value in row.values())]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_fingerprint(root: Path, relative: str, required: bool) -> dict[str, Any]:
    path = repo_path(root, relative)
    result: dict[str, Any] = {
        "path": relative,
        "required": required,
        "exists": path.exists(),
        "type": "missing",
        "sha256": "",
        "size": 0,
    }
    if path.is_file():
        result.update({"type": "file", "sha256": _file_sha256(path), "size": path.stat().st_size})
    elif path.is_dir():
        digest = hashlib.sha256()
        count = 0
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            rel = child.relative_to(root).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(_file_sha256(child).encode("ascii"))
            count += 1
        result.update({"type": "directory", "sha256": digest.hexdigest(), "size": count})
    return result


def _normalize_key(value: str, fallback: str) -> str:
    raw = (value or fallback).strip().lower()
    raw = re.sub(r"[^a-z0-9]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    if raw:
        return raw[:48].strip("-") or "item"
    digest = hashlib.sha1((value or fallback).encode("utf-8")).hexdigest()[:10]
    return f"item-{digest}"


def stable_task_id(prefix: str, semantic_key: str) -> str:
    normalized = _normalize_key(semantic_key, "task")
    if normalized.startswith("item-") or len(normalized) > 44:
        digest = hashlib.sha1(f"{prefix}:{semantic_key}".encode("utf-8")).hexdigest()[:10].upper()
        normalized = f"{normalized[:32].strip('-')}-{digest}".strip("-")
    return f"{prefix}-{normalized}".upper()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def load_seed_profile(root: Path, name: str) -> SeedProfile:
    path = repo_path(root, f"analysis/queue/seeding-profiles/{name}.toml")
    if not path.exists():
        raise QueueSeedError(f"Seed profile not found: {path.relative_to(root).as_posix()}")
    try:
        raw = read_toml(path)
    except Exception as exc:
        raise QueueSeedError(f"Could not parse seed profile {name}: {exc}") from exc
    if raw.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise QueueSeedError(f"Seed profile {name} must use schema_version={PROFILE_SCHEMA_VERSION}")
    profile_name = str(raw.get("name", "")).strip()
    if profile_name != name:
        raise QueueSeedError(f"Seed profile filename/name mismatch: filename={name} name={profile_name}")
    default_type = str(raw.get("default_type", "")).strip()
    if default_type not in VALID_TYPES:
        raise QueueSeedError(f"Seed profile {name} has invalid default_type: {default_type}")
    try:
        default_priority = int(raw.get("default_priority"))
    except Exception as exc:
        raise QueueSeedError(f"Seed profile {name} must define integer default_priority") from exc
    required_fields = ("generator", "task_id_prefix")
    for field_name in required_fields:
        if not str(raw.get(field_name, "")).strip():
            raise QueueSeedError(f"Seed profile {name} missing required field: {field_name}")
    inputs = raw.get("inputs", {})
    if not isinstance(inputs, dict):
        raise QueueSeedError(f"Seed profile {name} inputs must be a table")
    profile = SeedProfile(
        name=name,
        generator=str(raw["generator"]).strip(),
        task_id_prefix=str(raw["task_id_prefix"]).strip(),
        default_type=default_type,
        default_priority=default_priority,
        default_quality_gates=_as_list(raw.get("default_quality_gates")),
        required_inputs=_as_list(inputs.get("required")),
        optional_inputs=_as_list(inputs.get("optional")),
    )
    missing = [relative for relative in profile.required_inputs if not repo_path(root, relative).exists()]
    if missing:
        raise QueueSeedError(f"Seed profile {name} missing required input artifact(s): {', '.join(missing)}")
    return profile


def input_fingerprints(root: Path, profile: SeedProfile, queue_path: Path, override_path: Path) -> tuple[list[dict[str, Any]], str, str, list[str]]:
    artifacts = [_path_fingerprint(root, relative, True) for relative in profile.required_inputs]
    warnings: list[str] = []
    for relative in profile.optional_inputs:
        fingerprint = _path_fingerprint(root, relative, False)
        if not fingerprint["exists"]:
            warnings.append(f"Optional artifact is missing: {relative}")
        artifacts.append(fingerprint)
    queue_hash = _file_sha256(queue_path)
    override_hash = _file_sha256(override_path)
    digest = hashlib.sha256()
    for item in sorted(artifacts, key=lambda entry: str(entry["path"])):
        digest.update(str(item["path"]).encode("utf-8"))
        digest.update(str(item["sha256"]).encode("ascii"))
    digest.update(override_hash.encode("ascii"))
    return artifacts, digest.hexdigest(), queue_hash, warnings


def _initial_candidates(root: Path, profile: SeedProfile) -> list[SeedCandidate]:
    del root
    return [
        SeedCandidate(
            candidate_key="initial:discovery",
            task_id=stable_task_id(profile.task_id_prefix, "discovery"),
            title="Create initial functional bucket and feature candidate plan",
            task_type=profile.default_type,
            priority=profile.default_priority,
            feature_id="initial-discovery",
            scope=["project manifest", "available indexes", "source tree overview"],
            quality_gates=profile.default_quality_gates,
            expected_outputs=[
                "analysis/features/initial-discovery/brief.md",
                "analysis/features/initial-discovery/findings.md",
                "analysis/features/initial-discovery/evidence.csv",
                "analysis/features/initial-discovery/open-questions.md",
                "analysis/features/initial-discovery/review.md",
                "analysis/features/initial-discovery/feature-candidates.csv",
            ],
            evidence_sources=["project.toml", "analysis/indexes/diff-inventory.csv", "analysis/indexes/feature-map.csv"],
            source_artifacts=["project.toml", "analysis/indexes/diff-inventory.csv", "analysis/indexes/feature-map.csv"],
            selection_reasons=["Initial seed profile always creates the broad discovery task from repository source-of-truth artifacts."],
            selector="initial-discovery",
        )
    ]


def _second_pass_candidates(root: Path, profile: SeedProfile) -> list[SeedCandidate]:
    candidates: list[SeedCandidate] = []
    planner_result = plan_second_pass_work(root, default_quality_gates=profile.default_quality_gates, default_priority=profile.default_priority)
    for work in planner_result.candidates:
        fields = work.queue_fields
        candidates.append(
            SeedCandidate(
                candidate_key=work.semantic_key,
                task_id=stable_task_id(profile.task_id_prefix, work.semantic_key),
                title=str(fields.get("title") or work.semantic_key),
                task_type=str(fields.get("task_type") or profile.default_type),
                priority=int(fields.get("priority") or profile.default_priority),
                feature_id=work.feature_id,
                scope=_as_list(fields.get("scope")),
                markers=_as_list(fields.get("markers")),
                search_terms=_as_list(fields.get("search_terms")),
                quality_gates=_as_list(fields.get("quality_gates")),
                expected_outputs=_as_list(fields.get("expected_outputs")),
                evidence_sources=_as_list(fields.get("evidence_sources")),
                open_questions=_as_list(fields.get("open_questions")),
                source_artifacts=work.source_artifacts,
                selection_reasons=work.selection_reasons,
                selector=work.work_kind,
            )
        )

    unresolved_path = "analysis/reverse-map/unresolved.csv"
    for row in _read_csv_rows(repo_path(root, unresolved_path)):
        item_id = str(row.get("item_id") or row.get("diff_id") or row.get("workitem_id") or "").strip()
        if not item_id:
            continue
        candidates.append(
            SeedCandidate(
                candidate_key=f"reverse-blocker:{item_id}",
                task_id=stable_task_id(profile.task_id_prefix, f"reverse-blocker:{item_id}"),
                title=f"Разобрать reverse-map blocker: {item_id}",
                task_type="review",
                priority=profile.default_priority + 30,
                feature_id=str(row.get("scenario_id") or row.get("workitem_id") or item_id).strip(),
                scope=[str(row.get("reason") or "").strip(), str(row.get("needed_input") or "").strip()],
                markers=[item_id, str(row.get("diff_id") or "").strip()],
                quality_gates=profile.default_quality_gates,
                evidence_sources=[unresolved_path],
                source_artifacts=[unresolved_path],
                selection_reasons=["Reverse-map unresolved row blocks a final claim or requires follow-up evidence."],
                selector="reverse-map-blocker",
            )
        )

    for relative, key_field, title_field in (
        ("analysis/functional-gaps/open-questions.csv", "question_id", "question"),
        ("outputs/infobase-questions.csv", "question_id", "check_target"),
    ):
        for row in _read_csv_rows(repo_path(root, relative)):
            status = str(row.get("status") or "").strip().lower()
            if status in {"closed", "done", "skipped"}:
                continue
            key = str(row.get(key_field) or row.get("subject_card_slug") or row.get("feature_id") or "").strip()
            if not key:
                continue
            title = str(row.get(title_field) or row.get("reason") or key).strip()
            candidates.append(
                SeedCandidate(
                    candidate_key=f"runtime-topic:{relative}:{key}",
                    task_id=stable_task_id(profile.task_id_prefix, f"runtime:{key}"),
                    title=f"Уточнить runtime/ИБ-вопрос: {title[:120]}",
                    task_type="needs_infobase_data",
                    priority=profile.default_priority + 20,
                    feature_id=str(row.get("feature_id") or row.get("subject_card_slug") or key).strip(),
                    scope=[title, str(row.get("reason") or row.get("needed_source") or "").strip()],
                    markers=[key, str(row.get("subject_card_slug") or "").strip()],
                    quality_gates=["needs_infobase_data_marked"],
                    evidence_sources=[relative],
                    open_questions=[key],
                    source_artifacts=[relative],
                    selection_reasons=["Open runtime/infobase topic needs explicit queue tracking."],
                    selector="runtime-topic",
                )
            )

    return candidates


def generate_candidates(root: Path, profile: SeedProfile) -> list[SeedCandidate]:
    if profile.generator == "initial":
        candidates = _initial_candidates(root, profile)
    elif profile.generator == "second-pass-v8unpack":
        candidates = _second_pass_candidates(root, profile)
    else:
        raise QueueSeedError(f"Unsupported seed profile generator: {profile.generator}")
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()
    for candidate in candidates:
        if candidate.candidate_key in seen_keys:
            raise QueueSeedError(f"Duplicate seed candidate key: {candidate.candidate_key}")
        if candidate.task_id in seen_ids:
            raise QueueSeedError(f"Duplicate generated task id: {candidate.task_id}")
        seen_keys.add(candidate.candidate_key)
        seen_ids.add(candidate.task_id)
        if candidate.task_type not in VALID_TYPES:
            raise QueueSeedError(f"Candidate {candidate.candidate_key} has invalid type: {candidate.task_type}")
    return candidates


def _load_overrides(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        expected = OVERRIDE_HEADER.split(",")
        if reader.fieldnames != expected:
            raise QueueSeedError(f"Override file header must be: {OVERRIDE_HEADER}")
        rows = [dict(row) for row in reader if any((value or "").strip() for value in row.values())]
    warnings: list[str] = []
    valid_actions = {"exclude", "update", "group"}
    for index, row in enumerate(rows, 2):
        enabled = str(row.get("enabled") or "").strip().lower()
        if enabled in {"", "false", "0", "no"}:
            continue
        action = str(row.get("action") or "").strip()
        candidate_key = str(row.get("candidate_key") or "").strip()
        if action not in valid_actions:
            raise QueueSeedError(f"Malformed override at line {index}: unknown action {action}")
        if not candidate_key:
            raise QueueSeedError(f"Malformed override at line {index}: candidate_key is required")
        if row.get("priority", "").strip():
            try:
                int(row["priority"])
            except Exception as exc:
                raise QueueSeedError(f"Malformed override at line {index}: priority must be integer") from exc
        if row.get("type", "").strip() and row["type"].strip() not in VALID_TYPES:
            raise QueueSeedError(f"Malformed override at line {index}: invalid type {row['type']}")
    return rows, warnings


def apply_overrides(candidates: list[SeedCandidate], overrides: list[dict[str, str]]) -> tuple[list[SeedCandidate], list[str], list[str]]:
    by_key = {candidate.candidate_key: candidate for candidate in candidates}
    excluded: set[str] = set()
    warnings: list[str] = []
    group_map: dict[str, list[SeedCandidate]] = {}
    group_titles: dict[str, str] = {}
    group_notes: dict[str, str] = {}

    for row in overrides:
        enabled = str(row.get("enabled") or "").strip().lower()
        if enabled in {"", "false", "0", "no"}:
            continue
        key = str(row.get("candidate_key") or "").strip()
        candidate = by_key.get(key)
        if candidate is None:
            warnings.append(f"Override does not match any candidate: {key}")
            continue
        action = str(row.get("action") or "").strip()
        if action == "exclude":
            excluded.add(key)
            reason = str(row.get("reason") or "").strip()
            if reason:
                candidate.selection_reasons.append(f"Excluded by override: {reason}")
            continue
        if row.get("priority", "").strip():
            candidate.priority = int(row["priority"])
        if row.get("type", "").strip():
            candidate.task_type = row["type"].strip()
        if row.get("title", "").strip():
            candidate.title = row["title"].strip()
        if row.get("scope_note", "").strip():
            candidate.scope.append(row["scope_note"].strip())
        if action == "group":
            group_key = str(row.get("group_key") or "").strip()
            if not group_key:
                raise QueueSeedError(f"Group override for {key} must define group_key")
            group_map.setdefault(group_key, []).append(candidate)
            if row.get("title", "").strip():
                group_titles[group_key] = row["title"].strip()
            if row.get("scope_note", "").strip():
                group_notes[group_key] = row["scope_note"].strip()

    grouped_members = {member.candidate_key for members in group_map.values() for member in members}
    result = [candidate for candidate in candidates if candidate.candidate_key not in excluded and candidate.candidate_key not in grouped_members]
    for group_key, members in group_map.items():
        members = [member for member in members if member.candidate_key not in excluded]
        if not members:
            continue
        first = members[0]
        grouped = SeedCandidate(
            candidate_key=f"group:{group_key}",
            task_id=stable_task_id(first.task_id.rsplit("-", 1)[0], f"group:{group_key}"),
            title=group_titles.get(group_key) or f"Уточнить группу кандидатов: {group_key}",
            task_type=first.task_type,
            priority=max(member.priority for member in members),
            feature_id=first.feature_id,
            scope=[item for member in members for item in member.scope] + ([group_notes[group_key]] if group_key in group_notes else []),
            markers=sorted({item for member in members for item in member.markers if item}),
            search_terms=sorted({item for member in members for item in member.search_terms if item}),
            quality_gates=sorted({item for member in members for item in member.quality_gates if item}),
            expected_outputs=sorted({item for member in members for item in member.expected_outputs if item}),
            evidence_sources=sorted({item for member in members for item in member.evidence_sources if item}),
            open_questions=sorted({item for member in members for item in member.open_questions if item}),
            selection_reasons=[f"Grouped candidates: {', '.join(member.candidate_key for member in members)}"],
            source_artifacts=sorted({item for member in members for item in member.source_artifacts if item}),
            selector="override-group",
        )
        result.append(grouped)
    return result, sorted(excluded), warnings


def build_seed_plan(root: Path, profile_name: str, queue_path: Path, override_path: Path) -> dict[str, Any]:
    profile = load_seed_profile(root, profile_name)
    artifacts, input_fingerprint, queue_hash_before, warnings = input_fingerprints(root, profile, queue_path, override_path)
    overrides, override_warnings = _load_overrides(override_path)
    candidates = generate_candidates(root, profile)
    candidates, excluded, override_apply_warnings = apply_overrides(candidates, overrides)
    warnings.extend(override_warnings)
    warnings.extend(override_apply_warnings)
    task_payloads = [candidate.task_payload(profile, input_fingerprint) for candidate in sorted(candidates, key=lambda item: item.task_id)]
    existing = {str(task.get("id")): task for task in load_tasks(queue_path)}
    actions: list[dict[str, Any]] = []
    generated_ids = {task["id"] for task in task_payloads}
    for task in task_payloads:
        existing_task = existing.get(task["id"])
        if existing_task is None:
            action = "create"
        elif existing_task.get("status") in REFRESHABLE_STATUSES:
            action = "update"
        elif existing_task.get("status") in PROTECTED_STATUSES:
            action = "protected"
        else:
            action = "error"
        actions.append({"task_id": task["id"], "candidate_key": task["seed_candidate_key"], "action": action})
    for task in existing.values():
        if task.get("seed_profile") == profile.name and task.get("seed_generation") == SEED_GENERATION and str(task.get("id")) not in generated_ids:
            actions.append({"task_id": task.get("id"), "candidate_key": task.get("seed_candidate_key", ""), "action": "stale"})
    counts: dict[str, int] = {}
    for action in actions:
        counts[action["action"]] = counts.get(action["action"], 0) + 1
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "profile": profile.name,
        "mode": "plan",
        "generated_at": utc_now_iso(),
        "input_fingerprint": input_fingerprint,
        "input_artifacts": artifacts,
        "override_path": override_path.relative_to(root).as_posix(),
        "override_fingerprint": _file_sha256(override_path),
        "queue_path": queue_path.relative_to(root).as_posix(),
        "queue_hash_before": queue_hash_before,
        "queue_hash_after": "",
        "candidate_count": len(task_payloads),
        "task_ids": [task["id"] for task in task_payloads],
        "tasks": task_payloads,
        "actions": actions,
        "counts": counts,
        "excluded_candidate_keys": excluded,
        "warnings": warnings,
        "errors": [action for action in actions if action["action"] == "error"],
    }


def write_seed_report(root: Path, report: dict[str, Any]) -> Path:
    safe_profile = _normalize_key(str(report["profile"]), "profile")
    safe_mode = _normalize_key(str(report["mode"]), "mode")
    timestamp = str(report["generated_at"]).replace(":", "").replace("+", "Z")
    path = repo_path(root, f"analysis/queue/seed-runs/{timestamp}-{safe_profile}-{safe_mode}.json")
    _write_json(path, report)
    return path


def apply_seed_plan(root: Path, plan: dict[str, Any], queue_path: Path) -> dict[str, Any]:
    if plan.get("errors"):
        raise QueueSeedError("Seed plan contains merge errors; refusing to apply")
    with queue_lock(queue_path):
        current = load_tasks(queue_path)
        by_id = {str(task.get("id")): task for task in current}
        now = utc_now_iso()
        for action in plan["actions"]:
            if action["action"] not in {"create", "update"}:
                continue
            task = next(item for item in plan["tasks"] if item["id"] == action["task_id"])
            if action["action"] == "create":
                task = dict(task)
                task["created_at"] = now
                task["updated_at"] = now
                current.append(task)
            else:
                existing = by_id[action["task_id"]]
                preserved = {
                    key: existing[key]
                    for key in ("status", "claimed_by", "claimed_at", "completed_at", "result_summary", "created_at")
                    if key in existing
                }
                existing.clear()
                existing.update(task)
                existing.update(preserved)
                existing["updated_at"] = now
        write_jsonl(queue_path, current)
    plan["mode"] = "apply"
    plan["queue_hash_after"] = _file_sha256(queue_path)
    return plan


def seed_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    queue_path = repo_path(root, args.queue_path)
    override_path = repo_path(root, args.override_path)
    if args.plan == args.apply:
        raise QueueSeedError("Specify exactly one of --plan or --apply")
    plan = build_seed_plan(root, args.profile, queue_path, override_path)
    report = apply_seed_plan(root, plan, queue_path) if args.apply else plan
    path = write_seed_report(root, report)
    payload = {
        "profile": report["profile"],
        "mode": report["mode"],
        "candidate_count": report["candidate_count"],
        "counts": report["counts"],
        "warnings": report["warnings"],
        "report_path": path.relative_to(root).as_posix(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def validate_seed_configuration(root: Path) -> list[str]:
    errors: list[str] = []
    profile_dir = repo_path(root, "analysis/queue/seeding-profiles")
    for name in ("initial", "second-pass-v8unpack"):
        try:
            load_seed_profile(root, name)
        except Exception as exc:
            errors.append(str(exc))
    override_path = repo_path(root, "analysis/queue/seeding-overrides.csv")
    if override_path.exists():
        try:
            _load_overrides(override_path)
        except Exception as exc:
            errors.append(str(exc))
    if not profile_dir.exists():
        errors.append("Missing seed profile directory: analysis/queue/seeding-profiles")
    return errors
