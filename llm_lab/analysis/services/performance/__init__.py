"""Performance analyzers package.

Re-exports public symbols (and legacy underscore helpers used in tests)
from the focused submodules so that
``llm_lab.analysis.services.performance_analyzers`` keeps working.
"""

from __future__ import annotations

from ._common import GRADE_THRESHOLDS
from ._common import LIGHTHOUSE_CATEGORY_MAP
from ._common import SCORE_SEVERITY_THRESHOLDS
from ._common import _average_score_to_grade
from ._common import _score_to_severity
from .lighthouse import LighthouseAnalyzer
from .parsers import map_audit_to_category
from .parsers import parse_lighthouse_report

__all__ = [
    "GRADE_THRESHOLDS",
    "LIGHTHOUSE_CATEGORY_MAP",
    "SCORE_SEVERITY_THRESHOLDS",
    "LighthouseAnalyzer",
    "_average_score_to_grade",
    "_score_to_severity",
    "map_audit_to_category",
    "parse_lighthouse_report",
]
