from __future__ import annotations

import http.client
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import ssl
import stat
import threading
import time
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, final
from typing_extensions import override
from urllib.parse import urlsplit

from .contracts import (
    SECRET_KEYS,
    JsonValue,
    atomic_json,
    canonical_json,
    confined,
    json_object,
    parse_json,
    parse_json_object,
    repository_lock,
    sha256,
)
from .platform_support import current_uid
from .user_state import state_root, workspace_id


SCHEMA_VERSION = "search-services/v1"
PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 4_194_304
MAX_VECTORS = 256
MAX_DEADLINE_SECONDS = 60.0
PROBE_LIMITS: OperationLimits = {"requests": 1, "input_bytes": 65_536, "vectors": 1, "elapsed_seconds": 10.0}
QUERY_LIMITS: OperationLimits = {"requests": 1, "input_bytes": 65_536, "vectors": 1, "elapsed_seconds": 30.0}
BUILD_MAXIMA = {
    "requests": 10_000,
    "input_bytes": 2 * 1024 * 1024 * 1024,
    "vectors": 1_000_000,
    "concurrency": 4,
    "batch": 256,
    "elapsed_seconds": 1_800.0,
}
APPROVED_CA_BUNDLES: dict[str, Path] = {}
ITS_ENDPOINT = "https://code.1c.ai"


class BuildLimits(TypedDict):
    requests: int
    input_bytes: int
    vectors: int
    concurrency: int
    batch: int
    elapsed_seconds: float


class OperationLimits(TypedDict):
    requests: int
    input_bytes: int
    vectors: int
    elapsed_seconds: float


class ItsProfile(TypedDict):
    kind: Literal["its"]
    label: str
    enabled: bool
    credential: NotRequired[str | None]
    secret_version: NotRequired[str | None]
    service_identity: NotRequired[str]
    disclosure_acknowledged: NotRequired[bool]


class EmbeddingProfile(TypedDict):
    kind: Literal["embedding"]
    label: str
    enabled: bool
    provider: Literal["openai-compatible"]
    endpoint: str
    endpoint_class: Literal["public_https", "loopback_http"]
    model: str
    dimension: int
    ca_bundle_id: str | None
    build_limits: BuildLimits
    max_request_bytes: int
    max_response_bytes: int
    max_vectors: int
    deadline_seconds: float
    credential: NotRequired[str | None]
    secret_version: NotRequired[str | None]
    endpoint_hmac: NotRequired[str]
    semantic_identity: NotRequired[str]


StoredProfile = ItsProfile | EmbeddingProfile
JsonObject = dict[str, JsonValue]


class AppliedOperation(TypedDict):
    plan_fingerprint: str
    actor: str
    result: JsonObject


class SearchState(TypedDict):
    schema_version: Literal["search-services/v1"]
    identity_key: str
    profiles: dict[str, StoredProfile]
    applied: dict[str, AppliedOperation]


class ModalityStatus(TypedDict):
    ready: bool
    reason: str | None


class HybridStatus(ModalityStatus):
    embedding_identity: str | None


class SearchReadiness(TypedDict):
    lexical: ModalityStatus
    hybrid: HybridStatus


class ProfilePreview(TypedDict):
    schema: Literal["search-services-profile-preview/v1"]
    project: str
    actor: str
    idempotency_key: str
    expires_at: int
    identity_key: str
    profile_id: str
    profile: StoredProfile
    plan: JsonObject
    plan_fingerprint: str


def _root(repo: Path, base: Path | None = None) -> Path:
    root = confined(state_root(base), Path("projects") / workspace_id(repo))
    current = root
    missing: list[Path] = []
    while not current.exists():
        missing.append(current)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise ValueError("search_services.unsafe_state_root")
    for path in reversed(missing):
        path.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def _read_json(path: Path) -> JsonObject:
    if path.is_symlink():
        raise ValueError("search_services.unsafe_state_file")
    if not path.exists():
        return {}
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (current_uid() is not None and metadata.st_uid != current_uid())
            or metadata.st_mode & 0o077
        ):
            raise ValueError("search_services.unsafe_state_file")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            value = parse_json_object(stream.read())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return value


def _write_json(path: Path, value: object) -> None:
    if path.is_symlink():
        raise ValueError("search_services.unsafe_state_file")
    atomic_json(path, value)
    path.chmod(0o600)


def _path(repo: Path, base: Path | None = None) -> Path:
    return _root(repo, base) / "search-services.json"


def _empty_state(identity_key: str | None = None) -> SearchState:
    return {
        "schema_version": SCHEMA_VERSION,
        "identity_key": identity_key or secrets.token_hex(32),
        "profiles": {},
        "applied": {},
    }


def _state(repo: Path, base: Path | None = None) -> SearchState:
    value = _read_json(_path(repo, base))
    if not value:
        return _empty_state()
    identity_key = value.get("identity_key")
    profiles_value = value.get("profiles")
    applied_value = value.get("applied")
    if value.get("schema_version") != SCHEMA_VERSION or not isinstance(identity_key, str):
        raise ValueError("search_services.invalid_state")
    profiles_object = json_object(profiles_value)
    profiles = {profile_id: _stored_profile(profile) for profile_id, profile in profiles_object.items()}
    applied_object = json_object(applied_value)
    applied: dict[str, AppliedOperation] = {}
    for key, operation_value in applied_object.items():
        operation = json_object(operation_value)
        fingerprint = operation.get("plan_fingerprint")
        actor = operation.get("actor")
        result = json_object(operation.get("result"))
        if not isinstance(fingerprint, str) or not isinstance(actor, str):
            raise ValueError("search_services.invalid_state")
        applied[key] = {"plan_fingerprint": fingerprint, "actor": actor, "result": result}
    return {
        "schema_version": SCHEMA_VERSION,
        "identity_key": identity_key,
        "profiles": profiles,
        "applied": applied,
    }


