from __future__ import annotations

import json

import pytest
from django.test import Client

from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ContentBlockFactory
from backend.generation.tests.factories import GenerationProfileFactory
from backend.users.tests.factories import UserFactory


@pytest.mark.django_db
def test_list_app_templates_is_scoped_to_defaults_and_owner(client):
    user = UserFactory()
    client.force_login(user)

    default_template = AppRequirementTemplateFactory(is_default=True)
    owned_template = AppRequirementTemplateFactory(is_default=False, created_by=user)
    other_users_template = AppRequirementTemplateFactory(is_default=False, created_by=UserFactory())

    response = client.get("/api/generation/app-specs/")

    assert response.status_code == 200
    slugs = {item["slug"] for item in response.json()}
    # The DB also carries the real seeded defaults (auto-seeded post-migrate);
    # only check that ours are visible and the other user's private one isn't.
    assert default_template.slug in slugs
    assert owned_template.slug in slugs
    assert other_users_template.slug not in slugs


@pytest.mark.django_db
def test_non_owner_cannot_update_default_app_template(client):
    user = UserFactory()
    client.force_login(user)
    template = AppRequirementTemplateFactory(is_default=True, created_by=None)

    response = client.put(
        f"/api/generation/app-specs/{template.slug}/",
        data=json.dumps(
            {
                "name": template.name,
                "slug": template.slug,
                "description": "Updated description",
                "backend_requirements": [],
                "frontend_requirements": [],
                "admin_requirements": [],
            },
        ),
        content_type="application/json",
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_bundle_export_yaml_and_import_round_trip(client):
    owner = UserFactory()
    client.force_login(owner)
    block = ContentBlockFactory(is_system=False, created_by=owner)
    bundle = GenerationProfileFactory(
        is_system=False,
        is_default=False,
        created_by=owner,
        block_refs=[{"type": block.block_type, "slug": block.slug, "version": block.version}],
    )

    export_response = client.get(f"/api/generation/profiles/{bundle.slug}/export/?format=yaml")

    assert export_response.status_code == 200
    package_text = export_response.content.decode()
    assert "kind: llm-lab-template-bundle" in package_text

    importer = UserFactory()
    import_client = Client()
    import_client.force_login(importer)
    import_response = import_client.post(
        "/api/generation/profiles/import/",
        data=json.dumps(
            {
                "package_text": package_text,
                "conflict_strategy": "rename",
            },
        ),
        content_type="application/json",
    )

    assert import_response.status_code == 200
    payload = import_response.json()
    assert payload["slug"].startswith(bundle.slug)
    assert payload["is_system"] is False
    assert payload["block_refs"][0]["slug"].startswith(block.slug)


@pytest.mark.django_db
def test_bundle_import_renames_conflicting_block_and_bundle(client):
    user = UserFactory()
    client.force_login(user)
    conflicting_block = ContentBlockFactory(
        slug="shared-block",
        version=1,
        is_system=False,
        created_by=user,
        content="Old content",
    )
    GenerationProfileFactory(
        slug="shared-bundle",
        is_system=False,
        is_default=False,
        created_by=user,
        block_refs=[
            {
                "type": conflicting_block.block_type,
                "slug": conflicting_block.slug,
                "version": conflicting_block.version,
            },
        ],
    )

    package_text = """
bundle_package_schema_version: 1
kind: llm-lab-template-bundle
bundle:
  name: Imported Shared Bundle
  slug: shared-bundle
  description: Imported
  scaffolding_slug: flask-react
  block_refs:
    - type: prompt_tone
      slug: shared-block
      version: 1
  llm_config: {}
blocks:
  - block_type: prompt_tone
    slug: shared-block
    version: 1
    name: Shared Block
    description: Imported block
    content: New imported content
    metadata: {}
"""

    response = client.post(
        "/api/generation/profiles/import/",
        data=json.dumps(
            {
                "package_text": package_text,
                "conflict_strategy": "rename",
            },
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["slug"] != "shared-bundle"
    assert payload["block_refs"][0]["slug"] != "shared-block"


@pytest.mark.django_db
def test_template_package_export_and_import(client):
    owner = UserFactory()
    client.force_login(owner)
    app_template = AppRequirementTemplateFactory(
        is_default=False,
        created_by=owner,
        slug="shared-app",
    )
    block = ContentBlockFactory(
        is_system=False,
        created_by=owner,
        slug="shared-block-export",
    )
    bundle = GenerationProfileFactory(
        is_system=False,
        is_default=False,
        created_by=owner,
        slug="shared-bundle-export",
        scaffolding_slug="shared-stack",
        block_refs=[{"type": block.block_type, "slug": block.slug, "version": block.version}],
    )

    export_response = client.post(
        "/api/generation/packages/export/?format=json",
        data=json.dumps(
            {
                "app_template_slugs": [app_template.slug],
                "bundle_slugs": [bundle.slug],
                "block_refs": [],
            },
        ),
        content_type="application/json",
    )

    assert export_response.status_code == 200
    package = json.loads(export_response.content.decode())
    assert package["kind"] == "llm-lab-template-package"
    assert package["template_package_schema_version"] == 2
    assert package["assets"]["profiles"][0]["slug"] == bundle.slug
    assert package["assets"]["blocks"][0]["slug"] == block.slug

    importer = UserFactory()
    import_client = Client()
    import_client.force_login(importer)
    import_response = import_client.post(
        "/api/generation/packages/import/",
        data=json.dumps(
            {
                "package_text": json.dumps(package),
                "conflict_strategy": "rename",
            },
        ),
        content_type="application/json",
    )

    assert import_response.status_code == 200
    imported = import_response.json()
    # The importer can't see the owner's private assets, so the "rename"
    # strategy creates their own copies under fresh slugs.
    assert imported["app_specs"] == [f"{app_template.slug}-2"]
    assert imported["profiles"] == [f"{bundle.slug}-2"]


@pytest.mark.django_db
def test_import_skips_legacy_prompt_templates_section(client):
    """Packages exported before PromptTemplate removal must still import."""
    user = UserFactory()
    client.force_login(user)

    legacy_package = {
        "template_package_schema_version": 1,
        "kind": "llm-lab-template-package",
        "assets": {
            "app_templates": [
                {
                    "name": "Legacy App",
                    "slug": "legacy-app",
                    "category": "Productivity",
                    "description": "From an old export",
                    "backend_requirements": ["Store items"],
                    "frontend_requirements": [],
                    "admin_requirements": [],
                    "api_endpoints": [],
                    "data_model": {},
                    "admin_api_endpoints": [],
                },
            ],
            "prompt_templates": [
                {
                    "name": "Legacy Prompt",
                    "slug": "legacy-prompt",
                    "stage": "backend",
                    "role": "system",
                    "content": "You are a helpful assistant.",
                    "description": "",
                    "version": 1,
                },
            ],
            "blocks": [],
            "bundles": [],
        },
    }

    response = client.post(
        "/api/generation/packages/import/",
        data=json.dumps(
            {
                "package_text": json.dumps(legacy_package),
                "conflict_strategy": "rename",
            },
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    imported = response.json()
    # v1 keys are upgraded to the v2 vocabulary on import
    assert imported["app_specs"] == ["legacy-app"]
    assert "prompt_templates" not in imported


def _bundle_payload(block, **overrides):
    payload = {
        "name": "Manual Bundle",
        "slug": "manual-bundle",
        "description": "A hand-authored bundle",
        "scaffolding_slug": "flask-react",
        "block_refs": [{"type": block.block_type, "slug": block.slug, "version": block.version}],
        "llm_config": {},
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_create_bundle_creates_version_1(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=False, created_by=user)

    response = client.post(
        "/api/generation/profiles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["slug"] == "manual-bundle"


@pytest.mark.django_db
def test_create_bundle_rejects_duplicate_slug(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=False, created_by=user)
    client.post(
        "/api/generation/profiles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )

    response = client.post(
        "/api/generation/profiles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.django_db
def test_update_bundle_creates_new_version_not_edit_in_place(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=False, created_by=user)
    other_block = ContentBlockFactory(is_system=False, created_by=user)
    client.post(
        "/api/generation/profiles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )

    response = client.put(
        "/api/generation/profiles/manual-bundle/",
        data=json.dumps(_bundle_payload(other_block, description="Updated")),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2

    from backend.generation.models import GenerationProfile

    versions = GenerationProfile.objects.filter(slug="manual-bundle").order_by("version")
    assert [v.version for v in versions] == [1, 2]
    assert versions[0].description != "Updated"  # v1 is untouched
    assert versions[1].description == "Updated"


@pytest.mark.django_db
def test_update_bundle_rejects_no_op_change(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=False, created_by=user)
    client.post(
        "/api/generation/profiles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )

    response = client.put(
        "/api/generation/profiles/manual-bundle/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_delete_bundle_archives_all_versions(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=False, created_by=user)
    other_block = ContentBlockFactory(is_system=False, created_by=user)
    client.post(
        "/api/generation/profiles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )
    client.put(
        "/api/generation/profiles/manual-bundle/",
        data=json.dumps(_bundle_payload(other_block, description="v2")),
        content_type="application/json",
    )

    response = client.delete("/api/generation/profiles/manual-bundle/")

    assert response.status_code == 200
    assert response.json()["archived_versions"] == 2

    from backend.generation.models import GenerationProfile

    assert all(b.is_archived for b in GenerationProfile.objects.filter(slug="manual-bundle"))
    # Archived bundles drop out of the listing.
    list_response = client.get("/api/generation/profiles/")
    assert "manual-bundle" not in {b["slug"] for b in list_response.json()}


@pytest.mark.django_db
def test_non_owner_cannot_update_or_delete_others_bundle(client):
    owner = UserFactory()
    other = UserFactory()
    block = ContentBlockFactory(is_system=False, created_by=owner)

    owner_client = Client()
    owner_client.force_login(owner)
    owner_client.post(
        "/api/generation/profiles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )

    client.force_login(other)
    update_response = client.put(
        "/api/generation/profiles/manual-bundle/",
        data=json.dumps(_bundle_payload(block, description="hijacked")),
        content_type="application/json",
    )
    delete_response = client.delete("/api/generation/profiles/manual-bundle/")

    # A private bundle owned by someone else isn't even visible to `other`,
    # so this 404s rather than 403s — it doesn't leak that the slug exists.
    assert update_response.status_code == 404
    assert delete_response.status_code == 404


@pytest.mark.django_db
def test_non_staff_cannot_edit_visible_system_bundle(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=True)
    GenerationProfileFactory(
        slug="system-under-test",
        is_system=True,
        block_refs=[{"type": block.block_type, "slug": block.slug, "version": block.version}],
    )

    response = client.put(
        "/api/generation/profiles/system-under-test/",
        data=json.dumps(_bundle_payload(block, slug="system-under-test")),
        content_type="application/json",
    )

    # System bundles ARE visible, so a non-owner non-staff user gets a real
    # 403 (not a 404) — this one leaks existence by design.
    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_can_edit_system_bundle(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    block = ContentBlockFactory(is_system=True)
    GenerationProfileFactory(
        slug="system-under-test",
        is_system=True,
        block_refs=[{"type": block.block_type, "slug": block.slug, "version": block.version}],
    )

    response = client.put(
        "/api/generation/profiles/system-under-test/",
        data=json.dumps(_bundle_payload(block, slug="system-under-test", description="staff edit")),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2


@pytest.mark.django_db
def test_list_bundles_returns_only_latest_version_per_slug(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=False, created_by=user)
    other_block = ContentBlockFactory(is_system=False, created_by=user)
    client.post(
        "/api/generation/profiles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )
    client.put(
        "/api/generation/profiles/manual-bundle/",
        data=json.dumps(_bundle_payload(other_block, description="v2")),
        content_type="application/json",
    )

    response = client.get("/api/generation/profiles/")

    matches = [b for b in response.json() if b["slug"] == "manual-bundle"]
    assert len(matches) == 1
    assert matches[0]["version"] == 2


@pytest.mark.django_db
def test_bundle_versions_endpoint_lists_full_history(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=False, created_by=user)
    client.post(
        "/api/generation/profiles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )
    client.put(
        "/api/generation/profiles/manual-bundle/",
        data=json.dumps(_bundle_payload(block, description="v2")),
        content_type="application/json",
    )

    response = client.get("/api/generation/profiles/manual-bundle/versions/")

    assert response.status_code == 200
    versions = [b["version"] for b in response.json()]
    assert versions == [2, 1]


@pytest.mark.django_db
def test_list_and_import_starter_package(client):
    user = UserFactory()
    client.force_login(user)

    list_response = client.get("/api/generation/packages/starters/")

    assert list_response.status_code == 200
    starters = list_response.json()
    assert starters[0]["slug"] == "fastapi-stack-starter"
    assert starters[0]["bundle_count"] == 2

    import_response = client.post(
        "/api/generation/packages/starters/fastapi-stack-starter/import/",
        data=json.dumps({"conflict_strategy": "rename"}),
        content_type="application/json",
    )

    assert import_response.status_code == 200
    payload = import_response.json()
    # The real seeded catalog (auto-seeded post-migrate) already has these
    # slugs, so "rename" mangles them with a numeric suffix — check the
    # renamed copies were created, not the exact unsuffixed names.
    assert any(slug.startswith("analytics_campaign_monitor") for slug in payload["app_specs"])
    assert any(slug.startswith("system-fastapi-react-standard") for slug in payload["profiles"])


@pytest.mark.django_db
def test_profile_suggest_reports_provenance(client):
    user = UserFactory()
    client.force_login(user)
    # crud_todo_list has a seeded app-pilot profile; a fresh factory app doesn't.
    from backend.generation.models import AppRequirementTemplate

    pilot_app = AppRequirementTemplate.objects.get(slug="crud_todo_list")
    plain_app = AppRequirementTemplateFactory(is_default=True)

    response = client.get(
        f"/api/generation/profiles/suggest/?app_ids={pilot_app.id},{plain_app.id}&stack=flask-react",
    )

    assert response.status_code == 200
    by_app = {entry["app_id"]: entry for entry in response.json()}
    assert by_app[pilot_app.id]["provenance"] == "app-pilot"
    assert by_app[pilot_app.id]["slug"] == "app-crud-todo-list"
    assert by_app[plain_app.id]["provenance"] in ("stack-default", "system-default")
    assert by_app[plain_app.id]["profile_id"] is not None


@pytest.mark.django_db
def test_profile_preview_with_app_slug_renders_prompts(client):
    user = UserFactory()
    client.force_login(user)

    response = client.get(
        "/api/generation/profiles/system-scaffolding-standard/preview/?app_slug=crud_todo_list",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["app_slug"] == "crud_todo_list"
    rendered = data["rendered"]
    for stage in ("backend", "frontend"):
        assert rendered[stage]["system"]
        assert rendered[stage]["user"]
    # Materialized, not a raw template: app name substituted, no Jinja braces
    assert "{{" not in rendered["backend"]["user"]
    assert data["effective_llm"]["temperature"] == 0.3


@pytest.mark.django_db
def test_block_new_version_creates_user_owned_next_version(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=True, slug="versioned-block", version=1)

    response = client.post(
        f"/api/generation/blocks/{block.slug}/new-version/",
        data=json.dumps({"content": "Updated {{ name }} content"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == 2
    assert data["is_system"] is False
    assert data["block_type"] == block.block_type
    assert data["name"] == block.name  # carried over when not supplied


@pytest.mark.django_db
def test_block_new_version_rejects_bad_jinja(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=False, created_by=user)

    response = client.post(
        f"/api/generation/blocks/{block.slug}/new-version/",
        data=json.dumps({"content": "{% broken"}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "Jinja2" in response.json()["detail"]


@pytest.mark.django_db
def test_draft_preview_renders_unsaved_block_refs(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(
        is_system=False,
        created_by=user,
        block_type="prompt_stage",
        slug="draft-backend-system",
        content="You build {{ name }}.",
        metadata={"stage": "backend", "role": "system"},
    )

    response = client.post(
        "/api/generation/profiles/preview-draft/",
        data=json.dumps(
            {
                "block_refs": [{"type": block.block_type, "slug": block.slug, "version": block.version}],
                "app_slug": "crud_todo_list",
            },
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["block_count"] == 1
    assert data["prompt_templates"]["backend"]["system"] == "You build {{ name }}."
    assert "{{" not in data["rendered"]["backend"]["system"]


@pytest.mark.django_db
def test_draft_preview_rejects_bad_llm_config(client):
    user = UserFactory()
    client.force_login(user)

    response = client.post(
        "/api/generation/profiles/preview-draft/",
        data=json.dumps({"block_refs": [], "llm_config": {"temperature": 9}}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "temperature" in response.json()["detail"]


@pytest.mark.django_db
def test_package_round_trips_user_stacks(client):
    owner = UserFactory()
    client.force_login(owner)
    stack_payload = {
        "slug": "portable-stack",
        "name": "Portable",
        "has_frontend": False,
        "default_port": 8000,
        "patch_profile": "none",
        "backend_filename": "app.py",
        "backend_base_image": "python:3.12-slim",
        "server_kind": "python",
        "files": {"requirements.txt": "flask\n"},
    }
    created = client.post(
        "/api/generation/stacks/",
        data=json.dumps(stack_payload),
        content_type="application/json",
    )
    assert created.status_code == 200

    export_response = client.post(
        "/api/generation/packages/export/?format=json",
        data=json.dumps({"stack_slugs": ["portable-stack"]}),
        content_type="application/json",
    )
    assert export_response.status_code == 200
    package = json.loads(export_response.content.decode())
    assert package["assets"]["stacks"][0]["slug"] == "portable-stack"
    assert "Dockerfile" not in package["assets"]["stacks"][0]["files"]

    importer = UserFactory()
    import_client = Client()
    import_client.force_login(importer)
    import_response = import_client.post(
        "/api/generation/packages/import/",
        data=json.dumps({"package_text": json.dumps(package), "conflict_strategy": "rename"}),
        content_type="application/json",
    )
    assert import_response.status_code == 200
    # slug collides with the exporter's copy on the same install → renamed
    assert import_response.json()["stacks"] == ["portable-stack-2"]

    from backend.runtime.models import Stack

    row = Stack.objects.get(slug="portable-stack-2")
    assert row.dockerfile_mode == "generated"
    assert row.created_by == importer


@pytest.mark.django_db
def test_package_import_rejects_stack_with_disallowed_base_image(client):
    client.force_login(UserFactory())
    package = {
        "template_package_schema_version": 2,
        "kind": "llm-lab-template-package",
        "assets": {
            "stacks": [
                {
                    "slug": "evil-stack",
                    "name": "Evil",
                    "has_frontend": False,
                    "default_port": 8000,
                    "backend_filename": "app.py",
                    "backend_base_image": "evil/backdoor:latest",
                    "server_kind": "python",
                    "files": {"requirements.txt": "flask\n"},
                },
            ],
        },
    }

    response = client.post(
        "/api/generation/packages/import/",
        data=json.dumps({"package_text": json.dumps(package), "conflict_strategy": "rename"}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "backend_base_image" in response.json()["detail"]
