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
    assert has("max_llm_requests must be an integer between 1 and 2000")


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


# ── spec schema v3 ────────────────────────────────────────────────────


def _v3_spec(**overrides) -> dict:
    """A minimal v3 spec that meets every convention (tests break one at a time)."""
    spec = {
        "slug": "crud_thing_list",
        "name": "Things",
        "category": "CRUD",
        "description": "Track things",
        "spec_schema_version": 3,
        "backend_requirements": [
            "Access control: anyone may list things; require auth for creating and deleting things",
            "GET /api/things filters by ?tag= and returns newest first",
        ],
        "frontend_requirements": ["A list of things with a tag filter"],
        "api_endpoints": [
            {
                "method": "GET",
                "path": "/api/things",
                "description": "List things",
                "auth": "public",
                "query": {"tag": "Only things with this tag"},
                "request": None,
                "response": {"items": [{"id": 1, "name": "Lamp"}], "total": 1},
            },
            {
                "method": "POST",
                "path": "/api/things",
                "description": "Create a thing",
                "auth": "user",
                "success_status": 201,
                "request": {"name": "string", "tag": "string (optional)", "size": "integer (optional)"},
                "response": {"id": 1, "name": "Lamp"},
            },
            {
                "method": "DELETE",
                "path": "/api/things/:id",
                "description": "Delete a thing",
                "auth": "user",
                "success_status": 204,
                "request": None,
                "response": None,
            },
            {
                "method": "GET",
                "path": "/api/health",
                "description": "Health check",
                "auth": "public",
                "request": None,
                "response": {"status": "healthy"},
            },
        ],
        "admin_api_endpoints": [
            {
                "method": "GET",
                "path": "/api/admin/things",
                "description": "Admin overview",
                "auth": "admin",
                "request": None,
                "response": {"items": [], "total": 0, "active": 0, "inactive": 0},
            },
        ],
        "data_models": [
            {"name": "Thing", "description": "A thing", "fields": {"id": "integer", "name": "string"}},
            {"name": "Tag", "fields": {"id": "integer"}},
        ],
    }
    spec.update(overrides)
    return spec


def _endpoint(spec: dict, path: str, method: str = "GET") -> dict:
    return next(
        ep
        for ep in spec["api_endpoints"] + spec.get("admin_api_endpoints", [])
        if ep["path"] == path and ep["method"] == method
    )


def test_v3_spec_with_data_models_is_valid():
    spec = _v3_spec()
    assert data_lint.app_spec_errors(spec) == []
    assert data_lint.app_spec_warnings(spec) == []


def test_legacy_v2_spec_still_validates_and_skips_the_v3_conventions():
    legacy = {
        "slug": "old_app",
        "name": "Old",
        "category": "Test",
        "description": "d",
        "spec_schema_version": 2,
        "backend_requirements": ["anything at all, GET /api/nowhere"],
        "frontend_requirements": ["x"],
        "api_endpoints": [{"method": "GET", "path": "/api/x", "description": "d", "response": [1]}],
        "data_model": {"name": "Thing", "fields": {"id": "integer"}},
    }
    assert data_lint.app_spec_errors(legacy) == []
    assert data_lint.spec_convention_issues(legacy) == ([], [])
    # No version at all reads as v1.
    assert data_lint.app_spec_errors({k: v for k, v in legacy.items() if k != "spec_schema_version"}) == []


def test_a_spec_needs_data_model_or_data_models():
    spec = _v3_spec()
    del spec["data_models"]
    errors = data_lint.app_spec_errors(spec)
    assert errors
    assert any("data_model" in e for e in errors)
    # Empty values (an API body's defaults) count as absent.
    assert data_lint.app_spec_errors({**spec, "data_model": {}, "data_models": []})


def test_data_models_items_are_strict():
    bad = _v3_spec(data_models=[{"name": "Thing", "fields": {}, "extra": 1}])
    errors = data_lint.app_spec_errors(bad)
    assert any("fields" in e for e in errors)
    assert any("extra" in e for e in errors)
    assert data_lint.app_spec_errors(_v3_spec(spec_schema_version=4))


