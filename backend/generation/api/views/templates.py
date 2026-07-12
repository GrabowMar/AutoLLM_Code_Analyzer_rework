"""Template CRUD endpoints (stacks, app requirement templates)."""

from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from backend.generation.api.schema import AppRequirementCreateSchema
from backend.generation.api.schema import AppRequirementTemplateSchema
from backend.generation.api.schema import StackSchema
from backend.generation.api.views._router import router
from backend.generation.models import AppRequirementTemplate
from backend.runtime.services.scaffolding import load_manifest


def _visible_templates(model, user):
    return model.objects.filter(Q(is_default=True) | Q(created_by=user))


def _mutable_template_or_403(model, user, *, slug: str):
    obj = get_object_or_404(_visible_templates(model, user), slug=slug)
    if obj.created_by_id == getattr(user, "id", None):
        return obj
    if getattr(user, "is_staff", False) and obj.is_default:
        return obj
    raise HttpError(403, "You can only modify your own templates.")


# -- Stacks (DB rows seeded from runtime/scaffolding/, latest version per slug) --


def _latest_stacks(user=None):
    """Latest non-archived Stack per slug, builtin plus (later) user-visible ones."""
    from backend.runtime.models import Stack

    rows = Stack.objects.filter(is_archived=False).order_by("slug", "-version")
    latest: dict[str, Stack] = {}
    for row in rows:
        latest.setdefault(row.slug, row)
    return list(latest.values())


def _stack_payload(row, *, include_files: bool = False) -> dict:
    payload = {
        "slug": row.slug,
        "version": row.version,
        "name": row.name or row.slug,
        "description": row.description,
        "has_frontend": row.has_frontend,
        "aliases": row.aliases or [],
        "is_builtin": row.is_builtin,
        "default_port": row.default_port,
        "patch_profile": row.patch_profile,
        "file_count": len(row.files or {}),
    }
    if include_files:
        payload["files"] = row.files or {}
    return payload


@router.get("/stacks/", response=list[StackSchema])
def list_stacks(request):
    """List the stack skeletons available for scaffolding-mode jobs."""
    rows = _latest_stacks(request.auth)
    if rows:
        return [_stack_payload(row) for row in rows]
    # First boot before the post_migrate seeder ran: fall back to the manifest.
    manifest = load_manifest()
    return [
        {"slug": slug, "has_frontend": bool(config.get("has_frontend")), "aliases": config.get("aliases", [])}
        for slug, config in manifest.get("stacks", {}).items()
    ]


@router.get("/stacks/{slug}/", response={200: dict, 404: dict})
def get_stack(request, slug: str):
    """Latest version of a stack, including its full skeleton file map."""
    from backend.runtime.services.scaffolding import get_stack_row

    row = get_stack_row(slug)
    if row is None:
        return 404, {"detail": f"Unknown stack slug: {slug}"}
    return 200, _stack_payload(row, include_files=True)


# -- App Requirement Templates ------------------------------------------


@router.get("/app-specs/", response=list[AppRequirementTemplateSchema])
def list_app_templates(request):
    """List all app requirement templates."""
    return _visible_templates(AppRequirementTemplate, request.auth)


@router.post("/app-specs/", response=AppRequirementTemplateSchema)
def create_app_template(request, payload: AppRequirementCreateSchema):
    return AppRequirementTemplate.objects.create(
        **payload.dict(),
        created_by=request.auth,
    )


@router.get("/app-specs/{slug}/", response=AppRequirementTemplateSchema)
def get_app_template(request, slug: str):
    return get_object_or_404(_visible_templates(AppRequirementTemplate, request.auth), slug=slug)


@router.put("/app-specs/{slug}/", response=AppRequirementTemplateSchema)
def update_app_template(request, slug: str, payload: AppRequirementCreateSchema):
    template = _mutable_template_or_403(AppRequirementTemplate, request.auth, slug=slug)
    for attr, value in payload.dict().items():
        setattr(template, attr, value)
    template.save()
    return template


@router.delete("/app-specs/{slug}/")
def delete_app_template(request, slug: str):
    template = _mutable_template_or_403(AppRequirementTemplate, request.auth, slug=slug)
    template.delete()
    return {"success": True}
