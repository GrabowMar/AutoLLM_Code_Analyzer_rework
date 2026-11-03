"""Study schedules and post-runs (port of v2 ``experiment_automation``; spec sections 3.6, 9.4).

The executor calls :func:`fire_due_schedules` every 30 s and hands leased post-runs to :func:`run_post_run`
(``execution.workers``). Errors are recorded on the rows and never raised, except ``NotImplementedError``
from an area that has not landed yet (the worker then puts the post-run back to pending).

* **Schedules** (``StudySchedule``) fire a cron action on a study: ``top_up`` (refill infra / stopped cells;
  reopens a finished study), ``add_replicates`` (``params.replicates`` repeats beyond the plan, exploratory
  only, runs labelled ``beyond_plan``) or ``clone_and_run`` (an exact copy with a fresh ``base_seed`` derived
  from the source and the fire time, so the clone is an independent replication with its own plan hash,
  then launched). ``next_run_at`` advances from ``max(now, next_run_at)``: an executor that was down never
  fires a burst of catch-up runs (the no-burst rule; v2's reconciler fast-forward is folded in here).
  Draining stops schedule firing. ``skip_if_active`` skips a fire while the study (or, for
  ``clone_and_run``, a clone it launched) still has pending or running runs, or is paused.
* **Post-runs** (``StudyPostRun``): ``analysis`` first re-evaluates the retest sample when its config says
  ``retest: true`` (``evaluation.retest.run_retests``; an app study gets such a post-run, with no tools and
  no report, at launch), then runs the configured analyzers sequentially over the study's
  usable current main-stage runs that a configured tool has not measured yet (``only_passing`` keeps runs
  with pass rate 1), then the study conditions report; ``patterns`` runs the post-run's pattern analysis,
  creating it first when the post-run has none (WP-13: ``patterns.create_pattern_run(..., queue=False)`` pins
  the detector set and labels the run planned/exploratory, then ``patterns.run``). A slot study gets a
  ``patterns`` post-run at launch (``config = {"detectors": "default"}``); its detectors are pinned when it
  runs, after the variants exist.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timedelta
from itertools import pairwise
from typing import TYPE_CHECKING
from typing import Any
from typing import Final
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from backend.generation import identity
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models import StudyPostRun
from backend.generation.models import StudySchedule
from backend.generation.models.choices import PostRunKind
from backend.generation.models.choices import PostRunTrigger
from backend.generation.models.choices import RunStatus
from backend.generation.models.choices import ScheduleAction
from backend.generation.models.choices import StudyStatus
from backend.generation.models.choices import WorkStatus
from backend.generation.study.errors import StudyError

if TYPE_CHECKING:
    from backend.users.models import User

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_REPLICATES_PER_FIRE",
    "ScheduleError",
    "analysis_targets",
    "create_post_run",
    "cron_preview",
    "fire_due_schedules",
    "next_runs",
    "run_post_run",
    "run_schedule_action",
    "validate_schedule",
]

CRON_PREVIEW_RUNS: Final = 5
MAX_REPLICATES_PER_FIRE: Final = 5
DEFAULT_NAME_TEMPLATE: Final = "{name} {date}"
_ACTIVE: Final = (RunStatus.PENDING, RunStatus.RUNNING)
_ANALYSIS_DEFAULTS: Final = {"tool_slugs": [], "only_passing": False, "report": True, "retest": False}


class ScheduleError(ValueError):
    """An invalid schedule (cron, timezone, params); the API answers 400."""


# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        msg = f"Unknown timezone {name!r}"
        raise ScheduleError(msg) from exc


def _repeated_wall_time(moment: datetime, zone: ZoneInfo) -> bool:
    """Whether ``moment`` is the second occurrence of an ambiguous local time (DST fall-back)."""
    local = moment.astimezone(ZoneInfo("UTC")).astimezone(zone)
    naive = local.replace(tzinfo=None)
    first = naive.replace(tzinfo=zone, fold=0).utcoffset()
    second = naive.replace(tzinfo=zone, fold=1).utcoffset()
    return first != second and local.utcoffset() == second


def next_runs(cron: str, tz: str, *, after: datetime | None = None, count: int = CRON_PREVIEW_RUNS) -> list[datetime]:
    """The next ``count`` fire times of ``cron`` in timezone ``tz`` (aware, UTC-normalised)."""
    from croniter import croniter

    zone = _zone(tz)
    if not croniter.is_valid(cron or ""):
        msg = f"Invalid cron expression {cron!r}"
        raise ScheduleError(msg)
    it = croniter(cron, (after or timezone.now()).astimezone(zone))
    fields = cron.split()
    fixed_hour = len(fields) > 1 and "*" not in fields[1]
    runs: list[datetime] = []
    while len(runs) < count:
        candidate = it.get_next(datetime)
        if fixed_hour and _repeated_wall_time(candidate, zone):
            continue
        runs.append(candidate.astimezone(ZoneInfo("UTC")))
    return runs


def validate_cron(cron: str, tz: str) -> list[datetime]:
    """Check ``cron`` fires at most every ``SCHEDULE_MIN_INTERVAL_S``; returns the preview runs."""
    runs = next_runs(cron, tz, count=CRON_PREVIEW_RUNS + 1)
    minimum = int(settings.SCHEDULE_MIN_INTERVAL_S)
    gaps = [(b - a).total_seconds() for a, b in pairwise(runs)]
    if gaps and min(gaps) < minimum:
        msg = f"Schedules may fire at most every {minimum // 60} minutes"
        raise ScheduleError(msg)
    return runs[:CRON_PREVIEW_RUNS]


def cron_preview(cron: str, tz: str = "UTC") -> dict[str, Any]:
    """``{valid, detail, next_runs}``."""
    try:
        runs = validate_cron(cron, tz)
    except ScheduleError as exc:
        return {"valid": False, "detail": str(exc), "next_runs": []}
    return {"valid": True, "detail": "", "next_runs": runs}


def validate_schedule(spec: dict[str, Any]) -> dict[str, Any]:
    """A schedule spec as ``StudySchedule`` field values (with ``next_run_at``); raises :class:`ScheduleError`."""
    action = spec.get("action")
    if action not in ScheduleAction.values:
        msg = f"Unknown schedule action {action!r}"
        raise ScheduleError(msg)
    tz = spec.get("timezone") or "UTC"
    runs = validate_cron(spec.get("cron") or "", tz)
    raw = spec.get("params") or {}
    params: dict[str, Any] = {}
    if action == ScheduleAction.ADD_REPLICATES:
        replicates = int(raw.get("replicates") or 1)
        if not 1 <= replicates <= MAX_REPLICATES_PER_FIRE:
            msg = f"replicates must be between 1 and {MAX_REPLICATES_PER_FIRE}"
            raise ScheduleError(msg)
        params["replicates"] = replicates
    elif action == ScheduleAction.CLONE_AND_RUN:
        template = (raw.get("name_template") or DEFAULT_NAME_TEMPLATE).strip()
        try:
            template.format(name="x", date="x")
        except (KeyError, IndexError, ValueError) as exc:
            msg = "name_template may only use {name} and {date}"
            raise ScheduleError(msg) from exc
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


def _advance(schedule: StudySchedule, now: datetime) -> datetime | None:
    """The next fire time after ``max(now, next_run_at)`` (no burst catch-up)."""
    start = max(now, schedule.next_run_at) if schedule.next_run_at else now
    try:
        return next_runs(schedule.cron, schedule.timezone, after=start, count=1)[0]
    except ScheduleError:
        return None


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


def _draining() -> bool:
    from backend.execution.models import ExecutorSettings

    return bool(ExecutorSettings.get().draining)


def _study_active(study: Study) -> bool:
    return study.status == StudyStatus.PAUSED or study.runs.filter(status__in=_ACTIVE).exists()


def _active_detail(schedule: StudySchedule) -> str | None:
    if _study_active(schedule.study):
        return "study still active"
    if schedule.action == ScheduleAction.CLONE_AND_RUN:
        clones = Study.objects.filter(cloned_from=schedule.study, created_at__gte=schedule.created_at)
        if clones.filter(Q(status=StudyStatus.PAUSED) | Q(runs__status__in=_ACTIVE)).exists():
            return "previous clone still active"
    return None


def fire_due_schedules(now: datetime | None = None) -> int:
    """Fire every due ``StudySchedule`` (claimed ``FOR UPDATE SKIP LOCKED``); returns the number fired."""
    now = now or timezone.now()
    if _draining():
        return 0
    to_run: list[int] = []
    with transaction.atomic():
        due = list(
            StudySchedule.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("study")
            .filter(enabled=True, next_run_at__lte=now)
            .order_by("next_run_at"),
        )
        for schedule in due:
            schedule.next_run_at = _advance(schedule, now)
            if schedule.study.status == StudyStatus.CANCELLED and schedule.action != ScheduleAction.CLONE_AND_RUN:
                schedule.enabled = False
                schedule.last_result = {"outcome": "skipped", "detail": "study cancelled"}
                schedule.last_run_at = now
            elif schedule.skip_if_active and (detail := _active_detail(schedule)):
                schedule.last_result = {"outcome": "skipped", "detail": detail}
                schedule.last_run_at = now
            else:
                to_run.append(schedule.pk)
            schedule.save(update_fields=["next_run_at", "enabled", "last_result", "last_run_at"])
    for schedule_id in to_run:
        run_schedule_action(schedule_id, now=now)
    return len(to_run)


def _clone_name(schedule: StudySchedule, study: Study, now: datetime) -> str:
    template = (schedule.params or {}).get("name_template") or DEFAULT_NAME_TEMPLATE
    local = now.astimezone(_zone(schedule.timezone))
    return template.format(name=study.name, date=f"{local:%Y-%m-%d %H:%M}")[:200]


def _clone_seed(study: Study, now: datetime) -> int:
    """A fresh base seed for a scheduled replication (derived, so it is reproducible from the record)."""
    return int(identity.H({"clone_of": str(study.pk), "at": now.isoformat()})[:12], 16) % 2**31


def run_schedule_action(schedule_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Fire one schedule; returns and stores ``last_result``. Errors are recorded, never raised."""
    from backend.generation.study import launch as svc
    from backend.generation.study import plan as planner

    now = now or timezone.now()
    schedule = StudySchedule.objects.select_related("study", "study__created_by").filter(pk=schedule_id).first()
    if schedule is None:
        return {"outcome": "error", "detail": "schedule not found"}
    study = schedule.study
    owner = study.created_by
    result: dict[str, Any]
    if schedule.action != ScheduleAction.CLONE_AND_RUN and study.status in (StudyStatus.DRAFT, StudyStatus.PLANNED):
        result = {"outcome": "skipped", "detail": "study not launched yet", "runs_created": 0}
        StudySchedule.objects.filter(pk=schedule_id).update(last_result=result, last_run_at=now)
        return result
    clone = None
    try:
        if schedule.action == ScheduleAction.CLONE_AND_RUN:
            clone, _ = svc.clone_with_diffs(
                study,
                mode="exact",
                name=_clone_name(schedule, study, now),
                user=owner,
                base_seed=_clone_seed(study, now),
            )
            bundle = planner.resolve_bundle(clone, use_cache=False)
            # The source's pilot already vetted this design: a replication goes straight to main.
            piloted = study.cells.filter(stage="main").exists()
            launched = svc.launch(
                clone,
                bundle.plan.plan_hash,
                user=owner,
                confirm_usd=_auto_confirm(bundle),
                pilot_override=piloted,
            )
            result = {"study_id": str(clone.pk), "runs_created": launched["created_runs"]}
        elif schedule.action == ScheduleAction.ADD_REPLICATES:
            n = int((schedule.params or {}).get("replicates") or 1)
            added = svc.add_replicates(study, n, user=owner)
            result = {"study_id": str(study.pk), "runs_created": added["created_runs"]}
        else:
            topped = svc.top_up(study, "infra", reason=f"schedule {schedule_id}", user=owner)
            missing = svc.top_up(study, "missing", reason=f"schedule {schedule_id}", user=owner)
            created = topped["created_runs"] + missing["created_runs"]
            if not created:
                result = {"outcome": "skipped", "detail": "nothing to top up", "runs_created": 0}
                StudySchedule.objects.filter(pk=schedule_id).update(last_result=result, last_run_at=now)
                return result
            result = {"study_id": str(study.pk), "runs_created": created}
        result |= {"outcome": "fired", "detail": ""}
    except StudyError as exc:
        result = {"outcome": "error", "detail": exc.detail, "code": exc.code, "runs_created": 0}
    except Exception as exc:
        logger.exception("Schedule %s failed", schedule_id)
        result = {"outcome": "error", "detail": f"{type(exc).__name__}: {exc}"[:500], "runs_created": 0}
    if clone is not None and result["outcome"] == "error":
        result["study_id"] = str(clone.pk)  # the clone exists (a draft) although its launch failed
    StudySchedule.objects.filter(pk=schedule_id).update(last_result=result, last_run_at=now)
    return result