def test_endpoint_auth_query_and_success_status_are_typed():
    spec = _v3_spec()
    _endpoint(spec, "/api/things")["auth"] = "anyone"
    _endpoint(spec, "/api/things", "POST")["success_status"] = 500
    _endpoint(spec, "/api/things")["query"] = {"tag": 1}
    errors = data_lint.app_spec_errors(spec)
    assert any("auth" in e for e in errors)
    assert any("success_status" in e for e in errors)
    assert any("query" in e for e in errors)


def _convention_errors(spec: dict) -> list[str]:
    assert data_lint.app_spec_errors({**spec, "spec_schema_version": 2}) == [], "the schema itself must pass"
    return data_lint.spec_convention_issues(spec)[0]


def test_v3_uses_data_models_and_unique_model_names():
    spec = _v3_spec(data_model={"name": "Thing", "fields": {"id": "integer"}})
    assert any("data_models" in e for e in _convention_errors(spec))
    dup = _v3_spec(data_models=[{"name": "Thing", "fields": {"id": "integer"}}] * 2)
    assert any("listed twice" in e for e in _convention_errors(dup))


def test_v3_endpoints_are_unique_per_method_and_path():
    spec = _v3_spec()
    spec["api_endpoints"].append({**_endpoint(spec, "/api/things/:id", "DELETE"), "path": "/api/things/{thing_id}"})
    errors = _convention_errors(spec)
    assert any("DELETE /api/things/{thing_id} is already api_endpoints[2]" in e for e in errors)


def test_v3_endpoints_declare_auth():
    spec = _v3_spec()
    del _endpoint(spec, "/api/things")["auth"]
    assert any("auth is required" in e for e in _convention_errors(spec))


def test_v3_needs_a_public_health_endpoint():
    spec = _v3_spec()
    spec["api_endpoints"] = [ep for ep in spec["api_endpoints"] if ep["path"] != "/api/health"]
    assert any("/api/health is required" in e for e in _convention_errors(spec))
    spec = _v3_spec()
    _endpoint(spec, "/api/health")["auth"] = "user"
    assert any("must be public" in e for e in _convention_errors(spec))


def test_v3_access_control_line_comes_first_and_restricts_something():
    spec = _v3_spec()
    spec["backend_requirements"].reverse()
    assert any("backend_requirements[0]" in e for e in _convention_errors(spec))
    spec = _v3_spec()
    spec["backend_requirements"][0] = "Access control: everything is public"
    assert any("must say what it restricts" in e for e in _convention_errors(spec))


def test_v3_anonymous_write_target_must_require_a_user():
    spec = _v3_spec()
    _endpoint(spec, "/api/things", "POST")["auth"] = "public"
    assert any("sends anonymously" in e for e in _convention_errors(spec))


def test_v3_admin_endpoints_live_under_api_admin():
    spec = _v3_spec()
    _endpoint(spec, "/api/admin/things")["path"] = "/api/overview"
    assert any("start with /api/admin/" in e for e in _convention_errors(spec))
    spec = _v3_spec()
    spec["api_endpoints"].append(
        {"method": "GET", "path": "/api/admin/stats", "description": "d", "auth": "admin", "response": {}},
    )
    errors = _convention_errors(spec)
    assert any("belong in admin_api_endpoints" in e for e in errors)
    spec = _v3_spec()
    _endpoint(spec, "/api/admin/things")["auth"] = "user"
    assert any("have auth 'admin'" in e for e in _convention_errors(spec))


