"""Thin wrapper around the Docker SDK."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_docker_client = None


def client():
    """Return a cached Docker client, or None if daemon unavailable."""
    global _docker_client  # noqa: PLW0603
    if _docker_client is not None:
        return _docker_client
    try:
        import docker

        _docker_client = docker.from_env()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Docker client unavailable: %s", exc)
        return None
    else:
        return _docker_client


def ping() -> bool:
    """Return True if the Docker daemon is reachable."""
    try:
        c = client()
        if c is None:
            return False
        c.ping()
        return True
    except Exception:  # noqa: BLE001
        return False


def build_image(path: str, tag: str, dockerfile: str | None = None) -> tuple:
    """Build a Docker image from the given directory.

    Returns ``(image, log_text)`` on success.
    Raises ``docker.errors.BuildError`` (with ``build_log`` attribute) on failure;
    callers can use ``extract_build_error(exc)`` to get a human-readable summary.
    """
    import docker  # noqa: PLC0415

    c = client()
    if c is None:
        msg = "Docker daemon unavailable"
        raise ConnectionError(msg)
    kwargs: dict[str, Any] = {
        "path": path,
        "tag": tag,
        "rm": True,
        "decode": True,  # get dicts instead of raw bytes
    }
    if dockerfile:
        kwargs["dockerfile"] = dockerfile

    log_lines: list[str] = []
    build_errors: list[str] = []
    try:
        for chunk in c.api.build(**kwargs):
            stream = chunk.get("stream", "")
            if stream:
                log_lines.append(stream)
            error = chunk.get("error", "")
            if error:
                log_lines.append(f"ERROR: {error}\n")
                build_errors.append(error)
    except docker.errors.BuildError as exc:
        exc._collected_log = "".join(log_lines)  # noqa: SLF001
        raise

    if build_errors:
        # Stream had error chunks but no BuildError raised — construct one
        combined_log = "".join(log_lines)
        exc = docker.errors.BuildError(build_errors[-1], combined_log)
        exc._collected_log = combined_log  # noqa: SLF001
        raise exc

    # Use high-level client to get the image object
    image = c.images.get(tag)
    return image, "".join(log_lines)


def extract_build_error(exc) -> str:
    """Extract a readable error summary from a docker BuildError."""
    collected = getattr(exc, "_collected_log", "")
    if collected:
        # Return last 3000 chars of captured log — most relevant part
        return collected[-3000:] if len(collected) > 3000 else collected
    # Fall back to the SDK's build_log attribute
    lines: list[str] = []
    for entry in getattr(exc, "build_log", []):
        if isinstance(entry, dict):
            lines.append(entry.get("stream", "") + entry.get("error", ""))
        else:
            lines.append(str(entry))
    text = "".join(lines)
    return text[-3000:] if len(text) > 3000 else text


def run_container(
    image: str,
    name: str,
    ports: dict[str, int],
    env: dict[str, str],
    container_instance_id: str = "",
) -> str:
    """Start a container and return its container ID."""
    c = client()
    if c is None:
        msg = "Docker daemon unavailable"
        raise ConnectionError(msg)
    container = c.containers.run(
        image,
        name=name,
        ports=ports,
        environment=env,
        detach=True,
        cap_drop=["ALL"],
        read_only=False,
        mem_limit="512m",
        cpu_period=100000,
        cpu_quota=50000,
        network_mode="bridge",
        labels={
            "llm_lab.managed": "true",
            "llm_lab.instance_id": container_instance_id,
        },
    )
    return container.id


def stop(name: str) -> dict[str, Any]:
    """Stop a running container by name."""
    try:
        c = client()
        if c is None:
            return {"error": "Docker daemon unavailable"}
        container = c.containers.get(name)
        container.stop(timeout=10)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stop(%s) failed: %s", name, exc)
        return {"error": str(exc)}
    else:
        return {"status": "stopped"}


def start(name: str) -> dict[str, Any]:
    """Start a stopped container by name."""
    try:
        c = client()
        if c is None:
            return {"error": "Docker daemon unavailable"}
        container = c.containers.get(name)
        container.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("start(%s) failed: %s", name, exc)
        return {"error": str(exc)}
    else:
        return {"status": "started"}


def restart(name: str) -> dict[str, Any]:
    """Restart a container by name."""
    try:
        c = client()
        if c is None:
            return {"error": "Docker daemon unavailable"}
        container = c.containers.get(name)
        container.restart(timeout=10)
    except Exception as exc:  # noqa: BLE001
        logger.warning("restart(%s) failed: %s", name, exc)
        return {"error": str(exc)}
    else:
        return {"status": "restarted"}


def remove(name: str, *, force: bool = True) -> dict[str, Any]:
    """Remove a container by name."""
    try:
        c = client()
        if c is None:
            return {"error": "Docker daemon unavailable"}
        container = c.containers.get(name)
        container.remove(force=force)
    except Exception as exc:  # noqa: BLE001
        logger.warning("remove(%s) failed: %s", name, exc)
        return {"error": str(exc)}
    else:
        return {"status": "removed"}


def logs(name: str, tail: int = 200) -> str:
    """Fetch last *tail* lines of logs from a container."""
    try:
        c = client()
        if c is None:
            return "Docker daemon unavailable"
        container = c.containers.get(name)
        raw = container.logs(tail=tail, stream=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("logs(%s) failed: %s", name, exc)
        return f"Error fetching logs: {exc}"
    else:
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)


def inspect(name: str) -> dict[str, Any]:
    """Return low-level container info."""
    try:
        c = client()
        if c is None:
            return {"error": "Docker daemon unavailable"}
        container = c.containers.get(name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("inspect(%s) failed: %s", name, exc)
        return {"error": str(exc)}
    else:
        return container.attrs or {}


def exec_in(name: str, cmd: list[str], timeout_s: int = 10) -> dict[str, Any]:
    """Run a short command inside a running container; return exit_code + output."""
    try:
        c = client()
        if c is None:
            return {"error": "Docker daemon unavailable", "exit_code": -1, "output": ""}
        container = c.containers.get(name)
        result = container.exec_run(cmd, demux=False, tty=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("exec_in(%s, %s) failed: %s", name, cmd, exc)
        return {"error": str(exc), "exit_code": -1, "output": ""}
    out = result.output
    if isinstance(out, bytes):
        out = out.decode("utf-8", errors="replace")
    return {"exit_code": int(result.exit_code or 0), "output": out or ""}


def health(name: str) -> dict[str, Any]:
    """Return container health state."""
    try:
        c = client()
        if c is None:
            return {"error": "Docker daemon unavailable"}
        container = c.containers.get(name)
        state = (container.attrs or {}).get("State", {})
        health_info = state.get("Health", {})
        return {
            "status": container.status,
            "health": health_info.get("Status", "unknown"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("health(%s) failed: %s", name, exc)
        return {"error": str(exc)}


def list_managed() -> list[dict[str, Any]]:
    """List containers managed by llm_lab (label filter)."""
    try:
        c = client()
        if c is None:
            return []
        containers = c.containers.list(
            all=True,
            filters={"label": "llm_lab.managed=true"},
        )
        return [
            {
                "id": ct.short_id,
                "name": ct.name,
                "status": ct.status,
                "labels": ct.labels,
            }
            for ct in containers
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_managed failed: %s", exc)
        return []
