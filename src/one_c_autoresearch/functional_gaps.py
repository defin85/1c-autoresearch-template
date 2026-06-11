from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .common import read_toml, repo_path, utc_now_iso
from .functional_gap_probes import derive_behavior_probe_requests, evaluate_behavior_probe_requests, load_target_profile
from .subject_cards import load_subject_card, read_csv_rows, split_refs, write_csv_rows


FUNCTIONAL_GAP_SCHEMA_VERSION = "functional-gap-card/v2"
FUNCTIONAL_GAP_HYPOTHESES_HEADER = "hypothesis_id,gap_type,status,confidence,summary,evidence_ref,next_check,decision"
FUNCTIONAL_GAP_CHECKS_HEADER = "check_id,check_type,status,source,question,result,blocking"
FUNCTIONAL_GAP_TARGET_FINDINGS_HEADER = "finding_id,finding_type,target_object,target_path,match_basis,confidence,evidence_ref,notes"
FUNCTIONAL_GAP_OBJECT_MAPPING_HEADER = "mapping_id,source_object,source_path,target_object,target_path,mapping_type,confidence,decision,notes"
FUNCTIONAL_GAP_BEHAVIOR_PROBES_HEADER = "probe_id,capability_id,source_signal,source_ref,target_profile,profile_status,result,confidence,evidence_ref,finding_id,notes"
FUNCTIONAL_GAP_BEHAVIOR_PROBE_RESULTS = {
    "standard_supported",
    "adaptation_candidate",
    "not_supported",
    "needs_profile",
    "needs_runtime_check",
}
FUNCTIONAL_GAP_BEHAVIOR_PROBE_CONFIDENCE = {"high", "medium", "low"}
BEHAVIOR_RESULT_FINDING_TYPES = {
    "standard_supported": "standard_mechanism",
    "adaptation_candidate": "removed_or_changed_mechanism",
    "not_supported": "removed_or_changed_mechanism",
    "needs_runtime_check": "needs_runtime_check",
}
FUNCTIONAL_GAP_INDEX_HEADER = "subject_card_slug,title,status,gap_readiness,hypotheses_count,open_checks_count,gap_card_path,selected_decision,review_notes"
FUNCTIONAL_GAP_COVERAGE_HEADER = "subject_card_slug,gap_card_status,gap_readiness,gap_card_path,selected_decision,open_checks_count,notes"
FUNCTIONAL_GAP_OPEN_QUESTIONS_HEADER = "question_id,subject_card_slug,check_id,question,needed_source,blocking,status,notes"
FUNCTIONAL_GAP_TYPES = {
    "replace_by_standard",
    "adapt",
    "preserve",
    "retire",
    "split",
    "data_migration",
    "business_decision",
}
FUNCTIONAL_GAP_HYPOTHESIS_STATUSES = {"candidate", "supported", "rejected", "selected"}
FUNCTIONAL_GAP_CHECK_TYPES = {
    "subject_card_readiness",
    "target_release_static",
    "target_behavior_static",
    "target_release_rlm",
    "infobase_data",
    "ui_check",
    "analyst_decision",
}
FUNCTIONAL_GAP_CHECK_STATUSES = {"open", "done", "blocked", "not_applicable"}
FUNCTIONAL_GAP_STATUSES = {
    "draft",
    "needs_target_analysis",
    "needs_runtime_check",
    "needs_analyst_decision",
    "ready_for_review",
    "reviewed",
    "blocked",
    "out_of_scope",
}
FUNCTIONAL_GAP_READINESS = {
    "needs_subject_card_readiness",
    "needs_target_release_source",
    "needs_target_release_check",
    "ready_for_gap_review",
    "reviewed",
}
GAP_TYPE_LABELS = {
    "replace_by_standard": "заменить типовым механизмом",
    "adapt": "адаптировать доработку",
    "preserve": "сохранить без изменений",
    "retire": "вывести из эксплуатации",
    "split": "разделить решение по частям",
    "data_migration": "проверить перенос данных",
    "business_decision": "решение аналитика",
    "undecided": "итоговое решение не выбрано",
}
CHECK_TYPE_LABELS = {
    "subject_card_readiness": "готовность предметной карточки",
    "target_release_static": "статическая проверка целевого релиза",
    "target_behavior_static": "статическая проверка поведения целевого релиза",
    "target_release_rlm": "поиск в исходниках целевого релиза",
    "infobase_data": "проверка данных ИБ",
    "ui_check": "проверка интерфейса 1С",
    "analyst_decision": "решение аналитика",
}
CHECK_STATUS_LABELS = {
    "open": "открыта",
    "done": "закрыта",
    "blocked": "заблокирована",
    "not_applicable": "не требуется",
}
TARGET_INSPECTION_CHECK_IDS = {"FGC-0001", "FGC-0007"}
MANUAL_REVIEW_HEADING = "## Ручные заметки аналитика"
DEFAULT_ANALYST_DECISION_TEXT = "Пока не зафиксировано. После проверок целевого релиза нужно выбрать: заменить типовым механизмом, адаптировать, сохранить или вывести из эксплуатации."
READINESS_LABELS = {
    "needs_subject_card_readiness": "нужно довести предметную карточку",
    "needs_target_release_source": "нужен источник целевого релиза",
    "needs_target_release_check": "нужна проверка целевого релиза",
    "ready_for_gap_review": "готово к ревью разрыва",
    "reviewed": "проверено аналитиком",
}


def functional_gap_root(root: Path) -> Path:
    return repo_path(root, "analysis/functional-gaps")


def functional_gap_card_dir(root: Path, slug: str) -> Path:
    return functional_gap_root(root) / "cards" / slug


def first_csv_line(path: Path) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return lines[0] if lines else ""


def non_empty_csv_rows(path: Path) -> list[dict[str, str]]:
    return [row for row in read_csv_rows(path) if any((value or "").strip() for value in row.values())]


def load_manifest(root: Path) -> dict[str, Any]:
    path = repo_path(root, "project.toml")
    if not path.exists():
        return {}
    return read_toml(path)


def manifest_value(manifest: dict[str, Any], section: str, key: str) -> str:
    value = manifest.get(section, {}).get(key, "")
    return "" if value is None else str(value).strip()


def target_release_label(manifest: dict[str, Any]) -> str:
    value = manifest_value(manifest, "project", "next_vendor_version")
    if value:
        return value
    return "целевой релиз"


def default_target_profile_id(manifest: dict[str, Any]) -> str:
    configured = manifest_value(manifest, "functional_gap", "target_profile")
    if configured:
        return configured
    return ""


def target_sources(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        "next_vendor_path": manifest_value(manifest, "paths", "next_vendor"),
        "next_vendor_rlm": manifest_value(manifest, "rlm", "next_vendor"),
    }


def subject_card_relative(slug: str) -> str:
    return f"analysis/subject-cards/cards/{slug}/subject-card.json"


