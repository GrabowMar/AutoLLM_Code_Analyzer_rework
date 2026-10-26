"""Step dispatchers — one function per PipelineStep.Kind.

Each dispatcher receives:
  - step_run: PipelineStepRun instance (already saved, status=running)
  - params: resolved config dict for this step
  - run_params: top-level PipelineRun.params

Each dispatcher returns an output dict that is stored on PipelineStepRun.output.
Dispatchers BLOCK until the underlying job/task reaches a terminal state so
the engine can wait on them inside a thread without extra polling logic in
runner.py.

NOTE: dispatch_script intentionally does NOT execute arbitrary code. It only
supports a fixed set of safe operations (variable assignment, conditional
branching metadata). This is a security boundary.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError

from backend.realtime.events import publish

if TYPE_CHECKING:
    from backend.automation.models import PipelineStepRun

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2  # seconds between status polls
_SEVERITY_KEYS = ("critical", "high", "medium", "low", "info")
_REPORT_TIMEOUT_SECONDS = 600


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


def dispatch_generate(
    step_run: PipelineStepRun,
    params: dict[str, Any],
    run_params: dict[str, Any],
) -> dict[str, Any]:
    """Create and execute a GenerationJob, poll until terminal, return output."""
    from backend.generation.models import GenerationJob
    from backend.generation.services.generation_service import GenerationService

    model_id = params.get("model_id") or run_params.get("model_id")
    mode = params.get("mode", "custom")

    # The run's trigger, else the pipeline owner — never an arbitrary user.
    user = _analysis_user(step_run)
    if user is None:
        return {"status": "failed", "error": "Generate step has no user to run as"}

    job = GenerationJob.objects.create(
        mode=mode,
        created_by=user,
        model_id=model_id,
    )

    # Run inline (GenerationService.execute is synchronous)
    try:
        service = GenerationService()
        service.execute(
            GenerationJob.objects.select_related(
                "model",
                "app_requirement",
                "batch",
            ).get(id=job.id),
        )
    except Exception as exc:
        logger.exception("GenerationService failed for step_run %s", step_run.id)
        job.refresh_from_db()
        return {
            "generation_job_id": str(job.id),
            "status": "failed",
            "error": str(exc),
        }

    job.refresh_from_db()
    output: dict[str, Any] = {
        "generation_job_id": str(job.id),
        "status": job.status,
    }
    if job.status == "failed":
        output["error"] = job.error_message or "Generation failed"
    return output


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------

# A pipeline step submits an analysis run and *waits* for the executor to
# finish it (Celery task or executor thread); it never runs tools inline.
ANALYZE_POLL_SECONDS = 5.0
ANALYZE_DEFAULT_TIMEOUT_SECONDS = 4 * 3600
ANALYZE_MAX_TIMEOUT_SECONDS = 10 * 3600
THRESHOLD_KEYS = (*_SEVERITY_KEYS, "total")
# v1 tool names still found in saved pipelines.
_LEGACY_TOOL_ALIASES = {"llm_review": "ai_review", "detect_secrets": "detect-secrets"}


class AnalyzeConfigError(ValueError):
    """The analyze step's configuration cannot produce a valid analysis run."""


def threshold_limit(limit: Any) -> int | None:
    """A threshold limit as a non-negative int, or None when it is not one.

    Accepts ints and digit strings (a ``{{params.x}}`` value may resolve to
    "5"); an unresolved template, a negative number, a float or a bool is
    not a limit.
    """
    if isinstance(limit, bool):
        return None
    if isinstance(limit, int):
        return limit if limit >= 0 else None
    if isinstance(limit, str) and limit.strip().isdigit():
        return int(limit.strip())
    return None


