"""run_review: replicate loop, engine dispatch, quorum, consensus, early stops, usage totals."""

from __future__ import annotations

import types

import pytest
import requests

from backend.analysis.engine import reviewer
from backend.analysis.engine.reviewer import SampleOutcome
from backend.analysis.engine.reviewer import client
from backend.analysis.engine.reviewer import single_shot
from backend.analysis.tests.reviewer_support import SECRET
from backend.analysis.tests.reviewer_support import SQLI
from backend.analysis.tests.reviewer_support import XSS
from backend.analysis.tests.reviewer_support import FakeResponse
from backend.analysis.tests.reviewer_support import FakeSession
from backend.analysis.tests.reviewer_support import ai_finding as nf
from backend.analysis.tests.reviewer_support import answer
from backend.analysis.tests.reviewer_support import app_sample
from backend.analysis.tests.reviewer_support import finding
from backend.analysis.tests.reviewer_support import frozen_section
from backend.analysis.tests.reviewer_support import make_file
from backend.analysis.tests.reviewer_support import make_sample
from backend.analysis.tests.reviewer_support import ok
from backend.analysis.tests.reviewer_support import patch_normalize

BOTH = ["backend/app.py", "frontend/src/App.jsx"]


@pytest.fixture(autouse=True)
def _normalize(monkeypatch):
    patch_normalize(monkeypatch)


@pytest.fixture
def http(monkeypatch):
    """Route the client's module-level requests.post through a FakeSession."""

    def install(*responses):
        session = FakeSession(*responses)
        monkeypatch.setattr(
            client,
            "requests",
            types.SimpleNamespace(post=session.post, exceptions=requests.exceptions),
        )
        return session

    return install


def review(frozen=None, sample=None, **kw):
    return reviewer.run_review(
        frozen=frozen or frozen_section(),
        sample=sample or app_sample(),
        api_key=kw.pop("api_key", "sk-test"),
        run_id="run-1",
        **kw,
    )


def scripted(monkeypatch, outcomes):
    """Replace single_shot.run_sample with scripted replicates; returns the recorded calls."""
    calls = []

    def fake(*, frozen, sample, index, api_key, is_cancelled, heartbeat):
        calls.append(index)
        item = outcomes[index]
        return item(index) if callable(item) else item

    monkeypatch.setattr(single_shot, "run_sample", fake)
    return calls


def outcome(index, status="succeeded", reason="", findings=(), cost=0.01, **kw):
    return SampleOutcome(
        index=index,
        engine="single_shot",
        status=status,
        failure_reason=reason,
        error_message=kw.pop("error", ""),
        findings=list(findings),
        usage={
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "cost_usd": cost,
            "llm_calls": 1,
            "llm_calls_failed": 0,
        },
        providers={"InferenceNet": 1},
        grounding=kw.pop("grounding", {"exact": len(findings)}),
        **kw,
    )


# --------------------------------------------------------------------------
# End to end through single_shot with mocked HTTP
# --------------------------------------------------------------------------


def test_three_replicates_build_consensus(http):
    session = http(
        ok(answer([SQLI, SECRET], BOTH), gen_id="g0"),
        ok(answer([finding("backend/app.py", 11, 12, SQLI["evidence"]), XSS], BOTH), gen_id="g1"),
        ok(answer([SQLI], BOTH), gen_id="g2"),
    )
    out = review()
    assert out.status == "succeeded", out.message
    assert out.failure_reason == ""
    assert len(session.requests) == 3
    assert [s.index for s in out.samples] == [0, 1, 2]
    assert all(s.ok for s in out.samples)
    # Different file-order seeds per replicate, recorded.
    assert len({s.file_order_seed for s in out.samples}) == 3
    assert len(out.findings) == 5
    counted = [f for f in out.findings if f.counted]
    assert [(f.file_path, f.cwe) for f in counted] == [("backend/app.py", ["CWE-89"])]
    sqli = [f for f in out.findings if f.cwe == ["CWE-89"]]
    assert len(sqli) == 3
    assert all(f.is_consensus for f in sqli)
    assert len({f.issue_key for f in sqli}) == 1
    assert not any(f.is_consensus for f in out.findings if f.cwe != ["CWE-89"])
    agreement = out.agreement
    assert agreement["samples"] == 3
    assert agreement["successful_samples"] == 3
    assert agreement["quorum"] == 2
    assert agreement["threshold"] == 2
    assert agreement["clusters"] == 3
    assert agreement["consensus"] == 1
    assert agreement["counted"] == 1
    # Replicate clusters {sqli, secret}, {sqli, xss}, {sqli}: J = 1/3, 1/2, 1/2.
    assert agreement["mean_pairwise_jaccard"] == pytest.approx((1 / 3 + 1 / 2 + 1 / 2) / 3, abs=1e-6)
    assert agreement["grounding"]["exact"] == 5
    assert agreement["grounding_rate"] == 1.0
    assert agreement["stopped_early"] == ""
    assert out.usage["llm_calls"] == 3
    assert out.usage["cost_usd"] == pytest.approx(0.0036)
    assert out.usage["samples"] == 3
    assert out.usage["providers"] == {"InferenceNet": 3}


