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
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, NotRequired, TypedDict

from .contracts import ROLES, atomic_bytes, atomic_json, confined, external_id, json_array, json_object, parse_json_object, sha256
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


class Artifact(TypedDict):
    role: str
    kind: str
    semantic_key: str
    filename: str
    declared_size_bytes: int
    sha256: str
    external_artifact_id: NotRequired[str]


class SourceStat(TypedDict):
    device: int
    inode: int
    size: int
    mtime_ns: int
    path: str


class Entry(TypedDict):
    entry_id: str
    filename: str
    kind: str
    size_bytes: int
    role: str
    semantic_key: str
    stored_name: str
    sha256: NotRequired[str]
    status: NotRequired[str]
    conflict_reasons: NotRequired[list[str]]
    duplicate_of: NotRequired[str | None]
    invalid_reason: NotRequired[str]
    previous: NotRequired[dict[str, int | str | None]]
    source: NotRequired[SourceStat]
    received: NotRequired[bool]


class Selection(TypedDict, total=False):
    entry_id: str
    role: str
    semantic_key: str


class Classification(TypedDict):
    schema_version: str
    entries: list[Entry]
    ignored_unsupported_count: int
    status_counts: dict[str, int]
    total_bytes: int
    candidate_declaration_sha256: str
    declaration_diff: str
    workflow_fingerprint: str
    declaration_fingerprint: str
    draft_fingerprint: str


class Preview(TypedDict):
    schema_version: str
    preview_id: str
    created_at: float
    expires_at: float
    workflow_fingerprint: str
    declaration_fingerprint: str
    draft_fingerprint: str
    state: str
    entries: list[Entry]
    ignored_unsupported_count: int
    committed_declaration_fingerprint: NotRequired[str]
    selected_entries: NotRequired[list[Selection]]
    total_bytes: NotRequired[int]
    status_counts: NotRequired[dict[str, int]]
    candidate_declaration_sha256: NotRequired[str]
    declaration_diff: NotRequired[str]
    missing_external_ids: NotRequired[list[str]]


def _artifacts(value: object) -> list[Artifact]:
    result: list[Artifact] = []
    for raw in json_array(value):
        item = json_object(raw)
        role, kind, key = item.get("role"), item.get("kind"), item.get("semantic_key")
        filename, size, digest = item.get("filename"), item.get("declared_size_bytes"), item.get("sha256")
        if not isinstance(role, str) or not isinstance(kind, str) or not isinstance(key, str) or not isinstance(filename, str) or not isinstance(digest, str) or not isinstance(size, int) or isinstance(size, bool):
            raise ValueError("invalid external artifact declaration")
        result.append({"role": role, "kind": kind, "semantic_key": key, "filename": filename, "declared_size_bytes": size, "sha256": digest})
    return result