def validated_thresholds(thresholds: Any) -> dict[str, int]:
    """The step's *resolved* thresholds as ``{key: limit}``; AnalyzeConfigError otherwise.

    Templated limits pass the save-time DSL validation and resolve at run
    time; one that did not resolve (a missing run param — every scheduled
    run triggers with no params) or resolved to a non-limit must fail the
    step, never be skipped: a gate that was not evaluated cannot pass.
    """
    if thresholds in (None, ""):
        return {}
    if not isinstance(thresholds, dict):
        msg = "'thresholds' must map a severity (or 'total') to a maximum count"
        raise AnalyzeConfigError(msg)
    out: dict[str, int] = {}
    for key, limit in thresholds.items():
        if key not in THRESHOLD_KEYS:
            msg = f"Unknown threshold '{key}' (use one of {list(THRESHOLD_KEYS)})"
            raise AnalyzeConfigError(msg)
        value = threshold_limit(limit)
        if value is None:
            unresolved = isinstance(limit, str) and "{{" in limit
            detail = f"unresolved template {limit}" if unresolved else f"{limit!r} is not a non-negative integer"
            msg = f"Threshold '{key}' has no usable limit: {detail}"
            raise AnalyzeConfigError(msg)
        out[key] = value
    return out


def static_counts(measurement: Any, summary: Any) -> dict[str, Any]:
    """Static *counted* findings as ``{severity: n, "total": n}`` (values may be None).

    From the run's measurement (``static.findings``, the stored record every
    consumer reads), else from its summary (schema v2 ``severity_counts`` /
    ``total_findings``, the same counted findings). A count neither records
    stays None: missing data, never 0.
    """
    m = measurement if isinstance(measurement, dict) else {}
    static = m.get("static") if isinstance(m.get("static"), dict) else {}
    findings = static.get("findings") if isinstance(static.get("findings"), dict) else {}
    by_severity = findings.get("by_severity")
    if isinstance(by_severity, dict):
        return {**{s: by_severity.get(s) for s in _SEVERITY_KEYS}, "total": findings.get("total")}
    summary = summary if isinstance(summary, dict) else {}
    counts = summary.get("severity_counts") if isinstance(summary.get("severity_counts"), dict) else {}
    return {**{s: counts.get(s) for s in _SEVERITY_KEYS}, "total": summary.get("total_findings")}


def _threshold_status(
    summary: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    available: bool = True,
    counts: dict[str, Any] | None = None,
) -> str:
    """Compare static *counted* finding counts against thresholds.

    *counts* (``static_counts``) defaults to the summary's (schema v2).
    ``not_evaluated`` when no thresholds are set, the static measurement is
    unavailable (a required static tool failed), any limit is unusable
    (unknown key, unresolved template, not a non-negative integer) or a
    limited count is not recorded: a gate that was not fully evaluated must
    never "pass".
    """
    if not thresholds or not available:
        return "not_evaluated"
    if counts is None:
        counts = static_counts(None, summary)
    limits: dict[str, int] = {}
    for key, limit in thresholds.items():
        value = threshold_limit(limit)
        if key not in THRESHOLD_KEYS or value is None:
            return "not_evaluated"
        limits[key] = value
    for key, limit in limits.items():
        raw = counts.get(key)
        # A missing count is unknown, not zero: it can never pass the gate.
        if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return "not_evaluated"
        if int(raw) > limit:
            return "failed"
    return "passed"


def _analysis_user(step_run: PipelineStepRun):
    """Who the analysis runs as: the run's trigger, else the pipeline owner — never an arbitrary user."""
    run = step_run.run
    if run.triggered_by_id:
        return run.triggered_by
    pipeline = getattr(run, "pipeline", None)
    return getattr(pipeline, "owner", None)


def requested_tools(params: dict[str, Any]) -> list[str] | None:
    """Tool slugs from ``tool_slugs`` (or legacy ``analyzers``); None when neither key is set.

    Raises AnalyzeConfigError for a malformed list. An explicitly empty list
    is returned as ``[]`` so the caller can reject it.
    """
    key = "tool_slugs" if "tool_slugs" in params else "analyzers" if "analyzers" in params else None
    if key is None:
        return None
    raw = params.get(key)
    if raw is None:
        return None
    if isinstance(raw, str):
        # A templated value ({{params.tools}}) may resolve to "a,b".
        raw = raw.split(",")
    if not isinstance(raw, (list, tuple)) or not all(isinstance(t, str) for t in raw):
        msg = f"'{key}' must be a list of tool slugs"
        raise AnalyzeConfigError(msg)
    slugs: list[str] = []
    for item in raw:
        slug = _LEGACY_TOOL_ALIASES.get(item.strip(), item.strip())
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def _profile_ref(params: dict[str, Any]) -> Any:
    for key in ("profile", "profile_id", "profile_slug"):
        if params.get(key) not in (None, ""):
            return params[key]
    return None


