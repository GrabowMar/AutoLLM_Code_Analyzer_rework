"""The review sandbox: one harness container + one llm-gateway on a private network.

Topology per review sample (nested containers of apps-dind, like generation)::

    [harness]  --internal net-->  [llm-gateway]  --egress net-->  OpenRouter

The harness container is attached only to a per-sample ``internal``
network, so its only route anywhere is the gateway, which holds the real
key, pins the model, forces the frozen params, enforces the frozen review
budget and logs every exchange. No package egress: the gateway's CONNECT
allowlist is empty.

The gateway reaches OpenRouter through its own per-sample egress network
(inter-container traffic disabled), not the daemon's shared default
``bridge``: the gateway listens unauthenticated on 0.0.0.0, so on the shared
bridge any other container of apps-dind (a generated app under test, an
image build step) could post completions through it, spending this sample's
budget and landing in its call log (usage, cost, aider's final answer).
``ANALYSIS_REVIEW_GATEWAY_EGRESS = "bridge"`` restores the shared bridge, for
a daemon where a per-sample network cannot reach the internet.

Why not generation's ``AgentSandbox``: its entry script git-commits,
diffs and tars the workspace, it reads generation snapshot keys, it fails
without a workspace archive and it runs at the 3 GB generation memory cap.
Here the app copy is disposable (files read-only, never collected back), the
entry script only runs the harness under ``timeout`` and copies the findings
file to /out, and the limits come from analysis settings
(``ANALYSIS_REVIEW_SANDBOX_MEM_LIMIT``). Generation's pieces are reused
read-only: ``ensure_image`` (content-addressed, refuses a changed image
definition), ``make_tar`` / ``read_tar`` and the gateway image itself.

Every resource carries ``llmlab.analysis-review=<run id>``, the executor's
``llmlab.analysis-run=<run id>`` and ``llmlab.started``; everything is
removed in ``finally``. Leftovers of a dead worker are removed by the
executor's ``sandbox.reap`` (containers) and :func:`reap_review_sandboxes`
(containers and networks).
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings

from backend.analysis.engine.sandbox import LABEL_RUN
from backend.generation.services.agent.harnesses.base import GATEWAY_ALIAS
from backend.generation.services.agent.harnesses.base import GATEWAY_PORT
from backend.generation.services.agent.harnesses.base import OUTPUT_DIR
from backend.generation.services.agent.harnesses.base import WORKSPACE_DIR
from backend.generation.services.agent.images import GATEWAY_IMAGE
from backend.generation.services.agent.images import harness_image_plan
from backend.generation.services.agent.sandbox import SandboxError as GenerationSandboxError
from backend.generation.services.agent.sandbox import ensure_image
from backend.generation.services.agent.workspace import make_tar
from backend.generation.services.agent.workspace import read_tar
from backend.runtime.services import docker_manager

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

LABEL_REVIEW = "llmlab.analysis-review"
LABEL_SAMPLE = "llmlab.analysis-review.sample"
LABEL_STARTED = "llmlab.started"
GATEWAY_MEM_LIMIT = "256m"
GATEWAY_NANO_CPUS = 500_000_000
HARNESS_PIDS_LIMIT = 512
# The gateway's own egress network: nothing else is attached, and ICC is off besides.
EGRESS_NETWORK_OPTIONS = {"com.docker.network.bridge.enable_icc": "false"}
POLL_SECONDS = 5
# Backstop beyond the in-container ``timeout`` (which also gets 15 s to kill).
GRACE_SECONDS = 60
GATEWAY_HEALTH_SECONDS = 30
# Caps on what the entry script leaves in /out, so a runaway agent cannot
# make the collector read gigabytes back.
FINDINGS_CAP_BYTES = 4 * 1024 * 1024
OUT_FILE_CAP_BYTES = 20 * 1024 * 1024
UPSTREAM = "https://openrouter.ai/api/v1"


class ReviewSandboxError(Exception):
    """The sandbox could not be prepared (daemon, image, gateway): infrastructure, not the reviewer."""


class _Cancelled(Exception):  # noqa: N818 - control flow, not an error
    """Cancellation noticed before the harness started."""


@dataclass
class SandboxSpec:
    """Everything one review sample's sandbox needs (no database, no settings lookups)."""

    run_id: str
    index: int
    images: dict[str, Any]  # frozen tags: base, harness, gateway + harness_dir, harness_buildargs
    env: dict[str, str]
    workspace: dict[str, bytes]  # the disposable app copy (/workspace)
    out_files: dict[str, bytes]  # brief, harness.sh, configs (/out)
    gateway_config: dict[str, Any]
    wall_clock_seconds: int
    mem_limit: str
    nano_cpus: int
    findings_path: str | None = None  # in-container path copied to /out/findings.json


