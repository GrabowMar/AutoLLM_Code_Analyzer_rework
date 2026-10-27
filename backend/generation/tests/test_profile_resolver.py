"""Tests for template bundle resolution and prompt snapshots."""

from __future__ import annotations

import pytest

from backend.generation.models import ContentBlock
from backend.generation.services.profile_resolver import app_requirement_to_dict
from backend.generation.services.profile_resolver import assemble_prompt_templates
from backend.generation.services.profile_resolver import build_resolved_snapshot
from backend.generation.services.profile_resolver import get_profile_for_app
from backend.generation.services.profile_resolver import resolve_block_refs
from backend.generation.services.prompt_renderer import PromptRenderer
from backend.generation.services.spec_models import requirement_content_hash
from backend.generation.services.spec_models import spec_fields_of
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ContentBlockFactory
from backend.generation.tests.factories import GenerationProfileFactory
from backend.users.tests.factories import UserFactory


@pytest.mark.django_db
def test_resolve_block_refs_returns_content():
    block = ContentBlockFactory(
        slug="test-tone",
        version=1,
        block_type=ContentBlock.BlockType.PROMPT_TONE,
        content="Tone fragment",
        is_system=True,
    )
    resolved = resolve_block_refs(
        [{"type": "prompt_tone", "slug": block.slug, "version": 1}],
    )
    assert resolved[0]["resolved_content"] == "Tone fragment"


@pytest.mark.django_db
def test_assemble_prompt_templates_merges_tone_into_system():
    ContentBlockFactory(
        slug="stage-sys",
        version=1,
        block_type=ContentBlock.BlockType.PROMPT_STAGE,
        name="Sys",
        content="Base system",
        metadata={"stage": "backend", "role": "system"},
        is_system=True,
    )
    ContentBlockFactory(
        slug="tone-extra",
        version=1,
        block_type=ContentBlock.BlockType.PROMPT_TONE,
        name="Tone",
        content="Extra tone",
        is_system=True,
    )
    refs = resolve_block_refs(
        [
            {"type": "prompt_stage", "slug": "stage-sys", "version": 1},
            {"type": "prompt_tone", "slug": "tone-extra", "version": 1},
        ],
    )
    templates = assemble_prompt_templates(refs)
    assert "Base system" in templates["backend"]["system"]
    assert "Extra tone" in templates["backend"]["system"]


@pytest.mark.django_db
def test_build_resolved_snapshot_admin_api_endpoints_in_rendered_user_prompt():
    user = UserFactory()
    app_req = AppRequirementTemplateFactory(
        admin_api_endpoints=[{"method": "GET", "path": "/api/admin/stats"}],
    )
    ContentBlockFactory(
        slug="test-backend-user",
        version=1,
        block_type=ContentBlock.BlockType.PROMPT_STAGE,
        content="# {{ name }}\n{{ admin_api_endpoints }}",
        metadata={"stage": "backend", "role": "user"},
        is_system=True,
    )
    ContentBlockFactory(
        slug="test-backend-system",
        version=1,
        block_type=ContentBlock.BlockType.PROMPT_STAGE,
        content="System",
        metadata={"stage": "backend", "role": "system"},
        is_system=True,
    )
    bundle = GenerationProfileFactory(
        block_refs=[
            {"type": "prompt_stage", "slug": "test-backend-system", "version": 1},
            {"type": "prompt_stage", "slug": "test-backend-user", "version": 1},
        ],
    )
    snapshot = build_resolved_snapshot(
        app_requirement=app_req,
        profile=bundle,
        scaffolding_slug="flask-react",
        model=None,
        temperature=0.3,
        max_tokens=8000,
        user=user,
        experiment_seed=42,
    )
    assert snapshot["seed"] == 42
    messages = PromptRenderer().render_messages_from_snapshot(snapshot, stage="backend")
    assert "/api/admin/stats" in messages[1]["content"]