def _resolve_profile(ref: Any, user):
    """An analysis profile by id or slug (latest version) the user may use."""
    import uuid

    from django.db.models import Q

    from backend.analysis.models import AnalysisProfile

    qs = AnalysisProfile.objects.filter(is_archived=False)
    if not getattr(user, "is_staff", False):
        qs = qs.filter(Q(is_system=True) | Q(created_by=user))
    profile = None
    try:
        profile = qs.filter(id=uuid.UUID(str(ref))).first()
    except ValueError:
        profile = qs.filter(slug=str(ref)).order_by("-version").first()
    if profile is None:
        msg = f"Analysis profile not found or not usable: {ref}"
        raise AnalyzeConfigError(msg)
    return profile


def _restrict_config(config: dict[str, Any], tools: list[str]) -> dict[str, Any]:
    """*config* limited to *tools* (catalog tools missing from it are added with defaults)."""
    import copy

    from backend.analysis.engine import catalog

    specs = catalog.load_specs()
    cfg = copy.deepcopy(config)
    existing = {t.get("slug"): t for t in cfg.get("tools", []) if isinstance(t, dict)}
    wanted_ai = False
    selected: list[dict[str, Any]] = []
    unknown: list[str] = []
    for slug in tools:
        spec = specs.get(slug)
        if slug == catalog.AI_TOOL_SLUG or (spec is not None and spec.kind == "ai"):
            wanted_ai = True
        elif slug in existing:
            selected.append(existing[slug])
        elif spec is not None:
            selected.append({"slug": slug, "required": spec.default_required, "config": dict(spec.default_config)})
        else:
            unknown.append(slug)
    if unknown:
        msg = f"Unknown analysis tool(s): {', '.join(unknown)}"
        raise AnalyzeConfigError(msg)
    cfg["tools"] = selected
    reviewer = dict(cfg.get("reviewer") or {})
    reviewer["enabled"] = wanted_ai
    cfg["reviewer"] = reviewer
    return cfg


def _has_tools(frozen: dict[str, Any]) -> bool:
    return bool(frozen.get("tools")) or bool((frozen.get("reviewer") or {}).get("enabled"))


def build_analysis_plan(params: dict[str, Any], user) -> tuple[dict[str, Any], Any]:
    """Frozen plan (+ profile, for provenance) for an analyze step.

    ``profile`` (id or slug; legacy ``profile_id``) selects a profile, and a
    tool list (``tool_slugs`` / legacy ``analyzers``) restricts it; with no
    profile, the tool list restricts the default profile config. An empty
    tool list, unknown tools or a plan without any tool are rejected.
    """
    from backend.analysis.engine import plan as plan_engine

    tools = requested_tools(params)
    ref = _profile_ref(params)
    profile = _resolve_profile(ref, user) if ref is not None else None
    if tools is not None and not tools and profile is None:
        msg = "Analyze step has an empty tool list; select tools or a profile"
        raise AnalyzeConfigError(msg)
    if profile is None and tools is None:
        msg = "Analyze step needs a 'profile' or a non-empty 'tool_slugs' list"
        raise AnalyzeConfigError(msg)

    if tools:
        base = profile.config if profile is not None else plan_engine.default_profile_config()
        config = plan_engine.validate_profile_config(_restrict_config(base, tools))
        frozen = plan_engine.freeze_plan(config=config, user=user)
    else:
        frozen = plan_engine.freeze_plan(profile=profile, user=user)
    if not _has_tools(frozen):
        msg = "Analysis plan has no tools to run"
        raise AnalyzeConfigError(msg)
    return frozen, profile


