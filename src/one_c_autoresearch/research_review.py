from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import read_jsonl, repo_path, utc_now_iso, write_jsonl
from .queue import queue_lock
from .queue_seed import REFRESHABLE_STATUSES, PROTECTED_STATUSES, stable_task_id
from .migration_requirements import BACKLOG as MRQ_BACKLOG, MAPPING as MRQ_MAPPING, REQUIREMENTS as MRQ_REQUIREMENTS, SUMMARY as MRQ_SUMMARY, validate as validate_migration_requirements

TARGETS_HEADER = (
    "target_id,target_type,status,priority,feature_id,subject_card_slug,title,"
    "source_artifacts,selection_reasons,expected_outputs,infobase_required,notes"
)
CHECKS_HEADER = (
    "check_id,target_id,question,evidence_required,positive_search,negative_search,"
    "expected_outputs,quality_gates,infobase_required,status,blocking,notes"
)
DECISIONS_HEADER = "decision_id,target_id,check_id,decision,confidence,evidence_refs,queue_task_id,decided_at,notes"
QUEUE_PLAN_SCHEMA_VERSION = "research-review-queue-plan/v1"
REVIEW_PREPARATION_GENERATION = "research-review-preparation/v1"
TARGETS_PATH = "analysis/research-review/targets.csv"
CHECKS_PATH = "analysis/research-review/checks.csv"
DECISIONS_PATH = "analysis/research-review/decisions.csv"
QUEUE_PLAN_PATH = "analysis/research-review/queue-plan.json"
DEFAULT_QUEUE_PATH = "analysis/queue/tasks.jsonl"
DETAILED_MARKUP_PATH = "analysis/detailed-register-reverse-review/markup.csv"


class ResearchReviewError(RuntimeError):
    pass


@dataclass
class ReviewTarget:
    target_id: str
    target_type: str
    priority: int
    feature_id: str
    subject_card_slug: str
    title: str
    source_artifacts: list[str]
    selection_reasons: list[str]
    expected_outputs: list[str]
    infobase_required: bool = False
    status: str = "prepared"
    notes: str = ""

    def csv_row(self) -> dict[str, str]:
        return {
            "target_id": self.target_id,
            "target_type": self.target_type,
            "status": self.status,
            "priority": str(self.priority),
            "feature_id": self.feature_id,
            "subject_card_slug": self.subject_card_slug,
            "title": self.title,
            "source_artifacts": ";".join(self.source_artifacts),
            "selection_reasons": ";".join(self.selection_reasons),
            "expected_outputs": ";".join(self.expected_outputs),
            "infobase_required": "true" if self.infobase_required else "false",
            "notes": self.notes,
        }


@dataclass
class ReviewCheck:
    check_id: str
    target_id: str
    question: str
    evidence_required: list[str]
    positive_search: list[str]
    negative_search: list[str]
    expected_outputs: list[str]
    quality_gates: list[str]
    infobase_required: bool = False
    status: str = "prepared"
    blocking: bool = False
    notes: str = ""

    def csv_row(self) -> dict[str, str]:
        return {
            "check_id": self.check_id,
            "target_id": self.target_id,
            "question": self.question,
            "evidence_required": ";".join(self.evidence_required),
            "positive_search": ";".join(self.positive_search),
            "negative_search": ";".join(self.negative_search),
            "expected_outputs": ";".join(self.expected_outputs),
            "quality_gates": ";".join(self.quality_gates),
            "infobase_required": "true" if self.infobase_required else "false",
            "status": self.status,
            "blocking": "true" if self.blocking else "false",
            "notes": self.notes,
        }


@dataclass
class ReviewPlan:
    targets: list[ReviewTarget] = field(default_factory=list)
    checks: list[ReviewCheck] = field(default_factory=list)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle) if any(str(value or "").strip() for value in row.values())]