def _auto_confirm(bundle: Any) -> float | None:
    """A scheduled launch confirms its own estimate (the schedule's owner approved the source study)."""
    estimate = bundle.plan.estimate
    return estimate.p90_total if estimate is not None else None


# ---------------------------------------------------------------------------
# Post-runs
# ---------------------------------------------------------------------------


def _publish(post_run: StudyPostRun) -> None:
    from backend.realtime.events import publish_study_event

    event = {
        "type": "post_run",
        "id": post_run.pk,
        "kind": post_run.kind,
        "status": post_run.status,
        "progress": dict(post_run.progress or {}),
    }
    transaction.on_commit(lambda: publish_study_event(post_run.study_id, event))


def normalize_post_run_config(kind: str, config: dict[str, Any] | None) -> dict[str, Any]:
    """The frozen config of a post-run (analysis: tools, only_passing, report; patterns: detectors)."""
    raw = dict(config or {})
    if kind == PostRunKind.PATTERNS:
        detectors = raw.get("detectors", "default")
        if detectors != "default" and (
            not isinstance(detectors, list) or not all(isinstance(d, str) and "@" in d for d in detectors)
        ):
            msg = "detectors must be 'default' or a list of pinned detector refs"
            raise ScheduleError(msg)
        out: dict[str, Any] = {"detectors": detectors}
        for key in ("cluster_params", "scope", "normalizer_version"):
            if key in raw:
                out[key] = raw[key]
        return out
    tools = raw.get("tool_slugs") or []
    if not isinstance(tools, list) or not all(isinstance(t, str) and t for t in tools):
        msg = "tool_slugs must be a list of analyzer slugs"
        raise ScheduleError(msg)
    out = {
        "tool_slugs": list(dict.fromkeys(tools)),
        "only_passing": bool(raw.get("only_passing", False)),
        "report": bool(raw.get("report", True)),
    }
    if raw.get("retest"):
        out["retest"] = True
    return out


