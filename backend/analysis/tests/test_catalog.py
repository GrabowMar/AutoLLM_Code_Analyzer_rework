"""Tool catalog: spec loading/validation, config validation, hashes, pins."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from backend.analysis.engine import catalog
from backend.analysis.engine.adapters import get_adapter
from backend.analysis.engine.adapters import registered_slugs
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.engine.catalog import SpecError
from backend.analysis.engine.types import SampleFile

IMAGE_DIR = Path(catalog.__file__).resolve().parent.parent / "images" / "analyzer"
STATIC = ("bandit", "semgrep", "eslint", "ruff", "pylint", "mypy", "vulture", "gitleaks", "detect-secrets")
# Ported from the v1 tool shop; opt-in (default_enabled: false).
PORTED = ("flake8", "perflint", "deptry", "depcheck", "htmlhint", "stylelint", "shellcheck")
METRICS = ("radon", "lizard", "jscpd")
OTHER = ("codespell", "hadolint")


def _spec(**overrides):
    base = {
        "slug": "demo",
        "name": "Demo",
        "kind": "static",
        "category": "lint",
        "languages": ["python"],
        "file_kinds": ["source"],
        "output_kind": "findings",
        "version": "1.0.0",
        "adapter_version": 1,
        "config_schema": [
            {"name": "level", "type": "select", "default": "low", "options": ["low", "high"]},
            {"name": "limit", "type": "integer", "default": 5, "min": 1, "max": 10},
            {"name": "ratio", "type": "number", "default": 0.5, "min": 0, "max": 1},
            {"name": "strict", "type": "boolean", "default": False},
            {"name": "rules", "type": "string", "default": "", "pattern": "^[A-Z0-9,]*$", "max_length": 20},
            {"name": "packs", "type": "multiselect", "default": ["a"], "options": ["a", "b", "c"], "min_items": 1},
        ],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# The real catalog
# --------------------------------------------------------------------------


def test_catalog_has_every_tool_and_the_ai_reviewer():
    specs = catalog.load_specs(refresh=True)
    assert set(specs) == {*STATIC, *PORTED, *METRICS, *OTHER, catalog.AI_TOOL_SLUG}
    assert all(specs[s].kind == "static" and not specs[s].default_enabled for s in PORTED)
    assert specs["ai_review"].kind == "ai"
    assert specs["ai_review"].category == "ai"
    assert all(specs[s].kind == "metrics" for s in METRICS)
    assert not (catalog.TOOLS_DIR / "catalog.yaml").exists()
    assert not (catalog.TOOLS_DIR / "llm_review.yaml").exists()


def test_static_specs_are_ordered_and_exclude_ai():
    specs = catalog.static_specs()
    assert catalog.AI_TOOL_SLUG not in [s.slug for s in specs]
    keys = [(s.display_order, s.slug) for s in specs]
    assert keys == sorted(keys)


def test_get_spec_unknown_raises_key_error():
    with pytest.raises(KeyError):
        catalog.get_spec("nope")


def test_every_static_tool_has_exactly_one_adapter():
    static = {s.slug for s in catalog.static_specs()}
    assert set(registered_slugs()) == static
    for slug in static:
        assert get_adapter(slug).slug == slug
    with pytest.raises(KeyError):
        get_adapter(catalog.AI_TOOL_SLUG)


@pytest.mark.parametrize("spec", catalog.static_specs(), ids=lambda s: s.slug)
def test_static_specs_are_complete(spec):
    assert spec.version
    assert spec.adapter_version >= 1
    assert spec.description
    assert spec.docs_url.startswith("https://")
    assert spec.spec_hash == catalog.spec_hash(spec.raw)
    assert re.fullmatch(r"[0-9a-f]{64}", spec.spec_hash)
    # The defaults are a valid config of the tool itself.
    assert catalog.validate_tool_config(spec.slug, {}) == spec.default_config


def test_spec_versions_match_the_image_pins():
    """data/tools/*.yaml versions are the versions baked into the analyzer image."""
    pins: dict[str, str] = {}
    for req in (IMAGE_DIR / "requirements").glob("*.in"):
        # The tool's own pin (an .in may pin helpers too, and carry comments); a
        # tool shipped under another distribution name (shellcheck-py) states
        # its version in the leading comment: "# <slug> <version> ...".
        text = req.read_text()
        own = re.search(rf"^{re.escape(req.stem)}==(\S+)\s*$", text, re.MULTILINE)
        stated = re.match(rf"#\s*{re.escape(req.stem)}\s+v?(\d[\w.]*)", text)
        assert own or stated, f"{req.name} pins no {req.stem} version"
        pins[req.stem] = own.group(1) if own else stated.group(1)
    package = json.loads((IMAGE_DIR / "npm" / "package.json").read_text())
    pins["eslint"] = package["dependencies"]["eslint"]
    pins["jscpd"] = package["dependencies"]["jscpd"]
    for slug in ("depcheck", "htmlhint", "stylelint"):
        pins[slug] = package["dependencies"][slug]
    dockerfile = (IMAGE_DIR / "Dockerfile").read_text()
    for slug in ("gitleaks", "hadolint"):
        pins[slug] = re.search(rf"ARG {slug.upper()}_VERSION=(\S+)", dockerfile).group(1)
    assert {s.slug: s.version for s in catalog.static_specs()} == pins


def test_lock_files_pin_every_package_with_hashes():
    for lock in (IMAGE_DIR / "requirements").glob("*.txt"):
        text = lock.read_text()
        requirements = re.findall(r"^([A-Za-z0-9_.-]+)==(\S+) \\$", text, re.MULTILINE)
        assert requirements, lock.name
        assert text.count("--hash=sha256:") >= len(requirements), lock.name
    lock = json.loads((IMAGE_DIR / "npm" / "package-lock.json").read_text())
    for key, meta in lock["packages"].items():
        if key and not meta.get("optional"):
            assert meta.get("integrity", "").startswith("sha512-"), key


def test_image_definition_pins_base_images_by_digest():
    froms = re.findall(r"^FROM (\S+)", (IMAGE_DIR / "Dockerfile").read_text(), re.MULTILINE)
    assert froms
    assert all("@sha256:" in ref for ref in froms)


# --------------------------------------------------------------------------
# validate_tool_config
# --------------------------------------------------------------------------


def test_validate_tool_config_merges_defaults():
    assert catalog.validate_tool_config("ruff", None) == {
        "select": "E9,F,B,S,PLE",
        "ignore": "",
        "target_version": "py312",
    }
    assert catalog.validate_tool_config("ruff", {"select": "F,B"})["select"] == "F,B"


def test_validate_tool_config_reports_every_bad_field():
    with pytest.raises(ConfigError) as exc:
        catalog.validate_tool_config(
            "semgrep",
            {"rulesets": ["python", "bogus"], "per_file_timeout": 2, "exclude_rules": 5, "extra": 1},
        )
    assert set(exc.value.field_errors) == {"rulesets", "per_file_timeout", "exclude_rules", "extra"}
    assert exc.value.field_errors["extra"] == "unknown option"


@pytest.mark.parametrize(
    ("slug", "config", "field"),
    [
        ("ruff", {"select": "F; rm -rf /"}, "select"),  # pattern: no shell metacharacters reach argv
        ("ruff", {"target_version": "py27"}, "target_version"),
        ("bandit", {"skip": "B101,X"}, "skip"),
        ("vulture", {"min_confidence": 101}, "min_confidence"),
        ("vulture", {"min_confidence": 80.5}, "min_confidence"),
        ("vulture", {"min_confidence": True}, "min_confidence"),
        ("mypy", {"check_untyped_defs": "yes"}, "check_untyped_defs"),
        ("semgrep", {"rulesets": []}, "rulesets"),
        ("semgrep", {"rulesets": "python"}, "rulesets"),
        ("detect-secrets", {"base64_limit": float("nan")}, "base64_limit"),
        ("hadolint", {"ignore_rules": "DL300"}, "ignore_rules"),
        ("eslint", {"opt_in_rules": ["no-eval"]}, "opt_in_rules"),
    ],
)
def test_validate_tool_config_rejects(slug, config, field):
    with pytest.raises(ConfigError) as exc:
        catalog.validate_tool_config(slug, config)
    assert field in exc.value.field_errors


def test_validate_tool_config_normalizes_values():
    assert catalog.validate_tool_config("vulture", {"min_confidence": 60.0})["min_confidence"] == 60
    packs = catalog.validate_tool_config("semgrep", {"rulesets": ["html", "python", "html"]})["rulesets"]
    assert packs == ["python", "html"]  # option order, de-duplicated


def test_validate_tool_config_unknown_tool_and_bad_shape():
    with pytest.raises(ConfigError) as exc:
        catalog.validate_tool_config("nope", {})
    assert "slug" in exc.value.field_errors
    with pytest.raises(ConfigError) as exc:
        catalog.validate_tool_config("ruff", ["select"])
    assert "config" in exc.value.field_errors


def test_ai_review_accepts_no_tool_config():
    assert catalog.validate_tool_config("ai_review", {}) == {}
    with pytest.raises(ConfigError):
        catalog.validate_tool_config("ai_review", {"model": "x"})


# --------------------------------------------------------------------------
# Hashes
# --------------------------------------------------------------------------


def test_config_hash_is_canonical():
    base = catalog.config_hash("semgrep", {}, "1.180.0")
    assert base == catalog.config_hash("semgrep", catalog.get_spec("semgrep").default_config, "1.180.0")
    reordered = {"rulesets": ["html", "javascript-audit", "javascript", "python-audit", "python"]}
    assert catalog.config_hash("semgrep", reordered, "1.180.0") == base
    assert catalog.config_hash("semgrep", {"rulesets": ["python"]}, "1.180.0") != base
    assert catalog.config_hash("semgrep", {}, "1.181.0") != base
    assert re.fullmatch(r"[0-9a-f]{64}", base)


def test_config_hash_rejects_invalid_config():
    with pytest.raises(ConfigError):
        catalog.config_hash("ruff", {"select": 1})


def test_spec_hash_ignores_presentation_fields():
    a = catalog.parse_spec(_spec())
    b = catalog.parse_spec(_spec(name="Other", description="changed", icon="x", display_order=1, docs_url="https://x"))
    assert a.spec_hash == b.spec_hash
    assert catalog.parse_spec(_spec(version="2.0.0")).spec_hash != a.spec_hash
    assert catalog.parse_spec(_spec(adapter_version=2)).spec_hash != a.spec_hash
    assert catalog.parse_spec(_spec(timeout_s=999)).spec_hash != a.spec_hash
    changed_schema = _spec()
    changed_schema["config_schema"][1]["default"] = 6
    assert catalog.parse_spec(changed_schema).spec_hash != a.spec_hash


# --------------------------------------------------------------------------
# parse_spec
# --------------------------------------------------------------------------


def test_parse_spec_normalizes():
    spec = catalog.parse_spec(_spec(description="  two\n  lines  "))
    assert spec.description == "two lines"
    assert spec.default_config == {
        "level": "low",
        "limit": 5,
        "ratio": 0.5,
        "strict": False,
        "rules": "",
        "packs": ["a"],
    }
    level = spec.schema_field("level")
    assert level["options"] == [{"value": "low", "label": "low"}, {"value": "high", "label": "high"}]
    assert level["applies_to"] == "argv"
    assert spec.raw["config_schema"][0]["label"] == "Level"


def test_parse_spec_default_config_overrides_field_defaults():
    spec = catalog.parse_spec(_spec(default_config={"limit": 7}))
    assert spec.default_config["limit"] == 7


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"bogus": 1}, "unknown keys"),
        ({"kind": "plugin"}, "kind must be one of"),
        ({"category": "misc"}, "category must be one of"),
        ({"output_kind": "text"}, "output_kind"),
        ({"languages": []}, "languages must be a non-empty list"),
        ({"languages": ["cobol"]}, "unknown languages"),
        ({"languages": ["*", "python"]}, "must stand alone"),
        ({"file_kinds": ["sources"]}, "unknown file kinds"),
        ({"slug": "Bad Slug"}, "not a valid slug"),
        ({"mem_limit": "lots"}, "mem_limit"),
        ({"timeout_s": 0}, "timeout_s"),
        ({"default_required": "yes"}, "default_required must be a boolean"),
        ({"default_config": {"nope": 1}}, "without a config_schema field"),
        ({"default_config": {"limit": 99}}, "default_config.limit"),
        ({"config_schema": [{"name": "x", "type": "color", "default": 1}]}, "type must be one of"),
        ({"config_schema": [{"name": "x", "type": "boolean"}]}, "needs a default"),
        ({"config_schema": [{"name": "x", "type": "boolean", "default": 1}]}, "must be a boolean"),
        ({"config_schema": [{"name": "x", "type": "select", "default": "a"}]}, "options must be a non-empty list"),
        ({"config_schema": [{"name": "x", "type": "select", "default": "z", "options": ["a"]}]}, "must be one of"),
        ({"config_schema": [{"name": "x", "type": "string", "default": "", "pattern": "("}]}, "not a valid regex"),
        ({"config_schema": [{"name": "x", "type": "string", "default": "", "colour": 1}]}, "unknown keys"),
        ({"config_schema": [{"name": "X", "type": "boolean", "default": True}]}, "lower_snake"),
        (
            {"config_schema": [{"name": "x", "type": "boolean", "default": True, "applies_to": "env"}]},
            "applies_to",
        ),
        (
            {
                "config_schema": [
                    {"name": "x", "type": "boolean", "default": True},
                    {"name": "x", "type": "boolean", "default": True},
                ],
            },
            "duplicated",
        ),
    ],
)
def test_parse_spec_rejects(overrides, message):
    with pytest.raises(SpecError, match=re.escape(message)):
        catalog.parse_spec(_spec(**overrides), source="demo.yaml")


def test_parse_spec_requires_keys_and_a_matching_file_name():
    data = _spec()
    del data["languages"]
    with pytest.raises(SpecError, match="missing key 'languages'"):
        catalog.parse_spec(data)
    with pytest.raises(SpecError, match="does not match the file name"):
        catalog.parse_spec(_spec(), expected_slug="other")
    with pytest.raises(SpecError, match="must be a mapping"):
        catalog.parse_spec(["slug"])


def test_load_specs_rejects_a_broken_catalog(tmp_path, monkeypatch):
    for path in catalog.TOOLS_DIR.glob("*.yaml"):
        (tmp_path / path.name).write_text(path.read_text())
    broken = yaml.safe_load((tmp_path / "ruff.yaml").read_text())
    broken["kind"] = "nope"
    (tmp_path / "ruff.yaml").write_text(yaml.safe_dump(broken))
    monkeypatch.setattr(catalog, "TOOLS_DIR", tmp_path)
    try:
        with pytest.raises(SpecError, match=r"ruff\.yaml"):
            catalog.load_specs(refresh=True)
        (tmp_path / "ruff.yaml").write_text("slug: [")
        with pytest.raises(SpecError, match="invalid YAML"):
            catalog.load_specs(refresh=True)
        (tmp_path / "ruff.yaml").unlink()
        (tmp_path / "ai_review.yaml").unlink()
        with pytest.raises(SpecError, match=r"no ai_review\.yaml"):
            catalog.load_specs(refresh=True)
    finally:
        monkeypatch.undo()
        catalog.load_specs(refresh=True)


def _catalog_copy(tmp_path, monkeypatch):
    for path in catalog.TOOLS_DIR.glob("*.yaml"):
        (tmp_path / path.name).write_text(path.read_text())
    monkeypatch.setattr(catalog, "TOOLS_DIR", tmp_path)
    return tmp_path


def test_load_specs_rereads_a_changed_spec_without_refresh(tmp_path, monkeypatch):
    """A long-lived worker sees an edited spec at once, so the spec_hash it runs
    under (and procedure_drift compares against) is the one on disk."""
    tools = _catalog_copy(tmp_path, monkeypatch)
    try:
        before = catalog.load_specs()["ruff"].spec_hash
        data = yaml.safe_load((tools / "ruff.yaml").read_text())
        data["timeout_s"] = int(data.get("timeout_s") or 180) + 7
        (tools / "ruff.yaml").write_text(yaml.safe_dump(data))
        after = catalog.get_spec("ruff")
        assert after.spec_hash != before
        assert after.timeout_s == data["timeout_s"]
        assert catalog.load_specs()["ruff"].spec_hash == catalog.spec_hash(after.raw)
    finally:
        monkeypatch.undo()
        catalog.load_specs(refresh=True)


def test_load_specs_sees_added_and_removed_specs(tmp_path, monkeypatch):
    tools = _catalog_copy(tmp_path, monkeypatch)
    try:
        assert "ruff" in catalog.load_specs()
        (tools / "ruff.yaml").unlink()
        assert "ruff" not in catalog.load_specs()
        (tools / "demo.yaml").write_text(yaml.safe_dump(_spec()))
        assert catalog.get_spec("demo").kind == "static"
    finally:
        monkeypatch.undo()
        catalog.load_specs(refresh=True)
    # Restoring TOOLS_DIR serves the real catalog again (the directory is part of the key).
    assert "demo" not in catalog.load_specs()
    assert "ruff" in catalog.load_specs()


def test_load_specs_keeps_the_parsed_specs_for_unchanged_bytes(tmp_path, monkeypatch):
    tools = _catalog_copy(tmp_path, monkeypatch)
    try:
        first = catalog.get_spec("ruff")
        body = (tools / "ruff.yaml").read_bytes()
        (tools / "ruff.yaml").write_bytes(body)  # new mtime/ctime, same content
        assert catalog.get_spec("ruff") is first
        # A stable signature serves the cache without re-reading the files.
        monkeypatch.setattr(catalog, "_read_catalog", lambda: pytest.fail("re-read an unchanged catalog"))
        assert catalog.get_spec("ruff") is first
    finally:
        monkeypatch.undo()
        catalog.load_specs(refresh=True)


def test_load_specs_returns_a_copy():
    specs = catalog.load_specs()
    specs.pop("ruff")
    assert "ruff" in catalog.load_specs()


# --------------------------------------------------------------------------
# Config reaches argv
# --------------------------------------------------------------------------


def _files_for(spec) -> list[SampleFile]:
    by_language = {
        "python": ("app.py", b"import os\n"),
        "javascript": ("web/app.js", b"export const a = 1;\n"),
        "dockerfile": ("Dockerfile", b"FROM python:3.12\n"),
        "*": ("README.md", b"hello\n"),
    }
    language = spec.languages[0]
    path, content = by_language.get(language, by_language["javascript"])
    files = [
        SampleFile(
            path=path,
            content=content,
            sha256="0" * 64,
            size=len(content),
            language=language if language != "*" else "markdown",
            kind="source",
            origin="authored",
        ),
    ]
    # Dependency checkers only run on a project root, i.e. next to a manifest.
    manifests = {
        "deptry": ("requirements.txt", b"requests==2.32.3\n", "text"),
        "depcheck": ("web/package.json", b'{"name": "web", "dependencies": {}}\n', "json"),
    }
    if spec.slug in manifests:
        name, body, manifest_language = manifests[spec.slug]
        files.append(
            SampleFile(
                path=name,
                content=body,
                sha256="0" * 64,
                size=len(body),
                language=manifest_language,
                kind="manifest",
                origin="authored",
            ),
        )
    return files


# Non-default values for the free-text options.
STRING_VALUES = {
    ("ruff", "select"): "F",
    ("ruff", "ignore"): "S101",
    ("bandit", "skip"): "B101",
    ("semgrep", "exclude_rules"): "some.rule",
    ("pylint", "extra_disable"): "fixme",
    ("hadolint", "ignore_rules"): "DL3007",
    ("flake8", "select"): "F",
    ("flake8", "extend_ignore"): "E501",
    ("perflint", "extra_disable"): "use-list-copy",
    ("deptry", "ignore"): "DEP002",
    ("shellcheck", "exclude"): "SC2086",
}


def _other_value(slug, field):
    kind = field["type"]
    default = field["default"]
    if kind == "boolean":
        return not default
    if kind in ("integer", "number"):
        return field["max"] if default != field["max"] else field["min"]
    if kind == "select":
        return next(o["value"] for o in field["options"] if o["value"] != default)
    if kind == "multiselect":
        values = [o["value"] for o in field["options"]]
        return values[:1] if default != values[:1] else values[:2]
    return STRING_VALUES[(slug, field["name"])]


@pytest.mark.parametrize(
    ("slug", "field_name"),
    [
        (spec.slug, field["name"])
        for spec in catalog.static_specs()
        for field in spec.config_schema
        if field["applies_to"] == "argv"
    ],
)
def test_every_argv_option_changes_the_command_line(slug, field_name):
    """v1 bug: config_schema options never reached the tool. Every argv option must."""
    spec = catalog.get_spec(slug)
    adapter = get_adapter(slug)
    files = _files_for(spec)
    value = _other_value(slug, spec.schema_field(field_name))
    default_argv = adapter.invocation(files, catalog.validate_tool_config(slug, {})).argv
    changed_argv = adapter.invocation(files, catalog.validate_tool_config(slug, {field_name: value})).argv
    assert default_argv != changed_argv


@pytest.mark.parametrize(
    ("slug", "field_name"),
    [
        (spec.slug, field["name"])
        for spec in catalog.static_specs()
        for field in spec.config_schema
        if field["applies_to"] == "interpretation"
    ],
)
def test_interpretation_options_are_documented(slug, field_name):
    """Options that only change interpretation say so (and are tested with the adapters)."""
    assert catalog.get_spec(slug).schema_field(field_name)["description"]
