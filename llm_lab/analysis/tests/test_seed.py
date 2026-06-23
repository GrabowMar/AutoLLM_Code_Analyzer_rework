from __future__ import annotations

import pytest
from django.core.management import call_command

from llm_lab.analysis.models import AnalyzerTool

pytestmark = pytest.mark.django_db


def test_seed_is_idempotent():
    call_command("seed_analysis_tools")
    first = AnalyzerTool.objects.count()
    assert first >= 4
    assert AnalyzerTool.objects.filter(slug="bandit").exists()
    assert AnalyzerTool.objects.filter(slug="llm_review", kind="ai").exists()

    call_command("seed_analysis_tools")
    assert AnalyzerTool.objects.count() == first