def _normalized_endpoint(value: str) -> tuple[str, Literal["public_https", "loopback_http"]]:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") not in {"", "/v1"}
    ):
        raise ValueError("search_services.endpoint_forbidden")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("search_services.endpoint_forbidden") from exc
    default = 443 if parsed.scheme == "https" else 80
    host = parsed.hostname.lower()
    authority = (f"[{host}]" if ":" in host else host) + (
        f":{port}" if port and port != default else ""
    )
    endpoint = f"{parsed.scheme}://{authority}{'/v1' if parsed.path.rstrip('/') == '/v1' else ''}"
    return endpoint, "public_https" if parsed.scheme == "https" else "loopback_http"


def _addresses(host: str, port: int, scheme: str) -> list[str]:
    try:
        values = sorted({str(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except socket.gaierror as exc:
        raise RuntimeError("search_services.endpoint_unavailable") from exc
    if not values:
        raise RuntimeError("search_services.endpoint_unavailable")
    parsed = [ipaddress.ip_address(value) for value in values]
    if scheme == "http":
        if not all(value.is_loopback for value in parsed):
            raise ValueError("search_services.endpoint_address_forbidden")
    elif not all(value.is_global for value in parsed):
        raise ValueError("search_services.endpoint_address_forbidden")
    return values


def _validate_profile(
    profile: dict[str, object], *, verify_address: bool = True,
) -> ItsProfile | EmbeddingProfile:
    if set(profile) - {
        "kind", "label", "endpoint", "model", "dimension", "max_request_bytes",
        "max_response_bytes", "max_vectors", "deadline_seconds", "provider",
        "enabled", "ca_bundle_id", "build_limits",
    }:
        raise ValueError("search_services.invalid_profile")
    kind = profile.get("kind")
    label = profile.get("label")
    if kind not in {"embedding", "its"} or not isinstance(label, str) or not label.strip():
        raise ValueError("search_services.invalid_profile")
    enabled = profile.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("search_services.invalid_profile")
    if kind == "its":
        return {"kind": "its", "label": label.strip(), "enabled": enabled}
    endpoint, endpoint_class = _normalized_endpoint(str(profile.get("endpoint", "")))
    parsed_endpoint = urlsplit(endpoint)
    if verify_address:
        _ = _addresses(
            parsed_endpoint.hostname or "",
            parsed_endpoint.port or (443 if parsed_endpoint.scheme == "https" else 80),
            parsed_endpoint.scheme,
        )
    model = str(profile.get("model", "")).strip()
    dimension = profile.get("dimension")
    if not model or not isinstance(dimension, int) or isinstance(dimension, bool) or dimension <= 0:
        raise ValueError("search_services.invalid_profile")
    provider = profile.get("provider", "openai-compatible")
    ca_bundle_id = profile.get("ca_bundle_id")
    if provider != "openai-compatible" or (
        ca_bundle_id is not None
        and (not isinstance(ca_bundle_id, str) or ca_bundle_id not in APPROVED_CA_BUNDLES)
    ):
        raise ValueError("search_services.invalid_profile")
    try:
        build_limits_value = json_object(profile.get("build_limits"))
    except ValueError as exc:
        raise ValueError("search_services.invalid_profile") from exc

    if set(build_limits_value) != set(BUILD_MAXIMA):
        raise ValueError("search_services.invalid_profile")
    for name, maximum in BUILD_MAXIMA.items():
        value = build_limits_value[name]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value <= 0
            or value > maximum
            or name != "elapsed_seconds" and not isinstance(value, int)
        ):
            raise ValueError("search_services.invalid_profile")
    limits: dict[str, tuple[object, int | float]] = {
        "max_request_bytes": (profile.get("max_request_bytes", MAX_REQUEST_BYTES), MAX_REQUEST_BYTES),
        "max_response_bytes": (profile.get("max_response_bytes", MAX_RESPONSE_BYTES), MAX_RESPONSE_BYTES),
        "max_vectors": (profile.get("max_vectors", MAX_VECTORS), MAX_VECTORS),
        "deadline_seconds": (profile.get("deadline_seconds", 30.0), MAX_DEADLINE_SECONDS),
    }
    for name, (value, maximum) in limits.items():
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value <= 0
            or value > maximum
            or name != "deadline_seconds" and not isinstance(value, int)
        ):
            raise ValueError("search_services.invalid_profile")
    requests = build_limits_value["requests"]
    input_bytes = build_limits_value["input_bytes"]
    vectors = build_limits_value["vectors"]
    concurrency = build_limits_value["concurrency"]
    batch = build_limits_value["batch"]
    elapsed_seconds = build_limits_value["elapsed_seconds"]
    assert isinstance(requests, int) and not isinstance(requests, bool)
    assert isinstance(input_bytes, int) and not isinstance(input_bytes, bool)
    assert isinstance(vectors, int) and not isinstance(vectors, bool)
    assert isinstance(concurrency, int) and not isinstance(concurrency, bool)
    assert isinstance(batch, int) and not isinstance(batch, bool)
    assert isinstance(elapsed_seconds, (int, float)) and not isinstance(elapsed_seconds, bool)
    max_request_bytes = limits["max_request_bytes"][0]
    max_response_bytes = limits["max_response_bytes"][0]
    max_vectors = limits["max_vectors"][0]
    deadline_seconds = limits["deadline_seconds"][0]
    assert isinstance(max_request_bytes, int) and not isinstance(max_request_bytes, bool)
    assert isinstance(max_response_bytes, int) and not isinstance(max_response_bytes, bool)
    assert isinstance(max_vectors, int) and not isinstance(max_vectors, bool)
    assert isinstance(deadline_seconds, (int, float)) and not isinstance(deadline_seconds, bool)
    return {
        "kind": "embedding",
        "label": label.strip(),
        "enabled": enabled,
        "provider": "openai-compatible",
        "endpoint": endpoint,
        "endpoint_class": endpoint_class,
        "model": model,
        "dimension": dimension,
        "ca_bundle_id": ca_bundle_id,
        "build_limits": {
            "requests": requests, "input_bytes": input_bytes, "vectors": vectors,
            "concurrency": concurrency, "batch": batch, "elapsed_seconds": float(elapsed_seconds),
        },
        "max_request_bytes": max_request_bytes,
        "max_response_bytes": max_response_bytes,
        "max_vectors": max_vectors,
        "deadline_seconds": float(deadline_seconds),
    }


def _stored_profile(value: JsonValue) -> StoredProfile:
    source = json_object(value)
    public_keys = {
        "kind", "label", "endpoint", "model", "dimension", "max_request_bytes",
        "max_response_bytes", "max_vectors", "deadline_seconds", "provider", "enabled",
        "ca_bundle_id", "build_limits",
    }
    public_profile: dict[str, object] = {}
    for key in public_keys:
        child = source.get(key)
        if key in source:
            public_profile[key] = child
    normalized = _validate_profile(public_profile, verify_address=False)
    credential = source.get("credential")
    secret_version = source.get("secret_version")
    if credential is not None and not isinstance(credential, str):
        raise ValueError("search_services.invalid_state")
    if secret_version is not None and not isinstance(secret_version, str):
        raise ValueError("search_services.invalid_state")
    if credential is not None:
        normalized["credential"] = credential
    if secret_version is not None:
        normalized["secret_version"] = secret_version
    if normalized["kind"] == "its":
        service_identity = source.get("service_identity")
        acknowledged = source.get("disclosure_acknowledged")
        if service_identity is not None:
            if not isinstance(service_identity, str):
                raise ValueError("search_services.invalid_state")
            normalized["service_identity"] = service_identity
        if acknowledged is not None:
            if not isinstance(acknowledged, bool):
                raise ValueError("search_services.invalid_state")
            normalized["disclosure_acknowledged"] = acknowledged
        return normalized
    endpoint_hmac = source.get("endpoint_hmac")
    semantic = source.get("semantic_identity")
    if endpoint_hmac is not None:
        if not isinstance(endpoint_hmac, str):
            raise ValueError("search_services.invalid_state")
        normalized["endpoint_hmac"] = endpoint_hmac
    if semantic is not None:
        if not isinstance(semantic, str):
            raise ValueError("search_services.invalid_state")
        normalized["semantic_identity"] = semantic
    return normalized


def _profile_preview(value: JsonObject) -> ProfilePreview:
    strings = {
        key: value.get(key)
        for key in ("project", "actor", "idempotency_key", "identity_key", "profile_id", "plan_fingerprint")
    }
    expires_at = value.get("expires_at")
    if (
        value.get("schema") != "search-services-profile-preview/v1"
        or any(not isinstance(child, str) for child in strings.values())
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
    ):
        raise ValueError("search_services.preview_invalid_or_expired")
    return {
        "schema": "search-services-profile-preview/v1",
        "project": str(strings["project"]),
        "actor": str(strings["actor"]),
        "idempotency_key": str(strings["idempotency_key"]),
        "expires_at": expires_at,
        "identity_key": str(strings["identity_key"]),
        "profile_id": str(strings["profile_id"]),
        "profile": _stored_profile(value.get("profile")),
        "plan": json_object(value.get("plan")),
        "plan_fingerprint": str(strings["plan_fingerprint"]),
    }


def _endpoint_hmac(key: str, endpoint: str) -> str:
    return "hmac-sha256:" + hmac.new(bytes.fromhex(key), endpoint.encode(), "sha256").hexdigest()


def semantic_identity(profile: EmbeddingProfile) -> str:
    if "endpoint_hmac" not in profile or "secret_version" not in profile:
        raise ValueError("search_services.semantic_identity_unavailable")
    endpoint_hmac = profile["endpoint_hmac"]
    secret_version = profile["secret_version"]
    return "sha256:" + sha256(canonical_json({
        "schema": "embedding-identity/v1",
        "provider": profile["provider"],
        "endpoint_hmac": endpoint_hmac,
        "model": profile["model"],
        "dimension": profile["dimension"],
        "protocol": "openai-embeddings/v1",
        "secret_version": secret_version,
    }))


def _projection(profile_id: str, profile: StoredProfile) -> JsonObject:
    visible: JsonObject = {
        "kind": profile["kind"], "label": profile["label"], "enabled": profile["enabled"],
    }
    secret_version = profile.get("secret_version")
    if secret_version is not None:
        visible["secret_version"] = secret_version
    if profile["kind"] == "embedding":
        semantic = profile.get("semantic_identity")
        if semantic is not None:
            visible["semantic_identity"] = semantic
        limits = profile["build_limits"]
        visible.update({
            "provider": profile["provider"], "endpoint_class": profile["endpoint_class"],
            "model": profile["model"], "dimension": profile["dimension"],
            "ca_bundle_id": profile["ca_bundle_id"],
            "build_limits": {
                "requests": limits["requests"], "input_bytes": limits["input_bytes"],
                "vectors": limits["vectors"], "concurrency": limits["concurrency"],
                "batch": limits["batch"], "elapsed_seconds": limits["elapsed_seconds"],
            },
            "max_request_bytes": profile["max_request_bytes"],
            "max_response_bytes": profile["max_response_bytes"], "max_vectors": profile["max_vectors"],
            "deadline_seconds": profile["deadline_seconds"],
        })
    else:
        service_identity = profile.get("service_identity")
        acknowledgement = profile.get("disclosure_acknowledged")
        if service_identity is not None:
            visible["service_identity"] = service_identity
        if acknowledgement is not None:
            visible["disclosure_acknowledged"] = acknowledgement
    endpoint_hmac = profile.get("endpoint_hmac") if profile["kind"] == "embedding" else None
    return {
        "profile_id": profile_id,
        **visible,
        "credential_configured": bool(profile.get("credential")),
        "endpoint_hmac_prefix": (
            endpoint_hmac[:20] if endpoint_hmac else None
        ),
    }


def list_profiles(repo: Path, base: Path | None = None) -> list[JsonObject]:
    return [
        _projection(profile_id, profile)
        for profile_id, profile in sorted(_state(repo, base)["profiles"].items())
    ]


def _state_fingerprint(state: SearchState) -> str:
    profiles = {
        profile_id: {
            key: value for key, value in profile.items()
            if key not in {"credential", "endpoint"}
        }
        for profile_id, profile in sorted(state["profiles"].items())
    }
    return "sha256:" + sha256(canonical_json({
        "schema_version": SCHEMA_VERSION,
        "profiles": profiles,
    }))


def state_fingerprint(repo: Path, base: Path | None = None) -> str:
    return _state_fingerprint(_state(repo, base))


def _validate_disclosure(value: object, build_limits: BuildLimits) -> JsonObject:
    value = json_object(value)
    if set(value) != {
        "components", "file_count", "source_bytes", "estimated_requests",
        "estimated_input_bytes", "estimated_vectors", "cost",
    }:
        raise ValueError("search_services.invalid_disclosure")
    components = value["components"]
    integer_values = [value[key] for key in (
        "file_count", "source_bytes", "estimated_requests", "estimated_input_bytes", "estimated_vectors",
    )]
    if (
        not isinstance(components, list)
        or any(not isinstance(item, str) or not item for item in components)
        or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in integer_values)
    ):
        raise ValueError("search_services.invalid_disclosure")
    cost_value = json_object(value["cost"])
    if cost_value.get("kind") not in {"known", "unknown"}:
        raise ValueError("search_services.invalid_disclosure")
    if cost_value["kind"] == "known":
        if (
            set(cost_value) != {"kind", "amount", "currency"}
            or not isinstance(cost_value["amount"], (int, float))
            or isinstance(cost_value["amount"], bool)
            or cost_value["amount"] < 0
            or not isinstance(cost_value["currency"], str)
            or not cost_value["currency"]
        ):
            raise ValueError("search_services.invalid_disclosure")
    elif set(cost_value) != {"kind"}:
        raise ValueError("search_services.invalid_disclosure")
    components_list = [item for item in components if isinstance(item, str)]
    if components_list != sorted(set(components_list)):
        raise ValueError("search_services.invalid_disclosure")
    file_count, source_bytes, estimated_requests, estimated_input_bytes, estimated_vectors = integer_values
    assert all(isinstance(item, int) and not isinstance(item, bool) for item in integer_values)
    assert isinstance(estimated_requests, int) and isinstance(estimated_input_bytes, int) and isinstance(estimated_vectors, int)
    if (
        estimated_requests > build_limits["requests"]
        or estimated_input_bytes > build_limits["input_bytes"]
        or estimated_vectors > build_limits["vectors"]
    ):
        raise ValueError("search_services.invalid_disclosure")
    cost: JsonObject = dict(cost_value)
    return {
        "components": components_list,
        "file_count": file_count,
        "source_bytes": source_bytes,
        "estimated_requests": estimated_requests,
        "estimated_input_bytes": estimated_input_bytes,
        "estimated_vectors": estimated_vectors,
        "cost": cost,
    }


