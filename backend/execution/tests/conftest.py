"""Shared fixtures for the execution engine tests: endpoints, live relay sessions, executor-owned jobs."""

from __future__ import annotations

import itertools
import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from backend.execution.breaker import breaker
from backend.execution.capacity import EffectiveSlots
from backend.generation.models import Experiment
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import ExperimentFactory
from backend.inference.access import system_openrouter
from backend.inference.models import LLMEndpoint
from backend.inference.models import RelaySession
from backend.inference.models import RelayToken
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

_counter = itertools.count()


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


def running_experiment(user, **kw) -> Experiment:
    kw.setdefault("status", Experiment.Status.RUNNING)
    kw.setdefault("launched_at", timezone.now())
    return ExperimentFactory(created_by=user, **kw)


def make_job(user, model=None, *, lane="llm", experiment=None, sequence=None, **kw) -> GenerationJob:
    """An executor-owned PENDING job (``queued_at`` set), with the frozen endpoint section for local models."""
    bundle = dict(kw.pop("resolved_bundle", {}) or {})
    if model is not None and model.endpoint_id and not model.endpoint.is_system:
        bundle.setdefault(
            "endpoint",
            {"slug": model.endpoint.slug, "served_model_name": model.served_model_name, "digest": model.digest},
        )
    kw.setdefault("queued_at", timezone.now() - timedelta(seconds=1))
    kw.setdefault("mode", GenerationJob.Mode.AGENT if lane == "sandbox" else GenerationJob.Mode.SCAFFOLDING)
    return GenerationJob.objects.create(
        created_by=user,
        model=model,
        endpoint=model.endpoint if model is not None and model.endpoint_id else None,
        lane=lane,
        experiment=experiment,
        sequence=sequence,
        resolved_bundle=bundle,
        **kw,
    )


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def system_ep(db):
    return system_openrouter()
