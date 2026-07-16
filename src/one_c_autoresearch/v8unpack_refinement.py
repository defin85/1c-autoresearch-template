from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from .common import read_jsonl, repo_path, utc_now_iso


REFINEMENT_DIR = "analysis/v8unpack-refinement"
DASHBOARD_DIR = "outputs/v8unpack-refinement-dashboard"
NESTED_REPO = "analysis/cache/noise/clean-rebase-v8unpack/repo"
FIRST_PASS_QUEUE = "analysis/v8unpack-cleanup/object-queue.csv"
FIRST_PASS_LEDGER = "analysis/v8unpack-cleanup/decisions.jsonl"
VENDOR_TAG = "vendor-baseline"
TARGET_TAG = "target-cf"
SOURCE_BRANCH = "manual-cleanup"
REFINEMENT_BRANCH = "v8unpack-refinement"
REFINEMENT_BATCH = "BATCH-R001"
MANUAL_MARKUP = "analysis/detailed-register-reverse-review/markup.csv"

QUEUE_HEADER = [
    "item_id",
    "source_first_pass_group_id",
    "object_group",
    "object_kind",
    "object_name",
    "first_pass_decision",
    "first_pass_batch",
    "evidence_type",
    "feature_hint",
    "risk",
    "status",
    "decision",
    "noise_class",
    "related_paths",
    "path_count",
    "current_diff_paths",
    "current_diff_path_count",
    "normalization_check",
    "review_action",
    "rationale",
    "batch_id",
    "commit_ref",
    "evidence_ref",
    "notes",
]

EVIDENCE_CLASSES = {
    "module",
    "form",
    "template-content",
    "metadata-text",
    "mixed",
    "binary",
    "predefined-data",
    "unknown",
}


def _run_git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotePath=false", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _json_list(value: str) -> list[str]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=QUEUE_HEADER, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in QUEUE_HEADER})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _name_status(repo: Path, right_ref: str) -> list[tuple[str, str]]:
    result = _run_git(repo, ["diff", "--name-status", "--find-renames", f"{VENDOR_TAG}..{right_ref}"])
    entries: list[tuple[str, str]] = []
    for raw in result.stdout.splitlines():
        parts = raw.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            entries.append((status, parts[2]))
        else:
            entries.append((status, parts[1]))
    return entries


def _object_group_from_key(key_object: str) -> str:
    if "." not in key_object:
        return ""
    kind, name = key_object.split(".", 1)
    mapping = {
        "AccountingRegister": "AccountingRegister",
        "AccumulationRegister": "AccumulationRegister",
        "Catalog": "Catalog",
        "ChartOfAccounts": "ChartOfAccounts",
        "ChartOfCalculationTypes": "ChartOfCalculationTypes",
        "ChartOfCharacteristicType": "ChartOfCharacteristicType",
        "CommonCommand": "CommonCommand",
        "CommonForm": "CommonForm",
        "CommonModule": "CommonModule",
        "CommonPicture": "CommonPicture",
        "CommonTemplate": "CommonTemplate",
        "Constant": "Constant",
        "DataProcessor": "DataProcessor",
        "Document": "Document",
        "DocumentJournal": "DocumentJournal",
        "Enum": "Enum",
        "EventSubscription": "EventSubscription",
        "ExchangePlan": "ExchangePlan",
        "InformationRegister": "InformationRegister",
        "Interface": "Interface",
        "Report": "Report",
        "Role": "Role",
        "ScheduledJob": "ScheduledJob",
        "Sequence": "Sequence",
        "Subsystem": "Subsystem",
        "WSReference": "WSReference",
    }
    root = mapping.get(kind)
    if not root:
        return ""
    return f"{root}/{name}"


def _suffix(path: str) -> str:
    return Path(path).suffix.lower()


def _cleanup_recommendation(row: dict[str, str], diff_rows: list[tuple[str, str]]) -> tuple[str, str]:
    if not diff_rows:
        return "already_clean", "В текущем очищенном сравнении по объекту нет diff."
    statuses = {status for status, _ in diff_rows}
    suffixes = {_suffix(path) for _, path in diff_rows}
    review_status = row.get("review_status", "")
    confidence = row.get("confidence", "")
    has_payload = bool(suffixes & {".bsl", ".mxl", ".bin", ".c1b64", ".wsdl", ".txt", ".xsd"})
    if review_status == "technical_supporting" and statuses <= {"M"} and suffixes <= {".html", ".json"}:
        return "auto_candidate", "Ручная разметка считает строку технической опорой; текущий diff содержит только измененные HTML/JSON sidecar-файлы."
    if review_status == "technical_supporting":
        return "manual_review", "Техническая опора, но diff содержит payload или не только измененные HTML/JSON; нужен ручной просмотр перед откатом."
    if review_status == "needs_evidence":
        return "do_not_auto_clean", "Недостаточно доказательств не равно доказанный шум; не чистить автоматически."
    if review_status == "external_postponed":
        return "do_not_auto_clean", "Внешний источник отложен; физическую очистку по конфигурационному diff не делать."
    if review_status in {"corrected", "card_mismatch"}:
        return "keep", "Ручная разметка подтвердила или уточнила бизнес-привязку; это не основание для физической очистки."
    if has_payload:
        return "manual_review", "Diff содержит код, макет или бинарную полезную нагрузку; нужен ручной разбор."
    if confidence == "low":
        return "manual_review", "Низкая уверенность ручной разметки; нужен ручной разбор."
    return "manual_review", "Нет узкого безопасного правила автоочистки."


