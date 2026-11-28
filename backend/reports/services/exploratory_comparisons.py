"""Paired condition comparisons within a completed study without a declared baseline plan.

Also the group comparisons of a cross-study sample (``cohort_selection``, one run per group and task): there the
groups are pooled models and pairs match on the task alone.
"""

from collections import defaultdict
from itertools import combinations

from backend.reports.services.plan_stats import PLAN_METRICS
from backend.reports.services.plan_stats import paired_difference
from backend.reports.services.plan_stats import paired_mean_ci
from backend.reports.services.plan_stats import sign_test_p
from backend.reports.services.plan_stats import wilcoxon_p
from backend.statistics.services import inference

MIN_PAIRS = 5
# The final assessment's tested metrics: the whole-cohort rates (known output/build/start failures counted as 0)
# and the cost at list price, beside the issue metrics. The conditional rates and the billed cost stay descriptive
# (model_outcomes, funnel): conditional rates leave known failures out, and billed cost does not compare billing
# routes, so neither is tested (nor spends the Holm family). ``deploy_rate_itt`` is the per-app end-to-end deployment.
FINAL_ASSESSMENT_COMPARISONS = (
    "deploy_rate_itt",
    "pass_rate_itt",
    "functional_passed_itt",
    "unique_issues_per_app",
    "issues_per_kloc",
    "security_per_app",
    "list_cost_per_app",
)


