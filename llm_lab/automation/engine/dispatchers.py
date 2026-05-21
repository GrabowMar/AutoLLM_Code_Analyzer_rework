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

from llm_lab.realtime.events import publish

if TYPE_CHECKING:
    from llm_lab.automation.models import PipelineStepRun

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2  # seconds between status polls


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


def dispatch_generate(
    step_run: PipelineStepRun,
    params: dict[str, Any],
    run_params: dict[str, Any],
) -> dict[str, Any]:
    """Create and execute a GenerationJob, poll until terminal, return output."""
    from llm_lab.generation.models import GenerationJob
    from llm_lab.generation.services.generation_service import GenerationService
    from llm_lab.users.models import User

    model_id = params.get("model_id") or run_params.get("model_id")
    mode = params.get("mode", "custom")

    triggered_by = step_run.run.triggered_by
    user = triggered_by if triggered_by else User.objects.first()

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
                "scaffolding_template",
                "backend_prompt_template",
                "frontend_prompt_template",
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


def dispatch_analyze(
    step_run: PipelineStepRun,
    params: dict[str, Any],
    run_params: dict[str, Any],
) -> dict[str, Any]:
    """Create an AnalysisTask and run it synchronously, return output."""
    import threading

    from llm_lab.analysis.models import AnalysisTask
    from llm_lab.analysis.services.analysis_service import AnalysisService
    from llm_lab.users.models import User

    triggered_by = step_run.run.triggered_by
    user = triggered_by if triggered_by else User.objects.first()

    generation_job_id = params.get("generation_job_id") or run_params.get(
        "generation_job_id",
    )
    source_code = params.get("source_code")
    analyzers = params.get("analyzers", ["static"])
    target_url = params.get("target_url") or run_params.get("target_url")

    configuration: dict[str, Any] = {
        "analyzers": analyzers,
        "settings": params.get("settings", {}),
        "live_target": target_url,
        "generation_job_id": str(generation_job_id) if generation_job_id else None,
    }

    task = AnalysisTask.objects.create(
        name=f"Automation step {step_run.id}",
        generation_job_id=generation_job_id,
        source_code=source_code,
        configuration=configuration,
        created_by=user,
    )

    # Run synchronously in this thread
    done_event = threading.Event()
    exc_holder: list[Exception] = []

    def _run() -> None:
        try:
            service = AnalysisService()
            service.execute(
                AnalysisTask.objects.select_related(
                    "generation_job",
                    "created_by",
                ).get(id=task.id),
            )
        except Exception as exc:  # noqa: BLE001
            exc_holder.append(exc)
        finally:
            done_event.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    done_event.wait()

    task.refresh_from_db()
    output: dict[str, Any] = {
        "analysis_task_id": str(task.id),
        "status": task.status,
    }
    if exc_holder:
        output["error"] = str(exc_holder[0])
    elif task.status == "failed":
        output["error"] = task.error_message or "Analysis failed"
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

    from llm_lab.reports.services.report_service import create_and_dispatch

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
