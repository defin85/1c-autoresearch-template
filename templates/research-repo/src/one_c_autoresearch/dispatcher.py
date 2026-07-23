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
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .contracts import canonical_json, sha256
from .events import EventStore, process_identity
from .sqlite_state import (
    LEASE_EXPIRY_SECONDS,
    LEASE_RENEWAL_SECONDS,
    DispatcherStore,
    is_stale,
    open_store,
)


DISPATCHER_JOBS = ("discover-mrq", "decide-mrq")
LEASE_RENEWAL_SECONDS_EFFECTIVE = LEASE_RENEWAL_SECONDS
STALE_AFTER_SECONDS = 10  # UI-порог несвежего активного запуска (spec.md)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
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

    def thread_id(self) -> str:
        preimage = {
            "schema_version": "1",
            "project_id": self.project_id,
            "job_id": self.job_id,
            "work_unit_id": self.work_unit_id,
            "source_generation_id": self.source_generation_id,
            "diff_generation_id": self.diff_generation_id,
            "canonical_generation_id": self.canonical_generation_id,
            "workflow_fingerprint": self.workflow_fingerprint,
            "agent_profile_fingerprint": self.agent_profile_fingerprint,
            "instruction_supplement": self.instruction_supplement,
        }
        return sha256(canonical_json(preimage))

    def canonical_payload(self) -> dict[str, Any]:
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
            "thread_id": self.thread_id(),
        }


def load_bindings(repo: Path, project_id: str, job_id: str, work_unit_id: str, agent_profile: dict | None, instruction_supplement: str) -> DispatcherBindings:
    """Собирает привязки из активного состояния репозитория и профиля агента."""

    from .stage_recompute import active_state
    pointers, workflow_fingerprint = active_state(repo)
    return DispatcherBindings(
        project_id=project_id,
        job_id=job_id,
        work_unit_id=work_unit_id,
        source_generation_id=str((pointers.get("source") or {}).get("generation_id", "")),
        diff_generation_id=str((pointers.get("diff") or {}).get("generation_id", "")),
        canonical_generation_id=str((pointers.get("mrq") or {}).get("canonical_generation_id", "")),
        workflow_fingerprint=workflow_fingerprint,
        agent_profile_fingerprint=sha256(canonical_json(agent_profile)) if agent_profile else "",
        instruction_supplement=instruction_supplement,
    )


@dataclass
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
    summary: dict[str, Any]
    blocker: dict[str, Any] | None = None


