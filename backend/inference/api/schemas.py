"""Ninja schemas for /api/inference/ (spec §F.3). Foundation → Track B.

Mirrors ``frontend/src/lib/api/types/inference.ts``. Secrets are never
serialized: ``encrypted_api_key`` and token hashes have no field here, and
``secret_once`` only exists in the create / rotate responses. Fields marked
"owner/staff" are None unless ``access.is_endpoint_owner(user, endpoint)``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from ninja import Field
from ninja import Schema

EndpointKind = Literal["openrouter", "ollama", "openai_compatible"]
EndpointConnection = Literal["direct", "relay"]
ApiFlavor = Literal["native", "openai"]
Visibility = Literal["private", "shared"]
EndpointStatus = Literal["online", "offline", "degraded", "disabled"]


class OwnerRef(Schema):
    id: int
    username: str


class ActiveJobsCap(Schema):
    sandbox: int
    llm: int


class LiveSession(Schema):
    id: UUID
    role: Literal["active", "standby"]
    hostname: str = ""
    relay_version: str = ""
    client_ip: str | None = None
    capacity: int = 1
    last_seen_at: datetime


class LoadedModel(Schema):
    name: str
    size_vram: int | None = None
    cpu_spill: bool = False


class EndpointLive(Schema):
    session_count: int = 0
    sessions: list[LiveSession] | None = None  # owner/staff
    inflight_requests: int = 0
    queued_requests: int = 0
    active_jobs: int = 0
    loaded: list[LoadedModel] = []
    tokens_per_s_5m: float | None = None


class EndpointModelCounts(Schema):
    total: int = 0
    available: int = 0
    tools: int = 0


class EndpointSummary(Schema):
    id: int
    slug: str
    name: str
    kind: EndpointKind
    connection: EndpointConnection
    api_flavor: ApiFlavor
    enabled: bool
    is_system: bool
    visibility: Visibility
    owner: OwnerRef | None = None
    can_edit: bool = False
    has_api_key: bool = False
    api_key_prefix: str | None = None  # owner/staff
    base_url: str | None = None  # direct; owner/staff
    max_concurrency: int
    max_active_jobs_effective: ActiveJobsCap
    max_resident_models: int | None = None
    request_timeout_s: int
    num_ctx_cap: int
    keep_alive: str
    assume_tools: bool
    status: EndpointStatus
    live: EndpointLive = EndpointLive()
    models: EndpointModelCounts = EndpointModelCounts()
    last_inventory_at: datetime | None = None
    last_error: str | None = None  # owner/staff
    server_version: str = ""


class SessionOut(Schema):
    id: UUID
    role: Literal["active", "standby"]
    relay_id: UUID | None = None
    hostname: str = ""
    relay_version: str = ""
    upstream_kind: str = ""
    upstream_hint: str = ""
    capacity: int = 1
    client_ip: str | None = None
    token_prefix: str = ""
    model_count: int = 0
    inflight: int = 0
    started_at: datetime
    last_seen_at: datetime
    ended_at: datetime | None = None
    ended_reason: str = ""


class RecentError(Schema):
    at: datetime
    kind: str
    message: str


class EndpointDetail(EndpointSummary):
    sessions_recent: list[SessionOut] | None = None  # owner/staff
    recent_errors: list[RecentError] | None = None  # owner/staff


class EndpointModel(Schema):
    id: int
    model_id: str
    served_model_name: str
    model_name: str
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""
    context_window: int = 0
    num_ctx_override: int | None = None
    num_ctx_effective: int | None = None
    supports_function_calling: bool = False
    digest: str = ""
    is_available: bool = True
    is_hidden: bool = False
    loaded: bool = False
    last_used_at: datetime | None = None


class EndpointCreate(Schema):
    name: str = Field(..., max_length=120)
    slug: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9-]{1,38}$")
    kind: Literal["ollama", "openai_compatible"]
    connection: EndpointConnection = "relay"
    base_url: str | None = None
    api_key: str | None = None
    api_flavor: ApiFlavor | None = None
    max_concurrency: int = Field(1, ge=1, le=256)
    max_active_jobs: int | None = Field(None, ge=1, le=256)
    max_resident_models: int | None = Field(None, ge=1, le=64)
    request_timeout_s: int | None = Field(None, ge=30, le=7200)
    keep_alive: str | None = Field(None, max_length=16)
    num_ctx_cap: int | None = Field(None, ge=512)
    assume_tools: bool | None = None
    visibility: Visibility | None = None


class EndpointUpdate(Schema):
    """Every field optional; ``slug`` is immutable; ``api_key: ""`` clears the key."""

    name: str | None = Field(None, max_length=120)
    connection: EndpointConnection | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_flavor: ApiFlavor | None = None
    max_concurrency: int | None = Field(None, ge=1, le=256)
    max_active_jobs: int | None = Field(None, ge=1, le=256)
    max_resident_models: int | None = Field(None, ge=1, le=64)
    request_timeout_s: int | None = Field(None, ge=30, le=7200)
    keep_alive: str | None = Field(None, max_length=16)
    num_ctx_cap: int | None = Field(None, ge=512)
    assume_tools: bool | None = None
    visibility: Visibility | None = None
    enabled: bool | None = None


class TokenSecret(Schema):
    id: int
    prefix: str
    secret_once: str


class EndpointCreateResult(Schema):
    endpoint: EndpointDetail
    token: TokenSecret | None = None


class TokenOut(Schema):
    id: int
    label: str = ""
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    active_sessions: int = 0


class TokenCreate(Schema):
    label: str = Field("", max_length=80)


class TokenRotate(Schema):
    grace_minutes: int = Field(1440, ge=0, le=10080)


class TokenRotateResult(Schema):
    new: TokenSecret
    old_expires_at: datetime | None = None


class TokenRevokeResult(Schema):
    revoked_at: datetime
    sessions_ended: int = 0
    requests_requeued: int = 0


class EndpointModelUpdate(Schema):
    is_hidden: bool | None = None
    num_ctx_override: int | None = Field(None, ge=512)
    supports_function_calling: bool | None = None
    model_name: str | None = Field(None, max_length=300)


class EndpointTestBody(Schema):
    model_id: int | None = None


class UnloadBody(Schema):
    model_pk: int


class RequestRef(Schema):
    request_id: UUID


class ProbeResult(Schema):
    status: str
    latency_ms: int | None = None
    error: str | None = None
    sample: str | None = None


class RefreshResult(Schema):
    requested: bool = True


class MetricsPoint(Schema):
    minute: datetime
    inflight_avg: float = 0.0
    completed: int = 0
    tokens: int = 0
    errors: int = 0


class MetricsOut(Schema):
    points: list[MetricsPoint] = []


class InstallCommands(Schema):
    python: str
    docker: str
    systemd: str


class InstallOut(Schema):
    server_url: str
    endpoint_slug: str
    kind: str
    relay_version: str
    script_url: str
    script_sha256: str
    dockerfile_sha256: str
    commands: InstallCommands
    ollama_env: dict[str, str] = {}


class InUseError(Schema):
    detail: str = ""
    code: Literal["in_use"] = "in_use"
