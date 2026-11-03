"""App pool (owner A): candidate runs, adding runs / studies to a task, task-app edits and serialization.

Spec §5.2 + R1/R2/R3:

- **Candidate runs** are COMPLETED app runs (``app_agentic`` and slot variants, never
  ``reference`` runs) the user owns (staff: every run), newest first; ``beyond_plan`` runs
  never. ``current_only`` keeps only the current run of each study cell, the rule of
  ``generation.runs.view.current_runs``: the **first usable** attempt of the cell, else its
  latest attempt (a cell whose current run is not completed offers nothing). Runs outside a
  cell are kept. Each row shows its functional summary (latest evaluation, incl.
  ``deployed``) and whether a RUNNING ``llm-*`` container exists.
- **Adding** (draft only, else 409 ``design_frozen``) takes ``run_ids`` (each must be
  visible to the user, else 404, and completed, else 400) and/or a ``study_id`` (the
  user's own, else 404) whose completed current runs (``current_runs(study, "main")``) are
  added. Runs already in the pool are skipped silently. Each new app gets a light snapshot
  (``snapshot.run_snapshot`` with ``full=False``), its ``group_key`` and a provisional
  ``App NN`` label numbered from the highest existing number + 1. Everything is validated
  before the first write.
- **PATCH** ``is_active`` any time (items follow, see ``items.sync_app_items``), ``label``
  only before freeze; **DELETE** only before freeze (409 ``design_frozen``; deactivate instead).

``EvalTaskApp.job`` is the app's GenerationRun (the FK keeps its v2 attribute name, spec D5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.db.models import Exists
from django.db.models import Max
from django.db.models import OuterRef
from django.db.models import Q

from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models import choices
from backend.human_eval.api.schemas.common import paginate
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.services import counts
from backend.human_eval.services import hosting
from backend.human_eval.services import items as items_service
from backend.human_eval.services import snapshot as snapshot_service
from backend.human_eval.services import studies
from backend.human_eval.services.access import parse_uuid
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.errors import conflict
from backend.human_eval.services.errors import invalid
from backend.human_eval.services.errors import not_found

if TYPE_CHECKING:
    from django.db.models import QuerySet

MAX_LABEL_LENGTH = 40
#: Runs one add call may bring in (a study's current runs included).
MAX_RUNS_PER_ADD = 1000
#: Run kinds that are hostable apps a participant can test.
APP_KINDS = (choices.RunKind.APP_AGENTIC, choices.RunKind.SLOT_SINGLE_SHOT, choices.RunKind.SLOT_AGENT)
#: Relations the snapshot and the run view read (one query for a page of candidates).
RUN_RELATIONS = (
    *snapshot_service.RUN_RELATIONS,
    "task_version__task",
    "stack_profile",
    "style",
    "guidance",
    "harness_preset",
    "cell__unit",
    "manifest",
    "parent_run__stack_profile",
)


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def _is_staff(user: Any) -> bool:
    return bool(getattr(user, "is_staff", False))


def visible_runs(user: Any) -> QuerySet[GenerationRun]:
    """App runs the user may put in a task: their own, every one for staff (never ``beyond_plan``)."""
    qs = GenerationRun.objects.filter(kind__in=APP_KINDS, beyond_plan=False)
    if user is None or not getattr(user, "is_authenticated", False):
        return qs.none()
    if _is_staff(user):
        return qs
    return qs.filter(created_by=user)


def current_attempts(qs: QuerySet[GenerationRun]) -> QuerySet[GenerationRun]:
    """Only the current run of each study cell (the first usable attempt, else the latest); runs outside a cell kept.

    The rule of ``generation.runs.view.current_runs``, as a filter over any run queryset (attempt order is
    ``created_at``).
    """
    cell_runs = GenerationRun.objects.filter(cell_id=OuterRef("cell_id"), beyond_plan=False)
    earlier_usable = cell_runs.filter(usable=True, created_at__lt=OuterRef("created_at"))
    any_usable = cell_runs.filter(usable=True)
    later = cell_runs.filter(created_at__gt=OuterRef("created_at"))
    return qs.filter(
        Q(cell__isnull=True) | (Q(usable=True) & ~Exists(earlier_usable)) | (~Exists(any_usable) & ~Exists(later)),
    )


def owned_study(user: Any, study_id: Any) -> Study:
    """The user's study (staff: any); 404 for unknown, foreign or malformed ids."""
    pk = parse_uuid(study_id)
    qs = Study.objects.all()
    if not _is_staff(user):
        qs = qs.filter(created_by=user)
    study = qs.filter(pk=pk).first() if pk is not None else None
    if study is None:
        msg = "Study not found"
        raise not_found(msg)
    return study


