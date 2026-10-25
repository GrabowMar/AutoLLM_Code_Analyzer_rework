"""Experiment schedules and post-runs, ticked by the executor (spec §F.5). Contract stub → Track C.

The executor's main loop calls :func:`run_due_schedules` and
:func:`advance_post_runs` every 30 s; both only *claim* rows
(``FOR UPDATE SKIP LOCKED``) and submit the work to the post-run pool
(:func:`run_schedule_action`, ``execution.workers.run_post_run``). Errors are
recorded on the rows and never raised.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import Experiment
    from backend.generation.models import ExperimentPostRun
    from backend.llm_models.models import LLMModel


def run_due_schedules(now: datetime, submit=None) -> list[int]:
    """Claim enabled schedules with ``next_run_at <= now`` and advance ``next_run_at`` (croniter, schedule tz).

    Skips (``last_result={outcome: "skipped"}``) when ``skip_if_active`` and the
    experiment has PENDING/RUNNING jobs or is paused; otherwise hands the id
    to ``submit(run_schedule_action, schedule_id)`` (the post-run pool; when
    ``submit`` is None the caller submits the returned ids). Draining stops
    schedule firing. Returns the ids to run.
    """
    msg = "experiment_automation.run_due_schedules: Track C"
    raise NotImplementedError(msg)


def run_schedule_action(schedule_id: int) -> dict[str, Any]:
    """Fire one schedule: ``top_up`` / ``add_replicates`` / ``clone_and_run``; returns and stores ``last_result``.

    ``digest_drift`` blocks ``top_up``/``add_replicates`` and is recorded as
    ``{outcome: "error", detail: "digest_drift"}``.
    """
    msg = "experiment_automation.run_schedule_action: Track C"
    raise NotImplementedError(msg)


def advance_post_runs(executor_id: str) -> ExperimentPostRun | None:
    """Claim one pending post-run (SKIP LOCKED, leased to ``executor_id``) and run it to completion.

    Analyses run sequentially over completed current-attempt jobs without an
    analysis (``only_passing`` → ``pass_rate == 1``), then the
    ``experiment_report``; progress is published as ``post_run`` events.
    Returns the post-run it processed, or None.
    """
    msg = "experiment_automation.advance_post_runs: Track C"
    raise NotImplementedError(msg)


def on_experiment_completed(experiment: Experiment) -> ExperimentPostRun | None:
    """Called once per completion by ``maybe_complete_experiment``: create ``ExperimentPostRun(trigger=completed)``
    with a frozen copy of ``experiment.post_run`` when analysis or report is enabled; else None."""
    msg = "experiment_automation.on_experiment_completed: Track C"
    raise NotImplementedError(msg)


def pause_for_digest_drift(model: LLMModel) -> list[Any]:
    """Pause every RUNNING experiment with PENDING jobs whose frozen digest for ``model`` differs from
    ``model.digest`` (``pause_reason="digest_changed"``, ``state`` event). Called by inventory (Track B).
    Returns the paused experiment ids."""
    msg = "experiment_automation.pause_for_digest_drift: Track C"
    raise NotImplementedError(msg)
