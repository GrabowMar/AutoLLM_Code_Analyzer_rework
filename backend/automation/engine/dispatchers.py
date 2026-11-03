"""Step dispatchers — one function per PipelineStep.Kind.

Each dispatcher receives:
  - step_run: PipelineStepRun instance (already saved, status=running)
  - params: resolved config dict for this step
  - run_params: top-level PipelineRun.params

Each dispatcher returns an output dict that is stored on PipelineStepRun.output.
Dispatchers BLOCK until the underlying runs/task reach a terminal state so
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

from backend.realtime.events import publish

if TYPE_CHECKING:
    from backend.automation.models import PipelineStepRun

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2  # seconds between status polls


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})
_GENERATE_TIMEOUT_S = 6 * 3600


def _wait_for_runs(run_ids: list[str], timeout: float) -> dict[Any, str] | None:
    """``{run id: status}`` once every run is terminal; None when ``timeout`` seconds passed first."""
    from backend.generation.models import GenerationRun

    deadline = time.monotonic() + timeout
    while True:
        statuses = dict(GenerationRun.objects.filter(id__in=run_ids).values_list("id", "status"))
        if len(statuses) == len(run_ids) and all(v in _TERMINAL_RUN_STATUSES for v in statuses.values()):
            return statuses
        if time.monotonic() >= deadline:
            return None
        time.sleep(_POLL_INTERVAL)


def dispatch_generate(
    step_run: PipelineStepRun,
    params: dict[str, Any],
    run_params: dict[str, Any],
) -> dict[str, Any]:
    """Launch an ad-hoc study from a design, wait until its runs are terminal, return output.

    The step's ``design`` (or the pipeline run's) is a ``contracts.plan.Design`` payload; it is
    resolved and launched through ``study.launch.launch_adhoc`` (spec section 9.4), which returns
    the study and the created run ids. The step then blocks until every created run reached a
    terminal status (or ``timeout_seconds`` passed).
    """
    from pydantic import ValidationError

    from backend.generation.contracts.plan import Design
    from backend.generation.study.launch import launch_adhoc
    from backend.users.models import User

    raw = params.get("design") or run_params.get("design")
    if not isinstance(raw, dict):
        return {"status": "failed", "error": "generate step needs a 'design' (contracts.plan.Design)"}
    try:
        design = Design.model_validate(raw)
    except ValidationError as exc:
        return {"status": "failed", "error": f"invalid design: {exc.error_count()} error(s)", "details": exc.errors()}

    triggered_by = step_run.run.triggered_by
    user = triggered_by if triggered_by else User.objects.first()
    try:
        launched = launch_adhoc(design, user=user, launch=True)
    except Exception as exc:
        logger.exception("launch_adhoc failed for step_run %s", step_run.id)
        return {"status": "failed", "error": str(exc)}

    run_ids = [str(run_id) for run_id in launched.get("run_ids") or []]
    output: dict[str, Any] = {
        "study_id": str(launched["study_id"]) if launched.get("study_id") else None,
        "plan_hash": launched.get("plan_hash"),
        "generation_run_ids": run_ids,
    }
    if not run_ids:
        return {**output, "status": "failed", "error": "the launch created no runs"}

    timeout = float(params.get("timeout_seconds") or _GENERATE_TIMEOUT_S)
    statuses = _wait_for_runs(run_ids, timeout)
    if statuses is None:
        return {**output, "status": "failed", "error": f"runs not finished after {timeout:.0f}s"}

    counts: dict[str, int] = {}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1
    output["run_statuses"] = counts
    # The first run is what a following analyze step analyses by default.
    output["generation_run_id"] = run_ids[0]
    if counts.get("completed", 0) == 0:
        output["error"] = "no run completed"
    output["status"] = "failed" if "error" in output else "completed"
    return output


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------


def _threshold_status(summary: dict[str, Any], thresholds: dict[str, Any]) -> str:
    """Compare finding severity counts against optional thresholds."""
    if not thresholds:
        return "not_evaluated"
    counts = (summary or {}).get("severity_counts", {})
    for severity, limit in thresholds.items():
        try:
            if int(counts.get(severity, 0)) > int(limit):
                return "failed"
        except (TypeError, ValueError):
            continue
    return "passed"


def dispatch_analyze(
    step_run: PipelineStepRun,
    params: dict[str, Any],
    run_params: dict[str, Any],
) -> dict[str, Any]:
    """Create an AnalysisRun and run it synchronously, return output."""
    from backend.analysis.models import AnalysisRun
    from backend.analysis.services import runner
    from backend.analysis.services import workspace_service
    from backend.users.models import User

    triggered_by = step_run.run.triggered_by
    user = triggered_by if triggered_by else User.objects.first()

    generation_run_id = params.get("generation_run_id") or run_params.get("generation_run_id")
    source_code = params.get("source_code")
    # Accept either the new ``tool_slugs`` or the legacy ``analyzers`` key.
    tool_slugs = params.get("tool_slugs") or params.get("analyzers") or []
    thresholds = params.get("thresholds", {})

    workspace = workspace_service.get_workspace(user) if user else None
    run = AnalysisRun.objects.create(
        name=f"Automation step {step_run.id}",
        created_by=user,
        workspace=workspace,
        generation_job_id=generation_run_id or None,
        source_code=source_code,
        tool_slugs=list(tool_slugs),
    )

    error: str | None = None
    try:
        runner.execute(
            AnalysisRun.objects.select_related("created_by", "workspace").get(id=run.id),
        )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)

    run.refresh_from_db()
    output: dict[str, Any] = {
        "analysis_run_id": str(run.id),
        "status": run.status,
        "summary": run.summary,
        "threshold_status": _threshold_status(run.summary, thresholds),
    }
    if error:
        output["error"] = error
    elif run.status == AnalysisRun.Status.FAILED:
        output["error"] = run.error_message or "Analysis failed"
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

    report_type = params.get("report_type") or run_params.get("report_type", "summary")
    config = params.get("config", {})
    title = params.get("title")
    description = params.get("description", "")
    user = step_run.run.triggered_by

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
    deadline = time.monotonic() + 300
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