# ---------------------------------------------------------------------------
# Candidate runs
# ---------------------------------------------------------------------------


def _study_ref(run: GenerationRun) -> dict[str, str] | None:
    if run.study_id is None:
        return None
    return {"id": str(run.study_id), "name": run.study.name}


def _condition_ref(run: GenerationRun) -> dict[str, Any] | None:
    if run.condition_id is None:
        return None
    return {"id": run.condition_id, "label": snapshot_service.condition_label(run.condition)}


def running_run_ids(run_ids: list[Any]) -> set[str]:
    """Runs (as str ids) with a RUNNING ``llm-*`` container."""
    from backend.runtime.models import ContainerInstance

    if not run_ids:
        return set()
    return {
        str(run_id)
        for run_id in ContainerInstance.objects.filter(
            generation_job_id__in=run_ids,
            status=ContainerInstance.Status.RUNNING,
            name__startswith="llm-",
        ).values_list("generation_job_id", flat=True)
    }


def serialize_candidate(run: GenerationRun, running: set[str]) -> dict[str, Any]:
    """TS ``CandidateRun``."""
    from backend.generation.runs.view import run_view

    view = run_view(run)
    model = run.model if run.model_id else None
    return {
        "id": view.id,
        "model_name": model.model_name if model is not None else "",
        "model_id": model.model_id if model is not None else "",
        "app_name": view.task.title if view.task is not None else "",
        "app_slug": view.task.slug if view.task is not None else "",
        "study": _study_ref(run),
        "condition": _condition_ref(run),
        "repeat_index": view.repeat_index,
        "kind": view.kind,
        "stack": view.stack.ref if view.stack is not None else None,
        "created_at": run.created_at,
        "functional": snapshot_service.functional_summary(view, view.latest_evaluation()),
        "running": view.id in running,
    }


