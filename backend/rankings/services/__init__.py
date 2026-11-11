"""Rankings service layer.

* ``frame``: the MSS v2 sampling frame (canonical models, eligibility);
* ``mss``: the MSS v2 components and aggregation;
* ``pricing`` / ``popularity`` / ``openrouter_api``: the input importers;
* ``selection`` / ``study_selection`` / ``snapshot``: the stratified study selection;
* ``scoring``: empirical quality and the composite;
* ``aggregator`` / ``writer``: the rankings rows, filters and sorting;
* ``sensitivity``: severity-weight and selection sensitivity.
"""

from backend.rankings.services.aggregator import aggregate_rankings
from backend.rankings.services.aggregator import mss_sources
from backend.rankings.services.constants import MSS_TIER_WEIGHTS
from backend.rankings.services.constants import MSS_VERSION
from backend.rankings.services.constants import MSS_WEIGHTS
from backend.rankings.services.constants import SEVERITY_WEIGHT_SCHEMES
from backend.rankings.services.constants import SEVERITY_WEIGHTS
from backend.rankings.services.constants import SORT_KEY_MAP
from backend.rankings.services.mss import compute_mss
from backend.rankings.services.scoring import compute_composite
from backend.rankings.services.scoring import compute_empirical_quality
from backend.rankings.services.sensitivity import compute_weight_sensitivity
from backend.rankings.services.sensitivity import kendall_tau
from backend.rankings.services.sensitivity import rank_biased_overlap
from backend.rankings.services.writer import filter_rankings
from backend.rankings.services.writer import get_top_models
from backend.rankings.services.writer import sort_rankings

__all__ = [
    "MSS_TIER_WEIGHTS",
    "MSS_VERSION",
    "MSS_WEIGHTS",
    "SEVERITY_WEIGHTS",
    "SEVERITY_WEIGHT_SCHEMES",
    "SORT_KEY_MAP",
    "aggregate_rankings",
    "compute_composite",
    "compute_empirical_quality",
    "compute_mss",
    "compute_weight_sensitivity",
    "filter_rankings",
    "get_top_models",
    "kendall_tau",
    "mss_sources",
    "rank_biased_overlap",
    "sort_rankings",
]