def _preview(text: str) -> Preview:
    value = parse_json_object(text)
    required = {
        "schema_version", "preview_id", "created_at", "expires_at", "workflow_fingerprint",
        "declaration_fingerprint", "draft_fingerprint", "state", "entries", "ignored_unsupported_count",
    }
    if not required <= value.keys():
        raise ValueError("invalid folder preview")
    def string(key: str) -> str:
        item = value.get(key)
        if not isinstance(item, str):
            raise ValueError("invalid folder preview")
        return item
    def number(key: str) -> float:
        item = value.get(key)
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            raise ValueError("invalid folder preview")
        return float(item)
    ignored = value.get("ignored_unsupported_count")
    if not isinstance(ignored, int) or isinstance(ignored, bool):
        raise ValueError("invalid folder preview")
    entries: list[Entry] = []
    for raw in json_array(value.get("entries")):
        item = json_object(raw)
        required_entry = ("entry_id", "filename", "kind", "size_bytes", "role", "semantic_key", "stored_name")
        if not all(isinstance(item.get(key), str) for key in required_entry if key != "size_bytes"):
            raise ValueError("invalid folder preview entry")
        size = item.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool):
            raise ValueError("invalid folder preview entry")
        entry: Entry = {
            "entry_id": str(item["entry_id"]), "filename": str(item["filename"]),
            "kind": str(item["kind"]), "size_bytes": size, "role": str(item["role"]),
            "semantic_key": str(item["semantic_key"]), "stored_name": str(item["stored_name"]),
        }
        for key in ("sha256", "status", "invalid_reason"):
            child = item.get(key)
            if child is not None:
                if not isinstance(child, str): raise ValueError("invalid folder preview entry")
                entry[key] = child
        reasons = item.get("conflict_reasons")
        if reasons is not None:
            values = json_array(reasons)
            if not all(isinstance(child, str) for child in values): raise ValueError("invalid folder preview entry")
            entry["conflict_reasons"] = [child for child in values if isinstance(child, str)]
        duplicate = item.get("duplicate_of")
        if duplicate is not None and not isinstance(duplicate, str): raise ValueError("invalid folder preview entry")
        if "duplicate_of" in item: entry["duplicate_of"] = duplicate
        received = item.get("received")
        if received is not None:
            if not isinstance(received, bool): raise ValueError("invalid folder preview entry")
            entry["received"] = received
        previous = item.get("previous")
        if previous is not None:
            previous = json_object(previous)
            if not all(child is None or isinstance(child, (int, str)) and not isinstance(child, bool) for child in previous.values()):
                raise ValueError("invalid folder preview entry")
            entry["previous"] = {
                key: child for key, child in previous.items()
                if child is None or isinstance(child, (int, str)) and not isinstance(child, bool)
            }
        source = item.get("source")
        if source is not None:
            source = json_object(source)
            device, inode, source_size, mtime_ns, path = (source.get(key) for key in ("device", "inode", "size", "mtime_ns", "path"))
            if not all(isinstance(child, int) and not isinstance(child, bool) for child in (device, inode, source_size, mtime_ns)) or not isinstance(path, str):
                raise ValueError("invalid folder preview source")
            assert isinstance(device, int) and isinstance(inode, int) and isinstance(source_size, int) and isinstance(mtime_ns, int)
            entry["source"] = {"device": device, "inode": inode, "size": source_size, "mtime_ns": mtime_ns, "path": path}
        entries.append(entry)
    preview: Preview = {
        "schema_version": string("schema_version"), "preview_id": string("preview_id"),
        "created_at": number("created_at"), "expires_at": number("expires_at"),
        "workflow_fingerprint": string("workflow_fingerprint"),
        "declaration_fingerprint": string("declaration_fingerprint"),
        "draft_fingerprint": string("draft_fingerprint"), "state": string("state"),
        "entries": entries, "ignored_unsupported_count": ignored,
    }
    committed = value.get("committed_declaration_fingerprint")
    if committed is not None:
        if not isinstance(committed, str): raise ValueError("invalid folder preview")
        preview["committed_declaration_fingerprint"] = committed
    for key in ("candidate_declaration_sha256", "declaration_diff"):
        child = value.get(key)
        if child is not None:
            if not isinstance(child, str): raise ValueError("invalid folder preview")
            preview[key] = child
    for key in ("total_bytes",):
        child = value.get(key)
        if child is not None:
            if not isinstance(child, int) or isinstance(child, bool): raise ValueError("invalid folder preview")
            preview[key] = child
    counts = value.get("status_counts")
    if counts is not None:
        counts = json_object(counts)
        if not all(isinstance(child, int) and not isinstance(child, bool) for child in counts.values()): raise ValueError("invalid folder preview")
        preview["status_counts"] = {key: child for key, child in counts.items() if isinstance(child, int) and not isinstance(child, bool)}
    for key in ("missing_external_ids",):
        child = value.get(key)
        if child is not None:
            values = json_array(child)
            if not all(isinstance(item, str) for item in values): raise ValueError("invalid folder preview")
            preview[key] = [item for item in values if isinstance(item, str)]
    selected = value.get("selected_entries")
    if selected is not None:
        selections: list[Selection] = []
        for raw in json_array(selected):
            item = json_object(raw)
            selection: Selection = {}
            for key in ("entry_id", "role", "semantic_key"):
                child = item.get(key)
                if child is not None:
                    if not isinstance(child, str): raise ValueError("invalid folder preview selection")
                    selection[key] = child
            selections.append(selection)
        preview["selected_entries"] = selections
    return preview


