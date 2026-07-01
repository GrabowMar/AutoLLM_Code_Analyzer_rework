"""Core pipeline execution engine.

``execute_run(run_id)``  — entry point called by Celery task or thread.
``execute_step(step_run_id)`` — dispatches a single step, handles retries.

Concurrency notes
-----------------
* ``select_for_update()`` guards status transitions so two workers cannot
  double-execute the same run/step.
* Django DB connections are explicitly closed in thread finally blocks to
  avoid connection leaks.
* Steps are executed in parallel threads when their DAG dependencies allow.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC
from datetime import datetime
from typing import Any

from django.db import connection
from django.db import transaction
from django.utils import timezone

from backend.automation.engine.params import resolve_params
from backend.realtime.events import publish

logger = logging.getLogger(__name__)


def _get_dispatcher(kind: str) -> Any:
    """Look up dispatcher by step kind, enabling test-time mocking via module patch."""
    from backend.automation.engine import dispatchers

    return {
        "generate": dispatchers.dispatch_generate,
        "analyze": dispatchers.dispatch_analyze,
        "report": dispatchers.dispatch_report,
        "wait": dispatchers.dispatch_wait,
        "notify": dispatchers.dispatch_notify,
        "script": dispatchers.dispatch_script,
    }.get(kind, dispatchers.dispatch_script)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _publish_run_event(run_id: Any, status: str, **extra: Any) -> None:
    publish(
        f"automation:{run_id}",
        {
            "type": "run_status",
            "run_id": str(run_id),
            "status": status,
            "ts": datetime.now(UTC).isoformat(),
            **extra,
        },
    )


def _publish_step_event(
    run_id: Any,
    step_run_id: Any,
    step_name: str | None,
    status: str,
    **extra: Any,
) -> None:
    publish(
        f"automation:{run_id}",
        {
            "type": "step_status",
            "run_id": str(run_id),
            "step_run_id": str(step_run_id),
            "step_name": step_name,
            "status": status,
            "ts": datetime.now(UTC).isoformat(),
            **extra,
        },
    )


def _collect_prior_outputs(run_id: Any) -> dict[str, dict[str, Any]]:
    """Return {step_name: output_dict} for all succeeded step runs in a run."""
    from backend.automation.models import PipelineStepRun

    result: dict[str, dict[str, Any]] = {}
    for sr in PipelineStepRun.objects.filter(
        run_id=run_id,
        status="succeeded",
    ).select_related("step"):
        if sr.step and sr.step.name:
            result[sr.step.name] = {"output": sr.output or {}}
    return result


def _is_cancelled(run_id: Any) -> bool:
    """Check if a run has been externally cancelled."""
    from backend.automation.models import PipelineRun

    return PipelineRun.objects.filter(id=run_id, status="cancelled").exists()


def _output_is_failed(output: Any) -> bool:
    """Return True if a dispatcher output dict indicates failure."""
    if not isinstance(output, dict):
        return False
    if output.get("status") in ("failed", "error"):
        return True
    return bool(output.get("error") and output.get("status") != "succeeded")


# ---------------------------------------------------------------------------
# execute_step
# ---------------------------------------------------------------------------


def execute_step(step_run_id: Any) -> None:
    """Dispatch a single step, handle retries, record output.

    This function is designed to be called from a thread. It closes the
    Django DB connection in the finally block to avoid leaks.
    """
    from backend.automation.models import PipelineStepRun
    from backend.automation.models import RunStatus

    try:
        # Idempotency guard: only execute if still pending
        with transaction.atomic():
            sr = PipelineStepRun.objects.select_for_update().get(id=step_run_id)
            if sr.status != RunStatus.PENDING:
                logger.info(
                    "Step run %s is %s — skipping",
                    step_run_id,
                    sr.status,
                )
                return
            sr.status = RunStatus.RUNNING
            sr.started_at = timezone.now()
            sr.save(update_fields=["status", "started_at"])

        step_name = sr.step.name if sr.step else None
        run_id = sr.run_id

        _publish_step_event(run_id, step_run_id, step_name, "running")

        # Resolve params
        prior_outputs = _collect_prior_outputs(run_id)
        run = sr.run
        resolved_config = resolve_params(
            sr.step.config if sr.step else {},
            prior_outputs,
            run.params,
        )

        dispatcher = _get_dispatcher(sr.step.kind if sr.step else "script")

        try:
            output = dispatcher(sr, resolved_config, run.params)
        except Exception as exc:
            logger.exception("Dispatcher raised for step_run %s", step_run_id)
            output = {"error": str(exc), "status": "failed"}

        failed = _output_is_failed(output)

        if failed and sr.retries_remaining > 0:
            with transaction.atomic():
                sr_locked = PipelineStepRun.objects.select_for_update().get(
                    id=step_run_id,
                )
                sr_locked.status = RunStatus.PENDING
                sr_locked.retries_remaining -= 1
                sr_locked.attempt += 1
                sr_locked.error = output.get("error", "") if isinstance(output, dict) else ""
                sr_locked.save(
                    update_fields=[
                        "status",
                        "retries_remaining",
                        "attempt",
                        "error",
                    ],
                )
            logger.info(
                "Retrying step_run %s (attempt %d, retries_remaining=%d)",
                step_run_id,
                sr_locked.attempt,
                sr_locked.retries_remaining,
            )
            execute_step(step_run_id)
            return

        with transaction.atomic():
            sr_locked = PipelineStepRun.objects.select_for_update().get(id=step_run_id)
            sr_locked.output = output or {}
            sr_locked.completed_at = timezone.now()
            if failed:
                sr_locked.status = RunStatus.FAILED
                sr_locked.error = output.get("error", "Step failed") if isinstance(output, dict) else "Step failed"
            else:
                sr_locked.status = RunStatus.SUCCEEDED
                sr_locked.error = ""
            sr_locked.save(
                update_fields=["status", "output", "completed_at", "error"],
            )

        _publish_step_event(
            run_id,
            step_run_id,
            step_name,
            sr_locked.status,
            output=sr_locked.output,
        )

    except Exception:
        logger.exception("Unexpected error in execute_step for %s", step_run_id)
        try:
            from backend.automation.models import PipelineStepRun
            from backend.automation.models import RunStatus

            PipelineStepRun.objects.filter(
                id=step_run_id,
                status="running",
            ).update(
                status=RunStatus.FAILED,
                error="Internal engine error",
                completed_at=timezone.now(),
            )
        except Exception:
            logger.exception(
                "Failed to mark step_run %s as failed",
                step_run_id,
            )
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# execute_run helpers
# ---------------------------------------------------------------------------


def _cancel_pending_steps(run_id: Any) -> None:
    """Set all pending step_runs in *run_id* to cancelled."""
    from backend.automation.models import PipelineStepRun
    from backend.automation.models import RunStatus

    PipelineStepRun.objects.filter(
        run_id=run_id,
        status=RunStatus.PENDING,
    ).update(
        status=RunStatus.CANCELLED,
        completed_at=timezone.now(),
    )


def _deps_satisfied(
    sr: Any,
    fresh_step_runs: dict[Any, Any],
    name_to_sr_id: dict[str, Any],
) -> bool:
    """Return True when all dependencies of *sr* have succeeded."""
    from backend.automation.models import RunStatus

    if not (sr.step and sr.step.depends_on):
        return True
    for dep_name in sr.step.depends_on:
        dep_sr_id = name_to_sr_id.get(dep_name)
        if dep_sr_id is None:
            continue
        dep_status = fresh_step_runs[dep_sr_id].status
        if dep_status != RunStatus.SUCCEEDED:
            return False
    return True


# ---------------------------------------------------------------------------
# execute_run
# ---------------------------------------------------------------------------


def execute_run(run_id: Any) -> None:
    """Top-level entry point. Loads run + steps, executes DAG, marks terminal.

    Algorithm:
    1. Mark run RUNNING (idempotency guard via select_for_update).
    2. Build step dependency graph from PipelineStep.depends_on.
    3. Loop:
       a. Check for cancellation.
       b. Find step_runs whose dependencies are all succeeded and pending.
       c. Launch each in a separate daemon thread calling execute_step().
       d. Wait briefly then repeat.
    4. Once all step_runs are terminal, compute overall run status.
    5. Publish final run event.
    """
    from backend.automation.models import PipelineRun
    from backend.automation.models import PipelineStepRun
    from backend.automation.models import RunStatus

    try:
        with transaction.atomic():
            run = PipelineRun.objects.select_for_update().get(id=run_id)
            if run.status not in (RunStatus.PENDING, RunStatus.RUNNING):
                logger.info(
                    "Run %s is already %s — skipping",
                    run_id,
                    run.status,
                )
                return
            run.status = RunStatus.RUNNING
            run.started_at = timezone.now()
            run.save(update_fields=["status", "started_at"])

        _publish_run_event(run_id, "running")

        step_runs = list(
            PipelineStepRun.objects.filter(run_id=run_id).select_related("step").order_by("created_at"),
        )
        if not step_runs:
            _finish_run(run_id, RunStatus.SUCCEEDED, "No steps to execute")
            return

        # Map step_name → step_run_id for dependency resolution
        name_to_sr_id: dict[str, Any] = {}
        for sr in step_runs:
            if sr.step:
                name_to_sr_id[sr.step.name] = sr.id

        in_flight: set[Any] = set()
        in_flight_threads: dict[Any, threading.Thread] = {}

        while True:
            if _is_cancelled(run_id):
                for sr_id in list(in_flight):
                    PipelineStepRun.objects.filter(
                        id=sr_id,
                        status__in=["pending", "running"],
                    ).update(
                        status=RunStatus.CANCELLED,
                        completed_at=timezone.now(),
                    )
                _finish_run(run_id, RunStatus.CANCELLED, "Run cancelled by user")
                return

            fresh_step_runs: dict[Any, Any] = {
                sr.id: sr
                for sr in PipelineStepRun.objects.filter(
                    run_id=run_id,
                ).select_related("step")
            }

            any_failed = any(sr.status == RunStatus.FAILED for sr in fresh_step_runs.values())
            if any_failed:
                _cancel_pending_steps(run_id)
                for t in list(in_flight_threads.values()):
                    t.join(timeout=30)
                _finish_run(run_id, RunStatus.FAILED, "One or more steps failed")
                return

            terminal_statuses = (
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            )
            finished_ids = {sr_id for sr_id in in_flight if fresh_step_runs[sr_id].status in terminal_statuses}
            in_flight -= finished_ids

            for sr in fresh_step_runs.values():
                if sr.status != RunStatus.PENDING:
                    continue
                if sr.id in in_flight:
                    continue
                if _deps_satisfied(sr, fresh_step_runs, name_to_sr_id):
                    in_flight.add(sr.id)
                    t = threading.Thread(
                        target=execute_step,
                        args=(sr.id,),
                        daemon=True,
                        name=f"step-{sr.id}",
                    )
                    in_flight_threads[sr.id] = t
                    t.start()

            all_terminal = all(sr.status in terminal_statuses for sr in fresh_step_runs.values())
            if all_terminal and not in_flight:
                break

            threading.Event().wait(timeout=0.25)

        final_step_runs = list(PipelineStepRun.objects.filter(run_id=run_id))
        if any(sr.status == RunStatus.FAILED for sr in final_step_runs):
            _finish_run(run_id, RunStatus.FAILED, "One or more steps failed")
        elif all(sr.status == RunStatus.SUCCEEDED for sr in final_step_runs):
            _finish_run(run_id, RunStatus.SUCCEEDED, "All steps succeeded")
        else:
            _finish_run(run_id, RunStatus.CANCELLED, "Run partially cancelled")

    except Exception:
        logger.exception("Unexpected error in execute_run for %s", run_id)
        try:
            from backend.automation.models import PipelineRun

            PipelineRun.objects.filter(id=run_id, status="running").update(
                status="failed",
                error="Internal engine error",
                completed_at=timezone.now(),
            )
            _publish_run_event(run_id, "failed", error="Internal engine error")
        except Exception:
            logger.exception("Failed to mark run %s as failed", run_id)
    finally:
        connection.close()


def _finish_run(run_id: Any, status: str, summary_msg: str) -> None:
    """Mark run terminal and publish event."""
    from backend.automation.models import PipelineRun

    with transaction.atomic():
        PipelineRun.objects.filter(id=run_id).update(
            status=status,
            completed_at=timezone.now(),
            error="" if status == "succeeded" else summary_msg,
            result_summary={"message": summary_msg},
        )
    _publish_run_event(run_id, status, summary=summary_msg)
    logger.info("Run %s finished: %s — %s", run_id, status, summary_msg)
