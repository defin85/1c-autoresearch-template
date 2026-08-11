from __future__ import annotations

from hashlib import sha256 as hashlib_sha256
from pathlib import Path
from collections.abc import Callable, Iterator, Mapping
from typing import TypedDict

from . import indexes
from .contracts import (
    JsonValue, canonical_json, json_object, normalize_relative, parse_json_object, sha256,
)


POLICY_VERSION = "source-search-policy/v2"
TOOL_SCHEMA_VERSION = "source-search-tool/v2"
BRIDGE_VERSION = "source-search-mcp/v2"
V2_OPERATIONS = {
    "code.search_lexical": "code-search-lexical",
    "code.search_hybrid": "code-search-hybrid",
    "symbol.info": "symbol-info",
    "symbol.info_at": "symbol-info-positional",
    **{f"graph.{name}": f"graph-{name}" for name in (
        "overview", "schema", "resolve", "node", "source", "neighbors", "callers", "callees",
    )},
    **{f"metadata.{name}": f"metadata-{name}" for name in ("info", "tree", "object", "form")},
    **{f"diagnostics.{name}": f"diagnostics-{name}" for name in (
        "catalog", "schema", "file", "workspace",
    )},
    "reference.find_docs": "reference-docs-find",
    "reference.search_docs": "reference-docs-search",
    "reference.syntax_help": "reference-syntax-help",
    "reference.its_help": "reference-its-help",
}
OPERATIONS = V2_OPERATIONS
POLICY_LIMITS = {
    "max_calls": 64,
    "max_concurrent_calls": 4,
    "per_call_deadline_seconds": 60,
    "max_backend_seconds": 600,
    "max_query_bytes": 4096,
    "max_total_query_bytes": 65536,
    "max_results_per_call": 100,
    "max_total_results": 1000,
    "max_returned_bytes_per_call": 131072,
    "max_total_returned_bytes": 2097152,
}
POLICY_FIELDS = {"operations", *POLICY_LIMITS}
ROLE_OPERATIONS = {
    "analyzer": frozenset(V2_OPERATIONS),
    "grouper": frozenset(V2_OPERATIONS),
    "coordinator": frozenset(V2_OPERATIONS),
    "classifier": frozenset({
        "code.search_lexical", "code.search_hybrid", "symbol.info", "symbol.info_at",
    }),
    "researcher": frozenset(V2_OPERATIONS),
}
TOOL_FRAMING_BYTES_PER_CALL = 512


class SourceSearchPolicy(TypedDict):
    schema_version: str
    tool_schema_version: str
    bridge_version: str
    operations: list[str]
    source_generation_id: str
    component_ids: list[str]
    logical_path_prefixes: list[str]
    routing_fingerprint: str
    max_calls: int
    max_concurrent_calls: int
    per_call_deadline_seconds: int
    max_backend_seconds: int
    max_query_bytes: int
    max_total_query_bytes: int
    max_results_per_call: int
    max_total_results: int
    max_returned_bytes_per_call: int
    max_total_returned_bytes: int
    scope_fingerprint: str
    policy_fingerprint: str


class ProfilePolicy(TypedDict):
    operations: list[str]
    max_calls: int
    max_concurrent_calls: int
    per_call_deadline_seconds: int
    max_backend_seconds: int
    max_query_bytes: int
    max_total_query_bytes: int
    max_results_per_call: int
    max_total_results: int
    max_returned_bytes_per_call: int
    max_total_returned_bytes: int


class DiffScope(TypedDict, total=False):
    before_role: str
    after_role: str


class WorkUnit(TypedDict, total=False):
    allowed_paths: list[str]
    diff: DiffScope
    kind: str


class SearchProfile(TypedDict, total=False):
    source_search: object


class LedgerItem(TypedDict, total=False):
    status: str
    capability: str
    route_fingerprint: str
    fallback_reason: str | None
    adapter_id: str
    adapter_version: str
    capability_fingerprint: str
    index_fingerprint: str
    result_manifest_fingerprint: str
    query_hmac: str


class SearchLedger(TypedDict):
    items: list[LedgerItem]
    ledger_complete: bool
    reconciled: bool
    policy_fingerprint: str
    scope_fingerprint: str


def _json_list(value: object) -> list[JsonValue]:
    items = json_object({"items": value})["items"]
    if not isinstance(items, list):
        raise ValueError("expected a JSON array")
    return items


def normalize_operation(operation: object) -> str:
    value = str(operation)
    if value == "find_references":
        raise ValueError(
            "source_search.find_references_unsupported: use symbol.info usage summary or graph.callers"
        )
    if value not in V2_OPERATIONS:
        raise ValueError("invalid source_search operations")
    return value


