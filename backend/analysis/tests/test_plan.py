"""Profiles and frozen plans (engine/plan.py) against a fake catalog / reviewer config / image."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json

import pytest

from backend.analysis.engine import catalog
from backend.analysis.engine import measurement
from backend.analysis.engine import normalize
from backend.analysis.engine import plan
from backend.analysis.engine import sandbox
from backend.analysis.engine import types
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.engine.catalog import ToolSpec
from backend.analysis.engine.reviewer import config as reviewer_config
from backend.analysis.models import AnalysisProfile


def _spec(slug, order, *, kind="static", category="lint", version="1.0", enabled=True, required=True, **kw):
    return ToolSpec(
        slug=slug,
        name=slug.title(),
        kind=kind,
        category=category,
        languages=("python",),
        file_kinds=("source",),
        output_kind="findings",
        display_order=order,
        default_enabled=enabled,
        default_required=required,
        default_config=kw.pop("default_config", {}),
        config_schema=({"name": "select", "type": "string"},),
        spec_hash=f"spec-{slug}",
        raw={"slug": slug, "version": version},
        **kw,
    )


SPECS = {
    "ruff": _spec("ruff", 20, version="0.14.0", default_config={"select": "F"}),
    "bandit": _spec("bandit", 10, category="security", version="1.8.6"),
    "codespell": _spec("codespell", 90, category="style", enabled=False, required=False),
    "ai_review": _spec("ai_review", 200, kind="ai", category="ai"),
}
REVIEWER_DEFAULTS = {
    "enabled": True,
    "required": True,
    "engine": "single_shot",
    "model": "deepseek/deepseek-v4.1-flash",
    "samples": 3,
}
MANIFEST = {"schema_version": 1, "tools": {"ruff": {"version": "0.14.0", "version_output": "ruff 0.14.0"}}}


def _validate_tool_config(slug, config):
    spec = SPECS[slug]
    unknown = sorted(set(config or {}) - {"select"})
    if unknown:
        raise ConfigError({f"config.{unknown[0]}": "unknown field"})
    return {**spec.default_config, **(config or {})}


def _config_hash(slug, config, tool_version=""):
    payload = json.dumps([slug, config, tool_version, SPECS[slug].spec_hash], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _validate_reviewer(cfg):
    merged = {**REVIEWER_DEFAULTS, **(cfg or {})}
    if not isinstance(merged["samples"], int) or merged["samples"] < 1:
        raise ConfigError({"reviewer.samples": "must be >= 1"})
    return merged


def _freeze_reviewer(cfg, **_kwargs):
    return {
        **cfg,
        "model": {
            "slug": cfg["model"],
            "canonical_slug": cfg["model"] + "-20260910",
            "pricing": {"input_per_token": 1e-7, "snapshot_at": "2026-10-22T10:00:00Z"},
        },
        "prompt_version": "review-v1",
        "prompt_sha256": "p" * 64,
    }


@pytest.fixture
def fakes(monkeypatch):
    state = {"image_error": None}
    monkeypatch.setattr(catalog, "load_specs", lambda refresh=False: dict(SPECS))
    monkeypatch.setattr(catalog, "get_spec", lambda slug: SPECS[slug])
    monkeypatch.setattr(
        catalog,
        "static_specs",
        lambda: sorted((s for s in SPECS.values() if s.kind != "ai"), key=lambda s: s.display_order),
    )
    monkeypatch.setattr(catalog, "validate_tool_config", _validate_tool_config)
    monkeypatch.setattr(catalog, "config_hash", _config_hash)
    monkeypatch.setattr(reviewer_config, "default_reviewer_config", lambda: dict(REVIEWER_DEFAULTS))
    monkeypatch.setattr(reviewer_config, "validate_reviewer_config", _validate_reviewer)
    monkeypatch.setattr(reviewer_config, "freeze_reviewer", _freeze_reviewer)
    monkeypatch.setattr(normalize, "mapping_hashes", lambda: {"severity.yaml": "m" * 64})
    monkeypatch.setattr(sandbox, "image_tag", lambda: "llmlab/analyzer:abcdef123456")

    def ensure_image(*, expect_tag="", build=True, heartbeat=None, log=None):
        if state["image_error"] is not None:
            raise state["image_error"]
        return expect_tag, "sha256:" + "1" * 64

    monkeypatch.setattr(sandbox, "ensure_image", ensure_image)
    monkeypatch.setattr(sandbox, "image_manifest", lambda ref: copy.deepcopy(MANIFEST))
    return state


CONFIG = {
    "tools": [{"slug": "ruff"}, {"slug": "bandit", "required": False}],
    "reviewer": {"enabled": True, "samples": 3},
}


# --------------------------------------------------------------------------
# validate_profile_config
# --------------------------------------------------------------------------


def test_validation_normalizes_and_orders_tools(fakes):
    cfg = plan.validate_profile_config(
        {"tools": ["ruff", {"slug": "bandit", "required": False}, {"slug": "codespell", "enabled": False}]},
    )
    assert cfg["tools"] == [
        {"slug": "bandit", "required": False, "config": {}},
        {"slug": "ruff", "required": True, "config": {"select": "F"}},
    ]
    assert cfg["reviewer"] == REVIEWER_DEFAULTS
    assert cfg["scope"]["measured_origins"] == ["authored", "added", "modified"]
    assert cfg["outcomes"] == plan.DEFAULT_OUTCOMES


def test_validation_collects_dotted_field_errors(fakes):
    with pytest.raises(ConfigError) as caught:
        plan.validate_profile_config(
            {
                "tools": [
                    {"slug": "nope"},
                    {"slug": "ruff", "config": {"bogus": 1}},
                    {"slug": "bandit", "required": "yes"},
                    {"slug": "ai_review"},
                    {"slug": "ruff"},
                ],
                "reviewer": {"samples": 0},
                "scope": {"measured_origins": ["martian"], "max_file_bytes": 1, "weird": True},
                "outcomes": {"severity_weights": {"high": -1, "spicy": 1}, "min_sloc_for_density": "x"},
                "extra": 1,
            },
        )
    errors = caught.value.field_errors
    assert errors["tools.0.slug"].startswith("unknown tool")
    assert errors["tools.1.config.bogus"] == "unknown field"
    assert errors["tools.2.required"] == "must be a boolean"
    assert "reviewer" in errors["tools.3.slug"]
    assert errors["reviewer.samples"] == "must be >= 1"
    assert "unknown origin" in errors["scope.measured_origins"]
    assert "scope.max_file_bytes" in errors
    assert errors["scope.weird"] == "unknown field"
    assert errors["outcomes.severity_weights.high"] == "must be a number >= 0"
    assert errors["outcomes.severity_weights.spicy"] == "unknown severity"
    assert "outcomes.min_sloc_for_density" in errors
    assert errors["extra"] == "unknown field"


def test_duplicate_tools_are_rejected(fakes):
    with pytest.raises(ConfigError) as caught:
        plan.validate_profile_config({"tools": ["ruff", "ruff"]})
    assert caught.value.field_errors == {"tools.1.slug": "duplicate tool 'ruff'"}


def test_something_must_run(fakes):
    with pytest.raises(ConfigError) as caught:
        plan.validate_profile_config({"tools": [], "reviewer": {"enabled": False}})
    assert "tools" in caught.value.field_errors


def test_a_disabled_reviewer_never_blocks_a_profile(fakes):
    cfg = plan.validate_profile_config({"tools": ["ruff"], "reviewer": {"enabled": False, "samples": 0}})
    assert cfg["reviewer"] == {"enabled": False, "required": False}
    cfg = plan.validate_profile_config({"tools": ["ruff"], "reviewer": {"enabled": False, "samples": 5}})
    assert cfg["reviewer"]["samples"] == 5
    assert cfg["reviewer"]["enabled"] is False


def test_scope_lists_are_deduplicated_and_kinds_checked(fakes):
    cfg = plan.validate_profile_config(
        {"tools": ["ruff"], "scope": {"measured_origins": ["added", "added"], "exclude_kinds": ["lockfile"]}},
    )
    assert cfg["scope"]["measured_origins"] == ["added"]
    with pytest.raises(ConfigError) as caught:
        plan.validate_profile_config({"tools": ["ruff"], "scope": {"exclude_kinds": ["source"]}})
    assert "scope.exclude_kinds" in caught.value.field_errors


def test_measured_kinds_default_to_source_and_are_validated(fakes):
    cfg = plan.validate_profile_config({"tools": ["ruff"]})
    assert cfg["scope"]["measured_kinds"] == ["source"]
    cfg = plan.validate_profile_config({"tools": ["ruff"], "scope": {"measured_kinds": ["source", "env", "env"]}})
    assert cfg["scope"]["measured_kinds"] == ["source", "env"]
    # A different measured scope is a different procedure: never comparable.
    default = plan.freeze_plan(config={"tools": ["ruff"]})
    with_env = plan.freeze_plan(config={"tools": ["ruff"], "scope": {"measured_kinds": ["source", "env"]}})
    assert default["scope"]["measured_kinds"] == ["source"]
    assert plan.plan_hash(default) != plan.plan_hash(with_env)
    for kinds, message in [
        (["source", "binary"], "unknown file kind 'binary'"),
        ([], "at least one file kind must be measured"),
        (["source", "lockfile"], "'lockfile' files are excluded (scope.exclude_kinds)"),
        ("source", "must be a list of non-empty strings"),
    ]:
        with pytest.raises(ConfigError) as caught:
            plan.validate_profile_config({"tools": ["ruff"], "scope": {"measured_kinds": kinds}})
        assert caught.value.field_errors == {"scope.measured_kinds": message}


def test_content_hash_ignores_submission_order(fakes):
    a = plan.validate_profile_config({"tools": ["ruff", "bandit"]})
    b = plan.validate_profile_config({"tools": ["bandit", "ruff"]})
    assert plan.profile_content_hash(a) == plan.profile_content_hash(b)
    c = plan.validate_profile_config({"tools": ["ruff", {"slug": "bandit", "required": False}]})
    assert plan.profile_content_hash(a) != plan.profile_content_hash(c)


def test_default_profile_config(fakes):
    cfg = plan.default_profile_config()
    assert [t["slug"] for t in cfg["tools"]] == ["bandit", "ruff"]  # codespell is not default-enabled
    assert cfg["reviewer"] == REVIEWER_DEFAULTS
    plan.validate_profile_config(cfg)


# --------------------------------------------------------------------------
# save_profile
# --------------------------------------------------------------------------


def test_save_profile_versions_on_content_change_only(fakes, user):
    first = plan.save_profile(user=user, name="My Profile", description="d", config=CONFIG)
    assert (first.slug, first.version) == ("my-profile", 1)
    assert first.content_hash == plan.profile_content_hash(first.config)

    same = plan.save_profile(user=user, name="Renamed", description="d2", config=copy.deepcopy(CONFIG), base=first)
    assert same.pk == first.pk
    assert same.version == 1
    first.refresh_from_db()
    assert (first.name, first.description) == ("Renamed", "d2")

    changed = plan.save_profile(user=user, name="Renamed", description="", config={"tools": ["ruff"]}, base=first)
    assert (changed.slug, changed.version) == ("my-profile", 2)
    assert changed.pk != first.pk
    third = plan.save_profile(user=user, name="x", description="", config={"tools": ["bandit"]}, base=first)
    assert third.version == 3  # versions follow the latest row of the slug, not the base
    assert AnalysisProfile.objects.filter(slug="my-profile").count() == 3


def test_new_profiles_get_unique_slugs(fakes, user):
    a = plan.save_profile(user=user, name="Dup", description="", config=CONFIG)
    b = plan.save_profile(user=user, name="Dup", description="", config=CONFIG)
    assert (a.slug, b.slug) == ("dup", "dup-2")
    assert b.version == 1


def test_save_profile_rejects_invalid_content(fakes, user):
    with pytest.raises(ConfigError):
        plan.save_profile(user=user, name="Bad", description="", config={"tools": ["nope"]})
    assert not AnalysisProfile.objects.filter(slug="bad").exists()


# --------------------------------------------------------------------------
# freeze_plan / plan_hash
# --------------------------------------------------------------------------


def test_freeze_plan_with_a_built_image(fakes):
    frozen = plan.freeze_plan(config=CONFIG)
    assert frozen["schema_version"] == plan.PLAN_SCHEMA_VERSION
    assert frozen["profile"] is None
    assert frozen["analyzer_image"] == {"tag": "llmlab/analyzer:abcdef123456"}
    bandit, ruff = frozen["tools"]
    assert ruff == {
        "slug": "ruff",
        "name": "Ruff",
        "kind": "static",
        "category": "lint",
        "required": True,
        "config": {"select": "F"},
        "config_hash": _config_hash("ruff", {"select": "F"}, "0.14.0"),
        "spec_hash": "spec-ruff",
        "version": "0.14.0",
        "version_source": "image_manifest",
        "languages": ["python"],
    }
    # bandit is missing from the manifest: spec version, flagged.
    assert (bandit["version"], bandit["version_source"]) == ("1.8.6", "spec")
    assert frozen["notes"] == [
        {
            "code": "version_unverified",
            "detail": "Versions not read from a built analyzer image (spec versions used): bandit",
        },
    ]
    assert frozen["reviewer"]["enabled"] is True
    assert frozen["reviewer"]["required"] is True
    assert frozen["reviewer"]["tool_slug"] == "ai_review"
    assert frozen["reviewer"]["model"]["canonical_slug"] == "deepseek/deepseek-v4.1-flash-20260910"
    # The stage versions of the running code (engine/types.py), whatever they currently are.
    assert frozen["engine_versions"] == {
        "sample_resolver": types.SAMPLE_RESOLVER_VERSION,
        "sloc": types.SLOC_VERSION,
        "normalizer": types.NORMALIZER_VERSION,
        "dedup": types.DEDUP_VERSION,
        "measurement": types.MEASUREMENT_VERSION,
    }
    assert frozen["mapping_hashes"] == {"severity.yaml": "m" * 64}
    assert frozen["provenance"]["image_id"] == "sha256:" + "1" * 64
    assert frozen["provenance"]["image_manifest"]["tools"] == {"ruff": {"version": "0.14.0"}}


def test_freeze_plan_without_an_image_uses_spec_versions(fakes):
    fakes["image_error"] = sandbox.SandboxError("daemon down")
    frozen = plan.freeze_plan(config=CONFIG)
    assert {t["version_source"] for t in frozen["tools"]} == {"spec"}
    assert frozen["tools"][1]["version"] == "0.14.0"
    assert [n["code"] for n in frozen["notes"]] == ["image_unavailable", "version_unverified"]
    assert frozen["analyzer_image"]["tag"] == "llmlab/analyzer:abcdef123456"
    # Same versions, same tag: comparable with a plan frozen once the image exists.
    fakes["image_error"] = None
    verified = plan.freeze_plan(config={"tools": ["ruff"], "reviewer": {"enabled": False}})
    unverified_cfg = {"tools": ["ruff"], "reviewer": {"enabled": False}}
    fakes["image_error"] = sandbox.SandboxError("daemon down")
    unverified = plan.freeze_plan(config=unverified_cfg)
    assert plan.plan_hash(verified) == plan.plan_hash(unverified)


def test_freeze_review_only_plan_needs_no_image(fakes, monkeypatch):
    monkeypatch.setattr(sandbox, "image_tag", lambda: pytest.fail("no image needed"))
    frozen = plan.freeze_plan(config={"tools": [], "reviewer": {"enabled": True}})
    assert frozen["tools"] == []
    assert frozen["analyzer_image"] == {"tag": ""}
    assert frozen["reviewer"]["enabled"]


def test_freeze_plan_from_a_profile(fakes, user):
    profile = plan.save_profile(user=user, name="P", description="", config=CONFIG)
    frozen = plan.freeze_plan(profile=profile)
    assert frozen["profile"] == {
        "id": str(profile.pk),
        "slug": profile.slug,
        "version": 1,
        "content_hash": profile.content_hash,
    }
    # The same config frozen from another profile (or none) is comparable.
    other = plan.save_profile(user=user, name="Q", description="", config=copy.deepcopy(CONFIG))
    assert plan.plan_hash(plan.freeze_plan(profile=other)) == plan.plan_hash(frozen)
    assert plan.plan_hash(plan.freeze_plan(config=CONFIG)) == plan.plan_hash(frozen)


def test_freeze_plan_revalidates_a_stale_profile(fakes, user):
    profile = AnalysisProfile.objects.create(slug="old", name="Old", config={"tools": ["gone"]})
    with pytest.raises(ConfigError):
        plan.freeze_plan(profile=profile)


def test_plan_hash_covers_procedure_not_provenance(fakes):
    frozen = plan.freeze_plan(config=CONFIG)
    base = plan.plan_hash(frozen)
    noise = copy.deepcopy(frozen)
    noise["profile"] = {"id": "x", "slug": "y", "version": 9, "content_hash": "z"}
    noise["notes"] = [{"code": "other"}]
    noise["provenance"] = {"frozen_at": "later", "image_id": "sha256:other"}
    noise["tools"][0]["version_source"] = "image_manifest"
    noise["reviewer"]["model"]["pricing"] = {"input_per_token": 9.0, "snapshot_at": "tomorrow"}
    assert plan.plan_hash(noise) == base
    for mutate in (
        lambda p: p["tools"][1]["config"].update(select="E"),
        lambda p: p["tools"][0].update(version="9.9"),
        lambda p: p["reviewer"].update(samples=5),
        lambda p: p["reviewer"]["model"].update(canonical_slug="other"),
        lambda p: p["scope"].update(max_file_bytes=1024),
        lambda p: p["analyzer_image"].update(tag="llmlab/analyzer:000000000000"),
        lambda p: p["mapping_hashes"].update({"severity.yaml": "n" * 64}),
        lambda p: p["engine_versions"].update(dedup="dedup-next"),
    ):
        changed = copy.deepcopy(frozen)
        mutate(changed)
        assert plan.plan_hash(changed) != base
    # Hashing never mutates the plan.
    assert frozen["profile"] is None
    assert frozen["provenance"]


def test_plan_hash_ignores_tool_names_and_display_order(fakes, monkeypatch):
    """Presentation fields split no population: spec_hash leaves them out, so must plan_hash."""
    frozen = plan.freeze_plan(config=CONFIG)
    base = plan.plan_hash(frozen)
    renamed = copy.deepcopy(frozen)
    renamed["tools"][1]["name"] = "Ruff linter"
    assert plan.plan_hash(renamed) == base
    reordered = copy.deepcopy(frozen)
    reordered["tools"].reverse()
    assert [t["slug"] for t in reordered["tools"]] == ["ruff", "bandit"]
    assert plan.plan_hash(reordered) == base
    # A display-order edit in the catalog re-freezes to the same hash.
    monkeypatch.setitem(SPECS, "ruff", _spec("ruff", 5, version="0.14.0", default_config={"select": "F"}))
    refrozen = plan.freeze_plan(config=CONFIG)
    assert [t["slug"] for t in refrozen["tools"]] == ["ruff", "bandit"]
    assert plan.plan_hash(refrozen) == base
    # Execution order is unchanged by hashing.
    assert [t["slug"] for t in frozen["tools"]] == ["bandit", "ruff"]


def test_procedure_drift_is_empty_for_a_plan_frozen_by_the_running_code(fakes):
    assert plan.procedure_drift(plan.freeze_plan(config=CONFIG)) == {"tools": {}, "engine": []}


def test_procedure_drift_names_changed_specs_engine_versions_and_mappings(fakes, monkeypatch):
    frozen = plan.freeze_plan(config=CONFIG)
    # An adapter / spec change after freezing (adapter_version bump -> new spec_hash).
    monkeypatch.setitem(SPECS, "ruff", dataclasses.replace(SPECS["ruff"], spec_hash="spec-ruff-v2"))
    monkeypatch.setattr(measurement, "MEASUREMENT_VERSION", "measure-next")
    monkeypatch.setattr(normalize, "mapping_hashes", lambda: {"severity.yaml": "n" * 64, "cwe.yaml": "c" * 64})
    drift = plan.procedure_drift(frozen)
    assert set(drift["tools"]) == {"ruff"}
    assert "spec_hash spec-ruff -> spec-ruff-v2" in drift["tools"]["ruff"]
    assert f"measurement {types.MEASUREMENT_VERSION} -> measure-next" in drift["engine"]
    assert any(item.startswith("mapping table severity.yaml") for item in drift["engine"])
    assert any(item.startswith("mapping table cwe.yaml unrecorded") for item in drift["engine"])


def test_procedure_drift_of_a_tool_that_left_the_catalog(fakes):
    frozen = plan.freeze_plan(config=CONFIG)
    frozen["tools"].append({**frozen["tools"][0], "slug": "gone"})
    assert plan.procedure_drift(frozen)["tools"] == {"gone": "the tool 'gone' is no longer in the catalog"}


def test_procedure_drift_treats_unrecorded_engine_versions_as_drift(fakes):
    frozen = plan.freeze_plan(config=CONFIG)
    frozen["engine_versions"] = {}
    assert len(plan.procedure_drift(frozen)["engine"]) == len(plan.engine_versions())


SCOPE_ORIGINS = ["authored", "added", "modified"]
SCOPE_KINDS = ["source", "config", "manifest", "env"]


def _review_scope_config(*, measured_origins=None, measured_kinds=None, include_origins=None, enabled=True, **scope):
    return {
        "tools": ["ruff"],
        "reviewer": {
            "enabled": enabled,
            "include_origins": include_origins or list(SCOPE_ORIGINS),
            "include_kinds": list(SCOPE_KINDS),
        },
        "scope": {
            "measured_origins": measured_origins or list(SCOPE_ORIGINS),
            "measured_kinds": measured_kinds or ["source"],
            **scope,
        },
    }


def test_the_reviewer_must_see_every_measured_origin(fakes):
    """A whole-app study (scaffold measured) with the default reviewer would deflate the AI density."""
    whole_app = [*SCOPE_ORIGINS, "scaffold"]
    with pytest.raises(ConfigError) as caught:
        plan.validate_profile_config(_review_scope_config(measured_origins=whole_app))
    assert list(caught.value.field_errors) == ["reviewer.include_origins"]
    assert "'scaffold'" in caught.value.field_errors["reviewer.include_origins"]
    cfg = plan.validate_profile_config(_review_scope_config(measured_origins=whole_app, include_origins=whole_app))
    assert cfg["scope"]["measured_origins"] == whole_app
    # A narrower reviewer than the measured scope is the same defect.
    with pytest.raises(ConfigError, match="include_origins"):
        plan.validate_profile_config(_review_scope_config(include_origins=["added"]))
    # Without the reviewer there is no AI density to protect.
    plan.validate_profile_config(_review_scope_config(measured_origins=whole_app, enabled=False))


def test_the_reviewer_must_see_every_measured_kind(fakes):
    plan.validate_profile_config(_review_scope_config(measured_kinds=["source", "env", "config"]))
    with pytest.raises(ConfigError) as caught:
        plan.validate_profile_config(_review_scope_config(measured_kinds=["source", "doc"]))
    assert "'doc'" in caught.value.field_errors["reviewer.include_kinds"]
    # Lockfiles can never be shown to the reviewer: they cannot be measured while it is enabled.
    with pytest.raises(ConfigError) as caught:
        plan.validate_profile_config(
            _review_scope_config(measured_kinds=["source", "lockfile"], exclude_kinds=["build", "asset"]),
        )
    assert "'lockfile'" in caught.value.field_errors["scope.measured_kinds"]


@pytest.mark.django_db
def test_the_review_scope_check_runs_against_the_real_reviewer_config():
    with pytest.raises(ConfigError) as caught:
        plan.validate_profile_config(
            {"tools": [], "reviewer": {"enabled": True}, "scope": {"measured_origins": [*SCOPE_ORIGINS, "platform"]}},
        )
    assert "'platform'" in caught.value.field_errors["reviewer.include_origins"]


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("not a plan", TypeError),
        ({"schema_version": 2}, ValueError),
        ({"schema_version": 1, "tools": "x", "scope": {}}, ValueError),
        ({"schema_version": 1, "tools": [], "reviewer": None, "scope": {}}, ValueError),
        ({"schema_version": 1, "tools": [{"slug": "a"}], "reviewer": "x", "scope": {}}, ValueError),
        ({"schema_version": 1, "tools": [{"slug": "a"}], "reviewer": None}, TypeError),
    ],
)
def test_check_frozen_rejects_malformed_plans(value, error):
    with pytest.raises(error):
        plan.check_frozen(value)


def test_check_frozen_accepts_a_frozen_plan(fakes):
    plan.check_frozen(plan.freeze_plan(config=CONFIG))


# --------------------------------------------------------------------------
# Against the real catalog / mappings (other stages' modules)
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_freeze_the_real_static_only_profile(monkeypatch):
    import yaml

    from backend.analysis import seeding

    try:
        catalog.load_specs(refresh=True)
        normalize.mapping_hashes()
    except NotImplementedError:
        pytest.skip("catalog / mappings not implemented yet")
    # Never contact the daemon from this test: the image counts as not built.
    monkeypatch.setattr(sandbox, "ensure_image", lambda **kwargs: (_ for _ in ()).throw(sandbox.SandboxError("off")))
    data = yaml.safe_load((seeding.PROFILES_DIR / "static-only.yaml").read_text())
    frozen = plan.freeze_plan(config=data["config"])
    plan.check_frozen(frozen)
    slugs = [t["slug"] for t in frozen["tools"]]
    assert len(slugs) == 14
    assert "ai_review" not in slugs
    assert all(t["version"] and t["config_hash"] and t["spec_hash"] for t in frozen["tools"])
    assert {t["version_source"] for t in frozen["tools"]} == {"spec"}
    assert frozen["reviewer"] is None
    assert frozen["analyzer_image"]["tag"].startswith("llmlab/analyzer:")
    assert frozen["mapping_hashes"]
    assert {n["code"] for n in frozen["notes"]} == {"image_unavailable", "version_unverified"}
    assert plan.plan_hash(frozen) == plan.plan_hash(plan.freeze_plan(config=data["config"]))


def test_max_unparseable_share_is_frozen_with_the_outcomes(fakes):
    cfg = plan.validate_profile_config({"tools": ["ruff"]})
    # The default is frozen explicitly, and equals the measurement stage's own default.
    assert cfg["outcomes"]["max_unparseable_share"] == 0.1
    assert measurement.DEFAULT_OUTCOME_CONFIG["max_unparseable_share"] == 0.1
    custom = plan.validate_profile_config({"tools": ["ruff"], "outcomes": {"max_unparseable_share": 0}})
    assert custom["outcomes"]["max_unparseable_share"] == 0.0
    assert plan.profile_content_hash(custom) != plan.profile_content_hash(cfg)


@pytest.mark.parametrize("value", [-0.1, 1.5, True, "0.2", None, float("nan")])
def test_max_unparseable_share_must_be_a_share(fakes, value):
    with pytest.raises(ConfigError) as caught:
        plan.validate_profile_config({"tools": ["ruff"], "outcomes": {"max_unparseable_share": value}})
    assert caught.value.field_errors == {"outcomes.max_unparseable_share": "must be a number between 0 and 1"}
