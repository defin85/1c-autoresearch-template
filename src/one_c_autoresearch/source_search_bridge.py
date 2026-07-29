from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import indexes, source_search
from .contracts import canonical_json, sha256
from .sqlite_state import DispatcherStore


def _reply(identifier: Any, *, result: Any = None, error: dict[str, Any] | None = None) -> None:
    payload = {"jsonrpc": "2.0", "id": identifier}
    payload["error" if error is not None else "result"] = error if error is not None else result
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _error(exc: Exception) -> dict[str, Any]:
    raw = getattr(exc, "code", str(exc))
    code = (
        "source_search.timeout"
        if isinstance(exc, TimeoutError)
        else "source_search.cancelled"
        if isinstance(exc, InterruptedError)
        else raw
        if str(raw).startswith("source_search.")
        else "source_search.internal_error"
    )
    data = {"type": type(exc).__name__, "code": code}
    for field in ("limit", "limit_value", "consumed", "requested"):
        if hasattr(exc, field):
            data[field] = getattr(exc, field)
    return {
        "code": -32000,
        "message": str(code)[:200],
        "data": data,
    }


def _tool_failure(exc: Exception) -> dict[str, Any]:
    failure = {
        "schema_version": "source-search-failure/v1",
        **_error(exc)["data"],
    }
    return {
        "content": [{"type": "text", "text": canonical_json(failure).decode()}],
        "structuredContent": failure,
        "isError": True,
    }


def _key(root: Path) -> tuple[str, bytes]:
    pointer = root / "source-search-hmac-current.json"
    if pointer.is_file():
        version = str(json.loads(pointer.read_text(encoding="utf-8"))["version"])
    else:
        version = "v1"
        _create_key(root / f"source-search-hmac-{version}.key")
        from .contracts import atomic_json
        atomic_json(pointer, {"schema_version": "source-search-hmac-pointer/v1", "version": version})
    path = root / f"source-search-hmac-{version}.key"
    if not path.is_file():
        raise RuntimeError("source-search HMAC key pointer is stale")
    value = path.read_bytes()
    if len(value) != 32:
        raise RuntimeError("source-search HMAC key is invalid")
    return version, value