def validate_profile_policy(value: object) -> ProfilePolicy:
    mapping = json_object(value)
    if set(mapping) != POLICY_FIELDS:
        raise ValueError("invalid source_search policy fields")
    operations = mapping.get("operations")
    if (
        not isinstance(operations, list)
        or not operations
        or len(operations) != len(set(operations))
    ):
        raise ValueError("invalid source_search operations")
    normalized_operations = [normalize_operation(operation) for operation in operations]
    if len(normalized_operations) != len(set(normalized_operations)):
        raise ValueError("duplicate source_search operations after v1 migration")
    limits: dict[str, int] = {}
    for field, maximum in POLICY_LIMITS.items():
        item = mapping.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0 or item > maximum:
            raise ValueError(f"invalid source_search limit: {field}")
        limits[field] = item
    normalized = ProfilePolicy(
        operations=normalized_operations,
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
    )
    if normalized["max_concurrent_calls"] > normalized["max_calls"]:
        raise ValueError("source_search concurrency exceeds call budget")
    if normalized["max_query_bytes"] > normalized["max_total_query_bytes"]:
        raise ValueError("source_search query limit exceeds aggregate")
    if normalized["max_results_per_call"] > normalized["max_total_results"]:
        raise ValueError("source_search result limit exceeds aggregate")
    if normalized["max_returned_bytes_per_call"] > normalized["max_total_returned_bytes"]:
        raise ValueError("source_search byte limit exceeds aggregate")
    return normalized


def dynamic_reserve_bytes(policy: ProfilePolicy | SourceSearchPolicy) -> int:
    return (
        policy["max_total_query_bytes"]
        + policy["max_total_returned_bytes"]
        + policy["max_calls"] * TOOL_FRAMING_BYTES_PER_CALL
    )


def _logical_prefix(component_id: str) -> str:
    _role, kind, *identity = component_id.split(":")
    if kind == "configuration":
        return "configuration/"
    if kind == "extension":
        return f"extensions/{identity[0]}/"
    if kind == "external":
        return f"external/{identity[0]}/source/"
    raise ValueError("unsupported source component")


def resolve_policy(
    repo: Path,
    profile: SearchProfile,
    role_id: str,
    work_unit: WorkUnit,
) -> SourceSearchPolicy | None:
    raw = profile.get("source_search")
    if raw is None:
        return None
    policy = validate_profile_policy(raw)
    role_operations = ROLE_OPERATIONS.get(role_id, frozenset())
    operations = [operation for operation in policy["operations"] if operation in role_operations]
    if not operations:
        return None
    paths = work_unit.get("allowed_paths")
    if not isinstance(paths, list) or not paths:
        return None
    explicit_roles = tuple(
        role
        for role in ("vendor_baseline", "target_cf", "next_vendor")
        if any(str(path).replace("\\", "/").startswith(f"{role}/") for path in paths)
    )
    diff = work_unit.get("diff", DiffScope())
    diff_roles = tuple(
        role
        for role in (str(diff.get("before_role", "")), str(diff.get("after_role", "")))
        if role in ("vendor_baseline", "target_cf", "next_vendor")
    )
    roles = (
        explicit_roles
        or tuple(dict.fromkeys(diff_roles))
        or (
            ("target_cf", "next_vendor")
            if work_unit.get("kind") in {"migration-decision", "approval"}
            else ("target_cf",)
        )
    )
    component_ids = indexes.required_component_ids(repo, [str(path) for path in paths], roles)
    if not component_ids:
        return None
    logical_paths: list[str] = []
    for value in paths:
        normalized = normalize_relative(str(value))
        parts = normalized.split("/")
        if parts[0] in {"vendor_baseline", "target_cf", "next_vendor"}:
            normalized = "/".join(parts[1:])
        if any(
            normalized == _logical_prefix(component_id).rstrip("/")
            or normalized.startswith(_logical_prefix(component_id))
            for component_id in component_ids
        ):
            logical_paths.append(normalized)
    if not logical_paths:
        return None
    pointer = parse_json_object(
        (repo / "research/active-source-generation.json").read_text(encoding="utf-8")
    )
    config = indexes.load_config(repo)
    generation_id = pointer.get("generation_id")
    if not isinstance(generation_id, str):
        raise ValueError("invalid active source generation")
    result = SourceSearchPolicy(
        schema_version=POLICY_VERSION,
        tool_schema_version=TOOL_SCHEMA_VERSION,
        bridge_version=BRIDGE_VERSION,
        operations=operations,
        source_generation_id=generation_id,
        component_ids=component_ids,
        logical_path_prefixes=sorted(set(logical_paths)),
        routing_fingerprint="sha256:" + sha256(canonical_json(config["routes"])),
        max_calls=policy["max_calls"],
        max_concurrent_calls=policy["max_concurrent_calls"],
        per_call_deadline_seconds=policy["per_call_deadline_seconds"],
        max_backend_seconds=policy["max_backend_seconds"],
        max_query_bytes=policy["max_query_bytes"],
        max_total_query_bytes=policy["max_total_query_bytes"],
        max_results_per_call=policy["max_results_per_call"],
        max_total_results=policy["max_total_results"],
        max_returned_bytes_per_call=policy["max_returned_bytes_per_call"],
        max_total_returned_bytes=policy["max_total_returned_bytes"],
        scope_fingerprint="",
        policy_fingerprint="",
    )
    result["scope_fingerprint"] = "sha256:" + sha256(canonical_json({
        "source_generation_id": result["source_generation_id"],
        "component_ids": component_ids,
        "logical_path_prefixes": result["logical_path_prefixes"],
    }))
    result["policy_fingerprint"] = "sha256:" + sha256(canonical_json(result))
    return result


