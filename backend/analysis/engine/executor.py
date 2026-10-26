"""Durable run execution (owner: executor stage).

A run is a DB state machine. ``submit_run`` freezes everything and creates
one pending ToolResult per planned tool, then enqueues on commit.
``process_run`` (Celery task, or a thread when no worker answers) takes a
global slot, claims the run's lease, heartbeats, executes each pending tool
idempotently (re-running a tool first deletes its previous findings), and
finalizes: dedup, measurement, summary, status. ``reconcile`` (periodic +
opportunistic) requeues runs whose heartbeat went stale and reaps orphans.
Cancellation is cooperative and final: a cancelled run is never overwritten.

Status rules: completed = every required tool succeeded or was not
applicable; partial = finished but a required tool failed/timed out;
failed = the sample could not be resolved/verified, infra failed past retries,
no tool succeeded, the running code no longer implements the plan's frozen
procedure (``procedure_drift``), or the finalize stage (dedup / measurement)
failed (``measurement_failed``: never a complete run with wrong or missing
outcomes; :func:`retry` re-runs finalize); cancelled = cancel requested.

Mechanics
---------
* **Slots**: ``ANALYSIS_MAX_CONCURRENT_RUNS`` Postgres advisory locks held on
  the executing thread's connection (released by the database if the process
  dies). No free slot → the run is re-enqueued with a countdown.
* **Lease**: claimed with ``select_for_update(skip_locked=True)``; the owner
  token is written to ``lease_owner`` and every write the executor makes
  afterwards is conditional on still owning it, so a worker that lost its
  lease (presumed dead, run reclaimed elsewhere) can never write over the
  new owner's results.
* **Heartbeat**: a thread bumps ``heartbeat_at`` (and the running
  ToolResult) every ``ANALYSIS_HEARTBEAT_SECONDS`` and polls the cancel flag;
  sandbox and reviewer waits poll the same flag and kill their containers.
* **Retries**: infrastructure failures (daemon down, SDK errors, a missing
  analyzer image) are retried up to ``ANALYSIS_MAX_ATTEMPTS`` per tool and per
  run, run-level requeues with an exponential backoff; tool failures are
  deterministic and never retried automatically; the AI review (it costs
  money) is never retried automatically: a review a dead or crashed attempt
  left running is failed as interrupted (its spend unknown, bounded by the
  frozen budget), never reset to pending, and only :func:`retry` (a user
  action) runs it again. A review whose results could not be stored keeps
  its replicates' spend. Each retry keeps the spend of the attempts it
  replaces (``diagnostics.prior_attempts``).
* **Procedure**: before anything runs, the plan's frozen procedure is
  compared with the running code (``plan.procedure_drift``): a tool whose
  spec changed fails with ``procedure_drift``; a changed engine stage or
  mapping table fails the whole run, so one ``plan_hash`` never covers
  results measured by different code.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import socket
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from contextlib import suppress
from datetime import timedelta
from typing import Any
from typing import NoReturn

from django.conf import settings
from django.db import DatabaseError
from django.db import close_old_connections
from django.db import connection
from django.db import transaction
from django.db.models import Count
from django.db.models import F
from django.db.models import Q
from django.utils import timezone

from backend.analysis.engine import adapters
from backend.analysis.engine import artifacts
from backend.analysis.engine import catalog
from backend.analysis.engine import dedup
from backend.analysis.engine import locks
from backend.analysis.engine import measurement
from backend.analysis.engine import normalize
from backend.analysis.engine import reviewer
from backend.analysis.engine import sample as sample_mod
from backend.analysis.engine import sandbox
from backend.analysis.engine.catalog import AI_TOOL_SLUG
from backend.analysis.engine.plan import canonical_json
from backend.analysis.engine.plan import check_frozen
from backend.analysis.engine.plan import engine_versions
from backend.analysis.engine.plan import plan_hash
from backend.analysis.engine.plan import procedure_drift
from backend.analysis.engine.types import SEVERITIES
from backend.analysis.engine.types import SEVERITY_RANK
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import NormalizedFinding
from backend.analysis.engine.types import NotApplicable
from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleUnavailable
from backend.analysis.engine.types import ToolFailure
from backend.analysis.models import AnalysisRun
from backend.analysis.models import FileOrigin
from backend.analysis.models import Finding
from backend.analysis.models import FindingCategory
from backend.analysis.models import ReviewSample
from backend.analysis.models import ToolKind
from backend.analysis.models import ToolResult

logger = logging.getLogger(__name__)

Status = AnalysisRun.Status
TStatus = ToolResult.Status
Reason = ToolResult.FailureReason

# Seconds between cancel-flag reads from a sandbox/reviewer wait loop.
CANCEL_POLL_SECONDS = 2.0
# Countdown before a run without a free slot asks again (+ jitter, so a study's
# queue does not retry in lockstep).
SLOT_RETRY_SECONDS = 20
SLOT_RETRY_JITTER = 10
# Countdown before a run requeued for an infrastructure failure runs again:
# INFRA_RETRY_SECONDS after the first attempt, x INFRA_RETRY_FACTOR per further
# attempt, at most INFRA_RETRY_MAX_SECONDS (60 s, 5 min, 15 min, ...), so a
# daemon restart of a few minutes does not use up every attempt of a queued run.
INFRA_RETRY_SECONDS = 60
INFRA_RETRY_FACTOR = 5
INFRA_RETRY_MAX_SECONDS = 900
# Error-message prefixes of runs that failed for these reasons (retry() re-runs finalize for the first).
MEASUREMENT_FAILED = "measurement_failed"
PROCEDURE_DRIFT = "procedure_drift"
INTERRUPTED_REVIEW = (
    "interrupted: the worker stopped during the AI review. A review is paid per call, so it is never re-run "
    "automatically (the spend of the interrupted attempt is unknown); retry the run to review again"
)
# QUEUED runs untouched this long are re-enqueued by reconcile (lost message / thread).
QUEUED_REDISPATCH_SECONDS = 300
# Containers younger than this are never reaped (a run may have just been reclaimed).
REAP_MIN_AGE_SECONDS = 120
STDERR_TAIL_CHARS = 4000
MESSAGE_MAX_CHARS = 4000


def _setting(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _max_attempts() -> int:
    return max(1, int(_setting("ANALYSIS_MAX_ATTEMPTS", 3)))


def _stale_seconds() -> int:
    return max(30, int(_setting("ANALYSIS_STALE_SECONDS", 180)))


def _owner_token() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex[:8]}"[:200]


def _clip(text: Any, limit: int = MESSAGE_MAX_CHARS) -> str:
    value = _text(text)
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _text(value: Any) -> str:
    """*value* as text PostgreSQL can store (it rejects NUL characters in text and jsonb)."""
    return str(value or "").replace("\x00", "")


def _json_safe(value: Any) -> Any:
    """A JSON value with NUL characters removed from every string (tool output and model replies may carry them)."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {_json_safe(k) if isinstance(k, str) else k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _infra_retry_countdown(attempt: int) -> int:
    """Seconds before a run requeued after its *attempt*-th claim runs again (exponential backoff)."""
    exponent = max(0, int(attempt) - 1)
    return int(min(INFRA_RETRY_MAX_SECONDS, INFRA_RETRY_SECONDS * INFRA_RETRY_FACTOR**exponent))


def _is_soft_timeout(exc: BaseException) -> bool:
    """The task's soft time limit fired: terminal for the run, never swallowed as a stage warning."""
    from celery.exceptions import SoftTimeLimitExceeded

    return isinstance(exc, SoftTimeLimitExceeded)


def _review_spend_bound(frozen: Any) -> float | None:
    """The most a review attempt can spend under its frozen budget (per-replicate cap x most replicates), or None."""
    if not isinstance(frozen, dict):
        return None
    budget = frozen.get("budget") if isinstance(frozen.get("budget"), dict) else {}
    cap = budget.get("max_cost_usd")
    if isinstance(cap, bool) or not isinstance(cap, (int, float)):
        return None
    # Replacement replicates are paid too: k + max_replacements replicates at most.
    samples = reviewer.max_samples_of(frozen)
    if samples < 1:
        return None
    return round(float(cap) * samples, 6)


def _release_running(run_id: str, now) -> None:
    """Results an ended attempt (worker lost, requeue, crash) left RUNNING.

    A static tool is pending again: it is deterministic and free, and its old
    findings are replaced when it re-runs. An AI review is failed as
    interrupted instead (:func:`_interrupt_reviews`). Callers hold the run
    row (or the lease).
    """
    ToolResult.objects.filter(run_id=run_id, status=TStatus.RUNNING).exclude(tool_kind=ToolKind.AI).update(
        status=TStatus.PENDING,
        started_at=None,
        updated_at=now,
    )
    _interrupt_reviews(run_id, now)


def _interrupt_reviews(
    run_id: str,
    now,
    *,
    status: str = TStatus.FAILED,
    reason: str = Reason.INFRA_ERROR,
    message: str = INTERRUPTED_REVIEW,
) -> None:
    """End the AI reviews an ended attempt left RUNNING, never to be resumed automatically.

    A review is paid per call, so running it again is the user's decision
    (:func:`retry`, which resumes from the replicates already stored). The
    replicates stored as they finished keep their spend; the call in flight
    is unknown, so the spend is a lower bound marked ``cost_unknown`` with
    the frozen budget as its upper bound: no cost report mistakes it for zero.
    """
    for result in ToolResult.objects.filter(run_id=run_id, status=TStatus.RUNNING, tool_kind=ToolKind.AI):
        bound = _review_spend_bound(result.config)
        # Replicates stored as they finished keep their spend: a lower bound (the call in flight is unknown).
        known, persisted = _persisted_spend(result.pk)
        ToolResult.objects.filter(pk=result.pk).update(
            status=status,
            failure_reason=reason,
            error_message=_clip(message),
            finished_at=now,
            duration_seconds=(now - result.started_at).total_seconds() if result.started_at else None,
            summary={
                "cost_usd": known,
                "cost_unknown": True,
                "cost_complete": False,
                "max_cost_usd": bound,
                "samples_persisted": persisted,
            },
            diagnostics={**dict(result.diagnostics or {}), "interrupted": True, "spend_unknown_max_usd": bound},
            updated_at=now,
        )


def _publish(run_id: str, event: dict[str, Any]) -> None:
    """Best-effort realtime event on ``analysis:<run_id>`` (the UI polls; SSE is a bonus)."""
    try:
        from backend.realtime.events import publish

        publish(f"analysis:{run_id}", event)
    except Exception:  # noqa: BLE001 - realtime must never break execution
        logger.debug("Publishing analysis event failed", exc_info=True)


