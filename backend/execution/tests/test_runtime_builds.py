"""Preview builds share evaluation capacity and retain crash-safe leases."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from backend.execution import capacity
from backend.execution import runtime_builds
from backend.execution.models import ExecutorInstance
from backend.execution.models import RuntimeBuildLease
from backend.execution.registry import JobRegistry
from backend.execution.scheduler import Scheduler
from backend.execution.tests.conftest import free
from backend.execution.tests.conftest import make_job
from backend.execution.tests.conftest import make_model
from backend.execution.tests.conftest import slots
from backend.execution.workers import WorkerContext
from backend.execution.workers import run_preview_build
from backend.generation.models import GenerationJob
from backend.runtime.models import ContainerAction
from backend.runtime.models import ContainerInstance
from backend.runtime.tests.factories import ContainerActionFactory

pytestmark = pytest.mark.django_db
OWNER = "preview-executor:1:test"


def _preview(**kwargs):
    return ContainerActionFactory(action_type=ContainerAction.ActionType.BUILD, **kwargs)


def _owner():
    return ExecutorInstance.objects.create(
        id=OWNER,
        hostname="test",
        pid=1,
        code_version="test",
        is_leader=True,
        last_seen_at=timezone.now(),
    )


def test_preview_takes_eval_capacity_and_blocks_sandbox(user, system_ep):
    action = _preview()
    sandbox = make_job(user, make_model(), lane="sandbox")
    result = Scheduler(OWNER).tick(slots(exclusive_eval=True), free())
    assert result.preview_builds == [action.pk]
    assert result.claimed == []
    assert result.snapshot.eval_used == 1
    sandbox.refresh_from_db()
    action.refresh_from_db()
    assert sandbox.status == GenerationJob.Status.PENDING
    assert action.status == ContainerAction.Status.RUNNING
    assert RuntimeBuildLease.objects.get(pk=action.pk).owner == OWNER
    assert capacity.snapshot().eval_used == 1


def test_preview_waits_for_existing_sandbox(user, system_ep):
    action = _preview()
    make_job(
        user,
        make_model(),
        lane="sandbox",
        status=GenerationJob.Status.RUNNING,
        phase=GenerationJob.Phase.RUNNING,
        lease_owner=OWNER,
    )
    result = Scheduler(OWNER).tick(slots(exclusive_eval=True), free())
    assert result.preview_builds == []
    action.refresh_from_db()
    assert action.status == ContainerAction.Status.PENDING


def test_generation_evaluation_keeps_precedence(user, system_ep):
    action = _preview()
    waiting = make_job(
        user,
        make_model(),
        status=GenerationJob.Status.RUNNING,
        phase=GenerationJob.Phase.EVAL_QUEUED,
        stage_entered_at=timezone.now(),
    )
    result = Scheduler(OWNER).tick(slots(eval_slots=1), free())
    assert [claim.job_id for claim in result.claimed] == [waiting.pk]
    assert result.preview_builds == []
    action.refresh_from_db()
    assert action.status == ContainerAction.Status.PENDING


def test_preview_respects_free_eval_threads_and_drain(system_ep):
    action = _preview()
    assert Scheduler(OWNER).tick(slots(), free(eval=0)).preview_builds == []
    assert Scheduler(OWNER).tick(slots(draining=True), free()).preview_builds == []
    assert Scheduler(OWNER).tick(slots(), free()).preview_builds == [action.pk]
    assert Scheduler(OWNER).tick(slots(), free()).preview_builds == []


def test_running_analysis_blocks_preview_under_memory_exclusion(system_ep):
    _preview()
    pool = {**free(), "post": 0}
    result = Scheduler(OWNER).tick(slots(defer_post_runs=True, exclusive_eval=True), pool)
    assert result.preview_builds == []


def test_worker_registers_and_releases_capacity():
    action = _preview()
    runtime_builds.claim(OWNER, 1)
    ctx = WorkerContext(OWNER, JobRegistry())

    def execute(action_id, *, claimed, cancelled):
        assert action_id == action.pk
        assert claimed
        assert not cancelled()
        assert ctx.registry.count("preview") == 1
        action.mark_completed()

    with mock.patch("backend.runtime.services.container_service._execute", side_effect=execute):
        run_preview_build(ctx, action.pk)
    assert not RuntimeBuildLease.objects.exists()
    assert len(ctx.registry) == 0
    action.refresh_from_db()
    assert action.status == ContainerAction.Status.COMPLETED


def test_dead_executor_build_is_failed_not_requeued():
    action = _preview()
    runtime_builds.claim("dead-executor", 1)
    assert runtime_builds.recover(OWNER, []) == 1
    action.refresh_from_db()
    action.container.refresh_from_db()
    assert action.status == ContainerAction.Status.FAILED
    assert "interrupted" in action.error_message
    assert action.container.status == ContainerInstance.Status.FAILED
    assert not RuntimeBuildLease.objects.exists()


def test_registered_worker_keeps_capacity_until_teardown():
    _owner()
    action = _preview()
    runtime_builds.claim(OWNER, 1)
    RuntimeBuildLease.objects.filter(action=action).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert runtime_builds.cancelled(action.pk, OWNER)
    assert runtime_builds.recover(OWNER, [str(action.pk)]) == 0
    assert RuntimeBuildLease.objects.exists()
    runtime_builds.release(action.pk, OWNER)
    assert not RuntimeBuildLease.objects.exists()


def test_heartbeat_renews_only_registered_preview_actions():
    _owner()
    first, second = _preview(), _preview()
    runtime_builds.claim(OWNER, 2)
    past = timezone.now() - timedelta(seconds=1)
    RuntimeBuildLease.objects.update(expires_at=past)
    assert runtime_builds.renew(OWNER, [str(first.pk)]) == 1
    assert not runtime_builds.cancelled(first.pk, OWNER)
    assert runtime_builds.cancelled(second.pk, OWNER)


def test_larger_build_cap_is_included_in_memory_rule(settings):
    settings.SAMPLE_BUILD_MEMORY = "3g"
    settings.EVAL_SLOT_MEM = "1536m"
    assert capacity._terms()["eval"] == 3 * 1024**3


def test_cached_start_is_durable_without_consuming_eval_capacity(system_ep):
    action = _preview()
    owner = runtime_builds.claim_cached_start(action)
    assert owner.startswith(runtime_builds.CACHED_START_PREFIX)
    assert RuntimeBuildLease.objects.filter(action=action).exists()
    assert capacity.snapshot().eval_used == 0
    # A web startup is not an ExecutorInstance, so only its expiry decides
    # whether it died; ordinary dead-executor recovery must leave it alone.
    assert runtime_builds.recover(OWNER, []) == 0
    action.refresh_from_db()
    assert action.status == ContainerAction.Status.RUNNING


def test_dead_cached_start_is_failed_when_its_lease_expires():
    action = _preview()
    runtime_builds.claim_cached_start(action)
    RuntimeBuildLease.objects.filter(action=action).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert runtime_builds.recover(OWNER, []) == 1
    action.refresh_from_db()
    action.container.refresh_from_db()
    assert action.status == ContainerAction.Status.FAILED
    assert action.container.status == ContainerInstance.Status.FAILED
    assert not RuntimeBuildLease.objects.exists()


def test_cached_start_callback_renews_but_never_resurrects_expired_lease():
    action = _preview()
    owner = runtime_builds.claim_cached_start(action)
    soon = timezone.now() + timedelta(seconds=1)
    RuntimeBuildLease.objects.filter(action=action).update(expires_at=soon)
    assert not runtime_builds.cached_start_cancelled(action.pk, owner)
    assert RuntimeBuildLease.objects.get(action=action).expires_at > soon
    RuntimeBuildLease.objects.filter(action=action).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert runtime_builds.cached_start_cancelled(action.pk, owner)


def test_cached_worker_releases_lease_after_startup():
    action = _preview()
    owner = runtime_builds.claim_cached_start(action)
    built = SimpleNamespace(image_id="sha256:cached")

    def execute(action_id, *, claimed, cancelled, cached_build):
        assert action_id == action.pk
        assert claimed
        assert not cancelled()
        assert cached_build is built
        action.mark_completed()

    with mock.patch("backend.runtime.services.container_service._execute", side_effect=execute):
        runtime_builds.run_cached_start(action.pk, owner, built)
    assert not RuntimeBuildLease.objects.exists()
    action.refresh_from_db()
    assert action.status == ContainerAction.Status.COMPLETED


def test_fast_path_never_steals_an_executor_claim():
    action = _preview()
    runtime_builds.claim(OWNER, 1)
    assert runtime_builds.claim_cached_start(action) is None
    assert RuntimeBuildLease.objects.get(action=action).owner == OWNER
