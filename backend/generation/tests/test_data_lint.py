"""Tests for backend/generation/data/ validation (schema + stack parity)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from backend.generation.services import data_lint


def test_all_requirement_specs_are_currently_valid():
    assert data_lint.lint_requirement_specs() == []


def test_all_frontend_stacks_currently_have_parity():
    assert data_lint.lint_stack_parity() == []


def test_lint_all_is_clean_on_real_data():
    assert data_lint.lint_all() == []


def _write_spec(tmp_path: Path, name: str, content: str) -> Path:
    directory = tmp_path / "requirements"
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(content, encoding="utf-8")
    return directory


def test_lint_requirement_specs_catches_missing_required_field(tmp_path, monkeypatch):
    bad_spec = {
        "slug": "broken_app",
        "name": "Broken",
        "category": "Test",
        "description": "Missing backend_requirements and api_endpoints",
        "frontend_requirements": ["x"],
        "data_model": {"name": "Thing", "fields": {"id": "integer"}},
    }
    directory = _write_spec(tmp_path, "broken_app.json", json.dumps(bad_spec))
    monkeypatch.setattr(data_lint, "REQUIREMENTS_DIR", directory)

    errors = data_lint.lint_requirement_specs()

    assert errors
    assert any("backend_requirements" in e for e in errors)


def test_lint_requirement_specs_catches_bad_json(tmp_path, monkeypatch):
    directory = _write_spec(tmp_path, "broken.json", "{not valid json")
    monkeypatch.setattr(data_lint, "REQUIREMENTS_DIR", directory)

    errors = data_lint.lint_requirement_specs()

    assert len(errors) == 1
    assert "invalid JSON" in errors[0]


def test_lint_requirement_specs_catches_bad_difficulty_enum(tmp_path, monkeypatch):
    bad_spec = {
        "slug": "broken_app",
        "name": "Broken",
        "category": "Test",
        "description": "d",
        "difficulty": "extreme",  # not in the enum
        "backend_requirements": ["x"],
        "frontend_requirements": ["x"],
        "api_endpoints": [{"method": "GET", "path": "/api/x", "description": "d"}],
        "data_model": {"name": "Thing", "fields": {"id": "integer"}},
    }
    directory = _write_spec(tmp_path, "broken_app.json", json.dumps(bad_spec))
    monkeypatch.setattr(data_lint, "REQUIREMENTS_DIR", directory)

    errors = data_lint.lint_requirement_specs()

    assert any("difficulty" in e for e in errors)


def test_lint_requirement_specs_ignores_schema_json_itself(tmp_path, monkeypatch):
    directory = tmp_path / "requirements"
    directory.mkdir()
    (directory / "schema.json").write_text("this is not valid spec JSON at all {{{", encoding="utf-8")
    monkeypatch.setattr(data_lint, "REQUIREMENTS_DIR", directory)

    assert data_lint.lint_requirement_specs() == []


def test_lint_stack_parity_catches_missing_profile_for_frontend_stack(tmp_path, monkeypatch):
    fake_manifest = {"stacks": {"some-new-stack": {"has_frontend": True}}}
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text("bundles: []\n", encoding="utf-8")

    with mock.patch.object(data_lint, "load_manifest", return_value=fake_manifest):
        monkeypatch.setattr(data_lint, "CATALOG_PATH", catalog_path)
        errors = data_lint.lint_stack_parity()

    assert len(errors) == 1
    assert "some-new-stack" in errors[0]
    assert "no system profile" in errors[0]


def test_lint_stack_parity_catches_incomplete_stage_coverage(tmp_path, monkeypatch):
    fake_manifest = {"stacks": {"some-stack": {"has_frontend": True}}}
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(
        """
profiles:
  - slug: incomplete-bundle
    scaffolding_slug: some-stack
    block_refs:
      - type: prompt_stage
        slug: base-backend-system
      - type: prompt_stage
        slug: base-backend-user
""",
        encoding="utf-8",
    )

    with mock.patch.object(data_lint, "load_manifest", return_value=fake_manifest):
        monkeypatch.setattr(data_lint, "CATALOG_PATH", catalog_path)
        errors = data_lint.lint_stack_parity()

    assert len(errors) == 1
    assert "incomplete-bundle" in errors[0]
    assert "frontend" in errors[0]


def test_lint_stack_parity_ignores_backend_only_stacks(tmp_path, monkeypatch):
    fake_manifest = {"stacks": {"generic-python": {"has_frontend": False}}}
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text("bundles: []\n", encoding="utf-8")

    with mock.patch.object(data_lint, "load_manifest", return_value=fake_manifest):
        monkeypatch.setattr(data_lint, "CATALOG_PATH", catalog_path)
        errors = data_lint.lint_stack_parity()

    assert errors == []
