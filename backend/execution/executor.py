"""The executor process (spec §C.2): leader lock, pools, job registry, heartbeat, main loop.

Exactly one executor runs (``manage.py run_executor``). It takes a
session-level ``pg_try_advisory_lock`` on a dedicated connection as a guard
against an accidental second instance and re-verifies it in ``pg_locks``
every tick; losing it stops claiming, releases this instance's leases and
exits with code 1 (the compose service restarts it: ``restart: unless-stopped``).

Threads:

- main loop: ``BLPOP executor:wake`` (≤ tick seconds) → lost-job check →
  ``Scheduler.tick`` → submit claims to pools; ``reconcile`` every 15 s;
  schedules and post-runs every 30 s; LLM request purge daily;
- heartbeat: instance ``last_seen_at``; renews leases of registered runs only;
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
from backend.execution import runtime_builds
from backend.execution import transitions
from backend.execution import workers
from backend.execution.api import WAKE_KEY
from backend.execution.breaker import breaker as default_breaker
from backend.execution.models import ExecutorInstance
from backend.execution.models import ExecutorSettings
from backend.execution.registry import JobRegistry
from backend.execution.scheduler import Scheduler
from backend.execution.scheduler import TickResult
from backend.generation.models import GenerationRun
from backend.generation.models import StudyPostRun
from backend.generation.models.choices import RunPhase
from backend.generation.models.choices import RunStatus
from backend.generation.models.choices import WorkStatus

logger = logging.getLogger(__name__)

RECONCILE_EVERY_S = 15.0
AUTOMATION_EVERY_S = 30.0
PURGE_EVERY_S = 86400.0
TICK_EVENT_EVERY_S = 2.0
SCHEDULE_THREADS = 1  # schedule actions (top up / replicates / clone): short DB work, own pool
CAPACITY_EVENT_EVERY_S = 5.0
SHUTDOWN_LLM_GRACE_S = 20.0
ORPHAN_EVERY_N_RECONCILES = 4
SAMPLE_CACHE_GC_EVERY_S = 3600.0

S = RunStatus


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
            "schedule": BoundedPool("schedule", SCHEDULE_THREADS, sync=self.sync_pools),
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
        runtime_builds.recover(self.executor_id, [])
        self._last["reconcile"] = time.monotonic()
        self._last["sample_cache_gc"] = time.monotonic()
        if report.expired or report.lost:
            logger.info("Startup reconcile: %s expired, %s lost", report.expired, report.lost)
        self._start_thread("heartbeat", self._heartbeat_loop)
        if self.start_proxy:
            # Fatal: without the proxy every local-model agent run would fail as
            # infra while this instance still looks online, so a bind failure
            # (wrong LLM_PROXY_BIND, no llmproxy attachment) stops the executor
            # with exit 1 and shows up as a restart loop instead.
            self._start_service("llm-proxy", "backend.inference.proxy_server", "serve", self.stop_event, fatal=True)
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

    def _start_service(self, name: str, module: str, func: str, *args: Any, fatal: bool = False) -> None:
        """Run ``module.func(*args)`` in a daemon thread.

        ``fatal``: the service ending for any reason before shutdown (a crash or
        an early return) stops the executor with exit code 1.
        """

        def target() -> None:
            import importlib

            try:
                getattr(importlib.import_module(module), func)(*args)
            except NotImplementedError:
                logger.warning("%s is not implemented yet; not started", name)
                return
            except Exception:
                logger.exception("%s crashed", name)
            finally:
                workers.close_db()
            if fatal and not self.stop_event.is_set():
                logger.error("%s stopped unexpectedly; stopping the executor", name)
                self.exit_code = 1
                self.request_stop(f"{name} stopped")

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
        """One transaction: every run, post-run and pump lease of this instance expires now; ``stopped_at``."""
        from backend.execution.models import RuntimeBuildLease
        from backend.inference.models import LLMRequest

        now = timezone.now()
        with transaction.atomic():
            GenerationRun.objects.filter(lease_owner=self.executor_id).update(lease_expires_at=now)
            RuntimeBuildLease.objects.filter(owner=self.executor_id).update(expires_at=now)
            StudyPostRun.objects.filter(lease_owner=self.executor_id).update(lease_expires_at=now)
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
        runtime_builds.recover(
            self.executor_id,
            [entry.job_id for entry in self.registry.entries() if entry.lane == "preview"],
        )
        free = {name: pool.free() for name, pool in self.pools.items()}
        result = self.scheduler.tick(self.slots, free)
        for claimed in result.claimed:
            self._submit(claimed)
        for action_id in result.preview_builds:
            try:
                self.pools["eval"].submit(workers.run_preview_build, self.ctx, action_id)
            except Exception as exc:
                logger.exception("Submitting preview build %s failed", action_id)
                runtime_builds.submission_failed(action_id, self.executor_id, str(exc))
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
        if (
            now - self._last.get("sample_cache_gc", now) >= SAMPLE_CACHE_GC_EVERY_S
            and self.pools["eval"].idle()
            and self.pools["sandbox"].idle()
            and self.pools["schedule"].free()
        ):
            self._last["sample_cache_gc"] = now
            self.pools["schedule"].submit(workers.cleanup_sample_images)
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
            logger.exception("Submitting run %s to the %s pool failed", claimed.run_id, pool.name)
            run = GenerationRun.objects.filter(id=claimed.run_id, attempt=claimed.attempt).first()
            if run is None:
                return
            if claimed.lane == "eval":
                reconciler.requeue_evaluation(run)
            else:
                transitions.infra_retry(run, transitions.REASON_SUBMIT_FAILED, str(exc))

    @staticmethod
    def _publish_claim(claimed) -> None:
        """The ``run`` events of a claim (running / evaluating), from the claim's RETURNING row."""
        run = GenerationRun(
            id=claimed.run_id,
            status=S.RUNNING,
            phase=RunPhase.EVALUATING if claimed.lane == "eval" else RunPhase.RUNNING,
            attempt=claimed.attempt,
            study_id=claimed.study_id,
            condition_id=claimed.condition_id,
            cell_id=claimed.cell_id,
            repeat_index=claimed.repeat_index,
            endpoint_id=claimed.endpoint_id,
            started_at=timezone.now(),
        )
        transitions.publish_run(run)

    def _automation(self) -> None:
        """Schedules fire on their own pool and post-runs start on the post-run pool, never on this loop.

        Firing schedules is short DB work; sharing a post-run thread would queue a due fire behind a
        multi-hour analysis.
        """
        if self.slots is None or self.slots.draining:
            return
        post = self.pools["post"]
        schedule_pool = self.pools["schedule"]
        if schedule_pool.free() > 0:
            with contextlib.suppress(PoolFullError):
                schedule_pool.submit(workers.run_due_schedules)
        # Pending post-runs that are due and running ones whose lease expired (an executor restart mid-analysis).
        if post.free() <= 0 or not workers.claimable_post_runs().exists():
            return
        if self.slots.defer_post_runs:
            snap_busy = not self.pools["sandbox"].idle() or not self.pools["eval"].idle()
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
        from backend.runtime.models import ContainerAction

        if ContainerAction.objects.filter(
            action_type=ContainerAction.ActionType.BUILD,
            status__in=[ContainerAction.Status.PENDING, ContainerAction.Status.RUNNING],
        ).exists():
            return False
        if GenerationRun.objects.filter(queued_at__isnull=False, status__in=[S.PENDING, S.RUNNING]).exists():
            return False
        return (
            not workers.claimable_post_runs().exists()
            and not StudyPostRun.objects.filter(
                status=WorkStatus.RUNNING,
            ).exists()
        )

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
        """``last_seen_at`` + lease renewal for registered runs only (rows locked in id order, SKIP LOCKED)."""
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
                            GenerationRun.objects.select_for_update(skip_locked=True)
                            .filter(id__in=ids, lease_owner=self.executor_id)
                            .order_by("id")
                            .values_list("id", flat=True),
                        )
                        renewed = GenerationRun.objects.filter(id__in=locked).update(
                            lease_expires_at=now + lease,
                            heartbeat_at=now,
                        )
                    break
                except OperationalError as exc:
                    if "deadlock" not in str(exc).lower() or attempt == 2:
                        raise
        if self.pools.get("post") and not self.pools["post"].idle():
            StudyPostRun.objects.filter(
                lease_owner=self.executor_id,
                status=WorkStatus.RUNNING,
            ).update(lease_expires_at=now + lease)
        runtime_builds.renew(
            self.executor_id,
            [entry.job_id for entry in self.registry.entries() if entry.lane == "preview"],
        )
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
        """``tick`` events (spec §F.6) on ``study:<id>``: rollups of the **current run per cell**, like REST status.

        The current run is the cell's first usable attempt, else its latest (``runs.view.current_runs``);
        ``beyond_plan`` runs are excluded. Fields the executor cannot compute cheaply every tick
        (``throughput``, ``eta_seconds``, per-endpoint ``tokens_per_s`` / ``loaded``) are left out; the
        client keeps its last REST values for them.
        """
        from backend.generation.models import Study
        from backend.inference.models import LLMRequest
        from backend.realtime.events import publish_study_event

        studies = list(
            Study.objects.filter(status__in=["piloting", "running", "paused"]).values_list("id", "status"),
        )
        snap = result.snapshot
        system_ep = snap.system_ep if snap is not None else None
        leased = dict(
            LLMRequest.objects.filter(status=LLMRequest.Status.LEASED)
            .order_by()
            .values("endpoint_id")
            .annotate(n=Count("id"))
            .values_list("endpoint_id", "n"),
        )
        lanes_by_study: dict[str, list] = {}
        for state in result.lanes.values():
            lanes_by_study.setdefault(str(state.study_id), []).append(state)
        open_circuits = set(self.breaker.open_endpoints())
        live: set[str] = set()
        for study_id, status in studies:
            live.add(str(study_id))
            stage = "pilot" if status == "piloting" else "main"
            counts, total = study_progress(study_id, stage)
            endpoints = []
            for state in lanes_by_study.get(str(study_id), []):
                active = snap.ep_active.get(state.endpoint_id, 0) if snap else 0
                endpoints.append(
                    {
                        "id": state.endpoint_id,
                        # Same meaning as REST ``slots.used``: runs on the OpenRouter row, leased requests locally.
                        "slots_used": active if state.endpoint_id == system_ep else leased.get(state.endpoint_id, 0),
                        "active": active,
                        "queued": state.pending_in_window,
                        "blocked_reason": state.reason,
                        "circuit": "open" if state.endpoint_id in open_circuits else "closed",
                    },
                )
            reasons = [e["blocked_reason"] for e in endpoints if e["blocked_reason"]]
            event = {
                "type": "tick",
                "counts": counts,
                "cells": {
                    "total": total,
                    "done": counts.get("completed", 0),
                    "failed_final": counts.get("failed", 0) + counts.get("cancelled", 0),
                    "in_flight": counts.get("running", 0) + counts.get("eval_queued", 0) + counts.get("evaluating", 0),
                },
                "endpoints": endpoints,
                "bottleneck": reasons[0] if reasons else None,
            }
            if self._last_ticks.get(str(study_id)) == event:
                continue
            self._last_ticks[str(study_id)] = event
            publish_study_event(study_id, event)
        for stale in set(self._last_ticks) - live:
            self._last_ticks.pop(stale, None)


def study_progress(study_id: Any, stage: str = "main") -> tuple[dict[str, int], int]:
    """``({status or phase: n}, planned cells)`` over the current run of every cell of ``study_id`` at ``stage``.

    Running runs count under their phase (``running`` / ``eval_queued`` / ``evaluating``); a cell
    without any run yet counts only in the total.
    """
    from backend.generation.models import StudyCell
    from backend.generation.runs.view import current_runs

    counts: dict[str, int] = {}
    for status, phase in current_runs(study_id, stage).order_by().values_list("status", "phase"):
        key = (phase or "running") if status == S.RUNNING else status
        counts[key] = counts.get(key, 0) + 1
    total = StudyCell.objects.filter(study_id=study_id, stage=stage).count()
    return counts, total