def candidate_runs(
    user: Any,
    *,
    study_id: str | None = None,
    app_slug: str | None = None,
    model_id: str | None = None,
    search: str | None = None,
    current_only: bool = True,
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    """TS ``Paginated<CandidateRun>``: the user's completed app runs, filtered, newest first."""
    qs = visible_runs(user)
    if current_only:
        qs = current_attempts(qs)
    qs = qs.filter(status=choices.RunStatus.COMPLETED)
    if study_id:
        pk = parse_uuid(study_id)
        qs = qs.filter(study_id=pk) if pk is not None else qs.none()
    if app_slug:
        qs = qs.filter(task_version__task__slug=app_slug)
    if model_id:
        qs = qs.filter(model__model_id=model_id)
    if search and search.strip():
        term = search.strip()
        qs = qs.filter(
            Q(model__model_name__icontains=term)
            | Q(model__model_id__icontains=term)
            | Q(task_version__name__icontains=term)
            | Q(task_version__task__slug__icontains=term)
            | Q(study__name__icontains=term)
            | Q(condition__label__icontains=term),
        )
    qs = qs.select_related(*RUN_RELATIONS).order_by("-created_at", "-id")
    envelope = paginate(qs, page, per_page)
    runs = envelope["items"]
    running = running_run_ids([run.pk for run in runs])
    envelope["items"] = [serialize_candidate(run, running) for run in runs]
    return envelope


# ---------------------------------------------------------------------------
# Adding runs
# ---------------------------------------------------------------------------


def resolve_run_ids(user: Any, run_ids: list[Any] | None, *, path: str = "run_ids") -> list[GenerationRun]:
    """The listed runs, in the given order without repeats; 404 for invisible ones, 400 for unfinished ones."""
    if not run_ids:
        return []
    if not isinstance(run_ids, list):
        msg = "run_ids must be a list"
        raise invalid(msg, [{"path": path, "message": "Expected a list of run ids"}])
    if len(run_ids) > MAX_RUNS_PER_ADD:
        msg = f"At most {MAX_RUNS_PER_ADD} runs can be added at once"
        raise invalid(msg, [{"path": path, "message": msg}])
    wanted: list[Any] = []
    missing: list[dict[str, str]] = []
    for index, raw in enumerate(run_ids):
        pk = parse_uuid(raw)
        if pk is None:
            missing.append({"path": f"{path}.{index}", "message": "Run not found"})
        elif pk not in wanted:
            wanted.append(pk)
    found = {run.pk: run for run in visible_runs(user).filter(pk__in=wanted).select_related(*RUN_RELATIONS)}
    for index, raw in enumerate(run_ids):
        pk = parse_uuid(raw)
        if pk is not None and pk not in found:
            missing.append({"path": f"{path}.{index}", "message": "Run not found"})
    if missing:
        msg = "Run not found"
        raise HumanEvalServiceError(404, msg, "not_found", sorted(missing, key=lambda error: error["path"]))
    unfinished = [
        {"path": f"{path}.{index}", "message": "Only completed runs can be evaluated"}
        for index, raw in enumerate(run_ids)
        if found[parse_uuid(raw)].status != choices.RunStatus.COMPLETED
    ]
    if unfinished:
        msg = "Only completed runs can be added"
        raise invalid(msg, unfinished)
    return [found[pk] for pk in wanted]


def study_runs(user: Any, study: Study, *, current_only: bool = True) -> list[GenerationRun]:
    """The study's completed app runs the user may add (its current runs, ``current_runs(study)``, by default)."""
    from backend.generation.runs.view import current_runs

    qs = visible_runs(user).filter(study=study)
    if current_only:
        qs = qs.filter(pk__in=current_runs(study, stage="main").values("pk"))
    qs = qs.filter(status=choices.RunStatus.COMPLETED)
    runs = list(
        qs.select_related(*RUN_RELATIONS).order_by("condition_id", "cell__unit_id", "repeat_index", "created_at"),
    )
    if len(runs) > MAX_RUNS_PER_ADD:
        msg = f"At most {MAX_RUNS_PER_ADD} runs can be added at once"
        raise invalid(msg, [{"path": "study_id", "message": msg}])
    return runs


def ensure_draft(task: EvalTask) -> None:
    """409 ``design_frozen`` once the task was opened (the pool and design are frozen)."""
    if task.is_frozen or task.status != EvalTask.Status.DRAFT:
        msg = "The app pool is frozen once the task has been opened"
        raise conflict(msg, "design_frozen")


def create_task_apps(task: EvalTask, runs: list[GenerationRun]) -> list[EvalTaskApp]:
    """Add ``runs`` to the pool (skipping runs already in it): light snapshot, group key, provisional label."""
    existing = set(EvalTaskApp.objects.filter(task=task, job__isnull=False).values_list("job_id", flat=True))
    new_runs = [run for run in runs if run.pk not in existing]
    if not new_runs:
        return []
    labels = items_service.provisional_labels(task, len(new_runs))
    top = EvalTaskApp.objects.filter(task=task).aggregate(top=Max("position"))["top"]
    start = 0 if top is None else top + 1
    apps = []
    for offset, (run, label) in enumerate(zip(new_runs, labels, strict=True)):
        app = EvalTaskApp(task=task, job=run, label=label, position=start + offset)
        app.snapshot = snapshot_service.run_snapshot(run, full=False)
        app.group_key = snapshot_service.group_key(app.snapshot, app.id)
        apps.append(app)
    return EvalTaskApp.objects.bulk_create(apps)


def add_runs(
    task: EvalTask,
    user: Any,
    *,
    run_ids: list[Any] | None = None,
    study_id: Any = None,
    current_only: bool = True,
) -> list[EvalTaskApp]:
    """Add runs and/or a study's (current) completed runs to a draft task's pool; returns the new apps.

    Validates everything before writing. A task without a study is linked to the added one.
    """
    ensure_draft(task)
    if not run_ids and not study_id:
        msg = "Choose runs or a study to add"
        raise invalid(msg, [{"path": "run_ids", "message": msg}])
    runs = resolve_run_ids(user, run_ids)
    study = owned_study(user, study_id) if study_id else None
    if study is not None:
        known = {run.pk for run in runs}
        runs += [run for run in study_runs(user, study, current_only=current_only) if run.pk not in known]
    with transaction.atomic():
        created = create_task_apps(task, runs)
        if study is not None and task.study_id is None:
            studies.link_study(task, study)
            task.save(update_fields=["study", "updated_at"])
    return created


# ---------------------------------------------------------------------------
# Task apps
# ---------------------------------------------------------------------------


def _apps_qs() -> QuerySet[EvalTaskApp]:
    """Task apps with their task, run and container."""
    return EvalTaskApp.objects.select_related("task", "job", "container")


def task_app_or_404(task: EvalTask, app_id: Any) -> EvalTaskApp:
    """An app of this task (404 for apps of other tasks and malformed ids)."""
    pk = parse_uuid(app_id)
    app = _apps_qs().filter(task=task, pk=pk).first() if pk is not None else None
    if app is None:
        msg = "App not found"
        raise not_found(msg)
    return app


def serialize_task_app(app: EvalTaskApp, stats: dict[str, Any]) -> dict[str, Any]:
    """TS ``TaskApp`` (de-blinded: researchers only)."""
    snap = app.snapshot or {}
    functional = snap.get("functional") or {}
    entry = stats.get(str(app.id)) or {"submitted": 0, "in_progress": 0, "skipped": 0, "unavailable_skips": 0}
    return {
        "id": str(app.id),
        "label": app.label,
        "run_id": str(app.job_id) if app.job_id else None,
        "group_key": app.group_key,
        "position": app.position,
        "is_active": app.is_active,
        "model_name": snap.get("model_name") or "",
        "model_id": snap.get("model_id") or "",
        "condition_label": snap.get("condition_label") or "",
        "app_name": snap.get("app_name") or "",
        "app_slug": snap.get("app_slug") or "",
        "repeat_index": snap.get("repeat_index"),
        "functional": {
            "status": functional.get("status"),
            "pass_rate": functional.get("pass_rate"),
            "deployed": functional.get("deployed"),
            "source": functional.get("source"),
        },
        "hosting": hosting.researcher_hosting_info(app),
        "stats": {
            "submitted": entry["submitted"],
            "in_progress": entry["in_progress"],
            "skipped": entry["skipped"],
            "unavailable_skips": entry["unavailable_skips"],
        },
    }


def list_task_apps(task: EvalTask) -> list[dict[str, Any]]:
    """Every app of the task as TS ``TaskApp``, by position."""
    stats = counts.app_stats(task)
    apps = _apps_qs().filter(task=task).order_by("position", "id")
    return [serialize_task_app(app, stats) for app in apps]


def update_task_app(task: EvalTask, app: EvalTaskApp, data: dict[str, Any]) -> EvalTaskApp:
    """PATCH ``{is_active?, label?}``: ``is_active`` any time (items follow), ``label`` only before freeze."""
    is_active = data.get("is_active")
    label = data.get("label")
    if "is_active" in data and not isinstance(is_active, bool):
        msg = "is_active must be true or false"
        raise invalid(msg, [{"path": "is_active", "message": msg}])
    if "label" in data and label is not None and label != app.label:
        ensure_draft(task)
        label = str(label).strip()
        if not label or len(label) > MAX_LABEL_LENGTH:
            msg = f"Labels are 1 to {MAX_LABEL_LENGTH} characters"
            raise invalid(msg, [{"path": "label", "message": msg}])
        if EvalTaskApp.objects.filter(task=task, label=label).exclude(pk=app.pk).exists():
            msg = "Another app of this task already has this label"
            raise invalid(msg, [{"path": "label", "message": msg}])
    else:
        label = None
    with transaction.atomic():
        fields: list[str] = []
        if label is not None:
            app.label = label
            fields.append("label")
        if "is_active" in data and is_active != app.is_active:
            app.is_active = is_active
            fields.append("is_active")
        if fields:
            app.save(update_fields=fields)
            if "is_active" in fields:
                items_service.sync_app_items(app)
    return app


def delete_task_app(task: EvalTask, app: EvalTaskApp) -> None:
    """Remove an app from a draft task's pool (409 ``design_frozen`` after open: deactivate it instead)."""
    if task.is_frozen or task.status != EvalTask.Status.DRAFT:
        msg = "Apps cannot be removed once the task has been opened; deactivate the app instead"
        raise conflict(msg, "design_frozen")
    with transaction.atomic():
        # A draft's app can be started (and pinned): stop what it started before the row disappears.
        hosting.release_apps([app])
        app.delete()
