"""Tests for reproducibility: prompt_hash grouping, seeds, replay/rerun cloning."""

from __future__ import annotations

from unittest import mock

import pytest

from backend.generation.models import ContentBlock
from backend.generation.models import GenerationJob
from backend.generation.services.bundle_resolver import build_resolved_bundle
from backend.generation.services.bundle_resolver import bundle_key_from_snapshot
from backend.generation.services.bundle_resolver import derive_experiment_seed
from backend.generation.services.job_cloning import clone_job
from backend.generation.services.openrouter_client import OpenRouterClient
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ContentBlockFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.generation.tests.factories import TemplateBundleFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _make_bundle():
    ContentBlockFactory(
        slug="test-repro-backend-system",
        version=1,
        block_type=ContentBlock.BlockType.PROMPT_STAGE,
        content="System prompt",
        metadata={"stage": "backend", "role": "system"},
        is_system=True,
    )
    return TemplateBundleFactory(
        slug="test-repro-bundle",
        block_refs=[{"type": "prompt_stage", "slug": "test-repro-backend-system", "version": 1}],
    )


def _snapshot(app_req, bundle, *, seed, model=None, temperature=0.3):
    return build_resolved_bundle(
        app_requirement=app_req,
        template_bundle=bundle,
        scaffolding_slug="flask-react",
        model=model,
        temperature=temperature,
        max_tokens=8000,
        user=None,
        experiment_seed=seed,
    )


def test_prompt_hash_ignores_seed_and_model():
    """Jobs from the same prompt material must share a hash across seeds/models."""
    app_req = AppRequirementTemplateFactory()
    bundle = _make_bundle()
    model = LLMModelFactory()

    a = _snapshot(app_req, bundle, seed=1)
    b = _snapshot(app_req, bundle, seed=999, model=model, temperature=0.9)

    assert a["prompt_hash"] == b["prompt_hash"]
    assert a["run_fingerprint"] != b["run_fingerprint"]


def test_prompt_hash_changes_with_prompt_material():
    app_req = AppRequirementTemplateFactory()
    other_req = AppRequirementTemplateFactory(backend_requirements=["Different requirement"])
    bundle = _make_bundle()

    a = _snapshot(app_req, bundle, seed=1)
    b = _snapshot(other_req, bundle, seed=1)

    assert a["prompt_hash"] != b["prompt_hash"]


def test_snapshot_freezes_model_pricing():
    app_req = AppRequirementTemplateFactory()
    bundle = _make_bundle()
    model = LLMModelFactory(
        input_price_per_token=0.000001,
        output_price_per_token=0.000002,
        context_window=128000,
    )

    snapshot = _snapshot(app_req, bundle, seed=1, model=model)

    llm = snapshot["llm"]
    assert llm["input_price_per_token"] == 0.000001
    assert llm["output_price_per_token"] == 0.000002
    assert llm["context_window"] == 128000
    assert llm["pricing_snapshot_at"]


def test_bundle_key_from_snapshot():
    assert bundle_key_from_snapshot({"bundle_slug": "b", "bundle_version": 3}) == "b@3"
    assert bundle_key_from_snapshot({"bundle_slug": "b"}) == "b@1"
    assert bundle_key_from_snapshot({}) == ""


def test_derive_experiment_seed_is_deterministic():
    a = derive_experiment_seed(42, "cond-1", "app-a", 0)
    b = derive_experiment_seed(42, "cond-1", "app-a", 0)
    c = derive_experiment_seed(42, "cond-1", "app-a", 1)
    assert a == b
    assert a != c
    assert 0 <= a < 2_147_483_647


