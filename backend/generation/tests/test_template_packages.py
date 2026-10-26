"""Template packages v3 (presets, stack remapping, versioned overwrite) and data-file starters."""

from __future__ import annotations

import json

import pytest
from django.test import Client

from backend.generation.models import ContentBlock
from backend.generation.models import GenerationProfile
from backend.generation.models import RunPreset
from backend.generation.services.packages import build_starter_template_package
from backend.generation.services.packages.starters import resolve_content_file
from backend.generation.services.presets import create_preset
from backend.generation.services.presets import system_default_preset
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ContentBlockFactory
from backend.generation.tests.factories import GenerationProfileFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

AGENT_STARTER_PRESETS = {
    ("harness", "aider-whole-file"): {"harness": "aider", "options": {"edit_format": "whole"}},
    ("harness", "aider-no-repo-map"): {"harness": "aider", "options": {"map_tokens": 0}},
    ("harness", "mini-long-commands"): {"harness": "mini-swe-agent", "options": {"command_timeout_seconds": 120}},
    ("sampling", "temperature-zero"): {"llm_params": {"temperature": 0}},
    ("sampling", "reasoning-high"): {"llm_params": {"reasoning": {"effort": "high"}}},
    ("budget", "smoke"): {"max_llm_requests": 30, "wall_clock_seconds": 3600, "max_cost_usd": 3},
    ("budget", "extended"): {"max_llm_requests": 250, "wall_clock_seconds": 10800, "max_cost_usd": 25},
}


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _package(**assets) -> dict:
    return {"kind": "llm-lab-template-package", "template_package_schema_version": 3, "assets": assets}


def _import(client, package: dict, strategy: str = "rename"):
    return client.post(
        "/api/generation/packages/import/",
        data=json.dumps({"package_text": json.dumps(package), "conflict_strategy": strategy}),
        content_type="application/json",
    )


