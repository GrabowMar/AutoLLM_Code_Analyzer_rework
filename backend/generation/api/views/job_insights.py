"""Generated-app insight endpoints (code profile, phase timeline, code-quality metrics).

All three are read-only views derived from what a finished (or running) job
already stores; none of them recomputes a fingerprint or rewrites a measured
value. Access follows the job detail endpoint: the caller's own jobs only.

- ``GET /jobs/{job_id}/code-profile/``: :func:`backend.generation.services.code_profile.code_profile`.
- ``GET /jobs/{job_id}/timeline/``: lifecycle phases with durations, the
  executor's execution record and the experiment cell the job fills.
- ``GET /jobs/{job_id}/quality-metrics/``: the latest result per tool
  (:meth:`ToolResult.latest_ids_per_job`) of the metric-shaped analyzers
  (lizard, radon, jscpd, and any other performance-category tool that stored
  numeric metrics).
"""

from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from ninja.errors import HttpError

from backend.generation.api.views._router import router
from backend.generation.models import GenerationJob
from backend.generation.services.code_profile import code_profile

# Durations that disagree by less than this are the same span (clocks of the
# executor and the sandbox differ slightly; results are saved after the clock stops).
_TOLERANCE_S = 2.0
_MAX_TEXT = 300

QUALITY_TOOLS = ("lizard", "radon", "jscpd")
# Other tools are listed only from this catalog category (complexity / size metrics).
EXTRA_METRIC_CATEGORY = "performance"
_TOOL_KIND = {"lizard": "complexity", "radon": "complexity", "jscpd": "duplication"}


def _job_or_404(request, job_id: str, *related: str) -> GenerationJob:
    try:
        pk = uuid.UUID(job_id)
    except ValueError as exc:
        raise HttpError(404, "Not Found") from exc
    qs = GenerationJob.objects.select_related(*related) if related else GenerationJob.objects.all()
    return get_object_or_404(qs, id=pk, created_by=request.auth)


@router.get("/jobs/{job_id}/code-profile/", response=dict)
def job_code_profile(request, job_id: str):
    """Source LOC by layer and language, file origins, structure heuristics and dependency ownership of one app."""
    return code_profile(_job_or_404(request, job_id))


