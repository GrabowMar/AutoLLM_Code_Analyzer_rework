"""Dispatch hands jobs to the executor and wakes it only after the transaction commits (spec §C.9)."""

from __future__ import annotations

import uuid
from unittest import mock

import pytest
from django.db import transaction

from backend.execution.api import WAKE_KEY
from backend.execution.api import enqueue_jobs
from backend.generation.models import GenerationJob
from backend.generation.services import dispatcher
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _job(**kw):
    kw.setdefault("model", LLMModelFactory(model_id=f"vendor/{uuid.uuid4().hex[:8]}"))
    kw.setdefault("mode", GenerationJob.Mode.SCAFFOLDING)
    return GenerationJob.objects.create(created_by=UserFactory(), **kw)


def test_enqueue_inside_atomic_does_not_wake_before_commit(django_capture_on_commit_callbacks):
    job = _job()
    with mock.patch("backend.execution.api._redis") as redis_factory:
        with django_capture_on_commit_callbacks(execute=False) as callbacks, transaction.atomic():
            enqueue_jobs([job])
            assert not redis_factory.called  # nothing rung inside the transaction
        assert len(callbacks) == 1
        callbacks[0]()
    pipe = redis_factory.return_value.pipeline.return_value
    pipe.lpush.assert_called_once_with(WAKE_KEY, 1)
    pipe.ltrim.assert_called_once_with(WAKE_KEY, 0, 0)
    pipe.expire.assert_called_once_with(WAKE_KEY, 60)


def test_dispatch_job_marks_the_row_executor_owned(django_capture_on_commit_callbacks):
    agent = _job(mode=GenerationJob.Mode.AGENT)
    scaffolding = _job()
    with mock.patch("backend.execution.api.wake") as wake, django_capture_on_commit_callbacks(execute=True):
        dispatcher.dispatch_job(agent)
        dispatcher.enqueue_jobs([scaffolding])
    agent.refresh_from_db()
    scaffolding.refresh_from_db()
    assert agent.queued_at is not None
    assert (agent.lane, scaffolding.lane) == ("sandbox", "llm")
    assert (agent.status, agent.endpoint_id) == (GenerationJob.Status.PENDING, None)
    assert wake.call_count == 2


def test_enqueue_is_idempotent_and_keeps_queued_at_and_sequence():
    job = _job(sequence=7)
    with mock.patch("backend.execution.api.wake"):
        enqueue_jobs([job])
        first = GenerationJob.objects.get(id=job.id).queued_at
        enqueue_jobs([GenerationJob.objects.get(id=job.id)])
    job.refresh_from_db()
    assert (job.queued_at, job.sequence) == (first, 7)


def test_wake_survives_redis_outage():
    from backend.execution.api import wake

    with mock.patch("backend.execution.api._redis", side_effect=ConnectionError("down")):
        wake()  # never raises: the executor's tick still runs


def test_dispatch_evaluation_queues_a_reevaluation():
    job = _job(status=GenerationJob.Status.COMPLETED)
    with mock.patch("backend.execution.api.wake"):
        assert dispatcher.dispatch_evaluation(job) is True
    job.refresh_from_db()
    assert (job.status, job.phase, job.execution["reeval_prev_status"]) == ("running", "eval_queued", "completed")


def test_view_patch_targets_keep_resolving():
    from backend.generation.api.views import custom
    from backend.generation.api.views import jobs

    assert custom.dispatch_job is dispatcher.dispatch_job
    assert callable(custom.has_resolvable_key)
    assert jobs.dispatch_job is dispatcher.dispatch_job
    assert jobs.dispatch_evaluation is dispatcher.dispatch_evaluation
