"""Shared fixtures for the execution engine tests: endpoints, live relay sessions, studies and executor-owned runs."""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from django.utils import timezone

from backend.execution.breaker import breaker
from backend.execution.capacity import EffectiveSlots
from backend.generation.models import GenerationRun
from backend.generation.models import ResourcePolicy
from backend.generation.models import SlotTarget
from backend.generation.models import Study
from backend.generation.models import TaskSpecVersion
from backend.generation.testing.content import resource_policy_content
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import ResourcePolicyFactory
from backend.generation.tests.factories import SlotTargetFactory
from backend.generation.tests.factories import StudyFactory
from backend.generation.tests.factories import TaskSpecVersionFactory
from backend.inference.access import system_openrouter
from backend.inference.models import LLMEndpoint
from backend.inference.models import RelaySession
from backend.inference.models import RelayToken
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

_counter = itertools.count()
_SHARED: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _quiet_side_effects():
    """No Redis traffic from tests: doorbells and SSE publishes are no-ops; a fresh breaker per test."""
    breaker.reset()
    with (
        mock.patch("backend.execution.api.wake"),
        mock.patch("backend.realtime.events.publish"),
    ):
        yield
    breaker.reset()


def slots(**kw) -> EffectiveSlots:
    values = {
        "sandbox_slots": 1,
        "eval_slots": 1,
        "llm_job_threads": 16,
        "post_run_slots": 1,
        "draining": False,
        "defer_post_runs": False,
    }
    values.update(kw)
    return EffectiveSlots(**values)


def free(sandbox=8, eval=4, llm=32) -> dict[str, int]:
    return {"sandbox": sandbox, "eval": eval, "llm": llm}


def make_endpoint(owner, *, online=True, served=(), **kw) -> LLMEndpoint:
    n = next(_counter)
    kw.setdefault("kind", LLMEndpoint.Kind.OLLAMA)
    kw.setdefault("connection", LLMEndpoint.Connection.RELAY)
    kw.setdefault("max_concurrency", 2)
    endpoint = LLMEndpoint.objects.create(slug=f"gpu-{n}-{uuid.uuid4().hex[:4]}", name=f"GPU {n}", owner=owner, **kw)
    if online:
        go_online(endpoint, served)
    return endpoint


def go_online(endpoint: LLMEndpoint, served=(), *, role: str = "active") -> RelaySession:
    token = RelayToken.objects.create(
        endpoint=endpoint,
        prefix=f"llr_{uuid.uuid4().hex[:8]}",
        secret_hash="0" * 64,
    )
    return RelaySession.objects.create(
        endpoint=endpoint,
        token=token,
        relay_version="1.0.0",
        upstream_kind=endpoint.kind,
        capacity=endpoint.max_concurrency,
        role=role,
        last_seen_at=timezone.now(),
        models=[{"name": name, "digest": digest} for name, digest in served],
    )


def make_model(endpoint=None, served: str = "", digest: str = "", **kw):
    n = next(_counter)
    if endpoint is None:
        return LLMModelFactory(model_id=f"vendor/m{n}-{uuid.uuid4().hex[:6]}", **kw)
    return LLMModelFactory(
        model_id=f"local-{endpoint.slug}/{served or n}",
        endpoint=endpoint,
        served_model_name=served or f"m{n}",
        digest=digest,
        **kw,
    )


def _shared(key: str, model: type, make: Callable[[], Any]) -> Any:
    """One object per test for rows every run may share (a task version, a slot target); rebuilt after rollback."""
    obj = _SHARED.get(key)
    if obj is None or not model.objects.filter(pk=obj.pk).exists():
        obj = make()
        _SHARED[key] = obj
    return obj


def shared_task_version() -> TaskSpecVersion:
    return _shared("task_version", TaskSpecVersion, TaskSpecVersionFactory)


def shared_slot_target() -> SlotTarget:
    return _shared("slot_target", SlotTarget, SlotTargetFactory)


def policy_with_infra_retries(n: int) -> ResourcePolicy:
    content = resource_policy_content("standard")
    content["retries"]["infra_retries"] = n
    return ResourcePolicyFactory(content=content)


def running_study(user, *, scheduling: str | None = None, **kw) -> Study:
    """A study in ``running`` state (``launched_at`` set); ``scheduling`` is written past the design mirror."""
    kw.setdefault("status", "running")
    kw.setdefault("launched_at", timezone.now())
    study = StudyFactory(created_by=user, **kw)
    if scheduling is not None:
        Study.objects.filter(pk=study.pk).update(scheduling=scheduling)
        study.scheduling = scheduling
    return study


def make_run(user, model=None, *, lane="llm", study=None, sequence=None, **kw) -> GenerationRun:
    """An executor-owned PENDING run (``queued_at`` set) with the pinned served name of local models.

    ``lane="sandbox"`` makes an ``app_agentic`` run, ``lane="llm"`` a ``slot_single_shot`` variant (of a
    slot target shared within the test), so the lane follows from the kind as in production.
    """
    model = model if model is not None else make_model()
    if model.endpoint_id and not model.endpoint.is_system:
        kw.setdefault("pinned_served_name", model.served_model_name)
        kw.setdefault("pinned_digest", model.digest)
    kw.setdefault("queued_at", timezone.now() - timedelta(seconds=1))
    if lane == "sandbox":
        kw.setdefault("task_version", shared_task_version())
    else:
        kw.setdefault("slot_single_shot", True)
        kw.setdefault("slot_target", shared_slot_target())
    return GenerationRunFactory(
        created_by=user,
        model=model,
        endpoint=model.endpoint if model.endpoint_id else None,
        study=study,
        sequence=sequence,
        **kw,
    )


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def system_ep(db):
    return system_openrouter()