def public_preview(value: Preview) -> dict[str, object]:
    hidden = {"source", "stored_name"}
    result: dict[str, object] = {key: item for key, item in value.items() if key != "entries"}
    result["entries"] = [{key: item for key, item in entry.items() if key not in hidden} for entry in value.get("entries", [])]
    return result


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
            _ = output.write(chunk)
    return size, digest.hexdigest()


def serialize_declarations(items: list[Artifact]) -> bytes:
    def quoted(value: str) -> str:
        return json.dumps(unicodedata.normalize("NFC", value), ensure_ascii=False)

    lines = ['schema_version = "1"', ""]
    for item in sorted(items, key=lambda row: (row["role"], row["kind"], row["semantic_key"])):
        digest = str(item["sha256"]).removeprefix("sha256:").lower()
        if item["role"] not in ROLES or item["kind"] not in {"epf", "erf"} or not str(item["semantic_key"]) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid external artifact declaration candidate")
        filename = normalize_path(str(item["filename"]))
        size = item["declared_size_bytes"]
        if isinstance(size, bool) or size <= 0:
            raise ValueError("invalid external artifact declaration size")
        lines.extend(("[[artifacts]]", f'role = {quoted(item["role"])}', f'kind = {quoted(item["kind"])}', f'semantic_key = {quoted(item["semantic_key"])}', f'filename = {quoted(filename)}', f"declared_size_bytes = {size}", f'sha256 = "{digest}"', ""))
    return ("\n".join(lines)).encode()


def declaration_fingerprint(repo: Path) -> str:
    return "sha256:" + sha256((repo / "research/external-artifacts.toml").read_bytes())


def _candidate(existing: list[Artifact], entries: list[Entry], selected: set[str] | None = None) -> list[Artifact]:
    selected = selected if selected is not None else {entry["entry_id"] for entry in entries if entry.get("status") not in {"invalid", "conflict"}}
    by_binding: dict[tuple[str, str, str], Artifact] = {
        (item["role"], item["kind"], item["semantic_key"]): {
            "role": item["role"], "kind": item["kind"], "semantic_key": item["semantic_key"],
            "filename": item["filename"], "declared_size_bytes": item["declared_size_bytes"], "sha256": item["sha256"],
        }
        for item in existing
    }
    for entry in entries:
        if entry["entry_id"] not in selected:
            continue
        digest = entry.get("sha256")
        if digest is None:
            raise ValueError("selected external artifact has no digest")
        by_binding[(entry["role"], entry["kind"], entry["semantic_key"])] = {"role": entry["role"], "kind": entry["kind"], "semantic_key": entry["semantic_key"], "filename": entry["filename"], "declared_size_bytes": entry["size_bytes"], "sha256": digest}
    return list(by_binding.values())


def resolve_selection(preview: Preview, selection: Sequence[object]) -> list[Entry]:
    if not selection: raise ValueError("folder confirmation requires a non-empty selection")
    by_id = {entry["entry_id"]: entry for entry in preview["entries"]}; selected: list[Entry] = []; seen: set[str] = set()
    for choice in selection:
        choice = json_object(choice)
        if set(choice) - {"entry_id", "role", "semantic_key"} or not isinstance(choice.get("entry_id"), str): raise ValueError("invalid folder confirmation selection")
        identifier = choice.get("entry_id")
        assert isinstance(identifier, str)
        if identifier in seen or identifier not in by_id: raise ValueError("unknown or duplicate preview entry")
        seen.add(identifier); entry = by_id[identifier].copy()
        if entry.get("status") == "invalid" or entry.get("status") == "conflict" and ("portable_path" in entry.get("conflict_reasons", []) or not ({"role", "semantic_key"} & set(choice))): raise ValueError("invalid or unresolved conflicting entry cannot be selected")
        role = choice.get("role", entry["role"]); key = unicodedata.normalize("NFC", str(choice.get("semantic_key", entry["semantic_key"])))
        if not isinstance(role, str) or role not in ROLES or not key or any(ord(char) < 32 or ord(char) == 127 for char in key): raise ValueError("invalid confirmed role or semantic key")
        entry.update(role=role, semantic_key=key); selected.append(entry)
    bindings = [(item["role"], item["kind"], item["semantic_key"]) for item in selected]; ids = [external_id(item["kind"], item["semantic_key"]) for item in selected]
    if len(set(bindings)) != len(bindings) or len(set(ids)) != len(ids): raise ValueError("confirmed external artifact identities collide")
    return selected