def query_hmac(key: bytes, key_version: str, query: dict[str, object]) -> str:
    import hmac
    return f"{key_version}:" + hmac.new(key, canonical_json(query), hashlib_sha256).hexdigest()


def request_schema(tool_schema_version: str = TOOL_SCHEMA_VERSION) -> dict[str, JsonValue]:
    if tool_schema_version != TOOL_SCHEMA_VERSION:
        raise ValueError("unsupported source_search tool schema")
    text = {"type": "string", "minLength": 1, "maxLength": POLICY_LIMITS["max_query_bytes"]}
    component = {"type": ["string", "null"], "maxLength": 512}
    path = {"type": ["string", "null"], "maxLength": 4096}
    count = {
        "type": "integer", "minimum": 1,
        "maximum": POLICY_LIMITS["max_results_per_call"],
    }
    def variant(
        operation: str, required: tuple[str, ...], **properties: JsonValue
    ) -> dict[str, JsonValue]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", *required],
            "properties": {
                "operation": {"const": operation},
                **properties,
            },
        }

    variants = [
        variant(
            operation, ("query", "max_results"),
            query=text, component_id=component, path_prefix=path, max_results=count,
        )
        for operation in ("code.search_lexical", "code.search_hybrid")
    ]
    variants += [
        variant(
            "symbol.info", ("symbol",),
            symbol=text, component_id=component, max_results=count,
        ),
        variant(
            "symbol.info_at", ("component_id", "path", "line"),
            component_id=component,
            path={"type": "string", "minLength": 1, "maxLength": 4096},
            line={"type": "integer", "minimum": 1},
            column={"type": ["integer", "null"], "minimum": 1},
            include={
                "type": "array", "uniqueItems": True, "maxItems": 8,
                "items": {"type": "string", "enum": ["definition", "type", "docs", "usages"]},
            },
            locale={"type": ["string", "null"], "enum": ["en", "ru", None]},
        ),
    ]
    variants += [
        variant(
            f"graph.{operation}", (), component_id=component, max_results=count,
        )
        for operation in ("overview", "schema")
    ]
    variants += [variant(
        "graph.resolve", ("query", "max_results"),
        query=text, component_id=component, max_results=count,
    )]
    graph_id = {"type": "string", "minLength": 1, "maxLength": 1024}
    graph_filters = {
        "depth": {"type": ["integer", "null"], "minimum": 1, "maximum": 8},
        "direction": {"type": ["string", "null"], "enum": ["in", "out", "both", None]},
        "detail": {"type": ["string", "null"], "enum": ["summary", "full", None]},
        "edge_kinds": {
            "type": "array", "uniqueItems": True, "maxItems": 6,
            "items": {"type": "string", "enum": [
                "call", "manager_creates", "manager_access", "query_ref",
                "contains", "data_binding",
            ]},
        },
        "provenance": {
            "type": "array", "uniqueItems": True, "maxItems": 4,
            "items": {"type": "string", "enum": [
                "resolved", "inferred", "visibility_blocked", "unresolved",
            ]},
        },
    }
    variants += [
        variant(
            f"graph.{operation}", ("id", "max_results"),
            id=graph_id, component_id=component, max_results=count, **graph_filters,
        )
        for operation in ("node", "neighbors", "callers", "callees")
    ]
    variants += [variant(
        "graph.source", ("ids", "max_results"),
        component_id=component, max_results=count,
        ids={"type": "array", "minItems": 1, "maxItems": 100, "uniqueItems": True, "items": graph_id},
    )]
    variants += [
        variant("metadata.info", ("component_id",), component_id=component),
        variant(
            "metadata.tree", ("component_id", "max_results"),
            component_id=component, max_results=count,
            filter={"type": ["string", "null"], "maxLength": 512},
            meta_type={"type": ["string", "null"], "maxLength": 128},
            name_mask={"type": ["string", "null"], "maxLength": 512},
        ),
        variant(
            "metadata.object", ("component_id", "object_type", "object_name"),
            component_id=component,
            object_type={"type": "string", "minLength": 1, "maxLength": 128},
            object_name={"type": "string", "minLength": 1, "maxLength": 512},
        ),
        variant(
            "metadata.form", ("component_id", "object_type"),
            component_id=component,
            object_type={"type": "string", "minLength": 1, "maxLength": 128},
            object_name={"type": ["string", "null"], "maxLength": 512},
            form_name={"type": ["string", "null"], "maxLength": 512},
        ),
    ]
    diagnostic_filters = {
        "codes": {
            "type": "array", "uniqueItems": True, "maxItems": 100,
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "min_severity": {
            "type": ["string", "null"], "enum": ["info", "warning", "error", None],
        },
        "detail": {"type": ["string", "null"], "enum": ["summary", "full", None]},
        "range_start": {"type": ["integer", "null"], "minimum": 1},
        "range_end": {"type": ["integer", "null"], "minimum": 1},
    }
    variants += [
        variant(
            f"diagnostics.{operation}", ("max_results",),
            component_id=component, max_results=count, **diagnostic_filters,
        )
        for operation in ("catalog", "schema", "workspace")
    ]
    variants += [variant(
        "diagnostics.file", ("component_id", "path", "max_results"),
        component_id=component, max_results=count,
        path={"type": "string", "minLength": 1, "maxLength": 4096},
        **diagnostic_filters,
    )]
    variants += [
        variant(operation, ("query", "max_results"), query=text, max_results=count)
        for operation in ("reference.find_docs", "reference.search_docs")
    ]
    variants += [
        variant(
            "reference.syntax_help", ("name",),
            name=text, type_name={"type": ["string", "null"], "maxLength": 512},
        ),
        variant("reference.its_help", ("question",), question=text),
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": TOOL_SCHEMA_VERSION,
        "oneOf": variants,
    }


def response_schema() -> dict[str, JsonValue]:
    text = {"type": "string", "maxLength": 4096}
    path = {"type": "string", "minLength": 1, "maxLength": 4096}
    fingerprint = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}

    def item(
        kind: str, required: tuple[str, ...], **properties: JsonValue
    ) -> dict[str, JsonValue]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", *required],
            "properties": {"kind": {"const": kind}, **properties},
        }

    kinds = [
        item(
            "canonical-hit",
            ("path", "fingerprint", "source_generation_id", "component_id"),
            path=path, fingerprint=fingerprint, source_generation_id=text,
            component_id=text, line={"type": ["integer", "null"], "minimum": 1},
            symbol=text,
        ),
        item("canonical-excerpt", ("path", "fingerprint", "text"), path=path,
             fingerprint=fingerprint, text={"type": "string", "maxLength": 16384},
             start_line={"type": "integer", "minimum": 1}),
        item("navigation-node", ("id", "label"), id=text, label=text,
             provenance={"type": "string", "enum": ["resolved", "inferred", "visibility_blocked", "unresolved"]}),
        item("navigation-edge", ("source", "target", "edge_kind"), source=text,
             target=text, edge_kind=text, provenance=text),
        item("derived-finding", ("code", "message"), code=text, message=text,
             severity={"type": "string", "enum": ["info", "warning", "error"]}, path=path),
        item("reference-hit", ("title", "reference_id"), title=text, reference_id=text),
        item("native-text", ("schema_version", "text"),
             schema_version={"const": "native-text-envelope/v1"},
             text={"type": "string", "maxLength": 131072}),
        item("retry", ("reason", "narrowing_hint"), reason=text, narrowing_hint=text),
        item("degraded", ("reason", "narrowing_hint"), reason=text, narrowing_hint=text),
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "source-search-result/v2",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "operation", "capability", "route", "items",
            "truncated", "narrowing_hint", "result_count", "returned_bytes",
            "result_manifest_fingerprint",
        ],
        "properties": {
            "schema_version": {"const": "source-search-result/v2"},
            "operation": {"type": "string", "enum": sorted(V2_OPERATIONS)},
            "capability": {"type": "string", "enum": sorted(V2_OPERATIONS.values())},
            "route": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "route_fingerprint", "preferred_backend_id",
                    "selected_backend_id", "fallback_reason", "adapter_version",
                    "capability_fingerprint", "index_fingerprint",
                ],
                "properties": {
                    "route_fingerprint": text,
                    "preferred_backend_id": text,
                    "selected_backend_id": text,
                    "fallback_reason": {"type": ["string", "null"], "maxLength": 4096},
                    "adapter_version": text,
                    "capability_fingerprint": text,
                    "index_fingerprint": text,
                    "surface_identity": fingerprint,
                    "embedding_identity": fingerprint,
                    "reference_identity": fingerprint,
                },
            },
            "items": {
                "type": "array",
                "maxItems": POLICY_LIMITS["max_results_per_call"],
                "items": {"oneOf": kinds},
            },
            "truncated": {"type": "boolean"},
            "narrowing_hint": {"type": ["string", "null"], "maxLength": 4096},
            "result_count": {
                "type": "integer", "minimum": 0,
                "maximum": POLICY_LIMITS["max_results_per_call"] * 2,
            },
            "returned_bytes": {
                "type": "integer", "minimum": 0,
                "maximum": POLICY_LIMITS["max_returned_bytes_per_call"],
            },
            "result_manifest_fingerprint": fingerprint,
        },
    }


