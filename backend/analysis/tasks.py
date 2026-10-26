"""Celery tasks for analysis (owner: executor stage).

``analysis.process_run`` executes one run (``engine.executor.process_run``);
it is idempotent and lease-guarded, so a redelivered or duplicated message is
harmless. ``acks_late`` + ``reject_on_worker_lost`` hand the message to
another worker when a worker process dies mid-run, and explicit time limits
replace the global 60 s / 300 s defaults (a run may take a long time).

When no worker answers a (cached) ping, the same entry points run on a
daemon thread in the calling process; ``reconcile`` recovers any run such a
thread loses on a process restart.
"""

from __future__ import annotations

import logging
import threading
import time

from celery import shared_task
from django.conf import settings
from django.db import close_old_connections
from django.db import connection

from backend.common.threading import dispatch_in_thread

logger = logging.getLogger(__name__)

RUN_SOFT_TIME_LIMIT = int(getattr(settings, "ANALYSIS_TASK_SOFT_TIME_LIMIT", 4 * 3600))
BUILD_SOFT_TIME_LIMIT = 3 * 3600
CELERY_PING_TTL_SECONDS = 60.0

_ping_lock = threading.Lock()
_ping_cache = {"at": -1e9, "ok": False}


@shared_task(
    name="analysis.process_run",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=RUN_SOFT_TIME_LIMIT,
    time_limit=RUN_SOFT_TIME_LIMIT + 600,
    ignore_result=True,
)
def process_run_task(run_id: str) -> None:
    from backend.analysis.engine import executor

    executor.process_run(run_id)


@shared_task(name="analysis.reconcile", soft_time_limit=240, time_limit=300, ignore_result=True)
def reconcile_task() -> dict:
    from backend.analysis.engine import executor

    return executor.reconcile()


@shared_task(
    name="analysis.build_image",
    soft_time_limit=BUILD_SOFT_TIME_LIMIT,
    time_limit=BUILD_SOFT_TIME_LIMIT + 600,
    ignore_result=True,
)
def build_image_task() -> None:
    from backend.analysis.engine import sandbox

    sandbox.build_image()


def celery_available() -> bool:
    """Whether a Celery worker answers a ping (cached for a minute, both ways)."""
    if not getattr(settings, "ANALYSIS_USE_CELERY", True):
        return False
    with _ping_lock:
        if time.monotonic() - _ping_cache["at"] < CELERY_PING_TTL_SECONDS:
            return bool(_ping_cache["ok"])
        try:
            from celery import current_app

            ok = bool(current_app.control.ping(timeout=1.0))
        except Exception:  # noqa: BLE001 - broker down == no worker
            ok = False
        if not ok:
            logger.info("No Celery worker answered; analysis runs on in-process threads")
        _ping_cache.update(at=time.monotonic(), ok=ok)
        return ok


def _forget_ping() -> None:
    with _ping_lock:
        _ping_cache.update(at=-1e9, ok=False)


def dispatch_process_run(run_id: str, *, countdown: int = 0) -> str:
    """Hand a run to a worker; returns ``"celery"`` or ``"thread"``."""
    if celery_available():
        try:
            process_run_task.apply_async(args=[str(run_id)], countdown=max(0, int(countdown)))
        except Exception:
            logger.exception("Enqueuing analysis run %s on Celery failed; using a thread", run_id)
            _forget_ping()
        else:
            return "celery"
    dispatch_in_thread(_run_in_thread, str(run_id), countdown, name=f"analysis-run-{str(run_id)[:8]}")
    return "thread"


def _run_in_thread(run_id: str, countdown: int) -> None:
    if countdown > 0:
        time.sleep(countdown)
    close_old_connections()
    try:
        from backend.analysis.engine import executor

        executor.process_run(run_id)
    except Exception:
        logger.exception("Analysis run %s crashed on its thread", run_id)
    finally:
        # This thread's connection (it also drops any advisory lock left on it).
        connection.close()


