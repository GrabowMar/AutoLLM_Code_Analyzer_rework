"""Parse, validate, and import bundle/template package payloads.

Import order is app specs → blocks → stacks → profiles → presets, all in one
transaction. Stacks come before profiles so a stack renamed on conflict can
be remapped into the profiles that reference it, as blocks are remapped into
their ``block_refs``.

Conflict strategies (``rename`` | ``overwrite`` | ``error``) share one rule:
identical content that the importer can already see is reused, never
duplicated. Otherwise ``overwrite`` replaces the importer's *own* item --
profiles, blocks and presets get a new version (versions are immutable),
app specs are updated in place (one mutable row per slug), stacks are always
renamed -- and anything else is renamed (``rename``/``overwrite``) or refused
(``error``).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from typing import Any

import yaml
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils.text import slugify

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ContentBlock
from backend.generation.models import GenerationProfile
from backend.generation.services.llm_params import validate_llm_params
from backend.generation.services.packages.constants import ALLOWED_CONFLICT_STRATEGIES
from backend.generation.services.packages.constants import BUNDLE_PACKAGE_KIND
from backend.generation.services.packages.constants import BUNDLE_PACKAGE_SCHEMA_VERSION
from backend.generation.services.packages.constants import SUPPORTED_TEMPLATE_PACKAGE_SCHEMA_VERSIONS
from backend.generation.services.packages.constants import TEMPLATE_PACKAGE_KIND
from backend.generation.services.packages.constants import TEMPLATE_PACKAGE_SCHEMA_VERSION
from backend.generation.services.packages.visibility import visible_blocks_for
from backend.generation.services.versioning import content_hash

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from backend.generation.models import RunPreset

logger = logging.getLogger(__name__)

ASSET_SECTIONS = ("app_specs", "blocks", "profiles", "stacks", "presets")
_DIFFICULTIES = {"", "basic", "standard", "advanced"}
_MAX_PRESET_SLUG_BASE = 70  # leaves room for a "-<n>" rename suffix (slugs are at most 81 chars)


def parse_bundle_package_text(package_text: str) -> dict[str, Any]:
    data = _parse_package_text(package_text)
    if data.get("kind") != BUNDLE_PACKAGE_KIND:
        msg = f"Unsupported package kind: {data.get('kind')!r}"
        raise ValueError(msg)
    if data.get("bundle_package_schema_version") != BUNDLE_PACKAGE_SCHEMA_VERSION:
        msg = f"Unsupported bundle package schema version: {data.get('bundle_package_schema_version')!r}"
        raise ValueError(msg)
    if not isinstance(data.get("bundle"), dict):
        msg = "Bundle package is missing a valid 'bundle' section"
        raise ValueError(msg)
    if not isinstance(data.get("blocks", []), list):
        msg = "Bundle package 'blocks' must be a list"
        raise ValueError(msg)
    return data


def parse_template_package_text(package_text: str) -> dict[str, Any]:
    return upgrade_template_package(_parse_package_text(package_text))


def upgrade_template_package(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a template package document and upgrade it to the current schema (v3).

    v1 used ``app_templates``/``bundles`` and had no stacks; v1 and v2 had no
    presets. Each section must be a list.
    """
    if data.get("kind") != TEMPLATE_PACKAGE_KIND:
        msg = f"Unsupported package kind: {data.get('kind')!r}"
        raise ValueError(msg)
    schema_version = data.get("template_package_schema_version")
    if schema_version not in SUPPORTED_TEMPLATE_PACKAGE_SCHEMA_VERSIONS:
        msg = f"Unsupported template package schema version: {schema_version!r}"
        raise ValueError(msg)
    assets = data.get("assets")
    if not isinstance(assets, dict):
        msg = "Template package is missing a valid 'assets' section"
        raise ValueError(msg)
    if schema_version == 1:
        assets = _upgrade_v1_assets(assets)
    assets = dict(assets)
    for section in ASSET_SECTIONS:
        if assets.get(section) is None:
            assets[section] = []
        if not isinstance(assets[section], list):
            msg = f"Template package 'assets.{section}' must be a list"
            raise ValueError(msg)
    return {**data, "template_package_schema_version": TEMPLATE_PACKAGE_SCHEMA_VERSION, "assets": assets}