def _create_key(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(os.urandom(32))


def rotate_hmac_key(root: Path) -> str:
    current, _value = _key(root)
    try:
        number = int(current.removeprefix("v"))
    except ValueError as exc:
        raise RuntimeError("source-search HMAC key version is invalid") from exc
    version = f"v{number + 1}"
    _create_key(root / f"source-search-hmac-{version}.key")
    from .contracts import atomic_json
    atomic_json(
        root / "source-search-hmac-current.json",
        {"schema_version": "source-search-hmac-pointer/v1", "version": version},
    )
    return version


def serve(repo: Path, state_base: Path | None, invocation_id: str, capability: str) -> None:
    verifier = "sha256:" + sha256(capability.encode())
    with DispatcherStore(repo, state_base) as store:
        binding = store.conn.execute(
            "SELECT policy, capability_verifier, admission_state, expires_at "
            "FROM source_search_invocations "
            "WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        if binding is None or binding[1] != verifier:
            raise PermissionError("source_search.authentication_failed")
        if binding[2] != "open":
            raise RuntimeError("source_search.invocation_terminal")
        if datetime.fromisoformat(binding[3]) <= datetime.now(timezone.utc):
            raise RuntimeError("source_search.capability_expired")
        policy = json.loads(binding[0])
        key_version, hmac_key = _key(store.path.parent)
        for line in sys.stdin:
            identifier = None
            method = None
            if len(line.encode()) > 256 * 1024:
                raise ValueError("source_search.protocol_record_too_large")
            try:
                request = json.loads(line)
                identifier = request.get("id")
                method = request.get("method")
                if method == "initialize":
                    _reply(identifier, result={
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "one-c-autoresearch-source-search", "version": source_search.BRIDGE_VERSION},
                    })
                elif method == "notifications/initialized":
                    continue
                elif method == "tools/list":
                    _reply(identifier, result={"tools": [{
                        "name": "source_search",
                        "description": "Search active bounded 1C sources through coordinator-owned routing.",
                        "inputSchema": source_search.request_schema(),
                    }]})
                elif method == "tools/call":
                    params = request.get("params") or {}
                    if params.get("name") != "source_search":
                        raise ValueError("source_search.unknown_tool")
                    arguments = params.get("arguments")
                    if not isinstance(arguments, dict):
                        raise ValueError("source_search.invalid_request")
                    call_id = str(identifier)
                    operation = str(arguments.get("operation", ""))
                    query = {
                        "operation": operation,
                        "query": str(arguments.get("query", "")),
                        "component_id": arguments.get("component_id"),
                        "path_prefix": arguments.get("path_prefix"),
                    }
                    started = time.monotonic()
                    reserved = store.reserve_source_search_call(
                        invocation_id,
                        call_id,
                        verifier,
                        query_hmac=source_search.query_hmac(hmac_key, key_version, query),
                        capability=source_search.OPERATIONS.get(operation, ""),
                        query_bytes=len(str(arguments.get("query", "")).encode()),
                        requested_results=int(arguments.get("max_results", 0)),
                        requested_returned_bytes=policy["max_returned_bytes_per_call"],
                        requested_backend_seconds=min(
                            policy["per_call_deadline_seconds"],
                            policy["max_backend_seconds"],
                        ),
                    )
                    try:
                        result = source_search.execute_query(
                            repo,
                            policy,
                            arguments,
                            indexes.backend_statuses(repo),
                            lambda decision, request: indexes.query_backend(
                                repo,
                                decision,
                                request,
                                timeout_seconds=reserved["deadline_seconds"],
                                cancelled=lambda: not store.source_search_admission_open(
                                    invocation_id
                                ),
                            ),
                        )
                    except Exception as exc:
                        store.settle_source_search_call(
                            invocation_id,
                            call_id,
                            status="failed",
                            backend_seconds=min(time.monotonic() - started, reserved["deadline_seconds"]),
                            error_code=str(exc)[:200],
                        )
                        raise
                    settled = store.settle_source_search_call(
                        invocation_id,
                        call_id,
                        status="completed",
                        route_fingerprint=result["route"]["route_fingerprint"],
                        fallback_reason=result["route"]["fallback_reason"] or "",
                        adapter_id=result["route"]["selected_backend_id"],
                        adapter_version=result["route"]["adapter_version"],
                        capability_fingerprint=result["route"]["capability_fingerprint"],
                        index_fingerprint=result["route"]["index_fingerprint"],
                        result_manifest_fingerprint=result["result_manifest_fingerprint"],
                        result_count=result["result_count"],
                        returned_bytes=result["returned_bytes"],
                        backend_seconds=min(time.monotonic() - started, reserved["deadline_seconds"]),
                    )
                    if not settled:
                        raise InterruptedError("source_search.late_output_discarded")
                    _reply(identifier, result={
                        "content": [{"type": "text", "text": canonical_json(result).decode()}],
                        "structuredContent": result,
                        "isError": False,
                    })
                elif identifier is not None:
                    _reply(identifier, error={"code": -32601, "message": "method not found"})
            except Exception as exc:
                if identifier is not None:
                    if method == "tools/call":
                        _reply(identifier, result=_tool_failure(exc))
                    else:
                        _reply(identifier, error=_error(exc))


def main() -> None:
    repo = Path(os.environ["ONE_C_AUTORESEARCH_REPO"]).resolve()
    invocation_id = os.environ["ONE_C_AUTORESEARCH_INVOCATION_ID"]
    capability = os.environ["ONE_C_AUTORESEARCH_SOURCE_SEARCH_CAPABILITY"]
    state = os.environ.get("ONE_C_AUTORESEARCH_STATE_ROOT")
    serve(repo, Path(state).resolve() if state else None, invocation_id, capability)


if __name__ == "__main__":
    main()
