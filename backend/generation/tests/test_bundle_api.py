from __future__ import annotations

import json

import pytest
from django.test import Client

from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ContentBlockFactory
from backend.generation.tests.factories import PromptTemplateFactory
from backend.generation.tests.factories import TemplateBundleFactory
from backend.users.tests.factories import UserFactory


@pytest.mark.django_db
def test_list_app_templates_is_scoped_to_defaults_and_owner(client):
    user = UserFactory()
    client.force_login(user)

    default_template = AppRequirementTemplateFactory(is_default=True)
    owned_template = AppRequirementTemplateFactory(is_default=False, created_by=user)
    other_users_template = AppRequirementTemplateFactory(is_default=False, created_by=UserFactory())

    response = client.get("/api/generation/app-templates/")

    assert response.status_code == 200
    slugs = {item["slug"] for item in response.json()}
    # The DB also carries the real seeded defaults (auto-seeded post-migrate);
    # only check that ours are visible and the other user's private one isn't.
    assert default_template.slug in slugs
    assert owned_template.slug in slugs
    assert other_users_template.slug not in slugs


@pytest.mark.django_db
def test_non_owner_cannot_update_default_prompt_template(client):
    user = UserFactory()
    client.force_login(user)
    prompt = PromptTemplateFactory(is_default=True, created_by=None)

    response = client.put(
        f"/api/generation/prompt-templates/{prompt.slug}/",
        data=json.dumps(
            {
                "name": prompt.name,
                "slug": prompt.slug,
                "stage": prompt.stage,
                "role": prompt.role,
                "content": "Updated prompt",
                "description": prompt.description,
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
    bundle = TemplateBundleFactory(
        is_system=False,
        is_default=False,
        created_by=owner,
        block_refs=[{"type": block.block_type, "slug": block.slug, "version": block.version}],
    )

    export_response = client.get(f"/api/generation/bundles/{bundle.slug}/export/?format=yaml")

    assert export_response.status_code == 200
    package_text = export_response.content.decode()
    assert "kind: llm-lab-template-bundle" in package_text

    importer = UserFactory()
    import_client = Client()
    import_client.force_login(importer)
    import_response = import_client.post(
        "/api/generation/bundles/import/",
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
    TemplateBundleFactory(
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
        "/api/generation/bundles/import/",
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
    prompt = PromptTemplateFactory(
        is_default=False,
        created_by=owner,
        slug="shared-prompt",
    )
    block = ContentBlockFactory(
        is_system=False,
        created_by=owner,
        slug="shared-block-export",
    )
    bundle = TemplateBundleFactory(
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
                "prompt_template_slugs": [prompt.slug],
                "bundle_slugs": [bundle.slug],
                "block_refs": [],
            },
        ),
        content_type="application/json",
    )

    assert export_response.status_code == 200
    package = json.loads(export_response.content.decode())
    assert package["kind"] == "llm-lab-template-package"
    assert package["assets"]["bundles"][0]["slug"] == bundle.slug
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
    assert imported["app_templates"] == [f"{app_template.slug}-2"]
    assert imported["prompt_templates"] == [f"{prompt.slug}-2"]
    assert imported["bundles"] == [f"{bundle.slug}-2"]


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
        "/api/generation/bundles/",
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
        "/api/generation/bundles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )

    response = client.post(
        "/api/generation/bundles/",
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
        "/api/generation/bundles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )

    response = client.put(
        "/api/generation/bundles/manual-bundle/",
        data=json.dumps(_bundle_payload(other_block, description="Updated")),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2

    from backend.generation.models import TemplateBundle

    versions = TemplateBundle.objects.filter(slug="manual-bundle").order_by("version")
    assert [v.version for v in versions] == [1, 2]
    assert versions[0].description != "Updated"  # v1 is untouched
    assert versions[1].description == "Updated"


@pytest.mark.django_db
def test_update_bundle_rejects_no_op_change(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=False, created_by=user)
    client.post(
        "/api/generation/bundles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )

    response = client.put(
        "/api/generation/bundles/manual-bundle/",
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
        "/api/generation/bundles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )
    client.put(
        "/api/generation/bundles/manual-bundle/",
        data=json.dumps(_bundle_payload(other_block, description="v2")),
        content_type="application/json",
    )

    response = client.delete("/api/generation/bundles/manual-bundle/")

    assert response.status_code == 200
    assert response.json()["archived_versions"] == 2

    from backend.generation.models import TemplateBundle

    assert all(b.is_archived for b in TemplateBundle.objects.filter(slug="manual-bundle"))
    # Archived bundles drop out of the listing.
    list_response = client.get("/api/generation/bundles/")
    assert "manual-bundle" not in {b["slug"] for b in list_response.json()}


@pytest.mark.django_db
def test_non_owner_cannot_update_or_delete_others_bundle(client):
    owner = UserFactory()
    other = UserFactory()
    block = ContentBlockFactory(is_system=False, created_by=owner)

    owner_client = Client()
    owner_client.force_login(owner)
    owner_client.post(
        "/api/generation/bundles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )

    client.force_login(other)
    update_response = client.put(
        "/api/generation/bundles/manual-bundle/",
        data=json.dumps(_bundle_payload(block, description="hijacked")),
        content_type="application/json",
    )
    delete_response = client.delete("/api/generation/bundles/manual-bundle/")

    # A private bundle owned by someone else isn't even visible to `other`,
    # so this 404s rather than 403s — it doesn't leak that the slug exists.
    assert update_response.status_code == 404
    assert delete_response.status_code == 404


@pytest.mark.django_db
def test_non_staff_cannot_edit_visible_system_bundle(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=True)
    TemplateBundleFactory(
        slug="system-under-test",
        is_system=True,
        block_refs=[{"type": block.block_type, "slug": block.slug, "version": block.version}],
    )

    response = client.put(
        "/api/generation/bundles/system-under-test/",
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
    TemplateBundleFactory(
        slug="system-under-test",
        is_system=True,
        block_refs=[{"type": block.block_type, "slug": block.slug, "version": block.version}],
    )

    response = client.put(
        "/api/generation/bundles/system-under-test/",
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
        "/api/generation/bundles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )
    client.put(
        "/api/generation/bundles/manual-bundle/",
        data=json.dumps(_bundle_payload(other_block, description="v2")),
        content_type="application/json",
    )

    response = client.get("/api/generation/bundles/")

    matches = [b for b in response.json() if b["slug"] == "manual-bundle"]
    assert len(matches) == 1
    assert matches[0]["version"] == 2


@pytest.mark.django_db
def test_bundle_versions_endpoint_lists_full_history(client):
    user = UserFactory()
    client.force_login(user)
    block = ContentBlockFactory(is_system=False, created_by=user)
    client.post(
        "/api/generation/bundles/",
        data=json.dumps(_bundle_payload(block)),
        content_type="application/json",
    )
    client.put(
        "/api/generation/bundles/manual-bundle/",
        data=json.dumps(_bundle_payload(block, description="v2")),
        content_type="application/json",
    )

    response = client.get("/api/generation/bundles/manual-bundle/versions/")

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
    assert any(slug.startswith("analytics_campaign_monitor") for slug in payload["app_templates"])
    assert any(slug.startswith("system-fastapi-react-standard") for slug in payload["bundles"])
