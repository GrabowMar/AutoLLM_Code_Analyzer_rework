"""Tests for rankings service & API."""

from __future__ import annotations

import pytest

from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings import services
from backend.rankings.models import BenchmarkResult
from backend.rankings.services.constants import EMPIRICAL_DENSITY_CAP
from backend.users.tests.factories import UserFactory

CAP = EMPIRICAL_DENSITY_CAP

pytestmark = pytest.mark.django_db


def test_normalize_benchmark_score_clamps():
    assert services.normalize_benchmark_score("humaneval", 50.0) == 0.5
    assert services.normalize_benchmark_score("humaneval", 150.0) == 1.0
    assert services.normalize_benchmark_score("humaneval", -10.0) == 0.0
    assert services.normalize_benchmark_score("webdev_elo", 1100) == 0.5


def test_compute_benchmark_score_weighted_average():
    entry = {
        "bfcl_score": 80.0,
        "webdev_elo": 1200,
        "livebench_coding": 60.0,
    }
    score = services.compute_benchmark_score(entry)
    assert 0.0 <= score <= 1.0
    assert score > 0


def test_compute_benchmark_score_zero_when_empty():
    assert services.compute_benchmark_score({}) == 0.0


def test_compute_cost_efficiency_free_model_high_with_long_context():
    score = services.compute_cost_efficiency_score(
        {"is_free": True, "benchmark_score": 1.0, "context_length": 1_000_000},
    )
    # Max possible = 0.7 (price_eff) + 0.3 (ctx_bonus at very large context)
    assert 0.85 <= score <= 1.0


def test_compute_cost_efficiency_zero_without_pricing():
    assert services.compute_cost_efficiency_score({"benchmark_score": 0.5}) == 0.0


def test_compute_accessibility_score_defaults():
    score = services.compute_accessibility_score({})
    assert score == pytest.approx(0.7, rel=0, abs=0.01)


def test_compute_accessibility_score_with_inputs():
    score = services.compute_accessibility_score(
        {
            "license_type": "apache",
            "api_stability": "stable",
            "documentation_quality": "comprehensive",
        },
    )
    assert score == pytest.approx(1.0)


def test_compute_adoption_score_from_rank():
    assert services.compute_adoption_score({"openrouter_programming_rank": 1}) == 1.0
    assert services.compute_adoption_score(
        {"openrouter_programming_rank": 5},
    ) == pytest.approx(0.84)


def test_compute_adoption_score_falls_back_to_local_apps():
    assert services.compute_adoption_score({"apps": 0}) == 0.0
    high = services.compute_adoption_score({"apps": 100})
    assert 0.0 < high <= 1.0


def test_compute_mss_weighted_sum():
    entry = {
        "adoption_score": 1.0,
        "benchmark_score": 1.0,
        "cost_efficiency_score": 1.0,
        "accessibility_score": 1.0,
    }
    assert services.compute_mss(entry) == pytest.approx(1.0)


def test_aggregate_rankings_pulls_models_with_local_stats():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o", model_name="GPT-4o")
    GenerationJobFactory.create_batch(
        2,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        duration_seconds=30.0,
    )
    BenchmarkResult.objects.create(
        model_id=model.model_id,
        benchmark="humaneval",
        score=90.0,
    )

    rankings = services.aggregate_rankings()

    assert any(r["model_id"] == model.model_id for r in rankings)
    row = next(r for r in rankings if r["model_id"] == model.model_id)
    assert row["apps"] == 2
    assert row["apps_completed"] == 2
    assert row["adoption_score"] > 0  # from local apps fallback
    assert row["mss_score"] > 0
    assert row["composite_score"] == row["mss_score"]


def test_aggregate_rankings_prompt_hash_filter_scopes_local_stats():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o", model_name="GPT-4o")
    GenerationJobFactory.create_batch(
        2,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        prompt_hash="hash-a",
    )
    GenerationJobFactory.create_batch(
        3,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        prompt_hash="hash-b",
    )

    all_rankings = services.aggregate_rankings()
    scoped = services.aggregate_rankings(prompt_hash="hash-a")

    all_row = next(r for r in all_rankings if r["model_id"] == model.model_id)
    scoped_row = next(r for r in scoped if r["model_id"] == model.model_id)
    assert all_row["apps"] == 5
    assert scoped_row["apps"] == 2