def _upgrade_v1_assets(assets: dict[str, Any]) -> dict[str, Any]:
    """Map v1 asset keys to the v2 vocabulary (prompt_templates stay for the skip path)."""
    upgraded = dict(assets)
    if "app_specs" not in upgraded:
        upgraded["app_specs"] = upgraded.pop("app_templates", [])
    if "profiles" not in upgraded:
        upgraded["profiles"] = upgraded.pop("bundles", [])
    upgraded.setdefault("stacks", [])
    return upgraded


def import_bundle_package(
    *,
    package_text: str,
    user: AbstractUser,
    conflict_strategy: str = "rename",
) -> GenerationProfile:
    package = parse_bundle_package_text(package_text)
    imported = _import_assets(
        assets={
            "blocks": package.get("blocks", []),
            "bundles": [package["bundle"]],
        },
        user=user,
        conflict_strategy=conflict_strategy,
    )
    profiles = imported.get("profiles", [])
    if not profiles:
        msg = "Bundle package did not import any profiles"
        raise ValueError(msg)
    return profiles[0]


def import_template_package(
    *,
    package_text: str,
    user: AbstractUser,
    conflict_strategy: str = "rename",
) -> dict[str, list[Any]]:
    package = parse_template_package_text(package_text)
    return import_template_package_data(package=package, user=user, conflict_strategy=conflict_strategy)


def import_template_package_data(
    *,
    package: dict[str, Any],
    user: AbstractUser,
    conflict_strategy: str = "rename",
) -> dict[str, list[Any]]:
    """Import an already-parsed package document (validated and upgraded here)."""
    package = upgrade_template_package(package)
    return _import_assets(assets=package["assets"], user=user, conflict_strategy=conflict_strategy)


def _import_assets(
    *,
    assets: dict[str, Any],
    user: AbstractUser,
    conflict_strategy: str,
) -> dict[str, list[Any]]:
    if conflict_strategy not in ALLOWED_CONFLICT_STRATEGIES:
        msg = f"Unsupported conflict strategy: {conflict_strategy}"
        raise ValueError(msg)

    block_ref_map: dict[tuple[str, int], tuple[str, int]] = {}
    stack_slug_map: dict[str, str] = {}
    imported: dict[str, list[Any]] = {section: [] for section in ASSET_SECTIONS}

    # Pre-rename package files may still carry a prompt_templates section for
    # the removed PromptTemplate model; the rest imports normally.
    skipped_prompts = len(assets.get("prompt_templates") or [])
    if skipped_prompts:
        logger.warning(
            "Skipping %d legacy prompt_templates entries in package import",
            skipped_prompts,
        )

    # Accept raw v1 keys too (import_bundle_package builds assets directly).
    app_specs = assets.get("app_specs", assets.get("app_templates", [])) or []
    profiles = assets.get("profiles", assets.get("bundles", [])) or []

    with transaction.atomic():
        for raw_app in app_specs:
            template = _import_app_template(
                raw_app,
                user=user,
                conflict_strategy=conflict_strategy,
            )
            imported["app_specs"].append(template)

        for raw_block in assets.get("blocks") or []:
            block = _import_block(
                raw_block,
                user=user,
                conflict_strategy=conflict_strategy,
            )
            original = (str(raw_block.get("slug", "")).strip(), int(raw_block.get("version", 1)))
            block_ref_map[original] = (block.slug, block.version)
            imported["blocks"].append(block)

        for raw_stack in assets.get("stacks") or []:
            stack = _import_stack(raw_stack, user=user, conflict_strategy=conflict_strategy)
            stack_slug_map[str(raw_stack.get("slug", "")).strip()] = stack.slug
            imported["stacks"].append(stack)

        for raw_bundle in profiles:
            bundle = _import_bundle(
                raw_bundle,
                user=user,
                conflict_strategy=conflict_strategy,
                block_ref_map=block_ref_map,
                stack_slug_map=stack_slug_map,
            )
            imported["profiles"].append(bundle)

        for raw_preset in assets.get("presets") or []:
            imported["presets"].append(
                _import_preset(raw_preset, user=user, conflict_strategy=conflict_strategy),
            )

    return imported


