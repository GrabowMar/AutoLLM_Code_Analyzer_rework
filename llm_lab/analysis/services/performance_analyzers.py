"""Backwards-compatible shim.

The implementation now lives in
``llm_lab.analysis.services.performance``. Existing imports from
``llm_lab.analysis.services.performance_analyzers`` continue to work via
these re-exports.
"""

from __future__ import annotations

from llm_lab.analysis.services.performance import *  # noqa: F403
from llm_lab.analysis.services.performance import GRADE_THRESHOLDS  # noqa: F401
from llm_lab.analysis.services.performance import LIGHTHOUSE_CATEGORY_MAP  # noqa: F401
from llm_lab.analysis.services.performance import SCORE_SEVERITY_THRESHOLDS  # noqa: F401
from llm_lab.analysis.services.performance import LighthouseAnalyzer  # noqa: F401
from llm_lab.analysis.services.performance import _average_score_to_grade  # noqa: F401
from llm_lab.analysis.services.performance import _score_to_severity  # noqa: F401
from llm_lab.analysis.services.performance import map_audit_to_category  # noqa: F401
from llm_lab.analysis.services.performance import parse_lighthouse_report  # noqa: F401
