from __future__ import annotations

import hashlib
import uuid

import pytest

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
