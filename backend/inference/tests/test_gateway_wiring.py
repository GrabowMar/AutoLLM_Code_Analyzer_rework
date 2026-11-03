"""Gateway wiring for agent runs (spec §D.6).

The v2 ``AgentSandbox`` tests are gone with the sandbox; the v3 sandbox (``agentic/sandbox.py``) tests its own use.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from django.core import signing

from backend.generation.tests.factories import GenerationRunFactory
from backend.inference.access import LLMAccessError
from backend.inference.gateway_wiring import EGRESS_LABEL
from backend.inference.gateway_wiring import ensure_egress_network
from backend.inference.gateway_wiring import for_job
from backend.inference.gateway_wiring import proxy_token
from backend.inference.gateway_wiring import read_proxy_token
from backend.inference.snapshot import endpoint_section
from backend.inference.timeouts import chain

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _local_agent_runs_supported(settings):
    settings.LLM_LOCAL_AGENT_RUNS = True


def test_openrouter_wiring_is_todays(owner):
    job = GenerationRunFactory(created_by=owner)
    with mock.patch("backend.credentials.services.resolver.get_openrouter_key", return_value="sk-or"):
        wiring = for_job(job, {})
    assert (wiring.image_local, wiring.upstream, wiring.api_key, wiring.network) == (
        False,
        "https://openrouter.ai/api/v1",
        "sk-or",
        "bridge",
    )
    assert wiring.extra_env == {}
    assert wiring.upstream_timeout_s is None


def test_local_wiring(owner, endpoint, local_model, settings):
    job = GenerationRunFactory(created_by=owner, model=local_model, attempt=2)
    wiring = for_job(job, {"endpoint": endpoint_section(local_model)})
    assert wiring.image_local
    assert (wiring.upstream, wiring.network, wiring.api_key) == (
        settings.LLM_PROXY_UPSTREAM,
        settings.LLM_GATEWAY_EGRESS_NETWORK,
        "",
    )
    assert wiring.upstream_timeout_s == chain(endpoint).gateway_upstream_s
    assert read_proxy_token(wiring.extra_env["LLMLAB_UPSTREAM_TOKEN"], max_age=60) == (str(job.pk), 2)
    endpoint.enabled = False
    endpoint.save()
    with pytest.raises(LLMAccessError) as exc:
        for_job(job, {"endpoint": endpoint_section(local_model)})
    assert exc.value.code == "endpoint_disabled"


def test_local_wiring_refused_without_apps_dind(owner, endpoint, local_model, settings):
    # Production compose (host daemon, no apps-dind): the gateway cannot reach the
    # internal LLM proxy, so the run fails fast instead of burning infra retries.
    settings.LLM_LOCAL_AGENT_RUNS = False
    job = GenerationRunFactory(created_by=owner, model=local_model)
    with pytest.raises(LLMAccessError) as exc:
        for_job(job, {"endpoint": endpoint_section(local_model)})
    assert exc.value.retryable is False
    assert "not supported" in str(exc.value)
    # OpenRouter agent runs are unaffected.
    with mock.patch("backend.credentials.services.resolver.get_openrouter_key", return_value="sk-or"):
        assert for_job(job, {}).network == "bridge"


def test_local_wiring_rechecks_endpoint_access(owner, endpoint, local_model):
    from backend.inference.models import LLMEndpoint
    from backend.users.tests.factories import UserFactory

    other = UserFactory()
    endpoint.visibility = LLMEndpoint.Visibility.SHARED
    endpoint.save()
    job = GenerationRunFactory(created_by=other, model=local_model)
    section = endpoint_section(local_model)
    assert for_job(job, {"endpoint": section}).image_local
    # The owner makes the endpoint private: queued / replayed jobs of other users stop.
    endpoint.visibility = LLMEndpoint.Visibility.PRIVATE
    endpoint.save()
    with pytest.raises(LLMAccessError) as exc:
        for_job(job, {"endpoint": section})
    assert exc.value.code == "endpoint_forbidden"
    assert exc.value.retryable is False
    # The owner's own jobs are unaffected.
    assert for_job(GenerationRunFactory(created_by=owner, model=local_model), {"endpoint": section}).image_local


def test_proxy_token_is_signed_and_salted(owner):
    job = GenerationRunFactory(created_by=owner)
    token = proxy_token(job)
    with pytest.raises(signing.BadSignature):
        read_proxy_token(token + "x", max_age=60)
    with pytest.raises(signing.BadSignature):
        signing.TimestampSigner(salt="other").unsign_object(token)


class _Networks:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.created = []

    def list(self, names=None):
        return [n for n in self.existing if n.name in (names or [])]

    def create(self, name, **kwargs):
        self.created.append((name, kwargs))


def _network(name, *, options, subnet, containers=None, labels=None):
    net = mock.Mock()
    net.name = name
    net.attrs = {
        "Options": options,
        "IPAM": {"Config": [{"Subnet": subnet}]},
        "Internal": False,
        "Containers": containers or {},
        "Labels": labels or {},
    }
    return net


def test_egress_network_created_and_reconciled(settings):
    networks = _Networks()
    ensure_egress_network(SimpleNamespace(networks=networks))
    name, kwargs = networks.created[0]
    assert name == settings.LLM_GATEWAY_EGRESS_NETWORK
    assert kwargs["options"] == {
        "com.docker.network.bridge.name": "llmlab-gwegr",
        "com.docker.network.bridge.enable_icc": "false",
    }
    assert kwargs["internal"] is False
    assert kwargs["labels"] == {EGRESS_LABEL: "gw-egress"}
    good = _network(
        name,
        options=kwargs["options"],
        subnet=settings.LLM_GATEWAY_EGRESS_SUBNET,
        labels={EGRESS_LABEL: "gw-egress"},
    )
    networks = _Networks([good])
    ensure_egress_network(SimpleNamespace(networks=networks))
    assert networks.created == []
    wrong_in_use = _network(name, options={}, subnet="10.0.0.0/24", containers={"x": {}})
    networks = _Networks([wrong_in_use])
    ensure_egress_network(SimpleNamespace(networks=networks))
    wrong_in_use.remove.assert_not_called()
    wrong_idle = _network(name, options={}, subnet="10.0.0.0/24")
    networks = _Networks([wrong_idle])
    ensure_egress_network(SimpleNamespace(networks=networks))
    wrong_idle.remove.assert_called_once()
    assert len(networks.created) == 1


def test_egress_network_creation_race_is_tolerated(settings):
    import pytest
    from docker.errors import APIError

    name = settings.LLM_GATEWAY_EGRESS_NETWORK
    winner = _network(name, options={}, subnet=settings.LLM_GATEWAY_EGRESS_SUBNET)

    class Racing(_Networks):
        """``list`` sees nothing first; ``create`` then loses to a concurrent sandbox."""

        def create(self, name, **kwargs):
            self.existing = [winner]
            msg = "conflict"
            raise APIError(msg, response=SimpleNamespace(status_code=409, reason="Conflict"))

    ensure_egress_network(SimpleNamespace(networks=Racing()))

    class Broken(_Networks):
        def create(self, name, **kwargs):
            msg = "boom"
            raise APIError(msg, response=SimpleNamespace(status_code=500, reason="Server Error"))

    with pytest.raises(APIError):
        ensure_egress_network(SimpleNamespace(networks=Broken()))
