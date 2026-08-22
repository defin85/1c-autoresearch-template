from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import one_c_autoresearch.search_services as search_services
from one_c_autoresearch.search_services import (
    EmbeddingBroker,
    analyzer_environment,
    apply_profile,
    list_profiles,
    preview_profile,
    require_search_modality,
    search_readiness,
    state_fingerprint,
)


def embedding_profile(endpoint: str) -> dict:
    return {
        "kind": "embedding",
        "label": "local test",
        "endpoint": endpoint,
        "model": "test-model",
        "dimension": 2,
        "build_limits": {
            "requests": 10,
            "input_bytes": 1_000_000,
            "vectors": 100,
            "concurrency": 2,
            "batch": 10,
            "elapsed_seconds": 60,
        },
    }


def disclosure() -> dict:
    return {
        "components": ["target_cf:configuration"],
        "file_count": 10,
        "source_bytes": 1000,
        "estimated_requests": 2,
        "estimated_input_bytes": 1000,
        "estimated_vectors": 10,
        "cost": {"kind": "unknown"},
    }


def install_profile(
    repo: Path,
    profile_id: str,
    profile: dict,
    *,
    secret: str | None = None,
    base: Path,
) -> dict:
    expected = state_fingerprint(repo, base)
    key = f"test-{state_fingerprint(repo, base)}-{secret or 'none'}"
    preview = preview_profile(
        repo,
        profile_id,
        profile,
        actor="test-owner",
        idempotency_key=key,
        expected_state_fingerprint=expected,
        disclosure=disclosure(),
        acknowledged=False,
        secret=secret,
        base=base,
    )
    return apply_profile(
        repo,
        preview["preview_id"],
        actor="test-owner",
        idempotency_key=key,
        expected_state_fingerprint=expected,
        plan_fingerprint=preview["plan_fingerprint"],
        base=base,
    )["profile"]


