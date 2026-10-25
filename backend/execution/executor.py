"""The executor process (spec §C.2): leader lock, pools, job registry, heartbeat, main loop.

Exactly one executor runs (``manage.py run_executor``). It takes a
session-level ``pg_try_advisory_lock`` on a dedicated connection as a guard
against an accidental second instance and re-verifies it in ``pg_locks``
every tick; losing it stops claiming, releases this instance's leases and
exits (docker restarts it).

Threads:

- main loop: ``BLPOP executor:wake`` (≤ tick seconds) → lost-job check →
  ``Scheduler.tick`` → submit claims to pools; ``reconcile`` every 15 s;
  schedules and post-runs every 30 s; LLM request purge daily;
- heartbeat: instance ``last_seen_at``; renews leases of registered jobs only;
- pools: sandbox, eval, llm-jobs, post-run (:class:`BoundedPool`);
- the internal LLM proxy and the direct-endpoint pumps (``backend.inference``).
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import DatabaseError
from django.db import InterfaceError
from django.db import OperationalError
from django.db import connection
from django.db import connections
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from backend.execution import capacity
from backend.execution import reconciler
from backend.execution import transitions
from backend.execution import workers
from backend.execution.api import WAKE_KEY
from backend.execution.breaker import breaker as default_breaker
from backend.execution.models import ExecutorInstance
from backend.execution.models import ExecutorSettings
from backend.execution.registry import JobRegistry
from backend.execution.scheduler import Scheduler
from backend.execution.scheduler import TickResult
from backend.generation.models import GenerationJob

logger = logging.getLogger(__name__)

RECONCILE_EVERY_S = 15.0
AUTOMATION_EVERY_S = 30.0
PURGE_EVERY_S = 86400.0
TICK_EVENT_EVERY_S = 2.0
CAPACITY_EVENT_EVERY_S = 5.0
SHUTDOWN_LLM_GRACE_S = 20.0
ORPHAN_EVERY_N_RECONCILES = 4

S = GenerationJob.Status


class PoolFullError(RuntimeError):
    pass


class BoundedPool:
    """A ``ThreadPoolExecutor`` that knows how many of its threads are really free.

    ``free() = size - in-flight`` (counted by this wrapper, not by DB status).
    A resize swaps in a new executor and lets the old one drain; its busy
    threads keep counting against the new size until they finish.
    ``sync=True`` runs submissions inline (tests).
    """

    def __init__(self, name: str, size: int, *, sync: bool = False) -> None:
        self.name = name
        self.size = max(0, int(size))
        self.sync = sync
        self._lock = threading.Lock()
        self._inflight = 0
        self._pool = None if sync else self._new(self.size)
        self._old: list[ThreadPoolExecutor] = []

    def _new(self, size: int) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=max(1, size), thread_name_prefix=f"exec-{self.name}")

    @property
    def inflight(self) -> int:
        with self._lock:
            return self._inflight

    def free(self) -> int:
        with self._lock:
            return max(0, self.size - self._inflight)

    def idle(self) -> bool:
        return self.inflight == 0

    def resize(self, size: int) -> None:
        size = max(0, int(size))
        with self._lock:
            if size == self.size:
                return
            self.size = size
            if self.sync:
                return
            old, self._pool = self._pool, self._new(size)
        old.shutdown(wait=False)
        self._old.append(old)
        logger.info("Pool %s resized to %d", self.name, size)

    def submit(self, fn: Callable[..., Any], *args: Any, force: bool = False) -> Future | None:
        """Run ``fn(*args)`` on a free thread; ``force`` queues beyond the size (post-run pool only)."""
        with self._lock:
            if not force and self._inflight >= self.size:
                msg = f"pool {self.name} is full"
                raise PoolFullError(msg)
            self._inflight += 1
            pool = self._pool

        def call() -> None:
            try:
                fn(*args)
            finally:
                with self._lock:
                    self._inflight -= 1

        if self.sync:
            call()
            return None
        try:
            return pool.submit(call)
        except Exception:
            with self._lock:
                self._inflight -= 1
            raise

    def wait_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.idle():
                return True
            time.sleep(0.1)
        return self.idle()

    def shutdown(self) -> None:
        for pool in [self._pool, *self._old]:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)


def new_executor_id() -> str:
    return f"{socket.gethostname()[:60]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _lock_key_parts(key: int) -> tuple[int, int]:
    return (key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF


class Executor:
    def __init__(
        self,
        *,
        executor_id: str | None = None,
        redis_client=None,
        docker_factory: Callable[[], Any] | None = None,
        sync_pools: bool = False,
        start_proxy: bool = True,
        start_pumps: bool = True,
        max_ticks: int | None = None,
        stop_when_idle: bool = False,
        tick_seconds: float | None = None,
        breaker=None,
    ) -> None:
        self.executor_id = executor_id or new_executor_id()
        self.registry = JobRegistry()
        self.breaker = breaker or default_breaker
        self.scheduler = Scheduler(self.executor_id, breaker=self.breaker)
        self.stop_event = threading.Event()
        self.sync_pools = sync_pools
        self.start_proxy = start_proxy
        self.start_pumps = start_pumps
        self.max_ticks = max_ticks
        self.stop_when_idle = stop_when_idle
        self.tick_seconds = float(tick_seconds or settings.EXECUTOR_TICK_SECONDS)
        self._redis = redis_client
        self._docker_factory = docker_factory
        self.exit_code = 0
        self.stop_reason = ""
        self.lock_conn = None
        self.ctx = workers.WorkerContext(self.executor_id, self.registry)
        self.pools: dict[str, BoundedPool] = {}
        self.slots: capacity.EffectiveSlots | None = None
        self.code_version = ""
        self.ticks = 0
        self._threads: list[threading.Thread] = []
        self._last: dict[str, float] = {}
        self._reconciles = 0
        self._last_capacity: Any = None
        self._last_ticks: dict[str, Any] = {}
        self._last_endpoints: dict[int, tuple[dict, dict]] = {}
        self._warnings: tuple[str, ...] = ()

    # ── leadership ─────────────────────────────────────────────────────

    def acquire_leadership(self) -> bool:
        conn = connections.create_connection("default")
        conn.ensure_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [settings.EXECUTOR_LEADER_LOCK_KEY])
            won = bool(cursor.fetchone()[0])
        if not won:
            conn.close()
            logger.error("Another executor holds the leader lock; exiting")
            return False
        self.lock_conn = conn
        return True

    def verify_leadership(self) -> bool:
        if self.lock_conn is None:
            return False
        high, low = _lock_key_parts(settings.EXECUTOR_LEADER_LOCK_KEY)
        try:
            with self.lock_conn.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
                    "AND classid = %s AND objid = %s AND objsubid = 1 AND granted",
                    [high, low],
                )
                return cursor.fetchone() is not None
        except (DatabaseError, InterfaceError):
            return False

    def _release_leadership(self) -> None:
        if self.lock_conn is None:
            return
        try:
            self.lock_conn.close()  # session-level lock: released with the session
        except Exception:  # noqa: BLE001
            logger.debug("closing the leader lock connection failed", exc_info=True)
        self.lock_conn = None

    # ── lifecycle ──────────────────────────────────────────────────────

    def run(self) -> int:
        """Run until stopped; returns the process exit code."""
        if not self.acquire_leadership():
            return 1
        try:
            self.startup()
            while not self.stop_event.is_set():
                self.tick()
                if self.max_ticks is not None and self.ticks >= self.max_ticks:
                    break
                if self.stop_when_idle and self._idle():
                    break
                if not self.stop_event.is_set():
                    self.wait_for_wake()
        finally:
            self.shutdown()
        return self.exit_code

    def request_stop(self, reason: str = "signal") -> None:
        self.stop_reason = self.stop_reason or reason
        self.stop_event.set()

    def startup(self) -> None:
        now = timezone.now()
        self.code_version = capacity.compute_code_version()
        self.slots = capacity.effective_slots(ExecutorSettings.get())
        self._warnings = self.slots.warnings
        for warning in self.slots.warnings:
            logger.warning("capacity: %s", warning)
        self.pools = {
            "sandbox": BoundedPool("sandbox", self.slots.sandbox_slots, sync=self.sync_pools),
            "eval": BoundedPool("eval", self.slots.eval_slots, sync=self.sync_pools),
            "llm": BoundedPool("llm", self.slots.llm_job_threads, sync=self.sync_pools),
            "post": BoundedPool("post", self.slots.post_run_slots, sync=self.sync_pools),
        }
        ExecutorInstance.objects.exclude(id=self.executor_id).filter(stopped_at__isnull=True).update(
            stopped_at=now,
            is_leader=False,
        )
        ExecutorInstance.objects.update_or_create(
            id=self.executor_id,
            defaults={
                "hostname": socket.gethostname()[:120],
                "pid": os.getpid(),
                "code_version": self.code_version,
                "is_leader": True,
                "last_seen_at": now,
                "stopped_at": None,
                "config": self._status_config(),
            },
        )
        logger.info("Executor %s is leader (code %s)", self.executor_id, self.code_version[:12])
        report = reconciler.reconcile(self.executor_id, self.registry, docker_factory=self._docker_factory)
        self._last["reconcile"] = time.monotonic()
        if report.expired or report.lost:
            logger.info("Startup reconcile: %s expired, %s lost", report.expired, report.lost)
        self._start_thread("heartbeat", self._heartbeat_loop)
        if self.start_proxy:
            self._start_service("llm-proxy", "backend.inference.proxy_server", "serve", self.stop_event)
        if self.start_pumps:
            self._start_service(
                "direct-pumps",
                "backend.inference.pump",
                "run_direct_pumps",
                self.stop_event,
                self.executor_id,
            )
        if self.start_proxy:
            capacity.code_version()  # warm the cache used by the capacity API in this process

    def _start_thread(self, name: str, target: Callable[[], None]) -> None:
        thread = threading.Thread(target=target, name=f"exec-{name}", daemon=True)
        thread.start()
        self._threads.append(thread)

    def _start_service(self, name: str, module: str, func: str, *args: Any) -> None:
        def target() -> None:
            import importlib

            try:
                getattr(importlib.import_module(module), func)(*args)
            except NotImplementedError:
                logger.warning("%s is not implemented yet; not started", name)
            except Exception:
                logger.exception("%s crashed", name)
            finally:
                workers.close_db()

        self._start_thread(name, target)

    def shutdown(self) -> None:
        """SIGTERM path: stop claiming, give LLM calls 20 s, expire this instance's leases, exit."""
        self.stop_event.set()
        if self.pools.get("llm") and not self.sync_pools:
            self.pools["llm"].wait_idle(SHUTDOWN_LLM_GRACE_S)
        try:
            self.expire_own_leases()
        except Exception:
            logger.exception("Expiring own leases at shutdown failed")
        for pool in self.pools.values():
            pool.shutdown()
        self._release_leadership()
        logger.info("Executor %s stopped (%s)", self.executor_id, self.stop_reason or "done")

    def expire_own_leases(self) -> None:
        """One transaction: every job, post-run and pump lease of this instance expires now; ``stopped_at``."""
        from backend.generation.models import ExperimentPostRun
        from backend.inference.models import LLMRequest

        now = timezone.now()
        with transaction.atomic():
            GenerationJob.objects.filter(lease_owner=self.executor_id).update(lease_expires_at=now)
            ExperimentPostRun.objects.filter(lease_owner=self.executor_id).update(lease_expires_at=now)
            LLMRequest.objects.filter(status=LLMRequest.Status.LEASED, pump_owner=self.executor_id).update(
                lease_expires_at=now,
            )
            ExecutorInstance.objects.filter(id=self.executor_id).update(
                stopped_at=now,
                is_leader=False,
                last_seen_at=now,
            )

    # ── main loop ──────────────────────────────────────────────────────

    def wait_for_wake(self) -> None:
        timeout = max(1, math.ceil(self.tick_seconds))
        client = self._redis_client()
        if client is None:
            self.stop_event.wait(self.tick_seconds)
            return
        try:
            client.blpop([WAKE_KEY], timeout=timeout)
        except Exception:  # noqa: BLE001 — Redis down: fall back to the plain tick, never busy-spin
            logger.warning("executor wake wait failed (Redis unavailable?)", exc_info=True)
            self.stop_event.wait(self.tick_seconds)

    def _redis_client(self):
        if self._redis is None:
            try:
                import redis

                self._redis = redis.from_url(
                    settings.REDIS_URL,
                    socket_timeout=self.tick_seconds + 10,
                    socket_connect_timeout=5,
                )
            except Exception:  # noqa: BLE001
                return None
        return self._redis

    def tick(self) -> TickResult | None:
        self.ticks += 1
        if not self.verify_leadership():
            logger.error("Executor %s lost the leader lock; stopping", self.executor_id)
            self.exit_code = 1
            self.request_stop("leader lock lost")
            return None
        try:
            return self._tick()
        except (OperationalError, InterfaceError):
            logger.exception("Database error in the executor loop; reconnecting")
            if not connection.in_atomic_block:
                connection.close()
            self.stop_event.wait(self.tick_seconds)
        except Exception:
            logger.exception("Executor tick failed")
            self.stop_event.wait(self.tick_seconds)
        return None

    def _tick(self) -> TickResult:
        self.slots = capacity.effective_slots(ExecutorSettings.get())
        self._resize_pools(self.slots)
        reconciler.requeue_lost(self.executor_id, self.registry)
        free = {name: pool.free() for name, pool in self.pools.items()}
        result = self.scheduler.tick(self.slots, free)
        for claimed in result.claimed:
            self._submit(claimed)
        now = time.monotonic()
        if now - self._last.get("reconcile", 0) >= RECONCILE_EVERY_S:
            self._last["reconcile"] = now
            self._reconciles += 1
            reconciler.reconcile(
                self.executor_id,
                self.registry,
                docker_factory=self._docker_factory,
                reap=self._reconciles % ORPHAN_EVERY_N_RECONCILES == 0,
            )
        if now - self._last.get("automation", -AUTOMATION_EVERY_S) >= AUTOMATION_EVERY_S:
            self._last["automation"] = now
            self._automation()
        if now - self._last.get("purge", 0) >= PURGE_EVERY_S:
            self._last["purge"] = now
            self._purge()
        self._publish(result)
        return result

    def _resize_pools(self, slots: capacity.EffectiveSlots) -> None:
        self.pools["sandbox"].resize(slots.sandbox_slots)
        self.pools["eval"].resize(slots.eval_slots)
        self.pools["llm"].resize(slots.llm_job_threads)
        self.pools["post"].resize(slots.post_run_slots)

    def _submit(self, claimed) -> None:
        self._publish_claim(claimed)
        pool = self.pools["eval" if claimed.lane == "eval" else claimed.lane]
        try:
            pool.submit(workers.run_job, self.ctx, claimed)
        except Exception as exc:
            logger.exception("Submitting job %s to the %s pool failed", claimed.job_id, pool.name)
            job = GenerationJob.objects.filter(id=claimed.job_id, attempt=claimed.attempt).first()
            if job is None:
                return
            if claimed.lane == "eval":
                reconciler.requeue_evaluation(job)
            else:
                transitions.infra_retry(job, transitions.REASON_SUBMIT_FAILED, str(exc))

    @staticmethod
    def _publish_claim(claimed) -> None:
        """The ``job`` event of a claim (running / evaluating), from the claim's RETURNING row."""
        job = GenerationJob(
            id=claimed.job_id,
            status=S.RUNNING,
            phase=GenerationJob.Phase.EVALUATING if claimed.lane == "eval" else GenerationJob.Phase.RUNNING,
            attempt=claimed.attempt,
            experiment_id=claimed.experiment_id,
            condition_id=claimed.condition_id,
            app_requirement_id=claimed.app_id,
            repeat_index=claimed.repeat_index,
            endpoint_id=claimed.endpoint_id,
            started_at=timezone.now(),
        )
        transitions.publish_job(job)

    def _automation(self) -> None:
        """Schedules fire and post-runs start on the post-run pool, never on this loop."""
        if self.slots is None or self.slots.draining:
            return
        from backend.generation.models import ExperimentPostRun

        post = self.pools["post"]
        try:
            from backend.generation.services.experiment_automation import run_due_schedules

            run_due_schedules(
                timezone.now(),
                submit=lambda fn, schedule_id: post.submit(workers.run_schedule_action, schedule_id, force=True),
            )
        except NotImplementedError:
            pass
        except Exception:
            logger.exception("run_due_schedules failed")
        if post.free() <= 0 or not ExperimentPostRun.objects.filter(status=ExperimentPostRun.Status.PENDING).exists():
            return
        if self.slots.defer_post_runs:
            snap_busy = (
                self.pools["sandbox"].free() == 0
                and self.pools["eval"].free() == 0
                and (self.slots.sandbox_slots + self.slots.eval_slots) > 0
            )
            if snap_busy:
                return
        with contextlib.suppress(PoolFullError):
            post.submit(workers.run_post_run, self.ctx)

    def _purge(self) -> None:
        try:
            from backend.inference.requests import purge_old

            purge_old()
        except NotImplementedError:
            pass
        except Exception:
            logger.exception("Purging old LLM requests failed")

    def _idle(self) -> bool:
        if any(not pool.idle() for pool in self.pools.values()):
            return False
        from backend.generation.models import ExperimentPostRun

        if GenerationJob.objects.filter(queued_at__isnull=False, status__in=[S.PENDING, S.RUNNING]).exists():
            return False
        return not ExperimentPostRun.objects.filter(
            status__in=[ExperimentPostRun.Status.PENDING, ExperimentPostRun.Status.RUNNING],
        ).exists()

    # ── heartbeat ──────────────────────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        interval = float(settings.EXECUTOR_HEARTBEAT_SECONDS)
        while not self.stop_event.wait(interval):
            try:
                self.heartbeat()
            except Exception:
                logger.exception("Executor heartbeat failed")
            finally:
                workers.close_db()

    def heartbeat(self) -> int:
        """``last_seen_at`` + lease renewal for registered jobs only (rows locked in id order, SKIP LOCKED)."""
        from backend.generation.models import ExperimentPostRun

        now = timezone.now()
        lease = timedelta(seconds=settings.EXECUTOR_LEASE_SECONDS)
        ExecutorInstance.objects.filter(id=self.executor_id).update(
            last_seen_at=now,
            config=self._status_config(),
        )
        ids = self.registry.ids()
        renewed = 0
        if ids:
            for attempt in range(3):
                try:
                    with transaction.atomic():
                        locked = list(
                            GenerationJob.objects.select_for_update(skip_locked=True)
                            .filter(id__in=ids, lease_owner=self.executor_id)
                            .order_by("id")
                            .values_list("id", flat=True),
                        )
                        renewed = GenerationJob.objects.filter(id__in=locked).update(
                            lease_expires_at=now + lease,
                            heartbeat_at=now,
                        )
                    break
                except OperationalError as exc:
                    if "deadlock" not in str(exc).lower() or attempt == 2:
                        raise
        if self.pools.get("post") and not self.pools["post"].idle():
            ExperimentPostRun.objects.filter(
                lease_owner=self.executor_id,
                status=ExperimentPostRun.Status.RUNNING,
            ).update(lease_expires_at=now + lease)
        return renewed

    def _status_config(self) -> dict[str, Any]:
        slots = self.slots.as_dict() if self.slots is not None else {}
        last = self.scheduler.last
        return {
            "slots": slots,
            "warnings": list(self._warnings),
            "circuit_open": self.breaker.open_endpoints(),
            "blocked_by_endpoint": {str(ep): r for ep, r in last.endpoint_reasons().items()},
            "registry": len(self.registry),
        }

    # ── events ─────────────────────────────────────────────────────────

    def _publish(self, result: TickResult) -> None:
        now = time.monotonic()
        if now - self._last.get("capacity_event", 0) >= CAPACITY_EVENT_EVERY_S:
            self._last["capacity_event"] = now
            self._publish_capacity(result)
        if now - self._last.get("tick_event", 0) >= TICK_EVENT_EVERY_S:
            self._last["tick_event"] = now
            self._publish_ticks(result)
            try:
                self._publish_endpoints()
            except Exception:
                logger.exception("Publishing endpoint events failed")

    def _publish_capacity(self, result: TickResult) -> None:
        from backend.realtime.events import publish_capacity

        snap = result.snapshot
        if snap is None or self.slots is None:
            return
        lanes = {
            "sandbox": {"used": snap.sandbox_used, "total": self.slots.sandbox_slots},
            "eval": {"used": snap.eval_used, "total": self.slots.eval_slots},
            "llm": {"used": snap.llm_used, "total": self.slots.llm_job_threads},
            "post": {"used": self.pools["post"].inflight, "total": self.slots.post_run_slots},
        }
        event = {
            "lanes": lanes,
            "executor": {
                "online": True,
                "draining": self.slots.draining,
                "code_stale": bool(self.code_version) and self.code_version != capacity.code_version(),
            },
            "warnings": list(self.slots.warnings),
        }
        if event != self._last_capacity:
            self._last_capacity = event
            publish_capacity(event)

    def _publish_endpoints(self) -> None:
        """``endpoint:<id>`` state events (§F.6) for local endpoints, only when changed (≤ 1 per tick period)."""
        from backend.inference.events import endpoint_state
        from backend.inference.models import LLMEndpoint
        from backend.realtime.events import publish_endpoint_event

        seen: set[int] = set()
        for endpoint in LLMEndpoint.objects.exclude(kind=LLMEndpoint.Kind.OPENROUTER):
            seen.add(endpoint.pk)
            try:
                payloads = endpoint_state(endpoint)
            except Exception:  # noqa: BLE001 — live updates are best effort; pages poll as a fallback
                logger.debug("endpoint state for %s failed", endpoint.pk, exc_info=True)
                continue
            if self._last_endpoints.get(endpoint.pk) == payloads:
                continue
            self._last_endpoints[endpoint.pk] = payloads
            publish_endpoint_event(endpoint.pk, *payloads)
        for gone in set(self._last_endpoints) - seen:
            self._last_endpoints.pop(gone, None)

    def _publish_ticks(self, result: TickResult) -> None:
        from backend.generation.models import Experiment
        from backend.realtime.events import publish_experiment_event

        rows = (
            GenerationJob.objects.filter(
                experiment__status__in=[Experiment.Status.RUNNING, Experiment.Status.PAUSED],
            )
            .order_by()
            .values("experiment_id", "status", "phase")
            .annotate(n=Count("id"))
        )
        counts: dict[Any, dict[str, int]] = {}
        for row in rows:
            key = row["phase"] if row["status"] == S.RUNNING and row["phase"] else row["status"]
            bucket = counts.setdefault(row["experiment_id"], {})
            bucket[key] = bucket.get(key, 0) + row["n"]
        snap = result.snapshot
        lanes_by_exp: dict[str, list] = {}
        for state in result.lanes.values():
            lanes_by_exp.setdefault(str(state.experiment_id), []).append(state)
        open_circuits = set(self.breaker.open_endpoints())
        for experiment_id, bucket in counts.items():
            endpoints = []
            for state in lanes_by_exp.get(str(experiment_id), []):
                endpoints.append(
                    {
                        "id": state.endpoint_id,
                        "slots_used": snap.ep_active.get(state.endpoint_id, 0) if snap else 0,
                        "queued": state.pending_in_window,
                        "blocked_reason": state.reason,
                        "circuit": "open" if state.endpoint_id in open_circuits else "closed",
                    },
                )
            reasons = [e["blocked_reason"] for e in endpoints if e["blocked_reason"]]
            event = {
                "type": "tick",
                "counts": bucket,
                "endpoints": endpoints,
                "bottleneck": reasons[0] if reasons else None,
            }
            if self._last_ticks.get(str(experiment_id)) == event:
                continue
            self._last_ticks[str(experiment_id)] = event
            publish_experiment_event(experiment_id, event)
        for stale in set(self._last_ticks) - {str(e) for e in counts}:
            self._last_ticks.pop(stale, None)
