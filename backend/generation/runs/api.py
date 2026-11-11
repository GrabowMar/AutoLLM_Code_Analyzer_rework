"""Runs API (spec section 9.5).

Mounted by ``backend.generation.api`` without a prefix, so routes declare their full path under
``/api/generation/``. Static paths are registered before parameterised ones.

Access: staff see every run; anyone else sees the runs they created and the runs of studies they created
(the same rule as the ``run:<id>`` SSE channel). Unknown or invisible runs are 404. Mutations run inside the
request transaction; work reaches the executor only after commit (``agentic/actions.py``).

The live stream is not HTTP: clients subscribe to ``run:<id>`` (``contracts.events``: ``status``, ``step``,
``usage``, ``trajectory_step``, ``log``, ``evaluation``).
"""

import logging
import re
from datetime import datetime
from typing import Any
from typing import Literal
from uuid import UUID

from django.db.models import Avg
from django.db.models import Case
from django.db.models import CharField
from django.db.models import Count
from django.db.models import F
from django.db.models import FloatField
from django.db.models import Q
from django.db.models import QuerySet
from django.db.models import Value
from django.db.models import When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.fields.json import KeyTransform
from django.db.models.functions import Cast
from django.http import HttpRequest
from django.http import HttpResponse
from ninja import Field
from ninja import Query
from ninja import Router
from ninja import Schema
from ninja import Status

from backend.generation import blobs
from backend.generation.agentic import actions
from backend.generation.agentic import evidence
from backend.generation.agentic.runner import attempt_number
from backend.generation.contracts import ErrorOut
from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationRun
from backend.generation.models import RunAttempt
from backend.generation.models import RunEvaluation
from backend.generation.models import TaskSpecVersion
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunStatus
from backend.generation.runs.view import RunView
from backend.generation.runs.view import run_view
from backend.generation.runs.view import run_views
from backend.generation.services import token_metrics

logger = logging.getLogger(__name__)

router = Router(tags=["generation: runs"])

PAGE_SIZE_MAX = 200
_ERRORS = {400: ErrorOut, 404: ErrorOut, 409: ErrorOut, 415: ErrorOut}


# =====================================================================================
# Schemas
# =====================================================================================


class RunListOut(Schema):
    id: UUID
    kind: str
    status: str
    phase: str
    outcome: str
    usable: bool | None = None
    model: str | None = None
    task: str | None = None  # the task version's ref ``slug@v`` (None for runs without a task version)
    task_title: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    cost_usd: float | None = None
    wall_s: float | None = None
    agent_pass_rate: float | None = None
    created_at: datetime


class RunPageOut(Schema):
    items: list[RunListOut]
    total: int
    page: int
    page_size: int


class RunAttemptOut(Schema):
    attempt: int
    executor_id: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    termination: str
    outcome: str
    infra_error: str
    oom_killed: bool
    budget_exhausted: str
    served: dict[str, Any]
    usage: dict[str, Any]
    time_split: dict[str, Any]
    observed_inputs: dict[str, Any] | None = None
    has_trajectory: bool
    evidence_dir: str


class RunEvaluationOut(Schema):
    id: int
    eval_suite: str
    eval_level: str
    mode: str
    status: str
    stages: dict[str, Any]
    result: dict[str, Any] | None = None
    adherence: dict[str, Any] | None = None
    build_key: str
    image_id: str
    reused_from: int | None = None
    retest_of: int | None = None
    baseline: int | None = None
    created_at: datetime


class StudyRefOut(Schema):
    study_id: UUID | None = None
    study_name: str | None = None
    condition_id: int | None = None
    cell_id: int | None = None
    unit_key: str | None = None
    repeat_index: int | None = None
    stage: str = ""


class RunOut(RunListOut):
    protocol: str
    outcome_reason: str
    identity: dict[str, str]
    study: StudyRefOut
    cell_attempt: int | None = None
    beyond_plan: bool
    lock_source: str
    resolved_lock: dict[str, Any] | None = None
    parent_run: UUID | None = None
    slot_target: UUID | None = None
    attempt: int
    attempts: list[RunAttemptOut]
    validity: dict[str, Any]
    result: dict[str, Any]
    metrics: dict[str, Any]
    latest_evaluation: RunEvaluationOut | None = None
    error_message: str
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SourceSpanOut(Schema):
    """Which library entity owns ``text[char_start:char_end]`` of a stimulus (``contracts.manifest.SourceSpan``)."""

    template_ref: str
    owner_kind: str
    owner_ref: str
    role: str | None = None
    char_start: int
    char_end: int


