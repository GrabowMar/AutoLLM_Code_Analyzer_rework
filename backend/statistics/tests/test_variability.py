"""Run-to-run variability: hand-computed estimators and the experiment payload on a synthetic design."""

from __future__ import annotations

import math

import pytest
from django.core.cache import cache

from backend.generation.models import Experiment
from backend.generation.models import GenerationJob
from backend.generation.services.experiment_results import ResultFilters
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.statistics.services import variability as v
from backend.users.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestDescribe:
    def test_hand_computed(self):
        d = v.describe([1, 2, 3, 4], ratio=True)
        assert d["n"] == 4
        assert d["mean"] == 2.5
        assert d["sd"] == pytest.approx(math.sqrt(5 / 3), abs=1e-6)  # n-1 denominator
        assert (d["min"], d["max"], d["range"]) == (1, 4, 3)
        assert d["iqr"] == pytest.approx(1.5)  # 3.25 - 1.75 (linear interpolation)
        assert d["cv"] == pytest.approx(math.sqrt(5 / 3) / 2.5, abs=1e-4)

    def test_small_n_and_scales(self):
        d = v.describe([2, 4], ratio=True)
        assert d["sd"] == pytest.approx(math.sqrt(2))
        assert d["iqr"] is None  # IQR only from 4 values
        # Not ratio scale (a pass rate): no CV.
        assert v.describe([0.5, 1.0], ratio=False)["cv"] is None
        # One value: no SD; missing values are skipped, never zero.
        assert v.describe([3, None], ratio=True)["sd"] is None
        assert v.describe([None, None], ratio=True) == {
            "n": 0,
            "mean": None,
            "sd": None,
            "min": None,
            "max": None,
            "range": None,
            "iqr": None,
            "cv": None,
        }

    def test_pooled_sd(self):
        # Groups [1, 3] (SS 2, df 1) and [2, 2, 5] (SS 6, df 2): sqrt(8 / 3).
        sd, df = v.pooled_sd([[1, 3], [2, 2, 5], [7]])
        assert sd == pytest.approx(math.sqrt(8 / 3))
        assert df == 3
        assert v.pooled_sd([[1], [2]]) == (None, 0)


# Balanced 2 conditions x 2 apps x 2 repeats; SS: C 24.5, A 40.5, CxA 0.5, E 14 (total 79.5).
BALANCED = {
    ("c1", "a1"): [1, 3],
    ("c1", "a2"): [5, 7],
    ("c2", "a1"): [4, 6],
    ("c2", "a2"): [8, 12],
}