def test_aggregate_rankings_bundle_key_filter_scopes_local_stats():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o")
    GenerationJobFactory.create_batch(
        2,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        bundle_key="system-scaffolding-standard@1",
    )
    GenerationJobFactory.create_batch(
        4,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        bundle_key="system-scaffolding-standard@2",
    )

    scoped = services.aggregate_rankings(bundle_key="system-scaffolding-standard@2")

    scoped_row = next(r for r in scoped if r["model_id"] == model.model_id)
    assert scoped_row["apps"] == 4


def test_aggregate_rankings_experiment_id_filter_scopes_local_stats():
    from backend.generation.tests.factories import ExperimentFactory

    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o")
    experiment = ExperimentFactory(created_by=user)
    GenerationJobFactory.create_batch(
        2,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        experiment=experiment,
    )
    GenerationJobFactory.create_batch(
        5,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
    )

    all_rankings = services.aggregate_rankings()
    scoped = services.aggregate_rankings(experiment_id=str(experiment.id))

    all_row = next(r for r in all_rankings if r["model_id"] == model.model_id)
    scoped_row = next(r for r in scoped if r["model_id"] == model.model_id)
    assert all_row["apps"] == 7
    assert scoped_row["apps"] == 2


def test_aggregate_rankings_no_filter_matches_unfiltered_totals():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o")
    GenerationJobFactory.create_batch(
        3,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
    )

    unfiltered = services.aggregate_rankings()
    explicitly_unfiltered = services.aggregate_rankings(prompt_hash=None, bundle_key=None)

    row_a = next(r for r in unfiltered if r["model_id"] == model.model_id)
    row_b = next(r for r in explicitly_unfiltered if r["model_id"] == model.model_id)
    assert row_a["apps"] == row_b["apps"] == 3


def test_aggregate_rankings_counts_only_latest_run_per_job():
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import Finding
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import ToolResultFactory

    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o", model_name="GPT-4o")
    job = GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED)

    for n in (5, 2):  # stale run first, latest run second
        run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
        result = ToolResultFactory(run=run, tool_slug="bandit")
        for _ in range(n):
            Finding.objects.create(result=result, severity="high", title="f")

    row = next(r for r in services.aggregate_rankings() if r["model_id"] == model.model_id)
    assert row["findings"]["high"] == 2


def test_compute_empirical_quality_none_without_local_data():
    from backend.rankings.services.scoring import compute_empirical_quality

    assert compute_empirical_quality({"apps_completed": 0}) is None
    assert compute_empirical_quality({"apps_completed": 1, "local_loc": 0}) is None


def test_compute_empirical_quality_blends_density_and_functional():
    from backend.rankings.services.scoring import compute_empirical_quality

    entry = {
        "apps_completed": 1,
        "local_loc": 1000,
        # weighted = 2*5 + 5*1 = 15 → density 15/KLOC → density_score 1 - 15/cap
        "findings": {"critical": 0, "high": 2, "medium": 0, "low": 5, "info": 0},
        "functional_pass_rate": 1.0,
    }
    density_score = 1 - 15 / CAP
    assert compute_empirical_quality(entry) == pytest.approx(0.6 * density_score + 0.4 * 1.0, abs=1e-4)

    entry["functional_pass_rate"] = None
    assert compute_empirical_quality(entry) == pytest.approx(density_score, abs=1e-4)


def test_density_cap_is_calibrated_for_code_only_densities():
    """Code-only densities are ~5-30/KLOC: the scale must spread them, not squeeze them near 1."""
    from backend.rankings.services.scoring import compute_empirical_quality

    def score(weighted_per_kloc):
        entry = {"apps_completed": 1, "local_loc": 1000, "findings": {"low": weighted_per_kloc}}
        return compute_empirical_quality(entry)

    # 10/KLOC (a typical 13-tool code-only density) and 30/KLOC (a 29-tool one).
    assert score(10) == pytest.approx(0.8)
    assert score(30) == pytest.approx(0.4)
    assert score(80) == 0.0


