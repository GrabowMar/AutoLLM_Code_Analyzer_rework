"""Studies API (spec section 9.4), mounted under ``/api/generation/``.

Mounted by ``backend.generation.api`` without a prefix, so routes declare their full path (as in the
spec's tables). Static paths are registered before parameterised ones. Every study route is owner-scoped
(staff see every study); a foreign or malformed id is a 404. Operation ids are explicit and published in
``docs/design/SAMPLE_GEN_V3_CHANGES.md`` (MCP tools resolve them).

Errors: ``{"detail", "code", ...}`` with 400 (invalid design, plan errors, confirmation required), 404,
409 (``plan_stale``, ``study_locked``, wrong status.
"""

from __future__ import annotations

import uuid
from typing import Any
from typing import Literal

from django.db.models import Q
from django.http import FileResponse
from django.http import Http404
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja import Schema
from ninja.responses import Status
from pydantic import Field

from backend.common.exceptions import APIError
from backend.generation.models import Study
from backend.generation.models import StudyCondition
from backend.generation.models import StudyPlan
from backend.generation.models import StudyPostRun
from backend.generation.models import StudySchedule
from backend.generation.study import automation
from backend.generation.study import launch as svc
from backend.generation.study import plan as planner
from backend.generation.study import serialize
from backend.generation.study.analysis_plan import PlanError
from backend.generation.study.design import DesignError
from backend.generation.study.errors import StudyError

router = Router(tags=["generation: studies"])

_RESPONSES: dict[int, Any] = {200: dict, 400: dict, 404: dict, 409: dict}
_PAGE_SIZE = 50
_PAGE_SIZE_MAX = 200


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StudyCreateIn(Schema):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    hypothesis: str = ""
    design: dict[str, Any]