# ── stacks ────────────────────────────────────────────────────────────

_STACK_CONTENT_FIELDS = (
    "has_frontend",
    "default_port",
    "patch_profile",
    "frontend_component",
    "backend_filename",
    "backend_base_image",
    "frontend_base_image",
    "server_kind",
)


def _import_stack(raw_stack: Any, *, user: AbstractUser, conflict_strategy: str):
    """Import a user stack, re-validating against this install's allowlist.

    Imported stacks are always ``generated`` dockerfile mode and enter the
    approval queue like locally created ones. The importer's own identical
    stack is reused; any other slug conflict is renamed (``overwrite`` would
    mutate an immutable stack version) or refused (``error``).
    """
    from backend.runtime.models import Stack
    from backend.runtime.services.scaffolding import is_known_stack_slug
    from backend.runtime.services.stack_validation import validate_stack_config
    from backend.runtime.services.stack_validation import validate_stack_files

    if not isinstance(raw_stack, dict):
        msg = "Each stack must be an object"
        raise ValueError(msg)
    slug = str(raw_stack.get("slug", "")).strip()
    if not slug:
        msg = "Each stack must include a slug"
        raise ValueError(msg)

    files = raw_stack.get("files") or {}
    has_frontend = bool(raw_stack.get("has_frontend"))
    payload = {
        "name": str(raw_stack.get("name", slug)),
        "description": str(raw_stack.get("description", "")),
        "has_frontend": has_frontend,
        "default_port": int(raw_stack.get("default_port", 8000)),
        "patch_profile": raw_stack.get("patch_profile")
        if raw_stack.get("patch_profile") in ("flask", "none")
        else "none",
        "frontend_component": str(raw_stack.get("frontend_component", "")),
        "backend_filename": str(raw_stack.get("backend_filename", "app.py")),
        "backend_base_image": str(raw_stack.get("backend_base_image", "")),
        "frontend_base_image": str(raw_stack.get("frontend_base_image", "")) if has_frontend else "",
        "server_kind": raw_stack.get("server_kind")
        if raw_stack.get("server_kind") in ("python", "uvicorn")
        else "python",
    }
    errors = validate_stack_files(files) + validate_stack_config(
        backend_base_image=payload["backend_base_image"],
        frontend_base_image=payload["frontend_base_image"],
        has_frontend=payload["has_frontend"],
        default_port=payload["default_port"],
        backend_filename=payload["backend_filename"],
    )
    if errors:
        msg = f"Stack {slug}: {'; '.join(errors)}"
        raise ValueError(msg)

    if Stack.objects.filter(slug=slug).exists() or is_known_stack_slug(slug):
        latest = Stack.objects.filter(slug=slug).order_by("-version").first()
        if (
            latest is not None
            and not latest.is_builtin
            and not latest.is_archived
            and latest.created_by_id == user.id
            and _stack_matches(latest, payload, files)
        ):
            return latest
        if conflict_strategy == "error":
            msg = f"Stack conflict for slug {slug}"
            raise ValueError(msg)
        # "overwrite" would mutate immutable versions; rename in both cases.
        slug = _unique_slug(Stack, slug)

    approved = bool(getattr(user, "is_staff", False)) or not settings.STACK_REQUIRE_APPROVAL
    return Stack.objects.create(
        slug=slug,
        version=1,
        is_builtin=False,
        is_approved=approved,
        created_by=user,
        aliases=[],
        files=files,
        content_hash=content_hash({**payload, "files": files}),
        dockerfile_mode=Stack.DockerfileMode.GENERATED,
        **payload,
    )


