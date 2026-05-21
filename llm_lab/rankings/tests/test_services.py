"""Tests for rankings service & API."""

from __future__ import annotations

import pytest

from llm_lab.generation.models import GenerationJob
from llm_lab.generation.tests.factories import GenerationJobFactory
from llm_lab.llm_models.tests.factories import LLMModelFactory
from llm_lab.rankings import services
from llm_lab.rankings.models import BenchmarkResult
from llm_lab.users.tests.factories import UserFactory

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
