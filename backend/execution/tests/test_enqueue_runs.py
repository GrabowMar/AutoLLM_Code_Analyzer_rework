"""``enqueue_runs`` hands runs to the executor and wakes it only after commit (Appendix A #12, spec §C.9)."""

from __future__ import annotations

from unittest import mock

import pytest
from django.db import transaction

from backend.execution.api import WAKE_KEY
from backend.execution.api import enqueue_runs
from backend.execution.api import wake
from backend.execution.tests.conftest import make_run
from backend.generation.models import GenerationRun

pytestmark = pytest.mark.django_db


def test_enqueue_inside_atomic_does_not_wake_before_commit(user, django_capture_on_commit_callbacks):
    run = make_run(user, queued_at=None)
    # conftest stubs the doorbell; restore the real one (imported above) so the Redis calls are visible
    with mock.patch("backend.execution.api.wake", wake), mock.patch("backend.execution.api._redis") as redis_factory:
        with django_capture_on_commit_callbacks(execute=False) as callbacks, transaction.atomic():
            assert enqueue_runs([run]) == 1
            assert not redis_factory.called  # nothing rung inside the transaction
        assert len(callbacks) == 1
        callbacks[0]()
    pipe = redis_factory.return_value.pipeline.return_value
    pipe.lpush.assert_called_once_with(WAKE_KEY, 1)
    pipe.ltrim.assert_called_once_with(WAKE_KEY, 0, 0)
    pipe.expire.assert_called_once_with(WAKE_KEY, 60)
    run.refresh_from_db()
    assert run.queued_at is not None


def test_enqueue_is_idempotent_and_keeps_queued_at_and_sequence(user):
    run = make_run(user, queued_at=None, sequence=7)
    with mock.patch("backend.execution.api.wake"):
        enqueue_runs([run])
        first = GenerationRun.objects.get(pk=run.pk).queued_at
        assert first is not None
        enqueue_runs([GenerationRun.objects.get(pk=run.pk)])
        enqueue_runs([run.pk])  # ids work as well as objects
    run.refresh_from_db()
    assert (run.queued_at, run.sequence) == (first, 7)


def test_enqueue_sets_lane_from_kind(user):
    agentic = make_run(user, lane="sandbox", queued_at=None)
    single_shot = make_run(user, lane="llm", queued_at=None)
    assert (agentic.kind, single_shot.kind) == ("app_agentic", "slot_single_shot")
    # a stale lane column is corrected from the kind, not trusted
    GenerationRun.objects.filter(pk=agentic.pk).update(lane="llm")
    GenerationRun.objects.filter(pk=single_shot.pk).update(lane="sandbox")
    with mock.patch("backend.execution.api.wake"):
        assert enqueue_runs([agentic.pk, single_shot.pk]) == 2
    agentic.refresh_from_db()
    single_shot.refresh_from_db()
    assert (agentic.lane, single_shot.lane) == ("sandbox", "llm")


def test_wake_survives_redis_outage():
    with mock.patch("backend.execution.api._redis", side_effect=ConnectionError("down")):
        wake()  # never raises: the executor's tick still runs


def test_wake_survives_redis_error_on_execute():
    with mock.patch("backend.execution.api._redis") as redis_factory:
        redis_factory.return_value.pipeline.return_value.execute.side_effect = ConnectionError("down")
        wake()
