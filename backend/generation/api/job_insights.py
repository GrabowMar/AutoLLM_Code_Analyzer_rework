"""Generated-app insight endpoints (code profile, phase timeline, code-quality metrics).

All three are read-only views derived from what a finished (or running) run already stores; none of them
recomputes a fingerprint or rewrites a measured value. Access: the caller's own runs only.

- ``GET /runs/{run_id}/code-profile/``: :func:`backend.generation.services.code_profile.code_profile`.
- ``GET /runs/{run_id}/timeline/``: lifecycle phases with durations (queue, one generation segment per
  ``RunAttempt``, evaluation stages of the latest ``RunEvaluation``), the executor's execution record and
  the study cell the run fills.
- ``GET /runs/{run_id}/quality-metrics/``: the latest result per tool (``ToolResult.latest_ids_per_job``) of
  the metric-shaped analyzers (lizard, radon, jscpd, and any other performance-category tool that stored
  numeric metrics).
"""

from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from typing import Any

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from ninja import Router
from ninja.errors import HttpError

from backend.generation.models import GenerationRun
from backend.generation.models.choices import RunStatus

router = Router(tags=["generation"])

_MAX_TEXT = 300

QUALITY_TOOLS = ("lizard", "radon", "jscpd")
# Other tools are listed only from this catalog category (complexity / size metrics).
EXTRA_METRIC_CATEGORY = "performance"
_TOOL_KIND = {"lizard": "complexity", "radon": "complexity", "jscpd": "duplication"}
# Evaluation stages in pipeline order (``contracts.evaluation.EvalStages``).
EVAL_STAGES = ("lock", "build", "start", "tester", "adherence", "probes")
# ``RunAttempt.time_split`` parts (seconds) and their labels.
TIME_PARTS = (("llm_s", "LLM calls"), ("tool_s", "Tools"), ("install_s", "Installs"), ("idle_s", "Idle"))


def _run_or_404(request, run_id: str, *related: str) -> GenerationRun:
    try:
        pk = uuid.UUID(run_id)
    except ValueError as exc:
        raise HttpError(404, "Not Found") from exc
    qs = GenerationRun.objects.select_related(*related) if related else GenerationRun.objects.all()
    return get_object_or_404(qs, id=pk, created_by=request.auth)


@router.get("/runs/{run_id}/code-profile/", response=dict)
def run_code_profile(request, run_id: str):
    """Source LOC by layer and language, file origins, structure heuristics and dependency ownership of one app."""
    # Imported per request: the profile reads reports/analysis helpers, which the URLconf must not depend on.
    from backend.generation.services.code_profile import code_profile

    return code_profile(_run_or_404(request, run_id, "starter"))


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
    """Seconds from *start* to *end*; a span that runs backwards (clock skew between hosts) is unknown."""
    if start is None or end is None:
        return None
    seconds = round((end - start).total_seconds(), 2)
    return seconds if seconds >= 0 else None


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


def _part(key: str, label: str, seconds: float | None, source: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "seconds": round(seconds, 2) if seconds is not None else None,
        "source": source,
    }


def _attempt_segments(run: GenerationRun, now: datetime) -> list[dict[str, Any]]:
    """One generation segment per attempt (``RunAttempt.started_at`` → ``ended_at``) with its time split."""
    segments = []
    for attempt in run.attempts.order_by("attempt"):
        live = attempt.ended_at is None and run.status == RunStatus.RUNNING
        split = attempt.time_split if isinstance(attempt.time_split, dict) else {}
        parts = [
            _part(key, label, value, f"attempt.time_split.{key}")
            for key, label in TIME_PARTS
            if (value := _num(split.get(key))) is not None
        ]
        outcome = attempt.outcome or attempt.termination or ("running" if live else None)
        label = f"Attempt {attempt.attempt}" + (f" ({outcome})" if outcome else "")
        segment = _segment(
            f"attempt_{attempt.attempt}",
            label,
            _span(attempt.started_at, attempt.ended_at or (now if live else None)),
            start=attempt.started_at,
            end=attempt.ended_at,
            source="attempt.ended_at - attempt.started_at" if attempt.ended_at else "now - attempt.started_at",
            approximate=live,
            note=_short(attempt.infra_error) or None,
            parts=parts,
        )
        segment["termination"] = attempt.termination or None
        segment["budget_exhausted"] = attempt.budget_exhausted or None
        segment["oom_killed"] = attempt.oom_killed
        segments.append(segment)
    return segments


