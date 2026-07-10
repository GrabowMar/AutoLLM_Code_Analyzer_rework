"""Static validation of backend/generation/data/: requirement specs + stack parity.

Pure data checks — no DB access — so they can run in CI or as a pytest test
without a database. Used by ``manage.py lint_generation_data`` and
``backend.generation.tests.test_data_lint``.
"""

from __future__ import annotations

import json

import jsonschema
import yaml

from backend.generation.seeding import CATALOG_PATH
from backend.generation.seeding import PROMPT_STAGE_SEEDS
from backend.generation.seeding import REQUIREMENTS_DIR
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


def lint_requirement_specs() -> list[str]:
    """Validate every ``data/requirements/*.json`` against ``schema.json``."""
    errors: list[str] = []
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    for path in sorted(REQUIREMENTS_DIR.glob("*.json")):
        if path.name == "schema.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: invalid JSON ({exc})")
            continue
        for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
            loc = ".".join(str(p) for p in err.path) or "<root>"
            errors.append(f"{path.name}: {loc}: {err.message}")

    return errors


def lint_stack_parity() -> list[str]:
    """Every ``has_frontend`` stack must have a catalog bundle with full stage coverage."""
    errors: list[str] = []
    manifest = load_manifest()
    catalog = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}
    bundles = catalog.get("bundles", [])
    stage_role = _slug_stage_role_map()

    for stack_slug, config in manifest.get("stacks", {}).items():
        if not config.get("has_frontend"):
            continue
        matching = [b for b in bundles if b.get("scaffolding_slug") == stack_slug]
        if not matching:
            errors.append(f"stack {stack_slug!r}: no system bundle in catalog.yaml")
            continue
        for bundle in matching:
            covered = {
                stage_role[ref["slug"]]
                for ref in bundle.get("block_refs", [])
                if ref.get("type") == "prompt_stage" and ref.get("slug") in stage_role
            }
            missing = _REQUIRED_STAGE_ROLE_PAIRS - covered
            if missing:
                errors.append(
                    f"bundle {bundle['slug']!r} (stack {stack_slug!r}): "
                    f"missing prompt_stage coverage for {sorted(missing)}",
                )

    return errors


def lint_all() -> list[str]:
    return lint_requirement_specs() + lint_stack_parity()
