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


def test_output_kind_seeded_from_yaml():
    call_command("seed_analysis_tools")
    assert AnalyzerTool.objects.get(slug="radon").output_kind == "mixed"
    assert AnalyzerTool.objects.get(slug="jscpd").output_kind == "mixed"
    assert AnalyzerTool.objects.get(slug="bandit").output_kind == "findings"


def test_performance_category_has_an_enabled_tool():
    call_command("seed_analysis_tools")
    assert AnalyzerTool.objects.filter(category="performance", is_enabled=True).exists()


def test_tool_technologies_come_from_the_catalog_vocabulary():
    from backend.analysis.seeding import load_catalog_meta

    call_command("seed_analysis_tools")
    meta = load_catalog_meta()
    known = {t["slug"] for t in meta["technologies"]}
    groups = {g["slug"] for g in meta["technology_groups"]}
    assert all(t["group"] in groups for t in meta["technologies"])
    for tool in AnalyzerTool.objects.all():
        assert tool.technologies, f"{tool.slug} lists no technologies"
        assert set(tool.technologies) <= known, tool.slug


def test_dynamic_tools_seeded_with_url_placeholder():
    call_command("seed_analysis_tools")
    dynamic = AnalyzerTool.objects.filter(analysis_mode="dynamic")
    assert {"zap", "nuclei", "wapiti"} <= set(dynamic.values_list("slug", flat=True))
    for tool in dynamic:
        assert "{url}" in tool.run_cmd, tool.slug
        assert "{target}" not in tool.run_cmd, tool.slug
    assert AnalyzerTool.objects.get(slug="trivy").analysis_mode == "static"


def test_config_placeholders_have_defaults():
    import re

    call_command("seed_analysis_tools")
    for tool in AnalyzerTool.objects.filter(kind="container"):
        names = set(re.findall(r"\{config:([A-Za-z0-9_]+)\}", tool.run_cmd))
        schema_names = {f["name"] for f in tool.config_schema or []}
        assert names <= set(tool.default_config or {}), tool.slug
        assert names <= schema_names, tool.slug


def test_release_binary_downloads_are_checksummed():
    """Every tool that downloads a release binary verifies its published sha256."""
    import yaml

    from backend.analysis.seeding import DATA_DIR

    for path in sorted(DATA_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        install = (data.get("install_cmd") if isinstance(data, dict) else "") or ""
        if "/releases/download/" in install:
            assert "sha256sum -c" in install, path.name
