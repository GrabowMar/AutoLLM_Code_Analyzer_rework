"""Template bundle and content block catalog API."""

from __future__ import annotations

import jinja2
from django.db.models import Q
from django.shortcuts import get_object_or_404

from llm_lab.generation.api.schema import ContentBlockCreateSchema
from llm_lab.generation.api.schema import ContentBlockSchema
from llm_lab.generation.api.schema import TemplateBundleCreateSchema
from llm_lab.generation.api.schema import TemplateBundleSchema
from llm_lab.generation.api.views._router import router
from llm_lab.generation.models import ContentBlock
from llm_lab.generation.models import TemplateBundle
from llm_lab.generation.services.bundle_resolver import resolve_block_refs


def _validate_block_refs(refs: list[dict], user) -> list[str]:
    """Return list of error messages for unknown block refs."""
    errors: list[str] = []
    for ref in refs:
        slug = ref.get("slug")
        version = int(ref.get("version", 1))
        if not slug:
            errors.append("Block ref missing slug")
            continue
        qs = ContentBlock.objects.filter(slug=slug, version=version)
        if user and getattr(user, "is_authenticated", False):
            exists = qs.filter(Q(is_system=True) | Q(created_by=user)).exists()
        else:
            exists = qs.filter(is_system=True).exists()
        if not exists:
            errors.append(f"Unknown block: {slug} v{version}")
    return errors


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


@router.get("/blocks/", response=list[ContentBlockSchema])
def list_blocks(request):
    """List system blocks plus the current user's blocks."""
    return ContentBlock.objects.filter(
        Q(is_system=True) | Q(created_by=request.auth),
    ).order_by("block_type", "slug", "-version")


@router.post("/blocks/", response={200: ContentBlockSchema, 400: dict})
def create_block(request, payload: ContentBlockCreateSchema):
    """Create a user-owned content block."""
    err = _validate_jinja(payload.content)
    if err:
        return 400, {"detail": f"Invalid Jinja2: {err}"}
    if ContentBlock.objects.filter(slug=payload.slug, version=payload.version).exists():
        return 400, {"detail": f"Block {payload.slug} v{payload.version} already exists"}
    block = ContentBlock.objects.create(
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
    return block


@router.get("/blocks/{slug}/", response=ContentBlockSchema)
def get_block(request, slug: str, version: int = 1):
    return get_object_or_404(
        ContentBlock.objects.filter(Q(is_system=True) | Q(created_by=request.auth)),
        slug=slug,
        version=version,
    )


@router.get("/bundles/", response=list[TemplateBundleSchema])
def list_bundles(request):
    return TemplateBundle.objects.filter(
        Q(is_system=True) | Q(created_by=request.auth),
    ).order_by("-is_default", "name")


@router.post("/bundles/", response={200: TemplateBundleSchema, 400: dict})
def create_bundle(request, payload: TemplateBundleCreateSchema):
    ref_dicts = [
        {"type": r.type, "slug": r.slug, "version": r.version}
        for r in payload.block_refs
    ]
    errors = _validate_block_refs(ref_dicts, request.auth)
    if errors:
        return 400, {"detail": errors}
    if TemplateBundle.objects.filter(slug=payload.slug).exists():
        return 400, {"detail": f"Bundle slug already exists: {payload.slug}"}
    bundle = TemplateBundle.objects.create(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        scaffolding_slug=payload.scaffolding_slug,
        block_refs=ref_dicts,
        llm_config=payload.llm_config,
        is_system=False,
        is_default=False,
        created_by=request.auth,
    )
    return bundle


@router.get("/bundles/{slug}/", response=TemplateBundleSchema)
def get_bundle(request, slug: str):
    return get_object_or_404(
        TemplateBundle.objects.filter(Q(is_system=True) | Q(created_by=request.auth)),
        slug=slug,
    )


@router.get("/bundles/{slug}/preview/", response=dict)
def preview_bundle(request, slug: str):
    """Resolve block refs to assembled prompt templates (no app requirement context)."""
    bundle = get_object_or_404(
        TemplateBundle.objects.filter(Q(is_system=True) | Q(created_by=request.auth)),
        slug=slug,
    )
    resolved = resolve_block_refs(list(bundle.block_refs or []), request.auth)
    from llm_lab.generation.services.bundle_resolver import assemble_prompt_templates

    templates = assemble_prompt_templates(resolved)
    return {
        "slug": bundle.slug,
        "scaffolding_slug": bundle.scaffolding_slug,
        "block_count": len(resolved),
        "prompt_templates": templates,
    }