class StudyPatchIn(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    hypothesis: str | None = None
    design: dict[str, Any] | None = None
    analysis_plan: dict[str, Any] | None = None


class ResolveIn(Schema):
    drop_incompatible: bool = False
    sample_cells: int = Field(default=50, ge=0, le=2000)
    persist: bool = False


class FreezeIn(Schema):
    drop_incompatible: bool = False
    # E2E-14: a drawing design keeps the parents pinned at its first freeze; true draws them again.
    redraw_parents: bool = False


class LaunchIn(Schema):
    plan_hash: str = Field(min_length=64, max_length=64)
    confirm_usd: float | None = None
    study_cap: float | Literal["auto"] | None = None
    pilot_override: bool = False


class ExpandIn(Schema):
    override_gates: bool = False
    reason: str | None = None


class PauseIn(Schema):
    reason: str | None = None


class TopUpIn(Schema):
    cells: Literal["missing", "infra"] | list[int]
    reason: str = ""


class ReplicatesIn(Schema):
    replicates: int = Field(ge=1, le=5)


class CloneIn(Schema):
    mode: Literal["exact", "latest_versions"] = "exact"
    name: str = Field(min_length=1, max_length=200)


class DiffIn(Schema):
    a: str
    b: str


class OverrideIn(Schema):
    owner_kind: str
    owner_ref: str
    role: str | None = None
    template: str
    prompt_set_ref: str | None = None  # prompt/auth_fragment owners when several prompt sets use the template


class ScheduleIn(Schema):
    action: str
    cron: str
    timezone: str = "UTC"
    params: dict[str, Any] = Field(default_factory=dict)
    skip_if_active: bool = True
    enabled: bool = True


class SchedulePatchIn(Schema):
    cron: str | None = None
    timezone: str | None = None
    params: dict[str, Any] | None = None
    skip_if_active: bool | None = None
    enabled: bool | None = None


class PostRunIn(Schema):
    kind: Literal["analysis", "patterns"] = "analysis"
    trigger: Literal["manual", "completed"] = "manual"
    config: dict[str, Any] = Field(default_factory=dict)


class AdhocIn(Schema):
    design: dict[str, Any]
    launch: bool = True
    name: str | None = None
    confirm_usd: float | None = None
    # A quick generation ("Generate app" page): hidden from the list; with ``launch=False`` a dry run.
    quick: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(exc: Exception) -> Status:
    if isinstance(exc, StudyError):
        return Status(exc.status, exc.payload())
    if isinstance(exc, DesignError):
        body: dict[str, Any] = {"detail": str(exc), "code": "invalid_design", "path": exc.path}
        issues = getattr(exc, "issues", None)
        if issues:
            body["issues"] = [issue.model_dump(mode="json") for issue in issues]
        return Status(400, body)
    if isinstance(exc, (PlanError, automation.ScheduleError)):
        return Status(400, {"detail": str(exc), "code": "invalid_request"})
    if isinstance(exc, APIError):
        return Status(getattr(exc, "status_code", 409) or 409, {"detail": str(exc), "code": "conflict"})
    raise exc


def _visible(user: Any) -> Any:
    qs = Study.objects.select_related("resource_policy", "active_plan")
    return qs if getattr(user, "is_staff", False) else qs.filter(created_by=user)


def _study(request: Any, study_id: str) -> Study:
    try:
        pk = uuid.UUID(str(study_id))
    except ValueError as exc:
        raise Http404 from exc
    return get_object_or_404(_visible(request.auth), pk=pk)


def _bundle(study: Study, plan_hash: str) -> planner.PlanBundle:
    row = StudyPlan.objects.filter(study=study, plan_hash=plan_hash).first()
    if row is not None:
        return planner.load_bundle(row)
    cached = planner.cached_bundle(study, plan_hash)
    if cached is None:
        raise Http404
    return cached


# ---------------------------------------------------------------------------
# Collection and static routes (registered before /studies/{study_id}/)
# ---------------------------------------------------------------------------


@router.get("/studies/", response=dict, operation_id="generation_studies_list")
def list_studies(
    request,
    status: str | None = None,
    mode: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    include_quick: bool = False,
    page: int = 1,
    page_size: int = _PAGE_SIZE,
):
    """``Page[StudyListOut]`` of the caller's studies (newest first); quick generations only with
    ``include_quick``."""
    qs = _visible(request.auth).order_by("-created_at")
    if not include_quick:
        qs = qs.filter(is_quick=False)
    if status:
        qs = qs.filter(status=status)
    if mode:
        qs = qs.filter(mode=mode)
    if kind:
        qs = qs.filter(kind=kind)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q) | Q(hypothesis__icontains=q))
    page = max(1, page)
    size = max(1, min(_PAGE_SIZE_MAX, page_size))
    total = qs.count()
    items = list(qs[(page - 1) * size : page * size])
    counts = serialize.cell_counts(items)
    return {
        "items": [serialize.study_list_item(s, counts.get(s.pk)) for s in items],
        "total": total,
        "page": page,
        "page_size": size,
    }


@router.post("/studies/", response=_RESPONSES, operation_id="generation_studies_create")
def create_study(request, payload: StudyCreateIn):
    """A draft study from a design (validated; the analysis plan normalized)."""
    try:
        study = svc.create_study(
            user=request.auth,
            name=payload.name,
            design=payload.design,
            description=payload.description,
            hypothesis=payload.hypothesis,
        )
    except (StudyError, DesignError, PlanError) as exc:
        return _error(exc)
    return Status(200, serialize.study_out(study))


@router.post("/studies/adhoc/", response=_RESPONSES, operation_id="generation_studies_adhoc")
def adhoc(request, payload: AdhocIn):
    """Resolve and (by default) launch a design in one call: ``{study_id, plan_hash, run_ids, ...}``."""
    try:
        result = svc.launch_adhoc(
            payload.design,
            user=request.auth,
            launch=payload.launch,
            name=payload.name,
            confirm_usd=payload.confirm_usd,
            quick=payload.quick,
        )
    except (StudyError, DesignError, PlanError) as exc:
        return _error(exc)
    return Status(200, result)


@router.get("/studies/cron-preview/", response=dict, operation_id="generation_studies_cron_preview")
def cron_preview(request, cron: str, timezone: str = "UTC"):
    """``{valid, detail, next_runs}`` for a schedule's cron expression."""
    return automation.cron_preview(cron, timezone)


# ---------------------------------------------------------------------------
# One study
# ---------------------------------------------------------------------------


@router.get("/studies/{study_id}/", response=dict, operation_id="generation_studies_get")
def get_study(request, study_id: str):
    """``StudyOut``: the design, its hash, the active plan, the analysis plan, the lock and the factors."""
    return serialize.study_out(_study(request, study_id))


