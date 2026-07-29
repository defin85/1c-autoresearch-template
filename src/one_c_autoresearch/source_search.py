from __future__ import annotations

import json
from hashlib import sha256 as hashlib_sha256
from pathlib import Path
from typing import Any, Callable

from . import indexes
from .contracts import canonical_json, normalize_relative, sha256


POLICY_VERSION = "source-search-policy/v1"
TOOL_SCHEMA_VERSION = "source-search-tool/v1"
BRIDGE_VERSION = "source-search-mcp/v1"
OPERATIONS = {
    "search_text": "text-search",
    "find_symbol": "symbol-definition",
    "find_references": "symbol-references",
    "find_callers": "callers",
    "find_callees": "callees",
    "navigate_metadata": "metadata-navigation",
}
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
    "analyzer": frozenset(OPERATIONS),
    "grouper": frozenset(OPERATIONS),
    "coordinator": frozenset(OPERATIONS),
    "classifier": frozenset({"search_text", "find_symbol"}),
    "researcher": frozenset(OPERATIONS),
}
TOOL_FRAMING_BYTES_PER_CALL = 512


def validate_profile_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != POLICY_FIELDS:
        raise ValueError("invalid source_search policy fields")
    operations = value.get("operations")
    if (
        not isinstance(operations, list)
        or not operations
        or any(operation not in OPERATIONS for operation in operations)
        or len(operations) != len(set(operations))
    ):
        raise ValueError("invalid source_search operations")
    normalized = {"operations": list(operations)}
    for field, maximum in POLICY_LIMITS.items():
        item = value.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0 or item > maximum:
            raise ValueError(f"invalid source_search limit: {field}")
        normalized[field] = item
    if normalized["max_concurrent_calls"] > normalized["max_calls"]:
        raise ValueError("source_search concurrency exceeds call budget")
    if normalized["max_query_bytes"] > normalized["max_total_query_bytes"]:
        raise ValueError("source_search query limit exceeds aggregate")
    if normalized["max_results_per_call"] > normalized["max_total_results"]:
        raise ValueError("source_search result limit exceeds aggregate")
    if normalized["max_returned_bytes_per_call"] > normalized["max_total_returned_bytes"]:
        raise ValueError("source_search byte limit exceeds aggregate")
    return normalized


def dynamic_reserve_bytes(policy: dict[str, Any]) -> int:
    return (
        int(policy["max_total_query_bytes"])
        + int(policy["max_total_returned_bytes"])
        + int(policy["max_calls"]) * TOOL_FRAMING_BYTES_PER_CALL
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
    profile: dict[str, Any],
    role_id: str,
    work_unit: dict[str, Any],
) -> dict[str, Any] | None:
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
    diff = work_unit.get("diff") if isinstance(work_unit.get("diff"), dict) else {}
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
    logical_paths = []
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
    pointer = __import__("json").loads(
        (repo / "research/active-source-generation.json").read_text(encoding="utf-8")
    )
    config = indexes.load_config(repo)
    result = {
        "schema_version": POLICY_VERSION,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "bridge_version": BRIDGE_VERSION,
        "operations": operations,
        "source_generation_id": str(pointer["generation_id"]),
        "component_ids": component_ids,
        "logical_path_prefixes": sorted(set(logical_paths)),
        "routing_fingerprint": "sha256:" + sha256(canonical_json(config["routes"])),
        **{field: policy[field] for field in POLICY_LIMITS},
    }
    result["scope_fingerprint"] = "sha256:" + sha256(canonical_json({
        "source_generation_id": result["source_generation_id"],
        "component_ids": component_ids,
        "logical_path_prefixes": result["logical_path_prefixes"],
    }))
    result["policy_fingerprint"] = "sha256:" + sha256(canonical_json(result))
    return result


def query_hmac(key: bytes, key_version: str, query: dict[str, Any]) -> str:
    import hmac
    return f"{key_version}:" + hmac.new(key, canonical_json(query), hashlib_sha256).hexdigest()


def request_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "query", "component_id", "path_prefix", "max_results"],
        "properties": {
            "operation": {"type": "string", "enum": sorted(OPERATIONS)},
            "query": {"type": "string", "minLength": 1, "maxLength": POLICY_LIMITS["max_query_bytes"]},
            "component_id": {"type": ["string", "null"]},
            "path_prefix": {"type": ["string", "null"]},
            "max_results": {"type": "integer", "minimum": 1, "maximum": POLICY_LIMITS["max_results_per_call"]},
        },
    }


