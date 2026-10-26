"""Catalog / system-profile / beat-schedule seeding, the post_migrate hook and the management commands."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
import yaml
from django.core.management import call_command
from django_celery_beat.models import IntervalSchedule
from django_celery_beat.models import PeriodicTask

from backend.analysis import apps as analysis_apps
from backend.analysis import seeding
from backend.analysis.engine import catalog
from backend.analysis.engine import executor
from backend.analysis.engine import plan
from backend.analysis.engine import sandbox
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.engine.catalog import ToolSpec
from backend.analysis.models import AnalysisProfile
from backend.analysis.models import AnalyzerTool
from backend.users.tests.factories import UserFactory

REAL_PROFILES_DIR = seeding.PROFILES_DIR
CONTRACT_STATIC_TOOLS = {
    "ruff",
    "bandit",
    "semgrep",
    "pylint",
    "mypy",
    "vulture",
    "eslint",
    "gitleaks",
    "detect-secrets",
    "codespell",
    "hadolint",
    "jscpd",
    "radon",
    "lizard",
}


def _spec(slug, *, order=10, version="1.0", spec_hash=None, kind="static", category="lint", **kw):
    return ToolSpec(
        slug=slug,
        name=kw.pop("name", slug.title()),
        kind=kind,
        category=category,
        languages=("python",),
        file_kinds=("source",),
        output_kind="findings",
        display_order=order,
        config_schema=({"name": "select", "type": "string", "default": ""},),
        default_config={"select": ""},
        spec_hash=spec_hash or f"hash-{slug}-{version}",
        raw={"slug": slug, "version": version},
        **kw,
    )


@pytest.fixture
def specs(monkeypatch):
    current = {
        "ruff": _spec("ruff", order=20, version="0.14.0"),
        "bandit": _spec("bandit", order=10, version="1.8.6", category="security"),
        "ai_review": _spec("ai_review", order=200, kind="ai", category="ai", version=""),
    }
    monkeypatch.setattr(catalog, "load_specs", lambda refresh=False: dict(current))
    return current


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_tools_upserts_and_disables(specs):
    AnalyzerTool.objects.all().delete()
    AnalyzerTool.objects.create(slug="llm_review", name="Old reviewer", category="ai", kind="ai")
    created, updated = seeding.seed_tools()
    assert (created, updated) == (3, 0)
    ruff = AnalyzerTool.objects.get(slug="ruff")
    assert ruff.version == "0.14.0"
    assert ruff.spec_hash == "hash-ruff-0.14.0"
    assert ruff.config_schema == [{"name": "select", "type": "string", "default": ""}]
    assert ruff.languages == ["python"]
    assert ruff.is_enabled
    assert AnalyzerTool.objects.get(slug="ai_review").kind == "ai"
    old = AnalyzerTool.objects.get(slug="llm_review")
    assert not old.is_enabled  # disabled, not deleted

    assert seeding.seed_tools() == (0, 0)  # idempotent
    specs["ruff"] = _spec("ruff", order=20, version="0.15.0", name="Ruff linter")
    assert seeding.seed_tools() == (0, 1)
    ruff.refresh_from_db()
    assert (ruff.name, ruff.version) == ("Ruff linter", "0.15.0")


@pytest.mark.django_db
def test_seed_tools_versions_from_the_image(specs, monkeypatch):
    AnalyzerTool.objects.all().delete()
    manifest = {"tools": {"ruff": {"version": "0.14.0+image"}}}
    monkeypatch.setattr(seeding, "image_manifest_or_none", lambda: manifest)
    seeding.seed_tools(with_image=True)
    assert AnalyzerTool.objects.get(slug="ruff").version == "0.14.0+image"
    assert AnalyzerTool.objects.get(slug="bandit").version == "1.8.6"  # not in the manifest
    # Without the image, a synced version survives while the spec is unchanged...
    seeding.seed_tools()
    assert AnalyzerTool.objects.get(slug="ruff").version == "0.14.0+image"
    # ...and falls back to the spec when the spec changes.
    specs["ruff"] = _spec("ruff", order=20, version="0.15.0")
    seeding.seed_tools()
    assert AnalyzerTool.objects.get(slug="ruff").version == "0.15.0"


@pytest.mark.django_db
def test_sync_tool_versions(specs):
    AnalyzerTool.objects.all().delete()
    seeding.seed_tools()
    assert seeding.sync_tool_versions({"tools": {"ruff": {"version": "9.9.9"}, "ghost": {"version": "1"}}}) == 1
    assert AnalyzerTool.objects.get(slug="ruff").version == "9.9.9"
    assert seeding.sync_tool_versions(None) == 0


def test_image_manifest_or_none_is_quiet_without_an_image(monkeypatch):
    def unavailable(**kwargs):
        msg = "not built"
        raise sandbox.ImageUnavailable(msg)

    monkeypatch.setattr(sandbox, "ensure_image", unavailable)
    assert seeding.image_manifest_or_none() is None


# --------------------------------------------------------------------------
# System profiles
# --------------------------------------------------------------------------


def _fake_validate(config):
    tools = config.get("tools") or []
    if any((t if isinstance(t, str) else t.get("slug")) == "nope" for t in tools):
        raise ConfigError({"tools.0.slug": "unknown tool 'nope'"})
    return {"tools": tools, "reviewer": config.get("reviewer") or {"enabled": False}, "scope": {}, "outcomes": {}}


def _write(directory: Path, name: str, data: dict) -> None:
    (directory / f"{name}.yaml").write_text(yaml.safe_dump(data))


@pytest.mark.django_db
def test_seed_profiles_versions_by_content(tmp_path, monkeypatch):
    AnalysisProfile.objects.all().delete()
    monkeypatch.setattr(seeding, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(plan, "validate_profile_config", _fake_validate)
    _write(tmp_path, "alpha", {"slug": "alpha", "name": "Alpha", "description": "d", "config": {"tools": ["ruff"]}})
    _write(tmp_path, "beta", {"slug": "beta", "name": "Beta", "config": {"tools": ["nope"]}})

    counts = seeding.seed_profiles()
    assert counts["created"] == 1
    assert counts["invalid"] == 1
    alpha = AnalysisProfile.objects.get(slug="alpha")
    assert (alpha.version, alpha.is_system, alpha.created_by) == (1, True, None)
    assert alpha.content_hash == plan.profile_content_hash(alpha.config)

    assert seeding.seed_profiles()["unchanged"] == 1
    _write(tmp_path, "alpha", {"slug": "alpha", "name": "Alpha 2", "config": {"tools": ["ruff"]}})
    assert seeding.seed_profiles()["updated"] == 1
    assert AnalysisProfile.objects.get(slug="alpha").name == "Alpha 2"

    _write(tmp_path, "alpha", {"slug": "alpha", "name": "Alpha 2", "config": {"tools": ["ruff", "bandit"]}})
    assert seeding.seed_profiles()["versioned"] == 1
    assert list(
        AnalysisProfile.objects.filter(slug="alpha").order_by("version").values_list("version", flat=True),
    ) == [
        1,
        2,
    ]

    (tmp_path / "alpha.yaml").unlink()
    assert seeding.seed_profiles()["archived"] == 2
    assert not AnalysisProfile.objects.filter(slug="alpha", is_archived=False).exists()


@pytest.mark.django_db
def test_seed_profiles_never_takes_over_a_user_slug(tmp_path, monkeypatch):
    AnalysisProfile.objects.all().delete()
    monkeypatch.setattr(seeding, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(plan, "validate_profile_config", _fake_validate)
    AnalysisProfile.objects.create(slug="alpha", name="Mine", created_by=UserFactory(), config={})
    _write(tmp_path, "alpha", {"slug": "alpha", "config": {"tools": ["ruff"]}})
    assert seeding.seed_profiles()["invalid"] == 1
    assert AnalysisProfile.objects.filter(slug="alpha").count() == 1


def _load(name: str) -> dict:
    return yaml.safe_load((REAL_PROFILES_DIR / f"{name}.yaml").read_text())


def test_the_five_system_profiles_match_the_contract():
    names = sorted(p.stem for p in REAL_PROFILES_DIR.glob("*.yaml"))
    assert names == ["agentic-review", "quick-check", "research-default", "security-focus", "static-only"]
    profiles = {name: _load(name) for name in names}
    for name, data in profiles.items():
        assert data["slug"] == name
        assert data["name"]
        assert data["description"]
        assert set(data["config"]) <= set(plan.PROFILE_KEYS)

    def tools(name):
        return {t["slug"] for t in profiles[name]["config"]["tools"]}

    def reviewer(name):
        return profiles[name]["config"]["reviewer"]

    assert tools("research-default") == CONTRACT_STATIC_TOOLS
    assert reviewer("research-default") == {
        "enabled": True,
        "required": True,
        "engine": "single_shot",
        "samples": 3,
        "max_output_tokens": 32000,
    }
    assert tools("static-only") == CONTRACT_STATIC_TOOLS
    assert reviewer("static-only") == {"enabled": False}
    assert tools("security-focus") == {"bandit", "semgrep", "eslint", "gitleaks", "detect-secrets"}
    assert reviewer("security-focus")["enabled"]
    assert tools("quick-check") == {"ruff", "eslint", "semgrep", "gitleaks"}
    assert reviewer("quick-check") == {"enabled": False}
    assert tools("agentic-review") == CONTRACT_STATIC_TOOLS
    assert reviewer("agentic-review") == {
        "enabled": True,
        "required": True,
        "engine": "agent",
        "harness": "opencode",
        "samples": 1,
        "max_output_tokens": 32000,
    }
    # Reviewer profiles pin 32k output tokens (reasoning models truncate at 16k).
    assert reviewer("security-focus")["max_output_tokens"] == 32000
    # research-default and static-only measure static outcomes identically.
    assert profiles["research-default"]["config"]["tools"] == profiles["static-only"]["config"]["tools"]


@pytest.mark.django_db
def test_the_system_profiles_validate_against_the_real_catalog():
    try:
        catalog.load_specs(refresh=True)
        configs = {
            p.stem: plan.validate_profile_config(_load(p.stem)["config"]) for p in REAL_PROFILES_DIR.glob("*.yaml")
        }
    except NotImplementedError:
        pytest.skip("catalog / reviewer config not implemented yet")
    assert {t["slug"] for t in configs["research-default"]["tools"]} == CONTRACT_STATIC_TOOLS
    assert configs["research-default"]["reviewer"]["samples"] == 3
    assert configs["agentic-review"]["reviewer"]["engine"] == "agent"


# --------------------------------------------------------------------------
# Beat schedule, post_migrate, commands
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_reconcile_schedule_is_idempotent_and_respects_admins():
    PeriodicTask.objects.filter(name=seeding.RECONCILE_TASK_NAME).delete()
    assert seeding.ensure_reconcile_schedule() is True
    task = PeriodicTask.objects.get(name=seeding.RECONCILE_TASK_NAME)
    assert task.task == "analysis.reconcile"
    assert (task.interval.every, task.interval.period) == (60, IntervalSchedule.SECONDS)
    assert task.enabled

    task.enabled = False
    task.task = "something.else"
    task.save()
    assert seeding.ensure_reconcile_schedule() is False
    task.refresh_from_db()
    assert task.task == "analysis.reconcile"
    assert not task.enabled  # an admin's choice stays
    assert PeriodicTask.objects.filter(name=seeding.RECONCILE_TASK_NAME).count() == 1
    assert IntervalSchedule.objects.filter(every=60, period=IntervalSchedule.SECONDS).count() == 1


@pytest.mark.django_db
def test_post_migrate_seeding_never_raises(monkeypatch, specs):
    def broken(**kwargs):
        msg = "malformed YAML"
        raise ValueError(msg)

    monkeypatch.setattr(seeding, "seed_profiles", broken)
    report = seeding.seed_all()
    assert report["profiles"] == {"error": "ValueError: malformed YAML"}
    assert isinstance(report["tools"], tuple)
    assert report["schedule"] in (True, False)
    analysis_apps._seed_catalog(sender=None, using="default", verbosity=0)


@pytest.mark.django_db
def test_seed_command(monkeypatch, specs):
    monkeypatch.setattr(seeding, "seed_profiles", lambda **kwargs: {"created": 0})
    out = StringIO()
    call_command("seed_analysis_tools", "--no-image", stdout=out)
    assert "Tools:" in out.getvalue()
    assert "Done." in out.getvalue()


@pytest.mark.django_db
def test_reconcile_command(monkeypatch):
    monkeypatch.setattr(executor, "reconcile", lambda: {"requeued": ["r1"]})
    out = StringIO()
    call_command("analysis_reconcile", stdout=out)
    assert json.loads(out.getvalue()) == {"requeued": ["r1"]}


def test_build_image_command(monkeypatch):
    monkeypatch.setattr(sandbox, "image_status", lambda: {"built": False, "tag": "llmlab/analyzer:x"})
    with pytest.raises(SystemExit) as exited:
        call_command("analysis_build_image", "--check", stdout=StringIO())
    assert exited.value.code == 1

    monkeypatch.setattr(sandbox, "build_image", lambda log=None: ("llmlab/analyzer:x", "sha256:1"))
    monkeypatch.setattr(sandbox, "image_manifest", lambda ref: {"tools": {"ruff": {"version": "0.14.0"}}})
    out = StringIO()
    call_command("analysis_build_image", stdout=out)
    assert "llmlab/analyzer:x (sha256:1)" in out.getvalue()
    assert "ruff: 0.14.0" in out.getvalue()


@pytest.mark.django_db
def test_seed_all_with_the_real_catalog_and_profiles():
    try:
        specs = catalog.load_specs(refresh=True)
    except NotImplementedError:
        pytest.skip("catalog not implemented yet")
    AnalysisProfile.objects.filter(is_system=True).delete()
    report = seeding.seed_all()
    assert isinstance(report["tools"], tuple), report
    assert report["profiles"]["invalid"] == 0, report
    assert report["profiles"]["created"] == 5
    assert set(AnalyzerTool.objects.filter(is_enabled=True).values_list("slug", flat=True)) == set(specs)
    assert set(
        AnalysisProfile.objects.filter(is_system=True, is_archived=False).values_list("slug", flat=True),
    ) == {"research-default", "static-only", "security-focus", "quick-check", "agentic-review"}
    again = seeding.seed_all()
    assert again["profiles"]["unchanged"] == 5
    assert again["tools"] == (0, 0)
