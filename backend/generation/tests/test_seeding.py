"""Tests for content-hash-versioned seeding (backend/generation/seeding.py)."""

from __future__ import annotations

import pytest

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ContentBlock
from backend.generation.models import GenerationProfile
from backend.generation.seeding import _upsert_content_block_version
from backend.generation.seeding import _upsert_profile_version
from backend.generation.seeding import seed_all
from backend.generation.seeding import seed_content_blocks
from backend.generation.seeding import seed_profiles
from backend.generation.seeding import seed_requirements

pytestmark = pytest.mark.django_db


def test_seed_all_is_idempotent():
    """Running the full seeder twice must not create duplicate versions."""
    seed_all()
    block_count = ContentBlock.objects.count()
    bundle_count = GenerationProfile.objects.count()
    requirement_count = AppRequirementTemplate.objects.count()

    seed_all()

    assert ContentBlock.objects.count() == block_count
    assert GenerationProfile.objects.count() == bundle_count
    assert AppRequirementTemplate.objects.count() == requirement_count


def test_seed_content_blocks_bumps_version_on_content_change():
    seed_content_blocks()
    block = ContentBlock.objects.filter(slug="tone-standard").order_by("-version").first()
    assert block is not None
    original_version = block.version

    # Simulate the source file changing: the seeder should create version+1,
    # not overwrite the existing row's content.
    block.content_hash = "stale-hash-simulating-an-edited-source-file"
    block.save(update_fields=["content_hash"])

    seed_content_blocks()

    versions = list(ContentBlock.objects.filter(slug="tone-standard").order_by("version"))
    assert len(versions) == 2
    assert versions[0].version == original_version
    assert versions[1].version == original_version + 1
    # The stale row is untouched — old snapshots that reference it still resolve.
    assert versions[0].content_hash == "stale-hash-simulating-an-edited-source-file"


def test_seed_content_blocks_cosmetic_change_does_not_bump_version():
    seed_content_blocks()
    block = ContentBlock.objects.filter(slug="tone-standard", version=1).get()
    block.description = "a manually edited description"
    block.save(update_fields=["description"])

    seed_content_blocks()

    versions = ContentBlock.objects.filter(slug="tone-standard")
    assert versions.count() == 1
    refreshed = versions.get()
    # Cosmetic fields are resynced to the source on the existing version.
    assert refreshed.description != "a manually edited description"


def test_seed_profiles_bumps_version_on_block_refs_change():
    seed_profiles()
    bundle = GenerationProfile.objects.filter(slug="system-scaffolding-standard").order_by("-version").first()
    assert bundle is not None
    original_version = bundle.version

    bundle.content_hash = "stale-hash"
    bundle.save(update_fields=["content_hash"])

    seed_profiles()

    versions = list(GenerationProfile.objects.filter(slug="system-scaffolding-standard").order_by("version"))
    assert len(versions) == 2
    assert versions[1].version == original_version + 1


def test_seed_requirements_bumps_version_field_on_change():
    seed_requirements()
    req = AppRequirementTemplate.objects.get(slug="crud_todo_list")
    assert req.version == 1
    assert req.content_hash

    req.content_hash = "stale-hash"
    req.save(update_fields=["content_hash"])

    seed_requirements()

    req.refresh_from_db()
    assert req.version == 2


def test_seed_requirements_does_not_ingest_schema_json_as_a_spec():
    """schema.json lives alongside the *.json specs it validates; it must not be seeded as one."""
    seed_requirements()
    assert not AppRequirementTemplate.objects.filter(slug="schema").exists()


def test_seed_app_profiles_does_not_duplicate_for_every_requirement():
    """The blanket per-requirement auto-bundle generator was removed.

    Only apps with a real manifest under requirements/manifests/ get a
    dedicated bundle; the other ~31 seeded requirements fall back to the
    system default at resolve time instead of carrying a near-duplicate row.
    """
    seed_all()
    app_bundle_slugs = set(
        GenerationProfile.objects.filter(slug__startswith="app-", is_system=True).values_list("slug", flat=True),
    )
    # Real manifests (see backend/generation/data/requirements/manifests/).
    assert "app-crud-todo-list" in app_bundle_slugs
    assert "app-auth-user-login" in app_bundle_slugs
    assert "app-content-recipe-list" in app_bundle_slugs
    # No auto-generated bundle for requirements without a manifest.
    assert "app-api-url-shortener" not in app_bundle_slugs