def _evaluation_segment(run: GenerationRun) -> dict[str, Any] | None:
    """The latest evaluation's stages (retests excluded); ``None`` when the run was never evaluated."""
    evaluation = run.evaluations.filter(retest_of__isnull=True).select_related("eval_suite").order_by("-created_at")
    latest = evaluation.first()
    if latest is None:
        return None
    stages = latest.stages if isinstance(latest.stages, dict) else {}
    parts = []
    for key in EVAL_STAGES:
        record = stages.get(key)
        if not isinstance(record, dict):
            continue  # not planned at this evaluation level
        part = _part(key, key.capitalize(), _num(record.get("seconds")), f"evaluation.stages.{key}.seconds")
        part["status"] = record.get("status")
        parts.append(part)
    timed = [p["seconds"] for p in parts if p["seconds"] is not None]
    segment = _segment(
        "evaluation",
        f"Evaluation ({latest.eval_level})",
        sum(timed) if timed else None,
        source="sum of evaluation.stages[].seconds",
        approximate=len(timed) < len(parts),
        note=None if latest.reused_from_id is None else "Reused an earlier evaluation of the same tree.",
        parts=parts,
    )
    segment["evaluation_id"] = latest.pk
    segment["status"] = latest.status
    return segment


def _execution_record(run: GenerationRun) -> dict[str, Any]:
    ex = run.execution if isinstance(run.execution, dict) else {}
    infra_errors = [e for e in ex.get("infra_errors") or [] if isinstance(e, dict)]
    now = timezone.now()
    waiting = None
    if run.status == RunStatus.PENDING and run.run_after and run.run_after > now:
        waiting = "retry_backoff"
    return {
        "lane": run.lane,
        "endpoint_slug": run.endpoint.slug if run.endpoint_id and run.endpoint else None,
        "priority": run.priority,
        "sequence": run.sequence,
        "attempt": run.attempt,
        "policy": ex.get("policy"),
        "executor": ex.get("executor"),
        "started_seq": ex.get("started_seq"),
        "queue_seconds": _num(ex.get("queue_seconds")),
        "llm_queue_wait_s": _num(ex.get("llm_queue_wait_s")),
        "run_after": _iso(run.run_after),
        "requeued_at": ex.get("requeued_at"),
        "waiting_reason": waiting,
        "infra_errors": [{k: _short(v) for k, v in e.items()} for e in infra_errors[-5:]],
        "infra_error_count": len(infra_errors),
        "executor_lost_count": ex.get("executor_lost_count"),
        "relay_interruptions": ex.get("relay_interruptions"),
        "recorded": bool(ex),
    }


def _study_cell(run: GenerationRun) -> dict[str, Any] | None:
    if not run.study_id:
        return None
    study = run.study
    condition = run.condition
    cell = run.cell
    return {
        "study_id": str(run.study_id),
        "study_name": study.name if study else None,
        "study_status": study.status if study else None,
        "condition_id": run.condition_id,
        "condition_label": condition.label if condition else None,
        "cell_id": run.cell_id,
        "unit_key": cell.unit.unit_key if cell is not None else None,
        "stage": run.stage or None,
        "repeat_index": run.repeat_index,
        "repeat_number": run.repeat_index + 1 if run.repeat_index is not None else None,
        "planned_repeats": study.repeats if study else None,
        "cell_attempt": run.cell_attempt,
        "beyond_plan": run.beyond_plan,
        "cell_runs": cell.runs.count() if cell is not None else None,
        "is_current_in_cell": cell.current_run_id == run.id if cell is not None else None,
    }


