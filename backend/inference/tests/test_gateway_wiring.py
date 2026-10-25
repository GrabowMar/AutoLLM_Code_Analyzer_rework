"""Gateway wiring for agent jobs (spec §D.6) and the sandbox's use of it."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from django.core import signing

from backend.generation.services.agent.sandbox import AgentSandbox
from backend.generation.tests.factories import GenerationJobFactory
from backend.inference.access import LLMAccessError
from backend.inference.gateway_wiring import EGRESS_LABEL
from backend.inference.gateway_wiring import ensure_egress_network
from backend.inference.gateway_wiring import for_job
from backend.inference.gateway_wiring import proxy_token
from backend.inference.gateway_wiring import read_proxy_token
from backend.inference.snapshot import endpoint_section
from backend.inference.timeouts import chain

pytestmark = pytest.mark.django_db


def test_openrouter_wiring_is_todays(owner):
    job = GenerationJobFactory(created_by=owner)
    with mock.patch("backend.credentials.services.resolver.get_openrouter_key", return_value="sk-or"):
        wiring = for_job(job, {"mode": "agent"})
    assert (wiring.image_local, wiring.upstream, wiring.api_key, wiring.network) == (
        False,
        "https://openrouter.ai/api/v1",
        "sk-or",
        "bridge",
    )
    assert wiring.extra_env == {}
    assert wiring.upstream_timeout_s is None


def test_local_wiring(owner, endpoint, local_model, settings):
    job = GenerationJobFactory(created_by=owner, model=local_model, attempt=2)
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


def test_proxy_token_is_signed_and_salted(owner):
    job = GenerationJobFactory(created_by=owner)
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


def test_sandbox_names_carry_the_attempt():
    with mock.patch("backend.generation.services.agent.sandbox.docker_manager.new_client"):
        sandbox = AgentSandbox("12345678-1234-1234-1234-123456789abc", 3, snapshot={}, api_key="k")
    assert sandbox.short == "123456781234-a3"
    assert sandbox.wiring.network == "bridge"
    assert sandbox.wiring.api_key == "k"


def test_sandbox_starts_the_local_gateway_with_its_wiring(owner, endpoint, local_model):
    import json

    from backend.inference.gateway_wiring import GatewayWiring

    wiring = GatewayWiring(
        image_local=True,
        upstream="http://172.30.99.2:8090/v1",
        api_key="",
        network="llmlab-gw-egress",
        extra_env={"LLMLAB_UPSTREAM_TOKEN": "tok"},
        upstream_timeout_s=1200.0,
    )
    snapshot = {
        "agent": {"max_llm_requests": 5, "max_total_tokens": 0, "max_cost_usd": 0, "network_policy": "none"},
        "gateway": {"forced_params": {"seed": 1}, "stripped_params": []},
        "llm": {"model_slug": local_model.model_id},
    }
    client = mock.Mock()
    gateway = client.containers.create.return_value
    gateway.status = "running"
    gateway.exec_run.return_value = (0, b"")
    with mock.patch("backend.generation.services.agent.sandbox.docker_manager.new_client", return_value=client):
        sandbox = AgentSandbox("12345678-1234-1234-1234-123456789abc", 1, snapshot=snapshot, wiring=wiring)
        sandbox.network = mock.Mock()
        sandbox._start_gateway("llmlab/llm-gateway-local:abc")
    kwargs = client.containers.create.call_args.kwargs
    assert kwargs["network"] == "llmlab-gw-egress"
    assert kwargs["name"] == "llmlab-gateway-123456781234-a1"
    assert kwargs["environment"]["LLMLAB_UPSTREAM_TOKEN"] == "tok"  # noqa: S105
    assert kwargs["environment"]["OPENROUTER_API_KEY"] == ""
    config = json.loads(kwargs["environment"]["GATEWAY_CONFIG"])
    assert (config["upstream"], config["upstream_timeout_s"]) == ("http://172.30.99.2:8090/v1", 1200.0)


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