def create_post_run(study: Study, *, kind: str, trigger: str, config: dict[str, Any] | None) -> StudyPostRun:
    """Queue a post-run: ``manual`` runs now, ``completed`` waits for the study to complete or stop."""
    if trigger not in (PostRunTrigger.MANUAL, PostRunTrigger.COMPLETED):
        msg = "trigger must be manual or completed"
        raise ScheduleError(msg)
    frozen = normalize_post_run_config(kind, config)
    with transaction.atomic():
        Study.objects.select_for_update(of=("self",)).filter(pk=study.pk).values_list("pk").first()
        active = study.post_runs.filter(kind=kind, status__in=(WorkStatus.PENDING, WorkStatus.RUNNING))
        if trigger == PostRunTrigger.MANUAL and active.filter(trigger=PostRunTrigger.MANUAL).exists():
            raise StudyError(400, "post_run_active", "a manual post-run of this kind is already queued or running")
        post_run = StudyPostRun.objects.create(study=study, kind=kind, trigger=trigger, config=frozen)
        _publish(post_run)
        from backend.execution.api import wake

        transaction.on_commit(wake)
    return post_run


def _renew(post_run: StudyPostRun) -> bool:
    """Extend the lease; False when another executor took the row over (stop quietly)."""
    renewed = StudyPostRun.objects.filter(
        pk=post_run.pk,
        status=WorkStatus.RUNNING,
        lease_owner=post_run.lease_owner,
    ).update(lease_expires_at=timezone.now() + timedelta(seconds=int(settings.EXECUTOR_LEASE_SECONDS)))
    return bool(renewed)


