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


# -- Stacks (static scaffolding skeletons from runtime/scaffolding/manifest.json) --


@router.get("/stacks/", response=list[StackSchema])
def list_stacks(request):
    """List the stack skeletons available for scaffolding-mode jobs.

    Stacks are code shipped with the app (``runtime/scaffolding/<dir>``), not
    database rows — there is nothing per-user or per-install to create/edit
    here, unlike the (removed) ``ScaffoldingTemplate`` model this replaces.
    """
    manifest = load_manifest()
    return [
        {"slug": slug, "has_frontend": bool(config.get("has_frontend")), "aliases": config.get("aliases", [])}
        for slug, config in manifest.get("stacks", {}).items()
    ]


# -- App Requirement Templates ------------------------------------------


@router.get("/app-templates/", response=list[AppRequirementTemplateSchema])
def list_app_templates(request):
    """List all app requirement templates."""
    return _visible_templates(AppRequirementTemplate, request.auth)


@router.post("/app-templates/", response=AppRequirementTemplateSchema)
def create_app_template(request, payload: AppRequirementCreateSchema):
    return AppRequirementTemplate.objects.create(
        **payload.dict(),
        created_by=request.auth,
    )


@router.get("/app-templates/{slug}/", response=AppRequirementTemplateSchema)
def get_app_template(request, slug: str):
    return get_object_or_404(_visible_templates(AppRequirementTemplate, request.auth), slug=slug)


@router.put("/app-templates/{slug}/", response=AppRequirementTemplateSchema)
def update_app_template(request, slug: str, payload: AppRequirementCreateSchema):
    template = _mutable_template_or_403(AppRequirementTemplate, request.auth, slug=slug)
    for attr, value in payload.dict().items():
        setattr(template, attr, value)
    template.save()
    return template


@router.delete("/app-templates/{slug}/")
def delete_app_template(request, slug: str):
    template = _mutable_template_or_403(AppRequirementTemplate, request.auth, slug=slug)
    template.delete()
    return {"success": True}
