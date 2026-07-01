"""Template CRUD endpoints (scaffolding, app requirement, prompt templates)."""

from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Query
from ninja.errors import HttpError

from backend.generation.api.schema import AppRequirementCreateSchema
from backend.generation.api.schema import AppRequirementTemplateSchema
from backend.generation.api.schema import PromptTemplateCreateSchema
from backend.generation.api.schema import PromptTemplateSchema
from backend.generation.api.schema import ScaffoldingTemplateCreateSchema
from backend.generation.api.schema import ScaffoldingTemplateSchema
from backend.generation.api.views._router import router
from backend.generation.models import AppRequirementTemplate
from backend.generation.models import PromptTemplate
from backend.generation.models import ScaffoldingTemplate


def _visible_templates(model, user):
    return model.objects.filter(Q(is_default=True) | Q(created_by=user))


def _mutable_template_or_403(model, user, *, slug: str):
    obj = get_object_or_404(_visible_templates(model, user), slug=slug)
    if obj.created_by_id == getattr(user, "id", None):
        return obj
    if getattr(user, "is_staff", False) and obj.is_default:
        return obj
    raise HttpError(403, "You can only modify your own templates.")


# -- Scaffolding Templates ---------------------------------------------


@router.get("/scaffolding-templates/", response=list[ScaffoldingTemplateSchema])
def list_scaffolding_templates(request):
    """List all scaffolding templates."""
    return _visible_templates(ScaffoldingTemplate, request.auth)


@router.post("/scaffolding-templates/", response=ScaffoldingTemplateSchema)
def create_scaffolding_template(request, payload: ScaffoldingTemplateCreateSchema):
    """Create a new scaffolding template."""
    return ScaffoldingTemplate.objects.create(
        **payload.dict(),
        created_by=request.auth,
    )


@router.get("/scaffolding-templates/{slug}/", response=ScaffoldingTemplateSchema)
def get_scaffolding_template(request, slug: str):
    return get_object_or_404(_visible_templates(ScaffoldingTemplate, request.auth), slug=slug)


@router.put("/scaffolding-templates/{slug}/", response=ScaffoldingTemplateSchema)
def update_scaffolding_template(
    request,
    slug: str,
    payload: ScaffoldingTemplateCreateSchema,
):
    template = _mutable_template_or_403(ScaffoldingTemplate, request.auth, slug=slug)
    for attr, value in payload.dict().items():
        setattr(template, attr, value)
    template.save()
    return template


@router.delete("/scaffolding-templates/{slug}/")
def delete_scaffolding_template(request, slug: str):
    template = _mutable_template_or_403(ScaffoldingTemplate, request.auth, slug=slug)
    template.delete()
    return {"success": True}


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


# -- Prompt Templates ---------------------------------------------------


@router.get("/prompt-templates/", response=list[PromptTemplateSchema])
def list_prompt_templates(request, stage: str = Query(""), role: str = Query("")):
    """List prompt templates with optional filtering."""
    qs = _visible_templates(PromptTemplate, request.auth)
    if stage:
        qs = qs.filter(stage=stage)
    if role:
        qs = qs.filter(role=role)
    return qs


@router.post("/prompt-templates/", response=PromptTemplateSchema)
def create_prompt_template(request, payload: PromptTemplateCreateSchema):
    return PromptTemplate.objects.create(**payload.dict(), created_by=request.auth)


@router.get("/prompt-templates/{slug}/", response=PromptTemplateSchema)
def get_prompt_template(request, slug: str):
    return get_object_or_404(_visible_templates(PromptTemplate, request.auth), slug=slug)


@router.put("/prompt-templates/{slug}/", response=PromptTemplateSchema)
def update_prompt_template(request, slug: str, payload: PromptTemplateCreateSchema):
    template = _mutable_template_or_403(PromptTemplate, request.auth, slug=slug)
    for attr, value in payload.dict().items():
        setattr(template, attr, value)
    template.save()
    return template


@router.delete("/prompt-templates/{slug}/")
def delete_prompt_template(request, slug: str):
    template = _mutable_template_or_403(PromptTemplate, request.auth, slug=slug)
    template.delete()
    return {"success": True}