@dataclass
class ReviewSandboxResult:
    # completed | timeout | resource_limit | harness_killed | harness_error | cancelled | infra_error
    termination: str
    exit_code: int | None = None
    wall_seconds: float = 0.0
    outputs: dict[str, bytes] = field(default_factory=dict)
    gateway_logs: dict[str, bytes] = field(default_factory=dict)
    images: dict[str, str] = field(default_factory=dict)
    container_logs_tail: str = ""
    oom_killed: bool = False
    started: bool = False  # the harness container started (LLM spend possible)
    error: str = ""


def review_entry_script(*, wall_clock_seconds: int, findings_path: str | None) -> str:
    """Container entry script: run the harness under ``timeout``, keep its outputs.

    No git, no diff, no workspace archive: the app copy is disposable and only
    the findings document, the harness's own logs and exit status matter.
    """
    copy_findings = ""
    if findings_path:
        findings_dir = str(Path(findings_path).parent)
        copy_findings = (
            f"if [ -f {findings_path} ]; then head -c {FINDINGS_CAP_BYTES} {findings_path} "
            f"> {OUTPUT_DIR}/findings.json; fi\n"
        )
        prepare = f"mkdir -p {findings_dir}\nrm -f {findings_path} {OUTPUT_DIR}/findings.json\n"
    else:
        prepare = f"rm -f {OUTPUT_DIR}/findings.json\n"
    cap_kib = OUT_FILE_CAP_BYTES // 1024
    return f"""
set -u
cd {WORKSPACE_DIR}
{prepare}start=$(date +%s)
timeout -s TERM -k 15 {int(wall_clock_seconds)} sh {OUTPUT_DIR}/harness.sh \\
  > {OUTPUT_DIR}/events.jsonl 2> {OUTPUT_DIR}/harness.log
code=$?
end=$(date +%s)
echo "{{\\"exit_code\\": $code, \\"agent_seconds\\": $((end - start))}}" > {OUTPUT_DIR}/exit.json
{copy_findings}find {OUTPUT_DIR} -maxdepth 1 -type f -size +{cap_kib}k \\
  -exec truncate -s {OUT_FILE_CAP_BYTES} {{}} + 2>/dev/null
exit 0
"""


