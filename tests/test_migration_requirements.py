from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from one_c_autoresearch.customization_registry import bootstrap_registry
from one_c_autoresearch.migration_requirements import (
    LINKS,
    METADATA,
    REQUIREMENTS,
    BACKLOG,
    activate,
    bootstrap,
    build,
    context,
    build_outputs,
    deactivate,
    replace_graph,
    stable_requirement_id,
    validate,
    _safe_source_path,
    _mutate_link,
)
from one_c_autoresearch.customer_register import build_customer_register, validate_customer_register
from one_c_autoresearch.research_review import build_review_plan


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    write(path, "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows))


class MigrationRequirementsTest(unittest.TestCase):
    def seed(self, root: Path) -> None:
        write(root / "analysis/indexes/final-diff-inventory.csv", "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes,reverse_status,reverse_confidence,reverse_scenario_id\nV8D-1,x,M,Catalog/A.json,Catalog,Catalog.A,metadata,F,custom,high,kept,test,,,,,\n")
        write_jsonl(root / "analysis/custom-metadata/index.jsonl", [{"schema_version":"custom-metadata-inventory/v1","item_id":"CMI-1","item_key":"Catalog|Catalog.A|object||","metadata_full_name":"Catalog.A","part_kind":"object","part_name":"","part_path":"Catalogs/A.xml","change_type":"added","status":"non_typical","reconciliation":{"links":{"final_diff_ids":["V8D-1"],"subject_card_slugs":["demo"]}}}])
        write(root / "analysis/subject-cards/registry.csv", "slug,title,subject_type,status,owner_feature,card_path\ndemo,Демо,business_process,ready_for_review,BF-1,analysis/subject-cards/cards/demo/subject-card.json\n")
        write(root / "analysis/subject-cards/cards/demo/subject-card.json", json.dumps({"title":"Демо","summary":"Сценарий","migration_boundary":"Граница","key_conclusion":"Перенести сценарий","upgrade_risk":"Средний","subject_type":"business_process","source_artifacts":[],"sections":{"open_questions":[{"question":"Проверить сценарий","needed":"Сценарий работает"}]}}, ensure_ascii=False))
        write(root / "analysis/functional-gaps/cards/demo/gap-card.json", json.dumps({"selected_decision":"adapt","selected_decision_summary":"Частично покрыто","target_release":"БП 3.0"}, ensure_ascii=False))
        self.assertEqual(bootstrap_registry(root)["status"], "ok")

    def test_plan_apply_context_and_idempotence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.seed(root)
            before = set(root.rglob("*"))
            self.assertEqual(bootstrap(root)["status"], "planned")
            self.assertEqual(before, set(root.rglob("*")))
            self.assertEqual(bootstrap(root, apply=True)["status"], "ok")
            first = (root / REQUIREMENTS).read_text()
            self.assertEqual(bootstrap(root, apply=True)["status"], "ok")
            self.assertEqual(first, (root / REQUIREMENTS).read_text())
            rid = stable_requirement_id("subject:demo")
            payload = context(root, rid)
            self.assertEqual(payload["total_customizations"], 1)
            self.assertFalse(payload["truncated"])
            self.assertTrue(context(root, rid, limit=0)["truncated"])
            self.assertEqual(validate(root)["status"], "ok")

    def test_shared_link_has_one_primary_owner_and_activation_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.seed(root); bootstrap(root, apply=True)
            links = [json.loads(line) for line in (root / LINKS).read_text().splitlines()]
            self.assertEqual([row["role"] for row in links], ["primary"])
            self.assertEqual(activate(root, comparison_accepted=False)["status"], "fail")
            self.assertFalse(json.loads((root / METADATA).read_text())["canonical_active"])

    def test_replace_graph_rebuilds_uncovered_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.seed(root); bootstrap(root, apply=True)
            requirements = [json.loads(line) for line in (root / REQUIREMENTS).read_text().splitlines()]
            self.assertEqual(replace_graph(root, requirements, [])["status"], "ok")
            backlog = [json.loads(line) for line in (root / BACKLOG).read_text().splitlines()]
            items = [json.loads(line) for line in (root / "analysis/customization-registry/customization-items.jsonl").read_text().splitlines()]
            self.assertEqual([row["customization_id"] for row in backlog], [items[0]["customization_id"]])

    def test_validation_rejects_second_primary_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.seed(root); bootstrap(root, apply=True)
            requirements = [json.loads(line) for line in (root / REQUIREMENTS).read_text().splitlines()]
            duplicate = dict(requirements[0]); duplicate["stable_key"] = "subject:other"; duplicate["requirement_id"] = stable_requirement_id("subject:other")
            write_jsonl(root / REQUIREMENTS, requirements + [duplicate])
            links = [json.loads(line) for line in (root / LINKS).read_text().splitlines()]
            links.append({**links[0], "requirement_id": duplicate["requirement_id"]})
            write_jsonl(root / LINKS, links)
            self.assertEqual(validate(root)["status"], "fail")
            with self.assertRaises(ValueError):
                _safe_source_path(root, "../outside")

    def test_activation_switches_customer_register_to_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.seed(root); bootstrap(root, apply=True)
            requirements = [json.loads(line) for line in (root / REQUIREMENTS).read_text().splitlines()]
            requirements[0]["status"] = "ready_for_review"
            write_jsonl(root / REQUIREMENTS, requirements)
            # Rebuild refreshes card hashes while preserving the reviewed status.
            self.assertEqual(bootstrap(root, apply=True)["status"], "ok")
            self.assertEqual(build_outputs(root)["status"], "ok")
            write(root / "analysis/migration-requirements/comparison.md", "accepted\n")
            self.assertEqual(activate(root, comparison_accepted=True, comparison_report="analysis/migration-requirements/comparison.md")["status"], "ok")
            result = build_customer_register(root)
            self.assertEqual(result["source"], "migration-requirements")
            self.assertEqual(result["rows"], 1)
            self.assertEqual(validate_customer_register(root)["status"], "ok")
            links = [json.loads(line) for line in (root / LINKS).read_text().splitlines()]
            self.assertEqual(_mutate_link(root, links[0]["requirement_id"], links[0]["customization_id"], False, "primary", "changed after activation")["status"], "fail")
            self.assertEqual(deactivate(root)["status"], "ok")
            self.assertEqual(_mutate_link(root, links[0]["requirement_id"], links[0]["customization_id"], False, "primary", "changed after deactivation")["status"], "ok")

    def test_failed_generation_publish_keeps_previous_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.seed(root); bootstrap(root, apply=True)
            before = (root / REQUIREMENTS).read_bytes()
            original_replace = Path.replace

            def fail_staging_publish(path: Path, target: Path):
                if path.name.startswith("migration-requirements-") and Path(target).name == "migration-requirements":
                    raise OSError("injected publish failure")
                return original_replace(path, target)

            with patch.object(Path, "replace", fail_staging_publish), self.assertRaises(OSError):
                bootstrap(root, apply=True)
            self.assertEqual((root / REQUIREMENTS).read_bytes(), before)
            self.assertEqual(validate(root)["status"], "ok")

    def test_review_preparation_reads_migration_requirement_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.seed(root); bootstrap(root, apply=True)
            requirements = [json.loads(line) for line in (root / REQUIREMENTS).read_text().splitlines()]
            requirements[0]["target_solution"] = "needs_customer_decision"
            write_jsonl(root / REQUIREMENTS, requirements)
            self.assertEqual(build(root)["status"], "ok")
            plan = build_review_plan(root)
            mrq_targets = [target for target in plan.targets if target.target_type == "migration_requirement_review"]
            self.assertTrue(any("целевые решения" in target.title for target in mrq_targets))

    def test_source_registry_change_marks_requirement_graph_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.seed(root); bootstrap(root, apply=True)
            source = root / "analysis/customization-registry/customization-links.jsonl"
            source.write_text(source.read_text() + "\n", encoding="utf-8")
            self.assertEqual(validate(root)["status"], "fail")
            self.assertEqual(build(root)["status"], "fail")

    def test_bootstrap_preserves_split_requirement_and_manual_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.seed(root); bootstrap(root, apply=True)
            requirements = [json.loads(line) for line in (root / REQUIREMENTS).read_text().splitlines()]
            split = {**requirements[0], "stable_key": "split:demo:part-2", "requirement_id": stable_requirement_id("split:demo:part-2"), "title": "Демо, часть 2", "target_solution": "replace_by_standard"}
            write_jsonl(root / REQUIREMENTS, requirements + [split])
            links = [json.loads(line) for line in (root / LINKS).read_text().splitlines()]
            links.append({**links[0], "requirement_id": split["requirement_id"], "role": "supporting", "effort_owner": False, "rationale": "Ручное разделение"})
            write_jsonl(root / LINKS, links)
            self.assertEqual(build(root)["status"], "ok")
            self.assertEqual(bootstrap(root, apply=True)["status"], "ok")
            current = [json.loads(line) for line in (root / REQUIREMENTS).read_text().splitlines()]
            self.assertIn(split["requirement_id"], {row["requirement_id"] for row in current})
            current_links = [json.loads(line) for line in (root / LINKS).read_text().splitlines()]
            self.assertIn((split["requirement_id"], links[0]["customization_id"]), {(row["requirement_id"], row["customization_id"]) for row in current_links})
            self.assertGreater(validate(root)["counts"]["conflicts"], 0)

    def test_exclusion_and_stale_specification_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.seed(root); bootstrap(root, apply=True)
            requirements = [json.loads(line) for line in (root / REQUIREMENTS).read_text().splitlines()]
            requirements[0]["status"] = "excluded_by_customer"
            requirements[0]["agreement_reason"] = ""
            write_jsonl(root / REQUIREMENTS, requirements)
            self.assertEqual(validate(root)["status"], "fail")
            requirements[0]["status"] = "draft"
            write_jsonl(root / REQUIREMENTS, requirements)
            self.assertEqual(build(root)["status"], "ok")
            self.assertEqual(build_outputs(root)["status"], "ok")
            specification_path = root / "outputs/technical-specification-draft.json"
            specification = json.loads(specification_path.read_text())
            specification["requirements"][0]["requirement_hash"] = "stale"
            write(specification_path, json.dumps(specification))
            self.assertTrue(any("stale" in error.lower() for error in validate(root)["errors"]))


if __name__ == "__main__":
    unittest.main()
