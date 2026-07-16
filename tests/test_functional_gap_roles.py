import json

import one_c_autoresearch.functional_gaps as functional_gaps
from one_c_autoresearch.functional_gaps import (
    FUNCTIONAL_GAP_BEHAVIOR_PROBES_HEADER,
    FUNCTIONAL_GAP_CHECKS_HEADER,
    FUNCTIONAL_GAP_COVERAGE_HEADER,
    FUNCTIONAL_GAP_HYPOTHESES_HEADER,
    FUNCTIONAL_GAP_INDEX_HEADER,
    FUNCTIONAL_GAP_IMPLEMENTATION_SCOPE_HEADER,
    FUNCTIONAL_GAP_METADATA_REBASE_HEADER,
    FUNCTIONAL_GAP_METADATA_REBASE_CARD_SUPPORT_HEADER,
    FUNCTIONAL_GAP_METADATA_REBASE_OBJECT_COVERAGE_HEADER,
    FUNCTIONAL_GAP_METADATA_REBASE_UNRESOLVED_HEADER,
    FUNCTIONAL_GAP_OBJECT_MAPPING_HEADER,
    FUNCTIONAL_GAP_OPEN_QUESTIONS_HEADER,
    FUNCTIONAL_GAP_SCENARIO_HEADER,
    FUNCTIONAL_GAP_SCHEMA_VERSION,
    FUNCTIONAL_GAP_TARGET_FINDINGS_HEADER,
    build_metadata_rebase,
    build_metadata_rebase_candidates,
    build_functional_gap_map,
    build_functional_equivalence_rows,
    coverage_status_for_role,
    file_sha256,
    merge_curated_object_mappings,
    merge_curated_target_findings,
    refresh_index,
    object_role_for_mapping,
    source_contour_problem,
    target_inspection_source_refs,
    validate_functional_gaps,
)
from one_c_autoresearch.functional_gap_dashboard import _attach_finding_sources
from one_c_autoresearch.functional_gap_dashboard import build_functional_gap_dashboard_snapshot


def test_functional_gap_object_roles_separate_supporting_and_gap_drivers() -> None:
    assert object_role_for_mapping("Catalog.Банки", "same_name") == "supporting_standard_object"
    assert coverage_status_for_role("supporting_standard_object") == "covered"

    assert object_role_for_mapping("Document.ПлановыйПлатеж", "no_target_match") == "core_source_object"
    assert coverage_status_for_role("core_source_object") == "not_covered"

    assert object_role_for_mapping("Document.json", "shared_infrastructure") == "noise_or_infrastructure"


def test_functional_equivalence_skips_technical_noise() -> None:
    rows = build_functional_equivalence_rows(
        [
            {
                "source_object": "Catalog.Банки",
                "target_object": "Catalog.Банки",
                "object_role": "supporting_standard_object",
                "source_path": "subject-card.json#primary_objects",
                "confidence": "high",
                "notes": "Найден тот же объект в целевом релизе.",
            },
            {
                "source_object": "Document.ПлановыйПлатеж",
                "target_object": "",
                "object_role": "core_source_object",
                "source_path": "subject-card.json#primary_objects",
                "confidence": "low",
                "notes": "Нужно проверить функциональный аналог.",
            },
            {
                "source_object": "Document.json",
                "target_object": "",
                "object_role": "noise_or_infrastructure",
                "source_path": "subject-card.json#primary_objects",
                "confidence": "medium",
                "notes": "Технический след.",
            },
        ]
    )

    assert [row["status"] for row in rows] == ["standard_setting", "adaptation_required"]
    assert "json" not in "\n".join(row["scenario"] for row in rows)


def test_source_contour_requires_explicit_core_objects() -> None:
    assert source_contour_problem(
        {
            "scenario_summary": "Платежный календарь и заявки на оплату.",
            "migration_boundary": "Проверять казначейский сценарий целиком.",
            "accepted_contour_id": "SCN-0002",
            "core_source_objects": [],
        }
    ) == "не определены core_source_objects"


def test_dashboard_target_findings_show_source_object() -> None:
    findings = [{"finding_id": "FGF-0002", "target_path": "target.bsl"}]
    mappings = [{"mapping_id": "FGM-0002", "source_object": "DataProcessor.id", "source_path": "subject-card.json#primary_objects"}]

    assert _attach_finding_sources(findings, mappings)[0]["source_object"] == "DataProcessor.id"