def _export(client, body: dict) -> dict:
    response = client.post(
        "/api/generation/packages/export/?format=json",
        data=json.dumps(body),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return json.loads(response.content.decode())


def _create_stack(client, slug: str, **overrides) -> None:
    payload = {
        "slug": slug,
        "name": slug.title(),
        "has_frontend": False,
        "default_port": 8000,
        "patch_profile": "none",
        "backend_filename": "app.py",
        "backend_base_image": "python:3.12-slim",
        "server_kind": "python",
        "files": {"requirements.txt": "flask\n"},
        **overrides,
    }
    response = client.post("/api/generation/stacks/", data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 200, response.content


def _preset_payload(kind: str, slug: str, config: dict, **extra) -> dict:
    return {"kind": kind, "slug": slug, "name": extra.pop("name", slug.title()), "config": config, **extra}


# ── export ────────────────────────────────────────────────────────────


def test_export_includes_presets_profile_stack_and_spec_difficulty():
    owner = UserFactory()
    client = _client(owner)
    _create_stack(client, "pkg-stack")
    block = ContentBlockFactory(is_system=False, created_by=owner, slug="pkg-block")
    profile = GenerationProfileFactory(
        is_system=False,
        created_by=owner,
        slug="pkg-profile",
        scaffolding_slug="pkg-stack",
        block_refs=[{"type": block.block_type, "slug": block.slug, "version": 1}],
    )
    app = AppRequirementTemplateFactory(is_default=False, created_by=owner, slug="pkg_app", difficulty="advanced")
    preset = create_preset(
        owner,
        kind="harness",
        slug="pkg-aider-whole",
        name="Aider whole",
        description="whole-file edits",
        config={"harness": "aider", "options": {"edit_format": "whole", "map_tokens": 1024}},
    )

    package = _export(
        client,
        {
            "app_template_slugs": [app.slug],
            "bundle_slugs": [profile.slug],
            "preset_refs": [{"kind": "harness", "slug": preset.slug}, {"kind": "harness", "slug": "no-such-preset"}],
        },
    )

    assert package["template_package_schema_version"] == 3
    assets = package["assets"]
    # The profile's user stack comes along without being selected.
    assert [stack["slug"] for stack in assets["stacks"]] == ["pkg-stack"]
    assert assets["presets"] == [
        {
            "kind": "harness",
            "slug": "pkg-aider-whole",
            "name": "Aider whole",
            "description": "whole-file edits",
            # Stored normalized: options at their defaults are dropped.
            "config": {"harness": "aider", "options": {"edit_format": "whole"}},
        },
    ]
    assert assets["app_specs"][0]["difficulty"] == "advanced"


def test_export_of_presets_only_is_a_valid_package():
    owner = UserFactory()
    client = _client(owner)
    create_preset(owner, kind="sampling", slug="pkg-cool", name="Cool", config={"llm_params": {"temperature": 0.2}})

    package = _export(client, {"preset_refs": [{"kind": "sampling", "slug": "pkg-cool"}]})

    assert [p["slug"] for p in package["assets"]["presets"]] == ["pkg-cool"]


def test_export_skips_presets_the_user_cannot_see():
    other = UserFactory()
    create_preset(other, kind="sampling", slug="pkg-private", name="Private", config={"llm_params": {"top_p": 0.5}})
    client = _client(UserFactory())

    response = client.post(
        "/api/generation/packages/export/?format=json",
        data=json.dumps({"preset_refs": [{"kind": "sampling", "slug": "pkg-private"}]}),
        content_type="application/json",
    )

    assert response.status_code == 400  # nothing to export


def test_export_leaves_out_another_users_private_block_referenced_by_own_profile():
    owner = UserFactory()
    ContentBlockFactory(is_system=False, created_by=owner, slug="owner-private-rules", content="OWNER SECRET PROMPT")
    user = UserFactory()
    client = _client(user)
    own = ContentBlockFactory(is_system=False, created_by=user, slug="my-rules", content="mine")
    # Written directly: the profile API now refuses refs the author cannot see,
    # but rows from before that check (or a staff-edited system profile) exist.
    GenerationProfileFactory(
        is_system=False,
        created_by=user,
        slug="mine-x",
        block_refs=[
            {"type": own.block_type, "slug": own.slug, "version": 1},
            {"type": "prompt_rules", "slug": "owner-private-rules", "version": 1},
        ],
    )
    omitted = [
        {
            "profile": "mine-x",
            "type": "prompt_rules",
            "slug": "owner-private-rules",
            "version": 1,
            "reason": "missing or not visible to you",
        },
    ]

    response = client.post(
        "/api/generation/packages/export/?format=json",
        data=json.dumps({"bundle_slugs": ["mine-x"]}),
        content_type="application/json",
    )
    bundle_response = client.get("/api/generation/profiles/mine-x/export/?format=json")

    assert response.status_code == 200
    assert bundle_response.status_code == 200
    for raw, blocks_of in (
        (response.content.decode(), lambda pkg: pkg["assets"]["blocks"]),
        (bundle_response.content.decode(), lambda pkg: pkg["blocks"]),
    ):
        assert "OWNER SECRET PROMPT" not in raw
        package = json.loads(raw)
        assert [block["slug"] for block in blocks_of(package)] == ["my-rules"]
        assert package["omitted_blocks"] == omitted


def test_export_without_hidden_refs_has_no_omitted_blocks():
    user = UserFactory()
    block = ContentBlockFactory(is_system=False, created_by=user, slug="clean-rules")
    GenerationProfileFactory(
        is_system=False,
        created_by=user,
        slug="clean-profile",
        block_refs=[{"type": block.block_type, "slug": block.slug, "version": 1}],
    )

    package = _export(_client(user), {"bundle_slugs": ["clean-profile"]})

    assert [b["slug"] for b in package["assets"]["blocks"]] == ["clean-rules"]
    assert "omitted_blocks" not in package


def test_export_takes_the_latest_version_of_a_profile():
    user = UserFactory()
    old = ContentBlockFactory(is_system=False, created_by=user, slug="old-rules")
    new = ContentBlockFactory(is_system=False, created_by=user, slug="new-rules")
    for version, block in ((1, old), (2, new)):
        GenerationProfileFactory(
            is_system=False,
            created_by=user,
            slug="two-versions",
            version=version,
            block_refs=[{"type": block.block_type, "slug": block.slug, "version": 1}],
        )

    package = _export(_client(user), {"bundle_slugs": ["two-versions"]})

    # One profile per slug (two would import as a renamed duplicate), with only its blocks.
    assert [(p["slug"], p["version"]) for p in package["assets"]["profiles"]] == [("two-versions", 2)]
    assert [b["slug"] for b in package["assets"]["blocks"]] == ["new-rules"]


# ── import: stacks and profiles ───────────────────────────────────────


def test_import_remaps_a_renamed_stack_into_its_profiles():
    owner = UserFactory()
    owner_client = _client(owner)
    _create_stack(owner_client, "remap-stack")
    block = ContentBlockFactory(is_system=False, created_by=owner, slug="remap-block")
    GenerationProfileFactory(
        is_system=False,
        created_by=owner,
        slug="remap-profile",
        scaffolding_slug="remap-stack",
        block_refs=[{"type": block.block_type, "slug": block.slug, "version": 1}],
    )
    package = _export(owner_client, {"bundle_slugs": ["remap-profile"]})

    importer = UserFactory()
    response = _import(_client(importer), package)

    assert response.status_code == 200, response.content
    result = response.json()
    assert result["stacks"] == ["remap-stack-2"]
    profile = GenerationProfile.objects.get(slug=result["profiles"][0])
    assert profile.created_by == importer
    assert profile.scaffolding_slug == "remap-stack-2"


def test_reimporting_own_package_reuses_identical_stack_and_profile():
    owner = UserFactory()
    client = _client(owner)
    _create_stack(client, "own-stack")
    block = ContentBlockFactory(is_system=False, created_by=owner, slug="own-block")
    GenerationProfileFactory(
        is_system=False,
        created_by=owner,
        slug="own-profile",
        name="Own profile",
        scaffolding_slug="own-stack",
        block_refs=[{"type": block.block_type, "slug": block.slug, "version": 1}],
    )
    package = _export(client, {"bundle_slugs": ["own-profile"]})

    response = _import(client, package)

    assert response.status_code == 200, response.content
    assert response.json()["stacks"] == ["own-stack"]
    assert response.json()["profiles"] == ["own-profile"]
    assert GenerationProfile.objects.filter(slug__startswith="own-profile").count() == 1


def test_import_rejects_invalid_profile_llm_config_atomically():
    client = _client(UserFactory())
    package = _package(
        app_specs=[_spec("atomic_app")],
        profiles=[
            {
                "name": "Hot",
                "slug": "hot-profile",
                "block_refs": [{"type": "prompt_tone", "slug": "tone-standard"}],
                "llm_config": {"temperature": 9},
            },
        ],
    )

    response = _import(client, package)

    assert response.status_code == 400
    assert "llm_config" in response.json()["detail"]
    assert not GenerationProfile.objects.filter(slug="hot-profile").exists()
    # One transaction: the app spec imported before the failure is rolled back.
    from backend.generation.models import AppRequirementTemplate

    assert not AppRequirementTemplate.objects.filter(slug="atomic_app").exists()


def test_profile_ref_without_version_pins_latest_system_block():
    user = UserFactory()
    latest = ContentBlock.objects.filter(slug="tone-standard", is_system=True).order_by("-version").first()
    package = _package(
        profiles=[{"name": "Slug refs", "slug": "slug-ref-profile", "block_refs": [{"slug": "tone-standard"}]}],
    )

    response = _import(_client(user), package)

    assert response.status_code == 200, response.content
    profile = GenerationProfile.objects.get(slug="slug-ref-profile")
    # The type is filled in from the block (an untyped ref would be ignored).
    assert profile.block_refs == [{"type": "prompt_tone", "slug": "tone-standard", "version": latest.version}]
    assert profile.content_hash


def test_profile_overwrite_creates_a_new_version():
    owner = UserFactory()
    original = GenerationProfileFactory(
        is_system=False,
        created_by=owner,
        slug="versioned-profile",
        name="Versioned",
        description="v1",
        block_refs=[{"type": "prompt_tone", "slug": "tone-standard", "version": 1}],
    )
    package = _package(
        profiles=[
            {
                "name": "Versioned",
                "slug": "versioned-profile",
                "description": "v2",
                "scaffolding_slug": "flask-react",
                "block_refs": [{"type": "prompt_tone", "slug": "tone-minimal", "version": 1}],
            },
        ],
    )

    response = _import(_client(owner), package, strategy="overwrite")

    assert response.status_code == 200, response.content
    versions = list(GenerationProfile.objects.filter(slug="versioned-profile").order_by("version"))
    assert [(p.version, p.description) for p in versions] == [(1, "v1"), (2, "v2")]
    original.refresh_from_db()
    assert original.block_refs == [{"type": "prompt_tone", "slug": "tone-standard", "version": 1}]


def test_block_overwrite_creates_a_new_version_and_remaps_profiles():
    owner = UserFactory()
    ContentBlockFactory(is_system=False, created_by=owner, slug="ow-block", content="old text")
    package = _package(
        blocks=[{"block_type": "prompt_tone", "slug": "ow-block", "version": 1, "name": "Ow", "content": "new text"}],
        profiles=[
            {
                "name": "Uses ow",
                "slug": "ow-profile",
                "block_refs": [{"type": "prompt_tone", "slug": "ow-block", "version": 1}],
            },
        ],
    )

    response = _import(_client(owner), package, strategy="overwrite")

    assert response.status_code == 200, response.content
    assert response.json()["blocks"] == ["ow-block:2"]
    assert ContentBlock.objects.get(slug="ow-block", version=1).content == "old text"
    assert ContentBlock.objects.get(slug="ow-block", version=2).content == "new text"
    assert GenerationProfile.objects.get(slug="ow-profile").block_refs == [
        {"type": "prompt_tone", "slug": "ow-block", "version": 2},
    ]


def test_v2_package_imports_with_no_presets():
    client = _client(UserFactory())
    package = {
        "kind": "llm-lab-template-package",
        "template_package_schema_version": 2,
        "assets": {"app_specs": [_spec("old_v2_app")], "blocks": [], "profiles": [], "stacks": []},
    }

    response = _import(client, package)

    assert response.status_code == 200, response.content
    assert response.json() == {
        "app_specs": ["old_v2_app"],
        "blocks": [],
        "profiles": [],
        "stacks": [],
        "presets": [],
    }


def test_unsupported_schema_version_is_rejected():
    response = _import(_client(UserFactory()), {**_package(), "template_package_schema_version": 4})

    assert response.status_code == 400
    assert "schema version" in response.json()["detail"]


# ── import: presets ───────────────────────────────────────────────────


def test_preset_import_creates_normalized_presets_and_reuses_identical_ones():
    user = UserFactory()
    client = _client(user)
    package = _package(
        presets=[
            _preset_payload("sampling", "imp-cool", {"llm_params": {"temperature": 0.2, "top_p": None}}),
            _preset_payload("budget", "imp-short", {"max_llm_requests": 20.0}),
        ],
    )

    first = _import(client, package)
    second = _import(client, package)

    assert first.status_code == 200, first.content
    assert first.json()["presets"] == ["sampling/imp-cool:1", "budget/imp-short:1"]
    assert second.json()["presets"] == first.json()["presets"]
    assert RunPreset.objects.filter(slug__startswith="imp-").count() == 2
    cool = RunPreset.objects.get(slug="imp-cool")
    assert cool.config == {"llm_params": {"temperature": 0.2}}
    assert cool.created_by == user
    assert not cool.is_system
    assert RunPreset.objects.get(slug="imp-short").config == {"max_llm_requests": 20}


@pytest.mark.parametrize(
    ("kind", "config", "fragment"),
    [
        ("sampling", {"llm_params": {"provider": {"only": ["x"]}}}, "provider"),
        ("sampling", {"llm_params": {"temperature": 5}}, "temperature"),
        ("harness", {"harness": "aider", "options": {"edit_format": "telepathy"}}, "edit_format"),
        ("budget", {"harness": "aider"}, "Unknown budget preset keys"),
        ("nonsense", {}, "kind must be one of"),
    ],
)
def test_preset_import_validates_configs(kind, config, fragment):
    response = _import(_client(UserFactory()), _package(presets=[_preset_payload(kind, "bad-preset", config)]))

    assert response.status_code == 400
    assert fragment in response.json()["detail"]
    assert not RunPreset.objects.filter(slug="bad-preset").exists()


def test_preset_overwrite_adds_a_version_and_keeps_the_old_one():
    owner = UserFactory()
    create_preset(owner, kind="sampling", slug="imp-temp", name="Temp", config={"llm_params": {"temperature": 0.5}})
    package = _package(presets=[_preset_payload("sampling", "imp-temp", {"llm_params": {"temperature": 0.1}})])

    response = _import(_client(owner), package, strategy="overwrite")

    assert response.status_code == 200, response.content
    assert response.json()["presets"] == ["sampling/imp-temp:2"]
    versions = list(RunPreset.objects.filter(slug="imp-temp").order_by("version"))
    assert [v.config["llm_params"]["temperature"] for v in versions] == [0.5, 0.1]


def test_preset_overwrite_with_identical_content_only_updates_metadata():
    owner = UserFactory()
    preset = create_preset(owner, kind="budget", slug="imp-meta", name="Old name", config={"max_llm_requests": 10})
    package = _package(presets=[_preset_payload("budget", "imp-meta", {"max_llm_requests": 10}, name="New name")])

    response = _import(_client(owner), package, strategy="overwrite")

    assert response.json()["presets"] == ["budget/imp-meta:1"]
    preset.refresh_from_db()
    assert preset.name == "New name"
    assert RunPreset.objects.filter(slug="imp-meta").count() == 1


def test_preset_slug_taken_by_another_user_is_renamed_or_refused():
    create_preset(UserFactory(), kind="sampling", slug="imp-taken", name="Theirs", config={"llm_params": {}})
    importer = UserFactory()
    client = _client(importer)
    package = _package(presets=[_preset_payload("sampling", "imp-taken", {"llm_params": {}})])

    refused = _import(client, package, strategy="error")
    # Their preset is invisible to the importer, so even "overwrite" renames.
    renamed = _import(client, package, strategy="overwrite")

    assert refused.status_code == 400
    assert refused.json()["detail"] == "Preset sampling/imp-taken: slug already in use"
    assert renamed.json()["presets"] == ["sampling/imp-taken-2:1"]
    assert RunPreset.objects.get(slug="imp-taken-2").created_by == importer


def test_reserved_preset_slugs_are_renamed():
    package = _package(presets=[_preset_payload("budget", "team-default", {"max_llm_requests": 12})])
    client = _client(UserFactory())

    refused = _import(client, package, strategy="error")
    renamed = _import(client, package)

    assert refused.status_code == 400
    assert "reserved" in refused.json()["detail"]
    assert renamed.json()["presets"] == ["budget/team-default-2:1"]


def test_harness_preset_at_defaults_reuses_the_system_default():
    package = _package(
        presets=[_preset_payload("harness", "my-aider", {"harness": "aider", "options": {"edit_format": "diff"}})],
    )

    response = _import(_client(UserFactory()), package)

    default = system_default_preset("harness", "aider")
    assert response.json()["presets"] == [f"harness/aider-default:{default.version}"]
    assert not RunPreset.objects.filter(slug="my-aider").exists()


def test_exported_system_preset_is_reused_on_import():
    package = _package(presets=[_preset_payload("sampling", "provider-default", {"llm_params": {}})])

    response = _import(_client(UserFactory()), package)

    assert response.status_code == 200, response.content
    default = system_default_preset("sampling")
    assert response.json()["presets"] == [f"sampling/provider-default:{default.version}"]


# ── starters ──────────────────────────────────────────────────────────


def test_starters_list_counts_every_section():
    response = _client(UserFactory()).get("/api/generation/packages/starters/")

    assert response.status_code == 200
    starters = {item["slug"]: item for item in response.json()}
    agent = starters["agent-benchmark-starter"]
    assert agent["preset_count"] == len(AGENT_STARTER_PRESETS)
    assert (agent["app_spec_count"], agent["block_count"], agent["profile_count"], agent["stack_count"]) == (
        0,
        0,
        0,
        0,
    )
    assert agent["name"] == "Agent benchmark starter"


def test_agent_benchmark_starter_imports_its_presets_once():
    user = UserFactory()
    client = _client(user)
    url = "/api/generation/packages/starters/agent-benchmark-starter/import/"

    first = client.post(url, data=json.dumps({}), content_type="application/json")
    second = client.post(url, data=json.dumps({}), content_type="application/json")

    assert first.status_code == 200, first.content
    assert sorted(first.json()["presets"]) == sorted(f"{k}/{s}:1" for k, s in AGENT_STARTER_PRESETS)
    assert second.json() == first.json()
    rows = RunPreset.objects.filter(created_by=user)
    assert {(p.kind, p.slug): p.config for p in rows} == AGENT_STARTER_PRESETS


def test_second_user_importing_the_starter_gets_renamed_presets():
    url = "/api/generation/packages/starters/agent-benchmark-starter/import/"
    _client(UserFactory()).post(url, data=json.dumps({}), content_type="application/json")

    response = _client(UserFactory()).post(url, data=json.dumps({}), content_type="application/json")

    assert response.status_code == 200, response.content
    # Slugs are global per kind; method keys hash content, so the rename never splits results.
    assert "harness/aider-whole-file-2:1" in response.json()["presets"]


def test_unknown_starter_is_404():
    response = _client(UserFactory()).post(
        "/api/generation/packages/starters/no-such-starter/import/",
        data=json.dumps({}),
        content_type="application/json",
    )

    assert response.status_code == 404


def test_starter_content_files_are_resolved_from_data_dir():
    package = build_starter_template_package("fastapi-stack-starter")

    specs = package["assets"]["app_specs"]
    assert [spec["slug"] for spec in specs] == [
        "analytics_campaign_monitor",
        "operations_incident_center",
        "commerce_subscription_billing",
    ]
    assert all("content_file" not in spec for spec in specs)
    assert package["starter"]["slug"] == "fastapi-stack-starter"


@pytest.mark.parametrize("path", ["../../../config/settings/base.py", "/etc/passwd", "", "requirements/nope.json"])
def test_content_file_must_exist_inside_the_data_dir(path):
    with pytest.raises(ValueError, match="content_file"):
        resolve_content_file(path)


def test_uploaded_packages_never_read_content_files():
    """``content_file`` is a starter-only shortcut; an uploaded spec entry with one is just invalid."""
    package = _package(app_specs=[{"content_file": "requirements/crud_todo_list.json"}])

    response = _import(_client(UserFactory()), package)

    assert response.status_code == 400
    assert "requires name and slug" in response.json()["detail"]


def _spec(slug: str, **extra) -> dict:
    """A minimal app spec that meets data/requirements/schema.json."""
    return {
        "name": slug.replace("_", " ").title(),
        "slug": slug,
        "category": "CRUD",
        "description": "Track things",
        "backend_requirements": ["Thing model"],
        "frontend_requirements": ["Thing list"],
        "api_endpoints": [{"method": "GET", "path": "/api/things", "description": "List"}],
        "data_model": {"name": "Thing", "fields": {"id": "integer"}},
        **extra,
    }


def test_import_validates_app_specs_against_the_schema():
    client = _client(UserFactory())
    response = _import(client, _package(app_specs=[{"name": "Thin", "slug": "thin_app"}]))
    assert response.status_code == 400
    assert "App template thin_app" in response.json()["detail"]
    assert "backend_requirements" in response.json()["detail"]

    ok = _import(client, _package(app_specs=[_spec("valid_app")]))
    assert ok.status_code == 200, ok.content
    assert ok.json()["app_specs"] == ["valid_app"]


def test_import_rejects_unknown_block_type():
    client = _client(UserFactory())
    block = {"slug": "weird-block", "name": "Weird", "content": "x", "block_type": "nonsense"}
    response = _import(client, _package(blocks=[block]))
    assert response.status_code == 400
    assert "unknown block_type 'nonsense'" in response.json()["detail"]
    assert not ContentBlock.objects.filter(slug="weird-block").exists()


def test_import_rejects_profile_on_unknown_stack():
    client = _client(UserFactory())
    profile = {
        "name": "Lost",
        "slug": "lost-profile",
        "scaffolding_slug": "no-such-stack",
        "block_refs": [{"type": "prompt_tone", "slug": "tone-standard"}],
    }
    response = _import(client, _package(profiles=[profile]))
    assert response.status_code == 400
    assert "unknown stack 'no-such-stack'" in response.json()["detail"]
    assert not GenerationProfile.objects.filter(slug="lost-profile").exists()


def test_app_spec_api_validates_against_the_schema():
    client = _client(UserFactory())
    bad = client.post(
        "/api/generation/app-specs/",
        data=json.dumps({"name": "Thin", "slug": "thin_spec"}),
        content_type="application/json",
    )
    assert bad.status_code == 400
    assert bad.json()["errors"]
    assert "backend_requirements" in bad.json()["detail"]

    hyphen = client.post(
        "/api/generation/app-specs/",
        data=json.dumps({**_spec("x"), "slug": "Not-Snake"}),
        content_type="application/json",
    )
    assert hyphen.status_code == 400
    assert "slug" in hyphen.json()["detail"]

    created = client.post(
        "/api/generation/app-specs/",
        data=json.dumps(_spec("ok_spec")),
        content_type="application/json",
    )
    assert created.status_code == 200, created.content
    emptied = client.put(
        "/api/generation/app-specs/ok_spec/",
        data=json.dumps({**_spec("ok_spec"), "api_endpoints": []}),
        content_type="application/json",
    )
    assert emptied.status_code == 400
    assert "api_endpoints" in emptied.json()["detail"]
