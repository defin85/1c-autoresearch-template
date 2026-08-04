from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from .contracts import JsonValue, json_object, parse_json
from .service import ApplicationService
from .sources import ConnectionProfile, ExtensionInfo


class CLIArgs(argparse.Namespace):
    repo_path: str = "."
    command: str = ""
    name: str = ""
    offset: int = 0
    limit: int = 100
    approve_source_acquisition: bool = False
    approve_agent_proposal: bool = False
    max_units: int = 100
    strict: bool = False
    operation: str = ""
    payload: dict[str, JsonValue] = {}
    expected_fingerprint: str = ""
    workflow_v4: str = ""
    external_command: str = ""
    path: str = ""
    role: str = "target_cf"
    preview_id: str = ""
    selection_file: str | None = None


def _connections(value: dict[str, dict[str, JsonValue]]) -> dict[str, ConnectionProfile]:
    result: dict[str, ConnectionProfile] = {}
    string_fields = (
        "platform_path", "kind", "server", "reference", "path", "dbms", "db_server", "db_name",
        "db_user", "db_password", "infobase_user", "infobase_password", "profile_id",
        "tested_fingerprint", "client_connection",
    )
    for name, row in value.items():
        profile: ConnectionProfile = {}
        for field in string_fields:
            item = row.get(field)
            if isinstance(item, str):
                profile[field] = item
        tested = row.get("tested")
        if isinstance(tested, bool):
            profile["tested"] = tested
        for field in ("configuration", "tool_versions"):
            item = row.get(field)
            if isinstance(item, dict) and all(isinstance(part, str) for part in item.values()):
                profile[field] = {key: part for key, part in item.items() if isinstance(part, str)}
        extensions = row.get("extensions")
        if isinstance(extensions, list):
            converted: list[ExtensionInfo] = []
            for raw in extensions:
                item = json_object(raw)
                if not isinstance(item.get("uuid"), str) or not isinstance(item.get("name"), str) or not isinstance(item.get("version"), str) or not isinstance(item.get("active"), bool):
                    raise ValueError("invalid connection extension")
                uuid, extension_name = item["uuid"], item["name"]
                version, active = item["version"], item["active"]
                if not isinstance(uuid, str) or not isinstance(extension_name, str) or not isinstance(version, str) or not isinstance(active, bool):
                    raise ValueError("invalid connection extension")
                converted.append({"uuid": uuid, "name": extension_name, "version": version, "active": active})
            profile["extensions"] = converted
        result[name] = profile
    return result


def _folder_event(user_root: Path, project_id: str, preview_id: str, operation: str, workflow_fingerprint: str, raw_value: object) -> None:
    from .contracts import sha256
    from .events import EventStore, process_identity
    value = json_object(raw_value)
    digest = sha256(preview_id.encode()); run_id = f"external-folder-{digest[:16]}"; store = EventStore(user_root / "projects", project_id)
    _ = store.emit("run.created", run_id, {"status": "running", "actor": "local-user", "process_identity": process_identity(), "workflow_fingerprint": workflow_fingerprint, "operation": operation, "preview_digest": "sha256:" + digest})
    entries = value.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("invalid folder preview entries")
    _ = store.emit("run.finished", run_id, {"status": "completed", "duration_seconds": 0.0, "operation": operation, "entry_count": len(entries), "ignored_unsupported_count": int(str(value.get("ignored_unsupported_count", 0))), "status_counts": value.get("status_counts", {}), "total_bytes": int(str(value.get("total_bytes", 0))), "candidate_declaration_fingerprint": value.get("candidate_declaration_sha256"), "draft_result": value.get("status"), "validation": "passed"})


def _payload(value: str) -> dict[str, JsonValue]:
    parsed = parse_json(value)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("payload must be a JSON object")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="one-c-autoresearch")
    _ = result.add_argument("--repo-path", default=".")
    commands = result.add_subparsers(dest="command", required=True)
    _ = commands.add_parser("status")
    _ = commands.add_parser("source-scope")
    _ = commands.add_parser("next")
    registry = commands.add_parser("registry")
    _ = registry.add_argument("name", choices=("diff-inventory", "target-coverage", "mrq", "extension-diff", "extension-dependencies", "extension-path-coverage", "extension-physical-diff"))
    _ = registry.add_argument("--offset", type=int, default=0)
    _ = registry.add_argument("--limit", type=int, default=100)
    run = commands.add_parser("run-next")
    _ = run.add_argument("--approve-source-acquisition", action="store_true")
    _ = run.add_argument("--approve-agent-proposal", action="store_true")
    until = commands.add_parser("run-until-blocked")
    _ = until.add_argument("--max-units", type=int, default=100)
    _ = until.add_argument("--approve-source-acquisition", action="store_true")
    _ = until.add_argument("--approve-agent-proposal", action="store_true")
    doctor = commands.add_parser("doctor")
    _ = doctor.add_argument("--strict", action="store_true")
    apply = commands.add_parser("apply")
    _ = apply.add_argument("operation")
    _ = apply.add_argument("--payload", type=_payload, default={})
    _ = apply.add_argument("--expected-fingerprint", required=True)
    migrate = commands.add_parser("migrate-workflow-v4")
    _ = migrate.add_argument("--workflow-v4", required=True)
    external = commands.add_parser("external-artifacts").add_subparsers(dest="external_command", required=True)
    preview = external.add_parser("folder-preview")
    _ = preview.add_argument("path")
    _ = preview.add_argument("--role", default="target_cf", choices=("vendor_baseline", "target_cf", "next_vendor"))
    confirm = external.add_parser("folder-confirm")
    _ = confirm.add_argument("preview_id")
    _ = confirm.add_argument("--expected-fingerprint", required=True)
    _ = confirm.add_argument("--selection-file")
    return result