@router.patch("/studies/{study_id}/", response=_RESPONSES, operation_id="generation_studies_update")
def update_study(request, study_id: str, payload: StudyPatchIn):
    """Edit text fields always; design and analysis plan before launch only (409 when locked)."""
    study = _study(request, study_id)
    try:
        study = svc.update_study(
            study,
            name=payload.name,
            description=payload.description,
            hypothesis=payload.hypothesis,
            design=payload.design,
            analysis_plan=payload.analysis_plan,
        )
    except (StudyError, DesignError, PlanError, APIError) as exc:
        return _error(exc)
    return Status(200, serialize.study_out(study))


@router.post("/studies/{study_id}/resolve/", response=_RESPONSES, operation_id="generation_studies_resolve")
def resolve(request, study_id: str, payload: ResolveIn):
    """``PlanOut`` of the current design (ephemeral unless ``persist``; nothing else is written)."""
    study = _study(request, study_id)
    try:
        bundle = planner.resolve_bundle(study, persist=payload.persist, drop_incompatible=payload.drop_incompatible)
    except DesignError as exc:
        return _error(exc)
    return Status(200, serialize.plan_out(bundle, sample_cells=payload.sample_cells))


@router.post("/studies/{study_id}/freeze-plan/", response=_RESPONSES, operation_id="generation_studies_freeze")
def freeze_plan(request, study_id: str, payload: FreezeIn):
    """Persist the resolved plan as a draft ``StudyPlan`` (``PlanOut``, ``persisted: true``)."""
    study = _study(request, study_id)
    try:
        row = planner.freeze(
            study,
            drop_incompatible=payload.drop_incompatible,
            redraw_parents=payload.redraw_parents,
        )
    except DesignError as exc:
        return _error(exc)
    return Status(200, serialize.plan_out(planner.load_bundle(row)))


@router.get(
    "/studies/{study_id}/plans/{plan_hash}/cells/",
    response=dict,
    operation_id="generation_studies_plan_cells",
)
def plan_cells(
    request,
    study_id: str,
    plan_hash: str,
    condition: int | None = None,
    unit: int | None = None,
    page: int = 1,
    page_size: int = _PAGE_SIZE,
):
    """``Page[ResolvedCellOut]`` of a persisted or cached plan."""
    bundle = _bundle(_study(request, study_id), plan_hash)
    cells = [
        c
        for c in bundle.plan.cells
        if (condition is None or c.condition_index == condition) and (unit is None or c.unit_index == unit)
    ]
    page = max(1, page)
    size = max(1, min(_PAGE_SIZE_MAX, page_size))
    return {
        "items": [serialize.cell_out(c) for c in cells[(page - 1) * size : page * size]],
        "total": len(cells),
        "page": page,
        "page_size": size,
    }


@router.get(
    "/studies/{study_id}/plans/{plan_hash}/cells/{cell_key}/",
    response=dict,
    operation_id="generation_studies_plan_cell",
)
def plan_cell(request, study_id: str, plan_hash: str, cell_key: str):
    """``ResolvedCellDetailOut``: stimuli with source spans, effective roles, policy and params, identity."""
    bundle = _bundle(_study(request, study_id), plan_hash)
    cell = bundle.cell(cell_key)
    if cell is None:
        raise Http404
    return serialize.cell_detail(bundle, cell)


@router.post(
    "/studies/{study_id}/plans/{plan_hash}/diff/",
    response=dict,
    operation_id="generation_studies_plan_diff",
)
def plan_diff(request, study_id: str, plan_hash: str, payload: DiffIn):
    """``{levels_diff, stimuli_diff, params_diff}`` between two cells of one plan."""
    bundle = _bundle(_study(request, study_id), plan_hash)
    a, b = bundle.cell(payload.a), bundle.cell(payload.b)
    if a is None or b is None:
        raise Http404
    return serialize.cell_diff(bundle, a, b)


@router.post("/studies/{study_id}/launch/", response=_RESPONSES, operation_id="generation_studies_launch")
def launch(request, study_id: str, payload: LaunchIn):
    """``{status, created_runs, stage, resolved_cap}``; runs are queued after commit."""
    study = _study(request, study_id)
    try:
        result = svc.launch(
            study,
            payload.plan_hash,
            confirm_usd=payload.confirm_usd,
            study_cap=payload.study_cap,
            pilot_override=payload.pilot_override,
            user=request.auth,
        )
    except (StudyError, DesignError) as exc:
        return _error(exc)
    return Status(200, result)


