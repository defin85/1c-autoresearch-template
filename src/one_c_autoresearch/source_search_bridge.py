from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping
from typing import TypedDict

from . import indexes, source_search
from .contracts import JsonValue, canonical_json, json_object, parse_json_object, sha256
from .sqlite_state import DispatcherStore, SourceSearchBudgetError


class Provenance(TypedDict, total=False):
    surface_identity: str
    embedding_identity: str
    reference_identity: str
    result_class_counts: dict[str, int]
    canonical_manifest_fingerprint: str
    derived_manifest_fingerprint: str
    modality: str


def _reply(
    identifier: JsonValue,
    *,
    result: JsonValue = None,
    error: Mapping[str, JsonValue] | None = None,
) -> None:
    payload: dict[str, JsonValue] = {"jsonrpc": "2.0", "id": identifier}
    payload["error" if error is not None else "result"] = error if error is not None else result
    _ = sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    _ = sys.stdout.flush()


def _error(exc: Exception) -> dict[str, JsonValue]:
    raw = exc.code if isinstance(exc, SourceSearchBudgetError) else str(exc)
    code = (
        "source_search.timeout"
        if isinstance(exc, TimeoutError)
        else "source_search.cancelled"
        if isinstance(exc, InterruptedError)
        else raw
        if str(raw).startswith("source_search.")
        else "source_search.internal_error"
    )
    data: dict[str, JsonValue] = {"type": type(exc).__name__, "code": code}
    if isinstance(exc, SourceSearchBudgetError):
        data.update({
            "limit": exc.limit,
            "limit_value": exc.limit_value,
            "consumed": exc.consumed,
            "requested": exc.requested,
        })
    return {
        "code": -32000,
        "message": str(code)[:200],
        "data": data,
    }


def _tool_failure(exc: Exception) -> dict[str, JsonValue]:
    error_data = json_object(_error(exc).get("data"))
    failure = {
        "schema_version": "source-search-failure/v1",
        **error_data,
    }
    return {
        "content": [{"type": "text", "text": canonical_json(failure).decode()}],
        "structuredContent": failure,
        "isError": True,
    }


def hmac_key(root: Path) -> tuple[str, bytes]:
    pointer = root / "source-search-hmac-current.json"
    if pointer.is_file():
        value = parse_json_object(pointer.read_text(encoding="utf-8")).get("version")
        if not isinstance(value, str):
            raise RuntimeError("source-search HMAC key pointer is invalid")
        version = value
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


_key = hmac_key


