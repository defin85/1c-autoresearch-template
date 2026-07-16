from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import read_toml, repo_path


PROFILE_STATUSES = {"curated", "experimental", "generated"}
CAPABILITY_IDS = {
    "document_lifecycle_state",
    "state_driven_action",
    "state_driven_folder_move",
    "route_script_execution",
    "scheduled_object_processing",
    "additional_attributes",
    "access_by_state_or_role",
    "document_relation_semantics",
}


@dataclass(frozen=True)
class EvidenceRule:
    kind: str
    target: str
    context: str = ""
    note: str = ""


@dataclass(frozen=True)
class CapabilityProfile:
    capability_id: str
    standard_evidence: list[EvidenceRule]
    adaptation_evidence: list[EvidenceRule]
    negative_evidence: list[EvidenceRule]
    confidence_rules: dict[str, str]


@dataclass(frozen=True)
class TargetProfile:
    profile_id: str
    profile_title: str
    profile_status: str
    target_identity: dict[str, str]
    capabilities: dict[str, CapabilityProfile]


@dataclass(frozen=True)
class BehaviorProbeRequest:
    capability_id: str
    source_signal: str
    source_ref: str


def profile_path(root: Path, profile_id: str) -> Path:
    safe_profile_id = "".join(ch for ch in profile_id.strip() if ch.isalnum() or ch in {"_", "-"})
    return repo_path(root, f"analysis/functional-gaps/profiles/{safe_profile_id}.toml")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rules(rows: Any) -> list[EvidenceRule]:
    result: list[EvidenceRule] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        target = str(row.get("target") or "").strip()
        kind = str(row.get("kind") or "").strip()
        if not target or not kind:
            continue
        result.append(
            EvidenceRule(
                kind=kind,
                target=target,
                context=str(row.get("context") or "").strip(),
                note=str(row.get("note") or "").strip(),
            )
        )
    return result


def load_target_profile(root: Path, profile_id: str) -> TargetProfile | None:
    if not profile_id.strip():
        return None
    path = profile_path(root, profile_id)
    if not path.exists():
        return None
    raw = read_toml(path)
    capabilities: dict[str, CapabilityProfile] = {}
    for capability_id, payload in _as_dict(raw.get("capabilities")).items():
        if capability_id not in CAPABILITY_IDS or not isinstance(payload, dict):
            continue
        capabilities[capability_id] = CapabilityProfile(
            capability_id=capability_id,
            standard_evidence=_rules(payload.get("standard_evidence")),
            adaptation_evidence=_rules(payload.get("adaptation_evidence")),
            negative_evidence=_rules(payload.get("negative_evidence")),
            confidence_rules={str(k): str(v) for k, v in _as_dict(payload.get("confidence_rules")).items()},
        )
    return TargetProfile(
        profile_id=str(raw.get("profile_id") or profile_id).strip(),
        profile_title=str(raw.get("profile_title") or profile_id).strip(),
        profile_status=str(raw.get("profile_status") or "generated").strip(),
        target_identity={str(k): str(v) for k, v in _as_dict(raw.get("target_identity")).items()},
        capabilities=capabilities,
    )


