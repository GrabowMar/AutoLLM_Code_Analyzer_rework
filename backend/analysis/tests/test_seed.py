from __future__ import annotations

import pytest
from django.core.management import call_command

from backend.analysis.models import AnalyzerTool
from backend.analysis.services import parsers

pytestmark = pytest.mark.django_db


def test_catalog_is_auto_seeded_by_post_migrate():
    # No call_command here: the post_migrate hook must have seeded the
    # catalog when the test database was created. (Requires --create-db
    # after YAML changes; --reuse-db skips migrate.)
    assert AnalyzerTool.objects.count() >= 14
    assert AnalyzerTool.objects.filter(slug="ruff", is_system=True).exists()


def test_seed_is_idempotent():
    call_command("seed_analysis_tools")
    first = AnalyzerTool.objects.count()
    assert first >= 14
    for slug in ("bandit", "radon", "vulture", "detect-secrets", "codespell", "jscpd", "hadolint"):
        assert AnalyzerTool.objects.filter(slug=slug).exists(), slug
    assert AnalyzerTool.objects.filter(slug="llm_review", kind="ai").exists()

    call_command("seed_analysis_tools")
    assert AnalyzerTool.objects.count() == first


def test_every_container_tool_has_registered_parser():
    call_command("seed_analysis_tools")
    for tool in AnalyzerTool.objects.filter(kind="container").exclude(parser_key=""):
        assert parsers.has_parser(tool.parser_key), tool.slug


def test_performance_category_has_an_enabled_tool():
    call_command("seed_analysis_tools")
    assert AnalyzerTool.objects.filter(category="performance", is_enabled=True).exists()