def file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_fingerprint(path: Path) -> str:
    if not path.exists():
        return ""
    if path.is_file():
        return file_sha256(path)
    digest = hashlib.sha256()
    file_count = 0
    total_size = 0
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        try:
            stat = child.stat()
        except OSError:
            continue
        file_count += 1
        total_size += stat.st_size
        digest.update(child.relative_to(path).as_posix().encode("utf-8", errors="replace"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(file_sha256(child).encode("ascii"))
    digest.update(f"{file_count}:{total_size}".encode("ascii"))
    return digest.hexdigest()


def target_source_hash(root: Path, sources: dict[str, str]) -> str:
    relative = sources.get("next_vendor_path", "")
    if not relative:
        return ""
    return tree_fingerprint(repo_path(root, relative))


def status_from_readiness(readiness: str) -> str:
    if readiness in {"needs_subject_card_readiness", "needs_target_release_source"}:
        return "blocked"
    if readiness == "ready_for_gap_review":
        return "ready_for_review"
    if readiness == "reviewed":
        return "reviewed"
    return "needs_target_analysis"


def source_ref(root: Path, slug: str, field: str) -> str:
    return f"{subject_card_relative(slug)}#{field}"


def has_open_blocking_subject_gaps(gaps: list[dict[str, str]]) -> bool:
    return any(
        row.get("blocking", "").strip().lower() == "true"
        and row.get("status", "").strip().lower() not in {"closed", "done", "resolved"}
        for row in gaps
    )


def subject_ready_for_gap_pass(payload: dict[str, Any], gaps: list[dict[str, str]]) -> bool:
    status = str(payload.get("status") or "").strip()
    return status in {"ready_for_review", "reviewed"} and not has_open_blocking_subject_gaps(gaps)


def gap_readiness(payload: dict[str, Any], gaps: list[dict[str, str]], sources: dict[str, str]) -> str:
    if not subject_ready_for_gap_pass(payload, gaps):
        return "needs_subject_card_readiness"
    if not sources.get("next_vendor_path") and not sources.get("next_vendor_rlm"):
        return "needs_target_release_source"
    return "needs_target_release_check"


def text_blob(payload: dict[str, Any]) -> str:
    parts = [
        payload.get("title", ""),
        payload.get("subject_type", ""),
        payload.get("summary", ""),
        payload.get("key_conclusion", ""),
        payload.get("upgrade_risk", ""),
        payload.get("runtime_data_needed", ""),
    ]
    return "\n".join(str(part or "") for part in parts).lower()


def build_hypotheses(
    root: Path,
    slug: str,
    payload: dict[str, Any],
    target_label: str,
) -> list[dict[str, str]]:
    title = str(payload.get("title") or slug).strip()
    confidence = str(payload.get("confidence") or "medium").strip() or "medium"
    hypotheses: list[dict[str, str]] = [
        {
            "hypothesis_id": "FGH-0001",
            "gap_type": "replace_by_standard",
            "status": "candidate",
            "confidence": "medium",
            "summary": f"Проверить, закрывает ли типовой {target_label} предметную доработку «{title}» без переноса клиентского кода.",
            "evidence_ref": source_ref(root, slug, "key_conclusion"),
            "next_check": "FGC-0001",
            "decision": "",
        },
        {
            "hypothesis_id": "FGH-0002",
            "gap_type": "adapt",
            "status": "candidate",
            "confidence": confidence,
            "summary": "Если типовой механизм целевого релиза не закрывает фактическое поведение, доработку нужно переносить или адаптировать.",
            "evidence_ref": source_ref(root, slug, "upgrade_risk"),
            "next_check": "FGC-0001",
            "decision": "",
        },
        {
            "hypothesis_id": "FGH-0003",
            "gap_type": "preserve",
            "status": "candidate",
            "confidence": "low",
            "summary": "Сохранение без изменения допустимо только после доказательства, что объектная модель и сценарии целевого релиза совместимы.",
            "evidence_ref": source_ref(root, slug, "primary_objects"),
            "next_check": "FGC-0001",
            "decision": "",
        },
        {
            "hypothesis_id": "FGH-0004",
            "gap_type": "business_decision",
            "status": "candidate",
            "confidence": "medium",
            "summary": "Аналитик должен выбрать целевое решение: заменить типовым механизмом, адаптировать, сохранить или вывести из эксплуатации.",
            "evidence_ref": source_ref(root, slug, "summary"),
            "next_check": "FGC-0004",
            "decision": "",
        },
    ]
    if str(payload.get("runtime_data_needed") or "").strip():
        hypotheses.append(
            {
                "hypothesis_id": "FGH-0005",
                "gap_type": "data_migration",
                "status": "candidate",
                "confidence": "medium",
                "summary": "Нужно проверить фактические данные ИБ: есть ли исторические объекты или настройки, которые влияют на перенос.",
                "evidence_ref": source_ref(root, slug, "runtime_data_needed"),
                "next_check": "FGC-0003",
                "decision": "",
            }
        )
    blob = text_blob(payload)
    if str(payload.get("subject_type") or "").strip() == "technical_support" or any(term in blob for term in ("однораз", "устар", "временно")):
        hypotheses.append(
            {
                "hypothesis_id": "FGH-0006",
                "gap_type": "retire",
                "status": "candidate",
                "confidence": "low",
                "summary": "Возможен вывод доработки из эксплуатации, если она была временной или служебной и не нужна в целевом релизе.",
                "evidence_ref": source_ref(root, slug, "summary"),
                "next_check": "FGC-0004",
                "decision": "",
            }
        )
    return hypotheses


def build_required_checks(
    slug: str,
    payload: dict[str, Any],
    gaps: list[dict[str, str]],
    sources: dict[str, str],
    readiness: str,
    target_label: str,
) -> list[dict[str, str]]:
    title = str(payload.get("title") or slug).strip()
    primary_objects = ";".join(split_refs(payload.get("primary_objects", [])))
    checks: list[dict[str, str]] = []
    if readiness == "needs_subject_card_readiness":
        checks.append(
            {
                "check_id": "FGC-0000",
                "check_type": "subject_card_readiness",
                "status": "open",
                "source": f"analysis/subject-cards/cards/{slug}/gaps.csv",
                "question": "Довести предметную карточку до ready_for_review и закрыть блокирующие gaps.",
                "result": "",
                "blocking": "true",
            }
        )
    checks.append(
        {
            "check_id": "FGC-0001",
            "check_type": "target_release_static",
            "status": "open" if sources.get("next_vendor_path") else "blocked",
            "source": sources.get("next_vendor_path") or "project.toml:[paths].next_vendor",
            "question": f"Сравнить объекты «{title}» с типовой конфигурацией {target_label}: {primary_objects or 'объекты указаны в предметной карточке'}.",
            "result": "",
            "blocking": "true",
        }
    )
    checks.append(
        {
            "check_id": "FGC-0002",
            "check_type": "target_release_rlm",
            "status": "open" if sources.get("next_vendor_rlm") else "blocked",
            "source": sources.get("next_vendor_rlm") or "project.toml:[rlm].next_vendor",
            "question": "Проверить через rlm-tools-bsl, есть ли в целевом релизе типовой сценарий или близкий механизм.",
            "result": "",
            "blocking": "true",
        }
    )
    if str(payload.get("runtime_data_needed") or "").strip():
        checks.append(
            {
                "check_id": "FGC-0003",
                "check_type": "infobase_data",
                "status": "open",
                "source": "тестовая ИБ",
                "question": str(payload.get("runtime_data_needed") or "").strip(),
                "result": "",
                "blocking": "false",
            }
        )
    checks.append(
        {
            "check_id": "FGC-0004",
            "check_type": "analyst_decision",
            "status": "open",
            "source": f"analysis/functional-gaps/cards/{slug}/review.md",
            "question": "Зафиксировать итоговое решение по функциональному разрыву после проверок целевого релиза.",
            "result": "",
            "blocking": "true",
        }
    )
    return checks


def ensure_scaffold(root: Path) -> None:
    base = functional_gap_root(root)
    (base / "_templates").mkdir(parents=True, exist_ok=True)
    (base / "cards").mkdir(parents=True, exist_ok=True)
    (base / "profiles").mkdir(parents=True, exist_ok=True)
    readme = base / "README.md"
    readme_text = (
        "# Карта функциональных разрывов\n\n"
        "Этот слой строится поверх `analysis/subject-cards` и обрабатывает одну предметную карточку за проход.\n\n"
        "Базовый цикл:\n\n"
        "```bash\n"
        "python -m one_c_autoresearch functional-gap build --card <slug>\n"
        "python -m one_c_autoresearch functional-gap refresh --card <slug>\n"
        "python -m one_c_autoresearch functional-gap inspect-target --card <slug>\n"
        "python -m one_c_autoresearch functional-gap validate --card <slug>\n"
        "python -m one_c_autoresearch functional-gap map-build\n"
        "```\n\n"
        "`build` создает черновик gap-карточки, `refresh` обновляет generated-поля без перезаписи ручных решений, "
        "`inspect-target` ищет аналоги в целевом релизе, а `validate` проверяет доказательность и открытые вопросы. "
        "Для `build`, `refresh` и `inspect-target` флаг `--force` разрешает принудительную регенерацию ручных или "
        "заблокированных аналитиком полей; без него зафиксированные решения и curated target-analysis не перетираются.\n\n"
        "Назначение слоя: для каждой предметной доработки отдельно зафиксировать гипотезы перехода на целевой релиз, обязательные проверки и решение аналитика.\n"
    )
    if not readme.exists() or "inspect-target" not in readme.read_text(encoding="utf-8"):
        readme.write_text(
            readme_text,
            encoding="utf-8",
            newline="\n",
        )
    template = base / "_templates/gap-card.json"
    template_payload = {
        "schema_version": FUNCTIONAL_GAP_SCHEMA_VERSION,
        "subject_card_slug": "example-subject",
        "title": "Пример функционального разрыва",
        "status": "draft",
        "gap_readiness": "needs_target_release_check",
        "target_release": "целевой релиз",
        "source_subject_card": "analysis/subject-cards/cards/example-subject/subject-card.json",
        "source_subject_card_hash": "",
        "target_source_hash": "",
        "selected_decision": "",
        "selected_decision_summary": "",
        "manual_decision_locked": False,
        "generated_at": "",
        "updated_at": "",
        "inputs": {
            "subject_card_path": "analysis/subject-cards/cards/example-subject/subject-card.json",
            "next_vendor_path": "sources/next_vendor",
            "next_vendor_rlm": "",
        },
        "counts": {
            "hypotheses": 0,
            "checks_open": 0,
            "target_findings": 0,
            "object_mappings": 0,
            "behavior_probes": 0,
        },
        "source_artifacts": [],
        "subject_summary": "",
        "subject_key_conclusion": "",
        "subject_upgrade_risk": "",
        "primary_objects": [],
        "linked_features": [],
        "linked_detail_maps": [],
        "target_sources": {"next_vendor_path": "sources/next_vendor", "next_vendor_rlm": ""},
        "hypotheses": [],
        "required_checks": [],
    }
    if not template.exists() or json.loads(template.read_text(encoding="utf-8")).get("schema_version") != FUNCTIONAL_GAP_SCHEMA_VERSION:
        template.write_text(
            json.dumps(template_payload, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    for relative, header in (
        ("index.csv", FUNCTIONAL_GAP_INDEX_HEADER),
        ("coverage.csv", FUNCTIONAL_GAP_COVERAGE_HEADER),
        ("open-questions.csv", FUNCTIONAL_GAP_OPEN_QUESTIONS_HEADER),
    ):
        path = base / relative
        if not path.exists() or first_csv_line(path) != header:
            write_csv_rows(path, header, [])
    profile_template = base / "_templates/target-profile.toml"
    if not profile_template.exists():
        profile_template.write_text(
            'profile_id = "target"\n'
            'profile_title = "Целевая конфигурация"\n'
            'profile_status = "experimental"\n\n'
            '[target_identity]\n'
            'configuration = ""\n'
            'major_version = ""\n\n'
            '[[capabilities.document_lifecycle_state.standard_evidence]]\n'
            'kind = "source_call"\n'
            'target = "ИмяПроцедурыЗаписиСостояния"\n'
            'context = "типовой механизм записи состояния объекта"\n\n'
            '[capabilities.document_lifecycle_state.confidence_rules]\n'
            'standard_supported = "medium"\n',
            encoding="utf-8",
            newline="\n",
        )


def build_gap_payload(
    root: Path,
    slug: str,
    payload: dict[str, Any],
    gaps: list[dict[str, str]],
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    sources = target_sources(manifest)
    target_label = target_release_label(manifest)
    readiness = gap_readiness(payload, gaps, sources)
    hypotheses = build_hypotheses(root, slug, payload, target_label)
    checks = build_required_checks(slug, payload, gaps, sources, readiness, target_label)
    subject_path = repo_path(root, subject_card_relative(slug))
    now = utc_now_iso()
    card_payload = {
        "schema_version": FUNCTIONAL_GAP_SCHEMA_VERSION,
        "subject_card_slug": slug,
        "title": str(payload.get("title") or slug),
        "status": status_from_readiness(readiness),
        "gap_readiness": readiness,
        "target_release": target_label,
        "source_subject_card": subject_card_relative(slug),
        "source_subject_card_hash": file_sha256(subject_path),
        "target_source_hash": target_source_hash(root, sources),
        "selected_decision": "",
        "selected_decision_summary": "",
        "manual_decision_locked": False,
        "generated_at": now,
        "updated_at": now,
        "inputs": {
            "subject_card_path": subject_card_relative(slug),
            "next_vendor_path": sources.get("next_vendor_path", ""),
            "next_vendor_rlm": sources.get("next_vendor_rlm", ""),
        },
        "source_artifacts": split_refs(payload.get("source_artifacts", [])),
        "subject_summary": str(payload.get("summary") or ""),
        "subject_key_conclusion": str(payload.get("key_conclusion") or ""),
        "subject_upgrade_risk": str(payload.get("upgrade_risk") or ""),
        "subject_status": str(payload.get("status") or ""),
        "subject_confidence": str(payload.get("confidence") or ""),
        "subject_type": str(payload.get("subject_type") or ""),
        "primary_objects": split_refs(payload.get("primary_objects", [])),
        "linked_features": split_refs(payload.get("linked_features", [])),
        "linked_detail_maps": split_refs(payload.get("linked_detail_maps", [])),
        "target_sources": sources,
        "hypotheses": hypotheses,
        "required_checks": checks,
        "analyst_decision": "",
        "counts": {
            "hypotheses": len(hypotheses),
            "checks_open": sum(1 for row in checks if row.get("status") in {"open", "blocked"}),
            "target_findings": 0,
            "object_mappings": 0,
            "behavior_probes": 0,
        },
    }
    return card_payload, hypotheses, checks


def extract_manual_review_notes(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if MANUAL_REVIEW_HEADING in text:
        return text.split(MANUAL_REVIEW_HEADING, 1)[1].strip()
    generated_markers = (
        "## Вывод предметной карточки",
        "## Гипотезы перехода",
        "## Проверки",
        "## Поведенческие проверки целевого релиза",
        "## Решение аналитика",
    )
    if not any(marker in text for marker in generated_markers):
        return text.strip()
    if "## Решение аналитика" in text:
        decision_text = text.split("## Решение аналитика", 1)[1]
        if "\n## " in decision_text:
            decision_text = decision_text.split("\n## ", 1)[0]
        decision_text = decision_text.strip()
        if decision_text and decision_text != DEFAULT_ANALYST_DECISION_TEXT:
            return decision_text
    return ""


def write_review(path: Path, payload: dict[str, Any], hypotheses: list[dict[str, str]], checks: list[dict[str, str]], manual_notes: str = "") -> None:
    open_checks = [row for row in checks if row.get("status") == "open"]
    blocked_checks = [row for row in checks if row.get("status") == "blocked"]
    readiness = str(payload.get("gap_readiness") or "")
    decision_summary = str(payload.get("selected_decision_summary") or "").strip()
    lines = [
        f"# {payload.get('title')}",
        "",
        f"- Предметная карточка: `{payload.get('source_subject_card')}`",
        f"- Целевой релиз: {payload.get('target_release')}",
        f"- Готовность: {READINESS_LABELS.get(readiness, readiness)}",
        f"- Гипотезы: {len(hypotheses)}",
        f"- Открытые проверки: {len(open_checks)}",
        f"- Заблокированные проверки: {len(blocked_checks)}",
        "",
        "## Вывод предметной карточки",
        "",
        str(payload.get("subject_key_conclusion") or payload.get("subject_summary") or "Не заполнено."),
        "",
        "## Гипотезы перехода",
        "",
    ]
    for row in hypotheses:
        gap_type = row.get("gap_type", "")
        lines.append(f"- `{row['hypothesis_id']}` {GAP_TYPE_LABELS.get(gap_type, gap_type)}: {row['summary']}")
    lines.extend(["", "## Проверки", ""])
    for row in checks:
        check_type = row.get("check_type", "")
        status = row.get("status", "")
        lines.append(f"- `{row['check_id']}` {CHECK_TYPE_LABELS.get(check_type, check_type)} / {CHECK_STATUS_LABELS.get(status, status)}: {row['question']}")
    behavior_probes = non_empty_csv_rows(path.parent / "behavior-probes.csv")
    lines.extend(["", "## Поведенческие проверки целевого релиза", ""])
    if behavior_probes:
        for row in behavior_probes:
            lines.append(
                "- "
                f"`{row.get('probe_id')}` `{row.get('capability_id')}`: {row.get('result')} / "
                f"уверенность: {row.get('confidence') or 'не указана'}; "
                f"профиль: {row.get('target_profile') or 'не указан'}; "
                f"evidence: {row.get('evidence_ref') or 'не найдено'}; "
                f"{row.get('notes') or ''}"
            )
    else:
        lines.append("Поведенческие проверки еще не выполнялись.")
    lines.extend(
        [
            "",
            "## Решение аналитика",
            "",
            decision_summary or DEFAULT_ANALYST_DECISION_TEXT,
            "",
        ]
    )
    if manual_notes.strip():
        lines.extend([MANUAL_REVIEW_HEADING, "", manual_notes.strip(), ""])
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def merge_hypotheses(generated: list[dict[str, str]], existing: list[dict[str, str]]) -> list[dict[str, str]]:
    existing_by_id = {row.get("hypothesis_id", ""): row for row in existing if row.get("hypothesis_id")}
    merged: list[dict[str, str]] = []
    for row in generated:
        current = dict(row)
        previous = existing_by_id.get(row.get("hypothesis_id", ""))
        if previous and (previous.get("status") in {"supported", "rejected", "selected"} or previous.get("decision")):
            for field in ("status", "confidence", "summary", "evidence_ref", "next_check", "decision"):
                if previous.get(field):
                    current[field] = previous[field]
        merged.append(current)
    known_ids = {row.get("hypothesis_id", "") for row in merged}
    for row in existing:
        if row.get("hypothesis_id") and row["hypothesis_id"] not in known_ids:
            merged.append(row)
    return merged


def merge_checks(generated: list[dict[str, str]], existing: list[dict[str, str]]) -> list[dict[str, str]]:
    existing_by_id = {row.get("check_id", ""): row for row in existing if row.get("check_id")}
    merged: list[dict[str, str]] = []
    for row in generated:
        current = dict(row)
        previous = existing_by_id.get(row.get("check_id", ""))
        if previous and (
            previous.get("status") in {"done", "not_applicable"}
            or previous.get("result")
            or previous.get("blocking", "").strip().lower() == "false"
        ):
            for field in ("status", "source", "question", "result", "blocking"):
                if previous.get(field):
                    current[field] = previous[field]
        merged.append(current)
    known_ids = {row.get("check_id", "") for row in merged}
    for row in existing:
        if row.get("check_id") and row["check_id"] not in known_ids:
            merged.append(row)
    return merged


def preserve_manual_payload_fields(generated: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    if not existing:
        return generated
    result = dict(generated)
    for field in ("selected_decision", "selected_decision_summary", "manual_decision_locked", "analyst_decision"):
        if field in existing and existing.get(field) not in (None, ""):
            result[field] = existing[field]
    if existing.get("manual_decision_locked"):
        result["status"] = existing.get("status") or result["status"]
        result["gap_readiness"] = existing.get("gap_readiness") or result["gap_readiness"]
    result["generated_at"] = existing.get("generated_at") or result.get("generated_at")
    result["updated_at"] = utc_now_iso()
    return result


def update_counts(
    payload: dict[str, Any],
    hypotheses: list[dict[str, str]],
    checks: list[dict[str, str]],
    findings: list[dict[str, str]],
    mappings: list[dict[str, str]],
    behavior_probes: list[dict[str, str]] | None = None,
) -> None:
    behavior_probes = behavior_probes or []
    payload["counts"] = {
        "hypotheses": len(hypotheses),
        "checks_open": sum(1 for row in checks if row.get("status") in {"open", "blocked"}),
        "target_findings": len(findings),
        "object_mappings": len(mappings),
        "behavior_probes": len(behavior_probes),
    }


def write_functional_gap_bundle(
    root: Path,
    payload: dict[str, Any],
    hypotheses: list[dict[str, str]],
    checks: list[dict[str, str]],
    target_findings: list[dict[str, str]] | None = None,
    object_mappings: list[dict[str, str]] | None = None,
    behavior_probes: list[dict[str, str]] | None = None,
    preserve_review: bool = False,
) -> None:
    slug = str(payload["subject_card_slug"])
    card_dir = functional_gap_card_dir(root, slug)
    card_dir.mkdir(parents=True, exist_ok=True)
    target_findings = target_findings or []
    object_mappings = object_mappings or []
    behavior_probes = behavior_probes or []
    payload["hypotheses"] = hypotheses
    payload["required_checks"] = checks
    update_counts(payload, hypotheses, checks, target_findings, object_mappings, behavior_probes)
    (card_dir / "gap-card.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    write_csv_rows(card_dir / "hypotheses.csv", FUNCTIONAL_GAP_HYPOTHESES_HEADER, hypotheses)
    write_csv_rows(card_dir / "checks.csv", FUNCTIONAL_GAP_CHECKS_HEADER, checks)
    write_csv_rows(card_dir / "target-findings.csv", FUNCTIONAL_GAP_TARGET_FINDINGS_HEADER, target_findings)
    write_csv_rows(card_dir / "object-mapping.csv", FUNCTIONAL_GAP_OBJECT_MAPPING_HEADER, object_mappings)
    write_csv_rows(card_dir / "behavior-probes.csv", FUNCTIONAL_GAP_BEHAVIOR_PROBES_HEADER, behavior_probes)
    review_path = card_dir / "review.md"
    manual_notes = extract_manual_review_notes(review_path) if preserve_review else ""
    write_review(review_path, payload, hypotheses, checks, manual_notes=manual_notes)


def refresh_index(root: Path) -> dict[str, Any]:
    ensure_scaffold(root)
    rows: list[dict[str, str]] = []
    coverage_rows: list[dict[str, str]] = []
    open_question_rows: list[dict[str, str]] = []
    for path in sorted((functional_gap_root(root) / "cards").glob("*/gap-card.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        card_dir = path.parent
        checks = non_empty_csv_rows(card_dir / "checks.csv")
        open_checks = [row for row in checks if row.get("status") in {"open", "blocked"}]
        slug = str(payload.get("subject_card_slug") or card_dir.name)
        gap_card_path = path.relative_to(root).as_posix()
        rows.append(
            {
                "subject_card_slug": slug,
                "title": str(payload.get("title") or slug),
                "status": str(payload.get("status") or ""),
                "gap_readiness": str(payload.get("gap_readiness") or ""),
                "hypotheses_count": str(len(payload.get("hypotheses") or [])),
                "open_checks_count": str(len(open_checks)),
                "gap_card_path": gap_card_path,
                "selected_decision": str(payload.get("selected_decision") or ""),
                "review_notes": "",
            }
        )
        coverage_rows.append(
            {
                "subject_card_slug": slug,
                "gap_card_status": str(payload.get("status") or ""),
                "gap_readiness": str(payload.get("gap_readiness") or ""),
                "gap_card_path": gap_card_path,
                "selected_decision": str(payload.get("selected_decision") or ""),
                "open_checks_count": str(len(open_checks)),
                "notes": "",
            }
        )
        for index, check in enumerate(open_checks, 1):
            open_question_rows.append(
                {
                    "question_id": f"FGQ-{len(open_question_rows) + 1:04d}",
                    "subject_card_slug": slug,
                    "check_id": check.get("check_id", ""),
                    "question": check.get("question", ""),
                    "needed_source": check.get("source", ""),
                    "blocking": check.get("blocking", ""),
                    "status": check.get("status", ""),
                    "notes": check.get("result", ""),
                }
            )
    write_csv_rows(functional_gap_root(root) / "index.csv", FUNCTIONAL_GAP_INDEX_HEADER, rows)
    write_csv_rows(functional_gap_root(root) / "coverage.csv", FUNCTIONAL_GAP_COVERAGE_HEADER, coverage_rows)
    write_csv_rows(functional_gap_root(root) / "open-questions.csv", FUNCTIONAL_GAP_OPEN_QUESTIONS_HEADER, open_question_rows)
    return {"cards": len(rows), "index_path": (functional_gap_root(root) / "index.csv").relative_to(root).as_posix()}


def existing_gap_payload(root: Path, card: str) -> dict[str, Any] | None:
    path = functional_gap_card_dir(root, card) / "gap-card.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def target_inspection_is_stale(existing: dict[str, Any] | None, generated: dict[str, Any]) -> bool:
    if not existing:
        return False
    existing_inputs = existing.get("inputs") if isinstance(existing.get("inputs"), dict) else {}
    generated_inputs = generated.get("inputs") if isinstance(generated.get("inputs"), dict) else {}
    return (
        str(existing.get("target_source_hash") or "") != str(generated.get("target_source_hash") or "")
        or str(existing_inputs.get("next_vendor_path") or "") != str(generated_inputs.get("next_vendor_path") or "")
    )


def build_or_refresh_functional_gap_card(root: Path, card: str, preserve_manual: bool = True) -> dict[str, Any]:
    root = root.resolve()
    if not card:
        raise ValueError("--card is required; functional-gap build processes exactly one subject card per pass")
    ensure_scaffold(root)
    card_dir = functional_gap_card_dir(root, card)
    existing_payload = existing_gap_payload(root, card)
    existing_hypotheses = non_empty_csv_rows(card_dir / "hypotheses.csv")
    existing_checks = non_empty_csv_rows(card_dir / "checks.csv")
    existing_findings = non_empty_csv_rows(card_dir / "target-findings.csv")
    existing_mappings = non_empty_csv_rows(card_dir / "object-mapping.csv")
    existing_behavior_probes = non_empty_csv_rows(card_dir / "behavior-probes.csv")
    payload, _evidence_rows, gaps = load_subject_card(root, card)
    manifest = load_manifest(root)
    gap_payload, hypotheses, checks = build_gap_payload(root, card, payload, gaps, manifest)
    target_inspection_stale = target_inspection_is_stale(existing_payload, gap_payload)
    if target_inspection_stale:
        existing_findings = []
        existing_mappings = []
        existing_behavior_probes = []
        existing_checks = [row for row in existing_checks if row.get("check_id") not in TARGET_INSPECTION_CHECK_IDS]
    if preserve_manual:
        gap_payload = preserve_manual_payload_fields(gap_payload, existing_payload)
        hypotheses = merge_hypotheses(hypotheses, existing_hypotheses)
        checks = merge_checks(checks, existing_checks)
    write_functional_gap_bundle(
        root,
        gap_payload,
        hypotheses,
        checks,
        target_findings=existing_findings,
        object_mappings=existing_mappings,
        behavior_probes=existing_behavior_probes,
        preserve_review=bool(preserve_manual and (card_dir / "review.md").exists()),
    )
    refresh_index(root)
    return {
        "status": "ok",
        "card": card,
        "gap_readiness": gap_payload["gap_readiness"],
        "hypotheses": len(hypotheses),
        "checks": len(checks),
        "path": (functional_gap_card_dir(root, card) / "gap-card.json").relative_to(root).as_posix(),
    }


def build_functional_gap_card(root: Path, card: str, force: bool = False) -> dict[str, Any]:
    return build_or_refresh_functional_gap_card(root, card, preserve_manual=not force)


def refresh_functional_gap_card(root: Path, card: str, force: bool = False) -> dict[str, Any]:
    return build_or_refresh_functional_gap_card(root, card, preserve_manual=not force)


OBJECT_REF_RE = re.compile(
    r"\b(?:Документ|Справочник|РегистрСведений|РегистрНакопления|РегистрБухгалтерии|РегистрРасчета|"
    r"БизнесПроцесс|Задача|ПланВидовХарактеристик|ПланСчетов|ПланВидовРасчета|ОбщийМодуль|Отчет|Обработка|"
    r"Catalog|Document|InformationRegister|AccumulationRegister|AccountingRegister|CalculationRegister|"
    r"BusinessProcess|Task|ChartOfCharacteristicTypes|ChartOfAccounts|ChartOfCalculationTypes|CommonModule|"
    r"Report|DataProcessor|Enum|Role|ScheduledJob)\.[A-Za-zА-Яа-яЁё0-9_]+"
)
TARGET_TYPE_DIRS = {
    "Документ": "Documents",
    "Document": "Documents",
    "Справочник": "Catalogs",
    "Catalog": "Catalogs",
    "РегистрСведений": "InformationRegisters",
    "InformationRegister": "InformationRegisters",
    "РегистрНакопления": "AccumulationRegisters",
    "AccumulationRegister": "AccumulationRegisters",
    "РегистрБухгалтерии": "AccountingRegisters",
    "AccountingRegister": "AccountingRegisters",
    "РегистрРасчета": "CalculationRegisters",
    "CalculationRegister": "CalculationRegisters",
    "БизнесПроцесс": "BusinessProcesses",
    "BusinessProcess": "BusinessProcesses",
    "Задача": "Tasks",
    "Task": "Tasks",
    "ОбщийМодуль": "CommonModules",
    "CommonModule": "CommonModules",
    "Отчет": "Reports",
    "Report": "Reports",
    "Обработка": "DataProcessors",
    "DataProcessor": "DataProcessors",
    "Enum": "Enums",
    "Role": "Roles",
    "ScheduledJob": "ScheduledJobs",
}
TARGET_SEARCH_EXTENSIONS = {".xml", ".bsl", ".txt", ".md", ".json"}


def detail_map_candidates(root: Path, ref: str) -> list[Path]:
    value = str(ref or "").strip()
    if not value:
        return []
    path = repo_path(root, value)
    candidates = [path] if path.suffix == ".json" else [path / "detail-map.json"]
    slug = Path(value).name
    candidates.extend(
        [
            repo_path(root, f"analysis/detail-maps/{value}/detail-map.json"),
            repo_path(root, f"analysis/detail-maps/generated/{value}/detail-map.json"),
            repo_path(root, f"analysis/detail-maps/cards/{value}/detail-map.json"),
            repo_path(root, f"analysis/detail-maps/{slug}/detail-map.json"),
            repo_path(root, f"analysis/detail-maps/generated/{slug}/detail-map.json"),
            repo_path(root, f"analysis/detail-maps/cards/{slug}/detail-map.json"),
        ]
    )
    seen: set[str] = set()
    result: list[Path] = []
    for candidate in candidates:
        key = candidate.as_posix()
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def load_linked_detail_maps(root: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    maps: list[dict[str, Any]] = []
    refs = split_refs(payload.get("linked_detail_maps", []))
    refs.extend(ref for ref in split_refs(payload.get("source_artifacts", [])) if "detail-map" in ref)
    seen_paths: set[str] = set()
    for ref in refs:
        for path in detail_map_candidates(root, ref):
            if not path.exists() or path.as_posix() in seen_paths:
                continue
            seen_paths.add(path.as_posix())
            try:
                maps.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            break
    return maps


def collect_subject_object_refs(root: Path, payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    refs.extend(split_refs(payload.get("primary_objects", [])))

    def visit(value: Any) -> None:
        if isinstance(value, str):
            refs.extend(match.group(0) for match in OBJECT_REF_RE.finditer(value))
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)

    visit(payload.get("sections", {}))
    visit(payload.get("source_artifacts", []))
    for detail_map in load_linked_detail_maps(root, payload):
        visit(detail_map)
    seen: set[str] = set()
    result: list[str] = []
    for ref in refs:
        ref = str(ref or "").strip()
        if ref and ref not in seen:
            seen.add(ref)
            result.append(ref)
    return result


def object_tail(ref: str) -> str:
    return ref.rsplit(".", 1)[-1].strip()


def object_type(ref: str) -> str:
    return ref.split(".", 1)[0].strip() if "." in ref else ""


def text_file_candidates(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in TARGET_SEARCH_EXTENSIONS)


def find_target_object(target_root: Path, ref: str, files: list[Path]) -> tuple[str, Path | None, str, str]:
    type_dir = TARGET_TYPE_DIRS.get(object_type(ref), "")
    tail = object_tail(ref)
    ref_lower = ref.lower()
    tail_lower = tail.lower()
    if type_dir:
        expected_xml = f"{type_dir}/{tail}.xml".lower()
        expected_dir = f"{type_dir}/{tail}/".lower()
        for path in files:
            relative_lower = path.relative_to(target_root).as_posix().lower()
            if relative_lower == expected_xml or relative_lower.startswith(expected_dir):
                return "same_object", path, f"metadata:{type_dir}/{tail}", "high"
    for path in files:
        relative_lower = path.relative_to(target_root).as_posix().lower()
        if tail_lower and tail_lower in relative_lower:
            return "similar_object", path, f"path:{tail}", "medium"
    for path in files:
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except (OSError, UnicodeDecodeError):
            continue
        if ref_lower in text:
            return "same_object", path, f"content:{ref}", "high"
        if tail_lower and tail_lower in text:
            return "similar_object", path, f"content:{tail}", "medium"
    return "no_match", None, "not_found", "low"


def update_check_after_target_inspection(checks: list[dict[str, str]], findings: list[dict[str, str]]) -> list[dict[str, str]]:
    matched = sum(1 for row in findings if row.get("finding_type") != "no_match")
    missing = sum(1 for row in findings if row.get("finding_type") == "no_match")
    updated: list[dict[str, str]] = []
    for row in checks:
        current = dict(row)
        if current.get("check_id") == "FGC-0001":
            if findings:
                current["status"] = "done"
                current["result"] = f"Статическая проверка целевого релиза выполнена: найдено совпадений {matched}, без совпадений {missing}."
            else:
                current["status"] = "open"
                current["result"] = "Не найдены объекты предметной карточки для статической проверки целевого релиза; нужно заполнить primary_objects или уточнить evidence."
        updated.append(current)
    return updated


def update_check_after_behavior_probe_inspection(
    checks: list[dict[str, str]],
    behavior_probes: list[dict[str, str]],
    slug: str,
) -> list[dict[str, str]]:
    existing = [row for row in checks if row.get("check_id") != "FGC-0007"]
    results = {row.get("result", "") for row in behavior_probes}
    if not behavior_probes:
        status = "not_applicable"
        result = "Поведенческие признаки в предметной карточке не найдены."
        blocking = "false"
    elif results <= {"standard_supported"}:
        status = "done"
        result = "Статические behavior probes выполнены; все найденные проверки покрыты типовым evidence профиля."
        blocking = "false"
    elif "needs_profile" in results:
        status = "blocked"
        result = "Есть поведенческие проверки без профиля целевой конфигурации."
        blocking = "true"
    else:
        status = "open"
        result = "Статические behavior probes выполнены; часть выводов требуют ревью или runtime-проверки."
        blocking = "false"
    existing.append(
        {
            "check_id": "FGC-0007",
            "check_type": "target_behavior_static",
            "status": status,
            "source": f"analysis/functional-gaps/cards/{slug}/behavior-probes.csv",
            "question": "Проверить функциональные возможности целевого релиза по профилю конфигурации.",
            "result": result,
            "blocking": blocking,
        }
    )
    return existing


def next_finding_id(used_ids: set[str]) -> str:
    index = 1
    while True:
        finding_id = f"FGF-{index:04d}"
        if finding_id not in used_ids:
            used_ids.add(finding_id)
            return finding_id
        index += 1


def append_behavior_findings(findings: list[dict[str, str]], behavior_probes: list[dict[str, str]]) -> list[dict[str, str]]:
    result = [dict(row) for row in findings if not row.get("match_basis", "").startswith("behavior_probe:")]
    used_ids = {row.get("finding_id", "") for row in result if row.get("finding_id")}
    for probe in behavior_probes:
        finding_type = BEHAVIOR_RESULT_FINDING_TYPES.get(probe.get("result", ""))
        if not finding_type:
            probe["finding_id"] = ""
            continue
        finding_id = next_finding_id(used_ids)
        probe["finding_id"] = finding_id
        result.append(
            {
                "finding_id": finding_id,
                "finding_type": finding_type,
                "target_object": probe.get("target_profile", ""),
                "target_path": probe.get("evidence_ref", ""),
                "match_basis": f"behavior_probe:{probe.get('capability_id', '')}",
                "confidence": probe.get("confidence", ""),
                "evidence_ref": probe.get("source_ref", ""),
                "notes": probe.get("notes", ""),
            }
        )
    return result


def target_relative_from_gap_payload(payload: dict[str, Any]) -> str:
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    sources = payload.get("target_sources") if isinstance(payload.get("target_sources"), dict) else inputs
    return str(sources.get("next_vendor_path") or inputs.get("next_vendor_path") or "").strip()


def inspect_target_for_functional_gap(root: Path, card: str, force: bool = False, target_profile: str = "") -> dict[str, Any]:
    root = root.resolve()
    card_dir = functional_gap_card_dir(root, card)
    payload_path = card_dir / "gap-card.json"
    if payload_path.exists():
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        existing_findings = non_empty_csv_rows(card_dir / "target-findings.csv")
        existing_mappings = non_empty_csv_rows(card_dir / "object-mapping.csv")
        existing_behavior_probes = non_empty_csv_rows(card_dir / "behavior-probes.csv")
        if payload.get("manual_decision_locked") and existing_findings and existing_mappings and not force:
            if existing_behavior_probes and not target_profile.strip():
                refresh_index(root)
                return {
                    "status": "ok",
                    "card": card,
                    "target_findings": len(existing_findings),
                    "object_mappings": len(existing_mappings),
                    "behavior_probes": len(existing_behavior_probes),
                }
            manifest = load_manifest(root)
            target_relative = target_relative_from_gap_payload(payload)
            if not target_relative:
                raise ValueError("Не задан project.toml:[paths].next_vendor для inspect-target")
            target_root = repo_path(root, target_relative)
            if not target_root.exists():
                raise FileNotFoundError(f"Нет каталога целевого релиза: {target_relative}")
            subject_payload, evidence_rows, gaps = load_subject_card(root, card)
            files = text_file_candidates(target_root)
            profile_id = target_profile.strip() or default_target_profile_id(manifest)
            profile = load_target_profile(root, profile_id) if profile_id else None
            behavior_requests = derive_behavior_probe_requests(subject_payload, evidence_rows, gaps)
            behavior_probes = evaluate_behavior_probe_requests(root, behavior_requests, profile, files)
            checks = update_check_after_behavior_probe_inspection(non_empty_csv_rows(card_dir / "checks.csv"), behavior_probes, card)
            findings = append_behavior_findings(existing_findings, behavior_probes)
            hypotheses = non_empty_csv_rows(card_dir / "hypotheses.csv")
            payload["target_source_hash"] = target_source_hash(root, {"next_vendor_path": target_relative})
            payload["updated_at"] = utc_now_iso()
            write_functional_gap_bundle(
                root,
                payload,
                hypotheses,
                checks,
                target_findings=findings,
                object_mappings=existing_mappings,
                behavior_probes=behavior_probes,
                preserve_review=True,
            )
            refresh_index(root)
            return {
                "status": "ok",
                "card": card,
                "target_findings": len(findings),
                "object_mappings": len(existing_mappings),
                "behavior_probes": len(behavior_probes),
            }
    refresh_functional_gap_card(root, card)
    manifest = load_manifest(root)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    target_relative = target_relative_from_gap_payload(payload)
    if not target_relative:
        raise ValueError("Не задан project.toml:[paths].next_vendor для inspect-target")
    target_root = repo_path(root, target_relative)
    if not target_root.exists():
        raise FileNotFoundError(f"Нет каталога целевого релиза: {target_relative}")

    subject_payload, _evidence_rows, _gaps = load_subject_card(root, card)
    refs = collect_subject_object_refs(root, subject_payload)
    files = text_file_candidates(target_root)
    findings: list[dict[str, str]] = []
    mappings: list[dict[str, str]] = []
    for index, ref in enumerate(refs, 1):
        finding_type, target_path, match_basis, confidence = find_target_object(target_root, ref, files)
        relative_target_path = target_path.relative_to(root).as_posix() if target_path else ""
        target_object = ref if finding_type == "same_object" else ""
        findings.append(
            {
                "finding_id": f"FGF-{index:04d}",
                "finding_type": finding_type,
                "target_object": target_object,
                "target_path": relative_target_path,
                "match_basis": match_basis,
                "confidence": confidence,
                "evidence_ref": source_ref(root, card, "primary_objects"),
                "notes": "Совпадение найдено в исходниках целевого релиза." if target_path else "Совпадение в исходниках целевого релиза не найдено.",
            }
        )
        mappings.append(
            {
                "mapping_id": f"FGM-{index:04d}",
                "source_object": ref,
                "source_path": source_ref(root, card, "primary_objects"),
                "target_object": target_object,
                "target_path": relative_target_path,
                "mapping_type": "same_name" if finding_type == "same_object" else ("shared_infrastructure" if target_path else "no_target_match"),
                "confidence": confidence,
                "decision": "",
                "notes": "Найден тот же объект в целевом релизе." if finding_type == "same_object" else ("Найден похожий механизм или инфраструктурная ссылка; это не доказанное соответствие объекта." if target_path else "Нужно проверить, не заменяется ли типовым механизмом с другим именем."),
            }
        )
    checks = update_check_after_target_inspection(non_empty_csv_rows(card_dir / "checks.csv"), findings)
    profile_id = target_profile.strip() or default_target_profile_id(manifest)
    profile = load_target_profile(root, profile_id) if profile_id else None
    behavior_requests = derive_behavior_probe_requests(subject_payload, _evidence_rows, _gaps)
    behavior_probes = evaluate_behavior_probe_requests(root, behavior_requests, profile, files)
    checks = update_check_after_behavior_probe_inspection(checks, behavior_probes, card)
    findings = append_behavior_findings(findings, behavior_probes)
    hypotheses = non_empty_csv_rows(card_dir / "hypotheses.csv")
    payload["target_source_hash"] = target_source_hash(root, {"next_vendor_path": target_relative})
    payload["updated_at"] = utc_now_iso()
    write_functional_gap_bundle(
        root,
        payload,
        hypotheses,
        checks,
        target_findings=findings,
        object_mappings=mappings,
        behavior_probes=behavior_probes,
        preserve_review=True,
    )
    refresh_index(root)
    return {
        "status": "ok",
        "card": card,
        "target_findings": len(findings),
        "object_mappings": len(mappings),
        "behavior_probes": len(behavior_probes),
    }


def validate_functional_gaps(root: Path, card: str = "") -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    base = functional_gap_root(root)
    manifest = load_manifest(root)
    sources = target_sources(manifest)
    expected_next_vendor = sources.get("next_vendor_path", "")
    if not base.exists():
        return {"status": "fail", "cards": 0, "errors": ["Нет analysis/functional-gaps; выполните functional-gap build --card <slug>."]}
    for relative, header in (
        ("index.csv", FUNCTIONAL_GAP_INDEX_HEADER),
        ("coverage.csv", FUNCTIONAL_GAP_COVERAGE_HEADER),
        ("open-questions.csv", FUNCTIONAL_GAP_OPEN_QUESTIONS_HEADER),
        ("_templates/gap-card.json", ""),
    ):
        path = base / relative
        if not path.exists():
            errors.append(f"Нет артефакта functional-gap: {path.relative_to(root).as_posix()}")
            continue
        if header and first_csv_line(path) != header:
            errors.append(f"{path.relative_to(root).as_posix()}: неверный заголовок CSV")
    card_dirs = [functional_gap_card_dir(root, card)] if card else sorted((base / "cards").glob("*"))
    card_dirs = [path for path in card_dirs if path.is_dir()]
    if not card_dirs:
        errors.append("Нет functional-gap карточек.")
    seen: set[str] = set()
    for card_dir in card_dirs:
        payload_path = card_dir / "gap-card.json"
        hypotheses_path = card_dir / "hypotheses.csv"
        checks_path = card_dir / "checks.csv"
        findings_path = card_dir / "target-findings.csv"
        mapping_path = card_dir / "object-mapping.csv"
        behavior_probes_path = card_dir / "behavior-probes.csv"
        review_path = card_dir / "review.md"
        for path in (payload_path, hypotheses_path, checks_path, findings_path, mapping_path, behavior_probes_path, review_path):
            if not path.exists():
                errors.append(f"Нет артефакта functional-gap: {path.relative_to(root).as_posix()}")
        if first_csv_line(hypotheses_path) != FUNCTIONAL_GAP_HYPOTHESES_HEADER:
            errors.append(f"{hypotheses_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if first_csv_line(checks_path) != FUNCTIONAL_GAP_CHECKS_HEADER:
            errors.append(f"{checks_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if first_csv_line(findings_path) != FUNCTIONAL_GAP_TARGET_FINDINGS_HEADER:
            errors.append(f"{findings_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if first_csv_line(mapping_path) != FUNCTIONAL_GAP_OBJECT_MAPPING_HEADER:
            errors.append(f"{mapping_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if first_csv_line(behavior_probes_path) != FUNCTIONAL_GAP_BEHAVIOR_PROBES_HEADER:
            errors.append(f"{behavior_probes_path.relative_to(root).as_posix()}: неверный заголовок CSV")
        if not payload_path.exists():
            continue
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"Не удалось прочитать {payload_path.relative_to(root).as_posix()}: {exc}")
            continue
        if payload.get("schema_version") != FUNCTIONAL_GAP_SCHEMA_VERSION:
            errors.append(f"{payload_path.relative_to(root).as_posix()}: неверный schema_version")
        slug = str(payload.get("subject_card_slug") or "").strip()
        if not slug:
            errors.append(f"{payload_path.relative_to(root).as_posix()}: не заполнено subject_card_slug")
        elif slug in seen:
            errors.append(f"Дублируется functional-gap карточка для subject_card_slug={slug}")
        else:
            seen.add(slug)
        if card and slug and slug != card:
            errors.append(f"{payload_path.relative_to(root).as_posix()}: expected subject_card_slug={card}, got {slug}")
        if slug and not repo_path(root, f"analysis/subject-cards/cards/{slug}/subject-card.json").exists():
            errors.append(f"{slug}: нет исходной предметной карточки")
        readiness = str(payload.get("gap_readiness") or "").strip()
        status = str(payload.get("status") or "").strip()
        if status not in FUNCTIONAL_GAP_STATUSES:
            errors.append(f"{slug or card_dir.name}: недопустимый статус functional-gap: {status}")
        if readiness not in FUNCTIONAL_GAP_READINESS:
            errors.append(f"{slug or card_dir.name}: недопустимая готовность functional-gap: {readiness}")
        for field in ("title", "target_release", "source_subject_card", "subject_summary", "hypotheses", "required_checks", "inputs", "counts"):
            value = payload.get(field)
            if value in (None, "", []):
                errors.append(f"{slug or card_dir.name}: не заполнено поле {field}")
        if slug:
            expected_subject_hash = file_sha256(repo_path(root, subject_card_relative(slug)))
            if expected_subject_hash and payload.get("source_subject_card_hash") != expected_subject_hash:
                errors.append(f"{slug}: gap-card устарела относительно subject-card; выполните functional-gap refresh --card {slug}")
        inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        if expected_next_vendor and inputs.get("next_vendor_path") != expected_next_vendor:
            errors.append(f"{slug or card_dir.name}: next_vendor_path устарел; выполните functional-gap refresh --card {slug or card_dir.name}")
        if expected_next_vendor and payload.get("target_source_hash") != target_source_hash(root, sources):
            errors.append(f"{slug or card_dir.name}: target_source_hash устарел; выполните functional-gap refresh --card {slug or card_dir.name}")
        hypotheses = non_empty_csv_rows(hypotheses_path)
        checks = non_empty_csv_rows(checks_path)
        findings = non_empty_csv_rows(findings_path)
        mappings = non_empty_csv_rows(mapping_path)
        behavior_probes = non_empty_csv_rows(behavior_probes_path)
        hypothesis_ids = {row.get("hypothesis_id", "") for row in hypotheses if row.get("hypothesis_id")}
        check_ids = {row.get("check_id", "") for row in checks if row.get("check_id")}
        if len(hypothesis_ids) != len(hypotheses):
            errors.append(f"{slug or card_dir.name}: hypotheses.csv содержит пустые или повторяющиеся hypothesis_id")
        if len(check_ids) != len(checks):
            errors.append(f"{slug or card_dir.name}: checks.csv содержит пустые или повторяющиеся check_id")
        mapping_ids = {row.get("mapping_id", "") for row in mappings if row.get("mapping_id")}
        finding_ids = {row.get("finding_id", "") for row in findings if row.get("finding_id")}
        if len(finding_ids) != len(findings):
            errors.append(f"{slug or card_dir.name}: target-findings.csv содержит пустые или повторяющиеся finding_id")
        if len(mapping_ids) != len(mappings):
            errors.append(f"{slug or card_dir.name}: object-mapping.csv содержит пустые или повторяющиеся mapping_id")
        probe_ids = {row.get("probe_id", "") for row in behavior_probes if row.get("probe_id")}
        if len(probe_ids) != len(behavior_probes):
            errors.append(f"{slug or card_dir.name}: behavior-probes.csv содержит пустые или повторяющиеся probe_id")
        for row in behavior_probes:
            result = row.get("result", "")
            if result not in FUNCTIONAL_GAP_BEHAVIOR_PROBE_RESULTS:
                errors.append(f"{slug or card_dir.name}: behavior-probes.csv содержит недопустимый result {result}")
            confidence = row.get("confidence", "")
            if confidence not in FUNCTIONAL_GAP_BEHAVIOR_PROBE_CONFIDENCE:
                errors.append(f"{slug or card_dir.name}: behavior-probes.csv содержит недопустимый confidence {confidence}")
        for row in hypotheses:
            gap_type = row.get("gap_type", "")
            if gap_type not in FUNCTIONAL_GAP_TYPES:
                errors.append(f"{slug or card_dir.name}: недопустимый gap_type {gap_type}")
            hypothesis_status = row.get("status", "")
            if hypothesis_status not in FUNCTIONAL_GAP_HYPOTHESIS_STATUSES:
                errors.append(f"{slug or card_dir.name}: недопустимый статус гипотезы {hypothesis_status}")
            if row.get("next_check") and row["next_check"] not in check_ids:
                errors.append(f"{slug or card_dir.name}: hypothesis {row.get('hypothesis_id')} ссылается на неизвестную проверку {row.get('next_check')}")
        for row in checks:
            check_type = row.get("check_type", "")
            if check_type not in FUNCTIONAL_GAP_CHECK_TYPES:
                errors.append(f"{slug or card_dir.name}: недопустимый check_type {check_type}")
            status = row.get("status", "")
            if status not in FUNCTIONAL_GAP_CHECK_STATUSES:
                errors.append(f"{slug or card_dir.name}: недопустимый статус проверки {status}")
        if payload.get("status") in {"ready_for_review", "reviewed"}:
            decision = str(payload.get("selected_decision") or "").strip()
            if decision not in FUNCTIONAL_GAP_TYPES:
                errors.append(f"{slug or card_dir.name}: для ready/reviewed нужен selected_decision из контролируемого списка")
            blocking_open = [
                row
                for row in checks
                if row.get("blocking", "").strip().lower() == "true" and row.get("status") in {"open", "blocked"}
            ]
            if blocking_open:
                errors.append(f"{slug or card_dir.name}: есть открытые блокирующие проверки")
            if review_path.exists() and "## Решение аналитика" not in review_path.read_text(encoding="utf-8"):
                errors.append(f"{slug or card_dir.name}: review.md должен содержать раздел решения аналитика")
    return {"status": "ok" if not errors else "fail", "cards": len(card_dirs), "errors": errors}


def decision_bucket(decision: str) -> str:
    if not decision.strip():
        return "undecided"
    if decision in FUNCTIONAL_GAP_TYPES:
        return decision
    return "business_decision"


def load_gap_cards(root: Path) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    cards_root = functional_gap_root(root) / "cards"
    if not cards_root.exists():
        return cards
    for payload_path in sorted(cards_root.glob("*/gap-card.json")):
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Не удалось прочитать {payload_path.relative_to(root).as_posix()}: {exc}") from exc
        card_dir = payload_path.parent
        checks = non_empty_csv_rows(card_dir / "checks.csv")
        hypotheses = non_empty_csv_rows(card_dir / "hypotheses.csv")
        findings = non_empty_csv_rows(card_dir / "target-findings.csv")
        mappings = non_empty_csv_rows(card_dir / "object-mapping.csv")
        slug = str(payload.get("subject_card_slug") or card_dir.name)
        open_blocking_checks = [
            row
            for row in checks
            if row.get("blocking", "").strip().lower() == "true" and row.get("status") in {"open", "blocked"}
        ]
        cards.append(
            {
                "subject_card_slug": slug,
                "title": str(payload.get("title") or slug),
                "status": str(payload.get("status") or ""),
                "gap_readiness": str(payload.get("gap_readiness") or ""),
                "selected_decision": str(payload.get("selected_decision") or ""),
                "selected_decision_summary": str(payload.get("selected_decision_summary") or ""),
                "target_release": str(payload.get("target_release") or ""),
                "gap_card_path": payload_path.relative_to(root).as_posix(),
                "review_path": (card_dir / "review.md").relative_to(root).as_posix(),
                "hypotheses_count": len(hypotheses),
                "checks_count": len(checks),
                "open_checks_count": sum(1 for row in checks if row.get("status") in {"open", "blocked"}),
                "open_blocking_checks_count": len(open_blocking_checks),
                "target_findings_count": len(findings),
                "object_mappings_count": len(mappings),
                "target_findings": findings,
                "object_mappings": mappings,
                "checks": checks,
                "hypotheses": hypotheses,
            }
        )
    return cards


def build_functional_gap_map(root: Path) -> dict[str, Any]:
    root = root.resolve()
    ensure_scaffold(root)
    refresh_index(root)
    manifest = load_manifest(root)
    target_label = target_release_label(manifest)
    cards = load_gap_cards(root)
    output_dir = repo_path(root, "outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "cards_total": len(cards),
        "ready_for_review": sum(1 for card in cards if card.get("status") == "ready_for_review"),
        "reviewed": sum(1 for card in cards if card.get("status") == "reviewed"),
        "open_blocking_checks": sum(int(card.get("open_blocking_checks_count") or 0) for card in cards),
    }
    payload = {
        "target_release": target_label,
        "generated_at": utc_now_iso(),
        "summary": summary,
        "cards": cards,
    }
    (output_dir / "functional-gap-map.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    labels = {
        "undecided": "Доработки без итогового решения",
        "replace_by_standard": "Доработки к замене типовым механизмом",
        "adapt": "Доработки к адаптации",
        "preserve": "Доработки к переносу",
        "retire": "Доработки к исключению",
        "split": "Доработки со смешанным решением",
        "business_decision": "Доработки с бизнес-решением",
        "data_migration": "Доработки с проверкой переноса данных",
    }
    lines = [
        f"# Сводка по переходу на {target_label}",
        "",
        f"- Карточек функциональных разрывов: {summary['cards_total']}",
        f"- Готово к ревью: {summary['ready_for_review']}",
        f"- Отревьюировано: {summary['reviewed']}",
        f"- Открытых блокирующих проверок: {summary['open_blocking_checks']}",
        "",
    ]
    for bucket, title in labels.items():
        bucket_cards = [card for card in cards if decision_bucket(str(card.get("selected_decision") or "")) == bucket]
        lines.extend([f"## {title}", ""])
        if not bucket_cards:
            lines.extend(["Нет карточек.", ""])
            continue
        for card in bucket_cards:
            decision = card.get("selected_decision") or "undecided"
            lines.append(f"- `{card['subject_card_slug']}` {card['title']} - {GAP_TYPE_LABELS.get(str(decision), str(decision))}; статус: {card.get('status') or 'не указан'}; блокирующие проверки: {card.get('open_blocking_checks_count')}.")
        lines.append("")
    open_questions = non_empty_csv_rows(functional_gap_root(root) / "open-questions.csv")
    lines.extend(["## Открытые проверки", ""])
    if open_questions:
        for row in open_questions:
            lines.append(f"- `{row.get('subject_card_slug')}` / `{row.get('check_id')}`: {row.get('question')} ({row.get('status')})")
    else:
        lines.append("Открытых проверок нет.")
    lines.append("")
    (output_dir / "functional-gap-map.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {"status": "ok", "cards": len(cards), "path": "outputs/functional-gap-map.md"}


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = build_functional_gap_card(root, args.card, force=args.force)
    print(f"functional_gap_card: {result['card']}")
    print(f"gap_readiness: {result['gap_readiness']}")
    print(f"hypotheses: {result['hypotheses']}")
    print(f"checks: {result['checks']}")
    print(f"path: {result['path']}")
    return 0


def refresh_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = refresh_functional_gap_card(root, args.card, force=args.force)
    print(f"functional_gap_card: {result['card']}")
    print(f"gap_readiness: {result['gap_readiness']}")
    print(f"hypotheses: {result['hypotheses']}")
    print(f"checks: {result['checks']}")
    print(f"path: {result['path']}")
    return 0


def inspect_target_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = inspect_target_for_functional_gap(root, args.card, force=args.force, target_profile=args.target_profile)
    print(f"functional_gap_card: {result['card']}")
    print(f"target_findings: {result['target_findings']}")
    print(f"object_mappings: {result['object_mappings']}")
    print(f"behavior_probes: {result.get('behavior_probes', 0)}")
    return 0


def map_build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = build_functional_gap_map(root)
    print(f"functional_gap_map: {result['path']}")
    print(f"cards: {result['cards']}")
    return 0


def validate_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = validate_functional_gaps(root, card=args.card or "")
    print(f"functional_gap_status: {result['status']}")
    print(f"cards: {result.get('cards', 0)}")
    for error in result["errors"]:
        print(f"- {error}")
    return 0 if result["status"] == "ok" else 1


def status_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    index = refresh_index(root)
    rows = non_empty_csv_rows(functional_gap_root(root) / "index.csv")
    print(f"functional_gap_cards: {index['cards']}")
    for row in rows:
        print(f"- {row.get('subject_card_slug')}: {row.get('gap_readiness')} ({row.get('open_checks_count')} open checks)")
    return 0
