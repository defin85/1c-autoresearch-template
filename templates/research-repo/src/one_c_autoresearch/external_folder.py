from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from .contracts import ROLES, atomic_bytes, atomic_json, canonical_json, confined, external_id, sha256
from .sources import MINIMUM_FREE_BYTES, draft_fingerprint, load_contract


SCHEMA_VERSION = "1"
MAX_CANDIDATES = 10_000
MAX_FILE_BYTES = 2 * 1024**3
MAX_PREVIEW_BYTES = 20 * 1024**3
MAX_BODY_BYTES = 16 * 1024**2
MAX_STREAMS = 2
CHUNK_BYTES = 1024**2
PREVIEW_TTL_SECONDS = 24 * 60 * 60
STATUSES = ("invalid", "conflict", "changed", "unchanged", "new")
KINDS = {".epf": "epf", ".erf": "erf"}


def public_preview(value: dict[str, Any]) -> dict[str, Any]:
    hidden = {"source", "stored_name"}
    return {**{key: item for key, item in value.items() if key != "entries"}, "entries": [{key: item for key, item in entry.items() if key not in hidden} for entry in value.get("entries", [])]}


def normalize_path(value: str) -> str:
    value = unicodedata.normalize("NFC", value.replace("\\", "/"))
    parts = value.split("/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:/", value) or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("unsafe relative path")
    if any(any(ord(char) < 32 or ord(char) == 127 for char in part) for part in parts):
        raise ValueError("relative path contains control characters")
    if len(value.encode()) > 1024 or any(len(part.encode()) > 255 for part in parts):
        raise ValueError("relative path exceeds portable length limits")
    return value


def strip_browser_root(paths: list[str]) -> list[str]:
    split = [unicodedata.normalize("NFC", item.replace("\\", "/")).split("/") for item in paths]
    if not split or any(len(parts) < 2 or not parts[0] for parts in split) or len({parts[0] for parts in split}) != 1:
        raise ValueError("browser entries must share one non-empty folder root")
    return [normalize_path("/".join(parts[1:])) for parts in split]


def _kind(path: str) -> str | None:
    return KINDS.get(Path(path).suffix.lower())


def _key(path: str) -> str:
    return unicodedata.normalize("NFC", path[: -len(Path(path).suffix)])


def _hash_stream(stream: BinaryIO, output: BinaryIO | None = None) -> tuple[int, str]:
    digest = hashlib.sha256(); size = 0
    while chunk := stream.read(CHUNK_BYTES):
        if len(chunk) > CHUNK_BYTES:
            raise RuntimeError("stream returned an oversized chunk")
        size += len(chunk)
        if size > MAX_FILE_BYTES:
            raise ValueError("external artifact exceeds 2 GiB")
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
    return size, digest.hexdigest()