def test_compute_composite_blend_and_fallback():
    from backend.rankings.services.scoring import compute_composite

    assert compute_composite(0.5, None) == 0.5
    assert compute_composite(0.5, 1.0) == pytest.approx(0.7)  # 0.6*0.5 + 0.4*1.0


def test_aggregate_rankings_exposes_empirical_and_sorts_by_composite():
    user = UserFactory()
    good = LLMModelFactory(model_id="good/model")
    bad = LLMModelFactory(model_id="bad/model")
    for model, sev in ((good, "low"), (bad, "critical")):
        job = GenerationJobFactory(
            created_by=user,
            model=model,
            status=GenerationJob.Status.COMPLETED,
            metrics={"lines_of_code": 1000, "functional": {"passed": True, "pass_rate": 1.0}},
        )
        from backend.analysis.models import AnalysisRun
        from backend.analysis.models import Finding
        from backend.analysis.tests.factories import AnalysisRunFactory
        from backend.analysis.tests.factories import ToolResultFactory

        run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
        result = ToolResultFactory(run=run, tool_slug="bandit")
        for _ in range(10):
            Finding.objects.create(result=result, severity=sev, title="f")

    rankings = services.aggregate_rankings()
    rows = {r["model_id"]: r for r in rankings if r["model_id"] in ("good/model", "bad/model")}
    assert rows["good/model"]["empirical_quality_score"] > rows["bad/model"]["empirical_quality_score"]
    assert rows["good/model"]["composite_score"] > rows["bad/model"]["composite_score"]
    order = [r["model_id"] for r in rankings]
    assert order.index("good/model") < order.index("bad/model")


def test_filter_rankings_by_provider_and_search():
    rankings = [
        {
            "model_id": "a/1",
            "model_name": "Alpha",
            "provider": "OpenAI",
            "is_free": False,
            "context_length": 128_000,
            "price_per_million_input": 5.0,
            "mss_score": 0.7,
            "benchmark_score": 0.8,
        },
        {
            "model_id": "b/1",
            "model_name": "Beta",
            "provider": "Google",
            "is_free": True,
            "context_length": 32_000,
            "price_per_million_input": 0.0,
            "mss_score": 0.5,
            "benchmark_score": 0.0,
        },
    ]

    out = services.filter_rankings(rankings, providers=["openai"])
    assert [r["model_id"] for r in out] == ["a/1"]

    out = services.filter_rankings(rankings, search="beta")
    assert [r["model_id"] for r in out] == ["b/1"]

    out = services.filter_rankings(rankings, include_free=False)
    assert [r["model_id"] for r in out] == ["a/1"]

    out = services.filter_rankings(rankings, has_benchmarks=True)
    assert [r["model_id"] for r in out] == ["a/1"]


def test_sort_rankings_desc_and_asc():
    rankings = [
        {"model_id": "a", "mss_score": 0.5},
        {"model_id": "b", "mss_score": 0.9},
        {"model_id": "c", "mss_score": 0.1},
    ]
    desc = services.sort_rankings(rankings, sort_by="mss", sort_dir="desc")
    assert [r["model_id"] for r in desc] == ["b", "a", "c"]
    asc = services.sort_rankings(rankings, sort_by="mss", sort_dir="asc")
    assert [r["model_id"] for r in asc] == ["c", "a", "b"]


def test_get_top_models_with_custom_weights():
    LLMModelFactory(model_id="m1", model_name="M1")
    LLMModelFactory(model_id="m2", model_name="M2")
    BenchmarkResult.objects.create(
        model_id="m1",
        benchmark="bfcl_score",
        score=80.0,
    )

    top = services.get_top_models(
        count=5,
        weights={"benchmark_score": 1.0},
    )

    assert len(top) >= 2
    assert top[0]["model_id"] == "m1"


def test_get_status_counts_benchmark_rows():
    LLMModelFactory(model_id="m1")
    BenchmarkResult.objects.create(model_id="m1", benchmark="humaneval", score=80)
    BenchmarkResult.objects.create(model_id="m1", benchmark="mbpp", score=70)

    s = services.get_status()
    assert s["total_benchmark_rows"] == 2
    assert s["models_with_benchmarks"] == 1
    assert s["benchmarks"] == {"humaneval": 1, "mbpp": 1}


