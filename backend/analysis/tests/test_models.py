from __future__ import annotations

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.services.base import FindingData
from backend.analysis.services.base import build_severity_counts

pytestmark = pytest.mark.django_db


def test_run_get_code_inline(analysis_run):
    analysis_run.source_code = {"backend": "print('x')", "empty": ""}
    code = analysis_run.get_code_for_analysis()
    assert code["backend"] == "print('x')"


def test_run_defaults(user):
    run = AnalysisRun.objects.create(created_by=user, tool_slugs=["bandit"])
    assert run.status == AnalysisRun.Status.PENDING
    assert run.tool_slugs == ["bandit"]


def test_finding_factory(finding):
    assert finding.result.run is not None
    assert finding.severity == "high"


def test_build_severity_counts():
    counts = build_severity_counts(
        [
            FindingData(severity="high", category="security", title="a"),
            FindingData(severity="high", category="security", title="b"),
            FindingData(severity="low", category="quality", title="c"),
        ],
    )
    assert counts["high"] == 2
    assert counts["low"] == 1
    assert counts["critical"] == 0
