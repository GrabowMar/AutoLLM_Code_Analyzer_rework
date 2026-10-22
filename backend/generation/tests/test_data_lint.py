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


# ── catalog, manifests, presets, starters ─────────────────────────────


def _catalog(tmp_path: Path, monkeypatch, text: str) -> None:
    path = tmp_path / "catalog.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(data_lint, "CATALOG_PATH", path)


def test_real_catalog_manifests_presets_and_starters_are_clean():
    assert data_lint.lint_catalog() == []
    assert data_lint.lint_manifests() == []
    assert data_lint.lint_presets() == []
    assert data_lint.lint_starter_packages() == []


def test_lint_catalog_catches_pins_unknown_blocks_and_double_defaults(tmp_path, monkeypatch):
    _catalog(
        tmp_path,
        monkeypatch,
        """
default_bundle_slug: not-a-profile
profiles:
  - slug: one
    scaffolding_slug: flask-react
    is_default: true
    block_refs:
      - {type: prompt_tone, slug: tone-standard, version: 1}
      - {type: prompt_stage, slug: no-such-block}
      - {type: prompt_rules, slug: tone-minimal}
  - slug: two
    scaffolding_slug: flask-react
    is_default: true
    block_refs: []
  - slug: three
    scaffolding_slug: no-such-stack
    block_refs: []
""",
    )

    errors = data_lint.lint_catalog()

    assert any("default_bundle_slug 'not-a-profile'" in e for e in errors)
    assert any("'tone-standard' pins a version" in e for e in errors)
    assert any("'no-such-block' is not a system block" in e for e in errors)
    assert any("'tone-minimal' has type 'prompt_rules'" in e for e in errors)
    assert any("stack 'flask-react' has 2 default profiles" in e for e in errors)
    assert any("'no-such-stack' is not a builtin stack" in e for e in errors)


def test_lint_manifests_catch_unknown_base_profile_and_pins(tmp_path, monkeypatch):
    directory = tmp_path / "manifests"
    directory.mkdir()
    (directory / "x.yaml").write_text(
        "app_slug: no_such_app\nbase_profile_slug: nope\nextra_block_refs:\n"
        "  - {type: prompt_tone, slug: tone-minimal, version: 2}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(data_lint, "MANIFESTS_DIR", directory)

    errors = data_lint.lint_manifests()

    assert any("app_slug 'no_such_app'" in e for e in errors)
    assert any("base profile 'nope'" in e for e in errors)
    assert any("pins a version" in e for e in errors)


def test_lint_presets_catches_bad_files(tmp_path, monkeypatch):
    from backend.generation.services import presets

    for name in ("harness-aider-default.yaml", "harness-mini-swe-agent-default.yaml", "budget-platform-default.yaml"):
        (tmp_path / name).write_text((presets.PRESETS_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "harness-opencode-default.yaml").write_text(
        "kind: harness\nslug: opencode-default\nname: x\nconfig: {harness: opencode, options: {nope: 1}}\n",
        encoding="utf-8",
    )
    (tmp_path / "oops.yaml").write_text(
        "kind: sampling\nslug: fast\nname: Fast\nconfig: {llm_params: {provider: {only: [a]}}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(presets, "PRESETS_DIR", tmp_path)

    errors = data_lint.lint_presets()

    assert any("harness-opencode-default.yaml: Unknown opencode options" in e for e in errors)
    assert any("oops.yaml: file must be named sampling-fast.yaml" in e for e in errors)
    assert any("oops.yaml: provider" in e for e in errors)
    assert any("missing default preset file sampling-provider-default.yaml" in e for e in errors)


def _starter(tmp_path: Path, monkeypatch, name: str, text: str) -> None:
    from backend.generation.services.packages import starters

    (tmp_path / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(starters, "STARTERS_DIR", tmp_path)


def test_lint_starter_packages_catches_broken_starters(tmp_path, monkeypatch):
    _starter(
        tmp_path,
        monkeypatch,
        "broken.yaml",
        """
kind: llm-lab-template-package
template_package_schema_version: 2
starter: {slug: other, name: Broken}
assets:
  app_specs:
    - content_file: requirements/no_such_spec.json
    - content_file: ../../../config/settings/base.py
  profiles:
    - slug: system-fastapi-vue-standard
      name: Renamed FastAPI + Vue
      description: FastAPI backend prompts paired with Vue frontend prompts and shared FastAPI rules
      scaffolding_slug: fastapi-vue
      llm_config: {temperature: 7}
      block_refs:
        - {type: prompt_stage, slug: fastapi-backend-system, version: 1}
  presets:
    - {kind: harness, slug: lazy, name: Lazy, config: {harness: aider, options: {}}}
    - {kind: budget, slug: my-default, name: Mine, config: {max_llm_requests: 0}}
""",
    )

    errors = data_lint.lint_starter_packages()

    def has(fragment: str) -> bool:
        return any(fragment in e for e in errors)

    assert has("template_package_schema_version must be 3")
    assert has("starter.slug must be the file name ('broken')")
    assert has("'requirements/no_such_spec.json' does not exist")
    assert has("is outside backend/generation/data/")
    assert has("llm_config: temperature")
    assert has("'fastapi-backend-system' pins a version")
    assert has("name differs from catalog.yaml")
    assert has("block_refs differs from catalog.yaml")
    assert has("preset harness/lazy: Every option is at its default; use the system preset aider-default")
    assert has("preset budget/my-default: the slug is reserved")
    assert has("max_llm_requests must be a int between 1 and 2000")


def test_lint_starter_packages_validates_inline_app_specs(tmp_path, monkeypatch):
    _starter(
        tmp_path,
        monkeypatch,
        "specs.yaml",
        """
kind: llm-lab-template-package
template_package_schema_version: 3
starter: {slug: specs, name: Specs}
assets:
  app_specs:
    - {slug: half_spec, name: Half}
""",
    )

    errors = data_lint.lint_starter_packages()

    assert errors
    assert all("app spec 'half_spec'" in e for e in errors)
