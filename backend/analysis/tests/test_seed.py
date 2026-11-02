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


def test_run_commands_cover_jsx_and_unpackaged_python():
    """Regressions: lizard skipped .jsx/.tsx, pylint skipped dirs without __init__.py,
    jscpd scanned .jsx as three token streams."""
    import yaml

    from backend.analysis.seeding import DATA_DIR

    def run_cmd(slug: str) -> str:
        return yaml.safe_load((DATA_DIR / f"{slug}.yaml").read_text())["run_cmd"]

    assert "-l jsx" in run_cmd("lizard")
    assert "-l tsx" in run_cmd("lizard")
    assert "--recursive=y" in run_cmd("pylint")
    assert "--formats-exts" in run_cmd("jscpd")


def _tool_yaml(slug: str) -> dict:
    import yaml

    from backend.analysis.seeding import DATA_DIR

    return yaml.safe_load((DATA_DIR / f"{slug}.yaml").read_text())


def test_linters_ignore_config_files_the_app_ships():
    """A pyproject.toml/setup.cfg/.pylintrc the model writes must not change what is measured."""
    assert "--isolated" in _tool_yaml("ruff")["run_cmd"]
    assert "--isolated" in _tool_yaml("flake8")["run_cmd"]
    assert "--rcfile=/dev/null" in _tool_yaml("pylint")["run_cmd"]
    assert "--rcfile=/dev/null" in _tool_yaml("perflint")["run_cmd"]
    # An empty value: mypy reads no config file (/dev/null would print a warning).
    assert "--config-file= " in _tool_yaml("mypy")["run_cmd"]
    assert "--no-eslintrc" in _tool_yaml("eslint")["run_cmd"]


def test_pylint_install_is_pinned_like_the_other_pip_tools():
    pylint = _tool_yaml("pylint")
    assert "'pylint==4.1.*'" in pylint["install_cmd"]
    assert pylint["version"] == "4.1"


def test_stylelint_allows_tailwind_4():
    run_cmd = _tool_yaml("stylelint")["run_cmd"]
    for at_rule in ("theme", "source", "utility", "variant", "custom-variant", "plugin", "reference"):
        assert f'"{at_rule}"' in run_cmd, at_rule
    assert '"function-no-unknown":[true,{"ignoreFunctions":["theme","screen"]}]' in run_cmd


def test_dependency_hygiene_commands_know_framework_provided_modules():
    run_cmd = _tool_yaml("deptry")["run_cmd"]
    assert "Flask=flask|werkzeug|itsdangerous|" in run_cmd
    assert "fastapi=fastapi|starlette|pydantic" in run_cmd
    # deptry separates rules with commas and packages with "|".
    assert "--per-rule-ignores 'DEP002=uvicorn|gunicorn|" in run_cmd
    assert "jsdom" in _tool_yaml("depcheck")["run_cmd"]


def test_version_checks_report_the_real_build():
    assert _tool_yaml("shellcheck")["verify_cmd"] == "shellcheck --version | grep '^version:'"
    assert "m.version('perflint')" in _tool_yaml("perflint")["verify_cmd"]


def _semgrep_args(config: dict | None) -> list[str]:
    """The arguments the rendered semgrep command passes, with semgrep stubbed out."""
    import subprocess

    from backend.analysis.services.runner import render_command

    call_command("seed_analysis_tools")
    tool = AnalyzerTool.objects.get(slug="semgrep")
    cmd = render_command(tool, target="/work", config=config)
    script = "semgrep() { printf '%s\\n' \"$@\"; }\n" + cmd
    # The catalog's own command with the scanner replaced by a stub that echoes its arguments.
    done = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, check=True)  # noqa: S603
    return done.stdout.splitlines()


def test_semgrep_scans_test_dirs_and_skips_the_scaffold_bind():
    args = _semgrep_args(None)
    # Its built-in .semgrepignore would silently skip tests/ (every other tool scans it).
    assert "--x-ignore-semgrepignore-files" in args
    assert args[args.index("--exclude-rule") + 1] == (
        "python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host"
    )
    assert args[-1] == "/work"

    custom = _semgrep_args({"exclude_rules": "a.b.c,d.e*"})
    assert [custom[i + 1] for i, a in enumerate(custom) if a == "--exclude-rule"] == ["a.b.c", "d.e*"]
    assert "--exclude-rule" not in _semgrep_args({"exclude_rules": ""})


def test_stale_tools_reports_rows_that_differ_from_their_yaml():
    """A YAML change that was never seeded leaves the old instrument in the DB."""
    from backend.analysis.seeding import stale_tools

    call_command("seed_analysis_tools")
    assert stale_tools() == {}

    AnalyzerTool.objects.filter(slug="ruff").update(run_cmd="ruff check {target}", version="0.0")
    AnalyzerTool.objects.filter(slug="vulture").delete()
    stale = stale_tools()
    assert stale == {"ruff": ["run_cmd", "version"], "vulture": ["missing"]}
    # Display-only fields are not part of the instrument.
    AnalyzerTool.objects.filter(slug="bandit").update(description="edited", display_order=999)
    assert "bandit" not in stale_tools()

    call_command("seed_analysis_tools")
    assert stale_tools() == {}