def _v2_request(
    request: dict[str, object], operation: str, policy: SourceSearchPolicy
) -> dict[str, JsonValue]:
    request_values = json_object(request)
    variants = request_schema().get("oneOf")
    if not isinstance(variants, list):
        raise RuntimeError("source_search.request_schema_invalid")
    variant: dict[str, JsonValue] | None = None
    for candidate in variants:
        candidate_object = json_object(candidate)
        properties = json_object(candidate_object.get("properties"))
        operation_schema = json_object(properties.get("operation"))
        if operation_schema.get("const") == operation:
            variant = candidate_object
            break
    if variant is None:
        raise ValueError("source_search.invalid_request")
    properties = json_object(variant.get("properties"))
    required = variant.get("required")
    if not isinstance(required, list) or not all(isinstance(field, str) for field in required):
        raise RuntimeError("source_search.request_schema_invalid")
    if set(request_values) - set(properties) or any(
        field not in request_values for field in required
    ):
        raise ValueError("source_search.invalid_request")
    normalized = dict(request_values)
    requested = normalized.get("max_results", 1)
    if (
        not isinstance(requested, int) or isinstance(requested, bool)
        or not 1 <= requested <= policy["max_results_per_call"]
    ):
        raise ValueError("source_search.result_limit_invalid")
    normalized["max_results"] = requested
    query_value = next(
        (
            normalized[key]
            for key in ("query", "symbol", "question", "name")
            if key in normalized
        ),
        "",
    )
    if query_value and (
        not isinstance(query_value, str)
        or not query_value
        or len(query_value.encode()) > policy["max_query_bytes"]
    ):
        raise ValueError("source_search.query_bytes_exhausted")
    for key, value in normalized.items():
        if key == "operation" or value is None:
            continue
        if isinstance(value, bool):
            raise ValueError("source_search.invalid_request")
        if isinstance(value, str) and len(value.encode()) > 4096:
            raise ValueError("source_search.invalid_request")
        if isinstance(value, int) and value <= 0:
            raise ValueError("source_search.invalid_request")
        if isinstance(value, list):
            validated = _json_list(value)
            values = [str(item) for item in validated]
            if len(values) > 100 or len(values) != len(set(values)):
                raise ValueError("source_search.invalid_request")
    return normalized


