"""Experiment CRUD, matrix preview, launch, status, and export endpoints.

Rules (services/experiments.py has the layering):

* The stack, sampling preset and budget preset are experiment-wide. With a
  stack set, every condition profile must target it (400 otherwise); a null
  stack keeps the legacy behaviour (each condition's profile stack).
* A condition is a model plus a harness preset (agent; the profile is
  optional there) or a profile (scaffolding, required). Conditions that would
  duplicate an existing one are refused.
"""

from __future__ import annotations

from django.db import IntegrityError
from django.db import transaction
from django.db.models import Q
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
from backend.generation.models import GenerationJob
from backend.generation.models import GenerationProfile
from backend.generation.services.agent.config import validate_agent_config
from backend.generation.services.agent.harnesses import default_harness_name
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.experiments import ExperimentError
from backend.generation.services.experiments import check_design
from backend.generation.services.experiments import condition_label
from backend.generation.services.experiments import duplicate_condition
from backend.generation.services.experiments import experiment_preset
from backend.generation.services.experiments import experiment_status
from backend.generation.services.experiments import export_experiment
from backend.generation.services.experiments import launch_experiment
from backend.generation.services.experiments import preview_experiment
from backend.generation.services.experiments import resolve_stack
from backend.generation.services.experiments import stack_mismatch
from backend.generation.services.llm_params import validate_llm_params
from backend.generation.services.presets import preset_config_for_use
from backend.llm_models.models import LLMModel

# PATCH fields an explicit null clears; for the others it is ignored.
_NULLABLE_FIELDS = {"base_seed", "stack_slug", "sampling_preset", "budget_preset"}


def _owned_experiment_or_404(user, experiment_id: str) -> Experiment:
    return get_object_or_404(
        Experiment.objects.select_related("sampling_preset", "budget_preset"),
        id=experiment_id,
        created_by=user,
    )


def _draft_or_400(experiment: Experiment) -> None:
    if experiment.status != Experiment.Status.DRAFT:
        raise HttpError(400, "Only draft experiments can be edited. Archive and clone to make changes.")


def _validated(params: dict | None) -> dict:
    try:
        return validate_llm_params(params or {})
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


def _validated_agent(config: dict | None) -> dict:
    try:
        return validate_agent_config(config or {})
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


def _http(call, *args):
    """Run a service call, turning ``ExperimentError`` into the matching HTTP error."""
    try:
        return call(*args)
    except ExperimentError as exc:
        raise HttpError(exc.status, exc.detail) from exc


def _stack(raw: str | None, user) -> str:
    """Canonical stack slug ("" = legacy per-profile stacks); 404 unknown, 403 unusable."""
    raw = (raw or "").strip()
    return _http(resolve_stack, raw, user) if raw else ""


@router.get("/experiments/", response=list[ExperimentSchema])
def list_experiments(request):
    return (
        Experiment.objects.filter(created_by=request.auth)
        .select_related("sampling_preset", "budget_preset")
        .prefetch_related("app_requirements", "conditions")
    )


@router.post("/experiments/", response={200: ExperimentSchema, 400: dict})
def create_experiment(request, payload: ExperimentCreateSchema):
    if Experiment.objects.filter(created_by=request.auth, slug=payload.slug).exists():
        return 400, {"detail": f"You already have an experiment with slug {payload.slug!r}"}
    # Everything is validated before the first write (a 4xx must not leave a row).
    experiment = Experiment(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        hypothesis=payload.hypothesis,
        repeats=payload.repeats,
        base_seed=payload.base_seed,
        mode=payload.mode,
        stack_slug=_stack(payload.stack_slug, request.auth),
        sampling_preset=_http(experiment_preset, request.auth, payload.sampling_preset_id, "sampling"),
        budget_preset=_http(experiment_preset, request.auth, payload.budget_preset_id, "budget"),
        agent_config=_validated_agent(payload.agent_config.as_config() if payload.agent_config else {}),
        continuation_limit=payload.continuation_limit,
        enable_repair=payload.enable_repair,
        llm_defaults=_validated(payload.llm_defaults.as_params() if payload.llm_defaults else {}),
        created_by=request.auth,
    )
    _http(check_design, experiment)
    experiment.save()
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
    if "agent_config" in data:
        data["agent_config"] = _validated_agent(data["agent_config"])
    if "stack_slug" in data:
        data["stack_slug"] = _stack(data["stack_slug"], request.auth)
    for kind in ("sampling", "budget"):
        if f"{kind}_preset_id" in data:
            data[f"{kind}_preset"] = _http(experiment_preset, request.auth, data.pop(f"{kind}_preset_id"), kind)
    for field, value in data.items():
        if value is None and field not in _NULLABLE_FIELDS:
            continue
        setattr(experiment, field, value)
    # The final state must still agree with the conditions (stack, mode).
    _http(check_design, experiment)
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
    return experiment.conditions.select_related("model", "profile", "harness_preset")


