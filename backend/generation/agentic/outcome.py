"""Outcome and termination classification through ``contracts.outcome.TERMINATION_OUTCOME`` (spec section 6.12).

The generation stage decides the left half of the Termination x outcome table: whether the attempt goes on
to evaluation, is retried as infrastructure, or ends here. Evaluation decides the final outcome of the rows
marked ``EVALUATION_DECIDES`` (``output_invalid`` / ``build_fail`` / ``start_fail`` / ``tested``), except
that the collector's empty-output rule (section 6.8) already gives ``output_invalid`` at generation time:
a skeleton-only workspace is never ``tested`` and never evaluated.

Everything here is pure: it maps facts to decisions and is shared by the slot agent (section 7.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from typing import Final

from backend.generation.contracts.outcome import EVALUATION_DECIDES
from backend.generation.contracts.outcome import TERMINATION_OUTCOME
from backend.generation.contracts.outcome import TRUNCATING_TERMINATIONS
from backend.generation.contracts.outcome import USABLE_OUTCOMES
from backend.generation.contracts.outcome import ExclusionReason
from backend.generation.contracts.outcome import Outcome
from backend.generation.contracts.outcome import Termination

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunAttempt

__all__ = [
    "MODEL_CAUSED_OUTCOMES",
    "REASON_EDIT_VIOLATION",
    "REASON_HARNESS_CRASH",
    "REASON_HARNESS_NOT_ENABLED",
    "REASON_IMAGE_DIGEST_MISMATCH",
    "REASON_NO_MODEL_CODE",
    "REASON_WORKSPACE_TOO_LARGE",
    "Decision",
    "GenerationDecision",
    "classify",
    "decide",
    "is_model_caused",
    "is_retryable_outcome",
]

# outcome_reason values written by the generation stage (sections 6.3, 6.8, 7.6).
REASON_NO_MODEL_CODE: Final = "no_model_code"
REASON_WORKSPACE_TOO_LARGE: Final = "workspace_too_large"
REASON_EDIT_VIOLATION: Final = "edit_violation"
REASON_IMAGE_DIGEST_MISMATCH: Final = "image_digest_mismatch"
REASON_HARNESS_CRASH: Final = "harness_crash"
REASON_HARNESS_NOT_ENABLED: Final = ExclusionReason.HARNESS_NOT_ENABLED.value

# Outcomes caused by the model (section 6.14): a retry or top-up of such a run is refused.
MODEL_CAUSED_OUTCOMES: Final = frozenset(USABLE_OUTCOMES)


class Decision(StrEnum):
    """What the generation stage does with an attempt."""

    EVALUATE = "evaluate"  # hand the (possibly partial) tree to the eval lane
    DONE = "done"  # terminal now: output_invalid or excluded
    RETRY = "retry"  # infrastructure: requeue as attempt+1 (or end infra_error when retries run out)
    CANCELLED = "cancelled"  # the user cancelled the run


@dataclass(frozen=True)
class GenerationDecision:
    """The generation stage's verdict on one attempt.

    ``outcome``/``reason`` are set when the stage itself decides the final outcome (``DONE``, ``CANCELLED``)
    and, for ``RETRY``, give the outcome the run ends with once retries are exhausted. ``truncated`` and
    ``note`` come from the table row; ``note`` is a validity note code (the parametric base
    ``budget_exhausted`` is completed with its kind by ``agentic.validity``).
    """

    termination: Termination
    decision: Decision
    outcome: Outcome | None = None
    reason: str = ""
    truncated: bool = False
    note: str | None = None
    retry_reason: str = ""

    @property
    def usable(self) -> bool | None:
        """``usable`` of the decided outcome; ``None`` while evaluation still decides it."""
        if self.outcome is None:
            return None
        return self.outcome in USABLE_OUTCOMES


def decide(
    termination: Termination | str,
    *,
    model_code: bool,
    workspace_truncated: bool = False,
    prior_crash: bool = False,
    invalid_reason: str | None = None,
) -> GenerationDecision:
    """Apply the Termination x outcome table (section 6.12) and the empty-output rule (section 6.8).

    * ``model_code``: the collector found at least one authored/modified, non-excluded file with ``loc > 0``.
    * ``workspace_truncated``: the workspace tar exceeded ``run.max_workspace_bytes``.
    * ``prior_crash``: an earlier attempt of this run already ended ``HARNESS_CRASH``; the crash row is
      retried once, and a repeat proceeds to evaluation with the note ``harness_crash``.
    * ``invalid_reason``: a slot-agent collector's verdict (``edit_violation`` under ``edit_policy=fail``).
    """
    termination = Termination(termination)
    rule = TERMINATION_OUTCOME[termination]
    truncated = termination in TRUNCATING_TERMINATIONS
    if termination == Termination.CANCELLED:
        return GenerationDecision(termination, Decision.CANCELLED, Outcome.EXCLUDED, ExclusionReason.CANCELLED.value)
    if rule.generation == "excluded":
        return GenerationDecision(termination, Decision.DONE, Outcome.EXCLUDED, rule.reason or "", note=rule.note)
    if rule.generation == "infra_error":
        return GenerationDecision(
            termination,
            Decision.RETRY,
            Outcome.INFRA_ERROR,
            rule.reason or "",
            note=rule.note,
            retry_reason=rule.reason or "infra",
        )
    if rule.generation == "retry_once_then_evaluate" and not prior_crash:
        return GenerationDecision(
            termination,
            Decision.RETRY,
            None,
            REASON_HARNESS_CRASH,
            note=rule.note,
            retry_reason=REASON_HARNESS_CRASH,
        )
    if rule.final_outcome != EVALUATION_DECIDES:  # pragma: no cover - guarded by the table test
        msg = f"termination {termination} has no evaluated row"
        raise AssertionError(msg)
    if workspace_truncated:
        reason = REASON_WORKSPACE_TOO_LARGE
    elif invalid_reason:
        reason = invalid_reason
    elif not model_code:
        reason = REASON_NO_MODEL_CODE
    else:
        return GenerationDecision(termination, Decision.EVALUATE, truncated=truncated, note=rule.note)
    return GenerationDecision(
        termination,
        Decision.DONE,
        Outcome.OUTPUT_INVALID,
        reason,
        truncated=truncated,
        note=rule.note,
    )


def classify(run: GenerationRun, attempt: RunAttempt) -> tuple[str, str]:
    """``(outcome, outcome_reason)`` of a finished attempt as recorded.

    The attempt row keeps its own outcome; the reason of the run's current attempt is the run's. An attempt
    that went to evaluation and has no outcome yet gives ``("", "")``.
    """
    if attempt.outcome:
        current = int(run.attempt or 0) + 1 == attempt.attempt and run.outcome == attempt.outcome
        return attempt.outcome, (run.outcome_reason if current else attempt.infra_error) or ""
    if not attempt.termination:
        return "", ""
    rule = TERMINATION_OUTCOME[Termination(attempt.termination)]
    if rule.final_outcome == EVALUATION_DECIDES:
        if int(run.attempt or 0) + 1 == attempt.attempt and run.outcome:
            return run.outcome, run.outcome_reason
        return "", ""
    return str(rule.final_outcome), rule.reason or ""


def is_model_caused(outcome: str | None) -> bool:
    """``True`` for model-attributable outcomes (``tested``, ``build_fail``, ``start_fail``, ``output_invalid``)."""
    return bool(outcome) and outcome in MODEL_CAUSED_OUTCOMES


def is_retryable_outcome(outcome: str | None) -> bool:
    """Only ``infra_error`` runs may be retried (section 6.14, ``POST /runs/{id}/retry/``)."""
    return outcome == Outcome.INFRA_ERROR
