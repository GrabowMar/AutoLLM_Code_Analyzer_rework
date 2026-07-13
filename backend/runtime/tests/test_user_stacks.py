"""Tests for user-authored stacks: validation, Dockerfile generation, CRUD, gating."""

from __future__ import annotations

import json

import pytest

from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.runtime.models import Stack
from backend.runtime.services import scaffolding as svc
from backend.runtime.services.dockerfile_gen import generate_dockerfile
from backend.runtime.services.stack_validation import validate_stack_config
from backend.runtime.services.stack_validation import validate_stack_files
from backend.users.tests.factories import UserFactory

# ── File-map validation ───────────────────────────────────────────────


def test_validate_files_accepts_normal_skeleton():
    files = {
        "app.py": "print('hi')",
        "requirements.txt": "flask\n",
        "frontend/package.json": "{}",
        "frontend/src/App.jsx": "export default () => null;",
    }
    assert validate_stack_files(files) == []


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../escape.py",
        "a/../../escape.py",
        "~/.ssh/authorized_keys",
        "a//b.py",
        "dir/./file.py",
        "windows\\path.py",
        "Dockerfile",
        "sub/Dockerfile",
        "docker-compose.yml",
        ".dockerignore",
        ".gitignore",
        "a\x00b",
    ],
)
def test_validate_files_rejects_adversarial_paths(path):
    assert validate_stack_files({path: "x"})


def test_validate_files_enforces_caps():
    assert validate_stack_files({f"f{i}.txt": "x" for i in range(65)})
    assert validate_stack_files({"big.txt": "x" * (128 * 1024 + 1)})
    assert validate_stack_files("not a dict")


def test_validate_config_checks_allowlist_and_port():
    ok = validate_stack_config(
        backend_base_image="python:3.12-slim",
        frontend_base_image="node:20-alpine",
        has_frontend=True,
        default_port=9000,
        backend_filename="app.py",
    )
    assert ok == []

    errors = validate_stack_config(
        backend_base_image="evil/backdoor:latest",
        frontend_base_image="",
        has_frontend=True,
        default_port=80,
        backend_filename="../app.py",
    )
    assert len(errors) == 4  # backend image, frontend image, port, filename


# ── Dockerfile generation ─────────────────────────────────────────────


def _stack(**overrides) -> Stack:
    defaults = {
        "slug": "my-stack",
        "has_frontend": True,
        "default_port": 8000,
        "backend_filename": "app.py",
        "backend_base_image": "python:3.12-slim",
        "frontend_base_image": "node:20-alpine",
        "server_kind": "python",
        "dockerfile_mode": Stack.DockerfileMode.GENERATED,
    }
    defaults.update(overrides)
    return Stack(**defaults)


def test_generated_dockerfile_fullstack_python():
    text = generate_dockerfile(_stack())
    assert "FROM node:20-alpine AS frontend-builder" in text
    assert "FROM python:3.12-slim" in text
    assert "COPY --from=frontend-builder /frontend/dist ./static" in text
    assert "USER appuser" in text
    assert "EXPOSE 8000" in text
    assert 'CMD ["python", "app.py"]' in text


def test_generated_dockerfile_backend_only_uvicorn():
    text = generate_dockerfile(
        _stack(has_frontend=False, server_kind="uvicorn", backend_filename="main.py", default_port=9000),
    )
    assert "frontend-builder" not in text
    assert "EXPOSE 9000" in text
    assert 'CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]' in text


# ── CRUD API ──────────────────────────────────────────────────────────


