"""Операционное SQLite-хранилище диспетчера конвейера.

Хранилище содержит только идентификаторы, пути, отпечатки, ограниченные
предложения, состояния узлов, аренды и монотонную ревизию проекции. Оно
полностью удаляемо: отсутствие или удаление файла даёт пустую операционную
проекцию и не меняет канонические ворота, отпечатки и проверки ``doctor``.

Буквальный вызов ``sqlite3.connect`` намеренно отсутствует в исходниках
пакета: соединение открывает библиотечный контекстный менеджер
``SqliteSaver.from_conn_string``, а транзитивный модуль ``sqlite3`` импортируется
исключительно для типизации параметров PRAGMA.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .contracts import canonical_json, repository_lock, sha256
from .user_state import state_root, workspace_id


LEASE_RENEWAL_SECONDS = 10
LEASE_EXPIRY_SECONDS = 30
BUSY_TIMEOUT_MS = 5000
REVISION_SCHEMA_VERSION = "1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dispatcher_db_path(repo: Path, base: Path | None = None) -> Path:
    """Возвращает путь к операционной SQLite-базе проекта.

    База живёт вне Git в существующем пользовательском каталоге состояния и
    наследует права ``0o700`` родительского каталога.
    """

    root = state_root(base) / "projects" / workspace_id(repo)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "dispatcher.sqlite"
    return path


def _import_saver():
    """Импортирует ``SqliteSaver`` лениво, чтобы пакет оставался импортируемым
    без установленного extra ``workspace``.
    """

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise RuntimeError("Install one-c-autoresearch[workspace] to use the dispatcher") from exc
    return SqliteSaver


def _apply_pragmas(saver) -> None:
    """Применяет короткие безопасные PRAGMA к соединению библиотечного saver-а.

    ``journal_mode=WAL`` библиотека уже включает в ``setup()``; здесь добавляем
    только ``busy_timeout`` (по ``design.md`` §3) и подтверждаем ``synchrousnous=NORMAL``.
    """

    conn = getattr(saver, "conn", None)
    if conn is None:
        return
    cur = conn.cursor()
    try:
        cur.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_MS)};")
        cur.execute("PRAGMA synchronous = NORMAL;")
        conn.commit()
    finally:
        cur.close()


def _ensure_dispatcher_tables(conn) -> None:
    cur = conn.cursor()
    try:
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS dispatcher_revision (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                value INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO dispatcher_revision (id, value, updated_at) VALUES (1, 0, '');

            CREATE TABLE IF NOT EXISTS dispatcher_leases (
                job_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                work_unit_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                process_identity TEXT,
                acquired_at TEXT NOT NULL,
                renewed_at TEXT NOT NULL,
                state TEXT NOT NULL,
                summary TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stage_recompute_runs (
                run_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                idempotency_key_fingerprint TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                lease_token TEXT NOT NULL,
                boundary TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                predecessor_run_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stage_recompute_cancellations (
                idempotency_key TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dispatcher_proposals (
                key TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                consumed_at TEXT
            );
            """
        )
        columns = {row[1] for row in cur.execute("PRAGMA table_info(dispatcher_leases)")}
        if "lease_token" not in columns:
            cur.execute("ALTER TABLE dispatcher_leases ADD COLUMN lease_token TEXT")
        conn.commit()
    finally:
        cur.close()


def _acquire_saver(repo: Path, base: Path | None = None):
    """Открывает saver и подготавливает таблицы диспетчера.

    Возвращает кортеж ``(saver, conn)``; вызывающий ответственен за закрытие
    saver-а через его протокол контекстного менеджера.
    """

    SqliteSaver = _import_saver()
    path = dispatcher_db_path(repo, base)
    saver_cm = SqliteSaver.from_conn_string(str(path))
    saver = saver_cm.__enter__()
    try:
        _apply_pragmas(saver)
        _ensure_dispatcher_tables(saver.conn)
    except Exception:
        saver_cm.__exit__(None, None, None)
        raise
    return saver, saver_cm


def _touch(path: Path) -> None:
    """Гарантирует права ``0o600`` на файл базы, если он был создан."""

    try:
        mode = path.stat().st_mode & 0o777
        if mode != 0o600:
            path.chmod(0o600)
    except OSError:
        return