def _save(post_run: StudyPostRun, **fields: Any) -> None:
    for name, value in fields.items():
        setattr(post_run, name, value)
    StudyPostRun.objects.filter(pk=post_run.pk).update(**fields)
    _publish(post_run)


def analysis_targets(study: Study, *, only_passing: bool, tool_slugs: list[str]) -> list[GenerationRun]:
    """Usable current main-stage runs that a configured tool has not measured yet (``missing_tool_slugs`` set).

    A run counts as analysed by a tool only when that tool measured it (latest result completed); a
    not-applicable tool is recorded but is no measurement (spec 8.8), so it is not re-run either (the tree
    is immutable). Pass rates come from ``metrics.eval`` only when measured.
    """
    from backend.analysis.models import ToolResult
    from backend.generation.runs.queries import current_runs

    runs = list(current_runs(study, "main").filter(usable=True, status=RunStatus.COMPLETED).order_by("created_at"))
    if only_passing:
        runs = [
            run
            for run in runs
            if ((run.metrics or {}).get("eval") or {}).get("measured")
            and ((run.metrics or {}).get("eval") or {}).get("agent_pass_rate") == 1
        ]
    ids = [run.pk for run in runs]
    decided: dict[Any, set[str]] = {}
    latest = ToolResult.objects.filter(id__in=ToolResult.latest_ids_per_job(ids)).filter(
        Q(status=ToolResult.Status.COMPLETED)
        | Q(status=ToolResult.Status.SKIPPED, summary__skip_reason="not_applicable"),
    )
    for run_id, slug in latest.values_list("run__generation_job_id", "tool_slug"):
        decided.setdefault(run_id, set()).add(slug)
    targets = []
    for run in runs:
        missing = [slug for slug in tool_slugs if slug not in decided.get(run.pk, set())]
        if missing:
            run.missing_tool_slugs = missing  # type: ignore[attr-defined]
            targets.append(run)
    return targets


