"""Local-model endpoint sections and fingerprints (spec §D.5) under v3.

The v2 agent-snapshot builders are gone; what remains in ``inference.snapshot`` is the endpoint
section of a local model (what the proxy falls back to for keys the manifest does not freeze), the
``llm_source`` provenance, and the v3 rule that the pinned weights are part of ``config_fingerprint``
(``identity.compute_keys``, model ``{slug, endpoint_kind, pinned_digest}``).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.generation import identity
from backend.generation.tests.wp01_foundation.test_identity_algebra import APP_INPUTS
from backend.inference.proxy_server import frozen_endpoint
from backend.inference.snapshot import effective_num_ctx
from backend.inference.snapshot import endpoint_section
from backend.inference.snapshot import llm_source
from backend.inference.tests.conftest import make_endpoint
from backend.inference.tests.conftest import make_local_model
from backend.inference.timeouts import chain
from backend.llm_models.tests.factories import LLMModelFactory

pytestmark = pytest.mark.django_db
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64


def _local(owner, *, digest=D1, endpoint=None, **endpoint_kwargs):
    endpoint = endpoint or make_endpoint(owner, num_ctx_cap=16384, **endpoint_kwargs)
    return make_local_model(endpoint, digest=digest, context_window=131072)


def test_openrouter_models_have_no_endpoint_section():
    model = LLMModelFactory(model_id="vendor/z")
    assert endpoint_section(model) is None
    assert effective_num_ctx(model) is None
    assert llm_source({}) is None


def test_local_endpoint_section(owner):
    model = _local(owner)
    section = endpoint_section(model)
    assert (section["slug"], section["served_model_name"], section["digest"], section["num_ctx"]) == (
        model.endpoint.slug,
        "qwen2.5-coder:7b",
        D1,
        16384,  # min(context_window, endpoint cap)
    )
    assert section["client_timeout_s"] == chain(model.endpoint).harness_client_s
    assert section["capabilities"] == ["completion", "tools"]
    assert llm_source({"endpoint": section}) == {
        "kind": "ollama",
        "api_flavor": "native",
        "served_model_name": "qwen2.5-coder:7b",
        "digest": D1,
        "num_ctx": 16384,
    }


def test_manifest_endpoint_wins_over_the_live_row(owner):
    model = _local(owner)
    manifest = {
        "model": {
            "slug": model.model_id,
            "endpoint": {
                "id": model.endpoint_id,
                "kind": "ollama",
                "served_model_name": "frozen:7b",
                "digest": D2,
                "cost_basis": "free",
            },
        },
    }
    section = frozen_endpoint(manifest, model)
    assert (section["served_model_name"], section["digest"]) == ("frozen:7b", D2)
    # Not frozen by the v3 manifest yet: taken from the run's model, never left to the server default.
    assert section["num_ctx"] == 16384
    openrouter = {"model": {"endpoint": {"id": 1, "kind": "openrouter", "cost_basis": "billed"}}}
    assert frozen_endpoint(openrouter, model) is None
    assert frozen_endpoint({}, model) is None


def test_fingerprints_follow_the_weights(owner):
    def keys(model, **changes):
        section = endpoint_section(model)
        pinned = {"slug": model.model_id, "endpoint_kind": section["kind"], "pinned_digest": section["digest"]}
        return identity.compute_keys(replace(APP_INPUTS, model=pinned, **changes))

    endpoint = make_endpoint(owner)
    model = make_local_model(endpoint, name="m:7b", digest=D1)
    a = keys(model)
    model.digest = D2
    model.save(update_fields=["digest"])
    b = keys(model)
    assert a.run_fingerprint != b.run_fingerprint
    assert a.config_fingerprint != b.config_fingerprint
    # Same served name and digest on another endpoint: a different model slug, so a different condition.
    other = keys(make_local_model(make_endpoint(owner), name="m:7b", digest=D2))
    assert other.config_fingerprint != b.config_fingerprint
    # Replicates differ only by seed.
    again = keys(model, seed=7)
    assert again.config_fingerprint == b.config_fingerprint
    assert again.run_fingerprint != b.run_fingerprint