def preview_profile(
    repo: Path,
    profile_id: str,
    profile: dict[str, object],
    *,
    actor: str,
    idempotency_key: str,
    expected_state_fingerprint: str,
    disclosure: dict[str, object],
    acknowledged: bool,
    secret: str | None = None,
    base: Path | None = None,
    ttl_seconds: int = 300,
) -> JsonObject:
    if (
        not PROFILE_ID.fullmatch(profile_id)
        or not actor
        or not idempotency_key
        or not 1 <= ttl_seconds <= 900
    ):
        raise ValueError("search_services.invalid_preview")
    current_state = _state(repo, base)
    current_fingerprint = _state_fingerprint(current_state)
    if expected_state_fingerprint != current_fingerprint:
        raise RuntimeError("search_services.state_changed")
    current = current_state["profiles"].get(profile_id)
    profile_to_validate = dict(profile)
    if (
        profile_to_validate.get("kind") == "embedding"
        and not profile_to_validate.get("endpoint")
        and current is not None
        and current["kind"] == "embedding"
    ):
        profile_to_validate["endpoint"] = current["endpoint"]
    normalized = _validate_profile(profile_to_validate)
    credential = current.get("credential") if current is not None else None
    secret_version = current.get("secret_version") if current is not None else None
    if secret is not None:
        if not secret:
            raise ValueError("search_services.empty_secret")
        credential = secret
        secret_version = secrets.token_hex(16)
    if normalized["kind"] == "its":
        if not acknowledged:
            raise ValueError("search_services.disclosure_acknowledgement_required")
        if not credential:
            raise ValueError("search_services.its_token_required")
        proposed: StoredProfile = {
            "kind": "its",
            "label": normalized["label"],
            "enabled": normalized["enabled"],
            "credential": credential,
            "secret_version": secret_version,
            "service_identity": ITS_ENDPOINT,
            "disclosure_acknowledged": True,
        }
        its_plan: JsonObject = {
            "schema": "search-services-profile-plan/v1",
            "project": workspace_id(repo),
            "actor": actor,
            "profile": _projection(profile_id, proposed),
            "disclosure": {
                "service": ITS_ENDPOINT,
                "question_max_bytes": 4096,
                "response_max_bytes": 32768,
                "one_in_flight": True,
            },
            "external_disclosure_acknowledged": True,
            "impact": {"search_reuse_invalidated": True, "build_started": False},
            "expected_state_fingerprint": current_fingerprint,
        }
        return _store_preview(
            repo, base, current_state, profile_id, proposed, its_plan,
            actor, idempotency_key, ttl_seconds,
        )
    if normalized["endpoint_class"] == "public_https" and not acknowledged:
        raise ValueError("search_services.disclosure_acknowledgement_required")
    normalized_disclosure = _validate_disclosure(disclosure, normalized["build_limits"])
    assert normalized["kind"] == "embedding"
    proposed = normalized.copy()
    proposed["credential"] = credential
    proposed["secret_version"] = secret_version
    proposed["endpoint_hmac"] = _endpoint_hmac(current_state["identity_key"], normalized["endpoint"])
    proposed["semantic_identity"] = semantic_identity(proposed)
    current_identity = current.get("semantic_identity") if current and current["kind"] == "embedding" else None
    impact: JsonObject = {
        "semantic_rebuild_required": current_identity != proposed["semantic_identity"],
        "search_reuse_invalidated": current_identity != proposed["semantic_identity"],
        "build_started": False,
    }
    plan: JsonObject = {
        "schema": "search-services-profile-plan/v1",
        "project": workspace_id(repo),
        "actor": actor,
        "profile": _projection(profile_id, proposed),
        "disclosure": normalized_disclosure,
        "external_disclosure_acknowledged": acknowledged,
        "impact": impact,
        "expected_state_fingerprint": current_fingerprint,
    }
    return _store_preview(
        repo, base, current_state, profile_id, proposed, plan,
        actor, idempotency_key, ttl_seconds,
    )


