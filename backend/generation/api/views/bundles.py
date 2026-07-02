"""Template bundle and content block catalog API."""

from __future__ import annotations

import jinja2
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Query
from ninja.errors import HttpError

from backend.generation.api.schema import BundleImportSchema
from backend.generation.api.schema import ContentBlockCreateSchema
from backend.generation.api.schema import ContentBlockSchema
from backend.generation.api.schema import StarterTemplatePackageImportSchema
from backend.generation.api.schema import StarterTemplatePackageSchema
from backend.generation.api.schema import TemplateBundleSchema
from backend.generation.api.schema import TemplatePackageExportSchema
from backend.generation.api.schema import TemplatePackageImportSchema
from backend.generation.api.views._router import router
from backend.generation.models import ContentBlock
from backend.generation.models import TemplateBundle
from backend.generation.services.bundle_packages import dump_bundle_package
from backend.generation.services.bundle_packages import dump_template_package
from backend.generation.services.bundle_packages import export_bundle_package
from backend.generation.services.bundle_packages import export_template_package
from backend.generation.services.bundle_packages import import_bundle_package
from backend.generation.services.bundle_packages import import_starter_template_package
from backend.generation.services.bundle_packages import import_template_package
from backend.generation.services.bundle_packages import list_starter_template_packages
from backend.generation.services.bundle_packages import visible_blocks_for
from backend.generation.services.bundle_packages import visible_bundles_for
from backend.generation.services.bundle_resolver import resolve_block_refs


def _validate_jinja(content: str) -> str | None:
    try:
        jinja2.Environment(undefined=jinja2.StrictUndefined).parse(content)
    except jinja2.TemplateSyntaxError as exc:
        return str(exc)
    return None


@router.get("/blocks/catalog/", response=list[ContentBlockSchema])
def list_catalog_blocks(request):
    """List system content blocks (read-only catalog)."""
    return ContentBlock.objects.filter(is_system=True).order_by("block_type", "slug")


def _mutable_block_or_403(user, *, slug: str, version: int) -> ContentBlock:
    block = get_object_or_404(visible_blocks_for(user), slug=slug, version=version)
    if block.created_by_id == getattr(user, "id", None):
        return block
    if getattr(user, "is_staff", False) and block.is_system:
        return block
    raise HttpError(403, "You can only modify your own blocks.")


def _mutable_bundle_or_403(user, *, slug: str) -> TemplateBundle:
    bundle = get_object_or_404(visible_bundles_for(user), slug=slug)
    if bundle.created_by_id == getattr(user, "id", None):
        return bundle
    if getattr(user, "is_staff", False) and bundle.is_system:
        return bundle
    raise HttpError(403, "You can only modify your own bundles.")


@router.get("/blocks/", response=list[ContentBlockSchema])
def list_blocks(request):
    """List system blocks plus the current user's blocks."""
    return visible_blocks_for(request.auth).order_by("block_type", "slug", "-version")


@router.post("/blocks/", response={200: ContentBlockSchema, 400: dict})
def create_block(request, payload: ContentBlockCreateSchema):
    """Create a user-owned content block."""
    err = _validate_jinja(payload.content)
    if err:
        return 400, {"detail": f"Invalid Jinja2: {err}"}
    if ContentBlock.objects.filter(slug=payload.slug, version=payload.version).exists():
        return 400, {"detail": f"Block {payload.slug} v{payload.version} already exists"}
    return ContentBlock.objects.create(
        block_type=payload.block_type,
        slug=payload.slug,
        version=payload.version,
        name=payload.name,
        description=payload.description,
        content=payload.content,
        metadata=payload.metadata,
        is_system=False,
        created_by=request.auth,
    )


@router.get("/blocks/{slug}/", response=ContentBlockSchema)
def get_block(request, slug: str, version: int = 1):
    return get_object_or_404(visible_blocks_for(request.auth), slug=slug, version=version)


@router.put("/blocks/{slug}/", response={200: ContentBlockSchema, 400: dict})
def update_block(
    request,
    slug: str,
    payload: ContentBlockCreateSchema,
    version: int = Query(1),
):
    block = _mutable_block_or_403(request.auth, slug=slug, version=version)
    err = _validate_jinja(payload.content)
    if err:
        return 400, {"detail": f"Invalid Jinja2: {err}"}

    next_slug = payload.slug.strip()
    next_version = payload.version
    if (next_slug != block.slug or next_version != block.version) and ContentBlock.objects.filter(
        slug=next_slug,
        version=next_version,
    ).exists():
        return 400, {"detail": f"Block {next_slug} v{next_version} already exists"}

    block.block_type = payload.block_type
    block.slug = next_slug
    block.version = next_version
    block.name = payload.name
    block.description = payload.description
    block.content = payload.content
    block.metadata = payload.metadata
    block.save()
    return block


@router.delete("/blocks/{slug}/")
def delete_block(request, slug: str, version: int = Query(1)):
    block = _mutable_block_or_403(request.auth, slug=slug, version=version)
    block.delete()
    return {"success": True}


@router.get("/bundles/", response=list[TemplateBundleSchema])
def list_bundles(request):
    return visible_bundles_for(request.auth).order_by("-is_default", "name")