class LeaseRenewer:
    """Фоновый поток, обновляющий аренду независимо от агентных вызовов."""

    def __init__(self, store: DispatcherStore, job_id: str, stop_event: threading.Event, *, interval_seconds: int = LEASE_RENEWAL_SECONDS_EFFECTIVE, on_renewed: Callable[[], None] | None = None) -> None:
        self.store = store
        self.job_id = job_id
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
                renewed = self.store.renew_lease(self.job_id)
            except Exception:
                renewed = False
            if not renewed:
                return
            if self.on_renewed is not None:
                try:
                    self.on_renewed()
                except Exception:
                    # Подтверждение наблюдаемости не должно прекращать аренду.
                    pass


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
        self._lock = threading.RLock()

    # -- запуск и жизненный цикл ---------------------------------------

    def start(self, job_id: str, bindings: DispatcherBindings, *, emit_events: bool = True) -> DispatcherOutcome:
        """Запускает график, если аренда свободна. Иначе возвращает ``blocked``."""

        if job_id not in DISPATCHER_JOBS:
            raise ValueError(f"unsupported dispatcher job: {job_id}")
        thread_id = bindings.thread_id()
        run_id = str(uuid.uuid4())
        with self._lock:
            acquired = self.store.acquire_lease(job_id, thread_id, bindings.work_unit_id, self.actor, process_identity(), state="running", summary={"phase": "starting", "bindings": bindings.canonical_payload()})
            if not acquired:
                existing = self.store.lease(job_id) or {}
                return DispatcherOutcome(
                    job_id=job_id,
                    run_id=run_id,
                    status="blocked",
                    thread_id=thread_id,
                    revision=self.store.revision()[0],
                    summary={"existing": existing},
                    blocker={"code": "dispatcher.lease.busy", "message": "another process owns the dispatcher lease for this job", "action": job_id},
                )
            stop_event = threading.Event()
            self._stop_events[job_id] = stop_event
            renewer = LeaseRenewer(
                self.store,
                job_id,
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
        revision = self._emit(emit_events, run_id, job_id, thread_id, "step.action", {"status": "running", "kind": f"dispatcher.{job_id}.started", "effective_action": {"graph": job_id, "thread_id": thread_id, "bindings": bindings.canonical_payload()}}, revision_first=True)
        self.store.renew_lease(job_id, state="running", summary={"phase": "started", "run_id": run_id})
        return DispatcherOutcome(job_id=job_id, run_id=run_id, status="running", thread_id=thread_id, revision=revision, summary={"phase": "started", "bindings": bindings.canonical_payload()})

    def soft_stop(self, job_id: str, *, timeout_seconds: int | None = None, emit_events: bool = True) -> DispatcherOutcome:
        """Мягкая остановка: прекращает выдачу новых узлов и сохраняет чекпойнт."""

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
        run_id = (lease or {}).get("summary", {}).get("run_id") if lease else None
        run_id = run_id or str(uuid.uuid4())
        thread_id = (lease or {}).get("thread_id", "")
        self.store.renew_lease(job_id, state="resumable", summary={"phase": "soft_stopped"})
        revision = self._emit(emit_events, run_id, job_id, thread_id, "step.progress", {"status": "running", "kind": f"dispatcher.{job_id}.soft_stopped", "progress": {"resumable": True, "timeout_seconds": timeout_seconds or 0}}, revision_first=True)
        return DispatcherOutcome(job_id=job_id, run_id=run_id, status="resumable", thread_id=thread_id, revision=revision, summary={"phase": "soft_stopped"})

    def resume(self, job_id: str, bindings: DispatcherBindings, *, emit_events: bool = True) -> DispatcherOutcome:
        """Продолжение незавершённого потока, если привязки совпадают."""

        lease = self.store.lease(job_id)
        if lease is None:
            return self.start(job_id, bindings, emit_events=emit_events)
        thread_id = bindings.thread_id()
        if lease["thread_id"] != thread_id:
            return DispatcherOutcome(
                job_id=job_id,
                run_id=str(uuid.uuid4()),
                status="stale",
                thread_id=thread_id,
                revision=self.store.revision()[0],
                summary={"stored_thread_id": lease["thread_id"]},
                blocker={"code": "dispatcher.bindings.stale", "message": "stored thread bindings no longer match active generations or agent profile", "action": job_id},
            )
        if is_stale(lease["renewed_at"]):
            return DispatcherOutcome(
                job_id=job_id,
                run_id=str(uuid.uuid4()),
                status="stale",
                thread_id=thread_id,
                revision=self.store.revision()[0],
                summary={"renewed_at": lease["renewed_at"]},
                blocker={"code": "dispatcher.lease.expired", "message": "lease expired; restart the graph explicitly", "action": job_id},
            )
        run_id = lease.get("summary", {}).get("run_id") or str(uuid.uuid4())
        with self._lock:
            stop_event = threading.Event()
            self._stop_events[job_id] = stop_event
            renewer = LeaseRenewer(
                self.store,
                job_id,
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
        self.store.renew_lease(job_id, state="running", summary={"phase": "resumed", "run_id": run_id})
        return DispatcherOutcome(job_id=job_id, run_id=run_id, status="running", thread_id=thread_id, revision=revision, summary={"phase": "resumed"})

    def cancel(self, job_id: str, *, emit_events: bool = True) -> DispatcherOutcome:
        """Явная отмена: останавливает график и очищает операционные предложения."""

        with self._lock:
            stop_event = self._stop_events.pop(job_id, None)
            if stop_event is not None:
                stop_event.set()
            renewer = self._renewers.pop(job_id, None)
        if renewer is not None:
            renewer.join(timeout=2)
        lease = self.store.lease(job_id)
        thread_id = (lease or {}).get("thread_id", "")
        run_id = (lease or {}).get("summary", {}).get("run_id") or str(uuid.uuid4())
        revision = self._emit(emit_events, run_id, job_id, thread_id, "step.finished", {"status": "cancelled", "kind": f"dispatcher.{job_id}.cancelled", "exit": {"reason": "user_cancelled"}, "duration_seconds": 0.0}, revision_first=True)
        self.store.release_lease(job_id)
        if thread_id:
            self.store.delete_thread(thread_id)
        return DispatcherOutcome(job_id=job_id, run_id=run_id, status="cancelled", thread_id=thread_id, revision=revision, summary={"phase": "cancelled"})

    def retry(self, job_id: str, bindings: DispatcherBindings, *, emit_events: bool = True) -> DispatcherOutcome:
        """Явный повтор после сбоя: освобождает аренду и запускает заново."""

        lease = self.store.lease(job_id)
        if lease is not None and lease.get("state") not in {"failed", "resumable", "stale"}:
            return DispatcherOutcome(
                job_id=job_id,
                run_id=str(uuid.uuid4()),
                status="blocked",
                thread_id=bindings.thread_id(),
                revision=self.store.revision()[0],
                summary={"current_state": lease.get("state")},
                blocker={"code": "dispatcher.retry.busy", "message": "graph is still running or blocked on approval; stop it first", "action": job_id},
            )
        self.store.release_lease(job_id)
        return self.start(job_id, bindings, emit_events=emit_events)

    def finish(self, job_id: str, status: str, summary: dict[str, Any], *, emit_events: bool = True) -> DispatcherOutcome:
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
        thread_id = (lease or {}).get("thread_id", "")
        run_id = (lease or {}).get("summary", {}).get("run_id") or str(uuid.uuid4())
        revision = self._emit(emit_events, run_id, job_id, thread_id, "step.finished", {"status": status, "kind": f"dispatcher.{job_id}.finished", "exit": {"summary": summary}, "duration_seconds": 0.0}, revision_first=True)
        self.store.release_lease(job_id)
        if status in {"completed", "cancelled"} and thread_id:
            self.store.delete_thread(thread_id)
        return DispatcherOutcome(job_id=job_id, run_id=run_id, status=status, thread_id=thread_id, revision=revision, summary=summary)

    def launch_graph(self, outcome: DispatcherOutcome, bindings: DispatcherBindings, profile: dict[str, Any], *, timeout_seconds: int = 1800) -> None:
        """Запускает скомпилированный LangGraph после успешного захвата аренды."""

        if outcome.status != "running":
            return
        with self._lock:
            current = self._workers.get(outcome.job_id)
            if current is not None and current.is_alive():
                return
            worker = threading.Thread(
                target=self._run_graph,
                args=(outcome, bindings, profile, timeout_seconds),
                name=f"dispatcher-graph-{outcome.job_id}",
                daemon=True,
            )
            self._workers[outcome.job_id] = worker
            worker.start()

    def _run_graph(self, outcome: DispatcherOutcome, bindings: DispatcherBindings, profile: dict[str, Any], timeout_seconds: int) -> None:
        from .agents import execute as agent_execute
        from .pipeline_graphs import build_decide_state, build_discover_state, compile_decide_graph, compile_discover_graph

        stop_event = self._stop_events[outcome.job_id]
        proposal_root = self.store.path.parent / "proposal-work"

        def execute(repo: Path, selected_profile: dict[str, Any], operation: str, work_unit: dict[str, Any], supplement: str, timeout: int, cancelled: Callable[[], bool]) -> dict[str, Any]:
            proposal_dir = proposal_root / sha256(canonical_json({"run_id": outcome.run_id, "operation": operation, "work_unit_id": work_unit.get("id"), "nonce": str(uuid.uuid4())}))
            return agent_execute(repo, proposal_dir, selected_profile, operation, work_unit, supplement, timeout, cancelled)

        def result_key(name: str) -> str:
            return sha256(canonical_json({"thread_id": outcome.thread_id, "node_result": name}))

        def load_result(name: str) -> dict[str, Any] | None:
            row = self.store.proposal(result_key(name))
            return row["payload"] if row is not None else None

        def save_result(name: str, payload: dict[str, Any]) -> None:
            self.store.save_proposal(result_key(name), outcome.job_id, outcome.thread_id, "node-result", payload)

        common = {
            "repo": self.repo,
            "saver": self.store.saver,
            "executor": execute,
            "profile": profile,
            "supplement": bindings.instruction_supplement,
            "timeout_seconds": timeout_seconds,
            "cancelled": stop_event.is_set,
            "bindings_check": lambda: self.verify_bindings(outcome.job_id, bindings),
            "load_result": load_result,
            "save_result": save_result,
        }
        try:
            if outcome.job_id == "discover-mrq":
                graph = compile_discover_graph(**common)
                initial = build_discover_state(bindings.canonical_payload(), outcome.run_id, outcome.thread_id)
            else:
                graph = compile_decide_graph(**common)
                from .mrq import active
                from .mrq_batches import classify
                rows = active(self.repo)["mrq.jsonl"]
                pending = {item["mrq_id"] for item in rows if item.get("state") != "superseded" and not item.get("migration_decision", {}).get("decision")}
                batches = classify([item for item in rows if item.get("state") != "superseded"])
                batch = next((item for item in batches if bindings.work_unit_id in item.mrq_ids), None)
                if batch is None:
                    batch = next((item for item in batches if pending.intersection(item.mrq_ids)), None)
                initial = build_decide_state(bindings.canonical_payload(), outcome.run_id, outcome.thread_id, batch=batch)
            state = graph.invoke(initial, config={"configurable": {"thread_id": outcome.thread_id}})
            status = str(state.get("status", "failed"))
            blocker = state.get("blocker")
            if status == "blocked":
                proposals: list[tuple[str, dict[str, Any]]] = []
                if outcome.job_id == "decide-mrq":
                    for mrq_id, findings in sorted(state.get("findings", {}).items()):
                        decision = findings[-1].get("proposal") if findings else None
                        if decision:
                            key = sha256(canonical_json({"thread_id": outcome.thread_id, "mrq_id": mrq_id, "kind": "decision-approval"}))
                            proposals.append((key, {"decision_proposal": decision, "mrq_id": mrq_id}))
                else:
                    key = sha256(canonical_json({"thread_id": outcome.thread_id, "kind": "batch-approval"}))
                    proposals.append((key, {"batch_proposals": state.get("batch_proposals", []), "approved_noise_ids": state.get("approved_noise_ids", [])}))
                for key, payload in proposals:
                    self.store.save_proposal(key, outcome.job_id, outcome.thread_id, "approval", payload)
                proposal_keys = [key for key, _payload in proposals]
                self.store.renew_lease(outcome.job_id, state="blocked", summary={"phase": "approval_required", "run_id": outcome.run_id, "proposal_keys": proposal_keys})
                self.emit_transition(outcome.job_id, outcome.run_id, outcome.thread_id, "approval.required", f"dispatcher.{outcome.job_id}.approval_required", {"status": "blocked", "blocker": blocker, "proposal_keys": proposal_keys})
            elif status == "resumable":
                self.store.renew_lease(outcome.job_id, state="resumable", summary={"phase": "soft_stopped", "run_id": outcome.run_id})
            elif status == "stale":
                self.mark_stale(outcome.job_id)
            elif status == "completed":
                self.finish(outcome.job_id, "completed", {"phase": "completed"})
            else:
                self.store.renew_lease(outcome.job_id, state="failed", summary={"phase": "failed", "run_id": outcome.run_id, "blocker": blocker})
                self.emit_transition(outcome.job_id, outcome.run_id, outcome.thread_id, "step.finished", f"dispatcher.{outcome.job_id}.failed", {"status": "failed", "exit": {"blocker": blocker}, "duration_seconds": 0.0})
                self._stop_background(outcome.job_id)
        except Exception as exc:
            blocker = {"code": "dispatcher.graph.failed", "message": str(exc), "action": outcome.job_id}
            self.store.renew_lease(outcome.job_id, state="failed", summary={"phase": "failed", "run_id": outcome.run_id, "blocker": blocker})
            self.emit_transition(outcome.job_id, outcome.run_id, outcome.thread_id, "step.finished", f"dispatcher.{outcome.job_id}.failed", {"status": "failed", "exit": {"blocker": blocker}, "duration_seconds": 0.0})
            self._stop_background(outcome.job_id)
        finally:
            with self._lock:
                self._workers.pop(outcome.job_id, None)

    # -- проверка привязок перед эффектом ------------------------------

    def verify_bindings(self, job_id: str, bindings: DispatcherBindings) -> bool:
        """Перед агентным вызовом или эффектом сверяет сохранённые привязки."""

        lease = self.store.lease(job_id)
        if lease is None:
            return False
        if lease["thread_id"] != bindings.thread_id():
            return False
        if is_stale(lease["renewed_at"]):
            return False
        return True

    def mark_stale(self, job_id: str, *, emit_events: bool = True) -> DispatcherOutcome:
        """Помечает запуск устаревшим после изменения поколений.

        Эффект не применяется; пользователю нужно явно перезапустить график.
        """

        lease = self.store.lease(job_id)
        thread_id = (lease or {}).get("thread_id", "")
        run_id = (lease or {}).get("summary", {}).get("run_id") or str(uuid.uuid4())
        revision = self._emit(emit_events, run_id, job_id, thread_id, "approval.required", {"status": "blocked", "kind": "dispatcher.bindings.stale", "blocker": {"code": "dispatcher.bindings.stale", "message": "active generations changed during the run; restart the graph", "action": job_id}}, revision_first=True)
        self._stop_background(job_id)
        self.store.release_lease(job_id)
        if thread_id:
            self.store.delete_thread(thread_id)
        return DispatcherOutcome(job_id=job_id, run_id=run_id, status="stale", thread_id=thread_id, revision=revision, summary={"phase": "stale"})

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

    def _emit(self, emit_events: bool, run_id: str, job_id: str, thread_id: str, event_type: str, payload: dict[str, Any], *, revision_first: bool = True) -> int:
        """Сначала фиксирует ревизию, потом выпускает событие.

        Существующие типы ``workflow-event/v1`` не расширяются: переходы
        диспетчера кодируются полем ``kind`` внутри payload.
        """

        if not emit_events:
            return self.store.revision()[0]
        if revision_first:
            revision = self.store.bump_revision()
            payload = {**payload, "revision": revision}
        else:
            revision = payload.get("revision") or self.store.bump_revision()
        payload = self._fill_required_payload(event_type, job_id, payload)
        self.event_store.emit(event_type, run_id, payload, job_id=job_id, step_id=f"dispatcher-{job_id}", attempt=1)
        return revision

    def emit_transition(self, job_id: str, run_id: str, thread_id: str, event_type: str, kind: str, body: dict[str, Any]) -> int:
        """Публичный помощник для операционных переходов внутри графа."""

        if event_type not in {"step.progress", "step.action", "step.output", "step.validation", "step.finished", "log.append", "approval.required"}:
            raise ValueError(f"unsupported existing workflow event type: {event_type}")
        revision = self.store.bump_revision()
        payload = {"status": body.get("status", "running"), "kind": kind, "revision": revision, **{key: value for key, value in body.items() if key != "status"}}
        payload = self._fill_required_payload(event_type, job_id, payload, body)
        self.event_store.emit(event_type, run_id, payload, job_id=job_id, step_id=f"dispatcher-{job_id}", attempt=1)
        return revision

    def _fill_required_payload(self, event_type: str, job_id: str, payload: dict[str, Any], body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Дополняет payload обязательными полями существующих типов событий.

        Требования ``REQUIRED_PAYLOAD`` в :mod:`events` учитываются здесь, чтобы
        не расширять сам каталог типов: ``actor`` и ``operation`` подставляются
        из контекста диспетчера, а специфичные поля (``progress``, ``outputs``,
        ``validation``, ``log``, ``blocker`` и т. д.) — из тела перехода.
        """

        body = body or {}
        result = dict(payload)
        result.setdefault("actor", self.actor)
        result.setdefault("operation", job_id)
        result.setdefault("work_unit_id", None)
        if event_type == "step.started":
            result.setdefault("inputs", {})
            result.setdefault("input_fingerprint", "sha256:" + "0" * 64)
        if event_type == "step.progress" and "progress" not in result:
            result["progress"] = body.get("progress", {"kind": result.get("kind", "dispatcher.transition")})
        if event_type == "step.action" and "effective_action" not in result:
            result["effective_action"] = body.get("effective_action", {"kind": result.get("kind", "dispatcher.transition")})
        if event_type == "step.output":
            result.setdefault("outputs", body.get("outputs", {}))
            result.setdefault("output_fingerprint", sha256(canonical_json(result["outputs"])))
        if event_type == "step.validation":
            result.setdefault("validation", body.get("validation", {}))
        if event_type == "step.finished":
            result.setdefault("exit", body.get("exit", {"status": result.get("status", "completed")}))
            result.setdefault("duration_seconds", float(body.get("duration_seconds", 0.0)))
        if event_type == "log.append" and "log" not in result:
            result["log"] = body.get("log", {})
        if event_type == "approval.required":
            result.setdefault("blocker", body.get("blocker", {"code": "dispatcher.approval", "message": "dispatcher transition requires approval", "action": job_id}))
        return result

    def snapshot_projection(self) -> dict[str, Any]:
        """Собирает операционную проекцию из канонического снимка и SQLite.

        Возвращаемая структура присоединяется к существующему снимку под ключом
        ``dispatcher`` и НЕ участвует в отпечатках, ``status`` и ``doctor``.
        """

        revision, updated_at = self.store.revision()
        leases = self.store.leases()
        return {
            "schema_version": "1",
            "revision": revision,
            "fresh_at": updated_at,
            "jobs": {lease["job_id"]: lease for lease in leases},
        }

    def close(self) -> None:
        with self._lock:
            for stop_event in self._stop_events.values():
                stop_event.set()
            for renewer in self._renewers.values():
                renewer.join(timeout=2)
            for worker in self._workers.values():
                worker.join(timeout=2)
            self._stop_events.clear()
            self._renewers.clear()
            self._workers.clear()


def coordinator_for(repo: Path, project_id: str, event_store: EventStore, *, base: Path | None = None, actor: str = "local-user") -> tuple[DispatcherCoordinator, DispatcherStore]:
    """Открывает store и возвращает готовый координатор.

    Контекстный менеджер store-а остаётся открытым; вызывающий ответственен за
    закрытие через ``coordinator.store.close()``.
    """

    store = DispatcherStore(repo, base)
    store.open()
    return DispatcherCoordinator(repo, project_id, store, event_store, actor=actor), store