def execute_query(
    repo: Path,
    policy: dict[str, Any],
    request: dict[str, Any],
    states: list[dict[str, Any]],
    query_backend: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]],
) -> dict[str, Any]:
    if set(request) != {"operation", "query", "component_id", "path_prefix", "max_results"}:
        raise ValueError("source_search.invalid_request")
    operation = str(request["operation"])
    if operation not in policy["operations"]:
        raise ValueError("source_search.operation_forbidden")
    query = str(request["query"])
    if not query or len(query.encode()) > policy["max_query_bytes"]:
        raise ValueError("source_search.query_bytes_exhausted")
    requested = request["max_results"]
    if not isinstance(requested, int) or isinstance(requested, bool) or not 1 <= requested <= policy["max_results_per_call"]:
        raise ValueError("source_search.result_limit_invalid")
    component_id = str(request.get("component_id") or policy["component_ids"][0])
    if component_id not in policy["component_ids"]:
        raise ValueError("source_search.scope_forbidden")
    component = next((item for item in indexes.discover(repo) if item["component_id"] == component_id), None)
    if component is None or component["source_generation_id"] != policy["source_generation_id"]:
        raise RuntimeError("source_search.source_stale")
    path_prefix = request.get("path_prefix")
    component_relative_prefix = None
    if path_prefix:
        normalized_prefix = normalize_relative(str(path_prefix))
        logical_root = _logical_prefix(component_id)
        if not (
            normalized_prefix == logical_root.rstrip("/")
            or normalized_prefix.startswith(logical_root)
        ):
            raise ValueError("source_search.path_scope_forbidden")
        if not any(
            normalized_prefix == allowed.rstrip("/")
            or normalized_prefix.startswith(allowed.rstrip("/") + "/")
            for allowed in policy["logical_path_prefixes"]
        ):
            raise ValueError("source_search.path_scope_forbidden")
        component_relative_prefix = normalized_prefix[len(logical_root):] or None
    config = indexes.load_config(repo)
    decision = indexes.select_backend(config, OPERATIONS[operation], component, states)
    raw_hits = query_backend(decision, {
        "operation": operation,
        "query": query,
        "component_id": component_id,
        "component_relative_path_prefix": component_relative_prefix,
        "max_results": requested,
    })
    if not isinstance(raw_hits, list) or len(raw_hits) > requested:
        raise ValueError("source_search.backend_result_invalid")
    canonical = []
    identities: dict[tuple[str, int | None, str], bytes] = {}
    for raw in raw_hits:
        hit = indexes.normalize_hit(raw, decision, component)
        if component_relative_prefix and not (
            hit["component_relative_path"] == component_relative_prefix.rstrip("/")
            or hit["component_relative_path"].startswith(
                component_relative_prefix.rstrip("/") + "/"
            )
        ):
            raise ValueError("source_search.backend_result_out_of_scope")
        evidence = indexes.canonical_evidence(repo, component_id, hit["component_relative_path"])
        if not any(
            evidence["path"] == allowed.rstrip("/")
            or evidence["path"].startswith(allowed.rstrip("/") + "/")
            for allowed in policy["logical_path_prefixes"]
        ):
            raise ValueError("source_search.backend_result_out_of_scope")
        item = {
            **evidence,
            **{key: hit[key] for key in ("line", "symbol", "kind") if key in hit},
        }
        identity = (item["path"], item.get("line"), item["kind"])
        encoded_item = canonical_json(item)
        if identity in identities and identities[identity] != encoded_item:
            raise ValueError("source_search.conflicting_hits")
        if identity not in identities:
            identities[identity] = encoded_item
            canonical.append(item)
    encoded = canonical_json(canonical)
    if len(encoded) > policy["max_returned_bytes_per_call"]:
        raise ValueError("source_search.returned_bytes_exhausted")
    return {
        "schema_version": "source-search-result/v1",
        "operation": operation,
        "capability": OPERATIONS[operation],
        "route": {
            "route_fingerprint": decision["route_fingerprint"],
            "preferred_backend_id": decision["preferred_backend_id"],
            "selected_backend_id": decision["selected_backend_id"],
            "fallback_reason": decision["fallback_reason"],
            "adapter_version": decision["state"]["adapter_version"],
            "capability_fingerprint": decision["state"].get("capability_fingerprint", ""),
            "index_fingerprint": decision["state"]["index_fingerprint"],
        },
        "items": canonical,
        "result_count": len(canonical),
        "returned_bytes": len(encoded),
        "result_manifest_fingerprint": "sha256:" + sha256(encoded),
    }


def revalidate_proposal_evidence(
    repo: Path,
    policy: dict[str, Any],
    payload: dict[str, Any],
) -> list[dict[str, str]]:
    candidates = [
        row
        for row in indexes._nested_dicts(payload)
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
    policy: dict[str, Any],
    ledger: dict[str, Any],
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
    states = indexes.backend_statuses(repo)
    for item in ledger["items"]:
        if item.get("status") != "completed":
            continue
        compatible = False
        for component in components.values():
            try:
                decision = indexes.select_backend(
                    config, str(item["capability"]), component, states,
                )
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
    policy: dict[str, Any],
    ledger: dict[str, Any],
    evidence_manifest: list[dict[str, str]],
    hmac_key_version: str,
) -> dict[str, Any]:
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
    binding = {
        "schema_version": "source-search-reuse/v1",
        "policy_schema_version": policy["schema_version"],
        "policy_fingerprint": policy["policy_fingerprint"],
        "scope_fingerprint": policy["scope_fingerprint"],
        "source_generation_id": policy["source_generation_id"],
        "routing_fingerprint": policy["routing_fingerprint"],
        "query_hmac_key_version": hmac_key_version,
        "calls": [json.loads(item) for item in stable_calls],
        "canonical_evidence": evidence_manifest,
    }
    binding["binding_fingerprint"] = "sha256:" + sha256(canonical_json(binding))
    return binding