class SampleChanged(SampleUnavailable):
    """The target's code changed since submission (sample hash mismatch)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="sample_changed")


class _Cancelled(Exception):  # noqa: N818 - control flow, not an error
    pass


class _LeaseLost(Exception):  # noqa: N818 - control flow, not an error
    pass


class _Requeued(Exception):  # noqa: N818 - control flow, not an error
    pass


class _RunInfraError(Exception):
    """Infrastructure is down for the whole run (not one tool's problem)."""


class _ToolDone(Exception):  # noqa: N818 - control flow, not an error
    """The current tool reached a terminal state early (already recorded)."""


class _ProcedureDrift(Exception):  # noqa: N818 - control flow, not an error
    """The running code stopped implementing the plan's procedure mid-run (e.g. a mapping table changed)."""


# --------------------------------------------------------------------------
# Submission
# --------------------------------------------------------------------------


def submit_run(
    *,
    user,
    plan: dict[str, Any],
    profile=None,
    generation_job=None,
    source_code: dict[str, str] | None = None,
    sample: Sample | None = None,
    study=None,
    purpose: str = "adhoc",
    name: str = "",
    dispatch: bool = True,
):
    """Create a queued AnalysisRun (+ pending ToolResults) and enqueue it on commit.

    Raises ``PermissionError`` when *user* may not analyse the job, and
    ``ValueError`` (API 400) for an invalid plan or target, including an
    unresolvable sample (``SampleUnavailable``). *sample* may be passed when
    the caller already resolved it (preflight) under ``plan["scope"]``.
    """
    if (generation_job is None) == (source_code is None):
        msg = "Give exactly one target: a generation job or inline source code"
        raise ValueError(msg)
    if (
        generation_job is not None
        and not getattr(user, "is_staff", False)
        and generation_job.created_by_id != getattr(user, "pk", None)
    ):
        msg = "You can only analyse your own generation jobs"
        raise PermissionError(msg)
    try:
        check_frozen(plan)
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if purpose not in AnalysisRun.Purpose.values:
        msg = f"Unknown purpose {purpose!r}"
        raise ValueError(msg)
    if source_code is not None and (
        not isinstance(source_code, dict)
        or not source_code
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in source_code.items())
    ):
        msg = "source_code must be a non-empty {path: text} object"
        raise ValueError(msg)
    scope = plan["scope"]
    if sample is None:
        try:
            if generation_job is not None:
                sample = sample_mod.resolve_job(generation_job, scope)
            else:
                sample = sample_mod.resolve_inline(source_code, scope)
        except SampleUnavailable as exc:
            raise ValueError(str(exc)) from exc
    included = sample.included()
    if not included:
        msg = "No file of the target is in scope for analysis"
        raise ValueError(msg)
    for file in included:
        sandbox.check_path(file.path)
    # The resolver already built target_meta for this sample under the plan's
    # scope (``empty_output`` follows the scope; the run record is read once).
    meta = dict(sample.meta or {})
    if generation_job is not None and not meta:
        meta = sample_mod.target_meta(generation_job, sample=sample)
    if generation_job is None:
        meta.setdefault("source", sample.source)
    # A JSON snapshot: whatever the resolver put in (dates, decimals) is stored as text.
    meta = json.loads(json.dumps(meta, default=str))

    with transaction.atomic():
        run = AnalysisRun.objects.create(
            name=_clip(name or _default_name(meta, generation_job), 200),
            purpose=purpose,
            created_by=user,
            study=study,
            generation_job=generation_job,
            source_code=dict(source_code) if source_code is not None else None,
            profile=profile,
            plan=plan,
            plan_hash=plan_hash(plan),
            target_meta=meta,
            sample_manifest=sample.manifest(),
            sample_hash=sample.sample_hash,
            status=Status.QUEUED,
            phase="queued",
        )
        ToolResult.objects.bulk_create(_planned_results(run, plan))
        run_id = str(run.pk)
        if dispatch:
            transaction.on_commit(lambda: enqueue(run_id))
        transaction.on_commit(lambda: _publish(run_id, {"type": "status", "status": Status.QUEUED, "phase": "queued"}))
    return run


def _default_name(meta: dict[str, Any], job) -> str:
    if job is None:
        return "Inline sample"
    parts = [str(meta.get(k) or "") for k in ("model_name", "app_name")]
    label = " · ".join(p for p in parts if p)
    return label or f"Job {str(job.pk)[:8]}"


def _planned_results(run: AnalysisRun, plan: dict[str, Any]) -> list[ToolResult]:
    """One pending ToolResult per planned tool, in plan order; the AI reviewer last."""
    results = []
    tools = plan.get("tools") or []
    for position, tool in enumerate(tools):
        results.append(
            ToolResult(
                run=run,
                tool_slug=tool["slug"],
                tool_kind=tool.get("kind") or ToolKind.STATIC,
                category=tool.get("category") or "",
                position=position,
                required=bool(tool.get("required", True)),
                status=TStatus.PENDING,
                config=tool.get("config") or {},
                config_hash=tool.get("config_hash") or "",
                tool_version=_clip(tool.get("version"), 100),
            ),
        )
    frozen = plan.get("reviewer") or {}
    if frozen.get("enabled"):
        model = frozen.get("model") if isinstance(frozen.get("model"), dict) else {}
        results.append(
            ToolResult(
                run=run,
                tool_slug=frozen.get("tool_slug") or AI_TOOL_SLUG,
                tool_kind=ToolKind.AI,
                category="ai",
                position=len(tools),
                required=bool(frozen.get("required", False)),
                status=TStatus.PENDING,
                config=frozen,
                config_hash=hashlib.sha256(canonical_json(frozen).encode()).hexdigest(),
                tool_version=_clip(model.get("canonical_slug") or model.get("slug") or "", 100),
            ),
        )
    return results


def enqueue(run_id: str, *, countdown: int = 0) -> None:
    from backend.analysis import tasks

    tasks.dispatch_process_run(str(run_id), countdown=countdown)


# --------------------------------------------------------------------------
# Worker entry point
# --------------------------------------------------------------------------


def _acquire_slot() -> int | None:
    slots = max(1, int(_setting("ANALYSIS_MAX_CONCURRENT_RUNS", 2)))
    for i in range(slots):
        key = locks.SLOT_BASE + i
        if locks.try_lock(key):
            return key
    return None


def _release_slot(key: int) -> None:
    try:
        locks.unlock(key)
    except Exception:  # noqa: BLE001 - the database drops it with the connection anyway
        logger.warning("Releasing analysis slot %s failed", key, exc_info=True)


def slot_status() -> dict[str, Any]:
    """{total, in_use} run slots across every process (for progress / admin views)."""
    slots = max(1, int(_setting("ANALYSIS_MAX_CONCURRENT_RUNS", 2)))
    try:
        held = locks.held_keys(locks.SLOT_BASE, locks.SLOT_BASE + slots - 1)
    except Exception:  # noqa: BLE001 - informational
        return {"total": slots, "in_use": None}
    return {"total": slots, "in_use": len(held)}


def process_run(run_id: str) -> None:
    """Worker entry point; safe to call repeatedly / concurrently (lease)."""
    run_id = str(run_id)
    slot = _acquire_slot()
    if slot is None:
        _defer(run_id)
        return
    try:
        owner = _owner_token()
        run = _claim(run_id, owner)
        if run is None:
            return
        _RunExecution(run, owner).execute()
    finally:
        _release_slot(slot)


def _defer(run_id: str) -> None:
    """No free slot: keep a queued run alive (fresh ``updated_at`` for reconcile) and ask again later."""
    waiting = AnalysisRun.objects.filter(pk=run_id, status=Status.QUEUED, cancel_requested=False).update(
        phase="waiting_for_slot",
        updated_at=timezone.now(),
    )
    if waiting:
        enqueue(run_id, countdown=SLOT_RETRY_SECONDS + random.randint(0, SLOT_RETRY_JITTER))  # noqa: S311


def _claim(run_id: str, owner: str) -> AnalysisRun | None:
    """Take the run's lease, or return None when it is not ours to execute."""
    now = timezone.now()
    with transaction.atomic():
        run = AnalysisRun.objects.select_for_update(skip_locked=True).filter(pk=run_id).first()
        if run is None or run.status in AnalysisRun.TERMINAL_STATUSES:
            return None
        if run.cancel_requested:
            _finalize_unowned(run, Status.CANCELLED, "Cancelled before it ran", open_status=TStatus.CANCELLED)
            return None
        heartbeat_live = run.heartbeat_at is not None and run.heartbeat_at >= now - timedelta(seconds=_stale_seconds())
        if run.status == Status.RUNNING and heartbeat_live:
            return None  # a live worker owns it
        if run.attempt >= _max_attempts():
            _finalize_unowned(
                run,
                Status.FAILED,
                f"infra_error: the run was interrupted {run.attempt} times (worker lost or infrastructure "
                "failure); giving up",
                open_status=TStatus.FAILED,
                open_reason=Reason.INFRA_ERROR,
            )
            return None
        run.status = Status.RUNNING
        run.lease_owner = owner
        run.heartbeat_at = now
        run.attempt += 1
        run.phase = "starting"
        run.started_at = run.started_at or now
        run.error_message = ""
        run.save(
            update_fields=[
                "status",
                "lease_owner",
                "heartbeat_at",
                "attempt",
                "phase",
                "started_at",
                "error_message",
                "updated_at",
            ],
        )
        # Results a dead attempt left RUNNING: static tools pending again, an AI review interrupted.
        _release_running(run_id, now)
    _publish(run_id, {"type": "status", "status": Status.RUNNING, "phase": "starting", "attempt": run.attempt})
    return run


# --------------------------------------------------------------------------
# Heartbeat
# --------------------------------------------------------------------------