def _component_relative_request_path(
    value: str, component_id: str, policy: SourceSearchPolicy
) -> str:
    normalized = normalize_relative(value)
    logical_root = _logical_prefix(component_id)
    if normalized.startswith(logical_root):
        if not any(
            normalized == allowed.rstrip("/")
            or normalized.startswith(allowed.rstrip("/") + "/")
            for allowed in policy["logical_path_prefixes"]
        ):
            raise ValueError("source_search.path_scope_forbidden")
        return normalize_relative(normalized[len(logical_root):])
    return normalized


def _canonical_v2_item(
    repo: Path,
    policy: SourceSearchPolicy,
    component_id: str,
    operation: str,
    raw: Mapping[str, object],
) -> list[dict[str, object]]:
    if raw.get("kind") != "canonical-hit":
        if raw.get("kind") == "native-text" and operation not in {
            "graph.overview", "graph.schema", "metadata.info",
            "diagnostics.catalog", "diagnostics.schema",
        }:
            return [{
                "kind": "degraded",
                "reason": "source_search.native_source_text_discarded",
                "narrowing_hint": "use a structured BSL Analyzer response",
            }]
        return [dict(raw)]
    relative = normalize_relative(str(raw.get("component_relative_path", "")))
    evidence = indexes.canonical_evidence(repo, component_id, relative)
    if not any(
        evidence["path"] == allowed.rstrip("/")
        or evidence["path"].startswith(allowed.rstrip("/") + "/")
        for allowed in policy["logical_path_prefixes"]
    ):
        raise ValueError("source_search.backend_result_out_of_scope")
    hit = {
        "kind": "canonical-hit",
        **evidence,
        **{
            key: raw[key]
            for key in ("line", "symbol")
            if raw.get(key) is not None
        },
    }
    if operation != "graph.source":
        return [hit]
    component = next(
        item for item in indexes.discover(repo)
        if item["component_id"] == component_id
    )
    path = (
        repo / "sources/generations" / component["source_generation_id"]
        / component["path"] / relative
    )
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    raw_line = raw.get("line", 1)
    line = max(1, min(raw_line if isinstance(raw_line, int) else 1, len(lines) or 1))
    start = max(1, line - 10)
    excerpt = "\n".join(lines[start - 1:start + 19])
    return [hit, {
        "kind": "canonical-excerpt",
        "path": evidence["path"],
        "fingerprint": evidence["fingerprint"],
        "text": excerpt[:16384],
        "start_line": start,
    }]