def _write_csv(path: Path, header: str, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = header.split(",")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_fingerprint(root: Path, relative: str) -> dict[str, Any]:
    path = repo_path(root, relative)
    return {
        "path": relative,
        "exists": path.exists(),
        "sha256": _file_sha256(path),
        "size": path.stat().st_size if path.exists() and path.is_file() else 0,
    }


def _artifact_fingerprints(root: Path) -> list[dict[str, Any]]:
    relatives = {
        "analysis/external-source-review/source-code-decisions.csv",
        DETAILED_MARKUP_PATH,
        "analysis/subject-cards/registry.csv",
        "outputs/review/data.json",
        MRQ_REQUIREMENTS,
        MRQ_BACKLOG,
        MRQ_MAPPING,
    }
    for base in ("analysis/features", "analysis/reverse-map"):
        base_path = repo_path(root, base)
        if not base_path.exists():
            continue
        for path in sorted(base_path.rglob("*")):
            if path.is_file():
                relatives.add(path.relative_to(root).as_posix())
    return [_artifact_fingerprint(root, relative) for relative in sorted(relatives)]


def _split(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def _target_id(kind: str, key: str) -> str:
    return stable_task_id("RR", f"{kind}:{key}")


def _check_id(target_id: str) -> str:
    return f"CHK-{target_id[3:]}" if target_id.startswith("RR-") else f"CHK-{target_id}"


def _priority_from_rows(rows: list[dict[str, str]]) -> int:
    values = {str(row.get("priority") or "").upper() for row in rows}
    if "P0" in values:
        return 100
    if "P1" in values:
        return 90
    if "P2" in values:
        return 70
    return 60


def _load_subject_registry(root: Path) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    rows = _read_csv_rows(repo_path(root, "analysis/subject-cards/registry.csv"))
    by_slug = {row.get("slug", ""): row for row in rows if row.get("slug")}
    feature_to_slug: dict[str, str] = {}
    for row in rows:
        slug = row.get("slug", "")
        for feature_id in _split(row.get("linked_features", "")) + _split(row.get("owner_feature", "")):
            if feature_id and slug and feature_id not in feature_to_slug:
                feature_to_slug[feature_id] = slug
    return by_slug, feature_to_slug


def _subject_expected_outputs(slug: str) -> list[str]:
    if not slug:
        return [DECISIONS_PATH]
    base = f"analysis/subject-cards/cards/{slug}"
    return [
        f"{base}/subject-card.json",
        f"{base}/evidence.csv",
        f"{base}/gaps.csv",
        f"{base}/review.md",
        DECISIONS_PATH,
    ]


def _subject_card_source_artifacts(slug: str) -> list[str]:
    base = f"analysis/subject-cards/cards/{slug}"
    return [
        f"{base}/subject-card.json",
        f"{base}/evidence.csv",
        f"{base}/gaps.csv",
        f"{base}/review.md",
    ]


def _review_markup_rows(root: Path) -> list[dict[str, str]]:
    return _read_csv_rows(repo_path(root, DETAILED_MARKUP_PATH))


def _review_markup_stats(rows: list[dict[str, str]], expected_card: str) -> str:
    related = [row for row in rows if row.get("expected_card") == expected_card]
    statuses: dict[str, int] = {}
    for row in related:
        status = row.get("review_status", "")
        statuses[status] = statuses.get(status, 0) + 1
    status_text = ", ".join(f"{key}={value}" for key, value in sorted(statuses.items()))
    return f"markup rows={len(related)}; {status_text}"


def _review_target_subject_outputs(slug: str) -> list[str]:
    base = f"analysis/subject-cards/cards/{slug}"
    return [f"{base}/subject-card.json", f"{base}/evidence.csv", f"{base}/gaps.csv", f"{base}/review.md", DECISIONS_PATH]


def _existing_subject_sources(root: Path, slug: str) -> list[str]:
    return [relative for relative in _subject_card_source_artifacts(slug) if repo_path(root, relative).exists()]


def _build_markup_subject_card_targets(root: Path) -> list[ReviewTarget]:
    rows = _review_markup_rows(root)
    if not rows:
        return []

    specs = [
        {
            "key": "tax-reporting-split",
            "priority": 100,
            "feature_id": "BF-001",
            "subject_card_slug": "podgotovka-reglamentirovannoy-nalogovoy-otchetnosti",
            "title": "Раздробить подготовку регламентированной налоговой отчетности на подкарточки по видам налогов и отчетности",
            "cards": ["podgotovka-reglamentirovannoy-nalogovoy-otchetnosti"],
            "expected_cards": [
                "podgotovka-reglamentirovannoy-nalogovoy-otchetnosti",
                "nds-uchet-i-reglamentnye-operatsii",
                "nalog-na-pribyl-nalogovyy-uchet-i-otlozhennyy-nalog",
                "ndfl-strahovye-vznosy-i-personalnye-svedeniya",
                "missing:alkogolnaya-otchetnost-i-svedeniya-o-produktsii",
                "missing:ekologicheskaya-otchetnost-i-plata-za-nvos",
                "missing:statisticheskaya-otchetnost-fsgs",
            ],
            "notes": "Do not keep tax reporting as one broad card; do not use ambiguous Template.* rows as split proof.",
        },
        {
            "key": "vat-card-sync",
            "priority": 95,
            "feature_id": "BF-001",
            "subject_card_slug": "nds-uchet-i-reglamentnye-operatsii",
            "title": "Уточнить карточку НДС по строкам ручной разметки",
            "cards": ["nds-uchet-i-reglamentnye-operatsii"],
            "expected_cards": ["nds-uchet-i-reglamentnye-operatsii"],
            "notes": "Keep VAT separate from profit tax and generic reporting.",
        },
        {
            "key": "profit-tax-card-sync",
            "priority": 95,
            "feature_id": "BF-001",
            "subject_card_slug": "nalog-na-pribyl-nalogovyy-uchet-i-otlozhennyy-nalog",
            "title": "Уточнить карточку налога на прибыль, налогового учета и отложенного налога",
            "cards": ["nalog-na-pribyl-nalogovyy-uchet-i-otlozhennyy-nalog"],
            "expected_cards": ["nalog-na-pribyl-nalogovyy-uchet-i-otlozhennyy-nalog"],
            "notes": "Keep profit tax separate from VAT and generic regulated reporting.",
        },
        {
            "key": "ndfl-insurance-card-sync",
            "priority": 95,
            "feature_id": "BF-001",
            "subject_card_slug": "ndfl-strahovye-vznosy-i-personalnye-svedeniya",
            "title": "Уточнить карточку НДФЛ, страховых взносов и персональных сведений",
            "cards": ["ndfl-strahovye-vznosy-i-personalnye-svedeniya"],
            "expected_cards": ["ndfl-strahovye-vznosy-i-personalnye-svedeniya"],
            "notes": "Split further only if markup proves separate user or regulatory scenarios.",
        },
        {
            "key": "missing-alcohol-reporting",
            "priority": 90,
            "feature_id": "BF-001",
            "subject_card_slug": "alkogolnaya-otchetnost-i-svedeniya-o-produktsii",
            "title": "Создать или уточнить карточку алкогольной отчетности и сведений о продукции",
            "cards": [],
            "expected_cards": ["missing:alkogolnaya-otchetnost-i-svedeniya-o-produktsii"],
            "notes": "Large missing group; create card only from confirmed source-backed scenario.",
        },
        {
            "key": "missing-ecology-reporting",
            "priority": 88,
            "feature_id": "BF-001",
            "subject_card_slug": "ekologicheskaya-otchetnost-i-plata-za-nvos",
            "title": "Создать или уточнить карточку экологической отчетности и платы за НВОС",
            "cards": [],
            "expected_cards": ["missing:ekologicheskaya-otchetnost-i-plata-za-nvos"],
            "notes": "Create card only if rows prove separate regulatory process.",
        },
        {
            "key": "missing-statistical-reporting",
            "priority": 86,
            "feature_id": "BF-006",
            "subject_card_slug": "statisticheskaya-otchetnost-fsgs",
            "title": "Создать или уточнить карточку статистической отчетности ФСГС",
            "cards": [],
            "expected_cards": ["missing:statisticheskaya-otchetnost-fsgs"],
            "notes": "Create card only if rows prove separate reporting process, not generic report bucket.",
        },
        {
            "key": "missing-fixed-assets",
            "priority": 78,
            "feature_id": "BF-011",
            "subject_card_slug": "os-i-amortizatsiya",
            "title": "Проверить необходимость карточки основных средств и амортизации",
            "cards": [],
            "expected_cards": ["missing:os-i-amortizatsiya"],
            "notes": "Small but repeated missing group; merge into document/register cards if not separate scenario.",
        },
        {
            "key": "missing-workers-settlements",
            "priority": 76,
            "feature_id": "BF-001",
            "subject_card_slug": "raschety-s-rabotnikami-i-deponentami",
            "title": "Проверить необходимость карточки расчетов с работниками и депонентами",
            "cards": [],
            "expected_cards": ["missing:raschety-s-rabotnikami-i-deponentami"],
            "notes": "Create only if markup proves a separate payroll settlement process.",
        },
        {
            "key": "missing-foreign-bank-currency-control",
            "priority": 74,
            "feature_id": "BF-002",
            "subject_card_slug": "zarubezhnye-bankovskie-scheta-i-valyutnyy-kontrol",
            "title": "Проверить необходимость карточки зарубежных банковских счетов и валютного контроля",
            "cards": [],
            "expected_cards": ["missing:zarubezhnye-bankovskie-scheta-i-valyutnyy-kontrol"],
            "notes": "Create only if not covered by treasury and payments card.",
        },
        {
            "key": "missing-usn-kudir",
            "priority": 72,
            "feature_id": "BF-001",
            "subject_card_slug": "usn-i-kniga-ucheta-dohodov-rashodov",
            "title": "Проверить необходимость карточки УСН и книги учета доходов и расходов",
            "cards": [],
            "expected_cards": ["missing:usn-i-kniga-ucheta-dohodov-rashodov"],
            "notes": "Create only if separate USN scenario remains after tax reporting split.",
        },
        {
            "key": "ambiguous-template-owner",
            "priority": 70,
            "feature_id": "detailed-register-reverse-review",
            "subject_card_slug": "",
            "title": "Оставить неоднозначные Template.* без бизнес-карточки и подготовить правило разрешения владельца",
            "cards": [],
            "expected_cards": ["missing:ambiguous-template-owner"],
            "notes": "Do not create business card; prepare open question or technical category for ambiguous Template owner.",
        },
        {
            "key": "remaining-small-missing-groups",
            "priority": 65,
            "feature_id": "detailed-register-reverse-review",
            "subject_card_slug": "",
            "title": "Разобрать малые missing-группы после крупных карточек",
            "cards": [],
            "expected_cards": [],
            "notes": "Batch remaining missing:* groups; merge technical/supporting rows where possible.",
        },
    ]

    targets: list[ReviewTarget] = []
    for spec in specs:
        expected_cards = list(spec["expected_cards"])
        if spec["key"] == "remaining-small-missing-groups":
            expected_cards = sorted(
                {
                    row.get("expected_card", "")
                    for row in rows
                    if row.get("expected_card", "").startswith("missing:")
                    and row.get("expected_card")
                    not in {
                        "missing:ambiguous-template-owner",
                        "missing:alkogolnaya-otchetnost-i-svedeniya-o-produktsii",
                        "missing:ekologicheskaya-otchetnost-i-plata-za-nvos",
                        "missing:statisticheskaya-otchetnost-fsgs",
                        "missing:os-i-amortizatsiya",
                        "missing:raschety-s-rabotnikami-i-deponentami",
                        "missing:zarubezhnye-bankovskie-scheta-i-valyutnyy-kontrol",
                        "missing:usn-i-kniga-ucheta-dohodov-rashodov",
                    }
                }
            )
        source_artifacts = [DETAILED_MARKUP_PATH, "analysis/subject-cards/registry.csv"]
        for slug in spec["cards"]:
            source_artifacts.extend(_existing_subject_sources(root, slug))
        selection_reasons = [_review_markup_stats(rows, card) for card in expected_cards] or ["remaining small missing:* groups from markup.csv"]
        outputs = [DECISIONS_PATH, "analysis/subject-cards/registry.csv", "analysis/subject-cards/coverage.csv"]
        if spec["subject_card_slug"]:
            outputs.extend(_review_target_subject_outputs(str(spec["subject_card_slug"])))
        targets.append(
            ReviewTarget(
                target_id=_target_id("markup-subject-card-rebuild", str(spec["key"])),
                target_type="markup_subject_card_rebuild",
                priority=int(spec["priority"]),
                feature_id=str(spec["feature_id"]),
                subject_card_slug=str(spec["subject_card_slug"]),
                title=str(spec["title"]),
                source_artifacts=sorted(set(source_artifacts)),
                selection_reasons=selection_reasons,
                expected_outputs=sorted(set(outputs)),
                infobase_required=False,
                notes=str(spec["notes"]),
            )
        )
    return targets


def _subject_card_review_signal(root: Path, slug: str, registry_row: dict[str, str]) -> tuple[bool, list[str], bool]:
    base = repo_path(root, f"analysis/subject-cards/cards/{slug}")
    reasons: list[str] = []
    infobase_required = False

    evidence_path = base / "evidence.csv"
    if evidence_path.exists():
        text = evidence_path.read_text(encoding="utf-8")
        if "external_source_review" in text or "source-code-external-gap-review" in text:
            reasons.append("Subject card contains external source review evidence or notes.")

    gaps_path = base / "gaps.csv"
    for row in _read_csv_rows(gaps_path):
        status = str(row.get("status") or "").strip().lower()
        if status in {"open", "pending", "needs_followup", "needs_review"}:
            gap_id = str(row.get("gap_id") or "").strip() or "unknown"
            question = str(row.get("question") or "").strip()
            reasons.append(f"Subject card has open follow-up gap {gap_id}: {question[:120]}")
            marker_text = " ".join(
                str(row.get(field) or "").lower()
                for field in ("section", "question", "needed_source", "notes")
            )
            if any(marker in marker_text for marker in ("runtime", "данные иб", "инфобаз", "live")):
                infobase_required = True

    review_notes = str(registry_row.get("review_notes") or "").strip()
    if review_notes and any(marker in review_notes.lower() for marker in ("требует", "нужн", "follow", "runtime", "open")):
        reasons.append(f"Subject-card registry review note requires follow-up: {review_notes[:120]}")
        if any(marker in review_notes.lower() for marker in ("runtime", "данные иб", "инфобаз", "live")):
            infobase_required = True

    return bool(reasons), reasons, infobase_required


def _build_external_review_targets(root: Path) -> list[ReviewTarget]:
    decisions_path = "analysis/external-source-review/source-code-decisions.csv"
    rows = _read_csv_rows(repo_path(root, decisions_path))
    if not rows:
        return []
    _, feature_to_slug = _load_subject_registry(root)
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        feature_id = str(row.get("matched_feature_id") or "").strip() or "UNMATCHED"
        candidate_id = str(row.get("candidate_id") or "").strip()
        key = feature_id if feature_id != "UNMATCHED" else f"UNMATCHED:{candidate_id}"
        groups.setdefault(key, []).append(row)

    targets: list[ReviewTarget] = []
    for key, group_rows in sorted(groups.items()):
        feature_id = "UNMATCHED" if key.startswith("UNMATCHED:") else key
        subject_slug = feature_to_slug.get(feature_id, "")
        dispositions = sorted({str(row.get("disposition") or "").strip() for row in group_rows if row.get("disposition")})
        candidate_ids = [row.get("candidate_id", "") for row in group_rows if row.get("candidate_id")]
        title_source = group_rows[0].get("title") or feature_id
        source_artifacts = [decisions_path]
        for row in group_rows:
            source_artifacts.extend(_split(row.get("evidence_references", "")))
        target = ReviewTarget(
            target_id=_target_id("external-source-review", key),
            target_type="external_source_review",
            priority=_priority_from_rows(group_rows),
            feature_id=feature_id,
            subject_card_slug=subject_slug,
            title=f"Проверить результаты внешнего ревью: {title_source[:120]}",
            source_artifacts=sorted(set(source_artifacts)),
            selection_reasons=[
                f"source-code-decisions rows: {', '.join(candidate_ids)}",
                f"dispositions: {', '.join(dispositions) if dispositions else 'not specified'}",
            ],
            expected_outputs=_subject_expected_outputs(subject_slug),
            infobase_required=any("runtime" in str(row.get("reviewer_notes", "")).lower() for row in group_rows),
            notes=f"matched_feature_id={feature_id}",
        )
        targets.append(target)
    return targets


def _build_subject_card_targets(root: Path) -> list[ReviewTarget]:
    cards_root = repo_path(root, "analysis/subject-cards/cards")
    if not cards_root.exists():
        return []
    registry_by_slug, _ = _load_subject_registry(root)
    targets: list[ReviewTarget] = []
    for card_dir in sorted(path for path in cards_root.iterdir() if path.is_dir()):
        slug = card_dir.name
        registry_row = registry_by_slug.get(slug, {})
        eligible, selection_reasons, infobase_required = _subject_card_review_signal(root, slug, registry_row)
        if not eligible:
            continue
        card_path = card_dir / "subject-card.json"
        title = slug
        feature_id = str(registry_row.get("owner_feature") or "").strip()
        if card_path.exists():
            try:
                card = json.loads(card_path.read_text(encoding="utf-8"))
                title = str(card.get("title") or slug)
                linked_features = ";".join(str(item) for item in card.get("linked_features", []) if item)
                feature_id = linked_features or feature_id
            except Exception:
                pass
        target = ReviewTarget(
            target_id=_target_id("subject-card-review", slug),
            target_type="subject_card_review",
            priority=80,
            feature_id=feature_id,
            subject_card_slug=slug,
            title=f"Проверить синхронизацию карточки: {title}",
            source_artifacts=_subject_card_source_artifacts(slug),
            selection_reasons=selection_reasons,
            expected_outputs=_subject_expected_outputs(slug),
            infobase_required=infobase_required,
            notes="Prepared from subject-card review signals.",
        )
        targets.append(target)
    return targets


def _build_migration_requirement_targets(root: Path) -> list[ReviewTarget]:
    if not repo_path(root, MRQ_REQUIREMENTS).exists():
        return []
    requirements = [row for _, row in read_jsonl(repo_path(root, MRQ_REQUIREMENTS))]
    backlog = [row for _, row in read_jsonl(repo_path(root, MRQ_BACKLOG))] if repo_path(root, MRQ_BACKLOG).exists() else []
    mapping = [row for _, row in read_jsonl(repo_path(root, MRQ_MAPPING))] if repo_path(root, MRQ_MAPPING).exists() else []
    validation = validate_migration_requirements(root)
    specs = []
    if backlog:
        specs.append(("uncovered", 100, f"Разобрать непокрытые CUS: {len(backlog)}", [MRQ_BACKLOG, MRQ_REQUIREMENTS], ["uncovered included customizations"]))
    split_count = sum(row.get("migration_action") == "split_candidate" for row in mapping)
    if split_count or validation["counts"].get("conflicts"):
        specs.append(("conflicts", 100, f"Разобрать конфликты и кандидатов на разделение: {validation['counts'].get('conflicts', 0)}", [MRQ_MAPPING, MRQ_REQUIREMENTS], ["conflicting decisions", "split candidates"]))
    missing = sum(str(row.get("target_solution") or "") in {"", "needs_customer_decision", "business_decision"} for row in requirements)
    if missing:
        specs.append(("missing-decisions", 90, f"Уточнить целевые решения MRQ: {missing}", [MRQ_REQUIREMENTS], ["missing target decisions"]))
    stale = [error for error in validation["errors"] if "stale" in error.lower() or "generation" in error.lower()]
    if stale:
        specs.append(("stale-bundles", 90, f"Пересобрать устаревшие пакеты MRQ: {len(stale)}", [MRQ_REQUIREMENTS, MRQ_SUMMARY], stale))
    return [ReviewTarget(target_id=_target_id("migration-requirement", key), target_type="migration_requirement_review", priority=priority, feature_id="MRQ", subject_card_slug="", title=title, source_artifacts=sources, selection_reasons=reasons, expected_outputs=[MRQ_REQUIREMENTS, MRQ_MAPPING, MRQ_SUMMARY], notes="Prepared from migration-requirement validation state.") for key, priority, title, sources, reasons in specs]


def build_review_plan(root: Path) -> ReviewPlan:
    targets = _build_external_review_targets(root) + _build_subject_card_targets(root) + _build_markup_subject_card_targets(root) + _build_migration_requirement_targets(root)
    by_id: dict[str, ReviewTarget] = {}
    for target in targets:
        by_id[target.target_id] = target
    targets = sorted(by_id.values(), key=lambda item: (-item.priority, item.target_id))
    checks: list[ReviewCheck] = []
    for target in targets:
        quality_gates = ["source_lines", "positive_search", "negative_search", "review_passed"]
        if target.infobase_required:
            quality_gates.append("needs_infobase_data_marked")
        checks.append(
            ReviewCheck(
                check_id=_check_id(target.target_id),
                target_id=target.target_id,
                question=f"Проверить и зафиксировать выводы по цели ревью: {target.title}",
                evidence_required=target.source_artifacts,
                positive_search=[target.feature_id, target.subject_card_slug, target.title],
                negative_search=["не найдено в исходниках", "частично подтверждено", "runtime"],
                expected_outputs=target.expected_outputs,
                quality_gates=quality_gates,
                infobase_required=target.infobase_required,
                notes="Generated by research review preparation.",
            )
        )
    return ReviewPlan(targets=targets, checks=checks)


def _task_from_target(target: ReviewTarget, checks: list[ReviewCheck]) -> dict[str, Any]:
    task_id = stable_task_id("Q-RR", target.target_id)
    quality_gates = sorted({gate for check in checks for gate in check.quality_gates})
    expected_outputs = sorted({item for check in checks for item in check.expected_outputs})
    evidence_sources = sorted(set(target.source_artifacts))
    return {
        "id": task_id,
        "type": "review",
        "status": "pending",
        "priority": target.priority,
        "title": target.title,
        "feature_id": target.feature_id or target.subject_card_slug or target.target_id,
        "dependencies": [],
        "scope": [target.target_id, target.target_type, target.subject_card_slug, target.notes],
        "markers": [target.target_id, target.feature_id, target.subject_card_slug],
        "search_terms": sorted({term for check in checks for term in check.positive_search if term}),
        "quality_gates": quality_gates,
        "expected_outputs": expected_outputs,
        "evidence_sources": evidence_sources,
        "open_questions": [],
        "source_artifacts": sorted(set(target.source_artifacts + [TARGETS_PATH, CHECKS_PATH])),
        "selection_reasons": target.selection_reasons,
        "review_preparation_generation": REVIEW_PREPARATION_GENERATION,
        "review_target_id": target.target_id,
        "review_check_ids": [check.check_id for check in checks],
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }


def build_queue_plan(root: Path, plan: ReviewPlan, queue_path: Path, mode: str) -> dict[str, Any]:
    targets_by_id = {target.target_id: target for target in plan.targets}
    checks_by_target: dict[str, list[ReviewCheck]] = {}
    for check in plan.checks:
        checks_by_target.setdefault(check.target_id, []).append(check)
    tasks = [_task_from_target(target, checks_by_target.get(target.target_id, [])) for target in plan.targets]
    existing = {str(task.get("id")): task for _, task in read_jsonl(queue_path)} if queue_path.exists() else {}
    generated_ids = {task["id"] for task in tasks}
    actions: list[dict[str, Any]] = []
    for task in tasks:
        current = existing.get(task["id"])
        if current is None:
            action = "create"
        elif current.get("status") in REFRESHABLE_STATUSES:
            action = "update"
        elif current.get("status") in PROTECTED_STATUSES:
            action = "protected"
        else:
            action = "error"
        actions.append({"task_id": task["id"], "target_id": task["review_target_id"], "action": action})
    for task in existing.values():
        if task.get("review_preparation_generation") == REVIEW_PREPARATION_GENERATION and str(task.get("id")) not in generated_ids:
            actions.append({"task_id": task.get("id"), "target_id": task.get("review_target_id", ""), "action": "stale"})
    counts: dict[str, int] = {}
    for action in actions:
        counts[action["action"]] = counts.get(action["action"], 0) + 1
    return {
        "schema_version": QUEUE_PLAN_SCHEMA_VERSION,
        "mode": mode,
        "generated_at": utc_now_iso(),
        "input_artifacts": _artifact_fingerprints(root),
        "queue_path": queue_path.relative_to(root).as_posix(),
        "target_count": len(plan.targets),
        "check_count": len(plan.checks),
        "task_count": len(tasks),
        "tasks": tasks,
        "actions": actions,
        "counts": counts,
        "target_ids": sorted(targets_by_id),
        "errors": [action for action in actions if action["action"] == "error"],
    }


def write_review_artifacts(root: Path, plan: ReviewPlan, queue_plan: dict[str, Any]) -> None:
    _write_csv(repo_path(root, TARGETS_PATH), TARGETS_HEADER, [target.csv_row() for target in plan.targets])
    _write_csv(repo_path(root, CHECKS_PATH), CHECKS_HEADER, [check.csv_row() for check in plan.checks])
    decisions_path = repo_path(root, DECISIONS_PATH)
    if not decisions_path.exists():
        _write_csv(decisions_path, DECISIONS_HEADER, [])
    _write_json(repo_path(root, QUEUE_PLAN_PATH), queue_plan)


def apply_queue_plan(root: Path, queue_plan: dict[str, Any], queue_path: Path) -> dict[str, Any]:
    if queue_plan.get("errors"):
        raise ResearchReviewError("Review preparation queue plan contains merge errors; refusing to apply")
    with queue_lock(queue_path):
        current = [task for _, task in read_jsonl(queue_path)] if queue_path.exists() else []
        by_id = {str(task.get("id")): task for task in current}
        now = utc_now_iso()
        for action in queue_plan["actions"]:
            if action["action"] not in {"create", "update"}:
                continue
            task = next(item for item in queue_plan["tasks"] if item["id"] == action["task_id"])
            task = dict(task)
            task["updated_at"] = now
            if action["action"] == "create":
                task["created_at"] = now
                current.append(task)
                continue
            existing = by_id[action["task_id"]]
            preserved = {
                key: existing[key]
                for key in ("status", "claimed_by", "claimed_at", "completed_at", "result_summary", "created_at")
                if key in existing
            }
            existing.clear()
            existing.update(task)
            existing.update(preserved)
        write_jsonl(queue_path, current)
    queue_plan["mode"] = "apply"
    queue_plan["queue_hash_after"] = _file_sha256(queue_path)
    return queue_plan


def _validate_materialized_queue_tasks(
    root: Path,
    queue_path: Path,
    queue_plan: dict[str, Any],
    target_ids: set[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    queue_relative = queue_path.relative_to(root).as_posix()
    if not queue_path.exists():
        errors.append(f"Applied research-review queue plan has no materialized queue file: {queue_relative}")
        return

    queue_tasks = {str(task.get("id")): task for _, task in read_jsonl(queue_path)}
    tasks_by_id = {str(task.get("id")): task for task in queue_plan.get("tasks", [])}
    expected_actions = [
        action
        for action in queue_plan.get("actions", [])
        if action.get("action") in {"create", "update", "protected"}
    ]
    for action in expected_actions:
        task_id = str(action.get("task_id") or "")
        planned = tasks_by_id.get(task_id)
        actual = queue_tasks.get(task_id)
        if actual is None:
            errors.append(f"Applied research-review materialized queue task is missing: {task_id}")
            continue

        expected_statuses = REFRESHABLE_STATUSES | PROTECTED_STATUSES
        if actual.get("status") not in expected_statuses:
            errors.append(
                f"Applied research-review materialized queue task {task_id} has invalid status: {actual.get('status')}"
            )
        if actual.get("type") != "review":
            errors.append(f"Applied research-review materialized queue task {task_id} has invalid type: {actual.get('type')}")
        if actual.get("review_preparation_generation") != REVIEW_PREPARATION_GENERATION:
            errors.append(f"Applied research-review materialized queue task {task_id} has invalid review_preparation_generation")
        target_id = actual.get("review_target_id")
        if target_id not in target_ids:
            errors.append(f"Applied research-review materialized queue task {task_id} references missing target: {target_id}")

        for field in ("review_check_ids", "source_artifacts", "expected_outputs", "quality_gates"):
            if not actual.get(field):
                errors.append(f"Applied research-review materialized queue task {task_id} is missing {field}")

        for relative in _split(";".join(str(item) for item in actual.get("source_artifacts", []))):
            if not repo_path(root, relative.split(":", 1)[0]).exists():
                errors.append(f"Applied research-review materialized queue task {task_id} references missing source artifact: {relative}")

        if planned and actual.get("review_target_id") != planned.get("review_target_id"):
            errors.append(f"Applied research-review materialized queue task {task_id} target differs from queue plan")

    stale = [
        task_id
        for task_id, task in queue_tasks.items()
        if task.get("review_preparation_generation") == REVIEW_PREPARATION_GENERATION
        and task_id not in {str(action.get("task_id") or "") for action in expected_actions}
    ]
    if stale:
        warnings.append(f"Applied research-review queue has stale generated task(s): {', '.join(sorted(stale))}")


def validate_research_review(root: Path, queue_path: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    queue_path = queue_path or repo_path(root, DEFAULT_QUEUE_PATH)
    targets_path = repo_path(root, TARGETS_PATH)
    checks_path = repo_path(root, CHECKS_PATH)
    plan_path = repo_path(root, QUEUE_PLAN_PATH)
    decisions_path = repo_path(root, DECISIONS_PATH)
    for relative, path in ((TARGETS_PATH, targets_path), (CHECKS_PATH, checks_path), (DECISIONS_PATH, decisions_path), (QUEUE_PLAN_PATH, plan_path)):
        if not path.exists():
            errors.append(f"Missing research-review artifact: {relative}")
    targets = _read_csv_rows(targets_path)
    checks = _read_csv_rows(checks_path)
    target_ids = {row.get("target_id", "") for row in targets}
    for row in checks:
        if row.get("target_id") not in target_ids:
            errors.append(f"Check {row.get('check_id')} references missing target: {row.get('target_id')}")
        for relative in _split(row.get("evidence_required", "")):
            if not repo_path(root, relative.split(":", 1)[0]).exists():
                errors.append(f"Check {row.get('check_id')} references missing evidence artifact: {relative}")
    if plan_path.exists():
        try:
            queue_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"Could not parse {QUEUE_PLAN_PATH}: {exc}")
            queue_plan = {}
        if queue_plan.get("schema_version") != QUEUE_PLAN_SCHEMA_VERSION:
            errors.append(f"{QUEUE_PLAN_PATH} has invalid schema_version")
        planned_targets = {action.get("target_id") for action in queue_plan.get("actions", []) if action.get("action") in {"create", "update", "protected"}}
        for row in targets:
            if row.get("target_id") and row.get("target_id") not in planned_targets:
                errors.append(f"Target {row.get('target_id')} has no planned or protected queue task")
        task_targets = {task.get("review_target_id") for task in queue_plan.get("tasks", [])}
        for target_id in task_targets:
            if target_id not in target_ids:
                errors.append(f"Queue plan task references missing target: {target_id}")
        for task in queue_plan.get("tasks", []):
            task_id = task.get("id", "<unknown>")
            for relative in _split(";".join(str(item) for item in task.get("source_artifacts", []))):
                if not repo_path(root, relative.split(":", 1)[0]).exists():
                    errors.append(f"Queue plan task {task_id} references missing source artifact: {relative}")
        if queue_plan.get("mode") == "apply":
            _validate_materialized_queue_tasks(root, queue_path, queue_plan, target_ids, errors, warnings)
    elif not queue_path.exists():
        warnings.append(f"Queue path does not exist yet: {queue_path.relative_to(root).as_posix()}")
    return {
        "status": "fail" if errors else "ok",
        "targets": len(targets),
        "checks": len(checks),
        "errors": errors,
        "warnings": warnings,
    }


def prepare_research_review(root: Path, queue_path: Path, apply: bool) -> dict[str, Any]:
    plan = build_review_plan(root)
    queue_plan = build_queue_plan(root, plan, queue_path, "apply" if apply else "plan")
    write_review_artifacts(root, plan, queue_plan)
    if apply:
        queue_plan = apply_queue_plan(root, queue_plan, queue_path)
        write_review_artifacts(root, plan, queue_plan)
    validation = validate_research_review(root, queue_path)
    return {
        "status": validation["status"],
        "mode": queue_plan["mode"],
        "targets": len(plan.targets),
        "checks": len(plan.checks),
        "task_count": queue_plan["task_count"],
        "counts": queue_plan["counts"],
        "queue_plan": QUEUE_PLAN_PATH,
        "errors": validation["errors"],
        "warnings": validation["warnings"],
    }


def prepare_command(args: argparse.Namespace) -> int:
    if args.plan == args.apply:
        raise ResearchReviewError("Specify exactly one of --plan or --apply")
    root = Path(args.repo_path).resolve()
    queue_path = repo_path(root, args.queue_path)
    result = prepare_research_review(root, queue_path, apply=bool(args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] != "ok" else 0


def validate_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    result = validate_research_review(root, repo_path(root, args.queue_path))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] != "ok" else 0
