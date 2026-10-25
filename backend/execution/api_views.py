"""/api/execution/ — capacity, queue, settings (spec §F.4).

Everything here is read from the database (the executor's own in-memory
state, such as open circuits and per-endpoint blocked reasons, is published
into its ``ExecutorInstance.config`` by the heartbeat). Endpoints are
filtered to what the caller may see; other tenants' models only ever appear
as counts.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections import defaultdict
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.db.models import Max
from django.db.models import Min
from django.db.models import Q
from django.utils import timezone
from ninja import Query
from ninja import Router
from ninja.errors import HttpError

from backend.execution import capacity
from backend.execution.api import wake
from backend.execution.api_schemas import CapacityOut
from backend.execution.api_schemas import ExecutorSettingsOut
from backend.execution.api_schemas import ExecutorSettingsPatch
from backend.execution.api_schemas import MemoryRuleError
from backend.execution.api_schemas import QueueOut
from backend.execution.models import ExecutorInstance
from backend.execution.models import ExecutorSettings
from backend.execution.scheduler import Scheduler
from backend.generation.models import Experiment
from backend.generation.models import ExperimentPostRun
from backend.generation.models import GenerationJob

router = Router(tags=["execution"])

LEADER_STALE_S = 90
THROUGHPUT_WINDOW_S = 1800
S = GenerationJob.Status


def _require_staff(request) -> None:
    if not getattr(request.auth, "is_staff", False):
        raise HttpError(403, "Staff only")


def _leader() -> ExecutorInstance | None:
    leader = ExecutorInstance.objects.filter(is_leader=True, stopped_at__isnull=True).order_by("-last_seen_at").first()
    if leader is None or leader.last_seen_at < timezone.now() - timedelta(seconds=LEADER_STALE_S):
        return None
    return leader


def _settings_out(row: ExecutorSettings) -> dict[str, Any]:
    return {
        "sandbox_slots": row.sandbox_slots,
        "eval_slots": row.eval_slots,
        "llm_job_threads": row.llm_job_threads,
        "post_run_slots": row.post_run_slots,
        "draining": row.draining,
    }


def _eta(remaining: int, done_recently: int) -> float | None:
    if remaining <= 0:
        return 0.0
    if done_recently <= 0:
        return None
    return round(remaining / (done_recently / THROUGHPUT_WINDOW_S), 1)


@router.get("/capacity/", response=CapacityOut)
def get_capacity(request):
    from backend.inference.access import is_endpoint_owner
    from backend.inference.access import visible_endpoints
    from backend.inference.models import LLMRequest

    user = request.auth
    row = ExecutorSettings.get()
    slots = capacity.effective_slots(row)
    snap = capacity.snapshot()
    leader = _leader()
    config = (leader.config if leader is not None else {}) or {}
    open_circuits = {int(ep) for ep in config.get("circuit_open") or []}
    blocked = {int(k): v for k, v in (config.get("blocked_by_endpoint") or {}).items()}
    running_jobs = GenerationJob.objects.exclude(lease_owner="").count()
    post_used = ExperimentPostRun.objects.filter(status=ExperimentPostRun.Status.RUNNING).count()

    requests = defaultdict(Counter)
    for ep_id, status, n in (
        LLMRequest.objects.filter(status__in=[LLMRequest.Status.QUEUED, LLMRequest.Status.LEASED])
        .order_by()
        .values_list("endpoint_id", "status")
        .annotate(n=Count("id"))
    ):
        requests[ep_id][status] = n
    models_by_ep_user: dict[int, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for ep_id, user_id, served, model_name in (
        GenerationJob.objects.filter(status=S.RUNNING, phase=GenerationJob.Phase.RUNNING, queued_at__isnull=False)
        .exclude(endpoint_id=None)
        .values_list(
            "endpoint_id",
            "created_by_id",
            "resolved_bundle__endpoint__served_model_name",
            "model__model_name",
        )
        .distinct()
    ):
        models_by_ep_user[ep_id][user_id].add(served or model_name or "")

    endpoints = []
    for ep in visible_endpoints(user).order_by("-is_system", "name"):
        state = snap.endpoints.get(ep.pk)
        if state is None:
            continue
        own_models = sorted(m for m in models_by_ep_user[ep.pk].get(user.pk, set()) if m)
        others = {m for uid, ms in models_by_ep_user[ep.pk].items() if uid != user.pk for m in ms}
        active = snap.ep_active.get(ep.pk, 0)
        mine = snap.ep_user_active.get((ep.pk, user.pk), 0)
        if not ep.enabled:
            status = "disabled"
        elif not state.online:
            status = "offline"
        elif ep.pk in open_circuits:
            status = "degraded"
        else:
            status = "online"
        owner_view = is_endpoint_owner(user, ep)
        endpoints.append(
            {
                "id": ep.pk,
                "slug": ep.slug,
                "name": ep.name,
                "kind": ep.kind,
                "connection": ep.connection,
                "online": state.online and ep.enabled,
                "status": status,
                "slots": {"used": requests[ep.pk][LLMRequest.Status.LEASED], "total": ep.max_concurrency},
                "jobs": {"used": active, "total": state.job_cap},
                "held_by_others": max(0, active - mine),
                "queued_requests": requests[ep.pk][LLMRequest.Status.QUEUED],
                "resident_models": own_models if not ep.is_system else [],
                "resident_others": len(others - set(own_models)) if not ep.is_system else 0,
                "resident_limit": ep.max_resident_models,
                "loaded_count": len(state.loaded) if (owner_view or not ep.is_system) else 0,
                "circuit": "open" if ep.pk in open_circuits else "closed",
                "blocked_reason": blocked.get(ep.pk),
            },
        )

    experiments_qs = Experiment.objects.filter(status__in=[Experiment.Status.RUNNING, Experiment.Status.PAUSED])
    if not user.is_staff:
        experiments_qs = experiments_qs.filter(created_by=user)
    experiments = list(experiments_qs.order_by("-priority", "launched_at")[:50])
    since = timezone.now() - timedelta(seconds=THROUGHPUT_WINDOW_S)
    stats = {
        r["experiment_id"]: r
        for r in GenerationJob.objects.filter(experiment__in=experiments)
        .order_by()
        .values("experiment_id")
        .annotate(
            running=Count("id", filter=Q(status=S.RUNNING)),
            pending=Count("id", filter=Q(status=S.PENDING)),
            recent=Count("id", filter=Q(status__in=[S.COMPLETED, S.FAILED], completed_at__gte=since)),
        )
    }
    experiment_rows = []
    for experiment in experiments:
        st = stats.get(experiment.id, {})
        running, pending = st.get("running", 0), st.get("pending", 0)
        experiment_rows.append(
            {
                "id": experiment.id,
                "name": experiment.name,
                "status": experiment.status,
                "priority": experiment.priority,
                "running": running,
                "pending": pending,
                "eta_seconds": _eta(running + pending, st.get("recent", 0)),
            },
        )

    warnings = list(dict.fromkeys([*slots.warnings, *(config.get("warnings") or [])]))
    return {
        "executor": {
            "online": leader is not None,
            "leader": (
                {
                    "id": leader.id,
                    "started_at": leader.started_at,
                    "last_seen_at": leader.last_seen_at,
                    "code_version": leader.code_version,
                }
                if leader is not None
                else None
            ),
            "code_stale": bool(leader is not None and leader.code_version != capacity.code_version()),
            "draining": row.draining,
            "draining_remaining": {"jobs": running_jobs, "post_runs": post_used},
        },
        "settings": {k: v for k, v in _settings_out(row).items() if k != "draining"},
        "limits": {
            "apps_dind_mem": settings.APPS_DIND_MEM_LIMIT,
            "max_sandbox_slots_allowed": capacity.max_sandbox_slots_allowed(slots.eval_slots),
            "warnings": warnings,
        },
        "lanes": {
            "sandbox": {"used": snap.sandbox_used, "total": slots.sandbox_slots},
            "eval": {"used": snap.eval_used, "total": slots.eval_slots},
            "llm": {"used": snap.llm_used, "total": slots.llm_job_threads},
            "post": {"used": post_used, "total": slots.post_run_slots},
        },
        "endpoints": endpoints,
        "experiments": experiment_rows,
    }


@router.get("/queue/", response=QueueOut)
def get_queue(
    request,
    experiment_id: str = Query(""),
    endpoint: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    """The caller's next pending jobs in scheduler order, with why each lane waits (simulated, no locks)."""
    from backend.inference.access import system_openrouter
    from backend.inference.models import LLMEndpoint

    user = request.auth
    row = ExecutorSettings.get()
    slots = capacity.effective_slots(row)
    leader = _leader()
    config = (leader.config if leader is not None else {}) or {}
    open_circuits = {int(ep) for ep in config.get("circuit_open") or []}
    pool_free = {"sandbox": slots.sandbox_slots, "eval": slots.eval_slots, "llm": slots.llm_job_threads}
    plan = Scheduler("dry-run").tick(slots, pool_free, dry_run=True, open_circuits=open_circuits)
    admitted = {str(i) for i in plan.admitted_order}
    reasons = {key: state.reason for key, state in plan.lanes.items()}
    system_ep = system_openrouter().pk

    qs = GenerationJob.objects.filter(status=S.PENDING, queued_at__isnull=False)
    if not user.is_staff:
        qs = qs.filter(created_by=user)
    if experiment_id:
        try:
            qs = qs.filter(experiment_id=uuid.UUID(experiment_id))
        except ValueError as exc:
            raise HttpError(400, "experiment_id must be a UUID") from exc
    if endpoint:
        ep = LLMEndpoint.objects.filter(slug=endpoint).first()
        if ep is None:
            raise HttpError(404, "Unknown endpoint")
        qs = qs.filter(endpoint__isnull=True) if ep.is_system else qs.filter(endpoint=ep)
    jobs = list(
        qs.select_related("model", "endpoint")
        .defer("result_data", "resolved_bundle", "custom_system_prompt", "custom_user_prompt")
        .order_by("-priority", "sequence", "queued_at", "id")[: limit * 4],
    )
    now = timezone.now()
    items = []
    for job in jobs:
        ep_id = job.endpoint_id or system_ep
        key = (str(job.experiment_id) if job.experiment_id else f"adhoc:{job.created_by_id}", ep_id)
        if str(job.id) in admitted:
            reason = None
        elif job.run_after and job.run_after > now:
            reason = "retry_backoff"
        else:
            reason = reasons.get(key)
        items.append(
            {
                "job_id": job.id,
                "experiment_id": job.experiment_id,
                "condition_id": job.condition_id,
                "model_name": job.model.model_name if job.model else "",
                "endpoint_slug": job.endpoint.slug if job.endpoint else "openrouter",
                "lane": job.lane,
                "sequence": job.sequence,
                "priority": job.priority,
                "queued_at": job.queued_at,
                "run_after": job.run_after,
                "attempt": job.attempt,
                "blocked_reason": reason,
            },
        )
    items.sort(key=lambda i: (i["blocked_reason"] is not None, -i["priority"], i["sequence"] or 0, i["queued_at"]))
    items = items[:limit]

    others_ahead: dict[str, int] = {}
    mine = (
        GenerationJob.objects.filter(status=S.PENDING, queued_at__isnull=False, created_by=user)
        .order_by()
        .values("endpoint_id")
        .annotate(first=Min("queued_at"), top=Max("priority"))
    )
    slugs = dict(LLMEndpoint.objects.values_list("id", "slug"))
    for entry in mine:
        ep_filter = Q(endpoint_id=entry["endpoint_id"]) if entry["endpoint_id"] else Q(endpoint_id__isnull=True)
        ahead = (
            GenerationJob.objects.filter(ep_filter, status=S.PENDING, queued_at__isnull=False)
            .exclude(created_by=user)
            .filter(Q(priority__gt=entry["top"]) | Q(priority=entry["top"], queued_at__lt=entry["first"]))
            .count()
        )
        others_ahead[slugs.get(entry["endpoint_id"], "openrouter")] = ahead
    return {"items": items, "others_ahead": others_ahead}