def _store_preview(
    repo: Path,
    base: Path | None,
    current_state: SearchState,
    profile_id: str,
    proposed: StoredProfile,
    plan: JsonObject,
    actor: str,
    idempotency_key: str,
    ttl_seconds: int,
) -> JsonObject:
    plan_fingerprint = "sha256:" + sha256(canonical_json({
        **plan,
        **(
            {"endpoint_hmac": endpoint_hmac}
            if (endpoint_hmac := proposed.get("endpoint_hmac") if proposed["kind"] == "embedding" else None) else {}
        ),
    }))
    preview_id = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + ttl_seconds
    preview_path = _root(repo, base) / f".search-services-preview-{preview_id}.json"
    _write_json(preview_path, {
        "schema": "search-services-profile-preview/v1",
        "project": workspace_id(repo),
        "actor": actor,
        "idempotency_key": idempotency_key,
        "expires_at": expires_at,
        "identity_key": current_state["identity_key"],
        "profile_id": profile_id,
        "profile": proposed,
        "plan": plan,
        "plan_fingerprint": plan_fingerprint,
    })
    return {
        "preview_id": preview_id,
        "expires_at": expires_at,
        "plan_fingerprint": plan_fingerprint,
        **plan,
    }


def apply_profile(
    repo: Path,
    preview_id: str,
    *,
    actor: str,
    idempotency_key: str,
    expected_state_fingerprint: str,
    plan_fingerprint: str,
    base: Path | None = None,
) -> JsonObject:
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", preview_id):
        raise ValueError("search_services.invalid_preview")
    applied = _state(repo, base)["applied"].get(idempotency_key)
    if applied:
        if (
            applied["plan_fingerprint"] != plan_fingerprint
            or applied["actor"] != actor
        ):
            raise ValueError("search_services.idempotency_conflict")
        return applied["result"]
    preview_path = _root(repo, base) / f".search-services-preview-{preview_id}.json"
    preview_value = _read_json(preview_path)
    if not preview_value:
        applied = _state(repo, base)["applied"].get(idempotency_key)
        if applied and applied["plan_fingerprint"] == plan_fingerprint and applied["actor"] == actor:
            return applied["result"]
        raise ValueError("search_services.preview_invalid_or_expired")
    preview = _profile_preview(preview_value)
    expected_preview_fingerprint = preview["plan"].get("expected_state_fingerprint")
    if (
        preview["project"] != workspace_id(repo)
        or preview["actor"] != actor
        or preview["idempotency_key"] != idempotency_key
        or preview["plan_fingerprint"] != plan_fingerprint
        or expected_preview_fingerprint != expected_state_fingerprint
        or time.time() >= preview["expires_at"]
    ):
        raise ValueError("search_services.preview_invalid_or_expired")
    with repository_lock(repo):
        path = _path(repo, base)
        exists = path.is_file()
        state = _state(repo, base)
        applied = state["applied"].get(idempotency_key)
        if applied:
            if (
                applied["plan_fingerprint"] != plan_fingerprint
                or applied["actor"] != actor
            ):
                raise ValueError("search_services.idempotency_conflict")
            return applied["result"]
        if _state_fingerprint(state) != expected_state_fingerprint:
            raise RuntimeError("search_services.state_changed")
        if preview["profile"]["kind"] == "embedding":
            endpoint = urlsplit(preview["profile"]["endpoint"])
            _ = _addresses(
                endpoint.hostname or "",
                endpoint.port or (443 if endpoint.scheme == "https" else 80),
                endpoint.scheme,
            )
        if not exists:
            state["identity_key"] = preview["identity_key"]
        state["profiles"][preview["profile_id"]] = preview["profile"]
        impact = json_object(preview["plan"].get("impact"))
        result: JsonObject = {
            "status": "applied",
            "state_fingerprint": "",
            "profile": _projection(preview["profile_id"], preview["profile"]),
            "impact": impact,
        }
        state["applied"][idempotency_key] = {
            "plan_fingerprint": plan_fingerprint,
            "actor": actor,
            "result": result,
        }
        result["state_fingerprint"] = _state_fingerprint(state)
        state["applied"][idempotency_key]["result"] = result
        _write_json(path, state)
        preview_path.unlink(missing_ok=True)
        return result


