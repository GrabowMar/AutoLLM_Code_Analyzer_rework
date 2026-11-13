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
    # MSS v2 (docs/design/MSS_V2.md). Every score is None when its input is missing
    # (never 0); a model without MSS (insufficient evidence, not in the frame) has
    # no composite either.
    canonical_id: str | None = None
    vendor: str | None = None
    origin: str | None = None
    open_weights: bool | None = None
    release_date: str | None = None
    pinned_version: dict[str, Any] | None = None
    routes: dict[str, Any] = {}
    preferred_route: str | None = None
    eligible: bool = False
    exclusion: dict[str, Any] | None = None
    popularity_score: float | None = None
    price_score: float | None = None
    capability_score: float | None = None
    recency_score: float | None = None
    mss_score: float | None = None
    mss_coverage: float | None = None
    mss_missing: list[str] = []
    mss_flags: list[str] = []
    mss_inherited_from: str | None = None
    run_cost_usd: float | None = None
    planning_cost_usd: float | None = None
    price_tier: str | None = None
    popularity: dict[str, Any] | None = None
    webdev_rating: dict[str, Any] | None = None
    list_price: dict[str, Any] | None = None
    empirical_quality_score: float | None = None
    composite_score: float | None = None

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
    eligible: int = 0
    with_popularity: int = 0
    with_capability: int = 0
    unique_vendors: int = 0
    # Mean MSS over the filtered rows that have one; None when none has.
    avg_mss: float | None = None


class RankingsResponse(Schema):
    count: int
    rankings: list[dict[str, Any]]
    pagination: Pagination
    statistics: RankingStats


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


class SourcesResponse(Schema):
    as_of: str
    sources: list[dict[str, Any]]
    coverage: dict[str, int]
    tiers: dict[str, Any]
    flow: list[dict[str, Any]]


class SelectionSnapshotSummary(Schema):
    id: int
    created_at: str
    kind: str
    version: str
    status: str
    as_of: str | None = None
    inputs_sha256: str
    code_version: str
    picks: list[str] = []
    by_tier: dict[str, list[str]] = {}
    created_by: str | None = None
    confirmed_at: str | None = None
    confirmed_by: str | None = None
    confirmation: dict[str, Any] = {}


class SelectionSnapshotDetail(SelectionSnapshotSummary):
    params: dict[str, Any]
    payload: dict[str, Any]


class SelectionCreateIn(Schema):
    n: int | None = None
    tier_quotas: dict[str, int] | None = None
    include_previews: bool = False
    exclude: list[str] = []
    force: list[str] = []
    anthropic_budget: float | None = None
    openrouter_budget: float | None = None
    study_months: float | None = None
    sensitivity: bool = True


class ConfirmIn(Schema):
    """The researcher's final list; every change against the nominal picks needs an override with a reason."""

    final_ids: list[str]
    # [{"replace": id, "with": id, "reason": "..."}]  # noqa: ERA001 - documents the payload shape, not dead code
    overrides: list[dict[str, Any]] = []
    notes: str = ""