def dispatch_image_build() -> str:
    """Build the analyzer image in the background; returns ``"celery"`` or ``"thread"``."""
    if celery_available():
        try:
            build_image_task.apply_async()
        except Exception:
            logger.exception("Enqueuing the analyzer image build failed; using a thread")
            _forget_ping()
        else:
            return "celery"
    dispatch_in_thread(_build_in_thread, name="analysis-image-build")
    return "thread"


def _build_in_thread() -> None:
    close_old_connections()
    try:
        from backend.analysis.engine import sandbox

        sandbox.build_image()
    except Exception:
        logger.exception("Building the analyzer image failed")
    finally:
        connection.close()


# --------------------------------------------------------------------------
# Study work started by API requests (owner: API/research stage). Both entry
# points are idempotent and never raise; the API dispatches them from
# ``transaction.on_commit`` so the job sees the committed request.
# --------------------------------------------------------------------------

# A report of a few hundred runs takes seconds (pure-Python statistics);
# research/study.REPORT_STALE_SECONDS must stay above the hard limit.
REPORT_SOFT_TIME_LIMIT = 15 * 60
REPORT_TIME_LIMIT = REPORT_SOFT_TIME_LIMIT + 60
# Submitting runs resolves one sample per target (~50 ms each).
LAUNCH_SOFT_TIME_LIMIT = 30 * 60
LAUNCH_TIME_LIMIT = LAUNCH_SOFT_TIME_LIMIT + 60


@shared_task(
    name="analysis.generate_study_report",
    acks_late=True,
    soft_time_limit=REPORT_SOFT_TIME_LIMIT,
    time_limit=REPORT_TIME_LIMIT,
    ignore_result=True,
)
def generate_study_report_task(study_id: str, request_id: str) -> str:
    """Build and store a study's post-run report (only while *request_id* is the current request)."""
    from backend.analysis.research import study

    return study.generate_report(study_id, request_id)


@shared_task(
    name="analysis.launch_study",
    acks_late=True,
    # Submission is idempotent, so a message whose worker died is safe to redeliver.
    reject_on_worker_lost=True,
    soft_time_limit=LAUNCH_SOFT_TIME_LIMIT,
    time_limit=LAUNCH_TIME_LIMIT,
    ignore_result=True,
)
def launch_study_task(study_id: str) -> None:
    """Submit the runs of a launched study (targets that already have a run are skipped)."""
    from backend.analysis.research import study

    study.submit_launch(study_id)


def dispatch_study_report(study_id: str, request_id: str) -> str:
    """Generate a study report in the background; returns ``"celery"`` or ``"thread"``."""
    if celery_available():
        try:
            generate_study_report_task.apply_async(args=[str(study_id), str(request_id)])
        except Exception:
            logger.exception("Enqueuing the report of study %s failed; using a thread", study_id)
            _forget_ping()
        else:
            return "celery"
    dispatch_in_thread(
        _study_job_in_thread,
        "generate_report",
        str(study_id),
        str(request_id),
        name=f"analysis-report-{str(study_id)[:8]}",
    )
    return "thread"


def dispatch_study_launch(study_id: str) -> str:
    """Submit a launched study's runs in the background; returns ``"celery"`` or ``"thread"``."""
    if celery_available():
        try:
            launch_study_task.apply_async(args=[str(study_id)])
        except Exception:
            logger.exception("Enqueuing the launch of study %s failed; using a thread", study_id)
            _forget_ping()
        else:
            return "celery"
    dispatch_in_thread(
        _study_job_in_thread,
        "submit_launch",
        str(study_id),
        name=f"analysis-launch-{str(study_id)[:8]}",
    )
    return "thread"


def _study_job_in_thread(function: str, *args: str) -> None:
    close_old_connections()
    try:
        from backend.analysis.research import study

        getattr(study, function)(*args)
    except Exception:
        logger.exception("Study job %s%s crashed on its thread", function, args)
    finally:
        connection.close()
