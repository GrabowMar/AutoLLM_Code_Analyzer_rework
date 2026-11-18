"""Exploratory inference pairs only shared cells and corrects the complete family."""

from types import SimpleNamespace

from backend.reports.services.exploratory_comparisons import exploratory_comparisons
from backend.reports.tests.test_outcome_denominators import row
from backend.statistics.services.inference import holm


def observations(n=6):
    rows = []
    for group in ("a", "b", "c"):
        for unit in range(n):
            r = row(f"task-{unit}", "tested", measured=True, passed=True)
            r.update(
                group=group,
                model=group,
                condition_id=group,
                unit_key=f"task-{unit}",
                run_id=f"{group}-{unit}",
                repeat_index=0,
                pass_rate=0.3 + 0.2 * (ord(group) - ord("a")),
                unique_issues=None,
                security_unique_model=None,
                code_unique_model=None,
                cost=None,
                loc=None,
            )
            rows.append(r)
    return rows


def test_unscoped_or_running_reports_never_launch_posthoc_inference():
    assert exploratory_comparisons(observations(), None) is None
    assert exploratory_comparisons(observations(), SimpleNamespace(status="running")) is None


def test_pairs_and_correction_are_not_selected_by_significance():
    result = exploratory_comparisons(observations(), SimpleNamespace(status="completed"), seed=7)
    tested = [r for f in result["families"] for r in f["comparisons"] if r["p_wilcoxon"] is not None]
    assert len(tested) == 12
    assert result["family_size"] == 12
    expected = holm([r["p_wilcoxon"] for r in tested])
    assert [r["p_adjusted"] for r in tested] == expected
    for comparison in tested:
        assert comparison["n_pairs"] == 6
        assert comparison["mean_delta_ci95"] is not None
        assert comparison["mean_delta"] >= 0
        if comparison["all_zero"]:
            assert comparison["p_wilcoxon"] == comparison["p_sign"] == 1
    assert result["status"] == "exploratory"
    assert result["baseline_condition"] == "condition A (the right-hand member of B vs A)"


def test_unmatched_repeat_is_excluded_and_small_sample_is_not_tested():
    rows = observations(4)
    extra = dict(rows[0], run_id="extra", repeat_index=1, pass_rate=1)
    result = exploratory_comparisons([*rows, extra], SimpleNamespace(status="completed"))
    comparisons = next(f["comparisons"] for f in result["families"] if f["metric"] == "pass_rate")
    ab = next(c for c in comparisons if c["condition_a"] == "a" and c["condition_b"] == "b")
    assert ab["n_pairs"] == 4
    assert ab["cells_only_a"] == 1
    assert abs(ab["mean_delta"] - 0.2) < 1e-8
    assert ab["p_adjusted"] is None
    assert ab["skipped"] == "below_min_pairs"


def test_deployment_comparison_does_not_count_static_only_completion_as_deployed():
    rows = observations()
    rows[0]["outcome_reason"] = "not_tested:static"
    result = exploratory_comparisons(rows, SimpleNamespace(status="completed"))
    comparisons = next(f["comparisons"] for f in result["families"] if f["metric"] == "end_to_end_deploy_rate")
    ab = next(c for c in comparisons if c["condition_a"] == "a" and c["condition_b"] == "b")
    assert ab["n_pairs"] == 5
    assert ab["cells_only_b"] == 1
