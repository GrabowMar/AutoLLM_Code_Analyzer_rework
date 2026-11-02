"""Agent trajectory analytics endpoints (per-step timeline, per-model behaviour aggregates).

Read-only views over the run evidence (``services.agent.trajectory``): nothing
here writes to a job. Access follows the job detail endpoints: a job is
visible to the user who created it; a model resolves through
``LLMModel.objects.visible_to`` and only the caller's own jobs are pooled.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from typing import Any

from django.shortcuts import get_object_or_404
from ninja import Query
from ninja.errors import HttpError

from backend.generation.api.views._router import router
from backend.generation.models import GenerationJob
from backend.generation.services.agent import trajectory as traj
from backend.generation.services.conformance import conformance_pass_rate
from backend.llm_models.models import LLMModel

# metrics.behaviour keys aggregated as numbers / as yes-no shares.
NUMERIC_BEHAVIOUR_KEYS = (
    "actions_total",
    "tool_calls_gateway",
    "failed_actions",
    "killed_actions",
    "policy_denials",
    "builds",
    "files_written",
    "writes_after_first_build",
    "llm_seconds",
    "between_calls_seconds",
    "context_tokens_first",
    "context_tokens_max",
    "cache_hit_ratio",
    "reasoning_share",
    "first_build_action_index",
)
BOOLEAN_BEHAVIOUR_KEYS = ("ran_server", "probed_http", "ran_tests")


def _agent_job(request, job_id: str) -> GenerationJob:
    try:
        pk = uuid.UUID(job_id)
    except ValueError as exc:
        raise HttpError(404, "Not Found") from exc
    job = get_object_or_404(GenerationJob, id=pk, created_by=request.auth)
    if job.mode != GenerationJob.Mode.AGENT:
        raise HttpError(404, "Trajectories exist for agent-mode jobs only")
    return job


def _agent_artifacts(job: GenerationJob):
    return job.artifacts.filter(stage__startswith="agent_call_").order_by("stage")


@router.get("/jobs/{job_id}/trajectory/", response=dict)
def job_trajectory(request, job_id: str, limit: int = Query(traj.MAX_STEPS, ge=1, le=2000)):
    """The run's trajectory record plus one row per LLM call (tool calls, categories, outcomes).

    Built from the run record's gateway call log (and OpenCode's event
    stream); when the run record is gone, from the compact per-call
    artifacts (tool outcomes then unknown). A record computed here because
    none is stored (or it was built by an older parser) is returned, not saved.
    """
    job = _agent_job(request, job_id)
    agent = (job.result_data or {}).get("agent") or {}
    stored = agent.get("trajectory") if isinstance(agent.get("trajectory"), dict) else None
    notes: list[str] = []
    base = {
        "job_id": str(job.id),
        "harness": agent.get("harness"),
        "parser_version": traj.PARSER_VERSION,
        "rules": traj.rule_table(),
        "trajectory_error": agent.get("trajectory_error"),
    }
    try:
        files = traj.load_run_files(job)
    except traj.RunRecordUnavailable as exc:
        steps = traj.steps_from_artifacts(_agent_artifacts(job))
        if steps:
            notes.append(
                f"Run record unavailable ({exc}): steps rebuilt from the compact per-call artifacts, "
                "so tool outcomes are unknown.",
            )
        else:
            notes.append(f"Run record unavailable ({exc}) and no per-call artifacts are stored.")
        return {
            **base,
            "record": stored,
            "record_source": "stored" if stored else None,
            "steps": steps[:limit],
            "truncated": len(steps) > limit,
            "total_steps": len(steps),
            "source": "artifacts",
            "notes": notes,
        }
    parsed = traj.parse_run(files.calls, harness_events=files.harness_events, mini_traj=files.mini_traj)
    if stored and stored.get("parser_version") == traj.PARSER_VERSION:
        record, record_source = stored, "stored"
    else:
        record, record_source = traj.record_from_disk(job, files, parsed), "computed"
        notes.append(
            "No trajectory record is stored for this run yet (or it was built by an older parser): "
            "this one was computed on the fly and not saved; run backfill_agent_trajectories to store it.",
        )
    steps, truncated = traj.step_rows(parsed, limit=limit)
    if truncated:
        notes.append(f"Showing the first {limit} of {len(parsed.calls)} calls.")
    return {
        **base,
        "record": record,
        "record_source": record_source,
        "steps": steps,
        "truncated": truncated,
        "total_steps": len(parsed.calls),
        "source": "llm_calls",
        "notes": notes,
    }


@router.get("/jobs/{job_id}/trajectory/steps/{seq}/", response=dict)
def job_trajectory_step(request, job_id: str, seq: int):
    """One call in full: the inputs since the previous turn, the response, each tool call and its result.

    Texts are truncated at ~20k characters each.
    """
    job = _agent_job(request, job_id)
    try:
        raw = traj.calls_raw_from_disk(job)
        events = traj.harness_events_from_disk(job)
    except traj.RunRecordUnavailable as exc:
        art = _agent_artifacts(job).filter(stage=f"agent_call_{seq:04d}").first()
        if art is None:
            raise HttpError(404, f"No such step ({exc})") from exc
        return {
            **traj.step_detail_from_artifact(art),
            "source": "artifact",
            "notes": [f"Run record unavailable ({exc}): texts are the artifact's truncated copies."],
        }
    detail = traj.step_detail(raw, seq, harness_events=events)
    if detail is None:
        raise HttpError(404, "No such step")
    return {**detail, "source": "llm_calls", "notes": []}


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


@router.get("/models/{model_id}/behaviour/", response=dict)
def model_behaviour(request, model_id: int):
    """Per-harness descriptive aggregates of ``metrics.behaviour`` over the caller's completed agent runs.

    Descriptive only (n, mean, median, min, max; shares for yes/no fields):
    no tests. Samples whose validity verdict excludes them are left out and
    counted; runs without a stored record are counted too.
    """
    model = get_object_or_404(LLMModel.objects.visible_to(request.auth), pk=model_id)
    rows = (
        GenerationJob.objects.filter(
            created_by=request.auth,
            model=model,
            mode=GenerationJob.Mode.AGENT,
            status=GenerationJob.Status.COMPLETED,
        )
        .values(
            "id",
            "result_data__agent__harness",
            "result_data__agent__harness_version",
            "result_data__agent__validity__valid",
            "result_data__agent__budget__max_llm_requests",
            "metrics__behaviour",
            "metrics__functional",
        )
        .order_by("created_at")
    )
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["result_data__agent__harness"] or "unknown"].append(row)

    harnesses = []
    for harness, members in sorted(groups.items()):
        valid = [m for m in members if m["result_data__agent__validity__valid"] is not False]
        with_record = [m for m in valid if isinstance(m["metrics__behaviour"], dict)]
        behaviours = [m["metrics__behaviour"] for m in with_record]
        metrics = {
            key: _describe([v for b in behaviours if (v := _number(b.get(key))) is not None])
            for key in NUMERIC_BEHAVIOUR_KEYS
        }
        rates = {}
        for key in BOOLEAN_BEHAVIOUR_KEYS:
            answered = [b.get(key) for b in behaviours if isinstance(b.get(key), bool)]
            yes = sum(1 for v in answered if v)
            rates[key] = {"n": len(answered), "k": yes, "share": round(yes / len(answered), 4) if answered else None}
        # Conformance suite only: an older run's metrics.functional can be the
        # generation-time smoke check, and ungraded outcomes are not a measured 0.
        pass_rates = [v for m in valid if (v := conformance_pass_rate(m["metrics__functional"])) is not None]
        budgets = sorted(
            {b for m in with_record if (b := _number(m["result_data__agent__budget__max_llm_requests"])) is not None},
        )
        harnesses.append(
            {
                "harness": harness,
                "harness_versions": sorted({str(m["result_data__agent__harness_version"] or "?") for m in members}),
                "max_llm_requests": [int(b) for b in budgets],
                "n_jobs": len(members),
                "n_invalid_excluded": len(members) - len(valid),
                "n_without_record": len(valid) - len(with_record),
                "n": len(with_record),
                "comparable": bool(behaviours) and all(b.get("comparable") for b in behaviours),
                "parser_versions": sorted({str(b.get("parser_version")) for b in behaviours}),
                "metrics": metrics,
                "rates": rates,
                "pass_rate": _describe(pass_rates),
            },
        )
    notes = [
        "Descriptive only: no significance tests. Each harness is summarised on its own; categories and "
        "action counts are heuristics (rule table v" + traj.PARSER_VERSION + ") and are compared within a harness.",
        "Pooled over every app spec this model ran, so differences can reflect the task mix.",
    ]
    if any(not h["comparable"] and h["n"] for h in harnesses):
        notes.append("Harnesses without tool calls (Aider) have no action metrics; only time/token fields apply.")
    if any(len(h["parser_versions"]) > 1 for h in harnesses):
        notes.append("Records come from more than one parser version; backfill them to compare like with like.")
    mixed = [h for h in harnesses if len(h["max_llm_requests"]) > 1]
    if mixed:
        notes.append(
            "Runs with different LLM-request budgets are pooled ("
            + "; ".join(f"{h['harness']}: {', '.join(map(str, h['max_llm_requests']))}" for h in mixed)
            + "): action counts and times scale with the budget, so read the spread with that in mind.",
        )
    notes.append("Pass rate: conformance-suite runs only (generation-time smoke checks and ungraded runs left out).")
    return {
        "model_id": model.pk,
        "parser_version": traj.PARSER_VERSION,
        "harnesses": harnesses,
        "notes": notes,
    }
