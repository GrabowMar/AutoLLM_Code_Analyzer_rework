"""Constants used by rankings score computations."""

BENCHMARK_RANGES = {
    "bfcl_score": (0, 100),
    "webdev_elo": (800, 1400),
    "livebench_coding": (0, 100),
    "livecodebench": (0, 100),
    "arc_agi_score": (0, 100),
    "simplebench_score": (0, 100),
    "canaicode_score": (0, 100),
    "seal_coding_score": (800, 1400),
    "gpqa_score": (0, 100),
    "humaneval": (0, 100),
    "mbpp": (0, 100),
    "swebench": (0, 100),
}

BENCHMARK_WEIGHTS = {
    "bfcl_score": 0.12,
    "webdev_elo": 0.12,
    "livebench_coding": 0.08,
    "livecodebench": 0.08,
    "arc_agi_score": 0.08,
    "simplebench_score": 0.08,
    "canaicode_score": 0.08,
    "seal_coding_score": 0.08,
    "gpqa_score": 0.08,
    "humaneval": 0.08,
    "mbpp": 0.06,
    "swebench": 0.06,
}

LICENSE_SCORES = {
    "apache": 1.0,
    "mit": 1.0,
    "bsd": 1.0,
    "cc-by": 1.0,
    "llama": 0.7,
    "gemma": 0.7,
    "yi": 0.7,
    "commercial": 0.4,
    "api-only": 0.4,
    "unknown": 0.0,
    "proprietary": 0.0,
}

STABILITY_SCORES = {
    "stable": 1.0,
    "production": 1.0,
    "reliable": 0.7,
    "recent": 0.7,
    "beta": 0.4,
    "experimental": 0.4,
    "deprecated": 0.0,
    "unreliable": 0.0,
}

DOCS_SCORES = {
    "comprehensive": 1.0,
    "excellent": 1.0,
    "good": 0.7,
    "basic": 0.7,
    "minimal": 0.4,
    "poor": 0.4,
    "none": 0.0,
    "missing": 0.0,
}

# Empirical quality (from this platform's own measurements): the
# severity-weighted density of counted static *issues* (findings deduplicated
# across tools, measured scope only) per KLOC of measured SLOC is squashed to
# 0..1, then blended with the functional (conformance) pass rate. Numerator
# and denominator come from the same canonical runs of one frozen plan (see
# services/measured.py). EMPIRICAL_DENSITY_CAP is the density that scores
# zero. It was calibrated on the v1 7-tool set (~48-132 findings/KLOC); the
# v2 measurement (issues, model-written lines only) is a different scale, so
# the cap is provisional until recalibrated on v2 data: every row carries
# SCORING_VERSION so scores from different calibrations are never compared.
SEVERITY_WEIGHTS = {"critical": 10, "high": 5, "medium": 2, "low": 1, "info": 0}
SCORING_VERSION = "empirical-v2"

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

EMPIRICAL_DENSITY_CAP = 150.0
EMPIRICAL_FUNCTIONAL_WEIGHT = 0.4  # rest goes to findings density

# When local measurements exist, composite blends them with the metadata-based
# MSS; models we have never exercised keep composite == MSS.
COMPOSITE_MSS_WEIGHT = 0.6
COMPOSITE_EMPIRICAL_WEIGHT = 0.4

# Public sort keys -> row keys. Both the short and the long spelling of the
# component scores are accepted (the UI sends ``cost_efficiency`` and
# ``accessibility``); unknown keys fall back to MSS instead of silently
# leaving the list unsorted.
SORT_KEY_MAP = {
    "mss": "mss_score",
    "composite": "composite_score",
    "adoption": "adoption_score",
    "benchmark": "benchmark_score",
    "cost": "cost_efficiency_score",
    "cost_efficiency": "cost_efficiency_score",
    "access": "accessibility_score",
    "accessibility": "accessibility_score",
    "context": "context_length",
    "price": "price_per_million_input",
    "name": "model_name",
    "apps": "apps",
    "empirical": "empirical_quality_score",
    "n_trials": "n_trials",
}
DEFAULT_SORT_KEY = "mss_score"