def _condition_profile(experiment: Experiment, profile_id: int | None, user) -> GenerationProfile | None:
    """The condition's profile (required in scaffolding mode) on a stack the experiment can run."""
    if profile_id is None:
        if experiment.mode != GenerationJob.Mode.AGENT:
            raise HttpError(400, "Scaffolding conditions need a profile (profile_id).")
        return None
    profile = get_object_or_404(
        GenerationProfile.objects.filter(Q(is_system=True) | Q(created_by=user)),
        id=profile_id,
        is_archived=False,
    )
    if experiment.stack_slug:
        if problem := stack_mismatch(experiment.stack_slug, profile):
            raise HttpError(400, problem)
    else:
        # Legacy (no experiment stack): the condition runs on its profile's stack.
        _stack(profile.scaffolding_slug or "", user)
    return profile


def _condition_harness(experiment: Experiment, payload: ExperimentConditionCreateSchema, user):
    """(harness name to store, harness preset) of a new condition: a preset pins its harness name."""
    harness_name = payload.harness.strip()
    if payload.harness_preset_id is None and not harness_name:
        return "", None
    if experiment.mode != GenerationJob.Mode.AGENT:
        raise HttpError(400, "A harness only applies to agent-mode experiments.")
    if payload.harness_preset_id is None:
        try:
            get_harness(harness_name)
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        return harness_name, None
    preset = _http(experiment_preset, user, payload.harness_preset_id, "harness")
    preset_harness = preset_config_for_use(preset, "harness")["harness"]
    if harness_name and harness_name != preset_harness:
        raise HttpError(
            400,
            f"harness {harness_name!r} does not match harness preset {preset.slug} ({preset_harness!r}); "
            "send only harness_preset_id",
        )
    return preset_harness, preset


@router.post("/experiments/{experiment_id}/conditions/", response={200: ExperimentConditionSchema, 400: dict})
def create_condition(request, experiment_id: str, payload: ExperimentConditionCreateSchema):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    _draft_or_400(experiment)

    model = get_object_or_404(LLMModel, id=payload.model_id)
    profile = _condition_profile(experiment, payload.profile_id, request.auth)
    harness_name, preset = _condition_harness(experiment, payload, request.auth)
    if experiment.mode == GenerationJob.Mode.AGENT:
        # A blank harness runs the experiment's default; check that one too.
        effective = harness_name or (experiment.agent_config or {}).get("harness") or default_harness_name()
        try:
            harness = get_harness(effective)
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        if harness.requires_tool_calling and not model.supports_function_calling:
            raise HttpError(400, f"{harness.label} needs a tool-calling model; {model.model_id} has none.")

    condition = ExperimentCondition(
        experiment=experiment,
        label=payload.label,
        profile=profile,
        model=model,
        harness=harness_name,
        harness_preset=preset,
        param_overrides=_validated(payload.param_overrides),
    )
    same_row = experiment.conditions.filter(
        model=model,
        profile=profile,
        harness=harness_name,
        harness_preset=preset,
    )
    if same_row.exists():
        return 400, {
            "detail": "This experiment already has a condition for that model + profile + harness preset.",
            "code": "duplicate_condition",
        }
    if (twin := duplicate_condition(experiment, condition)) is not None:
        return 400, {
            "detail": f"Condition #{twin.id} ({condition_label(twin)}) already runs this exact configuration.",
            "code": "duplicate_condition",
        }
    try:
        with transaction.atomic():
            condition.save()
    except IntegrityError:  # a concurrent create of the same condition
        return 400, {"detail": "This condition already exists.", "code": "duplicate_condition"}
    return condition


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
        return 400, {"detail": "Add at least one condition before launching."}
    if not experiment.app_requirements.exists():
        return 400, {"detail": "Add at least one app spec before launching."}
    try:
        return launch_experiment(experiment, request.auth)
    except ExperimentError as exc:
        # Raised before any row is written.
        return exc.status, exc.payload()


@router.get("/experiments/{experiment_id}/status/", response=dict)
def status(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return experiment_status(experiment)


@router.get("/experiments/{experiment_id}/export/", response=dict)
def export(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return export_experiment(experiment)