def test_clone_job_replay_keeps_seed_and_snapshot():
    user = UserFactory()
    job = GenerationJobFactory(
        mode="scaffolding",
        created_by=user,
        experiment_seed=1234,
        prompt_hash="abcd1234abcd1234",
        bundle_key="repro-bundle@1",
        resolved_bundle={
            "bundle_slug": "repro-bundle",
            "bundle_version": 1,
            "seed": 1234,
            "prompt_hash": "abcd1234abcd1234",
            "prompt_templates": {"backend": {"system": "S", "user": "U"}},
            "llm": {"temperature": 0.3, "max_tokens": 8000},
        },
    )

    replay = clone_job(job, user, reuse_snapshot=True, new_seed=False, keep_batch=False)

    assert replay.experiment_seed == 1234
    assert replay.resolved_bundle["seed"] == 1234
    assert replay.prompt_hash == "abcd1234abcd1234"
    assert replay.bundle_key == "repro-bundle@1"
    assert replay.batch is None


def test_clone_job_rerun_draws_fresh_seed_same_prompt_hash():
    user = UserFactory()
    job = GenerationJobFactory(
        mode="scaffolding",
        created_by=user,
        experiment_seed=1234,
        resolved_bundle={
            "bundle_slug": "repro-bundle",
            "bundle_version": 1,
            "seed": 1234,
            "prompt_hash": "abcd1234abcd1234",
            "prompt_templates": {"backend": {"system": "S", "user": "U"}},
            "llm": {"temperature": 0.3, "max_tokens": 8000},
        },
    )

    with mock.patch("backend.generation.services.job_cloning.random.randint", return_value=777):
        rerun = clone_job(job, user, reuse_snapshot=True, new_seed=True)

    assert rerun.experiment_seed == 777
    assert rerun.resolved_bundle["seed"] == 777
    assert rerun.prompt_hash == "abcd1234abcd1234"
    # Original untouched
    job.refresh_from_db()
    assert job.experiment_seed == 1234


def test_chat_completion_sends_seed(settings):
    settings.OPENROUTER_API_KEY = "test-key"
    client = OpenRouterClient()
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json)
        response = mock.Mock(status_code=200)
        response.json.return_value = {"choices": [{"finish_reason": "stop"}], "usage": {}}
        return response

    with mock.patch("backend.generation.services.openrouter_client.requests.post", fake_post):
        client.chat_completion(
            model="test/model",
            messages=[{"role": "user", "content": "hi"}],
            seed=4242,
            top_p=0.9,
        )

    assert captured["seed"] == 4242
    assert captured["top_p"] == 0.9


def test_chat_completion_preserves_final_server_error(settings):
    settings.OPENROUTER_API_KEY = "test-key"
    client = OpenRouterClient()
    response = mock.Mock(status_code=502, text="bad gateway")

    with (
        mock.patch(
            "backend.generation.services.openrouter_client.requests.post",
            return_value=response,
        ),
        mock.patch("backend.generation.services.openrouter_client.time.sleep"),
        pytest.raises(Exception, match="Server error 502") as excinfo,
    ):
        client.chat_completion(model="test/model", messages=[])

    assert excinfo.value.status_code == 502


def test_replay_endpoint_creates_exact_clone(client):
    user = UserFactory()
    client.force_login(user)
    job = GenerationJobFactory(
        mode="scaffolding",
        created_by=user,
        status=GenerationJob.Status.COMPLETED,
        experiment_seed=55,
        resolved_bundle={
            "bundle_slug": "repro-bundle",
            "seed": 55,
            "prompt_hash": "beefbeefbeefbeef",
            "prompt_templates": {"backend": {"system": "S", "user": "U"}},
            "llm": {},
        },
    )

    with mock.patch("backend.generation.api.views.jobs.dispatch_job"):
        res = client.post(f"/api/generation/jobs/{job.id}/replay/")

    assert res.status_code == 200
    body = res.json()
    assert body["experiment_seed"] == 55
    assert body["prompt_hash"] == "beefbeefbeefbeef"
    assert body["id"] != str(job.id)


def test_replay_endpoint_rejects_snapshotless_job(client):
    user = UserFactory()
    client.force_login(user)
    job = GenerationJobFactory(
        created_by=user,
        status=GenerationJob.Status.COMPLETED,
        resolved_bundle={},
    )

    res = client.post(f"/api/generation/jobs/{job.id}/replay/")

    assert res.status_code == 400
