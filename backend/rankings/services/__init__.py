"""Rankings service layer (split into constants, scoring, aggregator, writer)."""

from backend.rankings.services.aggregator import aggregate_rankings
from backend.rankings.services.aggregator import get_status
from backend.rankings.services.constants import BENCHMARK_RANGES
from backend.rankings.services.constants import BENCHMARK_WEIGHTS
from backend.rankings.services.constants import DOCS_SCORES
from backend.rankings.services.constants import LICENSE_SCORES
from backend.rankings.services.constants import SEVERITY_WEIGHT_SCHEMES
from backend.rankings.services.constants import SEVERITY_WEIGHTS
from backend.rankings.services.constants import SORT_KEY_MAP
from backend.rankings.services.constants import STABILITY_SCORES
from backend.rankings.services.scoring import compute_accessibility_score
from backend.rankings.services.scoring import compute_adoption_score
from backend.rankings.services.scoring import compute_benchmark_score
from backend.rankings.services.scoring import compute_cost_efficiency_score
from backend.rankings.services.scoring import compute_empirical_quality
from backend.rankings.services.scoring import compute_mss
from backend.rankings.services.scoring import normalize_benchmark_score
from backend.rankings.services.sensitivity import compute_weight_sensitivity
from backend.rankings.services.sensitivity import kendall_tau
from backend.rankings.services.writer import filter_rankings
from backend.rankings.services.writer import get_top_models
from backend.rankings.services.writer import sort_rankings

__all__ = [
    "BENCHMARK_RANGES",
    "BENCHMARK_WEIGHTS",
    "DOCS_SCORES",
    "LICENSE_SCORES",
    "SEVERITY_WEIGHTS",
    "SEVERITY_WEIGHT_SCHEMES",
    "SORT_KEY_MAP",
    "STABILITY_SCORES",
    "aggregate_rankings",
    "compute_accessibility_score",
    "compute_adoption_score",
    "compute_benchmark_score",
    "compute_cost_efficiency_score",
    "compute_empirical_quality",
    "compute_mss",
    "compute_weight_sensitivity",
    "filter_rankings",
    "get_status",
    "get_top_models",
    "kendall_tau",
    "normalize_benchmark_score",
    "sort_rankings",
]