def _timeout_seconds(params: dict[str, Any]) -> int:
    raw = params.get("timeout_seconds", ANALYZE_DEFAULT_TIMEOUT_SECONDS)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        msg = "'timeout_seconds' must be a whole number of seconds"
        raise AnalyzeConfigError(msg) from exc
    if value <= 0:
        msg = "'timeout_seconds' must be positive"
        raise AnalyzeConfigError(msg)
    return min(value, ANALYZE_MAX_TIMEOUT_SECONDS)


def _sleep(seconds: float) -> None:
    """Indirection so tests can drive the poll loop without sleeping."""
    time.sleep(seconds)


def _pipeline_cancelled(step_run: PipelineStepRun) -> bool:
    from backend.automation.models import PipelineRun

    return PipelineRun.objects.filter(id=step_run.run_id, status="cancelled").exists()


def wait_for_analysis(run_id, *, step_run: PipelineStepRun, timeout_s: float, poll_s: float = ANALYZE_POLL_SECONDS):
    """Poll the run until it is terminal; ``(run, error)``.

    Bounded by *timeout_s*: on timeout — or when the pipeline is cancelled —
    the analysis run is asked to cancel (the executor stops it cooperatively)
    and the step reports why instead of hanging the pipeline.
    """
    from backend.analysis.engine import executor
    from backend.analysis.models import AnalysisRun

    deadline = time.monotonic() + timeout_s
    while True:
        run = AnalysisRun.objects.filter(id=run_id).first()
        if run is None:
            return None, "Analysis run was deleted while the step was waiting"
        if run.status in AnalysisRun.TERMINAL_STATUSES:
            return run, None
        if _pipeline_cancelled(step_run):
            executor.request_cancel(run)
            return run, "Pipeline cancelled; analysis cancellation requested"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            executor.request_cancel(run)
            return run, f"Analysis did not finish within {int(timeout_s)}s; cancellation requested"
        _sleep(min(poll_s, remaining))


def _output_summary(summary: Any, *, static_available: bool) -> dict[str, Any]:
    """The run summary for the step output; static counts are None when they are missing data.

    The stored summary zero-fills ``severity_counts`` / ``total_findings``
    whatever the run status; when the static measurement is unavailable
    those zeros would let a downstream step read a scan that did not happen
    as a clean one.
    """
    out = dict(summary) if isinstance(summary, dict) else {}
    if out and not static_available:
        for key in ("severity_counts", "total_findings", "issues"):
            if key in out:
                out[key] = None
    return out


def _failed_output(error: str, *, run=None) -> dict[str, Any]:
    return {
        "analysis_run_id": str(run.id) if run is not None else None,
        "status": "failed",
        "summary": _output_summary(run.summary if run is not None else {}, static_available=False),
        "threshold_status": "not_evaluated",
        "error": error,
    }