class _Heartbeat:
    """Keeps the lease alive and mirrors the run's cancel flag / lease ownership."""

    def __init__(self, run_id: str, owner: str) -> None:
        self.run_id = run_id
        self.owner = owner
        self.interval = max(1, int(_setting("ANALYSIS_HEARTBEAT_SECONDS", 15)))
        self.lost = threading.Event()
        self.cancelled = threading.Event()
        self.result_id = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_beat = time.monotonic()
        self._last_check = 0.0
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if bool(getattr(settings, "ANALYSIS_HEARTBEAT_THREAD", True)):
            self._thread = threading.Thread(target=self._loop, name=f"analysis-hb-{self.run_id[:8]}", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        try:
            while not self._stop.wait(self.interval):
                self.beat()
        finally:
            # This thread's own DB connection.
            connection.close()

    def beat(self) -> None:
        with self._lock:
            now = timezone.now()
            try:
                touched = AnalysisRun.objects.filter(pk=self.run_id, lease_owner=self.owner).update(heartbeat_at=now)
                if not touched:
                    self.lost.set()
                    return
                if self.result_id is not None:
                    ToolResult.objects.filter(pk=self.result_id).update(updated_at=now)
                self._read_flags()
            except DatabaseError:
                # Transient: a lease that really goes stale is recovered by reconcile.
                logger.warning("Heartbeat of run %s failed", self.run_id, exc_info=True)
                return
            self._last_beat = time.monotonic()

    def maybe_beat(self) -> None:
        if time.monotonic() - self._last_beat >= self.interval:
            self.beat()

    def _read_flags(self) -> None:
        row = AnalysisRun.objects.filter(pk=self.run_id).values("cancel_requested", "lease_owner").first()
        if row is None or row["lease_owner"] != self.owner:
            self.lost.set()
        elif row["cancel_requested"]:
            self.cancelled.set()
        self._last_check = time.monotonic()

    def should_stop(self, *, force: bool = False) -> bool:
        if self.lost.is_set() or self.cancelled.is_set():
            return True
        if force or time.monotonic() - self._last_check >= CANCEL_POLL_SECONDS:
            with self._lock:
                try:
                    self._read_flags()
                except DatabaseError:
                    logger.warning("Reading the cancel flag of run %s failed", self.run_id, exc_info=True)
        return self.lost.is_set() or self.cancelled.is_set()


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


class _RunExecution:
    """One claimed attempt of a run (owns the lease until finalize or requeue)."""

    def __init__(self, run: AnalysisRun, owner: str) -> None:
        self.run = run
        self.run_id = str(run.pk)
        self.owner = owner
        self.plan = run.plan or {}
        self.hb = _Heartbeat(self.run_id, owner)
        self.max_attempts = _max_attempts()
        self.warnings: list[str] = []
        # Set by _verify_procedure: per-tool spec drift, and what the summary records as the procedure used.
        self.tool_drift: dict[str, str] = {}
        self.procedure: dict[str, Any] = {}
        # Artifact entries of the review replicates stored so far: {sample index: {name: entry}}.
        self.review_artifacts: dict[int, dict[str, Any]] = {}

    # -- callbacks handed to sandbox / reviewer -------------------------

    def is_cancelled(self) -> bool:
        return self.hb.should_stop()

    def heartbeat(self) -> None:
        self.hb.maybe_beat()

    def _check_stop(self) -> None:
        if self.hb.should_stop(force=True):
            raise _LeaseLost if self.hb.lost.is_set() else _Cancelled

    # -- lease-guarded writes ----------------------------------------------

    @contextmanager
    def _guard(self):
        """A transaction that holds the run row and proves we still own the lease."""
        with transaction.atomic():
            owned = list(
                AnalysisRun.objects.select_for_update()
                .filter(pk=self.run_id, lease_owner=self.owner)
                .values_list("pk", flat=True),
            )
            if not owned:
                raise _LeaseLost
            yield

    def _set_phase(self, phase: str) -> None:
        touched = AnalysisRun.objects.filter(pk=self.run_id, lease_owner=self.owner).update(
            phase=_clip(phase, 50),
            updated_at=timezone.now(),
        )
        if not touched:
            raise _LeaseLost
        _publish(self.run_id, {"type": "status", "status": Status.RUNNING, "phase": phase})

    def _update_result(self, result: ToolResult, **values: Any) -> None:
        values = {k: _json_safe(v) if isinstance(v, (str, dict, list)) else v for k, v in values.items()}
        values.setdefault("updated_at", timezone.now())
        touched = ToolResult.objects.filter(pk=result.pk, run__lease_owner=self.owner).update(**values)
        if not touched:
            raise _LeaseLost

    def _start(self, result: ToolResult, *, argv: list[str], image_ref: str, tool_version: str) -> None:
        now = timezone.now()
        self._update_result(
            result,
            status=TStatus.RUNNING,
            started_at=now,
            finished_at=None,
            duration_seconds=None,
            attempt=F("attempt") + 1,
            failure_reason="",
            error_message="",
            exit_code=None,
            argv=list(argv),
            image_ref=_clip(image_ref, 300),
            tool_version=_clip(tool_version, 100),
        )
        result.refresh_from_db(fields=["attempt", "status", "started_at"])
        self.hb.result_id = result.pk
        _publish(self.run_id, {"type": "tool", "tool_slug": result.tool_slug, "status": TStatus.RUNNING})

    def _finish(self, result: ToolResult, status: str, reason: str = "", message: str = "", **extra: Any) -> None:
        now = timezone.now()
        if reason and reason not in Reason.values:
            message = f"[{reason}] {message}"
            reason = Reason.TOOL_ERROR
        if "duration_seconds" not in extra:
            extra["duration_seconds"] = (now - result.started_at).total_seconds() if result.started_at else None
        self._update_result(
            result,
            status=status,
            failure_reason=reason,
            error_message=_clip(message),
            finished_at=now,
            **extra,
        )
        result.status = status
        self.hb.result_id = None
        _publish(
            self.run_id,
            {"type": "tool", "tool_slug": result.tool_slug, "status": status, "failure_reason": reason},
        )

    # -- main flow -----------------------------------------------------------

    def execute(self) -> None:
        self.hb.start()
        try:
            try:
                self._execute()
            except _Cancelled:
                self._finalize(cancelled=True)
        except _LeaseLost:
            logger.warning("Run %s: lease lost (reclaimed by another worker); stopping without writes", self.run_id)
        except _Requeued:
            logger.info("Run %s requeued after an infrastructure failure", self.run_id)
        except Exception as exc:
            logger.exception("Run %s: executor error", self.run_id)
            self._handle_crash(exc)
        finally:
            self.hb.stop()

    def _execute(self) -> None:
        self._set_phase("loading_sample")
        drift = self._verify_procedure()
        if drift:
            message = (
                f"{PROCEDURE_DRIFT}: the running analysis engine no longer implements the procedure this run's plan "
                f"froze ({'; '.join(drift)}). Results would be stamped with a plan_hash they do not match: freeze a "
                "new plan (re-lock the study) or run the code version the plan was frozen with"
            )
            self._skip_open(f"Not run: {message}")
            self._finalize(error=message, measure=False)
            return
        try:
            sample = load_sample(self.run)
        except SampleUnavailable as exc:
            self._skip_open(f"Not run: {exc}")
            self._finalize(error=f"{getattr(exc, 'code', 'no_code')}: {exc}")
            return
        # Resolver notes of this execution (a manifest sha mismatch, a missing baseline...) next to the
        # submit-time ones the summary already carries from the sample manifest.
        stored = set((self.run.sample_manifest or {}).get("warnings") or [])
        self.warnings.extend(f"sample: {w}" for w in sample.warnings if w not in stored)
        self._check_stop()
        pending = list(self.run.results.filter(status=TStatus.PENDING).order_by("position", "tool_slug"))
        static = [r for r in pending if r.tool_kind != ToolKind.AI]
        reviews = [r for r in pending if r.tool_kind == ToolKind.AI]
        try:
            if static:
                self._static_phase(static, sample)
            for result in reviews:
                self._check_stop()
                self._run_review(result, sample)
        except _ProcedureDrift as exc:
            message = (
                f"{PROCEDURE_DRIFT}: {exc}. Findings normalized now would carry a plan_hash they do not match: "
                "freeze a new plan (re-lock the study) or run the code version the plan was frozen with"
            )
            for result in self._open_results():
                self._finish(result, TStatus.FAILED, Reason.UNAVAILABLE, message)
            self._finalize(error=message, measure=False)
            return
        self._finalize()

    def _verify_procedure(self) -> list[str]:
        """Compare the plan's frozen procedure with the running code; return run-level drift (empty when none).

        Per-tool spec drift is kept in ``self.tool_drift`` (only that tool
        fails). Failing to check at all counts as drift: the procedure is
        unverified, and measuring anyway is exactly what this guards against.
        """
        try:
            drift = procedure_drift(self.plan)
        except Exception as exc:
            logger.exception("Run %s: checking the plan's procedure failed", self.run_id)
            self.procedure = {"verified": False, "error": _clip(f"{type(exc).__name__}: {exc}", 500)}
            return [f"the procedure could not be verified: {type(exc).__name__}: {exc}"]
        self.tool_drift = dict(drift.get("tools") or {})
        engine = list(drift.get("engine") or [])
        self.procedure = {
            "verified": not engine,
            "engine_versions": engine_versions(),
            "drift": {"engine": engine, "tools": dict(self.tool_drift)} if engine or self.tool_drift else {},
        }
        return engine

    # -- static tools --------------------------------------------------------

    def _static_phase(self, results: list[ToolResult], sample: Sample) -> None:
        self._set_phase("preparing")
        try:
            image_ref, image_id, manifest = self._prepare_image()
        except sandbox.ImageUnavailable as exc:
            for result in results:
                self._finish(result, TStatus.FAILED, Reason.UNAVAILABLE, str(exc))
            return
        except sandbox.SandboxError as exc:
            self._infra_abort(exc)
            return
        files = {f.path: f.content for f in sample.included()}
        try:
            for path in files:
                sandbox.check_path(path)
        except ValueError as exc:
            # submit_run rejects such samples; a re-resolved sample could still carry one.
            for result in results:
                self._finish(result, TStatus.FAILED, Reason.UNAVAILABLE, str(exc))
            return
        try:
            with sandbox.run_volume(self.run_id, files, image=image_id) as volume:
                for result in results:
                    self._check_stop()
                    self._run_static(
                        result,
                        sample,
                        volume=volume,
                        image_id=image_id,
                        image_ref=image_ref,
                        manifest=manifest,
                    )
        except (sandbox.SandboxError, _RunInfraError) as exc:
            self._infra_abort(exc)

    def _prepare_image(self) -> tuple[str, str, dict[str, Any]]:
        """(image reference ``<tag>@<image id>``, image id, image manifest) for the plan's frozen toolchain."""
        expected = str((self.plan.get("analyzer_image") or {}).get("tag") or "")
        if not expected:
            msg = "The plan records no analyzer image (its definition was missing when the plan was frozen)"
            raise sandbox.ImageUnavailable(msg)
        self._set_phase("image")
        tag, image_id = sandbox.ensure_image(
            expect_tag=expected,
            build=bool(getattr(settings, "ANALYSIS_BUILD_IMAGE_ON_DEMAND", True)),
            heartbeat=self.heartbeat,
        )
        try:
            manifest = sandbox.image_manifest(image_id)
        except sandbox.ImageUnavailable as exc:
            self.warnings.append(f"Analyzer image manifest unreadable, tool versions unverified: {exc}")
            manifest = {}
        return sandbox.image_reference(tag, image_id), image_id, manifest

    def _infra_abort(self, exc: BaseException, *, message: str = "") -> None:
        """The daemon/volume is unavailable for the whole static phase: requeue, or fail after max attempts."""
        message = message or f"Infrastructure error: {exc}"
        if self.run.attempt < self.max_attempts:
            countdown = _infra_retry_countdown(self.run.attempt)
            with self._guard():
                # A static tool re-runs on the next attempt; an AI review this
                # attempt started is failed as interrupted (never re-paid automatically).
                _release_running(self.run_id, timezone.now())
                AnalysisRun.objects.filter(pk=self.run_id).update(
                    status=Status.QUEUED,
                    lease_owner="",
                    heartbeat_at=None,
                    phase="requeued",
                    error_message=_clip(message),
                    updated_at=timezone.now(),
                )
            enqueue(self.run_id, countdown=countdown)
            _publish(self.run_id, {"type": "status", "status": Status.QUEUED, "phase": "requeued"})
            raise _Requeued from exc
        for result in self._open_results(kinds=("static", "metrics")):
            self._finish(
                result,
                TStatus.FAILED,
                Reason.INFRA_ERROR,
                f"{message} (gave up after {self.run.attempt} attempts)",
            )

    def _open_results(self, *, kinds: tuple[str, ...] | None = None) -> list[ToolResult]:
        qs = ToolResult.objects.filter(run_id=self.run_id, status__in=[TStatus.PENDING, TStatus.RUNNING])
        if kinds is not None:
            qs = qs.filter(tool_kind__in=kinds)
        return list(qs.order_by("position"))

    def _end(self, result: ToolResult, status: str, reason: str = "", message: str = "", **extra: Any) -> NoReturn:
        """Record the tool's terminal state and leave its execution (see :meth:`_run_static`)."""
        self._finish(result, status, reason, message, **extra)
        raise _ToolDone

    def _run_static(
        self,
        result: ToolResult,
        sample: Sample,
        *,
        volume: str,
        image_id: str,
        image_ref: str,
        manifest: dict[str, Any],
    ) -> None:
        # _ToolDone: the tool already recorded its terminal state.
        with suppress(_ToolDone):
            self._execute_static(
                result,
                sample,
                volume=volume,
                image_id=image_id,
                image_ref=image_ref,
                manifest=manifest,
            )

    def _execute_static(
        self,
        result: ToolResult,
        sample: Sample,
        *,
        volume: str,
        image_id: str,
        image_ref: str,
        manifest: dict[str, Any],
    ) -> None:
        """One static tool: prepare, admit, run (with infra retries), interpret, normalize, persist."""
        slug = result.tool_slug
        frozen = next((t for t in self.plan.get("tools") or [] if t.get("slug") == slug), None)
        if frozen is None:
            self._end(result, TStatus.FAILED, Reason.UNAVAILABLE, "The tool is not part of the run's plan")
        drift = self.tool_drift.get(slug)
        if drift:
            # The adapter / spec in this process is not the one the plan froze:
            # its findings would carry a plan_hash they do not match.
            message = (
                f"{PROCEDURE_DRIFT}: {drift}; this run's plan cannot be executed with the current {slug} "
                "adapter. Freeze a new plan (re-lock the study) to use it"
            )
            self._end(result, TStatus.FAILED, Reason.UNAVAILABLE, message, diagnostics={"procedure_drift": drift})
        if result.attempt >= self.max_attempts:
            self._end(
                result,
                TStatus.FAILED,
                Reason.INFRA_ERROR,
                f"Gave up after {result.attempt} interrupted or failed executions",
            )
        config = dict(frozen.get("config") or {})
        adapter, files, invocation = self._prepare_tool(result, sample, config)
        version, drift, version_note = _version_check(frozen, sandbox.tool_version(manifest, slug))
        if drift:
            self._end(result, TStatus.FAILED, Reason.UNAVAILABLE, drift, tool_version=_clip(version, 100))
        try:
            mem_bytes = sandbox.parse_mem(invocation.mem_limit)
        except ValueError as exc:
            self._end(result, TStatus.FAILED, Reason.INVALID_CONFIG, str(exc))
        outcome = self._execute_with_retries(
            result,
            invocation,
            mem_bytes=mem_bytes,
            volume=volume,
            image_id=image_id,
            image_ref=image_ref,
            version=version,
        )
        diagnostics = {
            "files_selected": len(files),
            "timeout_s": invocation.timeout_s,
            "mem_limit": invocation.mem_limit,
            "procedure": _tool_procedure(slug),
            **_exec_diagnostics(outcome),
        }
        if version_note:
            diagnostics["version_note"] = version_note
        common = {
            "exit_code": outcome.exit_code,
            "duration_seconds": outcome.wall_seconds or None,
            "artifacts": self._store_outputs(result, outcome),
            "diagnostics": diagnostics,
        }
        if outcome.timed_out:
            self._end(result, TStatus.TIMED_OUT, Reason.TIMEOUT, f"Timed out after {invocation.timeout_s}s", **common)
        if outcome.oom_killed:
            message = f"Killed by the OOM killer (memory limit {invocation.mem_limit})"
            self._end(result, TStatus.FAILED, Reason.OOM, message, **common)
        try:
            parse = adapter.interpret(outcome, files, config)
        except ToolFailure as exc:
            message = exc.message
            if outcome.out_truncated:
                message = f"{message} (/out exceeded the collection cap: some report files were not collected)"
            self._end(result, TStatus.FAILED, exc.reason, message, **common)
        except NotApplicable as exc:
            self._end(result, TStatus.NOT_APPLICABLE, message=str(exc) or "Nothing in scope", **common)
        except Exception as exc:
            logger.exception("Run %s: adapter %s failed to interpret", self.run_id, slug)
            message = f"Adapter error: {type(exc).__name__}: {exc}"
            self._end(result, TStatus.FAILED, Reason.PARSE_ERROR, message, **common)
        self._check_mapping_drift(result)
        scope = self._normalize_scope()
        try:
            # A parse error an adapter let through is a coverage gap, never a finding.
            findings, unparseable = normalize.normalize_all(slug, parse.findings, sample, scope)
        except Exception as exc:
            logger.exception("Run %s: normalizing %s findings failed", self.run_id, slug)
            message = f"Normalizing findings failed: {type(exc).__name__}: {exc}"
            self._end(result, TStatus.FAILED, Reason.PARSE_ERROR, message, **common)
        diagnostics["notes"] = list(parse.notes)
        if unparseable:
            diagnostics["parse_errors_moved_to_coverage"] = len(unparseable)
        coverage = {"files_in_scope": len(files), "files_scanned": None, "unparseable": [], **(parse.coverage or {})}
        coverage["unparseable"] = _merge_unparseable(coverage.get("unparseable"), unparseable)
        self._persist_static(
            result,
            findings,
            coverage=coverage,
            metrics=parse.metrics or {},
            summary=_static_summary(findings, len(files)),
            **common,
        )

    def _prepare_tool(self, result: ToolResult, sample: Sample, config: dict[str, Any]):
        """(adapter, files, invocation), or end the tool (unavailable / not applicable / adapter error)."""
        slug = result.tool_slug
        try:
            adapter = adapters.get_adapter(slug)
        except KeyError:
            self._end(result, TStatus.FAILED, Reason.UNAVAILABLE, f"No adapter is registered for {slug!r}")
        except Exception as exc:
            logger.exception("Run %s: loading the adapter of %s failed", self.run_id, slug)
            message = f"The adapter of {slug!r} failed to load: {type(exc).__name__}: {exc}"
            self._end(result, TStatus.FAILED, Reason.UNAVAILABLE, message)
        try:
            files = adapter.select_files(sample, config)
            if not files:
                raise NotApplicable
            invocation = adapter.invocation(files, config)
        except NotApplicable as exc:
            message = str(exc) or "No in-scope files for this tool"
            self._end(result, TStatus.NOT_APPLICABLE, message=message, coverage={"files_in_scope": 0})
        except ToolFailure as exc:
            self._end(result, TStatus.FAILED, exc.reason, exc.message)
        except Exception as exc:
            logger.exception("Run %s: adapter %s failed to prepare", self.run_id, slug)
            self._end(result, TStatus.FAILED, Reason.TOOL_ERROR, f"Adapter error: {type(exc).__name__}: {exc}")
        return adapter, files, invocation

    def _normalize_scope(self) -> dict[str, Any]:
        scope = dict(self.plan.get("scope") or {})
        # normalize (static) and consensus (AI) decide `counted`; both read the
        # plan's counting rules from the scope.
        outcomes = self.plan.get("outcomes") or {}
        for key in ("count_style", "count_metric_findings"):
            scope.setdefault(key, bool(outcomes.get(key, False)))
        return scope

    def _execute_with_retries(
        self,
        result: ToolResult,
        invocation,
        *,
        mem_bytes: int,
        volume: str,
        image_id: str,
        image_ref: str,
        version: str,
    ) -> ExecOutcome:
        """run_tool with capacity admission and infra retries (ends the tool when it cannot run)."""
        capacity_wait = int(_setting("ANALYSIS_CAPACITY_WAIT_SECONDS", 1800))
        while True:
            self._start(result, argv=invocation.argv, image_ref=image_ref, tool_version=version)
            try:
                admitted = sandbox.wait_for_capacity(
                    mem_bytes,
                    is_cancelled=self.is_cancelled,
                    heartbeat=self.heartbeat,
                    timeout_s=capacity_wait,
                )
            except sandbox.SandboxError as exc:
                outcome = ExecOutcome(exit_code=None, infra_error=str(exc))
            else:
                if not admitted:
                    self._check_stop()
                    self._end(
                        result,
                        TStatus.FAILED,
                        Reason.INFRA_ERROR,
                        f"No apps-daemon memory free for {invocation.mem_limit} within {capacity_wait}s",
                    )
                self._set_phase(f"running:{result.tool_slug}")
                outcome = sandbox.run_tool(
                    image=image_id,
                    volume=volume,
                    invocation=invocation,
                    run_id=self.run_id,
                    tool_slug=result.tool_slug,
                    is_cancelled=self.is_cancelled,
                    heartbeat=self.heartbeat,
                )
            if outcome.cancelled:
                self._check_stop()
                raise _Cancelled
            if outcome.infra_error is None:
                return outcome
            logger.warning(
                "Run %s tool %s attempt %s: infra error: %s",
                self.run_id,
                result.tool_slug,
                result.attempt,
                outcome.infra_error,
            )
            if sandbox.image_missing(outcome.infra_error):
                # The image vanished mid-run (e.g. pruned): retrying the same image id
                # can never work. Requeue the run; its next attempt re-ensures the
                # image (rebuilt when the definition still matches the plan's tag).
                msg = f"The analyzer image disappeared during the run: {outcome.infra_error}"
                raise _RunInfraError(msg)
            if not sandbox.daemon_ok():
                raise _RunInfraError(outcome.infra_error)
            if result.attempt >= self.max_attempts:
                self._end(
                    result,
                    TStatus.FAILED,
                    Reason.INFRA_ERROR,
                    f"Infrastructure error after {result.attempt} attempts: {outcome.infra_error}",
                    diagnostics=_exec_diagnostics(outcome),
                )
            self._pause(5 * result.attempt)

    def _pause(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.heartbeat()
            self._check_stop()
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    def _store_outputs(self, result: ToolResult, outcome: ExecOutcome) -> dict[str, Any]:
        stored: dict[str, Any] = {}
        scope = result.tool_slug
        items: list[tuple[str, str, str, bytes]] = []
        if outcome.stdout:
            items.append(("stdout", scope, "stdout.txt", outcome.stdout))
        if outcome.stderr:
            items.append(("stderr", scope, "stderr.txt", outcome.stderr))
        for name, data in sorted(outcome.out_files.items()):
            items.append((f"out/{name}", f"{scope}/out", name.replace("/", "__"), data))
        for key, artifact_scope, filename, data in items:
            try:
                stored[key] = artifacts.store(self.run_id, artifact_scope, filename, data)
            except OSError as exc:
                logger.warning("Run %s: storing artifact %s failed: %s", self.run_id, key, exc)
                self.warnings.append(f"{result.tool_slug}: artifact {key} not stored ({exc})")
        return stored

    def _persist_static(self, result: ToolResult, findings: list[NormalizedFinding], **values: Any) -> None:
        rows = [_finding_row(result, nf) for nf in findings]
        with self._guard():
            Finding.objects.filter(result_id=result.pk).delete()
            Finding.objects.bulk_create(rows, batch_size=500)
            self._finish(result, TStatus.SUCCEEDED, **values)

    # -- AI review -------------------------------------------------------

    def _run_review(self, result: ToolResult, sample: Sample) -> None:
        frozen = self.plan.get("reviewer") or {}
        if not frozen.get("enabled"):
            self._finish(result, TStatus.SKIPPED, message="The plan's AI reviewer is disabled")
            return
        prior = _prior_attempts(result)
        if result.attempt > 0:
            # Started by an earlier attempt and never reset by a user retry (which
            # zeroes the counter): a paid review is never re-run automatically.
            bound = _review_spend_bound(frozen)
            self._finish(
                result,
                TStatus.FAILED,
                Reason.INFRA_ERROR,
                INTERRUPTED_REVIEW,
                summary={"cost_usd": None, "cost_unknown": True, "max_cost_usd": bound},
                diagnostics={**dict(result.diagnostics or {}), "interrupted": True, "spend_unknown_max_usd": bound},
            )
            return
        model = frozen.get("model") if isinstance(frozen.get("model"), dict) else {}
        images = frozen.get("harness_images") if isinstance(frozen.get("harness_images"), dict) else {}
        self._start(
            result,
            argv=[],
            image_ref=str(images.get("harness") or images.get("base") or ""),
            tool_version=str(model.get("canonical_slug") or model.get("slug") or ""),
        )
        self._set_phase("reviewing")
        from backend.analysis.engine.reviewer import client as review_client
        from backend.inference.access import LLMAccessError

        try:
            # OpenRouter key or a local endpoint (inference relay), whichever
            # the frozen model needs; local weights must still match the plan.
            api_key = review_client.resolve_review_access(self.run.created_by, model)
        except LLMAccessError as exc:
            self._finish(
                result,
                TStatus.FAILED,
                Reason.UNAVAILABLE,
                f"AI review unavailable: {exc} {exc.remediation or ''}".strip(),
            )
            return
        self._check_mapping_drift(result)
        resumed, first_index = self._resumable_samples(result, prior)
        try:
            outcome = reviewer.run_review(
                frozen=frozen,
                sample=sample,
                api_key=api_key,
                run_id=self.run_id,
                is_cancelled=self.is_cancelled,
                heartbeat=self.heartbeat,
                # The plan's scope with its counting rules folded in: consensus
                # counts an AI finding under the same rules as a static one.
                scope=self._normalize_scope(),
                on_sample=lambda so: self._persist_sample(result, so),
                resumed=resumed,
                first_index=first_index,
            )
        except _LeaseLost:
            raise
        except Exception as exc:
            logger.exception("Run %s: AI review crashed", self.run_id)
            bound = _review_spend_bound(frozen)
            known, persisted = _persisted_spend(result.pk)
            self._finish(
                result,
                TStatus.FAILED,
                Reason.TOOL_ERROR,
                f"AI review error: {type(exc).__name__}: {exc}",
                # The replicates stored as they finished keep their spend; a call in
                # flight at the crash may have been paid too: a lower bound.
                summary={
                    "cost_usd": known,
                    "cost_unknown": True,
                    "cost_complete": False,
                    "max_cost_usd": bound,
                    "samples_persisted": persisted,
                },
                diagnostics={**dict(result.diagnostics or {}), "spend_unknown_max_usd": bound},
            )
            if _is_soft_timeout(exc):
                raise
            return
        stopped = self.hb.should_stop(force=True)
        sample_artifacts = self._store_review_artifacts(result, outcome)
        try:
            self._persist_review(
                result,
                outcome,
                frozen,
                cancelled=stopped and outcome.status != "succeeded",
                sample_artifacts=sample_artifacts,
                prior=prior,
            )
        except _LeaseLost:
            raise
        except Exception as exc:
            # The review was paid for: keep its spend and fail the tool. Never let
            # this reach _handle_crash, which would requeue the run.
            logger.exception("Run %s: storing the AI review failed", self.run_id)
            self._persist_review_spend(result, outcome, frozen, exc, sample_artifacts=sample_artifacts, prior=prior)
            if _is_soft_timeout(exc):
                raise
        if stopped:
            self._check_stop()

    def _store_review_artifacts(self, result: ToolResult, outcome) -> dict[int, dict[str, Any]]:
        """Each replicate's raw responses / transcripts as artifacts: {sample index: {name: entry}}."""
        sample_artifacts: dict[int, dict[str, Any]] = {}
        for so in outcome.samples:
            if so.index in self.review_artifacts:
                # Stored when the replicate finished (or by the attempt it was resumed from).
                sample_artifacts[so.index] = self.review_artifacts[so.index]
                continue
            entries = {}
            for name, data in sorted((so.artifacts or {}).items()):
                try:
                    entries[name] = artifacts.store(self.run_id, f"{result.tool_slug}/sample-{so.index}", name, data)
                except OSError as exc:
                    self.warnings.append(f"{result.tool_slug}: sample {so.index} artifact {name} not stored ({exc})")
            sample_artifacts[so.index] = entries
        return sample_artifacts

    def _store_sample_artifacts(self, result: ToolResult, so) -> dict[str, Any]:
        entries: dict[str, Any] = {}
        for name, data in sorted((so.artifacts or {}).items()):
            try:
                entries[name] = artifacts.store(self.run_id, f"{result.tool_slug}/sample-{so.index}", name, data)
            except OSError as exc:
                self.warnings.append(f"{result.tool_slug}: sample {so.index} artifact {name} not stored ({exc})")
        self.review_artifacts[so.index] = entries
        return entries

    def _persist_sample(self, result: ToolResult, so) -> None:
        """Store one finished replicate at once (row, artifacts, uncounted findings): its spend is recorded
        even if the attempt dies before the review ends, and a retry resumes from it instead of paying again.

        Consensus is not known yet, so its findings are stored uncounted; the
        final :meth:`_persist_review` replaces every replicate row with the
        counted ones. A failure here (other than a lost lease) only warns: the
        review goes on and its end stores everything again.
        """
        try:
            entries = self._store_sample_artifacts(result, so)
            with self._guard():
                ReviewSample.objects.filter(result_id=result.pk, index=so.index).delete()
                row = ReviewSample.objects.create(**_review_sample_fields(result, so, entries))
                rows = []
                for nf in so.findings if so.ok else []:
                    finding = _finding_row(result, nf, review_sample=row, is_ai=True)
                    finding.counted = False
                    finding.is_consensus = False
                    rows.append(finding)
                Finding.objects.bulk_create(rows, batch_size=500)
        except _LeaseLost:
            raise
        except Exception as exc:
            if _is_soft_timeout(exc):
                raise
            logger.exception("Run %s: storing review sample %s failed", self.run_id, so.index)
            self.warnings.append(
                f"{result.tool_slug}: sample {so.index} could not be stored when it finished ({type(exc).__name__})",
            )

    def _resumable_samples(self, result: ToolResult, prior: list[dict[str, Any]]) -> tuple[list[Any], int]:
        """(replicates an earlier attempt finished and :func:`retry` kept, first index for new replicates).

        Only succeeded replicates with recorded usage are kept by a retry, so
        resuming never re-pays them; new replicates start after every index any
        earlier attempt used (a resumed review never repeats a replicate's seed).
        """
        rows = list(
            ReviewSample.objects.filter(result_id=result.pk, status=ReviewSample.Status.SUCCEEDED).order_by("index"),
        )
        resumed = []
        for row in rows:
            if not isinstance(row.usage, dict) or not row.usage:
                continue
            findings = []
            for finding in Finding.objects.filter(review_sample=row).order_by("id"):
                nf = _to_normalized(finding)
                nf.sample_index = row.index
                nf.counted = False
                nf.is_consensus = False
                findings.append(nf)
            resumed.append(_sample_outcome_from_row(row, findings))
            self.review_artifacts[row.index] = dict(row.artifacts or {})
        used = [row.index for row in rows]
        used += [int(p["max_index"]) for p in prior if isinstance(p.get("max_index"), int)]
        return resumed, (max(used) + 1 if used else 0)

    def _check_mapping_drift(self, result: ToolResult) -> None:
        """Fail closed before normalizing: the mapping tables loaded now must be the plan's (they are re-read
        when their files change, so a table edited mid-run is caught here, not only at the run's start)."""
        try:
            drifted = normalize.mapping_drift(self.plan.get("mapping_hashes"))
        except Exception as exc:  # noqa: BLE001 - unverifiable counts as drift
            drifted = [f"unverifiable ({type(exc).__name__}: {exc})"]
        if drifted:
            msg = (
                f"mapping table(s) {', '.join(drifted)} changed since the plan was frozen "
                f"(found before normalizing {result.tool_slug})"
            )
            raise _ProcedureDrift(msg)

    def _persist_review(
        self,
        result: ToolResult,
        outcome,
        frozen: dict[str, Any],
        *,
        cancelled: bool,
        sample_artifacts: dict[int, dict[str, Any]],
        prior: list[dict[str, Any]],
    ) -> None:
        """Store every replicate (failed ones too: their cost and transcripts are evidence)."""
        findings = list(outcome.findings)
        if not findings:
            # Defensive: consensus not applied → replicate findings, never counted.
            for so in outcome.samples:
                for nf in so.findings:
                    nf.counted = False
                    findings.append(nf)
        usage = dict(outcome.usage or {})
        summary = {
            **_review_cost_summary(outcome, prior),
            "findings": len(findings),
            "consensus": sum(1 for f in findings if f.is_consensus),
            "counted": sum(1 for f in findings if f.counted),
            "threshold": (outcome.agreement or {}).get("threshold"),
        }
        diagnostics = _review_diagnostics(frozen, outcome, prior)
        if cancelled or outcome.status == TStatus.CANCELLED:
            status, reason, message = TStatus.CANCELLED, Reason.CANCELLED, "Cancelled during the AI review"
        elif outcome.status == TStatus.SUCCEEDED:
            status, reason, message = TStatus.SUCCEEDED, "", outcome.message or ""
        elif outcome.status == TStatus.NOT_APPLICABLE:
            status, reason = TStatus.NOT_APPLICABLE, ""
            message = outcome.message or "Nothing in the sample is in the reviewer's scope"
        else:
            status = TStatus.FAILED
            reason = outcome.failure_reason or Reason.INVALID_OUTPUT
            message = outcome.message or "The AI review did not produce a valid result"
        if status != TStatus.SUCCEEDED:
            # Only a review that reached its quorum has a consensus: nothing else may count.
            for nf in findings:
                nf.counted = False
            summary["counted"] = 0
        with self._guard():
            by_index = self._replace_review_samples(result, outcome, sample_artifacts)
            rows = [
                _finding_row(result, nf, review_sample=by_index.get(nf.sample_index), is_ai=True) for nf in findings
            ]
            Finding.objects.bulk_create(rows, batch_size=500)
            self._finish(
                result,
                status,
                reason,
                message,
                summary=summary,
                metrics={"agreement": dict(outcome.agreement or {}), "usage": usage},
                diagnostics=diagnostics,
            )

    def _replace_review_samples(
        self,
        result: ToolResult,
        outcome,
        sample_artifacts: dict[int, dict[str, Any]],
    ) -> dict[int, ReviewSample]:
        """Replace the result's ReviewSample rows (and findings) with *outcome*'s replicates. Inside ``_guard``."""
        ReviewSample.objects.filter(result_id=result.pk).delete()
        Finding.objects.filter(result_id=result.pk).delete()
        by_index: dict[int, ReviewSample] = {}
        for so in outcome.samples:
            by_index[so.index] = ReviewSample.objects.create(
                **_review_sample_fields(result, so, sample_artifacts.get(so.index, {})),
            )
        return by_index

    def _persist_review_spend(
        self,
        result: ToolResult,
        outcome,
        frozen: dict[str, Any],
        exc: BaseException,
        *,
        sample_artifacts: dict[int, dict[str, Any]],
        prior: list[dict[str, Any]],
    ) -> None:
        """Storing a finished (paid) review failed: keep its spend, drop its findings, fail the tool.

        The ReviewSample rows carry each replicate's usage and cost, which is
        what every cost report sums; the findings are not stored, so nothing of
        this review can count. Only a user retry runs the review again.
        """
        message = (
            f"Storing the AI review failed ({type(exc).__name__}: {exc}). It was paid for, so it is not re-run "
            "automatically: its spend is kept, its findings are not; retry the run to review again"
        )
        summary = {**_review_cost_summary(outcome, prior), "findings": 0, "consensus": 0, "counted": 0}
        diagnostics = {**_review_diagnostics(frozen, outcome, prior), "persist_error": _clip(repr(exc), 1000)}
        try:
            with self._guard():
                self._replace_review_samples(result, outcome, sample_artifacts)
                self._finish(
                    result,
                    TStatus.FAILED,
                    Reason.TOOL_ERROR,
                    message,
                    summary=summary,
                    diagnostics=diagnostics,
                )
            return
        except _LeaseLost:
            raise
        except Exception:
            logger.exception("Run %s: storing the AI review's replicates failed too", self.run_id)
        # Last resort: the spend on the result row alone (outside any broken transaction).
        self._finish(result, TStatus.FAILED, Reason.TOOL_ERROR, message, summary=summary, diagnostics=diagnostics)

    # -- endings ---------------------------------------------------------------

    def _skip_open(self, message: str) -> None:
        for result in self._open_results():
            self._finish(result, TStatus.SKIPPED, message=message)

    def _handle_crash(self, exc: BaseException) -> None:
        """Unexpected error: requeue (attempts left) or fail the run; never leave it RUNNING silently."""
        message = f"Executor error: {type(exc).__name__}: {exc}"
        soft_timeout = _is_soft_timeout(exc)
        try:
            if self.run.attempt < self.max_attempts and not soft_timeout:
                self._infra_abort(exc, message=message)
                return
            with self._guard():
                _interrupt_reviews(self.run_id, timezone.now(), message=f"{message}. {INTERRUPTED_REVIEW}")
            for result in self._open_results():
                self._finish(result, TStatus.FAILED, Reason.INFRA_ERROR, message)
            # Past the soft time limit the hard kill is minutes away: record the end, skip the stages.
            self._finalize(error=f"infra_error: {message}", measure=not soft_timeout)
        except (_Requeued, _LeaseLost):
            return
        except Exception:
            # The database itself may be down: reconcile recovers the stale lease.
            logger.exception("Run %s: could not record the executor error", self.run_id)

    def _finalize(self, *, cancelled: bool = False, error: str = "", measure: bool = True) -> None:
        """Close open results, dedup, measure, and write the terminal status (lease-guarded).

        Fails closed: when dedup or the measurement stage fails, a run that
        would be completed/partial is FAILED with ``measurement_failed`` (never
        canonical) instead of carrying unkeyed issues (each finding its own
        issue: inflated counts) or an empty measurement; :func:`retry` re-runs
        this stage. *measure* False skips dedup and measurement (the procedure
        is not the plan's, or there is no time left).
        """
        self._set_phase("finalizing")
        fresh = AnalysisRun.objects.filter(pk=self.run_id).values("cancel_requested").first() or {}
        cancelled = cancelled or bool(fresh.get("cancel_requested"))
        for result in self._open_results():
            if cancelled:
                self._finish(result, TStatus.CANCELLED, Reason.CANCELLED, "Cancelled before it finished")
            else:
                self._finish(result, TStatus.SKIPPED, message="Not executed")
        stage_error = ""
        if measure and not cancelled:
            try:
                with self._guard():
                    _assign_issue_keys(self.run_id)
            except _LeaseLost:
                raise
            except Exception as exc:
                if _is_soft_timeout(exc):
                    raise
                logger.exception("Run %s: dedup failed", self.run_id)
                stage_error = f"issue deduplication failed: {type(exc).__name__}: {exc}"
                self.warnings.append(f"Issue deduplication failed: {type(exc).__name__}: {exc}")
        run = AnalysisRun.objects.get(pk=self.run_id)
        results = list(run.results.all())
        status, complete, message = run_status(results, cancelled=cancelled, error=error)
        self._note_unknown_review_spend(results)
        # Without issue keys every finding is its own issue: a measurement now would overcount.
        measurement_data, summary, measure_error = _measure(run, self.warnings, compute=measure and not stage_error)
        stage_error = stage_error or measure_error
        if stage_error and status in (Status.COMPLETED, Status.PARTIAL):
            status, complete = Status.FAILED, False
            detail = f" (tools: {message})" if message else ""
            message = f"{MEASUREMENT_FAILED}: {stage_error}{detail}; retry the run to measure it again"
            measurement_data = {}
            summary["complete"] = False
        if self.procedure:
            summary["procedure"] = self.procedure
        now = timezone.now()
        started = run.started_at or now
        qs = AnalysisRun.objects.filter(pk=self.run_id, lease_owner=self.owner, status=Status.RUNNING)
        if not cancelled:
            qs = qs.filter(cancel_requested=False)
        touched = qs.update(
            status=status,
            is_complete=complete,
            phase="done",
            lease_owner="",
            completed_at=now,
            duration_seconds=round((now - started).total_seconds(), 3),
            measurement=_json_safe(measurement_data),
            summary=_json_safe(summary),
            error_message=_clip(message),
            updated_at=now,
        )
        if not touched:
            row = AnalysisRun.objects.filter(pk=self.run_id).values("lease_owner", "cancel_requested").first()
            if row and row["lease_owner"] == self.owner and row["cancel_requested"] and not cancelled:
                # A cancel landed between our check and the final write: cancellation wins.
                self._finalize(cancelled=True)
                return
            raise _LeaseLost
        _publish(self.run_id, {"type": "status", "status": status, "phase": "done", "summary": summary})
        _run_finished(self.run_id, run.study_id)

    def _note_unknown_review_spend(self, results: list[ToolResult]) -> None:
        """Warn about AI reviews whose spend could not be recorded (worker lost / crash mid-review)."""
        for result in results:
            summary = result.summary or {}
            if result.tool_kind != ToolKind.AI or not summary.get("cost_unknown"):
                continue
            bound = _cost(summary.get("max_cost_usd"))
            limit = f" (at most ${bound:.4f} under the frozen budget)" if bound is not None else ""
            self.warnings.append(
                f"{result.tool_slug}: the review was interrupted or crashed; its spend is not recorded{limit}",
            )


def _run_finished(run_id: str, study_id) -> None:
    """Hand a run that reached its terminal state to its study (once committed), so the study can
    complete without waiting for someone to read it."""
    if study_id:
        transaction.on_commit(lambda: _notify_study(run_id))


def _notify_study(run_id: str) -> None:
    try:
        from backend.analysis.research import study as study_mod

        study_mod.on_run_finished(AnalysisRun.objects.select_related("study").get(pk=run_id))
    except Exception:
        # The study lifecycle must never break (or undo) the run's own completion;
        # reading the study (refresh) still completes it.
        logger.exception("Run %s: notifying its study failed", run_id)


def _prior_attempts(result: ToolResult) -> list[dict[str, Any]]:
    """Spend records of the review attempts user retries replaced (kept on the result by :func:`retry`)."""
    value = (result.diagnostics or {}).get("prior_attempts")
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _cost(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _review_cost_summary(outcome, prior: list[dict[str, Any]]) -> dict[str, Any]:
    """The review's sample counts and cost (+ the spend of the attempts earlier retries replaced)."""
    usage = dict(outcome.usage or {})
    cost = _cost(usage.get("cost_usd"))
    if cost is None:
        costs = [_cost((so.usage or {}).get("cost_usd")) for so in outcome.samples]
        known = [c for c in costs if c is not None]
        cost = round(math.fsum(known), 6) if known else None
    summary: dict[str, Any] = {
        "samples": len(outcome.samples),
        "samples_ok": sum(1 for so in outcome.samples if so.status == ReviewSample.Status.SUCCEEDED),
        "cost_usd": cost,
        # False: cost_usd is a lower bound (a replicate's spend unknown or incomplete) or unknown.
        "cost_complete": _review_cost_complete(outcome),
    }
    agreement = outcome.agreement or {}
    if agreement.get("replaced") or agreement.get("resumed"):
        summary["replaced_samples"] = int(agreement.get("replaced") or 0)
        summary["resumed_samples"] = int(agreement.get("resumed") or 0)
    if prior:
        known = [c for c in (_cost(p.get("cost_usd")) for p in prior) if c is not None]
        summary["prior_attempts"] = len(prior)
        summary["prior_attempts_cost_usd"] = round(math.fsum(known), 6) if known else None
        summary["prior_attempts_cost_unknown"] = any(bool(p.get("cost_unknown")) for p in prior)
    return summary


def _review_cost_complete(outcome) -> bool:
    """Whether the review's recorded spend is exact: the reviewer's totals say so (``usage.cost_complete``),
    else every replicate's spend is known and itself complete. No replicate: nothing was spent."""
    flag = (outcome.usage or {}).get("cost_complete")
    if isinstance(flag, bool):
        return flag
    for so in outcome.samples:
        usage = so.usage or {}
        if _cost(usage.get("cost_usd")) is None or usage.get("cost_complete") is False:
            return False
        if usage.get("gateway_log_missing"):
            return False
    return True


def _review_diagnostics(frozen: dict[str, Any], outcome, prior: list[dict[str, Any]]) -> dict[str, Any]:
    model = frozen.get("model") if isinstance(frozen.get("model"), dict) else {}
    diagnostics: dict[str, Any] = {
        "engine": frozen.get("engine"),
        "harness": frozen.get("harness") or "",
        "model": model.get("canonical_slug") if model else "",
        "prompt_version": frozen.get("prompt_version"),
        "samples": [
            {"index": so.index, "status": so.status, "failure_reason": so.failure_reason} for so in outcome.samples
        ],
    }
    if prior:
        diagnostics["prior_attempts"] = prior
    return diagnostics


def _review_sample_fields(result: ToolResult, so, sample_artifacts: dict[str, Any]) -> dict[str, Any]:
    """ReviewSample create() kwargs of one replicate."""
    return {
        "result_id": result.pk,
        "index": so.index,
        "engine": _clip(so.engine, 30),
        "harness": _clip(so.harness, 50),
        "harness_version": _clip(so.harness_version, 50),
        "model_slug": _clip(so.model_slug, 200),
        "model_canonical_slug": _clip(so.model_canonical_slug, 200),
        "status": so.status if so.status in ReviewSample.Status.values else ReviewSample.Status.FAILED,
        "failure_reason": _clip(so.failure_reason, 50),
        "error_message": _clip(so.error_message),
        "prompt_version": _clip(so.prompt_version, 50),
        "prompt_sha256": _clip(so.prompt_sha256, 64),
        "schema_sha256": _clip(so.schema_sha256, 64),
        "file_order_seed": so.file_order_seed,
        "files_reviewed": _json_safe(list(so.files_reviewed or [])),
        "coverage": _json_safe(dict(so.coverage or {})),
        "usage": _json_safe(dict(so.usage or {})),
        "providers": _json_safe(dict(so.providers or {})),
        "finish_reasons": _json_safe(dict(so.finish_reasons or {})),
        "grounding": _json_safe(dict(so.grounding or {})),
        "finding_count": len(so.findings or []),
        "repaired": bool(so.repaired),
        "latency_seconds": so.latency_seconds,
        "artifacts": dict(sample_artifacts or {}),
    }


def _sample_outcome_from_row(row: ReviewSample, findings: list[NormalizedFinding]):
    """A stored replicate as the reviewer's SampleOutcome (to resume a review without re-running it)."""
    return reviewer.SampleOutcome(
        index=row.index,
        engine=row.engine,
        harness=row.harness,
        harness_version=row.harness_version,
        model_slug=row.model_slug,
        model_canonical_slug=row.model_canonical_slug,
        status=row.status,
        failure_reason=row.failure_reason,
        error_message=row.error_message,
        prompt_version=row.prompt_version,
        prompt_sha256=row.prompt_sha256,
        schema_sha256=row.schema_sha256,
        file_order_seed=row.file_order_seed,
        files_reviewed=list(row.files_reviewed or []),
        coverage={**dict(row.coverage or {}), "resumed": True},
        usage=dict(row.usage or {}),
        providers=dict(row.providers or {}),
        finish_reasons=dict(row.finish_reasons or {}),
        grounding=dict(row.grounding or {}),
        repaired=row.repaired,
        latency_seconds=row.latency_seconds,
        findings=findings,
    )


def _resumable_rows(result: ToolResult):
    """The replicates of *result* a retry keeps for the next attempt to resume: succeeded, spend recorded."""
    return ReviewSample.objects.filter(result=result, status=ReviewSample.Status.SUCCEEDED).exclude(usage={})


def _persisted_spend(result_id) -> tuple[float | None, int]:
    """(known spend of the replicates stored for a review, None when none is known; how many are stored)."""
    usages = list(ReviewSample.objects.filter(result_id=result_id).values_list("usage", flat=True))
    known = [c for c in (_cost(u.get("cost_usd")) for u in usages if isinstance(u, dict)) if c is not None]
    return (round(math.fsum(known), 6) if known else None), len(usages)


def _attempt_record(result: ToolResult, *, kept: frozenset = frozenset()) -> dict[str, Any] | None:
    """What one ended review attempt spent, kept by :func:`retry` before it deletes the attempt's replicates.

    None for an attempt that never started (nothing was spent). ``cost_usd``
    is None when the spend is unknown; ``cost_unknown`` also marks a lower
    bound (an interrupted / crashed attempt whose in-flight call is unknown).
    *kept*: ids of replicate rows the retry keeps (their spend stays on the
    rows, never counted twice). ``max_index``: the highest replicate index
    the attempt used, so a resumed review never re-uses a seed.
    """
    if not result.attempt:
        return None
    rows = list(ReviewSample.objects.filter(result=result).values_list("pk", "index", "usage"))
    usages = [usage for pk, _index, usage in rows if pk not in kept]
    known = [c for c in (_cost(u.get("cost_usd")) for u in usages if isinstance(u, dict)) if c is not None]
    summary = result.summary or {}
    diagnostics = result.diagnostics or {}
    unknown = bool(summary.get("cost_unknown") or diagnostics.get("interrupted"))
    if known:
        cost = round(math.fsum(known), 6)
    elif usages or unknown:
        cost = None
    elif kept:
        cost = 0.0  # every stored replicate is kept: their spend stays on their rows
    else:
        # No replicate rows and no unknown-spend mark: nothing was called (e.g. no
        # API key), or only the summary kept the spend (see _persist_review_spend).
        cost = _cost(summary.get("cost_usd"))
        cost = 0.0 if cost is None else cost
    record = {
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
        "status": result.status,
        "failure_reason": result.failure_reason,
        "samples": len(usages),
        "cost_usd": cost,
        "cost_unknown": cost is None or unknown,
        "max_cost_usd": summary.get("max_cost_usd") or diagnostics.get("spend_unknown_max_usd"),
    }
    if rows:
        record["max_index"] = max(index for _pk, index, _usage in rows)
    if kept:
        record["kept_samples"] = len(kept)
    return record


def _tool_procedure(slug: str) -> dict[str, Any]:
    """The spec hash and adapter version that actually ran *slug* (provenance beside the plan's frozen hash)."""
    try:
        spec = catalog.get_spec(slug)
    except KeyError:
        return {}
    return {"spec_hash": spec.spec_hash, "adapter_version": spec.adapter_version}


def _version_check(frozen: dict[str, Any], actual: str) -> tuple[str, str, str]:
    """(version to record, drift failure message or "", provenance note or "")."""
    planned = str(frozen.get("version") or "")
    source = frozen.get("version_source", "spec")
    if not actual:
        return planned, "", "version_unverified: the image manifest does not list this tool"
    if actual == planned:
        return actual, "", ""
    if source == "image_manifest":
        return actual, f"Tool version drift: the plan froze {planned!r}, the analyzer image has {actual!r}", ""
    return actual, "", f"version_unverified: the plan recorded spec version {planned!r}; the image has {actual!r}"


def _merge_unparseable(reported: Any, moved: list[dict[str, Any]]) -> list[Any]:
    """The adapter's unparseable entries plus the parse errors normalize filtered out, one entry per path."""
    merged: list[Any] = []
    seen: set[str] = set()
    for entry in [*(reported if isinstance(reported, list) else []), *moved]:
        path = entry.get("path") if isinstance(entry, dict) else entry
        key = normalize.finding_path(str(path or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)
    return merged


def _exec_diagnostics(outcome: ExecOutcome) -> dict[str, Any]:
    stderr = outcome.stderr.decode("utf-8", errors="replace")
    return {
        "exit_code": outcome.exit_code,
        "wall_seconds": outcome.wall_seconds,
        "timed_out": outcome.timed_out,
        "oom_killed": outcome.oom_killed,
        "stdout_bytes": len(outcome.stdout),
        "stderr_bytes": len(outcome.stderr),
        "stdout_truncated": outcome.stdout_truncated,
        "stderr_truncated": outcome.stderr_truncated,
        "out_truncated": outcome.out_truncated,
        "stderr_tail": stderr[-STDERR_TAIL_CHARS:],
        "out_files": sorted(outcome.out_files),
        "container": outcome.container_name,
        "image_id": outcome.image_id,
        "infra_error": outcome.infra_error,
    }


def _static_summary(findings: list[NormalizedFinding], files_in_scope: int) -> dict[str, Any]:
    counted = [f for f in findings if f.counted]
    by_severity = dict.fromkeys(SEVERITIES, 0)
    for finding in counted:
        if finding.severity in by_severity:
            by_severity[finding.severity] += 1
    return {
        "findings": len(findings),
        "counted": len(counted),
        "by_severity": by_severity,
        "files_in_scope": files_in_scope,
    }


def _finding_row(
    result: ToolResult,
    nf: NormalizedFinding,
    *,
    review_sample=None,
    is_ai: bool | None = None,
) -> Finding:
    """Map a NormalizedFinding onto a Finding row (bulk_create bypasses save(), so ranks are set here).

    Text is stripped of NUL characters (PostgreSQL rejects them; tool output
    and model replies may carry them), so a stray byte never fails a result.
    """
    severity = nf.severity if nf.severity in SEVERITY_RANK else "info"
    category = nf.category if nf.category in FindingCategory.values else FindingCategory.OTHER
    origin = nf.file_origin if nf.file_origin in FileOrigin.values else FileOrigin.UNKNOWN
    grounding = nf.grounding if nf.grounding in Finding.Grounding.values else ""
    return Finding(
        result_id=result.pk,
        run_id=result.run_id,
        tool_slug=_clip(nf.tool_slug or result.tool_slug, 100),
        severity=severity,
        severity_rank=SEVERITY_RANK[severity],
        native_severity=_clip(nf.native_severity, 50),
        category=category,
        cwe=_json_safe(list(nf.cwe or [])),
        confidence=_clip(nf.confidence or "medium", 20),
        title=_clip(nf.title or nf.rule_id or "Finding", 500),
        description=_text(nf.description),
        suggestion=_text(nf.suggestion),
        rule_id=_clip(nf.rule_id, 200),
        file_path=_clip(nf.file_path, 500),
        line_number=nf.line,
        end_line=nf.end_line,
        column_number=nf.column,
        code_snippet=_text(nf.snippet),
        file_origin=origin,
        in_measured_scope=bool(nf.in_measured_scope),
        counted=bool(nf.counted),
        fingerprint=_clip(nf.fingerprint, 64),
        issue_key=_clip(nf.issue_key, 64),
        is_ai=bool(nf.is_ai) if is_ai is None else is_ai,
        review_sample=review_sample,
        grounding=grounding,
        is_consensus=bool(nf.is_consensus),
        tool_specific_data=_json_safe(dict(nf.extra or {})),
    )


def _to_normalized(row: Finding) -> NormalizedFinding:
    return NormalizedFinding(
        tool_slug=row.tool_slug,
        rule_id=row.rule_id,
        title=row.title,
        severity=row.severity,
        native_severity=row.native_severity,
        category=row.category,
        cwe=list(row.cwe or []),
        confidence=row.confidence,
        file_path=row.file_path,
        line=row.line_number,
        end_line=row.end_line,
        column=row.column_number,
        description=row.description,
        suggestion=row.suggestion,
        snippet=row.code_snippet,
        file_origin=row.file_origin,
        in_measured_scope=row.in_measured_scope,
        counted=row.counted,
        fingerprint=row.fingerprint,
        issue_key=row.issue_key,
        is_ai=row.is_ai,
        grounding=row.grounding,
        is_consensus=row.is_consensus,
        sample_index=None,
        extra=dict(row.tool_specific_data or {}),
    )


def _assign_issue_keys(run_id: str) -> None:
    """dedup.assign_issue_keys over the run's findings; persist only ``issue_key``."""
    rows = list(Finding.objects.filter(run_id=run_id).order_by("id"))
    if not rows:
        return
    normalized = [_to_normalized(row) for row in rows]
    dedup.assign_issue_keys(normalized)
    changed = []
    for row, nf in zip(rows, normalized, strict=True):
        key = _clip(nf.issue_key, 64)
        if row.issue_key != key:
            row.issue_key = key
            changed.append(row)
    if changed:
        Finding.objects.bulk_update(changed, ["issue_key"], batch_size=500)


def _measure(
    run: AnalysisRun,
    warnings: list[str],
    *,
    compute: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """(measurement, summary, measurement error or ""); never raises (a soft time limit excepted).

    A failing measure stage yields an empty measurement and its error (the
    caller fails the run); a failing summary degrades to :func:`basic_summary`
    (presentation only, the measurement is intact). *compute* False skips
    the measurement (empty) and only builds the summary.
    """
    error = ""
    measurement_data: dict[str, Any] = {}
    if compute:
        try:
            measurement_data = measurement.compute(run) or {}
        except Exception as exc:
            if _is_soft_timeout(exc):
                raise
            logger.exception("Run %s: measurement failed", run.pk)
            warnings.append(f"Measurement failed: {type(exc).__name__}: {exc}")
            error = f"measurement failed: {type(exc).__name__}: {exc}"
        else:
            if not measurement_data:
                error = "measurement failed: the measure stage returned nothing"
                warnings.append("Measurement failed: the measure stage returned nothing")
    run.measurement = measurement_data
    try:
        summary = dict(measurement.summarize(run) or {})
    except Exception as exc:
        if _is_soft_timeout(exc):
            raise
        logger.exception("Run %s: summary failed", run.pk)
        warnings.append(f"Summary failed: {type(exc).__name__}: {exc}")
        summary = basic_summary(run)
    if warnings:
        # Ordered and once each (a finalize re-entered for a late cancel sees the same warnings again).
        summary["warnings"] = list(dict.fromkeys([*list(summary.get("warnings") or []), *warnings]))
    summary["problems"] = tool_problems(list(run.results.all()))
    return measurement_data, summary, error


def tool_problems(results: list[ToolResult]) -> list[dict[str, Any]]:
    """Every tool that did not end ok, structured, in plan order (the run list shows them without parsing
    warning text). Same entries as the API's ``run_problems`` (``tool_slug``, ``message``) plus ``slug``."""
    return [
        {
            "slug": r.tool_slug,
            "tool_slug": r.tool_slug,
            "status": r.status,
            "failure_reason": r.failure_reason or "",
            "required": bool(r.required),
            "message": (r.error_message or "")[:300],
        }
        for r in sorted(results, key=lambda r: (r.position, r.tool_slug))
        if r.status not in ToolResult.OK_STATUSES
    ]


def run_status(  # noqa: PLR0911 - one return per status rule, in precedence order
    results: list[ToolResult],
    *,
    cancelled: bool = False,
    error: str = "",
) -> tuple[str, bool, str]:
    """(run status, is_complete, error message) from the final tool results (see module docstring)."""
    if cancelled:
        return Status.CANCELLED, False, error or "Cancelled"
    if error:
        return Status.FAILED, False, error
    if not results:
        return Status.FAILED, False, "The plan ran no tool"
    if not any(r.status == TStatus.SUCCEEDED for r in results):
        if all(r.status == TStatus.NOT_APPLICABLE for r in results):
            return Status.FAILED, False, "Nothing in the sample is in scope for any tool of the plan"
        details = "; ".join(f"{r.tool_slug}: {r.failure_reason or r.status}" for r in results)
        return Status.FAILED, False, f"No tool succeeded ({details})"
    bad_required = [r for r in results if r.required and r.status not in ToolResult.OK_STATUSES]
    if bad_required:
        details = ", ".join(f"{r.tool_slug} ({r.failure_reason or r.status})" for r in bad_required)
        return Status.PARTIAL, False, f"Required tool(s) did not succeed: {details}"
    return Status.COMPLETED, True, ""


def basic_summary(run: AnalysisRun) -> dict[str, Any]:
    """A summary (schema_version 2 keys) built from rows alone, used when the measure stage fails."""
    results = list(run.results.all())
    counts = Counter(r.status for r in results)
    tools = {"total": len(results), **{status: counts.get(status, 0) for status in TStatus.values}}
    # As measurement.summarize: without a succeeded static tool, zero static findings is not "clean".
    static_measured = any(r.status == TStatus.SUCCEEDED for r in results if r.tool_kind != ToolKind.AI)
    static = Finding.objects.filter(run_id=run.pk, is_ai=False, counted=True)
    ai = Finding.objects.filter(run_id=run.pk, is_ai=True, counted=True)
    severity_counts = dict.fromkeys(SEVERITIES, 0)
    for row in static.values("severity").annotate(n=Count("id")):
        severity_counts[row["severity"]] = row["n"]
    ai_counts = dict.fromkeys(SEVERITIES, 0)
    for row in ai.values("severity").annotate(n=Count("id")):
        ai_counts[row["severity"]] = row["n"]
    totals = (run.sample_manifest or {}).get("totals") or {}
    costs = [(r.summary or {}).get("cost_usd") for r in results if r.tool_kind == ToolKind.AI]
    known = [c for c in costs if isinstance(c, (int, float))]
    return {
        "schema_version": 2,
        "tools": tools,
        "complete": bool(results) and all(r.status in ToolResult.OK_STATUSES for r in results if r.required),
        "total_findings": sum(severity_counts.values()) if static_measured else None,
        "severity_counts": severity_counts if static_measured else None,
        "static_measured": static_measured,
        "ai_findings": sum(ai_counts.values()),
        "ai_severity_counts": ai_counts,
        "issues": static.exclude(issue_key="").values("issue_key").distinct().count() if static_measured else None,
        "sloc": {"measured": totals.get("measured_sloc"), "total": totals.get("sloc")},
        "cost_usd": sum(known) if known else None,
        "warnings": [],
        "tools_run": len(results),
        "tools_completed": counts.get(TStatus.SUCCEEDED, 0) + counts.get(TStatus.NOT_APPLICABLE, 0),
        "tools_failed": counts.get(TStatus.FAILED, 0) + counts.get(TStatus.TIMED_OUT, 0),
        "problems": tool_problems(results),
    }


def _finalize_unowned(
    run: AnalysisRun,
    status: str,
    message: str,
    *,
    open_status: str,
    open_reason: str = "",
) -> None:
    """Finish a run nobody executes (queued, or its worker is gone). Caller holds the row lock."""
    now = timezone.now()
    reason = open_reason or (Reason.CANCELLED if open_status == TStatus.CANCELLED else "")
    # A review the lost worker was running: same end, but its spend is marked unknown.
    _interrupt_reviews(str(run.pk), now, status=open_status, reason=reason, message=message)
    ToolResult.objects.filter(run=run, status__in=[TStatus.PENDING, TStatus.RUNNING]).update(
        status=open_status,
        failure_reason=reason,
        error_message=message,
        finished_at=now,
        updated_at=now,
    )
    summary = _unowned_summary(run)
    run.status = status
    run.is_complete = False
    run.phase = "done"
    run.lease_owner = ""
    run.completed_at = now
    if run.started_at:
        run.duration_seconds = round((now - run.started_at).total_seconds(), 3)
    run.summary = summary
    run.error_message = _clip(message)
    run.save(
        update_fields=[
            "status",
            "is_complete",
            "phase",
            "lease_owner",
            "completed_at",
            "duration_seconds",
            "summary",
            "error_message",
            "cancel_requested",
            "updated_at",
        ],
    )
    run_id = str(run.pk)
    transaction.on_commit(lambda: _publish(run_id, {"type": "status", "status": status, "phase": "done"}))
    _run_finished(run_id, run.study_id)


def _unowned_summary(run: AnalysisRun) -> dict[str, Any]:
    """The summary of a run finished without its executor: the measurement's summary (its None-not-0 rules:
    a cancelled run whose static tools never succeeded shows NA, never 0 findings), degraded to
    :func:`basic_summary` and then to the old summary; never raises (the status change matters more)."""
    try:
        summary = dict(measurement.summarize(run) or {})
        summary["problems"] = tool_problems(list(run.results.all()))
    except Exception:
        logger.exception("Run %s: summary failed", run.pk)
        try:
            summary = basic_summary(run)
        except Exception:
            logger.exception("Run %s: basic summary failed", run.pk)
            summary = dict(run.summary or {})
    return summary


# --------------------------------------------------------------------------
# Cancel / retry / reconcile
# --------------------------------------------------------------------------


def request_cancel(run):
    """Cancel a run; final. A queued (or orphaned) run is cancelled at once, a live one cooperatively.

    Returns the refreshed run. Cancelling a finished run is a no-op.
    """
    now = timezone.now()
    with transaction.atomic():
        locked = AnalysisRun.objects.select_for_update().get(pk=run.pk)
        if locked.status in AnalysisRun.TERMINAL_STATUSES:
            return locked
        locked.cancel_requested = True
        orphaned = locked.status == Status.RUNNING and (
            locked.heartbeat_at is None or locked.heartbeat_at < now - timedelta(seconds=_stale_seconds())
        )
        if locked.status == Status.QUEUED or orphaned:
            _finalize_unowned(locked, Status.CANCELLED, "Cancelled", open_status=TStatus.CANCELLED)
        else:
            locked.save(update_fields=["cancel_requested", "updated_at"])
            run_id = str(locked.pk)
            transaction.on_commit(
                lambda: _publish(run_id, {"type": "status", "status": Status.RUNNING, "phase": "cancelling"}),
            )
    return locked


RETRYABLE_STATUSES = (TStatus.FAILED, TStatus.TIMED_OUT, TStatus.CANCELLED, TStatus.SKIPPED)


def retry(run, *, tools: list[str] | None = None):
    """Reset failed/timed_out/cancelled tool results to pending and requeue.

    Skipped results (never executed because the run aborted) are reset too.
    Succeeded and not-applicable results are kept with their findings. A
    run that failed in its finalize stage (``measurement_failed``) with
    nothing else to retry is requeued as is, to dedup and measure again.
    A reset AI review keeps its succeeded replicates (rows, findings,
    artifacts, spend): the next attempt resumes from them and runs only the
    replicates still missing. What the replaced attempt spent on the other
    replicates is kept in ``diagnostics.prior_attempts`` (their rows are deleted).
    Raises ValueError when the run is still active or nothing is retryable.
    """
    with transaction.atomic():
        locked = AnalysisRun.objects.select_for_update().get(pk=run.pk)
        if locked.status in AnalysisRun.ACTIVE_STATUSES:
            msg = "The run is still queued or running"
            raise ValueError(msg)
        qs = ToolResult.objects.filter(run=locked, status__in=RETRYABLE_STATUSES)
        if tools is not None:
            known = set(ToolResult.objects.filter(run=locked).values_list("tool_slug", flat=True))
            unknown = sorted(set(tools) - known)
            if unknown:
                msg = f"Unknown tool(s) for this run: {', '.join(unknown)}"
                raise ValueError(msg)
            qs = qs.filter(tool_slug__in=list(tools))
        targets = list(qs)
        remeasure = (
            tools is None
            and locked.status == Status.FAILED
            and (locked.error_message or "").startswith(f"{MEASUREMENT_FAILED}:")
        )
        if not targets and not remeasure:
            msg = "Nothing to retry: no failed, timed-out, cancelled or skipped tool"
            raise ValueError(msg)
        ids = [r.pk for r in targets]
        spent: dict[Any, list[dict[str, Any]]] = {}
        # Succeeded replicates (with their recorded spend) survive the retry: the review resumes from
        # them instead of paying for them again. {result id: kept replicate ids}; artifacts of the
        # dropped replicates are removed per sample.
        kept: dict[Any, frozenset] = {}
        dropped_scopes: list[str] = []
        for target in targets:
            if target.tool_kind != ToolKind.AI:
                continue
            keep = frozenset(_resumable_rows(target).values_list("pk", flat=True))
            prior = _prior_attempts(target)
            record = _attempt_record(target, kept=keep)
            if record is not None:
                prior.append(record)
            if prior:
                spent[target.pk] = prior
            if keep:
                kept[target.pk] = keep
                for index in (
                    ReviewSample.objects.filter(result=target)
                    .exclude(pk__in=keep)
                    .values_list(
                        "index",
                        flat=True,
                    )
                ):
                    dropped_scopes.append(f"{target.tool_slug}/sample-{index}")
        kept_ids = [pk for keep in kept.values() for pk in keep]
        Finding.objects.filter(result_id__in=ids).exclude(review_sample_id__in=kept_ids).delete()
        # A kept replicate's findings wait, uncounted, for the resumed review's consensus.
        Finding.objects.filter(review_sample_id__in=kept_ids).update(counted=False, is_consensus=False)
        ReviewSample.objects.filter(result_id__in=ids).exclude(pk__in=kept_ids).delete()
        now = timezone.now()
        ToolResult.objects.filter(pk__in=ids).update(
            status=TStatus.PENDING,
            failure_reason="",
            error_message="",
            attempt=0,
            exit_code=None,
            argv=[],
            duration_seconds=None,
            started_at=None,
            finished_at=None,
            coverage={},
            metrics={},
            summary={},
            diagnostics={},
            artifacts={},
            updated_at=now,
        )
        for pk, prior in spent.items():
            ToolResult.objects.filter(pk=pk).update(diagnostics={"prior_attempts": prior})
        slugs = [result.tool_slug for result in targets if result.pk not in kept] + dropped_scopes
        transaction.on_commit(lambda: [artifacts.delete_scope(str(locked.pk), slug) for slug in slugs])
        locked.status = Status.QUEUED
        locked.cancel_requested = False
        locked.attempt = 0
        locked.lease_owner = ""
        locked.heartbeat_at = None
        locked.completed_at = None
        locked.duration_seconds = None
        locked.is_complete = False
        locked.error_message = ""
        locked.phase = "queued"
        locked.measurement = {}
        locked.save(
            update_fields=[
                "status",
                "cancel_requested",
                "attempt",
                "lease_owner",
                "heartbeat_at",
                "completed_at",
                "duration_seconds",
                "is_complete",
                "error_message",
                "phase",
                "measurement",
                "updated_at",
            ],
        )
        run_id = str(locked.pk)
        transaction.on_commit(lambda: enqueue(run_id))
        transaction.on_commit(lambda: _publish(run_id, {"type": "status", "status": Status.QUEUED, "phase": "queued"}))
    return locked


def reconcile(now=None) -> dict[str, Any]:
    """Recover runs whose worker died, re-dispatch lost queued runs, reap orphaned containers.

    * RUNNING with ``heartbeat_at`` older than ``ANALYSIS_STALE_SECONDS``: its
      running tool goes back to pending and the run is requeued, or it fails
      with ``infra_error`` once ``ANALYSIS_MAX_ATTEMPTS`` attempts were used
      (a cancel-requested one is cancelled).
    * QUEUED and untouched for 5 minutes: enqueued again (a duplicate message
      is harmless: only one worker can claim the lease).
    * Analysis containers/volumes and AI-review sandboxes (containers +
      networks) of runs that are not RUNNING are removed.

    Report keys (stable): ``requeued`` / ``failed`` / ``cancelled`` /
    ``redispatched`` (run ids), ``reaped`` / ``review_reaped`` (removal
    counts, None when the daemon was not reached) and ``reap_error`` /
    ``review_reap_error`` (only on failure).
    """
    now = now or timezone.now()
    stale_before = now - timedelta(seconds=_stale_seconds())
    report: dict[str, Any] = {
        "requeued": [],
        "failed": [],
        "cancelled": [],
        "redispatched": [],
        "reaped": None,
        "review_reaped": None,
    }
    stale = AnalysisRun.objects.filter(status=Status.RUNNING).filter(
        Q(heartbeat_at__lt=stale_before) | Q(heartbeat_at__isnull=True, updated_at__lt=stale_before),
    )
    for run_id in list(stale.values_list("pk", flat=True)):
        outcome = _recover_stale(run_id, stale_before)
        if outcome:
            report[outcome].append(str(run_id))
    queued_before = now - timedelta(seconds=QUEUED_REDISPATCH_SECONDS)
    for run in AnalysisRun.objects.filter(status=Status.QUEUED, updated_at__lt=queued_before):
        with transaction.atomic():
            locked = AnalysisRun.objects.select_for_update(skip_locked=True).filter(pk=run.pk).first()
            if locked is None or locked.status != Status.QUEUED:
                continue
            if locked.cancel_requested:
                _finalize_unowned(locked, Status.CANCELLED, "Cancelled", open_status=TStatus.CANCELLED)
                report["cancelled"].append(str(locked.pk))
                continue
            AnalysisRun.objects.filter(pk=locked.pk).update(updated_at=now)
            run_id = str(locked.pk)
            transaction.on_commit(lambda run_id=run_id: enqueue(run_id))
            report["redispatched"].append(run_id)
    live = {str(pk) for pk in AnalysisRun.objects.filter(status=Status.RUNNING).values_list("pk", flat=True)}
    try:
        report["reaped"] = sandbox.reap(live_run_ids=live, older_than_s=REAP_MIN_AGE_SECONDS)
    except Exception as exc:  # noqa: BLE001 - the daemon may be down; DB recovery still happened
        report["reap_error"] = str(exc)
    try:
        # Review sandboxes' containers carry the run label (reaped above); their
        # internal networks do not, so a dead worker's networks are removed here.
        from backend.analysis.engine.reviewer import agentic_sandbox

        report["review_reaped"] = agentic_sandbox.reap_review_sandboxes(
            live_run_ids=live,
            older_than_s=REAP_MIN_AGE_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - best effort, like the tool reap
        report["review_reap_error"] = str(exc)
    if any(report[k] for k in ("requeued", "failed", "cancelled", "redispatched")):
        logger.info("Analysis reconcile: %s", {k: v for k, v in report.items() if v})
    return report


def _recover_stale(run_id, stale_before) -> str:
    with transaction.atomic():
        run = AnalysisRun.objects.select_for_update(skip_locked=True).filter(pk=run_id).first()
        if run is None or run.status != Status.RUNNING:
            return ""
        beat = run.heartbeat_at or run.updated_at
        if beat is not None and beat >= stale_before:
            return ""  # it beat while we were looking
        if run.cancel_requested:
            _finalize_unowned(run, Status.CANCELLED, "Cancelled (worker lost)", open_status=TStatus.CANCELLED)
            return "cancelled"
        if run.attempt >= _max_attempts():
            _finalize_unowned(
                run,
                Status.FAILED,
                f"infra_error: the worker was lost {run.attempt} times; giving up",
                open_status=TStatus.FAILED,
                open_reason=Reason.INFRA_ERROR,
            )
            return "failed"
        now = timezone.now()
        # Static tools re-run on the next attempt; an AI review the dead worker started is interrupted.
        _release_running(str(run.pk), now)
        run.status = Status.QUEUED
        run.lease_owner = ""
        run.heartbeat_at = None
        run.phase = "requeued"
        run.error_message = "Requeued: the worker stopped heartbeating"
        run.save(update_fields=["status", "lease_owner", "heartbeat_at", "phase", "error_message", "updated_at"])
        rid = str(run.pk)
        transaction.on_commit(lambda: enqueue(rid))
        transaction.on_commit(lambda: _publish(rid, {"type": "status", "status": Status.QUEUED, "phase": "requeued"}))
        return "requeued"


_reconcile_lock = threading.Lock()
_last_reconcile = {"at": 0.0}


def maybe_reconcile(*, min_interval: float = 60.0, background: bool = True) -> bool:
    """Run :func:`reconcile` at most once per *min_interval* per process (API calls this opportunistically)."""
    with _reconcile_lock:
        if time.monotonic() - _last_reconcile["at"] < min_interval:
            return False
        _last_reconcile["at"] = time.monotonic()
    if background:
        from backend.common.threading import dispatch_in_thread

        dispatch_in_thread(_reconcile_quietly, name="analysis-reconcile")
    else:
        _reconcile_quietly(close=False)
    return True


def _reconcile_quietly(*, close: bool = True) -> None:
    try:
        if close:
            close_old_connections()
        reconcile()
    except Exception:
        logger.exception("Analysis reconcile failed")
    finally:
        if close:
            connection.close()


# --------------------------------------------------------------------------
# Sample
# --------------------------------------------------------------------------


def load_sample(run) -> Sample:
    """Re-resolve the run's sample for execution and verify its sample_hash.

    Raises ``SampleUnavailable`` when the target is gone or unresolvable,
    and :class:`SampleChanged` (``sample_changed``) when its code no longer
    matches what was submitted, or the same code is now attributed
    differently (``sample.attribution_changed``: a lost scaffold baseline
    re-attributes modified files whole), so results would describe other
    code or another measured scope than the run records.
    """
    scope = (run.plan or {}).get("scope")
    if run.generation_job_id is not None:
        sample = sample_mod.resolve_job(run.generation_job, scope)
    elif run.source_code is not None:
        sample = sample_mod.resolve_inline(run.source_code, scope)
    else:
        msg = "The run's target no longer exists (the generation job was deleted)"
        raise SampleUnavailable(msg, code="target_deleted")
    if run.sample_hash and sample.sample_hash != run.sample_hash:
        msg = (
            f"The target's code changed since this run was submitted (sample hash {run.sample_hash[:12]} -> "
            f"{sample.sample_hash[:12]}); submit a new run to analyse the current code"
        )
        raise SampleChanged(msg)
    changed = sample_mod.attribution_changed(run.sample_manifest, sample)
    if changed:
        # Same bytes, other origins/lines: findings would be counted against a scope that differs from
        # the stored denominator (measured SLOC). Fail closed rather than measure two scopes as one.
        msg = (
            f"The target's {changed}; its findings would not match the measured lines recorded at submit. "
            "Submit a new run to analyse it under the current attribution"
        )
        raise SampleChanged(msg)
    return sample