class TestVarianceComponents:
    def test_balanced_hand_computed(self):
        res = v.variance_components(BALANCED)
        comp = {c["source"]: c for c in res["components"]}
        assert res["estimable"] is True
        assert res["balanced"] is True
        assert res["r_harmonic"] == 2
        # MS_E = 14 / 4; MS_CA = 0.5; MS_A = 40.5; MS_C = 24.5.
        assert comp["repeat"]["variance"] == pytest.approx(3.5)
        assert comp["condition"]["variance"] == pytest.approx((24.5 - 0.5) / 4)
        assert comp["app"]["variance"] == pytest.approx((40.5 - 0.5) / 4)
        # (0.5 - 3.5) / 2 < 0: truncated at 0 and flagged.
        assert comp["condition_x_app"]["variance"] == 0
        assert comp["condition_x_app"]["raw"] == pytest.approx(-1.5)
        assert comp["condition_x_app"]["truncated"] is True
        assert res["truncated"] == ["condition_x_app"]
        assert res["total"] == pytest.approx(19.5)
        assert comp["condition"]["share"] == pytest.approx(6 / 19.5, abs=1e-4)
        assert comp["app"]["share"] == pytest.approx(10 / 19.5, abs=1e-4)
        assert comp["repeat"]["share"] == pytest.approx(3.5 / 19.5, abs=1e-4)
        assert sum(c["share"] for c in res["components"]) == pytest.approx(1, abs=1e-3)
        assert res["icc_within_condition"] == pytest.approx(10 / 13.5, abs=1e-4)
        assert [c["df"] for c in res["components"]] == [1, 1, 1, 4]

    def test_one_condition_is_one_way(self):
        res = v.variance_components({("c", "a1"): [1, 3], ("c", "a2"): [5, 7]})
        comp = {c["source"]: c for c in res["components"]}
        assert set(comp) == {"app", "repeat"}
        # MS_A = 2 * (2^2 + 2^2) / 1 = 16, MS_E = 2: (16 - 2) / 2 = 7.
        assert comp["app"]["variance"] == pytest.approx(7)
        assert comp["repeat"]["variance"] == pytest.approx(2)

    def test_incomplete_crossing_drops_apps_and_no_repeats_is_not_estimable(self):
        res = v.variance_components({**BALANCED, ("c1", "a3"): [9, 9]})
        assert (res["n_apps"], res["apps_dropped"]) == (2, 1)
        single = v.variance_components({("c1", "a1"): [1], ("c2", "a1"): [2]})
        assert single["estimable"] is False
        assert single["reason"] == "no cell has 2 or more repeats"
        # Repeats exist, but no app was run under every condition: say so, not "no repeats".
        disjoint = v.variance_components({("c1", "a1"): [1, 2], ("c2", "a2"): [3, 4]})
        assert disjoint["estimable"] is False
        assert disjoint["reason"] == "no app has values under every condition"

    def test_ceiling(self):
        res = v.variance_components(
            {("c1", "a1"): [1, 1], ("c2", "a1"): [1, 1], ("c1", "a2"): [1, 1], ("c2", "a2"): [1, 1]},
        )
        assert res["ceiling"] is True
        assert all(c["share"] is None for c in res["components"])
        assert v.precision_plan(res)["available"] is False


class TestICC:
    def test_balanced_hand_computed(self):
        res = v.icc_oneway([[1, 3], [5, 7], [4, 6]])
        # MSB = 8.6667, MSW = 2, r0 = 2 -> (8.6667 - 2) / (8.6667 + 2) = 0.625.
        assert res["icc"] == pytest.approx(0.625, abs=1e-4)
        assert res["r0"] == 2
        assert res["design_effect"] == pytest.approx(1.625, abs=1e-4)
        assert res["effective_n"] == pytest.approx(6 / 1.625, abs=1e-2)
        assert res["truncated"] is False

    def test_unbalanced_r0(self):
        res = v.icc_oneway([[1, 3], [5, 7, 9]])
        # MSB = 30, MSW = 10/3, r0 = (5 - 13/5) / 1 = 2.4.
        assert res["r0"] == pytest.approx(2.4)
        assert res["icc"] == pytest.approx((30 - 10 / 3) / (30 + 1.4 * 10 / 3), abs=1e-4)

    def test_negative_is_truncated_and_degenerate_cases(self):
        res = v.icc_oneway([[1, 9], [2, 8]])
        assert res["icc"] == 0
        assert res["icc_raw"] < 0
        assert res["truncated"] is True
        assert v.icc_oneway([[1, 2]])["reason"] == "fewer than 2 apps"
        assert v.icc_oneway([[1], [2]])["reason"] == "no app has 2 or more repeats"
        assert v.icc_oneway([[1, 1], [1, 1]])["icc"] is None

    def test_bootstrap_ci_is_deterministic_and_needs_three_apps(self):
        groups = [[1, 1.2], [5, 5.1], [9, 8.7], [3, 3.3]]
        ci = v.icc_bootstrap_ci(groups, seed=7, draws=300)
        assert ci == v.icc_bootstrap_ci(groups, seed=7, draws=300)
        assert 0 <= ci[0] <= ci[1] <= 1
        assert ci[1] > 0.9  # repeats agree far more than apps do
        assert v.icc_bootstrap_ci(groups[:2], seed=7) is None