def test_scope_reaches_normalization(http, monkeypatch):
    from backend.analysis.engine import normalize
    from backend.analysis.tests.reviewer_support import fake_normalize

    seen = []

    def capture(tool_slug, raw, sample, scope):
        seen.append(scope)
        return fake_normalize(tool_slug, raw, sample, scope)

    monkeypatch.setattr(normalize, "normalize", capture)
    http(ok(answer([SQLI], BOTH)))
    review(frozen=frozen_section(samples=1), scope={"measured_origins": ["added"]})
    assert seen == [{"measured_origins": ["added"]}]


# --------------------------------------------------------------------------
# Quorum and early stops (scripted replicates)
# --------------------------------------------------------------------------


def test_one_failed_replicate_still_meets_quorum(monkeypatch):
    calls = scripted(
        monkeypatch,
        [
            outcome(0, findings=[nf(index=0)]),
            outcome(1, status="truncated", reason="truncated", findings=[nf(index=1, line=30)]),
            outcome(2, findings=[]),
        ],
    )
    out = review()
    assert calls == [0, 1, 2]
    assert out.status == "succeeded"
    assert out.agreement["successful_samples"] == 2
    # Still a majority of the 3 planned samples: the failed one votes for nothing, so the
    # singleton is not consensus (with ceil(m/2) it was: 1 of 2).
    assert out.agreement["threshold"] == 2
    assert out.agreement["planned_samples"] == 3
    assert out.agreement["replicates_complete"] is False
    assert not any(f.counted for f in out.findings)
    # The failed replicate's findings are dropped, its spend is kept.
    assert [f.sample_index for f in out.findings] == [0]
    assert out.samples[1].findings == []
    assert out.samples[1].coverage["discarded_findings"] == 1
    assert out.usage["cost_usd"] == pytest.approx(0.03)
    assert out.agreement["statuses"] == {"succeeded": 2, "truncated": 1}


def test_quorum_missed_stops_early_and_fails(monkeypatch):
    calls = scripted(
        monkeypatch,
        [
            outcome(0, status="invalid_output", reason="invalid_output", error="schema"),
            outcome(1, status="truncated", reason="truncated"),
            outcome(2),
        ],
    )
    out = review()
    assert calls == [0, 1]  # the third replicate cannot reach the quorum any more
    assert out.status == "failed"
    assert out.failure_reason == "invalid_output"
    assert out.agreement["stopped_early"] == "quorum_unreachable"
    assert out.agreement["samples_run"] == 2
    assert "0 of 3 review samples succeeded (quorum 2)" in out.message
    assert "First error: schema" in out.message
    assert out.findings == []
    assert out.usage["cost_usd"] == pytest.approx(0.02)


def test_fatal_failure_stops_immediately(monkeypatch):
    calls = scripted(monkeypatch, [outcome(0, status="failed", reason="auth", error="HTTP 401: bad key")] * 3)
    out = review()
    assert calls == [0]
    assert out.status == "failed"
    assert out.failure_reason == "unavailable"
    assert out.agreement["stopped_early"] == "auth"


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        (["budget", "budget"], "budget"),
        (["timeout", "timeout"], "timeout"),
        (["api_error", "api_error"], "tool_error"),
        (["engine_error", "invalid_output"], "invalid_output"),
        (["budget", "timeout"], "budget"),
        # Agent sandboxes: infrastructure and memory failures are not tool errors.
        (["infra_error", "infra_error"], "infra_error"),
        (["oom", "oom"], "oom"),
        (["infra_error", "invalid_output"], "invalid_output"),
        (["oom", "infra_error"], "oom"),
        (["harness_error", "infra_error"], "infra_error"),
        (["incomplete_review", "incomplete_review"], "invalid_output"),
    ],
)
def test_failure_reason_mapping(monkeypatch, reasons, expected):
    statuses = {"budget": "budget_exhausted", "timeout": "timed_out"}
    scripted(monkeypatch, [outcome(i, status=statuses.get(r, "failed"), reason=r) for i, r in enumerate(reasons)])
    out = review(frozen=frozen_section(samples=3))
    assert out.status == "failed"
    assert out.failure_reason == expected


