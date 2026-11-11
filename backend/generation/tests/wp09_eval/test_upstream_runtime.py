"""eval-v2@2: the public-looking mock upstream network of the eval runtimes (``plan.upstream``).

A fake docker client records what ``DockerEvalRuntime`` asks of the daemon: without ``plan.upstream`` nothing
changes (one network, the same container arguments); with it there is a second internal network from
``UPSTREAM_POOL``, the app is attached before it starts and the tester carries the plan's hostnames as aliases
on that network only.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any

import pytest

from backend.generation.evaluation import runtime as runtime_mod
from backend.generation.evaluation.runtime import DockerEvalRuntime
from backend.generation.evaluation.runtime import LocalProcessRuntime
from backend.generation.evaluation.runtime import RuntimeInfraError
from backend.generation.evaluation.runtime import upstream_hosts

PIN = "sha256:" + "ab" * 32
SUITE = {"tester": {"image_digest": PIN}, "adherence_checker": {"image_digest": PIN}}
UPSTREAM = {"hosts": ["www.example-news.com", "cdn.example-news.com"], "port": 80}


class FakeNetwork:
    def __init__(self, name: str, kwargs: dict[str, Any], log: list[tuple[Any, ...]]) -> None:
        self.name = name
        self.kwargs = kwargs
        self.log = log
        self.removed = False

    def disconnect(self, container: Any) -> None:
        self.log.append(("disconnect", self.name, container.name))

    def connect(self, container: Any, aliases: list[str] | None = None) -> None:
        self.log.append(("connect", self.name, container.name, aliases))

    def remove(self) -> None:
        self.removed = True
        self.log.append(("remove_network", self.name))


class FakeContainer:
    def __init__(self, image: str, kwargs: dict[str, Any], log: list[tuple[Any, ...]], n: int) -> None:
        self.image = image
        self.kwargs = kwargs
        self.log = log
        self.name = str(kwargs.get("name") or f"anon-{n}")
        self.attrs: dict[str, Any] = {"Config": {"WorkingDir": "/app"}}
        self.status = "running"

    def put_archive(self, path: str, data: bytes) -> None:
        self.log.append(("put_archive", self.name, path))

    def start(self) -> None:
        self.log.append(("start", self.name))

    def wait(self, timeout: float | None = None) -> dict[str, int]:
        return {"StatusCode": 0}

    def get_archive(self, path: str) -> tuple[list[bytes], dict[str, Any]]:
        name = path.rsplit("/", 1)[-1]
        if name != "result.json":
            raise KeyError(path)
        body = json.dumps({"measured": True, "checks": [], "probes": []}).encode()
        return [runtime_mod._tar({name: body})], {}

    def remove(self, **kwargs: Any) -> None:
        self.log.append(("remove", self.name))

    def stop(self, **kwargs: Any) -> None:
        self.log.append(("stop", self.name))


class _Image:
    id = PIN
    attrs: dict[str, Any] = {"RepoDigests": []}


class FakeClient:
    def __init__(self, network_errors: list[Exception] | None = None) -> None:
        self.log: list[tuple[Any, ...]] = []
        self.created_networks: list[FakeNetwork] = []
        self.created_containers: list[FakeContainer] = []
        self.network_attempts: list[dict[str, Any]] = []
        self.network_errors = list(network_errors or [])
        self.images = self
        self.networks = _Networks(self)
        self.containers = _Containers(self)

    def get(self, tag: str) -> _Image:  # images.get
        return _Image()


class _Networks:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    def create(self, name: str, **kwargs: Any) -> FakeNetwork:
        self.client.network_attempts.append({"name": name, **kwargs})
        if "ipam" in kwargs and self.client.network_errors:
            raise self.client.network_errors.pop(0)
        network = FakeNetwork(name, kwargs, self.client.log)
        self.client.created_networks.append(network)
        return network


class _Containers:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    def create(self, image: str, **kwargs: Any) -> FakeContainer:
        container = FakeContainer(image, kwargs, self.client.log, len(self.client.created_containers))
        self.client.created_containers.append(container)
        return container


def _evaluate(plan: dict[str, Any], *, prepare: bool) -> tuple[DockerEvalRuntime, FakeClient]:
    client = FakeClient()
    runtime = DockerEvalRuntime(SUITE, client=client)
    if prepare:
        runtime.prepare_upstream(plan["upstream"])
    handle = runtime.start("sha256:app", {"ADMIN_PASSWORD": "pw"})
    runtime.run_tester(handle, plan, {"ADMIN_PASSWORD": "pw"}, 5)
    return runtime, client


def _normalised(client: FakeClient, runtime: DockerEvalRuntime) -> list[dict[str, Any]]:
    """Container create arguments without the per-evaluation random name suffix."""
    out = []
    for container in client.created_containers:
        kwargs = json.loads(json.dumps(container.kwargs).replace(runtime.short, "SHORT"))
        kwargs["labels"].pop("llmlab.started", None)
        out.append(kwargs)
    return out


def test_no_upstream_means_one_network_and_unchanged_containers() -> None:
    runtime, client = _evaluate({"checks": []}, prepare=False)
    assert [n.name for n in client.created_networks] == [f"{runtime_mod._name_prefix()}-{runtime.short}"]
    assert runtime.upstream_network is None
    app, pre, post = client.created_containers
    for container in (app, pre, post):
        assert "sysctls" not in container.kwargs
    connects = [entry for entry in client.log if entry[0] == "connect"]
    assert [entry[3] for entry in connects] == [["app"], ["mock.internal"], ["mock.internal"]]
    assert {entry[1] for entry in connects} == {runtime.network.name}


def test_upstream_only_adds_to_what_a_plain_evaluation_creates() -> None:
    plain_runtime, plain = _evaluate({"checks": []}, prepare=False)
    up_runtime, up = _evaluate({"checks": [], "upstream": UPSTREAM}, prepare=True)
    before = _normalised(plain, plain_runtime)
    after = _normalised(up, up_runtime)
    assert after[0] == before[0]  # the app container is created exactly as before
    for with_upstream, without in zip(after[1:], before[1:], strict=True):
        assert with_upstream.pop("sysctls") == {"net.ipv4.ip_unprivileged_port_start": "0"}
        assert with_upstream == without


def test_upstream_network_is_internal_public_looking_and_carries_the_aliases() -> None:
    runtime, client = _evaluate({"checks": [], "upstream": UPSTREAM}, prepare=True)
    upstream, eval_net = client.created_networks  # prepared before the app's network exists
    assert upstream.name == f"{runtime_mod._name_prefix()}-up-{runtime.short}"
    assert upstream.kwargs["internal"] is True
    assert upstream.kwargs["driver"] == "bridge"
    assert upstream.kwargs["labels"] == runtime.labels
    subnet = ipaddress.ip_network(upstream.kwargs["ipam"]["Config"][0]["Subnet"])
    assert subnet.prefixlen == 28
    assert subnet.subnet_of(ipaddress.ip_network(runtime_mod.UPSTREAM_POOL))
    assert all(host.is_global for host in subnet.hosts())
    assert "ipam" not in eval_net.kwargs

    app = client.created_containers[0].name
    log = client.log
    # The app joins the upstream network without an alias, before its process starts.
    assert log.index(("connect", upstream.name, app, None)) < log.index(("start", app))
    for tester in client.created_containers[1:]:
        assert ("connect", eval_net.name, tester.name, ["mock.internal"]) in log
        joined = ("connect", upstream.name, tester.name, UPSTREAM["hosts"])
        assert log.index(joined) < log.index(("start", tester.name))
    # mock.internal is never an alias on the public-looking network, the public names never on the eval network.
    for entry in log:
        if entry[0] == "connect" and entry[1] == upstream.name:
            assert "mock.internal" not in (entry[3] or [])
        if entry[0] == "connect" and entry[1] == eval_net.name:
            assert not set(entry[3] or []) & set(UPSTREAM["hosts"])


def test_close_removes_the_upstream_network() -> None:
    runtime, client = _evaluate({"checks": [], "upstream": UPSTREAM}, prepare=True)
    upstream, eval_net = client.created_networks
    runtime.close()
    assert upstream.removed
    assert eval_net.removed
    assert runtime.upstream_network is None
    assert runtime.upstream_aliases == []


def test_prepare_upstream_is_idempotent() -> None:
    client = FakeClient()
    runtime = DockerEvalRuntime(SUITE, client=client)
    runtime.prepare_upstream(UPSTREAM)
    runtime.prepare_upstream(UPSTREAM)
    assert len(client.created_networks) == 1


def test_an_overlapping_subnet_is_drawn_again() -> None:
    client = FakeClient(network_errors=[RuntimeError("Pool overlaps with other one on this address space")] * 2)
    runtime = DockerEvalRuntime(SUITE, client=client)
    runtime.prepare_upstream(UPSTREAM)
    assert len(client.network_attempts) == 3
    assert runtime.upstream_network is client.created_networks[0]


def test_other_network_failures_are_infra_errors_without_retries() -> None:
    client = FakeClient(network_errors=[RuntimeError("daemon unreachable")])
    runtime = DockerEvalRuntime(SUITE, client=client)
    with pytest.raises(RuntimeInfraError, match="mock upstream network"):
        runtime.prepare_upstream(UPSTREAM)
    assert len(client.network_attempts) == 1


def test_overlap_gives_up_after_the_bounded_attempts() -> None:
    errors: list[Exception] = [RuntimeError("Pool overlaps")] * 20
    client = FakeClient(network_errors=errors)
    runtime = DockerEvalRuntime(SUITE, client=client)
    with pytest.raises(RuntimeInfraError):
        runtime.prepare_upstream(UPSTREAM)
    assert len(client.network_attempts) == runtime_mod._UPSTREAM_NETWORK_ATTEMPTS


def test_a_plan_with_upstream_needs_the_prepared_network() -> None:
    client = FakeClient()
    runtime = DockerEvalRuntime(SUITE, client=client)
    handle = runtime.start("sha256:app", {})
    with pytest.raises(RuntimeInfraError, match="prepare_upstream"):
        runtime.run_tester(handle, {"checks": [], "upstream": UPSTREAM}, {}, 5)
    assert len(client.created_containers) == 1  # no tester was created


@pytest.mark.parametrize(
    "hosts",
    [
        [],
        ["mock.internal"],
        ["app"],
        ["db"],
        ["files.internal"],
        ["localhost"],
        ["10.0.0.1"],
        ["55.77.0.2"],
        ["bad host.com"],
        [""],
    ],
)
def test_upstream_hosts_rejects_names_that_are_not_public(hosts: list[str]) -> None:
    with pytest.raises(RuntimeInfraError):
        upstream_hosts({"hosts": hosts})
    with pytest.raises(RuntimeInfraError):
        LocalProcessRuntime().prepare_upstream({"hosts": hosts})


def test_upstream_hosts_are_lower_cased_and_deduplicated() -> None:
    assert upstream_hosts({"hosts": ["WWW.Example.com", "www.example.com", "a.example.org"]}) == [
        "www.example.com",
        "a.example.org",
    ]


def test_local_runtime_points_the_upstream_at_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.generation.evaluation import tester as tester_pkg

    seen: list[dict[str, Any]] = []

    class _Tester:
        @staticmethod
        def run_plan(plan: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            seen.append(plan)
            return {"measured": True}

    monkeypatch.setattr(tester_pkg, "tester_module", lambda: _Tester)
    runtime = LocalProcessRuntime()
    handle = runtime_mod.AppHandle(image="x", env={}, ref=None, port=4321)
    runtime.prepare_upstream(UPSTREAM)
    runtime.run_tester(handle, {"checks": [], "upstream": UPSTREAM}, {}, 5)
    runtime.run_tester(handle, {"checks": []}, {}, 5)
    assert seen[0]["upstream"] == {"hosts": ["127.0.0.1"], "port": 0, "bind": "127.0.0.1"}
    assert seen[0]["base_url"] == "http://127.0.0.1:4321"
    assert "upstream" not in seen[1]
    assert UPSTREAM == {"hosts": ["www.example-news.com", "cdn.example-news.com"], "port": 80}  # not mutated
