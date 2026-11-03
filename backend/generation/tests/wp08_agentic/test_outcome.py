"""Outcome taxonomy (spec 6.12): every Termination row maps as in the table; the empty-output rule (6.8)."""

from __future__ import annotations

import pytest

from backend.generation.agentic import outcome
from backend.generation.agentic.outcome import Decision
from backend.generation.contracts.outcome import TERMINATION_OUTCOME
from backend.generation.contracts.outcome import Outcome
from backend.generation.contracts.outcome import Termination

# (termination, decision, outcome, reason, truncated) with model code present, first attempt.
TABLE = [
    (Termination.COMPLETED, Decision.EVALUATE, None, "", False),
    (Termination.MAX_TURNS, Decision.EVALUATE, None, "", True),
    (Termination.BUDGET, Decision.EVALUATE, None, "", True),
    (Termination.WALL_CLOCK, Decision.EVALUATE, None, "", True),
    (Termination.IDLE, Decision.EVALUATE, None, "", True),
    (Termination.HARNESS_ERROR, Decision.EVALUATE, None, "", False),
    (Termination.HARNESS_CRASH, Decision.RETRY, None, "harness_crash", False),
    (Termination.LLM_ERROR, Decision.RETRY, Outcome.INFRA_ERROR, "llm_error", False),
    (Termination.INFRA, Decision.RETRY, Outcome.INFRA_ERROR, "infra_interrupted", False),
    (Termination.OOM, Decision.DONE, Outcome.EXCLUDED, "resource_limit", False),
    (Termination.CANCELLED, Decision.CANCELLED, Outcome.EXCLUDED, "cancelled", False),
]


def test_every_termination_is_covered() -> None:
    assert {row[0] for row in TABLE} == set(Termination) == set(TERMINATION_OUTCOME)


@pytest.mark.parametrize(("termination", "decision", "final", "reason", "truncated"), TABLE)
def test_termination_maps_as_in_the_table(termination, decision, final, reason, truncated) -> None:
    verdict = outcome.decide(termination, model_code=True)
    assert verdict.decision == decision
    assert verdict.outcome == final
    assert verdict.reason == reason
    assert verdict.truncated is truncated
    if final is not None:
        assert verdict.usable is False  # infra_error and excluded are never usable


def test_oom_is_excluded_resource_limit_and_not_retried() -> None:
    verdict = outcome.decide(Termination.OOM, model_code=True)
    assert (verdict.decision, verdict.outcome, verdict.reason) == (Decision.DONE, Outcome.EXCLUDED, "resource_limit")


def test_harness_crash_is_retried_once_then_evaluated() -> None:
    first = outcome.decide(Termination.HARNESS_CRASH, model_code=True)
    repeat = outcome.decide(Termination.HARNESS_CRASH, model_code=True, prior_crash=True)
    assert first.decision == Decision.RETRY
    assert repeat.decision == Decision.EVALUATE
    assert repeat.note == "harness_crash"


@pytest.mark.parametrize("termination", [Termination.COMPLETED, Termination.WALL_CLOCK, Termination.BUDGET])
def test_no_model_code_is_output_invalid_never_tested(termination) -> None:
    verdict = outcome.decide(termination, model_code=False)
    assert verdict.decision == Decision.DONE
    assert verdict.outcome == Outcome.OUTPUT_INVALID
    assert verdict.reason == "no_model_code"
    assert verdict.usable is True  # model behaviour: analysed as data (ITT)


def test_workspace_too_large_is_output_invalid() -> None:
    verdict = outcome.decide(Termination.COMPLETED, model_code=True, workspace_truncated=True)
    assert (verdict.outcome, verdict.reason) == (Outcome.OUTPUT_INVALID, "workspace_too_large")


def test_slot_edit_violation_is_output_invalid() -> None:
    verdict = outcome.decide(Termination.COMPLETED, model_code=True, invalid_reason="edit_violation")
    assert (verdict.outcome, verdict.reason) == (Outcome.OUTPUT_INVALID, "edit_violation")


def test_only_infra_error_is_retryable_and_model_outcomes_are_model_caused() -> None:
    assert outcome.is_retryable_outcome("infra_error")
    for value in ("tested", "build_fail", "start_fail", "output_invalid", "excluded", ""):
        assert not outcome.is_retryable_outcome(value)
    assert {o for o in Outcome if outcome.is_model_caused(o)} == {
        Outcome.TESTED,
        Outcome.BUILD_FAIL,
        Outcome.START_FAIL,
        Outcome.OUTPUT_INVALID,
    }
