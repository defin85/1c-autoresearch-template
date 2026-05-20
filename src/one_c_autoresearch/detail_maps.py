from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .autopilot import DIFF_INVENTORY_HEADER, FINAL_DIFF_INVENTORY_HEADER
from .common import repo_path, utc_now_iso


DETAIL_MAP_INDEX_HEADER = "slug,title,type,status,confidence,owner_feature,linked_features,source_rows,detail_map_path,generation_mode,completeness,notes"
DETAIL_MAP_GENERATION_MODES = {"generated", "manual", "enriched"}
DETAIL_MAP_SECTIONS = (
    "attributes",
    "form_rules",
    "validations",
    "lifecycle",
    "rights",
    "scheduled_jobs",
    "ui",
    "integrations",
    "sources",
    "open_questions",
)

_TYPE_BY_KIND = {
    "documents": "document",
    "catalogs": "catalog",
    "businessprocesses": "route",
    "tasks": "route",
    "scheduledjobs": "scheduled_job",
    "roles": "rights",
    "reports": "report",
    "httpservices": "integration",
    "webservices": "integration",
    "exchangeplans": "integration",
}

_TYPE_PREFIX = {
    "document": "document",
    "catalog": "catalog",
    "route": "route",
    "scheduled_job": "scheduled-job",
    "rights": "rights",
    "integration": "integration",
    "report": "report",
    "ui": "ui",
    "other": "object",
}

_TYPE_LABEL = {
    "document": "Документ",
    "catalog": "Справочник",
    "route": "Маршрут",
    "scheduled_job": "Регламентное задание",
    "rights": "Права и роли",
    "integration": "Интеграция",
    "report": "Отчет",
    "ui": "UI-поверхность",
    "other": "Объект",
}

_RU_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def non_empty_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if any((value or "").strip() for value in row.values())]


def csv_header_matches(path: Path, expected_header: str) -> bool:
    if not path.exists():
        return False
    return path.read_text(encoding="utf-8-sig").splitlines()[:1] == [expected_header]


def choose_inventory_rows(root: Path) -> tuple[str, list[dict[str, str]]]:
    final_path = repo_path(root, "analysis/indexes/final-diff-inventory.csv")
    if csv_header_matches(final_path, FINAL_DIFF_INVENTORY_HEADER):
        rows = non_empty_rows(read_csv_rows(final_path))
        if rows:
            return "analysis/indexes/final-diff-inventory.csv", rows

    diff_path = repo_path(root, "analysis/indexes/diff-inventory.csv")
    if csv_header_matches(diff_path, DIFF_INVENTORY_HEADER):
        return "analysis/indexes/diff-inventory.csv", non_empty_rows(read_csv_rows(diff_path))
    return "analysis/indexes/final-diff-inventory.csv", []


def include_row(row: dict[str, str]) -> bool:
    final_action = (row.get("final_action") or "").strip()
    if final_action == "exclude":
        return False
    final_status = (row.get("final_status") or row.get("status") or "").strip()
    if final_status in {"technical_noise_removed", "out_of_scope"}:
        return False
    classification = (row.get("classification") or "").strip()
    if classification == "technical_noise":
        return False
    return bool((row.get("diff_id") or row.get("path") or "").strip())


def normalize_kind(kind: str, path: str) -> str:
    value = (kind or "").strip()
    if value:
        return value
    parts = [part for part in re.split(r"[\\/]+", path or "") if part and part != "cf"]
    return parts[0] if parts else "Object"


def short_object_name(object_name: str, path: str, kind: str) -> str:
    value = (object_name or "").strip()
    if value:
        return value.split(".")[-1]
    parts = [part for part in re.split(r"[\\/]+", path or "") if part and part != "cf"]
    if len(parts) >= 2 and parts[0].lower() == kind.lower():
        return Path(parts[1]).stem
    if parts:
        return Path(parts[-1]).stem
    return "object"


def map_type_for(kind: str, object_name: str, area: str, path: str) -> str:
    kind_key = re.sub(r"[^A-Za-zА-Яа-я0-9]", "", kind).lower()
    direct = _TYPE_BY_KIND.get(kind_key)
    if direct:
        return direct
    text = " ".join([object_name, area, path]).lower()
    if "интеграц" in text or "обмен" in text or "exchange" in text:
        return "integration"
    if "/forms/" in (path or "").lower() or "форм" in text or "ui" in text:
        return "ui"
    return "other"


def transliterate(value: str) -> str:
    result: list[str] = []
    for char in value:
        lower = char.lower()
        if lower in _RU_TRANSLIT:
            result.append(_RU_TRANSLIT[lower])
        else:
            result.append(char.lower())
    return "".join(result)


