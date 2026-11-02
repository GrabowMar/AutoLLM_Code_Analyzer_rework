"""App pool (owner A): candidate jobs, adding jobs / experiments to a task, task-app edits and serialization.

Spec §5.2 + R1/R2/R3:

- **Candidate jobs** are COMPLETED generation jobs the user owns (staff: every job),
  newest first. ``current_only`` keeps only the newest attempt of each experiment cell
  (experiment, condition, app spec, repeat), exactly like the experiment's Results tab
  (``statistics.services.study.study_jobs``): a cell whose newest attempt is not completed
  offers nothing. Each row shows its functional summary (incl. ``deployed``) and whether a
  RUNNING ``llm-*`` container exists.
- **Adding** (draft only, else 409 ``design_frozen``) takes ``job_ids`` (each must be
  visible to the user, else 404, and completed, else 400) and/or an ``experiment_id`` (the
  user's own, else 404) whose completed (current) attempts are added. Jobs already in the
  pool are skipped silently. Each new app gets a light snapshot (``snapshot.job_snapshot``
  with ``full=False``), its ``group_key`` and a provisional ``App NN`` label numbered from the
  highest existing number + 1. Everything is validated before the first write.
- **PATCH** ``is_active`` any time (items follow, see ``items.sync_app_items``), ``label``
  only before freeze; **DELETE** only before freeze (409 ``design_frozen``; deactivate instead).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.db.models import Exists
from django.db.models import Max
from django.db.models import OuterRef
from django.db.models import Q

from backend.generation.models import Experiment
from backend.generation.models import GenerationJob
from backend.human_eval.api.schemas.common import paginate
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.services import counts
from backend.human_eval.services import hosting
from backend.human_eval.services import items as items_service
from backend.human_eval.services import snapshot as snapshot_service
from backend.human_eval.services.access import parse_uuid
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.errors import conflict
from backend.human_eval.services.errors import invalid
from backend.human_eval.services.errors import not_found

if TYPE_CHECKING:
    from django.db.models import QuerySet

MAX_LABEL_LENGTH = 40
#: Jobs one add call may bring in (an experiment's current attempts included).
MAX_JOBS_PER_ADD = 1000
_JOB_RELATIONS = (
    "model",
    "experiment",
    "app_requirement",
    "condition__model",
    "condition__profile",
    "condition__harness_preset",
)


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def _is_staff(user: Any) -> bool:
    return bool(getattr(user, "is_staff", False))


def visible_jobs(user: Any) -> QuerySet[GenerationJob]:
    """Generation jobs the user may put in a task: their own, every job for staff."""
    qs = GenerationJob.objects.all()
    if user is None or not getattr(user, "is_authenticated", False):
        return qs.none()
    if _is_staff(user):
        return qs
    return qs.filter(created_by=user)


def current_attempts(qs: QuerySet[GenerationJob]) -> QuerySet[GenerationJob]:
    """Only the newest attempt of each experiment cell (jobs outside a cell are kept)."""
    newer_attempt = GenerationJob.objects.filter(
        experiment_id=OuterRef("experiment_id"),
        condition_id=OuterRef("condition_id"),
        app_requirement_id=OuterRef("app_requirement_id"),
        repeat_index=OuterRef("repeat_index"),
        created_at__gt=OuterRef("created_at"),
    )
    # Ad-hoc jobs (no experiment cell) never match: NULL = NULL is not true in SQL.
    return qs.exclude(Exists(newer_attempt))


def owned_experiment(user: Any, experiment_id: Any) -> Experiment:
    """The user's experiment (staff: any); 404 for unknown, foreign or malformed ids."""
    pk = parse_uuid(experiment_id)
    qs = Experiment.objects.all()
    if not _is_staff(user):
        qs = qs.filter(created_by=user)
    experiment = qs.filter(pk=pk).first() if pk is not None else None
    if experiment is None:
        msg = "Experiment not found"
        raise not_found(msg)
    return experiment


# ---------------------------------------------------------------------------
# Candidate jobs
# ---------------------------------------------------------------------------


def _experiment_ref(job: GenerationJob) -> dict[str, str] | None:
    if job.experiment_id is None:
        return None
    return {"id": str(job.experiment_id), "name": job.experiment.name}


def _condition_ref(job: GenerationJob) -> dict[str, Any] | None:
    if job.condition_id is None:
        return None
    from backend.generation.services.experiments import condition_label

    return {"id": job.condition_id, "label": condition_label(job.condition)}