def private_profile(repo: Path, profile_id: str, base: Path | None) -> tuple[StoredProfile, str | None]:
    profile = _state(repo, base)["profiles"].get(profile_id)
    if not profile:
        raise KeyError("search_services.profile_not_found")
    return profile, profile.get("credential")


def selected_profile(
    repo: Path, kind: str, base: Path | None = None
) -> tuple[str, StoredProfile, str | None]:
    matches = [
        (profile_id, profile)
        for profile_id, profile in sorted(_state(repo, base)["profiles"].items())
        if profile.get("kind") == kind and profile.get("enabled")
    ]
    if len(matches) != 1:
        raise RuntimeError(f"search_services.{kind}_profile_not_selected")
    profile_id, profile = matches[0]
    return profile_id, profile, profile.get("credential")


def _request_payload(body: bytes, profile: EmbeddingProfile) -> tuple[bytes, int]:
    if len(body) > profile["max_request_bytes"]:
        raise ValueError("search_services.request_too_large")
    try:
        value = json_object(parse_json(body.decode("utf-8")))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("search_services.invalid_request") from exc
    if set(value) - {"input", "model", "encoding_format"}:
        raise ValueError("search_services.invalid_request")
    inputs = value.get("input")
    inputs = [inputs] if isinstance(inputs, str) else inputs
    if (
        not isinstance(inputs, list)
        or not inputs
        or len(inputs) > profile["max_vectors"]
        or any(not isinstance(item, str) or not item for item in inputs)
        or value.get("model") != profile["model"]
        or value.get("encoding_format", "float") != "float"
    ):
        raise ValueError("search_services.invalid_request")
    assert isinstance(inputs, list)
    string_inputs = [item for item in inputs if isinstance(item, str)]
    return json.dumps(
        {"input": string_inputs, "model": profile["model"], "encoding_format": "float"},
        ensure_ascii=False, separators=(",", ":"),
    ).encode(), len(string_inputs)