def dispatch_analyze(
    step_run: PipelineStepRun,
    params: dict[str, Any],
    run_params: dict[str, Any],
) -> dict[str, Any]:
    """Submit an analysis run through the executor and wait for its outcome.

    Config: ``profile`` (id/slug) and/or ``tool_slugs`` (legacy ``analyzers``),
    ``generation_job_id`` (default: the pipeline param) or ``source_code``,
    ``thresholds`` ({severity|total: max static counted findings}),
    ``timeout_seconds``, ``fail_on_partial``, ``fail_on_threshold``.
    Output keys: analysis_run_id, status, summary, threshold_status, error?.
    """
    from backend.analysis.engine import executor
    from backend.analysis.models import AnalysisRun
    from backend.generation.models import GenerationJob

    user = _analysis_user(step_run)
    if user is None:
        return _failed_output("Analyze step has no user to run as (pipeline run has no trigger or owner)")

    job_id = params.get("generation_job_id") or params.get("generation_job") or run_params.get("generation_job_id")
    source_code = params.get("source_code")
    try:
        if not job_id and not source_code:
            msg = "Analyze step needs a generation_job_id or source_code"
            raise AnalyzeConfigError(msg)
        if source_code is not None and not (
            isinstance(source_code, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in source_code.items())
        ):
            msg = "'source_code' must map file paths to file contents"
            raise AnalyzeConfigError(msg)
        # Checked before anything is submitted: an unusable limit fails the
        # step instead of silently dropping out of the gate.
        thresholds = validated_thresholds(params.get("thresholds"))
        timeout_s = _timeout_seconds(params)
        job = None
        if job_id:
            try:
                job = GenerationJob.objects.filter(id=job_id).first()
            except (ValueError, TypeError, DjangoValidationError):
                job = None
            if job is None:
                msg = f"Generation job not found: {job_id}"
                raise AnalyzeConfigError(msg)
            if job.created_by_id != user.id and not getattr(user, "is_staff", False):
                msg = "You can only analyse your own generation jobs"
                raise AnalyzeConfigError(msg)
        frozen, profile = build_analysis_plan(params, user)
    except Exception as exc:  # noqa: BLE001 - config errors of any kind become the step's error
        logger.warning("Analyze step %s rejected: %s", step_run.id, exc)
        return _failed_output(str(exc))

    try:
        run = executor.submit_run(
            user=user,
            plan=frozen,
            profile=profile,
            generation_job=job,
            source_code=None if job is not None else source_code,
            purpose=AnalysisRun.Purpose.ADHOC,
            name=f"Automation step {step_run.id}",
        )
    except Exception as exc:  # noqa: BLE001 - ownership / unresolvable sample / infra
        logger.warning("Analyze step %s could not submit: %s", step_run.id, exc)
        return _failed_output(f"Could not submit analysis: {exc}")

    run, error = wait_for_analysis(run.id, step_run=step_run, timeout_s=timeout_s)
    if run is None:
        return _failed_output(error or "Analysis run disappeared")

    measurement = run.measurement if isinstance(run.measurement, dict) else {}
    static_available = run.status in AnalysisRun.FINISHED_STATUSES and bool(
        (measurement.get("static") or {}).get("available"),
    )
    threshold_status = _threshold_status(
        run.summary,
        thresholds,
        available=static_available,
        counts=static_counts(measurement, run.summary),
    )
    output: dict[str, Any] = {
        "analysis_run_id": str(run.id),
        "status": run.status,
        "summary": _output_summary(run.summary, static_available=static_available),
        "threshold_status": threshold_status,
        "is_complete": run.is_complete,
        "static_available": static_available,
        "plan_hash": run.plan_hash,
        "profile": f"{profile.slug}@{profile.version}" if profile is not None else None,
    }
    if error is None:
        if run.status == AnalysisRun.Status.FAILED:
            error = run.error_message or "Analysis failed"
        elif run.status == AnalysisRun.Status.CANCELLED:
            error = run.error_message or "Analysis was cancelled"
        elif run.status == AnalysisRun.Status.PARTIAL and params.get("fail_on_partial"):
            error = "Analysis is partial: a required tool failed"
        elif threshold_status == "failed" and params.get("fail_on_threshold"):
            error = "Finding thresholds exceeded"
        elif thresholds and threshold_status == "not_evaluated" and params.get("fail_on_threshold"):
            error = "Thresholds could not be evaluated: the static measurement is unavailable"
    if error:
        output["error"] = error
    return output


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def dispatch_report(
    step_run: PipelineStepRun,
    params: dict[str, Any],
    run_params: dict[str, Any],
) -> dict[str, Any]:
    """Create and dispatch a Report generation job, poll until terminal."""

    from backend.reports.services.report_service import create_and_dispatch

    report_type = params.get("report_type") or run_params.get("report_type")
    if not report_type:
        return {"status": "failed", "error": "Report step needs a 'report_type'"}
    config = params.get("config", {})
    title = params.get("title")
    description = params.get("description", "")
    user = _analysis_user(step_run)
    try:
        timeout_s = max(1, int(params.get("timeout_seconds", _REPORT_TIMEOUT_SECONDS)))
    except (TypeError, ValueError):
        return {"status": "failed", "error": "'timeout_seconds' must be a whole number of seconds"}

    try:
        report = create_and_dispatch(
            report_type=report_type,
            config=config,
            title=title,
            description=description,
            user=user,
        )
    except Exception as exc:
        logger.exception("create_and_dispatch failed for step_run %s", step_run.id)
        return {"status": "failed", "error": str(exc)}

    # Poll until terminal
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        report.refresh_from_db()
        if report.status in ("completed", "failed"):
            break
        time.sleep(_POLL_INTERVAL)

    output: dict[str, Any] = {
        "report_id": str(report.id),
        "status": report.status,
    }
    if report.status == "failed":
        output["error"] = report.error_message or "Report generation failed"
    elif report.status != "completed":
        output["error"] = f"Report did not finish within {timeout_s}s"
    return output