def slugify(value: str) -> str:
    asciiish = transliterate(value)
    slug = re.sub(r"[^a-z0-9]+", "-", asciiish.lower()).strip("-")
    return slug or "object"


def feature_ids(rows: list[dict[str, str]]) -> list[str]:
    values: set[str] = set()
    for row in rows:
        for field in ("final_feature_id", "reverse_scenario_id", "feature_id", "scenario_id"):
            value = (row.get(field) or "").strip()
            if value:
                values.add(value)
    return sorted(values)


def owner_feature(rows: list[dict[str, str]], linked: list[str]) -> str:
    counter: Counter[str] = Counter()
    for row in rows:
        for field in ("final_feature_id", "reverse_scenario_id", "feature_id", "scenario_id"):
            value = (row.get(field) or "").strip()
            if value:
                counter[value] += 1
                break
    if counter:
        return counter.most_common(1)[0][0]
    return linked[0] if linked else ""


def aggregate_confidence(rows: list[dict[str, str]]) -> str:
    values = [(row.get("reverse_confidence") or row.get("confidence") or "").strip() for row in rows]
    values = [value for value in values if value]
    if not values:
        return "medium"
    if "low" in values:
        return "low"
    if "medium" in values:
        return "medium"
    return "high"


def row_source(row: dict[str, str]) -> str:
    return (row.get("path") or "").strip()


def row_line(row: dict[str, str]) -> str:
    return (row.get("line") or row.get("line_start") or "").strip()


def source_row(row: dict[str, str]) -> dict[str, str]:
    diff_id = (row.get("diff_id") or "").strip()
    return {
        "id": diff_id,
        "claim": (row.get("summary") or row.get("notes") or "").strip(),
        "source": row_source(row),
        "line": row_line(row),
        "evidence": (row.get("evidence_ref") or row.get("decision_ref") or "").strip(),
    }


def form_name(path: str) -> str:
    parts = [part for part in re.split(r"[\\/]+", path or "") if part]
    if "Forms" in parts:
        index = parts.index("Forms")
        if index + 1 < len(parts):
            return parts[index + 1]
    return Path(path).stem or "форма"


def module_name(path: str) -> str:
    parts = [part for part in re.split(r"[\\/]+", path or "") if part]
    for candidate in ("ObjectModule.bsl", "ManagerModule.bsl", "Module.bsl"):
        if parts and parts[-1] == candidate:
            return candidate
    return Path(path).name or "модуль"


def build_sections(rows: list[dict[str, str]], map_type: str, object_name: str, short_name: str, kind: str) -> dict[str, list[dict[str, str]]]:
    sections: dict[str, list[dict[str, str]]] = {section: [] for section in DETAIL_MAP_SECTIONS}
    for row in rows:
        diff_id = (row.get("diff_id") or "").strip()
        path = row_source(row)
        confidence = (row.get("reverse_confidence") or row.get("confidence") or "medium").strip()
        summary = (row.get("summary") or row.get("notes") or "").strip()
        line = row_line(row)

        sections["sources"].append(source_row(row))

        if map_type == "scheduled_job" or kind.lower() == "scheduledjobs":
            sections["scheduled_jobs"].append(
                {
                    "id": diff_id,
                    "name": short_name,
                    "description": summary,
                    "mechanism": path,
                    "status": "needs_review",
                    "confidence": confidence,
                    "source": path,
                    "line": line,
                }
            )
            continue

        if map_type == "rights" or kind.lower() == "roles":
            sections["rights"].append(
                {
                    "id": diff_id,
                    "role": short_name,
                    "description": summary,
                    "mechanism": path,
                    "confidence": confidence,
                    "source": path,
                    "line": line,
                }
            )
            continue

        if map_type == "integration":
            sections["integrations"].append(
                {
                    "id": diff_id,
                    "system": "Внешний контур или обмен",
                    "flow": short_name,
                    "description": summary,
                    "confidence": confidence,
                    "source": path,
                    "line": line,
                }
            )

        if "/Forms/" in path or "Forms/" in path:
            sections["ui"].append(
                {
                    "id": diff_id,
                    "surface": form_name(path),
                    "command": "",
                    "description": summary,
                    "confidence": confidence,
                    "source": path,
                    "line": line,
                }
            )
            if path.endswith(".bsl"):
                sections["form_rules"].append(
                    {
                        "id": diff_id,
                        "rule": f"Изменение формы {form_name(path)}",
                        "description": summary,
                        "mechanism": module_name(path),
                        "confidence": confidence,
                        "source": path,
                        "line": line,
                    }
                )
            continue

        if path.endswith(".xml") and "/Templates/" not in path:
            sections["attributes"].append(
                {
                    "object": object_name,
                    "kind": kind,
                    "name": Path(path).stem or short_name,
                    "synonym": "",
                    "data_type": "",
                    "vendor_status": (row.get("change_type") or "").strip(),
                    "relation": summary,
                    "confidence": confidence,
                    "source": path,
                    "line": line,
                    "comment": "Автосводка строки diff; реквизиты и табличные части требуют ручной детализации.",
                }
            )
        elif path.endswith(".bsl"):
            sections["lifecycle"].append(
                {
                    "id": diff_id,
                    "action": f"Измененная логика: {module_name(path)}",
                    "description": summary,
                    "mechanism": module_name(path),
                    "confidence": confidence,
                    "source": path,
                    "line": line,
                }
            )

    sections["open_questions"].append(
        {
            "id": "AUTO-REVIEW",
            "question": "Проверить полноту автоматически сгенерированной карты перед аналитическим выводом.",
            "why_open": "Builder группирует строки diff и reverse-map, но не выполняет глубокий разбор всех реквизитов, условий и UI-команд.",
            "needed": "Аналитик или отдельный агент должен дообогатить карту до режима enriched/complete, если блок идет в финальный отчет.",
        }
    )
    return sections