def classify(repo: Path, raw: list[Entry], ignored: int, fingerprints: dict[str, str]) -> Classification:
    existing = _artifacts(load_contract(repo)[1].get("artifacts", []))
    bindings = {(item["role"], item["kind"], item["semantic_key"]): item for item in existing}
    path_counts: dict[str, int] = {}; binding_counts: dict[tuple[str, str, str], int] = {}; id_preimages: dict[str, set[tuple[str, str]]] = {}; hashes: dict[str, list[str]] = {}
    for entry in raw:
        path_counts[entry["filename"].casefold()] = path_counts.get(entry["filename"].casefold(), 0) + 1
        binding = (entry["role"], entry["kind"], entry["semantic_key"]); binding_counts[binding] = binding_counts.get(binding, 0) + 1
        id_preimages.setdefault(external_id(entry["kind"], entry["semantic_key"]), set()).add((entry["kind"], entry["semantic_key"]))
        if digest := entry.get("sha256"): hashes.setdefault(digest, []).append(entry["entry_id"])
    for item in existing:
        digest = str(item.get("sha256", "")).removeprefix("sha256:")
        if digest: hashes.setdefault(digest, []).append(external_id(item["kind"], item["semantic_key"]))
    for entry in raw:
        binding = (entry["role"], entry["kind"], entry["semantic_key"]); prior = bindings.get(binding)
        reasons: list[str] = []
        if path_counts[entry["filename"].casefold()] > 1: reasons.append("portable_path")
        if binding_counts[binding] > 1: reasons.append("binding")
        if len(id_preimages[external_id(entry["kind"], entry["semantic_key"])]) > 1: reasons.append("external_id")
        conflict = bool(reasons); entry["conflict_reasons"] = reasons
        if entry.get("invalid_reason"):
            entry["status"] = "invalid"
        else:
            entry["status"] = "conflict" if conflict else "new" if prior is None else "unchanged" if (prior.get("filename"), prior.get("declared_size_bytes"), str(prior.get("sha256", "")).removeprefix("sha256:")) == (entry["filename"], entry["size_bytes"], entry.get("sha256", "")) else "changed"
        duplicates = [identifier for identifier in hashes.get(entry.get("sha256", ""), []) if identifier != entry["entry_id"]]
        entry["duplicate_of"] = duplicates[0] if duplicates else None
        if prior:
            entry["previous"] = {"size_bytes": prior.get("declared_size_bytes"), "sha256": str(prior.get("sha256", "")).removeprefix("sha256:")}
    raw.sort(key=lambda item: item["filename"])
    candidate = serialize_declarations(_candidate(existing, raw))
    current = (repo / "research/external-artifacts.toml").read_bytes()
    diff = "".join(difflib.unified_diff(current.decode().splitlines(True), candidate.decode().splitlines(True), fromfile="research/external-artifacts.toml", tofile="research/external-artifacts.toml"))
    totals = {status: sum(item.get("status") == status for item in raw) for status in STATUSES}
    return {
        "schema_version": SCHEMA_VERSION, "entries": raw, "ignored_unsupported_count": ignored,
        "status_counts": totals, "total_bytes": sum(item["size_bytes"] for item in raw),
        "candidate_declaration_sha256": "sha256:" + sha256(candidate), "declaration_diff": diff,
        "workflow_fingerprint": fingerprints["workflow_fingerprint"],
        "declaration_fingerprint": fingerprints["declaration_fingerprint"],
        "draft_fingerprint": fingerprints["draft_fingerprint"],
    }