class StimulusOut(Schema):
    role: str
    template_ref: str
    sha: str
    text: str | None = None
    sources: list[SourceSpanOut] = Field(default_factory=list)  # empty when the render recorded no spans


class ManifestOut(Schema):
    manifest: dict[str, Any]
    manifest_hash: str
    stimuli: list[StimulusOut]


class FileOut(Schema):
    path: str
    origin: str
    size: int
    loc: int
    language: str
    is_test: bool
    excluded: str
    blob: str


class TrajectoryOut(Schema):
    attempt: int
    trajectory: dict[str, Any] | None = None
    observed_inputs: dict[str, Any] | None = None


class CallOut(Schema):
    index: int
    attempt: int
    stage: str
    request_class: str | None = None
    prompt_tokens: int | None = None
    cached_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    finish_reason: str | None = None
    cost: float | None = None
    list_cost: float | None = None
    provider: str | None = None
    response_model: str | None = None
    ms: float | None = None
    tokens_per_s: float | None = None
    front_protocol: str | None = None
    protocol_mode: str | None = None
    serving_tier: str | None = None
    translation_dropped: dict[str, int] = Field(default_factory=dict)
    status: int | None = None


class EvaluateIn(Schema):
    eval_suite: str | None = None
    force_rebuild: bool = False


class EvaluateOut(Schema):
    evaluation_id: int


class RetryOut(Schema):
    run_id: UUID
    attempt: int


class RunFilters(Schema):
    q: str | None = Field(None, max_length=200)  # task slug/title, model id or run id prefix (case-insensitive)
    status: str | None = None  # one status or a comma-separated list (pending,running,...)
    study: UUID | None = None
    condition: int | None = None
    kind: str | None = None
    outcome: str | None = None
    model: str | None = None
    usable: bool | None = None
    beyond_plan: bool | None = None
    task: str | None = None  # task slug
    harness: str | None = None  # harness preset ``slug@version`` (or a bare slug: every version)
    stack: str | None = None  # stack profile ``slug@version`` (or a bare slug)
    # created (default, newest first), pass_rate, cost or wall; a leading "-" sorts descending
    sort: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=PAGE_SIZE_MAX)


class FacetValueOut(Schema):
    value: str
    label: str
    count: int


class RunFacetsSummaryOut(Schema):
    total: int
    tested: int
    # agent pass rate over measured runs (``null`` when none is measured; never a 0 stand-in)
    measured: int
    mean_pass_rate: float | None = None


class RunFacetsOut(Schema):
    summary: RunFacetsSummaryOut
    outcomes: list[FacetValueOut]
    models: list[FacetValueOut]
    tasks: list[FacetValueOut]
    harnesses: list[FacetValueOut]
    stacks: list[FacetValueOut]
    studies: list[FacetValueOut]


# =====================================================================================
# Helpers
# =====================================================================================


def _visible(request: HttpRequest) -> QuerySet[GenerationRun]:
    user = request.user
    runs = GenerationRun.objects.all()
    if getattr(user, "is_staff", False):
        return runs
    return runs.filter(Q(created_by=user) | Q(study__created_by=user))


def _get(request: HttpRequest, run_id: UUID) -> GenerationRun | None:
    return _visible(request).select_related("manifest", "study", "cell__unit", "model").filter(pk=run_id).first()


def _not_found() -> Status:
    return Status(404, {"detail": "Run not found", "code": "not_found"})


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _pass_rate(run: GenerationRun) -> float | None:
    summary = (run.metrics or {}).get("eval") or {}
    if summary.get("status") != "measured" or not summary.get("measured"):
        return None  # not measured is never a 0 pass rate
    return _number(summary.get("agent_pass_rate"))


