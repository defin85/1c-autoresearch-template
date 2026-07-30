from __future__ import annotations

import json
import hashlib
import os
import selectors
import signal
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping


REFERENCE_SCHEMA = "reference-search/v1"
STRUCTURED_SCHEMA = "1"
SYNTAX_KINDS = frozenset({"type", "method", "global_function", "keyword"})
DOCS_MAX_HITS = 50
DOCS_MAX_EXCERPT_BYTES = 16 * 1024
TEXT_MAX_BYTES = 32 * 1024
ITS_ENDPOINT = "https://code.1c.ai"
ITS_QUESTION_MAX_BYTES = 4096
ITS_DEADLINE_SECONDS = 60

_PROXY_KEYS = {
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}
_SECRET_KEYS = {"NAPARNIK_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}


def _bounded_text(value: Any, maximum: int, error: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise ValueError(error)
    return value


def validate_reference_contract(contract: Mapping[str, Any]) -> None:
    if contract.get("machine_contract_version") != "1.3":
        raise ValueError("reference_search.unsupported_machine_contract")
    allowed = contract.get("profiles", {}).get("reference", {}).get("allowed", {})
    expected = {
        "search": ["find_docs", "search_docs", "status"],
        "syntax_help": [],
        "its_help": [],
    }
    structured = contract.get("structured_outputs", {}).get(
        "reference.syntax_help", {}
    )
    if (
        allowed != expected
        or structured.get("schema_version") != 1
        or not str(structured.get("output_schema_fingerprint", "")).startswith(
            "blake3:"
        )
    ):
        raise ValueError("reference_search.incomplete_contract")


def validate_syntax_tool(tool: Mapping[str, Any]) -> None:
    output = tool.get("outputSchema")
    properties = output.get("properties", {}) if isinstance(output, Mapping) else {}
    schema = properties.get("schema_version", {})
    kind = properties.get("kind", {})
    schema_values = schema.get("enum", [schema.get("const")])
    kind_values = set(kind.get("enum", []))
    if (
        not isinstance(output, Mapping)
        or set(value for value in schema_values if value is not None) != {STRUCTURED_SCHEMA}
        or kind_values != SYNTAX_KINDS
    ):
        raise ValueError("reference_search.unsupported_syntax_output_schema")


def reference_process_spec(
    executable: Path,
    state_dir: Path,
    operation: str,
    *,
    token: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if operation not in {"probe", "build", "docs", "syntax", "its"}:
        raise ValueError("reference_search.unsupported_operation")
    env = {
        key: value
        for key, value in (environment or os.environ).items()
        if key not in _PROXY_KEYS and key not in _SECRET_KEYS
    }
    for name in (
        "HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
        "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
    ):
        suffix = {
            "HOME": "home",
            "XDG_CACHE_HOME": "cache",
            "XDG_CONFIG_HOME": "config",
            "XDG_DATA_HOME": "data",
            "XDG_STATE_HOME": "state",
            "XDG_RUNTIME_DIR": "runtime",
        }[name]
        env[name] = str(state_dir / suffix)
    if operation == "its":
        if not token:
            raise ValueError("reference_search.its_token_required")
        env["NAPARNIK_TOKEN"] = token
    elif token is not None:
        raise ValueError("reference_search.credentials_forbidden")
    return {
        "command": [
            str(executable), "mcp", "serve", "--profile", "reference",
            "--mode", "stdio",
        ],
        "environment": env,
        "network": operation == "its",
        "downloads": False,
        "reuse_process": False,
        "operation": operation,
    }


def stage_reference_index(
    root: Path,
    executable_fingerprint: str,
    corpus_manifest: Mapping[str, Any],
) -> Path:
    if (
        corpus_manifest.get("source") != "selected-build-bundled"
        or not corpus_manifest.get("corpus_fingerprint")
        or set(corpus_manifest) - {"source", "corpus_fingerprint", "build_version"}
    ):
        raise ValueError("reference_search.external_corpus_forbidden")
    staging = root / "staging" / executable_fingerprint
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, mode=0o700)
    (staging / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": REFERENCE_SCHEMA,
                "executable_fingerprint": executable_fingerprint,
                "corpus": dict(corpus_manifest),
                "state": "building",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return staging


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ValueError("reference_search.index_symlink_forbidden")
        descriptor = os.open(
            path, os.O_RDONLY | (os.O_DIRECTORY if path.is_dir() else 0)
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def promote_reference_index(staging: Path, result: Mapping[str, Any]) -> Path:
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    if (
        result.get("state") != "ready"
        or result.get("cancelled", False)
        or result.get("download_attempts", 0) != 0
        or result.get("network_attempts", 0) != 0
        or result.get("corpus_fingerprint")
        != manifest["corpus"]["corpus_fingerprint"]
    ):
        raise ValueError("reference_search.reference_index_not_promotable")
    root = staging.parent.parent
    destination = root / "instances" / manifest["executable_fingerprint"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("reference_search.index_symlink_forbidden")
    if destination.exists():
        shutil.rmtree(destination)
    manifest["state"] = "ready"
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    _fsync_tree(staging)
    staging.replace(destination)
    _fsync_tree(destination.parent)
    temporary = root / "current.tmp"
    temporary.write_text(destination.name, encoding="utf-8")
    descriptor = os.open(temporary, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    temporary.replace(root / "current")
    _fsync_tree(root)
    return destination


def current_reference_index(root: Path, executable_fingerprint: str) -> Path:
    pointer = root / "current"
    if (
        pointer.is_symlink()
        or not pointer.exists()
        or pointer.read_text(encoding="utf-8") != executable_fingerprint
    ):
        raise ValueError("reference_search.stale_reference_index")
    instance = root / "instances" / executable_fingerprint
    manifest_path = instance / "manifest.json"
    if instance.is_symlink() or manifest_path.is_symlink():
        raise ValueError("reference_search.index_symlink_forbidden")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("state") != "ready":
        raise ValueError("reference_search.stale_reference_index")
    return instance


def normalize_docs_result(value: Mapping[str, Any]) -> dict[str, Any]:
    body = value.get("structuredContent")
    if not isinstance(body, Mapping) or body.get("schema_version") != STRUCTURED_SCHEMA:
        raise ValueError("reference_search.unsupported_docs_schema")
    hits = body.get("hits")
    if not isinstance(hits, list) or len(hits) > DOCS_MAX_HITS:
        raise ValueError("reference_search.docs_bounds_exceeded")
    normalized = []
    for hit in hits:
        if not isinstance(hit, Mapping):
            raise ValueError("reference_search.invalid_docs_hit")
        copy = dict(hit)
        if "snippet" in copy:
            copy["snippet"] = _bounded_text(
                copy["snippet"], DOCS_MAX_EXCERPT_BYTES, "reference_search.docs_bounds_exceeded"
            )
        normalized.append(copy)
    return {
        "schema_version": STRUCTURED_SCHEMA,
        "hits": normalized,
        "shown": body.get("shown", len(normalized)),
        "total": body.get("total", len(normalized)),
        "evidence_class": "navigation_only",
    }


def normalize_syntax_result(value: Mapping[str, Any]) -> dict[str, Any]:
    body = value.get("structuredContent")
    if (
        not isinstance(body, Mapping)
        or body.get("schema_version") != STRUCTURED_SCHEMA
        or body.get("kind") not in SYNTAX_KINDS
    ):
        raise ValueError("reference_search.unsupported_syntax_schema")
    text_blocks = [
        item.get("text")
        for item in value.get("content", [])
        if isinstance(item, Mapping) and item.get("type") == "text"
    ]
    compatibility = ""
    if text_blocks:
        if len(text_blocks) != 1:
            raise ValueError("reference_search.invalid_compatibility_text")
        compatibility = _bounded_text(
            text_blocks[0], TEXT_MAX_BYTES, "reference_search.syntax_bounds_exceeded"
        )
    return {
        "structured": dict(body),
        "compatibility_diagnostic": compatibility,
        "evidence_class": "navigation_only",
    }


def validate_its_request(question: str, *, disclosure_acknowledged: bool, token: str | None) -> None:
    _bounded_text(question, ITS_QUESTION_MAX_BYTES, "reference_search.its_question_too_large")
    if not disclosure_acknowledged:
        raise ValueError("reference_search.its_disclosure_required")
    if not token:
        raise ValueError("reference_search.its_token_required")


def normalize_its_result(value: Mapping[str, Any]) -> dict[str, Any]:
    content = value.get("content")
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], Mapping)
        or content[0].get("type") != "text"
    ):
        raise ValueError("reference_search.invalid_its_response")
    return {
        "text": _bounded_text(
            content[0].get("text"), TEXT_MAX_BYTES, "reference_search.its_response_too_large"
        ),
        "endpoint": ITS_ENDPOINT,
        "deadline_seconds": ITS_DEADLINE_SECONDS,
        "evidence_class": "navigation_only",
    }


def require_repository_evidence(value: Mapping[str, Any]) -> None:
    if value.get("evidence_class") == "navigation_only":
        raise ValueError("reference_search.not_canonical_evidence")


class ItsAdmission:
    def __init__(self) -> None:
        self._slot = threading.Lock()

    @contextmanager
    def acquire(self) -> Iterator[None]:
        if not self._slot.acquire(blocking=False):
            raise RuntimeError("reference_search.its_busy")
        try:
            yield
        finally:
            self._slot.release()


_ITS_ADMISSION = ItsAdmission()


def _mcp_call(
    spec: Mapping[str, Any],
    tool: str,
    arguments: Mapping[str, Any],
    *,
    timeout_seconds: float,
    retry_not_ready: bool = False,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    for name in (
        "HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
        "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
    ):
        Path(spec["environment"][name]).mkdir(
            parents=True, exist_ok=True, mode=0o700
        )
    process = subprocess.Popen(
        spec["command"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=dict(spec["environment"]),
        start_new_session=True,
    )
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("reference_search.stdio_unavailable")
    requests = (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "one-c-autoresearch", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": tool, "arguments": dict(arguments),
        }},
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_seconds
    try:
        for request in requests:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()
        while time.monotonic() < deadline:
            if cancelled and cancelled():
                raise InterruptedError("reference_search.cancelled")
            if not selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                continue
            line = process.stdout.readline()
            if not line:
                break
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(response.get("id"), int) and response["id"] >= 2:
                if response.get("error"):
                    raise RuntimeError("reference_search.native_error")
                result = response.get("result") or {}
                structured = result.get("structuredContent")
                if (
                    retry_not_ready
                    and isinstance(structured, Mapping)
                    and structured.get("status") == "not_ready"
                ):
                    delay = min(
                        2.0, max(0.05, float(structured.get("retry_after_ms", 100)) / 1000)
                    )
                    if time.monotonic() + delay >= deadline:
                        raise TimeoutError("reference_search.build_deadline")
                    wake = time.monotonic() + delay
                    while time.monotonic() < wake:
                        if cancelled and cancelled():
                            raise InterruptedError("reference_search.cancelled")
                        time.sleep(min(0.05, wake - time.monotonic()))
                    identifier = response["id"] + 1
                    process.stdin.write(json.dumps({
                        "jsonrpc": "2.0", "id": identifier,
                        "method": "tools/call",
                        "params": {"name": tool, "arguments": dict(arguments)},
                    }, ensure_ascii=False) + "\n")
                    process.stdin.flush()
                    continue
                return result
        raise TimeoutError("reference_search.deadline")
    finally:
        selector.close()
        process.stdin.close()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def execute_reference(
    executable: Path,
    state_dir: Path,
    request: Mapping[str, Any],
    *,
    token: str | None = None,
    disclosure_acknowledged: bool = False,
    environment: Mapping[str, str] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    operation = str(request["operation"])
    if operation in {"reference.find_docs", "reference.search_docs"}:
        spec = reference_process_spec(
            executable, state_dir, "docs", environment=environment
        )
        result = _mcp_call(
            spec,
            "search",
            {
                "action": operation.removeprefix("reference.").replace("_", "_"),
                "query": request["query"],
                "limit": min(int(request["max_results"]), DOCS_MAX_HITS),
                "max_output_tokens": 8192,
            },
            timeout_seconds=ITS_DEADLINE_SECONDS,
            retry_not_ready=True,
            cancelled=cancelled,
        )
        body = normalize_docs_result(result)
        items = [
            {
                "kind": "reference-hit",
                "title": str(hit.get("symbol") or hit.get("path") or "BSL documentation")[:4096],
                "reference_id": "bsl-doc:" + hashlib.sha256(
                    json.dumps(
                        hit, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
            }
            for hit in body["hits"]
        ]
        return {"items": items, "truncated": body["total"] > len(items)}
    if operation == "reference.syntax_help":
        spec = reference_process_spec(
            executable, state_dir, "syntax", environment=environment
        )
        result = _mcp_call(
            spec,
            "syntax_help",
            {
                "name": request["name"],
                **(
                    {"type_name": request["type_name"]}
                    if request.get("type_name") else {}
                ),
                "max_output_tokens": 8192,
            },
            timeout_seconds=ITS_DEADLINE_SECONDS,
            cancelled=cancelled,
        )
        normalized = normalize_syntax_result(result)
        body = normalized["structured"]
        return {"items": [{
            "kind": "reference-hit",
            "title": str(body.get("name") or request["name"])[:4096],
            "reference_id": (
                f"syntax:{body['kind']}:{body.get('name') or request['name']}"
            )[:4096],
        }], "truncated": False}
    if operation == "reference.its_help":
        validate_its_request(
            str(request["question"]),
            disclosure_acknowledged=disclosure_acknowledged,
            token=token,
        )
        spec = reference_process_spec(
            executable, state_dir, "its", token=token, environment=environment
        )
        with _ITS_ADMISSION.acquire():
            result = _mcp_call(
                spec, "its_help", {"question": request["question"]},
                timeout_seconds=ITS_DEADLINE_SECONDS,
                cancelled=cancelled,
            )
        normalized = normalize_its_result(result)
        return {"items": [{
            "kind": "native-text",
            "schema_version": "native-text-envelope/v1",
            "text": normalized["text"],
        }], "truncated": False}
    raise ValueError("reference_search.unsupported_operation")
