"""Run one agent job in an isolated sandbox on the apps daemon.

Topology per job (all nested containers of apps-dind, never the host daemon)::

    [agent: harness]   --internal net-->  [gateway]  --bridge-->  OpenRouter
                                                    \\--CONNECT--> package registries (policy "packages")

For a model on a local endpoint the gateway is the ``llm-gateway-local``
overlay on the dedicated ``llmlab-gw-egress`` network, whose only reachable
upstream is the executor's internal LLM proxy (see ``inference.gateway_wiring``).

The agent container is attached *only* to a per-job ``internal`` network, so
it has no route anywhere except the gateway. It runs as an unprivileged user
with all capabilities dropped, no-new-privileges, and memory/CPU/PID caps.
The real API key lives only in the gateway's environment.

Every resource is labelled with the job id and torn down in ``finally``;
:func:`reap_orphans` removes leftovers of a crashed process.
"""

from __future__ import annotations

import io
import json
import logging
import tarfile
import time
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings

from backend.generation.services.agent.config import PACKAGE_REGISTRY_HOSTS
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.harnesses.base import GATEWAY_ALIAS
from backend.generation.services.agent.harnesses.base import OUTPUT_DIR
from backend.generation.services.agent.harnesses.base import WORKSPACE_DIR
from backend.generation.services.agent.harnesses.base import proxy_env
from backend.generation.services.agent.harnesses.base import run_script
from backend.generation.services.agent.images import IMAGES_DIR
from backend.generation.services.agent.images import gateway_image_plan
from backend.generation.services.agent.images import harness_image_plan
from backend.generation.services.agent.images import image_tag
from backend.generation.services.agent.workspace import make_tar
from backend.generation.services.agent.workspace import read_tar
from backend.runtime.services import docker_manager

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.inference.gateway_wiring import GatewayWiring

logger = logging.getLogger(__name__)

LABEL = "llmlab.agent-run"
GRACE_SECONDS = 60
POLL_SECONDS = 5


class SandboxError(Exception):
    """The sandbox could not be prepared or run (infrastructure, not the agent)."""


@dataclass
class SandboxResult:
    termination: str  # completed | timeout | resource_limit | harness_killed | cancelled | harness_error
    exit_code: int | None
    wall_seconds: float
    workspace: dict[str, bytes]
    outputs: dict[str, bytes]
    gateway_logs: dict[str, bytes]
    images: dict[str, str] = field(default_factory=dict)
    container_logs_tail: str = ""
    oom_killed: bool = False


def ensure_image(
    client,
    name: str,
    buildargs: dict[str, str] | None = None,
    *,
    expect_tag: str = "",
) -> tuple[str, str]:
    """Build ``images/<name>`` on the apps daemon if needed; return (tag, image id).

    The tag is content-addressed (see ``images.py``). *expect_tag* is the tag
    the job's snapshot recorded at creation: if the image definition changed
    since, the job refuses to run rather than silently using a different
    environment than it froze.
    """
    import docker

    buildargs = buildargs or {}
    tag = image_tag(name, buildargs)
    if expect_tag and tag != expect_tag:
        msg = (
            f"Image definition {name} changed since this job was created ({expect_tag} -> {tag}); "
            "re-create the job (retry) to run it on the current environment"
        )
        raise SandboxError(msg)
    try:
        return tag, client.images.get(tag).id
    except docker.errors.ImageNotFound:
        pass
    logger.info("Building sandbox image %s", tag)
    errors: list[str] = []
    for chunk in client.api.build(path=str(IMAGES_DIR / name), tag=tag, buildargs=buildargs, rm=True, decode=True):
        if chunk.get("error"):
            errors.append(chunk["error"])
    if errors:
        msg = f"Building {tag} failed: {errors[-1]}"
        raise SandboxError(msg)
    return tag, client.images.get(tag).id


def reap_orphans(client=None, *, older_than_seconds: int = 0) -> int:
    """Remove agent-run containers/networks left behind by a crashed process."""
    client = client or docker_manager.new_client()
    removed = 0
    now = time.time()
    for container in client.containers.list(all=True, filters={"label": LABEL}):
        started = container.labels.get("llmlab.started", "0")
        if now - float(started or 0) >= older_than_seconds:
            container.remove(force=True)
            removed += 1
    for network in client.networks.list(filters={"label": LABEL}):
        try:
            network.remove()
        except Exception as exc:  # noqa: BLE001 - still in use by a live run
            logger.debug("Keeping agent network %s: %s", network.name, exc)
    return removed


