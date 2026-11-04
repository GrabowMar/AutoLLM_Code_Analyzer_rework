"""Ninja schemas for rankings."""

from __future__ import annotations

from typing import Any

from ninja import Schema


class FindingsRollup(Schema):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0


class VarianceStats(Schema):
    """Spread of the empirical inputs over apps (repeats collapsed to app means; stdev is None below n=2)."""

    n_jobs: int = 0
    n_apps: int = 0
    density_per_kloc_mean: float | None = None
    density_per_kloc_stdev: float | None = None
    smoke_pass_rate_stdev: float | None = None


class RankingRow(Schema):
    model_id: str
    model_name: str
    provider: str
    is_free: bool = False
    context_length: int | None = None
    price_per_million_input: float | None = None
    price_per_million_output: float | None = None
    apps: int = 0
    apps_completed: int = 0
    apps_failed: int = 0
    # Mean generation time of completed apps; None when none completed.
    avg_duration: float | None = None
    # Study conditions this row pools (several = arms mixed; see pooling_note).
    conditions_pooled: int = 0
    pooling_note: str | None = None
    # Code issues the empirical score counts (common tools, no advisories, no
    # dynamic-tool or test-code findings); the others are counted apart.
    # Unmeasured is None, not 0: no measured app, or (dependency/runtime) no
    # advisory scanner / dynamic tool in the instrument set.
    findings: FindingsRollup | None = None
    ai_findings: FindingsRollup | None = None
    dependency_findings: int | None = None
    runtime_findings: int | None = None
    test_findings: int | None = None
    local_loc: int = 0
    n_trials: int = 0
    functional_pass_rate: float | None = None
    variance: VarianceStats | None = None
    benchmark_score: float
    cost_efficiency_score: float
    accessibility_score: float
    adoption_score: float
    mss_score: float
    empirical_quality_score: float | None = None
    composite_score: float

    # Allow extra benchmark fields to pass through.
    class Config:
        extra = "allow"


class Pagination(Schema):
    page: int
    per_page: int
    total: int
    total_pages: int
    has_next: bool
    has_prev: bool


class RankingStats(Schema):
    total: int
    with_benchmarks: int
    unique_providers: int
    free_models: int = 0
    avg_mss: float = 0.0


class RankingsResponse(Schema):
    count: int
    rankings: list[dict[str, Any]]
    pagination: Pagination
    statistics: RankingStats


class StatusResponse(Schema):
    benchmarks: dict[str, int]
    total_benchmark_rows: int
    models_with_benchmarks: int
    total_models: int


class TopModelsResponse(Schema):
    count: int
    models: list[dict[str, Any]]


class SensitivityRankEntry(Schema):
    model_id: str
    empirical_quality: float | None = None


class SensitivitySchemeRow(Schema):
    scheme: str
    weights: dict[str, int]
    ranking: list[SensitivityRankEntry]
    kendall_tau: float
    adjacent_swaps: list[list[str]]


class SensitivityResponse(Schema):
    models_evaluated: int
    baseline_ranking: list[SensitivityRankEntry]
    schemes: list[SensitivitySchemeRow]