def main(argv: list[str] | None = None) -> int:
    args = CLIArgs()
    _ = parser().parse_args(argv, namespace=args)
    repo = Path(args.repo_path).resolve()
    from .user_state import load_connections, workspace_id
    user_root = Path.home() / ".local/state/one-c-autoresearch"
    project_root = user_root / "projects" / workspace_id(repo)
    service = ApplicationService(repo, connections=_connections(load_connections(repo)), upload_drafts=project_root / "upload-drafts")
    value: JsonValue
    if args.command == "doctor":
        from .doctor import check
        value = json_object(check(Path(args.repo_path), args.strict))
    elif args.command == "migrate-workflow-v4":
        from .contracts import sha256
        from . import workflow_migration
        migration = workflow_migration
        workflow_bytes = (repo / args.workflow_v4).read_bytes() if not Path(args.workflow_v4).is_absolute() else Path(args.workflow_v4).read_bytes()
        workflow_fingerprint = "sha256:" + sha256((repo / "research/workflow.toml").read_bytes())
        repository_fingerprint = "sha256:" + sha256(str(repo).encode())
        journal = json_object(migration.start_migration(
            repo, repository_fingerprint=repository_fingerprint,
            workflow_fingerprint=workflow_fingerprint, owner="local-user",
        ))
        value = json_object(migration.execute(repo, str(journal["migration_id"]), workflow_v4=workflow_bytes))
    elif args.command == "status":
        value = service.snapshot()
    elif args.command == "source-scope":
        from .sources import extension_scope_status
        if service.connections is None:
            raise RuntimeError("connections are unavailable")
        value = json_object(extension_scope_status(repo, service.connections))
    elif args.command == "next":
        value = service.next()
    elif args.command == "registry":
        value = service.registry(args.name, args.offset, args.limit)
    elif args.command in {"run-next", "run-until-blocked"}:
        from .events import EventStore
        from .user_state import load_agent_profiles
        from .runner import run_next, run_until_blocked
        project_id = workspace_id(repo)
        store = EventStore(user_root / "projects", project_id)
        def invoke(operation: str, payload: dict[str, JsonValue], cancelled: Callable[[], bool]) -> dict[str, JsonValue]:
            return service.apply(operation, payload, str(service.snapshot()["workflow_fingerprint"]), cancelled)

        approvals: set[str] = {"sources.acquire"} if args.approve_source_acquisition else set()
        if args.approve_agent_proposal:
            approvals.update({"dif.classify-next", "mrq.consolidate", "mrq.decide-next"})
        profiles = load_agent_profiles(repo)
        if args.command == "run-until-blocked":
            value = json_object(run_until_blocked(repo, invoke, store, select=service.next, approved_operations=approvals, agent_profiles=profiles, max_units=args.max_units))
        else:
            value = json_object(run_next(repo, invoke, store, select=service.next, approved_operations=approvals, agent_profiles=profiles))
    elif args.command == "external-artifacts":
        from .external_folder import PreviewStore, public_preview
        store = PreviewStore(repo, project_root / "external-folder-previews", project_root / "upload-drafts")
        if args.external_command == "folder-preview":
            fingerprint = str(service.snapshot()["workflow_fingerprint"]); raw = store.scan_folder(Path(args.path), fingerprint, args.role); _folder_event(user_root, workspace_id(repo), raw["preview_id"], "external-artifacts.folder-preview/v1", fingerprint, raw); value = json_object(public_preview(raw))
        else:
            preview = store.load(args.preview_id)
            selection_value = parse_json(Path(args.selection_file).read_text(encoding="utf-8")) if args.selection_file else [{"entry_id": item["entry_id"]} for item in preview["entries"] if item.get("status") not in {"invalid", "conflict"}]
            if not isinstance(selection_value, list):
                raise ValueError("selection file must contain an array")
            selection: list[JsonValue] = list(selection_value)
            value = service.apply("sources.configure", {"external_artifact_preview_id": args.preview_id, "selected_entries": selection, "expected_declaration_fingerprint": preview["declaration_fingerprint"], "expected_draft_fingerprint": preview["draft_fingerprint"], "confirm": True}, args.expected_fingerprint); _folder_event(user_root, workspace_id(repo), args.preview_id, "sources.configure.external-artifacts/v1", args.expected_fingerprint, value)
    else:
        value = service.apply(args.operation, args.payload, args.expected_fingerprint)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if args.command == "doctor" and (not isinstance(value, dict) or not value.get("ok", False)) else 0
