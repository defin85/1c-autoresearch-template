from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .service import ApplicationService


def _folder_event(user_root: Path, project_id: str, preview_id: str, operation: str, workflow_fingerprint: str, value: dict[str, Any]) -> None:
    from .contracts import sha256
    from .events import EventStore, process_identity
    digest = sha256(preview_id.encode()); run_id = f"external-folder-{digest[:16]}"; store = EventStore(user_root / "projects", project_id)
    store.emit("run.created", run_id, {"status": "running", "actor": "local-user", "process_identity": process_identity(), "workflow_fingerprint": workflow_fingerprint, "operation": operation, "preview_digest": "sha256:" + digest})
    store.emit("run.finished", run_id, {"status": "completed", "duration_seconds": 0.0, "operation": operation, "entry_count": len(value.get("entries", [])), "ignored_unsupported_count": int(value.get("ignored_unsupported_count", 0)), "status_counts": value.get("status_counts", {}), "total_bytes": int(value.get("total_bytes", 0)), "candidate_declaration_fingerprint": value.get("candidate_declaration_sha256"), "draft_result": value.get("status"), "validation": "passed"})


def _payload(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("payload must be a JSON object")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="one-c-autoresearch")
    result.add_argument("--repo-path", default=".")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("next")
    registry = commands.add_parser("registry")
    registry.add_argument("name", choices=("diff-inventory", "target-coverage", "mrq", "extension-diff", "extension-dependencies", "extension-path-coverage", "extension-physical-diff"))
    registry.add_argument("--offset", type=int, default=0)
    registry.add_argument("--limit", type=int, default=100)
    run = commands.add_parser("run-next")
    run.add_argument("--approve-source-acquisition", action="store_true")
    run.add_argument("--approve-agent-proposal", action="store_true")
    until = commands.add_parser("run-until-blocked")
    until.add_argument("--max-units", type=int, default=100)
    until.add_argument("--approve-source-acquisition", action="store_true")
    until.add_argument("--approve-agent-proposal", action="store_true")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--strict", action="store_true")
    apply = commands.add_parser("apply")
    apply.add_argument("operation")
    apply.add_argument("--payload", type=_payload, default={})
    apply.add_argument("--expected-fingerprint", required=True)
    external = commands.add_parser("external-artifacts").add_subparsers(dest="external_command", required=True)
    preview = external.add_parser("folder-preview")
    preview.add_argument("path")
    preview.add_argument("--role", default="target_cf", choices=("vendor_baseline", "target_cf", "next_vendor"))
    confirm = external.add_parser("folder-confirm")
    confirm.add_argument("preview_id")
    confirm.add_argument("--expected-fingerprint", required=True)
    confirm.add_argument("--selection-file")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo = Path(args.repo_path).resolve()
    from .user_state import load_connections, workspace_id
    user_root = Path.home() / ".local/state/one-c-autoresearch"
    project_root = user_root / "projects" / workspace_id(repo)
    service = ApplicationService(repo, connections=load_connections(repo), upload_drafts=project_root / "upload-drafts")
    if args.command == "doctor":
        from .doctor import check
        value = check(Path(args.repo_path), args.strict)
    elif args.command == "status":
        value = service.snapshot()
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
        invoke = lambda operation, payload, cancelled: service.apply(operation, payload, service.snapshot()["workflow_fingerprint"], cancelled)
        approvals = {"sources.acquire"} if args.approve_source_acquisition else set()
        if args.approve_agent_proposal:
            approvals.update({"mrq.discover-next", "mrq.decide-next"})
        profiles = load_agent_profiles(repo)
        value = run_next(repo, invoke, store, select=service.next, approved_operations=approvals, agent_profiles=profiles) if args.command == "run-next" else run_until_blocked(repo, invoke, store, max_units=args.max_units, select=service.next, approved_operations=approvals, agent_profiles=profiles)
    elif args.command == "external-artifacts":
        from .external_folder import PreviewStore, public_preview
        store = PreviewStore(repo, project_root / "external-folder-previews", project_root / "upload-drafts")
        if args.external_command == "folder-preview":
            fingerprint = service.snapshot()["workflow_fingerprint"]; raw = store.scan_folder(Path(args.path), fingerprint, args.role); _folder_event(user_root, workspace_id(repo), raw["preview_id"], "external-artifacts.folder-preview/v1", fingerprint, raw); value = public_preview(raw)
        else:
            preview = store.load(args.preview_id)
            selection = json.loads(Path(args.selection_file).read_text(encoding="utf-8")) if args.selection_file else [{"entry_id": item["entry_id"]} for item in preview["entries"] if item["status"] not in {"invalid", "conflict"}]
            value = service.apply("sources.configure", {"external_artifact_preview_id": args.preview_id, "selected_entries": selection, "expected_declaration_fingerprint": preview["declaration_fingerprint"], "expected_draft_fingerprint": preview["draft_fingerprint"], "confirm": True}, args.expected_fingerprint); _folder_event(user_root, workspace_id(repo), args.preview_id, "sources.configure.external-artifacts/v1", args.expected_fingerprint, value)
    else:
        value = service.apply(args.operation, args.payload, args.expected_fingerprint)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
    return 0
