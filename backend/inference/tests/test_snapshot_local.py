"""Local-model snapshots and fingerprints (spec §D.5); OpenRouter snapshots stay byte-identical."""

from __future__ import annotations

import json

import pytest

from backend.generation.services.agent.images import gateway_image_plan
from backend.generation.services.agent.images import image_tag
from backend.generation.services.agent.snapshot import build_agent_snapshot
from backend.generation.services.agent.snapshot import reseed_agent_snapshot
from backend.generation.services.profile_resolver import build_custom_snapshot
from backend.generation.services.profile_resolver import run_fingerprint
from backend.generation.tests import test_default_run_golden as golden
from backend.inference.tests.conftest import make_endpoint
from backend.inference.tests.conftest import make_local_model
from backend.inference.timeouts import chain

pytestmark = pytest.mark.django_db
fixture_rows = golden.fixture_rows  # the golden module's own fixture (read, never edited)
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64


def _snapshot(model, app, harness="opencode", seed=None):
    return build_agent_snapshot(
        app_requirement=app,
        description="",
        profile=None,
        stack_slug="flask-react",
        model=model,
        max_tokens=32000,
        experiment_seed=golden._seed() if seed is None else seed,
        llm_overrides={},
        agent_overrides={"harness": harness},
    )


def _local(owner, *, digest=D1, endpoint=None, **endpoint_kwargs):
    endpoint = endpoint or make_endpoint(owner, num_ctx_cap=16384, **endpoint_kwargs)
    return make_local_model(endpoint, digest=digest, context_window=131072)


@pytest.mark.parametrize("harness", golden.HARNESS_NAMES)
def test_openrouter_snapshot_unchanged(fixture_rows, harness):
    model, app = fixture_rows
    snapshot = _snapshot(model, app, harness)
    assert "endpoint" not in snapshot
    assert snapshot["images"]["gateway"] == image_tag("llm-gateway")
    assert golden._identity(snapshot) == golden.GOLDEN[harness]
    assert snapshot["llm"]["provider_routing_source"] == "default"


@pytest.mark.parametrize("harness", golden.HARNESS_NAMES)
def test_local_snapshot(fixture_rows, owner, harness):
    _model, app = fixture_rows
    model = _local(owner)
    snapshot = _snapshot(model, app, harness)
    section = snapshot["endpoint"]
    assert (section["slug"], section["served_model_name"], section["digest"], section["num_ctx"]) == (
        model.endpoint.slug,
        "qwen2.5-coder:7b",
        D1,
        16384,
    )
    assert section["client_timeout_s"] == chain(model.endpoint).harness_client_s
    forced = snapshot["gateway"]["forced_params"]
    assert "provider" not in forced
    assert forced["seed"] == golden._seed()
    assert forced["max_tokens"] <= 16384
    assert snapshot["llm"]["provider_routing_source"] == "not_applicable"
    assert snapshot["llm"]["context_window_source"] == "endpoint_num_ctx"
    assert snapshot["llm"]["supported_params_source"] == "endpoint_static"
    assert snapshot["images"]["gateway"] == gateway_image_plan(local=True)[-1][2]
    assert snapshot["images"]["gateway"].startswith("llmlab/llm-gateway-local:")


def test_harness_rendering_gets_the_client_timeout(fixture_rows, owner):
    model_or, app = fixture_rows
    model = _local(owner)
    timeout = chain(model.endpoint).harness_client_s
    opencode = _snapshot(model, app, "opencode")["harness"]
    options = opencode["config"]["provider"]["openrouter"]["options"]
    assert options["timeout"] == timeout * 1000
    assert opencode["config"]["provider"]["openrouter"]["models"][model.model_id]["reasoning"] is False
    assert json.loads(opencode["env"]["OPENCODE_CONFIG_CONTENT"]) == opencode["config"]
    aider = _snapshot(model, app, "aider")["harness"]["args"]
    assert aider[aider.index("--timeout") + 1] == str(timeout)
    assert aider[-2:] == ["--message-file", "/out/brief.md"]
    mini = _snapshot(model, app, "mini-swe-agent")["harness"]["config"]["model"]["model_kwargs"]
    assert mini["timeout"] == timeout
    # OpenRouter rendering has none of it.
    assert (
        "timeout" not in _snapshot(model_or, app, "opencode")["harness"]["config"]["provider"]["openrouter"]["options"]
    )
    assert "--timeout" not in _snapshot(model_or, app, "aider")["harness"]["args"]
    assert "timeout" not in _snapshot(model_or, app, "mini-swe-agent")["harness"]["config"]["model"]["model_kwargs"]


def test_fingerprints_follow_the_weights(fixture_rows, owner):
    _model, app = fixture_rows
    endpoint = make_endpoint(owner)
    a = _snapshot(make_local_model(endpoint, name="m:7b", digest=D1), app)
    from backend.llm_models.models import LLMModel

    LLMModel.objects.filter(endpoint=endpoint).update(digest=D2)
    b = _snapshot(LLMModel.objects.get(endpoint=endpoint), app)
    assert a["run_fingerprint"] != b["run_fingerprint"]
    assert a["config_fingerprint"] != b["config_fingerprint"]
    # Same served name and digest on another endpoint: a different condition (no pooling).
    other = _snapshot(make_local_model(make_endpoint(owner), name="m:7b", digest=D2), app)
    assert other["run_fingerprint"] != b["run_fingerprint"]
    # Replicates keep the endpoint section and only change the seed.
    again = reseed_agent_snapshot(b, 7)
    assert again["endpoint"] == b["endpoint"]
    assert again["config_fingerprint"] == b["config_fingerprint"]


def test_scaffolding_fingerprint(owner):
    base = {"prompt_hash": "p", "seed": 1, "llm": {"model_slug": "x", "temperature": 0.2}, "scaffolding_slug": "s"}
    assert run_fingerprint(base) == run_fingerprint(json.loads(json.dumps(base)))
    model = _local(owner)
    custom = build_custom_snapshot(model=model, temperature=0.2, max_tokens=100)
    assert custom["endpoint"]["digest"] == D1
    assert custom["llm"]["served_model_name"] == "qwen2.5-coder:7b"
    local = {**base, "endpoint": custom["endpoint"]}
    changed = {**base, "endpoint": {**custom["endpoint"], "digest": D2}}
    assert run_fingerprint(local) != run_fingerprint(base)
    assert run_fingerprint(local) != run_fingerprint(changed)
    openrouter = build_custom_snapshot(
        model=golden.LLMModelFactory(model_id="vendor/z"),
        temperature=0.2,
        max_tokens=100,
    )
    assert "endpoint" not in openrouter
    assert "served_model_name" not in openrouter["llm"]