def serialize_declarations(items: list[dict[str, Any]]) -> bytes:
    def quoted(value: str) -> str:
        return json.dumps(unicodedata.normalize("NFC", value), ensure_ascii=False)

    lines = ['schema_version = "1"', ""]
    for item in sorted(items, key=lambda row: (row["role"], row["kind"], row["semantic_key"])):
        digest = str(item["sha256"]).removeprefix("sha256:").lower()
        if item["role"] not in ROLES or item["kind"] not in {"epf", "erf"} or not str(item["semantic_key"]) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid external artifact declaration candidate")
        filename = normalize_path(str(item["filename"]))
        size = item["declared_size_bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("invalid external artifact declaration size")
        lines.extend(("[[artifacts]]", f'role = {quoted(item["role"])}', f'kind = {quoted(item["kind"])}', f'semantic_key = {quoted(item["semantic_key"])}', f'filename = {quoted(filename)}', f"declared_size_bytes = {size}", f'sha256 = "{digest}"', ""))
    return ("\n".join(lines)).encode()


def declaration_fingerprint(repo: Path) -> str:
    return "sha256:" + sha256((repo / "research/external-artifacts.toml").read_bytes())


def _candidate(existing: list[dict[str, Any]], entries: list[dict[str, Any]], selected: set[str] | None = None) -> list[dict[str, Any]]:
    selected = selected if selected is not None else {entry["entry_id"] for entry in entries if entry["status"] not in {"invalid", "conflict"}}
    by_binding = {(item["role"], item["kind"], item["semantic_key"]): {key: value for key, value in item.items() if key != "external_artifact_id"} for item in existing}
    for entry in entries:
        if entry["entry_id"] not in selected:
            continue
        by_binding[(entry["role"], entry["kind"], entry["semantic_key"])] = {"role": entry["role"], "kind": entry["kind"], "semantic_key": entry["semantic_key"], "filename": entry["filename"], "declared_size_bytes": entry["size_bytes"], "sha256": entry["sha256"]}
    return list(by_binding.values())


def resolve_selection(preview: dict[str, Any], selection: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(selection, list) or not selection: raise ValueError("folder confirmation requires a non-empty selection")
    by_id = {entry["entry_id"]: dict(entry) for entry in preview["entries"]}; selected = []; seen: set[str] = set()
    for choice in selection:
        if not isinstance(choice, dict) or set(choice) - {"entry_id", "role", "semantic_key"} or not isinstance(choice.get("entry_id"), str): raise ValueError("invalid folder confirmation selection")
        identifier = choice["entry_id"]
        if identifier in seen or identifier not in by_id: raise ValueError("unknown or duplicate preview entry")
        seen.add(identifier); entry = by_id[identifier]
        if entry["status"] == "invalid" or entry["status"] == "conflict" and ("portable_path" in entry.get("conflict_reasons", []) or not ({"role", "semantic_key"} & set(choice))): raise ValueError("invalid or unresolved conflicting entry cannot be selected")
        role = choice.get("role", entry["role"]); key = unicodedata.normalize("NFC", str(choice.get("semantic_key", entry["semantic_key"])))
        if role not in ROLES or not key or any(ord(char) < 32 or ord(char) == 127 for char in key): raise ValueError("invalid confirmed role or semantic key")
        entry.update(role=role, semantic_key=key); selected.append(entry)
    bindings = [(item["role"], item["kind"], item["semantic_key"]) for item in selected]; ids = [external_id(item["kind"], item["semantic_key"]) for item in selected]
    if len(set(bindings)) != len(bindings) or len(set(ids)) != len(ids): raise ValueError("confirmed external artifact identities collide")
    return selected


def classify(repo: Path, raw: list[dict[str, Any]], ignored: int, fingerprints: dict[str, str]) -> dict[str, Any]:
    existing = load_contract(repo)[1].get("artifacts", [])
    bindings = {(item["role"], item["kind"], item["semantic_key"]): item for item in existing}
    path_counts: dict[str, int] = {}; binding_counts: dict[tuple[str, str, str], int] = {}; id_preimages: dict[str, set[tuple[str, str]]] = {}; hashes: dict[str, list[str]] = {}
    for entry in raw:
        path_counts[entry["filename"].casefold()] = path_counts.get(entry["filename"].casefold(), 0) + 1
        binding = (entry["role"], entry["kind"], entry["semantic_key"]); binding_counts[binding] = binding_counts.get(binding, 0) + 1
        id_preimages.setdefault(external_id(entry["kind"], entry["semantic_key"]), set()).add((entry["kind"], entry["semantic_key"]))
        if entry.get("sha256"): hashes.setdefault(entry["sha256"], []).append(entry["entry_id"])
    for item in existing:
        digest = str(item.get("sha256", "")).removeprefix("sha256:")
        if digest: hashes.setdefault(digest, []).append(external_id(item["kind"], item["semantic_key"]))
    for entry in raw:
        binding = (entry["role"], entry["kind"], entry["semantic_key"]); prior = bindings.get(binding)
        reasons = []
        if path_counts[entry["filename"].casefold()] > 1: reasons.append("portable_path")
        if binding_counts[binding] > 1: reasons.append("binding")
        if len(id_preimages[external_id(entry["kind"], entry["semantic_key"])]) > 1: reasons.append("external_id")
        conflict = bool(reasons); entry["conflict_reasons"] = reasons
        if entry.get("invalid_reason"):
            entry["status"] = "invalid"
        else:
            entry["status"] = "conflict" if conflict else "new" if prior is None else "unchanged" if (prior.get("filename"), prior.get("declared_size_bytes"), str(prior.get("sha256", "")).removeprefix("sha256:")) == (entry["filename"], entry["size_bytes"], entry["sha256"]) else "changed"
        duplicates = [identifier for identifier in hashes.get(entry.get("sha256", ""), []) if identifier != entry["entry_id"]]
        entry["duplicate_of"] = duplicates[0] if duplicates else None
        if prior:
            entry["previous"] = {"size_bytes": prior.get("declared_size_bytes"), "sha256": str(prior.get("sha256", "")).removeprefix("sha256:")}
    raw.sort(key=lambda item: item["filename"])
    candidate = serialize_declarations(_candidate(existing, raw))
    current = (repo / "research/external-artifacts.toml").read_bytes()
    diff = "".join(difflib.unified_diff(current.decode().splitlines(True), candidate.decode().splitlines(True), fromfile="research/external-artifacts.toml", tofile="research/external-artifacts.toml"))
    totals = {status: sum(item["status"] == status for item in raw) for status in STATUSES}
    return {"schema_version": SCHEMA_VERSION, "entries": raw, "ignored_unsupported_count": ignored, "status_counts": totals, "total_bytes": sum(item["size_bytes"] for item in raw), "candidate_declaration_sha256": "sha256:" + sha256(candidate), "declaration_diff": diff, **fingerprints}


class PreviewStore:
    def __init__(self, repo: Path, root: Path, drafts: Path):
        self.repo = repo.resolve(); self.root = root.resolve(); self.drafts = drafts.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700); os.chmod(self.root, 0o700)
        self.cleanup()

    def _path(self, preview_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", preview_id):
            raise ValueError("invalid preview ID")
        return confined(self.root, preview_id)

    def create(self, workflow_fingerprint: str) -> dict[str, Any]:
        preview_id = uuid.uuid4().hex; path = self._path(preview_id); path.mkdir(mode=0o700)
        value = {"schema_version": SCHEMA_VERSION, "preview_id": preview_id, "created_at": time.time(), "expires_at": time.time() + PREVIEW_TTL_SECONDS, "workflow_fingerprint": workflow_fingerprint, "declaration_fingerprint": declaration_fingerprint(self.repo), "draft_fingerprint": draft_fingerprint(self.drafts), "state": "open", "entries": [], "ignored_unsupported_count": 0}
        atomic_json(path / "preview.json", value); return value

    def load(self, preview_id: str, *, allow_expired: bool = False) -> dict[str, Any]:
        path = self._path(preview_id) / "preview.json"
        if not path.is_file(): raise ValueError("preview not found")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not allow_expired and (value.get("state") == "cancelled" or time.time() >= value["expires_at"]): raise RuntimeError("preview expired or cancelled")
        return value

    def save(self, value: dict[str, Any]) -> None:
        atomic_json(self._path(value["preview_id"]) / "preview.json", value)

    def scan_folder(self, folder: Path, workflow_fingerprint: str, role: str = "target_cf") -> dict[str, Any]:
        if role not in ROLES: raise ValueError("invalid external artifact role")
        folder = folder.resolve()
        if not folder.is_dir(): raise ValueError("folder does not exist")
        preview = self.create(workflow_fingerprint); ignored = 0; entries: list[dict[str, Any]] = []; total = 0
        for path in sorted(folder.rglob("*"), key=lambda item: unicodedata.normalize("NFC", item.relative_to(folder).as_posix())):
            relative_raw = path.relative_to(folder).as_posix(); mode = path.lstat().st_mode
            if stat.S_ISDIR(mode): continue
            kind = _kind(relative_raw)
            if kind is None:
                if stat.S_ISREG(mode): ignored += 1
                continue
            relative = normalize_path(relative_raw)
            if len(entries) >= MAX_CANDIDATES: raise ValueError("folder exceeds 10000 candidates")
            if not stat.S_ISREG(mode) or path.is_symlink() or path.stat(follow_symlinks=False).st_nlink != 1:
                entries.append({"entry_id": uuid.uuid4().hex, "filename": relative, "kind": kind, "size_bytes": 0, "sha256": "", "role": role, "semantic_key": _key(relative), "stored_name": "", "invalid_reason": "link_or_special_file"}); continue
            if folder not in path.resolve().parents: raise ValueError(f"path escapes selected folder: {relative}")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)); before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                os.close(descriptor); entries.append({"entry_id": uuid.uuid4().hex, "filename": relative, "kind": kind, "size_bytes": 0, "sha256": "", "role": role, "semantic_key": _key(relative), "stored_name": "", "invalid_reason": "link_or_special_file"}); continue
            if before.st_size <= 0 or before.st_size > MAX_FILE_BYTES: os.close(descriptor); raise ValueError(f"invalid file size: {relative}")
            target = self._path(preview["preview_id"]) / "bytes" / uuid.uuid4().hex; target.parent.mkdir(mode=0o700, exist_ok=True)
            with os.fdopen(descriptor, "rb", buffering=0) as source, target.open("xb") as output: size, digest = _hash_stream(source, output); after = os.fstat(source.fileno()); output.flush(); os.fsync(output.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns): raise RuntimeError(f"file changed during scan: {relative}")
            total += size
            if total > MAX_PREVIEW_BYTES: raise ValueError("folder exceeds 20 GiB")
            entries.append({"entry_id": target.name, "filename": relative, "kind": kind, "size_bytes": size, "sha256": digest, "role": role, "semantic_key": _key(relative), "stored_name": target.name, "source": {"device": before.st_dev, "inode": before.st_ino, "size": before.st_size, "mtime_ns": before.st_mtime_ns, "path": str(path)}})
        result = classify(self.repo, entries, ignored, {key: preview[key] for key in ("workflow_fingerprint", "declaration_fingerprint", "draft_fingerprint")})
        preview.update(result); preview["state"] = "finalized"; self.save(preview); return preview

    def create_browser(self, paths: list[str], sizes: list[int], workflow_fingerprint: str, role: str = "target_cf") -> dict[str, Any]:
        if role not in ROLES or len(paths) != len(sizes) or len(paths) > MAX_CANDIDATES:
            raise ValueError("invalid browser folder manifest")
        normalized = strip_browser_root(paths); preview = self.create(workflow_fingerprint); entries = []; ignored = 0; total = 0; folded: set[str] = set()
        for relative, size in zip(normalized, sizes, strict=True):
            kind = _kind(relative)
            if kind is None: ignored += 1; continue
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size > MAX_FILE_BYTES: raise ValueError("invalid browser entry size")
            if relative.casefold() in folded: raise ValueError("duplicate portable browser path")
            folded.add(relative.casefold()); total += size
            if total > MAX_PREVIEW_BYTES: raise ValueError("folder exceeds 20 GiB")
            identifier = uuid.uuid4().hex
            entries.append({"entry_id": identifier, "filename": relative, "kind": kind, "size_bytes": size, "role": role, "semantic_key": _key(relative), "stored_name": identifier, "received": False})
        if shutil.disk_usage(self.root).free < total + MINIMUM_FREE_BYTES: raise OSError("folder preview requires declared bytes plus 1 GiB free space")
        preview.update(entries=entries, ignored_unsupported_count=ignored, total_bytes=total, state="uploading"); self.save(preview)
        return {key: preview[key] for key in ("schema_version", "preview_id", "expires_at", "workflow_fingerprint", "declaration_fingerprint", "draft_fingerprint", "ignored_unsupported_count", "total_bytes")} | {"entries": [{key: item[key] for key in ("entry_id", "filename", "kind", "size_bytes")} for item in entries]}

    def uploaded(self, preview_id: str, entry_id: str, size: int, digest: str) -> dict[str, Any]:
        preview = self.load(preview_id)
        if preview.get("state") != "uploading": raise ValueError("preview is not accepting uploads")
        entry = next((item for item in preview["entries"] if item["entry_id"] == entry_id), None)
        if entry is None or size != entry["size_bytes"] or not re.fullmatch(r"[0-9a-f]{64}", digest): raise ValueError("upload does not match preview entry")
        entry.update(received=True, sha256=digest); self.save(preview)
        return {"entry_id": entry_id, "size_bytes": size, "sha256": digest}

    def finalize_browser(self, preview_id: str) -> dict[str, Any]:
        preview = self.load(preview_id)
        if preview.get("state") != "uploading" or not preview["entries"] or any(not item.get("received") for item in preview["entries"]): raise ValueError("browser preview uploads are incomplete")
        result = classify(self.repo, preview["entries"], preview["ignored_unsupported_count"], {key: preview[key] for key in ("workflow_fingerprint", "declaration_fingerprint", "draft_fingerprint")})
        preview.update(result); preview["state"] = "finalized"; self.save(preview); return preview

    def cancel(self, preview_id: str) -> dict[str, Any]:
        value = self.load(preview_id); value["state"] = "cancelled"; self.save(value); shutil.rmtree(self._path(preview_id) / "bytes", ignore_errors=True); return {"status": "cancelled"}

    def cleanup(self) -> int:
        removed = 0
        for path in self.root.iterdir():
            try: value = self.load(path.name, allow_expired=True)
            except (OSError, ValueError, json.JSONDecodeError): continue
            if value.get("state") == "cancelled" or time.time() >= value.get("expires_at", 0): shutil.rmtree(path); removed += 1
        return removed

    def confirm(self, preview_id: str, selection: list[dict[str, Any]], *, workflow_fingerprint: str, expected_declaration_fingerprint: str, expected_draft_fingerprint: str) -> dict[str, Any]:
        preview = self.load(preview_id)
        if preview.get("state") not in {"finalized", "staging_incomplete"}:
            raise ValueError("preview is not confirmable")
        if preview["workflow_fingerprint"] != workflow_fingerprint:
            raise RuntimeError("stale workflow fingerprint")
        current_declaration = declaration_fingerprint(self.repo)
        if preview.get("state") == "staging_incomplete":
            if expected_declaration_fingerprint not in {preview["declaration_fingerprint"], preview.get("committed_declaration_fingerprint")} or current_declaration != preview.get("committed_declaration_fingerprint"):
                raise RuntimeError("committed declaration changed before staging retry")
        elif preview["declaration_fingerprint"] != expected_declaration_fingerprint or current_declaration != expected_declaration_fingerprint:
            raise RuntimeError("stale external artifact declaration fingerprint")
        if preview["draft_fingerprint"] != expected_draft_fingerprint and preview.get("state") != "staging_incomplete":
            raise RuntimeError("stale upload draft fingerprint")
        selected = resolve_selection(preview, selection)
        for entry in selected:
            identifier = external_id(entry["kind"], entry["semantic_key"]); target = confined(self.drafts, f'{entry["role"]}/{identifier}/{entry["filename"]}')
            if preview.get("state") == "staging_incomplete" and target.is_file():
                with target.open("rb") as stream: staged_size, staged_digest = _hash_stream(stream)
                if (staged_size, staged_digest) == (entry["size_bytes"], entry["sha256"]): continue
            stored = self._path(preview_id) / "bytes" / entry["stored_name"]
            if not stored.is_file(): raise RuntimeError("preview bytes are missing")
            with stored.open("rb") as stream: size, digest = _hash_stream(stream)
            if (size, digest) != (entry["size_bytes"], entry["sha256"]): raise RuntimeError("preview bytes changed")
            source = entry.get("source")
            if source:
                descriptor = os.open(Path(source["path"]), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)); before = os.fstat(descriptor)
                with os.fdopen(descriptor, "rb", buffering=0) as stream: source_size, source_digest = _hash_stream(stream); after = os.fstat(stream.fileno())
                expected_stat = (source["device"], source["inode"], source["size"], source["mtime_ns"])
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != expected_stat or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != expected_stat or (source_size, source_digest) != (entry["size_bytes"], entry["sha256"]): raise RuntimeError("source file changed after preview")
        needed = sum(item["size_bytes"] for item in selected)
        self.drafts.mkdir(parents=True, exist_ok=True, mode=0o700)
        if shutil.disk_usage(self.drafts).free < needed + MINIMUM_FREE_BYTES:
            raise OSError("folder import requires declared bytes plus 1 GiB free space")
        existing = load_contract(self.repo)[1].get("artifacts", [])
        candidate_items = _candidate(existing, selected, {item["entry_id"] for item in selected})
        candidate_bindings = [(item["role"], item["kind"], item["semantic_key"]) for item in candidate_items]
        candidate_ids = [external_id(item["kind"], item["semantic_key"]) for item in candidate_items]
        if len(set(candidate_bindings)) != len(candidate_bindings) or len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("complete external artifact declaration collides")
        candidate = serialize_declarations(candidate_items)
        candidate_hash = "sha256:" + sha256(candidate)
        if preview.get("state") != "staging_incomplete":
            atomic_bytes(self.repo / "research/external-artifacts.toml", candidate)
            preview["committed_declaration_fingerprint"] = candidate_hash
        elif declaration_fingerprint(self.repo) != preview.get("committed_declaration_fingerprint"):
            raise RuntimeError("committed declaration changed before staging retry")
        missing: list[str] = []
        for entry in selected:
            identifier = external_id(entry["kind"], entry["semantic_key"]); target = confined(self.drafts, f'{entry["role"]}/{identifier}/{entry["filename"]}')
            try:
                if target.is_file():
                    with target.open("rb") as stream: size, digest = _hash_stream(stream)
                    if (size, digest) == (entry["size_bytes"], entry["sha256"]): continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
                with (self._path(preview_id) / "bytes" / entry["stored_name"]).open("rb") as source, temporary.open("xb") as output:
                    size, digest = _hash_stream(source, output); output.flush(); os.fsync(output.fileno())
                if (size, digest) != (entry["size_bytes"], entry["sha256"]): raise RuntimeError("staged bytes changed")
                os.replace(temporary, target)
            except (OSError, RuntimeError, ValueError):
                missing.append(identifier)
        preview["selected_entries"] = [{key: item[key] for key in ("entry_id", "role", "semantic_key")} for item in selected]
        preview["state"] = "staging_incomplete" if missing else "complete"; preview["missing_external_ids"] = sorted(missing); self.save(preview)
        if missing:
            required_names = {item["stored_name"] for item in selected if external_id(item["kind"], item["semantic_key"]) in missing}
            for path in (self._path(preview_id) / "bytes").iterdir():
                if path.name not in required_names: path.unlink()
            return {"status": "staging_incomplete", "missing_external_ids": sorted(missing), "declaration_fingerprint": candidate_hash, "draft_fingerprint": draft_fingerprint(self.drafts), "acquisition_pending": True}
        shutil.rmtree(self._path(preview_id) / "bytes", ignore_errors=True)
        return {"status": "complete", "missing_external_ids": [], "declaration_fingerprint": candidate_hash, "draft_fingerprint": draft_fingerprint(self.drafts), "acquisition_pending": True}

    def declaration_diff(self, preview_id: str, selection: list[dict[str, Any]], expected_declaration_fingerprint: str) -> dict[str, Any]:
        preview = self.load(preview_id)
        if preview.get("state") != "finalized" or preview["declaration_fingerprint"] != expected_declaration_fingerprint or declaration_fingerprint(self.repo) != expected_declaration_fingerprint: raise RuntimeError("stale external artifact declaration fingerprint")
        selected = resolve_selection(preview, selection); existing = load_contract(self.repo)[1].get("artifacts", []); items = _candidate(existing, selected, {item["entry_id"] for item in selected})
        bindings = [(item["role"], item["kind"], item["semantic_key"]) for item in items]; ids = [external_id(item["kind"], item["semantic_key"]) for item in items]
        if len(set(bindings)) != len(bindings) or len(set(ids)) != len(ids): raise ValueError("complete external artifact declaration collides")
        candidate = serialize_declarations(items)
        current = (self.repo / "research/external-artifacts.toml").read_bytes(); diff = "".join(difflib.unified_diff(current.decode().splitlines(True), candidate.decode().splitlines(True), fromfile="research/external-artifacts.toml", tofile="research/external-artifacts.toml"))
        return {"schema_version": SCHEMA_VERSION, "selected_count": len(selected), "candidate_declaration_sha256": "sha256:" + sha256(candidate), "diff": diff}