class TestPassAtK:
    def test_hand_computed(self):
        assert v.pass_at_k(3, 2, 1) == pytest.approx(2 / 3)
        assert v.pass_at_k(3, 2, 2) == 1.0  # only one failure: any 2 repeats include a pass
        assert v.pass_at_k(5, 1, 2) == pytest.approx(1 - 6 / 10)
        assert v.pass_hat_k(3, 2, 1) == pytest.approx(2 / 3)
        assert v.pass_hat_k(3, 2, 2) == pytest.approx(1 / 3)
        assert v.pass_hat_k(3, 2, 3) == 0
        assert v.pass_at_k(2, 0, 2) == 0
        assert v.pass_at_k(2, 1, 3) is None


class TestRankIntervals:
    def test_separated_conditions_have_certain_ranks(self):
        data = {
            1: {a: [0.9, 0.9] for a in range(4)},
            2: {a: [0.5, 0.5] for a in range(4)},
            3: {a: [0.1, 0.1] for a in range(4)},
        }
        res = v.rank_intervals(data, higher_is_better=True, seed=1, draws=200)
        rows = {r["condition_id"]: r for r in res["rows"]}
        assert rows[1]["rank"] == 1
        assert rows[1]["rank_ci95"] == [1, 1]
        assert rows[1]["p_rank_1"] == 1.0
        assert rows[3]["p_top_3"] == 1.0
        lower = v.rank_intervals(data, higher_is_better=False, seed=1, draws=200)
        assert {r["condition_id"]: r["rank"] for r in lower["rows"]}[3] == 1

    def test_ties_share_the_best_rank_and_too_few_apps(self):
        data = {1: {a: [1.0] for a in range(3)}, 2: {a: [1.0] for a in range(3)}}
        res = v.rank_intervals(data, higher_is_better=True, seed=1, draws=50)
        assert all(r["rank"] == 1 and r["p_rank_1"] == 1.0 for r in res["rows"])
        few = v.rank_intervals({1: {0: [1]}, 2: {0: [0]}}, higher_is_better=True, seed=1)
        assert few["rows"] == []
        assert "fewer than 3 apps" in few["reason"]

    def test_rank_interval_bounds_are_integer_ranks(self):
        # Interpolated percentiles gave bounds such as 2.0249999999999773 (shown verbatim in Table 6).
        import random

        rng = random.Random(1)  # noqa: S311 - reproducible test data
        for trial in range(8):
            data = {c: {a: [rng.gauss(c * 0.1, 1) for _ in range(2)] for a in range(4)} for c in range(3)}
            for row in v.rank_intervals(data, higher_is_better=True, seed=trial)["rows"]:
                lo, hi = row["rank_ci95"]
                assert isinstance(lo, int)
                assert isinstance(hi, int)
                assert 1 <= lo <= row["median_rank"] <= hi <= 3


class TestPrecisionPlan:
    def test_hand_computed_half_widths(self):
        plan = v.precision_plan(v.variance_components(BALANCED), apps_options=(5,), repeats_options=(1, 2))
        assert plan["available"] is True
        r1, r2 = plan["rows"]
        # s_b = app 10 + interaction 0, s_w = 3.5.
        assert r1["mean_half_width"] == pytest.approx(v.Z95 * math.sqrt(10 / 5 + 3.5 / 5), abs=1e-5)
        assert r2["mean_half_width"] == pytest.approx(v.Z95 * math.sqrt(10 / 5 + 3.5 / 10), abs=1e-5)
        assert r1["delta_half_width"] == pytest.approx(v.Z95 * math.sqrt((0 + 7) / 5), abs=1e-5)

    def test_exact_test_floor(self):
        assert v.min_apps_for_exact_test(0.05) == 6  # 2^(1-6) = 0.03125


# ── experiment payload ──