@pytest.mark.parametrize(
    ("scope", "counted_categories"),
    [
        (None, ["security"]),
        ({"measured_origins": ["authored"]}, ["security"]),
        ({"count_style": True}, ["security", "style"]),
        ({"count_style": True, "count_metric_findings": True}, ["complexity", "security", "style"]),
    ],
)
def test_consensus_counts_categories_under_the_static_outcome_rules(monkeypatch, scope, counted_categories):
    def replicate(index):
        return outcome(
            index,
            findings=[
                nf(line=10, index=index),
                nf(line=30, index=index, category="style", cwe=()),
                nf(line=60, index=index, category="complexity", cwe=()),
            ],
        )

    scripted(monkeypatch, [replicate, replicate])
    out = review(frozen=frozen_section(samples=2), scope=scope)
    assert out.status == "succeeded"
    assert sorted(f.category for f in out.findings if f.counted) == counted_categories
    assert out.agreement["consensus"] == 3
    assert out.agreement["counted"] == len(counted_categories)
    assert out.agreement["uncounted_category"] == 3 - len(counted_categories)


@pytest.mark.parametrize(("k", "quorum"), [(1, 1), (2, 2), (3, 2), (4, 3), (5, 3)])
def test_quorum_is_a_majority_of_the_planned_samples(monkeypatch, k, quorum):
    # Quorum and consensus threshold are one rule, so a successful review can always reach consensus.
    scripted(monkeypatch, [outcome(i) for i in range(k)])
    out = review(frozen=frozen_section(samples=k))
    assert out.agreement["quorum"] == quorum
    assert out.agreement["threshold"] == quorum


def test_even_k_needs_a_majority_of_successful_samples(monkeypatch):
    # k=2: one success can never reach the 2 votes consensus needs, so the review fails instead of
    # reporting "0 consensus findings" (with quorum ceil(k/2) = 1 it succeeded).
    calls = scripted(
        monkeypatch,
        [outcome(0, findings=[nf(index=0)]), outcome(1, status="truncated", reason="truncated")],
    )
    out = review(frozen=frozen_section(samples=2))
    assert calls == [0, 1]
    assert (out.status, out.failure_reason) == ("failed", "invalid_output")


def test_single_replicate_review(monkeypatch):
    scripted(monkeypatch, [outcome(0, findings=[nf(index=0)])])
    out = review(frozen=frozen_section(samples=1))
    assert out.status == "succeeded"
    assert out.agreement["threshold"] == 1
    assert out.agreement["mean_pairwise_jaccard"] is None
    assert [f.counted for f in out.findings] == [True]


def test_engine_crash_is_a_failed_replicate_not_a_crash(monkeypatch):
    def boom(index):
        msg = "unexpected"
        raise RuntimeError(msg)

    scripted(monkeypatch, [boom, outcome(1), outcome(2)])
    out = review()
    assert out.status == "succeeded"
    crashed = out.samples[0]
    assert (crashed.status, crashed.failure_reason) == ("failed", "engine_error")
    assert "RuntimeError: unexpected" in crashed.error_message
    assert crashed.model_slug == "deepseek/deepseek-v4.1-flash"
    assert crashed.prompt_sha256


# --------------------------------------------------------------------------
# Cancellation, applicability, configuration problems
# --------------------------------------------------------------------------


def test_cancelled_before_any_replicate(monkeypatch):
    calls = scripted(monkeypatch, [outcome(0)] * 3)
    out = review(is_cancelled=lambda: True)
    assert calls == []
    assert out.status == "cancelled"
    assert out.failure_reason == "cancelled"


def test_cancelled_replicate_stops_the_review(monkeypatch):
    calls = scripted(monkeypatch, [outcome(0), outcome(1, status="cancelled", reason="cancelled"), outcome(2)])
    out = review()
    assert calls == [0, 1]
    assert out.status == "cancelled"
    assert len(out.samples) == 2


def test_heartbeat_between_replicates(monkeypatch):
    scripted(monkeypatch, [outcome(0), outcome(1), outcome(2)])
    beats = []
    review(heartbeat=lambda: beats.append(1))
    assert len(beats) == 3


def test_not_applicable_without_reviewable_files(http):
    session = http()
    sample = make_sample([make_file("README.md", "# hi\n", kind="doc"), make_file("x.py", "a=1\n", origin="scaffold")])
    out = review(sample=sample)
    assert out.status == "not_applicable"
    assert out.samples == []
    assert session.requests == []


