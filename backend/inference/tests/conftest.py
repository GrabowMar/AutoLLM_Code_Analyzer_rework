from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.inference.models import LLMEndpoint
from backend.llm_models.models import LLMModel
from backend.users.tests.factories import UserFactory

# A sha256 that no relay secret hashes to (rows created directly by tests).
UNUSED_HASH = hashlib.sha256(b"no relay secret").hexdigest()


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end tests with real subprocesses (relay, gateway), no Docker")


@pytest.fixture(autouse=True)
def _redis_namespace(settings):
    """Every test gets its own Redis key namespace (suites share one Redis)."""
    settings.INFERENCE_REDIS_PREFIX = f"test-{uuid.uuid4().hex[:10]}:"


@pytest.fixture
def owner(db):
    return UserFactory()


@pytest.fixture
def staff(db):
    return UserFactory(is_staff=True)


def make_endpoint(owner, **kwargs) -> LLMEndpoint:
    defaults = {
        "slug": f"gpu-{uuid.uuid4().hex[:6]}",
        "name": "GPU box",
        "kind": "ollama",
        "connection": "relay",
        "api_flavor": "native",
        "max_concurrency": 2,
        "owner": owner,
    }
    defaults.update(kwargs)
    return LLMEndpoint.objects.create(**defaults)


def make_local_model(endpoint, name="qwen2.5-coder:7b", digest="sha256:" + "ab" * 32, **kwargs) -> LLMModel:
    defaults = {
        "model_id": f"local-{endpoint.slug}/{name.replace(':', '-')}",
        "canonical_slug": f"local-{endpoint.slug}_{name.replace(':', '-')}",
        "provider": endpoint.slug,
        "model_name": name,
        "endpoint": endpoint,
        "served_model_name": name,
        "digest": digest,
        "context_window": 32768,
        "max_output_tokens": 0,
        "is_free": True,
        "supports_function_calling": True,
        "metadata": {"ollama": {"capabilities": ["completion", "tools"], "show_digest": digest}},
    }
    defaults.update(kwargs)
    return LLMModel.objects.create(**defaults)


@pytest.fixture
def endpoint(owner):
    return make_endpoint(owner)


@pytest.fixture
def local_model(endpoint):
    return make_local_model(endpoint)


def make_agent_run(model, owner, *, endpoint_section=None, wall_clock_s=600, **kwargs):
    """A RUNNING app run whose manifest freezes ``model``'s endpoint (``endpoint_section`` overrides it)."""
    from backend.generation.models.choices import RunPhase
    from backend.generation.models.choices import RunStatus
    from backend.generation.tests.factories import GenerationRunFactory
    from backend.generation.tests.factories import RunManifestFactory
    from backend.generation.tests.factories import run_manifest_content

    fields = {
        "status": RunStatus.RUNNING,
        "phase": RunPhase.RUNNING,
        "attempt": 1,
        "priority": 3,
        "lease_expires_at": timezone.now() + timedelta(minutes=5),
        **kwargs,
    }
    run = GenerationRunFactory(model=model, created_by=owner, **fields)
    manifest = run_manifest_content(run)
    manifest["model"]["endpoint"] = endpoint_section or {
        "id": model.endpoint_id,
        "kind": model.endpoint.kind,
        "served_model_name": model.served_model_name,
        "digest": model.digest,
        "cost_basis": "free",
    }
    manifest["resources"]["effective"] = {"content": {"time": {"wall_clock_s": wall_clock_s}}}
    RunManifestFactory(run=run, manifest=manifest)
    return run
