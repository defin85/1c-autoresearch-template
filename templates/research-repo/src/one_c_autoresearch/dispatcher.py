"""Координатор диспетчера конвейера.

Координатор оркеструет два фиксированных графа LangGraph (``discover-mrq`` и
``decide-mrq``) вокруг существующего ограниченного исполнителя агентных
предложений. Он НЕ выбирает ворота, не запускает иной агентный протокол и не
пишет канонические файлы напрямую: любой эффект проходит через прикладной
сервис, ожидаемый отпечаток и идемпотентность.

Ответственности координатора:

* расчёт и проверка ``thread_id`` по активным поколениям и профилю агента;
* аренды 10/30 секунд с фоновым обновлением;
* мягкая остановка, продолжение, отмена и явный повтор;
* публикация операционных переходов через существующие типы
  ``workflow-event/v1`` (новые типы событий не вводятся).

Координатор работает в тандеме с :class:`DispatcherStore` для операционного
состояния и с существующим :class:`ApplicationService` для канонических
эффектов.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import final

from . import source_search
from .contracts import JsonValue, canonical_json, json_object, parse_json_object, sha256
from .events import EventStore, process_identity
from .sqlite_state import (
    LEASE_EXPIRY_SECONDS,
    LEASE_RENEWAL_SECONDS,
    DispatcherStore,
    is_stale,
)


DISPATCHER_JOBS = ("analyze-dif", "consolidate-mrq", "classify-mrq", "decide-mrq")
LEASE_RENEWAL_SECONDS_EFFECTIVE = LEASE_RENEWAL_SECONDS
STALE_AFTER_SECONDS = 10  # UI-порог несвежего активного запуска (spec.md)


def _object(value: object) -> dict[str, JsonValue]:
    return json_object(value)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected integer")
    return value


def _number(value: object) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("expected number")
    return value


def _nested(value: object, key: str) -> dict[str, JsonValue]:
    return _object(_object(value).get(key))


def _objects(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        raise ValueError("expected object array")
    return [_object(item) for item in value]


def _strings(value: JsonValue) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("expected string array")
    return [item for item in value if isinstance(item, str)]


def _profiles(value: object) -> dict[str, dict[str, JsonValue]]:
    return {key: _object(item) for key, item in _object(value).items()}


def _source_search_policy(value: object) -> source_search.SourceSearchPolicy:
    raw = _object(value)
    operations = _strings(raw.get("operations", []))
    component_ids = _strings(raw.get("component_ids", []))
    paths = _strings(raw.get("logical_path_prefixes", []))
    limits = source_search.validate_profile_policy({
        "operations": operations,
        **{field: raw.get(field) for field in source_search.POLICY_LIMITS},
    })
    return source_search.SourceSearchPolicy(
        schema_version=_text(raw.get("schema_version")),
        tool_schema_version=_text(raw.get("tool_schema_version")),
        bridge_version=_text(raw.get("bridge_version")),
        operations=operations,
        source_generation_id=_text(raw.get("source_generation_id")),
        component_ids=component_ids,
        logical_path_prefixes=paths,
        routing_fingerprint=_text(raw.get("routing_fingerprint")),
        max_calls=limits["max_calls"], max_concurrent_calls=limits["max_concurrent_calls"],
        per_call_deadline_seconds=limits["per_call_deadline_seconds"],
        max_backend_seconds=limits["max_backend_seconds"], max_query_bytes=limits["max_query_bytes"],
        max_total_query_bytes=limits["max_total_query_bytes"],
        max_results_per_call=limits["max_results_per_call"], max_total_results=limits["max_total_results"],
        max_returned_bytes_per_call=limits["max_returned_bytes_per_call"],
        max_total_returned_bytes=limits["max_total_returned_bytes"],
        scope_fingerprint=_text(raw.get("scope_fingerprint")),
        policy_fingerprint=_text(raw.get("policy_fingerprint")),
    )


def _source_search_ledger(value: object) -> source_search.SearchLedger:
    raw = _object(value)
    items: list[source_search.LedgerItem] = []
    for item_value in _objects(raw.get("items")):
        item = source_search.LedgerItem()
        for field in (
            "status", "capability", "route_fingerprint", "adapter_id",
            "adapter_version", "capability_fingerprint", "index_fingerprint",
            "result_manifest_fingerprint", "query_hmac",
        ):
            text = item_value.get(field)
            if isinstance(text, str):
                item[field] = text
        fallback = item_value.get("fallback_reason")
        if fallback is None or isinstance(fallback, str):
            item["fallback_reason"] = fallback
        items.append(item)
    return source_search.SearchLedger(
        items=items,
        ledger_complete=raw.get("ledger_complete") is True,
        reconciled=raw.get("reconciled") is True,
        policy_fingerprint=_text(raw.get("policy_fingerprint")),
        scope_fingerprint=_text(raw.get("scope_fingerprint")),
    )


@dataclass(frozen=True)
@final
class DispatcherBindings:
    """Полный набор отпечатков, по которым считается ``thread_id``.

    Любое изменение любого поля делает сохранённый поток устаревшим; повторное
    применение результата блокируется.
    """

    project_id: str
    job_id: str
    work_unit_id: str
    source_generation_id: str
    diff_generation_id: str
    canonical_generation_id: str
    workflow_fingerprint: str
    agent_profile_fingerprint: str
    instruction_supplement: str
    run_id: str = ""
    execution_snapshot_fingerprint: str = ""
    classification_generation_id: str = ""
    consolidation_transaction_id: str = ""

    def thread_id(self) -> str:
        preimage = {
            "schema_version": "2",
            "run_id": self.run_id,
            "execution_snapshot_fingerprint": self.execution_snapshot_fingerprint,
            "classification_generation_id": self.classification_generation_id,
            "consolidation_transaction_id": self.consolidation_transaction_id,
            "project_id": self.project_id,
            "job_id": self.job_id,
            "work_unit_id": self.work_unit_id,
        }
        return sha256(canonical_json(preimage))

    def canonical_payload(self) -> dict[str, JsonValue]:
        return {
            "project_id": self.project_id,
            "job_id": self.job_id,
            "work_unit_id": self.work_unit_id,
            "source_generation_id": self.source_generation_id,
            "diff_generation_id": self.diff_generation_id,
            "canonical_generation_id": self.canonical_generation_id,
            "workflow_fingerprint": self.workflow_fingerprint,
            "agent_profile_fingerprint": self.agent_profile_fingerprint,
            "instruction_supplement": self.instruction_supplement,
            "run_id": self.run_id,
            "execution_snapshot_fingerprint": self.execution_snapshot_fingerprint,
            "classification_generation_id": self.classification_generation_id,
            "consolidation_transaction_id": self.consolidation_transaction_id,
            "thread_id": self.thread_id(),
        }


def load_bindings(repo: Path, project_id: str, job_id: str, work_unit_id: str, agent_profile: dict[str, JsonValue] | None, instruction_supplement: str) -> DispatcherBindings:
    """Собирает привязки из активного состояния репозитория и профиля агента."""

    from .stage_recompute import active_state
    pointers, workflow_fingerprint = active_state(repo)
    classification = parse_json_object((repo / "research/active-dif-classification-generation.json").read_text(encoding="utf-8")) if (repo / "research/active-dif-classification-generation.json").is_file() else {}
    consolidation = parse_json_object((repo / "research/active-consolidation-generation.json").read_text(encoding="utf-8")) if (repo / "research/active-consolidation-generation.json").is_file() else {}
    source = _object(pointers.get("source") or {})
    diff = _object(pointers.get("diff") or {})
    return DispatcherBindings(
        project_id=project_id,
        job_id=job_id,
        work_unit_id=work_unit_id,
        source_generation_id=str(source.get("generation_id", "")),
        diff_generation_id=str(diff.get("generation_id", "")),
        canonical_generation_id=str(consolidation.get("mrq_generation_id") or ""),
        workflow_fingerprint=workflow_fingerprint,
        agent_profile_fingerprint="sha256:" + sha256(canonical_json(agent_profile)) if agent_profile else "",
        instruction_supplement=instruction_supplement,
        classification_generation_id=str(classification.get("generation_id") or ""),
        consolidation_transaction_id=str(consolidation.get("transaction_id") or ""),
    )


@dataclass
@final
class DispatcherOutcome:
    """Результат одной операции над графиком.

    Канонические эффекты здесь нет — только операционная сводка для события
    ``step.finished`` и проекции.
    """

    job_id: str
    run_id: str
    status: str  # running | resumable | cancelled | completed | blocked | failed | stale
    thread_id: str
    revision: int
    summary: dict[str, JsonValue]
    blocker: dict[str, JsonValue] | None = None


@final
class LeaseRenewer:
    """Фоновый поток, обновляющий аренду независимо от агентных вызовов."""

    def __init__(self, store: DispatcherStore, job_id: str, lease_token: str, stop_event: threading.Event, *, interval_seconds: int = LEASE_RENEWAL_SECONDS_EFFECTIVE, on_renewed: Callable[[], object] | None = None) -> None:
        self.store = store
        self.job_id = job_id
        self.lease_token = lease_token
        self.stop_event = stop_event
        self.interval_seconds = interval_seconds
        self.on_renewed = on_renewed
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name=f"dispatcher-lease-{self.job_id}", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self.stop_event.wait(timeout=self.interval_seconds):
            try:
                renewed = self.store.renew_lease(self.job_id, self.lease_token)
            except Exception:
                renewed = False
            if not renewed:
                return
            if self.on_renewed is not None:
                try:
                    _ = self.on_renewed()
                except Exception:
                    # Подтверждение наблюдаемости не должно прекращать аренду.
                    pass


@final
class DispatcherCoordinator:
    """Управляет жизненным циклом графов ``discover-mrq`` и ``decide-mrq``.

    Один экземпляр на проект хранит активные остановки и поток обновления
    аренд. Не хранит каноническое состояние: всё, что нужно, читается заново
    перед каждым агентным вызовом и эффектом.
    """

    def __init__(self, repo: Path, project_id: str, store: DispatcherStore, event_store: EventStore, actor: str = "local-user") -> None:
        self.repo = repo.resolve()
        self.project_id = project_id
        self.store = store
        self.event_store = event_store
        self.actor = actor
        self._stop_events: dict[str, threading.Event] = {}
        self._renewers: dict[str, LeaseRenewer] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._lease_tokens: dict[str, str] = {}
        self._lock = threading.RLock()
        _ = self.store.reconcile_invocation_outbox(self.event_store)

    # -- запуск и жизненный цикл ---------------------------------------

    def _guard_migration(self) -> None:
        from .workflow_migration import guard_mutation
        guard_mutation(self.repo, base=self.store.base)

    def start(self, job_id: str, bindings: DispatcherBindings, *, run_id: str | None = None, execution_snapshot: dict[str, JsonValue] | None = None, emit_events: bool = True) -> DispatcherOutcome:
        """Запускает график, если аренда свободна. Иначе возвращает ``blocked``."""

        self._guard_migration()
        if job_id not in DISPATCHER_JOBS:
            raise ValueError(f"unsupported dispatcher job: {job_id}")
        if execution_snapshot is None:
            raise ValueError("dispatcher execution snapshot is required")
        run_id = run_id or str(uuid.uuid4())
        snapshot = execution_snapshot
        snapshot_fingerprint = self.event_store.prepare_run(run_id, snapshot)
        bindings = replace(bindings, run_id=run_id, execution_snapshot_fingerprint=snapshot_fingerprint)
        thread_id = bindings.thread_id()
        with self._lock:
            acquired = self.store.acquire_lease(
                job_id,
                thread_id,
                bindings.work_unit_id,
                self.actor,
                process_identity(),
                state="running",
                summary={
                    "phase": "starting",
                    "run_id": run_id,
                    "bindings": bindings.canonical_payload(),
                    "execution_snapshot_fingerprint": snapshot_fingerprint,
                },
                run_id=run_id,
                execution_snapshot_fingerprint=snapshot_fingerprint,
            )
            if not acquired:
                _ = self.event_store.remove_prepared_run(run_id)
                existing = self.store.lease(job_id) or {}
                return DispatcherOutcome(
                    job_id=job_id,
                    run_id=run_id,
                    status="blocked",
                    thread_id=thread_id,
                    revision=self.store.revision()[0],
                    summary={"existing": _object(existing)},
                    blocker={"code": "dispatcher.lease.busy", "message": "another process owns the dispatcher lease for this job", "action": job_id},
                )
            self._lease_tokens[job_id] = acquired
            stop_event = threading.Event()
            self._stop_events[job_id] = stop_event
            renewer = LeaseRenewer(
                self.store,
                job_id,
                acquired,
                stop_event,
                on_renewed=lambda: self._emit(
                    emit_events,
                    run_id,
                    job_id,
                    thread_id,
                    "step.progress",
                    {"status": "running", "kind": f"dispatcher.{job_id}.lease_renewed", "progress": {"lease_renewed": True}},
                ),
            )
            renewer.start()
            self._renewers[job_id] = renewer
        if emit_events:
            with self.store.lease_guard(job_id, acquired, thread_id):
                _ = self.event_store.emit(
                    "run.created",
                    run_id,
                    {
                        "status": "running",
                        "actor": self.actor,
                        "process_identity": process_identity(),
                        "workflow_fingerprint": bindings.workflow_fingerprint,
                        "operation": job_id,
                        "execution_snapshot_fingerprint": snapshot_fingerprint,
                        "policy_source": snapshot.get("policy_source", "current-policy"),
                        "tool_versions": {"codex": snapshot.get("codex_version", "")},
                    },
                )
        try:
            with self.store.lease_guard(job_id, acquired, thread_id):
                self._validate_new_run_snapshot(snapshot, bindings)
        except (RuntimeError, ValueError, OSError) as exc:
            if emit_events:
                with self.store.lease_guard(job_id, acquired, thread_id):
                    _ = self.event_store.emit(
                        "run.finished",
                        run_id,
                        {
                            "status": "failed",
                            "duration_seconds": 0.0,
                            "execution_snapshot_fingerprint": snapshot_fingerprint,
                            "policy_source": snapshot.get("policy_source", "current-policy"),
                            "tool_versions": {"codex": snapshot.get("codex_version", "")},
                            "error_class": "execution_snapshot_conflict",
                            "message": str(exc),
                        },
                    )
            self._stop_background(job_id)
            _ = self._lease_tokens.pop(job_id, None)
            _ = self.store.release_lease(job_id, acquired)
            return DispatcherOutcome(
                job_id,
                run_id,
                "failed",
                thread_id,
                self.store.revision()[0],
                {"phase": "snapshot_conflict"},
                {"code": "dispatcher.snapshot.conflict", "message": str(exc), "action": job_id},
            )
        revision = self._emit(
            emit_events,
            run_id,
            job_id,
            thread_id,
            "step.action",
            {
                "status": "running",
                "kind": f"dispatcher.{job_id}.started",
                "effective_action": {
                    "graph": job_id,
                    "thread_id": thread_id,
                    "bindings": bindings.canonical_payload(),
                    "policy_source": snapshot.get("policy_source", "current-policy"),
                    "predecessor_run_id": snapshot.get("predecessor_run_id", ""),
                },
            },
            revision_first=True,
        )
        _ = self.store.renew_lease(job_id, acquired, state="running", summary={"phase": "started", "run_id": run_id, "bindings": bindings.canonical_payload(), "execution_snapshot_fingerprint": snapshot_fingerprint})
        return DispatcherOutcome(job_id=job_id, run_id=run_id, status="running", thread_id=thread_id, revision=revision, summary={"phase": "started", "bindings": bindings.canonical_payload(), "execution_snapshot_fingerprint": snapshot_fingerprint})

    def _validate_new_run_snapshot(self, snapshot: dict[str, JsonValue], bindings: DispatcherBindings) -> None:
        from .agents import validate_execution_snapshot
        from .workflow import state_fingerprint
        from .user_state import load_agent_profiles

        validate_execution_snapshot(self.repo, snapshot)
        # Вызывается под lease_guard: повторный repository_lock здесь дал бы
        # ложный конфликт с собственным процессом.
        workflow_fingerprint = state_fingerprint(self.repo)
        if snapshot.get("policy_source") != "reuse-snapshot" and snapshot.get("workflow_fingerprint", bindings.workflow_fingerprint) != workflow_fingerprint:
            raise RuntimeError("workflow changed after the execution snapshot was prepared")
        current_profiles = load_agent_profiles(self.repo, self.store.base)
        for name, profile in _object(snapshot.get("profiles")).items():
            if snapshot.get("policy_source") != "reuse-snapshot" and current_profiles.get(name) != profile:
                raise RuntimeError(f"agent profile changed after the execution snapshot was prepared: {name}")

    def soft_stop(self, job_id: str, *, timeout_seconds: int | None = None, emit_events: bool = True) -> DispatcherOutcome:
        """Мягкая остановка: прекращает выдачу новых узлов и сохраняет чекпойнт."""

        self._guard_migration()
        with self._lock:
            stop_event = self._stop_events.get(job_id)
            if stop_event is not None:
                stop_event.set()
            renewer = self._renewers.pop(job_id, None)
        if renewer is not None:
            renewer.join(timeout=min(timeout_seconds or 0, LEASE_EXPIRY_SECONDS))
        worker = self._workers.get(job_id)
        if worker is not None:
            worker.join(timeout=max(0, timeout_seconds or 0))
        lease = self.store.lease(job_id)
        if lease is None:
            return DispatcherOutcome(
                job_id,
                "",
                "blocked",
                "",
                self.store.revision()[0],
                {"phase": "missing"},
                {"code": "dispatcher.lease.missing", "message": "there is no active dispatcher run to stop", "action": job_id},
            )
        run_id = _text(_nested(lease, "summary").get("run_id")) or str(uuid.uuid4())
        thread_id = _text(lease.get("thread_id"))
        token = self._lease_tokens.get(job_id) or _text(lease.get("lease_token"))
        _ = self.store.cancel_running_work(job_id, run_id, token)
        _ = self.store.reconcile_invocation_outbox(self.event_store)
        _ = self.store.renew_lease(job_id, token, state="resumable", summary={"phase": "soft_stopped"})
        revision = self._emit(emit_events, run_id, job_id, thread_id, "step.progress", {"status": "running", "kind": f"dispatcher.{job_id}.soft_stopped", "progress": {"resumable": True, "timeout_seconds": timeout_seconds or 0}}, revision_first=True)
        return DispatcherOutcome(job_id=job_id, run_id=run_id, status="resumable", thread_id=thread_id, revision=revision, summary={"phase": "soft_stopped"})

    def resume(self, job_id: str, bindings: DispatcherBindings, *, emit_events: bool = True) -> DispatcherOutcome:
        """Продолжение незавершённого потока, если привязки совпадают."""

        self._guard_migration()
        lease = self.store.lease(job_id)
        if lease is None:
            return DispatcherOutcome(
                job_id,
                "",
                "stale",
                "",
                self.store.revision()[0],
                {"phase": "stale"},
                {"code": "dispatcher.resume.missing", "message": "saved dispatcher run is unavailable", "action": job_id},
            )
        if lease.get("state") != "resumable":
            return DispatcherOutcome(
                job_id,
                str(lease.get("run_id", "")),
                "blocked",
                str(lease.get("thread_id", "")),
                self.store.revision()[0],
                {"current_state": lease.get("state")},
                {
                    "code": "dispatcher.resume.busy",
                    "message": "dispatcher run is not resumable",
                    "action": job_id,
                },
            )
        saved_value = _nested(lease, "summary").get("bindings")
        if not isinstance(saved_value, dict):
            thread_id = bindings.thread_id()
            saved_bindings = bindings
        else:
            saved = _object(saved_value)
            saved_bindings = DispatcherBindings(
                **{key: _text(saved.get(key)) for key in DispatcherBindings.__dataclass_fields__}
            )
            thread_id = saved_bindings.thread_id()
        subject_fields = ("project_id", "job_id", "work_unit_id", "source_generation_id", "diff_generation_id", "canonical_generation_id")
        if lease["thread_id"] != thread_id or any(getattr(saved_bindings, key) != getattr(bindings, key) for key in subject_fields):
            return self.mark_stale(job_id, emit_events=emit_events)
        if is_stale(_text(lease.get("renewed_at"))):
            return self.mark_stale(job_id, emit_events=emit_events)
        run_id = _text(lease.get("run_id")) or _text(_nested(lease, "summary").get("run_id"))
        run_snapshot = self.event_store.run_snapshot(run_id)
        try:
            if run_snapshot is None:
                raise RuntimeError("execution snapshot is missing or corrupt")
            snapshot = _object(run_snapshot["execution_snapshot"])
            if run_snapshot["execution_snapshot_fingerprint"] != lease.get("execution_snapshot_fingerprint"):
                raise RuntimeError("execution snapshot fingerprint differs from the lease")
            if snapshot.get("application_version") != "one-c-autoresearch/0.2":
                raise RuntimeError("workflow_contract_stale")
            if snapshot.get("schema_version") == "1":
                raise RuntimeError("legacy_execution_snapshot_requires_current_policy_retry")
            from .agents import validate_execution_snapshot
            validate_execution_snapshot(self.repo, snapshot)
            snap_work = _object(snapshot.get("work_unit"))
            if str(snap_work.get("id", "")) != saved_bindings.work_unit_id:
                raise RuntimeError("execution snapshot work unit differs from the lease")
        except (KeyError, RuntimeError, ValueError) as exc:
            outcome = self.mark_stale(job_id, emit_events=emit_events)
            outcome.blocker = {
                "code": (
                    "dispatcher.snapshot.legacy_restart_required"
                    if str(exc) == "legacy_execution_snapshot_requires_current_policy_retry"
                    else "workflow_contract_stale"
                    if str(exc) == "workflow_contract_stale"
                    else "dispatcher.snapshot.stale"
                ),
                "message": str(exc),
                "action": "retry-current-policy"
                if str(exc) == "legacy_execution_snapshot_requires_current_policy_retry"
                else job_id,
            }
            return outcome
        lease_token = self.store.resume_lease(
            job_id,
            thread_id,
            str(lease["lease_token"]),
            self.actor,
            process_identity(),
        )
        if lease_token is None:
            return DispatcherOutcome(
                job_id,
                run_id,
                "blocked",
                thread_id,
                self.store.revision()[0],
                {"phase": "resume_race"},
                {
                    "code": "dispatcher.resume.busy",
                    "message": "another process resumed the dispatcher run",
                    "action": job_id,
                },
            )
        with self._lock:
            self._lease_tokens[job_id] = lease_token
            stop_event = threading.Event()
            self._stop_events[job_id] = stop_event
            renewer = LeaseRenewer(
                self.store,
                job_id,
                lease_token,
                stop_event,
                on_renewed=lambda: self._emit(
                    emit_events,
                    run_id,
                    job_id,
                    thread_id,
                    "step.progress",
                    {"status": "running", "kind": f"dispatcher.{job_id}.lease_renewed", "progress": {"lease_renewed": True}},
                ),
            )
            renewer.start()
            self._renewers[job_id] = renewer
        revision = self._emit(emit_events, run_id, job_id, thread_id, "step.action", {"status": "running", "kind": f"dispatcher.{job_id}.resumed", "effective_action": {"graph": job_id, "thread_id": thread_id}}, revision_first=True)
        _ = self.store.renew_lease(job_id, lease_token, state="running", summary={"phase": "resumed", "run_id": run_id, "bindings": saved_bindings.canonical_payload(), "execution_snapshot_fingerprint": saved_bindings.execution_snapshot_fingerprint})
        return DispatcherOutcome(job_id=job_id, run_id=run_id, status="running", thread_id=thread_id, revision=revision, summary={"phase": "resumed", "bindings": saved_bindings.canonical_payload(), "execution_snapshot_fingerprint": saved_bindings.execution_snapshot_fingerprint})

    def cancel(self, job_id: str, *, emit_events: bool = True) -> DispatcherOutcome:
        """Явная отмена: останавливает график и очищает операционные предложения."""

        self._guard_migration()
        with self._lock:
            stop_event = self._stop_events.pop(job_id, None)
            if stop_event is not None:
                stop_event.set()
            renewer = self._renewers.pop(job_id, None)
        if renewer is not None:
            renewer.join(timeout=2)
        worker = self._workers.get(job_id)
        if worker is not None:
            worker.join(timeout=2)
        lease = self.store.lease(job_id)
        if lease is None:
            return DispatcherOutcome(job_id, "", "cancelled", "", self.store.revision()[0], {"phase": "cancelled"})
        thread_id = _text(lease.get("thread_id"))
        run_id = _text(_nested(lease, "summary").get("run_id")) or str(uuid.uuid4())
        token = self._lease_tokens.pop(job_id, None) or _text(lease.get("lease_token"))
        _ = self.store.cancel_run_work(job_id, run_id, token)
        _ = self.store.reconcile_invocation_outbox(self.event_store)
        revision = self._emit(emit_events, run_id, job_id, thread_id, "step.finished", {"status": "cancelled", "kind": f"dispatcher.{job_id}.cancelled", "exit": {"reason": "user_cancelled"}, "duration_seconds": 0.0}, revision_first=True, lease_token=token)
        if emit_events:
            run = self.event_store.run_snapshot(run_id) or {}
            with self.store.lease_guard(job_id, token, thread_id):
                _ = self.event_store.emit(
                    "run.finished",
                    run_id,
                    {
                        "status": "cancelled",
                        "duration_seconds": 0.0,
                        "execution_snapshot_fingerprint": run.get("execution_snapshot_fingerprint", ""),
                        "policy_source": _object(run.get("execution_snapshot")).get("policy_source", "current-policy"),
                        "tool_versions": {"codex": _object(run.get("execution_snapshot")).get("codex_version", "")},
                    },
                )
        if thread_id:
            _ = self.store.delete_thread(thread_id, job_id, token)
        _ = self.store.release_lease(job_id, token)
        outcome = DispatcherOutcome(job_id=job_id, run_id=run_id, status="cancelled", thread_id=thread_id, revision=revision, summary={"phase": "cancelled"})
        if retry_record := self.store.retry_run(run_id):
            _ = self.store.update_retry(
                run_id,
                _text(retry_record.get("owner_token")),
                "terminal",
                {"job_id": job_id, "action": "retry", "outcome": {
                    "status": outcome.status,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "revision": revision,
                    "summary": outcome.summary,
                    "blocker": None,
                }},
            )
        return outcome

    def retry(self, job_id: str, bindings: DispatcherBindings, *, run_id: str | None = None, execution_snapshot: dict[str, JsonValue] | None = None, emit_events: bool = True) -> DispatcherOutcome:
        """Явный повтор после сбоя: освобождает аренду и запускает заново."""

        self._guard_migration()
        lease = self.store.lease(job_id)
        predecessor_run_id = str((execution_snapshot or {}).get("predecessor_run_id", ""))
        source_thread_id = ""
        predecessor_terminal = False
        if predecessor_run_id:
            predecessor_file = self.event_store.run_snapshot(predecessor_run_id)
            predecessor_terminal = _text((predecessor_file or {}).get("status")) in {"completed", "cancelled"}
            if predecessor_file is not None and not predecessor_terminal:
                source_thread_id = replace(
                    bindings,
                    run_id=predecessor_run_id,
                    execution_snapshot_fingerprint=_text(predecessor_file.get("execution_snapshot_fingerprint")),
                ).thread_id()
            if lease is not None and str(lease.get("run_id", "")) == predecessor_run_id:
                source_thread_id = _text(lease.get("thread_id"))
        if lease is not None and lease.get("state") not in {"failed", "resumable", "stale"}:
            return DispatcherOutcome(
                job_id=job_id,
                run_id=run_id or str(uuid.uuid4()),
                status="blocked",
                thread_id=bindings.thread_id(),
                revision=self.store.revision()[0],
                summary={"current_state": lease.get("state")},
                blocker={"code": "dispatcher.retry.busy", "message": "graph is still running or blocked on approval; stop it first", "action": job_id},
            )
        if lease is not None:
            _ = self.store.interrupt_running_work(
                job_id,
                str(lease.get("run_id", "")),
                _text(lease.get("lease_token")),
            )
            _ = self.store.reconcile_invocation_outbox(self.event_store)
            _ = self.store.release_lease(job_id, _text(lease.get("lease_token")))
        outcome = self.start(job_id, bindings, run_id=run_id, execution_snapshot=execution_snapshot, emit_events=emit_events)
        if outcome.status == "running" and source_thread_id and not predecessor_terminal:
            _ = self.store.copy_compatible_results(
                source_thread_id,
                outcome.thread_id,
                job_id,
                self._lease_tokens[job_id],
            )
        return outcome

    def finish(self, job_id: str, status: str, summary: dict[str, JsonValue], *, emit_events: bool = True) -> DispatcherOutcome:
        """Фиксирует итоговую операционную сводку и удаляет поток."""

        if status not in {"completed", "failed", "blocked", "cancelled"}:
            raise ValueError(f"unsupported terminal dispatcher status: {status}")
        with self._lock:
            stop_event = self._stop_events.pop(job_id, None)
            if stop_event is not None:
                stop_event.set()
            renewer = self._renewers.pop(job_id, None)
        if renewer is not None:
            renewer.join(timeout=2)
        lease = self.store.lease(job_id)
        if lease is None:
            return DispatcherOutcome(job_id, "", status, "", self.store.revision()[0], summary)
        thread_id = _text(lease.get("thread_id"))
        run_id = _text(_nested(lease, "summary").get("run_id")) or str(uuid.uuid4())
        token = self._lease_tokens.get(job_id) or _text(lease.get("lease_token"))
        revision = self._emit(emit_events, run_id, job_id, thread_id, "step.finished", {"status": status, "kind": f"dispatcher.{job_id}.finished", "exit": {"summary": summary}, "duration_seconds": 0.0}, revision_first=True, lease_token=token)
        if emit_events:
            with self.store.lease_guard(job_id, token, thread_id):
                _ = self.event_store.emit(
                    "run.finished",
                    run_id,
                    {
                        "status": status,
                        "duration_seconds": 0.0,
                        "execution_snapshot_fingerprint": str((lease or {}).get("execution_snapshot_fingerprint", "")),
                        "policy_source": str(_nested(self.event_store.run_snapshot(run_id), "execution_snapshot").get("policy_source", "current-policy")),
                        "tool_versions": {
                            "codex": str(_nested(self.event_store.run_snapshot(run_id), "execution_snapshot").get("codex_version", ""))
                        },
                    },
                )
        if status in {"completed", "cancelled"} and thread_id:
            _ = self.store.delete_thread(thread_id, job_id, token)
        _ = self._lease_tokens.pop(job_id, None)
        _ = self.store.release_lease(job_id, token)
        outcome = DispatcherOutcome(job_id=job_id, run_id=run_id, status=status, thread_id=thread_id, revision=revision, summary=summary)
        if retry_record := self.store.retry_run(run_id):
            _ = self.store.update_retry(
                run_id,
                _text(retry_record.get("owner_token")),
                "terminal",
                {"job_id": job_id, "action": "retry", "outcome": {
                    "status": status,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "revision": revision,
                    "summary": summary,
                    "blocker": None,
                }},
            )
        return outcome

    def launch_graph(self, outcome: DispatcherOutcome, bindings: DispatcherBindings, profile: dict[str, JsonValue], *, phase_policies: dict[str, dict[str, JsonValue]] | None = None, profiles_by_role: dict[str, dict[str, JsonValue]] | None = None, timeout_seconds: int = 1800) -> None:
        """Запускает скомпилированный LangGraph после успешного захвата аренды."""

        if outcome.status != "running":
            return
        saved_value = outcome.summary.get("bindings")
        if isinstance(saved_value, dict):
            saved = _object(saved_value)
            bindings = DispatcherBindings(
                **{key: _text(saved.get(key)) for key in DispatcherBindings.__dataclass_fields__}
            )
        else:
            bindings = replace(
                bindings,
                run_id=outcome.run_id,
                execution_snapshot_fingerprint=str(outcome.summary.get("execution_snapshot_fingerprint", "")),
            )
        with self._lock:
            current = self._workers.get(outcome.job_id)
            if current is not None and current.is_alive():
                return
            worker = threading.Thread(
                target=self._run_graph,
                args=(outcome, bindings, profile, phase_policies or {}, profiles_by_role or {}, timeout_seconds),
                name=f"dispatcher-graph-{outcome.job_id}",
                daemon=True,
            )
            self._workers[outcome.job_id] = worker
            worker.start()

    def _record_graph_failure(self, outcome: DispatcherOutcome, blocker: dict[str, JsonValue]) -> None:
        token = self._lease_tokens[outcome.job_id]
        _ = self.store.cancel_queued_work(outcome.job_id, outcome.run_id, token)
        _ = self.store.renew_lease(
            outcome.job_id,
            token,
            state="failed",
            summary={"phase": "failed", "run_id": outcome.run_id, "blocker": blocker},
        )
        revision = self.emit_transition(
            outcome.job_id,
            outcome.run_id,
            outcome.thread_id,
            "step.finished",
            f"dispatcher.{outcome.job_id}.failed",
            {"status": "failed", "exit": {"blocker": blocker}, "duration_seconds": 0.0},
        )
        run = self.event_store.run_snapshot(outcome.run_id) or {}
        with self.store.lease_guard(outcome.job_id, token, outcome.thread_id):
            _ = self.event_store.emit(
                "run.finished",
                outcome.run_id,
                {
                    "status": "failed",
                    "duration_seconds": 0.0,
                    "execution_snapshot_fingerprint": run.get("execution_snapshot_fingerprint", ""),
                    "policy_source": _nested(run, "execution_snapshot").get("policy_source", "current-policy"),
                    "tool_versions": {"codex": _nested(run, "execution_snapshot").get("codex_version", "")},
                },
            )
        if retry_record := self.store.retry_run(outcome.run_id):
            _ = self.store.update_retry(
                outcome.run_id,
                _text(retry_record.get("owner_token")),
                "terminal",
                {
                    "job_id": outcome.job_id,
                    "action": "retry",
                    "outcome": {
                        "status": "failed",
                        "run_id": outcome.run_id,
                        "thread_id": outcome.thread_id,
                        "revision": revision,
                        "summary": {"phase": "failed", "blocker": blocker},
                        "blocker": blocker,
                    },
                },
            )
        self._stop_background(outcome.job_id)

    def _run_graph(self, outcome: DispatcherOutcome, bindings: DispatcherBindings, profile: dict[str, JsonValue], phase_policies: dict[str, dict[str, JsonValue]], profiles_by_role: dict[str, dict[str, JsonValue]], timeout_seconds: int) -> None:
        from .agents import (
            build_context_manifest,
            execute as agent_execute,
            prepare_context_envelope,
            validate_execution_snapshot,
        )
        from .pipeline_graphs import build_classify_state, build_decide_state, build_discover_state, compile_analyze_graph, compile_classify_graph, compile_consolidate_graph, compile_decide_graph

        stop_event = self._stop_events[outcome.job_id]
        run_file = self.event_store.run_snapshot(outcome.run_id)
        if run_file is None:
            raise RuntimeError("dispatcher execution snapshot is missing or corrupt")
        execution_snapshot = _object(run_file["execution_snapshot"])
        validate_execution_snapshot(self.repo, execution_snapshot)
        snapshot_phases = _objects(execution_snapshot.get("agent_phases"))
        snapshot_profiles = _profiles(execution_snapshot.get("profiles"))
        phase_policies = {
            _text(phase.get("phase_id")): phase for phase in snapshot_phases
        }
        profiles_by_role = {
            _text(role.get("role_id")): snapshot_profiles[_text(role.get("agent_profile"))]
            for phase in snapshot_phases
            for role in _objects(phase.get("roles"))
        }
        timeout_value = execution_snapshot.get("timeout_seconds")
        if not isinstance(timeout_value, int) or isinstance(timeout_value, bool):
            raise RuntimeError("dispatcher execution snapshot timeout is invalid")
        timeout_seconds = timeout_value
        primary_role = {"analyze-dif": "analyzer", "consolidate-mrq": "grouper", "classify-mrq": "classifier", "decide-mrq": "researcher"}[outcome.job_id]
        profile = profiles_by_role[primary_role]
        if not phase_policies:
            phase_policies = {
                "analyze-dif": {"max_concurrency": 4, "roles": [{"role_id": "analyzer", "count": 4}]},
                "form-mrq": {"max_concurrency": 4, "roles": [{"role_id": "coordinator", "count": 1}, {"role_id": "grouper", "count": 4}]},
                "research-target": {"max_concurrency": 4, "roles": [{"role_id": "researcher", "count": 4}]},
            }
        proposal_root = self.store.path.parent / "proposal-work"

        def execute(repo: Path, selected_profile: dict[str, JsonValue], operation: str, work_unit: dict[str, JsonValue], supplement: str, timeout: int, cancelled: Callable[[], bool]) -> dict[str, JsonValue]:
            if operation == "mrq.decide-next":
                phase_id, role_id = "research-target", "researcher"
            elif operation == "mrq.classify-batches":
                phase_id, role_id = "classify-batches", "classifier"
            elif operation == "mrq.consolidate" and work_unit.get("kind") in {
                "consolidation-coordinate",
                "consolidation-link-page",
                "consolidation-reduce-pair",
                "consolidation-noise-page",
            }:
                phase_id, role_id = "form-mrq", "coordinator"
            elif operation == "mrq.consolidate":
                phase_id, role_id = "form-mrq", "grouper"
            elif work_unit.get("kind") == "coordinate-groups":
                phase_id, role_id = "form-mrq", "coordinator"
            elif work_unit.get("kind") == "preliminary-group":
                phase_id, role_id = "form-mrq", "grouper"
            else:
                phase_id, role_id = "analyze-dif", "analyzer"
            policy = phase_policies[phase_id]
            roles = _objects(policy.get("roles"))
            configured_slots = next(
                _integer(role.get("count"))
                for role in roles
                if role.get("role_id") == role_id
            )
            context_manifest = build_context_manifest(repo, work_unit)
            profile_id = next(
                _text(role.get("agent_profile"))
                for role in roles
                if role.get("role_id") == role_id
            )
            try:
                prepared_context = prepare_context_envelope(
                    repo,
                    selected_profile,
                    operation,
                    phase_id,
                    role_id,
                    work_unit,
                    supplement,
                    execution_snapshot,
                    context_manifest,
                )
            except ValueError as exc:
                from .events import redact
                message = str(redact(str(exc)))
                _ = self.store.record_phase_context(
                    outcome.job_id,
                    outcome.run_id,
                    phase_id,
                    role_id,
                    str(work_unit.get("id", "")),
                    self._lease_tokens[outcome.job_id],
                    execution_kind="preflight_failed",
                    status="failed",
                    context_diagnostics={
                        "contract_version": "context-envelope/v1",
                        "error_code": (
                            "agent.context_capacity"
                            if "capacity" in message
                            else "agent.context_validation"
                        ),
                        "error_summary": message,
                    },
                )
                raise
            envelope = _object(prepared_context.get("envelope"))
            invocation = self.store.start_invocation(
                outcome.job_id,
                outcome.run_id,
                phase_id,
                role_id,
                str(work_unit.get("id", "")),
                configured_slots,
                self._lease_tokens[outcome.job_id],
                execution_snapshot_fingerprint=str(
                    run_file.get("execution_snapshot_fingerprint")
                ),
                profile_id=profile_id,
                context_manifest_fingerprint="sha256:"
                + sha256(canonical_json(context_manifest)),
                context_envelope_fingerprint=str(
                    envelope.get("envelope_fingerprint")
                ),
                prepared_input_fingerprint=str(
                    envelope.get("prepared_input_fingerprint")
                ),
                context_provenance=_objects(prepared_context.get("provenance")),
                context_diagnostics=_object(prepared_context.get("diagnostics")),
            )
            if invocation is None:
                raise RuntimeError("dispatcher lease or logical slot is unavailable")
            invocation_id = _text(invocation.get("invocation_id"))
            if not invocation_id:
                raise RuntimeError("dispatcher invocation id is missing")
            search_policy = _object(execution_snapshot.get("source_search_policies")).get(
                f"{phase_id}:{role_id}"
            )
            resolved_search_policy = _source_search_policy(search_policy) if search_policy else None
            search_capability = ""
            if resolved_search_policy:
                import secrets
                search_capability = secrets.token_hex(32)
                _ = self.store.configure_source_search(
                    invocation_id,
                    _object(resolved_search_policy),
                    "sha256:" + sha256(search_capability.encode()),
                )
            _ = self.store.reconcile_invocation_outbox(self.event_store)
            proposal_dir = proposal_root / sha256(canonical_json({"run_id": outcome.run_id, "operation": operation, "work_unit_id": work_unit.get("id"), "nonce": str(uuid.uuid4())}))
            try:
                result = agent_execute(
                    repo,
                    proposal_dir,
                    selected_profile,
                    operation,
                    work_unit,
                    supplement,
                    timeout,
                    cancelled,
                    execution_snapshot,
                    context_manifest,
                    prepared_context,
                    invocation_id,
                    search_capability,
                    self.store.base,
                )
                if resolved_search_policy:
                    _ = self.store.complete_source_search(invocation_id)
                    ledger = _source_search_ledger(self.store.source_search_ledger(
                        invocation_id, limit=100
                    ))
                    if ledger["items"]:
                        from .source_search import (
                            revalidate_proposal_evidence,
                            revalidate_reuse_environment,
                            reuse_binding,
                        )
                        from .source_search_bridge import hmac_key
                        revalidate_reuse_environment(
                            repo, resolved_search_policy, ledger,
                        )
                        work_unit["dynamic_search_binding"] = json_object(reuse_binding(
                            resolved_search_policy,
                            ledger,
                            revalidate_proposal_evidence(
                                repo, resolved_search_policy, result
                            ),
                            hmac_key(self.store.path.parent)[0],
                        ))
            except InterruptedError:
                if resolved_search_policy:
                    _ = self.store.close_source_search(invocation_id, "cancelled")
                _ = self.store.terminalize_invocation(
                    invocation_id,
                    "cancelled",
                    self._lease_tokens[outcome.job_id],
                    error_code="cancelled",
                    error_summary="agent invocation was cancelled",
                )
                _ = self.store.reconcile_invocation_outbox(self.event_store)
                raise
            except Exception as exc:
                if resolved_search_policy:
                    _ = self.store.close_source_search(invocation_id, "interrupted")
                from .events import redact
                message = str(redact(str(exc)))
                lowered = message.lower()
                error_code = (
                    "provider_blocked"
                    if "cloudflare" in lowered or "blocked" in lowered
                    else "provider_timeout"
                    if isinstance(exc, TimeoutError) or "timeout" in lowered
                    else "validation_error"
                    if isinstance(exc, ValueError)
                    else "internal_error"
                )
                _ = self.store.terminalize_invocation(
                    invocation_id,
                    "failed",
                    self._lease_tokens[outcome.job_id],
                    error_code=error_code,
                    error_summary=message,
                )
                _ = self.store.reconcile_invocation_outbox(self.event_store)
                raise
            work_unit_id = str(work_unit.get("id", ""))
            result_name = (
                "form-mrq:coordinate"
                if role_id == "coordinator"
                else f"form-mrq:{work_unit_id}"
                if role_id == "grouper"
                else f"classify-batches:{work_unit_id}"
                if role_id == "classifier"
                else f"research-target:{work_unit_id}"
                if role_id == "researcher"
                else f"analyze-dif:{work_unit_id}"
            )
            result_ref = sha256(
                canonical_json(
                    {"thread_id": outcome.thread_id, "node_result": result_name}
                )
            )
            if not self.store.terminalize_invocation(
                invocation_id,
                "completed",
                self._lease_tokens[outcome.job_id],
                result_ref=result_ref,
            ):
                raise RuntimeError("dispatcher lease was fenced before invocation completion")
            _ = self.store.reconcile_invocation_outbox(self.event_store)
            return result

        def result_key(name: str) -> str:
            return sha256(canonical_json({"thread_id": outcome.thread_id, "node_result": name}))

        def load_result(name: str) -> dict[str, JsonValue] | None:
            row = self.store.proposal(result_key(name))
            if row is None:
                return None
            payload = _object(row.get("payload"))
            return _object(payload.get("envelope")) if payload.get("name") == name else payload

        def save_result(name: str, payload: dict[str, JsonValue]) -> None:
            if not self.store.save_proposal(result_key(name), outcome.job_id, outcome.thread_id, "node-result", {"name": name, "envelope": payload}, self._lease_tokens[outcome.job_id]):
                raise RuntimeError("dispatcher lease was fenced before result persistence")

        def register_work(phase_id: str, role_id: str, work_unit_ids: list[str]) -> None:
            if not self.store.register_phase_work(
                outcome.job_id,
                outcome.run_id,
                phase_id,
                role_id,
                work_unit_ids,
                self._lease_tokens[outcome.job_id],
            ):
                raise RuntimeError("dispatcher lease was fenced before phase work derivation")

        def reuse_work(phase_id: str, role_id: str, work_unit_id: str) -> None:
            result_name = (
                "form-mrq:coordinate"
                if role_id == "coordinator"
                else f"research-target:{work_unit_id}"
                if role_id == "researcher"
                else f"classify-batches:{work_unit_id}"
                if role_id == "classifier"
                else f"form-mrq:{work_unit_id}"
                if role_id == "grouper"
                else f"analyze-dif:{work_unit_id}"
            )
            if not self.store.complete_reused_work(
                outcome.job_id,
                outcome.run_id,
                phase_id,
                role_id,
                work_unit_id,
                self._lease_tokens[outcome.job_id],
                source_result_ref=result_key(result_name),
                context_diagnostics={
                    "contract_version": "context-envelope/v1",
                    "reuse_status": "compatible",
                },
            ):
                raise RuntimeError("dispatcher lease was fenced before compatible result reuse")

        def record_work(
            phase_id: str,
            role_id: str,
            work_unit_id: str,
            execution_kind: str,
            status: str,
            diagnostics: dict[str, JsonValue],
        ) -> None:
            if not self.store.record_phase_context(
                outcome.job_id,
                outcome.run_id,
                phase_id,
                role_id,
                work_unit_id,
                self._lease_tokens[outcome.job_id],
                execution_kind=execution_kind,
                status=status,
                context_diagnostics=diagnostics,
            ):
                raise RuntimeError("dispatcher lease was fenced before phase context persistence")

        saver = self.store.fenced_saver(
            outcome.job_id, self._lease_tokens[outcome.job_id], outcome.thread_id
        )
        bindings_check = lambda: self.verify_bindings(outcome.job_id, bindings)
        required_reuse_origin: dict[str, str] | None = None
        if execution_snapshot.get("policy_source") == "reuse-snapshot":
            predecessor_run_id = str(execution_snapshot.get("predecessor_run_id", ""))
            predecessor = self.event_store.run_snapshot(predecessor_run_id)
            if predecessor is None:
                raise RuntimeError("reuse-snapshot predecessor is missing or corrupt")
            required_reuse_origin = {
                "source_run_id": predecessor_run_id,
                "source_execution_snapshot_fingerprint": str(predecessor["execution_snapshot_fingerprint"]),
            }
        try:
            if outcome.job_id == "analyze-dif":
                graph = compile_analyze_graph(
                    repo=self.repo, saver=saver, executor=execute, profile=profile,
                    supplement=bindings.instruction_supplement,
                    timeout_seconds=timeout_seconds, cancelled=stop_event.is_set,
                    bindings_check=bindings_check, load_result=load_result,
                    save_result=save_result, register_work=register_work,
                    reuse_work=reuse_work, record_work=record_work,
                    phase_policies=phase_policies or None,
                    profiles_by_role=profiles_by_role or None,
                )
                initial = build_discover_state(bindings.canonical_payload(), outcome.run_id, outcome.thread_id)
                if required_reuse_origin is not None:
                    initial["required_reuse_origin"] = required_reuse_origin
                state = _object(graph.invoke(initial, config={"configurable": {"thread_id": outcome.thread_id}}))
            elif outcome.job_id == "consolidate-mrq":
                graph = compile_consolidate_graph(
                    repo=self.repo, saver=saver, executor=execute, profile=profile,
                    supplement=bindings.instruction_supplement,
                    timeout_seconds=timeout_seconds, cancelled=stop_event.is_set,
                    bindings_check=bindings_check, register_work=register_work,
                    phase_policies=phase_policies or None,
                    profiles_by_role=profiles_by_role or None,
                    plan_root=self.store.path.parent,
                )
                initial = build_discover_state(bindings.canonical_payload(), outcome.run_id, outcome.thread_id)
                if required_reuse_origin is not None:
                    initial["required_reuse_origin"] = required_reuse_origin
                state = _object(graph.invoke(initial, config={"configurable": {"thread_id": outcome.thread_id}}))
            elif outcome.job_id == "classify-mrq":
                graph = compile_classify_graph(
                    repo=self.repo, saver=saver, executor=execute, profile=profile,
                    supplement=bindings.instruction_supplement,
                    timeout_seconds=timeout_seconds, cancelled=stop_event.is_set,
                    bindings_check=bindings_check, load_result=load_result,
                    save_result=save_result, register_work=register_work,
                    reuse_work=reuse_work, record_work=record_work,
                    phase_policy=phase_policies.get("classify-batches") or None,
                    profiles_by_role=profiles_by_role or None,
                )
                initial = build_classify_state(bindings.canonical_payload(), outcome.run_id, outcome.thread_id)
                state = _object(graph.invoke(initial, config={"configurable": {"thread_id": outcome.thread_id}}))
            else:
                graph = compile_decide_graph(
                    repo=self.repo, saver=saver, executor=execute, profile=profile,
                    supplement=bindings.instruction_supplement,
                    timeout_seconds=timeout_seconds, cancelled=stop_event.is_set,
                    bindings_check=bindings_check, load_result=load_result,
                    save_result=save_result, register_work=register_work,
                    reuse_work=reuse_work, record_work=record_work,
                    phase_policy=phase_policies.get("research-target") or None,
                    profiles_by_role=profiles_by_role or None,
                )
                from .consolidation import load_active as load_consolidation
                from .decision_generations import validate_generation as validate_decisions
                from .mrq_batches import load_active
                consolidated = _object(load_consolidation(self.repo))
                rows = _objects(_nested(consolidated, "mrq").get("mrq.jsonl"))
                decision_id = _nested(consolidated, "pointer").get("decision_generation_id")
                decision_generation = _object(validate_decisions(self.repo, _text(decision_id))) if decision_id else {}
                decided: set[str] = {
                    _text(item.get("mrq_id"))
                    for item in _objects(decision_generation.get("decisions.jsonl"))
                } if decision_id else set()
                pending = {
                    _text(item.get("mrq_id"))
                    for item in rows
                    if _text(item.get("mrq_id")) not in decided
                }
                batches = load_active(self.repo)
                batch = next((item for item in batches if bindings.work_unit_id in item.mrq_ids), None)
                if batch is None:
                    batch = next((item for item in batches if pending.intersection(item.mrq_ids)), None)
                initial = build_decide_state(bindings.canonical_payload(), outcome.run_id, outcome.thread_id, batch=batch)
                if required_reuse_origin is not None:
                    initial["required_reuse_origin"] = required_reuse_origin
                state = _object(graph.invoke(initial, config={"configurable": {"thread_id": outcome.thread_id}}))
            status = str(state.get("status", "failed"))
            blocker = state.get("blocker")
            if status == "blocked":
                proposals: list[tuple[str, dict[str, JsonValue]]] = []
                if outcome.job_id == "consolidate-mrq" and state.get("consolidation_plan"):
                    saved_plan = _object(state.get("consolidation_plan"))
                    key = sha256(canonical_json({"thread_id": outcome.thread_id, "plan_fingerprint": saved_plan.get("plan_fingerprint", "")}))
                    proposals.append((key, saved_plan))
                elif outcome.job_id == "decide-mrq":
                    for mrq_id, finding_value in sorted(_object(state.get("findings")).items()):
                        findings = _objects(finding_value)
                        decision = _object(findings[-1].get("proposal")) if findings else None
                        if decision:
                            key = sha256(canonical_json({"thread_id": outcome.thread_id, "mrq_id": mrq_id, "kind": "decision-approval"}))
                            proposals.append((key, {"decision_proposal": decision, "mrq_id": mrq_id}))
                else:
                    noise_ids = _strings(state.get("noise_review_ids", []))
                    blocker_object = _object(blocker) if blocker else {}
                    if blocker_object.get("code") == "approval.noise":
                        key = sha256(canonical_json({"thread_id": outcome.thread_id, "kind": "noise-approval"}))
                        analyzed = _object(state.get("analyzed"))
                        proposals.append(
                            (
                                key,
                                {
                                    "approval_stage": "noise",
                                    "noise_proposals": [
                                        {
                                            "stable_diff_id": identifier,
                                            "rationale": _nested(analyzed, identifier).get("rationale", ""),
                                            "evidence": _nested(analyzed, identifier).get("evidence", []),
                                        }
                                        for identifier in noise_ids
                                    ],
                                },
                            )
                        )
                    else:
                        key = sha256(canonical_json({"thread_id": outcome.thread_id, "kind": "batch-approval"}))
                        proposals.append(
                            (
                                key,
                                {
                                    "approval_stage": "batch",
                                    "batch_proposals": state.get("batch_proposals", []),
                                    "approved_noise": state.get("approved_noise", []),
                                    "approved_noise_ids": state.get("approved_noise_ids", []),
                                },
                            )
                        )
                for key, payload in proposals:
                    if not self.store.save_proposal(key, outcome.job_id, outcome.thread_id, "approval", payload, self._lease_tokens[outcome.job_id]):
                        raise RuntimeError("dispatcher lease was fenced before proposal persistence")
                proposal_keys = [key for key, _payload in proposals]
                _ = self.store.renew_lease(outcome.job_id, self._lease_tokens[outcome.job_id], state="blocked", summary={"phase": "approval_required", "run_id": outcome.run_id, "proposal_keys": proposal_keys})
                _ = self.emit_transition(outcome.job_id, outcome.run_id, outcome.thread_id, "approval.required", f"dispatcher.{outcome.job_id}.approval_required", {"status": "blocked", "blocker": blocker, "proposal_keys": proposal_keys})
            elif status == "resumable":
                _ = self.store.renew_lease(outcome.job_id, self._lease_tokens[outcome.job_id], state="resumable", summary={"phase": "soft_stopped", "run_id": outcome.run_id})
            elif status == "stale":
                _ = self.mark_stale(outcome.job_id)
            elif status == "completed":
                if outcome.job_id == "classify-mrq" and state.get("published"):
                    _ = self.emit_transition(
                        outcome.job_id,
                        outcome.run_id,
                        outcome.thread_id,
                        "step.output",
                        "dispatcher.classify-mrq.published",
                        {
                            "status": "completed",
                            "outputs": {
                                "batch_generation": state.get("batch_generation"),
                                "batch_count": len(_objects(state.get("batches", []))),
                            },
                        },
                    )
                _ = self.finish(outcome.job_id, "completed", {"phase": "completed"})
            else:
                failure: dict[str, JsonValue] = (
                    _object(blocker)
                    if isinstance(blocker, Mapping)
                    else {"code": "dispatcher.graph.failed", "message": "dispatcher graph failed", "action": outcome.job_id}
                )
                self._record_graph_failure(outcome, failure)
        except Exception as exc:
            exception_failure: dict[str, JsonValue] = {
                "code": "dispatcher.graph.failed", "message": str(exc), "action": outcome.job_id
            }
            self._record_graph_failure(outcome, exception_failure)
        finally:
            with self._lock:
                _ = self._workers.pop(outcome.job_id, None)

    # -- проверка привязок перед эффектом ------------------------------

    def verify_bindings(self, job_id: str, bindings: DispatcherBindings) -> bool:
        """Перед агентным вызовом или эффектом сверяет сохранённые привязки."""

        lease = self.store.lease(job_id)
        if lease is None:
            return False
        token = self._lease_tokens.get(job_id)
        if not token or lease.get("lease_token") != token:
            return False
        if _text(lease.get("thread_id")) != bindings.thread_id():
            return False
        if is_stale(_text(lease.get("renewed_at"))):
            return False
        try:
            from .stage_recompute import active_state
            pointers, _workflow_fingerprint = active_state(self.repo)
            classification = parse_json_object((self.repo / "research/active-dif-classification-generation.json").read_text(encoding="utf-8")) if (self.repo / "research/active-dif-classification-generation.json").is_file() else {}
            consolidation = parse_json_object((self.repo / "research/active-consolidation-generation.json").read_text(encoding="utf-8")) if (self.repo / "research/active-consolidation-generation.json").is_file() else {}
            source = _object(pointers.get("source") or {})
            diff = _object(pointers.get("diff") or {})
            if (
                str(source.get("generation_id", "")) != bindings.source_generation_id
                or str(diff.get("generation_id", "")) != bindings.diff_generation_id
                or str(consolidation.get("mrq_generation_id") or "") != bindings.canonical_generation_id
            ):
                return False
            if (
                job_id != "analyze-dif"
                and str(classification.get("generation_id") or "") != bindings.classification_generation_id
            ) or str(consolidation.get("transaction_id") or "") != bindings.consolidation_transaction_id:
                return False
            run_file = self.event_store.run_snapshot(bindings.run_id)
            if run_file is None or run_file.get("execution_snapshot_fingerprint") != bindings.execution_snapshot_fingerprint:
                return False
        except (OSError, ValueError, KeyError):
            return False
        return True

    def mark_stale(self, job_id: str, *, emit_events: bool = True) -> DispatcherOutcome:
        """Помечает запуск устаревшим после изменения поколений.

        Эффект не применяется; пользователю нужно явно перезапустить график.
        """

        lease = self.store.lease(job_id)
        if lease is None:
            return DispatcherOutcome(job_id, "", "stale", "", self.store.revision()[0], {"phase": "stale"})
        thread_id = _text(lease.get("thread_id"))
        run_id = _text(_nested(lease, "summary").get("run_id")) or str(uuid.uuid4())
        token = self._lease_tokens.get(job_id) or _text(lease.get("lease_token"))
        revision = self._emit(emit_events, run_id, job_id, thread_id, "approval.required", {"status": "blocked", "kind": "dispatcher.bindings.stale", "blocker": {"code": "dispatcher.bindings.stale", "message": "active generations changed during the run; restart the graph", "action": job_id}}, revision_first=True, lease_token=token)
        if emit_events:
            run = self.event_store.run_snapshot(run_id) or {}
            with self.store.lease_guard(job_id, token, thread_id):
                _ = self.event_store.emit(
                    "run.finished",
                    run_id,
                    {
                        "status": "interrupted",
                        "duration_seconds": 0.0,
                        "execution_snapshot_fingerprint": run.get("execution_snapshot_fingerprint", ""),
                        "policy_source": _nested(run, "execution_snapshot").get("policy_source", "current-policy"),
                        "tool_versions": {"codex": _nested(run, "execution_snapshot").get("codex_version", "")},
                        "error_class": "dispatcher_bindings_stale",
                    },
                )
        self._stop_background(job_id)
        _ = self.store.interrupt_running_work(job_id, run_id, token)
        _ = self.store.reconcile_invocation_outbox(self.event_store)
        if thread_id:
            _ = self.store.delete_thread(thread_id, job_id, token, preserve_results=True)
        _ = self._lease_tokens.pop(job_id, None)
        _ = self.store.release_lease(job_id, token)
        outcome = DispatcherOutcome(
            job_id=job_id,
            run_id=run_id,
            status="stale",
            thread_id=thread_id,
            revision=revision,
            summary={"phase": "stale"},
            blocker={
                "code": "dispatcher.bindings.stale",
                "message": "active generations changed during the run; restart the graph",
                "action": job_id,
            },
        )
        if retry_record := self.store.retry_run(run_id):
            _ = self.store.update_retry(
                run_id,
                _text(retry_record.get("owner_token")),
                "terminal",
                {"job_id": job_id, "action": "retry", "outcome": {
                    "status": outcome.status,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "revision": revision,
                    "summary": outcome.summary,
                    "blocker": outcome.blocker,
                }},
            )
        return outcome

    def _stop_background(self, job_id: str) -> None:
        """Останавливает аренду после терминального исхода, не трогая чекпойнт сбоя."""

        with self._lock:
            stop_event = self._stop_events.pop(job_id, None)
            if stop_event is not None:
                stop_event.set()
            renewer = self._renewers.pop(job_id, None)
        if renewer is not None:
            renewer.join(timeout=2)

    # -- события и проекция -------------------------------------------

    def _emit(
        self,
        emit_events: bool,
        run_id: str,
        job_id: str,
        thread_id: str,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        revision_first: bool = True,
        lease_token: str | None = None,
    ) -> int:
        """Сначала фиксирует ревизию, потом выпускает событие.

        Существующие типы ``workflow-event/v1`` не расширяются: переходы
        диспетчера кодируются полем ``kind`` внутри payload.
        """

        if not emit_events:
            return self.store.revision()[0]
        token = lease_token or self._lease_tokens.get(job_id)
        if not token:
            raise RuntimeError("dispatcher event requires an active lease token")
        with self.store.lease_guard(job_id, token, thread_id):
            if revision_first:
                revision = self.store.bump_revision_if_lease(job_id, token)
                if revision is None:
                    raise RuntimeError("dispatcher lease was fenced before revision persistence")
                payload = {**payload, "revision": revision}
            else:
                revision_value = payload.get("revision")
                revision = (
                    revision_value
                    if isinstance(revision_value, int) and not isinstance(revision_value, bool)
                    else self.store.bump_revision_if_lease(job_id, token)
                )
                if revision is None:
                    raise RuntimeError("dispatcher lease was fenced before revision persistence")
            payload = self._fill_required_payload(event_type, job_id, payload)
            _ = self.event_store.emit(event_type, run_id, payload, job_id=job_id, step_id=f"dispatcher-{job_id}", attempt=1)
        return revision

    def emit_transition(self, job_id: str, run_id: str, thread_id: str, event_type: str, kind: str, body: dict[str, JsonValue]) -> int:
        """Публичный помощник для операционных переходов внутри графа."""

        if event_type not in {"step.progress", "step.action", "step.output", "step.validation", "step.finished", "log.append", "approval.required"}:
            raise ValueError(f"unsupported existing workflow event type: {event_type}")
        lease = self.store.lease(job_id) or {}
        token = self._lease_tokens.get(job_id) or str(lease.get("lease_token", ""))
        return self._emit(
            True,
            run_id,
            job_id,
            thread_id,
            event_type,
            {
                "status": body.get("status", "running"),
                "kind": kind,
                **{key: value for key, value in body.items() if key != "status"},
            },
            revision_first=True,
            lease_token=token,
        )

    def _fill_required_payload(self, event_type: str, job_id: str, payload: dict[str, JsonValue], body: dict[str, JsonValue] | None = None) -> dict[str, JsonValue]:
        """Дополняет payload обязательными полями существующих типов событий.

        Требования ``REQUIRED_PAYLOAD`` в :mod:`events` учитываются здесь, чтобы
        не расширять сам каталог типов: ``actor`` и ``operation`` подставляются
        из контекста диспетчера, а специфичные поля (``progress``, ``outputs``,
        ``validation``, ``log``, ``blocker`` и т. д.) — из тела перехода.
        """

        body = body or {}
        result = dict(payload)
        _ = result.setdefault("actor", self.actor)
        _ = result.setdefault("operation", job_id)
        _ = result.setdefault("work_unit_id", None)
        if event_type == "step.started":
            _ = result.setdefault("inputs", {})
            _ = result.setdefault("input_fingerprint", "sha256:" + "0" * 64)
        if event_type == "step.progress" and "progress" not in result:
            result["progress"] = body.get("progress", {"kind": result.get("kind", "dispatcher.transition")})
        if event_type == "step.action" and "effective_action" not in result:
            result["effective_action"] = body.get("effective_action", {"kind": result.get("kind", "dispatcher.transition")})
        if event_type == "step.output":
            _ = result.setdefault("outputs", body.get("outputs", {}))
            _ = result.setdefault("output_fingerprint", sha256(canonical_json(result["outputs"])))
        if event_type == "step.validation":
            _ = result.setdefault("validation", body.get("validation", {}))
        if event_type == "step.finished":
            _ = result.setdefault("exit", body.get("exit", {"status": result.get("status", "completed")}))
            _ = result.setdefault("duration_seconds", _number(body.get("duration_seconds", 0.0)))
        if event_type == "log.append" and "log" not in result:
            result["log"] = body.get("log", {})
        if event_type == "approval.required":
            _ = result.setdefault("blocker", body.get("blocker", {"code": "dispatcher.approval", "message": "dispatcher transition requires approval", "action": job_id}))
        return result

    def snapshot_projection(self) -> dict[str, JsonValue]:
        """Собирает операционную проекцию из канонического снимка и SQLite.

        Возвращаемая структура присоединяется к существующему снимку под ключом
        ``dispatcher`` и НЕ участвует в отпечатках, ``status`` и ``doctor``.
        """

        revision, updated_at = self.store.revision()
        leases = self.store.leases()
        return {
            "schema_version": "2",
            "revision": revision,
            "fresh_at": updated_at,
            "jobs": {_text(lease.get("job_id")): lease for lease in leases},
        }

    def close(self) -> None:
        with self._lock:
            for stop_event in self._stop_events.values():
                stop_event.set()
            renewers = list(self._renewers.values())
            workers = list(self._workers.values())
            self._stop_events.clear()
            self._renewers.clear()
            self._workers.clear()
        for renewer in renewers:
            renewer.join(timeout=2)
        for worker in workers:
            worker.join(timeout=2)


def coordinator_for(repo: Path, project_id: str, event_store: EventStore, *, base: Path | None = None, actor: str = "local-user") -> tuple[DispatcherCoordinator, DispatcherStore]:
    """Открывает store и возвращает готовый координатор.

    Контекстный менеджер store-а остаётся открытым; вызывающий ответственен за
    закрытие через ``coordinator.store.close()``.
    """

    store = DispatcherStore(repo, base)
    _ = store.open()
    return DispatcherCoordinator(repo, project_id, store, event_store, actor=actor), store