def test_kendall_tau_identity_reverse_and_single_swap():
    assert services.kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    assert services.kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == -1.0
    # one discordant pair out of three: (2 - 1) / 3
    assert services.kendall_tau(["a", "b", "c"], ["a", "c", "b"]) == pytest.approx(0.3333, abs=1e-4)
    # fewer than two common items cannot disagree
    assert services.kendall_tau(["a"], ["a"]) == 1.0


def test_compute_empirical_quality_custom_weights():
    from backend.rankings.services.scoring import compute_empirical_quality

    entry = {
        "apps_completed": 1,
        "local_loc": 1000,
        "findings": {"critical": 0, "high": 2, "medium": 0, "low": 5, "info": 0},
        "functional_pass_rate": None,
    }
    flat = {"critical": 1, "high": 1, "medium": 1, "low": 1, "info": 0}
    # weighted = 7 → density 7/KLOC → 1 - 7/cap
    assert compute_empirical_quality(entry, severity_weights=flat) == pytest.approx(1 - 7 / CAP, abs=1e-4)


def test_compute_weight_sensitivity_detects_adjacent_swap():
    from backend.rankings.services.scoring import compute_empirical_quality

    # crit_model: 3 criticals (weighted 30 baseline, 3 flat);
    # low_model: 25 lows (weighted 25 in both) — order flips under "flat".
    entries = []
    for model_id, findings in (
        ("crit/model", {"critical": 3, "high": 0, "medium": 0, "low": 0, "info": 0}),
        ("low/model", {"critical": 0, "high": 0, "medium": 0, "low": 25, "info": 0}),
    ):
        entry = {
            "model_id": model_id,
            "apps_completed": 1,
            "local_loc": 1000,
            "findings": findings,
            "functional_pass_rate": None,
        }
        entry["empirical_quality_score"] = compute_empirical_quality(entry)
        entries.append(entry)

    out = services.compute_weight_sensitivity(entries)

    assert out["models_evaluated"] == 2
    assert [r["model_id"] for r in out["baseline_ranking"]] == ["low/model", "crit/model"]
    flat = next(s for s in out["schemes"] if s["scheme"] == "flat")
    assert [r["model_id"] for r in flat["ranking"]] == ["crit/model", "low/model"]
    assert flat["kendall_tau"] == -1.0
    assert flat["adjacent_swaps"] == [("low/model", "crit/model")]
    # a scheme that keeps the order reports stability
    heavy = next(s for s in out["schemes"] if s["scheme"] == "security_heavy")
    assert heavy["kendall_tau"] == 1.0
    assert heavy["adjacent_swaps"] == []


def test_compute_weight_sensitivity_skips_unmeasured_models():
    out = services.compute_weight_sensitivity(
        [{"model_id": "never/ran", "empirical_quality_score": None}],
    )
    assert out["models_evaluated"] == 0
    assert out["baseline_ranking"] == []


def test_aggregate_rankings_excludes_ai_findings_from_empirical():
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import Finding
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import AnalyzerToolFactory
    from backend.analysis.tests.factories import ToolResultFactory
    from backend.rankings.services.scoring import compute_empirical_quality

    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o")
    job = GenerationJobFactory(
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        metrics={"lines_of_code": 1000, "functional": {"passed": True, "pass_rate": 1.0}},
    )
    run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    static_result = ToolResultFactory(run=run, tool_slug="bandit")
    ai_result = ToolResultFactory(run=run, tool_slug="llm_review")
    for _ in range(2):
        Finding.objects.create(result=static_result, severity="high", title="f")
    for _ in range(5):
        Finding.objects.create(result=ai_result, severity="critical", title="opinion")

    row = next(r for r in services.aggregate_rankings() if r["model_id"] == model.model_id)

    assert row["findings"] == {"critical": 0, "high": 2, "medium": 0, "low": 0, "info": 0}
    assert row["ai_findings"] == {"critical": 5, "high": 0, "medium": 0, "low": 0, "info": 0}
    # empirical score reflects only the deterministic findings
    expected = compute_empirical_quality(
        {
            "apps_completed": 1,
            "local_loc": 1000,
            "findings": row["findings"],
            "functional_pass_rate": 1.0,
        },
    )
    assert row["empirical_quality_score"] == pytest.approx(expected)