def _list_item(view: RunView) -> dict[str, Any]:
    run = view._run  # noqa: SLF001
    metrics = run.metrics or {}
    task = view.task
    return {
        "id": run.pk,
        "kind": run.kind,
        "status": run.status,
        "phase": run.phase or "",
        "outcome": run.outcome or "",
        "usable": run.usable,
        "model": view.model_slug,
        "task": task.ref if task is not None else None,
        "task_title": (task.title or None) if task is not None else None,
        "labels": view.labels(),
        "cost_usd": _number(metrics.get("cost_usd")),
        "wall_s": _number(metrics.get("wall_s")) if metrics.get("wall_s") is not None else run.duration_seconds,
        "agent_pass_rate": _pass_rate(run),
        "created_at": run.created_at,
    }


_RUN_ID_PREFIX = re.compile(r"^[0-9a-f-]{4,36}$")


def _search(runs: QuerySet[GenerationRun], q: str) -> QuerySet[GenerationRun]:
    """Task slug / title / version name, model id, or a run id prefix (``GET /runs/?q=``)."""
    match = (
        Q(task_version__task__slug__icontains=q)
        | Q(task_version__name__icontains=q)
        | Q(task_version__content__title__icontains=q)
        | Q(model__model_id__icontains=q)
    )
    if _RUN_ID_PREFIX.match(q.lower()):
        runs = runs.annotate(_id_text=Cast("id", output_field=CharField()))
        match |= Q(_id_text__startswith=q.lower())
    return runs.filter(match)


def _attempt_out(row: RunAttempt) -> dict[str, Any]:
    return {
        "attempt": row.attempt,
        "executor_id": row.executor_id,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "termination": row.termination,
        "outcome": row.outcome,
        "infra_error": row.infra_error,
        "oom_killed": row.oom_killed,
        "budget_exhausted": row.budget_exhausted,
        "served": row.served or {},
        "usage": row.usage or {},
        "time_split": row.time_split or {},
        "observed_inputs": row.observed_inputs,
        "has_trajectory": bool(row.trajectory_blob),
        "evidence_dir": row.evidence_dir,
    }


def _evaluation_out(row: RunEvaluation) -> dict[str, Any]:
    return {
        "id": row.pk,
        "eval_suite": row.eval_suite.pinned_ref,
        "eval_level": row.eval_level,
        "mode": row.mode,
        "status": row.status,
        "stages": row.stages or {},
        "result": row.result,
        "adherence": row.adherence,
        "build_key": row.build_key,
        "image_id": row.image_id,
        "reused_from": row.reused_from_id,
        "retest_of": row.retest_of_id,
        "baseline": row.baseline_id,
        "created_at": row.created_at,
    }


def _resolved_lock(run: GenerationRun) -> dict[str, Any] | None:
    if not run.resolved_lock_blob:
        return None
    try:
        value = blobs.get_json(run.resolved_lock_blob)
    except (OSError, LookupError, ValueError):
        logger.warning("resolved lock blob %s of run %s is unreadable", run.resolved_lock_blob, run.pk)
        return None
    return value if isinstance(value, dict) else None


def _refused(exc: actions.ActionRefused) -> Status:
    return Status(exc.status_code, {"detail": exc.message, "code": exc.code})


# =====================================================================================
# Endpoints
# =====================================================================================


def _metric(*path: str) -> Cast:
    """A numeric metrics value; JSON null / missing become SQL NULL (so they sort last, not first)."""
    expr: Any = "metrics"
    for key in path[:-1]:
        expr = KeyTransform(key, expr)
    return Cast(KeyTextTransform(path[-1], expr), FloatField())


def _sort_expr(name: str) -> Any:
    """Sort keys of ``GET /runs/?sort=``. The pass rate counts only measured evaluations, as in the rows."""
    if name == "created":
        return F("created_at")
    if name == "pass_rate":
        return Case(
            When(
                metrics__eval__status="measured",
                metrics__eval__measured=True,
                then=_metric("eval", "agent_pass_rate"),
            ),
            default=Value(None),
            output_field=FloatField(),
        )
    if name == "cost":
        return _metric("cost_usd")
    if name == "wall":
        return _metric("wall_s")
    return None