def _stack_matches(row: Any, payload: dict[str, Any], files: dict[str, Any]) -> bool:
    return all(getattr(row, field) == payload[field] for field in _STACK_CONTENT_FIELDS) and (row.files or {}) == files


# ── app specs ─────────────────────────────────────────────────────────


def _import_app_template(
    raw_template: Any,
    *,
    user: AbstractUser,
    conflict_strategy: str,
) -> AppRequirementTemplate:
    payload = _validate_app_template_payload(raw_template)
    existing = AppRequirementTemplate.objects.filter(slug=payload["slug"]).first()
    if existing is None:
        return AppRequirementTemplate.objects.create(
            **payload,
            is_default=False,
            created_by=user,
        )

    if _app_template_matches(existing, payload) and _is_visible_defaultish(existing, user):
        return existing

    if conflict_strategy == "overwrite" and existing.created_by_id == user.id:
        _assign_fields(existing, payload)
        existing.save(update_fields=[*payload.keys() - {"slug"}, "updated_at"])
        return existing

    if conflict_strategy == "error":
        msg = f"App template conflict for slug {payload['slug']}"
        raise ValueError(msg)

    payload["slug"] = _unique_slug(AppRequirementTemplate, payload["slug"])
    return AppRequirementTemplate.objects.create(
        **payload,
        is_default=False,
        created_by=user,
    )


# ── blocks ────────────────────────────────────────────────────────────


def _import_block(
    raw_block: Any,
    *,
    user: AbstractUser,
    conflict_strategy: str,
) -> ContentBlock:
    payload = _validate_block_payload(raw_block)
    slug = payload["slug"]
    version = payload["version"]
    block_hash = _block_content_hash(payload)
    existing = ContentBlock.objects.filter(slug=slug, version=version).first()
    if existing is None:
        return ContentBlock.objects.create(
            **payload,
            content_hash=block_hash,
            is_system=False,
            created_by=user,
        )

    if _block_matches(existing, payload) and _is_visible_systemish(existing, user):
        return existing

    if conflict_strategy == "overwrite" and existing.created_by_id == user.id:
        # Profiles pin (slug, version), so the replacement is a new version;
        # the package's own profiles are remapped onto it.
        next_version = (ContentBlock.objects.filter(slug=slug).aggregate(top=Max("version"))["top"] or 0) + 1
        return ContentBlock.objects.create(
            **{**payload, "version": next_version},
            content_hash=block_hash,
            is_system=False,
            created_by=user,
        )

    if conflict_strategy == "error":
        msg = f"Block conflict for {slug} v{version}"
        raise ValueError(msg)

    payload["slug"] = _unique_block_slug(slug, version)
    return ContentBlock.objects.create(
        **payload,
        content_hash=block_hash,
        is_system=False,
        created_by=user,
    )


def _block_content_hash(payload: dict[str, Any]) -> str:
    """The seeder's block hash (``seeding._upsert_content_block_version``)."""
    return content_hash(
        {"block_type": payload["block_type"], "content": payload["content"], "metadata": payload["metadata"] or {}},
    )


# ── profiles ──────────────────────────────────────────────────────────