def test_aggregate_rankings_reports_trial_variance():
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import Finding
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import ToolResultFactory

    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o")
    # two trials: densities 10 and 20 per KLOC, pass rates 1.0 and 0.5
    for n_high, pass_rate in ((2, 1.0), (4, 0.5)):
        job = GenerationJobFactory(
            created_by=user,
            model=model,
            status=GenerationJob.Status.COMPLETED,
            metrics={"lines_of_code": 1000, "functional": {"passed": True, "pass_rate": pass_rate}},
        )
        run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
        result = ToolResultFactory(run=run, tool_slug="bandit")
        for _ in range(n_high):
            Finding.objects.create(result=result, severity="high", title="f")

    row = next(r for r in services.aggregate_rankings() if r["model_id"] == model.model_id)

    assert row["n_trials"] == 2
    variance = row["variance"]
    assert variance["n_jobs"] == 2
    assert variance["density_per_kloc_mean"] == pytest.approx(15.0)
    assert variance["density_per_kloc_stdev"] == pytest.approx(7.0711, abs=1e-3)
    assert variance["smoke_pass_rate_stdev"] == pytest.approx(0.3536, abs=1e-3)


def test_aggregate_rankings_variance_none_below_two_trials():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o")
    GenerationJobFactory(
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        metrics={"lines_of_code": 1000},
    )

    row = next(r for r in services.aggregate_rankings() if r["model_id"] == model.model_id)

    # completed but never analyzed: no density samples at all
    assert row["variance"]["n_jobs"] == 0
    assert row["variance"]["density_per_kloc_stdev"] is None


def test_aggregate_rankings_skips_ungraded_pass_rate():
    """An infra_error evaluation stores ``pass_rate: None``: excluded from the mean, not counted as 0."""
    user = UserFactory()
    model = LLMModelFactory(model_id="infra/model")
    for functional in (
        {"status": "passed", "passed": True, "pass_rate": 0.8},
        {"status": "infra_error", "passed": False, "pass_rate": None},
    ):
        GenerationJobFactory(
            created_by=user,
            model=model,
            status=GenerationJob.Status.COMPLETED,
            metrics={"lines_of_code": 100, "functional": functional},
        )
    row = next(r for r in services.aggregate_rankings() if r["model_id"] == model.model_id)
    assert row["functional_pass_rate"] == 0.8


# ── empirical density: code issues only, one instrument set, app units ─────


def _analyzed_job(user, model, findings_by_tool, *, app=None, files=None, metrics=None):
    """A completed job with one analysis run; findings_by_tool = {tool: [(severity, category, file_path), ...]}."""
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import Finding
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import ToolResultFactory

    job = GenerationJobFactory(
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        app_requirement=app,
        result_data={"files": files or {"app.py": "x = 1\n" * 1000}},
        metrics=metrics or {},
    )
    run = AnalysisRunFactory(
        created_by=user,
        generation_job=job,
        status=AnalysisRun.Status.COMPLETED,
        tool_slugs=list(findings_by_tool),
    )
    for tool, findings in findings_by_tool.items():
        result = ToolResultFactory(run=run, tool_slug=tool)
        for severity, category, path in findings:
            Finding.objects.create(result=result, severity=severity, category=category, file_path=path, title="f")
    return job


def _row(model, **scope):
    return next(r for r in services.aggregate_rankings(**scope) if r["model_id"] == model.model_id)


def test_empirical_density_leaves_out_dependency_advisories_but_not_manifest_hygiene():
    user = UserFactory()
    model = LLMModelFactory(model_id="dep/model")
    _analyzed_job(
        user,
        model,
        {
            "bandit": [("high", "security", "app.py")],
            # Scaffold-pinned packages: advisories about packages, not the model's code.
            "pip-audit": [("high", "dependency", "requirements.txt")] * 20,
            # A missing/unused dependency is the app's own manifest issue.
            "deptry": [("low", "dependency", "requirements.txt")],
        },
    )

    row = _row(model)

    assert row["findings"] == {"critical": 0, "high": 1, "medium": 0, "low": 1, "info": 0}
    assert row["dependency_findings"] == 20
    # 1 high (5) + 1 low (1) over 1000 source LOC = 6/KLOC -> 1 - 6/cap, blended with no smoke data.
    assert row["empirical_quality_score"] == pytest.approx(1 - 6 / CAP, abs=1e-4)