@router.post("/studies/{study_id}/expand/", response=_RESPONSES, operation_id="generation_studies_expand")
def expand(request, study_id: str, payload: ExpandIn):
    """Pilot to main: ``{created_runs, reused_pilot_cells, gates}``."""
    try:
        result = svc.expand_pilot(
            _study(request, study_id),
            override_gates=payload.override_gates,
            reason=payload.reason,
            user=request.auth,
        )
    except StudyError as exc:
        return _error(exc)
    return Status(200, result)


@router.post("/studies/{study_id}/pause/", response=_RESPONSES, operation_id="generation_studies_pause")
def pause(request, study_id: str, payload: PauseIn):
    """Stop claiming new runs (running ones finish); ``pause_reason=manual``."""
    del payload  # the free-text reason is not stored: pause_reason is a closed vocabulary
    try:
        study = svc.pause(_study(request, study_id), reason="manual")
    except StudyError as exc:
        return _error(exc)
    return Status(200, serialize.study_out(study))


@router.post("/studies/{study_id}/resume/", response=_RESPONSES, operation_id="generation_studies_resume")
def resume(request, study_id: str):
    try:
        study = svc.resume(_study(request, study_id))
    except StudyError as exc:
        return _error(exc)
    return Status(200, serialize.study_out(study))


@router.post("/studies/{study_id}/cancel/", response=_RESPONSES, operation_id="generation_studies_cancel")
def cancel(request, study_id: str):
    try:
        study = svc.cancel(_study(request, study_id))
    except StudyError as exc:
        return _error(exc)
    return Status(200, serialize.study_out(study))


@router.post("/studies/{study_id}/top-up/", response=_RESPONSES, operation_id="generation_studies_top_up")
def top_up(request, study_id: str, payload: TopUpIn):
    """``{created_runs, refused: [{cell_id, code}]}`` (only infra / stopped / missing cells; 6.14)."""
    try:
        result = svc.top_up(_study(request, study_id), payload.cells, reason=payload.reason, user=request.auth)
    except StudyError as exc:
        return _error(exc)
    return Status(200, result)


@router.post(
    "/studies/{study_id}/add-replicates/",
    response=_RESPONSES,
    operation_id="generation_studies_add_replicates",
)
def add_replicates(request, study_id: str, payload: ReplicatesIn):
    """Repeats beyond the plan (exploratory only; their runs are ``beyond_plan``)."""
    try:
        result = svc.add_replicates(_study(request, study_id), payload.replicates, user=request.auth)
    except (StudyError, DesignError) as exc:
        return _error(exc)
    return Status(200, result)


@router.post("/studies/{study_id}/clone/", response=_RESPONSES, operation_id="generation_studies_clone")
def clone(request, study_id: str, payload: CloneIn):
    """A draft copy: ``StudyOut`` plus ``version_diffs`` (``latest_versions`` re-pins library levels)."""
    try:
        study, diffs = svc.clone_with_diffs(
            _study(request, study_id),
            mode=payload.mode,
            name=payload.name,
            user=request.auth,
        )
    except (StudyError, DesignError, PlanError) as exc:
        return _error(exc)
    return Status(200, serialize.study_out(study) | {"version_diffs": diffs})


def _condition(request, study_id: str, condition_id: int) -> StudyCondition:
    study = _study(request, study_id)
    return get_object_or_404(StudyCondition, pk=condition_id, study=study)


@router.post(
    "/studies/{study_id}/conditions/{condition_id}/pause/",
    response=dict,
    operation_id="generation_studies_condition_pause",
)
def pause_condition(request, study_id: str, condition_id: int):
    condition = svc.pause_condition(_condition(request, study_id, condition_id))
    return {"id": condition.pk, "paused": condition.paused, "pause_reason": condition.pause_reason}


@router.post(
    "/studies/{study_id}/conditions/{condition_id}/resume/",
    response=dict,
    operation_id="generation_studies_condition_resume",
)
def resume_condition(request, study_id: str, condition_id: int):
    condition = svc.resume_condition(_condition(request, study_id, condition_id))
    return {"id": condition.pk, "paused": condition.paused, "pause_reason": condition.pause_reason}