def _job(experiment, condition, app, repeat, *, pass_rate, cost, files, passed=None, functional=None, **extra):
    passed = pass_rate == 1.0 if passed is None else passed
    return GenerationJob.objects.create(
        mode="scaffolding",
        created_by=experiment.created_by,
        experiment=experiment,
        condition=condition,
        app_requirement=app,
        repeat_index=repeat,
        status=GenerationJob.Status.COMPLETED,
        metrics={
            # A conformance-suite verdict, as evaluate_job stores it (``functional`` overrides it).
            "functional": functional
            or {
                "source": "conformance",
                "status": "passed" if passed else "failed",
                "pass_rate": pass_rate,
                "passed": passed,
            },
            "cost": cost,
            "total_tokens": int(cost * 1e6),
            "llm_calls": 10 + repeat,
        },
        result_data={"files": files},
        **extra,
    )


def _design(status=Experiment.Status.COMPLETED, n_apps=3):
    user = UserFactory()
    experiment = ExperimentFactory(created_by=user, mode="scaffolding", repeats=2, status=status)
    apps = [AppRequirementTemplateFactory() for _ in range(n_apps)]
    experiment.app_requirements.set(apps)
    conditions = [ExperimentConditionFactory(experiment=experiment, model=LLMModelFactory()) for _ in range(2)]
    return experiment, conditions, apps