@router.get("/settings/", response=ExecutorSettingsOut)
def get_settings(request):
    _require_staff(request)
    return _settings_out(ExecutorSettings.get())


@router.patch("/settings/", response={200: ExecutorSettingsOut, 400: MemoryRuleError})
def patch_settings(request, payload: ExecutorSettingsPatch):
    """Change slot counts or drain; refused with the arithmetic when the apps-dind memory rule breaks."""
    _require_staff(request)
    row = ExecutorSettings.get()
    changes = payload.dict(exclude_unset=True, exclude_none=True)
    merged = {**_settings_out(row), **changes}
    check = capacity.check_memory(merged["sandbox_slots"], merged["eval_slots"], merged["post_run_slots"])
    if not check.ok_without_post and not capacity.minimal_slots(merged["sandbox_slots"], merged["eval_slots"]):
        return 400, {
            "detail": "These slots do not fit in apps-dind's memory.",
            "arithmetic": check.arithmetic,
            "mem_limit": settings.APPS_DIND_MEM_LIMIT,
            "required": capacity.fmt_mem(check.required),
        }
    for name, value in changes.items():
        setattr(row, name, value)
    row.updated_by = request.auth
    row.save()
    transaction.on_commit(wake)  # after commit, so the executor's next tick reads the new row
    return 200, _settings_out(row)