def _import_bundle(
    raw_bundle: Any,
    *,
    user: AbstractUser,
    conflict_strategy: str,
    block_ref_map: dict[tuple[str, int], tuple[str, int]],
    stack_slug_map: dict[str, str] | None = None,
) -> GenerationProfile:
    payload = _validate_bundle_payload(raw_bundle)
    # A stack renamed on import keeps its profiles pointing at it.
    payload["scaffolding_slug"] = (stack_slug_map or {}).get(payload["scaffolding_slug"], payload["scaffolding_slug"])
    # An unknown stack would silently fall back to the default one at run time.
    from backend.runtime.services.scaffolding import is_known_stack_slug

    if not is_known_stack_slug(payload["scaffolding_slug"]):
        msg = f"Bundle {payload['slug']} references unknown stack {payload['scaffolding_slug']!r}"
        raise ValueError(msg)
    normalized_refs = []
    for raw_ref in payload["block_refs"]:
        ref = _validate_block_ref(raw_ref)
        if ref["version"] is None:
            ref["version"] = _default_ref_version(ref["slug"], user=user, block_ref_map=block_ref_map)
        mapped = block_ref_map.get((ref["slug"], ref["version"]))
        if mapped:
            ref["slug"], ref["version"] = mapped
            block = ContentBlock.objects.filter(slug=ref["slug"], version=ref["version"]).first()
        else:
            block = (
                visible_blocks_for(user)
                .filter(
                    slug=ref["slug"],
                    version=ref["version"],
                )
                .first()
            )
            if not block:
                msg = f"Bundle references missing block {ref['slug']} v{ref['version']}"
                raise ValueError(msg)
        if not ref["type"] and block is not None:
            # An untyped ref would be ignored by prompt assembly.
            ref["type"] = block.block_type
        normalized_refs.append(ref)
    payload["block_refs"] = normalized_refs
    profile_hash = content_hash(
        {
            "scaffolding_slug": payload["scaffolding_slug"],
            "block_refs": payload["block_refs"],
            "llm_config": payload["llm_config"],
        },
    )

    # Compare against the latest version of the slug — multiple versions can
    # now exist, and an unordered .first() would pick an arbitrary one.
    existing = GenerationProfile.objects.filter(slug=payload["slug"]).order_by("-version").first()
    if existing is None:
        return GenerationProfile.objects.create(
            **payload,
            version=1,
            content_hash=profile_hash,
            is_system=False,
            is_default=False,
            created_by=user,
        )

    if _bundle_matches(existing, payload) and _is_visible_systemish(existing, user):
        return existing

    if conflict_strategy == "overwrite" and existing.created_by_id == user.id and not existing.is_system:
        # Versions are immutable: "overwrite" adds version+1 of the importer's profile.
        return GenerationProfile.objects.create(
            **payload,
            version=existing.version + 1,
            content_hash=profile_hash,
            is_system=False,
            is_default=existing.is_default,
            created_by=user,
        )

    if conflict_strategy == "error":
        msg = f"Bundle conflict for slug {payload['slug']}"
        raise ValueError(msg)

    payload["slug"] = _unique_slug(GenerationProfile, payload["slug"])
    return GenerationProfile.objects.create(
        **payload,
        content_hash=profile_hash,
        is_system=False,
        is_default=False,
        created_by=user,
    )


def _default_ref_version(
    slug: str,
    *,
    user: AbstractUser,
    block_ref_map: dict[tuple[str, int], tuple[str, int]],
) -> int:
    """Version for a ref that names only a slug.

    The package's own block of that slug wins, then the latest system version
    (how starters reference seeded prompt files), then the importer's latest
    own version.
    """
    package_versions = [version for (ref_slug, version) in block_ref_map if ref_slug == slug]
    if package_versions:
        return max(package_versions)
    for qs in (
        ContentBlock.objects.filter(slug=slug, is_system=True),
        visible_blocks_for(user).filter(slug=slug),
    ):
        latest = qs.order_by("-version").values_list("version", flat=True).first()
        if latest is not None:
            return latest
    msg = f"Bundle references missing block {slug}"
    raise ValueError(msg)


# ── presets ───────────────────────────────────────────────────────────


