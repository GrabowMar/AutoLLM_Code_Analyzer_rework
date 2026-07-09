"""Tests for template bundle resolution and prompt snapshots."""

from __future__ import annotations

import pytest

from backend.generation.models import ContentBlock
from backend.generation.services.bundle_resolver import assemble_prompt_templates
from backend.generation.services.bundle_resolver import build_resolved_bundle
from backend.generation.services.bundle_resolver import get_bundle_for_app
from backend.generation.services.bundle_resolver import resolve_block_refs
from backend.generation.services.prompt_renderer import PromptRenderer
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ContentBlockFactory
from backend.generation.tests.factories import TemplateBundleFactory
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
def test_build_resolved_bundle_admin_api_endpoints_in_rendered_user_prompt():
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
    bundle = TemplateBundleFactory(
        block_refs=[
            {"type": "prompt_stage", "slug": "test-backend-system", "version": 1},
            {"type": "prompt_stage", "slug": "test-backend-user", "version": 1},
        ],
    )
    snapshot = build_resolved_bundle(
        app_requirement=app_req,
        template_bundle=bundle,
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
def test_get_bundle_for_app_prefers_matching_scaffolding_default():
    user = UserFactory()
    # A synthetic scaffolding_slug that doesn't collide with any real seeded
    # stack, so this test's is_default=True bundle is unambiguously "the"
    # default for it regardless of what the real catalog seeds.
    app_req = AppRequirementTemplateFactory(slug="test-analytics-campaign-monitor")
    TemplateBundleFactory(
        slug="app-test-analytics-campaign-monitor",
        scaffolding_slug="test-other-stack",
        is_system=True,
    )
    fastapi_bundle = TemplateBundleFactory(
        slug="test-fastapi-react-standard",
        scaffolding_slug="test-fastapi-stack",
        is_system=True,
        is_default=True,
    )

    bundle = get_bundle_for_app(
        app_req,
        user=user,
        scaffolding_slug="test-fastapi-stack",
    )

    assert bundle == fastapi_bundle
