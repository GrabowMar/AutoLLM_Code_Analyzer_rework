"""Django Ninja API views for runtime container management."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.shortcuts import get_object_or_404
from ninja import Query
from ninja import Router

from backend.common.pagination import paginate_queryset
from backend.generation.models import GenerationJob
from backend.runtime.api.schema import ActionListResponse
from backend.runtime.api.schema import ContainerActionSchema
from backend.runtime.api.schema import ContainerExecRequest
from backend.runtime.api.schema import ContainerExecResponse
from backend.runtime.api.schema import ContainerHealthResponse
from backend.runtime.api.schema import ContainerInspectResponse
from backend.runtime.api.schema import ContainerInstanceSchema
from backend.runtime.api.schema import ContainerListResponse
from backend.runtime.api.schema import DockerInfo
from backend.runtime.api.schema import GenericResponse
from backend.runtime.models import ContainerAction
from backend.runtime.models import ContainerInstance
from backend.runtime.services import container_service
from backend.runtime.services import docker_manager

if TYPE_CHECKING:
    from django.http import HttpRequest

router = Router(tags=["runtime"])


@router.get(
    "/docker/info/",
    response={200: DockerInfo, 503: GenericResponse},
)
def get_docker_info(request: HttpRequest):
    """Return Docker daemon availability and version info."""
    if not docker_manager.ping():
        return 503, GenericResponse(success=False, message="Docker daemon unavailable")
    c = docker_manager.client()
    info: dict = {}
    version: dict = {}
    try:
        info = c.info()
        version = c.version()
    except Exception:  # noqa: BLE001, S110
        pass  # best-effort; daemon_available already confirmed True
    return 200, DockerInfo(
        daemon_available=True,
        version=version.get("Version"),
        os=info.get("OSType"),
        arch=info.get("Architecture"),
    )


@router.get(
    "/containers/",
    response={200: ContainerListResponse},
)
def list_containers(
    request: HttpRequest,
    status: str | None = None,
    job_id: str | None = None,
    page: int = Query(1),
    per_page: int = Query(20),
):
    """List ContainerInstance rows with optional filters."""
    qs = ContainerInstance.objects.select_related("created_by", "generation_job")
    if status:
        qs = qs.filter(status=status)
    if job_id:
        qs = qs.filter(generation_job_id=job_id)
    items, total, page_num, total_pages = paginate_queryset(qs, page, per_page)
    return 200, ContainerListResponse(
        containers=list(items),
        pagination={
            "total": total,
            "page": page_num,
            "per_page": per_page,
            "total_pages": total_pages,
        },
    )


@router.get(
    "/containers/{container_id}/",
    response={200: ContainerInstanceSchema, 404: GenericResponse},
)
def get_container(request: HttpRequest, container_id: str):
    """Return detail for a single ContainerInstance."""
    instance = get_object_or_404(ContainerInstance, id=container_id)
    return 200, instance


@router.post(
    "/jobs/{job_id}/build/",
    response={200: ContainerInstanceSchema, 400: GenericResponse, 503: GenericResponse},
)
def build_container_for_job(request: HttpRequest, job_id: str):
    """Kick off a container build for the given generation job."""
    if not docker_manager.ping():
        return 503, GenericResponse(success=False, message="Docker daemon unavailable")
    job = get_object_or_404(GenerationJob, id=job_id)
    instance = container_service.build_for_job(job, request.auth)
    return 200, instance


@router.post(
    "/containers/{container_id}/start/",
    response={200: ContainerActionSchema, 409: GenericResponse, 503: GenericResponse},
)
def start_container(request: HttpRequest, container_id: str):
    """Start a stopped container."""
    if not docker_manager.ping():
        return 503, GenericResponse(success=False, message="Docker daemon unavailable")
    instance = get_object_or_404(ContainerInstance, id=container_id)
    if instance.status == ContainerInstance.Status.FAILED:
        last_err = (
            instance.actions.filter(status="failed")
            .order_by("-created_at")
            .values_list("error_message", flat=True)
            .first()
        )
        return 409, GenericResponse(
            success=False,
            message=(
                "Container is in a failed state. Rebuild it before starting."
                + (f" Last error: {last_err}" if last_err else "")
            ),
        )
    if instance.status in (
        ContainerInstance.Status.PENDING,
        ContainerInstance.Status.BUILDING,
    ):
        return 409, GenericResponse(
            success=False,
            message=f"Container is {instance.status}; wait for build to complete.",
        )
    action = container_service.start_instance(instance, request.auth)
    return 200, action


@router.post(
    "/containers/{container_id}/stop/",
    response={200: ContainerActionSchema, 503: GenericResponse},
)
def stop_container(request: HttpRequest, container_id: str):
    """Stop a running container."""
    if not docker_manager.ping():
        return 503, GenericResponse(success=False, message="Docker daemon unavailable")
    instance = get_object_or_404(ContainerInstance, id=container_id)
    action = container_service.stop_instance(instance, request.auth)
    return 200, action


@router.post(
    "/containers/{container_id}/restart/",
    response={200: ContainerActionSchema, 409: GenericResponse, 503: GenericResponse},
)
def restart_container(request: HttpRequest, container_id: str):
    """Restart a container."""
    if not docker_manager.ping():
        return 503, GenericResponse(success=False, message="Docker daemon unavailable")
    instance = get_object_or_404(ContainerInstance, id=container_id)
    if instance.status == ContainerInstance.Status.FAILED:
        return 409, GenericResponse(
            success=False,
            message="Container is in a failed state. Rebuild it before restarting.",
        )
    action = container_service.restart_instance(instance, request.auth)
    return 200, action


@router.post(
    "/containers/{container_id}/remove/",
    response={200: ContainerActionSchema, 503: GenericResponse},
)
def remove_container(request: HttpRequest, container_id: str):
    """Remove a container (force=True)."""
    if not docker_manager.ping():
        return 503, GenericResponse(success=False, message="Docker daemon unavailable")
    instance = get_object_or_404(ContainerInstance, id=container_id)
    action = container_service.remove_instance(instance, request.auth)
    return 200, action


@router.get(
    "/containers/{container_id}/logs/",
    response={200: str, 503: GenericResponse},
)
def get_container_logs(
    request: HttpRequest,
    container_id: str,
    tail: int = Query(200),
):
    """Return last *tail* lines of container logs as plain text."""
    if not docker_manager.ping():
        return 503, GenericResponse(success=False, message="Docker daemon unavailable")
    instance = get_object_or_404(ContainerInstance, id=container_id)
    output = docker_manager.logs(instance.name, tail=tail)
    return 200, output


@router.get(
    "/containers/{container_id}/health/",
    response={200: ContainerHealthResponse, 503: GenericResponse},
)
def get_container_health(request: HttpRequest, container_id: str):
    """Return current health state of a container."""
    if not docker_manager.ping():
        return 503, GenericResponse(success=False, message="Docker daemon unavailable")
    instance = get_object_or_404(ContainerInstance, id=container_id)
    data = docker_manager.health(instance.name)
    if "error" in data:
        return 503, GenericResponse(success=False, message=data["error"])
    return 200, ContainerHealthResponse(
        status=data.get("status", "unknown"),
        health=data.get("health", "unknown"),
        last_check=instance.last_health_check,
    )


@router.get(
    "/actions/",
    response={200: ActionListResponse},
)
def list_actions(
    request: HttpRequest,
    container_id: str | None = None,
    status: str | None = None,
    page: int = Query(1),
    per_page: int = Query(20),
):
    """List ContainerAction rows with optional filters."""
    qs = ContainerAction.objects.select_related("container", "triggered_by")
    if container_id:
        qs = qs.filter(container_id=container_id)
    if status:
        qs = qs.filter(status=status)
    items, total, page_num, total_pages = paginate_queryset(qs, page, per_page)
    return 200, ActionListResponse(
        actions=list(items),
        pagination={
            "total": total,
            "page": page_num,
            "per_page": per_page,
            "total_pages": total_pages,
        },
    )


@router.get(
    "/actions/{action_id}/",
    response={200: ContainerActionSchema, 404: GenericResponse},
)
def get_action(request: HttpRequest, action_id: str):
    """Return detail for a single ContainerAction."""
    action = get_object_or_404(ContainerAction, action_id=action_id)
    return 200, action


# ---- whitelisted exec commands ----
_SENSITIVE_ENV_PATTERNS = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASS",
    "DSN",
    "API",
)
_EXEC_WHITELIST: dict[str, list[str]] = {
    "health": [
        "sh",
        "-c",
        (
            "curl -sf http://127.0.0.1:8000/health 2>/dev/null "
            "|| curl -sf http://127.0.0.1:5000/health 2>/dev/null "
            "|| echo 'no /health endpoint'"
        ),
    ],
    "structure": [
        "sh",
        "-c",
        "ls -la /app 2>/dev/null || ls -la /code 2>/dev/null || ls -la /",
    ],
    "disk": ["sh", "-c", "df -h / && echo --- && du -sh /app/* 2>/dev/null | head -20"],
    "environment": ["sh", "-c", "env | sort"],
    "processes": ["sh", "-c", "ps aux 2>/dev/null || ps -ef"],
}


def _mask_env(env_list: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in env_list or []:
        if "=" not in entry:
            continue
        k, _, v = entry.partition("=")
        key_upper = k.upper()
        if any(p in key_upper for p in _SENSITIVE_ENV_PATTERNS):
            out[k] = "••••••••" if v else ""
        else:
            out[k] = v
    return out


@router.get(
    "/containers/{container_id}/inspect/",
    response={200: ContainerInspectResponse, 503: GenericResponse},
)
def inspect_container(request: HttpRequest, container_id: str):
    """Return image, command, masked env, mounts, ports for a container."""
    if not docker_manager.ping():
        return 503, GenericResponse(success=False, message="Docker daemon unavailable")
    instance = get_object_or_404(ContainerInstance, id=container_id)
    data = docker_manager.inspect(instance.name)
    if "error" in data:
        return 200, ContainerInspectResponse(error=str(data["error"]))
    config = data.get("Config") or {}
    state = data.get("State") or {}
    network = data.get("NetworkSettings") or {}
    return 200, ContainerInspectResponse(
        image=config.get("Image", "") or "",
        command=list(config.get("Cmd") or []),
        state=state.get("Status", "") or "",
        started_at=state.get("StartedAt", "") or "",
        finished_at=state.get("FinishedAt", "") or "",
        env=_mask_env(list(config.get("Env") or [])),
        mounts=list(data.get("Mounts") or []),
        ports=dict(network.get("Ports") or {}),
    )


@router.post(
    "/containers/{container_id}/exec/",
    response={200: ContainerExecResponse, 400: GenericResponse, 503: GenericResponse},
)
def exec_container_command(
    request: HttpRequest,
    container_id: str,
    payload: ContainerExecRequest,
):
    """Run a whitelisted diagnostic command inside the container."""
    if not docker_manager.ping():
        return 503, GenericResponse(success=False, message="Docker daemon unavailable")
    cmd = _EXEC_WHITELIST.get(payload.action)
    if not cmd:
        return 400, GenericResponse(
            success=False,
            message=f"Unknown action; allowed: {sorted(_EXEC_WHITELIST)}",
        )
    instance = get_object_or_404(ContainerInstance, id=container_id)
    result = docker_manager.exec_in(instance.name, cmd, timeout_s=10)
    return 200, ContainerExecResponse(
        action=payload.action,
        cmd=cmd,
        exit_code=int(result.get("exit_code", -1)),
        output=str(result.get("output", "")),
        error=str(result.get("error", "")),
    )
