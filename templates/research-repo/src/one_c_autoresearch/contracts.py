from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = "1"
ROLES = ("vendor_baseline", "target_cf", "next_vendor")
DECISIONS = ("adopt_vendor", "adapt", "retain_custom", "out_of_scope")
MRQ_STATES = ("draft", "ready_for_review", "approved", "superseded")
SECRET_KEYS = re.compile(r"(?:password|passwd|pwd|token|secret|private[_-]?key)", re.I)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def content_id(prefix: str, preimage: dict[str, Any]) -> str:
    return prefix + sha256(canonical_json(preimage))[:16].upper()


def external_id(kind: str, semantic_key: str, schema_version: str = SCHEMA_VERSION) -> str:
    return content_id("EXT-", {"kind": kind, "schema_version": schema_version, "semantic_key": _nfc(semantic_key)})


def mrq_id(semantic_key: str, schema_version: str = SCHEMA_VERSION) -> str:
    return content_id("MRQ-", {"schema_version": schema_version, "semantic_key": _nfc(semantic_key)})


def comparison_id(kind: str, before: str, after: str, profile: str, representation: str, normalizer: str, schema_version: str = SCHEMA_VERSION) -> str:
    return content_id("CMP-", {
        "acquisition_profile_id": profile,
        "after_role": after,
        "before_role": before,
        "comparison_kind": kind,
        "normalizer_version": normalizer,
        "representation_schema": representation,
        "schema_version": schema_version,
    })


def diff_id(comparison: str, path: str, change_type: str, schema_version: str = SCHEMA_VERSION) -> str:
    return content_id("DIF-", {"change_type": change_type, "comparison_id": comparison, "path": normalize_relative(path), "schema_version": schema_version})


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def normalize_relative(value: str) -> str:
    path = _nfc(value.replace("\\", "/"))
    if not path or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(f"unsafe relative path: {value!r}")
    return path


def confined(root: Path, value: str | Path) -> Path:
    root = root.resolve()
    candidate = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes repository: {value}")
    return candidate


def file_manifest(root: Path) -> list[dict[str, Any]]:
    root = root.resolve()
    rows: list[dict[str, Any]] = []
    inode_seen: set[tuple[int, int]] = set()
    for path in sorted(root.rglob("*"), key=lambda item: _nfc(item.relative_to(root).as_posix())):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode) or path.is_symlink():
            raise ValueError(f"unsupported source file type: {path}")
        inode = (path.stat().st_dev, path.stat().st_ino)
        if inode in inode_seen or path.stat().st_nlink != 1:
            raise ValueError(f"hardlink is forbidden: {path}")
        inode_seen.add(inode)
        relative = normalize_relative(path.relative_to(root).as_posix())
        payload = path.read_bytes()
        rows.append({"path": relative, "sha256": sha256(payload), "size_bytes": len(payload)})
    return rows


def tree_fingerprint(root: Path) -> str:
    return "sha256:" + sha256(canonical_json(file_manifest(root)))


def require_tracked_clean(repo: Path, paths: list[Path]) -> None:
    repo = repo.resolve()
    required: set[str] = set()
    selectors: list[str] = []
    for path in paths:
        path = confined(repo, path)
        selectors.append(path.relative_to(repo).as_posix())
        candidates = [path] if path.is_file() or path.is_symlink() else list(path.rglob("*"))
        for candidate in candidates:
            if candidate.is_dir():
                continue
            mode = candidate.lstat().st_mode
            if not stat.S_ISREG(mode) or candidate.is_symlink() or candidate.stat().st_nlink != 1:
                raise ValueError(f"canonical path has unsupported file type: {candidate.relative_to(repo)}")
            required.add(candidate.relative_to(repo).as_posix())
    selected = sorted(required)
    path_input = ("\0".join(selected) + "\0").encode()
    tracked = set(subprocess.run(["git", "ls-files", "-z"], cwd=repo, stdout=subprocess.PIPE, check=True).stdout.decode().split("\0"))
    if not required <= tracked:
        raise ValueError(f"canonical path is not tracked: {sorted(required - tracked)[0]}")
    ignored = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-z"], cwd=repo, input=path_input, stdout=subprocess.PIPE, check=False).stdout.decode().split("\0")
    if any(ignored):
        raise ValueError(f"canonical path is ignored: {next(item for item in ignored if item)}")
    dirty = subprocess.run(["git", "status", "--porcelain=v1", "--", *selectors], cwd=repo, stdout=subprocess.PIPE, check=True, text=True).stdout
    if dirty:
        raise ValueError("canonical paths are dirty relative to HEAD")


def reject_secrets(value: Any, location: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEYS.search(str(key)) and child not in (None, "", [], {}):
                raise ValueError(f"secret value is forbidden at {location}.{key}")
            reject_secrets(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secrets(child, f"{location}[{index}]")


def validate_unique_ids(items: list[dict[str, Any]], id_key: str, preimage_key: str) -> None:
    by_id: dict[str, bytes] = {}
    by_preimage: dict[bytes, str] = {}
    for item in items:
        identifier = str(item[id_key])
        preimage = canonical_json(item[preimage_key])
        if identifier in by_id and by_id[identifier] != preimage:
            raise ValueError(f"truncated hash collision: {identifier}")
        if preimage in by_preimage and by_preimage[preimage] != identifier:
            raise ValueError(f"one preimage has multiple IDs: {identifier}")
        by_id[identifier] = preimage
        by_preimage[preimage] = identifier


@contextmanager
def repository_lock(repo: Path, timeout_seconds: float = 0) -> Iterator[None]:
    identity = sha256(str(repo.resolve()).encode())
    lock_dir = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) / "one-c-autoresearch-locks"
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(lock_dir / f"{identity}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if timeout_seconds == 0 else 0)
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError as exc:
            raise RuntimeError("repository writer is busy") from exc
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def atomic_json(path: Path, value: Any) -> None:
    atomic_bytes(path, canonical_json(value) + b"\n")


def atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