def test_upsert_content_block_version_creates_v1_for_new_slug():
    created, updated = _upsert_content_block_version(
        ContentBlock,
        "default",
        {"slug": "brand-new-block", "name": "New", "description": "", "content": "hello"},
        default_block_type="prompt_tone",
        log=None,
    )
    assert (created, updated) == (1, 0)
    assert ContentBlock.objects.get(slug="brand-new-block", version=1).content == "hello"


def test_upsert_content_block_version_reuses_unchanged_content():
    seed = {"slug": "stable-block", "name": "Stable", "description": "", "content": "same content"}
    _upsert_content_block_version(ContentBlock, "default", seed, default_block_type="prompt_tone", log=None)
    created, updated = _upsert_content_block_version(
        ContentBlock,
        "default",
        seed,
        default_block_type="prompt_tone",
        log=None,
    )
    assert (created, updated) == (0, 0)
    assert ContentBlock.objects.filter(slug="stable-block").count() == 1


def test_upsert_profile_version_creates_v1_for_new_slug():
    created, updated = _upsert_profile_version(
        GenerationProfile,
        "default",
        slug="brand-new-bundle",
        name="New Bundle",
        description="",
        scaffolding_slug="flask-react",
        block_refs=[{"type": "prompt_stage", "slug": "x", "version": 1}],
        is_default=False,
        log=None,
    )
    assert (created, updated) == (1, 0)
    assert GenerationProfile.objects.get(slug="brand-new-bundle", version=1)


def test_seeder_takes_the_next_free_version_after_a_user_version():
    """A user's own vN+1 of a system block must not make the next seed pass collide."""
    from backend.users.tests.factories import UserFactory

    seed_content_blocks()
    system = ContentBlock.objects.filter(slug="tone-standard", is_system=True).order_by("-version").first()
    ContentBlock.objects.create(
        block_type=system.block_type,
        slug="tone-standard",
        version=system.version + 1,
        name="My tone",
        content="my own tone",
        is_system=False,
        created_by=UserFactory(),
    )
    # Simulate an edited source file.
    system.content_hash = "stale-hash-simulating-an-edited-source-file"
    system.save(update_fields=["content_hash"])

    seed_content_blocks()

    newest = ContentBlock.objects.filter(slug="tone-standard", is_system=True).order_by("-version").first()
    assert newest.version == system.version + 2
    assert ContentBlock.objects.get(slug="tone-standard", version=system.version + 1).is_system is False


def test_system_profiles_pin_the_latest_system_block_version():
    seed_all()
    block = ContentBlock.objects.filter(slug="tone-standard", is_system=True).order_by("-version").first()
    block.content_hash = "stale-hash-simulating-an-edited-source-file"
    block.save(update_fields=["content_hash"])

    seed_content_blocks()
    seed_profiles()

    bumped = block.version + 1
    for slug in ("system-scaffolding-standard", "system-fastapi-vue-standard", "system-fastapi-react-standard"):
        latest = GenerationProfile.objects.filter(slug=slug, is_system=True).order_by("-version").first()
        refs = {ref["slug"]: ref["version"] for ref in latest.block_refs}
        assert refs["tone-standard"] == bumped, slug
        # Older profile versions keep pointing at what they pinned.
        assert latest.version > 1


def test_seed_profiles_skips_a_profile_whose_block_is_missing(monkeypatch, tmp_path):
    from backend.generation import seeding

    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        "default_bundle_slug: broken-profile\n"
        "profiles:\n"
        "  - slug: broken-profile\n"
        "    name: Broken\n"
        "    scaffolding_slug: flask-react\n"
        "    block_refs:\n"
        "      - {type: prompt_tone, slug: no-such-block}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(seeding, "CATALOG_PATH", catalog)

    assert seed_profiles() == (0, 0)
    assert not GenerationProfile.objects.filter(slug="broken-profile").exists()


def test_global_default_profile_is_the_catalog_default_bundle():
    from backend.generation.services.profile_resolver import get_default_profile

    seed_all()

    # Every stack has its own default; without a stack the catalog names one.
    assert get_default_profile().slug == "system-scaffolding-standard"
    assert get_default_profile(scaffolding_slug="fastapi-vue").slug == "system-fastapi-vue-standard"
    assert get_default_profile(scaffolding_slug="fastapi-react").slug == "system-fastapi-react-standard"