def _import_preset(raw_preset: Any, *, user: AbstractUser, conflict_strategy: str) -> RunPreset:
    """Import a run preset; slugs are global per kind (any owner), like ``POST /presets/``.

    * identical content under the same slug that the importer can see
      (system or own) is reused;
    * a harness preset whose options are all defaults *is* that harness's
      system default preset, which is reused;
    * ``overwrite`` of the importer's own preset adds a new version (never in
      place; a name/description difference alone updates the metadata);
    * anything else under a taken or reserved slug is renamed, or refused
      with ``error``.
    """
    from backend.generation.models import RunPreset
    from backend.generation.services.presets import PresetError
    from backend.generation.services.presets import config_hash
    from backend.generation.services.presets import create_preset
    from backend.generation.services.presets import slug_problem
    from backend.generation.services.presets import system_default_preset
    from backend.generation.services.presets import update_preset

    payload = _validate_preset_payload(raw_preset)
    kind, slug, config = payload["kind"], payload["slug"], payload["config"]
    label = f"Preset {kind}/{slug}"

    if kind == "harness" and not config.get("options"):
        default = system_default_preset("harness", config["harness"])
        if default is None:
            msg = f"{label}: the system default preset of {config['harness']} is unavailable"
            raise ValueError(msg)
        return default

    digest = config_hash(kind, config)
    latest = RunPreset.objects.filter(kind=kind, slug=slug).order_by("-version").first()
    try:
        if latest is not None:
            own = not latest.is_system and latest.created_by_id == user.id
            usable = (latest.is_system or own) and not latest.is_archived
            if usable and latest.content_hash == digest:
                metadata_differs = (latest.name, latest.description) != (payload["name"], payload["description"])
                if conflict_strategy == "overwrite" and own and metadata_differs:
                    return update_preset(user, kind=kind, slug=slug, name=payload["name"], **_preset_body(payload))
                return latest
            if conflict_strategy == "overwrite" and own and not latest.is_archived:
                return update_preset(user, kind=kind, slug=slug, name=payload["name"], **_preset_body(payload))
            if conflict_strategy == "error":
                msg = f"{label}: slug already in use"
                raise ValueError(msg)
            slug = _unique_preset_slug(kind, slug)
        elif problem := slug_problem(kind, slug):
            if conflict_strategy == "error":
                msg = f"{label}: {problem}"
                raise ValueError(msg)
            slug = _unique_preset_slug(kind, slug)
        return create_preset(user, kind=kind, slug=slug, name=payload["name"], **_preset_body(payload))
    except PresetError as exc:
        msg = f"{label}: {exc.detail}"
        raise ValueError(msg) from exc


def _preset_body(payload: dict[str, Any]) -> dict[str, Any]:
    return {"description": payload["description"], "config": payload["config"]}


def _validate_preset_payload(raw_preset: Any) -> dict[str, Any]:
    from backend.generation.services.presets import KINDS
    from backend.generation.services.presets import check_config

    if not isinstance(raw_preset, dict):
        msg = "Each preset must be an object"
        raise ValueError(msg)
    slug = str(raw_preset.get("slug", "")).strip()
    kind = str(raw_preset.get("kind", "")).strip()
    if not slug:
        msg = "Each preset must include a slug"
        raise ValueError(msg)
    if kind not in KINDS:
        msg = f"Preset {slug}: kind must be one of {', '.join(KINDS)}"
        raise ValueError(msg)
    normalized, errors = check_config(kind, raw_preset.get("config"))
    if errors or normalized is None:
        msg = f"Preset {kind}/{slug}: {'; '.join(errors)}"
        raise ValueError(msg)
    return {
        "kind": kind,
        "slug": slug,
        "name": str(raw_preset.get("name") or slug).strip(),
        "description": str(raw_preset.get("description") or "").strip(),
        "config": normalized,
    }


def _unique_preset_slug(kind: str, base_slug: str) -> str:
    from backend.generation.services.presets import SLUG_PATTERN
    from backend.generation.services.presets import slug_problem

    candidate = slugify(base_slug).replace("_", "-").strip("-")[:_MAX_PRESET_SLUG_BASE].strip("-")
    if not SLUG_PATTERN.match(candidate):
        candidate = "imported-preset"
    if slug_problem(kind, candidate) is None:
        return candidate
    idx = 2
    while slug_problem(kind, f"{candidate}-{idx}") is not None:
        idx += 1
    return f"{candidate}-{idx}"