def test_missing_api_key_is_unavailable(monkeypatch):
    calls = scripted(monkeypatch, [outcome(0)] * 3)
    out = review(api_key="")
    assert calls == []
    assert (out.status, out.failure_reason) == ("failed", "unavailable")


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"prompt_sha256": "0" * 64}, "changed since the plan was frozen"),
        ({"schema_sha256": "0" * 64}, "changed since the plan was frozen"),
        ({"prompt_version": "review-v9"}, "unknown review prompt version"),
        ({"engine": "oracle"}, "unknown review engine"),
        ({"samples": 0}, "invalid replicate count"),
        ({"model": {}}, "no model"),
        ({"enabled": False}, "not enabled"),
        ({"engine_version": "reviewer-v1"}, "re-freeze the plan"),
    ],
)
def test_invalid_frozen_sections_fail_without_calls(monkeypatch, overrides, fragment):
    calls = scripted(monkeypatch, [outcome(0)] * 3)
    out = review(frozen=frozen_section(**overrides))
    assert calls == []
    assert out.status == "failed"
    assert out.failure_reason == "invalid_config"
    assert fragment in out.message


# --------------------------------------------------------------------------
# Agent engine dispatch
# --------------------------------------------------------------------------


def test_agent_engine_dispatches_to_agentic(monkeypatch):
    from backend.analysis.engine.reviewer import agentic

    seen = []

    def run_sample(*, frozen, sample, index, api_key, run_id, is_cancelled, heartbeat):
        seen.append((index, run_id, frozen["harness"]))
        return SampleOutcome(index=index, engine="agent", harness="opencode", status="succeeded")

    monkeypatch.setattr(agentic, "run_sample", run_sample)
    out = review(frozen=frozen_section(engine="agent", harness="opencode", samples=2))
    assert out.status == "succeeded"
    assert seen == [(0, "run-1", "opencode"), (1, "run-1", "opencode")]
    assert out.agreement["mean_pairwise_jaccard"] == 1.0


def test_agent_engine_not_applicable_uses_scope_selection(monkeypatch):
    from backend.analysis.engine.reviewer import agentic

    monkeypatch.setattr(agentic, "run_sample", lambda **kw: pytest.fail("must not run"))
    sample = make_sample([make_file("README.md", "# hi\n", kind="doc")])
    out = review(frozen=frozen_section(engine="agent", harness="opencode"), sample=sample)
    assert out.status == "not_applicable"


@pytest.mark.parametrize("engine", ["single_shot", "agent"])
def test_only_blank_files_in_scope_is_not_applicable_for_both_engines(monkeypatch, engine):
    # Regression: the agent engine used scope selection alone, ran replicates that each failed
    # "no_files" and reported a FAILED tool_error review where single-shot said not_applicable.
    from backend.analysis.engine.reviewer import agentic

    monkeypatch.setattr(agentic, "run_sample", lambda **kw: pytest.fail("must not run"))
    monkeypatch.setattr(single_shot, "run_sample", lambda **kw: pytest.fail("must not run"))
    sample = make_sample([make_file("app.py", "\n\n   \n"), make_file("README.md", "# hi\n", kind="doc")])
    out = review(
        frozen=frozen_section(engine=engine, harness="opencode" if engine == "agent" else None),
        sample=sample,
    )
    assert out.status == "not_applicable"
    assert out.samples == []


def test_usage_totals_flag_unknown_spend(monkeypatch):
    # Regression: a replicate whose spend is unknown (lost gateway log) was skipped and the total
    # presented as exact; it is a lower bound now, flagged.
    unknown = outcome(1)
    unknown.usage = {"cost_usd": None, "llm_calls": None, "gateway_log_missing": True}
    partial = outcome(2, cost=0.004)
    partial.usage["cost_complete"] = False
    scripted(monkeypatch, [outcome(0), unknown, partial])
    out = review()
    assert out.usage["cost_usd"] == pytest.approx(0.014)
    assert out.usage["cost_complete"] is False
    assert out.usage["cost_incomplete_samples"] == 2

    scripted(monkeypatch, [outcome(0), outcome(1), outcome(2)])
    assert review().usage["cost_complete"] is True

    nothing = [outcome(i) for i in range(3)]
    for item in nothing:
        item.usage = {"cost_usd": None, "llm_calls": None, "gateway_log_missing": True}
    scripted(monkeypatch, nothing)
    out = review()
    assert out.usage["cost_usd"] is None  # unknown, never 0
    assert out.usage["cost_complete"] is False


def test_http_failure_end_to_end_is_never_zero_findings(http, monkeypatch):
    monkeypatch.setattr(client, "_backoff", lambda attempt: 0.0)
    http(*[FakeResponse(500, "down")] * 8)
    out = review(frozen=frozen_section(samples=1))
    assert out.status == "failed"
    assert out.failure_reason == "tool_error"
    assert out.findings == []
    assert out.samples[0].failure_reason == "api_error"
