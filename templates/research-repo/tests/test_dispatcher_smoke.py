"""Сквозной smoke-тест диспетчера с реальным codex CLI.

Помечен ``@pytest.mark.smoke`` и по умолчанию отключен. Запускается явно:

    uv run --extra workspace --with pytest --with httpx \\
        python -m pytest tests/test_dispatcher_smoke.py -m smoke

Тест проверяет, что полный путь «DIF → agent → операционная проекция»
работает на реальном репозитории с настроенным профилем ``local``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


def _codex_available() -> bool:
    return shutil.which("codex") is not None


pytestmark = pytest.mark.skipif(not _codex_available() or not (REPO / "research/active-generation.json").exists(), reason="codex CLI или активное каноническое поколение недоступны; smoke-тест отключен")


@pytest.mark.smoke
def test_dispatcher_projection_runs_against_real_repo() -> None:
    """Проекция читается на реальном репозитории без ошибок.

    Этот тест не запускает агентные вызовы (они требуют активного DIF без
    владельца); он проверяет, что серверная проекция устойчиво собирается из
    канонического состояния и SQLite, не падая на отсутствующих файлах.
    """

    from one_c_autoresearch.workflow import attach_dispatcher, status
    snapshot = status(REPO)
    enriched = attach_dispatcher(snapshot, REPO)
    assert "dispatcher" in enriched
    projection = enriched["dispatcher"]
    assert projection["schema_version"] == "2"
    assert [circuit["id"] for circuit in projection["circuits"]] == ["prepare-diffs", "analyze-dif", "form-mrq", "classify-mrq", "decide-target"]
    # prepare-diffs должен иметь агрегаты (есть активное diff-поколение)
    prepare = next(circuit for circuit in projection["circuits"] if circuit["id"] == "prepare-diffs")
    assert prepare["aggregates"]["diff_count"] > 0


@pytest.mark.smoke
def test_dispatcher_api_round_trip_with_real_repo(tmp_path: Path) -> None:
    """API диспетчера отвечает на реальном репозитории: старт → снимок → отмена.

    Использует настроенный профиль ``local`` из пользовательского каталога.
    """

    from fastapi.testclient import TestClient
    from one_c_autoresearch.workspace_api import create_app

    app = create_app(tmp_path / "state", [REPO], testing=True)
    headers = {"Origin": "http://testserver", "Idempotency-Key": "smoke-bookmark"}
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "example", "root": str(REPO)}, headers=headers).json()
        # снимок включает секцию dispatcher
        snapshot = client.get(f"/api/v1/projects/{project['id']}/workflow").json()
        assert "dispatcher" in snapshot
        # эндпоинт проекции отвечает
        projection = client.get(f"/api/v1/projects/{project['id']}/dispatcher").json()
        assert projection["schema_version"] == "2"
        # запуск и отмена discover-mrq
        start = client.post(f"/api/v1/projects/{project['id']}/dispatcher/discover-mrq/start", json={"actor": "smoke"}, headers={**headers, "Idempotency-Key": "smoke-start"})
        assert start.status_code == 200
        assert start.json()["outcome"]["status"] in {"running", "blocked"}
        cancel = client.post(f"/api/v1/projects/{project['id']}/dispatcher/discover-mrq/cancel", json={"actor": "smoke"}, headers={**headers, "Idempotency-Key": "smoke-cancel"})
        assert cancel.status_code == 200
        assert cancel.json()["outcome"]["status"] == "cancelled"