def _ref_filter(runs: QuerySet[GenerationRun], field: str, ref: str) -> QuerySet[GenerationRun]:
    """``slug@version`` pins one library version; a bare slug matches every version."""
    slug, _, version = ref.strip().partition("@")
    runs = runs.filter(**{f"{field}__slug": slug})
    if version:
        runs = runs.filter(**{f"{field}__version": int(version)}) if version.isdigit() else runs.none()
    return runs


def _filtered(request: HttpRequest, filters: RunFilters) -> QuerySet[GenerationRun] | Status:
    """The caller's runs with every list filter applied (no ordering, no paging)."""
    runs = _visible(request)
    if filters.status:
        statuses = [part.strip() for part in filters.status.split(",") if part.strip()]
        unknown = sorted(set(statuses) - set(RunStatus.values))
        if unknown:
            return Status(400, {"detail": f"unknown status: {', '.join(unknown)}", "code": "invalid_filter"})
        runs = runs.filter(status__in=statuses)
    if filters.q and filters.q.strip():
        runs = _search(runs, filters.q.strip())
    if filters.study is not None:
        runs = runs.filter(study_id=filters.study)
    if filters.condition is not None:
        runs = runs.filter(condition_id=filters.condition)
    if filters.kind:
        runs = runs.filter(kind=filters.kind)
    if filters.outcome:
        runs = runs.filter(outcome="" if filters.outcome == "none" else filters.outcome)
    if filters.model:
        runs = runs.filter(model__model_id=filters.model)
    if filters.usable is not None:
        runs = runs.filter(usable=filters.usable)
    if filters.beyond_plan is not None:
        runs = runs.filter(beyond_plan=filters.beyond_plan)
    if filters.task:
        runs = runs.filter(task_version__task__slug=filters.task.strip())
    if filters.harness:
        runs = _ref_filter(runs, "harness_preset", filters.harness)
    if filters.stack:
        runs = _ref_filter(runs, "stack_profile", filters.stack)
    return runs


@router.get("/runs/", response={200: RunPageOut, 400: ErrorOut})
def list_runs(request: HttpRequest, filters: Query[RunFilters]) -> Any:
    """Runs visible to the caller, newest first (or ``sort``), filtered and paginated."""
    runs = _filtered(request, filters)
    if isinstance(runs, Status):
        return runs
    key = (filters.sort or "-created").strip()
    descending = key.startswith("-")
    expr = _sort_expr(key.lstrip("-"))
    if expr is None:
        return Status(400, {"detail": f"unknown sort: {key}", "code": "invalid_filter"})
    order = expr.desc(nulls_last=True) if descending else expr.asc(nulls_last=True)
    runs = runs.order_by(order, "-created_at", "pk")
    total = runs.count()
    start = (filters.page - 1) * filters.page_size
    page = list(runs[start : start + filters.page_size])
    views = run_views(page)
    items = [_list_item(view) for view in views]
    return {"items": items, "total": total, "page": filters.page, "page_size": filters.page_size}


