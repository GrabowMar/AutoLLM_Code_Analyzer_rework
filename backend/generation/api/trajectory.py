"""Agent behaviour analytics: per-model, per-harness aggregates of the parsed trajectories.

* ``GET /models/{model_id}/behaviour/`` -- descriptive aggregates over the caller's finished agentic runs
  of a model, one group per harness (name and version from the run manifest).

A run's own trajectory (steps, observed inputs) is ``GET /runs/{id}/trajectory/`` (``runs/api.py``, WP-08).
The aggregates read each run's **last attempt** (``RunAttempt.trajectory_blob``, the harness adapter's
``Trajectory`` in the blob store, and ``RunAttempt.time_split``). Read-only: nothing here writes to a run.
Access: a model resolves through ``LLMModel.objects.visible_to``; only the caller's own runs are pooled.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from typing import Any

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from ninja import Router

from backend.generation import blobs
from backend.generation.models import GenerationRun
from backend.generation.models import RunAttempt
from backend.generation.models.choices import RunKind
from backend.generation.models.choices import RunStatus
from backend.llm_models.models import LLMModel

logger = logging.getLogger(__name__)

router = Router(tags=["generation"])

# Trajectory counters aggregated as numbers (``contracts.harness.Trajectory``), plus the step count.
NUMERIC_TRAJECTORY_KEYS = ("steps", "tool_calls", "edits", "shell_commands", "compactions", "subagent_calls")
BOOLEAN_TRAJECTORY_KEYS = ("ended_on_llm_error", "max_turns_hit")
# ``RunAttempt.time_split`` (seconds).
TIME_KEYS = ("llm_s", "tool_s", "install_s", "idle_s")


def _describe(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _trajectory(attempt: RunAttempt | None) -> dict[str, Any] | None:
    """The parsed trajectory of *attempt* as counters, ``None`` when none was stored or the blob is unreadable."""
    if attempt is None or not attempt.trajectory_blob:
        return None
    try:
        payload = blobs.get_json(attempt.trajectory_blob)
    except (OSError, LookupError, ValueError):
        logger.warning("Trajectory blob %s of run %s is unreadable", attempt.trajectory_blob, attempt.run_id)
        return None
    if not isinstance(payload, dict):
        return None
    steps = payload.get("steps")
    return {**payload, "steps": len(steps) if isinstance(steps, list) else None}


def _harness_key(run: GenerationRun) -> tuple[str, str]:
    manifest = getattr(run, "manifest", None)
    harness = ((manifest.manifest if manifest is not None else None) or {}).get("harness") or {}
    if harness.get("name"):
        return str(harness["name"]), str(harness.get("version") or "?")
    preset = run.harness_preset
    if preset is not None:
        return preset.harness, preset.harness_version or "?"
    return "unknown", "?"


def _eval_pass_rate(run: GenerationRun) -> float | None:
    """``metrics.eval.agent_pass_rate`` of a **measured** evaluation; ``None`` otherwise (never a 0)."""
    summary = (run.metrics or {}).get("eval") if isinstance(run.metrics, dict) else None
    if not isinstance(summary, dict) or not summary.get("measured"):
        return None
    return _number(summary.get("agent_pass_rate"))


@router.get("/models/{model_id}/behaviour/", response=dict)
def model_behaviour(request, model_id: int):
    """Per-harness descriptive aggregates of the trajectories of the caller's finished agentic runs.

    Descriptive only (n, mean, median, min, max; shares for yes/no fields): no tests. Runs whose outcome is
    not usable are left out and counted; usable runs without a stored trajectory are counted too.
    """
    model = get_object_or_404(LLMModel.objects.visible_to(request.auth), pk=model_id)
    runs = (
        GenerationRun.objects.filter(
            created_by=request.auth,
            model=model,
            kind=RunKind.APP_AGENTIC,
            status=RunStatus.COMPLETED,
        )
        .select_related("manifest", "harness_preset")
        .prefetch_related(Prefetch("attempts", queryset=RunAttempt.objects.order_by("-attempt")))
        .order_by("created_at")
    )
    groups: dict[tuple[str, str], list[GenerationRun]] = defaultdict(list)
    for run in runs:
        groups[_harness_key(run)].append(run)

    harnesses = []
    for (harness, version), members in sorted(groups.items()):
        usable = [r for r in members if r.usable]
        records = []
        times = []
        for run in usable:
            attempts = list(run.attempts.all())
            last = attempts[0] if attempts else None
            record = _trajectory(last)
            if record is not None:
                records.append(record)
            if last is not None and isinstance(last.time_split, dict) and last.time_split:
                times.append(last.time_split)
        metrics = {
            key: _describe([v for r in records if (v := _number(r.get(key))) is not None])
            for key in NUMERIC_TRAJECTORY_KEYS
        }
        time_split = {
            key: _describe([v for t in times if (v := _number(t.get(key))) is not None]) for key in TIME_KEYS
        }
        rates = {}
        for key in BOOLEAN_TRAJECTORY_KEYS:
            answered = [r.get(key) for r in records if isinstance(r.get(key), bool)]
            yes = sum(1 for v in answered if v)
            rates[key] = {"n": len(answered), "k": yes, "share": round(yes / len(answered), 4) if answered else None}
        pass_rates = [v for r in usable if (v := _eval_pass_rate(r)) is not None]
        harnesses.append(
            {
                "harness": harness,
                "harness_version": version,
                "n_runs": len(members),
                "n_unusable_excluded": len(members) - len(usable),
                "n_without_trajectory": len(usable) - len(records),
                "n": len(records),
                "parsers": sorted({f"{r.get('parser')}@{r.get('parser_version')}" for r in records}),
                "metrics": metrics,
                "time_split": time_split,
                "rates": rates,
                "pass_rate": _describe(pass_rates),
            },
        )
    notes = [
        (
            "Descriptive only: no significance tests. Each harness version is summarised on its own; counters come "
            "from each harness's own trajectory parser and are compared within a harness."
        ),
        "Pooled over every task this model ran, so differences can reflect the task mix.",
        "Pass rate: measured evaluations only (runs never evaluated or not measured are left out, not 0).",
    ]
    if any(len(h["parsers"]) > 1 for h in harnesses):
        notes.append("Trajectories come from more than one parser version; compare like with like.")
    return {"model_id": model.pk, "harnesses": harnesses, "notes": notes}