def test_profile_state_is_private_and_projection_hides_secret_and_endpoint(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    first = install_profile(repo, "semantic", embedding_profile("http://127.0.0.1:9999/v1"), secret="first", base=state)
    second = install_profile(repo, "semantic", embedding_profile("http://127.0.0.1:9999/v1"), secret="second", base=state)

    assert first["credential_configured"] is True
    assert first["secret_version"] != second["secret_version"]
    projection = list_profiles(repo, state)
    assert projection == [second]
    assert "endpoint" not in projection[0]
    service_root = next(state.glob("projects/*"))
    assert service_root.stat().st_mode & 0o777 == 0o700
    assert (service_root / "search-services.json").stat().st_mode & 0o777 == 0o600
    assert '"value"' not in json.dumps(projection)
    assert '"credential":"second"' in (service_root / "search-services.json").read_text()


def test_profile_state_rejects_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    install_profile(repo, "semantic", embedding_profile("http://127.0.0.1:9999"), base=state)
    profiles = next(state.glob("projects/*/search-services.json"))
    profiles.unlink()
    profiles.symlink_to(tmp_path / "elsewhere")

    with pytest.raises(ValueError, match="unsafe_state_file"):
        list_profiles(repo, state)


def test_profile_preview_apply_is_reviewed_idempotent_and_secret_safe(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    expected = state_fingerprint(repo, state)
    preview = preview_profile(
        repo,
        "semantic",
        embedding_profile("http://127.0.0.1:9999/v1"),
        actor="owner",
        idempotency_key="apply-1",
        expected_state_fingerprint=expected,
        disclosure=disclosure(),
        acknowledged=False,
        secret="write-only",
        base=state,
    )
    serialized = json.dumps(preview)
    assert "write-only" not in serialized
    assert "127.0.0.1" not in serialized
    assert preview["impact"] == {
        "semantic_rebuild_required": True,
        "search_reuse_invalidated": True,
        "build_started": False,
    }
    arguments = {
        "actor": "owner",
        "idempotency_key": "apply-1",
        "expected_state_fingerprint": expected,
        "plan_fingerprint": preview["plan_fingerprint"],
        "base": state,
    }
    applied = apply_profile(repo, preview["preview_id"], **arguments)

    assert applied["status"] == "applied"
    assert applied == apply_profile(repo, preview["preview_id"], **arguments)
    assert applied["profile"]["credential_configured"] is True
    assert state_fingerprint(repo, state) == applied["state_fingerprint"]


def test_existing_embedding_profile_keeps_hidden_endpoint_when_editing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    installed = install_profile(
        repo, "semantic", embedding_profile("http://127.0.0.1:9999/v1"), base=state,
    )
    edited = embedding_profile("")
    edited["label"] = "renamed"
    preview = preview_profile(
        repo, "semantic", edited, actor="owner", idempotency_key="keep-endpoint",
        expected_state_fingerprint=state_fingerprint(repo, state), disclosure=disclosure(),
        acknowledged=False, base=state,
    )

    assert preview["profile"]["label"] == "renamed"
    assert preview["impact"]["semantic_rebuild_required"] is False
    assert preview["profile"]["semantic_identity"] == installed["semantic_identity"]


def test_new_embedding_profile_still_requires_endpoint(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"

    with pytest.raises(ValueError, match="endpoint_forbidden"):
        preview_profile(
            repo, "semantic", embedding_profile(""), actor="owner",
            idempotency_key="missing-endpoint",
            expected_state_fingerprint=state_fingerprint(repo, state), disclosure=disclosure(),
            acknowledged=False, base=state,
        )


def test_remote_preview_requires_disclosure_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_services, "_addresses", lambda *args: ["203.0.113.10"])
    repo = tmp_path / "repo"
    repo.mkdir()
    profile = embedding_profile("https://embeddings.example/v1")
    arguments = {
        "actor": "owner",
        "idempotency_key": "remote-1",
        "expected_state_fingerprint": state_fingerprint(repo, tmp_path / "state"),
        "disclosure": disclosure(),
        "secret": "write-only",
        "base": tmp_path / "state",
    }
    with pytest.raises(ValueError, match="disclosure_acknowledgement_required"):
        preview_profile(repo, "semantic", profile, acknowledged=False, **arguments)
    preview = preview_profile(repo, "semantic", profile, acknowledged=True, **arguments)
    assert preview["external_disclosure_acknowledged"] is True


def test_local_profile_accepts_large_repository_within_build_limit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    profile = embedding_profile("http://127.0.0.1:9999/v1")
    profile["build_limits"]["input_bytes"] = 2 * 1024 * 1024 * 1024
    large_disclosure = disclosure()
    large_disclosure["source_bytes"] = 724_000_000
    large_disclosure["estimated_input_bytes"] = 724_000_000
    preview = preview_profile(
        repo, "semantic", profile, actor="owner", idempotency_key="large-local",
        expected_state_fingerprint=state_fingerprint(repo, state),
        disclosure=large_disclosure, acknowledged=False, base=state,
    )
    assert preview["disclosure"]["estimated_input_bytes"] == 724_000_000


def test_its_profile_is_reviewed_and_never_returns_token(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    expected = state_fingerprint(repo, state)
    with pytest.raises(ValueError, match="disclosure_acknowledgement_required"):
        preview_profile(
            repo, "its-main",
            {"kind": "its", "label": "ИТС", "enabled": True},
            actor="owner", idempotency_key="its-1",
            expected_state_fingerprint=expected, disclosure={},
            acknowledged=False, secret="write-only", base=state,
        )
    preview = preview_profile(
        repo, "its-main",
        {"kind": "its", "label": "ИТС", "enabled": True},
        actor="owner", idempotency_key="its-1",
        expected_state_fingerprint=expected, disclosure={},
        acknowledged=True, secret="write-only", base=state,
    )
    assert "write-only" not in json.dumps(preview)
    applied = apply_profile(
        repo, preview["preview_id"], actor="owner", idempotency_key="its-1",
        expected_state_fingerprint=expected,
        plan_fingerprint=preview["plan_fingerprint"], base=state,
    )
    assert applied["profile"]["service_identity"] == "https://code.1c.ai"
    assert "credential" not in applied["profile"]
    assert "embeddings.example" not in json.dumps(preview)


def test_semantic_identity_changes_with_secret_and_readiness_never_downgrades(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    first = install_profile(
        repo, "semantic", embedding_profile("http://127.0.0.1:9999"),
        secret="first", base=state,
    )
    second = install_profile(
        repo, "semantic", embedding_profile("http://127.0.0.1:9999"),
        secret="second", base=state,
    )
    assert first["semantic_identity"] != second["semantic_identity"]

    readiness = search_readiness(
        lexical_index_ready=True,
        profile=second,
        semantic_index_identity=first["semantic_identity"],
        probe_identity=second["semantic_identity"],
    )
    assert readiness["lexical"]["ready"] is True
    assert readiness["hybrid"] == {
        "ready": False,
        "reason": "semantic_index_stale",
        "embedding_identity": second["semantic_identity"],
    }
    assert require_search_modality("lexical", readiness) == "lexical"
    with pytest.raises(RuntimeError, match="hybrid_unavailable"):
        require_search_modality("hybrid", readiness)


def test_analyzer_environment_removes_inherited_embedding_proxy_and_global_config() -> None:
    inherited = {
        "PATH": "/bin",
        "EMBEDDING_URL": "https://unapproved",
        "HTTPS_PROXY": "https://proxy",
        "BSL_ANALYZER_CONFIG": "/host/config",
        "OPENAI_API_KEY": "unrelated-secret",
        "SSL_CERT_FILE": "/host/ca.pem",
    }
    assert analyzer_environment(inherited, modality="lexical") == {"PATH": "/bin"}
    profile = embedding_profile("http://127.0.0.1:9999")
    environment = analyzer_environment(
        inherited,
        modality="hybrid",
        profile=profile,
        broker_environment={
            "EMBEDDING_URL": "http://127.0.0.1:1234/v1/embeddings",
            "EMBEDDING_API_KEY": "ephemeral-capability",
        },
    )
    assert environment == {
        "PATH": "/bin",
        "EMBEDDING_PROVIDER": "openai-compatible",
        "EMBEDDING_URL": "http://127.0.0.1:1234/v1/embeddings",
        "EMBEDDING_API_KEY": "ephemeral-capability",
        "EMBEDDING_MODEL": "test-model",
        "EMBEDDING_DIM": "2",
        "EMBEDDING_CONCURRENCY": "2",
        "EMBEDDING_BATCH_SIZE": "10",
    }


def test_embedding_broker_forwards_only_bounded_normalized_request(tmp_path: Path) -> None:
    captured: dict = {}

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers["Content-Length"])
            captured["path"] = self.path
            captured["authorization"] = self.headers["Authorization"]
            captured["body"] = json.loads(self.rfile.read(size))
            body = json.dumps({
                "data": [
                    {"index": 0, "embedding": [0.25, 0.75], "secret_extra": "discard"},
                ],
                "usage": {"prompt_tokens": 999},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    install_profile(
        repo,
        "semantic",
        embedding_profile(f"http://127.0.0.1:{upstream.server_address[1]}/v1"),
        secret="upstream-key",
        base=state,
    )
    broker = EmbeddingBroker(repo, "semantic", base=state)
    environment = broker.start()
    try:
        request = Request(
            environment["EMBEDDING_URL"],
            data=json.dumps({"input": ["source"], "model": "test-model"}).encode(),
            headers={
                "Authorization": f"Bearer {environment['EMBEDDING_API_KEY']}",
                "Content-Type": "application/json",
            },
        )
        result = json.loads(urlopen(request, timeout=2).read())
        assert result == {
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.25, 0.75]}],
            "model": "test-model",
        }
        assert captured == {
            "path": "/v1/embeddings",
            "authorization": "Bearer upstream-key",
            "body": {"input": ["source"], "model": "test-model", "encoding_format": "float"},
        }
        with pytest.raises(HTTPError) as error:
            urlopen(Request(
                environment["EMBEDDING_URL"],
                data=b"{}",
                headers={"Authorization": "Bearer wrong"},
            ), timeout=2)
        assert error.value.code == 401
    finally:
        broker.close()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)


def test_embedding_build_retries_only_inside_budget_and_replay_is_forbidden(tmp_path: Path) -> None:
    attempts = 0

    class RetryUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal attempts
            self.rfile.read(int(self.headers["Content-Length"]))
            attempts += 1
            if attempts < 3:
                self.send_response(503)
                self.send_header("Retry-After", "0")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps({"data": [{"embedding": [0.1, 0.2]}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), RetryUpstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    install_profile(
        repo,
        "semantic",
        embedding_profile(f"http://127.0.0.1:{upstream.server_address[1]}"),
        base=state,
    )
    broker = EmbeddingBroker(repo, "semantic", base=state)
    budget = broker.new_budget("build")
    request = json.dumps({"input": ["source"], "model": "test-model"}).encode()
    try:
        result = json.loads(broker.forward(request, operation="build", budget=budget))
        assert result["data"][0]["embedding"] == [0.1, 0.2]
        assert attempts == 3
        assert budget.diagnostics()["requests"] == 3
        with pytest.raises(RuntimeError, match="replay_forbidden"):
            broker.forward(request, operation="build", replay=True)
    finally:
        broker.close()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("status, error", [
    (400, "upstream_http_400"),
    (503, "embedding_budget_exhausted"),
])
def test_embedding_query_never_retries_outside_one_request_budget(
    tmp_path: Path, status: int, error: str,
) -> None:
    attempts = 0

    class FailingUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal attempts
            self.rfile.read(int(self.headers["Content-Length"]))
            attempts += 1
            self.send_response(status)
            self.send_header("Retry-After", "0")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FailingUpstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    install_profile(
        repo,
        "semantic",
        embedding_profile(f"http://127.0.0.1:{upstream.server_address[1]}"),
        base=state,
    )
    broker = EmbeddingBroker(repo, "semantic", base=state)
    try:
        with pytest.raises(RuntimeError, match=error):
            broker.forward(json.dumps({"input": ["source"], "model": "test-model"}).encode())
        assert attempts == 1
    finally:
        broker.close()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)


def test_embedding_broker_cancellation_closes_admission(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    install_profile(
        repo,
        "semantic",
        embedding_profile("http://127.0.0.1:9999/v1"),
        base=state,
    )
    broker = EmbeddingBroker(repo, "semantic", base=state)
    broker.cancel()

    with pytest.raises(RuntimeError, match="cancelled"):
        broker.forward(json.dumps({"input": ["source"], "model": "test-model"}).encode())


def test_embedding_broker_cancellation_closes_active_connection(tmp_path: Path) -> None:
    accepted = threading.Event()

    class SlowUpstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            accepted.set()
            time.sleep(2)

        def log_message(self, format: str, *args: object) -> None:
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), SlowUpstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    install_profile(
        repo,
        "semantic",
        embedding_profile(f"http://127.0.0.1:{upstream.server_address[1]}"),
        base=state,
    )
    broker = EmbeddingBroker(repo, "semantic", base=state)
    errors: list[Exception] = []
    worker = threading.Thread(
        target=lambda: _capture_error(
            errors,
            lambda: broker.forward(json.dumps({
                "input": ["source"], "model": "test-model",
            }).encode()),
        ),
    )
    worker.start()
    assert accepted.wait(1)
    broker.cancel()
    worker.join(timeout=1)
    upstream.shutdown()
    upstream.server_close()
    upstream_thread.join(timeout=2)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert str(errors[0]) == "search_services.cancelled"


def _capture_error(errors: list[Exception], action: Callable[[], object]) -> None:
    try:
        action()
    except Exception as exc:
        errors.append(exc)


@pytest.mark.parametrize("endpoint", [
    "http://example.com/v1",
    "https://user:password@example.com/v1",
    "https://example.com/v1?token=secret",
    "file:///tmp/service",
])
def test_embedding_profile_rejects_unsafe_endpoint(tmp_path: Path, endpoint: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="endpoint_forbidden|endpoint_address_forbidden"):
        install_profile(repo, "semantic", embedding_profile(endpoint), base=tmp_path / "state")