@router.get("/runs/facets/", response={200: RunFacetsOut, 400: ErrorOut})
def run_facets(request: HttpRequest, filters: Query[RunFilters]) -> Any:
    """Filter values with counts for the runs list, plus a summary, over the caller's filtered runs.

    Each facet ignores its own filter (so a chosen outcome still lists the other outcomes with their counts);
    paging and sorting are ignored.
    """

    def scope(**drop: Any) -> QuerySet[GenerationRun] | Status:
        return _filtered(request, filters.model_copy(update=drop))

    base = scope()
    if isinstance(base, Status):
        return base

    def counts(qs: QuerySet[GenerationRun] | Status, *fields: str) -> list[tuple[tuple[Any, ...], int]]:
        if isinstance(qs, Status):
            return []
        rows = qs.values(*fields).annotate(n=Count("id")).order_by("-n")
        return [(tuple(row[f] for f in fields), row["n"]) for row in rows if row[fields[0]] not in (None, "")]

    outcomes = [{"value": v[0], "label": v[0], "count": n} for v, n in counts(scope(outcome=None), "outcome")]
    models = [{"value": v[0], "label": v[0], "count": n} for v, n in counts(scope(model=None), "model__model_id")]
    task_rows = counts(scope(task=None), "task_version__task__slug")
    titles: dict[str, str] = {}
    for version in TaskSpecVersion.objects.filter(task__slug__in=[v[0] for v, _ in task_rows]).order_by("version"):
        titles[version.task.slug] = (version.content or {}).get("title") or version.name or version.task.slug
    tasks = [{"value": v[0], "label": titles.get(v[0], v[0]), "count": n} for v, n in task_rows]
    harnesses = [
        {"value": f"{v[0]}@{v[1]}", "label": f"{v[0]}@{v[1]}", "count": n}
        for v, n in counts(scope(harness=None), "harness_preset__slug", "harness_preset__version")
    ]
    stacks = [
        {"value": f"{v[0]}@{v[1]}", "label": f"{v[0]}@{v[1]}", "count": n}
        for v, n in counts(scope(stack=None), "stack_profile__slug", "stack_profile__version")
    ]
    studies = [
        {"value": str(v[0]), "label": v[1] or str(v[0]), "count": n}
        for v, n in counts(scope(study=None), "study_id", "study__name")
    ]

    measured = base.filter(metrics__eval__status="measured", metrics__eval__measured=True)
    agg = measured.aggregate(
        n=Count("id"),
        mean=Avg(_metric("eval", "agent_pass_rate")),
    )
    summary = {
        "total": base.count(),
        "tested": base.filter(outcome=Outcome.TESTED).count(),
        "measured": agg["n"] or 0,
        "mean_pass_rate": agg["mean"] if agg["n"] else None,
    }
    return {
        "summary": summary,
        "outcomes": outcomes,
        "models": models,
        "tasks": tasks,
        "harnesses": harnesses,
        "stacks": stacks,
        "studies": studies,
    }


@router.get("/runs/{run_id}/", response={200: RunOut, 404: ErrorOut})
def get_run(request: HttpRequest, run_id: UUID) -> Any:
    """One run with its identity, attempts, validity and latest evaluation."""
    run = _get(request, run_id)
    if run is None:
        return _not_found()
    view = run_view(run)
    latest = view.latest_evaluation()
    out = _list_item(view)
    out.update(
        {
            "protocol": run.protocol,
            "outcome_reason": run.outcome_reason,
            "identity": view.fingerprints,
            "study": {
                "study_id": run.study_id,
                "study_name": run.study.name if run.study is not None else None,
                "condition_id": run.condition_id,
                "cell_id": run.cell_id,
                "unit_key": view.unit_key,
                "repeat_index": run.repeat_index,
                "stage": run.stage,
            },
            "cell_attempt": run.cell_attempt,
            "beyond_plan": run.beyond_plan,
            "lock_source": run.lock_source,
            "resolved_lock": _resolved_lock(run),
            "parent_run": run.parent_run_id,
            "slot_target": run.slot_target_id,
            "attempt": attempt_number(run),
            "attempts": [_attempt_out(row) for row in run.attempts.order_by("attempt")],
            "validity": run.validity or {},
            "result": run.result or {},
            "metrics": run.metrics or {},
            "latest_evaluation": _evaluation_out(latest) if latest is not None else None,
            "error_message": run.error_message,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
        },
    )
    return out


@router.get("/runs/{run_id}/manifest/", response={200: ManifestOut, 404: ErrorOut})
def get_run_manifest(request: HttpRequest, run_id: UUID) -> Any:
    """The frozen manifest and the rendered stimulus texts."""
    run = _get(request, run_id)
    if run is None or not hasattr(run, "manifest"):
        return Status(404, {"detail": "Run or manifest not found", "code": "not_found"})
    row = run.manifest
    stimuli = []
    for record in row.stimuli or []:
        text = None
        if record.get("blob"):
            try:
                text = blobs.get_text(record["blob"])
            except (OSError, LookupError, ValueError):
                logger.warning("stimulus blob %s of run %s is unreadable", record.get("blob"), run.pk)
        stimuli.append(
            {
                "role": record["role"],
                "template_ref": record["template_ref"],
                "sha": record["sha"],
                "text": text,
                "sources": list(record.get("sources") or []),
            },
        )
    return {"manifest": row.manifest, "manifest_hash": row.manifest_hash, "stimuli": stimuli}


