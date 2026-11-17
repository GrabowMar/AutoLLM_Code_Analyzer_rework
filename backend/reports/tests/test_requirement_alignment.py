"""Prompt exposure sensitivity must retain failures, unknown tags and app units."""

from backend.reports.services.requirement_alignment import alignment_fields
from backend.reports.services.requirement_alignment import alignment_section


def test_statedness_excludes_infra_flaky_but_counts_skips_and_keeps_unknowns():
    result = {
        "checks": [
            {"id": "a", "category": "functional", "status": "pass", "stated_in": ["standard"]},
            {"id": "b", "category": "functional", "status": "skipped", "stated_in": ["standard"]},
            {"id": "c", "category": "functional", "status": "error", "stated_in": ["exhaustive"]},
            {"id": "d", "category": "functional", "status": "pass"},
            {"id": "e", "category": "infra", "status": "fail", "stated_in": ["standard"]},
            {"id": "f", "category": "functional", "status": "fail", "stated_in": ["standard"]},
        ],
        "flaky_checks": ["f"],
    }
    values = alignment_fields(result, "standard", measured=True)
    assert values["stated_checks"] == 2
    assert values["stated_pass_rate"] == 0.5
    assert values["unstated_checks"] == 1
    assert values["unstated_pass_rate"] == 0
    assert values["all_stated_checks_passed"] is False
    assert values["statedness_unknown_checks"] == 1
    assert alignment_fields(result, None, measured=True)["stated_pass_rate"] is None
    assert alignment_fields(result, "standard", measured=False)["stated_pass_rate"] is None
    assert alignment_fields({}, "standard", measured=True)["all_stated_checks_passed"] is None


def test_alignment_averages_repeats_within_apps_and_reports_measured_denominators():
    base = {"group": "model", "model": "model", "kind": "task", "condition_id": None, "unit_key": "a"}
    rows = [
        {**base, "run_id": "1", "stated_pass_rate": 0, "pass_rate": 0},
        {**base, "run_id": "2", "stated_pass_rate": 0, "pass_rate": 0},
        {**base, "run_id": "3", "unit_key": "b", "stated_pass_rate": 1, "pass_rate": 0.5},
        {**base, "run_id": "4", "unit_key": "c", "stated_pass_rate": None, "pass_rate": None},
    ]
    section = alignment_section(rows)
    overall = section["overall"]
    assert overall["runs"] == 4
    assert overall["stated_measured_runs"] == 3
    assert overall["stated_measured_apps"] == 2
    assert overall["stated_all_pass_runs"] == 1
    assert overall["stated_mean_pass_rate"] == 0.5
    assert overall["stated_all_pass_rate"] == 0.5
    assert overall["unstated_mean_pass_rate"] is None
    assert overall["strict_mean_pass_rate"] == 0.25
