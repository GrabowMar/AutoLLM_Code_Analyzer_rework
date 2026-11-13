"""Constants used by rankings score computations (MSS v2 and the empirical score)."""

from __future__ import annotations

# ── MSS v2 (docs/design/MSS_V2.md) ─────────────────────────────────────────
# The Model Selection Score is a weighted sum of four components on fixed
# goalposts (pre-registered anchors, so adding or removing a model never
# reorders the others). A missing component is left out and the weights are
# renormalised; it is never scored 0. Popularity and price are required.
MSS_VERSION = "mss-2.0"

MSS_WEIGHTS = {"popularity": 0.40, "price": 0.30, "capability": 0.20, "recency": 0.10}
# Amendment A1: the within-tier score that drives the stratified selection.
# Price is re-normalised inside the tier band; popularity stays primary and
# capability rises because every pick must be good.
MSS_TIER_WEIGHTS = {"popularity": 0.45, "capability": 0.30, "price": 0.15, "recency": 0.10}
COMPONENTS = ("popularity", "price", "capability", "recency")
REQUIRED_COMPONENTS = ("popularity", "price")

GOALPOSTS = {
    # log10 of the geometric-mean market share: 0.01% scores 0, 10% scores 1.
    "popularity_log10_share": (-4.0, -1.0),
    # log10 USD per reference run: $0.01 scores 1, $10 scores 0 (cheaper is better).
    "price_log10_usd": (-2.0, 1.0),
    # LMArena WebDev rating (Bradley-Terry scale, linear): 1450 scores 0, 1850 scores 1.
    "capability_rating": (1450.0, 1850.0),
    # Days since the pinned version's release: 0 scores 1, 365 scores 0.
    "recency_days": 365,
}

# One OpenCode agentic app run (midpoint of the measured 2026-11 profiles:
# deepseek-v4.1-flash 0.66M/27k, qwen3.8-flash 1.10M/44.5k), in M tokens.
REFERENCE_BASKET = {"prompt_mtok": 0.9, "output_mtok": 0.036, "cache_hit": 0.8}
# The more verbose measured profile; feasibility and budgets only, never the score.
PLANNING_BASKET = {"prompt_mtok": 1.1, "output_mtok": 0.045, "cache_hit": 0.8}
RETRY_FACTOR = 1.25
RUNS_PER_MODEL = 30

POPULARITY_WINDOW_DAYS = 28
# Days a model is off the published top-50 list are left-censored: imputed as
# CENSOR_FACTOR x that day's smallest listed volume (Hornung & Reed 1990).
CENSOR_FACTOR = 0.5
RECENCY_MAX_DAYS = 365
MIN_CONTEXT_TOKENS = 128_000
DEPRECATION_HORIZON_DAYS = 60

ROUTE_RANK = {"go": 0, "anthropic": 1, "openrouter": 2}
TIERS = ("frontier", "mid", "budget")  # processing order of the stratified selection

SELECTION_DEFAULTS = {
    "n": 10,
    # Amendment A1: exact quotas per price tertile of the reference run cost.
    "tier_quotas": {"frontier": 3, "mid": 3, "budget": 4},
    # Explicit USD cut points per run (budget <= low < mid <= high < frontier);
    # None = tertiles of the eligible frame.
    "tier_cuts": None,
    "vendor_max": 2,
    "vendors_min": 6,
    "open_min": 3,
    "closed_min": 3,
    "claude_min": 1,
    "origin_us_min": 0,
    "origin_cn_min": 0,
    # Quality floor: a pick needs capability >= this percentile of its tier;
    # relaxed stepwise to the later entries when a tier cannot be filled.
    "quality_floor": True,
    "floor_percentiles": [50.0, 40.0, 33.0],
    # A model without capability evidence passes the floor only with popularity
    # at or above this percentile of the eligible frame.
    "missing_capability_popularity_pct": 75.0,
    # Hard budgets (USD for RUNS_PER_MODEL runs x RETRY_FACTOR), each with a contingency.
    "contingency": 0.15,
    "anthropic_budget": 200.0,
    "openrouter_budget": None,  # None = live OpenRouter credit (or the --openrouter-budget fallback)
    "study_months": 1,
}

SENSITIVITY_SEED = 20261109
SENSITIVITY_DRAWS = {"S1": 10_000, "S2": 2_000, "S3": 2_000}
# Interactive API previews cap the draws and say so.
SENSITIVITY_PREVIEW_DRAWS = {"S1": 1_000, "S2": 500, "S3": 0}
DIRICHLET_CONCENTRATION = 50.0
ROBUST_FREQUENCY = 0.70
BORDERLINE_FREQUENCY = 0.40

# Empirical quality (from this platform's own measurements): a weighted
# code-issue-per-KLOC density is squashed to 0..1, then blended with the
# functional smoke-test pass rate. EMPIRICAL_DENSITY_CAP is the density that
# scores zero. The density counts code issues only (no dependency advisories,
# dynamic-tool or test-code findings) over source LOC: real experiments
# measure ~6-10/KLOC per model with a 13-tool set (per app up to ~14) and
# ~32/KLOC with a 29-tool set, so 50 spreads models across the scale without
# flooring the wider instrument sets. (150 was tuned on all-category
# densities of ~48-132/KLOC, which advisories dominated; on code-only
# densities it squeezed every model into 0.94-0.97.)
SEVERITY_WEIGHTS = {"critical": 10, "high": 5, "medium": 2, "low": 1, "info": 0}

# Alternative severity weightings for the rank-stability sensitivity analysis:
# the empirical ranking is recomputed under each scheme and compared to the
# baseline (Kendall's tau + adjacent swaps). "baseline" aliases
# SEVERITY_WEIGHTS so the two can never drift apart.
SEVERITY_WEIGHT_SCHEMES = {
    "baseline": SEVERITY_WEIGHTS,
    "security_heavy": {"critical": 10, "high": 5, "medium": 1, "low": 0, "info": 0},
    "flat": {"critical": 1, "high": 1, "medium": 1, "low": 1, "info": 0},
    "info_included": {"critical": 10, "high": 5, "medium": 2, "low": 1, "info": 1},
}

EMPIRICAL_DENSITY_CAP = 50.0
EMPIRICAL_FUNCTIONAL_WEIGHT = 0.4  # rest goes to findings density

# When local measurements exist, composite blends them with the metadata-based
# MSS; models we have never exercised keep composite == MSS.
COMPOSITE_MSS_WEIGHT = 0.6
COMPOSITE_EMPIRICAL_WEIGHT = 0.4

SORT_KEY_MAP = {
    "mss": "mss_score",
    "composite": "composite_score",
    "popularity": "popularity_score",
    "price": "run_cost_usd",
    "capability": "capability_score",
    "recency": "recency_score",
    "coverage": "mss_coverage",
    "context": "context_length",
    "name": "model_name",
    "apps": "apps",
    "empirical": "empirical_quality_score",
}