@router.get("/runs/{run_id}/files/", response={200: list[FileOut], 400: ErrorOut, 404: ErrorOut})
def list_run_files(
    request: HttpRequest,
    run_id: UUID,
    scope: Literal["model", "app", "slot"] = "model",
    origin: str | None = None,
) -> Any:
    """The run's files in ``scope`` (slot variants: parent rows overlaid as ``parent`` for ``app``)."""
    run = _get(request, run_id)
    if run is None:
        return _not_found()
    entries = run_view(run).files(scope)
    return [
        {
            "path": e.path,
            "origin": e.origin,
            "size": e.size,
            "loc": e.loc,
            "language": e.language,
            "is_test": e.is_test,
            "excluded": e.excluded,
            "blob": e.blob,
        }
        for e in entries
        if origin is None or e.origin == origin
    ]


_TEXT_PLAIN_200 = {
    "responses": {
        "200": {
            "description": "The file's text (UTF-8, undecodable bytes replaced)",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
    },
}


@router.get(
    "/runs/{run_id}/files/content/",
    response={404: ErrorOut, 415: ErrorOut},
    openapi_extra=_TEXT_PLAIN_200,
)
def get_run_file_content(request: HttpRequest, run_id: UUID, path: str) -> Any:
    """One file's text (app scope, so a variant's overlaid parent files are readable too)."""
    run = _get(request, run_id)
    if run is None:
        return _not_found()
    entry = next((e for e in run_view(run).files("app") if e.path == path), None)
    if entry is None:
        entry = next((e for e in run_view(run).files("model") if e.path == path), None)
    if entry is None or not entry.blob:
        return Status(404, {"detail": "File not found", "code": "not_found"})
    if entry.excluded == "binary":
        return Status(415, {"detail": "Binary file", "code": "binary"})
    try:
        data = blobs.get_bytes(entry.blob)
    except (OSError, LookupError, ValueError):
        return Status(404, {"detail": "File content is missing from the blob store", "code": "blob_missing"})
    return HttpResponse(data.decode("utf-8", errors="replace"), content_type="text/plain; charset=utf-8")


def _attempt_row(run: GenerationRun, attempt: int | None) -> RunAttempt | None:
    rows = run.attempts.all()
    if attempt is not None:
        return rows.filter(attempt=attempt).first()
    return rows.order_by("-attempt").first()


@router.get("/runs/{run_id}/trajectory/", response={200: TrajectoryOut, 404: ErrorOut})
def get_run_trajectory(request: HttpRequest, run_id: UUID, attempt: int | None = None) -> Any:
    """The parsed ``Trajectory`` and ``ObservedInputs`` of one attempt (the last one by default)."""
    run = _get(request, run_id)
    if run is None:
        return _not_found()
    row = _attempt_row(run, attempt)
    if row is None:
        return Status(404, {"detail": "No such attempt", "code": "not_found"})
    trajectory = None
    if row.trajectory_blob:
        try:
            trajectory = blobs.get_json(row.trajectory_blob)
        except (OSError, LookupError, ValueError):
            logger.warning("trajectory blob %s of run %s is unreadable", row.trajectory_blob, run.pk)
    if trajectory is None:
        trajectory = evidence.read_json(run.pk, row.attempt, evidence.TRAJECTORY_FILE)
    return {"attempt": row.attempt, "trajectory": trajectory, "observed_inputs": row.observed_inputs}


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@router.get("/runs/{run_id}/calls/", response={200: list[CallOut], 404: ErrorOut})
def list_run_calls(request: HttpRequest, run_id: UUID, attempt: int | None = None) -> Any:
    """The LLM calls of one attempt (the current one by default), from the per-call artifacts."""
    run = _get(request, run_id)
    if run is None:
        return _not_found()
    number = attempt if attempt is not None else attempt_number(run)
    artifacts = GenerationArtifact.objects.filter(run=run, attempt=number).filter(
        Q(stage__startswith="agent_call_") | Q(stage__startswith="slot_call_"),
    )
    out = []
    for row in artifacts.order_by("stage"):
        telemetry = row.telemetry or {}
        tokens = telemetry.get("tokens") or {}
        duration = _number(telemetry.get("duration_s"))
        out.append(
            {
                "index": int(row.stage.rsplit("_", 1)[-1]),
                "attempt": row.attempt,
                "stage": row.stage,
                "request_class": telemetry.get("request_class"),
                "prompt_tokens": _int_or_none(tokens.get("prompt")),
                "cached_tokens": _int_or_none(tokens.get("cached")),
                "completion_tokens": _int_or_none(tokens.get("completion")),
                "reasoning_tokens": _int_or_none(tokens.get("reasoning")),
                "finish_reason": telemetry.get("finish_reason"),
                "cost": _number(telemetry.get("cost")),
                "list_cost": _number(telemetry.get("list_cost")),
                "provider": telemetry.get("provider"),
                "response_model": telemetry.get("response_model"),
                "ms": round(duration * 1000, 3) if duration is not None else None,
                # Derived on read (services.token_metrics): the end-to-end output rate, never a stored
                # value an older runner copied from the gateway.
                "tokens_per_s": token_metrics.telemetry_tokens_per_s(telemetry),
                "front_protocol": telemetry.get("front_protocol"),
                "protocol_mode": telemetry.get("protocol_mode"),
                "serving_tier": telemetry.get("serving_tier"),
                "translation_dropped": telemetry.get("translation_dropped") or {},
                "status": _int_or_none(telemetry.get("status")),
            },
        )
    return out


@router.get("/runs/{run_id}/evaluations/", response={200: list[RunEvaluationOut], 404: ErrorOut})
def list_run_evaluations(request: HttpRequest, run_id: UUID) -> Any:
    """Every evaluation of the run, newest first (append-only; retests included and marked)."""
    run = _get(request, run_id)
    if run is None:
        return _not_found()
    rows = RunEvaluation.objects.filter(run=run).select_related("eval_suite").order_by("-created_at", "-pk")
    return [_evaluation_out(row) for row in rows]


@router.post("/runs/{run_id}/evaluate/", response={200: EvaluateOut, **_ERRORS})
def evaluate_run(request: HttpRequest, run_id: UUID, payload: EvaluateIn) -> Any:
    """Queue a re-evaluation in the eval lane (a new ``RunEvaluation``; the current run never changes)."""
    run = _get(request, run_id)
    if run is None:
        return _not_found()
    try:
        evaluation = actions.evaluate(run, suite_ref=payload.eval_suite, force_rebuild=payload.force_rebuild)
    except actions.ActionRefused as exc:
        return _refused(exc)
    return {"evaluation_id": evaluation.pk}


@router.post("/runs/{run_id}/retry/", response={200: RetryOut, **_ERRORS})
def retry_run(request: HttpRequest, run_id: UUID) -> Any:
    """A new attempt on the same row (same manifest and seed); only for ``infra_error`` (409 ``not_infra``)."""
    run = _get(request, run_id)
    if run is None:
        return _not_found()
    try:
        run = actions.retry(run)
    except actions.ActionRefused as exc:
        return _refused(exc)
    return {"run_id": run.pk, "attempt": attempt_number(run)}


@router.post("/runs/{run_id}/cancel/", response={200: RunOut, **_ERRORS})
def cancel_run(request: HttpRequest, run_id: UUID) -> Any:
    """Cancel a pending or generating run (an evaluation already queued finishes)."""
    run = _get(request, run_id)
    if run is None:
        return _not_found()
    actions.cancel(run)
    return get_run(request, run_id)


@router.get("/runs/{run_id}/evidence/", response={404: ErrorOut})
def get_run_evidence(request: HttpRequest, run_id: UUID) -> Any:
    """A zip of ``GENERATION_RUNS_ROOT/<run>/`` (every attempt's evidence)."""
    run = _get(request, run_id)
    if run is None:
        return _not_found()
    response = HttpResponse(evidence.zip_run(run.pk), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="run-{run.pk}-evidence.zip"'
    return response