@pytest.mark.django_db
def test_get_profile_for_app_prefers_matching_scaffolding_default():
    user = UserFactory()
    # A synthetic scaffolding_slug that doesn't collide with any real seeded
    # stack, so this test's is_default=True bundle is unambiguously "the"
    # default for it regardless of what the real catalog seeds.
    app_req = AppRequirementTemplateFactory(slug="test-analytics-campaign-monitor")
    GenerationProfileFactory(
        slug="app-test-analytics-campaign-monitor",
        scaffolding_slug="test-other-stack",
        is_system=True,
    )
    fastapi_bundle = GenerationProfileFactory(
        slug="test-fastapi-react-standard",
        scaffolding_slug="test-fastapi-stack",
        is_system=True,
        is_default=True,
    )

    bundle = get_profile_for_app(
        app_req,
        user=user,
        scaffolding_slug="test-fastapi-stack",
    )

    assert bundle == fastapi_bundle


@pytest.mark.django_db
def test_catalog_fallback_uses_the_default_profiles_refs_at_latest_system_versions():
    from backend.generation.services.profile_resolver import catalog_default_block_refs

    refs = catalog_default_block_refs()

    assert [ref["slug"] for ref in refs] == [
        "base-backend-system",
        "base-backend-user",
        "base-frontend-system",
        "base-frontend-user",
        "tone-standard",
        "rules-flask-global",
    ]
    for ref in refs:
        latest = ContentBlock.objects.filter(slug=ref["slug"], is_system=True).order_by("-version").first()
        assert ref["version"] == latest.version
        assert ref["type"] == latest.block_type


@pytest.mark.django_db
def test_profile_for_app_without_stack_is_the_global_default():
    app_req = AppRequirementTemplateFactory(slug="no-pilot-app")

    assert get_profile_for_app(app_req).slug == "system-scaffolding-standard"


@pytest.mark.django_db
def test_app_dict_lists_models_and_names_the_spec_content():
    legacy = AppRequirementTemplateFactory(data_model={"name": "Book", "fields": {"id": "integer"}}, version=4)
    app = app_requirement_to_dict(legacy)
    assert app["data_models"] == [{"name": "Book", "fields": {"id": "integer"}}]
    assert app["data_model"] == {"name": "Book", "fields": {"id": "integer"}}
    assert app["version"] == 4
    assert app["content_hash"] == requirement_content_hash(spec_fields_of(legacy))
    assert len(app["content_hash"]) == 64

    models = [
        {"name": "Book", "fields": {"id": "integer"}},
        {"name": "Review", "description": "A rating", "fields": {"id": "integer", "book_id": "integer"}},
    ]
    v3 = AppRequirementTemplateFactory(data_model=models, spec_schema_version=3)
    app = app_requirement_to_dict(v3)
    assert app["data_models"] == models
    assert app["data_model"] == models[0]
    assert app["spec_schema_version"] == 3
    assert app["content_hash"] != app_requirement_to_dict(legacy)["content_hash"]

    empty = app_requirement_to_dict(AppRequirementTemplateFactory())
    assert empty["data_models"] == []
    assert empty["data_model"] == {}


@pytest.mark.django_db
def test_spec_content_change_changes_prompt_hash_and_fingerprint():
    app_req = AppRequirementTemplateFactory(data_model={"name": "Book", "fields": {"id": "integer"}})
    kwargs = {
        "profile": None,
        "scaffolding_slug": "flask-react",
        "model": None,
        "temperature": 0.3,
        "max_tokens": 8000,
        "user": None,
        "experiment_seed": 7,
    }
    before = build_resolved_snapshot(app_requirement=app_req, **kwargs)
    again = build_resolved_snapshot(app_requirement=app_req, **kwargs)
    assert before["prompt_hash"] == again["prompt_hash"]
    assert before["run_fingerprint"] == again["run_fingerprint"]

    app_req.api_endpoints = [{"method": "GET", "path": "/api/books", "description": "List", "auth": "public"}]
    app_req.save()
    after = build_resolved_snapshot(app_requirement=app_req, **kwargs)
    assert after["app_requirement"]["content_hash"] != before["app_requirement"]["content_hash"]
    assert after["prompt_hash"] != before["prompt_hash"]
    assert after["run_fingerprint"] != before["run_fingerprint"]
