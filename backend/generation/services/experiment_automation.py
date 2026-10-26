"""Experiment schedules and post-runs, ticked by the executor (spec §F.5).

The executor's main loop calls :func:`run_due_schedules` and
:func:`advance_post_runs` every 30 s; both only *claim* rows
(``FOR UPDATE SKIP LOCKED``) and the work runs in the post-run pool
(:func:`run_schedule_action`, ``execution.workers.run_post_run``). Errors are
recorded on the rows and never raised.

* A schedule (``ExperimentSchedule``) fires a cron action on its experiment:
  ``top_up`` (fill failed / cancelled cells; reopens a finished experiment),
  ``add_replicates`` (``repeats += n`` then top up; new repeat blocks are
  sequenced after the existing ones) or ``clone_and_run`` (a fresh copy,
  launched). ``next_run_at`` is always computed from ``max(now, next_run_at)``
  so an executor that was down never fires a burst of catch-up runs.
* A post-run (``ExperimentPostRun``) is created once per completion
  (:func:`on_experiment_completed`, called by ``maybe_complete_experiment``
  only when its conditional UPDATE won) or by hand. It runs the configured
  analyzers sequentially over the completed current-attempt jobs that have no
  analysis yet (the analyzer workspace's ``/work`` is shared), then the
  ``experiment_report``, publishing ``post_run`` events as it goes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timedelta
from itertools import pairwise
from typing import TYPE_CHECKING
from typing import Any
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from croniter import croniter
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from backend.generation.models import Experiment
from backend.generation.models import ExperimentPostRun
from backend.generation.models import ExperimentSchedule
from backend.generation.models import GenerationJob
from backend.realtime.events import publish_experiment_event

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.llm_models.models import LLMModel

logger = logging.getLogger(__name__)

CRON_PREVIEW_RUNS = 5
MAX_REPLICATES_PER_FIRE = 5
MAX_REPEATS = 100
DEFAULT_NAME_TEMPLATE = "{name} {date}"
_ACTIVE = (GenerationJob.Status.PENDING, GenerationJob.Status.RUNNING)


class ScheduleError(ValueError):
    """An invalid schedule (cron, timezone, params); the API returns it as a 400."""


# ── cron ──────────────────────────────────────────────────────────────


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        msg = f"Unknown timezone {name!r}"
        raise ScheduleError(msg) from exc


def next_runs(cron: str, tz: str, *, after: datetime | None = None, count: int = CRON_PREVIEW_RUNS) -> list[datetime]:
    """The next *count* fire times of *cron* in timezone *tz*, as aware datetimes (UTC-normalised)."""
    zone = _zone(tz)
    if not croniter.is_valid(cron or ""):
        msg = f"Invalid cron expression {cron!r}"
        raise ScheduleError(msg)
    start = (after or timezone.now()).astimezone(zone)
    it = croniter(cron, start)
    fields = cron.split()
    fixed_hour = len(fields) > 1 and "*" not in fields[1]
    runs: list[datetime] = []
    while len(runs) < count:
        candidate = it.get_next(datetime)
        if fixed_hour and _repeated_wall_time(candidate, zone):
            continue
        runs.append(candidate.astimezone(ZoneInfo("UTC")))
    return runs


def _repeated_wall_time(moment: datetime, zone: ZoneInfo) -> bool:
    """Whether *moment* is the second occurrence of an ambiguous local time (DST fall-back).

    A cron with a fixed hour ("30 2 * * *") already fired at the first
    occurrence an hour earlier; like cron itself, it does not fire again.
    Wildcard-hour crons keep firing through the repeated hour.
    """
    local = moment.astimezone(ZoneInfo("UTC")).astimezone(zone)
    naive = local.replace(tzinfo=None)
    first = naive.replace(tzinfo=zone, fold=0).utcoffset()
    second = naive.replace(tzinfo=zone, fold=1).utcoffset()
    return first != second and local.utcoffset() == second


def validate_cron(cron: str, tz: str) -> list[datetime]:
    """Check *cron* fires at most every ``SCHEDULE_MIN_INTERVAL_S``; returns the preview runs."""
    runs = next_runs(cron, tz, count=CRON_PREVIEW_RUNS + 1)
    minimum = int(settings.SCHEDULE_MIN_INTERVAL_S)
    gaps = [(b - a).total_seconds() for a, b in pairwise(runs)]
    if gaps and min(gaps) < minimum:
        msg = f"Schedules may fire at most every {minimum // 60} minutes"
        raise ScheduleError(msg)
    return runs[:CRON_PREVIEW_RUNS]


def cron_preview(cron: str, tz: str = "UTC") -> dict[str, Any]:
    """``{valid, detail, next_runs}`` for ``GET /generation/cron-preview/``."""
    try:
        runs = validate_cron(cron, tz)
    except ScheduleError as exc:
        return {"valid": False, "detail": str(exc), "next_runs": []}
    return {"valid": True, "detail": "", "next_runs": runs}


def validate_schedule(spec: dict[str, Any]) -> dict[str, Any]:
    """A ``ScheduleSpec`` as ``ExperimentSchedule`` field values (with ``next_run_at``); raises ``ExperimentError``."""
    from backend.generation.services.experiments import ExperimentError

    action = spec.get("action")
    if action not in ExperimentSchedule.Action.values:
        raise ExperimentError(400, f"Unknown schedule action {action!r}", "invalid_schedule")
    tz = spec.get("timezone") or "UTC"
    try:
        runs = validate_cron(spec.get("cron") or "", tz)
    except ScheduleError as exc:
        raise ExperimentError(400, str(exc), "invalid_schedule") from exc
    raw = spec.get("params") or {}
    params: dict[str, Any] = {}
    if action == ExperimentSchedule.Action.ADD_REPLICATES:
        replicates = int(raw.get("replicates") or 1)
        if not 1 <= replicates <= MAX_REPLICATES_PER_FIRE:
            raise ExperimentError(400, "replicates must be between 1 and 5", "invalid_schedule")
        params["replicates"] = replicates
    elif action == ExperimentSchedule.Action.CLONE_AND_RUN:
        template = (raw.get("name_template") or DEFAULT_NAME_TEMPLATE).strip()
        try:
            template.format(name="x", date="x")
        except (KeyError, IndexError, ValueError) as exc:
            msg = "name_template may only use {name} and {date}"
            raise ExperimentError(400, msg, "invalid_schedule") from exc
        params["name_template"] = template[:150]
    enabled = bool(spec.get("enabled", True))
    return {
        "action": action,
        "cron": spec["cron"].strip(),
        "timezone": tz,
        "params": params,
        "skip_if_active": bool(spec.get("skip_if_active", True)),
        "enabled": enabled,
        "next_run_at": runs[0] if enabled else None,
    }


def _advance(schedule: ExperimentSchedule, now: datetime) -> datetime | None:
    """The next fire time after ``max(now, next_run_at)`` (no burst catch-up)."""
    start = max(now, schedule.next_run_at) if schedule.next_run_at else now
    try:
        return next_runs(schedule.cron, schedule.timezone, after=start, count=1)[0]
    except ScheduleError:
        return None


def _draining() -> bool:
    from backend.execution.models import ExecutorSettings

    return bool(ExecutorSettings.get().draining)


def _is_active(experiment: Experiment) -> bool:
    return experiment.status == Experiment.Status.PAUSED or experiment.jobs.filter(status__in=_ACTIVE).exists()


def _active_detail(schedule: ExperimentSchedule) -> str | None:
    """Why ``skip_if_active`` skips this fire, or None.

    ``clone_and_run`` launches clones, not its source (often a template that never
    runs), so a clone it launched that is still queued, running or paused counts too:
    a sweep longer than the cron period must not pile up overlapping clones.
    """
    if _is_active(schedule.experiment):
        return "experiment still active"
    if schedule.action == ExperimentSchedule.Action.CLONE_AND_RUN:
        clones = Experiment.objects.filter(created_by_schedule=schedule)
        if clones.filter(
            Q(status=Experiment.Status.PAUSED) | Q(jobs__status__in=_ACTIVE),
        ).exists():
            return "previous clone still active"
    return None


# ── schedules ─────────────────────────────────────────────────────────


def run_due_schedules(now: datetime, submit: Callable | None = None) -> list[int]:
    """Claim enabled schedules with ``next_run_at <= now`` and advance ``next_run_at`` (croniter, schedule tz).

    Skips (``last_result={outcome: "skipped"}``) when ``skip_if_active`` and the
    experiment (for ``clone_and_run`` also a clone it launched) has PENDING/RUNNING
    jobs or is paused; otherwise hands the id
    to ``submit(run_schedule_action, schedule_id)`` (the post-run pool; when
    ``submit`` is None the caller submits the returned ids). Draining stops
    schedule firing. Returns the ids to run.
    """
    if _draining():
        return []
    to_run: list[int] = []
    with transaction.atomic():
        due = list(
            ExperimentSchedule.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("experiment")
            .filter(enabled=True, next_run_at__lte=now)
            .order_by("next_run_at"),
        )
        for schedule in due:
            schedule.next_run_at = _advance(schedule, now)
            experiment = schedule.experiment
            if experiment.status == Experiment.Status.ARCHIVED:
                schedule.enabled = False
                schedule.last_result = {"outcome": "skipped", "detail": "experiment archived"}
                schedule.last_run_at = now
            elif schedule.skip_if_active and (detail := _active_detail(schedule)):
                schedule.last_result = {"outcome": "skipped", "detail": detail}
                schedule.last_run_at = now
            else:
                to_run.append(schedule.id)
            schedule.save(update_fields=["next_run_at", "enabled", "last_result", "last_run_at"])
    if submit is not None:
        for schedule_id in to_run:
            submit(run_schedule_action, schedule_id)
    return to_run


def fast_forward_schedules(now: datetime) -> int:
    """Move ``next_run_at`` values more than one period in the past to the next future run (reconciler step 8)."""
    moved = 0
    for schedule in ExperimentSchedule.objects.filter(enabled=True, next_run_at__lt=now):
        try:
            following = next_runs(schedule.cron, schedule.timezone, after=schedule.next_run_at, count=1)[0]
        except ScheduleError:
            continue
        if following < now:  # more than one period behind
            schedule.next_run_at = _advance(schedule, now)
            schedule.save(update_fields=["next_run_at"])
            moved += 1
    return moved


def _clone_name(schedule: ExperimentSchedule, experiment: Experiment, now: datetime) -> str:
    template = (schedule.params or {}).get("name_template") or DEFAULT_NAME_TEMPLATE
    local = now.astimezone(_zone(schedule.timezone))
    return template.format(name=experiment.name, date=f"{local:%Y-%m-%d %H:%M}")[:200]


def _has_uncovered_cells(experiment: Experiment) -> bool:
    """Whether a top-up of *experiment* would create any job (a failed, cancelled or new cell)."""
    from backend.generation.services import experiments as svc

    covered = svc._covered_cells(experiment)  # noqa: SLF001
    condition_ids = list(experiment.conditions.values_list("id", flat=True))
    app_ids = list(experiment.app_requirements.values_list("id", flat=True))
    return any(
        (condition_id, app_id, repeat) not in covered
        for condition_id in condition_ids
        for app_id in app_ids
        for repeat in range(experiment.repeats)
    )


def run_schedule_action(schedule_id: int) -> dict[str, Any]:
    """Fire one schedule: ``top_up`` / ``add_replicates`` / ``clone_and_run``; returns and stores ``last_result``.

    ``digest_drift`` blocks ``top_up``/``add_replicates`` and is recorded as
    ``{outcome: "error", detail: "digest_drift"}``. Errors are recorded, never raised.
    """
    from backend.generation.models import GenerationBatch
    from backend.generation.services import experiments as svc

    schedule = (
        ExperimentSchedule.objects.select_related("experiment", "experiment__created_by")
        .filter(
            id=schedule_id,
        )
        .first()
    )
    if schedule is None:
        return {"outcome": "error", "detail": "schedule not found"}
    experiment = schedule.experiment
    owner = experiment.created_by
    now = timezone.now()
    result: dict[str, Any]
    if schedule.action != ExperimentSchedule.Action.CLONE_AND_RUN and experiment.status == Experiment.Status.DRAFT:
        # A top-up of a draft would be its first launch: launching is the user's call.
        result = {"outcome": "skipped", "detail": "experiment not launched yet", "jobs_created": 0}
        ExperimentSchedule.objects.filter(id=schedule_id).update(last_result=result, last_run_at=now)
        return result
    if schedule.action == ExperimentSchedule.Action.TOP_UP and not _has_uncovered_cells(experiment):
        # Every cell already has a live or completed job: no empty batch per fire.
        result = {"outcome": "skipped", "detail": "nothing to top up", "jobs_created": 0}
        ExperimentSchedule.objects.filter(id=schedule_id).update(last_result=result, last_run_at=now)
        return result
    clone = None
    try:
        if schedule.action == ExperimentSchedule.Action.CLONE_AND_RUN:
            clone = svc.clone_experiment(
                experiment,
                owner,
                name=_clone_name(schedule, experiment, now),
                include_schedules=False,
                created_by_schedule=schedule,
            )
            batch = svc.launch_experiment(clone, owner, kind=GenerationBatch.Kind.SCHEDULE)
            result = {"experiment_id": str(clone.id)}
        else:
            # One transaction: a refused launch also rolls back the added repeats,
            # so a schedule that keeps failing never inflates the design.
            with transaction.atomic():
                if schedule.action == ExperimentSchedule.Action.ADD_REPLICATES:
                    locked = Experiment.objects.select_for_update().get(pk=experiment.pk)
                    if svc._digest_drift(svc._conditions(locked)):  # noqa: SLF001
                        raise svc.ExperimentError(400, "digest_drift", "digest_drift")
                    n = int((schedule.params or {}).get("replicates") or 1)
                    locked.repeats = min(MAX_REPEATS, locked.repeats + n)
                    locked.save(update_fields=["repeats", "updated_at"])
                batch = svc.launch_experiment(experiment, owner, mode=svc.TOP_UP, kind=GenerationBatch.Kind.SCHEDULE)
            result = {"experiment_id": str(experiment.id)}
        jobs_created = batch.jobs.exclude(status=GenerationJob.Status.FAILED).count()
        result |= {"outcome": "fired", "batch_id": str(batch.id), "jobs_created": jobs_created, "detail": ""}
    except svc.ExperimentError as exc:
        detail = "digest_drift" if exc.code == "digest_drift" else exc.detail
        result = {"outcome": "error", "detail": detail, "code": exc.code, "jobs_created": 0}
    except Exception as exc:
        logger.exception("Schedule %s failed", schedule_id)
        result = {"outcome": "error", "detail": f"{type(exc).__name__}: {exc}"[:500], "jobs_created": 0}
    if clone is not None and result["outcome"] == "error":
        # The clone was created (a draft) but its launch failed: point at it.
        result["experiment_id"] = str(clone.id)
    ExperimentSchedule.objects.filter(id=schedule_id).update(last_result=result, last_run_at=now)
    return result


# ── post-runs ─────────────────────────────────────────────────────────


def _publish_post_run(post_run: ExperimentPostRun) -> None:
    publish_experiment_event(
        post_run.experiment_id,
        {"type": "post_run", "id": post_run.id, "status": post_run.status, "progress": dict(post_run.progress or {})},
    )


def _wants_post_run(config: dict[str, Any]) -> bool:
    return bool((config.get("analysis") or {}).get("enabled") or (config.get("report") or {}).get("enabled"))


def on_experiment_completed(experiment: Experiment) -> ExperimentPostRun | None:
    """Called once per completion by ``maybe_complete_experiment``: create ``ExperimentPostRun(trigger=completed)``
    with a frozen copy of ``experiment.post_run`` when analysis or report is enabled; else None."""
    from backend.generation.services.experiments import effective_post_run

    config = effective_post_run(experiment)
    if not _wants_post_run(config):
        return None
    post_run = ExperimentPostRun.objects.create(
        experiment=experiment,
        trigger=ExperimentPostRun.Trigger.COMPLETED,
        config=config,
    )
    transaction.on_commit(lambda: _publish_post_run(post_run))
    _wake_on_commit()
    return post_run


def create_manual_post_run(experiment: Experiment) -> ExperimentPostRun:
    """``POST …/post-run/run/``: queue a post-run now.

    ``ExperimentError`` (400) when one is already active or nothing is configured.
    """
    from backend.generation.services.experiments import ExperimentError
    from backend.generation.services.experiments import effective_post_run

    with transaction.atomic():
        Experiment.objects.select_for_update().filter(pk=experiment.pk).values_list("pk").first()
        now = timezone.now()
        # A running row whose lease lapsed belongs to a dead executor (restart, crash);
        # a manual re-run supersedes it instead of being refused forever.
        stale = list(
            experiment.post_runs.select_for_update()
            .filter(status=ExperimentPostRun.Status.RUNNING)
            .filter(Q(lease_expires_at__lt=now) | Q(lease_expires_at__isnull=True))
            .values_list("pk", flat=True),
        )
        if stale:
            ExperimentPostRun.objects.filter(pk__in=stale).update(
                status=ExperimentPostRun.Status.CANCELLED,
                lease_owner="",
                lease_expires_at=None,
                completed_at=now,
                error="Interrupted (executor restart); superseded by a manual re-run.",
            )
        active = experiment.post_runs.filter(
            status__in=(ExperimentPostRun.Status.PENDING, ExperimentPostRun.Status.RUNNING),
        )
        if active.exists():
            raise ExperimentError(
                400,
                "A post-run is already pending or running for this experiment.",
                "post_run_active",
            )
        config = effective_post_run(experiment)
        if not _wants_post_run(config):
            raise ExperimentError(400, "Enable post-run analysis or the report first.", "post_run_disabled")
        post_run = ExperimentPostRun.objects.create(
            experiment=experiment,
            trigger=ExperimentPostRun.Trigger.MANUAL,
            config=config,
        )
        _wake_on_commit()
    return post_run


def _wake_on_commit() -> None:
    from backend.execution.api import wake

    transaction.on_commit(wake)


def _post_run_deferred() -> bool:
    """Whether the memory rule needs post-runs to wait while sandboxes and evaluations fill their slots (§C.10).

    The analyzer workspace only fits next to full sandbox + eval lanes when
    the memory formula (``execution.capacity.check_memory``) holds with the
    post-run term included.
    """
    from backend.execution.capacity import check_memory
    from backend.execution.models import ExecutorSettings

    executor_settings = ExecutorSettings.get()
    try:
        fits = check_memory(
            int(executor_settings.sandbox_slots),
            int(executor_settings.eval_slots),
            int(executor_settings.post_run_slots),
        ).ok
    except ValueError:
        return False
    if fits:
        return False
    running = GenerationJob.objects.filter(status=GenerationJob.Status.RUNNING, queued_at__isnull=False)
    sandbox_used = running.filter(lane=GenerationJob.Lane.SANDBOX, phase=GenerationJob.Phase.RUNNING).count()
    eval_used = running.filter(phase=GenerationJob.Phase.EVALUATING).count()
    return sandbox_used + eval_used >= executor_settings.sandbox_slots + executor_settings.eval_slots


def claimable_post_runs(now=None):
    """Post-runs an executor may start: pending ones, and running ones whose lease expired (restart, crash)."""
    now = now or timezone.now()
    return ExperimentPostRun.objects.filter(
        Q(status=ExperimentPostRun.Status.PENDING)
        | Q(status=ExperimentPostRun.Status.RUNNING, lease_expires_at__lt=now)
        | Q(status=ExperimentPostRun.Status.RUNNING, lease_expires_at__isnull=True),
    )


def _claim_post_run(executor_id: str) -> ExperimentPostRun | None:
    now = timezone.now()
    lease = timedelta(seconds=int(settings.EXECUTOR_LEASE_SECONDS))
    with transaction.atomic():
        post_run = claimable_post_runs(now).select_for_update(skip_locked=True).order_by("created_at").first()
        if post_run is None:
            return None
        post_run.status = ExperimentPostRun.Status.RUNNING
        post_run.lease_owner = executor_id
        post_run.lease_expires_at = now + lease
        post_run.started_at = post_run.started_at or now
        post_run.save(update_fields=["status", "lease_owner", "lease_expires_at", "started_at"])
    return post_run


def _renew(post_run: ExperimentPostRun) -> bool:
    """Extend the lease; False when another executor took the row over (stop quietly)."""
    renewed = ExperimentPostRun.objects.filter(
        id=post_run.id,
        status=ExperimentPostRun.Status.RUNNING,
        lease_owner=post_run.lease_owner,
    ).update(lease_expires_at=timezone.now() + timedelta(seconds=int(settings.EXECUTOR_LEASE_SECONDS)))
    return bool(renewed)


def _save_progress(post_run: ExperimentPostRun, **fields: Any) -> None:
    for name, value in fields.items():
        setattr(post_run, name, value)
    ExperimentPostRun.objects.filter(id=post_run.id).update(**fields)
    _publish_post_run(post_run)


def analysis_targets(experiment: Experiment, *, only_passing: bool) -> list[GenerationJob]:
    """Completed current-attempt jobs of *experiment* that have no analysis run yet."""
    from backend.analysis.models import AnalysisRun
    from backend.generation.services.experiments import current_jobs

    rows = current_jobs(experiment, ("id", "status", "metrics__functional__pass_rate"))
    ids = [r["id"] for r in rows if r["status"] == GenerationJob.Status.COMPLETED]
    if only_passing:
        ids = [
            r["id"]
            for r in rows
            if r["status"] == GenerationJob.Status.COMPLETED and r["metrics__functional__pass_rate"] == 1
        ]
    analyzed = set(
        AnalysisRun.objects.filter(generation_job_id__in=ids)
        .exclude(status=AnalysisRun.Status.CANCELLED)
        .values_list("generation_job_id", flat=True),
    )
    return list(GenerationJob.objects.filter(id__in=[i for i in ids if i not in analyzed]).order_by("created_at"))


def _run_analyses(post_run: ExperimentPostRun, experiment: Experiment, config: dict[str, Any]) -> bool:
    """Analyze each target job sequentially; returns False when the lease was lost."""
    from backend.analysis.models import AnalysisRun
    from backend.analysis.services import runner
    from backend.analysis.services import workspace_service

    analysis = config.get("analysis") or {}
    targets = analysis_targets(experiment, only_passing=bool(analysis.get("only_passing")))
    user = experiment.created_by
    workspace = workspace_service.get_workspace(user)
    progress = {"analysis_done": 0, "analysis_total": len(targets), "analysis_failed": 0}
    run_ids: list[str] = list(post_run.analysis_run_ids or [])
    _save_progress(post_run, progress=progress)
    for job in targets:
        if not _renew(post_run):
            return False
        run = AnalysisRun.objects.create(
            name=f"Experiment {experiment.slug} post-run",
            created_by=user,
            workspace=workspace,
            generation_job=job,
            tool_slugs=list(analysis.get("tool_slugs") or []),
        )
        run_ids.append(str(run.id))
        try:
            runner.execute(AnalysisRun.objects.select_related("created_by", "workspace").get(id=run.id))
            run.refresh_from_db(fields=["status"])
            failed = run.status == AnalysisRun.Status.FAILED
        except Exception:
            logger.exception("Post-run analysis %s failed", run.id)
            AnalysisRun.objects.filter(id=run.id).update(
                status=AnalysisRun.Status.FAILED,
                error_message="Internal error during analysis",
                completed_at=timezone.now(),
            )
            failed = True
        progress = {
            **progress,
            "analysis_done": progress["analysis_done"] + 1,
            "analysis_failed": progress["analysis_failed"] + int(failed),
        }
        _save_progress(post_run, progress=progress, analysis_run_ids=run_ids)
    return True


def _run_report(post_run: ExperimentPostRun, experiment: Experiment, config: dict[str, Any]) -> None:
    from backend.reports.services.report_service import create_and_run

    report_config = config.get("report") or {}
    report = create_and_run(
        report_type="experiment_report",
        config={
            "experiment_id": str(experiment.id),
            "exclude_truncated": bool(report_config.get("exclude_truncated", True)),
        },
        user=experiment.created_by,
        description=f"Generated by post-run #{post_run.id} ({post_run.trigger})",
    )
    _save_progress(post_run, report=report)
    if report.status != report.Status.COMPLETED:
        msg = report.error_message or "report generation failed"
        raise RuntimeError(msg)


def run_post_run(post_run: ExperimentPostRun) -> ExperimentPostRun:
    """Run a claimed post-run to completion (analyses, then the report); records failure on the row."""
    experiment = Experiment.objects.select_related("created_by").get(pk=post_run.experiment_id)
    config = post_run.config if isinstance(post_run.config, dict) else {}
    try:
        if (config.get("analysis") or {}).get("enabled") and not _run_analyses(post_run, experiment, config):
            return post_run  # lease lost: the new owner finishes it
        if (config.get("report") or {}).get("enabled"):
            _run_report(post_run, experiment, config)
    except Exception as exc:
        logger.exception("Post-run %s failed", post_run.id)
        _save_progress(
            post_run,
            status=ExperimentPostRun.Status.FAILED,
            error=f"{type(exc).__name__}: {exc}"[:2000],
            completed_at=timezone.now(),
            lease_owner="",
            lease_expires_at=None,
        )
        return post_run
    _save_progress(
        post_run,
        status=ExperimentPostRun.Status.COMPLETED,
        completed_at=timezone.now(),
        lease_owner="",
        lease_expires_at=None,
    )
    return post_run


def advance_post_runs(executor_id: str) -> ExperimentPostRun | None:
    """Claim one pending post-run (SKIP LOCKED, leased to ``executor_id``) and run it to completion.

    Analyses run sequentially over completed current-attempt jobs without an
    analysis (``only_passing`` → ``pass_rate == 1``), then the
    ``experiment_report``; progress is published as ``post_run`` events.
    Deferred (returns None) while the memory rule needs the sandbox and
    evaluation lanes to drain first, and while draining. Returns the
    post-run it processed, or None.
    """
    if _draining() or _post_run_deferred():
        return None
    post_run = _claim_post_run(executor_id)
    if post_run is None:
        return None
    _publish_post_run(post_run)
    return run_post_run(post_run)


# ── digest drift ──────────────────────────────────────────────────────


def pause_for_digest_drift(model: LLMModel) -> list[Any]:
    """Pause every RUNNING experiment with PENDING jobs whose frozen digest for ``model`` differs from
    ``model.digest`` (``pause_reason="digest_changed"``, ``state`` event). Called by inventory (Track B).
    Returns the paused experiment ids."""
    pending = (
        GenerationJob.objects.filter(
            model=model,
            status=GenerationJob.Status.PENDING,
            experiment__status=Experiment.Status.RUNNING,
        )
        .exclude(resolved_bundle__endpoint__digest=model.digest or "")
        .values_list("experiment_id", flat=True)
        .distinct()
    )
    paused: list[Any] = []
    now = timezone.now()
    for experiment_id in list(pending):
        with transaction.atomic():
            updated = Experiment.objects.filter(id=experiment_id, status=Experiment.Status.RUNNING).update(
                status=Experiment.Status.PAUSED,
                pause_reason=Experiment.PauseReason.DIGEST_CHANGED,
                paused_at=now,
                updated_at=now,
            )
            if updated:
                paused.append(experiment_id)
                event = {
                    "type": "state",
                    "status": Experiment.Status.PAUSED,
                    "pause_reason": Experiment.PauseReason.DIGEST_CHANGED,
                }
                transaction.on_commit(lambda eid=experiment_id, ev=event: publish_experiment_event(eid, ev))
    return paused