@router.post("/bundles/", response={200: TemplateBundleSchema, 400: dict})
def create_bundle(request):
    raise HttpError(
        403,
        "Manual bundle creation is disabled. Export or import template packages from existing assets instead.",
    )


@router.post("/bundles/import/", response={200: TemplateBundleSchema, 400: dict})
def import_bundle(request, payload: BundleImportSchema):
    try:
        bundle = import_bundle_package(
            package_text=payload.package_text,
            user=request.auth,
            conflict_strategy=payload.conflict_strategy,
        )
    except ValueError as exc:
        return 400, {"detail": str(exc)}
    return bundle


@router.post("/packages/export/")
def export_package(
    request,
    payload: TemplatePackageExportSchema,
    fmt: str = Query("json", alias="format"),
):
    if fmt not in {"json", "yaml"}:
        raise HttpError(400, "format must be 'json' or 'yaml'")
    package = export_template_package(
        user=request.auth,
        scaffolding_slugs=payload.scaffolding_slugs,
        app_template_slugs=payload.app_template_slugs,
        prompt_template_slugs=payload.prompt_template_slugs,
        bundle_slugs=payload.bundle_slugs,
        block_refs=[{"type": ref.type, "slug": ref.slug, "version": ref.version} for ref in payload.block_refs],
    )
    asset_count = sum(len(items) for items in package.get("assets", {}).values())
    if asset_count == 0:
        raise HttpError(400, "Select at least one asset to export.")
    response_payload, content_type = dump_template_package(package, fmt)
    extension = "yaml" if fmt == "yaml" else "json"
    response = HttpResponse(response_payload, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="generation-template-package.{extension}"'
    return response


@router.post("/packages/import/", response={200: dict, 400: dict})
def import_package(request, payload: TemplatePackageImportSchema):
    try:
        imported = import_template_package(
            package_text=payload.package_text,
            user=request.auth,
            conflict_strategy=payload.conflict_strategy,
        )
    except ValueError as exc:
        return 400, {"detail": str(exc)}
    return {
        "scaffolding_templates": [item.slug for item in imported["scaffolding_templates"]],
        "app_templates": [item.slug for item in imported["app_templates"]],
        "prompt_templates": [item.slug for item in imported["prompt_templates"]],
        "blocks": [f"{item.slug}:{item.version}" for item in imported["blocks"]],
        "bundles": [item.slug for item in imported["bundles"]],
    }


@router.get("/packages/starters/", response=list[StarterTemplatePackageSchema])
def list_package_starters(request):
    return list_starter_template_packages()


@router.post("/packages/starters/{slug}/import/", response={200: dict, 400: dict})
def import_package_starter(
    request,
    slug: str,
    payload: StarterTemplatePackageImportSchema,
):
    try:
        imported = import_starter_template_package(
            slug=slug,
            user=request.auth,
            conflict_strategy=payload.conflict_strategy,
        )
    except FileNotFoundError as exc:
        raise HttpError(404, str(exc)) from exc
    except ValueError as exc:
        return 400, {"detail": str(exc)}
    return {
        "scaffolding_templates": [item.slug for item in imported["scaffolding_templates"]],
        "app_templates": [item.slug for item in imported["app_templates"]],
        "prompt_templates": [item.slug for item in imported["prompt_templates"]],
        "blocks": [f"{item.slug}:{item.version}" for item in imported["blocks"]],
        "bundles": [item.slug for item in imported["bundles"]],
    }


@router.get("/bundles/{slug}/", response=TemplateBundleSchema)
def get_bundle(request, slug: str):
    return get_object_or_404(visible_bundles_for(request.auth), slug=slug)


@router.put("/bundles/{slug}/", response={200: TemplateBundleSchema, 400: dict})
def update_bundle(request, slug: str):
    raise HttpError(
        403,
        "Manual bundle editing is disabled. Export or import template packages from existing assets instead.",
    )


@router.delete("/bundles/{slug}/")
def delete_bundle(request, slug: str):
    raise HttpError(
        403,
        "Manual bundle deletion is disabled. Export or import template packages from existing assets instead.",
    )


@router.get("/bundles/{slug}/export/")
def export_bundle(
    request,
    slug: str,
    fmt: str = Query("json", alias="format"),
):
    bundle = get_object_or_404(visible_bundles_for(request.auth), slug=slug)
    if fmt not in {"json", "yaml"}:
        raise HttpError(400, "format must be 'json' or 'yaml'")
    package = export_bundle_package(bundle)
    payload, content_type = dump_bundle_package(package, fmt)
    extension = "yaml" if fmt == "yaml" else "json"
    response = HttpResponse(payload, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{bundle.slug}.{extension}"'
    return response


@router.get("/bundles/{slug}/preview/", response=dict)
def preview_bundle(request, slug: str):
    """Resolve block refs to assembled prompt templates (no app requirement context)."""
    bundle = get_object_or_404(visible_bundles_for(request.auth), slug=slug)
    resolved = resolve_block_refs(list(bundle.block_refs or []), request.auth)
    from backend.generation.services.bundle_resolver import assemble_prompt_templates

    templates = assemble_prompt_templates(resolved)
    return {
        "slug": bundle.slug,
        "scaffolding_slug": bundle.scaffolding_slug,
        "block_count": len(resolved),
        "prompt_templates": templates,
    }