def test_empirical_density_counts_only_tools_that_measured_every_app():
    user = UserFactory()
    model_a = LLMModelFactory(model_id="a/model")
    model_b = LLMModelFactory(model_id="b/model")
    _analyzed_job(
        user,
        model_a,
        {"bandit": [("high", "security", "app.py")], "ruff": [("low", "lint", "app.py")] * 30},
    )
    # ruff never ran on b's app: counting it would make a look worse for being measured more.
    _analyzed_job(user, model_b, {"bandit": [("high", "security", "app.py")]})

    a, b = _row(model_a), _row(model_b)

    assert a["findings"]["low"] == 0
    assert a["empirical_quality_score"] == b["empirical_quality_score"]
    assert a["instrument_set"]["tools_counted"] == ["bandit"]
    assert a["instrument_set"]["tools_left_out"] == ["ruff"]


def test_empirical_density_leaves_out_test_code_and_dynamic_tools():
    from backend.analysis.tests.factories import AnalyzerToolFactory

    AnalyzerToolFactory(slug="zap", analysis_mode="dynamic", category="security")
    user = UserFactory()
    model = LLMModelFactory(model_id="t/model")
    files = {"app.py": "x = 1\n" * 1000, "tests/test_app.py": "assert True\n" * 500}
    _analyzed_job(
        user,
        model,
        {
            "bandit": [("high", "security", "app.py"), ("high", "security", "tests/test_app.py")],
            "zap": [("medium", "security", "http://localhost/")] * 4,
        },
        files=files,
    )

    row = _row(model)

    assert row["findings"]["high"] == 1
    assert row["test_findings"] == 1
    assert row["runtime_findings"] == 4
    # The denominator is source LOC without the test file.
    assert row["local_loc"] == 1000
    assert row["empirical_quality_score"] == pytest.approx(1 - 5 / CAP, abs=1e-4)


def test_empirical_density_uses_source_loc_not_metrics_lines():
    user = UserFactory()
    model = LLMModelFactory(model_id="loc/model")
    files = {"app.py": "x = 1\n" * 1000, "frontend/package-lock.json": "{}\n" * 9000}
    _analyzed_job(
        user,
        model,
        {"bandit": [("high", "security", "app.py")] * 3},
        files=files,
        metrics={"lines_of_code": 10000},
    )

    row = _row(model)

    assert row["local_loc"] == 1000
    assert row["empirical_quality_score"] == pytest.approx(1 - 15 / CAP, abs=1e-4)


def test_repeats_collapse_to_app_means_before_the_model_aggregate():
    from backend.generation.tests.factories import AppRequirementTemplateFactory

    user = UserFactory()
    model = LLMModelFactory(model_id="rep/model")
    noisy, clean = AppRequirementTemplateFactory(slug="noisy"), AppRequirementTemplateFactory(slug="clean")
    # Three repeats of an app with 10 highs (50/KLOC) and one of an app with none.
    for _ in range(3):
        _analyzed_job(user, model, {"bandit": [("high", "security", "app.py")] * 10}, app=noisy)
    _analyzed_job(user, model, {"bandit": []}, app=clean)

    row = _row(model)

    assert row["findings"]["high"] == 30  # the job-level rollup still counts every run
    assert row["variance"]["n_jobs"] == 4
    assert row["variance"]["n_apps"] == 2
    assert row["variance"]["density_per_kloc_mean"] == pytest.approx(25.0)
    # (mean 10 highs + 0) x 5 over (1000 + 1000) LOC = 25/KLOC, not 150/4000 = 37.5/KLOC.
    assert row["empirical_quality_score"] == pytest.approx(1 - 25 / CAP, abs=1e-4)