def _payload(**overrides) -> dict:
    payload = {
        "slug": "my-flask",
        "name": "My Flask",
        "description": "custom skeleton",
        "has_frontend": False,
        "default_port": 8000,
        "patch_profile": "flask",
        "backend_filename": "app.py",
        "backend_base_image": "python:3.12-slim",
        "server_kind": "python",
        "files": {"requirements.txt": "flask\n", "README.md": "hi"},
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_stack_create_update_archive_flow(client):
    user = UserFactory()
    client.force_login(user)

    created = client.post(
        "/api/generation/stacks/",
        data=json.dumps(_payload()),
        content_type="application/json",
    )
    assert created.status_code == 200
    body = created.json()
    assert body["version"] == 1
    assert body["is_builtin"] is False
    assert body["dockerfile_mode"] == "generated"
    assert "FROM python:3.12-slim" in body["generated_dockerfile"]

    # visible in list + usable for resolution
    assert svc.is_known_stack_slug("my-flask")
    listing = client.get("/api/generation/stacks/")
    assert "my-flask" in {row["slug"] for row in listing.json()}

    updated = client.put(
        "/api/generation/stacks/my-flask/",
        data=json.dumps(_payload(files={"requirements.txt": "flask\nrequests\n"})),
        content_type="application/json",
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    noop = client.put(
        "/api/generation/stacks/my-flask/",
        data=json.dumps(_payload(files={"requirements.txt": "flask\nrequests\n"})),
        content_type="application/json",
    )
    assert noop.status_code == 400

    archived = client.delete("/api/generation/stacks/my-flask/")
    assert archived.status_code == 200
    assert archived.json()["archived_versions"] == 2
    assert not Stack.objects.filter(slug="my-flask", is_archived=False).exists()


@pytest.mark.django_db
def test_stack_create_rejects_bad_files_and_builtin_slug(client):
    client.force_login(UserFactory())

    bad = client.post(
        "/api/generation/stacks/",
        data=json.dumps(_payload(files={"../escape.py": "x"})),
        content_type="application/json",
    )
    assert bad.status_code == 400
    assert "traversal" in bad.json()["detail"]

    dupe = client.post(
        "/api/generation/stacks/",
        data=json.dumps(_payload(slug="flask-react")),
        content_type="application/json",
    )
    assert dupe.status_code == 400


@pytest.mark.django_db
def test_only_owner_can_update_and_builtins_are_immutable(client):
    owner = UserFactory()
    client.force_login(owner)
    client.post("/api/generation/stacks/", data=json.dumps(_payload()), content_type="application/json")

    from django.test import Client

    other = Client()
    other.force_login(UserFactory())
    response = other.put(
        "/api/generation/stacks/my-flask/",
        data=json.dumps(_payload(description="hijack")),
        content_type="application/json",
    )
    assert response.status_code == 403

    builtin = client.put(
        "/api/generation/stacks/flask-react/",
        data=json.dumps(_payload(slug="flask-react")),
        content_type="application/json",
    )
    assert builtin.status_code == 403


@pytest.mark.django_db
def test_approval_gate_blocks_other_users_jobs(client, settings):
    settings.STACK_REQUIRE_APPROVAL = True
    owner = UserFactory()
    client.force_login(owner)
    client.post("/api/generation/stacks/", data=json.dumps(_payload()), content_type="application/json")

    row = Stack.objects.get(slug="my-flask")
    assert row.is_approved is False

    from backend.runtime.services.stack_validation import stack_usable_by

    assert stack_usable_by(row, owner) is True  # owners can always use their own
    assert stack_usable_by(row, UserFactory()) is False

    staff = UserFactory(is_staff=True)
    from django.test import Client

    staff_client = Client()
    staff_client.force_login(staff)
    approved = staff_client.post("/api/generation/stacks/my-flask/approve/")
    assert approved.status_code == 200
    row.refresh_from_db()
    assert row.is_approved is True
    assert stack_usable_by(row, UserFactory()) is True


# ── Provisioning ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_apply_scaffold_generates_dockerfile_for_user_stack(tmp_path):
    user = UserFactory()
    Stack.objects.create(
        slug="my-gen-stack",
        version=1,
        created_by=user,
        has_frontend=False,
        default_port=8000,
        patch_profile="flask",
        backend_filename="app.py",
        backend_base_image="python:3.12-slim",
        server_kind="python",
        files={"requirements.txt": "flask\n"},
        content_hash="x",
        dockerfile_mode=Stack.DockerfileMode.GENERATED,
    )
    app_req = AppRequirementTemplateFactory(slug="user-stack-app")
    job = GenerationJobFactory(
        mode="scaffolding",
        stack_slug="my-gen-stack",
        app_requirement=app_req,
        created_by=user,
        result_data={"backend_code": "print('hi')"},
    )
    dest = tmp_path / "build"

    svc.apply_scaffold(job, dest, phase=svc.ScaffoldPhase.BUILD)

    dockerfile = (dest / "Dockerfile").read_text()
    assert "FROM python:3.12-slim" in dockerfile
    assert "USER appuser" in dockerfile
    assert (dest / "requirements.txt").is_file()
    assert (dest / "app.py").is_file()