# ---------------------------------------------------------------------------
# Wait
# ---------------------------------------------------------------------------


def dispatch_wait(
    step_run: PipelineStepRun,
    params: dict[str, Any],
    run_params: dict[str, Any],
) -> dict[str, Any]:
    """Sleep for ``seconds`` from config."""
    seconds = float(params.get("seconds", run_params.get("seconds", 0)))
    logger.info("Step %s waiting %.1fs", step_run.id, seconds)
    time.sleep(seconds)
    return {"waited_seconds": seconds}


# ---------------------------------------------------------------------------
# Notify
# ---------------------------------------------------------------------------


def dispatch_notify(
    step_run: PipelineStepRun,
    params: dict[str, Any],
    run_params: dict[str, Any],
) -> dict[str, Any]:
    """Log + publish a notification event. No actual email delivery yet."""
    channel = params.get("channel", "general")
    message = params.get("message", "Pipeline step completed")
    run_id = str(step_run.run_id)

    logger.info("NOTIFY [%s]: %s (run=%s)", channel, message, run_id)

    publish(
        f"automation:{run_id}",
        {
            "type": "notify",
            "channel": channel,
            "message": message,
            "step_run_id": str(step_run.id),
            "run_id": run_id,
        },
    )
    return {"channel": channel, "message": message, "delivered": True}


# ---------------------------------------------------------------------------
# Script (safe predefined operations ONLY — no arbitrary code execution)
# ---------------------------------------------------------------------------

_ALLOWED_SCRIPT_OPERATIONS = frozenset(
    {
        "set_variables",  # Store key-value pairs into step output
        "conditional",  # Evaluate a condition, store result in output
        "log",  # Emit a log message
        "noop",  # Do nothing (placeholder)
    },
)


def dispatch_script(
    step_run: PipelineStepRun,
    params: dict[str, Any],
    run_params: dict[str, Any],
) -> dict[str, Any]:
    """Execute a predefined safe scripting operation.

    SECURITY: This dispatcher does NOT execute arbitrary code. Only the
    operations listed in ``_ALLOWED_SCRIPT_OPERATIONS`` are permitted.
    The ``code`` field in config must be one of those operation names.
    Any attempt to pass arbitrary Python/shell code is silently treated
    as a ``noop`` and a warning is emitted.
    """
    operation = params.get("code", "noop")

    if operation not in _ALLOWED_SCRIPT_OPERATIONS:
        logger.warning(
            "Step %s requested disallowed script operation %r — treating as noop",
            step_run.id,
            operation,
        )
        return {
            "operation": "noop",
            "warning": f"Operation '{operation}' is not allowed",
        }

    if operation == "set_variables":
        variables = params.get("variables", {})
        return {"operation": "set_variables", "variables": variables}

    if operation == "conditional":
        condition_key = params.get("condition_key", "")
        condition_value = params.get("condition_value")
        actual = run_params.get(condition_key)
        result = actual == condition_value
        return {
            "operation": "conditional",
            "result": result,
            "condition_key": condition_key,
        }

    if operation == "log":
        message = params.get("message", "")
        logger.info("Script log [step %s]: %s", step_run.id, message)
        return {"operation": "log", "message": message}

    # noop
    return {"operation": "noop"}
