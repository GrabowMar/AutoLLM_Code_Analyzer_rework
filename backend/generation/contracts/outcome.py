"""Run outcome taxonomy, terminations and validity (spec sections 5.1, 6.12, 6.13).

``TERMINATION_OUTCOME`` is the single table that maps a harness ``Termination``
to what the generation stage does next and to the final outcome. The
generation stage decides the left part; evaluation decides the final outcome
of the rows marked ``EVALUATION_DECIDES``.

``usable = outcome in USABLE_OUTCOMES``: those outcomes are model-attributable
and analysed as data (intention to treat). ``infra_error`` and ``excluded``
are never usable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from typing import Final
from typing import Literal

from pydantic import Field
from pydantic import field_validator

from backend.generation.contracts._base import ContractModel
from backend.generation.contracts._base import NonNegFloat
from backend.generation.contracts._base import NonNegInt
from backend.generation.contracts._base import Sha256

__all__ = [
    "EVALUATION_DECIDES",
    "NOTE_CODES",
    "PARAMETRIC_NOTE_CODES",
    "TERMINATION_OUTCOME",
    "TRUNCATING_TERMINATIONS",
    "USABLE_OUTCOMES",
    "AttemptServed",
    "AttemptUsage",
    "CollectionSummary",
    "EvalSummary",
    "ExclusionReason",
    "GenerationStep",
    "Note",
    "Outcome",
    "RunMetrics",
    "RunResult",
    "Termination",
    "TerminationRule",
    "TimeSplit",
    "Validity",
    "is_usable",
    "note_code_valid",
]


class Outcome(StrEnum):
    OUTPUT_INVALID = "output_invalid"
    BUILD_FAIL = "build_fail"
    START_FAIL = "start_fail"
    TESTED = "tested"
    INFRA_ERROR = "infra_error"
    EXCLUDED = "excluded"


class Termination(StrEnum):
    COMPLETED = "completed"
    WALL_CLOCK = "wall_clock"
    IDLE = "idle_timeout"
    BUDGET = "budget_exhausted"
    HARNESS_ERROR = "harness_error"
    HARNESS_CRASH = "harness_crash"
    LLM_ERROR = "llm_error"
    INFRA = "infra_interrupted"
    CANCELLED = "cancelled"
    MAX_TURNS = "max_turns"
    OOM = "oom_killed"


class ExclusionReason(StrEnum):
    """Reasons for outcome ``excluded`` (counted per condition, never hidden)."""

    CELL_BUDGET = "cell_budget"
    BUDGET_STOP = "budget_stop"
    CANCELLED = "cancelled"
    STOPPED_BY_RULE = "stopped_by_rule"
    HARNESS_NOT_ENABLED = "harness_not_enabled"
    CONFIG_INVALID = "config_invalid"
    RESOURCE_LIMIT = "resource_limit"


USABLE_OUTCOMES: Final = frozenset(
    {Outcome.TESTED, Outcome.BUILD_FAIL, Outcome.START_FAIL, Outcome.OUTPUT_INVALID},
)
TRUNCATING_TERMINATIONS: Final = frozenset(
    {Termination.BUDGET, Termination.WALL_CLOCK, Termination.IDLE, Termination.MAX_TURNS},
)


def is_usable(outcome: str | Outcome | None) -> bool | None:
    """``True`` for model-attributable outcomes, ``False`` for infra/excluded, ``None`` while unknown."""
    if not outcome:
        return None
    return Outcome(outcome) in USABLE_OUTCOMES


GenerationStep = Literal["evaluate", "evaluate_partial", "retry_once_then_evaluate", "infra_error", "excluded"]

# Sentinel for rows whose final outcome is decided by evaluation
# (output_invalid / build_fail / start_fail / tested).
EVALUATION_DECIDES: Final = "evaluation"


@dataclass(frozen=True)
class TerminationRule:
    """One row of the Termination x outcome table (section 6.12)."""

    termination: Termination
    generation: GenerationStep
    final_outcome: Outcome | Literal["evaluation"]
    usable: bool
    truncated: bool | None  # None = not applicable (the run is not usable)
    retryable: bool
    reason: str | None = None  # outcome_reason for terminal rows
    # Validity note added when the row applies: a NOTE_CODES entry, or the base of a
    # parametric code that the runner completes ("budget_exhausted" -> "budget_exhausted:cost").
    note: str | None = None


def _rule(*args: Any, **kwargs: Any) -> TerminationRule:
    return TerminationRule(*args, **kwargs)


TERMINATION_OUTCOME: Final[Mapping[Termination, TerminationRule]] = {
    rule.termination: rule
    for rule in (
        _rule(Termination.COMPLETED, "evaluate", EVALUATION_DECIDES, usable=True, truncated=False, retryable=False),
        _rule(
            Termination.MAX_TURNS,
            "evaluate_partial",
            EVALUATION_DECIDES,
            usable=True,
            truncated=True,
            retryable=False,
            note="max_turns",
        ),
        _rule(
            Termination.BUDGET,
            "evaluate_partial",
            EVALUATION_DECIDES,
            usable=True,
            truncated=True,
            retryable=False,
            note="budget_exhausted",
        ),
        _rule(
            Termination.WALL_CLOCK,
            "evaluate_partial",
            EVALUATION_DECIDES,
            usable=True,
            truncated=True,
            retryable=False,
            note="wall_clock",
        ),
        _rule(
            Termination.IDLE,
            "evaluate_partial",
            EVALUATION_DECIDES,
            usable=True,
            truncated=True,
            retryable=False,
            note="idle_timeout",
        ),
        _rule(
            Termination.HARNESS_ERROR,
            "evaluate",
            EVALUATION_DECIDES,
            usable=True,
            truncated=False,
            retryable=False,
            note="harness_error",
        ),
        _rule(
            Termination.HARNESS_CRASH,
            "retry_once_then_evaluate",
            EVALUATION_DECIDES,
            usable=True,
            truncated=False,
            retryable=True,
            note="harness_crash",
        ),
        _rule(
            Termination.LLM_ERROR,
            "infra_error",
            Outcome.INFRA_ERROR,
            usable=False,
            truncated=None,
            retryable=True,
            reason="llm_error",
            note="ended_on_llm_error",
        ),
        _rule(
            Termination.INFRA,
            "infra_error",
            Outcome.INFRA_ERROR,
            usable=False,
            truncated=None,
            retryable=True,
            reason="infra_interrupted",
        ),
        _rule(
            Termination.OOM,
            "excluded",
            Outcome.EXCLUDED,
            usable=False,
            truncated=None,
            retryable=False,
            reason=ExclusionReason.RESOURCE_LIMIT.value,
        ),
        _rule(
            Termination.CANCELLED,
            "excluded",
            Outcome.EXCLUDED,
            usable=False,
            truncated=None,
            retryable=False,
            reason=ExclusionReason.CANCELLED.value,
        ),
    )
}


# ---------------------------------------------------------------------------
# Validity notes (section 6.13)
# ---------------------------------------------------------------------------

NOTE_CODES: Final = frozenset(
    {
        # truncation kinds
        "wall_clock",
        "idle_timeout",
        "max_turns",
        # call / harness behaviour
        "ended_on_llm_error",
        "harness_error",
        "harness_crash",
        "truncated",  # slot single-shot finish_reason=length (7.6)
        # served model / provider
        "served_model_differs",
        "provider_mixed",
        "response_model_differs",
        "routing_partial",  # appendix B
        # protocol and harness bundle
        "translation_lossy",
        "tool_set_varies",
        "vendor_affinity",
        "call_cap_without_compaction",
        # evaluation
        "skeleton_equal_baseline",
        "depcache_unavailable",
        "eval_reused",
        "batch_unsupported",
    },
)
# Parametric notes are written "<code>:<param>" (e.g. "budget_exhausted:cost", "call_truncations:3").
PARAMETRIC_NOTE_CODES: Final[Mapping[str, re.Pattern[str]]] = {
    "budget_exhausted": re.compile(r"^[a-z][a-z0-9_]*$"),
    "call_truncations": re.compile(r"^[1-9][0-9]*$"),
    "unsupported_knob": re.compile(r"^[a-z][a-z0-9_.]*$"),
    "lock_source": re.compile(r"^(model|platform)$"),
    "adherence": re.compile(r"^(compliant|partial|violated|not_measured)$"),
}


def note_code_valid(code: str) -> bool:
    if code in NOTE_CODES:
        return True
    base, sep, param = code.partition(":")
    pattern = PARAMETRIC_NOTE_CODES.get(base)
    return bool(sep) and pattern is not None and bool(pattern.match(param))


class Note(ContractModel):
    """A typed validity note ``{code, level}``."""

    code: str
    level: Literal["info", "warn", "error"] = "info"
    detail: str | None = None

    @field_validator("code")
    @classmethod
    def _known(cls, value: str) -> str:
        if not note_code_valid(value):
            msg = f"unregistered validity note {value!r} (add it to contracts/outcome.py via the CHANGES file)"
            raise ValueError(msg)
        return value


class Validity(ContractModel):
    """``GenerationRun.validity``: termination, truncation and typed notes."""

    termination: Termination | None = None
    truncated: bool = False
    notes: list[Note] = Field(default_factory=list)

    def has_note(self, code: str) -> bool:
        return any(note.code == code or note.code.startswith(f"{code}:") for note in self.notes)


# ---------------------------------------------------------------------------
# Per-attempt facts (RunAttempt JSON columns, section 3.7)
# ---------------------------------------------------------------------------


class AttemptServed(ContractModel):
    """``RunAttempt.served``: who actually served the calls."""

    providers: dict[str, NonNegInt] = Field(default_factory=dict)
    models: dict[str, NonNegInt] = Field(default_factory=dict)
    response_models: dict[str, NonNegInt] = Field(default_factory=dict)
    system_fingerprints: list[str] = Field(default_factory=list)
    routes: dict[str, NonNegInt] = Field(default_factory=dict)


class AttemptUsage(ContractModel):
    """``RunAttempt.usage``: totals over every call of one attempt."""

    requests_by_class: dict[Literal["main", "subagent", "compaction", "auxiliary"], NonNegInt] = Field(
        default_factory=dict,
    )
    prompt_tokens: NonNegInt = 0
    uncached_prompt_tokens: NonNegInt = 0
    completion_tokens: NonNegInt = 0
    reasoning_tokens: NonNegInt = 0
    cached_tokens: NonNegInt = 0
    cache_write_tokens: NonNegInt = 0
    cost_usd: NonNegFloat = 0.0
    list_cost_usd: NonNegFloat = 0.0
    call_truncations: NonNegInt = 0
    translation_dropped: dict[str, NonNegInt] = Field(default_factory=dict)
    tokens_per_s_p50: NonNegFloat | None = None


class TimeSplit(ContractModel):
    """``RunAttempt.time_split`` in seconds."""

    install_s: NonNegFloat = 0.0
    llm_s: NonNegFloat = 0.0
    tool_s: NonNegFloat = 0.0
    idle_s: NonNegFloat = 0.0


# ---------------------------------------------------------------------------
# GenerationRun.result / .metrics (small summaries only; no file contents)
# ---------------------------------------------------------------------------


class CollectionSummary(ContractModel):
    """Counts written by the collector (section 6.8)."""

    files: NonNegInt = 0
    by_origin: dict[Literal["starter", "authored", "modified", "deleted", "artifact", "slot"], NonNegInt] = Field(
        default_factory=dict,
    )
    excluded: dict[str, NonNegInt] = Field(default_factory=dict)
    loc_model: NonNegInt = 0
    workspace_bytes: NonNegInt = 0
    workspace_truncated: bool = False


class RunResult(ContractModel):
    """``GenerationRun.result``."""

    seed_tree_hash: Sha256 | None = None
    collection: CollectionSummary | None = None
    static: dict[str, Any] = Field(default_factory=dict)  # informational static checks; never gate the outcome
    termination: Termination | None = None
    budget_exhausted: str | None = None
    exit_code: int | None = None
    error: str | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)  # execution step timings {step, status, seconds}


class EvalSummary(ContractModel):
    """Mirror of the latest RunEvaluation in ``GenerationRun.metrics["eval"]``."""

    eval_suite: str
    eval_level: str
    status: Literal["pending", "running", "measured", "not_measured", "error"]
    measured: bool
    agent_pass_rate: float | None = None
    baseline_pass_rate: float | None = None
    over_baseline: float | None = None
    probes_tested: NonNegInt = 0
    probes_exploited: NonNegInt = 0
    sec_pass: bool | None = None
    slot_pass_rate: float | None = None
    adherence: Literal["compliant", "partial", "violated", "not_measured"] | None = None
    evaluation_id: int | None = None


class RunMetrics(ContractModel):
    """``GenerationRun.metrics``: the documented contract keys read in bulk by stats (``.values()``)."""

    cost_usd: NonNegFloat | None = None
    list_cost_usd: NonNegFloat | None = None
    prompt_tokens: NonNegInt | None = None
    uncached_prompt_tokens: NonNegInt | None = None
    completion_tokens: NonNegInt | None = None
    reasoning_tokens: NonNegInt | None = None
    cached_tokens: NonNegInt | None = None
    cache_write_tokens: NonNegInt | None = None
    total_tokens: NonNegInt | None = None
    requests_by_class: dict[str, NonNegInt] = Field(default_factory=dict)
    call_truncations: NonNegInt | None = None
    wall_s: NonNegFloat | None = None
    loc_model: NonNegInt | None = None
    loc_app: NonNegInt | None = None
    eval: EvalSummary | None = None