def existing_manual_slugs(detail_root: Path) -> set[str]:
    slugs: set[str] = set()
    if not detail_root.exists():
        return slugs
    generated_root = detail_root / "generated"
    for path in sorted(detail_root.rglob("detail-map.json")):
        if "_templates" in path.relative_to(detail_root).parts:
            continue
        try:
            path.relative_to(generated_root)
            continue
        except ValueError:
            pass
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            slugs.add(path.parent.name)
            continue
        slugs.add(str(data.get("slug") or path.parent.name).strip())
    return {slug for slug in slugs if slug}


def manual_detail_map_index_rows(root: Path, detail_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not detail_root.exists():
        return rows
    generated_root = detail_root / "generated"
    for path in sorted(detail_root.rglob("detail-map.json")):
        if "_templates" in path.relative_to(detail_root).parts:
            continue
        try:
            path.relative_to(generated_root)
            continue
        except ValueError:
            pass
        relative_path = path.relative_to(root).as_posix()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            rows.append(
                {
                    "slug": path.parent.name,
                    "title": path.parent.name,
                    "type": "other",
                    "status": "needs_review",
                    "confidence": "",
                    "owner_feature": "",
                    "linked_features": "",
                    "source_rows": "",
                    "detail_map_path": relative_path,
                    "generation_mode": "manual",
                    "completeness": "",
                    "notes": "Could not parse manual detail map; doctor reports the parse error separately.",
                }
            )
            continue
        linked = data.get("linked_features", [])
        if not isinstance(linked, list):
            linked = []
        rows.append(
            {
                "slug": str(data.get("slug") or path.parent.name),
                "title": str(data.get("title") or data.get("slug") or path.parent.name),
                "type": str(data.get("type") or "other"),
                "status": str(data.get("status") or "draft"),
                "confidence": str(data.get("confidence") or ""),
                "owner_feature": str(data.get("owner_feature") or ""),
                "linked_features": ";".join(str(item) for item in linked if str(item).strip()),
                "source_rows": ";".join(str(item) for item in data.get("source_diff_ids", []) if str(item).strip()) if isinstance(data.get("source_diff_ids", []), list) else "",
                "detail_map_path": relative_path,
                "generation_mode": str(data.get("generation_mode") or "manual"),
                "completeness": str(data.get("completeness") or ""),
                "notes": "Analyst-owned detail map outside generated/; builder does not overwrite it.",
            }
        )
    return rows


def unique_slug(base_slug: str, used: set[str]) -> str:
    slug = base_slug
    index = 2
    while slug in used:
        slug = f"{base_slug}-{index}"
        index += 1
    used.add(slug)
    return slug


def build_detail_maps(root: Path, force: bool = False) -> dict[str, Any]:
    root = root.resolve()
    input_relative, input_rows = choose_inventory_rows(root)
    rows = [row for row in input_rows if include_row(row)]
    detail_root = repo_path(root, "analysis/detail-maps")
    generated_root = detail_root / "generated"
    detail_root.mkdir(parents=True, exist_ok=True)
    generated_root.mkdir(parents=True, exist_ok=True)

    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    group_meta: dict[tuple[str, str], tuple[str, str, str]] = {}
    for row in rows:
        kind = normalize_kind(row.get("object_kind", ""), row.get("path", ""))
        short_name = short_object_name(row.get("object_name", ""), row.get("path", ""), kind)
        object_name = (row.get("object_name") or f"{kind}.{short_name}").strip()
        map_type = map_type_for(kind, object_name, row.get("area", ""), row.get("path", ""))
        key = (kind, object_name)
        groups[key].append(row)
        group_meta.setdefault(key, (map_type, object_name, short_name))

    used_slugs = existing_manual_slugs(detail_root)
    index_rows: list[dict[str, str]] = manual_detail_map_index_rows(root, detail_root)
    manual_index_rows = len(index_rows)
    written: list[str] = []
    skipped: list[str] = []
    generated_at = utc_now_iso()

    for key in sorted(groups, key=lambda item: (item[0].lower(), item[1].lower())):
        kind, object_name = key
        group_rows = sorted(groups[key], key=lambda row: (row.get("diff_id") or "", row.get("path") or ""))
        map_type, _, short_name = group_meta[key]
        base_slug = f"{_TYPE_PREFIX.get(map_type, 'object')}-{slugify(short_name)}"
        if base_slug in used_slugs:
            base_slug = f"{base_slug}-generated"
        slug = unique_slug(base_slug, used_slugs)
        linked = feature_ids(group_rows)
        owner = owner_feature(group_rows, linked)
        confidence = aggregate_confidence(group_rows)
        title = f"{_TYPE_LABEL.get(map_type, 'Объект')} {short_name}"
        relative_path = f"analysis/detail-maps/generated/{slug}/detail-map.json"
        output_path = repo_path(root, relative_path)
        if output_path.exists() and not force:
            skipped.append(relative_path)
        else:
            sections = build_sections(group_rows, map_type, object_name, short_name, kind)
            payload: dict[str, Any] = {
                "schema_version": "detail-map/v1",
                "id": slug,
                "slug": slug,
                "title": title,
                "type": map_type,
                "generation_mode": "generated",
                "completeness": "generated_seed",
                "status": "needs_review",
                "confidence": confidence,
                "owner_feature": owner,
                "linked_features": linked,
                "summary": (
                    f"Автоматически сгенерированная карта по объекту {object_name}. "
                    f"Источник: {input_relative}; строк diff: {len(group_rows)}; связанные BF: {', '.join(linked) or 'не указаны'}."
                ),
                "generated_at": generated_at,
                "source_inventory": input_relative,
                "source_diff_ids": [(row.get("diff_id") or "").strip() for row in group_rows if (row.get("diff_id") or "").strip()],
                "migration_notes": [
                    "Карта создана автоматически из final-diff/reverse-map и является стартовой инвентаризацией, а не финальной аналитической детализацией.",
                    "Для перехода на целевой релиз проверить, покрывает ли типовой механизм этот объект и какие строки требуют переноса или отказа от доработки.",
                ],
                **sections,
            }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            written.append(relative_path)

        index_rows.append(
            {
                "slug": slug,
                "title": title,
                "type": map_type,
                "status": "needs_review",
                "confidence": confidence,
                "owner_feature": owner,
                "linked_features": ";".join(linked),
                "source_rows": ";".join((row.get("diff_id") or "").strip() for row in group_rows if (row.get("diff_id") or "").strip()),
                "detail_map_path": relative_path,
                "generation_mode": "generated",
                "completeness": "generated_seed",
                "notes": f"Generated from {input_relative}; source row count: {len(group_rows)}.",
            }
        )

    index_path = detail_root / "index.csv"
    with index_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=DETAIL_MAP_INDEX_HEADER.split(","), lineterminator="\n")
        writer.writeheader()
        writer.writerows(index_rows)

    return {
        "root": str(root),
        "input": input_relative,
        "source_rows": len(rows),
        "generated_maps": len(groups),
        "manual_index_rows": manual_index_rows,
        "index_rows": len(index_rows),
        "written": written,
        "skipped": skipped,
        "index": "analysis/detail-maps/index.csv",
    }


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    result = build_detail_maps(root, force=args.force)
    print(f"Detail maps built at: {root}")
    print(f"Input: {result['input']}")
    print(f"Source rows: {result['source_rows']}")
    print(f"Generated maps: {result['generated_maps']}")
    print(f"Manual/enriched index rows: {result['manual_index_rows']}")
    print(f"Index rows: {result['index_rows']}")
    print(f"Index: {result['index']}")
    if result["written"]:
        print("Written:")
        for item in result["written"]:
            print(f"- {item}")
    if result["skipped"]:
        print("Skipped existing generated files without --force:")
        for item in result["skipped"]:
            print(f"- {item}")
    return 0