def _app_names(job: GenerationJob) -> tuple[str, str]:
    if job.app_requirement_id is not None:
        return job.app_requirement.name, job.app_requirement.slug
    spec = snapshot_service.spec_dict(job) or {}
    return str(spec.get("name") or ""), str(spec.get("slug") or "")


def running_job_ids(job_ids: list[Any]) -> set[str]:
    """Jobs (as str ids) with a RUNNING ``llm-*`` container."""
    from backend.runtime.models import ContainerInstance

    if not job_ids:
        return set()
    return {
        str(job_id)
        for job_id in ContainerInstance.objects.filter(
            generation_job_id__in=job_ids,
            status=ContainerInstance.Status.RUNNING,
            name__startswith="llm-",
        ).values_list("generation_job_id", flat=True)
    }


def serialize_candidate(job: GenerationJob, running: set[str]) -> dict[str, Any]:
    """TS ``CandidateJob``."""
    app_name, app_slug = _app_names(job)
    model = job.model if job.model_id else None
    return {
        "id": str(job.id),
        "model_name": model.model_name if model is not None else "",
        "model_id": model.model_id if model is not None else "",
        "app_name": app_name,
        "app_slug": app_slug,
        "experiment": _experiment_ref(job),
        "condition": _condition_ref(job),
        "repeat_index": job.repeat_index,
        "mode": job.mode,
        "created_at": job.created_at,
        "functional": snapshot_service.functional_summary(job.metrics),
        "running": str(job.id) in running,
    }