def _create_key(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    with os.fdopen(descriptor, "wb") as stream:
        _ = stream.write(os.urandom(32))


def rotate_hmac_key(root: Path) -> str:
    current, _value = hmac_key(root)
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


def _modality(operation: str) -> str:
    if operation == "code.search_lexical":
        return "lexical"
    if operation == "code.search_hybrid":
        return "hybrid"
    if operation == "reference.its_help":
        return "its"
    if operation.startswith("reference."):
        return "reference"
    return "workspace"


def _text(value: object, error: str) -> str:
    if not isinstance(value, str):
        raise ValueError(error)
    return value


def _integer(value: object, error: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(error)
    return value


def _number(value: object, error: str) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(error)
    return value


def _policy(value: object) -> source_search.SourceSearchPolicy:
    raw = json_object(value)
    operations = raw.get("operations")
    component_ids = raw.get("component_ids", [])
    logical_paths = raw.get("logical_path_prefixes", [])
    if (
        not isinstance(operations, list)
        or not all(isinstance(item, str) for item in operations)
        or not isinstance(component_ids, list)
        or not all(isinstance(item, str) for item in component_ids)
        or not isinstance(logical_paths, list)
        or not all(isinstance(item, str) for item in logical_paths)
    ):
        raise ValueError("source_search.invalid_policy")
    limits = source_search.validate_profile_policy({
        "operations": operations,
        **{field: raw.get(field) for field in source_search.POLICY_LIMITS},
    })
    return source_search.SourceSearchPolicy(
        schema_version=_text(raw.get("schema_version"), "source_search.invalid_policy"),
        tool_schema_version=str(raw.get("tool_schema_version", source_search.TOOL_SCHEMA_VERSION)),
        bridge_version=str(raw.get("bridge_version", source_search.BRIDGE_VERSION)),
        operations=[item for item in operations if isinstance(item, str)],
        source_generation_id=str(raw.get("source_generation_id", "")),
        component_ids=[item for item in component_ids if isinstance(item, str)],
        logical_path_prefixes=[item for item in logical_paths if isinstance(item, str)],
        routing_fingerprint=str(raw.get("routing_fingerprint", "")),
        max_calls=limits["max_calls"],
        max_concurrent_calls=limits["max_concurrent_calls"],
        per_call_deadline_seconds=limits["per_call_deadline_seconds"],
        max_backend_seconds=limits["max_backend_seconds"],
        max_query_bytes=limits["max_query_bytes"],
        max_total_query_bytes=limits["max_total_query_bytes"],
        max_results_per_call=limits["max_results_per_call"],
        max_total_results=limits["max_total_results"],
        max_returned_bytes_per_call=limits["max_returned_bytes_per_call"],
        max_total_returned_bytes=limits["max_total_returned_bytes"],
        scope_fingerprint=_text(raw.get("scope_fingerprint"), "source_search.invalid_policy"),
        policy_fingerprint=_text(raw.get("policy_fingerprint"), "source_search.invalid_policy"),
    )


def _backend_state(value: object) -> indexes.BackendState:
    raw = json_object(value)
    state = indexes.BackendState()
    for field in ("adapter_id", "component_id", "status", "adapter_version", "capability_fingerprint", "index_fingerprint"):
        item = raw.get(field)
        if isinstance(item, str):
            state[field] = item
    capabilities = raw.get("capabilities")
    if isinstance(capabilities, list):
        values = [item for item in capabilities if isinstance(item, str)]
        if len(values) == len(capabilities):
            state["capabilities"] = values
    return state


def _v2_provenance(result: Mapping[str, object]) -> Provenance:
    result_json = json_object(result)
    if result_json.get("schema_version") != "source-search-result/v2":
        return {}
    operation = source_search.normalize_operation(result_json.get("operation"))
    route = result_json.get("route")
    items = result_json.get("items")
    if not isinstance(route, Mapping) or not isinstance(items, list):
        raise ValueError("source_search.incomplete_v2_provenance")
    classes = {
        "canonical-hit": "canonical_navigation_hit",
        "canonical-excerpt": "canonical_excerpt",
        "reference-hit": "reference_navigation_finding",
    }
    counts: dict[str, int] = {}
    canonical: list[dict[str, JsonValue]] = []
    derived: list[dict[str, JsonValue]] = []
    for item in items:
        item_object = json_object(item)
        result_class = classes.get(str(item_object.get("kind")), "derived_navigation_finding")
        counts[result_class] = counts.get(result_class, 0) + 1
        identity = {
            key: item_object[key]
            for key in (
                "kind", "component_id", "source_generation_id", "path",
                "fingerprint", "line", "symbol",
            )
            if key in item_object
        }
        (canonical if result_class.startswith("canonical_") else derived).append(identity)
    def fingerprint(values: list[dict[str, JsonValue]]) -> str:
        return "sha256:" + sha256(canonical_json(values))
    surface_identity = str(
        route.get("surface_identity")
        or route.get("surface_manifest_fingerprint")
        or result_json.get("surface_identity", "")
    )
    if not surface_identity.startswith("sha256:"):
        raise ValueError("source_search.incomplete_v2_provenance")
    return Provenance(
        surface_identity=surface_identity,
        embedding_identity=str(
            route.get("embedding_identity") or result_json.get("embedding_identity", "")
        ),
        reference_identity=str(
            route.get("reference_identity") or result_json.get("reference_identity", "")
        ),
        result_class_counts=counts,
        canonical_manifest_fingerprint=fingerprint(canonical) if canonical else "",
        derived_manifest_fingerprint=fingerprint(derived) if derived else "",
        modality=_modality(operation),
    )


def serve(repo: Path, state_base: Path | None, invocation_id: str, capability: str) -> None:
    verifier = "sha256:" + sha256(capability.encode())
    with DispatcherStore(repo, state_base) as store:
        binding = store.conn.execute(
            "SELECT policy, capability_verifier, admission_state, expires_at FROM source_search_invocations WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        if binding is None or binding[1] != verifier:
            raise PermissionError("source_search.authentication_failed")
        if binding[2] != "open":
            raise RuntimeError("source_search.invocation_terminal")
        expires_at = _text(binding[3], "source_search.invalid_invocation")
        if datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc):
            raise RuntimeError("source_search.capability_expired")
        policy = _policy(parse_json_object(
            _text(binding[0], "source_search.invalid_invocation")
        ))
        key_version, hmac_key_value = hmac_key(store.path.parent)
        for line in sys.stdin:
            identifier = None
            method = None
            if len(line.encode()) > 256 * 1024:
                raise ValueError("source_search.protocol_record_too_large")
            try:
                request = parse_json_object(line)
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
                        "inputSchema": source_search.request_schema(
                            str(policy.get("tool_schema_version", source_search.TOOL_SCHEMA_VERSION)),
                        ),
                    }]})
                elif method == "tools/call":
                    params = json_object(request.get("params") or {})
                    if params.get("name") != "source_search":
                        raise ValueError("source_search.unknown_tool")
                    argument_value = params.get("arguments")
                    if not isinstance(argument_value, Mapping):
                        raise ValueError("source_search.invalid_request")
                    arguments: dict[str, object] = dict(argument_value)
                    call_id = str(identifier)
                    requested_operation = str(arguments.get("operation", ""))
                    operation = source_search.normalize_operation(requested_operation)
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
                        query_hmac=source_search.query_hmac(hmac_key_value, key_version, query),
                        capability=source_search.OPERATIONS[operation],
                        operation=operation,
                        modality=_modality(operation),
                        query_bytes=len(str(arguments.get("query", "")).encode()),
                        requested_results=_integer(
                            arguments.get("max_results", 0),
                            "source_search.invalid_request",
                        ),
                        requested_returned_bytes=policy["max_returned_bytes_per_call"],
                        requested_backend_seconds=min(
                            policy["per_call_deadline_seconds"],
                            policy["max_backend_seconds"],
                        ),
                    )
                    deadline = _number(
                        reserved.get("deadline_seconds"),
                        "source_search.invalid_reservation",
                    )
                    try:
                        def run_backend(
                            decision: indexes.BackendDecision,
                            backend_request: Mapping[str, object],
                        ) -> JsonValue:
                            output = indexes.query_backend(
                                repo,
                                decision,
                                json_object(backend_request),
                                timeout_seconds=deadline,
                                cancelled=lambda: not store.source_search_admission_open(invocation_id),
                            )
                            if isinstance(output, list):
                                return [json_object(dict(item)) for item in output]
                            return output

                        result = source_search.execute_query(
                            repo,
                            policy,
                            arguments,
                            [_backend_state(row) for row in indexes.backend_statuses(repo)],
                            run_backend,
                            cancelled=lambda: not store.source_search_admission_open(
                                invocation_id
                            ),
                        )
                    except Exception as exc:
                        _ = store.settle_source_search_call(
                            invocation_id,
                            call_id,
                            status="failed",
                            backend_seconds=min(
                                time.monotonic() - started,
                                deadline,
                            ),
                            error_code=str(exc)[:200],
                        )
                        raise
                    provenance = _v2_provenance(result)
                    result_json = json_object(result)
                    route = json_object(result_json.get("route"))
                    route_fingerprint = _text(route.get("route_fingerprint"), "source_search.incomplete_result")
                    fallback_reason = route.get("fallback_reason")
                    selected_backend_id = _text(route.get("selected_backend_id"), "source_search.incomplete_result")
                    adapter_version = _text(route.get("adapter_version"), "source_search.incomplete_result")
                    capability_fingerprint = _text(route.get("capability_fingerprint"), "source_search.incomplete_result")
                    index_fingerprint = _text(route.get("index_fingerprint"), "source_search.incomplete_result")
                    result_manifest_fingerprint = _text(
                        result_json.get("result_manifest_fingerprint"),
                        "source_search.incomplete_result",
                    )
                    settled = store.settle_source_search_call(
                        invocation_id,
                        call_id,
                        status="completed",
                        route_fingerprint=route_fingerprint,
                        fallback_reason=str(fallback_reason or ""),
                        adapter_id=selected_backend_id,
                        adapter_version=adapter_version,
                        capability_fingerprint=capability_fingerprint,
                        index_fingerprint=index_fingerprint,
                        result_manifest_fingerprint=result_manifest_fingerprint,
                        surface_identity=provenance.get("surface_identity", ""),
                        embedding_identity=provenance.get("embedding_identity", ""),
                        reference_identity=provenance.get("reference_identity", ""),
                        result_class_counts=provenance.get("result_class_counts"),
                        canonical_manifest_fingerprint=provenance.get(
                            "canonical_manifest_fingerprint", ""
                        ),
                        derived_manifest_fingerprint=provenance.get(
                            "derived_manifest_fingerprint", ""
                        ),
                        result_count=_integer(result_json.get("result_count"), "source_search.incomplete_result"),
                        returned_bytes=_integer(result_json.get("returned_bytes"), "source_search.incomplete_result"),
                        backend_seconds=min(time.monotonic() - started, deadline),
                    )
                    if not settled:
                        raise InterruptedError("source_search.late_output_discarded")
                    _reply(identifier, result={
                        "content": [{"type": "text", "text": canonical_json(result).decode()}],
                        "structuredContent": result_json,
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