class DispatcherStore:
    """Тонкая обёртка над операционной SQLite-базой диспетчера.

    Отвечает только за операционные артефакты: монотонную ревизию проекции,
    аренды и пакеты предложений. Чекпойнты LangGraph пишет сама ``SqliteSaver``
    через таблицы ``checkpoints``/``writes``; эта обёртка их не трогает.
    """

    def __init__(self, repo: Path, base: Path | None = None) -> None:
        self.repo = repo.resolve()
        self.base = base
        self.path = dispatcher_db_path(self.repo, base)
        self._lock = threading.RLock()
        self._saver = None
        self._saver_cm = None

    # -- жизненный цикл соединения -------------------------------------

    def __enter__(self) -> "DispatcherStore":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        with self._lock:
            if self._saver is not None:
                return
            self._saver, self._saver_cm = _acquire_saver(self.repo, self.base)
            _touch(self.path)

    def close(self) -> None:
        with self._lock:
            if self._saver_cm is None:
                return
            try:
                self._saver_cm.__exit__(None, None, None)
            finally:
                self._saver = None
                self._saver_cm = None

    @property
    def saver(self):
        if self._saver is None:
            self.open()
        return self._saver

    @property
    def conn(self):
        return self.saver.conn

    # -- ревизия проекции ----------------------------------------------

    def bump_revision(self) -> int:
        """Атомарно увеличивает монотонную ревизию и возвращает новое значение."""

        with self._lock, self.conn:  # короткая транзакция
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "UPDATE dispatcher_revision SET value = value + 1, updated_at = ? WHERE id = 1 RETURNING value",
                    (_now_iso(),),
                )
                row = cur.fetchone()
            finally:
                cur.close()
        if row is None:
            raise RuntimeError("dispatcher revision row is missing")
        return int(row[0])

    def revision(self) -> tuple[int, str]:
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT value, updated_at FROM dispatcher_revision WHERE id = 1")
                row = cur.fetchone()
            finally:
                cur.close()
        if row is None:
            return 0, ""
        return int(row[0]), str(row[1])

    # -- аренды --------------------------------------------------------

    def acquire_lease(self, job_id: str, thread_id: str, work_unit_id: str, owner: str, process_identity: dict | None, state: str = "running", summary: dict | None = None) -> bool:
        """Захватывает аренду, если она свободна или истекла. Возвращает успех."""

        renewed_at = _now_iso()
        acquired_at = renewed_at
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                if job_id != "stage-recompute":
                    cur.execute("SELECT 1 FROM dispatcher_leases WHERE job_id = 'stage-recompute'")
                    if cur.fetchone() is not None:
                        return False
                cur.execute("SELECT renewed_at, owner FROM dispatcher_leases WHERE job_id = ?", (job_id,))
                existing = cur.fetchone()
                now = time.time()
                if existing is not None:
                    renewed_iso = str(existing[0])
                    try:
                        renewed_epoch = datetime.fromisoformat(renewed_iso).timestamp()
                    except ValueError:
                        renewed_epoch = now
                    expired = (now - renewed_epoch) >= LEASE_EXPIRY_SECONDS
                    if not expired:
                        return False
                    cur.execute("DELETE FROM dispatcher_leases WHERE job_id = ?", (job_id,))
                cur.execute(
                    "INSERT INTO dispatcher_leases (job_id, thread_id, work_unit_id, owner, process_identity, acquired_at, renewed_at, state, summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, thread_id, work_unit_id, owner, canonical_json(process_identity).decode("utf-8") if process_identity else None, acquired_at, renewed_at, state, canonical_json(summary or {}).decode("utf-8")),
                )
            finally:
                cur.close()
        return True

    def renew_lease(self, job_id: str, state: str | None = None, summary: dict | None = None) -> bool:
        """Обновляет аренду, если она всё ещё принадлежит активному владельцу."""

        renewed_at = _now_iso()
        with self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT renewed_at FROM dispatcher_leases WHERE job_id = ?", (job_id,))
                existing = cur.fetchone()
                if existing is None:
                    return False
                now = time.time()
                try:
                    renewed_epoch = datetime.fromisoformat(str(existing[0])).timestamp()
                except ValueError:
                    renewed_epoch = now
                if (now - renewed_epoch) >= LEASE_EXPIRY_SECONDS:
                    return False
                if state is None and summary is None:
                    cur.execute("UPDATE dispatcher_leases SET renewed_at = ? WHERE job_id = ?", (renewed_at, job_id))
                else:
                    assignments = ["renewed_at = ?"]
                    params: list[Any] = [renewed_at]
                    if state is not None:
                        assignments.append("state = ?")
                        params.append(state)
                    if summary is not None:
                        assignments.append("summary = ?")
                        params.append(canonical_json(summary).decode("utf-8"))
                    params.append(job_id)
                    cur.execute(f"UPDATE dispatcher_leases SET {', '.join(assignments)} WHERE job_id = ?", tuple(params))
            finally:
                cur.close()
        return True

    def release_lease(self, job_id: str) -> bool:
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute("DELETE FROM dispatcher_leases WHERE job_id = ?", (job_id,))
            finally:
                cur.close()
        return True

    def lease(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT job_id, thread_id, work_unit_id, owner, process_identity, acquired_at, renewed_at, state, summary FROM dispatcher_leases WHERE job_id = ?", (job_id,))
                row = cur.fetchone()
            finally:
                cur.close()
        if row is None:
            return None
        job, thread, unit, owner, identity, acquired, renewed, state, summary = row
        return {"job_id": job, "thread_id": thread, "work_unit_id": unit, "owner": owner, "process_identity": json.loads(identity) if identity else None, "acquired_at": acquired, "renewed_at": renewed, "state": state, "summary": json.loads(summary) if summary else {}}

    def leases(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT job_id, thread_id, work_unit_id, owner, process_identity, acquired_at, renewed_at, state, summary FROM dispatcher_leases ORDER BY job_id")
                rows = cur.fetchall()
            finally:
                cur.close()
        return [{"job_id": r[0], "thread_id": r[1], "work_unit_id": r[2], "owner": r[3], "process_identity": json.loads(r[4]) if r[4] else None, "acquired_at": r[5], "renewed_at": r[6], "state": r[7], "summary": json.loads(r[8]) if r[8] else {}} for r in rows]

    # -- каскадный пересчёт -------------------------------------------

    def start_stage_recompute(
        self,
        idempotency_key: str,
        request_fingerprint: str,
        boundary: str,
        plan: dict[str, Any],
        predecessor_run_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Атомарно сохраняет запрос и захватывает взаимно исключающую аренду."""

        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT run_id, request_fingerprint FROM stage_recompute_runs WHERE idempotency_key = ?", (idempotency_key,))
                existing = cur.fetchone()
                if existing is not None:
                    if existing[1] != request_fingerprint:
                        raise RuntimeError("idempotency key is already bound to another stage recompute request")
                    return self._stage_run_locked(cur, str(existing[0])), False
                if predecessor_run_id is not None:
                    predecessor = self._stage_run_locked(cur, predecessor_run_id)
                    if (
                        predecessor is None
                        or predecessor["boundary"] != boundary
                        or predecessor["status"] not in {"failed", "cancelled", "resumable"}
                    ):
                        raise RuntimeError("incompatible stage recompute predecessor")
                cur.execute("SELECT job_id, thread_id, renewed_at FROM dispatcher_leases")
                leases = cur.fetchall()
                mrq = [row for row in leases if row[0] != "stage-recompute"]
                if mrq:
                    raise RuntimeError(f"dispatcher lease is busy: {mrq[0][0]}")
                active_stage = next((row for row in leases if row[0] == "stage-recompute"), None)
                if active_stage is not None and is_stale(str(active_stage[2])):
                    cur.execute(
                        "UPDATE stage_recompute_runs SET status = 'resumable', result = ?, updated_at = ? WHERE run_id = ? AND status = 'running'",
                        (canonical_json({"status": "resumable", "reason": "lease_expired"}).decode(), _now_iso(), active_stage[1]),
                    )
                    cur.execute("DELETE FROM dispatcher_leases WHERE job_id = 'stage-recompute'")
                    leases = []
                busy = [str(row[0]) for row in leases]
                if busy:
                    raise RuntimeError(f"dispatcher lease is busy: {busy[0]}")
                run_id = str(uuid.uuid4())
                token = str(uuid.uuid4())
                now = _now_iso()
                key_fingerprint = "sha256:" + sha256(idempotency_key.encode())
                summary = {"run_id": run_id, "boundary": boundary, "plan_fingerprint": plan["plan_fingerprint"]}
                cur.execute(
                    "INSERT INTO dispatcher_leases (job_id, thread_id, work_unit_id, owner, process_identity, acquired_at, renewed_at, state, summary, lease_token) VALUES (?, ?, ?, ?, NULL, ?, ?, 'running', ?, ?)",
                    ("stage-recompute", run_id, boundary, run_id, now, now, canonical_json(summary).decode(), token),
                )
                cur.execute(
                    "INSERT INTO stage_recompute_runs (run_id, idempotency_key, idempotency_key_fingerprint, request_fingerprint, lease_token, boundary, plan, status, result, predecessor_run_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', NULL, ?, ?, ?)",
                    (run_id, idempotency_key, key_fingerprint, request_fingerprint, token, boundary, canonical_json(plan).decode(), predecessor_run_id, now, now),
                )
                return self._stage_run_locked(cur, run_id), True
            finally:
                cur.close()

    def stage_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self.conn.cursor()
            try:
                return self._stage_run_locked(cur, run_id)
            finally:
                cur.close()

    def latest_stage_recompute(self) -> dict[str, Any] | None:
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT run_id FROM stage_recompute_runs ORDER BY created_at DESC, run_id DESC LIMIT 1")
                row = cur.fetchone()
                return self._stage_run_locked(cur, str(row[0])) if row else None
            finally:
                cur.close()

    def _stage_run_locked(self, cur, run_id: str) -> dict[str, Any] | None:
        cur.execute(
            "SELECT run_id, idempotency_key_fingerprint, request_fingerprint, lease_token, boundary, plan, status, result, predecessor_run_id, created_at, updated_at FROM stage_recompute_runs WHERE run_id = ?",
            (run_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "idempotency_key_fingerprint": row[1],
            "request_fingerprint": row[2],
            "lease_token": row[3],
            "boundary": row[4],
            "plan": json.loads(row[5]),
            "status": row[6],
            "result": json.loads(row[7]) if row[7] else None,
            "predecessor_run_id": row[8],
            "created_at": row[9],
            "updated_at": row[10],
        }

    def stage_token_current(self, run_id: str, lease_token: str) -> bool:
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT 1 FROM dispatcher_leases WHERE job_id = 'stage-recompute' AND thread_id = ? AND lease_token = ?",
                    (run_id, lease_token),
                )
                return cur.fetchone() is not None
            finally:
                cur.close()

    def renew_stage_lease(self, run_id: str, lease_token: str, summary: dict[str, Any] | None = None) -> bool:
        with self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT renewed_at FROM dispatcher_leases WHERE job_id = 'stage-recompute' AND thread_id = ? AND lease_token = ?",
                    (run_id, lease_token),
                )
                row = cur.fetchone()
                if row is None or is_stale(str(row[0])):
                    return False
                if summary is None:
                    cur.execute(
                        "UPDATE dispatcher_leases SET renewed_at = ? WHERE job_id = 'stage-recompute' AND thread_id = ? AND lease_token = ?",
                        (_now_iso(), run_id, lease_token),
                    )
                else:
                    cur.execute(
                        "UPDATE dispatcher_leases SET renewed_at = ?, summary = ? WHERE job_id = 'stage-recompute' AND thread_id = ? AND lease_token = ?",
                        (_now_iso(), canonical_json(summary).decode(), run_id, lease_token),
                    )
                return cur.rowcount == 1
            finally:
                cur.close()

    def finish_stage_recompute(self, run_id: str, lease_token: str, status: str, result: dict[str, Any]) -> bool:
        """Записывает итог и освобождает аренду только для текущего токена."""

        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT 1 FROM dispatcher_leases WHERE job_id = 'stage-recompute' AND thread_id = ? AND lease_token = ?",
                    (run_id, lease_token),
                )
                if cur.fetchone() is None:
                    return False
                now = _now_iso()
                cur.execute(
                    "UPDATE stage_recompute_runs SET status = ?, result = ?, updated_at = ? WHERE run_id = ? AND lease_token = ?",
                    (status, canonical_json(result).decode(), now, run_id, lease_token),
                )
                cur.execute(
                    "DELETE FROM dispatcher_leases WHERE job_id = 'stage-recompute' AND thread_id = ? AND lease_token = ?",
                    (run_id, lease_token),
                )
                return cur.rowcount > 0
            finally:
                cur.close()

    def cancel_stage_recompute(self, run_id: str, lease_token: str) -> bool:
        return self.finish_stage_recompute(run_id, lease_token, "cancelled", {"status": "cancelled"})

    def bind_stage_cancellation(self, idempotency_key: str, run_id: str) -> bool:
        """Связывает отдельный ключ отмены с одним запуском."""

        with self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT run_id FROM stage_recompute_cancellations WHERE idempotency_key = ?", (idempotency_key,))
                existing = cur.fetchone()
                if existing is not None:
                    if existing[0] != run_id:
                        raise RuntimeError("idempotency key is already bound to another stage recompute cancellation")
                    return False
                cur.execute(
                    "INSERT INTO stage_recompute_cancellations (idempotency_key, run_id, created_at) VALUES (?, ?, ?)",
                    (idempotency_key, run_id, _now_iso()),
                )
                return True
            finally:
                cur.close()

    # -- предложения ---------------------------------------------------

    def save_proposal(self, key: str, job_id: str, thread_id: str, kind: str, payload: dict) -> None:
        with self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "INSERT OR REPLACE INTO dispatcher_proposals (key, job_id, thread_id, kind, payload, created_at, consumed_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (key, job_id, thread_id, kind, canonical_json(payload).decode("utf-8"), _now_iso()),
                )
            finally:
                cur.close()

    def proposal(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT key, job_id, thread_id, kind, payload, created_at, consumed_at FROM dispatcher_proposals WHERE key = ?", (key,))
                row = cur.fetchone()
            finally:
                cur.close()
        if row is None:
            return None
        return {"key": row[0], "job_id": row[1], "thread_id": row[2], "kind": row[3], "payload": json.loads(row[4]), "created_at": row[5], "consumed_at": row[6]}

    def proposals(self) -> list[dict[str, Any]]:
        """Возвращает ограниченные операционные предложения для проекции."""

        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT key, job_id, thread_id, kind, payload, created_at, consumed_at FROM dispatcher_proposals ORDER BY created_at, key")
                rows = cur.fetchall()
            finally:
                cur.close()
        return [{"key": row[0], "job_id": row[1], "thread_id": row[2], "kind": row[3], "payload": json.loads(row[4]), "created_at": row[5], "consumed_at": row[6]} for row in rows]

    def consume_proposal(self, key: str) -> bool:
        with self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute("UPDATE dispatcher_proposals SET consumed_at = ? WHERE key = ? AND consumed_at IS NULL", (_now_iso(), key))
                affected = cur.rowcount
            finally:
                cur.close()
        return affected > 0

    # -- очистка -------------------------------------------------------

    def delete_thread(self, thread_id: str) -> None:
        """Удаляет чекпойнты LangGraph и операционные предложения потока."""

        with self._lock:
            try:
                self.saver.delete_thread(thread_id)
            except Exception:
                pass
        with self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute("DELETE FROM dispatcher_proposals WHERE thread_id = ?", (thread_id,))
            finally:
                cur.close()


def is_stale(renewed_at: str, *, now: float | None = None) -> bool:
    """Признак устаревшей аренды по ``renewed_at`` ISO-строке."""

    if not renewed_at:
        return True
    try:
        renewed_epoch = datetime.fromisoformat(renewed_at).timestamp()
    except ValueError:
        return True
    current = now if now is not None else time.time()
    return (current - renewed_epoch) >= LEASE_EXPIRY_SECONDS


def fresh_lease_seconds(renewed_at: str, *, now: float | None = None) -> float:
    """Сколько секунд назад была подтверждена аренда; для UI-порога 10 секунд."""

    if not renewed_at:
        return float(LEASE_EXPIRY_SECONDS)
    try:
        renewed_epoch = datetime.fromisoformat(renewed_at).timestamp()
    except ValueError:
        return float(LEASE_EXPIRY_SECONDS)
    current = now if now is not None else time.time()
    return max(0.0, current - renewed_epoch)


@contextmanager
def open_store(repo: Path, base: Path | None = None) -> Iterator[DispatcherStore]:
    store = DispatcherStore(repo, base)
    store.open()
    try:
        yield store
    finally:
        store.close()


def wipe(repo: Path, base: Path | None = None) -> bool:
    """Удаляет файл операционной базы. Возвращает True, если файл существовал.

    Канонические ворота, отпечатки, ``status`` и ``doctor`` от этого не меняются.
    """

    path = dispatcher_db_path(repo, base)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    for suffix in ("-wal", "-shm"):
        sibling = path.with_name(path.name + suffix)
        try:
            sibling.unlink()
        except FileNotFoundError:
            pass
    return True