@router.get("/studies/{study_id}/cells/", response=dict, operation_id="generation_studies_cells")
def cells(
    request,
    study_id: str,
    state: str | None = None,
    condition: int | None = None,
    unit: int | None = None,
    stage: str | None = None,
):
    """``CellsPayload``: cells with outcome, current run, attempts, beyond-plan runs, spend and block."""
    return serialize.cells_payload(_study(request, study_id), state=state, condition=condition, unit=unit, stage=stage)


@router.get("/studies/{study_id}/results/", response=_RESPONSES, operation_id="generation_studies_results")
def results(
    request,
    study_id: str,
    metric: str | None = None,
    fingerprint: str = "majority",
    sensitivity: str | None = None,
    baseline: int | None = None,
    common_cells: bool = True,
):
    """Leaderboard, paired comparisons, gate, sensitivity, attrition, by-factor EMMs (intention-to-treat)."""
    from backend.generation.study.results import study_results

    study = _study(request, study_id)
    try:
        return Status(
            200,
            study_results(
                study,
                metric=metric,
                fingerprint=fingerprint,
                sensitivity=sensitivity,
                baseline=baseline,
                common_cells=common_cells,
            ),
        )
    except ValueError as exc:
        return Status(400, {"detail": str(exc), "code": "invalid_results_query"})


@router.get("/studies/{study_id}/attrition/", response=list[dict], operation_id="generation_studies_attrition")
def attrition(request, study_id: str, stage: str = "main"):
    """Per condition: planned cells, runs per outcome, usable, truncated, excluded by reason, infra retries."""
    from backend.generation.runs.queries import attrition as attrition_table

    return attrition_table(_study(request, study_id), stage)


@router.get("/studies/{study_id}/export/", operation_id="generation_studies_export")
def export(request, study_id: str, format: str = "zip"):
    """The study archive (plan, manifests, runs, attrition, files, evaluations, locks, patterns)."""
    from backend.generation.study.export import export_zip

    if format != "zip":
        return Status(400, {"detail": "format must be zip", "code": "invalid_format"})
    study = _study(request, study_id)
    return FileResponse(export_zip(study), as_attachment=True, filename=f"{study.slug or 'study'}-export.zip")


@router.get(
    "/studies/{study_id}/prompt-overrides/",
    response=list[dict],
    operation_id="generation_studies_prompt_overrides",
)
def prompt_overrides(request, study_id: str):
    study = _study(request, study_id)
    return [_override_out(o) for o in study.prompt_overrides.order_by("created_at")]


def _override_out(override: Any) -> dict[str, Any]:
    return {
        "id": override.pk,
        "owner_kind": override.owner_kind,
        "owner_ref_base": override.owner_ref_base,
        "owner_ref_new": override.owner_ref_new,
        "role": override.role,
        "swapped_factor": override.swapped_factor,
        "diff": override.diff,
        "created_at": override.created_at.isoformat(),
    }


@router.post(
    "/studies/{study_id}/prompt-overrides/",
    response=_RESPONSES,
    operation_id="generation_studies_prompt_override_create",
)
def create_prompt_override(request, study_id: str, payload: OverrideIn):
    """Fork the entity owning the edited text as a user version, swap the factor level, record the diff."""
    from backend.generation.study.overrides import apply_override

    try:
        override = apply_override(
            _study(request, study_id),
            owner_kind=payload.owner_kind,
            owner_ref=payload.owner_ref,
            template=payload.template,
            role=payload.role or "",
            user=request.auth,
            prompt_set_ref=payload.prompt_set_ref or None,
        )
    except (StudyError, DesignError, PlanError, APIError) as exc:
        return _error(exc)
    return Status(200, _override_out(override))


# ---------------------------------------------------------------------------
# Schedules and post-runs
# ---------------------------------------------------------------------------


def _schedule_out(schedule: StudySchedule) -> dict[str, Any]:
    return {
        "id": schedule.pk,
        "action": schedule.action,
        "cron": schedule.cron,
        "timezone": schedule.timezone,
        "params": schedule.params,
        "skip_if_active": schedule.skip_if_active,
        "enabled": schedule.enabled,
        "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        "last_run_at": schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        "last_result": schedule.last_result,
    }


