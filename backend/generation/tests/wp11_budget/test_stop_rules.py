"""Stop rules (4.6): each rule fires and its action applies; pilot gates; precision stopping."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from backend.generation.budget import stop_rules
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models import StudyCondition
from backend.generation.tests.factories import PatternDetectorFactory
from backend.generation.tests.factories import PatternHitFactory
from backend.generation.tests.factories import PatternRunFactory
from backend.generation.tests.wp11_budget.conftest import make_study

pytestmark = pytest.mark.django_db


def _rule(code, *, threshold, action, scope="study", window=20, **kw):
    return {"code": code, "scope": scope, "window": window, "threshold": threshold, "action": action, **kw}


def _study(user, rules, **kw):
    kw.setdefault("n_conditions", 2)
    kw.setdefault("n_units", 1)
    kw.setdefault("repeats", 6)
    return make_study(user, policy_overrides={"study.stop_rules": rules}, **kw)


def _finish(runs, outcome, *, reason="", minutes_ago=0, **fields):
    now = timezone.now() - timedelta(minutes=minutes_ago)
    for index, run in enumerate(runs):
        GenerationRun.objects.filter(pk=run.pk).update(
            status="completed" if outcome != "infra_error" else "failed",
            outcome=outcome,
            outcome_reason=reason,
            usable=outcome in ("tested", "build_fail", "start_fail", "output_invalid"),
            completed_at=now + timedelta(seconds=index),
            **fields,
        )


def _evaluate(tree, on_commit):
    study = Study.objects.select_related("resource_policy").get(pk=tree.study.pk)
    with on_commit():
        fired = stop_rules.evaluate(study)
    study.refresh_from_db()
    return study, fired


def test_infra_error_rate_pauses(user, on_commit, events):
    tree = _study(user, [_rule("infra_error_rate", threshold=0.30, action="pause")])
    runs = tree.runs
    _finish(runs[:2], "infra_error")
    _finish(runs[2:5], "tested")
    study, fired = _evaluate(tree, on_commit)
    assert [(f["code"], f["applied"]) for f in fired] == [("infra_error_rate", True)]
    assert fired[0]["value"] == pytest.approx(0.4)
    assert (study.status, study.pause_reason) == ("paused", "rule:infra_error_rate")
    assert any(e["reason"] == "rule:infra_error_rate" for e in events.of("study:", "paused"))
    # Pending runs are not dropped: a pause keeps them queued.
    assert GenerationRun.objects.filter(study=study, status="pending").count() == len(runs) - 5


def test_topped_up_failures_no_longer_count(user, on_commit):
    """An attempt a top-up replaced (a newer run on its cell) is acknowledged: the rule does not re-fire on it."""
    tree = _study(user, [_rule("infra_error_rate", threshold=0.30, action="pause")])
    failed = tree.runs[:5]
    _finish(failed, "infra_error")
    study, fired = _evaluate(tree, on_commit)
    assert study.pause_reason == "rule:infra_error_rate"
    # The researcher fixes the cause, tops the cells up (a new attempt per cell) and resumes.
    retries = [
        GenerationRun.objects.create(
            **{
                f.attname: getattr(run, f.attname)
                for f in GenerationRun._meta.concrete_fields
                if f.attname not in ("id", "created_at", "updated_at", "status", "outcome", "outcome_reason", "usable")
            },
        )
        for run in failed
    ]
    Study.objects.filter(pk=study.pk).update(status="running", pause_reason="")
    study, fired = _evaluate(tree, on_commit)
    assert fired == []
    assert study.status in ("piloting", "running")
    _finish(retries, "tested")
    _finish(tree.runs[5:7], "infra_error")
    _study_row, fired = _evaluate(tree, on_commit)
    assert fired == []  # 2 / 7 current failures, the 5 replaced ones are not counted


def test_rates_need_enough_runs(user, on_commit):
    tree = _study(user, [_rule("infra_error_rate", threshold=0.30, action="pause")])
    _finish(tree.runs[:2], "infra_error")
    _study_row, fired = _evaluate(tree, on_commit)
    assert fired == []


def test_no_provider_rate(user, on_commit):
    tree = _study(user, [_rule("no_provider_rate", threshold=0.20, action="pause")])
    _finish(tree.runs[:2], "infra_error", reason="no_provider")
    _finish(tree.runs[2:6], "tested")
    study, _fired = _evaluate(tree, on_commit)
    assert study.pause_reason == "rule:no_provider_rate"


def test_output_invalid_rate_stops_exploratory_only(user, on_commit):
    rules = [_rule("output_invalid_rate", threshold=0.5, action="stop")]
    tree = _study(user, rules)
    _finish(tree.runs[:4], "output_invalid")
    _finish(tree.runs[4:6], "tested")
    study, fired = _evaluate(tree, on_commit)
    assert fired
    assert fired[0]["applied"]
    assert study.stop_reason["code"] == "rule:output_invalid_rate"
    assert study.stop_reason["rule"] == "output_invalid_rate"
    pending = GenerationRun.objects.filter(study=study, outcome_reason="stopped_by_rule")
    assert pending.count() == len(tree.runs) - 6
    assert StudyCell.objects.filter(study=study, exclusion_reason="stopped_by_rule").count() == len(tree.runs) - 6
    assert study.status == "stopped"  # nothing active: stops at once

    confirmatory = _study(user, rules, kind="confirmatory")
    _finish(confirmatory.runs[:4], "output_invalid")
    _study_row, fired = _evaluate(confirmatory, on_commit)
    assert fired == []


def test_consecutive_failures_pauses_only_that_condition(user, on_commit, tick, events):
    tree = _study(
        user,
        [_rule("consecutive_failures", threshold=5, action="pause_condition", scope="condition", window=5)],
        repeats=8,
    )
    c0 = tree.runs_of(condition=0)
    _finish(c0[:5], "build_fail")
    study, fired = _evaluate(tree, on_commit)
    assert [(f["code"], f["scope_key"]) for f in fired] == [("consecutive_failures", tree.conditions[0].pk)]
    paused = StudyCondition.objects.get(pk=tree.conditions[0].pk)
    assert (paused.paused, paused.pause_reason) == (True, "rule:consecutive_failures")
    assert study.status == "running"
    assert any(e.get("condition_id") == paused.pk for e in events.of("study:", "paused"))
    # The scheduler now claims only the other condition.
    result = tick(8)
    claimed_conditions = set(
        GenerationRun.objects.filter(pk__in=[c.run_id for c in result.claimed]).values_list("condition_id", flat=True),
    )
    assert claimed_conditions == {tree.conditions[1].pk}
    # Evaluating again does not pause twice.
    _study_row, again = _evaluate(tree, on_commit)
    assert [f["applied"] for f in again] == [False]


def test_a_tested_run_breaks_the_failure_rule(user, on_commit):
    tree = _study(
        user,
        [_rule("consecutive_failures", threshold=5, action="pause_condition", scope="condition", window=5)],
        repeats=8,
    )
    c0 = tree.runs_of(condition=0)
    _finish(c0[:1], "tested", minutes_ago=10)
    _finish(c0[1:6], "build_fail")
    _study_row, fired = _evaluate(tree, on_commit)
    assert fired == []


def test_cost_overrun_pauses_the_condition(user, on_commit):
    rules = [_rule("cost_overrun", threshold=2.0, action="pause_condition", scope="condition", window=5)]
    tree = _study(user, rules, estimate_usd=0.10)
    c0 = tree.runs_of(condition=0)
    _finish(c0[:5], "tested", metrics={"cost_usd": 0.25})
    c1 = tree.runs_of(condition=1)
    _finish(c1[:5], "tested", metrics={"cost_usd": 0.15})
    _study_row, fired = _evaluate(tree, on_commit)
    assert [(f["scope_key"], f["value"]) for f in fired] == [(tree.conditions[0].pk, pytest.approx(2.5))]
    assert StudyCondition.objects.get(pk=tree.conditions[0].pk).paused
    assert not StudyCondition.objects.get(pk=tree.conditions[1].pk).paused


def test_spend_fraction_alert_and_pause(user, on_commit, tick):
    from backend.generation.budget import ledger

    tree = _study(user, [_rule("spend_fraction", threshold=0.5, action="alert")], cap=1.0, repeats=1, n_conditions=1)
    run = GenerationRun.objects.get(pk=tick(1).claimed[0].run_id)
    ledger.record_usage(
        run,
        attempt=1,
        source="gateway_live",
        amount_usd=0.6,
        list_cost_usd=0.6,
        tokens_in=0,
        tokens_out=0,
        requests=1,
        dedupe_key="live:t:1",
    )
    study, fired = _evaluate(tree, on_commit)
    assert [(f["code"], f["applied"], f["value"]) for f in fired] == [("spend_fraction", False, 0.6)]
    assert study.status == "running"
    pausing = _study(
        user,
        [_rule("spend_fraction", threshold=0.5, action="pause")],
        cap=0.2,
        cap_usd=0.15,
        repeats=1,
        n_conditions=1,
    )
    run = GenerationRun.objects.get(pk=tick(2).claimed[0].run_id)
    ledger.record_usage(
        run,
        attempt=1,
        source="gateway_live",
        amount_usd=0.15,
        list_cost_usd=0.15,
        tokens_in=0,
        tokens_out=0,
        requests=1,
        dedupe_key="live:t:2",
    )
    study, _fired = _evaluate(pausing, on_commit)
    assert study.pause_reason == "rule:spend_fraction"


def test_condition_error_rate_stops_that_condition_only(user, on_commit):
    rules = [_rule("condition_error_rate", threshold=0.5, action="stop", scope="condition", window=5)]
    tree = _study(user, rules, repeats=8)
    c0 = tree.runs_of(condition=0)
    _finish(c0[:5], "infra_error")
    study, _fired = _evaluate(tree, on_commit)
    assert not study.stop_reason
    excluded = GenerationRun.objects.filter(study=study, outcome_reason="stopped_by_rule")
    assert set(excluded.values_list("condition_id", flat=True)) == {tree.conditions[0].pk}
    assert excluded.count() == 3
    assert GenerationRun.objects.filter(study=study, condition=tree.conditions[1], status="pending").count() == 8


def test_wall_clock_total_stops_the_study(user, on_commit):
    tree = _study(user, [_rule("wall_clock_total", threshold=2.0, action="stop")])
    Study.objects.filter(pk=tree.study.pk).update(launched_at=timezone.now() - timedelta(hours=3))
    study, _fired = _evaluate(tree, on_commit)
    assert study.status == "stopped"
    assert study.stop_reason["code"] == "rule:wall_clock_total"


def test_stages_filter(user, on_commit):
    tree = _study(user, [_rule("infra_error_rate", threshold=0.3, action="pause", stages=["pilot"])])
    _finish(tree.runs[:5], "infra_error")
    _study_row, fired = _evaluate(tree, on_commit)
    assert fired == []  # a main-stage study ignores a pilot-only rule


def _precision_study(user, kind):
    rules = [
        _rule(
            "ci_width_reached",
            threshold=0.15,
            action="stop",
            scope="condition",
            features=["py-sql-fstring"],
            n_min=10,
        ),
    ]
    tree = _study(user, rules, kind=kind, mode="slot", repeats=14, n_conditions=2, n_units=1)
    detector = PatternDetectorFactory()
    live = PatternRunFactory(study=tree.study, is_live=True)
    c0 = tree.runs_of(condition=0)
    _finish(c0[:12], "tested")
    for run in c0[:12]:
        PatternHitFactory(pattern_run=live, run=run, detector=detector, provisional=True)
    return tree


def test_ci_width_reached_stops_the_condition_target_and_flags_adaptive_n(user, on_commit):
    tree = _precision_study(user, "exploratory")
    study, fired = _evaluate(tree, on_commit)
    assert len(fired) == 1
    hit = fired[0]
    # Wilson 95% half-width of 0/12 is 0.1212 < 0.15 with n = 12 >= n_min 10; looks K = 12 - 10 + 1.
    assert hit["value"] == pytest.approx(stop_rules.wilson_half_width(0, 12))
    assert (hit["n"], hit["looks"]) == (12, 3)
    cells = StudyCell.objects.filter(study=study, condition=tree.conditions[0])
    assert all(cells.values_list("adaptive_n", flat=True))
    assert (
        GenerationRun.objects.filter(
            study=study,
            condition=tree.conditions[0],
            outcome_reason="stopped_by_rule",
        ).count()
        == 2
    )
    assert not StudyCell.objects.filter(study=study, condition=tree.conditions[1], adaptive_n=True).exists()


def test_confirmatory_refuses_ci_width_reached(user, on_commit):
    tree = _precision_study(user, "confirmatory")
    study, fired = _evaluate(tree, on_commit)
    assert fired == []
    assert not StudyCell.objects.filter(study=study, adaptive_n=True).exists()
    from backend.generation.budget import policy

    issues = policy.lint(
        study.resource_policy.content,
        study_ctx=policy.StudyPolicyContext(study_kind="confirmatory", mode="slot"),
    )
    assert "adaptive_stop_in_confirmatory" in {i.code for i in issues}


def test_wilson_half_width_vectors():
    assert stop_rules.wilson_half_width(0, 0) == 1.0
    assert stop_rules.wilson_half_width(5, 10) == pytest.approx(0.2634, abs=1e-4)  # 0.2366 .. 0.7634
    assert stop_rules.wilson_half_width(0, 12) == pytest.approx(0.1212, abs=1e-4)


# --- pilot gates ---------------------------------------------------------------------------------------------


def _gates(gates):
    return {(g["code"], g.get("condition_id")): g for g in gates}


def test_pilot_gates(user):
    tree = make_study(user, n_conditions=2, n_units=1, repeats=5, stage="pilot", status="piloting", estimate_usd=0.10)
    c0, c1 = tree.runs_of(condition=0), tree.runs_of(condition=1)
    _finish(c0[:2], "infra_error")
    _finish(
        c0[2:5],
        "tested",
        metrics={"cost_usd": 0.20},
        validity={"truncated": True, "termination": "wall_clock", "notes": [{"code": "wall_clock", "level": "warn"}]},
    )
    _finish(c1, "tested", metrics={"cost_usd": 0.10, "call_truncations": 0})
    study = Study.objects.select_related("resource_policy").get(pk=tree.study.pk)
    gates = _gates(stop_rules.pilot_gates(study))
    a, b = tree.conditions[0].pk, tree.conditions[1].pk
    assert gates[("max_infra_error_rate", a)]["value"] == pytest.approx(0.4)
    assert not gates[("max_infra_error_rate", a)]["ok"]
    assert gates[("max_infra_error_rate", b)]["ok"]
    assert gates[("min_build_rate", a)]["value"] == 1.0
    assert gates[("max_cost_overrun", a)]["value"] == pytest.approx(2.0)
    assert not gates[("max_cost_overrun", a)]["ok"]
    assert gates[("max_cost_overrun", b)]["value"] == pytest.approx(1.0)
    assert gates[("max_cap_hit_rate", a)]["value"] == pytest.approx(0.6)
    assert gates[("max_truncation_rate", a)]["value"] == pytest.approx(0.6)
    assert gates[("max_wall_clock_rate", a)]["value"] == pytest.approx(0.6)
    assert gates[("cap_hits_by_kind", None)]["value"] == {"wall_clock": 3}
    assert not stop_rules.gates_pass(list(gates.values()))
    assert stop_rules.pilot_observations(study) == {0: [0.2, 0.2, 0.2], 1: [0.1] * 5}


def test_pilot_gate_without_usable_runs_fails_and_is_not_measured(user):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=2, stage="pilot", status="piloting")
    _finish(tree.runs, "infra_error")
    gates = _gates(stop_rules.pilot_gates(Study.objects.get(pk=tree.study.pk)))
    build = gates[("min_build_rate", tree.conditions[0].pk)]
    assert (build["ok"], build["value"], build["measured"]) == (False, None, False)


def test_usd_cap_binding_only_under_tokens_basis(user):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=2, stage="pilot", status="piloting")
    _finish(tree.runs, "tested", validity={"notes": [{"code": "budget_exhausted:cost", "level": "warn"}]})
    study = Study.objects.get(pk=tree.study.pk)
    assert ("usd_cap_binding", tree.conditions[0].pk) not in _gates(stop_rules.pilot_gates(study))
    Study.objects.filter(pk=study.pk).update(budget_basis="tokens")
    study.refresh_from_db()
    gate = _gates(stop_rules.pilot_gates(study))[("usd_cap_binding", tree.conditions[0].pk)]
    assert (gate["value"], gate["ok"]) == (1.0, False)


def test_oom_gate_per_stack(user):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=2, stage="pilot", status="piloting")
    _finish(tree.runs[:1], "excluded", reason="resource_limit")
    _finish(tree.runs[1:], "tested")
    gates = [g for g in stop_rules.pilot_gates(Study.objects.get(pk=tree.study.pk)) if g["code"] == "max_oom_rate"]
    assert len(gates) == 1
    assert gates[0]["value"] == 0.5
    assert not gates[0]["ok"]