def _run_retests(post_run: StudyPostRun, study: Study) -> None:
    """Re-evaluate the study's retest sample (``evaluation.retest.run_retests``, WP-09; spec 8.5).

    ``retest_agreement`` (results) reads the rows; runs already retested are skipped, so a post-run that
    restarts after a lost lease does not retest twice.
    """
    from backend.generation.evaluation.retest import run_retests

    created = run_retests(study)
    _save(post_run, progress={**dict(post_run.progress or {}), "retests": len(created)})


def _run_analyses(post_run: StudyPostRun, study: Study, config: dict[str, Any]) -> bool:
    """Analyse each target sequentially; False when the lease was lost."""
    from backend.analysis.models import AnalysisRun
    from backend.analysis.services import runner
    from backend.analysis.services import workspace_service

    run_ids: list[str] = list(post_run.analysis_run_ids or [])
    if run_ids:
        runner.reap_stale_runs(timedelta(seconds=2 * runner.HEARTBEAT_INTERVAL_S), run_ids=run_ids)
    tools = list(config.get("tool_slugs") or [])
    if not tools:
        return True
    targets = analysis_targets(study, only_passing=bool(config.get("only_passing")), tool_slugs=tools)
    user = study.created_by
    workspace = workspace_service.get_workspace(user)
    progress = {"analysis_done": 0, "analysis_total": len(targets), "analysis_failed": 0}
    _save(post_run, progress=progress)
    for run in targets:
        if not _renew(post_run):
            return False
        analysis = AnalysisRun.objects.create(
            name=f"Study {study.slug} post-run",
            created_by=user,
            workspace=workspace,
            generation_job=run,
            tool_slugs=run.missing_tool_slugs,  # type: ignore[attr-defined]
        )
        run_ids.append(str(analysis.pk))
        _save(post_run, analysis_run_ids=run_ids)
        try:
            runner.execute(AnalysisRun.objects.select_related("created_by", "workspace").get(pk=analysis.pk))
            analysis.refresh_from_db(fields=["status"])
            failed = analysis.status == AnalysisRun.Status.FAILED
        except Exception:
            logger.exception("Post-run analysis %s failed", analysis.pk)
            AnalysisRun.objects.filter(pk=analysis.pk).update(
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
        _save(post_run, progress=progress, analysis_run_ids=run_ids)
    return True


def _run_report(post_run: StudyPostRun, study: Study) -> None:
    from backend.reports.services.report_service import create_and_run

    report = create_and_run(
        report_type="experiment_report",
        config={"study_id": str(study.pk)},
        user=study.created_by,
        description=f"Generated by post-run #{post_run.pk} ({post_run.trigger})",
    )
    _save(post_run, report=report)
    if report.status != report.Status.COMPLETED:
        msg = report.error_message or "report generation failed"
        raise RuntimeError(msg)


def _run_patterns(post_run: StudyPostRun, study: Study, user: User | None) -> None:
    """Create (when the post-run has none yet) and run the pattern analysis (WP-13 ``patterns``)."""
    from backend.generation import patterns
    from backend.generation.models import PatternRun

    pattern_run = PatternRun.objects.filter(pk=post_run.pattern_run_id).first() if post_run.pattern_run_id else None
    if pattern_run is None:
        config = dict(post_run.config or {})
        pattern_run = patterns.create_pattern_run(
            study,
            detectors=config.get("detectors", "default"),
            cluster_params=config.get("cluster_params"),
            scope=config.get("scope"),
            user=user,
            queue=False,
        )
        _save(post_run, pattern_run=pattern_run)
    patterns.run(pattern_run)


def run_post_run(post_run: StudyPostRun) -> None:
    """Execute one leased post-run (analysis then report, or pattern analysis); ends it completed or failed."""
    study = Study.objects.select_related("created_by").get(pk=post_run.study_id)
    config = post_run.config if isinstance(post_run.config, dict) else {}
    try:
        if post_run.kind == PostRunKind.PATTERNS:
            _run_patterns(post_run, study, study.created_by)
        else:
            settings_ = {**_ANALYSIS_DEFAULTS, **config}
            if settings_.get("retest"):
                _run_retests(post_run, study)
            if not _run_analyses(post_run, study, settings_):
                return  # lease lost: the new owner finishes it
            if settings_.get("report"):
                _run_report(post_run, study)
    except NotImplementedError:
        raise
    except Exception as exc:
        logger.exception("Post-run %s failed", post_run.pk)
        _save(
            post_run,
            status=WorkStatus.FAILED,
            error=f"{type(exc).__name__}: {exc}"[:2000],
            completed_at=timezone.now(),
            lease_owner="",
            lease_expires_at=None,
        )
        return
    _save(post_run, status=WorkStatus.COMPLETED, completed_at=timezone.now(), lease_owner="", lease_expires_at=None)
