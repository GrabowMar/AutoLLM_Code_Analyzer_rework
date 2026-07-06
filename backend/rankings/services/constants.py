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

# Empirical quality (from this platform's own measurements): a weighted
# findings-per-KLOC density is squashed to 0..1, then blended with the
# functional smoke-test pass rate. EMPIRICAL_DENSITY_CAP is the density that
# scores zero — our 4-model URL-shortener experiment measured ~48-132/KLOC
# with the full 7-tool set, so 150 spreads observed models across the scale.
SEVERITY_WEIGHTS = {"critical": 10, "high": 5, "medium": 2, "low": 1, "info": 0}
EMPIRICAL_DENSITY_CAP = 150.0
EMPIRICAL_FUNCTIONAL_WEIGHT = 0.4  # rest goes to findings density

# When local measurements exist, composite blends them with the metadata-based
# MSS; models we have never exercised keep composite == MSS.
COMPOSITE_MSS_WEIGHT = 0.6
COMPOSITE_EMPIRICAL_WEIGHT = 0.4

SORT_KEY_MAP = {
    "mss": "mss_score",
    "composite": "composite_score",
    "adoption": "adoption_score",
    "benchmark": "benchmark_score",
    "cost": "cost_efficiency_score",
    "access": "accessibility_score",
    "context": "context_length",
    "price": "price_per_million_input",
    "name": "model_name",
    "apps": "apps",
    "empirical": "empirical_quality_score",
}