def _classify_evidence(paths: list[str], primary: str, group_type: str) -> str:
    lowered = [path.lower() for path in paths]
    classes: set[str] = set()
    if any(path.endswith(".c1brace") or path.endswith(".bin") for path in lowered):
        classes.add("binary")
    if any("predefined" in path or "предопредел" in path for path in lowered):
        classes.add("predefined-data")
    if any("template" in path or "commontemplate" in path for path in lowered):
        if any(path.endswith((".mxl", ".html", ".bin")) for path in lowered):
            classes.add("template-content")
        else:
            classes.add("metadata-text")
    if any(path.endswith(".bsl") for path in lowered):
        classes.add("module")
    if group_type == "form" or any("form/" in path or "form." in path or "form/" in path.replace("\\", "/") for path in lowered):
        classes.add("form")
    if any(path.endswith((".json", ".html")) for path in lowered):
        classes.add("metadata-text")
    if primary == "mixed" or len(classes) > 1:
        return "mixed"
    if primary == "module":
        return "module"
    if primary == "form":
        return "form"
    if primary == "metadata":
        return "metadata-text"
    if primary in EVIDENCE_CLASSES:
        return primary
    return next(iter(classes), "unknown")


def _feature_hint(text: str) -> str:
    source = text.lower()
    groups = [
        (("ндс", "ндфл", "налог", "взнос", "фсбу", "регламентирован"), "налоги и регламентированный учет"),
        (("бюджет", "бддс", "бдр", "план", "лимит"), "бюджетирование и планирование"),
        (("обмен", "интеграц", "xml", "эдо", "электронн"), "интеграции и обмены"),
        (("перевоз", "транспорт", "маршрут", "логист", "заявк"), "логистика и транспорт"),
        (("зарплат", "кадр", "сотрудник", "ндфл"), "кадры и зарплата"),
        (("печать", "форма", "отчет", "макет"), "формы, печать и отчеты"),
        (("тнл", "тнф", "бро", "бэд"), "корпоративные расширения"),
    ]
    for needles, label in groups:
        if any(needle in source for needle in needles):
            return label
    return "прочие объекты"


def _text_hint_from_paths(repo: Path, paths: list[str], limit: int = 5, max_chars: int = 12000) -> str:
    chunks: list[str] = []
    text_suffixes = {".bsl", ".json", ".html", ".txt", ".mxl"}
    for relative in paths[:limit]:
        path = repo / relative
        if path.suffix.lower() not in text_suffixes or not path.exists() or not path.is_file():
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore")[:max_chars])
        except OSError:
            continue
    return "\n".join(chunks)


def _risk_for(evidence_type: str, current_diff_count: int) -> str:
    if current_diff_count == 0 or evidence_type == "unknown":
        return "high"
    if evidence_type in {"binary", "predefined-data", "mixed", "template-content"}:
        return "high"
    if evidence_type in {"form", "metadata-text"}:
        return "medium"
    return "low"


def _status_decision(evidence_type: str, current_diff_count: int) -> tuple[str, str, str, str, str]:
    if current_diff_count == 0:
        return (
            "blocked",
            "blocked_by_missing_evidence",
            "missing-current-diff-path",
            "Путь первого прохода отсутствует в текущем очищенном сравнении; нужен ручной разбор перед удалением.",
            "Проверить соответствие group_id текущему diff.",
        )
    if evidence_type == "unknown":
        return (
            "blocked",
            "blocked_by_missing_evidence",
            "unclassified-evidence",
            "Класс доказательства не определен автоматически; удаление запрещено до ручного анализа.",
            "Назначить класс доказательства и критерий проверки.",
        )
    if evidence_type in {"binary", "predefined-data"}:
        return (
            "manual_review",
            "needs_manual_review",
            "requires-normalized-payload-digest",
            "Бинарные или предопределенные данные сохранены: нет воспроизводимого нормализованного digest для безопасного удаления.",
            "Получить извлеченный или нормализованный digest перед возможным revert.",
        )
    return (
        "kept",
        "preserve_customization",
        "no-safe-second-pass-noise-criterion",
        "Кандидат сохранен: воспроизводимый критерий шума второго прохода не доказан, поэтому изменение остается в очищенном сравнении.",
        "Использовать как retained candidate для reverse-map или предметного анализа.",
    )


def _normalization_check(evidence_type: str) -> str:
    checks = {
        "template-content": "requires_template_extractor_or_normalized_digest",
        "metadata-text": "eligible_for_semantic_projection_review",
        "binary": "requires_binary_payload_digest",
        "predefined-data": "requires_predefined_data_payload_digest",
        "mixed": "requires_class_split_before_revert",
        "form": "requires_form_semantic_projection_review",
        "module": "requires_bsl_semantic_review",
        "unknown": "requires_manual_classification",
    }
    return checks.get(evidence_type, "requires_manual_review")


def _ensure_refinement_branch(repo: Path) -> str:
    current = _run_git(repo, ["branch", "--show-current"]).stdout.strip()
    if current != REFINEMENT_BRANCH:
        _run_git(repo, ["checkout", "-B", REFINEMENT_BRANCH, SOURCE_BRANCH])
    existing = _run_git(repo, ["log", "--format=%s", "-1"], check=False).stdout.strip()
    if existing != f"{REFINEMENT_BATCH} retain classified v8unpack candidates":
        _run_git(
            repo,
            [
                "-c",
                "user.name=one-c-autoresearch",
                "-c",
                "user.email=one-c-autoresearch@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                f"{REFINEMENT_BATCH} retain classified v8unpack candidates",
            ],
        )
    return _run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()


def _first_pass_state(root: Path) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    queue_rows = _read_csv(repo_path(root, FIRST_PASS_QUEUE))
    ledger_rows = [row for _, row in read_jsonl(repo_path(root, FIRST_PASS_LEDGER))]
    queue_ids = [row.get("group_id", "") for row in queue_rows]
    ledger_ids = [str(row.get("group_id", "")) for row in ledger_rows]
    if len(queue_ids) != len(set(queue_ids)):
        raise RuntimeError("First-pass queue contains duplicate group_id values")
    if len(ledger_ids) != len(set(ledger_ids)):
        raise RuntimeError("First-pass ledger contains duplicate group_id values")
    if set(queue_ids) != set(ledger_ids):
        raise RuntimeError("First-pass queue and ledger group_id sets differ")
    pending = [row for row in queue_rows if row.get("review_status") == "pending"]
    if pending:
        raise RuntimeError(f"First-pass queue still has pending groups: {len(pending)}")
    return queue_rows, ledger_rows