# ── payload validation ────────────────────────────────────────────────


def _parse_package_text(package_text: str) -> dict[str, Any]:
    try:
        data = json.loads(package_text)
    except json.JSONDecodeError:
        try:
            data = yaml.safe_load(package_text)
        except yaml.YAMLError as exc:
            msg = f"Package is neither valid JSON nor valid YAML: {exc}"
            raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = "Package must be a JSON or YAML object"
        raise ValueError(msg)
    return data


def _validate_app_template_payload(raw_template: Any) -> dict[str, Any]:
    if not isinstance(raw_template, dict):
        msg = "App template must be an object"
        raise ValueError(msg)
    slug = str(raw_template.get("slug", "")).strip()
    name = str(raw_template.get("name", "")).strip()
    if not slug or not name:
        msg = "App template requires name and slug"
        raise ValueError(msg)
    payload = {
        "name": name,
        "slug": slug,
        "category": str(raw_template.get("category", "")).strip(),
        "description": str(raw_template.get("description", "")).strip(),
        "backend_requirements": raw_template.get("backend_requirements") or [],
        "frontend_requirements": raw_template.get("frontend_requirements") or [],
        "admin_requirements": raw_template.get("admin_requirements") or [],
        "api_endpoints": raw_template.get("api_endpoints") or [],
        "data_model": raw_template.get("data_model") or {},
        "admin_api_endpoints": raw_template.get("admin_api_endpoints") or [],
    }
    # v3 exports carry the difficulty; older packages leave it unset (and
    # unmatched against, so they still reuse identical specs).
    if "difficulty" in raw_template:
        difficulty = str(raw_template.get("difficulty") or "").strip()
        if difficulty not in _DIFFICULTIES:
            msg = f"App template {slug}: difficulty must be one of basic, standard, advanced"
            raise ValueError(msg)
        payload["difficulty"] = difficulty
    # The same contract the seeder's specs and POST /app-specs/ meet (the
    # slug pattern aside: older specs and import renames use hyphens).
    from backend.generation.services.data_lint import app_spec_errors

    errors = app_spec_errors(payload, label=f"App template {slug}", check_slug=False)
    if errors:
        msg = "; ".join(errors[:5]) + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else "")
        raise ValueError(msg)
    return payload


def _validate_block_payload(raw_block: Any) -> dict[str, Any]:
    if not isinstance(raw_block, dict):
        msg = "Each block must be an object"
        raise ValueError(msg)
    slug = str(raw_block.get("slug", "")).strip()
    name = str(raw_block.get("name", "")).strip()
    content = str(raw_block.get("content", ""))
    if not slug:
        msg = "Each block must include a slug"
        raise ValueError(msg)
    if not name:
        msg = f"Block {slug} is missing a name"
        raise ValueError(msg)
    if not content:
        msg = f"Block {slug} is missing content"
        raise ValueError(msg)
    block_type = str(raw_block.get("block_type", "")).strip() or ContentBlock.BlockType.PROMPT_TONE
    if block_type not in ContentBlock.BlockType.values:
        msg = f"Block {slug} has unknown block_type {block_type!r} (one of {', '.join(ContentBlock.BlockType.values)})"
        raise ValueError(msg)
    return {
        "block_type": block_type,
        "slug": slug,
        "version": int(raw_block.get("version", 1)),
        "name": name,
        "description": str(raw_block.get("description", "")).strip(),
        "content": content,
        "metadata": raw_block.get("metadata") or {},
    }