def _backend_state(value: object) -> indexes.BackendState:
    raw = json_object(value)
    state = indexes.BackendState()
    adapter_id = raw.get("adapter_id")
    component_id = raw.get("component_id")
    status = raw.get("status")
    adapter_version = raw.get("adapter_version")
    capability_fingerprint = raw.get("capability_fingerprint")
    index_fingerprint = raw.get("index_fingerprint")
    if isinstance(adapter_id, str): state["adapter_id"] = adapter_id
    if isinstance(component_id, str): state["component_id"] = component_id
    if isinstance(status, str): state["status"] = status
    if isinstance(adapter_version, str): state["adapter_version"] = adapter_version
    if isinstance(capability_fingerprint, str): state["capability_fingerprint"] = capability_fingerprint
    if isinstance(index_fingerprint, str): state["index_fingerprint"] = index_fingerprint
    capabilities = raw.get("capabilities")
    if isinstance(capabilities, list):
        capability_values = [item for item in capabilities if isinstance(item, str)]
        if len(capability_values) == len(capabilities):
            state["capabilities"] = capability_values
    last_validation = raw.get("last_validation")
    if last_validation is None or isinstance(last_validation, str):
        state["last_validation"] = last_validation
    legacy_adopted = raw.get("legacy_adopted")
    if isinstance(legacy_adopted, bool):
        state["legacy_adopted"] = legacy_adopted
    return state


