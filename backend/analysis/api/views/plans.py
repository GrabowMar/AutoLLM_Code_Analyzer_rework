"""Frozen plans in use: which analysis plans an experiment's or batch's jobs were analysed under.

``plan_hash`` equal means comparable (contract rule 4), so before comparing
runs a client needs to know which plans exist for a set of jobs and how
much of the set each plan covers. One aggregate over the visible runs (no
per-run work); the plan JSON is read once per distinct plan for a short
description (profile, tools, reviewer model).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import CharField
from django.db.models import Count
from django.db.models import Max
from django.db.models import Min
from django.db.models import Q
from django.db.models.fields.json import KT
from django.db.models.functions import Cast
from django.db.models.functions import Coalesce
from ninja import Query

from backend.analysis.api import common
from backend.analysis.api.serializers import iso
from backend.analysis.api.views._router import router
from backend.analysis.models import AnalysisRun

MAX_PLANS = 100
_FINISHED = (AnalysisRun.Status.COMPLETED, AnalysisRun.Status.PARTIAL)


def _plan_brief(plan: dict[str, Any]) -> dict[str, Any]:
    plan = plan if isinstance(plan, dict) else {}
    tools = [t.get("slug") for t in plan.get("tools") or [] if isinstance(t, dict) and t.get("slug")]
    reviewer = plan.get("reviewer") if isinstance(plan.get("reviewer"), dict) else {}
    model = reviewer.get("model") if isinstance(reviewer.get("model"), dict) else {}
    profile = plan.get("profile") if isinstance(plan.get("profile"), dict) else {}
    return {
        "profile": {k: profile.get(k) for k in ("id", "slug", "version")} if profile else None,
        "tools": tools,
        "reviewer": {
            "enabled": bool(reviewer.get("enabled")),
            "model": model.get("slug") or model.get("canonical_slug"),
            "samples": reviewer.get("samples"),
        }
        if reviewer
        else None,
        "analyzer_image": (plan.get("analyzer_image") or {}).get("tag"),
    }


def _scope_jobs(user, experiment_id: UUID | None, batch_id: UUID | None) -> int | None:
    """Completed jobs of the experiment / batch you can see (None without a scope)."""
    if experiment_id is None and batch_id is None:
        return None
    from backend.generation.models import GenerationJob

    qs = common.visible_jobs(user, GenerationJob.objects.filter(status=GenerationJob.Status.COMPLETED))
    if experiment_id is not None:
        qs = qs.filter(experiment_id=experiment_id)
    if batch_id is not None:
        qs = qs.filter(batch_id=batch_id)
    return qs.count()


def _post_run_plans(experiment_id: UUID | None) -> set[str]:
    if experiment_id is None:
        return set()
    from backend.generation.models import ExperimentPostRun

    hashes = ExperimentPostRun.objects.filter(experiment_id=experiment_id).values_list(
        "progress__analysis_plan_hash",
        flat=True,
    )
    return {str(h) for h in hashes if h}


@router.get("/plans/", response=dict)
def list_plans(
    request,
    experiment_id: UUID | None = None,
    batch_id: UUID | None = None,
    study_id: UUID | None = None,
    purpose: str = Query("", description="comma-separated purposes (default: all but canary)"),
):
    """Frozen plans of your runs (staff: all), optionally for one experiment, batch or study.

    Per plan: ``runs`` / ``finished_runs`` / ``complete_runs``, ``jobs`` (distinct
    generation jobs with a run) and ``complete_jobs`` (jobs with a complete run),
    ``first_used`` / ``last_used``, profiles and purposes, and a brief of the
    plan. ``scope.jobs_total`` is the experiment's / batch's completed job
    count, so ``jobs / jobs_total`` is a plan's coverage. Newest first,
    at most 100 plans.
    """
    purposes = common.csv_param(purpose)
    if bad := [p for p in purposes if p not in AnalysisRun.Purpose.values]:
        return common.query_error("purpose", f"unknown value(s): {', '.join(bad)}")
    runs = common.visible_runs(request.auth).exclude(plan_hash="")
    runs = runs.filter(purpose__in=purposes) if purposes else runs.exclude(purpose=AnalysisRun.Purpose.CANARY)
    # The target_meta snapshot keeps a run in its experiment / batch after the job is deleted.
    if experiment_id is not None:
        runs = runs.filter(
            Q(generation_job__experiment_id=experiment_id) | Q(target_meta__experiment_id=str(experiment_id)),
        )
    if batch_id is not None:
        runs = runs.filter(Q(generation_job__batch_id=batch_id) | Q(target_meta__batch_id=str(batch_id)))
    if study_id is not None:
        runs = runs.filter(study_id=study_id)
    job_key = Coalesce(Cast("generation_job_id", output_field=CharField()), KT("target_meta__job_id"))
    rows = list(
        runs.order_by()
        .values("plan_hash")
        .annotate(
            runs=Count("id"),
            finished_runs=Count("id", filter=Q(status__in=_FINISHED)),
            complete_runs=Count("id", filter=Q(is_complete=True)),
            jobs=Count(job_key, distinct=True),
            complete_jobs=Count(job_key, distinct=True, filter=Q(is_complete=True)),
            first_used=Min("created_at"),
            last_used=Max("created_at"),
            profiles=ArrayAgg("profile__slug", distinct=True, filter=Q(profile__isnull=False), default=[]),
            purposes=ArrayAgg("purpose", distinct=True, default=[]),
            studies=Count("study_id", distinct=True),
        )
        .order_by("-last_used", "plan_hash")[:MAX_PLANS],
    )
    hashes = [row["plan_hash"] for row in rows]
    plans: dict[str, dict[str, Any]] = {}
    for plan_hash, plan in (
        AnalysisRun.objects.filter(plan_hash__in=hashes)
        .order_by("plan_hash", "-created_at")
        .distinct("plan_hash")
        .values_list("plan_hash", "plan")
    ):
        plans[plan_hash] = plan or {}
    post_run = _post_run_plans(experiment_id)
    items = [
        {
            "plan_hash": row["plan_hash"],
            "runs": row["runs"],
            "finished_runs": row["finished_runs"],
            "complete_runs": row["complete_runs"],
            "jobs": row["jobs"],
            "complete_jobs": row["complete_jobs"],
            "studies": row["studies"],
            "first_used": iso(row["first_used"]),
            "last_used": iso(row["last_used"]),
            "profiles": sorted(row["profiles"]),
            "purposes": sorted(row["purposes"]),
            "experiment_post_run": row["plan_hash"] in post_run,
            **_plan_brief(plans.get(row["plan_hash"], {})),
        }
        for row in rows
    ]
    return {
        "items": items,
        "truncated": len(rows) == MAX_PLANS,
        "scope": {
            "experiment_id": str(experiment_id) if experiment_id else None,
            "batch_id": str(batch_id) if batch_id else None,
            "study_id": str(study_id) if study_id else None,
            "jobs_total": _scope_jobs(request.auth, experiment_id, batch_id),
        },
    }
