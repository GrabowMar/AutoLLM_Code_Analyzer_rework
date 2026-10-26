"""Target selection endpoints: resolve a target spec into candidate jobs, and the options to build one.

``/targets/resolve/`` resolves samples only for the requested page (sample
resolution is the expensive part); the full eligibility pass over every
target is the study preflight's job.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from django.db.models import Count
from django.db.models import Q

from backend.analysis.api import common
from backend.analysis.api.schema import TargetResolveSchema  # noqa: TC001 - Ninja resolves it at runtime
from backend.analysis.api.views._router import router
from backend.analysis.engine import preflight as preflight_mod
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.research import study as study_mod


def _summary(jobs: list) -> dict[str, dict[str, int]]:
    """Counts by identity factor over every resolved job (DB fields only, no sample resolution)."""
    counters: dict[str, Counter] = {k: Counter() for k in ("models", "apps", "harnesses", "modes", "stacks")}
    for job in jobs:
        counters["models"][job.model.model_id if job.model_id else "(none)"] += 1
        counters["apps"][job.app_requirement.slug if job.app_requirement_id else "(none)"] += 1
        counters["harnesses"][getattr(job, "harness_name", None) or "(none)"] += 1
        counters["modes"][job.mode] += 1
        counters["stacks"][job.stack_slug or "(none)"] += 1
    return {name: dict(counter.most_common()) for name, counter in counters.items()}


@router.post("/targets/resolve/", response=dict)
def resolve_targets(request, payload: TargetResolveSchema):
    """Candidate jobs for a target spec with eligibility and sample totals (paginated)."""
    try:
        spec = study_mod.validate_target_spec(payload.target_spec)
        rules = study_mod.validate_exclusion_rules(payload.exclusion_rules)
    except ConfigError as exc:
        return common.config_error(exc)
    scope = None
    if payload.profile_id is not None:
        profile = common.visible_profiles(request.auth).filter(id=payload.profile_id).first()
        if profile is None:
            return common.validation_error({"profile_id": "profile not found"})
        scope = (profile.config or {}).get("scope")
    try:
        jobs, notes = study_mod.resolve_targets_detailed(spec, request.auth)
    except ConfigError as exc:
        return common.config_error(exc)
    page_jobs, meta = common.paginate(jobs, payload.page, payload.per_page)
    items = [preflight_mod.describe_job(job, scope=scope, rules=rules)[0] for job in page_jobs]
    return study_mod.json_safe(
        {
            "items": items,
            **meta,
            "eligible_on_page": sum(1 for item in items if item["eligible"]),
            "summary": _summary(jobs),
            "notes": notes,
            "target_spec": spec,
            "exclusion_rules": rules,
        },
    )


def _counts(qs, *fields: str) -> list[dict[str, Any]]:
    return list(qs.order_by().values(*fields).annotate(jobs=Count("id")).order_by("-jobs", *fields))


@router.get("/targets/options/", response=dict)
def target_options(request):
    """Your experiments, batches, models, harnesses, stacks, apps and modes with completed-job counts."""
    from backend.generation.models import Experiment
    from backend.generation.models import ExperimentCondition
    from backend.generation.models import GenerationBatch
    from backend.generation.models import GenerationJob
    from backend.generation.services.job_listing import harness_expression

    user = request.auth
    completed = GenerationJob.Status.COMPLETED
    jobs = GenerationJob.objects.filter(created_by=user, status=completed)
    experiments = list(
        Experiment.objects.filter(created_by=user)
        .annotate(
            total_jobs=Count("jobs", distinct=True),
            completed_jobs=Count("jobs", filter=Q(jobs__status=completed), distinct=True),
        )
        .order_by("-created_at"),
    )
    conditions: dict[Any, list[dict[str, Any]]] = {}
    for condition in ExperimentCondition.objects.filter(experiment__in=experiments).select_related("model"):
        conditions.setdefault(condition.experiment_id, []).append(
            {
                "id": condition.id,
                "label": str(condition),
                "model_id": condition.model.model_id if condition.model_id else None,
                "harness": condition.harness,
            },
        )
    apps_by_experiment: dict[Any, list[str]] = {}
    for experiment_id, slug in (
        jobs.filter(experiment__in=experiments, app_requirement__isnull=False)
        .values_list("experiment_id", "app_requirement__slug")
        .distinct()
        .order_by("experiment_id", "app_requirement__slug")
    ):
        apps_by_experiment.setdefault(experiment_id, []).append(slug)
    batches = (
        GenerationBatch.objects.filter(created_by=user)
        .annotate(
            total=Count("jobs", distinct=True),
            completed=Count("jobs", filter=Q(jobs__status=completed), distinct=True),
        )
        .order_by("-created_at")
    )
    harnesses = (
        jobs.annotate(harness_name=harness_expression())
        .exclude(harness_name__isnull=True)
        .values("harness_name")
        .annotate(jobs=Count("id"))
        .order_by("-jobs", "harness_name")
    )
    return study_mod.json_safe(
        {
            "completed_jobs": jobs.count(),
            "experiments": [
                {
                    "id": e.id,
                    "name": e.name,
                    "status": e.status,
                    "mode": e.mode,
                    "repeats": e.repeats,
                    "total_jobs": e.total_jobs,
                    "completed_jobs": e.completed_jobs,
                    "conditions": conditions.get(e.id, []),
                    "app_slugs": apps_by_experiment.get(e.id, []),
                    "created_at": e.created_at,
                }
                for e in experiments
            ],
            "batches": [
                {
                    "id": b.id,
                    "name": str(b),
                    "status": b.status,
                    "mode": b.mode,
                    "total_jobs": b.total,
                    "completed_jobs": b.completed,
                    "created_at": b.created_at,
                }
                for b in batches
            ],
            "models": [
                {
                    "id": row["model_id"],
                    "model_id": row["model__model_id"],
                    "name": row["model__model_name"],
                    "provider": row["model__provider"],
                    "jobs": row["jobs"],
                }
                for row in _counts(
                    jobs.filter(model__isnull=False),
                    "model_id",
                    "model__model_id",
                    "model__model_name",
                    "model__provider",
                )
            ],
            "harnesses": [{"name": row["harness_name"], "jobs": row["jobs"]} for row in harnesses],
            "stacks": [{"slug": row["stack_slug"], "jobs": row["jobs"]} for row in _counts(jobs, "stack_slug")],
            "apps": [
                {
                    "id": row["app_requirement_id"],
                    "slug": row["app_requirement__slug"],
                    "name": row["app_requirement__name"],
                    "jobs": row["jobs"],
                }
                for row in _counts(
                    jobs.filter(app_requirement__isnull=False),
                    "app_requirement_id",
                    "app_requirement__slug",
                    "app_requirement__name",
                )
            ],
            "modes": [{"mode": row["mode"], "jobs": row["jobs"]} for row in _counts(jobs, "mode")],
        },
    )