def test_scope_without_a_common_tool_has_no_empirical_score():
    user = UserFactory()
    model = LLMModelFactory(model_id="none/model")
    _analyzed_job(user, model, {"bandit": [("high", "security", "app.py")]})
    _analyzed_job(user, model, {"ruff": [("low", "lint", "app.py")]})

    row = _row(model)

    assert row["empirical_quality_score"] is None
    # No app was measured with one instrument set: unknown counts, not zero (P8).
    assert row["findings"] is None
    assert (row["dependency_findings"], row["runtime_findings"], row["test_findings"]) == (None, None, None)
    assert row["composite_score"] == row["mss_score"]


# ── P8: what nothing measured is None, not 0 ─────


def test_counts_no_counted_tool_can_report_are_unmeasured_not_zero():
    user = UserFactory()
    model = LLMModelFactory(model_id="p8/model")
    # bandit only: no advisory scanner, no dynamic tool in the instrument set.
    _analyzed_job(user, model, {"bandit": [("high", "security", "app.py")]})

    row = _row(model)

    assert row["findings"]["high"] == 1
    assert row["test_findings"] == 0  # bandit reads test files too: a measured zero
    assert row["dependency_findings"] is None
    assert row["runtime_findings"] is None


def test_unmeasured_model_counts_are_none_while_measured_ones_are_counted():
    user = UserFactory()
    measured = LLMModelFactory(model_id="measured/model")
    unmeasured = LLMModelFactory(model_id="unmeasured/model")
    _analyzed_job(user, measured, {"bandit": [], "pip-audit": [("high", "dependency", "requirements.txt")]})
    # Completed but never analyzed.
    GenerationJobFactory(created_by=user, model=unmeasured, status=GenerationJob.Status.COMPLETED)
    never_ran = LLMModelFactory(model_id="never/ran")

    rows = {r["model_id"]: r for r in services.aggregate_rankings()}

    assert rows["measured/model"]["findings"] == {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    assert rows["measured/model"]["dependency_findings"] == 1
    for model_id in (unmeasured.model_id, never_ran.model_id):
        row = rows[model_id]
        assert row["findings"] is None
        assert (row["dependency_findings"], row["runtime_findings"], row["test_findings"]) == (None, None, None)
        assert row["empirical_quality_score"] is None


def test_avg_duration_is_none_when_nothing_completed():
    user = UserFactory()
    model = LLMModelFactory(model_id="slow/model")
    GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.FAILED, duration_seconds=12)
    timed = LLMModelFactory(model_id="timed/model")
    GenerationJobFactory(created_by=user, model=timed, status=GenerationJob.Status.COMPLETED, duration_seconds=30)

    assert _row(model)["avg_duration"] is None
    assert _row(timed)["avg_duration"] == 30.0
    assert _row(LLMModelFactory(model_id="idle/model"))["avg_duration"] is None


# ── P6: several conditions of one model pooled into one row are flagged ─────


def test_rows_report_how_many_experiment_conditions_they_pool():
    from backend.generation.tests.factories import ExperimentConditionFactory
    from backend.generation.tests.factories import ExperimentFactory

    user = UserFactory()
    model = LLMModelFactory(model_id="arms/model")
    single = LLMModelFactory(model_id="single/model")
    experiment = ExperimentFactory(created_by=user)
    for m in (model, model, single):
        condition = ExperimentConditionFactory(experiment=experiment, model=m)
        GenerationJobFactory(
            created_by=user,
            model=m,
            experiment=experiment,
            condition=condition,
            status=GenerationJob.Status.COMPLETED,
        )

    pooled = _row(model, experiment_id=str(experiment.id))
    alone = _row(single, experiment_id=str(experiment.id))

    assert pooled["conditions_pooled"] == 2
    assert "2 experiment conditions" in pooled["pooling_note"]
    assert alone["conditions_pooled"] == 1
    assert alone["pooling_note"] is None


def test_rankings_count_failed_generations():
    user = UserFactory()
    model = LLMModelFactory(model_id="fail/model")
    GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED)
    GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.FAILED)
    GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.CANCELLED)

    row = _row(model)

    assert (row["apps"], row["apps_completed"], row["apps_failed"]) == (3, 1, 1)