def exploratory_comparisons(rows, study, *, seed=0, cross_study=False, final_assessment=False):
    """Exploratory effects of recorded condition systems, not isolated model causation.

    Pair only matching task/slot units and repeat indices. Repeats are averaged
    inside matched units before inference. All condition pairs and metrics share
    one Holm correction family; missing pairs are reported rather than imputed.

    *cross_study*: *rows* are a cross-study selection (no one study; one run per group and task), so the
    groups are compared without a study and pairs match on the unit alone (the repeat index of each group's
    newest run is not a design position).

    *final_assessment*: *rows* carry the final-assessment columns (``study_report.app_table(prices=...)``), so the
    tested metrics are :data:`FINAL_ASSESSMENT_COMPARISONS` instead of the plan metrics.
    """
    if not cross_study and (study is None or study.status != "completed"):
        return None
    from backend.reports.services.study_report import deployment_outcome
    from backend.reports.services.study_report import model_criteria

    groups = defaultdict(list)
    for row in rows:
        groups[row["group"]].append(row)
    if len(groups) < 2:
        return None

    def metric_value(items, metric):
        if metric == "end_to_end_deploy_rate":
            outcomes = [deployment_outcome(r) for r in items]
            return sum(outcomes) / len(outcomes) if outcomes and None not in outcomes else None
        return model_criteria(items)[metric]

    metrics = list(FINAL_ASSESSMENT_COMPARISONS) if final_assessment else [*PLAN_METRICS, "end_to_end_deploy_rate"]
    values = {row["run_id"]: {metric: metric_value([row], metric) for metric in metrics} for row in rows}
    families = []
    tested = []
    for metric in metrics:
        cells = {
            group: {
                (r.get("kind"), r.get("unit_key") or r["run_id"], 0 if cross_study else r.get("repeat_index", 0)): r
                for r in items
                if values[r["run_id"]][metric] is not None
            }
            for group, items in groups.items()
        }
        comparisons = []
        for first, second in combinations(sorted(groups), 2):
            common = sorted(set(cells[first]) & set(cells[second]), key=str)
            by_unit = defaultdict(list)
            for cell in common:
                by_unit[cell[:2]].append(cell)
            deltas = []
            for keys in by_unit.values():
                a = metric_value([cells[first][key] for key in keys], metric)
                b = metric_value([cells[second][key] for key in keys], metric)
                deltas.append(paired_difference(a, b))
            wins = sum(d > 0 for d in deltas)
            losses = sum(d < 0 for d in deltas)
            row = {
                "condition": f"{second} vs {first}",
                "condition_a": first,
                "condition_b": second,
                "n_pairs": len(deltas),
                "common_cells": len(common),
                "cells_only_a": len(set(cells[first]) - set(cells[second])),
                "cells_only_b": len(set(cells[second]) - set(cells[first])),
                "mean_delta": sum(deltas) / len(deltas) if deltas else None,
                "mean_delta_ci95": paired_mean_ci(deltas, seed=seed),
                "median_delta": inference.percentile(deltas, 0.5) if deltas else None,
                "wins": wins,
                "losses": losses,
                "ties": len(deltas) - wins - losses,
                "all_zero": bool(deltas) and not wins and not losses,
                "p_wilcoxon": None,
                "p_sign": None,
                "p_adjusted": None,
                "p_sign_adjusted": None,
                "significant": None,
                "skipped": "below_min_pairs" if len(deltas) < MIN_PAIRS else None,
            }
            if len(deltas) >= MIN_PAIRS:
                row["_p"] = 1.0 if row["all_zero"] else wilcoxon_p(deltas)
                row["_sign"] = 1.0 if row["all_zero"] else sign_test_p(wins, losses)
                tested.append(row)
            comparisons.append(row)
        families.append(
            {
                "metric": metric,
                "role": "exploratory",
                "status": "exploratory",
                "alternative": "two-sided",
                "comparisons": comparisons,
            },
        )
    raw = [r.pop("_p") for r in tested]
    signs = [r.pop("_sign") for r in tested]
    for row, p, sign, adjusted, sign_adjusted in zip(
        tested,
        raw,
        signs,
        inference.holm(raw, digits=None),
        inference.holm(signs, digits=None),
        strict=True,
    ):
        row.update(
            p_wilcoxon=inference.round_p(p),
            p_sign=inference.round_p(sign),
            p_adjusted=inference.round_p(adjusted),
            p_sign_adjusted=inference.round_p(sign_adjusted),
            significant=adjusted is not None and adjusted < 0.05,
        )
    cross_notes = (
        [
            (
                "Cross-study sample: each group's run of a task is its newest usable run, possibly from another "
                "experiment, condition or time than the other group's; pairs match on the task only."
            ),
        ]
        if cross_study
        else []
    )
    return {
        "status": "exploratory",
        "baseline_condition": f"{'group' if cross_study else 'condition'} A (the right-hand member of B vs A)",
        "alpha": 0.05,
        "correction": "holm",
        "multiplicity": "all",
        "minimum_pairs": MIN_PAIRS,
        "family_size": inference.family_size(raw),
        "comparisons_tested": len(tested),
        "families": families,
        "effect_interval": (
            f"95% paired-app percentile bootstrap, seed {seed}; not multiplicity-adjusted; absent below 3 apps"
        ),
        "notes": [
            *cross_notes,
            "Post hoc comparisons of recorded condition systems; they do not isolate causal model effects.",
            "Only shared unit/repeat cells are paired; missing counterparts are excluded with counts.",
            "Wilcoxon p-values are Holm-adjusted jointly across every tested metric and condition pair. "
            "Sign-test sensitivity p-values form a separate Holm family and do not select the primary conclusion.",
            "Fewer than five paired units: effects may be shown, but significance tests are withheld.",
            "Paired differences are rounded to twelve decimals before ranking to stabilize floating-point ties.",
            "All-zero differences receive p=1 and remain in the eligible correction family; this is not evidence "
            "of equivalence.",
            "Signed ranks use the exact conditional sign-flip distribution through 50 nonzero pairs (tied "
            "midranks retained), then a tie/continuity-corrected normal approximation. The null requires "
            "independent symmetric task differences; the sign test does not require symmetry.",
            (
                "Final assessment: the rates tested are whole-cohort (known output/build/start failures counted as "
                "0, unknown outcomes left out) and cost is at list price; the conditional rates and the billed cost "
                "are reported descriptively but not tested (billed cost does not compare billing routes)."
                if final_assessment
                else "deploy_rate is conditional on measured evaluation; end_to_end_deploy_rate includes known final "
                "failures."
            ),
        ],
    }