def test_dashboard_marks_added_source_metadata(tmp_path) -> None:
    (tmp_path / "outputs").mkdir()
    (tmp_path / "analysis/custom-metadata").mkdir(parents=True)
    (tmp_path / "analysis/functional-gaps/cards/demo").mkdir(parents=True)
    (tmp_path / "outputs/functional-gap-map.json").write_text(
        json.dumps({"cards": [{"subject_card_slug": "demo", "gap_card_path": "analysis/functional-gaps/cards/demo/gap-card.json"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "analysis/custom-metadata/index.csv").write_text(
        "metadata_full_name,change_type,status\nCommonModule.УчетУСН,added,non_typical\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/gap-card.json").write_text(
        json.dumps({"subject_card_slug": "demo", "title": "demo"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/functional-equivalence.csv").write_text(
        "scenario_id,scenario,status,standard_mechanism,target_object,evidence_ref,gap_or_limit,next_action,confidence,notes\n"
        "FGE-1,Проверить,standard_setting,CommonModule.УчетУСН,CommonModule.УчетУСН,e,Найден тот же объект,Зафиксировать,high,типовая опорная часть\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/object-mapping.csv").write_text(
        "mapping_id,scenario_id,source_object,target_object,mapping_type,object_role,coverage_status\n"
        "FGM-1,FGE-1,CommonModule.УчетУСН,CommonModule.УчетУСН,same_name,supporting_standard_object,covered\n",
        encoding="utf-8",
    )

    card = build_functional_gap_dashboard_snapshot(tmp_path)["cards"][0]

    assert card["scenarios"][0]["source_change_label"] == "добавлен в доработанном источнике"
    assert card["object_mappings"][0]["source_change_label"] == "добавлен в доработанном источнике"


def test_dashboard_uses_mapping_source_object_not_target_standard_mechanism(tmp_path) -> None:
    (tmp_path / "outputs").mkdir()
    (tmp_path / "analysis/custom-metadata").mkdir(parents=True)
    (tmp_path / "analysis/functional-gaps/cards/demo").mkdir(parents=True)
    (tmp_path / "outputs/functional-gap-map.json").write_text(
        json.dumps({"cards": [{"subject_card_slug": "demo", "gap_card_path": "analysis/functional-gaps/cards/demo/gap-card.json"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "analysis/custom-metadata/index.csv").write_text(
        "metadata_full_name,change_type,status\nCatalog.scanТипыДокументов,added,non_typical\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/gap-card.json").write_text(
        json.dumps({"subject_card_slug": "demo", "title": "demo"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/functional-equivalence.csv").write_text(
        "scenario_id,scenario,status,standard_mechanism,target_object,evidence_ref,gap_or_limit,next_action,confidence,notes\n"
        "FGE-1,Проверить функциональное покрытие: scanТипыДокументов,standard_setting,Catalog.СканированныеДокументыДляПередачиВЭлектронномВиде,Catalog.СканированныеДокументыДляПередачиВЭлектронномВиде,e,Найден типовой аналог,Зафиксировать,high,\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/object-mapping.csv").write_text(
        "mapping_id,scenario_id,source_object,target_object,mapping_type,object_role,coverage_status\n"
        "FGM-1,FGE-1,Catalog.scanТипыДокументов,Catalog.СканированныеДокументыДляПередачиВЭлектронномВиде,renamed_or_replaced,supporting_standard_object,covered\n",
        encoding="utf-8",
    )

    scenario = build_functional_gap_dashboard_snapshot(tmp_path)["cards"][0]["scenarios"][0]

    assert scenario["source_object"] == "Catalog.scanТипыДокументов"
    assert scenario["target_object"] == "Catalog.СканированныеДокументыДляПередачиВЭлектронномВиде"
    assert scenario["standard_mechanism"] == "Catalog.СканированныеДокументыДляПередачиВЭлектронномВиде"
    assert scenario["source_change_label"] == "добавлен в доработанном источнике"


def test_dashboard_fills_missing_source_metadata_status(tmp_path) -> None:
    (tmp_path / "outputs").mkdir()
    (tmp_path / "analysis/custom-metadata").mkdir(parents=True)
    (tmp_path / "analysis/functional-gaps/cards/demo").mkdir(parents=True)
    (tmp_path / "outputs/functional-gap-map.json").write_text(
        json.dumps({"cards": [{"subject_card_slug": "demo", "gap_card_path": "analysis/functional-gaps/cards/demo/gap-card.json"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "analysis/custom-metadata/index.csv").write_text("metadata_full_name,change_type,status\n", encoding="utf-8")
    (tmp_path / "analysis/functional-gaps/cards/demo/gap-card.json").write_text(
        json.dumps({"subject_card_slug": "demo", "title": "demo"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/functional-equivalence.csv").write_text(
        "scenario_id,scenario,status,standard_mechanism,target_object,evidence_ref,gap_or_limit,next_action,confidence,notes\n"
        "FGE-1,Проверить,adaptation_required,не найден,,e,Нет аналога,Проверить,low,ядро разрыва\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/object-mapping.csv").write_text(
        "mapping_id,scenario_id,source_object,target_object,mapping_type,object_role,coverage_status\n"
        "FGM-1,FGE-1,Catalog.Демо,,no_target_match,core_source_object,not_covered\n",
        encoding="utf-8",
    )

    card = build_functional_gap_dashboard_snapshot(tmp_path)["cards"][0]

    assert card["scenarios"][0]["source_change_label"] == "не найден в нетиповых метаданных"
    assert card["object_mappings"][0]["source_change_label"] == "не найден в нетиповых метаданных"


def test_target_inspection_uses_accepted_contour_not_section_tokens() -> None:
    gap_payload = {"source_contour": {"core_source_objects": ["DataProcessor.ПлатежныйКалендарь"]}}
    subject_payload = {"sections": {"noise": "DataProcessor.id DataProcessor.json"}}

    assert target_inspection_source_refs(gap_payload, subject_payload) == ["DataProcessor.ПлатежныйКалендарь"]


def test_target_inspection_refresh_preserves_valid_curated_roles() -> None:
    generated = [
        {
            "source_object": "Catalog.Бюджеты",
            "target_object": "",
            "object_role": "core_source_object",
            "scenario_id": "FGE-0001",
            "coverage_status": "not_covered",
            "is_gap_driver": "true",
            "confidence": "low",
            "decision": "",
            "notes": "generated",
        }
    ]
    existing = [
        {
            "source_object": "Catalog.Бюджеты",
            "target_object": "Catalog.Бюджеты",
            "object_role": "target_candidate_object",
            "scenario_id": "FGE-0001",
            "coverage_status": "partially_covered",
            "is_gap_driver": "true",
            "confidence": "medium",
            "decision": "adapt",
            "notes": "curated",
        }
    ]

    merged = merge_curated_object_mappings(generated, existing)

    assert merged[0]["object_role"] == "target_candidate_object"
    assert merged[0]["coverage_status"] == "partially_covered"
    assert merged[0]["decision"] == "adapt"
    assert merged[0]["notes"] == "curated"
    assert merged[0]["is_gap_driver"] == "false"


def test_target_findings_relevance_follows_curated_role() -> None:
    generated = [
        {
            "target_object": "Catalog.Бюджеты",
            "match_basis": "metadata:Catalog.Бюджеты",
            "object_role": "standard_target_object",
            "functional_relevance": "direct_standard_support",
            "confidence": "high",
            "notes": "generated",
        }
    ]
    existing = [
        {
            "target_object": "Catalog.Бюджеты",
            "match_basis": "metadata:Catalog.Бюджеты",
            "object_role": "target_candidate_object",
            "functional_relevance": "gap_driver",
            "confidence": "medium",
            "notes": "curated candidate",
        }
    ]

    merged = merge_curated_target_findings(generated, existing)

    assert merged[0]["object_role"] == "target_candidate_object"
    assert merged[0]["functional_relevance"] == "candidate_only"
    assert merged[0]["notes"] == "curated candidate"


def test_validate_functional_gaps_reuses_target_source_hash(tmp_path, monkeypatch) -> None:
    root = tmp_path
    (root / "project.toml").write_text(
        "[project]\nnext_vendor_version = \"3.0\"\n\n[paths]\nnext_vendor = \"sources/next_vendor\"\n",
        encoding="utf-8",
    )
    base = root / "analysis/functional-gaps"
    (base / "_templates").mkdir(parents=True)
    (base / "_templates/gap-card.json").write_text("{}\n", encoding="utf-8")
    (base / "index.csv").write_text(FUNCTIONAL_GAP_INDEX_HEADER + "\n", encoding="utf-8")
    (base / "coverage.csv").write_text(FUNCTIONAL_GAP_COVERAGE_HEADER + "\n", encoding="utf-8")
    (base / "open-questions.csv").write_text(FUNCTIONAL_GAP_OPEN_QUESTIONS_HEADER + "\n", encoding="utf-8")

    call_count = 0

    def fake_target_source_hash(_root, _sources):
        nonlocal call_count
        call_count += 1
        return "target-hash"

    monkeypatch.setattr(functional_gaps, "target_source_hash", fake_target_source_hash)
    monkeypatch.setattr(
        functional_gaps,
        "source_contour_snapshot",
        lambda *_args, **_kwargs: {
            "accepted_contour_id": "SCN-1",
            "core_source_objects": ["Document.X"],
        },
    )

    for slug in ("card-a", "card-b"):
        subject_dir = root / "analysis/subject-cards/cards" / slug
        subject_dir.mkdir(parents=True)
        subject_path = subject_dir / "subject-card.json"
        subject_path.write_text(json.dumps({"slug": slug}, ensure_ascii=False), encoding="utf-8")
        subject_hash = file_sha256(subject_path)

        card_dir = base / "cards" / slug
        card_dir.mkdir(parents=True)
        payload = {
            "schema_version": FUNCTIONAL_GAP_SCHEMA_VERSION,
            "subject_card_slug": slug,
            "title": slug,
            "target_release": "3.0",
            "source_subject_card": f"analysis/subject-cards/cards/{slug}/subject-card.json",
            "source_subject_card_hash": subject_hash,
            "subject_summary": "summary",
            "hypotheses": [{"id": "H-1"}],
            "required_checks": [{"id": "C-1"}],
            "inputs": {"next_vendor_path": "sources/next_vendor"},
            "counts": {"target_findings": 0},
            "status": "needs_target_analysis",
            "gap_readiness": "needs_target_release_check",
            "target_source_hash": "target-hash",
            "source_contour": {
                "slug": slug,
                "card_path": f"analysis/subject-cards/cards/{slug}/subject-card.json",
                "subject_card_hash": subject_hash,
                "accepted_contour_id": "SCN-1",
                "scenario_summary": "scenario",
                "migration_boundary": "boundary",
                "core_source_objects": ["Document.X"],
            },
        }
        (card_dir / "gap-card.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        (card_dir / "hypotheses.csv").write_text(FUNCTIONAL_GAP_HYPOTHESES_HEADER + "\n", encoding="utf-8")
        (card_dir / "checks.csv").write_text(FUNCTIONAL_GAP_CHECKS_HEADER + "\n", encoding="utf-8")
        (card_dir / "target-findings.csv").write_text(FUNCTIONAL_GAP_TARGET_FINDINGS_HEADER + "\n", encoding="utf-8")
        (card_dir / "object-mapping.csv").write_text(FUNCTIONAL_GAP_OBJECT_MAPPING_HEADER + "\n", encoding="utf-8")
        (card_dir / "behavior-probes.csv").write_text(FUNCTIONAL_GAP_BEHAVIOR_PROBES_HEADER + "\n", encoding="utf-8")
        (card_dir / "functional-equivalence.csv").write_text(FUNCTIONAL_GAP_SCENARIO_HEADER + "\n", encoding="utf-8")
        (card_dir / "implementation-scope.csv").write_text(FUNCTIONAL_GAP_IMPLEMENTATION_SCOPE_HEADER + "\n", encoding="utf-8")
        (card_dir / "review.md").write_text("# Review\n", encoding="utf-8")

    result = validate_functional_gaps(root)

    assert result["status"] == "ok", result
    assert call_count == 1


def test_validate_functional_gaps_rejects_v8d_without_stable_id(tmp_path, monkeypatch) -> None:
    root = tmp_path
    (root / "project.toml").write_text("[project]\nnext_vendor_version = \"3.0\"\n", encoding="utf-8")
    base = root / "analysis/functional-gaps"
    (base / "_templates").mkdir(parents=True)
    (base / "_templates/gap-card.json").write_text("{}\n", encoding="utf-8")
    (base / "index.csv").write_text(FUNCTIONAL_GAP_INDEX_HEADER + "\n", encoding="utf-8")
    (base / "coverage.csv").write_text(FUNCTIONAL_GAP_COVERAGE_HEADER + "\n", encoding="utf-8")
    (base / "open-questions.csv").write_text(FUNCTIONAL_GAP_OPEN_QUESTIONS_HEADER + "\n", encoding="utf-8")
    (root / "analysis/indexes").mkdir(parents=True)
    (root / "analysis/indexes/final-diff-inventory.csv").write_text(
        "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes,reverse_status,reverse_confidence,reverse_scenario_id,final_feature_id,final_status,final_action,blocking_reason\n"
        "V8D-00001,v8unpack-refinement,M,Report/Demo/Report.obj.bsl,Report,Report.Demo,bsl,BF-001,custom,high,retained_candidate,summary,analysis/cache/noise/clean-rebase-v8unpack/repo#Report/Demo/Report.obj.bsl,,,,,,,,\n",
        encoding="utf-8",
    )
    (root / "analysis/indexes/diff-id-map.csv").write_text(
        "stable_diff_id,current_diff_id,source,change_type,path,object_kind,object_name,area,active,content_fingerprint,first_seen_ref,last_seen_ref,removed_by,notes\n"
        "DIF-0000000000000001,V8D-00001,v8unpack-refinement,M,Report/Demo/Report.obj.bsl,Report,Report.Demo,bsl,true,vendor_blob=;target_blob=,,,,\n",
        encoding="utf-8",
    )

    slug = "demo"
    subject_dir = root / "analysis/subject-cards/cards" / slug
    subject_dir.mkdir(parents=True)
    subject_path = subject_dir / "subject-card.json"
    subject_path.write_text(json.dumps({"slug": slug}, ensure_ascii=False), encoding="utf-8")
    subject_hash = file_sha256(subject_path)
    (subject_dir / "evidence.csv").write_text(
        "evidence_id,section,claim,source_type,source_path,line,linked_diff_id,linked_feature_id,confidence,notes\n"
        "E-1,sources,claim,static_source,analysis/indexes/final-diff-inventory.csv,2,V8D-00001,BF-001,high,\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        functional_gaps,
        "source_contour_snapshot",
        lambda *_args, **_kwargs: {
            "accepted_contour_id": "SCN-1",
            "core_source_objects": ["Report.Demo"],
        },
    )

    card_dir = base / "cards" / slug
    card_dir.mkdir(parents=True)
    payload = {
        "schema_version": FUNCTIONAL_GAP_SCHEMA_VERSION,
        "subject_card_slug": slug,
        "title": slug,
        "target_release": "3.0",
        "source_subject_card": f"analysis/subject-cards/cards/{slug}/subject-card.json",
        "source_subject_card_hash": subject_hash,
        "subject_summary": "summary",
        "hypotheses": [{"id": "H-1"}],
        "required_checks": [{"id": "C-1"}],
        "inputs": {},
        "counts": {"target_findings": 0},
        "status": "needs_target_analysis",
        "gap_readiness": "needs_target_release_check",
        "source_contour": {
            "slug": slug,
            "card_path": f"analysis/subject-cards/cards/{slug}/subject-card.json",
            "subject_card_hash": subject_hash,
            "accepted_contour_id": "SCN-1",
            "scenario_summary": "scenario",
            "migration_boundary": "boundary",
            "core_source_objects": ["Report.Demo"],
        },
    }
    (card_dir / "gap-card.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (card_dir / "hypotheses.csv").write_text(FUNCTIONAL_GAP_HYPOTHESES_HEADER + "\n", encoding="utf-8")
    (card_dir / "checks.csv").write_text(FUNCTIONAL_GAP_CHECKS_HEADER + "\n", encoding="utf-8")
    (card_dir / "target-findings.csv").write_text(FUNCTIONAL_GAP_TARGET_FINDINGS_HEADER + "\n", encoding="utf-8")
    (card_dir / "object-mapping.csv").write_text(FUNCTIONAL_GAP_OBJECT_MAPPING_HEADER + "\n", encoding="utf-8")
    (card_dir / "behavior-probes.csv").write_text(FUNCTIONAL_GAP_BEHAVIOR_PROBES_HEADER + "\n", encoding="utf-8")
    (card_dir / "functional-equivalence.csv").write_text(FUNCTIONAL_GAP_SCENARIO_HEADER + "\n", encoding="utf-8")
    (card_dir / "implementation-scope.csv").write_text(
        FUNCTIONAL_GAP_IMPLEMENTATION_SCOPE_HEADER
        + "\nFGS-0001,FGE-0001,Report.Demo,key,V8D-00001,,analysis/source,target,needs_gap_decision,check,\n",
        encoding="utf-8",
    )
    (card_dir / "review.md").write_text("# Review\n", encoding="utf-8")

    result = validate_functional_gaps(root)

    assert result["status"] == "fail"
    assert "без linked_stable_diff_ids" in "\n".join(result["errors"])


def write_metadata_rebase_inputs(root, metadata_rows: str, decisions_rows: str = "", final_rows: str = "") -> None:
    (root / "analysis/custom-metadata").mkdir(parents=True, exist_ok=True)
    (root / "analysis/reverse-map").mkdir(parents=True, exist_ok=True)
    (root / "analysis/indexes").mkdir(parents=True, exist_ok=True)
    (root / "analysis/functional-gaps").mkdir(parents=True, exist_ok=True)
    (root / "analysis/custom-metadata/index.csv").write_text(metadata_rows, encoding="utf-8")
    (root / "analysis/reverse-map/decisions.csv").write_text(
        decisions_rows
        or "decision_id,workitem_id,diff_id,scenario_id,decision,confidence,rationale,evidence_ref,decided_by,decided_at\n",
        encoding="utf-8",
    )
    (root / "analysis/indexes/final-diff-inventory.csv").write_text(
        final_rows
        or "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes,reverse_status,reverse_confidence,reverse_scenario_id,final_feature_id,final_status,final_action,blocking_reason\n",
        encoding="utf-8",
    )


def test_metadata_rebase_counts_unique_diff_votes(tmp_path) -> None:
    metadata_header = "schema_version,item_id,item_key,comparison_mode,source_format,metadata_kind,metadata_name,metadata_full_name,part_kind,part_name,part_path,change_type,status,typical_item_key,typical_content_hash,customer_item_key,customer_content_hash,reconciliation_status,diff_ids,final_diff_ids,feature_ids,reverse_statuses,subject_card_slugs,artifact_paths,diagnostics\n"
    write_metadata_rebase_inputs(
        tmp_path,
        metadata_header
        + "v,CMI-1,k,full,xml-bsl,Document,Demo,Document.Demo,attribute,A,path,modified,non_typical,,,,,,x,V8D-1,BF-001,,,,\n"
        + "v,CMI-2,k,full,xml-bsl,Document,Demo,Document.Demo,resource,R,path,modified,non_typical,,,,,,x,V8D-1;V8D-2,BF-001,,,,\n",
        "decision_id,workitem_id,diff_id,scenario_id,decision,confidence,rationale,evidence_ref,decided_by,decided_at\n"
        "D-1,RM-1,V8D-1,BF-001,confirmed_in_scenario,medium,,,,\n"
        "D-2,RM-2,V8D-2,BF-002,confirmed_in_scenario,medium,,,,\n",
        "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes,reverse_status,reverse_confidence,reverse_scenario_id,final_feature_id,final_status,final_action,blocking_reason\n"
        "V8D-1,s,M,p,Document,Document.Demo,bsl,BF-001,c,medium,s,,,,,,,,,,\n"
        "V8D-2,s,M,p,Document,Document.Demo,bsl,BF-002,c,medium,s,,,,,,,,,,\n",
    )

    rows = build_metadata_rebase_candidates(tmp_path)

    assert len(rows) == 1
    assert rows[0]["scenario_votes"] == "BF-001:1;BF-002:1"
    assert rows[0]["confidence_bucket"] == "weak"
    assert rows[0]["needs_bsl_review"] == "true"
    assert rows[0]["structural_part_kinds"] == "attribute;resource"


def test_metadata_rebase_excludes_form_and_template_only_objects(tmp_path) -> None:
    metadata_header = "schema_version,item_id,item_key,comparison_mode,source_format,metadata_kind,metadata_name,metadata_full_name,part_kind,part_name,part_path,change_type,status,typical_item_key,typical_content_hash,customer_item_key,customer_content_hash,reconciliation_status,diff_ids,final_diff_ids,feature_ids,reverse_statuses,subject_card_slugs,artifact_paths,diagnostics\n"
    write_metadata_rebase_inputs(
        tmp_path,
        metadata_header
        + "v,CMI-1,k,full,xml-bsl,Report,Alcohol,Report.Alcohol,form,ОсновнаяФорма,path,modified,non_typical,,,,,,x,V8D-1,BF-001,,,,\n"
        + "v,CMI-2,k,full,xml-bsl,Report,Alcohol,Report.Alcohol,template,Макет,path,modified,non_typical,,,,,,x,V8D-2,BF-001,,,,\n",
        "decision_id,workitem_id,diff_id,scenario_id,decision,confidence,rationale,evidence_ref,decided_by,decided_at\n"
        "D-1,RM-1,V8D-1,BF-001,confirmed_in_scenario,medium,,,,\n"
        "D-2,RM-2,V8D-2,BF-001,confirmed_in_scenario,medium,,,,\n",
        "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes,reverse_status,reverse_confidence,reverse_scenario_id,final_feature_id,final_status,final_action,blocking_reason\n"
        "V8D-1,s,M,p,Report,Report.Alcohol,form,BF-001,c,medium,s,,,,,,,,,,\n"
        "V8D-2,s,M,p,Report,Report.Alcohol,template,BF-001,c,medium,s,,,,,,,,,,\n",
    )

    result = build_metadata_rebase(tmp_path)

    assert result["rows"] == 0
    assert result["excluded_rows"] == 2
    excluded = (tmp_path / "analysis/functional-gaps/metadata-rebase/excluded-parts.csv").read_text(encoding="utf-8")
    assert "Report.Alcohol" in excluded
    assert "nonstructural_part" in excluded


def test_metadata_rebase_preserves_parser_only_no_votes(tmp_path) -> None:
    metadata_header = "schema_version,item_id,item_key,comparison_mode,source_format,metadata_kind,metadata_name,metadata_full_name,part_kind,part_name,part_path,change_type,status,typical_item_key,typical_content_hash,customer_item_key,customer_content_hash,reconciliation_status,diff_ids,final_diff_ids,feature_ids,reverse_statuses,subject_card_slugs,artifact_paths,diagnostics\n"
    write_metadata_rebase_inputs(
        tmp_path,
        metadata_header
        + "v,CMI-1,k,full,xml-bsl,Catalog,ParserOnly,Catalog.ParserOnly,object,,path,added,non_typical,,,,,,,,,,,,\n",
    )

    rows = build_metadata_rebase_candidates(tmp_path)

    assert rows[0]["metadata_object"] == "Catalog.ParserOnly"
    assert rows[0]["confidence_bucket"] == "no_votes"
    assert rows[0]["needs_bsl_review"] == "true"
    assert "parser_only_no_final_diff_ids" in rows[0]["notes"]


def test_metadata_rebase_keeps_parser_only_structural_part(tmp_path) -> None:
    metadata_header = "schema_version,item_id,item_key,comparison_mode,source_format,metadata_kind,metadata_name,metadata_full_name,part_kind,part_name,part_path,change_type,status,typical_item_key,typical_content_hash,customer_item_key,customer_content_hash,reconciliation_status,diff_ids,final_diff_ids,feature_ids,reverse_statuses,subject_card_slugs,artifact_paths,diagnostics\n"
    write_metadata_rebase_inputs(
        tmp_path,
        metadata_header
        + "v,CMI-1,k,full,xml-bsl,InformationRegister,Demo,InformationRegister.Demo,dimension,Организация,path,added,non_typical,,,,,,,,,,,,\n",
    )

    rows = build_metadata_rebase_candidates(tmp_path)

    assert len(rows) == 1
    assert rows[0]["metadata_object"] == "InformationRegister.Demo"
    assert rows[0]["structural_part_kinds"] == "dimension"
    assert rows[0]["confidence_bucket"] == "no_votes"


def test_metadata_rebase_treats_role_rights_as_structural_driver(tmp_path) -> None:
    metadata_header = "schema_version,item_id,item_key,comparison_mode,source_format,metadata_kind,metadata_name,metadata_full_name,part_kind,part_name,part_path,change_type,status,typical_item_key,typical_content_hash,customer_item_key,customer_content_hash,reconciliation_status,diff_ids,final_diff_ids,feature_ids,reverse_statuses,subject_card_slugs,artifact_paths,diagnostics\n"
    write_metadata_rebase_inputs(
        tmp_path,
        metadata_header
        + "v,CMI-1,k,full,v8unpack,Role,OnlyView,Role.OnlyView,unknown,Rights,Roles/OnlyView/Ext/Rights.xml,added,non_typical,,,,,,x,V8D-1,BF-008,,bf-008-prava-dostupa-i-roli,,,\n",
    )

    rows = build_metadata_rebase_candidates(tmp_path)

    assert len(rows) == 1
    assert rows[0]["metadata_object"] == "Role.OnlyView"
    assert rows[0]["structural_part_kinds"] == "rights"
    assert rows[0]["confidence_bucket"] == "no_votes"


def test_metadata_rebase_writes_report_without_touching_cards_or_outputs(tmp_path) -> None:
    metadata_header = "schema_version,item_id,item_key,comparison_mode,source_format,metadata_kind,metadata_name,metadata_full_name,part_kind,part_name,part_path,change_type,status,typical_item_key,typical_content_hash,customer_item_key,customer_content_hash,reconciliation_status,diff_ids,final_diff_ids,feature_ids,reverse_statuses,subject_card_slugs,artifact_paths,diagnostics\n"
    write_metadata_rebase_inputs(
        tmp_path,
        metadata_header
        + "v,CMI-1,k,full,xml-bsl,Role,Demo,Role.Demo,object,,path,added,non_typical,,,,,,x,V8D-1,BF-008,,bf-008-prava-dostupa-i-roli,,,\n",
        "decision_id,workitem_id,diff_id,scenario_id,decision,confidence,rationale,evidence_ref,decided_by,decided_at\n"
        "D-1,RM-1,V8D-1,BF-008,confirmed_in_scenario,medium,,,,\n",
        "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes,reverse_status,reverse_confidence,reverse_scenario_id,final_feature_id,final_status,final_action,blocking_reason\n"
        "V8D-1,s,A,Role/Demo/Role.json,Role,Role.Demo,metadata,BF-008,c,medium,s,,,,,,,,,,\n",
    )
    card_path = tmp_path / "analysis/functional-gaps/cards/demo/gap-card.json"
    card_path.parent.mkdir(parents=True)
    card_path.write_text('{"keep": true}\n', encoding="utf-8")
    output_path = tmp_path / "outputs/functional-gap-map.json"
    output_path.parent.mkdir()
    output_path.write_text('{"keep": true}\n', encoding="utf-8")

    result = build_metadata_rebase(tmp_path)

    assert result["rows"] == 1
    assert result["card_support_rows"] == 1
    assert card_path.read_text(encoding="utf-8") == '{"keep": true}\n'
    assert output_path.read_text(encoding="utf-8") == '{"keep": true}\n'
    assert (tmp_path / "analysis/functional-gaps/metadata-rebase/candidates.csv").read_text(encoding="utf-8").splitlines()[0] == FUNCTIONAL_GAP_METADATA_REBASE_HEADER
    assert (tmp_path / "analysis/functional-gaps/metadata-rebase/summary.md").exists()
    assert (tmp_path / "analysis/functional-gaps/metadata-rebase/excluded-parts.csv").exists()
    assert (tmp_path / "analysis/functional-gaps/metadata-rebase/card-support.csv").exists()
    assert (tmp_path / "analysis/functional-gaps/metadata-rebase/object-coverage.csv").read_text(encoding="utf-8").splitlines()[0] == FUNCTIONAL_GAP_METADATA_REBASE_OBJECT_COVERAGE_HEADER
    assert (tmp_path / "analysis/functional-gaps/metadata-rebase/unresolved-candidates.csv").read_text(encoding="utf-8").splitlines()[0] == FUNCTIONAL_GAP_METADATA_REBASE_UNRESOLVED_HEADER


def test_metadata_rebase_marks_actual_card_object_coverage(tmp_path) -> None:
    metadata_header = "schema_version,item_id,item_key,comparison_mode,source_format,metadata_kind,metadata_name,metadata_full_name,part_kind,part_name,part_path,change_type,status,typical_item_key,typical_content_hash,customer_item_key,customer_content_hash,reconciliation_status,diff_ids,final_diff_ids,feature_ids,reverse_statuses,subject_card_slugs,artifact_paths,diagnostics\n"
    write_metadata_rebase_inputs(
        tmp_path,
        metadata_header
        + "v,CMI-1,k,full,xml-bsl,Catalog,Covered,Catalog.Covered,object,,path,added,non_typical,,,,,,x,V8D-1,BF-001,,demo,,,\n"
        + "v,CMI-2,k,full,xml-bsl,Catalog,Missing,Catalog.Missing,object,,path,added,non_typical,,,,,,x,V8D-2,BF-001,,demo,,,\n",
    )
    contours = tmp_path / "analysis/subject-cards/contours.csv"
    contours.parent.mkdir(parents=True, exist_ok=True)
    contours.write_text(
        "contour_id,slug,title,contour_type,linked_features,primary_objects\n"
        "SCN-1,demo,Demo,business_process,BF-001,Catalog.Covered\n",
        encoding="utf-8",
    )

    result = build_metadata_rebase(tmp_path)

    assert result["covered_rows"] == 1
    assert result["unresolved_rows"] == 1
    unresolved = (tmp_path / "analysis/functional-gaps/metadata-rebase/unresolved-candidates.csv").read_text(encoding="utf-8")
    assert "Catalog.Missing" in unresolved
    assert "Catalog.Covered" not in unresolved


def test_metadata_rebase_card_support_filters_functional_gap_outputs(tmp_path) -> None:
    cards_root = tmp_path / "analysis/functional-gaps/cards"
    for slug in ("supported", "noise"):
        card_dir = cards_root / slug
        card_dir.mkdir(parents=True)
        card_dir.joinpath("gap-card.json").write_text(
            json.dumps(
                {
                    "subject_card_slug": slug,
                    "title": slug,
                    "status": "ready_for_review",
                    "gap_readiness": "needs_target_release_check",
                    "selected_decision": "adapt",
                    "hypotheses": [{}],
                    "target_release": "demo",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        card_dir.joinpath("checks.csv").write_text("check_id,status,blocking,question,source,result\n", encoding="utf-8")
        card_dir.joinpath("hypotheses.csv").write_text("hypothesis_id\n", encoding="utf-8")
        card_dir.joinpath("target-findings.csv").write_text("finding_id\n", encoding="utf-8")
        card_dir.joinpath("object-mapping.csv").write_text("mapping_id\n", encoding="utf-8")

    support_dir = tmp_path / "analysis/functional-gaps/metadata-rebase"
    support_dir.mkdir(parents=True)
    functional_gaps.write_csv_rows(
        support_dir / "card-support.csv",
        FUNCTIONAL_GAP_METADATA_REBASE_CARD_SUPPORT_HEADER,
        [
            {
                "subject_card_slug": "supported",
                "source_objects_count": "1",
                "structural_matches_count": "1",
                "excluded_only_count": "0",
                "rebuild_action": "keep_rebuild_from_structural_candidates",
                "structural_objects": "Catalog.Демо",
                "excluded_only_objects": "",
                "notes": "",
            },
            {
                "subject_card_slug": "noise",
                "source_objects_count": "1",
                "structural_matches_count": "0",
                "excluded_only_count": "1",
                "rebuild_action": "remove_or_merge_form_only",
                "structural_objects": "",
                "excluded_only_objects": "Report.Шум",
                "notes": "",
            },
        ],
    )

    refresh_index(tmp_path)
    build_functional_gap_map(tmp_path)

    index_text = (tmp_path / "analysis/functional-gaps/index.csv").read_text(encoding="utf-8")
    map_text = (tmp_path / "outputs/functional-gap-map.json").read_text(encoding="utf-8")
    assert "supported" in index_text
    assert "noise" not in index_text
    assert "supported" in map_text
    assert "noise" not in map_text