def _normalized_response(body: bytes, profile: EmbeddingProfile, expected: int) -> bytes:
    try:
        value = json_object(parse_json(body.decode("utf-8")))
        rows = value["data"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("search_services.invalid_response") from exc
    if not isinstance(rows, list) or len(rows) != expected:
        raise RuntimeError("search_services.invalid_response")
    normalized: list[JsonObject] = []
    for index, row in enumerate(rows):
        vector = row.get("embedding") if isinstance(row, dict) else None
        if (
            not isinstance(vector, list)
            or len(vector) != profile["dimension"]
            or any(
                not isinstance(item, (int, float))
                or isinstance(item, bool)
                or not math.isfinite(item)
                for item in vector
            )
        ):
            raise RuntimeError("search_services.invalid_response")
        assert isinstance(vector, list)
        numeric_vector: list[int | float] = []
        for item in vector:
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                numeric_vector.append(item)
        normalized.append({"object": "embedding", "index": index, "embedding": numeric_vector})
    return json.dumps(
        {"object": "list", "data": normalized, "model": profile["model"]},
        separators=(",", ":"),
    ).encode()


def analyzer_environment(
    inherited: dict[str, str],
    *,
    modality: str,
    profile: EmbeddingProfile | None = None,
    broker_environment: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = {
        key: value for key, value in inherited.items()
        if not (
            key.upper().startswith("EMBEDDING_")
            or key.upper().endswith("_PROXY")
            or key.upper().startswith(("BSL_ANALYZER_", "BSL_AGENT_"))
            or key.upper() in {
                "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_DIR", "SSL_CERT_FILE",
            }
            or key.upper().endswith(("_API_KEY", "_ACCESS_KEY"))
            or SECRET_KEYS.search(key)
        )
    }
    if modality == "lexical":
        return environment
    if modality != "hybrid" or not profile or not broker_environment:
        raise ValueError("search_services.invalid_modality")
    environment.update({
        "EMBEDDING_PROVIDER": "openai-compatible",
        "EMBEDDING_URL": broker_environment["EMBEDDING_URL"],
        "EMBEDDING_API_KEY": broker_environment["EMBEDDING_API_KEY"],
        "EMBEDDING_MODEL": profile["model"],
        "EMBEDDING_DIM": str(profile["dimension"]),
        "EMBEDDING_CONCURRENCY": str(profile["build_limits"]["concurrency"]),
        "EMBEDDING_BATCH_SIZE": str(profile["build_limits"]["batch"]),
    })
    return environment


def search_readiness(
    *,
    lexical_index_ready: bool,
    profile: EmbeddingProfile | None,
    semantic_index_identity: str | None,
    probe_identity: str | None,
) -> SearchReadiness:
    expected = profile.get("semantic_identity") if profile else None
    hybrid_reason = None
    if not profile or not profile.get("enabled"):
        hybrid_reason = "embedding_profile_unavailable"
    elif semantic_index_identity != expected:
        hybrid_reason = "semantic_index_stale"
    elif probe_identity != expected:
        hybrid_reason = "embedding_probe_stale"
    return {
        "lexical": {
            "ready": lexical_index_ready,
            "reason": None if lexical_index_ready else "lexical_index_unavailable",
        },
        "hybrid": {
            "ready": hybrid_reason is None,
            "reason": hybrid_reason,
            "embedding_identity": expected,
        },
    }


def require_search_modality(requested: str, readiness: SearchReadiness) -> str:
    if requested not in {"lexical", "hybrid"}:
        raise ValueError("search_services.invalid_modality")
    if not readiness[requested]["ready"]:
        raise RuntimeError(f"search_services.{requested}_unavailable")
    return requested


@final
class EmbeddingBudget:
    def __init__(self, limits: OperationLimits):
        self.limits = limits
        self.started = time.monotonic()
        self.requests = 0
        self.input_bytes = 0
        self.vectors = 0
        self._lock = threading.Lock()

    def reserve(self, input_bytes: int, vectors: int) -> None:
        with self._lock:
            if (
                time.monotonic() - self.started >= self.limits["elapsed_seconds"]
                or self.requests + 1 > self.limits["requests"]
                or self.input_bytes + input_bytes > self.limits["input_bytes"]
                or self.vectors + vectors > self.limits["vectors"]
            ):
                raise RuntimeError("search_services.embedding_budget_exhausted")
            self.requests += 1
            self.input_bytes += input_bytes
            self.vectors += vectors

    @property
    def deadline(self) -> float:
        return self.started + self.limits["elapsed_seconds"]

    def diagnostics(self) -> dict[str, int | float]:
        return {
            "requests": self.requests,
            "input_bytes": self.input_bytes,
            "vectors": self.vectors,
            "elapsed_seconds": min(
                self.limits["elapsed_seconds"], max(0.0, time.monotonic() - self.started),
            ),
        }


@final
class _RetryableUpstream(RuntimeError):
    def __init__(self, status: int, retry_after: str | None):
        super().__init__(f"search_services.upstream_http_{status}")
        self.retry_after = retry_after


def _retry_delay(value: str | None, deadline: float) -> float:
    delay = 0.1
    if value:
        try:
            delay = max(0.0, float(value))
        except ValueError:
            try:
                delay = max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                delay = 0.1
    delay = min(2.0, delay) + secrets.randbelow(101) / 1000
    return min(delay, max(0.0, deadline - time.monotonic()))


@final
class EmbeddingBroker:
    def __init__(
        self,
        repo: Path,
        profile_id: str,
        *,
        operation: str = "query",
        base: Path | None = None,
    ):
        profile, upstream_secret = private_profile(repo, profile_id, base)
        if profile["kind"] != "embedding":
            raise ValueError("search_services.not_embedding_profile")
        self.profile = profile
        self.upstream_secret = upstream_secret
        self.capability = secrets.token_urlsafe(32)
        self._cancelled = threading.Event()
        self._sockets: set[socket.socket] = set()
        self._sockets_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._concurrency = threading.BoundedSemaphore(profile["build_limits"]["concurrency"])
        self.operation = operation
        self.budget = self.new_budget(operation)

    def start(self) -> dict[str, str]:
        if self._server is not None:
            raise RuntimeError("search_services.broker_already_started")
        broker = self

        class Handler(BaseHTTPRequestHandler):
            connection: socket.socket

            @override
            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(broker.profile["deadline_seconds"])

            def do_POST(self) -> None:
                if self.path != "/v1/embeddings":
                    self._error(404, "search_services.route_not_found")
                    return
                if self.headers.get("Authorization") != f"Bearer {broker.capability}":
                    self._error(401, "search_services.authentication_failed")
                    return
                if (
                    self.headers.get_content_type() != "application/json"
                    or self.headers.get("Transfer-Encoding") is not None
                ):
                    self._error(400, "search_services.invalid_request")
                    return
                try:
                    size = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    size = -1
                if size < 0 or size > broker.profile["max_request_bytes"]:
                    self._error(413, "search_services.request_too_large")
                    return
                try:
                    result = broker.forward(
                        self.rfile.read(size),
                        operation=broker.operation,
                        budget=broker.budget,
                    )
                except (ValueError, RuntimeError) as exc:
                    self._error(502, str(exc))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(result)))
                self.end_headers()
                _ = self.wfile.write(result)

            def _error(self, status: int, code: str) -> None:
                result = json.dumps({"error": {"code": code}}, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(result)))
                self.end_headers()
                _ = self.wfile.write(result)

            @override
            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        port = self._server.server_address[1]
        return {
            "EMBEDDING_URL": f"http://127.0.0.1:{port}/v1/embeddings",
            "EMBEDDING_API_KEY": self.capability,
        }

    def new_budget(self, operation: str) -> EmbeddingBudget:
        if operation == "probe":
            limits = PROBE_LIMITS
        elif operation == "query":
            limits = QUERY_LIMITS
        elif operation == "build":
            build_limits = self.profile["build_limits"]
            limits = OperationLimits(
                requests=build_limits["requests"], input_bytes=build_limits["input_bytes"],
                vectors=build_limits["vectors"], elapsed_seconds=build_limits["elapsed_seconds"],
            )
        else:
            raise ValueError("search_services.invalid_embedding_operation")
        return EmbeddingBudget(OperationLimits(
            requests=limits["requests"], input_bytes=limits["input_bytes"],
            vectors=limits["vectors"], elapsed_seconds=limits["elapsed_seconds"],
        ))

    def forward(
        self,
        body: bytes,
        *,
        operation: str | None = None,
        budget: EmbeddingBudget | None = None,
        replay: bool = False,
    ) -> bytes:
        if self._cancelled.is_set():
            raise RuntimeError("search_services.cancelled")
        if replay:
            raise RuntimeError("search_services.replay_forbidden")
        operation = operation or self.operation
        request, expected = _request_payload(body, self.profile)
        budget = budget or (self.budget if operation == self.operation else self.new_budget(operation))
        if operation == "build" and expected > self.profile["build_limits"]["batch"]:
            raise ValueError("search_services.embedding_batch_too_large")
        deadline = min(
            budget.deadline,
            time.monotonic() + (
                PROBE_LIMITS["elapsed_seconds"] if operation == "probe"
                else QUERY_LIMITS["elapsed_seconds"] if operation == "query"
                else self.profile["build_limits"]["elapsed_seconds"]
            ),
        )
        if not self._concurrency.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise RuntimeError("search_services.embedding_budget_exhausted")
        try:
            retries = 0
            while True:
                budget.reserve(len(request), expected)
                try:
                    return self._forward_once(request, expected, deadline)
                except _RetryableUpstream as exc:
                    if retries >= 2:
                        raise RuntimeError(str(exc)) from exc
                    retries += 1
                    delay = _retry_delay(exc.retry_after, deadline)
                    if delay <= 0:
                        raise RuntimeError("search_services.deadline_exceeded") from exc
                    if self._cancelled.wait(delay):
                        raise RuntimeError("search_services.cancelled") from exc
        finally:
            self._concurrency.release()

    def _forward_once(self, request: bytes, expected: int, deadline: float) -> bytes:
        parsed = urlsplit(self.profile["endpoint"])
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        address = _addresses(parsed.hostname or "", port, parsed.scheme)[0]
        connection: socket.socket = socket.create_connection(
            (address, port), timeout=max(0.001, deadline - time.monotonic()),
        )
        if parsed.scheme == "https":
            try:
                context = ssl.create_default_context()
                ca_bundle_id = self.profile["ca_bundle_id"]
                if ca_bundle_id:
                    context.load_verify_locations(
                        cafile=str(APPROVED_CA_BUNDLES[ca_bundle_id]),
                    )
                connection = context.wrap_socket(
                    connection, server_hostname=parsed.hostname,
                )
            except BaseException:
                connection.close()
                raise
        with self._sockets_lock:
            self._sockets.add(connection)
        try:
            if connection.getpeername()[0] != address:
                raise RuntimeError("search_services.endpoint_address_changed")
            path = "/v1/embeddings"
            host = parsed.hostname or ""
            default_port = 443 if parsed.scheme == "https" else 80
            host_for_header = f"[{host}]" if ":" in host else host
            host_header = host_for_header if port == default_port else f"{host_for_header}:{port}"
            headers = {
                "Accept": "application/json",
                "Connection": "close",
                "Content-Length": str(len(request)),
                "Content-Type": "application/json",
                "Host": host_header,
                "User-Agent": "one-c-autoresearch-search-services/1",
            }
            if self.upstream_secret:
                headers["Authorization"] = f"Bearer {self.upstream_secret}"
            request_head = (
                f"POST {path} HTTP/1.1\r\n"
                + "".join(f"{key}: {value}\r\n" for key, value in headers.items())
                + "\r\n"
            ).encode("ascii")
            connection.sendall(request_head + request)
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            response = http.client.HTTPResponse(connection)
            response.begin()
            if 300 <= response.status < 400:
                raise RuntimeError("search_services.redirect_forbidden")
            if response.status != 200:
                if response.status in {429, 502, 503, 504}:
                    raise _RetryableUpstream(response.status, response.getheader("Retry-After"))
                raise RuntimeError(f"search_services.upstream_http_{response.status}")
            chunks: list[bytes] = []
            size = 0
            while True:
                if self._cancelled.is_set():
                    raise RuntimeError("search_services.cancelled")
                if time.monotonic() >= deadline:
                    raise RuntimeError("search_services.deadline_exceeded")
                chunk = response.read(min(65_536, self.profile["max_response_bytes"] - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > self.profile["max_response_bytes"]:
                    raise RuntimeError("search_services.response_too_large")
                chunks.append(chunk)
            return _normalized_response(b"".join(chunks), self.profile, expected)
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            code = "search_services.cancelled" if self._cancelled.is_set() else "search_services.endpoint_unavailable"
            raise RuntimeError(code) from exc
        finally:
            with self._sockets_lock:
                self._sockets.discard(connection)
            connection.close()

    def cancel(self) -> None:
        self._cancelled.set()
        with self._sockets_lock:
            for connection in tuple(self._sockets):
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()

    def close(self) -> None:
        self.cancel()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def __enter__(self) -> EmbeddingBroker:
        _ = self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