def candidate_jobs(
    user: Any,
    *,
    experiment_id: str | None = None,
    app_slug: str | None = None,
    model_id: str | None = None,
    search: str | None = None,
    current_only: bool = True,
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    """TS ``Paginated<CandidateJob>``: the user's completed jobs, filtered, newest first."""
    qs = visible_jobs(user)
    if current_only:
        qs = current_attempts(qs)
    qs = qs.filter(status=GenerationJob.Status.COMPLETED)
    if experiment_id:
        pk = parse_uuid(experiment_id)
        qs = qs.filter(experiment_id=pk) if pk is not None else qs.none()
    if app_slug:
        qs = qs.filter(app_requirement__slug=app_slug)
    if model_id:
        qs = qs.filter(model__model_id=model_id)
    if search and search.strip():
        term = search.strip()
        qs = qs.filter(
            Q(model__model_name__icontains=term)
            | Q(model__model_id__icontains=term)
            | Q(app_requirement__name__icontains=term)
            | Q(app_requirement__slug__icontains=term)
            | Q(experiment__name__icontains=term)
            | Q(condition__label__icontains=term),
        )
    qs = qs.select_related(*_JOB_RELATIONS).defer("result_data", "resolved_bundle").order_by("-created_at", "-id")
    envelope = paginate(qs, page, per_page)
    jobs = envelope["items"]
    running = running_job_ids([job.id for job in jobs])
    envelope["items"] = [serialize_candidate(job, running) for job in jobs]
    return envelope


# ---------------------------------------------------------------------------
# Adding jobs
# ---------------------------------------------------------------------------


def resolve_job_ids(user: Any, job_ids: list[Any] | None, *, path: str = "job_ids") -> list[GenerationJob]:
    """The listed jobs, in the given order without repeats; 404 for invisible ones, 400 for unfinished ones."""
    if not job_ids:
        return []
    if not isinstance(job_ids, list):
        msg = "job_ids must be a list"
        raise invalid(msg, [{"path": path, "message": "Expected a list of job ids"}])
    if len(job_ids) > MAX_JOBS_PER_ADD:
        msg = f"At most {MAX_JOBS_PER_ADD} jobs can be added at once"
        raise invalid(msg, [{"path": path, "message": msg}])
    wanted: list[Any] = []
    missing: list[dict[str, str]] = []
    for index, raw in enumerate(job_ids):
        pk = parse_uuid(raw)
        if pk is None:
            missing.append({"path": f"{path}.{index}", "message": "Job not found"})
        elif pk not in wanted:
            wanted.append(pk)
    found = {
        job.id: job
        for job in visible_jobs(user).filter(id__in=wanted).select_related(*_JOB_RELATIONS).defer("result_data")
    }
    for index, raw in enumerate(job_ids):
        pk = parse_uuid(raw)
        if pk is not None and pk not in found:
            missing.append({"path": f"{path}.{index}", "message": "Job not found"})
    if missing:
        msg = "Job not found"
        raise HumanEvalServiceError(404, msg, "not_found", sorted(missing, key=lambda error: error["path"]))
    unfinished = [
        {"path": f"{path}.{index}", "message": "Only completed jobs can be evaluated"}
        for index, raw in enumerate(job_ids)
        if found[parse_uuid(raw)].status != GenerationJob.Status.COMPLETED
    ]
    if unfinished:
        msg = "Only completed jobs can be added"
        raise invalid(msg, unfinished)
    return [found[pk] for pk in wanted]


def experiment_jobs(user: Any, experiment: Experiment, *, current_only: bool = True) -> list[GenerationJob]:
    """The experiment's completed jobs the user may add (its current attempts when ``current_only``)."""
    qs = visible_jobs(user).filter(experiment=experiment)
    if current_only:
        qs = current_attempts(qs)
    qs = qs.filter(status=GenerationJob.Status.COMPLETED)
    jobs = list(
        qs.select_related(*_JOB_RELATIONS)
        .defer("result_data")
        .order_by("condition_id", "app_requirement_id", "repeat_index", "created_at"),
    )
    if len(jobs) > MAX_JOBS_PER_ADD:
        msg = f"At most {MAX_JOBS_PER_ADD} jobs can be added at once"
        raise invalid(msg, [{"path": "experiment_id", "message": msg}])
    return jobs


def ensure_draft(task: EvalTask) -> None:
    """409 ``design_frozen`` once the task was opened (the pool and design are frozen)."""
    if task.is_frozen or task.status != EvalTask.Status.DRAFT:
        msg = "The app pool is frozen once the task has been opened"
        raise conflict(msg, "design_frozen")


def create_task_apps(task: EvalTask, jobs: list[GenerationJob]) -> list[EvalTaskApp]:
    """Add ``jobs`` to the pool (skipping jobs already in it): light snapshot, group key, provisional label."""
    existing = set(EvalTaskApp.objects.filter(task=task, job__isnull=False).values_list("job_id", flat=True))
    new_jobs = [job for job in jobs if job.id not in existing]
    if not new_jobs:
        return []
    labels = items_service.provisional_labels(task, len(new_jobs))
    top = EvalTaskApp.objects.filter(task=task).aggregate(top=Max("position"))["top"]
    start = 0 if top is None else top + 1
    apps = []
    for offset, (job, label) in enumerate(zip(new_jobs, labels, strict=True)):
        app = EvalTaskApp(task=task, job=job, label=label, position=start + offset)
        app.snapshot = snapshot_service.job_snapshot(job, full=False)
        app.group_key = snapshot_service.group_key(app.snapshot, app.id)
        apps.append(app)
    return EvalTaskApp.objects.bulk_create(apps)


def add_jobs(
    task: EvalTask,
    user: Any,
    *,
    job_ids: list[Any] | None = None,
    experiment_id: Any = None,
    current_only: bool = True,
) -> list[EvalTaskApp]:
    """Add jobs and/or an experiment's (current) completed jobs to a draft task's pool; returns the new apps.

    Validates everything before writing. A task without an experiment is linked to the added one.
    """
    ensure_draft(task)
    if not job_ids and not experiment_id:
        msg = "Choose jobs or an experiment to add"
        raise invalid(msg, [{"path": "job_ids", "message": msg}])
    jobs = resolve_job_ids(user, job_ids)
    experiment = owned_experiment(user, experiment_id) if experiment_id else None
    if experiment is not None:
        known = {job.id for job in jobs}
        jobs += [job for job in experiment_jobs(user, experiment, current_only=current_only) if job.id not in known]
    with transaction.atomic():
        created = create_task_apps(task, jobs)
        if experiment is not None and task.experiment_id is None:
            task.experiment = experiment
            task.save(update_fields=["experiment", "updated_at"])
    return created


# ---------------------------------------------------------------------------
# Task apps
# ---------------------------------------------------------------------------


def _apps_qs() -> QuerySet[EvalTaskApp]:
    """Task apps with their task, job and container (the job's large JSON columns deferred)."""
    return EvalTaskApp.objects.select_related("task", "job", "container").defer(
        "job__result_data",
        "job__resolved_bundle",
        "job__metrics",
    )


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
        "job_id": str(app.job_id) if app.job_id else None,
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
