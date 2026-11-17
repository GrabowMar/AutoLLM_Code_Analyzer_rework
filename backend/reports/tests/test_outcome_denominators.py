"""Whole-cohort outcomes must not disappear when functional evaluation is unavailable."""

from types import SimpleNamespace

from backend.reports.services.study_report import _evaluation_fields
from backend.reports.services.study_report import funnel_section


def row(key, outcome, *, measured=False, passed=None):
    return {
        "run_id": key,
        "unit_key": key,
        "kind": "app_agentic",
        "model": "m",
        "group": "m",
        "condition_id": None,
        "task": key,
        "outcome": outcome,
        "generated": outcome != "output_invalid",
        "evaluated": measured,
        "deployed": outcome == "tested" if measured else None,
        "functional_passed": passed,
        "pass_rate": float(passed) if passed is not None else None,
        "analyzed": False,
        "truncated": False,
    }


def test_cohort_rate_includes_build_and_output_failures_and_keeps_unknown_grades():
    rows = [
        row("a", "tested", measured=True, passed=True),
        row("b", "tested"),
        row("c", "build_fail"),
        row("d", "output_invalid"),
    ]
    summary = funnel_section(rows)["overall"]
    assert summary["deploy_rate"]["rate"] == 1
    assert summary["end_to_end_deploy_rate"]["rate"] == 0.5
    assert summary["end_to_end_deploy_rate"]["n"] == 4
    assert summary["end_to_end_functional_bounds"]["lower"] == 0.25
    assert summary["end_to_end_functional_bounds"]["upper"] == 0.5
    assert summary["end_to_end_functional_bounds"]["unknown_runs"] == 1


def test_unknown_repeat_is_bounded_with_equal_app_weights():
    rows = [row("a", "tested", measured=True, passed=True), row("a", "tested"), row("b", "start_fail")]
    bounds = funnel_section(rows)["overall"]["end_to_end_functional_bounds"]
    assert bounds["apps"] == 2
    assert bounds["lower"] == 0.25
    assert bounds["upper"] == 0.5
    assert bounds["unknown_apps"] == 1


def test_empty_cohort_has_no_zero_success_bound():
    summary = funnel_section([])["overall"]
    assert summary["end_to_end_deploy_rate"]["rate"] is None
    assert summary["end_to_end_functional_bounds"]["lower"] is None


def test_static_or_build_only_completion_does_not_establish_deployment():
    static = row("static", "tested")
    static["outcome_reason"] = "not_tested:static"
    summary = funnel_section([row("normal", "tested"), static])["overall"]
    assert summary["end_to_end_deploy_rate"]["rate"] is None
    assert summary["end_to_end_deploy_rate"]["unknown_runs"] == 1
    assert summary["end_to_end_deploy_rate"]["k"] == 1
    static["start_stage_status"] = "ok"
    assert funnel_section([static])["overall"]["end_to_end_deploy_rate"]["rate"] == 1


def test_summary_names_conditional_rates_and_exposes_full_cohort_rate():
    from backend.reports.services.report_service import _make_summary

    data = {"funnel": funnel_section([row("a", "tested", measured=True, passed=True), row("b", "build_fail")])}
    summary = _make_summary("study_report", data)
    assert summary["end_to_end_deploy_rate"] == 0.5
    assert summary["conditional_deploy_rate"] == 1
    assert "deploy_rate" not in summary


def test_conditional_deployment_uses_the_selected_evaluation_stage_evidence():
    evaluation = SimpleNamespace(
        status="measured",
        result={"measured": True, "metrics": {"agent_pass_rate": 0.5}},
        stages={"build": {"status": "fail"}},
        eval_suite=SimpleNamespace(ref="suite@1"),
    )
    view = SimpleNamespace(latest_evaluation=lambda: evaluation, task_dict=lambda: {"variant": "standard"})
    run = SimpleNamespace(outcome="tested", outcome_reason="")
    fields = _evaluation_fields(view, run)
    assert fields["deployed"] is False
    assert fields["pass_rate"] == 0
    assert fields["functional_passed"] is False
    assert run.outcome == "tested"  # historical final deployment remains a distinct recorded outcome