def execute_query(
    repo: Path,
    policy: SourceSearchPolicy,
    request: dict[str, object],
    states: list[indexes.BackendState],
    query_backend: Callable[[indexes.BackendDecision, Mapping[str, object]], JsonValue],
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    requested_operation = str(request["operation"])
    operation = normalize_operation(requested_operation)
    if operation not in {normalize_operation(item) for item in policy["operations"]}:
        raise ValueError("source_search.operation_forbidden")
    normalized = _v2_request(request, operation, policy)
    if operation.startswith("reference."):
        from . import reference_search

        executable = indexes.backend_executable(repo, "bsl-analyzer")
        backend = next(
            (
                item for item in indexes.load_config(repo)["backends"]
                if item["adapter_id"] == "bsl-analyzer"
            ),
            None,
        )
        if not executable or not backend:
            raise RuntimeError("source_search.reference_backend_unavailable")
        probe = indexes.probe_backend(repo, backend)
        surface = probe.get("surface_manifest", {})
        surface_identity = str(surface.get("surface_fingerprint", ""))
        executable_fingerprint = probe.get("executable_fingerprint")
        capability_fingerprint = probe.get("capability_fingerprint")
        if (
            not probe.get("available")
            or not surface_identity.startswith("sha256:")
            or not isinstance(executable_fingerprint, str)
            or not isinstance(capability_fingerprint, str)
        ):
            raise RuntimeError("source_search.reference_surface_incompatible")
        _selected, reference_identity, _probe, reference_root = (
            indexes.reference_index_target(repo, backend)
        )
        token = None
        acknowledged = False
        if operation == "reference.its_help":
            from . import search_services
            _profile_id, its_profile, token = search_services.selected_profile(
                repo, "its"
            )
            acknowledged = bool(
                its_profile.get("disclosure_acknowledged")
            )
            service_identity = its_profile.get("service_identity")
            secret_version = its_profile.get("secret_version")
            if not isinstance(service_identity, str) or not isinstance(secret_version, str):
                raise RuntimeError("source_search.reference_profile_incomplete")
            reference_identity = "sha256:" + sha256(canonical_json({
                "schema_version": "its-reference-identity/v1",
                "executable_fingerprint": executable_fingerprint,
                "surface_identity": surface_identity,
                "service": service_identity,
                "secret_version": secret_version,
            }))
        if operation in {
            "reference.find_docs", "reference.search_docs",
        }:
            try:
                reference_root = reference_search.current_reference_index(
                    reference_root, executable_fingerprint
                )
            except (OSError, KeyError, ValueError):
                raise RuntimeError(
                    "source_search.reference_index_not_ready: run indexes.build"
                )
        backend_result = reference_search.execute_reference(
            Path(executable),
            reference_root,
            normalized,
            token=token,
            disclosure_acknowledged=acknowledged,
            cancelled=cancelled,
        )
        raw_items = backend_result.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("source_search.backend_result_invalid")
        reference_items: list[dict[str, JsonValue]] = [
            json_object(raw_items[index]) for index in range(len(raw_items))
        ]
        encoded = canonical_json(reference_items)
        route = {
            "route_fingerprint": "sha256:" + sha256(canonical_json({
                "operation": operation,
                "backend": "bsl-analyzer",
                "reference_identity": reference_identity,
            })),
            "preferred_backend_id": "bsl-analyzer",
            "selected_backend_id": "bsl-analyzer",
            "fallback_reason": None,
            "adapter_version": indexes.BACKEND_CATALOG["bsl-analyzer"]["adapter_version"],
            "capability_fingerprint": capability_fingerprint,
            "index_fingerprint": reference_identity,
            "surface_identity": surface_identity,
            "reference_identity": reference_identity,
        }
        return {
            "schema_version": "source-search-result/v2",
            "operation": operation,
            "capability": V2_OPERATIONS[operation],
            "route": route,
            "items": reference_items,
            "truncated": bool(backend_result.get("truncated")),
            "narrowing_hint": backend_result.get("narrowing_hint"),
            "result_count": len(reference_items),
            "returned_bytes": len(encoded),
            "result_manifest_fingerprint": "sha256:" + sha256(encoded),
        }
    component_id = str(
        normalized.get("component_id") or policy["component_ids"][0]
    )
    if component_id not in policy["component_ids"]:
        raise ValueError("source_search.scope_forbidden")
    component = next(
        (
            item for item in indexes.discover(repo)
            if item["component_id"] == component_id
        ),
        None,
    )
    if component is None or component["source_generation_id"] != policy["source_generation_id"]:
        raise RuntimeError("source_search.source_stale")
    for field in ("path", "path_prefix"):
        if normalized.get(field):
            normalized[field] = _component_relative_request_path(
                str(normalized[field]), component_id, policy,
            )
    normalized["component_id"] = component_id
    config = indexes.load_config(repo)
    decision = indexes.select_backend(
        config, V2_OPERATIONS[operation], component, states,
    )
    backend_result = json_object(query_backend(decision, normalized))
    raw_items = backend_result.get("items")
    max_results = normalized["max_results"]
    if not isinstance(raw_items, list) or not isinstance(max_results, int) or len(raw_items) > max_results:
        raise ValueError("source_search.backend_result_invalid")
    items: list[dict[str, object]] = []
    for index in range(len(raw_items)):
        v2_raw: object = raw_items[index]
        items.extend(
            _canonical_v2_item(
                repo, policy, component_id, operation, json_object(v2_raw),
            )
        )
    encoded = canonical_json(items)
    if len(encoded) > policy["max_returned_bytes_per_call"]:
        raise ValueError("source_search.returned_bytes_exhausted")
    state = decision["state"]
    state_values: dict[str, object] = dict(state)
    adapter_version = state.get("adapter_version")
    index_fingerprint = state.get("index_fingerprint")
    if not isinstance(adapter_version, str) or not isinstance(index_fingerprint, str):
        raise ValueError("source_search.backend_state_invalid")
    result: dict[str, object] = {
        "schema_version": "source-search-result/v2",
        "operation": operation,
        "capability": V2_OPERATIONS[operation],
        "route": {
            "route_fingerprint": decision["route_fingerprint"],
            "preferred_backend_id": decision["preferred_backend_id"],
            "selected_backend_id": decision["selected_backend_id"],
            "fallback_reason": decision["fallback_reason"],
            "adapter_version": adapter_version,
            "capability_fingerprint": state.get(
                "capability_fingerprint", ""
            ),
            "index_fingerprint": index_fingerprint,
            **(
                {"surface_identity": state_values["surface_identity"]}
                if state_values.get("surface_identity") else {}
            ),
            **(
                {"embedding_identity": backend_result["embedding_identity"]}
                if operation == "code.search_hybrid"
                and backend_result.get("embedding_identity") else {}
            ),
        },
        "items": items,
        "truncated": bool(backend_result.get("truncated")),
        "narrowing_hint": backend_result.get("narrowing_hint"),
        "result_count": len(items),
        "returned_bytes": len(encoded),
        "result_manifest_fingerprint": "sha256:" + sha256(encoded),
    }
    return result


def revalidate_proposal_evidence(
    repo: Path,
    policy: SourceSearchPolicy,
    payload: object,
) -> list[dict[str, str]]:
    def nested_objects(value: JsonValue) -> Iterator[dict[str, JsonValue]]:
        if isinstance(value, Mapping):
            row = dict(value)
            yield row
            for child in row.values():
                yield from nested_objects(child)
        elif isinstance(value, list):
            for child in value:
                yield from nested_objects(child)

    candidates = [
        row
        for row in nested_objects(json_object(payload))
        if isinstance(row.get("path"), str)
        and isinstance(row.get("fingerprint"), str)
    ]
    manifests: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in candidates:
        logical_path = str(row["path"]).replace("\\", "/")
        if not any(
            logical_path == prefix.rstrip("/") or logical_path.startswith(prefix)
            for prefix in policy["logical_path_prefixes"]
        ):
            continue
        matches: list[dict[str, str]] = []
        for component_id in policy["component_ids"]:
            prefix = _logical_prefix(component_id)
            if not logical_path.startswith(prefix):
                continue
            relative = logical_path[len(prefix):]
            if not relative:
                continue
            try:
                evidence = indexes.canonical_evidence(repo, component_id, relative)
            except ValueError:
                continue
            if (
                evidence["source_generation_id"] == policy["source_generation_id"]
                and evidence["fingerprint"] == row["fingerprint"]
            ):
                matches.append(evidence)
        if len(matches) != 1:
            raise ValueError("source_search.final_evidence_stale_or_unbound")
        match = matches[0]
        manifests[(match["component_id"], match["path"], match["fingerprint"])] = match
    return [manifests[key] for key in sorted(manifests)]


def revalidate_reuse_environment(
    repo: Path,
    policy: SourceSearchPolicy,
    ledger: SearchLedger,
) -> None:
    config = indexes.load_config(repo)
    current_routing = "sha256:" + sha256(canonical_json(config["routes"]))
    if current_routing != policy["routing_fingerprint"]:
        raise RuntimeError("source_search.route_stale")
    components = {
        item["component_id"]: item
        for item in indexes.discover(repo)
        if item["component_id"] in policy["component_ids"]
    }
    states = [_backend_state(row) for row in indexes.backend_statuses(repo)]
    for item in ledger["items"]:
        if item.get("status") != "completed":
            continue
        compatible = False
        for component in components.values():
            try:
                capability = item.get("capability")
                if not isinstance(capability, str):
                    raise RuntimeError("source_search.ledger_incomplete")
                decision = indexes.select_backend(config, capability, component, states)
            except RuntimeError:
                continue
            if (
                decision["route_fingerprint"] == item.get("route_fingerprint")
                and decision["selected_backend_id"] == item.get("adapter_id")
                and decision["state"].get("index_fingerprint")
                == item.get("index_fingerprint")
            ):
                compatible = True
                break
        if not compatible:
            raise RuntimeError("source_search.route_or_index_stale")


def reuse_binding(
    policy: SourceSearchPolicy,
    ledger: SearchLedger,
    evidence_manifest: list[dict[str, str]],
    hmac_key_version: str,
) -> dict[str, object]:
    if (
        not ledger.get("ledger_complete")
        or not ledger.get("reconciled")
        or ledger.get("policy_fingerprint") != policy["policy_fingerprint"]
        or ledger.get("scope_fingerprint") != policy["scope_fingerprint"]
    ):
        raise RuntimeError("source_search.ledger_incomplete")
    ledger_key_versions = {
        str(item.get("query_hmac", "")).split(":", 1)[0]
        for item in ledger["items"]
    }
    if ledger_key_versions != {hmac_key_version}:
        raise RuntimeError("source_search.hmac_key_rotated")
    stable_calls = sorted({
        canonical_json({
            "capability": item.get("capability"),
            "route_fingerprint": item.get("route_fingerprint"),
            "fallback_reason": item.get("fallback_reason"),
            "adapter_id": item.get("adapter_id"),
            "adapter_version": item.get("adapter_version"),
            "capability_fingerprint": item.get("capability_fingerprint"),
            "index_fingerprint": item.get("index_fingerprint"),
            "result_manifest_fingerprint": item.get("result_manifest_fingerprint"),
            "status": item.get("status"),
        }).decode()
        for item in ledger["items"]
    })
    binding: dict[str, object] = {
        "schema_version": "source-search-reuse/v1",
        "policy_schema_version": policy["schema_version"],
        "policy_fingerprint": policy["policy_fingerprint"],
        "scope_fingerprint": policy["scope_fingerprint"],
        "source_generation_id": policy["source_generation_id"],
        "routing_fingerprint": policy["routing_fingerprint"],
        "query_hmac_key_version": hmac_key_version,
        "calls": [parse_json_object(item) for item in stable_calls],
        "canonical_evidence": evidence_manifest,
    }
    binding["binding_fingerprint"] = "sha256:" + sha256(canonical_json(binding))
    return binding