class PreviewStore:
    def __init__(self, repo: Path, root: Path, drafts: Path):
        self.repo: Path = repo.resolve(); self.root: Path = root.resolve(); self.drafts: Path = drafts.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700); os.chmod(self.root, 0o700)
        _ = self.cleanup()

    def _path(self, preview_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", preview_id):
            raise ValueError("invalid preview ID")
        return confined(self.root, preview_id)

    def create(self, workflow_fingerprint: str) -> Preview:
        preview_id = uuid.uuid4().hex; path = self._path(preview_id); path.mkdir(mode=0o700)
        value: Preview = {"schema_version": SCHEMA_VERSION, "preview_id": preview_id, "created_at": time.time(), "expires_at": time.time() + PREVIEW_TTL_SECONDS, "workflow_fingerprint": workflow_fingerprint, "declaration_fingerprint": declaration_fingerprint(self.repo), "draft_fingerprint": draft_fingerprint(self.drafts), "state": "open", "entries": [], "ignored_unsupported_count": 0}
        atomic_json(path / "preview.json", value); return value

    def load(self, preview_id: str, *, allow_expired: bool = False) -> Preview:
        path = self._path(preview_id) / "preview.json"
        if not path.is_file(): raise ValueError("preview not found")
        value = _preview(path.read_text(encoding="utf-8"))
        if not allow_expired and (value.get("state") == "cancelled" or time.time() >= value["expires_at"]): raise RuntimeError("preview expired or cancelled")
        return value

    def save(self, value: Preview) -> None:
        atomic_json(self._path(value["preview_id"]) / "preview.json", value)

    def scan_folder(self, folder: Path, workflow_fingerprint: str, role: str = "target_cf") -> Preview:
        if role not in ROLES: raise ValueError("invalid external artifact role")
        folder = folder.resolve()
        if not folder.is_dir(): raise ValueError("folder does not exist")
        preview = self.create(workflow_fingerprint); ignored = 0; entries: list[Entry] = []; total = 0
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
        preview.update(entries=result["entries"], ignored_unsupported_count=result["ignored_unsupported_count"], status_counts=result["status_counts"], total_bytes=result["total_bytes"], candidate_declaration_sha256=result["candidate_declaration_sha256"], declaration_diff=result["declaration_diff"])
        preview["state"] = "finalized"; self.save(preview); return preview

    def create_browser(self, paths: list[str], sizes: list[int], workflow_fingerprint: str, role: str = "target_cf") -> dict[str, object]:
        if role not in ROLES or len(paths) != len(sizes) or len(paths) > MAX_CANDIDATES:
            raise ValueError("invalid browser folder manifest")
        normalized = strip_browser_root(paths); preview = self.create(workflow_fingerprint); entries: list[Entry] = []; ignored = 0; total = 0; folded: set[str] = set()
        for relative, size in zip(normalized, sizes, strict=True):
            kind = _kind(relative)
            if kind is None: ignored += 1; continue
            if isinstance(size, bool) or size <= 0 or size > MAX_FILE_BYTES: raise ValueError("invalid browser entry size")
            if relative.casefold() in folded: raise ValueError("duplicate portable browser path")
            folded.add(relative.casefold()); total += size
            if total > MAX_PREVIEW_BYTES: raise ValueError("folder exceeds 20 GiB")
            identifier = uuid.uuid4().hex
            entries.append({"entry_id": identifier, "filename": relative, "kind": kind, "size_bytes": size, "role": role, "semantic_key": _key(relative), "stored_name": identifier, "received": False})
        if shutil.disk_usage(self.root).free < total + MINIMUM_FREE_BYTES: raise OSError("folder preview requires declared bytes plus 1 GiB free space")
        preview.update(entries=entries, ignored_unsupported_count=ignored, total_bytes=total, state="uploading"); self.save(preview)
        result: dict[str, object] = {key: preview[key] for key in ("schema_version", "preview_id", "expires_at", "workflow_fingerprint", "declaration_fingerprint", "draft_fingerprint", "ignored_unsupported_count")}
        result["total_bytes"] = total
        result["entries"] = [{key: item[key] for key in ("entry_id", "filename", "kind", "size_bytes")} for item in entries]
        return result

    def uploaded(self, preview_id: str, entry_id: str, size: int, digest: str) -> dict[str, object]:
        preview = self.load(preview_id)
        if preview.get("state") != "uploading": raise ValueError("preview is not accepting uploads")
        entry = next((item for item in preview["entries"] if item["entry_id"] == entry_id), None)
        if entry is None or size != entry["size_bytes"] or not re.fullmatch(r"[0-9a-f]{64}", digest): raise ValueError("upload does not match preview entry")
        entry.update(received=True, sha256=digest); self.save(preview)
        return {"entry_id": entry_id, "size_bytes": size, "sha256": digest}

    def finalize_browser(self, preview_id: str) -> Preview:
        preview = self.load(preview_id)
        if preview.get("state") != "uploading" or not preview["entries"] or any(not item.get("received") for item in preview["entries"]): raise ValueError("browser preview uploads are incomplete")
        result = classify(self.repo, preview["entries"], preview["ignored_unsupported_count"], {key: preview[key] for key in ("workflow_fingerprint", "declaration_fingerprint", "draft_fingerprint")})
        preview.update(entries=result["entries"], ignored_unsupported_count=result["ignored_unsupported_count"], status_counts=result["status_counts"], total_bytes=result["total_bytes"], candidate_declaration_sha256=result["candidate_declaration_sha256"], declaration_diff=result["declaration_diff"])
        preview["state"] = "finalized"; self.save(preview); return preview

    def cancel(self, preview_id: str) -> dict[str, object]:
        value = self.load(preview_id); value["state"] = "cancelled"; self.save(value); shutil.rmtree(self._path(preview_id) / "bytes", ignore_errors=True); return {"status": "cancelled"}

    def cleanup(self) -> int:
        removed = 0
        for path in self.root.iterdir():
            try: value = self.load(path.name, allow_expired=True)
            except (OSError, ValueError, json.JSONDecodeError): continue
            if value.get("state") == "cancelled" or time.time() >= value.get("expires_at", 0): shutil.rmtree(path); removed += 1
        return removed

    def confirm(self, preview_id: str, selection: list[Selection], *, workflow_fingerprint: str, expected_declaration_fingerprint: str, expected_draft_fingerprint: str) -> dict[str, object]:
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
            entry_digest = entry.get("sha256")
            if entry_digest is None:
                raise RuntimeError("selected preview entry has no digest")
            identifier = external_id(entry["kind"], entry["semantic_key"]); target = confined(self.drafts, f'{entry["role"]}/{identifier}/{entry["filename"]}')
            if preview.get("state") == "staging_incomplete" and target.is_file():
                with target.open("rb") as stream: staged_size, staged_digest = _hash_stream(stream)
                if (staged_size, staged_digest) == (entry["size_bytes"], entry_digest): continue
            stored = self._path(preview_id) / "bytes" / entry["stored_name"]
            if not stored.is_file(): raise RuntimeError("preview bytes are missing")
            with stored.open("rb") as stream: size, digest = _hash_stream(stream)
            if (size, digest) != (entry["size_bytes"], entry_digest): raise RuntimeError("preview bytes changed")
            source = entry.get("source")
            if source:
                descriptor = os.open(Path(source["path"]), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)); before = os.fstat(descriptor)
                with os.fdopen(descriptor, "rb", buffering=0) as stream: source_size, source_digest = _hash_stream(stream); after = os.fstat(stream.fileno())
                expected_stat = (source["device"], source["inode"], source["size"], source["mtime_ns"])
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != expected_stat or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != expected_stat or (source_size, source_digest) != (entry["size_bytes"], entry_digest): raise RuntimeError("source file changed after preview")
        needed = sum(item["size_bytes"] for item in selected)
        self.drafts.mkdir(parents=True, exist_ok=True, mode=0o700)
        if shutil.disk_usage(self.drafts).free < needed + MINIMUM_FREE_BYTES:
            raise OSError("folder import requires declared bytes plus 1 GiB free space")
        existing = _artifacts(load_contract(self.repo)[1].get("artifacts", []))
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
            entry_digest = entry.get("sha256")
            if entry_digest is None:
                raise RuntimeError("selected preview entry has no digest")
            identifier = external_id(entry["kind"], entry["semantic_key"]); target = confined(self.drafts, f'{entry["role"]}/{identifier}/{entry["filename"]}')
            try:
                if target.is_file():
                    with target.open("rb") as stream: size, digest = _hash_stream(stream)
                    if (size, digest) == (entry["size_bytes"], entry_digest): continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
                with (self._path(preview_id) / "bytes" / entry["stored_name"]).open("rb") as source, temporary.open("xb") as output:
                    size, digest = _hash_stream(source, output); output.flush(); os.fsync(output.fileno())
                if (size, digest) != (entry["size_bytes"], entry_digest): raise RuntimeError("staged bytes changed")
                os.replace(temporary, target)
            except (OSError, RuntimeError, ValueError):
                missing.append(identifier)
        preview["selected_entries"] = [{"entry_id": item["entry_id"], "role": item["role"], "semantic_key": item["semantic_key"]} for item in selected]
        preview["state"] = "staging_incomplete" if missing else "complete"; preview["missing_external_ids"] = sorted(missing); self.save(preview)
        if missing:
            required_names = {item["stored_name"] for item in selected if external_id(item["kind"], item["semantic_key"]) in missing}
            for path in (self._path(preview_id) / "bytes").iterdir():
                if path.name not in required_names: path.unlink()
            return {"status": "staging_incomplete", "missing_external_ids": sorted(missing), "declaration_fingerprint": candidate_hash, "draft_fingerprint": draft_fingerprint(self.drafts), "acquisition_pending": True}
        shutil.rmtree(self._path(preview_id) / "bytes", ignore_errors=True)
        return {"status": "complete", "missing_external_ids": [], "declaration_fingerprint": candidate_hash, "draft_fingerprint": draft_fingerprint(self.drafts), "acquisition_pending": True}

    def declaration_diff(self, preview_id: str, selection: list[Selection], expected_declaration_fingerprint: str) -> dict[str, object]:
        preview = self.load(preview_id)
        if preview.get("state") != "finalized" or preview["declaration_fingerprint"] != expected_declaration_fingerprint or declaration_fingerprint(self.repo) != expected_declaration_fingerprint: raise RuntimeError("stale external artifact declaration fingerprint")
        selected = resolve_selection(preview, selection); existing = _artifacts(load_contract(self.repo)[1].get("artifacts", [])); items = _candidate(existing, selected, {item["entry_id"] for item in selected})
        bindings = [(item["role"], item["kind"], item["semantic_key"]) for item in items]; ids = [external_id(item["kind"], item["semantic_key"]) for item in items]
        if len(set(bindings)) != len(bindings) or len(set(ids)) != len(ids): raise ValueError("complete external artifact declaration collides")
        candidate = serialize_declarations(items)
        current = (self.repo / "research/external-artifacts.toml").read_bytes(); diff = "".join(difflib.unified_diff(current.decode().splitlines(True), candidate.decode().splitlines(True), fromfile="research/external-artifacts.toml", tofile="research/external-artifacts.toml"))
        return {"schema_version": SCHEMA_VERSION, "selected_count": len(selected), "candidate_declaration_sha256": "sha256:" + sha256(candidate), "diff": diff}