def build_refinement(root: Path, force: bool = False) -> dict[str, Any]:
    nested_repo = repo_path(root, NESTED_REPO)
    refinement_dir = repo_path(root, REFINEMENT_DIR)
    dashboard_dir = repo_path(root, DASHBOARD_DIR)
    decisions_path = refinement_dir / "decisions.jsonl"
    if decisions_path.exists() and not force:
        raise FileExistsError(f"{decisions_path.relative_to(root)} already exists; rerun with --force")

    top = Path(_run_git(nested_repo, ["rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
    if top != nested_repo.resolve():
        raise RuntimeError(f"Nested git top-level mismatch: {top}")
    initial_status = _run_git(nested_repo, ["status", "--short"]).stdout.strip()
    if initial_status:
        raise RuntimeError(f"Nested git repository has uncommitted work:\n{initial_status}")

    vendor_commit = _run_git(nested_repo, ["rev-parse", VENDOR_TAG]).stdout.strip()
    target_commit = _run_git(nested_repo, ["rev-parse", TARGET_TAG]).stdout.strip()
    manual_cleanup_commit = _run_git(nested_repo, ["rev-parse", SOURCE_BRANCH]).stdout.strip()
    clean_entries = _name_status(nested_repo, SOURCE_BRANCH)
    clean_diff_paths = {path for _, path in clean_entries}
    queue_rows, ledger_rows = _first_pass_state(root)
    ledger_by_id = {str(row["group_id"]): row for row in ledger_rows}

    refinement_commit = _ensure_refinement_branch(nested_repo)
    refined_entries = _name_status(nested_repo, REFINEMENT_BRANCH)
    refined_diff_paths = {path for _, path in refined_entries}
    final_status = _run_git(nested_repo, ["status", "--short"]).stdout.strip()
    if final_status:
        raise RuntimeError(f"Nested git repository is dirty after refinement branch setup:\n{final_status}")
    if _run_git(nested_repo, ["rev-parse", VENDOR_TAG]).stdout.strip() != vendor_commit:
        raise RuntimeError("vendor-baseline changed during refinement")
    if _run_git(nested_repo, ["rev-parse", TARGET_TAG]).stdout.strip() != target_commit:
        raise RuntimeError("target-cf changed during refinement")

    kept_first_pass = [row for row in queue_rows if row.get("decision") == "keep"]
    refined_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    now = utc_now_iso()
    for index, row in enumerate(kept_first_pass, 1):
        related_paths = _json_list(row.get("related_paths_json", "[]"))
        current_paths = [path for path in related_paths if path in refined_diff_paths]
        evidence_type = _classify_evidence(related_paths, row.get("primary_evidence_type", ""), row.get("group_type", ""))
        risk = _risk_for(evidence_type, len(current_paths))
        status, decision, noise_class, rationale, action = _status_decision(evidence_type, len(current_paths))
        feature_text = " ".join([row.get("object_group", ""), *related_paths])
        feature_text = f"{feature_text}\n{_text_hint_from_paths(nested_repo, current_paths)}"
        feature_hint = _feature_hint(feature_text)
        item_id = f"VR-{index:05d}"
        commit_ref = refinement_commit
        evidence_ref = f"{NESTED_REPO}#{row.get('object_group', '')}"
        queue_row = {
            "item_id": item_id,
            "source_first_pass_group_id": row.get("group_id", ""),
            "object_group": row.get("object_group", ""),
            "object_kind": row.get("object_kind", ""),
            "object_name": row.get("object_name", ""),
            "first_pass_decision": "keep",
            "first_pass_batch": row.get("batch_id", ""),
            "evidence_type": evidence_type,
            "feature_hint": feature_hint,
            "risk": risk,
            "status": status,
            "decision": decision,
            "noise_class": noise_class,
            "related_paths": json.dumps(related_paths, ensure_ascii=False, separators=(",", ":")),
            "path_count": len(related_paths),
            "current_diff_paths": json.dumps(current_paths, ensure_ascii=False, separators=(",", ":")),
            "current_diff_path_count": len(current_paths),
            "normalization_check": _normalization_check(evidence_type),
            "review_action": action,
            "rationale": rationale,
            "batch_id": REFINEMENT_BATCH,
            "commit_ref": commit_ref,
            "evidence_ref": evidence_ref,
            "notes": "Second-pass conservative classification; no path was restored without stronger reproducible evidence.",
        }
        refined_rows.append(queue_row)
        first_pass_decision = ledger_by_id[row.get("group_id", "")]
        decision_rows.append(
            {
                "timestamp": now,
                "batch_id": REFINEMENT_BATCH,
                "item_id": item_id,
                "source_first_pass_group_id": row.get("group_id", ""),
                "object_group": row.get("object_group", ""),
                "decision": decision,
                "status": status,
                "evidence_type": evidence_type,
                "feature_hint": feature_hint,
                "risk": risk,
                "noise_class": noise_class,
                "rationale": rationale,
                "evidence": {
                    "related_paths": related_paths,
                    "current_diff_paths": current_paths,
                    "first_pass_batch": row.get("batch_id", ""),
                    "first_pass_rationale": first_pass_decision.get("rationale", ""),
                    "normalization_check": _normalization_check(evidence_type),
                },
                "commands": [
                    f"git -C {NESTED_REPO} diff {VENDOR_TAG}..{REFINEMENT_BRANCH} -- {row.get('object_group', '')}",
                    f"git -C {NESTED_REPO} show {commit_ref}",
                ],
                "before_commit": manual_cleanup_commit,
                "after_commit": commit_ref,
                "reviewer": "codex",
                "notes": "No additional revert was applied in this batch.",
            }
        )

    counts_by_status = Counter(row["status"] for row in refined_rows)
    counts_by_decision = Counter(row["decision"] for row in refined_rows)
    counts_by_evidence = Counter(row["evidence_type"] for row in refined_rows)
    counts_by_risk = Counter(row["risk"] for row in refined_rows)
    summary = {
        "schema_version": "v8unpack-refinement/v1",
        "generated_at": now,
        "nested_repo": NESTED_REPO,
        "source_branch": SOURCE_BRANCH,
        "refinement_branch": REFINEMENT_BRANCH,
        "vendor_baseline": vendor_commit,
        "target_cf": target_commit,
        "starting_manual_cleanup": manual_cleanup_commit,
        "refinement_head": refinement_commit,
        "first_pass": {
            "queue_rows": len(queue_rows),
            "ledger_rows": len(ledger_rows),
            "kept": sum(1 for row in queue_rows if row.get("decision") == "keep"),
            "reverted": sum(1 for row in queue_rows if row.get("decision") == "revert"),
            "pending": sum(1 for row in queue_rows if row.get("review_status") == "pending"),
        },
        "diff": {
            "clean_diff_command": f"git -C {NESTED_REPO} diff {VENDOR_TAG}..{SOURCE_BRANCH}",
            "refined_diff_command": f"git -C {NESTED_REPO} diff {VENDOR_TAG}..{REFINEMENT_BRANCH}",
            "first_pass_clean_diff_paths": len(clean_entries),
            "refined_diff_paths": len(refined_entries),
            "paths_restored_in_second_pass": 0,
        },
        "refinement": {
            "queue_rows": len(refined_rows),
            "processed_decisions": len(decision_rows),
            "counts_by_status": dict(sorted(counts_by_status.items())),
            "counts_by_decision": dict(sorted(counts_by_decision.items())),
            "counts_by_evidence_type": dict(sorted(counts_by_evidence.items())),
            "counts_by_risk": dict(sorted(counts_by_risk.items())),
            "confirmed_customizations": 0,
            "retained_candidates": counts_by_status.get("kept", 0),
            "reverted_noise": counts_by_status.get("reverted", 0),
            "manual_review": counts_by_status.get("manual_review", 0),
            "blocked": counts_by_status.get("blocked", 0),
        },
        "noise_criteria": [
            {
                "id": "template-normalized-digest",
                "status": "not_applied",
                "reason": "No authoritative template extractor or normalized digest was configured for this pass.",
            },
            {
                "id": "metadata-semantic-projection",
                "status": "not_applied",
                "reason": "No second-pass projection with preserved human-readable and structural evidence was proven beyond first-pass rules.",
            },
            {
                "id": "binary-or-predefined-payload-digest",
                "status": "not_applied",
                "reason": "No extracted binary/predefined payload digest was available.",
            },
        ],
        "batch_reports": [f"{REFINEMENT_DIR}/batches/{REFINEMENT_BATCH}.md"],
        "dashboard": {
            "data": f"{DASHBOARD_DIR}/data.json",
            "html": f"{DASHBOARD_DIR}/index.html",
        },
    }

    refinement_dir.mkdir(parents=True, exist_ok=True)
    (refinement_dir / "batches").mkdir(exist_ok=True)
    (refinement_dir / "reports").mkdir(exist_ok=True)
    _write_csv(refinement_dir / "refinement-queue.csv", refined_rows)
    _write_jsonl(decisions_path, decision_rows)
    _write_json(refinement_dir / "summary.json", summary)
    _write_reports(root, summary, clean_entries, refined_entries)
    _write_readme(refinement_dir / "README.md", summary)
    _write_batch_report(refinement_dir / "batches" / f"{REFINEMENT_BATCH}.md", summary)
    dashboard_data = _dashboard_data(summary, refined_rows, decision_rows)
    _write_json(dashboard_dir / "data.json", dashboard_data)
    (dashboard_dir / "index.html").write_text(_dashboard_html(dashboard_data), encoding="utf-8", newline="\n")
    return {
        "status": "ok",
        "queue_rows": len(refined_rows),
        "processed_decisions": len(decision_rows),
        "summary": str((refinement_dir / "summary.json").relative_to(root)),
        "dashboard": str((dashboard_dir / "index.html").relative_to(root)),
        "refinement_head": refinement_commit,
    }


def _write_reports(root: Path, summary: dict[str, Any], clean_entries: list[tuple[str, str]], refined_entries: list[tuple[str, str]]) -> None:
    reports = repo_path(root, f"{REFINEMENT_DIR}/reports")
    nested_repo = repo_path(root, NESTED_REPO)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "clean-name-status.txt").write_text(
        "".join(f"{status}\t{path}\n" for status, path in clean_entries),
        encoding="utf-8",
        newline="\n",
    )
    (reports / "refined-name-status.txt").write_text(
        "".join(f"{status}\t{path}\n" for status, path in refined_entries),
        encoding="utf-8",
        newline="\n",
    )
    clean_stat = _run_git(nested_repo, ["diff", "--stat", f"{VENDOR_TAG}..{SOURCE_BRANCH}"]).stdout
    refined_stat = _run_git(nested_repo, ["diff", "--stat", f"{VENDOR_TAG}..{REFINEMENT_BRANCH}"]).stdout
    (reports / "clean-diff-stat.txt").write_text(clean_stat, encoding="utf-8", newline="\n")
    (reports / "refined-diff-stat.txt").write_text(refined_stat, encoding="utf-8", newline="\n")
    _write_json(reports / "verification.json", {
        "vendor_baseline": summary["vendor_baseline"],
        "target_cf": summary["target_cf"],
        "nested_repo_status": "clean",
        "ledger_processed_equals_queue": summary["refinement"]["processed_decisions"] == summary["refinement"]["queue_rows"],
    })


def _write_readme(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Второй проход очистки v8unpack",
        "",
        "Этот каталог хранит durable-состояние второго прохода над уже очищенной веткой `manual-cleanup`.",
        "Первый журнал `analysis/v8unpack-cleanup/` остается историческим источником доказательств и не переписывается.",
        "",
        "## Входные ссылки",
        "",
        f"- Вложенный Git: `{summary['nested_repo']}`",
        f"- Сырой baseline: `{VENDOR_TAG}` -> `{summary['vendor_baseline']}`",
        f"- Сырой target: `{TARGET_TAG}` -> `{summary['target_cf']}`",
        f"- Стартовый `manual-cleanup`: `{summary['starting_manual_cleanup']}`",
        f"- Refinement-ветка: `{summary['refinement_branch']}` -> `{summary['refinement_head']}`",
        "",
        "## Команды продолжения",
        "",
        "```bash",
        "python3 -m one_c_autoresearch v8unpack-refinement build --force",
        "python3 -m one_c_autoresearch v8unpack-refinement smoke",
        f"git -C {summary['nested_repo']} status --short",
        f"git -C {summary['nested_repo']} diff {VENDOR_TAG}..{summary['refinement_branch']}",
        "```",
        "",
        "## Guardrails",
        "",
        "- Не перемещать теги `vendor-baseline` и `target-cf`.",
        "- Не перестраивать исходные `v8unpack`-снимки в рамках второго прохода.",
        "- Удалять пути только при наличии документированного воспроизводимого критерия.",
        "- Неоднозначные кандидаты оставлять в статусе `kept`, `manual_review` или `blocked`.",
        "- Панель `outputs/v8unpack-refinement-dashboard/` пересобирается из queue, ledger, summary и reports.",
        "",
        "## Текущая сводка",
        "",
        f"- Очередь второго прохода: {summary['refinement']['queue_rows']}",
        f"- Обработанные решения: {summary['refinement']['processed_decisions']}",
        f"- Сохраненные кандидаты: {summary['refinement']['retained_candidates']}",
        f"- Ручной разбор: {summary['refinement']['manual_review']}",
        f"- Блокеры: {summary['refinement']['blocked']}",
        f"- Удаленный шум второго прохода: {summary['refinement']['reverted_noise']}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _write_batch_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        f"# {REFINEMENT_BATCH}: классификация сохраненных кандидатов",
        "",
        "Пакет не восстанавливает пути из `vendor-baseline`. Он фиксирует консервативную классификацию всех first-pass `keep`-групп и отдельную refinement-ветку.",
        "",
        "## Результат",
        "",
        f"- Очередь: {summary['refinement']['queue_rows']}",
        f"- Решения: {summary['refinement']['processed_decisions']}",
        f"- Сохранено как кандидаты: {summary['refinement']['retained_candidates']}",
        f"- Ручной разбор: {summary['refinement']['manual_review']}",
        f"- Блокеры: {summary['refinement']['blocked']}",
        f"- Удалено шумом второго прохода: {summary['refinement']['reverted_noise']}",
        "",
        "## Инварианты",
        "",
        f"- `vendor-baseline`: `{summary['vendor_baseline']}`",
        f"- `target-cf`: `{summary['target_cf']}`",
        f"- `manual-cleanup` на старте: `{summary['starting_manual_cleanup']}`",
        f"- `v8unpack-refinement`: `{summary['refinement_head']}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _dashboard_data(summary: dict[str, Any], rows: list[dict[str, Any]], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "v8unpack-refinement-dashboard/v1",
        "generated_at": summary["generated_at"],
        "summary": summary,
        "filters": {
            "statuses": sorted(set(row["status"] for row in rows)),
            "evidence_types": sorted(set(row["evidence_type"] for row in rows)),
            "feature_hints": sorted(set(row["feature_hint"] for row in rows)),
            "object_kinds": sorted(set(row["object_kind"] for row in rows)),
            "risks": sorted(set(row["risk"] for row in rows)),
            "batches": sorted(set(row["batch_id"] for row in rows)),
        },
        "candidates": rows,
        "decisions": decisions,
    }


def _dashboard_html(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Второй проход v8unpack</title>
  <style>
    :root {{ color-scheme: light; --line: #d7dde5; --muted: #596575; --bg: #f7f8fa; --ink: #17202c; --accent: #0f766e; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; color: var(--ink); background: var(--bg); }}
    header {{ padding: 20px 28px 12px; background: #fff; border-bottom: 1px solid var(--line); }}
    h1 {{ margin: 0 0 6px; font-size: 26px; font-weight: 700; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 17px; }}
    main {{ display: grid; grid-template-columns: minmax(0, 1fr) 420px; gap: 16px; padding: 16px 28px 28px; }}
    .summary {{ display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 10px; margin-top: 14px; }}
    .metric, .panel {{ background: #fff; border: 1px solid var(--line); border-radius: 6px; }}
    .metric {{ padding: 10px 12px; }}
    .metric strong {{ display: block; font-size: 22px; }}
    .metric span, .muted {{ color: var(--muted); }}
    .toolbar {{ display: grid; grid-template-columns: repeat(6, minmax(130px, 1fr)); gap: 8px; padding: 12px; border-bottom: 1px solid var(--line); }}
    select, input {{ width: 100%; padding: 8px; border: 1px solid var(--line); border-radius: 4px; background: #fff; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }}
    th {{ text-align: left; font-size: 12px; color: var(--muted); background: #fbfcfd; position: sticky; top: 0; }}
    tr {{ cursor: pointer; }}
    tr:hover, tr.active {{ background: #edf7f5; }}
    .table-wrap {{ max-height: calc(100vh - 260px); overflow: auto; }}
    aside.panel {{ padding: 14px; align-self: start; position: sticky; top: 16px; max-height: calc(100vh - 32px); overflow: auto; }}
    code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }}
    pre {{ white-space: pre-wrap; background: #f0f3f6; padding: 10px; border-radius: 4px; }}
    .badge {{ display: inline-block; margin: 0 4px 4px 0; padding: 2px 7px; border-radius: 999px; background: #e8eef6; color: #203044; font-size: 12px; }}
    .badge.warn {{ background: #fff3cd; }}
    .badge.ok {{ background: #dff5ef; }}
    .paths {{ max-height: 180px; overflow: auto; border: 1px solid var(--line); border-radius: 4px; padding: 8px; }}
    @media (max-width: 1100px) {{ main {{ grid-template-columns: 1fr; }} aside.panel {{ position: static; }} .summary, .toolbar {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
  </style>
</head>
<body>
<header>
  <h1>Второй проход очистки v8unpack</h1>
  <div class="muted">Проверочная панель retained-кандидатов после первого прохода. Источник истины: CSV, JSONL и summary в analysis/v8unpack-refinement.</div>
  <section class="summary" id="summary"></section>
</header>
<main>
  <section class="panel">
    <div class="toolbar">
      <select id="status"></select>
      <select id="evidence"></select>
      <select id="batch"></select>
      <select id="feature"></select>
      <select id="kind"></select>
      <select id="risk"></select>
      <input id="search" placeholder="Поиск по объекту или пути">
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Группа</th><th>Статус</th><th>Доказательство</th><th>Область</th><th>Риск</th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
  </section>
  <aside class="panel" id="detail"></aside>
</main>
<script id="v8unpack-refinement-data" type="application/json">{payload}</script>
<script>
const snapshot = JSON.parse(document.getElementById("v8unpack-refinement-data").textContent);
const candidates = snapshot.candidates || [];
const byId = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[c]));
const labels = {{
  kept: "Сохранено",
  manual_review: "Ручной разбор",
  blocked: "Блокер",
  reverted: "Удаленный шум",
  preserve_customization: "Сохранить кандидат",
  needs_manual_review: "Нужен ручной разбор",
  blocked_by_missing_evidence: "Недостаточно доказательств",
  high: "Высокий",
  medium: "Средний",
  low: "Низкий"
}};
function label(value) {{ return labels[value] || value || "не указано"; }}
function fillSelect(id, title, values) {{
  byId(id).innerHTML = `<option value="">${{title}}</option>` + values.map(v => `<option value="${{esc(v)}}">${{esc(label(v))}}</option>`).join("");
}}
function parseList(value) {{ try {{ return JSON.parse(value || "[]"); }} catch {{ return []; }} }}
function renderSummary() {{
  const s = snapshot.summary.refinement || {{}};
  const d = snapshot.summary.diff || {{}};
  const cards = [
    ["Кандидатов", s.queue_rows || 0],
    ["Решений", s.processed_decisions || 0],
    ["Сохранено", s.retained_candidates || 0],
    ["Ручной разбор", s.manual_review || 0],
    ["Блокеры", s.blocked || 0],
    ["Путей в сравнении", d.refined_diff_paths || 0],
  ];
  byId("summary").innerHTML = cards.map(([name, value]) => `<div class="metric"><strong>${{value}}</strong><span>${{name}}</span></div>`).join("");
}}
function filtered() {{
  const status = byId("status").value, evidence = byId("evidence").value, batch = byId("batch").value;
  const feature = byId("feature").value, kind = byId("kind").value, risk = byId("risk").value;
  const q = byId("search").value.toLowerCase();
  return candidates.filter(row =>
    (!status || row.status === status) &&
    (!evidence || row.evidence_type === evidence) &&
    (!batch || row.batch_id === batch) &&
    (!feature || row.feature_hint === feature) &&
    (!kind || row.object_kind === kind) &&
    (!risk || row.risk === risk) &&
    (!q || `${{row.object_group}} ${{row.related_paths}} ${{row.feature_hint}}`.toLowerCase().includes(q))
  );
}}
function renderRows() {{
  const rows = filtered();
  byId("rows").innerHTML = rows.slice(0, 1000).map(row => `
    <tr data-id="${{esc(row.item_id)}}">
      <td><strong>${{esc(row.item_id)}}</strong><br>${{esc(row.object_group)}}</td>
      <td>${{esc(label(row.status))}}</td>
      <td>${{esc(row.evidence_type)}}</td>
      <td>${{esc(row.feature_hint)}}</td>
      <td>${{esc(label(row.risk))}}</td>
    </tr>`).join("");
  document.querySelectorAll("tr[data-id]").forEach(tr => tr.addEventListener("click", () => showDetail(tr.dataset.id)));
  if (rows[0]) showDetail(rows[0].item_id);
}}
function showDetail(id) {{
  const row = candidates.find(item => item.item_id === id);
  if (!row) return;
  document.querySelectorAll("tr[data-id]").forEach(tr => tr.classList.toggle("active", tr.dataset.id === id));
  const paths = parseList(row.related_paths);
  const current = parseList(row.current_diff_paths);
  byId("detail").innerHTML = `
    <h2>${{esc(row.item_id)}}: ${{esc(row.object_group)}}</h2>
    <p><span class="badge ok">${{esc(label(row.status))}}</span><span class="badge warn">${{esc(label(row.risk))}}</span><span class="badge">${{esc(row.evidence_type)}}</span></p>
    <p class="muted">${{esc(row.rationale)}}</p>
    <h2>Трассировка</h2>
    <p>Первый проход: <code>${{esc(row.source_first_pass_group_id)}}</code>, пакет <code>${{esc(row.first_pass_batch)}}</code></p>
    <p>Решение второго прохода: <code>${{esc(row.decision)}}</code></p>
    <p>Критерий: <code>${{esc(row.noise_class)}}</code></p>
    <h2>Пути</h2>
    <div class="paths">${{paths.map(p => `<div><code>${{esc(p)}}</code></div>`).join("")}}</div>
    <h2>Текущий diff</h2>
    <div class="paths">${{current.map(p => `<div><code>${{esc(p)}}</code></div>`).join("") || "<span class='muted'>Нет пересечения с текущим сравнением</span>"}}</div>
    <h2>Команды Git</h2>
    <pre>git -C {NESTED_REPO} diff {VENDOR_TAG}..{REFINEMENT_BRANCH} -- ${{esc(row.object_group)}}
git -C {NESTED_REPO} show ${{esc(row.commit_ref)}}</pre>
    <h2>Следующее действие</h2>
    <p>${{esc(row.review_action)}}</p>`;
}}
function init() {{
  const f = snapshot.filters || {{}};
  fillSelect("status", "Все статусы", f.statuses || []);
  fillSelect("evidence", "Все типы", f.evidence_types || []);
  fillSelect("batch", "Все пакеты", f.batches || []);
  fillSelect("feature", "Все области", f.feature_hints || []);
  fillSelect("kind", "Все виды объектов", f.object_kinds || []);
  fillSelect("risk", "Все риски", f.risks || []);
  ["status","evidence","batch","feature","kind","risk","search"].forEach(id => byId(id).addEventListener("input", renderRows));
  renderSummary();
  renderRows();
}}
init();
</script>
</body>
</html>
"""


def build_manual_markup_cleanup_report(root: Path) -> dict[str, Any]:
    nested_repo = repo_path(root, NESTED_REPO)
    markup_path = repo_path(root, MANUAL_MARKUP)
    reports_dir = repo_path(root, f"{REFINEMENT_DIR}/reports")
    if not markup_path.exists():
        raise FileNotFoundError(f"Manual markup file not found: {MANUAL_MARKUP}")

    diff_by_group: dict[str, list[tuple[str, str]]] = {}
    for status, path in _name_status(nested_repo, REFINEMENT_BRANCH):
        parts = Path(path).parts
        if len(parts) >= 2:
            diff_by_group.setdefault(f"{parts[0]}/{parts[1]}", []).append((status, path))

    rows: list[dict[str, Any]] = []
    for source in _read_csv(markup_path):
        key_object = source.get("key_objects", "")
        object_group = _object_group_from_key(key_object)
        diff_rows = diff_by_group.get(object_group, []) if object_group else []
        recommendation, reason = _cleanup_recommendation(source, diff_rows)
        if recommendation == "already_clean" and source.get("review_status") not in {"technical_supporting", "needs_evidence"}:
            continue
        suffixes = sorted({_suffix(path) for _, path in diff_rows})
        statuses = sorted({status for status, _ in diff_rows})
        rows.append(
            {
                "row_id": source.get("row_id", ""),
                "key_objects": key_object,
                "object_group": object_group,
                "review_status": source.get("review_status", ""),
                "confidence": source.get("confidence", ""),
                "expected_card": source.get("expected_card", ""),
                "recommendation": recommendation,
                "reason": reason,
                "current_diff_path_count": len(diff_rows),
                "diff_statuses": ";".join(statuses),
                "diff_suffixes": ";".join(suffixes),
                "current_diff_paths": json.dumps([path for _, path in diff_rows], ensure_ascii=False, separators=(",", ":")),
                "evidence_refs": source.get("evidence_refs", ""),
                "manual_reason": source.get("reason", ""),
            }
        )

    order = {
        "auto_candidate": 0,
        "manual_review": 1,
        "do_not_auto_clean": 2,
        "keep": 3,
        "already_clean": 4,
    }
    rows.sort(key=lambda row: (order.get(str(row["recommendation"]), 99), -int(row["current_diff_path_count"]), str(row["key_objects"])))

    reports_dir.mkdir(parents=True, exist_ok=True)
    csv_path = reports_dir / "manual-markup-cleanup-candidates.csv"
    fieldnames = [
        "row_id",
        "key_objects",
        "object_group",
        "review_status",
        "confidence",
        "expected_card",
        "recommendation",
        "reason",
        "current_diff_path_count",
        "diff_statuses",
        "diff_suffixes",
        "current_diff_paths",
        "evidence_refs",
        "manual_reason",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})

    counts = Counter(str(row["recommendation"]) for row in rows)
    summary = {
        "schema_version": "manual-markup-cleanup-report/v1",
        "generated_at": utc_now_iso(),
        "manual_markup": MANUAL_MARKUP,
        "nested_repo": NESTED_REPO,
        "diff_ref": f"{VENDOR_TAG}..{REFINEMENT_BRANCH}",
        "rows": len(rows),
        "counts_by_recommendation": dict(sorted(counts.items())),
        "csv": f"{REFINEMENT_DIR}/reports/{csv_path.name}",
        "markdown": f"{REFINEMENT_DIR}/reports/manual-markup-cleanup-candidates.md",
    }
    json_path = reports_dir / "manual-markup-cleanup-candidates.json"
    _write_json(json_path, {"summary": summary, "rows": rows})

    lines = [
        "# Кандидаты очистки по ручной разметке",
        "",
        "Отчет не меняет Git. Он сопоставляет `markup.csv` с текущим очищенным diff и показывает, что можно рассматривать для следующих узких batch-проходов.",
        "",
        "## Сводка",
        "",
        f"- Строк отчета: {len(rows)}",
        f"- `auto_candidate`: {counts.get('auto_candidate', 0)}",
        f"- `manual_review`: {counts.get('manual_review', 0)}",
        f"- `do_not_auto_clean`: {counts.get('do_not_auto_clean', 0)}",
        f"- `keep`: {counts.get('keep', 0)}",
        f"- `already_clean`: {counts.get('already_clean', 0)}",
        "",
        "## Правило auto_candidate",
        "",
        "`review_status=technical_supporting`, текущий diff по объекту содержит только измененные `.html`/`.json` файлы, без BSL, MXL, бинарей, WSDL, TXT и XSD.",
        "",
        "## Первые auto_candidate",
        "",
        "| row_id | object | paths | expected_card |",
        "| --- | --- | ---: | --- |",
    ]
    auto_rows = [row for row in rows if row["recommendation"] == "auto_candidate"]
    for row in auto_rows[:50]:
        lines.append(
            f"| `{row['row_id']}` | `{row['key_objects']}` | {row['current_diff_path_count']} | `{row['expected_card']}` |"
        )
    if not auto_rows:
        lines.append("| нет |  |  |  |")
    lines.extend(
        [
            "",
            "## Команда просмотра",
            "",
            "```bash",
            f"git -C {NESTED_REPO} diff {VENDOR_TAG}..{REFINEMENT_BRANCH} -- <object_group>",
            "```",
        ]
    )
    md_path = reports_dir / "manual-markup-cleanup-candidates.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return summary


def validate_refinement_artifacts(root: Path) -> list[str]:
    errors: list[str] = []
    required = [
        f"{REFINEMENT_DIR}/README.md",
        f"{REFINEMENT_DIR}/refinement-queue.csv",
        f"{REFINEMENT_DIR}/decisions.jsonl",
        f"{REFINEMENT_DIR}/summary.json",
        f"{REFINEMENT_DIR}/batches/{REFINEMENT_BATCH}.md",
        f"{REFINEMENT_DIR}/reports/verification.json",
        f"{DASHBOARD_DIR}/data.json",
        f"{DASHBOARD_DIR}/index.html",
    ]
    for relative in required:
        if not repo_path(root, relative).exists():
            errors.append(f"Missing v8unpack refinement artifact: {relative}")
    if errors:
        return errors
    rows = _read_csv(repo_path(root, f"{REFINEMENT_DIR}/refinement-queue.csv"))
    decisions = [row for _, row in read_jsonl(repo_path(root, f"{REFINEMENT_DIR}/decisions.jsonl"))]
    summary = json.loads(repo_path(root, f"{REFINEMENT_DIR}/summary.json").read_text(encoding="utf-8"))
    data = json.loads(repo_path(root, f"{DASHBOARD_DIR}/data.json").read_text(encoding="utf-8"))
    html_text = repo_path(root, f"{DASHBOARD_DIR}/index.html").read_text(encoding="utf-8")
    if not rows:
        errors.append("v8unpack refinement queue is empty")
    if len(rows) != len(decisions):
        errors.append("v8unpack refinement decisions count differs from queue rows")
    if len({row["item_id"] for row in rows}) != len(rows):
        errors.append("v8unpack refinement queue has duplicate item_id values")
    if len({str(row.get("item_id")) for row in decisions}) != len(decisions):
        errors.append("v8unpack refinement ledger has duplicate item_id values")
    allowed_statuses = {"kept", "reverted", "split", "manual_review", "blocked"}
    bad_statuses = sorted({row.get("status", "") for row in rows if row.get("status", "") not in allowed_statuses})
    if bad_statuses:
        errors.append(f"v8unpack refinement queue has invalid statuses: {', '.join(bad_statuses)}")
    required_columns = {"source_first_pass_group_id", "object_group", "related_paths", "path_count", "evidence_type", "feature_hint", "risk", "status"}
    missing_columns = sorted(required_columns - set(rows[0].keys()))
    if missing_columns:
        errors.append(f"v8unpack refinement queue misses required columns: {', '.join(missing_columns)}")
    if int(summary.get("refinement", {}).get("queue_rows") or -1) != len(rows):
        errors.append("v8unpack refinement summary queue_rows does not match queue")
    if data.get("schema_version") != "v8unpack-refinement-dashboard/v1":
        errors.append("v8unpack refinement dashboard data has invalid schema_version")
    if "v8unpack-refinement-data" not in html_text:
        errors.append("v8unpack refinement dashboard HTML does not embed data")
    embedded_match = re.search(
        r'<script id="v8unpack-refinement-data" type="application/json">(.*?)</script>',
        html_text,
        flags=re.DOTALL,
    )
    if not embedded_match:
        errors.append("v8unpack refinement dashboard HTML has no parseable embedded data script")
    else:
        try:
            embedded = json.loads(embedded_match.group(1))
            if embedded.get("schema_version") != "v8unpack-refinement-dashboard/v1":
                errors.append("v8unpack refinement embedded dashboard data has invalid schema_version")
        except Exception as exc:
            errors.append(f"v8unpack refinement embedded dashboard data does not parse: {exc}")
    for token in ("Все статусы", "Ручной разбор", "Команды Git", "source_first_pass_group_id"):
        if token not in html_text and token not in repo_path(root, f"{DASHBOARD_DIR}/data.json").read_text(encoding="utf-8"):
            errors.append(f"v8unpack refinement dashboard misses token: {token}")
    return errors


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = build_refinement(root, force=bool(args.force))
    print(f"v8unpack_refinement_queue_rows: {result['queue_rows']}")
    print(f"v8unpack_refinement_decisions: {result['processed_decisions']}")
    print(f"v8unpack_refinement_summary: {result['summary']}")
    print(f"v8unpack_refinement_dashboard: {result['dashboard']}")
    print(f"v8unpack_refinement_head: {result['refinement_head']}")
    return 0


def smoke_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    errors = validate_refinement_artifacts(root)
    if errors:
        raise RuntimeError("\n".join(f"- {error}" for error in errors))
    print("v8unpack refinement smoke passed")
    return 0


def manual_cleanup_report_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    summary = build_manual_markup_cleanup_report(root)
    print(f"manual_cleanup_report_rows: {summary['rows']}")
    print(f"manual_cleanup_report_counts: {summary['counts_by_recommendation']}")
    print(f"manual_cleanup_report_csv: {summary['csv']}")
    print(f"manual_cleanup_report_markdown: {summary['markdown']}")
    return 0
