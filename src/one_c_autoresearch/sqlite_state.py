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
import sqlite3
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
INVOCATION_ERROR_CODES = {
    "validation_error",
    "provider_error",
    "provider_blocked",
    "provider_timeout",
    "tool_error",
    "cancelled",
    "interrupted",
    "environment_unavailable",
    "internal_error",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_context_columns(row: dict[str, Any]) -> dict[str, Any]:
    for key, empty in (
        ("context_provenance", []),
        ("context_diagnostics", {}),
    ):
        encoded = row.get(key)
        if not encoded:
            row[key] = empty
            continue
        try:
            decoded = json.loads(str(encoded))
        except json.JSONDecodeError:
            decoded = empty
        row[key] = decoded if isinstance(decoded, type(empty)) else empty
    return row


def _terminalize_rows(
    cur,
    invocation_ids: list[str],
    status: str,
    now: str,
    *,
    error_code: str = "",
    error_summary: str = "",
    result_ref: str = "",
) -> int:
    changed = 0
    for invocation_id in invocation_ids:
        row = cur.execute(
            "SELECT run_id, phase_id, role_id, slot_id, work_unit_id, created_at "
            "FROM dispatcher_invocations WHERE invocation_id = ? AND status = 'running'",
            (invocation_id,),
        ).fetchone()
        if row is None:
            continue
        cur.execute(
            "UPDATE dispatcher_invocations SET status = ?, updated_at = ?, "
            "finished_at = ?, error_code = ?, error_summary = ?, result_ref = ? "
            "WHERE invocation_id = ? AND status = 'running'",
            (
                status,
                now,
                now,
                error_code or (status if status in {"cancelled", "interrupted"} else None),
                error_summary or None,
                result_ref or None,
                invocation_id,
            ),
        )
        if cur.rowcount != 1:
            continue
        changed += 1
        cur.execute(
            "UPDATE dispatcher_phase_work SET status = ?, updated_at = ? "
            "WHERE invocation_id = ? AND status = 'running'",
            (status, now, invocation_id),
        )
        try:
            duration = max(
                0.0,
                (datetime.fromisoformat(now) - datetime.fromisoformat(row[5])).total_seconds(),
            )
        except (TypeError, ValueError):
            duration = 0.0
        payload = {
            "transition_key": f"invocation:{invocation_id}:finished",
            "invocation_id": invocation_id,
            "run_id": row[0],
            "phase_id": row[1],
            "role_id": row[2],
            "slot_id": row[3],
            "work_unit_id": row[4],
            "invocation_status": status,
            "timestamp": now,
            "duration_seconds": duration,
        }
        if error_code or status in {"cancelled", "interrupted"}:
            payload["error_code"] = error_code or status
        if error_summary:
            payload["error_summary"] = error_summary
        if result_ref:
            payload["result_ref"] = result_ref
        cur.execute(
            "INSERT OR IGNORE INTO dispatcher_invocation_outbox "
            "(transition_key, invocation_id, run_id, event_type, payload, created_at) "
            "VALUES (?, ?, ?, 'invocation.finished', ?, ?)",
            (
                payload["transition_key"],
                invocation_id,
                row[0],
                canonical_json(payload).decode(),
                now,
            ),
        )
    return changed


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
                run_id TEXT NOT NULL DEFAULT '',
                execution_snapshot_fingerprint TEXT NOT NULL DEFAULT '',
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
            CREATE TABLE IF NOT EXISTS dispatcher_invocations (
                invocation_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                phase_id TEXT NOT NULL,
                role_id TEXT NOT NULL,
                work_unit_id TEXT NOT NULL,
                slot_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dispatcher_invocation_outbox (
                transition_key TEXT PRIMARY KEY,
                invocation_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                delivered_at TEXT
            );
            CREATE TABLE IF NOT EXISTS dispatcher_phase_work (
                job_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                phase_id TEXT NOT NULL,
                role_id TEXT NOT NULL,
                work_unit_id TEXT NOT NULL,
                status TEXT NOT NULL,
                invocation_id TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, phase_id, role_id, work_unit_id)
            );
            CREATE TABLE IF NOT EXISTS dispatcher_retry_runs (
                run_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                job_id TEXT NOT NULL,
                predecessor_run_id TEXT NOT NULL,
                policy_source TEXT NOT NULL,
                execution_snapshot TEXT NOT NULL,
                execution_snapshot_fingerprint TEXT NOT NULL,
                owner_token TEXT NOT NULL,
                state TEXT NOT NULL,
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workflow_migration_runs (
                migration_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                lease_token TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                repository_fingerprint TEXT NOT NULL,
                workflow_fingerprint TEXT NOT NULL,
                legacy_lease_preimage TEXT,
                legacy_audit_ids TEXT NOT NULL,
                journal_seed TEXT NOT NULL,
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in cur.execute("PRAGMA table_info(dispatcher_leases)")}
        if "lease_token" not in columns:
            cur.execute("ALTER TABLE dispatcher_leases ADD COLUMN lease_token TEXT")
        if "run_id" not in columns:
            cur.execute("ALTER TABLE dispatcher_leases ADD COLUMN run_id TEXT NOT NULL DEFAULT ''")
        if "execution_snapshot_fingerprint" not in columns:
            cur.execute("ALTER TABLE dispatcher_leases ADD COLUMN execution_snapshot_fingerprint TEXT NOT NULL DEFAULT ''")
        retry_columns = {row[1] for row in cur.execute("PRAGMA table_info(dispatcher_retry_runs)")}
        if "owner_token" not in retry_columns:
            cur.execute("ALTER TABLE dispatcher_retry_runs ADD COLUMN owner_token TEXT NOT NULL DEFAULT ''")
        invocation_columns = {
            row[1] for row in cur.execute("PRAGMA table_info(dispatcher_invocations)")
        }
        additions = {
            "execution_snapshot_fingerprint": "TEXT",
            "profile_id": "TEXT",
            "context_manifest_fingerprint": "TEXT",
            "context_envelope_fingerprint": "TEXT",
            "prepared_input_fingerprint": "TEXT",
            "context_provenance": "TEXT",
            "context_diagnostics": "TEXT",
            "finished_at": "TEXT",
            "error_code": "TEXT",
            "error_summary": "TEXT",
            "result_ref": "TEXT",
        }
        for name, column_type in additions.items():
            if name not in invocation_columns:
                cur.execute(
                    f"ALTER TABLE dispatcher_invocations ADD COLUMN {name} {column_type}"
                )
        phase_columns = {
            row[1] for row in cur.execute("PRAGMA table_info(dispatcher_phase_work)")
        }
        for name in ("execution_kind", "source_result_ref", "context_diagnostics"):
            if name not in phase_columns:
                cur.execute(
                    f"ALTER TABLE dispatcher_phase_work ADD COLUMN {name} TEXT"
                )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS dispatcher_invocation_history "
            "ON dispatcher_invocations "
            "(run_id, phase_id, role_id, slot_id, created_at DESC, invocation_id DESC)"
        )
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
        columns = {
            row[1]
            for row in saver.conn.execute(
                "PRAGMA table_info(dispatcher_invocations)"
            )
        }
        if columns and "execution_snapshot_fingerprint" not in columns:
            backup = path.with_suffix(".pre-inspector.sqlite")
            if not backup.exists():
                destination = sqlite3.connect(backup)
                try:
                    saver.conn.backup(destination)
                finally:
                    destination.close()
                backup.chmod(0o600)
        if columns and "context_envelope_fingerprint" not in columns:
            backup = path.with_suffix(".pre-context-envelope.sqlite")
            if not backup.exists():
                destination = sqlite3.connect(backup)
                try:
                    saver.conn.backup(destination)
                finally:
                    destination.close()
                backup.chmod(0o600)
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

    def owns_lease(self, job_id: str, lease_token: str, thread_id: str | None = None) -> bool:
        with self._lock:
            cur = self.conn.cursor()
            try:
                sql = "SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND lease_token = ?"
                parameters: tuple[Any, ...] = (job_id, lease_token)
                if thread_id is not None:
                    sql += " AND thread_id = ?"
                    parameters += (thread_id,)
                cur.execute(sql, parameters)
                return cur.fetchone() is not None
            finally:
                cur.close()

    @contextmanager
    def lease_guard(self, job_id: str, lease_token: str, thread_id: str | None = None) -> Iterator[None]:
        """Исключает перехват аренды на время внешней записи или эффекта."""

        with repository_lock(self.repo), self._lock:
            if not self.owns_lease(job_id, lease_token, thread_id):
                raise RuntimeError("dispatcher lease was fenced")
            yield

    def fenced_saver(self, job_id: str, lease_token: str, thread_id: str):
        """Ограждает записи LangGraph тем же токеном, что и результаты."""

        from langgraph.checkpoint.base import BaseCheckpointSaver

        store = self
        saver = self.saver

        class FencedSaver(BaseCheckpointSaver):
            def __init__(self):
                super().__init__(serde=saver.serde)

            @property
            def config_specs(self):
                return saver.config_specs

            def get_tuple(self, *args, **kwargs):
                return saver.get_tuple(*args, **kwargs)

            def list(self, *args, **kwargs):
                return saver.list(*args, **kwargs)

            def get_next_version(self, *args, **kwargs):
                return saver.get_next_version(*args, **kwargs)

            def put(self, *args, **kwargs):
                with store.lease_guard(job_id, lease_token, thread_id):
                    return saver.put(*args, **kwargs)

            def put_writes(self, *args, **kwargs):
                with store.lease_guard(job_id, lease_token, thread_id):
                    return saver.put_writes(*args, **kwargs)

            def delete_thread(self, *args, **kwargs):
                with store.lease_guard(job_id, lease_token, thread_id):
                    return saver.delete_thread(*args, **kwargs)

        return FencedSaver()

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

    def bump_revision_if_lease(self, job_id: str, lease_token: str) -> int | None:
        """Увеличивает ревизию только для текущего владельца аренды."""

        with self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "UPDATE dispatcher_revision SET value = value + 1, updated_at = ? "
                    "WHERE id = 1 AND EXISTS "
                    "(SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND lease_token = ?) "
                    "RETURNING value",
                    (_now_iso(), job_id, lease_token),
                )
                row = cur.fetchone()
            finally:
                cur.close()
        return int(row[0]) if row is not None else None

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

    def acquire_lease(
        self,
        job_id: str,
        thread_id: str,
        work_unit_id: str,
        owner: str,
        process_identity: dict | None,
        state: str = "running",
        summary: dict | None = None,
        *,
        run_id: str = "",
        execution_snapshot_fingerprint: str = "",
    ) -> str | None:
        """Захватывает аренду и возвращает новый непрозрачный ограждающий токен."""

        renewed_at = _now_iso()
        acquired_at = renewed_at
        lease_token = str(uuid.uuid4())
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                if job_id == "workflow-migration":
                    return None
                if job_id != "workflow-migration" and cur.execute(
                    "SELECT 1 FROM dispatcher_leases WHERE job_id = 'workflow-migration'"
                ).fetchone():
                    return None
                if job_id != "stage-recompute":
                    cur.execute("SELECT 1 FROM dispatcher_leases WHERE job_id = 'stage-recompute'")
                    if cur.fetchone() is not None:
                        return None
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
                        return None
                    invocation_ids = [
                        str(row[0])
                        for row in cur.execute(
                            "SELECT invocation_id FROM dispatcher_invocations "
                            "WHERE job_id = ? AND status = 'running'",
                            (job_id,),
                        )
                    ]
                    _terminalize_rows(
                        cur, invocation_ids, "interrupted", renewed_at
                    )
                    cur.execute("DELETE FROM dispatcher_leases WHERE job_id = ?", (job_id,))
                cur.execute(
                    "INSERT INTO dispatcher_leases "
                    "(job_id, thread_id, work_unit_id, run_id, execution_snapshot_fingerprint, owner, process_identity, acquired_at, renewed_at, state, summary, lease_token) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        thread_id,
                        work_unit_id,
                        run_id,
                        execution_snapshot_fingerprint,
                        owner,
                        canonical_json(process_identity).decode("utf-8") if process_identity else None,
                        acquired_at,
                        renewed_at,
                        state,
                        canonical_json(summary or {}).decode("utf-8"),
                        lease_token,
                    ),
                )
            finally:
                cur.close()
        return lease_token

    def acquire_workflow_migration(
        self,
        migration_id: str,
        *,
        owner: str,
        process_identity: dict | None,
        repository_fingerprint: str,
        workflow_fingerprint: str,
        journal_seed: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Атомарно заменяет допустимую старую аренду глобальным барьером миграции."""

        now = _now_iso()
        token = str(uuid.uuid4())
        thread_id = f"workflow-migration:{migration_id}"
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                prior = cur.execute(
                    "SELECT migration_id, status, lease_token, thread_id, result "
                    "FROM workflow_migration_runs WHERE migration_id = ?",
                    (migration_id,),
                ).fetchone()
                if prior is not None:
                    return {
                        "migration_id": prior[0],
                        "status": prior[1],
                        "lease_token": prior[2],
                        "thread_id": prior[3],
                        "result": json.loads(prior[4]) if prior[4] else None,
                    }
                leases = cur.execute(
                    "SELECT job_id, thread_id, work_unit_id, run_id, "
                    "execution_snapshot_fingerprint, owner, process_identity, acquired_at, "
                    "renewed_at, state, summary, lease_token FROM dispatcher_leases ORDER BY job_id"
                ).fetchall()
                if any(row[0] != "discover-mrq" for row in leases) or len(leases) > 1:
                    return None
                legacy = None
                audit_ids: dict[str, list[str]] = {
                    "invocation_ids": [], "phase_work_ids": [], "approval_keys": [],
                }
                if leases:
                    row = leases[0]
                    if row[9] not in {"running", "resumable"}:
                        return None
                    keys = (
                        "job_id", "thread_id", "work_unit_id", "run_id",
                        "execution_snapshot_fingerprint", "owner", "process_identity",
                        "acquired_at", "renewed_at", "state", "summary", "lease_token",
                    )
                    legacy = dict(zip(keys, row, strict=True))
                    legacy["process_identity"] = json.loads(legacy["process_identity"]) if legacy["process_identity"] else None
                    legacy["summary"] = json.loads(legacy["summary"]) if legacy["summary"] else {}
                    run_id = str(legacy["run_id"])
                    audit_ids["invocation_ids"] = [
                        str(item[0]) for item in cur.execute(
                            "SELECT invocation_id FROM dispatcher_invocations "
                            "WHERE job_id = 'discover-mrq' AND run_id = ?",
                            (run_id,),
                        )
                    ]
                    audit_ids["phase_work_ids"] = [
                        "|".join(map(str, item)) for item in cur.execute(
                            "SELECT run_id, phase_id, role_id, work_unit_id "
                            "FROM dispatcher_phase_work WHERE job_id = 'discover-mrq' AND run_id = ?",
                            (run_id,),
                        )
                    ]
                    audit_ids["approval_keys"] = [
                        str(item[0]) for item in cur.execute(
                            "SELECT key FROM dispatcher_proposals "
                            "WHERE job_id = 'discover-mrq' AND thread_id = ? "
                            "AND kind = 'approval' AND consumed_at IS NULL",
                            (str(legacy["thread_id"]),),
                        )
                    ]
                    cur.execute(
                        "UPDATE dispatcher_proposals SET consumed_at = ? "
                        "WHERE job_id = 'discover-mrq' AND thread_id = ? "
                        "AND kind = 'approval' AND consumed_at IS NULL",
                        (now, str(legacy["thread_id"])),
                    )
                    _terminalize_rows(
                        cur, audit_ids["invocation_ids"], "interrupted", now,
                        error_code="interrupted",
                        error_summary="legacy workflow interrupted by version-4 migration",
                    )
                    cur.execute(
                        "UPDATE dispatcher_phase_work SET status = 'interrupted', updated_at = ? "
                        "WHERE job_id = 'discover-mrq' AND run_id = ? "
                        "AND status IN ('queued', 'running')",
                        (now, run_id),
                    )
                    cur.execute("DELETE FROM dispatcher_leases WHERE job_id = 'discover-mrq'")
                cur.execute(
                    "INSERT INTO dispatcher_leases "
                    "(job_id, thread_id, work_unit_id, run_id, execution_snapshot_fingerprint, "
                    "owner, process_identity, acquired_at, renewed_at, state, summary, lease_token) "
                    "VALUES ('workflow-migration', ?, ?, ?, '', ?, ?, ?, ?, 'running', ?, ?)",
                    (
                        thread_id, migration_id, migration_id, owner,
                        canonical_json(process_identity).decode() if process_identity else None,
                        now, now,
                        canonical_json({"phase": "handoff_prepared"}).decode(), token,
                    ),
                )
                cur.execute(
                    "INSERT INTO workflow_migration_runs "
                    "(migration_id, status, lease_token, thread_id, repository_fingerprint, "
                    "workflow_fingerprint, legacy_lease_preimage, legacy_audit_ids, journal_seed, "
                    "result, created_at, updated_at) "
                    "VALUES (?, 'handoff_prepared', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                    (
                        migration_id, token, thread_id, repository_fingerprint,
                        workflow_fingerprint,
                        canonical_json(legacy).decode() if legacy else None,
                        canonical_json(audit_ids).decode(),
                        canonical_json(journal_seed).decode(), now, now,
                    ),
                )
            finally:
                cur.close()
        return {
            "migration_id": migration_id,
            "status": "handoff_prepared",
            "lease_token": token,
            "thread_id": thread_id,
            "result": None,
        }

    def workflow_migration(self, migration_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            sql = (
                "SELECT migration_id, status, lease_token, thread_id, repository_fingerprint, "
                "workflow_fingerprint, legacy_lease_preimage, legacy_audit_ids, journal_seed, "
                "result, created_at, updated_at FROM workflow_migration_runs"
            )
            parameters: tuple[Any, ...] = ()
            if migration_id is not None:
                sql += " WHERE migration_id = ?"
                parameters = (migration_id,)
            sql += " ORDER BY created_at DESC LIMIT 1"
            row = self.conn.execute(sql, parameters).fetchone()
        if row is None:
            return None
        keys = (
            "migration_id", "status", "lease_token", "thread_id",
            "repository_fingerprint", "workflow_fingerprint", "legacy_lease_preimage",
            "legacy_audit_ids", "journal_seed", "result", "created_at", "updated_at",
        )
        result = dict(zip(keys, row, strict=True))
        for key in ("legacy_lease_preimage", "legacy_audit_ids", "journal_seed", "result"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    def update_workflow_migration(
        self,
        migration_id: str,
        lease_token: str,
        status: str,
        result: dict[str, Any] | None = None,
        *,
        release: bool = False,
    ) -> bool:
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                changed = cur.execute(
                    "UPDATE workflow_migration_runs SET status = ?, result = ?, updated_at = ? "
                    "WHERE migration_id = ? AND lease_token = ? AND EXISTS "
                    "(SELECT 1 FROM dispatcher_leases WHERE job_id = 'workflow-migration' "
                    "AND lease_token = ?)",
                    (
                        status,
                        canonical_json(result).decode() if result is not None else None,
                        _now_iso(), migration_id, lease_token, lease_token,
                    ),
                ).rowcount
                if changed and release:
                    cur.execute(
                        "DELETE FROM dispatcher_leases "
                        "WHERE job_id = 'workflow-migration' AND lease_token = ?",
                        (lease_token,),
                    )
                return changed == 1
            finally:
                cur.close()

    def rollback_workflow_migration(
        self,
        migration_id: str,
        lease_token: str,
        backup_path: Path,
        result: dict[str, Any],
    ) -> bool:
        """Восстанавливает неарендные таблицы и оставляет старую работу прерванной."""

        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            backup = sqlite3.connect(backup_path)
            try:
                if not cur.execute(
                    "SELECT 1 FROM dispatcher_leases "
                    "WHERE job_id = 'workflow-migration' AND lease_token = ?",
                    (lease_token,),
                ).fetchone():
                    return False
                tables = [
                    str(row[0])
                    for row in backup.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                        "AND name NOT IN ('dispatcher_leases', 'workflow_migration_runs')"
                    )
                    if cur.execute(
                        "SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = ?",
                        (str(row[0]),),
                    ).fetchone()
                ]
                for table in tables:
                    quoted = '"' + table.replace('"', '""') + '"'
                    rows = backup.execute(f"SELECT * FROM {quoted}").fetchall()
                    cur.execute(f"DELETE FROM main.{quoted}")
                    if rows:
                        placeholders = ",".join("?" for _ in rows[0])
                        cur.executemany(
                            f"INSERT INTO main.{quoted} VALUES ({placeholders})", rows
                        )
                invocation_ids = [
                    str(row[0])
                    for row in cur.execute(
                        "SELECT invocation_id FROM dispatcher_invocations "
                        "WHERE job_id = 'discover-mrq' AND status = 'running'"
                    )
                ]
                _terminalize_rows(
                    cur, invocation_ids, "interrupted", _now_iso(),
                    error_code="interrupted",
                    error_summary="legacy workflow remains interrupted after migration rollback",
                )
                cur.execute(
                    "UPDATE dispatcher_phase_work SET status = 'interrupted', updated_at = ? "
                    "WHERE job_id = 'discover-mrq' AND status IN ('queued', 'running')",
                    (_now_iso(),),
                )
                cur.execute(
                    "UPDATE dispatcher_proposals SET consumed_at = ? "
                    "WHERE job_id = 'discover-mrq' AND kind = 'approval' "
                    "AND consumed_at IS NULL",
                    (_now_iso(),),
                )
                cur.execute(
                    "UPDATE workflow_migration_runs SET status = 'rolled_back', result = ?, "
                    "updated_at = ? WHERE migration_id = ? AND lease_token = ?",
                    (
                        canonical_json(result).decode(), _now_iso(),
                        migration_id, lease_token,
                    ),
                )
                cur.execute(
                    "DELETE FROM dispatcher_leases "
                    "WHERE job_id = 'workflow-migration' AND lease_token = ?",
                    (lease_token,),
                )
                return True
            finally:
                backup.close()
                cur.close()

    def renew_lease(self, job_id: str, lease_token: str, state: str | None = None, summary: dict | None = None) -> bool:
        """Обновляет аренду, если она всё ещё принадлежит активному владельцу."""

        renewed_at = _now_iso()
        with self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT renewed_at, summary FROM dispatcher_leases WHERE job_id = ? AND lease_token = ?", (job_id, lease_token))
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
                    cur.execute("UPDATE dispatcher_leases SET renewed_at = ? WHERE job_id = ? AND lease_token = ?", (renewed_at, job_id, lease_token))
                else:
                    assignments = ["renewed_at = ?"]
                    params: list[Any] = [renewed_at]
                    if state is not None:
                        assignments.append("state = ?")
                        params.append(state)
                    if summary is not None:
                        assignments.append("summary = ?")
                        prior_summary = json.loads(existing[1]) if existing[1] else {}
                        params.append(canonical_json({**prior_summary, **summary}).decode("utf-8"))
                    params.extend((job_id, lease_token))
                    cur.execute(f"UPDATE dispatcher_leases SET {', '.join(assignments)} WHERE job_id = ? AND lease_token = ?", tuple(params))
                if cur.rowcount != 1:
                    return False
            finally:
                cur.close()
        return True

    def resume_lease(
        self,
        job_id: str,
        thread_id: str,
        prior_token: str,
        owner: str,
        process_identity: dict | None,
    ) -> str | None:
        """Однократно передаёт возобновляемую аренду новому владельцу."""

        token = str(uuid.uuid4())
        now = _now_iso()
        try:
            with repository_lock(self.repo), self._lock, self.conn:
                cur = self.conn.cursor()
                try:
                    cur.execute(
                        "UPDATE dispatcher_leases SET lease_token = ?, owner = ?, process_identity = ?, "
                        "renewed_at = ?, state = 'running' "
                        "WHERE job_id = ? AND thread_id = ? AND lease_token = ? AND state = 'resumable'",
                        (
                            token,
                            owner,
                            canonical_json(process_identity).decode() if process_identity else None,
                            now,
                            job_id,
                            thread_id,
                            prior_token,
                        ),
                    )
                    return token if cur.rowcount == 1 else None
                finally:
                    cur.close()
        except RuntimeError as exc:
            if str(exc) != "repository writer is busy":
                raise
            return None

    def release_lease(self, job_id: str, lease_token: str) -> bool:
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute("DELETE FROM dispatcher_leases WHERE job_id = ? AND lease_token = ?", (job_id, lease_token))
                affected = cur.rowcount
            finally:
                cur.close()
        return affected == 1

    def lease(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT job_id, thread_id, work_unit_id, run_id, execution_snapshot_fingerprint, "
                    "owner, process_identity, acquired_at, renewed_at, state, summary, lease_token "
                    "FROM dispatcher_leases WHERE job_id = ?",
                    (job_id,),
                )
                row = cur.fetchone()
            finally:
                cur.close()
        if row is None:
            return None
        job, thread, unit, run_id, snapshot_fingerprint, owner, identity, acquired, renewed, state, summary, token = row
        return {
            "job_id": job,
            "thread_id": thread,
            "work_unit_id": unit,
            "run_id": run_id,
            "execution_snapshot_fingerprint": snapshot_fingerprint,
            "owner": owner,
            "process_identity": json.loads(identity) if identity else None,
            "acquired_at": acquired,
            "renewed_at": renewed,
            "state": state,
            "summary": json.loads(summary) if summary else {},
            "lease_token": token,
        }

    def leases(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT job_id, thread_id, work_unit_id, run_id, execution_snapshot_fingerprint, "
                    "owner, process_identity, acquired_at, renewed_at, state, summary "
                    "FROM dispatcher_leases ORDER BY job_id"
                )
                rows = cur.fetchall()
            finally:
                cur.close()
        return [
            {
                "job_id": r[0],
                "thread_id": r[1],
                "work_unit_id": r[2],
                "run_id": r[3],
                "execution_snapshot_fingerprint": r[4],
                "owner": r[5],
                "process_identity": json.loads(r[6]) if r[6] else None,
                "acquired_at": r[7],
                "renewed_at": r[8],
                "state": r[9],
                "summary": json.loads(r[10]) if r[10] else {},
            }
            for r in rows
        ]

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

    # -- явный повтор агентного запуска -------------------------------

    def reserve_retry(
        self,
        idempotency_key: str,
        request_fingerprint: str,
        run_id: str,
        job_id: str,
        predecessor_run_id: str,
        policy_source: str,
        execution_snapshot: dict[str, Any],
        execution_snapshot_fingerprint: str,
    ) -> tuple[dict[str, Any], bool]:
        """Один раз связывает ключ повтора с запуском и полным снимком."""

        now = _now_iso()
        owner_token = str(uuid.uuid4())
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT run_id, request_fingerprint FROM dispatcher_retry_runs WHERE idempotency_key = ?",
                    (idempotency_key,),
                )
                existing = cur.fetchone()
                if existing is not None:
                    if str(existing[1]) != request_fingerprint:
                        raise RuntimeError("idempotency key is already bound to another dispatcher retry request")
                    return self._retry_run_locked(cur, str(existing[0])), False
                cur.execute(
                    "INSERT INTO dispatcher_retry_runs "
                    "(run_id, idempotency_key, request_fingerprint, job_id, predecessor_run_id, policy_source, "
                    "execution_snapshot, execution_snapshot_fingerprint, owner_token, state, result, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'preparing', NULL, ?, ?)",
                    (
                        run_id,
                        idempotency_key,
                        request_fingerprint,
                        job_id,
                        predecessor_run_id,
                        policy_source,
                        canonical_json(execution_snapshot).decode(),
                        execution_snapshot_fingerprint,
                        owner_token,
                        now,
                        now,
                    ),
                )
                return self._retry_run_locked(cur, run_id), True
            finally:
                cur.close()

    def retry_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self.conn.cursor()
            try:
                return self._retry_run_locked(cur, run_id)
            finally:
                cur.close()

    def abandon_retry(self, run_id: str, owner_token: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "DELETE FROM dispatcher_retry_runs WHERE run_id = ? AND owner_token = ? AND state = 'preparing'",
                (run_id, owner_token),
            )

    @staticmethod
    def _retry_run_locked(cur, run_id: str) -> dict[str, Any] | None:
        cur.execute(
            "SELECT run_id, request_fingerprint, job_id, predecessor_run_id, policy_source, execution_snapshot, "
            "execution_snapshot_fingerprint, owner_token, state, result, created_at, updated_at "
            "FROM dispatcher_retry_runs WHERE run_id = ?",
            (run_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "request_fingerprint": row[1],
            "job_id": row[2],
            "predecessor_run_id": row[3],
            "policy_source": row[4],
            "execution_snapshot": json.loads(row[5]),
            "execution_snapshot_fingerprint": row[6],
            "owner_token": row[7],
            "state": row[8],
            "result": json.loads(row[9]) if row[9] else None,
            "created_at": row[10],
            "updated_at": row[11],
        }

    def update_retry(
        self,
        run_id: str,
        owner_token: str,
        state: str,
        result: dict[str, Any] | None = None,
    ) -> bool:
        if state not in {"preparing", "started", "terminal"}:
            raise ValueError("invalid dispatcher retry state")
        with self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "UPDATE dispatcher_retry_runs SET state = ?, result = ?, updated_at = ? "
                    "WHERE run_id = ? AND owner_token = ?",
                    (
                        state,
                        canonical_json(result).decode() if result is not None else None,
                        _now_iso(),
                        run_id,
                        owner_token,
                    ),
                )
                return cur.rowcount == 1
            finally:
                cur.close()

    # -- предложения ---------------------------------------------------

    def save_proposal(self, key: str, job_id: str, thread_id: str, kind: str, payload: dict, lease_token: str | None = None) -> bool:
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                if lease_token is not None:
                    cur.execute("SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND thread_id = ? AND lease_token = ?", (job_id, thread_id, lease_token))
                    if cur.fetchone() is None:
                        return False
                cur.execute(
                    "INSERT OR REPLACE INTO dispatcher_proposals (key, job_id, thread_id, kind, payload, created_at, consumed_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (key, job_id, thread_id, kind, canonical_json(payload).decode("utf-8"), _now_iso()),
                )
            finally:
                cur.close()
        return True

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

    def copy_compatible_results(
        self,
        source_thread_id: str,
        target_thread_id: str,
        job_id: str,
        lease_token: str,
    ) -> int:
        """Копирует только типизированные envelopes; совместимость проверит граф."""

        copied = 0
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND thread_id = ? AND lease_token = ?", (job_id, target_thread_id, lease_token))
                if cur.fetchone() is None:
                    return 0
                cur.execute(
                    "SELECT payload FROM dispatcher_proposals "
                    "WHERE thread_id = ? AND kind = 'node-result' AND consumed_at IS NULL",
                    (source_thread_id,),
                )
                now = _now_iso()
                for (encoded,) in cur.fetchall():
                    payload = json.loads(encoded)
                    name = payload.get("name")
                    envelope = payload.get("envelope")
                    if not isinstance(name, str) or not isinstance(envelope, dict):
                        continue
                    key = sha256(canonical_json({"thread_id": target_thread_id, "node_result": name}))
                    cur.execute(
                        "INSERT OR IGNORE INTO dispatcher_proposals "
                        "(key, job_id, thread_id, kind, payload, created_at, consumed_at) "
                        "VALUES (?, ?, ?, 'node-result', ?, ?, NULL)",
                        (key, job_id, target_thread_id, canonical_json(payload).decode(), now),
                    )
                    copied += cur.rowcount
            finally:
                cur.close()
        return copied

    def start_invocation(
        self,
        job_id: str,
        run_id: str,
        phase_id: str,
        role_id: str,
        work_unit_id: str,
        configured_slots: int,
        lease_token: str,
        *,
        execution_snapshot_fingerprint: str = "",
        profile_id: str = "",
        context_manifest_fingerprint: str = "",
        context_envelope_fingerprint: str = "",
        prepared_input_fingerprint: str = "",
        context_provenance: list[dict[str, Any]] | None = None,
        context_diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, str] | None:
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND lease_token = ?", (job_id, lease_token))
                if cur.fetchone() is None:
                    return None
                cur.execute("SELECT slot_id FROM dispatcher_invocations WHERE job_id = ? AND run_id = ? AND phase_id = ? AND role_id = ? AND status = 'running'", (job_id, run_id, phase_id, role_id))
                used = {str(row[0]) for row in cur.fetchall()}
                ordinal = next((value for value in range(1, configured_slots + 1) if f"{phase_id}:{role_id}:{value}" not in used), None)
                if ordinal is None:
                    return None
                invocation_id = str(uuid.uuid4())
                slot_id = f"{phase_id}:{role_id}:{ordinal}"
                now = _now_iso()
                cur.execute(
                    "SELECT context_diagnostics FROM dispatcher_phase_work "
                    "WHERE run_id = ? AND phase_id = ? AND role_id = ? AND work_unit_id = ?",
                    (run_id, phase_id, role_id, work_unit_id),
                )
                prior_row = cur.fetchone()
                prior_diagnostics = (
                    json.loads(prior_row[0])
                    if prior_row and prior_row[0]
                    else {}
                )
                merged_diagnostics = {
                    **prior_diagnostics,
                    **(context_diagnostics or {}),
                }
                cur.execute(
                    "INSERT INTO dispatcher_invocations "
                    "(invocation_id, job_id, run_id, phase_id, role_id, "
                    "work_unit_id, slot_id, status, created_at, updated_at, "
                    "execution_snapshot_fingerprint, profile_id, "
                    "context_manifest_fingerprint, context_envelope_fingerprint, "
                    "prepared_input_fingerprint, context_provenance, "
                    "context_diagnostics) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        invocation_id,
                        job_id,
                        run_id,
                        phase_id,
                        role_id,
                        work_unit_id,
                        slot_id,
                        now,
                        now,
                        execution_snapshot_fingerprint or None,
                        profile_id or None,
                        context_manifest_fingerprint or None,
                        context_envelope_fingerprint or None,
                        prepared_input_fingerprint or None,
                        canonical_json(context_provenance or []).decode(),
                        canonical_json(merged_diagnostics).decode(),
                    ),
                )
                cur.execute(
                    "UPDATE dispatcher_phase_work SET status = 'running', "
                    "execution_kind = 'provider', context_diagnostics = ?, "
                    "invocation_id = ?, updated_at = ? "
                    "WHERE run_id = ? AND phase_id = ? AND role_id = ? AND work_unit_id = ?",
                    (
                        canonical_json(merged_diagnostics).decode(),
                        invocation_id,
                        now,
                        run_id,
                        phase_id,
                        role_id,
                        work_unit_id,
                    ),
                )
                payload = {
                    "transition_key": f"invocation:{invocation_id}:started",
                    "invocation_id": invocation_id,
                    "run_id": run_id,
                    "phase_id": phase_id,
                    "role_id": role_id,
                    "slot_id": slot_id,
                    "work_unit_id": work_unit_id,
                    "invocation_status": "running",
                    "timestamp": now,
                }
                cur.execute(
                    "INSERT OR IGNORE INTO dispatcher_invocation_outbox "
                    "(transition_key, invocation_id, run_id, event_type, payload, created_at) "
                    "VALUES (?, ?, ?, 'invocation.started', ?, ?)",
                    (
                        payload["transition_key"],
                        invocation_id,
                        run_id,
                        canonical_json(payload).decode(),
                        now,
                    ),
                )
                return {"invocation_id": invocation_id, "slot_id": slot_id}
            finally:
                cur.close()

    def terminalize_invocation(
        self,
        invocation_id: str,
        status: str,
        lease_token: str | None = None,
        *,
        error_code: str = "",
        error_summary: str = "",
        result_ref: str = "",
    ) -> bool:
        if status not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("invalid invocation terminal status")
        if error_code and (
            error_code not in INVOCATION_ERROR_CODES
            or
            len(error_code) > 64
            or not all(character.isascii() and (character.isalnum() or character in "_.-") for character in error_code)
        ):
            raise ValueError("invalid invocation error code")
        from .events import redact
        encoded_summary = str(redact(error_summary)).encode("utf-8")[:4096]
        while encoded_summary:
            try:
                safe_summary = encoded_summary.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded_summary = encoded_summary[:-1]
        else:
            safe_summary = ""
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                now = _now_iso()
                if lease_token is not None and not cur.execute(
                    "SELECT 1 FROM dispatcher_invocations i JOIN dispatcher_leases l "
                    "ON l.job_id = i.job_id WHERE i.invocation_id = ? AND l.lease_token = ?",
                    (invocation_id, lease_token),
                ).fetchone():
                    return False
                return bool(_terminalize_rows(
                    cur,
                    [invocation_id],
                    status,
                    now,
                    error_code=error_code,
                    error_summary=safe_summary,
                    result_ref=result_ref,
                ))
            finally:
                cur.close()

    def finish_invocation(self, invocation_id: str, status: str, lease_token: str) -> bool:
        return self.terminalize_invocation(invocation_id, status, lease_token)

    def reconcile_invocation_outbox(self, event_store) -> int:
        delivered = 0
        with self._lock:
            rows = self.conn.execute(
                "SELECT transition_key, event_type, run_id, payload "
                "FROM dispatcher_invocation_outbox WHERE delivered_at IS NULL "
                "ORDER BY created_at, transition_key"
            ).fetchall()
        for transition_key, event_type, run_id, encoded in rows:
            payload = json.loads(encoded)
            event_store.emit(event_type, run_id, payload)
            with self._lock, self.conn:
                changed = self.conn.execute(
                    "UPDATE dispatcher_invocation_outbox SET delivered_at = ? "
                    "WHERE transition_key = ? AND delivered_at IS NULL",
                    (_now_iso(), transition_key),
                ).rowcount
            delivered += changed
        return delivered

    def register_phase_work(
        self,
        job_id: str,
        run_id: str,
        phase_id: str,
        role_id: str,
        work_unit_ids: list[str],
        lease_token: str,
    ) -> bool:
        """Регистрирует только реально выведенные единицы как ожидающие слот."""

        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND lease_token = ?", (job_id, lease_token))
                if cur.fetchone() is None:
                    return False
                now = _now_iso()
                cur.executemany(
                    "INSERT OR IGNORE INTO dispatcher_phase_work "
                    "(job_id, run_id, phase_id, role_id, work_unit_id, status, invocation_id, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'queued', NULL, ?)",
                    [(job_id, run_id, phase_id, role_id, item, now) for item in work_unit_ids],
                )
                return True
            finally:
                cur.close()

    def phase_work(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT job_id, run_id, phase_id, role_id, work_unit_id, status, invocation_id, updated_at, "
                    "execution_kind, source_result_ref, context_diagnostics "
                    "FROM dispatcher_phase_work WHERE run_id = ? ORDER BY phase_id, role_id, work_unit_id",
                    (run_id,),
                )
                rows = cur.fetchall()
            finally:
                cur.close()
        keys = ("job_id", "run_id", "phase_id", "role_id", "work_unit_id", "status", "invocation_id", "updated_at", "execution_kind", "source_result_ref", "context_diagnostics")
        return [
            _decode_context_columns(dict(zip(keys, row, strict=True)))
            for row in rows
        ]

    def complete_reused_work(
        self,
        job_id: str,
        run_id: str,
        phase_id: str,
        role_id: str,
        work_unit_id: str,
        lease_token: str,
        *,
        source_result_ref: str = "",
        context_diagnostics: dict[str, Any] | None = None,
    ) -> bool:
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "UPDATE dispatcher_phase_work SET status = 'completed', "
                    "execution_kind = 'reused', source_result_ref = ?, "
                    "context_diagnostics = ?, updated_at = ? "
                    "WHERE run_id = ? AND phase_id = ? AND role_id = ? AND work_unit_id = ? "
                    "AND status IN ('queued', 'completed') AND EXISTS "
                    "(SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND lease_token = ?)",
                    (
                        source_result_ref or None,
                        canonical_json(context_diagnostics or {}).decode(),
                        _now_iso(),
                        run_id,
                        phase_id,
                        role_id,
                        work_unit_id,
                        job_id,
                        lease_token,
                    ),
                )
                return cur.rowcount == 1
            finally:
                cur.close()

    def record_phase_context(
        self,
        job_id: str,
        run_id: str,
        phase_id: str,
        role_id: str,
        work_unit_id: str,
        lease_token: str,
        *,
        execution_kind: str,
        status: str,
        context_diagnostics: dict[str, Any],
    ) -> bool:
        if execution_kind not in {"provider", "deterministic", "preflight_failed"}:
            raise ValueError("invalid phase execution kind")
        with repository_lock(self.repo), self._lock, self.conn:
            changed = self.conn.execute(
                "UPDATE dispatcher_phase_work SET status = ?, execution_kind = ?, "
                "context_diagnostics = ?, updated_at = ? "
                "WHERE run_id = ? AND phase_id = ? AND role_id = ? "
                "AND work_unit_id = ? AND EXISTS "
                "(SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND lease_token = ?)",
                (
                    status,
                    execution_kind,
                    canonical_json(context_diagnostics).decode(),
                    _now_iso(),
                    run_id,
                    phase_id,
                    role_id,
                    work_unit_id,
                    job_id,
                    lease_token,
                ),
            ).rowcount
        return changed == 1

    def cancel_queued_work(self, job_id: str, run_id: str, lease_token: str) -> int:
        """Терминально закрывает невыданные единицы после ошибки фазы."""

        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "UPDATE dispatcher_phase_work SET status = 'cancelled', updated_at = ? "
                    "WHERE job_id = ? AND run_id = ? AND status = 'queued' AND EXISTS "
                    "(SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND lease_token = ?)",
                    (_now_iso(), job_id, run_id, job_id, lease_token),
                )
                return cur.rowcount
            finally:
                cur.close()

    def cancel_running_work(
        self, job_id: str, run_id: str, lease_token: str
    ) -> int:
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                if not cur.execute(
                    "SELECT 1 FROM dispatcher_leases "
                    "WHERE job_id = ? AND run_id = ? AND lease_token = ?",
                    (job_id, run_id, lease_token),
                ).fetchone():
                    return 0
                invocation_ids = [
                    str(row[0])
                    for row in cur.execute(
                        "SELECT invocation_id FROM dispatcher_invocations "
                        "WHERE job_id = ? AND run_id = ? AND status = 'running'",
                        (job_id, run_id),
                    )
                ]
                return _terminalize_rows(
                    cur, invocation_ids, "cancelled", _now_iso()
                )
            finally:
                cur.close()

    def cancel_run_work(self, job_id: str, run_id: str, lease_token: str) -> int:
        """Закрывает выполняемые и ожидающие единицы явной отменой запуска."""

        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND run_id = ? AND lease_token = ?",
                    (job_id, run_id, lease_token),
                )
                if cur.fetchone() is None:
                    return 0
                now = _now_iso()
                invocation_ids = [
                    str(row[0])
                    for row in cur.execute(
                        "SELECT invocation_id FROM dispatcher_invocations "
                        "WHERE job_id = ? AND run_id = ? AND status = 'running'",
                        (job_id, run_id),
                    )
                ]
                invocation_count = _terminalize_rows(
                    cur, invocation_ids, "cancelled", now
                )
                cur.execute(
                    "UPDATE dispatcher_phase_work SET status = 'cancelled', updated_at = ? "
                    "WHERE job_id = ? AND run_id = ? AND status IN ('running', 'queued')",
                    (now, job_id, run_id),
                )
                return invocation_count + cur.rowcount
            finally:
                cur.close()

    def interrupt_running_work(self, job_id: str, run_id: str, lease_token: str) -> int:
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND run_id = ? AND lease_token = ?",
                    (job_id, run_id, lease_token),
                )
                if cur.fetchone() is None:
                    return 0
                now = _now_iso()
                invocation_ids = [
                    str(row[0])
                    for row in cur.execute(
                        "SELECT invocation_id FROM dispatcher_invocations "
                        "WHERE job_id = ? AND run_id = ? AND status = 'running'",
                        (job_id, run_id),
                    )
                ]
                return _terminalize_rows(
                    cur, invocation_ids, "interrupted", now
                )
            finally:
                cur.close()

    def latest_phase_run(self, job_id: str) -> str:
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT run_id FROM dispatcher_phase_work WHERE job_id = ? "
                    "ORDER BY updated_at DESC, run_id DESC LIMIT 1",
                    (job_id,),
                )
                row = cur.fetchone()
                return str(row[0]) if row else ""
            finally:
                cur.close()

    def invocations(
        self,
        run_id: str | None = None,
        limit: int = 100,
        phase_id: str | None = None,
        role_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            cur = self.conn.cursor()
            try:
                clauses, parameters = [], []
                for column, value in (("run_id", run_id), ("phase_id", phase_id), ("role_id", role_id)):
                    if value is not None:
                        clauses.append(f"{column} = ?")
                        parameters.append(value)
                where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
                cur.execute(
                    "SELECT invocation_id, job_id, run_id, phase_id, role_id, work_unit_id, slot_id, status, created_at, updated_at, "
                    "execution_snapshot_fingerprint, profile_id, context_manifest_fingerprint, "
                    "context_envelope_fingerprint, prepared_input_fingerprint, "
                    "context_provenance, context_diagnostics, "
                    "finished_at, error_code, error_summary, result_ref "
                    f"FROM dispatcher_invocations{where} ORDER BY created_at DESC, invocation_id DESC LIMIT ?",
                    (*parameters, limit),
                )
                rows = cur.fetchall()
            finally:
                cur.close()
        keys = ("invocation_id", "job_id", "run_id", "phase_id", "role_id", "work_unit_id", "slot_id", "status", "created_at", "updated_at", "execution_snapshot_fingerprint", "profile_id", "context_manifest_fingerprint", "context_envelope_fingerprint", "prepared_input_fingerprint", "context_provenance", "context_diagnostics", "finished_at", "error_code", "error_summary", "result_ref")
        return [_decode_context_columns(dict(zip(keys, row, strict=True))) for row in rows]

    def invocation(self, invocation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT invocation_id, job_id, run_id, phase_id, role_id, "
                "work_unit_id, slot_id, status, created_at, updated_at, "
                "execution_snapshot_fingerprint, profile_id, "
                "context_manifest_fingerprint, context_envelope_fingerprint, "
                "prepared_input_fingerprint, context_provenance, "
                "context_diagnostics, finished_at, error_code, "
                "error_summary, result_ref FROM dispatcher_invocations "
                "WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
        if row is None:
            return None
        keys = (
            "invocation_id", "job_id", "run_id", "phase_id", "role_id",
            "work_unit_id", "slot_id", "status", "created_at", "updated_at",
            "execution_snapshot_fingerprint", "profile_id",
            "context_manifest_fingerprint", "context_envelope_fingerprint",
            "prepared_input_fingerprint", "context_provenance",
            "context_diagnostics", "finished_at", "error_code",
            "error_summary", "result_ref",
        )
        return _decode_context_columns(dict(zip(keys, row, strict=True)))

    def invocation_history(
        self,
        run_id: str,
        phase_id: str,
        role_id: str,
        slot_id: str,
        limit: int,
        before: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = [run_id, phase_id, role_id, slot_id]
        before_clause = ""
        if before is not None:
            before_clause = (
                " AND (created_at < ? OR (created_at = ? AND invocation_id < ?))"
            )
            parameters.extend((before[0], before[0], before[1]))
        with self._lock:
            rows = self.conn.execute(
                "SELECT invocation_id, job_id, run_id, phase_id, role_id, "
                "work_unit_id, slot_id, status, created_at, updated_at, "
                "execution_snapshot_fingerprint, profile_id, "
                "context_manifest_fingerprint, context_envelope_fingerprint, "
                "prepared_input_fingerprint, context_provenance, "
                "context_diagnostics, finished_at, error_code, "
                "error_summary, result_ref FROM dispatcher_invocations "
                "WHERE run_id = ? AND phase_id = ? AND role_id = ? AND slot_id = ?"
                + before_clause
                + " ORDER BY created_at DESC, invocation_id DESC LIMIT ?",
                (*parameters, limit),
            ).fetchall()
        keys = (
            "invocation_id", "job_id", "run_id", "phase_id", "role_id",
            "work_unit_id", "slot_id", "status", "created_at", "updated_at",
            "execution_snapshot_fingerprint", "profile_id",
            "context_manifest_fingerprint", "context_envelope_fingerprint",
            "prepared_input_fingerprint", "context_provenance",
            "context_diagnostics", "finished_at", "error_code",
            "error_summary", "result_ref",
        )
        return [_decode_context_columns(dict(zip(keys, row, strict=True))) for row in rows]

    def invocation_count(self, run_id: str, phase_id: str, role_id: str) -> int:
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM dispatcher_invocations WHERE run_id = ? AND phase_id = ? AND role_id = ?",
                    (run_id, phase_id, role_id),
                )
                return int(cur.fetchone()[0])
            finally:
                cur.close()

    def slot_assignments(
        self, run_id: str, phase_id: str, role_id: str
    ) -> dict[str, dict[str, dict[str, Any] | None]]:
        """Returns current and latest invocation per slot in one bounded-result query."""

        with self._lock:
            rows = self.conn.execute(
                "WITH ranked AS ("
                "SELECT invocation_id, job_id, run_id, phase_id, role_id, "
                "work_unit_id, slot_id, status, created_at, updated_at, "
                "execution_snapshot_fingerprint, profile_id, "
                "context_manifest_fingerprint, context_envelope_fingerprint, "
                "prepared_input_fingerprint, context_provenance, "
                "context_diagnostics, finished_at, error_code, "
                "error_summary, result_ref, "
                "ROW_NUMBER() OVER (PARTITION BY slot_id "
                "ORDER BY created_at DESC, invocation_id DESC) AS row_number "
                "FROM dispatcher_invocations "
                "WHERE run_id = ? AND phase_id = ? AND role_id = ?"
                ") SELECT * FROM ranked WHERE row_number = 1 OR status = 'running' "
                "ORDER BY slot_id, row_number",
                (run_id, phase_id, role_id),
            ).fetchall()
        keys = (
            "invocation_id", "job_id", "run_id", "phase_id", "role_id",
            "work_unit_id", "slot_id", "status", "created_at", "updated_at",
            "execution_snapshot_fingerprint", "profile_id",
            "context_manifest_fingerprint", "context_envelope_fingerprint",
            "prepared_input_fingerprint", "context_provenance",
            "context_diagnostics", "finished_at", "error_code",
            "error_summary", "result_ref",
        )
        result: dict[str, dict[str, dict[str, Any] | None]] = {}
        for row in rows:
            invocation = _decode_context_columns(
                dict(zip(keys, row[: len(keys)], strict=True))
            )
            slot = result.setdefault(
                invocation["slot_id"], {"current": None, "latest": None}
            )
            if row[-1] == 1:
                slot["latest"] = invocation
            if invocation["status"] == "running":
                slot["current"] = invocation
        return result

    def consume_proposal(self, key: str, lease_token: str | None = None) -> bool:
        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                if lease_token is not None:
                    cur.execute("SELECT job_id, thread_id FROM dispatcher_proposals WHERE key = ?", (key,))
                    owner = cur.fetchone()
                    if owner is None:
                        return False
                    cur.execute("SELECT 1 FROM dispatcher_leases WHERE job_id = ? AND thread_id = ? AND lease_token = ?", (owner[0], owner[1], lease_token))
                    if cur.fetchone() is None:
                        return False
                cur.execute("UPDATE dispatcher_proposals SET consumed_at = ? WHERE key = ? AND consumed_at IS NULL", (_now_iso(), key))
                affected = cur.rowcount
            finally:
                cur.close()
        return affected > 0

    def approve_noise_review(
        self,
        proposal_key: str,
        approval_key: str,
        job_id: str,
        thread_id: str,
        lease_token: str,
        payload: dict[str, Any],
        summary: dict[str, Any],
    ) -> bool:
        """Атомарно сохраняет одобрение шума и делает поток возобновляемым."""

        with repository_lock(self.repo), self._lock, self.conn:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT summary FROM dispatcher_leases "
                    "WHERE job_id = ? AND thread_id = ? AND lease_token = ?",
                    (job_id, thread_id, lease_token),
                )
                lease = cur.fetchone()
                cur.execute(
                    "SELECT 1 FROM dispatcher_proposals "
                    "WHERE key = ? AND job_id = ? AND thread_id = ? "
                    "AND kind = 'approval' AND consumed_at IS NULL",
                    (proposal_key, job_id, thread_id),
                )
                if lease is None or cur.fetchone() is None:
                    return False
                now = _now_iso()
                cur.execute(
                    "INSERT OR REPLACE INTO dispatcher_proposals "
                    "(key, job_id, thread_id, kind, payload, created_at, consumed_at) "
                    "VALUES (?, ?, ?, 'noise-approval', ?, ?, NULL)",
                    (approval_key, job_id, thread_id, canonical_json(payload).decode(), now),
                )
                cur.execute(
                    "UPDATE dispatcher_proposals SET consumed_at = ? "
                    "WHERE key = ? AND consumed_at IS NULL",
                    (now, proposal_key),
                )
                merged = {**(json.loads(lease[0]) if lease[0] else {}), "phase": "noise_approved", **summary}
                cur.execute(
                    "UPDATE dispatcher_leases SET state = 'resumable', summary = ?, renewed_at = ? "
                    "WHERE job_id = ? AND thread_id = ? AND lease_token = ?",
                    (canonical_json(merged).decode(), now, job_id, thread_id, lease_token),
                )
                return cur.rowcount == 1
            finally:
                cur.close()

    # -- очистка -------------------------------------------------------

    def delete_thread(
        self,
        thread_id: str,
        job_id: str | None = None,
        lease_token: str | None = None,
        *,
        preserve_results: bool = False,
    ) -> bool:
        """Удаляет чекпойнты LangGraph и операционные предложения потока."""

        guard = self.lease_guard(job_id, lease_token, thread_id) if job_id and lease_token else repository_lock(self.repo)
        with guard, self._lock:
            try:
                self.saver.delete_thread(thread_id)
            except Exception:
                pass
            with self.conn:
                cur = self.conn.cursor()
                try:
                    cur.execute(
                        "DELETE FROM dispatcher_proposals WHERE thread_id = ?"
                        + (" AND kind != 'node-result'" if preserve_results else ""),
                        (thread_id,),
                    )
                finally:
                    cur.close()
        return True


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
