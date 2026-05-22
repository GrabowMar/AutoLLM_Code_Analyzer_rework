"""Live-target preparation and teardown for analysis tasks."""

from __future__ import annotations

import logging
import socket
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_lab.analysis.models import AnalysisTask
    from llm_lab.runtime.models import ContainerInstance

logger = logging.getLogger(__name__)

# Port range used by runtime -- allowed for live-target SSRF bypass.
LIVE_TARGET_PORT_MIN = 5000
LIVE_TARGET_PORT_MAX = 9000

# Timing constants
POLL_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 3
TCP_PROBE_TIMEOUT_SECONDS = 30
TCP_PROBE_INTERVAL_SECONDS = 2


def validate_live_target_url(url: str) -> tuple[bool, str]:
    """Validate a URL for live-target analysis.

    Allows 127.0.0.1 / localhost on the runtime port range
    (LIVE_TARGET_PORT_MIN - LIVE_TARGET_PORT_MAX).  All other addresses
    fall back to the standard SSRF guard.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"Invalid URL scheme: {parsed.scheme!r}. Only http/https allowed."
    if not parsed.hostname:
        return False, "URL has no hostname."
    hostname = parsed.hostname.lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if hostname in ("127.0.0.1", "localhost"):
        if LIVE_TARGET_PORT_MIN <= port <= LIVE_TARGET_PORT_MAX:
            return True, ""
        return (
            False,
            f"Loopback port {port} is not in the allowed runtime range {LIVE_TARGET_PORT_MIN}-{LIVE_TARGET_PORT_MAX}.",
        )
    from llm_lab.analysis.services.base import validate_target_url

    return validate_target_url(url)


def _tcp_probe(host: str, port: int, timeout: float = 3.0) -> bool:
    """Return True if a TCP connection to *host*:*port* succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_probe(url: str, timeout: float = 5.0) -> bool:
    """Return True if an HTTP GET to *url* receives any response (including 4xx/5xx)."""
    import urllib.error

    try:
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout):  # noqa: S310
            return True
    except urllib.error.HTTPError:
        # 4xx/5xx still means a server is listening.
        return True
    except OSError:
        return False


def prepare_live_target(
    task: AnalysisTask,
    generation_job_id: str,
) -> tuple[ContainerInstance, str]:
    """Build + start a container for *generation_job_id*, wait until healthy.

    Polls until ``ContainerInstance.status == running`` (max
    POLL_TIMEOUT_SECONDS), then waits for the published port to accept TCP
    connections.

    Returns:
        (ContainerInstance, target_url) where *target_url* is e.g.
        ``"http://127.0.0.1:<app_port>"``.

    Side-effect:
        Saves ``container_instance_id`` into ``task.configuration`` so it
        survives even if the caller raises before we return.
    """
    from llm_lab.generation.models import GenerationJob
    from llm_lab.runtime.models import ContainerInstance
    from llm_lab.runtime.services import container_service

    job = GenerationJob.objects.get(id=generation_job_id)
    instance = container_service.build_for_job(job, user=None)

    # Persist instance id in task config before we start polling so that the
    # teardown finally-block can find it even if we raise below.
    task.configuration["container_instance_id"] = str(instance.id)
    task.save(update_fields=["configuration"])

    logger.info(
        "Waiting for container %s (id=%s) to reach running status…",
        instance.name,
        instance.id,
    )
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        instance.refresh_from_db()
        if instance.status == ContainerInstance.Status.RUNNING:
            break
        if instance.status == ContainerInstance.Status.FAILED:
            msg = f"Container {instance.name} failed to build/start."
            raise RuntimeError(msg)
        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        msg = f"Container {instance.name} did not reach running status within {POLL_TIMEOUT_SECONDS}s."
        raise TimeoutError(msg)

    port = instance.app_port
    if port is None:
        msg = f"Container {instance.name} has no allocated port."
        raise RuntimeError(msg)

    target_url = f"http://127.0.0.1:{port}"

    # Brief TCP probe to confirm port is accepting connections.
    probe_deadline = time.monotonic() + TCP_PROBE_TIMEOUT_SECONDS
    while time.monotonic() < probe_deadline:
        if _tcp_probe("127.0.0.1", port):
            break
        time.sleep(TCP_PROBE_INTERVAL_SECONDS)
    else:
        logger.warning(
            "TCP probe to 127.0.0.1:%s timed out - proceeding anyway",
            port,
        )

    logger.info("Container %s ready at %s", instance.name, target_url)
    return instance, target_url


def teardown_live_target(instance: ContainerInstance) -> None:
    """Stop and remove a live-target container.

    Errors are logged but not re-raised so that the caller's finally block
    cannot be interrupted.
    """
    from llm_lab.runtime.services import container_service

    try:
        container_service.stop_instance(instance, user=None)
        # Give the stop action a moment to complete before removing.
        time.sleep(2)
        container_service.remove_instance(instance, user=None)
        logger.info("Torn down container %s", instance.name)
    except Exception:
        logger.exception(
            "Failed to tear down live-target container %s",
            instance.name,
        )
