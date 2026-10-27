"""Static validation of backend/generation/data/.

Covers requirement specs, stack parity of the catalog profiles, the catalog
and manifests themselves (system block refs, one default per stack), system
run presets (``data/presets``) and starter packages (``data/packages``).

Specs are checked against ``schema.json``; specs of ``spec_schema_version``
3 or later must also meet the conventions the hidden evaluation relies on
(:func:`spec_convention_issues`). Older specs only have to validate, so
legacy packages keep importing.

Pure data checks — no DB access — so they can run in CI or as a pytest test
without a database. Used by ``manage.py lint_generation_data`` and
``backend.generation.tests.test_data_lint``.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from backend.generation.seeding import CATALOG_PATH
from backend.generation.seeding import MANIFESTS_DIR
from backend.generation.seeding import PROMPT_STAGE_SEEDS
from backend.generation.seeding import REQUIREMENTS_DIR
from backend.generation.seeding import _yaml_block_seeds
from backend.generation.services.evaluation import contract
from backend.generation.services.spec_models import SPEC_SCHEMA_VERSION
from backend.runtime.services.scaffolding import load_manifest

SCHEMA_PATH = REQUIREMENTS_DIR / "schema.json"

_REQUIRED_STAGE_ROLE_PAIRS = frozenset(
    {
        ("backend", "system"),
        ("backend", "user"),
        ("frontend", "system"),
        ("frontend", "user"),
    },
)


def _slug_stage_role_map() -> dict[str, tuple[str, str]]:
    return {seed["slug"]: (seed["stage"], seed["role"]) for seed in PROMPT_STAGE_SEEDS}


def _load_catalog() -> dict[str, Any]:
    return yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}


def _catalog_profiles(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    # "bundles" is the pre-rename catalog key; accept it for older files.
    return catalog.get("profiles", catalog.get("bundles", [])) or []


def system_block_types() -> dict[str, str]:
    """``{slug: block_type}`` of every block the seeder creates as a system block."""
    types = {seed["slug"]: "prompt_stage" for seed in PROMPT_STAGE_SEEDS}
    types.update({seed["slug"]: seed["block_type"] for seed in _yaml_block_seeds()})
    return types


def _spec_validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _error_text(err: jsonschema.ValidationError, label: str) -> str:
    message = err.message
    if err.validator == "anyOf" and not err.path:
        # The root anyOf is the model rule; the default message dumps the whole spec.
        message = "a spec needs data_models (schema v3) or data_model (v1/v2)"
    return f"{label}: {'.'.join(str(p) for p in err.path) or '<root>'}: {message}"


def _spec_errors(validator: jsonschema.Draft202012Validator, data: Any, label: str) -> list[str]:
    return [_error_text(err, label) for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path))]


# Optional keys an API or form payload sends empty when they are unset.
_EMPTY_MEANS_ABSENT = ("difficulty", "data_model", "data_models", "spec_schema_version")


def app_spec_errors(spec: dict[str, Any], *, label: str = "app spec", check_slug: bool = True) -> list[str]:
    """Errors of one app spec (used by the API and package import).

    ``schema.json`` errors, plus :func:`spec_convention_issues` errors for a
    v3+ spec. Empty optional values (``difficulty: ""``, ``data_model: {}``)
    count as absent. With ``check_slug=False`` the slug's snake_case pattern
    is not enforced: specs created before the schema, and slugs the importer
    renames (``slug-2``), keep working.
    """
    data = {k: v for k, v in spec.items() if not (k in _EMPTY_MEANS_ABSENT and not v)}
    errors = []
    for err in sorted(_spec_validator().iter_errors(data), key=lambda e: list(e.path)):
        if not check_slug and list(err.path) == ["slug"] and err.validator == "pattern":
            continue
        errors.append(_error_text(err, label))
    if not errors:
        errors.extend(f"{label}: {problem}" for problem in spec_convention_issues(data)[0])
    return errors


def app_spec_warnings(spec: dict[str, Any], *, label: str = "app spec") -> list[str]:
    """Warnings of one v3+ app spec: what saves, but the evaluation may grade differently than intended."""
    data = {k: v for k, v in spec.items() if not (k in _EMPTY_MEANS_ABSENT and not v)}
    if list(_spec_validator().iter_errors(data)):
        return []
    return [f"{label}: {problem}" for problem in spec_convention_issues(data)[1]]


def _spec_file_documents() -> list[tuple[str, Any]]:
    """``(file name, parsed JSON or JSONDecodeError)`` of every spec file."""
    documents: list[tuple[str, Any]] = []
    for path in sorted(REQUIREMENTS_DIR.glob("*.json")):
        if path.name == "schema.json":
            continue
        try:
            documents.append((path.name, json.loads(path.read_text(encoding="utf-8"))))
        except json.JSONDecodeError as exc:
            documents.append((path.name, exc))
    return documents


def lint_requirement_specs() -> list[str]:
    """Validate every ``data/requirements/*.json``: ``schema.json``, and the v3 conventions."""
    errors: list[str] = []
    validator = _spec_validator()

    for name, data in _spec_file_documents():
        if isinstance(data, json.JSONDecodeError):
            errors.append(f"{name}: invalid JSON ({data})")
            continue
        schema_errors = _spec_errors(validator, data, name)
        errors.extend(schema_errors)
        if not schema_errors:
            errors.extend(f"{name}: {problem}" for problem in spec_convention_issues(data)[0])
        if isinstance(data, dict) and data.get("slug") and data["slug"] != Path(name).stem:
            errors.append(f"{name}: slug {data['slug']!r} must equal the file name")

    return errors


def lint_requirement_warnings() -> list[str]:
    """Warnings of the shipped v3 specs (printed by ``lint_generation_data``; they do not fail it)."""
    warnings: list[str] = []
    validator = _spec_validator()
    for name, data in _spec_file_documents():
        if isinstance(data, json.JSONDecodeError) or _spec_errors(validator, data, name):
            continue
        warnings.extend(f"{name}: {problem}" for problem in spec_convention_issues(data)[1])
    return warnings


# ── v3 spec conventions ───────────────────────────────────────────────

TESTER_PATH = Path(__file__).resolve().parents[1] / "images" / "conformance-tester" / "tester.py"

_PATH_PARAM_RE = re.compile(r":[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\}")
_PROSE_PATH_RE = re.compile(r"(?:\b(GET|POST|PUT|PATCH|DELETE|WS)\s+)?(/api/[A-Za-z0-9_\-./:{}]*)")
_PROSE_QUERY_RE = re.compile(r"[?&]([A-Za-z_][A-Za-z0-9_]*)=")
_REQUIREMENT_KEYS = ("backend_requirements", "frontend_requirements", "admin_requirements")
# Contract endpoints every app with accounts has, listed in the spec or not.
_IMPLICIT_PATHS = frozenset({contract.REGISTER_PATH, contract.LOGIN_PATH})
# Words that start a value meant as a type descriptor (the tester knows only some of them).
_TYPE_WORDS = frozenset(
    {
        "string", "str", "text", "integer", "int", "number", "float", "decimal", "boolean", "bool",
        "array", "list", "object", "dict", "date", "datetime", "time", "timestamp", "uuid", "enum",
        "email", "url", "uri", "json", "iso", "iso8601", "iso-8601", "one",
    },
)  # fmt: skip
_DESCRIPTOR_HINTS = ("optional", "required", " | ", "e.g.", "one of", "max ", "min ")


@functools.cache
def _tester_module() -> Any:
    """The conformance tester itself, so request-body checks cannot drift from its synthesis."""
    spec = importlib.util.spec_from_file_location("llmlab_conformance_tester_lint", TESTER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norm_path(path: str) -> str:
    """``/api/books/{book_id}/`` -> ``/api/books/:p`` (parameter names and a trailing slash do not matter)."""
    path = _PATH_PARAM_RE.sub(":p", path.strip())
    return path.rstrip("/") if path != "/" else path


def _has_param(path: str) -> bool:
    return bool(_PATH_PARAM_RE.search(path))


def _labelled_endpoints(spec: dict[str, Any]) -> list[tuple[str, bool, dict[str, Any]]]:
    out = []
    for key, admin in (("api_endpoints", False), ("admin_api_endpoints", True)):
        out += [(f"{key}[{i}]", admin, ep) for i, ep in enumerate(spec.get(key) or []) if isinstance(ep, dict)]
    return out


def _verbatim_descriptor_values(sample: Any, key: str, where: str) -> list[str]:
    """Request-body strings the tester would send as-is although they read like a type descriptor."""
    if isinstance(sample, dict):
        return [
            problem
            for name, value in sample.items()
            for problem in _verbatim_descriptor_values(value, name, f"{where}.{name}")
        ]
    if isinstance(sample, list):
        return _verbatim_descriptor_values(sample[0], key, f"{where}[0]") if sample else []
    if not isinstance(sample, str):
        return []
    sent = _tester_module().Synth("lint").value(sample, key)
    if sent != sample:
        return []
    text = sample.strip().lower()
    first = re.split(r"[^a-z0-9\-]+", text, maxsplit=1)[0]
    if first in _TYPE_WORDS or any(hint in text for hint in _DESCRIPTOR_HINTS):
        return [
            f"{where}: {sample!r} is sent verbatim by the conformance tester; use a descriptor it types "
            "('string', 'integer (optional)', 'number', 'boolean', 'string (a|b)', 'YYYY-MM-DD', 'HH:MM', "
            "'ISO8601') or a literal example value",
        ]
    return []


def _list_response_problems(response: Any) -> list[str]:
    """A collection GET answers ``{"items": [...], "total": <int>, ...}``."""
    if isinstance(response, list):
        return ['a list response must be the envelope {"items": [...], "total": n}']
    if not isinstance(response, dict):
        return []
    if "items" in response:
        problems = []
        if not isinstance(response["items"], list):
            problems.append("items must be a list")
        total = response.get("total")
        if not isinstance(total, int) or isinstance(total, bool):
            problems.append('a list response needs an integer "total" (the number of matching items)')
        return problems
    if any(isinstance(v, list) for v in response.values()):
        return ['a response with a list must put it under "items", with an integer "total"']
    return []


def spec_convention_issues(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` of a schema-valid spec against the v3 conventions.

    Empty for specs older than ``spec_schema_version`` 3. The rules keep a
    spec gradable by the hidden evaluation (``evaluation/contract.py`` and
    the conformance tester): unique endpoints, every endpoint named in the
    prose listed, a public health check, an access-control line the contract
    recognises, admin endpoints under ``/api/admin/``, the list envelope,
    and request samples the tester can turn into valid bodies.
    """
    if not isinstance(spec, dict) or int(spec.get("spec_schema_version") or 1) < SPEC_SCHEMA_VERSION:
        return [], []
    errors: list[str] = []
    warnings: list[str] = []
    endpoints = _labelled_endpoints(spec)

    if "data_model" in spec:
        errors.append("data_model: v3 specs list every model under data_models")
    model_names = [str(m.get("name", "")) for m in spec.get("data_models") or [] if isinstance(m, dict)]
    errors.extend(
        f"data_models: model {name!r} is listed twice"
        for name in sorted(set(model_names))
        if model_names.count(name) > 1
    )

    seen: dict[tuple[str, str], str] = {}
    query_params: set[str] = set()
    for label, admin, ep in endpoints:
        method, path = str(ep.get("method", "")).upper(), str(ep.get("path", ""))
        sig = (method, _norm_path(path))
        if sig in seen:
            errors.append(f"{label}: {method} {path} is already {seen[sig]}")
        seen.setdefault(sig, label)
        query_params.update(ep.get("query") or {})
        auth = ep.get("auth")
        if auth is None:
            errors.append(f"{label}: {method} {path}: auth is required in v3 (public, user or admin)")
        if admin:
            if not path.startswith("/api/admin/"):
                errors.append(f"{label}: admin endpoint paths start with /api/admin/")
            if auth not in (None, "admin"):
                errors.append(f"{label}: admin endpoints have auth 'admin'")
        else:
            if path.startswith("/api/admin"):
                errors.append(f"{label}: /api/admin/ endpoints belong in admin_api_endpoints")
            if auth == "admin":
                errors.append(f"{label}: admin-only endpoints belong in admin_api_endpoints (under /api/admin/)")
        if method == "GET" and not _has_param(path) and path != contract.HEALTH_PATH:
            errors.extend(f"{label}: {method} {path}: {p}" for p in _list_response_problems(ep.get("response")))
        status = ep.get("success_status")
        if status == 204 and ep.get("response") not in (None, {}, []):
            errors.append(f"{label}: success_status 204 has no body; set response to null")
        elif isinstance(status, int) and status >= 300:
            warnings.append(
                f"{label}: success_status {status}: the conformance tester follows redirects and counts only "
                "2xx answers (an external redirect target is unreachable from its network)",
            )
        if method in ("POST", "PUT", "PATCH") and isinstance(ep.get("request"), dict):
            warnings.extend(_verbatim_descriptor_values(ep["request"], "", f"{label}.request"))

    health = [ep for label, admin, ep in endpoints if not admin and str(ep.get("method", "")).upper() == "GET"
              and ep.get("path") == contract.HEALTH_PATH]  # fmt: skip
    if not health:
        errors.append(f"api_endpoints: GET {contract.HEALTH_PATH} is required (the evaluation polls it)")
    elif health[0].get("auth") not in (None, "public"):
        errors.append(f"api_endpoints: GET {contract.HEALTH_PATH} must be public")

    backend = spec.get("backend_requirements") or []
    first = str(backend[0]) if backend else ""
    if not first.lower().startswith("access control:"):
        errors.append('backend_requirements[0]: v3 specs start with an "Access control: ..." requirement')
    elif not contract.writes_require_auth(spec):
        errors.append(
            'backend_requirements[0]: the access-control line must say what it restricts ("require auth for ..." '
            'or "restrict ... to ...")',
        )
    elif target := next(
        (
            (label, ep)
            for label, admin, ep in endpoints
            if not admin
            and str(ep.get("method", "")).upper() == "POST"
            and ep.get("path") not in _IMPLICIT_PATHS
            and not _has_param(str(ep.get("path", "")))
        ),
        None,
    ):
        label, ep = target
        if ep.get("auth") == "public":
            errors.append(
                f"{label}: POST {ep.get('path')} is the write the evaluation sends anonymously and expects "
                "401/403 for; it must have auth 'user' (or list a user-only create endpoint first)",
            )

    known = {sig[1] for sig in seen} | set(_IMPLICIT_PATHS)
    for key in _REQUIREMENT_KEYS:
        for i, line in enumerate(spec.get(key) or []):
            for method, raw in _PROSE_PATH_RE.findall(str(line)):
                path = _norm_path(raw.split("?", 1)[0].rstrip(".,;:)"))
                if path in ("/api", "") or path.endswith("/..."):
                    continue
                if method and method != "WS" and (method, path) not in seen and path not in _IMPLICIT_PATHS:
                    errors.append(f"{key}[{i}]: mentions {method} {raw.rstrip('.,;:)')}, which is not an endpoint")
                elif path not in known:
                    errors.append(f"{key}[{i}]: mentions {raw.rstrip('.,;:)')}, which is not an endpoint")
            errors.extend(
                f"{key}[{i}]: mentions query parameter {name!r}, which no endpoint's query documents"
                for name in _PROSE_QUERY_RE.findall(str(line))
                if name not in query_params
            )
    return errors, warnings


def lint_stack_parity() -> list[str]:
    """Every ``has_frontend`` stack must have a catalog profile with full stage coverage."""
    errors: list[str] = []
    manifest = load_manifest()
    profiles = _catalog_profiles(_load_catalog())
    stage_role = _slug_stage_role_map()

    for stack_slug, config in manifest.get("stacks", {}).items():
        if not config.get("has_frontend"):
            continue
        matching = [p for p in profiles if p.get("scaffolding_slug") == stack_slug]
        if not matching:
            errors.append(f"stack {stack_slug!r}: no system profile in catalog.yaml")
            continue
        for profile in matching:
            covered = {
                stage_role[ref["slug"]]
                for ref in profile.get("block_refs", [])
                if ref.get("type") == "prompt_stage" and ref.get("slug") in stage_role
            }
            missing = _REQUIRED_STAGE_ROLE_PAIRS - covered
            if missing:
                errors.append(
                    f"profile {profile['slug']!r} (stack {stack_slug!r}): "
                    f"missing prompt_stage coverage for {sorted(missing)}",
                )

    return errors


def _system_ref_errors(refs: Any, label: str, block_types: dict[str, str]) -> list[str]:
    """Refs in seeded data name system blocks by slug only, with the block's own type."""
    if not isinstance(refs, list):
        return [f"{label}: block_refs must be a list"]
    errors: list[str] = []
    for ref in refs:
        if not isinstance(ref, dict) or not ref.get("slug"):
            errors.append(f"{label}: every block ref needs a slug")
            continue
        slug = ref["slug"]
        if "version" in ref:
            errors.append(
                f"{label}: block ref {slug!r} pins a version; system refs name the slug only "
                "(the seeder pins the latest system version)",
            )
        if slug not in block_types:
            errors.append(f"{label}: block ref {slug!r} is not a system block")
        elif ref.get("type") and ref["type"] != block_types[slug]:
            errors.append(f"{label}: block ref {slug!r} has type {ref['type']!r}, the block is {block_types[slug]!r}")
    return errors


def lint_catalog() -> list[str]:
    """``catalog.yaml``: the global default exists, one default per stack, refs are system slugs."""
    errors: list[str] = []
    catalog = _load_catalog()
    profiles = _catalog_profiles(catalog)
    block_types = system_block_types()
    stacks = set(load_manifest().get("stacks", {}))
    slugs = [p.get("slug") for p in profiles]

    default_slug = catalog.get("default_bundle_slug")
    if not default_slug:
        errors.append("catalog.yaml: default_bundle_slug is required (the global default profile)")
    elif default_slug not in slugs:
        errors.append(f"catalog.yaml: default_bundle_slug {default_slug!r} is not a catalog profile")

    defaults_per_stack: dict[str, list[str]] = {}
    for profile in profiles:
        slug = profile.get("slug")
        label = f"catalog.yaml profile {slug!r}"
        if not slug:
            errors.append("catalog.yaml: every profile needs a slug")
            continue
        if slugs.count(slug) > 1:
            errors.append(f"{label}: duplicate slug")
        stack = profile.get("scaffolding_slug")
        if stack not in stacks:
            errors.append(f"{label}: scaffolding_slug {stack!r} is not a builtin stack")
        if profile.get("is_default"):
            defaults_per_stack.setdefault(str(stack), []).append(slug)
        errors.extend(_system_ref_errors(profile.get("block_refs", []), label, block_types))
    errors.extend(
        f"catalog.yaml: stack {stack!r} has {len(found)} default profiles ({', '.join(found)}); at most one"
        for stack, found in sorted(defaults_per_stack.items())
        if len(found) > 1
    )
    return sorted(set(errors), key=errors.index)


def lint_manifests() -> list[str]:
    """Per-app pilot manifests: a known app and base profile, system block refs by slug."""
    errors: list[str] = []
    if not MANIFESTS_DIR.is_dir():
        return errors
    catalog_slugs = {p.get("slug") for p in _catalog_profiles(_load_catalog())}
    app_slugs = {path.stem for path in REQUIREMENTS_DIR.glob("*.json") if path.name != "schema.json"}
    block_types = system_block_types()
    for path in sorted(MANIFESTS_DIR.glob("*.yaml")):
        label = f"manifests/{path.name}"
        try:
            manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            errors.append(f"{label}: invalid YAML ({exc})")
            continue
        if manifest.get("app_slug") not in app_slugs:
            errors.append(f"{label}: app_slug {manifest.get('app_slug')!r} is not a requirement spec")
        base = manifest.get("base_profile_slug") or manifest.get("base_bundle_slug") or "system-scaffolding-standard"
        if base not in catalog_slugs:
            errors.append(f"{label}: base profile {base!r} is not a catalog profile")
        errors.extend(_system_ref_errors(manifest.get("extra_block_refs", []), label, block_types))
    return errors


def lint_presets() -> list[str]:
    """System run presets: valid configs, ``<kind>-<slug>.yaml`` names, every default present."""
    from backend.generation.services.agent.harnesses import HARNESSES
    from backend.generation.services.presets import KINDS
    from backend.generation.services.presets import SYSTEM_DEFAULT_SLUGS
    from backend.generation.services.presets import check_config
    from backend.generation.services.presets import harness_default_slug
    from backend.generation.services.presets import load_system_preset_files
    from backend.generation.services.presets import validate_slug

    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for entry in load_system_preset_files():
        label = f"presets/{entry.path.name}"
        if entry.kind not in KINDS:
            errors.append(f"{label}: kind must be one of {', '.join(KINDS)}")
            continue
        errors.extend(f"{label}: {problem}" for problem in validate_slug(entry.slug))
        if entry.path.name != f"{entry.kind}-{entry.slug}.yaml":
            errors.append(f"{label}: file must be named {entry.kind}-{entry.slug}.yaml")
        if (entry.kind, entry.slug) in seen:
            errors.append(f"{label}: duplicate preset {entry.kind}/{entry.slug}")
        seen.add((entry.kind, entry.slug))
        normalized, problems = check_config(entry.kind, entry.config)
        errors.extend(f"{label}: {problem}" for problem in problems)
        if (
            normalized
            and entry.kind == "harness"
            and entry.slug == harness_default_slug(normalized["harness"])
            and normalized["options"]
        ):
            errors.append(f"{label}: a harness's default preset must not set options")
    # The code falls back to built-in defaults, but the files are the record.
    wanted = {("harness", harness_default_slug(name)) for name in HARNESSES} | set(SYSTEM_DEFAULT_SLUGS.items())
    errors.extend(f"presets: missing default preset file {kind}-{slug}.yaml" for kind, slug in sorted(wanted - seen))
    return errors


def lint_starter_packages() -> list[str]:
    """Starter packages: current schema, resolvable files, valid assets, no drift from the catalog."""
    from backend.generation.services.packages.constants import TEMPLATE_PACKAGE_KIND
    from backend.generation.services.packages.constants import TEMPLATE_PACKAGE_SCHEMA_VERSION
    from backend.generation.services.packages.starters import load_starter_document
    from backend.generation.services.packages.starters import starter_paths

    errors: list[str] = []
    for path in starter_paths():
        label = f"packages/{path.name}"
        try:
            document = load_starter_document(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
            continue
        if document.get("kind") != TEMPLATE_PACKAGE_KIND:
            errors.append(f"{label}: kind must be {TEMPLATE_PACKAGE_KIND!r}")
        if document.get("template_package_schema_version") != TEMPLATE_PACKAGE_SCHEMA_VERSION:
            errors.append(f"{label}: template_package_schema_version must be {TEMPLATE_PACKAGE_SCHEMA_VERSION}")
        header = document.get("starter")
        if not isinstance(header, dict):
            errors.append(f"{label}: a starter needs a `starter: {{slug, name, description}}` header")
            header = {}
        if header.get("slug") != path.stem:
            errors.append(f"{label}: starter.slug must be the file name ({path.stem!r})")
        if not str(header.get("name") or "").strip():
            errors.append(f"{label}: starter.name is required")
        assets = document.get("assets")
        if not isinstance(assets, dict):
            errors.append(f"{label}: assets must be a mapping")
            continue
        errors.extend(_starter_asset_errors(assets, label))
    return errors


def _section(assets: dict[str, Any], key: str, label: str, errors: list[str]) -> list[Any]:
    items = assets.get(key) or []
    if not isinstance(items, list):
        errors.append(f"{label}: assets.{key} must be a list")
        return []
    return items


def _starter_asset_errors(assets: dict[str, Any], label: str) -> list[str]:
    from backend.generation.services.packages.starters import resolve_app_spec_entry
    from backend.generation.services.packages.starters import resolve_content_file

    errors: list[str] = []
    validator = _spec_validator()
    for entry in _section(assets, "app_specs", label, errors):
        if not isinstance(entry, dict):
            errors.append(f"{label}: every app spec must be a mapping")
            continue
        try:
            data = resolve_app_spec_entry(entry)
        except (ValueError, OSError) as exc:
            errors.append(f"{label}: {exc}")
            continue
        spec_label = f"{label} app spec {data.get('slug')!r}"
        schema_errors = _spec_errors(validator, data, spec_label)
        errors.extend(schema_errors)
        if not schema_errors:
            errors.extend(f"{spec_label}: {problem}" for problem in spec_convention_issues(data)[0])

    package_blocks: dict[str, str] = {}
    for entry in _section(assets, "blocks", label, errors):
        if not isinstance(entry, dict) or not entry.get("slug") or not entry.get("name"):
            errors.append(f"{label}: every block needs a slug and a name")
            continue
        package_blocks[entry["slug"]] = str(entry.get("block_type") or "prompt_tone")
        if "content_file" in entry:
            try:
                resolve_content_file(entry["content_file"])
            except ValueError as exc:
                errors.append(f"{label} block {entry['slug']!r}: {exc}")
        elif not entry.get("content"):
            errors.append(f"{label} block {entry['slug']!r}: content or content_file is required")

    package_stacks = {
        entry.get("slug") for entry in _section(assets, "stacks", label, errors) if isinstance(entry, dict)
    }
    errors.extend(_starter_profile_errors(assets, label, package_blocks, package_stacks))
    errors.extend(_starter_preset_errors(_section(assets, "presets", label, errors), label))
    return errors


def _starter_profile_errors(
    assets: dict[str, Any],
    label: str,
    package_blocks: dict[str, str],
    package_stacks: set[Any],
) -> list[str]:
    from backend.generation.services.llm_params import validate_llm_params

    errors: list[str] = []
    block_types = {**system_block_types(), **package_blocks}
    stacks = set(load_manifest().get("stacks", {})) | package_stacks
    catalog = {p.get("slug"): p for p in _catalog_profiles(_load_catalog())}
    for profile in assets.get("profiles") or []:
        if not isinstance(profile, dict) or not profile.get("slug") or not profile.get("name"):
            errors.append(f"{label}: every profile needs a slug and a name")
            continue
        plabel = f"{label} profile {profile['slug']!r}"
        if profile.get("scaffolding_slug") not in stacks:
            errors.append(f"{plabel}: scaffolding_slug {profile.get('scaffolding_slug')!r} is not a known stack")
        try:
            validate_llm_params(profile.get("llm_config") or {})
        except ValueError as exc:
            errors.append(f"{plabel}: llm_config: {exc}")
        refs = profile.get("block_refs") or []
        # Versions may only pin the package's own blocks; system blocks are
        # named by slug and resolve to the latest system version on import.
        system_refs = [
            {k: v for k, v in ref.items() if not (k == "version" and ref.get("slug") in package_blocks)}
            for ref in refs
            if isinstance(ref, dict)
        ]
        errors.extend(_system_ref_errors(system_refs, plabel, block_types))
        if profile["slug"] in catalog:
            errors.extend(_catalog_drift_errors(profile, catalog[profile["slug"]], plabel))
    return errors


def _catalog_drift_errors(profile: dict[str, Any], entry: dict[str, Any], label: str) -> list[str]:
    """A starter profile that reuses a catalog slug must equal it, or import would duplicate it."""

    def shape(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": item.get("name"),
            "description": item.get("description", ""),
            "scaffolding_slug": item.get("scaffolding_slug"),
            "block_refs": [(ref.get("type"), ref.get("slug")) for ref in item.get("block_refs") or []],
            "llm_config": item.get("llm_config") or {},
        }

    mine, theirs = shape(profile), shape(entry)
    return [
        f"{label}: {key} differs from catalog.yaml (importing would duplicate the system profile)"
        for key in mine
        if mine[key] != theirs[key]
    ]


def _starter_preset_errors(presets: list[Any], label: str) -> list[str]:
    from backend.generation.services.presets import KINDS
    from backend.generation.services.presets import RESERVED_SLUG_SUFFIX
    from backend.generation.services.presets import check_config
    from backend.generation.services.presets import reserved_slugs
    from backend.generation.services.presets import saving_errors
    from backend.generation.services.presets import validate_slug

    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for preset in presets:
        if not isinstance(preset, dict):
            errors.append(f"{label}: every preset must be a mapping")
            continue
        kind, slug = str(preset.get("kind", "")), str(preset.get("slug", ""))
        plabel = f"{label} preset {kind}/{slug}"
        if kind not in KINDS:
            errors.append(f"{plabel}: kind must be one of {', '.join(KINDS)}")
            continue
        errors.extend(f"{plabel}: {problem}" for problem in validate_slug(slug))
        if slug.endswith(RESERVED_SLUG_SUFFIX) or slug in reserved_slugs(kind):
            errors.append(f"{plabel}: the slug is reserved for system presets")
        if (kind, slug) in seen:
            errors.append(f"{plabel}: duplicate preset")
        seen.add((kind, slug))
        if not str(preset.get("name") or "").strip():
            errors.append(f"{plabel}: name is required")
        normalized, problems = check_config(kind, preset.get("config"))
        errors.extend(f"{plabel}: {problem}" for problem in problems)
        if normalized is not None:
            errors.extend(f"{plabel}: {problem}" for problem in saving_errors(kind, normalized))
    return errors


def lint_all() -> list[str]:
    return (
        lint_requirement_specs()
        + lint_stack_parity()
        + lint_catalog()
        + lint_manifests()
        + lint_presets()
        + lint_starter_packages()
    )