def _flatten_subject_text(subject_payload: dict[str, Any], evidence_rows: list[dict[str, str]], gaps: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for field in ("title", "summary", "identification", "key_conclusion", "upgrade_risk", "runtime_data_needed"):
        value = subject_payload.get(field)
        if value:
            parts.append(str(value))
    sections = subject_payload.get("sections") if isinstance(subject_payload.get("sections"), dict) else {}
    for rows in sections.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("text"):
                parts.append(str(row["text"]))
    for row in evidence_rows:
        parts.extend(str(row.get(field) or "") for field in ("claim", "notes"))
    for row in gaps:
        parts.extend(str(row.get(field) or "") for field in ("question", "notes"))
    return "\n".join(parts).lower()


def derive_behavior_probe_requests(
    subject_payload: dict[str, Any],
    evidence_rows: list[dict[str, str]],
    gaps: list[dict[str, str]],
) -> list[BehaviorProbeRequest]:
    text = _flatten_subject_text(subject_payload, evidence_rows, gaps)
    slug = str(subject_payload.get("slug") or "").strip()
    source_ref = f"analysis/subject-cards/cards/{slug}/subject-card.json" if slug else "analysis/subject-cards"
    requests: dict[str, BehaviorProbeRequest] = {}

    def add(capability_id: str, source_signal: str) -> None:
        requests.setdefault(capability_id, BehaviorProbeRequest(capability_id, source_signal, source_ref))

    if any(word in text for word in ("состоя", "статус", "жизненн")):
        add("document_lifecycle_state", "упоминание состояния, статуса или жизненного цикла")
    if any(word in text for word in ("состоя", "статус", "переход")) and any(
        word in text for word in ("действ", "обработчик", "выполн", "достижен")
    ):
        add("state_driven_action", "действие или обработчик при достижении состояния")
    if "папк" in text and any(word in text for word in ("состоя", "статус", "архив", "действующ", "разработ")):
        add("state_driven_folder_move", "папки жизненного цикла связаны с состояниями или архивом")
    if any(word in text for word in ("маршрут", "задач", "согласован", "процесс")):
        add("route_script_execution", "упоминание маршрута, задачи, согласования или процесса")
    if any(word in text for word in ("регламент", "фонов", "расписан")):
        add("scheduled_object_processing", "упоминание регламентной или фоновой обработки")
    if any(word in text for word in ("дополнительн", "свойств", "реквизит")):
        add("additional_attributes", "упоминание дополнительных реквизитов, сведений или свойств")
    if any(word in text for word in ("прав", "доступ", "роль", "видим")):
        add("access_by_state_or_role", "упоминание прав, доступности, роли или видимости")
    if any(word in text for word in ("связ", "верси", "замещ", "отмен")):
        add("document_relation_semantics", "упоминание связей, версий, замещения или отмены действия")

    return [requests[key] for key in sorted(requests)]


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > 2_000_000:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _strip_bsl_strings_and_comments(line: str, in_multiline_string: bool = False) -> tuple[str, bool]:
    result: list[str] = []
    index = 0
    in_string = in_multiline_string
    if in_multiline_string:
        stripped_index = len(line) - len(line.lstrip())
        if stripped_index < len(line) and line[stripped_index] == "|":
            index = stripped_index + 1
        else:
            in_string = False
    while index < len(line):
        char = line[index]
        if not in_string and char == "/" and index + 1 < len(line) and line[index + 1] == "/":
            break
        if char == '"':
            if in_string and index + 1 < len(line) and line[index + 1] == '"':
                index += 2
                continue
            in_string = not in_string
            index += 1
            continue
        if not in_string:
            result.append(char)
        index += 1
    return "".join(result), in_string


def _identifier_pattern(target: str, suffix: str = "") -> re.Pattern[str]:
    return re.compile(rf"(?<![\w.]){re.escape(target)}(?![\w.]){suffix}", re.IGNORECASE)


def _has_exact_identifier(line: str, target: str) -> bool:
    return bool(_identifier_pattern(target).search(line))


def _has_exact_call(line: str, target: str) -> bool:
    return bool(_identifier_pattern(target, r"\s*\(").search(line))


def _is_call_candidate_line(line: str) -> bool:
    stripped = line.strip().lower()
    return not stripped.startswith(("процедура ", "функция "))


def _is_assignment_line(line: str) -> bool:
    stripped = line.strip().lower()
    if not stripped:
        return False
    if stripped.startswith("("):
        return False
    if " тогда" in stripped:
        return False
    if stripped.startswith(("если", "иначеесли", "пока", "для", "возврат", "и ", "или ")):
        return False
    if any(operator in stripped for operator in (">=", "<=", "<>")):
        return False
    if stripped.count("=") != 1:
        return False
    left, _right = stripped.split("=", 1)
    left = left.strip()
    if not left:
        return False
    return bool(re.fullmatch(r"[^\W\d]\w*(?:\.[^\W\d]\w*)*(?:\[[^\]]+\])?", left, flags=re.IGNORECASE))


def _target_found(rule: EvidenceRule, files: list[Path]) -> Path | None:
    target_lower = rule.target.lower()
    for path in files:
        text = _read_text(path).lower()
        if not text:
            continue
        if rule.kind == "metadata_ref":
            if target_lower in text:
                return path
            continue
        condition_until = ""
        in_multiline_string = False
        for raw_line in text.splitlines():
            if in_multiline_string and not raw_line.lstrip().startswith("|"):
                in_multiline_string = False
            line, in_multiline_string = _strip_bsl_strings_and_comments(raw_line, in_multiline_string)
            stripped = line.strip()
            if not stripped:
                continue
            if rule.kind == "source_assignment":
                if condition_until:
                    if condition_until in stripped:
                        condition_until = ""
                    continue
                if stripped.startswith(("если", "иначеесли")):
                    condition_until = "" if "тогда" in stripped else "тогда"
                    continue
                if stripped.startswith("пока"):
                    condition_until = "" if "цикл" in stripped else "цикл"
                    continue
                if not _is_assignment_line(line):
                    continue
                left, _right = line.split("=", 1)
                if left.strip().lower() == target_lower:
                    return path
                continue
            if rule.kind == "source_call":
                if not _is_call_candidate_line(line):
                    continue
                if _has_exact_call(line, target_lower):
                    return path
                continue
    return None


def _confidence(profile: TargetProfile, capability_id: str, result: str, default: str) -> str:
    capability = profile.capabilities.get(capability_id)
    if capability is None:
        return default
    value = capability.confidence_rules.get(result) or default
    return value if value in {"high", "medium", "low"} else default


def _row(
    index: int,
    request: BehaviorProbeRequest,
    profile: TargetProfile | None,
    result: str,
    confidence: str,
    evidence_ref: str,
    notes: str,
) -> dict[str, str]:
    return {
        "probe_id": f"BFP-{index:04d}",
        "capability_id": request.capability_id,
        "source_signal": request.source_signal,
        "source_ref": request.source_ref,
        "target_profile": profile.profile_id if profile else "",
        "profile_status": profile.profile_status if profile else "",
        "result": result,
        "confidence": confidence,
        "evidence_ref": evidence_ref,
        "finding_id": "",
        "notes": notes,
    }


def _evidence_ref(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def evaluate_behavior_probe_requests(
    root: Path,
    requests: list[BehaviorProbeRequest],
    profile: TargetProfile | None,
    files: list[Path],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, request in enumerate(requests, 1):
        if profile is None:
            rows.append(
                _row(
                    index,
                    request,
                    None,
                    "needs_profile",
                    "low",
                    "",
                    "Нет профиля целевой конфигурации для проверки поведения.",
                )
            )
            continue
        capability = profile.capabilities.get(request.capability_id)
        if capability is None:
            rows.append(
                _row(
                    index,
                    request,
                    profile,
                    "needs_profile",
                    "low",
                    "",
                    f"Профиль {profile.profile_id} не описывает capability {request.capability_id}.",
                )
            )
            continue
        standard_path = next((path for rule in capability.standard_evidence if (path := _target_found(rule, files))), None)
        if standard_path:
            confidence = _confidence(profile, request.capability_id, "standard_supported", "high")
            if profile.profile_status != "curated" and confidence == "high":
                confidence = "medium"
            rows.append(
                _row(
                    index,
                    request,
                    profile,
                    "standard_supported",
                    confidence,
                    _evidence_ref(root, standard_path),
                    "Найдено evidence типового механизма по профилю целевой конфигурации.",
                )
            )
            continue
        adaptation_path = next((path for rule in capability.adaptation_evidence if (path := _target_found(rule, files))), None)
        if adaptation_path:
            rows.append(
                _row(
                    index,
                    request,
                    profile,
                    "adaptation_candidate",
                    _confidence(profile, request.capability_id, "adaptation_candidate", "medium"),
                    _evidence_ref(root, adaptation_path),
                    "Найден механизм расширения или скрипта; это не доказательство чистой типовой настройки.",
                )
            )
            continue
        negative_path = next((path for rule in capability.negative_evidence if (path := _target_found(rule, files))), None)
        if negative_path:
            rows.append(
                _row(
                    index,
                    request,
                    profile,
                    "not_supported",
                    _confidence(profile, request.capability_id, "not_supported", "medium"),
                    _evidence_ref(root, negative_path),
                    "Найдено отрицательное evidence по профилю целевой конфигурации.",
                )
            )
            continue
        rows.append(
            _row(
                index,
                request,
                profile,
                "needs_runtime_check",
                "low",
                "",
                "Статическое evidence по профилю не найдено.",
            )
        )
    return rows