class ReviewSandbox:
    """One review sample's network + gateway + harness containers (always torn down)."""

    def __init__(self, spec: SandboxSpec, *, api_key: str, client=None) -> None:
        self.spec = spec
        self.api_key = api_key
        self._own_client = client is None
        self.client = client
        suffix = secrets.token_hex(3)
        short = spec.run_id.replace("-", "")[:12]
        self.name = f"llmlab-review-{short}-{spec.index}-{suffix}"
        self.labels = {
            LABEL_REVIEW: spec.run_id,
            # The executor's own label too: its reconcile() reaps the containers
            # of runs that are no longer live, and capacity() counts them as analysis.
            LABEL_RUN: spec.run_id,
            LABEL_SAMPLE: str(spec.index),
            LABEL_STARTED: str(int(time.time())),
        }
        self.network = None
        self.egress = None
        self.gateway = None
        self.harness = None

    # -- lifecycle -------------------------------------------------------

    def run(
        self,
        *,
        is_cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> ReviewSandboxResult:
        """Run the sample; infrastructure failures come back as ``termination="infra_error"``."""
        result = ReviewSandboxResult(termination="infra_error")
        try:
            if self.client is None:
                self.client = docker_manager.new_client()
            self._run(result, is_cancelled=is_cancelled, heartbeat=heartbeat)
        except _Cancelled:
            result.termination = "cancelled"
        except (ReviewSandboxError, GenerationSandboxError) as exc:
            result.termination = "infra_error"
            result.error = str(exc)
        except Exception as exc:
            logger.exception("Review sandbox %s failed", self.name)
            result.termination = "infra_error"
            result.error = f"Review sandbox failed: {exc}"
        finally:
            # Evidence first (spend + transcript survive cancels and crashes), then cleanup.
            if not result.gateway_logs and self.gateway is not None:
                result.gateway_logs = self._collect_gateway_logs()
            self.teardown()
        return result

    def _run(
        self,
        result: ReviewSandboxResult,
        *,
        is_cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> None:
        spec = self.spec
        result.images = self._ensure_images()
        if is_cancelled():
            raise _Cancelled

        self.network = self.client.networks.create(self.name, driver="bridge", internal=True, labels=self.labels)
        if getattr(settings, "ANALYSIS_REVIEW_GATEWAY_EGRESS", "isolated") != "bridge":
            self.egress = self.client.networks.create(
                f"{self.name}-egress",
                driver="bridge",
                internal=False,
                labels=self.labels,
                options=dict(EGRESS_NETWORK_OPTIONS),
            )
        self._start_gateway(result.images["gateway"], is_cancelled=is_cancelled, heartbeat=heartbeat)

        script = review_entry_script(wall_clock_seconds=spec.wall_clock_seconds, findings_path=spec.findings_path)
        self.harness = self.client.containers.create(
            result.images["harness"],
            command=["sh", "-c", script],
            name=f"{self.name}-agent",
            environment=dict(spec.env),
            network=self.network.name,
            labels=self.labels,
            working_dir=WORKSPACE_DIR,
            mem_limit=spec.mem_limit,
            memswap_limit=spec.mem_limit,
            nano_cpus=int(spec.nano_cpus),
            pids_limit=HARNESS_PIDS_LIMIT,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
        )
        # Read-only app files: the review must not depend on (or be confused by)
        # edits; the copy is thrown away either way.
        self.harness.put_archive(WORKSPACE_DIR, make_tar(spec.workspace, mode=0o444))
        self.harness.put_archive(OUTPUT_DIR, make_tar(spec.out_files))
        if is_cancelled():
            raise _Cancelled

        started = time.monotonic()
        self.harness.start()
        result.started = True
        termination = self._wait(started, spec.wall_clock_seconds + GRACE_SECONDS, is_cancelled, heartbeat)
        result.wall_seconds = round(time.monotonic() - started, 2)

        self.harness.reload()
        state = self.harness.attrs.get("State", {})
        result.oom_killed = bool(state.get("OOMKilled"))
        result.outputs = self._archive(self.harness, OUTPUT_DIR)
        exit_info = _json_object(result.outputs.get("exit.json"))
        harness_exit = exit_info.get("exit_code")
        result.exit_code = harness_exit if isinstance(harness_exit, int) else state.get("ExitCode")
        result.termination = classify(termination, harness_exit=harness_exit, oom_killed=result.oom_killed)
        try:
            result.container_logs_tail = self.harness.logs(tail=200).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - logs are evidence, not a requirement
            logger.warning("Reading review container logs failed: %s", exc)
        result.gateway_logs = self._collect_gateway_logs()

    def _ensure_images(self) -> dict[str, str]:
        recorded = self.spec.images
        gateway_tag, gateway_id = ensure_image(self.client, GATEWAY_IMAGE, expect_tag=recorded.get("gateway", ""))
        images = {"gateway": gateway_tag, "gateway_id": gateway_id}
        plan = harness_image_plan(recorded["harness_dir"], dict(recorded.get("harness_buildargs") or {}))
        for (name, args, _tag), role in zip(plan, ("base", "harness"), strict=True):
            tag, image_id = ensure_image(self.client, name, args, expect_tag=recorded.get(role, ""))
            images[role], images[f"{role}_id"] = tag, image_id
        return images

    def _start_gateway(self, tag: str, *, is_cancelled: Callable[[], bool], heartbeat: Callable[[], None]) -> None:
        self.gateway = self.client.containers.create(
            tag,
            name=f"{self.name}-gw",
            environment={"OPENROUTER_API_KEY": self.api_key, "GATEWAY_CONFIG": json.dumps(self.spec.gateway_config)},
            network=self.egress.name if self.egress is not None else "bridge",
            labels=self.labels,
            mem_limit=GATEWAY_MEM_LIMIT,
            memswap_limit=GATEWAY_MEM_LIMIT,
            nano_cpus=GATEWAY_NANO_CPUS,
            pids_limit=256,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
        )
        self.network.connect(self.gateway, aliases=[GATEWAY_ALIAS])
        self.gateway.start()
        probe = f"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{GATEWAY_PORT}/healthz')"
        deadline = time.monotonic() + GATEWAY_HEALTH_SECONDS
        while time.monotonic() < deadline:
            heartbeat()
            if is_cancelled():
                raise _Cancelled
            self.gateway.reload()
            if self.gateway.status != "running":
                logs = self.gateway.logs(tail=50).decode(errors="replace")
                msg = f"LLM gateway exited during startup: {logs[-1000:]}"
                raise ReviewSandboxError(msg)
            code, _ = self.gateway.exec_run(["python", "-c", probe])
            if code == 0:
                return
            time.sleep(0.5)
        msg = f"LLM gateway did not become healthy within {GATEWAY_HEALTH_SECONDS}s"
        raise ReviewSandboxError(msg)

    def _wait(
        self,
        started: float,
        limit: float,
        is_cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> str:
        import requests

        while True:
            try:
                self.harness.wait(timeout=POLL_SECONDS)
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                pass
            else:
                return "completed"
            heartbeat()
            if is_cancelled():
                self.harness.kill()
                return "cancelled"
            if time.monotonic() - started > limit:
                # The in-container timeout should have fired; this is the backstop.
                self.harness.kill()
                return "timeout"

    def _collect_gateway_logs(self) -> dict[str, bytes]:
        try:
            self.gateway.stop(timeout=5)
        except Exception as exc:  # noqa: BLE001 - logs may still be readable from a stopped/dead container
            logger.warning("Stopping the review gateway failed: %s", exc)
        return self._archive(self.gateway, "/logs")

    @staticmethod
    def _archive(container, path: str) -> dict[str, bytes]:
        try:
            stream, _ = container.get_archive(path)
            return read_tar(stream, strip_prefix=Path(path).name)
        except Exception as exc:  # noqa: BLE001 - a missing dir just means nothing to collect
            logger.warning("get_archive(%s) failed: %s", path, exc)
            return {}

    def teardown(self) -> None:
        for container in (self.harness, self.gateway):
            if container is None:
                continue
            try:
                container.remove(force=True)
            except Exception as exc:  # noqa: BLE001 - reap_review_sandboxes catches what is left
                logger.warning("Removing review container failed: %s", exc)
        for network in (self.network, self.egress):
            if network is None:
                continue
            try:
                network.remove()
            except Exception as exc:  # noqa: BLE001 - reap_review_sandboxes catches what is left
                logger.warning("Removing review network failed: %s", exc)
        if self._own_client and self.client is not None:
            with contextlib.suppress(Exception):  # closing an HTTP pool cannot matter here
                self.client.close()


def classify(termination: str, *, harness_exit: Any, oom_killed: bool) -> str:  # noqa: PLR0911 - one per class
    """How the harness ended (generation's taxonomy, minus workspace semantics)."""
    if termination != "completed":
        return termination  # cancelled / timeout (backstop)
    if harness_exit == 124:  # coreutils timeout
        return "timeout"
    if oom_killed:
        return "resource_limit"
    if harness_exit in (137, 143):
        # Signalled without our timeout or the OOM killer: something inside
        # the sandbox (the agent's own commands) killed the harness.
        return "harness_killed"
    if harness_exit is None:
        return "harness_error"  # the entry script never wrote exit.json
    if harness_exit != 0:
        return "harness_error"
    return "completed"


def gateway_config(
    *,
    model_slug: str,
    forced_params: dict[str, Any],
    stripped_params: list[str],
    budget: dict[str, Any],
    title: str,
) -> dict[str, Any]:
    """``GATEWAY_CONFIG`` for a review: pinned model, frozen params, review budget, no egress."""
    gateway_budget = {"max_requests": int(budget["max_llm_requests"]), "max_cost_usd": float(budget["max_cost_usd"])}
    if budget.get("max_total_tokens"):
        gateway_budget["max_total_tokens"] = int(budget["max_total_tokens"])
    return {
        "model": model_slug,
        "forced_params": forced_params,
        "stripped_params": sorted(stripped_params),
        "budget": gateway_budget,
        "connect_allowlist": [],
        "upstream": UPSTREAM,
        "referer": getattr(settings, "OPENROUTER_HTTP_REFERER", "https://llm-lab.local"),
        "title": title,
    }


def reap_review_sandboxes(*, live_run_ids: set[str], older_than_s: int = 0, client=None) -> dict[str, int]:
    """Remove review containers/networks whose run is not live (``executor.reconcile`` / maintenance).

    Returns ``{"containers": n, "networks": n}``. A network still attached to
    a live container is kept.
    """
    client = client or docker_manager.new_client()
    now = time.time()
    removed = {"containers": 0, "networks": 0}
    for container in client.containers.list(all=True, filters={"label": LABEL_REVIEW}):
        labels = container.labels or {}
        if labels.get(LABEL_REVIEW) in live_run_ids:
            continue
        started = labels.get(LABEL_STARTED) or "0"
        try:
            age = now - float(started)
        except ValueError:
            age = now
        if age < older_than_s:
            continue
        try:
            container.remove(force=True)
            removed["containers"] += 1
        except Exception as exc:  # noqa: BLE001 - best effort; the next sweep retries
            logger.warning("Reaping review container %s failed: %s", container.name, exc)
    for network in client.networks.list(filters={"label": LABEL_REVIEW}):
        if (network.attrs.get("Labels") or {}).get(LABEL_REVIEW) in live_run_ids:
            continue
        try:
            network.remove()
            removed["networks"] += 1
        except Exception as exc:  # noqa: BLE001 - still in use
            logger.debug("Keeping review network %s: %s", network.name, exc)
    return removed


def _json_object(data: bytes | None) -> dict[str, Any]:
    if not data:
        return {}
    try:
        value = json.loads(data)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
