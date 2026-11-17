"""Intervals describe paired app effects, without counting repeats as independent apps."""

from backend.reports.services.plan_stats import paired_mean_ci


def test_paired_interval_requires_three_independent_apps():
    assert paired_mean_ci([]) is None
    assert paired_mean_ci([0.5, 0.5]) is None


def test_paired_interval_constant_shift_and_identical_pairs():
    assert paired_mean_ci([0.25] * 8) == [0.25, 0.25]
    assert paired_mean_ci([0.0] * 8) == [0.0, 0.0]


def test_paired_interval_is_seeded_and_translates_with_effect():
    deltas = [-0.5, -0.1, 0.0, 0.3, 0.8]
    interval = paired_mean_ci(deltas, seed=42)
    assert interval == paired_mean_ci(deltas, seed=42)
    shifted = paired_mean_ci([d + 2 for d in deltas], seed=42)
    assert shifted == [round(v + 2, 4) for v in interval]
    assert interval[0] < sum(deltas) / len(deltas) < interval[1]