def _validate_bundle_payload(raw_bundle: Any) -> dict[str, Any]:
    if not isinstance(raw_bundle, dict):
        msg = "Bundle must be an object"
        raise ValueError(msg)
    slug = str(raw_bundle.get("slug", "")).strip()
    name = str(raw_bundle.get("name", "")).strip()
    if not slug or not name:
        msg = "Bundle requires name and slug"
        raise ValueError(msg)
    block_refs = raw_bundle.get("block_refs") or []
    if not isinstance(block_refs, list):
        msg = f"Bundle {slug} has invalid block_refs"
        raise ValueError(msg)
    try:
        llm_config = validate_llm_params(raw_bundle.get("llm_config") or {})
    except ValueError as exc:
        msg = f"Bundle {slug} has invalid llm_config: {exc}"
        raise ValueError(msg) from exc
    default_stack = settings.GENERATION_DEFAULT_STACK
    return {
        "name": name,
        "slug": slug,
        "description": str(raw_bundle.get("description", "")).strip(),
        "scaffolding_slug": str(raw_bundle.get("scaffolding_slug", default_stack)).strip() or default_stack,
        "block_refs": block_refs,
        "llm_config": llm_config,
    }


def _validate_block_ref(raw_ref: Any) -> dict[str, Any]:
    """``{type, slug, version}``; ``version`` is None when the ref names only a slug."""
    if not isinstance(raw_ref, dict):
        msg = "Each block reference must be an object"
        raise ValueError(msg)
    slug = str(raw_ref.get("slug", "")).strip()
    if not slug:
        msg = "Block reference missing slug"
        raise ValueError(msg)
    version = raw_ref.get("version")
    return {
        "type": str(raw_ref.get("type", "")).strip() or "",
        "slug": slug,
        "version": None if version is None else int(version),
    }


def _assign_fields(instance: Any, payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        setattr(instance, key, value)


def _is_visible_defaultish(instance: Any, user: AbstractUser) -> bool:
    return bool(getattr(instance, "is_default", False) or instance.created_by_id == user.id)


def _is_visible_systemish(instance: Any, user: AbstractUser) -> bool:
    return bool(getattr(instance, "is_system", False) or instance.created_by_id == user.id)


def _app_template_matches(existing: AppRequirementTemplate, payload: dict[str, Any]) -> bool:
    return (
        existing.name == payload["name"]
        and existing.category == payload["category"]
        and existing.description == payload["description"]
        and (existing.backend_requirements or []) == payload["backend_requirements"]
        and (existing.frontend_requirements or []) == payload["frontend_requirements"]
        and (existing.admin_requirements or []) == payload["admin_requirements"]
        and (existing.api_endpoints or []) == payload["api_endpoints"]
        and (existing.data_model or {}) == payload["data_model"]
        and (existing.admin_api_endpoints or []) == payload["admin_api_endpoints"]
        and ("difficulty" not in payload or existing.difficulty == payload["difficulty"])
    )


def _block_matches(existing: ContentBlock, payload: dict[str, Any]) -> bool:
    return (
        existing.block_type == payload["block_type"]
        and existing.name == payload["name"]
        and existing.description == payload["description"]
        and existing.content == payload["content"]
        and (existing.metadata or {}) == (payload["metadata"] or {})
    )


def _bundle_matches(existing: GenerationProfile, payload: dict[str, Any]) -> bool:
    return (
        existing.name == payload["name"]
        and existing.description == payload["description"]
        and existing.scaffolding_slug == payload["scaffolding_slug"]
        and (existing.block_refs or []) == payload["block_refs"]
        and (existing.llm_config or {}) == payload["llm_config"]
    )


def _unique_slug(model, base_slug: str) -> str:
    candidate = slugify(base_slug) or "imported-item"
    if not model.objects.filter(slug=candidate).exists():
        return candidate
    idx = 2
    while True:
        next_candidate = f"{candidate}-{idx}"
        if not model.objects.filter(slug=next_candidate).exists():
            return next_candidate
        idx += 1


def _unique_block_slug(base_slug: str, version: int) -> str:
    candidate = slugify(base_slug) or "imported-block"
    if not ContentBlock.objects.filter(slug=candidate, version=version).exists():
        return candidate
    idx = 2
    while True:
        next_candidate = f"{candidate}-{idx}"
        if not ContentBlock.objects.filter(slug=next_candidate, version=version).exists():
            return next_candidate
        idx += 1
