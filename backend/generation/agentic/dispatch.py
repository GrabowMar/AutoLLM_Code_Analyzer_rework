"""``GenerationRun`` to stage-function dispatcher (replaces the v2 orchestrator).

``run_stage(run)`` is the executor's only entry point for the generation stage of every kind
(``execution/workers.py`` calls nothing else):

========================= ===========================================================================
kind                      stage
========================= ===========================================================================
``app_agentic``           ``agentic.runner.run_app_agentic`` (sandbox lane)
``slot_agent``            ``agentic.runner.run_agentic`` with ``slot.executors.restricted_agent.prepare``
``slot_single_shot``      ``slot.executors.single_shot.run_stage``, or the batch submitter when the run's
                          serving tier resolved to ``batch`` and ``BATCH_BACKENDS_VERIFIED`` (llm lane)
``reference``             no generation stage: handed straight to the eval lane (D6)
========================= ===========================================================================
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from backend.generation.agentic.sandbox import SandboxBackend
    from backend.generation.models import GenerationRun

logger = logging.getLogger(__name__)

__all__ = ["run_stage", "uses_batch"]


def uses_batch(run: GenerationRun) -> bool:
    """Whether a single-shot slot run goes through the provider batch API (section 7.6)."""
    if not getattr(settings, "BATCH_BACKENDS_VERIFIED", False):
        return False
    manifest = getattr(getattr(run, "manifest", None), "manifest", None) or {}
    effective = (manifest.get("resources") or {}).get("effective") or {}
    content = effective.get("content") if isinstance(effective.get("content"), dict) else effective
    tier = ((content or {}).get("routing") or {}).get("serving_tier")
    return tier == "batch"


def run_stage(run: GenerationRun, *, backend: SandboxBackend | None = None) -> None:
    """Run the generation stage of ``run``; the stage ends itself through ``execution.transitions``."""
    from backend.execution import transitions
    from backend.generation.models.choices import RunKind

    kind = run.kind
    if kind == RunKind.APP_AGENTIC:
        from backend.generation.agentic.runner import run_app_agentic

        run_app_agentic(run, backend=backend)
    elif kind == RunKind.SLOT_AGENT:
        from backend.generation.agentic.runner import run_agentic
        from backend.generation.slot.executors import restricted_agent

        run_agentic(run, backend=backend, slot=restricted_agent.prepare(run))
    elif kind == RunKind.SLOT_SINGLE_SHOT:
        if uses_batch(run):
            from backend.generation.slot.executors import batch_submitter

            batch_submitter.run_batch([run])
        else:
            from backend.generation.slot.executors import single_shot

            single_shot.run_stage(run)
    elif kind == RunKind.REFERENCE:
        logger.warning("reference run %s reached the generation stage; handing it to the eval lane", run.pk)
        transitions.finish_stage(run, transitions.OUTCOME_EVALUATE)
    else:
        msg = f"run {run.pk} has an unknown kind {kind!r}"
        raise ValueError(msg)