@router.get("/studies/{study_id}/schedules/", response=list[dict], operation_id="generation_studies_schedules")
def schedules(request, study_id: str):
    return [_schedule_out(s) for s in _study(request, study_id).schedules.order_by("pk")]


@router.post(
    "/studies/{study_id}/schedules/",
    response=_RESPONSES,
    operation_id="generation_studies_schedule_create",
)
def create_schedule(request, study_id: str, payload: ScheduleIn):
    study = _study(request, study_id)
    try:
        fields = automation.validate_schedule(payload.model_dump())
    except automation.ScheduleError as exc:
        return _error(exc)
    schedule = StudySchedule.objects.create(study=study, created_by=request.auth, **fields)
    return Status(200, _schedule_out(schedule))


@router.patch(
    "/studies/{study_id}/schedules/{schedule_id}/",
    response=_RESPONSES,
    operation_id="generation_studies_schedule_update",
)
def update_schedule(request, study_id: str, schedule_id: int, payload: SchedulePatchIn):
    schedule = get_object_or_404(StudySchedule, pk=schedule_id, study=_study(request, study_id))
    spec = {
        "action": schedule.action,
        "cron": payload.cron if payload.cron is not None else schedule.cron,
        "timezone": payload.timezone if payload.timezone is not None else schedule.timezone,
        "params": payload.params if payload.params is not None else schedule.params,
        "skip_if_active": payload.skip_if_active if payload.skip_if_active is not None else schedule.skip_if_active,
        "enabled": payload.enabled if payload.enabled is not None else schedule.enabled,
    }
    try:
        fields = automation.validate_schedule(spec)
    except automation.ScheduleError as exc:
        return _error(exc)
    for name, value in fields.items():
        setattr(schedule, name, value)
    schedule.save()
    return Status(200, _schedule_out(schedule))


@router.delete(
    "/studies/{study_id}/schedules/{schedule_id}/",
    response={204: None},
    operation_id="generation_studies_schedule_delete",
)
def delete_schedule(request, study_id: str, schedule_id: int):
    get_object_or_404(StudySchedule, pk=schedule_id, study=_study(request, study_id)).delete()
    return Status(204, None)


@router.post(
    "/studies/{study_id}/schedules/{schedule_id}/run-now/",
    response=dict,
    operation_id="generation_studies_schedule_run_now",
)
def run_schedule_now(request, study_id: str, schedule_id: int):
    schedule = get_object_or_404(StudySchedule, pk=schedule_id, study=_study(request, study_id))
    return automation.run_schedule_action(schedule.pk)


def _post_run_out(post_run: StudyPostRun) -> dict[str, Any]:
    return {
        "id": post_run.pk,
        "kind": post_run.kind,
        "trigger": post_run.trigger,
        "status": post_run.status,
        "config": post_run.config,
        "progress": post_run.progress,
        "analysis_run_ids": post_run.analysis_run_ids,
        "report_id": str(post_run.report_id) if post_run.report_id else None,
        "pattern_run_id": str(post_run.pattern_run_id) if post_run.pattern_run_id else None,
        "error": post_run.error,
        "created_at": post_run.created_at.isoformat(),
        "started_at": post_run.started_at.isoformat() if post_run.started_at else None,
        "completed_at": post_run.completed_at.isoformat() if post_run.completed_at else None,
    }


@router.get("/studies/{study_id}/post-runs/", response=list[dict], operation_id="generation_studies_post_runs")
def post_runs(request, study_id: str):
    return [_post_run_out(p) for p in _study(request, study_id).post_runs.order_by("-created_at")]


@router.post(
    "/studies/{study_id}/post-runs/",
    response=_RESPONSES,
    operation_id="generation_studies_post_run_create",
)
def create_post_run(request, study_id: str, payload: PostRunIn):
    """Queue a post-run (``manual``: now; ``completed``: when the study completes or stops)."""
    try:
        post_run = automation.create_post_run(
            _study(request, study_id),
            kind=payload.kind,
            trigger=payload.trigger,
            config=payload.config,
        )
    except (StudyError, automation.ScheduleError) as exc:
        return _error(exc)
    return Status(200, _post_run_out(post_run))
