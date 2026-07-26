from __future__ import annotations

import json
import tomllib
from copy import deepcopy
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any

from . import diffs, indexes, mrq, sources, workflow
from .contracts import atomic_bytes, atomic_json, canonical_json, confined, reject_secrets, repository_lock, sha256


class ApplicationService:
    def __init__(self, repo: Path, rlm_executable: str | None = None, connections: dict[str, dict[str, Any]] | None = None, upload_drafts: Path | None = None, routing_previews: Path | None = None):
        self.repo = repo.resolve()
        self.rlm_executable = rlm_executable or indexes.discover_executable(self.repo)
        self.connections = connections
        self.upload_drafts = upload_drafts
        self.routing_previews = routing_previews
        workflow.validate_workflow(self.repo)

    def snapshot(self, *, deep: bool = True) -> dict[str, Any]:
        return workflow.status(self.repo, deep=deep)

    def registry(
        self,
        name: str,
        offset: int = 0,
        limit: int = 100,
        expected_generation: str = "",
    ) -> dict[str, Any]:
        allowed = {"diff-inventory", "target-coverage", "mrq", "extension-diff", "extension-dependencies", "extension-path-coverage", "extension-physical-diff"}
        if name not in allowed or offset < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid registry page")
        generation_id = ""
        if name == "mrq":
            state = mrq.active(self.repo)
            generation_id = str(state["pointer"].get("diff_generation_id", ""))
            if expected_generation and expected_generation != generation_id:
                raise RuntimeError("stale diff generation")
            rows = state["mrq.jsonl"][offset : offset + limit + 1]
        else:
            pointer = json.loads((self.repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
            generation_id = str(pointer.get("generation_id", ""))
            if expected_generation and expected_generation != generation_id:
                raise RuntimeError("stale diff generation")
            if not generation_id:
                rows = []
            else:
                suffix = ".jsonl" if name in {"extension-diff", "extension-dependencies"} else ".csv"
                path = self.repo / "analysis/indexes/generations" / generation_id / f"{name}{suffix}"
                if not path.is_file() and name.startswith("extension-") and pointer.get("schema_version") == "1":
                    rows = []
                elif suffix == ".jsonl":
                    with path.open(encoding="utf-8") as stream:
                        rows = [
                            json.loads(line)
                            for line in islice((line for line in stream if line.strip()), offset, offset + limit + 1)
                        ]
                else:
                    import csv
                    with path.open(encoding="utf-8", newline="") as stream:
                        rows = list(islice(csv.DictReader(stream), offset, offset + limit + 1))
        return {
            "diff_generation_id": generation_id,
            "offset": offset,
            "limit": limit,
            "items": rows[:limit],
            "has_more": len(rows) > limit,
        }

    def next(self) -> dict[str, Any] | None:
        snapshot = self.snapshot()
        canonical = workflow.next_work(self.repo)
        if canonical and canonical["action"] in {"mrq.discover-next", "mrq.decide-next"}:
            paths = (canonical.get("work_unit") or {}).get("allowed_paths", [])
            roles = ("vendor_baseline", "target_cf") if canonical["action"] == "mrq.discover-next" else ("target_cf", "next_vendor")
            required = indexes.required_component_ids(self.repo, paths, roles)
            if required:
                if not self.rlm_executable:
                    return {**canonical, "job_id": "index-sources", "domain_action": canonical["action"], "action": "indexes.build", "blocker": {"code": "indexes.tool_missing", "message": "rlm-bsl-index is unavailable", "action": "indexes.build"}}
                probe = lambda path: indexes.cli_probe(self.rlm_executable, path)
                pending = [item for item in indexes.statuses(self.repo, probe=probe) if item["component_id"] in required and item["status"] not in {"ready", "not_indexable"}]
                if pending:
                    work_unit = {**(canonical.get("work_unit") or {}), "component_ids": [item["component_id"] for item in pending], "index_keys": {item["component_id"]: item["index_key"] for item in pending}}
                    return {**canonical, "job_id": "index-sources", "domain_action": canonical["action"], "action": "indexes.build", "blocker": {"code": "indexes.missing", "message": f"{len(pending)} source component indexes require ensure", "action": "indexes.build"}, "work_unit": work_unit}
        return canonical

    def index_statuses(self) -> list[dict[str, Any]]:
        probe = (lambda path: indexes.cli_probe(self.rlm_executable, path)) if self.rlm_executable else None
        return indexes.statuses(self.repo, probe=probe)

    def workflow_configuration(self) -> dict[str, Any]:
        manifest = workflow.validate_workflow(self.repo)
        return {"manifest_fingerprint": workflow.workflow_fingerprint(self.repo), "jobs": [{"id": job["id"], "needs": job["needs"]} for job in manifest["jobs"]], "steps": workflow.step_configurations(self.repo)}

    def preview_step_patch(self, payload: dict[str, Any], state_base: Path | None = None) -> dict[str, Any]:
        if set(payload) != {"step_id", "parameters", "expected_manifest_fingerprint"}:
            raise ValueError("invalid workflow step patch")
        return workflow.preview_step_patch(self.repo, payload["step_id"], payload["parameters"], payload["expected_manifest_fingerprint"], state_base)

    def apply(self, operation: str, payload: dict[str, Any], expected_fingerprint: str, cancelled: callable | None = None, *, staged: dict[str, dict[str, Any]] | None = None, fence: callable | None = None) -> dict[str, Any]:
        reject_secrets(payload, "operation payload")
        current = workflow.state_fingerprint(self.repo)
        if expected_fingerprint != current:
            raise RuntimeError("stale workflow fingerprint")
        self._expected_fingerprint = expected_fingerprint
        self._cancelled = cancelled
        self._staged = staged
        self._fence = fence
        handlers = {
            "project.configure": self._configure,
            "sources.configure": lambda value: self._locked(self._configure_sources, value),
            "sources.acquire": self._acquire_sources,
            "indexes.ensure": self._ensure_indexes,
            "indexes.build": self._ensure_indexes,
            "diff.build": self._build_diffs,
            "mrq.propose": self._mrq_propose,
            "mrq.decide": self._mrq_decide,
            "mrq.review": self._mrq_review,
            "mrq.approve-noise": self._mrq_approve_noise,
            "mrq.restructure": self._mrq_restructure,
            "mrq.revalidate-unchanged": self._mrq_revalidate_unchanged,
            # Внутренняя атомарная граница применения существующей операции
            # ``mrq.discover-next``: публикует одно каноническое MRQ-поколение из
            # одобренного пакета групп. Не входит в ``OPERATION_CATALOG`` и не
            # является девятой операцией workflow-каталога; вызывается только
            # диспетчером после явного одобрения пакета локальным пользователем.
            # ``mrq.publish`` сам берёт репозиторную блокировку, поэтому здесь
            # без внешнего ``_locked`` (иначе вложенная блокировка ``fcntl``).
            "mrq.publish-source-batch": self._mrq_publish_source_batch,
            "projections.build": lambda value: self._locked(self._build_projections, value),
            "workflow.patch-step": lambda value: self._locked(self._patch_step, value),
            "workflow.verify": self._verify_workflow,
        }
        try:
            handler = handlers[operation]
        except KeyError as exc:
            raise ValueError(f"unsupported typed operation: {operation}") from exc
        return handler(payload)

    def _verify_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload:
            raise ValueError("workflow.verify takes no parameters")
        from .doctor import check
        result = check(self.repo, strict=True)
        if not result["ok"]:
            raise RuntimeError(json.dumps({"code": "workflow.verify.failed", "blockers": result["failures"]}, ensure_ascii=False, sort_keys=True))
        return result

    def _locked(self, handler, payload: dict[str, Any]) -> dict[str, Any]:
        with repository_lock(self.repo):
            if workflow.state_fingerprint(self.repo) != self._expected_fingerprint:
                raise RuntimeError("stale workflow fingerprint")
            result = handler(payload)
            return {**result, "workflow_fingerprint": workflow.state_fingerprint(self.repo)}

    def _configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"product", "baseline_version", "target_version", "next_vendor_version", "description"}
        if set(payload) - allowed or any(not str(payload.get(key, "")).strip() for key in ("product", "baseline_version", "target_version", "next_vendor_version")):
            raise ValueError("invalid project configuration patch")
        with repository_lock(self.repo):
            if workflow.state_fingerprint(self.repo) != self._expected_fingerprint:
                raise RuntimeError("stale workflow fingerprint")
            path = self.repo / "project.toml"; text = path.read_text(encoding="utf-8")
            for key, value in payload.items():
                import re
                escaped = json.dumps(str(value), ensure_ascii=False)
                text, count = re.subn(rf"(?m)^{re.escape(key)}\s*=.*$", f"{key} = {escaped}", text, count=1)
                if count != 1: raise ValueError(f"project field not found: {key}")
            atomic_bytes(path, text.encode())
        return {"operation": "project.configure", "project_fingerprint": "sha256:" + sha256(path.read_bytes())}

    def _acquire_sources(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) - {"timeout_seconds", "source_routing_preview_id", "routing_plan_fingerprint"}:
            raise ValueError("invalid source acquisition payload")
        if not self.connections:
            raise RuntimeError("current user-scope connection profiles are unavailable")
        platform_paths = {str(profile.get("platform_path", "")) for profile in self.connections.values()}
        if len(platform_paths) != 1 or not next(iter(platform_paths)):
            raise ValueError("one generation-wide platform path is required")
        platform = Path(next(iter(platform_paths)))
        preview_id = str(payload.get("source_routing_preview_id", ""))
        if not self.routing_previews or not preview_id or Path(preview_id).name != preview_id:
            raise RuntimeError("routing_preview_stale")
        preview_path = self.routing_previews / f"{preview_id}.json"
        if not preview_path.is_file():
            raise RuntimeError("routing_preview_stale")
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
        from .user_state import workspace_id
        if preview.get("project_id") != workspace_id(self.repo) or preview.get("status") != "ready" or preview.get("routing_plan_fingerprint") != payload.get("routing_plan_fingerprint"):
            raise RuntimeError("routing_preview_stale")
        if datetime.fromisoformat(str(preview.get("expires_at", ""))) <= datetime.now(timezone.utc):
            raise RuntimeError("routing_preview_stale")
        result = sources.acquire(self.repo, platform, self.connections, routing_preview=preview, timeout_seconds=int(payload.get("timeout_seconds", 1800)), upload_drafts=self.upload_drafts, cancelled=self._cancelled, activate=self._staged is None)
        if self._staged is not None:
            self._staged["source"] = result
        return result

    def _configure_sources(self, payload: dict[str, Any]) -> dict[str, Any]:
        external_keys = {"external_artifact_preview_id", "selected_entries", "expected_declaration_fingerprint", "expected_draft_fingerprint", "confirm"}
        if set(payload) == external_keys:
            if payload["confirm"] is not True or self.upload_drafts is None:
                raise ValueError("invalid external artifact source setup patch")
            from .external_folder import PreviewStore
            store = PreviewStore(self.repo, self.upload_drafts.parent / "external-folder-previews", self.upload_drafts)
            return {"operation": "sources.configure", "comparison_epoch_changed": False, **store.confirm(payload["external_artifact_preview_id"], payload["selected_entries"], workflow_fingerprint=self._expected_fingerprint, expected_declaration_fingerprint=payload["expected_declaration_fingerprint"], expected_draft_fingerprint=payload["expected_draft_fingerprint"])}
        if set(payload) != {"acquisition_profile", "connection_profiles", "expected_manifest_fingerprint", "confirm_new_epoch"} or payload["acquisition_profile"] not in sources.PROFILES:
            raise ValueError("invalid source setup patch")
        path = self.repo / "research/infobases.toml"
        if payload["expected_manifest_fingerprint"] != "sha256:" + sha256(path.read_bytes()):
            raise RuntimeError("stale source setup fingerprint")
        if not payload["confirm_new_epoch"]:
            raise ValueError("source profile changes require explicit comparison-epoch confirmation")
        bindings, _artifacts = sources.load_contract(self.repo); roles = bindings.get("roles", {}); profiles = payload["connection_profiles"]
        import re
        if tuple(roles) != sources.ROLES or set(profiles) != set(sources.ROLES) or any(not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) is None for value in profiles.values()):
            raise ValueError("exactly three safe connection-profile bindings are required")
        lines = ['schema_version = "1"', f'acquisition_profile = {json.dumps(payload["acquisition_profile"])}', ""]
        for role in sources.ROLES:
            item = roles[role]
            lines.extend((f"[roles.{role}]", f'connection_profile = {json.dumps(profiles[role])}', f'configuration_name = {json.dumps(item["configuration_name"], ensure_ascii=False)}', f'root_uuid = {json.dumps(item["root_uuid"])}', f'version = {json.dumps(item["version"])}', ""))
        atomic_bytes(path, ("\n".join(lines)).encode())
        return {"operation": "sources.configure", "manifest_fingerprint": "sha256:" + sha256(path.read_bytes()), "comparison_epoch_changed": True}

    def _ensure_indexes(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) - {"component_ids", "mode", "confirmed"}:
            raise ValueError("invalid index operation payload")
        mode = payload.get("mode", "ensure")
        if mode not in {"ensure", "rebuild"}:
            raise ValueError("invalid index operation mode")
        if not self.rlm_executable:
            raise RuntimeError("rlm-bsl-index is unavailable in PATH")
        indexes.validate_engine_version(self.repo, self.rlm_executable)
        probe = lambda path: indexes.cli_probe(self.rlm_executable, path)
        builder = lambda path, component_id: indexes.cli_build(self.rlm_executable, path, component_id)
        rows = indexes.ensure(self.repo, builder, selected=payload.get("component_ids"), rebuild=mode == "rebuild", confirmed=bool(payload.get("confirmed")), probe=probe)
        return {"components": rows}

    def _build_diffs(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload:
            raise ValueError("diff.build takes no parameters")
        from .stage_recompute import active_pointers
        pointers = active_pointers(self.repo) if self._staged is None else {}
        pointer = self._staged.get("source") if self._staged and self._staged.get("source") else pointers["source"]
        if self._staged is None and sources.validate_active(self.repo, deep=True) != pointer:
            raise RuntimeError("active source generation changed during validation")
        prior_pointer = pointers["mrq"] if self._staged is None else active_pointers(self.repo)["mrq"]
        result = diffs.build(self.repo, pointer, cancelled=self._cancelled, activate=self._staged is None)
        if self._staged is not None:
            self._staged["diff"] = result
            return result
        prior_generation = prior_pointer.get("canonical_generation_id")
        analyzer = json.loads((self.repo / "analysis/indexes/generations" / result["generation_id"] / "extension-analyzer-manifest.json").read_text(encoding="utf-8"))
        new_epoch = mrq.comparison_epoch_fingerprint(pointer, analyzer)
        if prior_generation:
            prior_manifest = json.loads((self.repo / "research/generations" / prior_generation / "manifest.json").read_text(encoding="utf-8"))
        if not prior_generation or prior_manifest.get("comparison_epoch_fingerprint") != new_epoch:
            mrq.publish(self.repo, {name: [] for name in mrq.FILES}, pointer["generation_id"], result["generation_id"], pointer, prior_generation)
        return result

    def _mrq_revalidate_unchanged(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"actor", "rationale", "timestamp"}:
            raise ValueError("invalid unchanged MRQ revalidation")
        from .stage_recompute import active_pointers
        pointers = active_pointers(self.repo)
        source = self._staged.get("source") if self._staged and self._staged.get("source") else pointers["source"]
        diff = self._staged.get("diff") if self._staged and self._staged.get("diff") else pointers["diff"]
        result = mrq.revalidate_unchanged(self.repo, source, diff, payload["actor"], payload["rationale"], payload["timestamp"], activate=self._staged is None)
        if self._staged is not None:
            self._staged["mrq"] = {
                key: result[key]
                for key in ("schema_version", "canonical_generation_id", "source_generation_id", "diff_generation_id")
            }
        return result

    def _mrq_rows(self) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        state = mrq.active(self.repo)
        return state["pointer"], {name: deepcopy(state[name]) for name in mrq.FILES}

    def _publish_mrq(self, pointer: dict[str, Any], rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        source = json.loads((self.repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
        diff = json.loads((self.repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
        return mrq.publish(
            self.repo,
            rows,
            source["generation_id"],
            diff["generation_id"],
            source,
            pointer.get("canonical_generation_id"),
            fence=getattr(self, "_fence", None),
        )

    def _mrq_propose(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"semantic_key", "title", "stable_diff_ids", "supporting_diff_ids", "evidence", "business_meaning", "scope", "confidence", "rationale"}
        if set(payload) != required:
            raise ValueError("invalid MRQ proposal")
        pointer, rows = self._mrq_rows()
        source = json.loads((self.repo / "research/active-source-generation.json").read_text(encoding="utf-8"))["generation_id"]
        diff = json.loads((self.repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))["generation_id"]
        identifier = mrq.propose(rows, payload["semantic_key"], payload["title"], source, diff, payload["stable_diff_ids"], payload["supporting_diff_ids"], payload["evidence"], payload["business_meaning"], payload["scope"], payload["confidence"], payload["rationale"])
        result = self._publish_mrq(pointer, rows); result["mrq_id"] = identifier
        return result

    def _mrq_publish_source_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Атомарная внутренняя граница применения ``mrq.discover-next``.

        Принимает одобренный локальным пользователем пакет групп и публикует
        ровно одно каноническое MRQ-поколение, либо ничего. Это не девятая
        операция workflow-каталога, а детерминированная атомарная граница:
        повторная проверка под существующей репозиторной блокировкой
        гарантирует полное непересекающееся покрытие и свободное владение.
        """

        required = {"approved_noise", "group_proposals"}
        if set(payload) != required:
            raise ValueError("invalid MRQ source batch payload")
        noise_entries = payload["approved_noise"]
        group_proposals = payload["group_proposals"]
        if (
            not isinstance(noise_entries, list)
            or not isinstance(group_proposals, list)
            or not (noise_entries or group_proposals)
        ):
            raise ValueError("MRQ source batch requires at least one reviewed item")
        for proposal in group_proposals:
            required_fields = {"semantic_key", "title", "stable_diff_ids", "supporting_diff_ids", "evidence", "business_meaning", "scope", "confidence", "rationale"}
            if set(proposal) != required_fields:
                raise ValueError("invalid MRQ batch group proposal")
        for entry in noise_entries:
            required_noise = {"stable_diff_id", "actor", "rationale", "evidence", "timestamp"}
            if set(entry) != required_noise:
                raise ValueError("invalid MRQ batch approved noise entry")
        pointer, rows = self._mrq_rows()
        source = json.loads((self.repo / "research/active-source-generation.json").read_text(encoding="utf-8"))["generation_id"]
        diff = json.loads((self.repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))["generation_id"]
        # повторная проверка свободного владения до эффекта: ни один DIF пакета
        # не должен уже иметь первичную диспозицию в текущем поколении
        active_ids = {item["mrq_id"] for item in rows["mrq.jsonl"] if item.get("state") != "superseded"}
        already_owned = {item["stable_diff_id"] for item in rows["dispositions.jsonl"] if item.get("primary") and (not item.get("mrq_id") or item.get("mrq_id") in active_ids)}
        for proposal in group_proposals:
            clash = already_owned.intersection(proposal["stable_diff_ids"])
            if clash:
                raise ValueError(f"customer DIF already has a primary disposition: {sorted(clash)[0]}")
        # пересечение внутри пакета: один DIF не может быть primary в двух группах
        seen_primary: set[str] = set()
        for proposal in group_proposals:
            clash = seen_primary.intersection(proposal["stable_diff_ids"])
            if clash:
                raise ValueError(f"batch group proposals overlap on primary DIF: {sorted(clash)[0]}")
            seen_primary.update(proposal["stable_diff_ids"])
        # шум диспозиций тоже не должен пересекаться с primary DIF пакета
        for entry in noise_entries:
            if entry["stable_diff_id"] in seen_primary:
                raise ValueError(f"approved noise conflicts with primary DIF: {entry['stable_diff_id']}")
        import csv
        inventory_path = self.repo / "analysis/indexes/generations" / diff / "diff-inventory.csv"
        with inventory_path.open(encoding="utf-8", newline="") as stream:
            customer_ids = {
                row["stable_diff_id"]
                for row in csv.DictReader(stream)
                if row.get("before_role") == "vendor_baseline" and row.get("after_role") == "target_cf"
            }
        noise_ids = {entry["stable_diff_id"] for entry in noise_entries}
        covered = already_owned | seen_primary | noise_ids
        if covered != customer_ids:
            missing = sorted(customer_ids - covered)
            extra = sorted(covered - customer_ids)
            detail = missing[0] if missing else extra[0]
            raise ValueError(f"MRQ source batch does not form complete customer DIF coverage: {detail}")
        published_mrq_ids: list[str] = []
        for proposal in group_proposals:
            identifier = mrq.propose(rows, proposal["semantic_key"], proposal["title"], source, diff, proposal["stable_diff_ids"], proposal["supporting_diff_ids"], proposal["evidence"], proposal["business_meaning"], proposal["scope"], proposal["confidence"], proposal["rationale"])
            published_mrq_ids.append(identifier)
        for entry in noise_entries:
            mrq.approve_noise(rows, entry["stable_diff_id"], entry["actor"], entry["rationale"], entry["evidence"], source, diff, entry["timestamp"], pointer.get("canonical_generation_id"))
        result = self._publish_mrq(pointer, rows)
        result["mrq_ids"] = published_mrq_ids
        result["operation"] = "mrq.publish-source-batch"
        return result

    def _mrq_decide(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"mrq_id", "decision", "target_evidence", "target_coverage", "residual_gap", "target_solution", "rationale", "acceptance_criteria", "risk", "open_questions"}
        if set(payload) != required:
            raise ValueError("invalid MRQ decision")
        pointer, rows = self._mrq_rows()
        mrq.decide(rows, payload["mrq_id"], payload["decision"], payload["target_evidence"], payload["target_coverage"], payload["residual_gap"], payload["target_solution"], payload["rationale"], payload["acceptance_criteria"], payload["risk"], payload["open_questions"])
        return self._publish_mrq(pointer, rows)

    def _mrq_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"mrq_id", "event", "actor", "rationale", "evidence", "timestamp"}
        if set(payload) != required:
            raise ValueError("invalid MRQ review")
        pointer, rows = self._mrq_rows()
        source = json.loads((self.repo / "research/active-source-generation.json").read_text(encoding="utf-8"))["generation_id"]
        diff = json.loads((self.repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))["generation_id"]
        mrq.review(rows, payload["mrq_id"], payload["event"], payload["actor"], payload["rationale"], payload["evidence"], source, diff, payload["timestamp"], pointer.get("canonical_generation_id"))
        return self._publish_mrq(pointer, rows)

    def _mrq_approve_noise(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"stable_diff_id", "actor", "rationale", "evidence", "timestamp"}
        if set(payload) != required:
            raise ValueError("invalid approved-noise review")
        pointer, rows = self._mrq_rows()
        source = json.loads((self.repo / "research/active-source-generation.json").read_text(encoding="utf-8"))["generation_id"]
        diff = json.loads((self.repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))["generation_id"]
        mrq.approve_noise(rows, payload["stable_diff_id"], payload["actor"], payload["rationale"], payload["evidence"], source, diff, payload["timestamp"], pointer.get("canonical_generation_id"))
        return self._publish_mrq(pointer, rows)

    def _mrq_restructure(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"kind", "source_ids", "target_proposals", "rationale", "evidence", "actor"}
        if set(payload) != required:
            raise ValueError("invalid MRQ restructuring payload")
        pointer, rows = self._mrq_rows()
        source = json.loads((self.repo / "research/active-source-generation.json").read_text(encoding="utf-8"))["generation_id"]
        diff = json.loads((self.repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))["generation_id"]
        targets = mrq.restructure(rows, payload["kind"], payload["source_ids"], payload["target_proposals"], payload["rationale"], payload["evidence"], payload["actor"], source, diff)
        result = self._publish_mrq(pointer, rows); result["target_ids"] = targets
        return result

    def _build_projections(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload:
            raise ValueError("projections.build takes no parameters")
        state = mrq.active(self.repo)
        if not state["pointer"].get("canonical_generation_id"):
            raise ValueError("canonical MRQ generation is absent")
        projections = workflow.projection_value(state)
        atomic_json(self.repo / "outputs/projections.json", projections)
        return projections

    def _patch_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"step_id", "parameters", "expected_manifest_fingerprint"}:
            raise ValueError("invalid workflow step patch")
        path = self.repo / "research/workflow.toml"
        # TOML has no stdlib writer; serialize only the already validated fixed step.
        text = path.read_text(encoding="utf-8")
        preview = workflow.preview_step_patch(self.repo, payload["step_id"], payload["parameters"], payload["expected_manifest_fingerprint"])
        marker_at = text.find(f'{{ id = {json.dumps(payload["step_id"])}, operation =')
        if marker_at < 0:
            raise ValueError("workflow step not found")
        start = marker_at
        depth = 0
        end = -1
        for index in range(start, len(text)):
            if text[index] in "[{":
                depth += 1
            elif text[index] in "]}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if start < 0 or end < 0:
            raise ValueError("workflow step is malformed")
        operation = preview["operation"]
        if set(payload["parameters"]) - workflow.PARAMETERS.get(operation, set()):
            raise ValueError("unsupported workflow step parameter")

        def toml_value(value: Any) -> str:
            if isinstance(value, str):
                return json.dumps(value, ensure_ascii=False)
            if isinstance(value, bool):
                return "true" if value else "false"
            if isinstance(value, int):
                return str(value)
            if isinstance(value, list):
                return "[" + ", ".join(toml_value(item) for item in value) + "]"
            if isinstance(value, dict):
                return "{ " + ", ".join(f"{key} = {toml_value(item)}" for key, item in value.items()) + " }"
            raise ValueError("unsupported workflow step value")

        updated = "{ " + ", ".join(f"{key} = {toml_value(value)}" for key, value in preview["after"].items()) + " }"
        candidate = text[:start] + updated + text[end:]
        workflow.validate_workflow_manifest(tomllib.loads(candidate))
        atomic_bytes(path, candidate.encode())
        return {"workflow_fingerprint": workflow.workflow_fingerprint(self.repo)}
