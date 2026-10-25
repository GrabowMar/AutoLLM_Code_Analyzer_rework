"""Experiments API v2 (spec §F.1): design, launch and lifecycle, read models, schedules and post-runs.

Every route is owner-scoped through :func:`_owned_experiment_or_404`, and
every child object in a path (condition, schedule) is looked up scoped to its
experiment. Models resolve through ``LLMModel.objects.visible_to(user)``.

Rules (``services/experiments.py`` has the layering):

* The stack, sampling preset and budget preset are experiment-wide. With a
  stack set, every condition profile must target it; a null stack keeps the
  legacy behaviour (each condition's profile stack).
* A condition is a model plus a harness preset (agent; the profile is
  optional there) or a profile (scaffolding, required). Conditions that would
  duplicate an existing one are refused (single create) or skipped (sets).
* Static paths (``/experiments/preview/``, ``/cron-preview/``) are registered
  before the ``{experiment_id}`` routes so they can never be shadowed.

Requests run inside ``ATOMIC_REQUESTS``. The create-and-launch route commits
the draft in its own savepoint before launching in a second one, so a failed
launch rolls back only the launch and the draft survives (400 with
``experiment_id``). Validation runs before the first write, so other 4xx
responses leave nothing behind.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from django.db import transaction
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError
from ninja.responses import Status
from pydantic import ValidationError

from backend.generation.api.schema import BatchSummarySchema
from backend.generation.api.schema import ExperimentConditionCreateSchema
from backend.generation.api.schemas_experiments import CancelBody
from backend.generation.api.schemas_experiments import CancelResult
from backend.generation.api.schemas_experiments import CellsPayload
from backend.generation.api.schemas_experiments import CloneBody
from backend.generation.api.schemas_experiments import ConditionBulkResult
from backend.generation.api.schemas_experiments import ConditionSetSpec
from backend.generation.api.schemas_experiments import CronPreviewOut
from backend.generation.api.schemas_experiments import ErrorResponseSchema
from backend.generation.api.schemas_experiments import ExperimentConditionV2
from backend.generation.api.schemas_experiments import ExperimentCreateV2
from backend.generation.api.schemas_experiments import ExperimentDetail
from backend.generation.api.schemas_experiments import ExperimentPreviewV2
from backend.generation.api.schemas_experiments import ExperimentResults
from backend.generation.api.schemas_experiments import ExperimentState
from backend.generation.api.schemas_experiments import ExperimentStatusV2Schema
from backend.generation.api.schemas_experiments import ExperimentUpdateV2
from backend.generation.api.schemas_experiments import LaunchBody
from backend.generation.api.schemas_experiments import LaunchResult
from backend.generation.api.schemas_experiments import PaginatedExperiments
from backend.generation.api.schemas_experiments import PostRunConfigOut
from backend.generation.api.schemas_experiments import PostRunOut
from backend.generation.api.schemas_experiments import PostRunSpec
from backend.generation.api.schemas_experiments import RequeueLegacyResult
from backend.generation.api.schemas_experiments import ResumeBody
from backend.generation.api.schemas_experiments import ScheduleOut
from backend.generation.api.schemas_experiments import ScheduleRunNowResult
from backend.generation.api.schemas_experiments import ScheduleSpec
from backend.generation.api.schemas_experiments import ScheduleUpdateSchema
from backend.generation.api.schemas_experiments import SuccessSchema
from backend.generation.api.views._router import router
from backend.generation.models import Experiment
from backend.generation.models import ExperimentCondition
from backend.generation.models import ExperimentSchedule
from backend.generation.models import GenerationJob
from backend.generation.services import experiment_automation as automation
from backend.generation.services import experiments as svc
from backend.generation.services.batches import batch_summaries
from backend.generation.services.experiment_results import ResultFilters
from backend.generation.services.experiment_results import compute as compute_results
from backend.generation.services.experiments import ExperimentError

_ERRORS = {400: ErrorResponseSchema}


def _owned_experiment_or_404(user, experiment_id: str) -> Experiment:
    try:
        uuid.UUID(str(experiment_id))
    except ValueError as exc:  # a malformed id is a missing experiment, not a 500
        raise Http404 from exc
    return get_object_or_404(
        Experiment.objects.select_related(
            "sampling_preset",
            "budget_preset",
            "cloned_from",
            "created_by_schedule",
            "created_by",
        ),
        id=experiment_id,
        created_by=user,
    )


def _fail(exc: ExperimentError, **extra: Any):
    """A 400 ``{detail, code, errors}`` response; other statuses become ``HttpError``."""
    if exc.status == 400:
        return Status(400, {**exc.payload(), **extra})
    raise HttpError(exc.status, exc.detail) from exc


def _call(fn, *args, **kwargs):
    """Run a service call; ``ExperimentError`` becomes the matching response."""
    try:
        return fn(*args, **kwargs)
    except ExperimentError as exc:
        return _fail(exc)


def _optional_body(request, schema):
    """An optional JSON body (``{}`` or none at all means the defaults); 422 on invalid content."""
    raw = request.body or b""
    if request.content_type != "application/json" or not raw.strip():
        return schema()
    try:
        return schema.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise HttpError(422, f"Invalid request body: {exc}") from exc


def _detail(experiment: Experiment) -> dict:
    experiment.refresh_from_db()
    return svc.experiment_detail(experiment)


# ── collection (static paths first) ───────────────────────────────────


@router.get("/experiments/", response={200: PaginatedExperiments, **_ERRORS})
def list_experiments(
    request,
    page: int = 1,
    per_page: int = 25,
    status: str | None = None,
    mode: str | None = None,
    search: str | None = None,
    endpoint: str | None = None,
    ordering: str = "-updated_at",
):
    return _call(
        svc.list_experiments,
        request.auth,
        page=page,
        per_page=per_page,
        status=status,
        mode=mode,
        search=search,
        endpoint=endpoint,
        ordering=ordering,
    )


@router.post("/experiments/", response={200: ExperimentDetail, 201: LaunchResult, **_ERRORS})
def create_experiment(request, payload: ExperimentCreateV2, launch: bool = False):
    """Create the draft with its conditions in one call; ``?launch=true`` launches it too."""
    user = request.auth
    try:
        with transaction.atomic():
            experiment = svc.create_experiment(user, payload.dict(exclude_unset=True))
    except ExperimentError as exc:
        return _fail(exc)
    if not launch:
        return Status(200, _detail(experiment))
    try:
        batch = svc.launch_experiment(experiment, user)
    except ExperimentError as exc:
        # The draft stays (its savepoint committed); only the launch rolled back.
        return _fail(exc, experiment_id=str(experiment.id))
    return Status(201, _launch_result(experiment, batch))


@router.post("/experiments/preview/", response={200: ExperimentPreviewV2, **_ERRORS})
def preview_design(request, payload: ExperimentCreateV2):
    """Stateless preview of an unsaved design (the composer's plan rail); writes nothing."""
    return _call(svc.preview_design, request.auth, payload.dict(exclude_unset=True))


@router.get("/cron-preview/", response=CronPreviewOut)
def cron_preview(request, cron: str, tz: str = "UTC"):
    return automation.cron_preview(cron, tz)


# ── one experiment ────────────────────────────────────────────────────


@router.get("/experiments/{experiment_id}/", response=ExperimentDetail)
def get_experiment(request, experiment_id: str):
    return svc.experiment_detail(_owned_experiment_or_404(request.auth, experiment_id))


@router.patch("/experiments/{experiment_id}/", response={200: ExperimentDetail, **_ERRORS})
def update_experiment(request, experiment_id: str, payload: ExperimentUpdateV2):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    try:
        svc.update_experiment(experiment, request.auth, payload.dict(exclude_unset=True))
    except ExperimentError as exc:
        return _fail(exc)
    return Status(200, _detail(experiment))


@router.delete("/experiments/{experiment_id}/", response={200: SuccessSchema, **_ERRORS})
def delete_experiment(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    try:
        svc.delete_experiment(experiment)
    except ExperimentError as exc:
        return _fail(exc)
    return Status(200, {"success": True})


# ── conditions ────────────────────────────────────────────────────────


@router.get("/experiments/{experiment_id}/conditions/", response=list[ExperimentConditionV2])
def list_conditions(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return experiment.conditions.select_related("model__endpoint", "profile", "harness_preset").order_by("id")


@router.put("/experiments/{experiment_id}/conditions/", response={200: list[ExperimentConditionV2], **_ERRORS})
def replace_conditions(request, experiment_id: str, payload: ConditionSetSpec):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return _call(svc.replace_conditions, experiment, payload.dict(), request.auth)


@router.post("/experiments/{experiment_id}/conditions/bulk/", response={200: ConditionBulkResult, **_ERRORS})
def add_conditions(request, experiment_id: str, payload: ConditionSetSpec):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    try:
        result = svc.add_conditions(experiment, payload.dict(), request.auth)
    except ExperimentError as exc:
        return _fail(exc)
    created = ExperimentCondition.objects.filter(id__in=[c.id for c in result.conditions]).select_related(
        "model__endpoint",
        "profile",
        "harness_preset",
    )
    return Status(200, {"created": list(created.order_by("id")), "skipped": result.skipped})


@router.post("/experiments/{experiment_id}/conditions/", response={200: ExperimentConditionV2, **_ERRORS})
def create_condition(request, experiment_id: str, payload: ExperimentConditionCreateSchema):
    """One condition. A harness the model cannot drive is not refused here: it blocks the launch."""
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    spec = {
        "cross": {"model_ids": []},
        "extra": [
            {
                "model_id": payload.model_id,
                "harness_preset_id": payload.harness_preset_id,
                "harness": payload.harness or None,
                "profile_id": payload.profile_id,
                "label": payload.label,
                "param_overrides": payload.param_overrides or {},
            },
        ],
    }
    try:
        result = svc.add_conditions(experiment, spec, request.auth)
    except ExperimentError as exc:
        return _fail(exc)
    if not result.conditions:
        skipped = result.skipped[0] if result.skipped else {"reason": "duplicate", "detail": ""}
        code = "duplicate_condition" if skipped["reason"] == "duplicate" else "stack_mismatch"
        return Status(400, {"detail": skipped["detail"] or "This condition already exists.", "code": code})
    condition = result.conditions[0]
    return Status(
        200,
        ExperimentCondition.objects.select_related("model__endpoint", "profile", "harness_preset").get(
            id=condition.id,
        ),
    )


@router.delete("/experiments/{experiment_id}/conditions/{condition_id}/", response={200: SuccessSchema, **_ERRORS})
def delete_condition(request, experiment_id: str, condition_id: int):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    if experiment.status != Experiment.Status.DRAFT:
        return Status(400, {"detail": "Only draft experiments can be edited.", "code": "not_draft"})
    condition = get_object_or_404(ExperimentCondition, id=condition_id, experiment=experiment)
    with transaction.atomic():
        if experiment.baseline_condition_id == condition.id:
            experiment.baseline_condition = experiment.conditions.exclude(id=condition.id).order_by("id").first()
            experiment.save(update_fields=["baseline_condition", "updated_at"])
        condition.delete()
    return Status(200, {"success": True})


# ── preview, launch, lifecycle ────────────────────────────────────────


@router.get("/experiments/{experiment_id}/preview/", response=ExperimentPreviewV2)
def preview(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return svc.preview_experiment_v2(experiment, request.auth)


@router.post("/experiments/{experiment_id}/preview/", response=ExperimentPreviewV2)
def preview_post(request, experiment_id: str):
    """Alias of the GET (kept for older clients)."""
    return preview(request, experiment_id)


def _launch_result(experiment: Experiment, batch) -> dict:
    outcome = svc.launch_outcome(batch)
    return {
        "experiment": _detail(experiment),
        "batch": {"id": batch.id, "kind": batch.kind, "created_at": batch.created_at, "total_jobs": batch.total_jobs},
        **outcome,
    }


@router.post("/experiments/{experiment_id}/launch/", response={200: LaunchResult, **_ERRORS})
def launch(request, experiment_id: str):
    """Body ``LaunchBody`` (optional)."""
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    body = _optional_body(request, LaunchBody)
    try:
        batch = svc.launch_experiment(experiment, request.auth, mode=body.mode, condition_ids=body.condition_ids)
    except ExperimentError as exc:
        return _fail(exc)
    return Status(200, _launch_result(experiment, batch))


@router.post("/experiments/{experiment_id}/pause/", response={200: ExperimentState, **_ERRORS})
def pause(request, experiment_id: str):
    return _call(svc.pause_experiment, _owned_experiment_or_404(request.auth, experiment_id))


@router.post("/experiments/{experiment_id}/resume/", response={200: ExperimentState, **_ERRORS})
def resume(request, experiment_id: str):
    """Body ``ResumeBody`` (optional)."""
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    accept = _optional_body(request, ResumeBody).accept_digest_drift
    return _call(svc.resume_experiment, experiment, request.auth, accept_digest_drift=accept)


@router.post("/experiments/{experiment_id}/cancel/", response={200: CancelResult, **_ERRORS})
def cancel(request, experiment_id: str):
    """Body ``CancelBody`` (optional)."""
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return _call(svc.cancel_experiment, experiment, _optional_body(request, CancelBody).scope)


@router.post("/experiments/{experiment_id}/archive/", response={200: ExperimentDetail, **_ERRORS})
def archive(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    try:
        svc.archive_experiment(experiment)
    except ExperimentError as exc:
        return _fail(exc)
    return Status(200, _detail(experiment))


@router.post("/experiments/{experiment_id}/clone/", response={200: ExperimentDetail, 201: LaunchResult, **_ERRORS})
def clone(request, experiment_id: str):
    """Body ``CloneBody`` (optional)."""
    source = _owned_experiment_or_404(request.auth, experiment_id)
    body = _optional_body(request, CloneBody)
    try:
        with transaction.atomic():
            copy = svc.clone_experiment(
                source,
                request.auth,
                name=body.name,
                slug=body.slug,
                include_schedules=body.include_schedules,
                include_post_run=body.include_post_run,
                overrides=body.overrides,
            )
    except ExperimentError as exc:
        return _fail(exc)
    if not body.launch:
        return Status(200, _detail(copy))
    try:
        batch = svc.launch_experiment(copy, request.auth)
    except ExperimentError as exc:
        return _fail(exc, experiment_id=str(copy.id))
    return Status(201, _launch_result(copy, batch))


@router.post("/experiments/{experiment_id}/jobs/requeue-legacy/", response=RequeueLegacyResult)
def requeue_legacy(request, experiment_id: str):
    return svc.requeue_legacy_jobs(_owned_experiment_or_404(request.auth, experiment_id))


# ── read models ───────────────────────────────────────────────────────


@router.get("/experiments/{experiment_id}/status/", response=ExperimentStatusV2Schema)
def status(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return svc.experiment_status_v2(experiment, request.auth)


@router.get("/experiments/{experiment_id}/cells/", response=CellsPayload)
def cells(request, experiment_id: str):
    return svc.cells_payload(_owned_experiment_or_404(request.auth, experiment_id))


@router.get("/experiments/{experiment_id}/results/", response={200: ExperimentResults, **_ERRORS})
def results(
    request,
    experiment_id: str,
    baseline: int | None = None,
    exclude_invalid: bool = False,
    exclude_truncated: bool = True,
    common_cells: bool = True,
    fingerprint: str = "all",
    metric: str = "pass_rate",
):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    filters = ResultFilters(
        metric=metric,
        exclude_invalid=exclude_invalid,
        exclude_truncated=exclude_truncated,
        common_cells=common_cells,
        fingerprint=fingerprint,
    )
    try:
        return Status(200, compute_results(experiment, baseline, filters, request.auth))
    except ValueError as exc:
        return Status(400, {"detail": str(exc), "code": "invalid_results_query"})


@router.get("/experiments/{experiment_id}/batches/", response=list[BatchSummarySchema])
def batches(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return batch_summaries(experiment.batches.order_by("-created_at"))


@router.get("/experiments/{experiment_id}/export/", response={200: dict, **_ERRORS})
def export(request, experiment_id: str, format: str = "json"):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    if format == "csv":
        response = HttpResponse(svc.export_experiment_csv(experiment), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{experiment.slug}-jobs.csv"'
        return response
    if format != "json":
        return Status(400, {"detail": "format must be json or csv", "code": "invalid_format"})
    return Status(200, svc.export_experiment(experiment))


# ── schedules ─────────────────────────────────────────────────────────


def _schedule_or_404(experiment: Experiment, schedule_id: int) -> ExperimentSchedule:
    return get_object_or_404(ExperimentSchedule, id=schedule_id, experiment=experiment)


@router.get("/experiments/{experiment_id}/schedules/", response=list[ScheduleOut])
def list_schedules(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    return [svc.schedule_out(s) for s in experiment.schedules.order_by("id")]


@router.post("/experiments/{experiment_id}/schedules/", response={200: ScheduleOut, **_ERRORS})
def create_schedule(request, experiment_id: str, payload: ScheduleSpec):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    if experiment.status == Experiment.Status.ARCHIVED:
        return Status(400, {"detail": "Archived experiments cannot be scheduled.", "code": "archived"})
    try:
        fields = automation.validate_schedule(payload.dict())
    except ExperimentError as exc:
        return _fail(exc)
    schedule = ExperimentSchedule.objects.create(experiment=experiment, created_by=request.auth, **fields)
    return Status(200, svc.schedule_out(schedule))


@router.patch("/experiments/{experiment_id}/schedules/{schedule_id}/", response={200: ScheduleOut, **_ERRORS})
def update_schedule(request, experiment_id: str, schedule_id: int, payload: ScheduleUpdateSchema):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    schedule = _schedule_or_404(experiment, schedule_id)
    current = svc.schedule_out(schedule)
    changes = {k: v for k, v in payload.dict(exclude_unset=True).items() if v is not None}
    try:
        fields = automation.validate_schedule({**current, **changes})
    except ExperimentError as exc:
        return _fail(exc)
    for name, value in fields.items():
        setattr(schedule, name, value)
    schedule.save()
    return Status(200, svc.schedule_out(schedule))


@router.delete("/experiments/{experiment_id}/schedules/{schedule_id}/", response=SuccessSchema)
def delete_schedule(request, experiment_id: str, schedule_id: int):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    _schedule_or_404(experiment, schedule_id).delete()
    return {"success": True}


@router.post("/experiments/{experiment_id}/schedules/{schedule_id}/run-now/", response=ScheduleRunNowResult)
def run_schedule_now(request, experiment_id: str, schedule_id: int):
    """Fire the schedule's action now (its cron is unchanged). Runs synchronously in this request."""
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    schedule = _schedule_or_404(experiment, schedule_id)
    result = automation.run_schedule_action(schedule.id)
    return {
        "outcome": result.get("outcome", "error"),
        "batch_id": result.get("batch_id"),
        "experiment_id": result.get("experiment_id"),
        "jobs_created": result.get("jobs_created", 0),
    }


# ── post-run ──────────────────────────────────────────────────────────


def _post_run_config(experiment: Experiment) -> dict:
    return {
        "config": svc.effective_post_run(experiment),
        "runs": [svc.post_run_out(r) for r in experiment.post_runs.order_by("-created_at")[:20]],
    }


@router.get("/experiments/{experiment_id}/post-run/", response=PostRunConfigOut)
def get_post_run(request, experiment_id: str):
    return _post_run_config(_owned_experiment_or_404(request.auth, experiment_id))


@router.put("/experiments/{experiment_id}/post-run/", response={200: PostRunConfigOut, **_ERRORS})
def put_post_run(request, experiment_id: str, payload: PostRunSpec):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    if experiment.status == Experiment.Status.ARCHIVED:
        return Status(400, {"detail": "Archived experiments cannot be edited.", "code": "archived"})
    try:
        experiment.post_run = svc.normalize_post_run(payload.dict(), experiment.mode)
    except ExperimentError as exc:
        return _fail(exc)
    experiment.save(update_fields=["post_run", "updated_at"])
    return Status(200, _post_run_config(experiment))


@router.post("/experiments/{experiment_id}/post-run/run/", response={200: PostRunOut, **_ERRORS})
def run_post_run(request, experiment_id: str):
    experiment = _owned_experiment_or_404(request.auth, experiment_id)
    if not experiment.jobs.filter(status=GenerationJob.Status.COMPLETED).exists():
        return Status(400, {"detail": "The experiment has no completed jobs yet.", "code": "nothing_to_analyze"})
    try:
        post_run = automation.create_manual_post_run(experiment)
    except ExperimentError as exc:
        return _fail(exc)
    return Status(200, svc.post_run_out(post_run))
