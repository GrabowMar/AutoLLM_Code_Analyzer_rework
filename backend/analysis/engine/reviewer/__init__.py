"""AI reviewer with a switchable engine/harness and model (owner: reviewer stages).

Engines: ``single_shot`` (one structured OpenRouter call per chunk, no
harness) and ``agent`` (a coding-agent harness — opencode, mini-swe-agent,
aider — exploring a read-only copy of the app in a sandbox behind the
llm-gateway, writing a findings file). Both return the same
:class:`SampleOutcome`; findings are schema-validated and grounded against
the sample, k replicates are clustered into consensus findings.
An invalid, truncated or empty review is a failed sample, never 0 findings.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.analysis.engine.types import NormalizedFinding
    from backend.analysis.engine.types import Sample

logger = logging.getLogger(__name__)

# Sample failure reasons that will recur on every replicate (credentials,
# credits, an unknown model): stop instead of paying for k identical failures.
FATAL_SAMPLE_REASONS = frozenset({"auth", "insufficient_credits", "model_unavailable", "invalid_config"})
# ReviewSample.failure_reason -> ToolResult.FailureReason, and the order in
# which a review-level reason is picked when replicates failed differently.
_TOOL_REASONS = {
    "invalid_output": "invalid_output",
    "truncated": "invalid_output",
    "empty_output": "invalid_output",
    "refusal": "invalid_output",
    "content_filter": "invalid_output",
    # Agent engine: the agent's own document says it did not review the files under review.
    "incomplete_review": "invalid_output",
    "budget": "budget",
    "timeout": "timeout",
    "cancelled": "cancelled",
    "auth": "unavailable",
    "insufficient_credits": "unavailable",
    "model_unavailable": "unavailable",
    "invalid_config": "invalid_config",
    # Agent sandboxes: the infrastructure (daemon, gateway) or the memory
    # limit failed, not the model's review.
    "infra_error": "infra_error",
    "oom": "oom",
}
_REASON_PRIORITY = (
    "unavailable",
    "invalid_config",
    "invalid_output",
    "budget",
    "timeout",
    "oom",
    "infra_error",
    "tool_error",
)
_USAGE_SUM_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "total_tokens",
    "cost_usd",
    "catalog_cost_usd",
    "llm_calls",
    "llm_calls_failed",
    "http_attempts",
    "retries",
)


@dataclass
class SampleOutcome:
    """One review replicate (persisted as models.ReviewSample + its findings)."""

    index: int
    engine: str
    harness: str = ""
    harness_version: str = ""
    model_slug: str = ""
    model_canonical_slug: str = ""
    status: str = "failed"  # models.ReviewSample.Status value
    failure_reason: str = ""
    error_message: str = ""
    prompt_version: str = ""
    prompt_sha256: str = ""
    schema_sha256: str = ""
    file_order_seed: int | None = None
    # Paths the model says it reviewed (its claim; what was sent is in coverage).
    files_reviewed: list[str] = field(default_factory=list)
    # Keys files_in_scope, files_sent, chars_sent, chunks, files_not_sent [{path, reason}], files_split, ...
    coverage: dict[str, Any] = field(default_factory=dict)
    # Keys prompt_tokens, completion_tokens, reasoning_tokens, cached_tokens, cost_usd, llm_calls,
    # llm_calls_failed (None = not reported, never 0); cost_complete False = cost_usd is a lower bound.
    usage: dict[str, Any] = field(default_factory=dict)
    providers: dict[str, int] = field(default_factory=dict)
    finish_reasons: dict[str, int] = field(default_factory=dict)
    # Finding counts per grounding status: exact, relocated, ungrounded, invalid_file, out_of_range.
    grounding: dict[str, int] = field(default_factory=dict)
    repaired: bool = False
    latency_seconds: float | None = None
    findings: list[NormalizedFinding] = field(default_factory=list)  # grounded + ungrounded, sample_index set
    artifacts: dict[str, bytes] = field(default_factory=dict)  # name -> raw bytes (responses, transcript)

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass
class ReviewOutcome:
    status: str  # "succeeded" | "failed" | "not_applicable" | "cancelled" (ToolResult.Status values)
    failure_reason: str = ""  # models.ToolResult.FailureReason value
    message: str = ""
    samples: list[SampleOutcome] = field(default_factory=list)
    # All findings of all samples, with is_consensus / counted set by consensus.
    findings: list[NormalizedFinding] = field(default_factory=list)
    # {samples, successful_samples, planned_samples, quorum, threshold, threshold_rule, replicates_complete,
    #  mean_pairwise_jaccard, clusters, consensus, counted, grounding, grounding_rate, ...}
    agreement: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)  # totals over samples (+ cost_complete)


def run_review(
    *,
    frozen: dict[str, Any],
    sample: Sample,
    api_key: str,
    run_id: str,
    is_cancelled: Callable[[], bool] = lambda: False,
    heartbeat: Callable[[], None] = lambda: None,
    scope: dict[str, Any] | None = None,
) -> ReviewOutcome:
    """Run ``frozen["samples"]`` replicates with the frozen engine and build consensus.

    Quorum and consensus threshold are the same rule: a strict majority of
    the k *planned* samples (``k // 2 + 1``: 2 of 3, 3 of 4). The review
    succeeds when that many samples succeeded, and a finding is consensus
    when that many samples reported it, so a failed sample is a vote for
    nothing: a review with a failed replicate can count fewer issues than the
    full review would have, never more, and every run of a plan applies the
    same rule (``agreement["replicates_complete"]`` marks the m < k runs).

    *frozen* is the plan's ``reviewer`` section; *scope* the plan's scope
    (passed to normalization as ``frozen["scope"]``) with the plan's
    ``count_style`` / ``count_metric_findings`` outcome flags folded in:
    consensus counts a finding only when its category is counted under
    them, as for static findings (absent flags: style, complexity and
    duplication are not counted). Status is a
    ToolResult.Status value: ``not_applicable`` when nothing reviewable is
    in scope, ``cancelled`` on cancellation, ``failed`` (with a
    ToolResult.FailureReason) when the quorum was missed or the frozen
    instrument no longer matches the code on disk. Replicates stop early
    once the quorum is unreachable or a failure would recur (auth, credits,
    unknown model). Findings of failed replicates are dropped (their raw
    responses stay in the sample artifacts); usage sums every replicate.
    """
    from backend.analysis.engine.reviewer import consensus

    started = time.monotonic()
    problem = _check_frozen(frozen)
    if problem:
        return ReviewOutcome(status="failed", failure_reason="invalid_config", message=problem)
    if scope is not None:
        frozen = {**frozen, "scope": scope}
    k = int(frozen["samples"])
    quorum = consensus.majority(k)
    if not api_key:
        return ReviewOutcome(status="failed", failure_reason="unavailable", message="no OpenRouter API key")
    if not _applicable(frozen, sample):
        return ReviewOutcome(
            status="not_applicable",
            message="no reviewable files in the reviewer's scope (origins/kinds)",
            agreement={"samples": k, "quorum": quorum, "successful_samples": 0},
        )

    samples: list[SampleOutcome] = []
    stopped = ""
    cancelled = False
    for index in range(k):
        if is_cancelled():
            cancelled = True
            break
        heartbeat()
        outcome = _run_sample(frozen, sample, index, api_key, run_id, is_cancelled, heartbeat)
        if not outcome.ok and outcome.findings:
            outcome.coverage = {**outcome.coverage, "discarded_findings": len(outcome.findings)}
            outcome.findings = []
        samples.append(outcome)
        if outcome.status == "cancelled":
            cancelled = True
            break
        if outcome.failure_reason in FATAL_SAMPLE_REASONS:
            stopped = outcome.failure_reason
            break
        failed = sum(not s.ok for s in samples)
        if k - failed < quorum and index < k - 1:
            stopped = "quorum_unreachable"
            break

    ok = [s for s in samples if s.ok]
    m = len(ok)
    usage = _total_usage(samples, time.monotonic() - started)
    agreement: dict[str, Any] = {
        "samples": k,
        "samples_run": len(samples),
        "successful_samples": m,
        "quorum": quorum,
        "stopped_early": stopped,
        "statuses": dict(Counter(s.status for s in samples)),
        **_quality(ok),
    }
    if cancelled:
        return ReviewOutcome(
            status="cancelled",
            failure_reason="cancelled",
            message="review cancelled",
            samples=samples,
            findings=[f for s in ok for f in s.findings],
            agreement=agreement,
            usage=usage,
        )
    if m < quorum:
        reason = _failure_reason(samples)
        detail = ", ".join(
            f"#{s.index} {s.status}{f' ({s.failure_reason})' if s.failure_reason else ''}" for s in samples
        )
        first_error = next((s.error_message for s in samples if s.error_message), "")
        message = f"{m} of {k} review samples succeeded (quorum {quorum}): {detail}"
        if first_error:
            message = f"{message}. First error: {first_error}"
        return ReviewOutcome(
            status="failed",
            failure_reason=reason,
            message=message[:4000],
            samples=samples,
            findings=[f for s in ok for f in s.findings],
            agreement=agreement,
            usage=usage,
        )

    stats = consensus.apply_consensus(
        [s.findings for s in ok],
        successful=m,
        planned=k,
        line_window=int((frozen.get("consensus") or {}).get("line_window") or 3),
        uncounted_categories=_uncounted_categories(frozen.get("scope")),
    )
    agreement.update(stats)
    return ReviewOutcome(
        status="succeeded",
        message=f"{m} of {k} review samples succeeded; {stats['counted']} counted consensus finding(s)",
        samples=samples,
        findings=[f for s in ok for f in s.findings],
        agreement=agreement,
        usage=usage,
    )


def _check_frozen(frozen: dict[str, Any]) -> str:  # noqa: PLR0911 - one early return per check
    """Why a frozen reviewer section cannot run (empty = it can)."""
    from backend.analysis.engine.reviewer import prompt
    from backend.analysis.engine.reviewer.config import ENGINES
    from backend.analysis.engine.reviewer.config import REVIEWER_ENGINE_VERSION

    if not isinstance(frozen, dict) or not frozen.get("enabled", True):
        return "the reviewer is not enabled in the frozen plan"
    if frozen.get("engine") not in ENGINES:
        return f"unknown review engine {frozen.get('engine')!r}"
    # Grounding, consensus and sample-acceptance rules are part of the
    # instrument: a plan frozen under other rules would mix two instruments
    # under one plan_hash.
    version = frozen.get("engine_version")
    if version is not None and version != REVIEWER_ENGINE_VERSION:
        return (
            f"reviewer engine {version} changed to {REVIEWER_ENGINE_VERSION} since the plan was frozen "
            "(grounding/consensus rules); re-freeze the plan to run it"
        )
    samples = frozen.get("samples")
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        return f"invalid replicate count {samples!r}"
    if not (frozen.get("model") or {}).get("slug"):
        return "frozen reviewer has no model"
    # The instrument must be the one the plan froze: a prompt or schema
    # edited after freezing would silently change what is measured.
    try:
        if frozen.get("prompt_sha256") != prompt.prompt_sha256(frozen.get("prompt_version") or ""):
            return f"review prompt {frozen.get('prompt_version')} changed since the plan was frozen"
        if frozen.get("schema_sha256") != prompt.schema_sha256(frozen.get("schema_version") or ""):
            return f"findings schema {frozen.get('schema_version')} changed since the plan was frozen"
    except prompt.UnknownVersionError as exc:
        return str(exc)
    return ""


def _uncounted_categories(scope: dict[str, Any] | None) -> frozenset[str]:
    """Categories the plan does not count (style unless ``count_style``, complexity/duplication unless
    ``count_metric_findings``), read from the scope the executor folds the outcome flags into: the same
    rule ``normalize`` applies to static findings."""
    from backend.analysis.engine import normalize

    return frozenset(normalize.uncounted_categories(scope if isinstance(scope, dict) else {}))


def _applicable(frozen: dict[str, Any], sample: Sample) -> bool:
    """Whether anything is reviewable: one rule for both engines (scope selection minus binary/blank files)."""
    from backend.analysis.engine.reviewer import single_shot

    return single_shot.reviewable(frozen, sample)


def _run_sample(
    frozen: dict[str, Any],
    sample: Sample,
    index: int,
    api_key: str,
    run_id: str,
    is_cancelled: Callable[[], bool],
    heartbeat: Callable[[], None],
) -> SampleOutcome:
    """One replicate with the frozen engine; an engine crash is a failed replicate, not a crashed run."""
    engine = frozen["engine"]
    try:
        if engine == "agent":
            from backend.analysis.engine.reviewer import agentic

            return agentic.run_sample(
                frozen=frozen,
                sample=sample,
                index=index,
                api_key=api_key,
                run_id=run_id,
                is_cancelled=is_cancelled,
                heartbeat=heartbeat,
            )
        from backend.analysis.engine.reviewer import single_shot

        return single_shot.run_sample(
            frozen=frozen,
            sample=sample,
            index=index,
            api_key=api_key,
            is_cancelled=is_cancelled,
            heartbeat=heartbeat,
        )
    except Exception as exc:
        logger.exception("AI review sample %d (%s) crashed for run %s", index, engine, run_id)
        model = frozen.get("model") or {}
        return SampleOutcome(
            index=index,
            engine=engine,
            harness=frozen.get("harness") or "",
            harness_version=frozen.get("harness_version") or "",
            model_slug=model.get("slug", ""),
            model_canonical_slug=model.get("canonical_slug", ""),
            status="failed",
            failure_reason="engine_error",
            error_message=f"{type(exc).__name__}: {exc}"[:2000],
            prompt_version=frozen.get("prompt_version", ""),
            prompt_sha256=frozen.get("prompt_sha256", ""),
            schema_sha256=frozen.get("schema_sha256", ""),
        )


def _failure_reason(samples: list[SampleOutcome]) -> str:
    """The ToolResult.FailureReason that best describes why the quorum was missed."""
    reasons = Counter(_TOOL_REASONS.get(s.failure_reason, "tool_error") for s in samples if not s.ok)
    if not reasons:
        return "tool_error"
    for fatal in ("unavailable", "invalid_config"):
        if reasons.get(fatal):
            return fatal

    def rank(reason: str) -> tuple[int, int, str]:
        # Most frequent first; ties by priority (reasons outside it last).
        priority = _REASON_PRIORITY.index(reason) if reason in _REASON_PRIORITY else len(_REASON_PRIORITY)
        return (-reasons[reason], priority, reason)

    return min(reasons, key=rank)


def _total_usage(samples: list[SampleOutcome], wall_seconds: float) -> dict[str, Any]:
    """Usage summed over every replicate (failed ones were billed too); None stays None.

    Spend follows the per-sample convention: ``cost_usd`` is None when no
    replicate's spend is known, else the sum of the known ones, which is a
    lower bound flagged ``cost_complete=False`` whenever a replicate's spend
    is unknown (``cost_usd`` None: a crashed replicate, a lost gateway log)
    or itself incomplete (calls without usage, abandoned in-flight calls).
    """
    totals: dict[str, Any] = dict.fromkeys(_USAGE_SUM_KEYS)
    served: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    estimated = False
    incomplete = 0
    for s in samples:
        usage = s.usage or {}
        for key in _USAGE_SUM_KEYS:
            value = usage.get(key)
            if value is not None:
                totals[key] = (totals[key] or 0) + value
        if usage.get("cost_usd") is None or usage.get("cost_complete") is False or usage.get("gateway_log_missing"):
            incomplete += 1
        served.update(usage.get("served_models") or {})
        providers.update(s.providers or {})
        estimated = estimated or bool(usage.get("cost_estimated"))
    for key in ("cost_usd", "catalog_cost_usd"):
        if totals[key] is not None:
            totals[key] = round(totals[key], 8)
    return {
        **totals,
        "cost_estimated": estimated,
        "cost_complete": incomplete == 0,
        "cost_incomplete_samples": incomplete,
        "samples": len(samples),
        "served_models": dict(served),
        "providers": dict(providers),
        "wall_seconds": round(wall_seconds, 3),
    }


def _quality(ok: list[SampleOutcome]) -> dict[str, Any]:
    """Grounding totals and rate over the successful replicates."""
    from backend.analysis.engine.reviewer.grounding import GROUNDED
    from backend.analysis.engine.reviewer.grounding import empty_counts

    counts = empty_counts()
    for s in ok:
        for key, value in (s.grounding or {}).items():
            counts[key] = counts.get(key, 0) + int(value or 0)
    total = sum(counts.values())
    grounded = sum(counts[k] for k in GROUNDED)
    return {
        "grounding": counts,
        "grounding_rate": round(grounded / total, 6) if total else None,
        "repaired_samples": sum(1 for s in ok if s.repaired),
    }
