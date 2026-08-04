from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from collections.abc import Callable, Iterable
from typing import TypedDict, final

from . import diffs, indexes, mrq, sources, workflow
from .contracts import ROLES, JsonValue, atomic_bytes, atomic_json, canonical_json, json_object, parse_json, parse_json_object, reject_secrets, repository_lock, sha256, stage_active_pointers


JsonObject = dict[str, JsonValue]
Handler = Callable[[JsonObject], JsonObject]


class Staged(TypedDict, total=False):
    source: sources.SourcePointer
    diff: diffs.DiffPointer


def _active_pointers(repo: Path) -> dict[str, JsonObject | None]:
    pointers = stage_active_pointers(repo)
    return {name: json_object(pointer) if pointer is not None else None for name, pointer in pointers.items()}


def _objects(value: JsonValue) -> list[JsonObject]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("expected object array")
    return [item for item in value if isinstance(item, dict)]


def _strings(value: JsonValue) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("expected string array")
    return [item for item in value if isinstance(item, str)]


def _string(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _integer(value: JsonValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected integer")
    return value


def _json(value: object) -> JsonObject:
    return json_object(value)


def _value(value: object) -> JsonValue:
    return parse_json(canonical_json(value).decode("utf-8"))


def _index_candidate(value: JsonValue) -> indexes.ConfigCandidate:
    source = json_object(value)
    result: indexes.ConfigCandidate = {}
    for key in ("schema_version", "machine_contract_version"):
        if key in source:
            result[key] = _string(source[key])
    if "backends" in source:
        backends: list[indexes.BackendRow] = []
        for row in _objects(source["backends"]):
            backends.append({"adapter_id": _string(row.get("adapter_id")), "engine_version": _string(row.get("engine_version"))})
        result["backends"] = backends
    if "routes" in source:
        result["routes"] = {name: _strings(route) for name, route in json_object(source["routes"]).items()}
    if "service_profiles" in source:
        result["service_profiles"] = {name: _string(profile) for name, profile in json_object(source["service_profiles"]).items()}
    return result


def backend_state(value: JsonObject) -> indexes.BackendState:
    result: indexes.BackendState = {}
    if "adapter_id" in value: result["adapter_id"] = _string(value["adapter_id"])
    if "component_id" in value: result["component_id"] = _string(value["component_id"])
    if "status" in value: result["status"] = _string(value["status"])
    if "modality" in value: result["modality"] = _string(value["modality"])
    if "adapter_version" in value: result["adapter_version"] = _string(value["adapter_version"])
    if "capability_fingerprint" in value: result["capability_fingerprint"] = _string(value["capability_fingerprint"])
    if "index_fingerprint" in value: result["index_fingerprint"] = _string(value["index_fingerprint"])
    if "target_fingerprint" in value: result["target_fingerprint"] = _string(value["target_fingerprint"])
    if "contract_version" in value: result["contract_version"] = _string(value["contract_version"])
    if "index_key" in value: result["index_key"] = _string(value["index_key"])
    if "instance_path" in value: result["instance_path"] = _string(value["instance_path"])
    if "index_dir" in value: result["index_dir"] = _string(value["index_dir"])
    if "embedding_identity" in value: result["embedding_identity"] = _string(value["embedding_identity"])
    if "reference_identity" in value: result["reference_identity"] = _string(value["reference_identity"])
    if "validated_at" in value: result["validated_at"] = _string(value["validated_at"])
    if "capabilities" in value:
        result["capabilities"] = _strings(value["capabilities"])
    if "legacy_adopted" in value:
        legacy = value["legacy_adopted"]
        if not isinstance(legacy, bool):
            raise ValueError("expected boolean")
        result["legacy_adopted"] = legacy
    if "last_validation" in value: result["last_validation"] = None if value["last_validation"] is None else _string(value["last_validation"])
    if "readiness_reason" in value: result["readiness_reason"] = None if value["readiness_reason"] is None else _string(value["readiness_reason"])
    if "recovery_action" in value: result["recovery_action"] = None if value["recovery_action"] is None else _string(value["recovery_action"])
    return result


def _diff_source_pointer(value: sources.SourcePointer) -> diffs.SourcePointer:
    result: diffs.SourcePointer = {}
    for key in (
        "schema_version", "generation_id", "acquisition_profile_id",
        "representation_schema", "normalizer_version", "routing_manifest_path",
        "routing_manifest_fingerprint", "source_comparison_epoch_fingerprint",
    ):
        if key in value:
            result[key] = _string(value.get(key))
    if "components" in value:
        result["components"] = value["components"]
    return result


@final
class ApplicationService:
    progress: sources.ProgressCallback | None = None

    def __init__(self, repo: Path, rlm_executable: str | None = None, connections: dict[str, sources.ConnectionProfile] | None = None, upload_drafts: Path | None = None, routing_previews: Path | None = None, progress: sources.ProgressCallback | None = None, agent_profiles: dict[str, JsonObject] | None = None):
        self.repo = repo.resolve()
        self.rlm_executable = rlm_executable or indexes.discover_executable(self.repo)
        self.connections = connections
        self.upload_drafts = upload_drafts
        self.routing_previews = routing_previews
        self.agent_profiles = agent_profiles
        self.progress = progress
        self._expected_fingerprint = ""
        self._cancelled: sources.CancelCallback | None = None
        self._staged: Staged | None = None
        self._fence: Callable[[], None] | None = None
        _ = workflow.validate_workflow(self.repo)

    def _required_index_capabilities(self) -> tuple[str, ...]:
        if self.agent_profiles is None:
            return tuple(indexes.CAPABILITIES)
        from .source_search import OPERATIONS
        assigned: set[str] = set()
        configurations: Iterable[object] = workflow.step_configurations(self.repo)
        for row_value in configurations:
            row = json_object(_value(row_value))
            step = json_object(row.get("step"))
            for phase in _objects(step.get("agent_phases", [])):
                for role in _objects(phase.get("roles", [])):
                    assigned.add(_string(role.get("agent_profile")))
        operations: set[str] = set()
        for name in assigned:
            profile = self.agent_profiles.get(name, {})
            source_search = json_object(profile.get("source_search", {}))
            operations.update(_strings(source_search.get("operations", [])))
        return tuple(sorted({
            OPERATIONS[operation]
            for operation in operations
            if operation in OPERATIONS
        }))

    def snapshot(self, *, deep: bool = True) -> JsonObject:
        snapshot = _json(_value(workflow.status(self.repo, deep=deep)))
        if not self.connections:
            return snapshot
        scope = sources.extension_scope_status(self.repo, self.connections)
        blockers = _objects(_value(scope["blockers"]))
        snapshot["extension_scope_blockers"] = blockers
        gates = _objects(snapshot["gates"])
        if scope["ready"] or gates[0]["state"] != "complete":
            return snapshot
        gates[1] = {
            "id": "sources-acquired",
            "state": "ready",
            "blockers": [
                {
                    **blocker,
                    "message": f"extension {blocker['uuid']} requires an explicit include or exclude decision",
                }
                for blocker in blockers
            ],
        }
        for gate in gates[2:]:
            gate["state"] = "blocked"
            gate["blockers"] = [{
                "code": "predecessor.blocked",
                "message": "a predecessor gate is incomplete",
                "action": "",
            }]
        snapshot["gates"] = gates
        snapshot["state"] = "ready"
        return snapshot

    def registry(
        self,
        name: str,
        offset: int = 0,
        limit: int = 100,
        expected_generation: str = "",
        item_id: str = "",
    ) -> JsonObject:
        allowed = {"diff-inventory", "target-coverage", "mrq", "extension-diff", "extension-dependencies", "extension-path-coverage", "extension-physical-diff"}
        if name not in allowed or offset < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid registry page")
        generation_id = ""
        if name == "mrq":
            from .consolidation import load_active
            state: JsonObject = load_active(self.repo)
            generation_id = str(json_object(state["pointer"]).get("mrq_generation_id", ""))
            if expected_generation and expected_generation != generation_id:
                raise RuntimeError("stale MRQ generation")
            mrqs = _objects(json_object(state["mrq"]).get("mrq.jsonl", []))
            rows = mrqs if item_id else mrqs[offset : offset + limit + 1]
        else:
            pointer = parse_json_object((self.repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
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
                            parse_json_object(line)
                            for line in (
                                (line for line in stream if line.strip())
                                if item_id
                                else islice((line for line in stream if line.strip()), offset, offset + limit + 1)
                            )
                        ]
                else:
                    import csv
                    with path.open(encoding="utf-8", newline="") as stream:
                        reader = csv.DictReader(stream)
                        rows = [_json(row) for row in (reader if item_id else islice(reader, offset, offset + limit + 1))]
        if item_id:
            identifier_fields = {
                "diff-inventory": ("stable_diff_id",),
                "mrq": ("mrq_id",),
            }.get(name, ("item_id", "stable_diff_id", "mrq_id", "id"))
            rows = [
                row for row in rows
                if any(str(row.get(field, "")) == item_id for field in identifier_fields)
            ]
            offset = 0
        return {
            "diff_generation_id": generation_id,
            "offset": offset,
            "limit": limit,
            "items": rows[:limit],
            "has_more": len(rows) > limit,
        }

    def next(self) -> JsonObject | None:
        snapshot = self.snapshot()
        scope_blockers = _objects(snapshot.get("extension_scope_blockers", []))
        if scope_blockers:
            blocker = scope_blockers[0]
            return {
                "gate_id": "sources-acquired",
                "action": blocker["action"],
                "blocker": blocker,
                "workflow_fingerprint": snapshot["workflow_fingerprint"],
            }
        canonical: JsonObject | None = workflow.next_work(self.repo)
        if canonical and canonical["action"] in {"mrq.discover-next", "mrq.decide-next"}:
            components = indexes.discover(self.repo)
            if components:
                statuses = [backend_state(row) for row in indexes.backend_statuses(self.repo)]
                config = indexes.load_config(self.repo)
                profiles = getattr(self, "agent_profiles", None)
                if profiles is not None:
                    required = set(self._required_index_capabilities())
                    config["routes"] = {
                        capability: route
                        for capability, route in config["routes"].items()
                        if capability in required
                    }
                coverage = indexes.route_coverage(config, components, statuses)
                if coverage["blockers"]:
                    pending = coverage["blockers"]
                    work_unit = {
                        **json_object(canonical.get("work_unit", {})),
                        "component_ids": sorted({item["component_id"] for item in pending}),
                        "backend_ids": sorted({
                            adapter_id
                            for item in pending
                            for adapter_id in item["backend_ids"]
                        }),
                    }
                    return {
                        **canonical,
                        "job_id": "index-sources",
                        "domain_action": canonical["action"],
                        "action": "indexes.build",
                        "blocker": {
                            "code": "indexes.route_unavailable",
                            "message": f"{len(pending)} capability routes require a ready index",
                            "action": "indexes.build",
                        },
                        "work_unit": work_unit,
                    }
        return canonical

    def index_statuses(self) -> list[JsonObject]:
        return [_json(row) for row in indexes.backend_statuses(self.repo)]

    def workflow_configuration(self) -> JsonObject:
        manifest = _json(_value(workflow.validate_workflow(self.repo)))
        jobs = _objects(manifest["jobs"])
        configurations: Iterable[object] = workflow.step_configurations(self.repo)
        return {"manifest_fingerprint": workflow.workflow_fingerprint(self.repo), "jobs": [{"id": job["id"], "needs": _value(job["needs"])} for job in jobs], "steps": [_json(_value(item)) for item in configurations]}

    def preview_step_patch(self, payload: JsonObject, state_base: Path | None = None) -> JsonObject:
        if set(payload) != {"step_id", "parameters", "expected_manifest_fingerprint"}:
            raise ValueError("invalid workflow step patch")
        parameters: dict[str, object] = dict(json_object(payload["parameters"]))
        return _json(_value(workflow.preview_step_patch(self.repo, _string(payload["step_id"]), parameters, _string(payload["expected_manifest_fingerprint"]), state_base)))

    def preview_index_configuration(self, payload: JsonObject) -> JsonObject:
        if set(payload) != {"configuration", "expected_file_fingerprint"}:
            raise ValueError("invalid indexing configuration preview")
        configuration = _index_candidate(payload["configuration"])
        if configuration.get("schema_version") == "3":
            return _json(indexes.preview_schema3_migration(
                self.repo,
                configuration,
                str(payload["expected_file_fingerprint"]),
                self._schema3_profile_operations(),
            ))
        return _json(indexes.preview_config(
            self.repo,
            configuration,
            str(payload["expected_file_fingerprint"]),
            self._required_index_capabilities(),
        ))

    def _schema3_profile_operations(self) -> dict[str, list[str]]:
        from .source_search import V2_OPERATIONS, normalize_operation
        configured: set[str] = set()
        for profile in (self.agent_profiles or {}).values():
            source_search = json_object(profile.get("source_search", {}))
            configured.update(_strings(source_search.get("operations", [])))
        operations = sorted({normalize_operation(operation) for operation in configured if normalize_operation(operation) in V2_OPERATIONS})
        if not operations:
            operations = sorted(V2_OPERATIONS)
        return {
            "lexical": [
                operation for operation in operations
                if operation != "code.search_hybrid"
            ],
            "hybrid": [
                operation for operation in operations
                if operation == "code.search_hybrid"
            ] or ["code.search_hybrid"],
        }

    def apply(self, operation: str, payload: JsonObject, expected_fingerprint: str, cancelled: sources.CancelCallback | None = None, *, staged: Staged | None = None, fence: Callable[[], None] | None = None) -> JsonObject:
        from .workflow_migration import guard_mutation
        guard_mutation(self.repo)
        reject_secrets(payload, "operation payload")
        current = workflow.state_fingerprint(self.repo)
        if expected_fingerprint != current:
            raise RuntimeError("stale workflow fingerprint")
        if (
            self.connections
            and operation not in {"sources.configure", "sources.acquire"}
            and not (staged is not None and staged.get("source"))
        ):
            blockers = sources.extension_scope_status(self.repo, self.connections)["blockers"]
            if blockers:
                raise RuntimeError(json.dumps({
                    "code": "extension_scope_required",
                    "blockers": blockers,
                }, ensure_ascii=False, sort_keys=True))
        self._expected_fingerprint = expected_fingerprint
        self._cancelled = cancelled
        self._staged = staged
        self._fence = fence
        handlers: dict[str, Handler] = {
            "project.configure": self._configure,
            "sources.configure": lambda value: self._locked(self._configure_sources, value),
            "sources.acquire": self._acquire_sources,
            "indexes.ensure": self._ensure_indexes,
            "indexes.build": self._ensure_indexes,
            "indexes.configure": lambda value: self._locked(self._configure_indexes, value),
            "diff.build": self._build_diffs,
            "projections.build": lambda value: self._locked(self._build_projections, value),
            "workflow.patch-step": lambda value: self._locked(self._patch_step, value),
            "workflow.verify": self._verify_workflow,
        }
        try:
            handler = handlers[operation]
        except KeyError as exc:
            raise ValueError(f"unsupported typed operation: {operation}") from exc
        return handler(payload)

    def _verify_workflow(self, payload: JsonObject) -> JsonObject:
        if payload:
            raise ValueError("workflow.verify takes no parameters")
        from .doctor import check
        result = check(self.repo, strict=True)
        if not result["ok"]:
            raise RuntimeError(json.dumps({"code": "workflow.verify.failed", "blockers": result["failures"]}, ensure_ascii=False, sort_keys=True))
        return _json(result)

    def _locked(self, handler: Handler, payload: JsonObject) -> JsonObject:
        with repository_lock(self.repo):
            if workflow.state_fingerprint(self.repo) != self._expected_fingerprint:
                raise RuntimeError("stale workflow fingerprint")
            result = handler(payload)
            return {**result, "workflow_fingerprint": workflow.state_fingerprint(self.repo)}

    def _configure(self, payload: JsonObject) -> JsonObject:
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

    def _acquire_sources(self, payload: JsonObject) -> JsonObject:
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
        raw_preview = parse_json_object(preview_path.read_text(encoding="utf-8"))
        from .user_state import workspace_id
        if raw_preview.get("project_id") != workspace_id(self.repo) or raw_preview.get("status") != "ready" or raw_preview.get("routing_plan_fingerprint") != payload.get("routing_plan_fingerprint"):
            raise RuntimeError("routing_preview_stale")
        if datetime.fromisoformat(str(raw_preview.get("expires_at", ""))) <= datetime.now(timezone.utc):
            raise RuntimeError("routing_preview_stale")
        preview = sources.routing_preview(raw_preview)
        result = sources.acquire(self.repo, platform, self.connections, routing_preview=preview, timeout_seconds=_integer(payload.get("timeout_seconds", 1800)), upload_drafts=self.upload_drafts, cancelled=self._cancelled, progress=self.progress, activate=self._staged is None)
        if self._staged is not None:
            self._staged["source"] = result
        return _json(result)

    def _configure_sources(self, payload: JsonObject) -> JsonObject:
        external_keys = {"external_artifact_preview_id", "selected_entries", "expected_declaration_fingerprint", "expected_draft_fingerprint", "confirm"}
        if set(payload) == external_keys:
            if payload["confirm"] is not True or self.upload_drafts is None:
                raise ValueError("invalid external artifact source setup patch")
            from .external_folder import PreviewStore
            from .external_folder import Selection
            store = PreviewStore(self.repo, self.upload_drafts.parent / "external-folder-previews", self.upload_drafts)
            selection: list[Selection] = []
            for row in _objects(payload["selected_entries"]):
                item: Selection = {}
                if "entry_id" in row: item["entry_id"] = _string(row["entry_id"])
                if "role" in row: item["role"] = _string(row["role"])
                if "semantic_key" in row: item["semantic_key"] = _string(row["semantic_key"])
                selection.append(item)
            return _json({"operation": "sources.configure", "comparison_epoch_changed": False, **store.confirm(_string(payload["external_artifact_preview_id"]), selection, workflow_fingerprint=self._expected_fingerprint, expected_declaration_fingerprint=_string(payload["expected_declaration_fingerprint"]), expected_draft_fingerprint=_string(payload["expected_draft_fingerprint"]))})
        extension_keys = {"extension_decisions", "expected_manifest_fingerprint", "confirm_new_epoch"}
        if set(payload) == extension_keys:
            path = self.repo / "research/infobases.toml"
            if payload["expected_manifest_fingerprint"] != "sha256:" + sha256(path.read_bytes()):
                raise RuntimeError("stale source setup fingerprint")
            bindings, _artifacts = sources.load_contract(self.repo)
            bindings["extension_decisions"] = sources.normalize_extension_decisions(payload["extension_decisions"])
            candidate = sources.serialize_infobases(bindings)
            result = {
                "operation": "sources.configure",
                "manifest_fingerprint": "sha256:" + sha256(candidate),
                "extension_decisions": bindings["extension_decisions"],
                "comparison_epoch_changed": candidate != path.read_bytes(),
            }
            if payload["confirm_new_epoch"] is not True:
                return _json({**result, "preview": True})
            atomic_bytes(path, candidate)
            return _json({**result, "preview": False})
        if set(payload) != {"acquisition_profile", "connection_profiles", "expected_manifest_fingerprint", "confirm_new_epoch"} or payload["acquisition_profile"] not in sources.PROFILES:
            raise ValueError("invalid source setup patch")
        path = self.repo / "research/infobases.toml"
        if payload["expected_manifest_fingerprint"] != "sha256:" + sha256(path.read_bytes()):
            raise RuntimeError("stale source setup fingerprint")
        if not payload["confirm_new_epoch"]:
            raise ValueError("source profile changes require explicit comparison-epoch confirmation")
        bindings, _artifacts = sources.load_contract(self.repo); roles = bindings["roles"]; profiles = {name: _string(value) for name, value in json_object(payload["connection_profiles"]).items()}
        import re
        if tuple(roles) != ROLES or set(profiles) != set(ROLES) or any(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) is None for value in profiles.values()):
            raise ValueError("exactly three safe connection-profile bindings are required")
        bindings["acquisition_profile"] = _string(payload["acquisition_profile"])
        for role in ROLES:
            roles[role]["connection_profile"] = profiles[role]
        atomic_bytes(path, sources.serialize_infobases(bindings))
        return {"operation": "sources.configure", "manifest_fingerprint": "sha256:" + sha256(path.read_bytes()), "comparison_epoch_changed": True}

    def _ensure_indexes(self, payload: JsonObject) -> JsonObject:
        if set(payload) - {"component_ids", "backend_ids", "mode", "confirmed"}:
            raise ValueError("invalid index operation payload")
        mode = payload.get("mode", "ensure")
        if mode not in {"ensure", "rebuild", "validate"}:
            raise ValueError("invalid index operation mode")
        if mode == "validate":
            return _json({
                "operation": "indexes.validate",
                "components": indexes.validate_configured(
                    self.repo,
                    component_ids=_strings(payload["component_ids"]) if "component_ids" in payload else None,
                    backend_ids=_strings(payload["backend_ids"]) if "backend_ids" in payload else None,
                ),
            })
        rows = indexes.ensure_configured(
            self.repo,
            component_ids=_strings(payload["component_ids"]) if "component_ids" in payload else None,
            backend_ids=_strings(payload["backend_ids"]) if "backend_ids" in payload else None,
            rebuild=mode == "rebuild",
            confirmed=bool(payload.get("confirmed")),
            cancelled=self._cancelled,
        )
        return _json({"operation": "indexes.build", "components": rows})

    def _configure_indexes(self, payload: JsonObject) -> JsonObject:
        if set(payload) != {
            "configuration",
            "expected_file_fingerprint",
            "expected_plan_fingerprint",
        }:
            raise ValueError("invalid indexing configuration apply")
        configuration = _index_candidate(payload["configuration"])
        if configuration.get("schema_version") == "3":
            return _json(indexes.apply_schema3_migration(
                self.repo,
                configuration,
                _string(payload["expected_file_fingerprint"]),
                _string(payload["expected_plan_fingerprint"]),
                self._schema3_profile_operations(),
            ))
        return _json(indexes.apply_config(
            self.repo,
            configuration,
            _string(payload["expected_file_fingerprint"]),
            _string(payload["expected_plan_fingerprint"]),
            self._required_index_capabilities(),
        ))

    def _build_diffs(self, payload: JsonObject) -> JsonObject:
        if payload:
            raise ValueError("diff.build takes no parameters")
        pointers = _active_pointers(self.repo) if self._staged is None else {}
        if self._staged is not None and "source" in self._staged:
            pointer = self._staged["source"]
        else:
            pointer = sources.validate_active(self.repo, deep=True)
            if _json(pointer) != pointers["source"]:
                raise RuntimeError("active source generation changed during validation")
        prior_pointer = pointers["mrq"] if self._staged is None else _active_pointers(self.repo)["mrq"]
        diff_source = _diff_source_pointer(pointer)
        result = diffs.build(self.repo, diff_source, cancelled=self._cancelled, activate=self._staged is None)
        if self._staged is not None:
            self._staged["diff"] = result
            return _json(result)
        prior = {} if prior_pointer is None else prior_pointer
        prior_generation_value = prior.get("canonical_generation_id")
        prior_generation = prior_generation_value if isinstance(prior_generation_value, str) else None
        analyzer = parse_json_object((self.repo / "analysis/indexes/generations" / result["generation_id"] / "extension-analyzer-manifest.json").read_text(encoding="utf-8"))
        epoch = _json(pointer)
        new_epoch = mrq.comparison_epoch_fingerprint(epoch, analyzer)
        prior_manifest: JsonObject = {}
        if prior_generation:
            prior_manifest = parse_json_object((self.repo / "research/generations" / prior_generation / "manifest.json").read_text(encoding="utf-8"))
        if not prior_generation or prior_manifest.get("comparison_epoch_fingerprint") != new_epoch:
            empty: mrq.ArtifactRows = {"mrq.jsonl": [], "dispositions.jsonl": [], "evidence.jsonl": [], "lineage.jsonl": [], "approvals.jsonl": []}
            _ = mrq.publish(self.repo, empty, _string(diff_source.get("generation_id")), result["generation_id"], epoch, prior_generation)
        return _json(result)

    def _build_projections(self, payload: JsonObject) -> JsonObject:
        if payload:
            raise ValueError("projections.build takes no parameters")
        from .consolidation import load_active
        state = _json(load_active(self.repo))
        pointer = json_object(state["pointer"])
        if pointer.get("state") != "active":
            raise ValueError("canonical consolidation generation is absent")
        projections = _json(workflow.projection_value(state))
        atomic_json(self.repo / "outputs/projections.json", projections)
        return projections

    def _patch_step(self, payload: JsonObject) -> JsonObject:
        if set(payload) != {"step_id", "parameters", "expected_manifest_fingerprint"}:
            raise ValueError("invalid workflow step patch")
        path = self.repo / "research/workflow.toml"
        # TOML has no stdlib writer; serialize only the already validated fixed step.
        text = path.read_text(encoding="utf-8")
        step_id = _string(payload["step_id"])
        parameters: dict[str, object] = dict(json_object(payload["parameters"]))
        preview = _json(workflow.preview_step_patch(self.repo, step_id, parameters, _string(payload["expected_manifest_fingerprint"])))
        marker_at = text.find(f'{{ id = {json.dumps(step_id)}, operation =')
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
        operation = _string(preview["operation"])
        if set(parameters) - workflow.PARAMETERS.get(operation, set()):
            raise ValueError("unsupported workflow step parameter")

        def toml_value(value: JsonValue) -> str:
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

        updated = "{ " + ", ".join(f"{key} = {toml_value(value)}" for key, value in json_object(preview["after"]).items()) + " }"
        candidate = text[:start] + updated + text[end:]
        _ = workflow.validate_workflow_manifest(tomllib.loads(candidate))
        atomic_bytes(path, candidate.encode())
        return {"workflow_fingerprint": workflow.workflow_fingerprint(self.repo)}
