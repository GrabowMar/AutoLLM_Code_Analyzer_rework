"""Experiment CRUD, matrix preview, launch, status, and export endpoints."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from backend.credentials.services.resolver import MissingApiKeyError
from backend.credentials.services.resolver import has_resolvable_key
from backend.generation.api.schema import ExperimentConditionCreateSchema
from backend.generation.api.schema import ExperimentConditionSchema
from backend.generation.api.schema import ExperimentCreateSchema
from backend.generation.api.schema import ExperimentSchema
from backend.generation.api.schema import ExperimentUpdateSchema
from backend.generation.api.schema import GenerationBatchSchema
from backend.generation.api.views._router import router
from backend.generation.models import AppRequirementTemplate
from backend.generation.models import Experiment
from backend.generation.models import ExperimentCondition
from backend.generation.models import GenerationProfile
from backend.generation.services.experiments import experiment_status
from backend.generation.services.experiments import export_experiment
from backend.generation.services.experiments import launch_experiment
from backend.generation.services.experiments import preview_experiment
from backend.generation.services.llm_params import validate_llm_params
from backend.llm_models.models import LLMModel


def _owned_experiment_or_404(user, experiment_id: str) -> Experiment:
    return get_object_or_404(Experiment, id=experiment_id, created_by=user)


def _draft_or_400(experiment: Experiment) -> None:
    if experiment.status != Experiment.Status.DRAFT:
        raise HttpError(400, "Only draft experiments can be edited. Archive and clone to make changes.")


def _validated(params: dict | None) -> dict:
    try:
        return validate_llm_params(params or {})
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


@router.get("/experiments/", response=list[ExperimentSchema])
def list_experiments(request):
    return Experiment.objects.filter(created_by=request.auth).prefetch_related("app_requirements", "conditions")


@router.post("/experiments/", response={200: ExperimentSchema, 400: dict})
def create_experiment(request, payload: ExperimentCreateSchema):
    if Experiment.objects.filter(created_by=request.auth, slug=payload.slug).exists():
        return 400, {"detail": f"You already have an experiment with slug {payload.slug!r}"}
    experiment = Experiment.objects.create(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        hypothesis=payload.hypothesis,
        repeats=payload.repeats,
        base_seed=payload.base_seed,
        continuation_limit=payload.continuation_limit,
        enable_repair=payload.enable_repair,
        llm_defaults=_validated(payload.llm_defaults.as_params() if payload.llm_defaults else {}),
        created_by=request.auth,
    )
    if payload.app_requirement_ids:
        apps = AppRequirementTemplate.objects.filter(id__in=payload.app_requirement_ids)
        experiment.app_requirements.set(apps)
    return experiment


@router.get("/experiments/{experiment_id}/", response=ExperimentSchema)
def get_experiment(request, experiment_id: str):
    return _owned_experiment_or_404(request.auth, experiment_id)


@router.patch("/experiments/{experiment_id}/", response={200: ExperimentSchema, 400: dict})
def update_experiment(request, experiment_id: str, payload: ExperimentUpdateSchema):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    _draft_or_400(experiment)

    data = payload.dict(exclude_unset=True)
    app_ids = data.pop("app_requirement_ids", None)
    if "llm_defaults" in data:
        data["llm_defaults"] = _validated(data["llm_defaults"])
    for field, value in data.items():
        setattr(experiment, field, value)
    experiment.save()
    if app_ids is not None:
        apps = AppRequirementTemplate.objects.filter(id__in=app_ids)
        experiment.app_requirements.set(apps)
    return experiment


@router.delete("/experiments/{experiment_id}/")
def delete_experiment(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    _draft_or_400(experiment)
    experiment.delete()
    return {"success": True}


@router.post("/experiments/{experiment_id}/archive/", response=ExperimentSchema)
def archive_experiment(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    experiment.status = Experiment.Status.ARCHIVED
    experiment.save(update_fields=["status", "updated_at"])
    return experiment


@router.get("/experiments/{experiment_id}/conditions/", response=list[ExperimentConditionSchema])
def list_conditions(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return experiment.conditions.select_related("model", "profile")


@router.post("/experiments/{experiment_id}/conditions/", response={200: ExperimentConditionSchema, 400: dict})
def create_condition(request, experiment_id: str, payload: ExperimentConditionCreateSchema):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    _draft_or_400(experiment)

    profile = get_object_or_404(GenerationProfile, id=payload.profile_id, is_archived=False)
    model = get_object_or_404(LLMModel, id=payload.model_id)
    if experiment.conditions.filter(profile=profile, model=model).exists():
        return 400, {"detail": "This experiment already has a condition for that profile + model."}
    return ExperimentCondition.objects.create(
        experiment=experiment,
        label=payload.label,
        profile=profile,
        model=model,
        param_overrides=_validated(payload.param_overrides),
    )


@router.delete("/experiments/{experiment_id}/conditions/{condition_id}/")
def delete_condition(request, experiment_id: str, condition_id: int):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    _draft_or_400(experiment)
    condition = get_object_or_404(ExperimentCondition, id=condition_id, experiment=experiment)
    condition.delete()
    return {"success": True}


@router.post("/experiments/{experiment_id}/preview/", response=dict)
def preview(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return preview_experiment(experiment)


@router.post("/experiments/{experiment_id}/launch/", response={200: GenerationBatchSchema, 400: dict})
def launch(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    if experiment.status == Experiment.Status.ARCHIVED:
        return 400, {"detail": "Cannot launch an archived experiment."}
    if not has_resolvable_key(request.auth):
        msg = "No OpenRouter API key is configured for your account."
        return 400, {
            "detail": msg,
            "remediation": MissingApiKeyError(msg).remediation,
            "code": "missing_api_key",
        }
    if not experiment.conditions.exists():
        return 400, {"detail": "Add at least one condition (model x bundle) before launching."}
    if not experiment.app_requirements.exists():
        return 400, {"detail": "Add at least one app requirement before launching."}
    return launch_experiment(experiment, request.auth)


@router.get("/experiments/{experiment_id}/status/", response=dict)
def status(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return experiment_status(experiment)


@router.get("/experiments/{experiment_id}/export/", response=dict)
def export(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return export_experiment(experiment)
