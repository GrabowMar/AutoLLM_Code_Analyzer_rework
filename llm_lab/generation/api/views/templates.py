"""Template CRUD endpoints (scaffolding, app requirement, prompt templates)."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from ninja import Query

from llm_lab.generation.api.schema import AppRequirementCreateSchema
from llm_lab.generation.api.schema import AppRequirementTemplateSchema
from llm_lab.generation.api.schema import PromptTemplateCreateSchema
from llm_lab.generation.api.schema import PromptTemplateSchema
from llm_lab.generation.api.schema import ScaffoldingTemplateCreateSchema
from llm_lab.generation.api.schema import ScaffoldingTemplateSchema
from llm_lab.generation.api.views._router import router
from llm_lab.generation.models import AppRequirementTemplate
from llm_lab.generation.models import PromptTemplate
from llm_lab.generation.models import ScaffoldingTemplate

# -- Scaffolding Templates ---------------------------------------------


@router.get("/scaffolding-templates/", response=list[ScaffoldingTemplateSchema])
def list_scaffolding_templates(request):
    """List all scaffolding templates."""
    return ScaffoldingTemplate.objects.all()


@router.post("/scaffolding-templates/", response=ScaffoldingTemplateSchema)
def create_scaffolding_template(request, payload: ScaffoldingTemplateCreateSchema):
    """Create a new scaffolding template."""
    return ScaffoldingTemplate.objects.create(
        **payload.dict(),
        created_by=request.auth,
    )


@router.get("/scaffolding-templates/{slug}/", response=ScaffoldingTemplateSchema)
def get_scaffolding_template(request, slug: str):
    return get_object_or_404(ScaffoldingTemplate, slug=slug)


@router.put("/scaffolding-templates/{slug}/", response=ScaffoldingTemplateSchema)
def update_scaffolding_template(
    request,
    slug: str,
    payload: ScaffoldingTemplateCreateSchema,
):
    template = get_object_or_404(ScaffoldingTemplate, slug=slug)
    for attr, value in payload.dict().items():
        setattr(template, attr, value)
    template.save()
    return template


@router.delete("/scaffolding-templates/{slug}/")
def delete_scaffolding_template(request, slug: str):
    template = get_object_or_404(ScaffoldingTemplate, slug=slug)
    template.delete()
    return {"success": True}


# -- App Requirement Templates ------------------------------------------


@router.get("/app-templates/", response=list[AppRequirementTemplateSchema])
def list_app_templates(request):
    """List all app requirement templates."""
    return AppRequirementTemplate.objects.all()


@router.post("/app-templates/", response=AppRequirementTemplateSchema)
def create_app_template(request, payload: AppRequirementCreateSchema):
    return AppRequirementTemplate.objects.create(
        **payload.dict(),
        created_by=request.auth,
    )


@router.get("/app-templates/{slug}/", response=AppRequirementTemplateSchema)
def get_app_template(request, slug: str):
    return get_object_or_404(AppRequirementTemplate, slug=slug)


@router.put("/app-templates/{slug}/", response=AppRequirementTemplateSchema)
def update_app_template(request, slug: str, payload: AppRequirementCreateSchema):
    template = get_object_or_404(AppRequirementTemplate, slug=slug)
    for attr, value in payload.dict().items():
        setattr(template, attr, value)
    template.save()
    return template


@router.delete("/app-templates/{slug}/")
def delete_app_template(request, slug: str):
    template = get_object_or_404(AppRequirementTemplate, slug=slug)
    template.delete()
    return {"success": True}


# -- Prompt Templates ---------------------------------------------------


@router.get("/prompt-templates/", response=list[PromptTemplateSchema])
def list_prompt_templates(request, stage: str = Query(""), role: str = Query("")):
    """List prompt templates with optional filtering."""
    qs = PromptTemplate.objects.all()
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
    return get_object_or_404(PromptTemplate, slug=slug)


@router.put("/prompt-templates/{slug}/", response=PromptTemplateSchema)
def update_prompt_template(request, slug: str, payload: PromptTemplateCreateSchema):
    template = get_object_or_404(PromptTemplate, slug=slug)
    for attr, value in payload.dict().items():
        setattr(template, attr, value)
    template.save()
    return template


@router.delete("/prompt-templates/{slug}/")
def delete_prompt_template(request, slug: str):
    template = get_object_or_404(PromptTemplate, slug=slug)
    template.delete()
    return {"success": True}