def build_timeline(run: GenerationRun) -> dict[str, Any]:
    """Lifecycle phases of *run* with start/end/duration, its execution record and its study cell."""
    ex = run.execution if isinstance(run.execution, dict) else {}
    notes: list[str] = []
    now = timezone.now()
    live = run.status in (RunStatus.PENDING, RunStatus.RUNNING)
    created, queued, started, completed = run.created_at, run.queued_at, run.started_at, run.completed_at

    segments: list[dict[str, Any]] = []
    # Queue: from creation until the run started (until now while pending; until the run ended when it
    # never started, e.g. cancelled in the queue).
    terminal_unstarted = started is None and completed is not None and not live
    queue_end = completed if terminal_unstarted else started or (now if run.status == RunStatus.PENDING else None)
    if queue_end is not None:
        if terminal_unstarted:
            label = "Queued until cancelled" if run.status == RunStatus.CANCELLED else "Queued, never started"
        else:
            label = "Queued" if started else "Queued (waiting)"
        queue_parts = []
        if (queue_seconds := _num(ex.get("queue_seconds"))) is not None:
            queue_parts.append(_part("scheduler_queue", "Scheduler queue", queue_seconds, "execution.queue_seconds"))
        segments.append(
            _segment(
                "queue",
                label,
                _span(created, queue_end),
                start=created,
                end=started or (completed if terminal_unstarted else None),
                source="completed_at - created_at" if terminal_unstarted else "started_at - created_at",
                kind="wait",
                approximate=queue_end is now,
                parts=queue_parts,
            ),
        )

    attempts = _attempt_segments(run, now)
    segments.extend(attempts)
    if not attempts and started is not None and (completed is not None or live):
        segments.append(
            _segment(
                "run",
                "Running (in progress)" if completed is None else "Run (no attempt recorded)",
                _span(started, completed or now),
                start=started,
                end=completed,
                source="completed_at - started_at" if completed else "now - started_at",
                approximate=completed is None,
            ),
        )
    evaluation = _evaluation_segment(run)
    if evaluation is not None:
        segments.append(evaluation)
    if len(attempts) > 1:
        notes.append("The run was retried: each attempt is shown; only the last attempt's sample is kept.")

    milestones = [
        {"key": "created", "label": "Created", "at": _iso(created)},
        {"key": "queued", "label": "Queued", "at": _iso(queued)},
        {"key": "requeued", "label": "Requeued (retry)", "at": _iso(_dt(ex.get("requeued_at")))},
        {"key": "claimed", "label": "Claimed by executor", "at": _iso(_dt(ex.get("claimed_at")))},
        {"key": "started", "label": "Started", "at": _iso(started)},
        {"key": "finished", "label": run.status.capitalize() if not live else "Finished", "at": _iso(completed)},
    ]
    return {
        "run_id": str(run.id),
        "kind": run.kind,
        "status": run.status,
        "phase": run.phase,
        "outcome": run.outcome or None,
        "live": live,
        "milestones": [m for m in milestones if m["at"]],
        "segments": segments,
        "total_seconds": _span(created, completed),
        "run_seconds": _span(started, completed),
        "duration_seconds": _num(run.duration_seconds),
        "execution": _execution_record(run),
        "study": _study_cell(run),
        "notes": notes,
    }


@router.get("/runs/{run_id}/timeline/", response=dict)
def run_timeline(request, run_id: str):
    """Phases (queue → attempts → evaluation) with durations, the execution record and study cell."""
    run = _run_or_404(request, run_id, "study", "condition", "cell__unit", "endpoint")
    return build_timeline(run)


# ── quality metrics ──────────────────────────────────────────────────────


def _numeric_metrics(metrics: Any) -> bool:
    return isinstance(metrics, dict) and any(_num(v) is not None for v in metrics.values())


def _derived(slug: str, metrics: dict[str, Any]) -> dict[str, Any]:
    if slug == "lizard":
        functions = _num(metrics.get("functions"))
        nloc = _num(metrics.get("total_nloc"))
        return {"avg_function_nloc": round(nloc / functions, 2) if functions and nloc is not None else None}
    return {}


def quality_metrics(run: GenerationRun, user) -> dict[str, Any]:
    """The latest result per metric tool for *run*: metrics, tool version, parser/command hashes and source run."""
    from backend.analysis.models import AnalyzerTool
    from backend.analysis.models import ToolResult
    from backend.statistics.services.study import tool_measured

    latest = (
        ToolResult.objects.filter(id__in=ToolResult.latest_ids_per_job([run.id]))
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
        # A not-applicable skip measured nothing (spec section 8.8): not_measured, never a zero.
        measured = tool_measured(tr.status, summary)
        metrics = tr.metrics if isinstance(tr.metrics, dict) and tr.status == ToolResult.Status.COMPLETED else {}
        analysis = tr.run
        accessible = analysis.created_by_id == getattr(user, "id", None)
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
                    "id": str(analysis.id),
                    # Another user's analysis of this run: its id only, not its name.
                    "name": analysis.name if accessible else None,
                    "status": analysis.status,
                    "created_at": _iso(analysis.created_at),
                    "accessible": accessible,
                },
            },
        )
    return {
        "run_id": str(run.id),
        "selection": "latest result per tool for this run (ToolResult.latest_ids_per_job)",
        "measured": sum(1 for t in tools if t["measured"]),
        "tools": tools,
        "notes": [
            "Lizard counts every function it parses, anonymous JavaScript callbacks included, so its averages mix "
            "backend and frontend code.",
            "Metrics from different tool versions are not directly comparable.",
        ],
    }


@router.get("/runs/{run_id}/quality-metrics/", response=dict)
def run_quality_metrics(request, run_id: str):
    """Complexity and duplication metrics (latest result per tool) for one app, with tool provenance."""
    return quality_metrics(_run_or_404(request, run_id), request.auth)
