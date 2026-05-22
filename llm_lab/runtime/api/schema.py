"""Pydantic schemas for the runtime API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ninja import ModelSchema
from ninja import Schema

from llm_lab.runtime.models import ContainerAction
from llm_lab.runtime.models import ContainerInstance


class ContainerInstanceSchema(ModelSchema):
    id: UUID
    generation_job_id: UUID | None = None
    created_by_id: int | None = None
    last_error: str = ""
    app_url: str | None = None

    class Meta:
        model = ContainerInstance
        fields = (
            "name",
            "image",
            "container_id",
            "status",
            "app_port",
            "health_status",
            "last_health_check",
            "config",
            "metadata",
            "created_at",
            "updated_at",
        )

    @staticmethod
    def resolve_last_error(obj: ContainerInstance) -> str:
        last_failed = (
            obj.actions.filter(status=ContainerAction.Status.FAILED)
            .order_by("-created_at")
            .values_list("error_message", flat=True)
            .first()
        )
        return last_failed or ""

    @staticmethod
    def resolve_app_url(obj: ContainerInstance) -> str | None:
        if not obj.app_port:
            return None
        from django.conf import settings
        if getattr(settings, "TRAEFIK_DYNAMIC_DIR", ""):
            domain = getattr(settings, "DJANGO_DOMAIN", "localhost")
            return f"https://{domain}:{obj.app_port}"
        host = getattr(settings, "CONTAINER_APP_HOST", "") or getattr(settings, "DJANGO_DOMAIN", "localhost")
        return f"http://{host}:{obj.app_port}"


class ContainerActionSchema(ModelSchema):
    id: UUID
    container_id: UUID | None = None
    triggered_by_id: int | None = None

    class Meta:
        model = ContainerAction
        fields = (
            "action_id",
            "action_type",
            "status",
            "progress_percent",
            "output",
            "error_message",
            "exit_code",
            "started_at",
            "completed_at",
            "created_at",
        )


class ContainerListResponse(Schema):
    containers: list[ContainerInstanceSchema]
    pagination: dict[str, int]


class ActionListResponse(Schema):
    actions: list[ContainerActionSchema]
    pagination: dict[str, int]


class DockerInfo(Schema):
    daemon_available: bool
    version: str | None = None
    os: str | None = None
    arch: str | None = None


class ContainerHealthResponse(Schema):
    status: str
    health: str
    last_check: datetime | None


class ContainerInspectResponse(Schema):
    image: str = ""
    command: list[str] = []
    state: str = ""
    started_at: str = ""
    finished_at: str = ""
    env: dict[str, str] = {}
    mounts: list[dict] = []
    ports: dict = {}
    error: str = ""


class ContainerExecRequest(Schema):
    action: str  # whitelisted key


class ContainerExecResponse(Schema):
    action: str
    cmd: list[str]
    exit_code: int
    output: str
    error: str = ""


class GenericResponse(Schema):
    success: bool
    message: str = ""


__all__ = [
    "ActionListResponse",
    "ContainerActionSchema",
    "ContainerExecRequest",
    "ContainerExecResponse",
    "ContainerHealthResponse",
    "ContainerInspectResponse",
    "ContainerInstanceSchema",
    "ContainerListResponse",
    "DockerInfo",
    "GenericResponse",
]

_ = (datetime,)
