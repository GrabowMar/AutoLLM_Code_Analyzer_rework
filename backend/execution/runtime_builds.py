"""Durable preview builds sharing the executor's evaluation slots."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from backend.execution.models import ExecutorInstance
from backend.execution.models import RuntimeBuildLease
from backend.runtime.models import ContainerAction
from backend.runtime.models import ContainerInstance

logger = logging.getLogger(__name__)
CACHED_START_PREFIX = "cached:"
CACHED_START_LEASE_S = 600


def claim_cached_start(action: ContainerAction) -> str | None:
    """Track a cache-only startup without charging an image-building slot.

    The executor may have claimed the initially pending action first. In that
    case keep its ownership rather than creating a second startup thread.
    """
    with transaction.atomic():
        current = ContainerAction.objects.select_for_update().get(pk=action.pk)
        if current.status != ContainerAction.Status.PENDING:
            action.status = current.status
            return None
        action.mark_running()
        owner = CACHED_START_PREFIX + uuid.uuid4().hex
        RuntimeBuildLease.objects.create(
            action=action,
            owner=owner,
            expires_at=timezone.now() + timedelta(seconds=CACHED_START_LEASE_S),
        )
        return owner


def cached_start_cancelled(action_id, owner: str) -> bool:
    """Readiness checks renew only a still-live, still-owned startup lease."""
    now = timezone.now()
    updated = RuntimeBuildLease.objects.filter(
        action_id=action_id,
        owner=owner,
        expires_at__gt=now,
        action__status=ContainerAction.Status.RUNNING,
    ).update(expires_at=now + timedelta(seconds=CACHED_START_LEASE_S))
    return not updated


def run_cached_start(action_id, owner: str, cached_build) -> None:
    """Start the reserved image; a dead web process leaves a recoverable lease."""
    from backend.execution.workers import close_db
    from backend.runtime.services import container_service

    try:
        if cached_start_cancelled(action_id, owner):
            submission_failed(action_id, owner, "cached preview startup lease expired")
            return
        container_service._execute(  # noqa: SLF001 — the lease owns this cached startup
            action_id,
            claimed=True,
            cancelled=lambda: cached_start_cancelled(action_id, owner),
            cached_build=cached_build,
        )
    except Exception:
        logger.exception("Cached preview startup %s failed", action_id)
        submission_failed(action_id, owner, "cached preview startup worker exited unexpectedly")
    finally:
        try:
            release(action_id, owner)
        finally:
            close_db()


def claim(owner: str, limit: int) -> list:
    """Claim pending BUILD actions; the leader decides the available slots."""
    if limit <= 0:
        return []
    with transaction.atomic():
        actions = list(
            ContainerAction.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(action_type=ContainerAction.ActionType.BUILD, status=ContainerAction.Status.PENDING)
            .exclude(runtimebuildlease__isnull=False)
            .order_by("created_at", "id")[:limit],
        )
        expires = timezone.now() + timedelta(seconds=settings.EXECUTOR_LEASE_SECONDS)
        for action in actions:
            action.mark_running()
            RuntimeBuildLease.objects.create(action=action, owner=owner, expires_at=expires)
        return [action.pk for action in actions]


def cancelled(action_id, owner: str) -> bool:
    """A lost lease cancels Docker work before its worker releases capacity."""
    return not RuntimeBuildLease.objects.filter(
        action_id=action_id,
        owner=owner,
        expires_at__gt=timezone.now(),
        action__status=ContainerAction.Status.RUNNING,
    ).exists()


def renew(owner: str, action_ids: list[str]) -> int:
    if not action_ids:
        return 0
    return RuntimeBuildLease.objects.filter(owner=owner, action_id__in=action_ids).update(
        expires_at=timezone.now() + timedelta(seconds=settings.EXECUTOR_LEASE_SECONDS),
    )


def recover(owner: str, registered: list[str]) -> int:
    """Fail interrupted previews rather than starting a second copy of them.

    A local live worker retains its lease until finally; its cancellation
    callback observes the expiry and stops Docker. Dead executors and workers
    that never started cannot renew their leases and release capacity here.
    """
    now = timezone.now()
    live_owners = set(
        ExecutorInstance.objects.filter(
            stopped_at__isnull=True,
            last_seen_at__gt=now - timedelta(seconds=settings.EXECUTOR_LEASE_SECONDS),
        ).values_list("id", flat=True),
    )
    expired = RuntimeBuildLease.objects.filter(expires_at__lte=now)
    dead = RuntimeBuildLease.objects.exclude(owner__in=live_owners).exclude(owner__startswith=CACHED_START_PREFIX)
    candidates = expired | dead
    if registered:
        candidates = candidates.exclude(owner=owner, action_id__in=registered)
    recovered = 0
    for lease_id in candidates.values_list("pk", flat=True):
        with transaction.atomic():
            lease = RuntimeBuildLease.objects.select_for_update().filter(pk=lease_id).first()
            if lease is None:
                continue
            # A cached startup can renew after the candidate scan, before we
            # lock its row. Recheck its deadline rather than failing live work.
            if lease.expires_at > now and (lease.owner.startswith(CACHED_START_PREFIX) or lease.owner in live_owners):
                continue
            action = ContainerAction.objects.select_for_update().get(pk=lease.action_id)
            if action.status == ContainerAction.Status.RUNNING:
                action.mark_failed("Preview build or startup interrupted when its worker stopped; rebuild the sample.")
                ContainerInstance.objects.filter(pk=action.container_id).update(status=ContainerInstance.Status.FAILED)
            lease.delete()
            recovered += 1
    return recovered


def release(action_id, owner: str) -> None:
    RuntimeBuildLease.objects.filter(action_id=action_id, owner=owner).delete()


def submission_failed(action_id, owner: str, error: str) -> None:
    with transaction.atomic():
        # Recovery uses the same lease -> action lock order. A late worker
        # must not fail an action whose ownership it has already lost.
        lease = RuntimeBuildLease.objects.select_for_update().filter(action_id=action_id, owner=owner).first()
        if lease is None:
            return
        action = ContainerAction.objects.select_for_update().get(pk=action_id)
        if action.status == ContainerAction.Status.RUNNING:
            action.mark_failed(f"Could not start preview build worker: {error}")
            ContainerInstance.objects.filter(pk=action.container_id).update(status=ContainerInstance.Status.FAILED)
        lease.delete()