class AgentSandbox:
    """One job's network + gateway + agent containers.

    *wiring* (``inference.gateway_wiring.for_job``) says which gateway image,
    upstream, credentials and egress network the job uses; passing only
    *api_key* is the OpenRouter wiring. *attempt* suffixes container and
    network names (``-a<attempt>``) so leftovers of an earlier attempt never
    collide with a retry; it is runtime-only and not in the snapshot.
    """

    def __init__(
        self,
        job_id: str,
        attempt: int = 0,
        *,
        snapshot: dict[str, Any],
        api_key: str = "",
        wiring: GatewayWiring | None = None,
    ) -> None:
        from backend.inference.gateway_wiring import OPENROUTER_UPSTREAM
        from backend.inference.gateway_wiring import GatewayWiring

        self.job_id = str(job_id)
        self.short = f"{self.job_id.replace('-', '')[:12]}-a{int(attempt)}"
        self.wiring = wiring or GatewayWiring(
            image_local=False,
            upstream=OPENROUTER_UPSTREAM,
            api_key=api_key,
            network="bridge",
        )
        self.api_key = self.wiring.api_key
        self.snapshot = snapshot
        self.client = docker_manager.new_client()
        self.network = None
        self.gateway = None
        self.agent = None
        self.labels = {LABEL: self.job_id, "llmlab.started": str(int(time.time()))}

    # -- lifecycle -------------------------------------------------------

    def run(
        self,
        *,
        seed: dict[str, bytes],
        brief: str,
        is_cancelled: Callable[[], bool],
    ) -> SandboxResult:
        try:
            return self._run(seed=seed, brief=brief, is_cancelled=is_cancelled)
        finally:
            self.teardown()

    def _run(self, *, seed: dict[str, bytes], brief: str, is_cancelled: Callable[[], bool]) -> SandboxResult:
        agent_cfg = self.snapshot["agent"]
        section = self.snapshot["harness"]
        adapter = get_harness(section["name"])
        recorded = self.snapshot.get("images") or {}
        gateway_plan = gateway_image_plan(self.wiring.image_local)
        for index, (name, args, _tag) in enumerate(gateway_plan):
            # Only the last image (the one that runs) is pinned by the snapshot.
            last = index == len(gateway_plan) - 1
            gateway_tag, gateway_id = ensure_image(
                self.client,
                name,
                args,
                expect_tag=recorded.get("gateway", "") if last else "",
            )
        images = {"gateway": gateway_tag, "gateway_id": gateway_id}
        if self.wiring.network != "bridge":
            from backend.inference.gateway_wiring import ensure_egress_network

            ensure_egress_network(self.client)
        plan = harness_image_plan(adapter.image, adapter.build_args(section["version"]))
        for (name, args, _tag), role in zip(plan, ("base", "harness"), strict=True):
            tag, image_id = ensure_image(self.client, name, args, expect_tag=recorded.get(role, ""))
            images[role], images[f"{role}_id"] = tag, image_id

        self.network = self.client.networks.create(
            f"llmlab-agent-{self.short}",
            driver="bridge",
            internal=True,
            labels=self.labels,
        )
        self._start_gateway(gateway_tag)

        wall = int(agent_cfg["wall_clock_seconds"])
        self.agent = self.client.containers.create(
            images["harness"],
            command=[
                "sh",
                "-c",
                run_script(wall_clock_seconds=wall, artifacts=adapter.workspace_artifacts),
            ],
            name=f"llmlab-agent-{self.short}",
            environment={**adapter.env(section), **proxy_env(agent_cfg["network_policy"])},
            network=self.network.name,
            labels=self.labels,
            working_dir=WORKSPACE_DIR,
            mem_limit=agent_cfg["sandbox_mem_limit"],
            memswap_limit=agent_cfg["sandbox_mem_limit"],
            nano_cpus=int(agent_cfg["sandbox_nano_cpus"]),
            pids_limit=1024,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
        )
        self.agent.put_archive(WORKSPACE_DIR, make_tar(seed))
        self.agent.put_archive(OUTPUT_DIR, make_tar(adapter.files(section, brief)))

        started = time.monotonic()
        self.agent.start()
        termination = self._wait(started, wall + GRACE_SECONDS, is_cancelled)
        wall_seconds = round(time.monotonic() - started, 2)

        self.agent.reload()
        state = self.agent.attrs.get("State", {})
        exit_code = state.get("ExitCode")
        oom_killed = bool(state.get("OOMKilled"))
        outputs = self._archive(self.agent, OUTPUT_DIR)
        exit_info = _json_or_empty(outputs.get("exit.json"))
        harness_exit = exit_info.get("exit_code")
        if termination == "completed" and harness_exit == 124:  # coreutils timeout
            termination = "timeout"
        elif termination == "completed" and oom_killed:
            termination = "resource_limit"
        elif termination == "completed" and harness_exit in (137, 143):
            # Signalled without our timeout or the OOM killer: a process inside
            # the sandbox (i.e. the agent's own commands) killed the harness.
            termination = "harness_killed"
        elif termination == "completed" and harness_exit not in (0, None):
            termination = "harness_error"
        workspace_tar = outputs.pop("workspace.tar", None)
        if workspace_tar is None:
            msg = "The sandbox did not produce a workspace archive"
            raise SandboxError(msg)
        workspace = read_tar([workspace_tar], strip_prefix=".")
        logs_tail = self.agent.logs(tail=200).decode("utf-8", errors="replace")

        self.gateway.stop(timeout=5)
        gateway_logs = self._archive(self.gateway, "/logs")
        return SandboxResult(
            termination=termination,
            exit_code=harness_exit if harness_exit is not None else exit_code,
            wall_seconds=wall_seconds,
            workspace=workspace,
            outputs=outputs,
            gateway_logs=gateway_logs,
            images=images,
            container_logs_tail=logs_tail,
            oom_killed=oom_killed,
        )

    def _start_gateway(self, tag: str) -> None:
        agent_cfg = self.snapshot["agent"]
        policy = self.snapshot["gateway"]
        gateway_cfg = {
            "model": self.snapshot["llm"]["model_slug"],
            "forced_params": policy["forced_params"],
            "stripped_params": policy["stripped_params"],
            "budget": {
                "max_requests": agent_cfg["max_llm_requests"],
                "max_total_tokens": agent_cfg["max_total_tokens"],
                "max_cost_usd": agent_cfg["max_cost_usd"],
            },
            "connect_allowlist": list(PACKAGE_REGISTRY_HOSTS) if agent_cfg["network_policy"] == "packages" else [],
            "upstream": self.wiring.upstream,
            "referer": settings.OPENROUTER_HTTP_REFERER,
            "title": f"LLM Lab agent job {self.short}",
        }
        if self.wiring.upstream_timeout_s is not None:
            gateway_cfg["upstream_timeout_s"] = self.wiring.upstream_timeout_s
        self.gateway = self.client.containers.create(
            tag,
            name=f"llmlab-gateway-{self.short}",
            environment={
                "OPENROUTER_API_KEY": self.wiring.api_key,
                "GATEWAY_CONFIG": json.dumps(gateway_cfg),
                **self.wiring.extra_env,
            },
            network=self.wiring.network,
            labels=self.labels,
            mem_limit="256m",
            nano_cpus=500_000_000,
            pids_limit=256,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
        )
        self.network.connect(self.gateway, aliases=[GATEWAY_ALIAS])
        self.gateway.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.gateway.reload()
            if self.gateway.status != "running":
                logs = self.gateway.logs(tail=50).decode(errors="replace")
                msg = f"LLM gateway exited during startup: {logs[-1000:]}"
                raise SandboxError(msg)
            code, _ = self.gateway.exec_run(
                ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"],
            )
            if code == 0:
                return
            time.sleep(0.5)
        msg = "LLM gateway did not become healthy within 30s"
        raise SandboxError(msg)

    def _wait(self, started: float, limit: float, is_cancelled: Callable[[], bool]) -> str:
        import requests

        while True:
            try:
                self.agent.wait(timeout=POLL_SECONDS)
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                pass
            else:
                return "completed"
            if is_cancelled():
                self.agent.kill()
                return "cancelled"
            if time.monotonic() - started > limit:
                # The in-container timeout should have fired; this is the backstop.
                self.agent.kill()
                return "timeout"

    @staticmethod
    def _archive(container, path: str) -> dict[str, bytes]:
        try:
            stream, _ = container.get_archive(path)
        except Exception as exc:  # noqa: BLE001 - a missing dir just means nothing to collect
            logger.warning("get_archive(%s) failed: %s", path, exc)
            return {}
        return read_tar(stream, strip_prefix=Path(path).name)

    def teardown(self) -> None:
        for container in (self.agent, self.gateway):
            if container is None:
                continue
            try:
                container.remove(force=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Removing sandbox container failed: %s", exc)
        if self.network is not None:
            try:
                self.network.remove()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Removing sandbox network failed: %s", exc)


def _json_or_empty(data: bytes | None) -> dict:
    if not data:
        return {}
    try:
        value = json.loads(data)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def tar_bytes(files: dict[str, bytes]) -> bytes:
    """Compressed tar of *files* (used to keep the full run record on disk)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in sorted(files):
            info = tarfile.TarInfo(rel)
            info.size = len(files[rel])
            tar.addfile(info, io.BytesIO(files[rel]))
    return buf.getvalue()