def test_v3_list_endpoints_use_the_items_total_envelope():
    for response, needle in (
        ([{"id": 1}], "envelope"),
        ({"things": [{"id": 1}]}, 'under "items"'),
        ({"items": [{"id": 1}]}, '"total"'),
        ({"items": [], "total": "1"}, '"total"'),
    ):
        spec = _v3_spec()
        _endpoint(spec, "/api/things")["response"] = response
        assert any(needle in e for e in _convention_errors(spec)), response
    # Extra keys are fine; item endpoints and non-list GETs are not list endpoints.
    spec = _v3_spec()
    _endpoint(spec, "/api/things")["response"] = {"items": [], "total": 0, "page": 1}
    spec["api_endpoints"].append(
        {"method": "GET", "path": "/api/stats", "description": "d", "auth": "public", "response": {"count": 1}},
    )
    assert _convention_errors(spec) == []


def test_v3_prose_mentions_only_listed_endpoints_and_query_params():
    spec = _v3_spec()
    spec["backend_requirements"].append("PUT /api/things/:id updates; GET /api/things/:id/history lists changes.")
    spec["frontend_requirements"].append("Search box sends ?search= to /api/things")
    spec["admin_requirements"] = ["Sign in at /api/auth/login, then use /api/admin/things"]
    errors = _convention_errors(spec)
    assert any("backend_requirements[2]: mentions PUT /api/things/:id" in e for e in errors)
    assert any("mentions GET /api/things/:id/history" in e for e in errors)
    assert any("query parameter 'search'" in e for e in errors)
    assert not any("admin_requirements" in e for e in errors)
    assert len(errors) == 3


def test_v3_204_has_no_body():
    spec = _v3_spec()
    _endpoint(spec, "/api/things/:id", "DELETE")["response"] = {"ok": True}
    assert any("204" in e for e in _convention_errors(spec))


def test_v3_warns_about_request_values_the_tester_sends_verbatim():
    spec = _v3_spec()
    _endpoint(spec, "/api/things", "POST")["request"] = {
        "name": "string",
        "due": "datetime",
        "kind": "enum: small, large",
        "notes": "text, optional",
        "colour": "red",  # a literal example value: fine
        "starts_at": "datetime",  # the tester synthesises *_at keys: fine
        "size": "integer (1-5)",  # a descriptor the tester types: fine
        "nested": {"when": "date (YYYY-MM-DD)"},
    }
    errors, warnings = data_lint.spec_convention_issues(spec)
    assert errors == []
    flagged = sorted(w.split(":", 1)[0] for w in warnings)
    assert flagged == [
        "api_endpoints[1].request.due",
        "api_endpoints[1].request.kind",
        "api_endpoints[1].request.nested.when",
        "api_endpoints[1].request.notes",
    ]
    assert data_lint.app_spec_warnings(spec) == [f"app spec: {w}" for w in warnings]
    # Warnings never block a save.
    assert data_lint.app_spec_errors(spec) == []


def test_v3_redirect_success_status_warns():
    spec = _v3_spec()
    spec["api_endpoints"].append(
        {"method": "GET", "path": "/api/r/:code", "description": "Redirect", "auth": "public", "success_status": 302},
    )
    errors, warnings = data_lint.spec_convention_issues(spec)
    assert errors == []
    assert any("302" in w for w in warnings)


def test_lint_requirement_specs_applies_v3_conventions_and_file_names(tmp_path, monkeypatch):
    bad = _v3_spec()
    bad["api_endpoints"] = [ep for ep in bad["api_endpoints"] if ep["path"] != "/api/health"]
    directory = _write_spec(tmp_path, "crud_thing_list.json", json.dumps(bad))
    (directory / "renamed.json").write_text(json.dumps(_v3_spec()), encoding="utf-8")
    monkeypatch.setattr(data_lint, "REQUIREMENTS_DIR", directory)

    errors = data_lint.lint_requirement_specs()

    assert any(e.startswith("crud_thing_list.json:") and "/api/health" in e for e in errors)
    assert any(e.startswith("renamed.json:") and "file name" in e for e in errors)


def test_shipped_reference_spec_is_v3_and_warning_free():
    data = json.loads((data_lint.REQUIREMENTS_DIR / "crud_book_library.json").read_text(encoding="utf-8"))
    assert data["spec_schema_version"] == 3
    assert "data_model" not in data
    assert data_lint.spec_convention_issues(data) == ([], [])
