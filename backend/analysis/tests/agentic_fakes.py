"""A fake apps-daemon Docker client for the agentic reviewer's sandbox tests.

Only the slice of docker-py the review sandbox uses: images.get, networks
create/list, containers create/list, and per container put_archive / start /
wait / kill / reload / attrs / get_archive / logs / exec_run / stop / remove.
Every call is recorded so tests can assert on labels, limits, networks and
cleanup.
"""

from __future__ import annotations

import io
import tarfile
from dataclasses import dataclass
from dataclasses import field
from typing import Any

import requests


def tar_dir(name: str, files: dict[str, bytes]) -> list[bytes]:
    """A get_archive-style stream: members under ``<name>/``."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        for rel, data in files.items():
            member = tarfile.TarInfo(f"{name}/{rel}")
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return [buf.getvalue()]


def untar(data: bytes) -> dict[str, Any]:
    """{name: bytes} of regular files plus {name + ':mode': mode} for assertions."""
    out: dict[str, Any] = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar:
            if member.isreg():
                out[member.name] = tar.extractfile(member).read()
                out[f"{member.name}:mode"] = member.mode
    return out


@dataclass
class Behaviour:
    """How the fake harness run goes."""

    out_files: dict[str, bytes] = field(default_factory=dict)  # what /out holds after the run
    gateway_logs: dict[str, bytes] = field(default_factory=dict)  # what /logs holds
    exit_code: int = 0  # container exit code
    oom_killed: bool = False
    waits_before_exit: int = 0  # wait() timeouts before the container exits (never exits if None)
    never_exits: bool = False
    fail_on: str = ""  # "put_archive" | "network" | "gateway_start" | "gateway_unhealthy" | "start"
    gateway_health_after: int = 0  # failed health probes before healthy


class FakeImage:
    def __init__(self, tag: str) -> None:
        self.id = f"sha256:{abs(hash(tag)):x}"
        self.tags = [tag]


class FakeImages:
    def __init__(self, missing: set[str] | None = None) -> None:
        self.missing = missing or set()
        self.requested: list[str] = []

    def get(self, tag: str) -> FakeImage:
        import docker

        self.requested.append(tag)
        if tag in self.missing:
            raise docker.errors.ImageNotFound(tag)
        return FakeImage(tag)


class FakeNetwork:
    def __init__(self, client: FakeClient, name: str, kwargs: dict[str, Any]) -> None:
        self.client = client
        self.name = name
        self.kwargs = kwargs
        self.connected: list[tuple[str, list[str]]] = []
        self.removed = False
        self.attrs = {"Labels": dict(kwargs.get("labels") or {})}

    def connect(self, container: FakeContainer, aliases: list[str] | None = None) -> None:
        self.connected.append((container.name, list(aliases or [])))

    def remove(self) -> None:
        self.removed = True
        self.client.network_list.remove(self)


class FakeContainer:
    def __init__(self, client: FakeClient, image: str, kwargs: dict[str, Any]) -> None:
        self.client = client
        self.image = image
        self.kwargs = kwargs
        self.name = kwargs.get("name", "")
        self.labels = dict(kwargs.get("labels") or {})
        self.is_gateway = self.name.endswith("-gw")
        self.archives: list[tuple[str, bytes]] = []
        self.started = False
        self.killed = False
        self.stopped = False
        self.removed = False
        self.status = "created"
        self.wait_calls = 0
        self.health_probes = 0
        self.attrs: dict[str, Any] = {"State": {}}

    @property
    def behaviour(self) -> Behaviour:
        return self.client.behaviour

    def put_archive(self, path: str, data: bytes) -> bool:
        if self.behaviour.fail_on == "put_archive":
            msg = "put_archive failed"
            raise RuntimeError(msg)
        self.archives.append((path, data))
        return True

    def start(self) -> None:
        if self.is_gateway and self.behaviour.fail_on == "gateway_start":
            self.status = "exited"
        elif not self.is_gateway and self.behaviour.fail_on == "start":
            msg = "start failed"
            raise RuntimeError(msg)
        else:
            self.status = "running"
        self.started = True

    def reload(self) -> None:
        if not self.is_gateway and self.status == "exited":
            self.attrs = {"State": {"ExitCode": self.behaviour.exit_code, "OOMKilled": self.behaviour.oom_killed}}

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        self.wait_calls += 1
        if self.behaviour.never_exits or self.wait_calls <= self.behaviour.waits_before_exit:
            raise requests.exceptions.ReadTimeout
        self.status = "exited"
        return {"StatusCode": self.behaviour.exit_code}

    def kill(self) -> None:
        self.killed = True
        self.status = "exited"

    def stop(self, timeout: int = 10) -> None:
        self.stopped = True
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.removed = True
        if self in self.client.container_list:
            self.client.container_list.remove(self)

    def exec_run(self, cmd: list[str]) -> tuple[int, bytes]:
        self.health_probes += 1
        if self.behaviour.fail_on == "gateway_unhealthy":
            return 1, b""
        return (0 if self.health_probes > self.behaviour.gateway_health_after else 1), b""

    def logs(self, tail: int = 200) -> bytes:
        return b"gateway: listening\n" if self.is_gateway else b"container log tail\n"

    def get_archive(self, path: str) -> tuple[list[bytes], dict[str, Any]]:
        if self.is_gateway and path == "/logs":
            return tar_dir("logs", self.behaviour.gateway_logs), {}
        if not self.is_gateway and path == "/out":
            return tar_dir("out", self.behaviour.out_files), {}
        msg = f"no such path {path}"
        raise RuntimeError(msg)


class FakeContainers:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    def create(self, image: str, **kwargs: Any) -> FakeContainer:
        container = FakeContainer(self.client, image, kwargs)
        self.client.created.append(container)
        self.client.container_list.append(container)
        return container

    def list(self, all: bool = False, filters: dict[str, Any] | None = None) -> list[FakeContainer]:
        label = (filters or {}).get("label")
        return [c for c in self.client.container_list if not label or label in c.labels]


class FakeNetworks:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    def create(self, name: str, **kwargs: Any) -> FakeNetwork:
        if self.client.behaviour.fail_on == "network":
            msg = "network create failed"
            raise RuntimeError(msg)
        network = FakeNetwork(self.client, name, kwargs)
        self.client.networks_created.append(network)
        self.client.network_list.append(network)
        return network

    def list(self, filters: dict[str, Any] | None = None) -> list[FakeNetwork]:
        label = (filters or {}).get("label")
        return [n for n in self.client.network_list if not label or label in n.attrs["Labels"]]


class FakeClient:
    def __init__(self, behaviour: Behaviour | None = None, *, missing_images: set[str] | None = None) -> None:
        self.behaviour = behaviour or Behaviour()
        self.images = FakeImages(missing_images)
        self.containers = FakeContainers(self)
        self.networks = FakeNetworks(self)
        self.created: list[FakeContainer] = []
        self.container_list: list[FakeContainer] = []
        self.networks_created: list[FakeNetwork] = []
        self.network_list: list[FakeNetwork] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    @property
    def harness(self) -> FakeContainer:
        return next(c for c in self.created if not c.is_gateway)

    @property
    def gateway(self) -> FakeContainer:
        return next(c for c in self.created if c.is_gateway)
