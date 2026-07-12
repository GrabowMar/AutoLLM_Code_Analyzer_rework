"""Tests for DB-backed stacks: seeding, resolution, provisioning, snapshot pinning."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.runtime.models import Stack
from backend.runtime.seeding import seed_stacks
from backend.runtime.services import scaffolding as svc


@pytest.mark.django_db
def test_seed_stacks_creates_builtin_rows():
    # post_migrate already seeded; a second run must be a no-op.
    created, updated = seed_stacks()
    assert created == 0
    assert updated == 0

    slugs = set(Stack.objects.filter(is_builtin=True).values_list("slug", flat=True))
    assert {"flask-react", "generic-python", "fastapi-vue", "fastapi-react"} <= slugs

    flask = Stack.objects.get(slug="flask-react", version=1)
    assert flask.has_frontend is True
    assert flask.patch_profile == "flask"
    assert flask.aliases == ["react-flask"]
    assert flask.dockerfile_mode == Stack.DockerfileMode.BUNDLED
    assert "Dockerfile" in flask.files
    assert "app.py" in flask.files
    assert "frontend/src/App.jsx" in flask.files
    assert flask.content_hash


@pytest.mark.django_db
def test_seed_stacks_bumps_version_on_content_change():
    flask = Stack.objects.get(slug="flask-react", is_builtin=True)
    flask.content_hash = "stale"
    flask.save(update_fields=["content_hash"])

    created, updated = seed_stacks()

    assert created == 0
    assert updated == 1
    versions = list(
        Stack.objects.filter(slug="flask-react").order_by("version").values_list("version", flat=True),
    )
    assert versions == [1, 2]


@pytest.mark.django_db
def test_db_resolution_handles_aliases():
    assert svc.canonical_stack_slug("react-flask") == "flask-react"
    assert svc.is_known_stack_slug("react-flask")
    config = svc.get_stack_config("flask-react")
    assert config["canonical_slug"] == "flask-react"
    assert config["stack_version"] >= 1


@pytest.mark.django_db
def test_apply_scaffold_materializes_from_db_row(tmp_path: Path):
    app_req = AppRequirementTemplateFactory(slug="db-stack-app")
    job = GenerationJobFactory(
        mode="scaffolding",
        stack_slug="flask-react",
        app_requirement=app_req,
        result_data={"backend_code": "print('hi')", "frontend_code": ""},
    )
    dest = tmp_path / "build"

    svc.apply_scaffold(job, dest, phase=svc.ScaffoldPhase.BUILD)

    row = Stack.objects.filter(slug="flask-react", is_archived=False).order_by("-version").first()
    assert (dest / "Dockerfile").read_text() == row.files["Dockerfile"]
    assert (dest / "frontend" / "package.json").is_file()
    assert (dest / "app.py").is_file()


@pytest.mark.django_db
def test_apply_scaffold_honors_pinned_stack_version(tmp_path: Path):
    # Freeze a doctored v2 of flask-react and pin a job to v1.
    v1 = Stack.objects.get(slug="flask-react", version=1)
    Stack.objects.create(
        slug="flask-react",
        version=2,
        is_builtin=True,
        has_frontend=v1.has_frontend,
        default_port=v1.default_port,
        patch_profile=v1.patch_profile,
        backend_filename=v1.backend_filename,
        files={**v1.files, "MARKER.txt": "v2 content"},
        content_hash="doctored",
    )
    app_req = AppRequirementTemplateFactory(slug="pinned-app")
    job = GenerationJobFactory(
        mode="scaffolding",
        stack_slug="flask-react",
        app_requirement=app_req,
        result_data={"backend_code": "print('hi')"},
        resolved_bundle={
            "bundle_schema_version": 3,
            "scaffolding_slug": "flask-react",
            "stack": {"slug": "flask-react", "version": 1, "content_hash": v1.content_hash},
        },
    )
    dest = tmp_path / "build"

    svc.apply_scaffold(job, dest, phase=svc.ScaffoldPhase.BUILD)

    assert not (dest / "MARKER.txt").exists()  # v2-only file must not appear


@pytest.mark.django_db
def test_snapshot_pins_stack_and_fingerprint_tracks_content():
    from backend.generation.services.profile_resolver import build_resolved_snapshot
    from backend.generation.tests.factories import GenerationProfileFactory

    app = AppRequirementTemplateFactory()
    profile = GenerationProfileFactory(scaffolding_slug="flask-react")

    snapshot = build_resolved_snapshot(
        app_requirement=app,
        profile=profile,
        scaffolding_slug="flask-react",
        model=None,
        temperature=0.3,
        max_tokens=32000,
        user=None,
        experiment_seed=7,
    )

    row = Stack.objects.filter(slug="flask-react", is_archived=False).order_by("-version").first()
    assert snapshot["stack"] == {
        "slug": "flask-react",
        "version": row.version,
        "content_hash": row.content_hash,
    }

    # Same prompt material on a different skeleton: prompt_hash stable,
    # fingerprint changes.
    doctored = dict(snapshot)
    doctored["stack"] = {**snapshot["stack"], "content_hash": "different"}
    from backend.generation.services.profile_resolver import run_fingerprint

    assert run_fingerprint(doctored) != snapshot["run_fingerprint"]


@pytest.mark.django_db
def test_stacks_api_serves_db_rows(client):
    from backend.users.tests.factories import UserFactory

    client.force_login(UserFactory())

    response = client.get("/api/generation/stacks/")
    assert response.status_code == 200
    by_slug = {row["slug"]: row for row in response.json()}
    assert by_slug["flask-react"]["is_builtin"] is True
    assert by_slug["flask-react"]["file_count"] > 5

    detail = client.get("/api/generation/stacks/flask-react/")
    assert detail.status_code == 200
    assert "Dockerfile" in detail.json()["files"]
