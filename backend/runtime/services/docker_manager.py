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
    import docker

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
    network: str = "",
) -> str:
    """Start a container and return its container ID.

    When *network* is provided the container is attached to that Docker network
    and *ports* host-bindings are ignored (Traefik routes traffic via the
    network).  Without *network* the container uses the default bridge with the
    given *ports* mapping.
    """
    c = client()
    if c is None:
        msg = "Docker daemon unavailable"
        raise ConnectionError(msg)

    kwargs: dict[str, Any] = {
        "name": name,
        "environment": env,
        "detach": True,
        "cap_drop": ["ALL"],
        "read_only": False,
        "mem_limit": "512m",
        "cpu_period": 100000,
        "cpu_quota": 50000,
        "labels": {
            "backend.managed": "true",
            "backend.instance_id": container_instance_id,
        },
    }
    if network:
        kwargs["network"] = network
    else:
        kwargs["ports"] = ports
        kwargs["network_mode"] = "bridge"

    container = c.containers.run(image, **kwargs)
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


def exec_in(
    name: str,
    cmd: list[str] | str,
    timeout_s: int = 10,
    workdir: str | None = None,
) -> dict[str, Any]:
    """Run a command inside a running container; return exit_code + output.

    ``cmd`` may be an argv list or a shell string. When *timeout_s* is given it
    is enforced inside the container via ``timeout`` so a hung tool cannot block
    the worker thread forever.
    """
    try:
        c = client()
        if c is None:
            return {"error": "Docker daemon unavailable", "exit_code": -1, "output": ""}
        container = c.containers.get(name)
        if isinstance(cmd, str):
            run_cmd: list[str] = ["sh", "-c", cmd]
        else:
            run_cmd = list(cmd)
        if timeout_s and timeout_s > 0:
            # -s KILL (not --signal=KILL): the short form is the only one
            # BusyBox's timeout (Alpine-based images) understands; GNU
            # coreutils' timeout (analyzer-base) accepts both.
            run_cmd = ["timeout", "-s", "KILL", str(timeout_s), *run_cmd]
        result = container.exec_run(
            run_cmd,
            demux=False,
            tty=False,
            workdir=workdir,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("exec_in(%s, %s) failed: %s", name, cmd, exc)
        return {"error": str(exc), "exit_code": -1, "output": ""}
    out = result.output
    if isinstance(out, bytes):
        out = out.decode("utf-8", errors="replace")
    return {"exit_code": int(result.exit_code or 0), "output": out or ""}


def copy_files_in(name: str, files: dict[str, str], dest: str = "/work") -> dict[str, Any]:
    """Write a mapping of ``{relative_path: content}`` into *dest* in a container.

    Uses ``put_archive`` (the SDK equivalent of ``docker cp``). Returns
    ``{"status": "ok"}`` or ``{"error": ...}``.
    """
    import io
    import tarfile

    try:
        c = client()
        if c is None:
            return {"error": "Docker daemon unavailable"}
        container = c.containers.get(name)
        # Ensure the destination directory exists.
        container.exec_run(["mkdir", "-p", dest])

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for rel_path, content in files.items():
                safe = rel_path.lstrip("/").replace("..", "_")
                data = (content or "").encode("utf-8")
                info = tarfile.TarInfo(name=safe)
                info.size = len(data)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        container.put_archive(dest, buf.getvalue())
    except Exception as exc:  # noqa: BLE001
        logger.warning("copy_files_in(%s) failed: %s", name, exc)
        return {"error": str(exc)}
    else:
        return {"status": "ok"}


def run_detached(
    image: str,
    name: str,
    command: list[str] | str,
    env: dict[str, str] | None = None,
    container_instance_id: str = "",
    network: str = "",
) -> str:
    """Start a long-lived, hardened, network-isolated container and return its id.

    Used for analyzer workspaces: no ports are published, all capabilities are
    dropped, privilege escalation is disabled and a PID limit is enforced. The
    container stays alive running *command* (typically ``sleep infinity``) so
    tools can be installed and executed via :func:`exec_in`.
    """
    c = client()
    if c is None:
        msg = "Docker daemon unavailable"
        raise ConnectionError(msg)

    kwargs: dict[str, Any] = {
        "name": name,
        "command": command,
        "environment": env or {},
        "detach": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "pids_limit": 256,
        "mem_limit": "1g",
        "cpu_period": 100000,
        "cpu_quota": 100000,
        "labels": {
            "backend.managed": "true",
            "backend.kind": "analyzer-workspace",
            "backend.instance_id": container_instance_id,
        },
    }
    if network:
        kwargs["network"] = network
    container = c.containers.run(image, **kwargs)
    return container.id


def run_once_with_files(
    image: str,
    files: dict[str, str],
    command: list[str] | str,
    *,
    timeout_s: int = 30,
    workdir: str = "/work",
) -> dict[str, Any]:
    """Copy *files* into a throwaway hardened container and run *command*.

    Used for one-shot validation (e.g. syntax-checking generated code) where
    no long-lived workspace is needed: start a network-isolated container,
    write the files with :func:`copy_files_in`, run the check with
    :func:`exec_in`, then remove the container. Never raises; failures come
    back as ``{"error": ..., "exit_code": -1}``.
    """
    import uuid

    c = client()
    if c is None:
        return {"error": "Docker daemon unavailable", "exit_code": -1, "output": ""}

    name = f"validator-{uuid.uuid4().hex[:10]}"
    try:
        c.containers.run(
            image,
            name=name,
            command=["sleep", str(timeout_s + 15)],
            detach=True,
            network_disabled=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            pids_limit=128,
            mem_limit="512m",
            cpu_period=100000,
            cpu_quota=50000,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_once_with_files(%s) failed to start: %s", image, exc)
        return {"error": str(exc), "exit_code": -1, "output": ""}

    try:
        copy_result = copy_files_in(name, files, dest=workdir)
        if "error" in copy_result:
            return {"error": copy_result["error"], "exit_code": -1, "output": ""}
        return exec_in(name, command, timeout_s=timeout_s, workdir=workdir)
    finally:
        remove(name)


def image_exists(tag: str) -> bool:
    """Return True if a local image with *tag* exists."""
    try:
        c = client()
        if c is None:
            return False
        c.images.get(tag)
    except Exception:  # noqa: BLE001
        return False
    else:
        return True


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
    """List containers managed by backend (label filter)."""
    try:
        c = client()
        if c is None:
            return []
        containers = c.containers.list(
            all=True,
            filters={"label": "backend.managed=true"},
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