@pytest.mark.django_db
class TestExperimentPayload:
    def _populate(self, experiment, conditions, apps):
        a, b = conditions
        for i, app in enumerate(apps):
            for r in range(2):
                _job(
                    experiment,
                    a,
                    app,
                    r,
                    pass_rate=1.0,
                    cost=0.1 + 0.01 * r,
                    files={"app.py": f"value = {i}\nother = {r}\n", "util.py": "x = 1\n"},
                )
                _job(
                    experiment,
                    b,
                    app,
                    r,
                    pass_rate=0.5 if r else 1.0,
                    cost=0.2 + 0.1 * r,
                    files={"app.py": f"different = {i}\nother = {r}\n"},
                )

    def test_dispersion_components_and_blocks(self):
        experiment, (a, b), apps = _design()
        self._populate(experiment, (a, b), apps)
        res = v.experiment_variability(experiment)
        assert res["interim"] is False
        assert res["design"] == {
            "n_conditions": 2,
            "n_apps": 3,
            "n_cells": 6,
            "n_runs": 12,
            "cells_with_repeats": 6,
            "repeats_min": 2,
            "repeats_max": 2,
            "repeats_median": 2,
            "planned_repeats": 2,
        }
        rows = {r["condition_id"]: r for r in res["conditions"]}
        # Condition a: cost 0.10 / 0.11 in every cell -> within-cell SD = 0.01 / sqrt(2).
        cost_a = rows[a.id]["dispersion"]["cost"]
        assert cost_a["pooled_sd"] == pytest.approx(0.01 / math.sqrt(2), abs=1e-6)
        assert cost_a["df"] == 3
        assert cost_a["median_cv"] == pytest.approx((0.01 / math.sqrt(2)) / 0.105, abs=1e-3)
        # Pass rate is not ratio-scale: no CV; a passes everywhere (a ceiling within a), b disagrees in every cell.
        assert rows[a.id]["dispersion"]["pass_rate"]["median_cv"] is None
        assert rows[a.id]["pass_agreement"] == {"cells": 3, "agree": 3, "share": 1.0}
        assert rows[b.id]["pass_agreement"] == {"cells": 3, "agree": 0, "share": 0.0}
        # Unanalyzed: code issues are absent (unmeasured), never 0.
        assert "code_issues" not in rows[a.id]["dispersion"]
        assert {m["key"]: m["n_runs"] for m in res["metrics"]}["code_issues"] == 0
        # Every block is there and carries the interim flag.
        assert res["components"]["cost"]["estimable"] is True
        assert res["components"]["cost"]["interim"] is False
        icc_cost = {r["condition_id"]: r for r in res["icc"]["cost"]}
        assert icc_cost[a.id]["n_apps"] == 3
        assert icc_cost[a.id]["ci95"] is None or len(icc_cost[a.id]["ci95"]) == 2
        pass_b = next(r for r in res["pass_at_k"]["conditions"] if r["condition_id"] == b.id)
        assert [(k["k"], k["pass_at_k"], k["pass_hat_k"]) for k in pass_b["by_k"]] == [(1, 0.5, 0.5), (2, 1.0, 0.0)]
        assert pass_b["by_k"][0]["n_cells"] == 3
        assert pass_b["by_k"][0]["pass_at_k_ci95"] == [0.5, 0.5]
        # Similarity: 3 within-cell pairs per condition; references pair a and b on each (app, repeat).
        sim_rows = {r["condition_id"]: r for r in res["similarity"]["conditions"]}
        assert sim_rows[a.id]["within"]["n_pairs"] == 3
        assert sim_rows[a.id]["reference"]["n_pairs"] == 6
        assert sim_rows[a.id]["within"]["median_path_jaccard"] == 1.0
        assert sim_rows[a.id]["within"]["median_identical_files"] == 1  # util.py
        assert sim_rows[a.id]["within"]["median_line_jaccard"] == 0.5  # {value, other=0, x} vs {value, other=1, x}
        assert sim_rows[a.id]["reference"]["median_path_jaccard"] == 0.5
        # Not analyzed: no finding overlap at all (unmeasured, not 0).
        assert res["finding_overlap"]["measured"] is False
        assert all(c["finding_overlap"] is None for c in res["cells"])
        # Ranks: pass rate a = 1.0 > b = 0.75 with 3 shared apps.
        ranks = {r["condition_id"]: r for r in res["rank_stability"]["pass_rate"]["rows"]}
        assert ranks[a.id]["rank"] == 1
        assert ranks[a.id]["p_rank_1"] == 1.0
        # Terminal: the planner is there; pass rate within a condition is not at a ceiling overall.
        assert res["planner"] is not None
        assert res["planner"]["metrics"]["cost"]["available"] is True
        assert res["planner_reason"] is None
        assert v.NOTE_TWO_REPEATS in res["notes"]
        # Deterministic and cached.
        assert v.experiment_variability(experiment) == res
        cache.clear()
        again = v.experiment_variability(experiment)
        assert again["icc"] == res["icc"]
        assert again["rank_stability"] == res["rank_stability"]

    def test_running_experiment_withholds_planner_and_flags_interim(self):
        experiment, conditions, apps = _design(status=Experiment.Status.RUNNING)
        self._populate(experiment, conditions, apps)
        res = v.experiment_variability(experiment)
        assert res["interim"] is True
        assert res["notes"][0] == v.NOTE_INTERIM
        assert res["planner"] is None
        assert "optional-stopping" in res["planner_reason"]
        assert res["pass_at_k"]["interim"] is True
        assert res["similarity"]["interim"] is True
        assert all(row["interim"] for rows in res["icc"].values() for row in rows)

    def test_pass_rate_follows_the_results_tab_and_names_a_smoke_check(self):
        """Experiment cells count pass rate as the Results tab does; a smoke-check value is named, not hidden."""
        from backend.generation.services import experiment_results as er

        experiment, (a, b), apps = _design()
        self._populate(experiment, (a, b), apps)
        # An older job whose metrics.functional is the generation-time smoke check (no source), 1.0.
        smoke = GenerationJob.objects.get(condition=b, app_requirement=apps[0], repeat_index=1)
        smoke.metrics = {
            **smoke.metrics,
            "functional": {"passed": True, "pass_rate": 1.0, "health_ok": True, "endpoints_ok": 2},
        }
        smoke.save(update_fields=["metrics", "updated_at"])
        res = v.experiment_variability(experiment)
        results = er.compute(experiment, None, None, experiment.created_by)
        cells = {(c["condition_id"], c["app_id"]): c for c in res["cells"]}
        assert cells[(b.id, apps[0].id)]["values"]["pass_rate"] == [1.0, 1.0]
        assert cells[(b.id, apps[0].id)]["k_passed"] == 2
        # Same numbers as the Results tab: per condition, the mean of app means.
        for row in results["leaderboard"]:
            means = [cells[(row["condition_id"], app.id)]["stats"]["pass_rate"]["mean"] for app in apps]
            assert round(sum(means) / len(means), 4) == row["pass_rate_mean"]
        assert v.NOTE_NOT_CONFORMANCE.format(n=1, total=12) in res["notes"]

    def test_kept_cells_follow_the_results_filters(self):
        experiment, (a, b), apps = _design()
        self._populate(experiment, (a, b), apps)
        # b's cell on app 0, repeat 1 truncated: excluded for every condition by the common-cells rule.
        job = GenerationJob.objects.get(condition=b, app_requirement=apps[0], repeat_index=1)
        job.result_data = {**job.result_data, "backend_truncated": True}
        job.save(update_fields=["result_data"])
        kept, _ctx = v.kept_cells(experiment)
        assert len(kept) == 10
        assert all(not (c.app_id == apps[0].id and c.repeat == 1) for c in kept)
        kept_all, _ctx = v.kept_cells(experiment, ResultFilters(common_cells=False))
        assert len(kept_all) == 11
        kept_trunc, _ctx = v.kept_cells(experiment, ResultFilters(exclude_truncated=False))
        assert len(kept_trunc) == 12
        with pytest.raises(ValueError, match="fingerprint"):
            v.kept_cells(experiment, ResultFilters(fingerprint="nope"))

    def test_csv_has_one_row_per_cell(self):
        experiment, conditions, apps = _design()
        self._populate(experiment, conditions, apps)
        res = v.experiment_variability(experiment)
        lines = v.experiment_variability_csv(res).strip().splitlines()
        assert len(lines) == 1 + 6
        header = lines[0].split(",")
        assert "cost_sd" in header
        assert "within_shingle_jaccard" in header
        # Unmeasured metrics get no columns; missing values stay empty, never 0.
        assert "code_issues_mean" not in header

    def test_cached_payload_follows_backfills_and_results_finished_in_place(self):
        from backend.analysis.models import AnalysisRun
        from backend.analysis.models import ToolResult
        from backend.analysis.tests.factories import AnalysisRunFactory
        from backend.analysis.tests.factories import ToolResultFactory

        experiment, conditions, apps = _design()
        self._populate(experiment, conditions, apps)
        first = v.experiment_variability(experiment)
        assert not any(m["key"].startswith("behaviour.") for m in first["metrics"])
        # A backfill adds metrics.behaviour with a queryset update: updated_at does not move.
        jobs = list(GenerationJob.objects.filter(experiment=experiment))
        stamps = {j.id: j.updated_at for j in jobs}
        for i, job in enumerate(jobs):
            GenerationJob.objects.filter(id=job.id).update(metrics={**job.metrics, "behaviour": {"builds": i % 3}})
        assert {j.id: j.updated_at for j in GenerationJob.objects.filter(experiment=experiment)} == stamps
        second = v.experiment_variability(experiment)
        assert {m["key"]: m["n_runs"] for m in second["metrics"]}["behaviour.builds"] == 12
        # An analysis is created running and finished in place: no new row, the payload must still refresh.
        job = jobs[0]
        run = AnalysisRunFactory(
            created_by=job.created_by,
            generation_job=job,
            status=AnalysisRun.Status.RUNNING,
            source_code=None,
            tool_slugs=["ruff"],
        )
        result = ToolResultFactory(run=run, tool_slug="ruff", status=ToolResult.Status.RUNNING)
        running = v.experiment_variability(experiment)["issue_coverage"]
        assert running is None or running["apps_measured"] == 0
        ToolResult.objects.filter(id=result.id).update(status=ToolResult.Status.COMPLETED)
        AnalysisRun.objects.filter(id=run.id).update(status=AnalysisRun.Status.COMPLETED)
        assert v.experiment_variability(experiment)["issue_coverage"]["apps_measured"] == 1

    def test_subscription_billed_cost_is_not_a_floor(self):
        experiment, (a, b), apps = _design()
        for i, app in enumerate(apps):
            for r in range(2):
                for cond in (a, b):
                    job = _job(experiment, cond, app, r, pass_rate=1.0, cost=0.0, files={"app.py": f"x = {i}\n"})
                    job.metrics = {**job.metrics, "list_cost": 0.4 + 0.1 * r + 0.01 * i, "billing_source": "go"}
                    job.save(update_fields=["metrics", "updated_at"])
        res = v.experiment_variability(experiment)
        n_runs = {m["key"]: m["n_runs"] for m in res["metrics"]}
        assert n_runs["list_cost"] == 12
        assert res["components"]["list_cost"]["estimable"] is True
        # The $0 billed cost is explained by the billing note, not reported as a ceiling/floor effect.
        assert any(n.startswith("12 of 12 kept runs were subscription-billed (go)") for n in res["notes"])
        assert not any(n.startswith("Cost: every kept run scored 0") for n in res["notes"])
        assert "List-price cost" not in " ".join(n for n in res["notes"] if "every kept run" in n)
        # Nor in the markers: no ceiling anywhere for cost, a billing marker instead (the UI says why).
        assert "cost" not in res["ceilings"]
        for row in res["conditions"]:
            assert row["dispersion"]["cost"]["ceiling"] is False
            assert row["dispersion"]["cost"]["billing"] == ["go"]
            assert row["dispersion"]["cost"]["pooled_sd"] == 0.0
            assert row["dispersion"]["list_cost"]["billing"] is None
        assert res["billing"] == {"sources": ["go"], "n_runs": 12, "n_kept": 12, "metrics": ["cost"]}
        assert (res["components"]["cost"]["ceiling"], res["components"]["cost"]["billing"]) == (False, ["go"])
        cost_plan = res["planner"]["metrics"]["cost"]
        assert cost_plan["available"] is False
        assert "subscription-billed (go)" in cost_plan["reason"]

    def test_a_free_zero_cost_next_to_billed_runs_is_still_a_floor(self):
        """Billing explains the $0 only when it covers every run: a $0 run that was not billed is a measured floor."""
        experiment, (a, b), apps = _design()
        for i, app in enumerate(apps):
            for r in range(2):
                for cond in (a, b):
                    job = _job(experiment, cond, app, r, pass_rate=1.0, cost=0.0, files={"app.py": f"x = {i}\n"})
                    if cond == a:
                        job.metrics = {**job.metrics, "billing_source": "go"}
                        job.save(update_fields=["metrics", "updated_at"])
        res = v.experiment_variability(experiment)
        rows = {r["condition_id"]: r for r in res["conditions"]}
        assert (rows[a.id]["dispersion"]["cost"]["ceiling"], rows[a.id]["dispersion"]["cost"]["billing"]) == (
            False,
            ["go"],
        )
        assert (rows[b.id]["dispersion"]["cost"]["ceiling"], rows[b.id]["dispersion"]["cost"]["billing"]) == (
            True,
            None,
        )
        assert "cost" in res["ceilings"]
        assert res["billing"]["metrics"] == []

    def test_single_trial_design(self):
        experiment, (a, b), apps = _design()
        Experiment.objects.filter(id=experiment.id).update(repeats=1)
        experiment.refresh_from_db()
        for app in apps:
            _job(experiment, a, app, 0, pass_rate=1.0, cost=0.1, files={"app.py": "x = 1\n"})
            _job(experiment, b, app, 0, pass_rate=0.0, cost=0.2, files={"app.py": "y = 2\n"})
        res = v.experiment_variability(experiment)
        assert res["design"]["cells_with_repeats"] == 0
        assert v.NOTE_SINGLE_TRIAL in res["notes"]
        assert res["components"]["cost"]["estimable"] is False
        assert all(r["dispersion"]["cost"]["pooled_sd"] is None for r in res["conditions"])