# ── timeline ─────────────────────────────────────────────────────────────


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _dt(value: Any) -> datetime | None:
    """A datetime from a model value, an ISO string or epoch seconds (None when unreadable)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        parsed = parse_datetime(value)
        if parsed is not None and timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, UTC)
        return parsed
    if _num(value) is not None and value > 0:
        return datetime.fromtimestamp(float(value), tz=UTC)
    return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _span(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 2)


def _nonnegative(seconds: float | None) -> float | None:
    """A span that runs backwards (clock skew between hosts) is unknown, not negative."""
    return seconds if seconds is None or seconds >= 0 else None


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_TEXT:
        return value[:_MAX_TEXT] + "…"
    return value


def _segment(
    key: str,
    label: str,
    seconds: float | None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    source: str,
    kind: str = "work",
    approximate: bool = False,
    note: str | None = None,
    parts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "kind": kind,
        "start": _iso(start),
        "end": _iso(end),
        "seconds": round(seconds, 2) if seconds is not None else None,
        "source": source,
        "approximate": approximate,
        "note": note,
        "parts": parts or [],
    }


def _part(key: str, label: str, seconds: float | None, source: str, *, approximate: bool = False) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "seconds": round(seconds, 2) if seconds is not None else None,
        "source": source,
        "approximate": approximate,
    }


def _agent_start(job: GenerationJob) -> datetime | None:
    """When the agent itself started (``result_data.agent.schedule.started_at``), None when not recorded."""
    data = job.result_data if isinstance(job.result_data, dict) else {}
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    schedule = agent.get("schedule") if isinstance(agent.get("schedule"), dict) else {}
    return _dt(schedule.get("started_at"))


def _generation_segment(job: GenerationJob, started: datetime | None) -> tuple[dict[str, Any] | None, float | None]:
    """The generation phase: the agent's sandbox wall clock, or the template stages' LLM time.

    *started* is when the phase began: the job's ``started_at``, or the agent's own
    start when the job waited for a sandbox after it was started (pre-executor runs).
    """
    data = job.result_data if isinstance(job.result_data, dict) else {}
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else None
    if agent:
        wall = _num(agent.get("wall_seconds"))
        llm = _num(agent.get("llm_seconds"))
        execution = job.execution if isinstance(job.execution, dict) else {}
        # Accumulated by the chat client while the run went on: inside the calls' time, not a phase of its own.
        slot_wait = _num(execution.get("llm_queue_wait_s"))
        parts = []
        if llm is not None:
            parts.append(_part("llm", "LLM calls", llm, "result_data.agent.llm_seconds"))
        if slot_wait:
            label = "of which waiting for an endpoint slot" if llm is not None else "Waiting for an endpoint slot"
            parts.append(_part("llm_slot_wait", label, slot_wait, "execution.llm_queue_wait_s"))
        if llm is not None and wall is not None and wall >= llm:
            parts.append(
                _part(
                    "tools_other",
                    "Tools & harness (wall - LLM)",
                    wall - llm,
                    "agent.wall_seconds - agent.llm_seconds",
                    approximate=True,
                ),
            )
        trajectory = agent.get("trajectory") if isinstance(agent.get("trajectory"), dict) else {}
        traj_time = trajectory.get("time") if isinstance(trajectory.get("time"), dict) else {}
        split = {k: round(v, 2) for k, v in traj_time.items() if _num(v) is not None}
        segment = _segment(
            "generation",
            f"Agent run ({agent.get('harness') or 'harness'})",
            wall,
            start=started,
            end=started + timedelta(seconds=wall) if started and wall is not None else None,
            source="result_data.agent.wall_seconds",
            parts=parts,
            note=None if llm is not None else "LLM time was not recorded for this run.",
        )
        segment["trajectory_time"] = split or None
        return segment, wall

    total = _num(metrics.get("total_duration"))
    backend = _num(metrics.get("backend_duration"))
    frontend = _num(metrics.get("frontend_duration"))
    parts = []
    if backend is not None:
        parts.append(_part("backend_stage", "Backend stage", backend, "metrics.backend_duration"))
    if frontend is not None:
        parts.append(_part("frontend_stage", "Frontend stage", frontend, "metrics.frontend_duration"))
    source = "metrics.total_duration"
    if total is None and (backend is not None or frontend is not None):
        total, source = (backend or 0.0) + (frontend or 0.0), "metrics.backend_duration + frontend_duration"
    if total is None:
        total, source = _num(metrics.get("duration_seconds")), "metrics.duration_seconds"
    if total is None:
        return None, None
    segment = _segment(
        "generation",
        "Generation (LLM stages)",
        total,
        start=started,
        end=started + timedelta(seconds=total) if started else None,
        source=source,
        parts=parts,
    )
    segment["trajectory_time"] = None
    return segment, total


def _evaluation_segment(job: GenerationJob) -> tuple[dict[str, Any] | None, float | None]:
    data = job.result_data if isinstance(job.result_data, dict) else {}
    ev = data.get("evaluation") if isinstance(data.get("evaluation"), dict) else None
    if not ev:
        return None, None
    seconds = _num(ev.get("seconds"))
    build = ev.get("build") if isinstance(ev.get("build"), dict) else {}
    attempts = [a for a in build.get("attempts") or [] if isinstance(a, dict)]
    build_s = sum(_num(a.get("seconds")) or 0.0 for a in attempts) if attempts else None
    parts = []
    if build_s is not None:
        label = "Image build" + (f" ({len(attempts)} attempts)" if len(attempts) > 1 else "")
        parts.append(_part("build", label, build_s, "evaluation.build.attempts[].seconds"))
        if seconds is not None and seconds >= build_s:
            parts.append(
                _part(
                    "tests",
                    "App start & conformance tests",
                    seconds - build_s,
                    "evaluation.seconds - build",
                    approximate=True,
                ),
            )
    segment = _segment("evaluation", "Evaluation", seconds, source="result_data.evaluation.seconds", parts=parts)
    return segment, seconds


def _execution_record(job: GenerationJob) -> dict[str, Any]:
    ex = job.execution if isinstance(job.execution, dict) else {}
    data = job.result_data if isinstance(job.result_data, dict) else {}
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    schedule = agent.get("schedule") if isinstance(agent.get("schedule"), dict) else {}
    attempts = []
    for record in ex.get("attempts") or []:
        if not isinstance(record, dict):
            continue
        infra = record.get("infra_errors") or []
        attempts.append(
            {
                "attempt": record.get("attempt"),
                "reason": record.get("reason"),
                "error": _short(record.get("error")),
                "at": record.get("at"),
                "cost": _num(record.get("cost")),
                "total_tokens": _num(record.get("total_tokens")),
                "infra_errors": len(infra) if isinstance(infra, list) else None,
            },
        )
    infra_errors = [e for e in ex.get("infra_errors") or [] if isinstance(e, dict)]
    prior = {
        key: _num(ex.get(f"prior_attempts_{key}"))
        for key in ("cost", "prompt_tokens", "completion_tokens", "total_tokens")
        if ex.get(f"prior_attempts_{key}") is not None
    }
    now = timezone.now()
    waiting = None
    if job.status == GenerationJob.Status.PENDING and job.run_after and job.run_after > now:
        waiting = "retry_backoff"
    return {
        "lane": job.lane,
        "endpoint_slug": job.endpoint.slug if job.endpoint_id and job.endpoint else None,
        "priority": job.priority,
        "sequence": job.sequence,
        "attempt": job.attempt,
        "policy": ex.get("policy"),
        "executor": ex.get("executor"),
        "started_seq": ex.get("started_seq"),
        "run_order": schedule.get("run_order"),
        "queue_seconds": _num(ex.get("queue_seconds") if "queue_seconds" in ex else schedule.get("queue_seconds")),
        "llm_queue_wait_s": _num(ex.get("llm_queue_wait_s")),
        "prefetch_load_s": _num(schedule.get("prefetch_load_s")),
        "run_after": _iso(job.run_after),
        "requeued_at": ex.get("requeued_at"),
        "waiting_reason": waiting,
        "attempts": attempts,
        "infra_errors": [{k: _short(v) for k, v in e.items()} for e in infra_errors[-5:]],
        "infra_error_count": len(infra_errors),
        "executor_lost_count": ex.get("executor_lost_count"),
        "relay_interruptions": ex.get("relay_interruptions"),
        # Set only while a requested re-evaluation is queued or running (the finish transition drops it).
        "reevaluated": bool(ex.get("reeval") or ex.get("reeval_prev_status")),
        "prior_attempts": prior or None,
        "recorded": bool(ex),
    }


def _experiment_cell(job: GenerationJob) -> dict[str, Any] | None:
    if not job.experiment_id:
        return None
    experiment = job.experiment
    condition = job.condition
    label = None
    method = None
    model_name = None
    if condition is not None:
        model_name = condition.model.model_name if condition.model_id else None
        method = condition.method_label()
        label = condition.label or " · ".join(x for x in (model_name, method) if x)
    cell = GenerationJob.objects.filter(
        experiment_id=job.experiment_id,
        condition_id=job.condition_id,
        app_requirement_id=job.app_requirement_id,
        repeat_index=job.repeat_index,
    )
    latest = cell.order_by("-created_at").values_list("id", flat=True).first()
    repeat = job.repeat_index
    return {
        "experiment_id": str(job.experiment_id),
        "experiment_name": experiment.name if experiment else None,
        "experiment_status": experiment.status if experiment else None,
        "condition_id": job.condition_id,
        "condition_label": label,
        "condition_method": method,
        "condition_model": model_name,
        "app_slug": job.app_requirement.slug if job.app_requirement_id and job.app_requirement else None,
        "repeat_index": repeat,
        "repeat_number": repeat + 1 if repeat is not None else None,
        "planned_repeats": experiment.repeats if experiment else None,
        "cell_jobs": cell.count(),
        "is_current_in_cell": latest == job.id,
    }


def build_timeline(job: GenerationJob) -> dict[str, Any]:
    """Lifecycle phases of *job* with start/end/duration, its execution record and its experiment cell."""
    ex = job.execution if isinstance(job.execution, dict) else {}
    data = job.result_data if isinstance(job.result_data, dict) else {}
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    schedule = agent.get("schedule") if isinstance(agent.get("schedule"), dict) else {}
    notes: list[str] = []

    created = job.created_at
    queued = job.queued_at or _dt(ex.get("queued_at")) or _dt(schedule.get("queued_at"))
    requeued = _dt(ex.get("requeued_at"))
    claimed = _dt(ex.get("claimed_at")) or _dt(schedule.get("claimed_at"))
    started = job.started_at
    completed = job.completed_at
    now = timezone.now()
    live = job.status in (GenerationJob.Status.PENDING, GenerationJob.Status.RUNNING)

    segments: list[dict[str, Any]] = []
    # Queue: from creation until this attempt started (until now while pending; until the
    # job ended when it never started, e.g. cancelled in the queue).
    terminal_unstarted = started is None and completed is not None and not live
    queue_end = started or (now if job.status == GenerationJob.Status.PENDING else None)
    if terminal_unstarted:
        queue_end = completed
    queue_span = _nonnegative(_span(created, queue_end))
    if _num(ex.get("queue_seconds")) is not None:
        queue_seconds, queue_source = _num(ex.get("queue_seconds")), "execution.queue_seconds"
    else:
        queue_seconds, queue_source = _num(schedule.get("queue_seconds")), "result_data.agent.schedule.queue_seconds"
    # Pre-executor agent runs were started on dispatch and then waited for a sandbox:
    # that wait lies inside started→completed, before the agent's own start.
    agent_start = _agent_start(job) if agent else None
    sandbox_wait = _span(started, agent_start)
    sandbox_wait = sandbox_wait if sandbox_wait is not None and sandbox_wait > _TOLERANCE_S else None
    queue_parts = []
    if queue_seconds is not None and (queue_span is None or queue_seconds <= queue_span + _TOLERANCE_S):
        queue_parts.append(_part("scheduler_queue", "Scheduler queue (this attempt)", queue_seconds, queue_source))
    if queue_end is not None:
        if terminal_unstarted:
            cancelled = job.status == GenerationJob.Status.CANCELLED
            label = "Queued until cancelled" if cancelled else "Queued, never started"
        else:
            label = "Queued" if started else "Queued (waiting)"
        segments.append(
            _segment(
                "queue",
                label,
                queue_span,
                start=created,
                end=started or (completed if terminal_unstarted else None),
                source="completed_at - created_at" if terminal_unstarted else "started_at - created_at",
                kind="wait",
                note="Includes earlier attempts." if job.attempt else None,
                parts=queue_parts,
            ),
        )
    if sandbox_wait is not None:
        wait_parts = []
        if queue_seconds is not None and not queue_parts and queue_seconds <= sandbox_wait + _TOLERANCE_S:
            wait_parts.append(_part("scheduler_queue", "Recorded queue wait", queue_seconds, queue_source))
        segments.append(
            _segment(
                "sandbox_wait",
                "Waiting for a sandbox",
                sandbox_wait,
                start=started,
                end=agent_start,
                source="agent.schedule.started_at - started_at",
                kind="wait",
                note="Started on dispatch, before the executor queue existed: the queue wait falls inside the run.",
                parts=wait_parts,
            ),
        )

    gen, gen_s = _generation_segment(job, agent_start if sandbox_wait is not None else started)
    ev, ev_s = _evaluation_segment(job)
    span = _span(started, completed)
    eval_in_run = None
    residual = None
    if span is not None and gen_s is not None:
        rest = span - gen_s - (sandbox_wait or 0.0)
        if ev_s is not None:
            eval_in_run = rest - ev_s >= -_TOLERANCE_S
        if eval_in_run:
            residual = max(rest - ev_s, 0.0)
        elif rest >= -_TOLERANCE_S:
            residual = max(rest, 0.0)
        if rest < -_TOLERANCE_S:
            notes.append(
                "The recorded generation time is longer than started→completed (clocks of different hosts); "
                "the phases are shown without the residual.",
            )
    if gen is not None:
        segments.append(gen)
    elif started is not None and (completed is not None or live):
        # No phase detail stored (still running, or it failed before writing results).
        segments.append(
            _segment(
                "run",
                "Running (in progress)" if completed is None else "Run (no phase detail recorded)",
                _span(started, completed or now),
                start=started,
                end=completed,
                source="completed_at - started_at" if completed else "now - started_at",
                approximate=completed is None,
            ),
        )
    if residual is not None and residual > 0.05:
        gen_start = agent_start if sandbox_wait is not None else started
        gen_end = gen_start + timedelta(seconds=gen_s) if gen_start and gen_s is not None else None
        waited = " - sandbox wait" if sandbox_wait is not None else ""
        segments.append(
            _segment(
                "other",
                "Waiting / other",
                residual,
                start=gen_end,
                end=gen_end + timedelta(seconds=residual) if gen_end else None,
                source=(
                    f"(completed - started){waited} - generation - evaluation"
                    if eval_in_run
                    else f"(completed - started){waited} - generation"
                ),
                kind="wait",
                approximate=True,
                note=(
                    "Residual: evaluation queue, collecting and saving results."
                    if eval_in_run
                    else "Residual: collecting and saving results."
                ),
            ),
        )
    if ev is not None:
        if eval_in_run:
            ev["end"] = _iso(completed)
            ev["start"] = _iso(completed - timedelta(seconds=ev_s)) if completed and ev_s is not None else None
            ev["approximate"] = True  # start = completed - evaluation.seconds
        elif eval_in_run is False:
            ev["note"] = "Evaluated outside this run's span (a later re-evaluation); not part of its duration."
            ev["separate"] = True
        segments.append(ev)

    milestones = [
        {"key": "created", "label": "Created", "at": _iso(created)},
        {"key": "queued", "label": "Queued", "at": _iso(queued)},
        {"key": "requeued", "label": "Requeued (retry)", "at": _iso(requeued)},
        {"key": "claimed", "label": "Claimed by executor", "at": _iso(claimed)},
        {"key": "started", "label": "Started", "at": _iso(started)},
        {"key": "finished", "label": job.status.capitalize() if not live else "Finished", "at": _iso(completed)},
    ]
    if not ex and queued is None:
        notes.append("Dispatched before the executor queue existed: no queue timestamps were recorded.")

    return {
        "job_id": str(job.id),
        "mode": job.mode,
        "status": job.status,
        "phase": job.phase,
        "live": live,
        "milestones": [m for m in milestones if m["at"]],
        "segments": segments,
        "total_seconds": _span(created, completed),
        "run_seconds": span,
        "duration_seconds": _num(job.duration_seconds),
        "evaluation_in_run": eval_in_run,
        "execution": _execution_record(job),
        "experiment": _experiment_cell(job),
        "notes": notes,
    }


@router.get("/jobs/{job_id}/timeline/", response=dict)
def job_timeline(request, job_id: str):
    """Phases (queue → generation → waiting → evaluation) with durations, the execution record and experiment cell."""
    job = _job_or_404(
        request,
        job_id,
        "experiment",
        "condition__model",
        "condition__profile",
        "condition__harness_preset",
        "app_requirement",
        "endpoint",
    )
    return build_timeline(job)


# ── quality metrics ──────────────────────────────────────────────────────


def _numeric_metrics(metrics: Any) -> bool:
    return isinstance(metrics, dict) and any(_num(v) is not None for v in metrics.values())


def _derived(slug: str, metrics: dict[str, Any]) -> dict[str, Any]:
    if slug == "lizard":
        functions = _num(metrics.get("functions"))
        nloc = _num(metrics.get("total_nloc"))
        return {"avg_function_nloc": round(nloc / functions, 2) if functions and nloc is not None else None}
    return {}


def quality_metrics(job: GenerationJob, user) -> dict[str, Any]:
    """The latest result per metric tool for *job*: metrics, tool version, parser/command hashes and source run."""
    from backend.analysis.models import AnalyzerTool
    from backend.analysis.models import ToolResult

    latest = (
        ToolResult.objects.filter(id__in=ToolResult.latest_ids_per_job([job.id]))
        .select_related("run")
        .order_by("tool_slug")
    )
    by_slug = {tr.tool_slug: tr for tr in latest}
    candidates = [s for s, tr in by_slug.items() if s not in QUALITY_TOOLS and _numeric_metrics(tr.metrics)]
    # Only code-metric tools: a dependency or security scanner's bookkeeping counters
    # (osv-scanner's dev_only_advisories_skipped) are not complexity or duplication.
    metric_tools = AnalyzerTool.objects.filter(slug__in=candidates, category=EXTRA_METRIC_CATEGORY)
    extra = sorted(metric_tools.values_list("slug", flat=True))
    slugs = [*QUALITY_TOOLS, *extra]
    names = dict(AnalyzerTool.objects.filter(slug__in=slugs).values_list("slug", "name"))

    tools = []
    for slug in slugs:
        tr = by_slug.get(slug)
        row: dict[str, Any] = {
            "tool_slug": slug,
            "tool_name": names.get(slug) or slug,
            "kind": _TOOL_KIND.get(slug, "other"),
        }
        if tr is None:
            tools.append({**row, "status": "not_run", "measured": False, "metrics": {}, "derived": {}, "run": None})
            continue
        summary = tr.summary if isinstance(tr.summary, dict) else {}
        skip_reason = summary.get("skip_reason")
        measured = tr.status == ToolResult.Status.COMPLETED or (
            tr.status == ToolResult.Status.SKIPPED and skip_reason == "not_applicable"
        )
        metrics = tr.metrics if isinstance(tr.metrics, dict) and tr.status == ToolResult.Status.COMPLETED else {}
        run = tr.run
        accessible = run.created_by_id == getattr(user, "id", None)
        tools.append(
            {
                **row,
                "status": tr.status,
                "measured": measured,
                "skip_reason": skip_reason,
                "error": _short(tr.error_message) or None,
                "metrics": metrics,
                "derived": _derived(slug, metrics),
                "tool_version": summary.get("tool_version"),
                "parser_sha": summary.get("parser_sha"),
                "command_sha": summary.get("command_sha"),
                "patterns_sha": summary.get("patterns_sha"),
                "result_id": str(tr.id),
                "result_created_at": _iso(tr.created_at),
                "duration_seconds": _num(tr.duration_seconds),
                "run": {
                    "id": str(run.id),
                    # Another user's run on this job: its id only, not its name.
                    "name": run.name if accessible else None,
                    "status": run.status,
                    "created_at": _iso(run.created_at),
                    "accessible": accessible,
                },
            },
        )
    return {
        "job_id": str(job.id),
        "selection": "latest result per tool for this job (ToolResult.latest_ids_per_job)",
        "measured": sum(1 for t in tools if t["measured"]),
        "tools": tools,
        "notes": [
            "Lizard counts every function it parses, anonymous JavaScript callbacks included, so its averages mix "
            "backend and frontend code.",
            "Metrics from different tool versions are not directly comparable.",
        ],
    }


@router.get("/jobs/{job_id}/quality-metrics/", response=dict)
def job_quality_metrics(request, job_id: str):
    """Complexity and duplication metrics (latest result per tool) for one app, with tool provenance."""
    return quality_metrics(_job_or_404(request, job_id), request.auth)
