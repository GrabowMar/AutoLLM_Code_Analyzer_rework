"""/api/inference/ — endpoints, models, tokens, sessions, metrics, install (spec §F.3).

Visibility: an endpoint is visible when it is the system row, shared, or
owned by the user (staff: all); invisible endpoints are 404. Mutations need
the owner or staff. Operational fields (base URL, key prefix, sessions, IPs,
hostnames, errors) are serialized only for the owner or staff. Secrets are
never serialized: ``secret_once`` exists only in the create/rotate responses.
Nested objects (models, tokens, probe requests) are always looked up scoped
to the endpoint in the URL.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import replace
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from ninja import Query
from ninja import Router
from ninja.errors import HttpError
from ninja.responses import Status

from backend.inference import tokens as relay_tokens
from backend.inference.access import endpoint_access
from backend.inference.access import is_endpoint_owner
from backend.inference.access import owner_label
from backend.inference.access import visible_endpoints
from backend.inference.api.schemas import EndpointCreate
from backend.inference.api.schemas import EndpointCreateResult
from backend.inference.api.schemas import EndpointDetail
from backend.inference.api.schemas import EndpointModel
from backend.inference.api.schemas import EndpointModelUpdate
from backend.inference.api.schemas import EndpointSummary
from backend.inference.api.schemas import EndpointTestBody
from backend.inference.api.schemas import EndpointUpdate
from backend.inference.api.schemas import InstallOut
from backend.inference.api.schemas import MetricsOut
from backend.inference.api.schemas import ProbeResult
from backend.inference.api.schemas import RefreshResult
from backend.inference.api.schemas import RequestRef
from backend.inference.api.schemas import SessionOut
from backend.inference.api.schemas import TokenCreate
from backend.inference.api.schemas import TokenOut
from backend.inference.api.schemas import TokenRevokeResult
from backend.inference.api.schemas import TokenRotate
from backend.inference.api.schemas import TokenRotateResult
from backend.inference.api.schemas import TokenSecret
from backend.inference.api.schemas import UnloadBody
from backend.inference.events import loaded_summary
from backend.inference.live import endpoint_status
from backend.inference.live import live_sessions
from backend.inference.models import ENDPOINT_SLUG_RE
from backend.inference.models import RESERVED_ENDPOINT_SLUGS
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.inference.models import RelaySession
from backend.inference.models import RelayToken
from backend.inference.snapshot import effective_num_ctx

router = Router(tags=["inference"])

_SYSTEM_EDITABLE = frozenset({"max_concurrency", "max_active_jobs"})
_CAP_FIELDS = (
    "max_concurrency",
    "max_active_jobs",
    "max_resident_models",
    "request_timeout_s",
    "keep_alive",
    "num_ctx_cap",
    "assume_tools",
)
_KEEP_ALIVE_RE = re.compile(r"^(-1|0|\d+(\.\d+)?[smh]?)$")
TOKENS_PER_S_WINDOW = timedelta(minutes=5)
RECENT_ERRORS = 10


# ── Lookups and permissions ─────────────────────────────────────────────


def listable_endpoints(user):
    """``visible_endpoints`` plus the user's own disabled endpoints (owners must be able to re-enable them)."""
    qs = visible_endpoints(user)
    if user is not None and getattr(user, "is_authenticated", False) and not getattr(user, "is_staff", False):
        qs = LLMEndpoint.objects.filter(Q(pk__in=qs.values("pk")) | Q(owner=user))
    return qs


def _visible(request, endpoint_id: int) -> LLMEndpoint:
    return get_object_or_404(listable_endpoints(request.auth).select_related("owner"), pk=endpoint_id)


def _owned(request, endpoint_id: int) -> LLMEndpoint:
    endpoint = _visible(request, endpoint_id)
    if not is_endpoint_owner(request.auth, endpoint):
        raise HttpError(403, "Only the endpoint's owner or staff can do this.")
    return endpoint


def _is_staff(request) -> bool:
    return bool(getattr(request.auth, "is_staff", False))


# ── Serialization ───────────────────────────────────────────────────────


def _active_job_counts(endpoints: list[LLMEndpoint]) -> dict[int, int]:
    from backend.generation.models import GenerationJob

    running = GenerationJob.objects.filter(status=GenerationJob.Status.RUNNING)
    counts = dict(
        running.filter(endpoint_id__in=[e.pk for e in endpoints])
        .values("endpoint_id")
        .annotate(n=Count("id"))
        .values_list("endpoint_id", "n"),
    )
    for endpoint in endpoints:
        if endpoint.is_system:
            counts[endpoint.pk] = counts.get(endpoint.pk, 0) + running.filter(endpoint__isnull=True).count()
    return counts


def _loaded(endpoint: LLMEndpoint) -> list[dict[str, Any]]:
    return loaded_summary(endpoint)


def _session_dict(session: RelaySession, *, inflight: int = 0, token_prefix: str = "") -> dict[str, Any]:
    return {
        "id": session.pk,
        "role": session.role,
        "relay_id": session.relay_id,
        "hostname": session.hostname,
        "relay_version": session.relay_version,
        "upstream_kind": session.upstream_kind,
        "upstream_hint": session.upstream_hint,
        "capacity": session.capacity,
        "client_ip": session.client_ip,
        "token_prefix": token_prefix,
        "model_count": len(session.models or []),
        "inflight": inflight,
        "started_at": session.started_at,
        "last_seen_at": session.last_seen_at,
        "ended_at": session.ended_at,
        "ended_reason": session.ended_reason,
    }


def summarize(endpoints: list[LLMEndpoint], user, *, detail: bool = False) -> list[dict[str, Any]]:
    """``EndpointSummary`` (or ``EndpointDetail``) dicts for ``endpoints`` with a handful of grouped queries."""
    from backend.llm_models.models import LLMModel

    ids = [e.pk for e in endpoints]
    sessions_by_endpoint: dict[int, list[RelaySession]] = defaultdict(list)
    for session in live_sessions(ids).order_by("role", "-last_seen_at"):
        sessions_by_endpoint[session.endpoint_id].append(session)
    request_counts: dict[tuple[int, str], int] = {
        (row["endpoint_id"], row["status"]): row["n"]
        for row in LLMRequest.objects.filter(
            endpoint_id__in=ids,
            status__in=[LLMRequest.Status.QUEUED, LLMRequest.Status.LEASED],
        )
        .values("endpoint_id", "status")
        .annotate(n=Count("id"))
    }
    inflight_by_session = dict(
        LLMRequest.objects.filter(endpoint_id__in=ids, status=LLMRequest.Status.LEASED, session__isnull=False)
        .values("session_id")
        .annotate(n=Count("id"))
        .values_list("session_id", "n"),
    )
    model_counts = {
        row["endpoint_id"]: row
        for row in LLMModel.objects.filter(endpoint_id__in=ids)
        .values("endpoint_id")
        .annotate(
            total=Count("id"),
            available=Count("id", filter=Q(is_available=True)),
            tools=Count("id", filter=Q(is_available=True, supports_function_calling=True)),
        )
    }
    since = timezone.now() - TOKENS_PER_S_WINDOW
    throughput = {
        row["endpoint_id"]: row
        for row in LLMRequest.objects.filter(endpoint_id__in=ids, completed_at__gte=since, eval_ms__gt=0)
        .values("endpoint_id")
        .annotate(tokens=Sum("completion_tokens"), ms=Sum("eval_ms"))
    }
    active_jobs = _active_job_counts(endpoints)
    out = []
    for endpoint in endpoints:
        owner_view = is_endpoint_owner(user, endpoint)
        sessions = sessions_by_endpoint.get(endpoint.pk, [])
        has_active = any(s.role == RelaySession.Role.ACTIVE for s in sessions)
        counts = model_counts.get(endpoint.pk) or {}
        rate = throughput.get(endpoint.pk)
        tokens_per_s = round(rate["tokens"] / (rate["ms"] / 1000), 2) if rate and rate["ms"] else None
        cap = endpoint.max_active_jobs or endpoint.max_concurrency
        item: dict[str, Any] = {
            "id": endpoint.pk,
            "slug": endpoint.slug,
            "name": endpoint.name,
            "kind": endpoint.kind,
            "connection": endpoint.connection,
            "api_flavor": endpoint.api_flavor,
            "enabled": endpoint.enabled,
            "is_system": endpoint.is_system,
            "visibility": endpoint.visibility,
            "owner": {"id": endpoint.owner.pk, "username": owner_label(endpoint.owner)} if endpoint.owner else None,
            "can_edit": owner_view and (not endpoint.is_system or bool(getattr(user, "is_staff", False))),
            "has_api_key": bool(endpoint.encrypted_api_key),
            "api_key_prefix": endpoint.api_key_prefix if owner_view else None,
            "base_url": endpoint.base_url if owner_view and endpoint.connection == "direct" else None,
            "max_concurrency": endpoint.max_concurrency,
            "max_active_jobs_effective": {"sandbox": cap, "llm": cap},
            "max_resident_models": endpoint.max_resident_models,
            "request_timeout_s": endpoint.request_timeout_s,
            "num_ctx_cap": endpoint.num_ctx_cap,
            "keep_alive": endpoint.keep_alive,
            "assume_tools": endpoint.assume_tools,
            "status": endpoint_status(endpoint, has_live_session=has_active),
            "live": {
                "session_count": len(sessions),
                "sessions": [
                    {
                        "id": s.pk,
                        "role": s.role,
                        "hostname": s.hostname,
                        "relay_version": s.relay_version,
                        "client_ip": s.client_ip,
                        "capacity": s.capacity,
                        "last_seen_at": s.last_seen_at,
                    }
                    for s in sessions
                ]
                if owner_view
                else None,
                "inflight_requests": request_counts.get((endpoint.pk, LLMRequest.Status.LEASED), 0),
                "queued_requests": request_counts.get((endpoint.pk, LLMRequest.Status.QUEUED), 0),
                "active_jobs": active_jobs.get(endpoint.pk, 0),
                "loaded": _loaded(endpoint),
                "tokens_per_s_5m": tokens_per_s,
            },
            "models": {
                "total": counts.get("total", 0),
                "available": counts.get("available", 0),
                "tools": counts.get("tools", 0),
            },
            "last_inventory_at": endpoint.last_inventory_at,
            "last_error": (endpoint.last_error or "") if owner_view else None,
            "server_version": endpoint.server_version,
        }
        if detail:
            item["sessions_recent"] = None
            item["recent_errors"] = None
            if owner_view:
                recent = (
                    RelaySession.objects.filter(endpoint=endpoint)
                    .select_related("token")
                    .order_by(
                        "-started_at",
                    )[:20]
                )
                item["sessions_recent"] = [
                    _session_dict(s, inflight=inflight_by_session.get(s.pk, 0), token_prefix=s.token.prefix)
                    for s in recent
                ]
                errors = (
                    LLMRequest.objects.filter(endpoint=endpoint)
                    .filter(Q(status__in=[LLMRequest.Status.FAILED, LLMRequest.Status.EXPIRED]) | Q(error_kind="http"))
                    .exclude(completed_at__isnull=True)
                    .order_by("-completed_at")[:RECENT_ERRORS]
                )
                item["recent_errors"] = [
                    {"at": e.completed_at, "kind": e.error_kind or e.status, "message": (e.error or "")[:500]}
                    for e in errors
                ]
        out.append(item)
    return out


def _detail(endpoint: LLMEndpoint, user) -> dict[str, Any]:
    return summarize([endpoint], user, detail=True)[0]


def _model_dict(model, loaded_names: set[str]) -> dict[str, Any]:
    return {
        "id": model.pk,
        "model_id": model.model_id,
        "served_model_name": model.served_model_name,
        "model_name": model.model_name,
        "family": model.family,
        "parameter_size": model.parameter_size,
        "quantization": model.quantization,
        "context_window": model.context_window,
        "num_ctx_override": model.num_ctx_override,
        "num_ctx_effective": effective_num_ctx(model),
        "supports_function_calling": model.supports_function_calling,
        "digest": model.digest,
        "is_available": model.is_available,
        "is_hidden": model.is_hidden,
        "loaded": model.served_model_name in loaded_names,
        "last_used_at": getattr(model, "last_used_at", None),
    }


# ── Validation ──────────────────────────────────────────────────────────


def _unique_slug(base: str) -> str:
    base = slugify(base)[:38].strip("-") or "endpoint"
    if len(base) < 2:
        base = f"{base}-ep"
    candidate = base
    n = 2
    while candidate in RESERVED_ENDPOINT_SLUGS or LLMEndpoint.objects.filter(slug=candidate).exists():
        suffix = f"-{n}"
        candidate = f"{base[: 40 - len(suffix)]}{suffix}"
        n += 1
    return candidate


def _validate_direct_url(url: str) -> str:
    from backend.inference.security import UnsafeTargetError
    from backend.inference.security import validate_direct_base_url

    try:
        validate_direct_base_url(url)
    except UnsafeTargetError as exc:
        raise HttpError(400, f"Base URL rejected: {exc}") from exc
    return url.strip().rstrip("/")


def _apply_caps(endpoint: LLMEndpoint, data: dict[str, Any]) -> None:
    for field in _CAP_FIELDS:
        if field in data and data[field] is not None:
            if field == "keep_alive" and not _KEEP_ALIVE_RE.match(str(data[field])):
                raise HttpError(400, "keep_alive must look like 30m, 1h, 300s, 0 or -1.")
            setattr(endpoint, field, data[field])
    if "max_resident_models" in data and data["max_resident_models"] is None:
        endpoint.max_resident_models = None
    if "max_active_jobs" in data and data["max_active_jobs"] is None:
        endpoint.max_active_jobs = None


def _set_api_key(endpoint: LLMEndpoint, api_key: str | None) -> None:
    if api_key is None:
        return
    from backend.common.crypto import encrypt

    if api_key == "":
        endpoint.encrypted_api_key = ""
        endpoint.api_key_prefix = ""
        return
    endpoint.encrypted_api_key = encrypt(api_key)
    endpoint.api_key_prefix = api_key[:8]


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("/endpoints/", response=list[EndpointSummary])
def list_endpoints(request, include_system: bool = Query(True)):  # noqa: FBT003
    qs = listable_endpoints(request.auth).select_related("owner").order_by("-is_system", "name")
    if not include_system:
        qs = qs.filter(is_system=False)
    return summarize(list(qs), request.auth)


@router.post("/endpoints/", response={201: EndpointCreateResult})
def create_endpoint(request, data: EndpointCreate):
    user = request.auth
    staff = _is_staff(request)
    if data.connection == "direct" and not staff:
        raise HttpError(403, "Only staff can create direct endpoints; use a relay instead.")
    if data.visibility == "shared" and not staff:
        raise HttpError(400, "Only staff can share an endpoint.")
    if not staff and (
        LLMEndpoint.objects.filter(owner=user, is_system=False).count() >= int(settings.RELAY_MAX_ENDPOINTS_PER_USER)
    ):
        raise HttpError(400, "You have reached the maximum number of endpoints; delete one first.")
    if data.slug:
        if data.slug in RESERVED_ENDPOINT_SLUGS or not re.match(ENDPOINT_SLUG_RE, data.slug):
            raise HttpError(400, "This slug is reserved or invalid.")
        if LLMEndpoint.objects.filter(slug=data.slug).exists():
            raise HttpError(409, "This slug is already taken.")
        slug = data.slug
    else:
        slug = _unique_slug(data.name)
    flavor = data.api_flavor or ("native" if data.kind == "ollama" else "openai")
    if flavor == "native" and data.kind != "ollama":
        raise HttpError(400, "The native API flavor exists only for Ollama.")
    endpoint = LLMEndpoint(
        slug=slug,
        name=data.name,
        kind=data.kind,
        connection=data.connection,
        api_flavor=flavor,
        owner=user,
        visibility=data.visibility or LLMEndpoint.Visibility.PRIVATE,
        max_concurrency=data.max_concurrency,
    )
    if data.connection == "direct":
        if not data.base_url:
            raise HttpError(400, "A direct endpoint needs a base URL.")
        endpoint.base_url = _validate_direct_url(data.base_url)
        _set_api_key(endpoint, data.api_key or None)
    elif data.base_url or data.api_key:
        raise HttpError(400, "Relay endpoints have no base URL or API key; the relay talks to the model server.")
    _apply_caps(endpoint, data.dict(exclude={"max_concurrency"}))
    token = None
    with transaction.atomic():
        endpoint.save()
        if endpoint.connection == LLMEndpoint.Connection.RELAY:
            raw, row = relay_tokens.generate_token(endpoint, label="initial", created_by=user)
            token = {"id": row.pk, "prefix": row.prefix, "secret_once": raw}
    endpoint.refresh_from_db()
    return Status(201, {"endpoint": _detail(endpoint, user), "token": token})


@router.get("/endpoints/{endpoint_id}/", response=EndpointDetail)
def get_endpoint(request, endpoint_id: int):
    return _detail(_visible(request, endpoint_id), request.auth)


@router.patch("/endpoints/{endpoint_id}/", response=EndpointDetail)
def update_endpoint(request, endpoint_id: int, data: EndpointUpdate):
    endpoint = _owned(request, endpoint_id)
    staff = _is_staff(request)
    changes = data.dict(exclude_unset=True)
    if endpoint.is_system:
        if not staff:
            raise HttpError(403, "Only staff can change the OpenRouter endpoint.")
        if set(changes) - _SYSTEM_EDITABLE:
            raise HttpError(400, "Only max_concurrency and max_active_jobs can change on the OpenRouter endpoint.")
    if changes.get("visibility") == "shared" and not staff:
        raise HttpError(400, "Only staff can share an endpoint.")
    connection = changes.get("connection") or endpoint.connection
    if connection == "direct" and not staff and {"connection", "base_url", "api_key"} & set(changes):
        raise HttpError(403, "Only staff can create or change direct endpoints.")
    if connection == "relay" and changes.get("base_url"):
        raise HttpError(400, "Relay endpoints have no base URL.")
    if changes.get("name"):
        endpoint.name = changes["name"]
    if changes.get("api_flavor"):
        if changes["api_flavor"] == "native" and endpoint.kind != "ollama":
            raise HttpError(400, "The native API flavor exists only for Ollama.")
        endpoint.api_flavor = changes["api_flavor"]
    if connection == "direct":
        url = changes.get("base_url", endpoint.base_url)
        if not url:
            raise HttpError(400, "A direct endpoint needs a base URL.")
        if "base_url" in changes or endpoint.connection != "direct":
            endpoint.base_url = _validate_direct_url(url)
        _set_api_key(endpoint, changes.get("api_key"))
    else:
        endpoint.base_url = ""
        endpoint.encrypted_api_key = ""
        endpoint.api_key_prefix = ""
    endpoint.connection = connection
    if changes.get("visibility"):
        endpoint.visibility = changes["visibility"]
    if "enabled" in changes and changes["enabled"] is not None:
        endpoint.enabled = changes["enabled"]
    _apply_caps(endpoint, changes)
    endpoint.save()
    if "enabled" in changes:
        from backend.inference import doorbell

        transaction.on_commit(lambda: doorbell.ring_endpoint(endpoint.pk))
    return _detail(endpoint, request.auth)


@router.delete("/endpoints/{endpoint_id}/", response={200: dict, 409: dict})
def delete_endpoint(request, endpoint_id: int):
    from backend.generation.models import ExperimentCondition
    from backend.generation.models import GenerationJob
    from backend.llm_models.models import LLMModel

    endpoint = _owned(request, endpoint_id)
    if endpoint.is_system:
        raise HttpError(400, "The OpenRouter endpoint cannot be deleted.")
    in_use = (
        GenerationJob.objects.filter(Q(endpoint=endpoint) | Q(model__endpoint=endpoint)).exists()
        or ExperimentCondition.objects.filter(model__endpoint=endpoint).exists()
        or LLMRequest.objects.filter(endpoint=endpoint).exists()
    )
    if in_use:
        return Status(409, {"code": "in_use", "detail": "This endpoint has runs or requests; disable it instead."})
    with transaction.atomic():
        for session in RelaySession.objects.filter(endpoint=endpoint, ended_at__isnull=True):
            relay_tokens.end_session(session, reason="endpoint_deleted")
        LLMModel.objects.filter(endpoint=endpoint).delete()
        endpoint.delete()
    return Status(200, {"success": True})


@router.post("/endpoints/{endpoint_id}/refresh/", response={202: RefreshResult})
def refresh_endpoint(request, endpoint_id: int):
    endpoint = _owned(request, endpoint_id)
    LLMEndpoint.objects.filter(pk=endpoint.pk).update(inventory_resync_requested=True)
    from backend.inference import doorbell

    transaction.on_commit(lambda: doorbell.ring_endpoint(endpoint.pk))
    return Status(202, {"requested": True})


# ── Probes and unload ───────────────────────────────────────────────────


def _endpoint_model(endpoint: LLMEndpoint, model_pk: int):
    from backend.llm_models.models import LLMModel

    return get_object_or_404(LLMModel, pk=model_pk, endpoint=endpoint)


@router.post("/endpoints/{endpoint_id}/test/", response=RequestRef)
def test_endpoint(request, endpoint_id: int, data: EndpointTestBody):
    from backend.inference.chat import build_upstream_body
    from backend.inference.requests import enqueue

    endpoint = _owned(request, endpoint_id)
    if endpoint.is_system:
        raise HttpError(400, "OpenRouter has no probe; use the key check on the Credentials page.")
    if data.model_id is not None:
        from backend.inference.access import model_capabilities

        model = _endpoint_model(endpoint, data.model_id)
        access = replace(
            endpoint_access(endpoint, served_model_name=model.served_model_name, digest=model.digest),
            num_ctx=effective_num_ctx(model),
            capabilities=model_capabilities(model),
        )
        path, body = build_upstream_body(
            access,
            [{"role": "user", "content": "Reply with the single word: ok"}],
            {"max_tokens": 1, "temperature": 0},
            None,
        )
        rid = enqueue(access, path, body, source=LLMRequest.Source.PROBE, digest=model.digest)
    else:
        path = "/api/version" if endpoint.kind == LLMEndpoint.Kind.OLLAMA else "/v1/models"
        rid = enqueue(endpoint_access(endpoint), path, None, source=LLMRequest.Source.PROBE, method="GET")
    return {"request_id": rid}


def _probe_sample(row: LLMRequest) -> str | None:
    payload = row.response if isinstance(row.response, dict) else {}
    if row.path == "/api/version":
        return str(payload.get("version") or "")
    if row.path == "/v1/models":
        return f"{len(payload.get('data') or [])} models"
    message = payload.get("message") or ((payload.get("choices") or [{}])[0].get("message") or {})
    return str(message.get("content") or "")[:200]


@router.get("/endpoints/{endpoint_id}/test/{request_id}/", response=ProbeResult)
def test_result(request, endpoint_id: int, request_id: uuid.UUID):
    endpoint = _owned(request, endpoint_id)
    row = get_object_or_404(LLMRequest, pk=request_id, endpoint=endpoint, source=LLMRequest.Source.PROBE)
    latency = int((row.completed_at - row.created_at).total_seconds() * 1000) if row.completed_at else None
    status = row.status
    error = None
    sample = None
    if row.status == LLMRequest.Status.DONE and int(row.response_status or 200) >= 400:
        status, error = "failed", f"HTTP {row.response_status}: {row.error or ''}"[:500]
    elif row.status == LLMRequest.Status.DONE:
        sample = _probe_sample(row)
    elif row.status in (LLMRequest.Status.FAILED, LLMRequest.Status.EXPIRED, LLMRequest.Status.CANCELLED):
        error = f"{row.error_kind or row.status}: {row.error}"[:500]
    return {"status": status, "latency_ms": latency, "error": error, "sample": sample}


@router.post("/endpoints/{endpoint_id}/unload/", response=RequestRef)
def unload_model(request, endpoint_id: int, data: UnloadBody):
    from backend.inference.requests import enqueue

    endpoint = _owned(request, endpoint_id)
    if endpoint.kind != LLMEndpoint.Kind.OLLAMA:
        raise HttpError(400, "Unloading is only supported on Ollama endpoints.")
    model = _endpoint_model(endpoint, data.model_pk)
    rid = enqueue(
        endpoint_access(endpoint, served_model_name=model.served_model_name),
        "/api/generate",
        {"model": model.served_model_name, "keep_alive": 0},
        source=LLMRequest.Source.PROBE,
    )
    return {"request_id": rid}


# ── Models ──────────────────────────────────────────────────────────────


@router.get("/endpoints/{endpoint_id}/models/", response=list[EndpointModel])
def list_endpoint_models(request, endpoint_id: int, include_hidden: bool = Query(False)):  # noqa: FBT003
    from backend.llm_models.models import LLMModel

    endpoint = _visible(request, endpoint_id)
    if endpoint.is_system:
        return []
    qs = LLMModel.objects.filter(endpoint=endpoint).order_by("served_model_name")
    if not include_hidden:
        qs = qs.filter(is_hidden=False)
    loaded = {str(m.get("name")) for m in endpoint.loaded_models or [] if isinstance(m, dict)}
    return [_model_dict(m, loaded) for m in qs]


@router.patch("/endpoints/{endpoint_id}/models/{model_pk}/", response=EndpointModel)
def update_endpoint_model(request, endpoint_id: int, model_pk: int, data: EndpointModelUpdate):
    endpoint = _owned(request, endpoint_id)
    model = _endpoint_model(endpoint, model_pk)
    changes = data.dict(exclude_unset=True)
    for field in ("is_hidden", "supports_function_calling"):
        if changes.get(field) is not None:
            setattr(model, field, changes[field])
    if "num_ctx_override" in changes:
        model.num_ctx_override = changes["num_ctx_override"]
    if changes.get("model_name"):
        model.model_name = changes["model_name"]
    model.save()
    loaded = {str(m.get("name")) for m in endpoint.loaded_models or [] if isinstance(m, dict)}
    return _model_dict(model, loaded)


# ── Tokens and sessions ─────────────────────────────────────────────────


def _relay_endpoint(request, endpoint_id: int) -> LLMEndpoint:
    endpoint = _owned(request, endpoint_id)
    if endpoint.connection != LLMEndpoint.Connection.RELAY:
        raise HttpError(400, "Only relay endpoints have tokens.")
    return endpoint


@router.get("/endpoints/{endpoint_id}/tokens/", response=list[TokenOut])
def list_tokens(request, endpoint_id: int):
    endpoint = _relay_endpoint(request, endpoint_id)
    active = dict(
        RelaySession.objects.filter(endpoint=endpoint, ended_at__isnull=True)
        .values("token_id")
        .annotate(n=Count("id"))
        .values_list("token_id", "n"),
    )
    return [
        {
            "id": t.pk,
            "label": t.label,
            "prefix": t.prefix,
            "created_at": t.created_at,
            "last_used_at": t.last_used_at,
            "expires_at": t.expires_at,
            "revoked_at": t.revoked_at,
            "active_sessions": active.get(t.pk, 0),
        }
        for t in RelayToken.objects.filter(endpoint=endpoint).order_by("-created_at")
    ]


@router.post("/endpoints/{endpoint_id}/tokens/", response={201: TokenSecret})
def create_token(request, endpoint_id: int, data: TokenCreate):
    endpoint = _relay_endpoint(request, endpoint_id)
    if not _is_staff(request):
        live = (
            RelayToken.objects.filter(endpoint=endpoint, revoked_at__isnull=True)
            .exclude(expires_at__lte=timezone.now())
            .count()
        )
        if live >= int(settings.RELAY_MAX_TOKENS_PER_ENDPOINT):
            raise HttpError(400, "This endpoint has too many live tokens; revoke one first.")
    raw, token = relay_tokens.generate_token(endpoint, label=data.label, created_by=request.auth)
    return Status(201, {"id": token.pk, "prefix": token.prefix, "secret_once": raw})


def _token(endpoint: LLMEndpoint, token_id: int) -> RelayToken:
    return get_object_or_404(RelayToken, pk=token_id, endpoint=endpoint)


@router.post("/endpoints/{endpoint_id}/tokens/{token_id}/rotate/", response=TokenRotateResult)
def rotate_token(request, endpoint_id: int, token_id: int, data: TokenRotate):
    endpoint = _relay_endpoint(request, endpoint_id)
    token = _token(endpoint, token_id)
    if token.revoked_at is not None:
        raise HttpError(400, "This token is revoked.")
    raw, new, expires = relay_tokens.rotate(token, grace_minutes=data.grace_minutes, user=request.auth)
    return {"new": {"id": new.pk, "prefix": new.prefix, "secret_once": raw}, "old_expires_at": expires}


@router.post("/endpoints/{endpoint_id}/tokens/{token_id}/revoke/", response=TokenRevokeResult)
def revoke_token(request, endpoint_id: int, token_id: int):
    endpoint = _relay_endpoint(request, endpoint_id)
    return relay_tokens.revoke(_token(endpoint, token_id))


@router.get("/endpoints/{endpoint_id}/sessions/", response=list[SessionOut])
def list_sessions(request, endpoint_id: int, active: bool = Query(False)):  # noqa: FBT003
    endpoint = _owned(request, endpoint_id)
    qs = RelaySession.objects.filter(endpoint=endpoint).select_related("token").order_by("-started_at")
    if active:
        qs = qs.filter(pk__in=live_sessions([endpoint.pk]).values("pk"))
    sessions = list(qs[:100])
    inflight = dict(
        LLMRequest.objects.filter(session__in=sessions, status=LLMRequest.Status.LEASED)
        .values("session_id")
        .annotate(n=Count("id"))
        .values_list("session_id", "n"),
    )
    return [_session_dict(s, inflight=inflight.get(s.pk, 0), token_prefix=s.token.prefix) for s in sessions]


# ── Metrics ─────────────────────────────────────────────────────────────


@router.get("/endpoints/{endpoint_id}/metrics/", response=MetricsOut)
def endpoint_metrics(request, endpoint_id: int, minutes: int = Query(60, ge=5, le=1440)):
    endpoint = _visible(request, endpoint_id)
    now = timezone.now().replace(second=0, microsecond=0)
    start = now - timedelta(minutes=minutes - 1)
    rows = (
        LLMRequest.objects.filter(endpoint=endpoint)
        .filter(
            Q(completed_at__gte=start) | Q(completed_at__isnull=True, leased_at__isnull=False),
        )
        .values("leased_at", "completed_at", "status", "response_status", "prompt_tokens", "completion_tokens")[:20000]
    )
    points = {
        start + timedelta(minutes=i): {"inflight_s": 0.0, "completed": 0, "tokens": 0, "errors": 0}
        for i in range(minutes)
    }
    end = now + timedelta(minutes=1)
    for row in rows:
        done = row["completed_at"]
        if done is not None and done >= start:
            bucket = points.get(done.replace(second=0, microsecond=0))
            if bucket is not None:
                failed = (
                    row["status"] in (LLMRequest.Status.FAILED, LLMRequest.Status.EXPIRED)
                    or (row["response_status"] or 0) >= 400
                )
                bucket["errors" if failed else "completed"] += 1
                bucket["tokens"] += int(row["prompt_tokens"] or 0) + int(row["completion_tokens"] or 0)
        leased = row["leased_at"]
        if leased is None:
            continue
        span_start, span_end = max(leased, start), min(done or timezone.now(), end)
        cursor = span_start.replace(second=0, microsecond=0)
        while cursor < span_end:
            nxt = cursor + timedelta(minutes=1)
            if cursor in points:
                overlap = (min(span_end, nxt) - max(span_start, cursor)).total_seconds()
                points[cursor]["inflight_s"] += max(0.0, overlap)
            cursor = nxt
    return {
        "points": [
            {
                "minute": minute,
                "inflight_avg": round(p["inflight_s"] / 60.0, 3),
                "completed": p["completed"],
                "tokens": p["tokens"],
                "errors": p["errors"],
            }
            for minute, p in sorted(points.items())
        ],
    }


# ── Install ─────────────────────────────────────────────────────────────


def server_url(request) -> str:
    """The public base URL a remote relay dials.

    ``LLMLAB_PUBLIC_URL`` wins, then the browser-facing ``FRONTEND_PUBLIC_ORIGIN``
    (the request host is often an internal name such as ``django:8000`` behind
    the Vite dev proxy), and only then the request's own scheme and host.
    """
    for name in ("LLMLAB_PUBLIC_URL", "FRONTEND_PUBLIC_ORIGIN"):
        configured = str(getattr(settings, name, "") or "").strip()
        if configured:
            return configured.rstrip("/")
    return f"{request.scheme}://{request.get_host()}"


@router.get("/endpoints/{endpoint_id}/install/", response=InstallOut)
def install(request, endpoint_id: int):
    from backend.inference.relay.views import relay_file

    endpoint = _relay_endpoint(request, endpoint_id)
    _script, script_sha, version = relay_file("llmlab_relay.py")
    _dockerfile, dockerfile_sha, _v = relay_file("Dockerfile")
    _unit, unit_sha, _v = relay_file("llmlab-relay.service")
    server = server_url(request)
    script_url = f"{server}/api/relay/v1/relay.py"
    dockerfile_url = f"{server}/api/relay/v1/Dockerfile"
    unit_url = f"{server}/api/relay/v1/llmlab-relay.service"
    slots = endpoint.max_concurrency
    kind_args = (
        ""
        if endpoint.kind == LLMEndpoint.Kind.OLLAMA
        else " --kind openai_compatible --upstream http://127.0.0.1:8000"
    )
    run_cmd = (
        f"python3 llmlab_relay.py --server {server} --token-file ~/.llmlab-relay/token --parallel {slots}{kind_args}"
    )
    python_cmd = "\n".join(
        [
            "mkdir -p ~/.llmlab-relay && cd ~/.llmlab-relay",
            f"curl -fsSL {script_url} -o llmlab_relay.py",
            f'echo "{script_sha}  llmlab_relay.py" | sha256sum -c -',
            "install -m600 /dev/stdin ~/.llmlab-relay/token   # paste the token, then Ctrl-D",
            f"{run_cmd} --check",
            run_cmd,
        ],
    )
    docker_cmd = "\n".join(
        [
            "mkdir -p ~/llmlab-relay-build && cd ~/llmlab-relay-build",
            f"curl -fsSL {script_url} -o llmlab_relay.py",
            f'echo "{script_sha}  llmlab_relay.py" | sha256sum -c -',
            f"curl -fsSL {dockerfile_url} -o Dockerfile",
            f'echo "{dockerfile_sha}  Dockerfile" | sha256sum -c -',
            f"docker build -t llmlab-relay:{version} .",
            "docker run --rm -i -v llmlab-relay:/state alpine sh -c "
            # The relay runs as 65534 and keeps its relay id in /state, so it owns the volume too.
            "'umask 077; cat > /state/token; chown 65534 /state /state/token'   # paste the token, then Ctrl-D",
            "docker run -d --name llmlab-relay --restart unless-stopped --read-only --network host "
            f"-v llmlab-relay:/state -e LLMLAB_SERVER={server} -e LLMLAB_TOKEN_FILE=/state/token "
            f"-e LLMLAB_STATE_DIR=/state llmlab-relay:{version} --parallel {slots}{kind_args} --on-unauthorized wait",
        ],
    )
    systemd_cmd = "\n".join(
        [
            "sudo useradd --system --home /var/lib/llmlab-relay --shell /usr/sbin/nologin llmlab-relay",
            "sudo install -d -m 700 -o llmlab-relay /var/lib/llmlab-relay /etc/llmlab",
            f"curl -fsSL {script_url} -o llmlab_relay.py",
            f'echo "{script_sha}  llmlab_relay.py" | sha256sum -c -',
            "sudo install -m 755 llmlab_relay.py /usr/local/lib/llmlab_relay.py",
            "sudo install -m 600 -o llmlab-relay /dev/stdin /etc/llmlab/relay-token   # paste the token, then Ctrl-D",
            f"curl -fsSL {unit_url} -o llmlab-relay.service",
            f'echo "{unit_sha}  llmlab-relay.service" | sha256sum -c -',
            "sudo install -m 644 llmlab-relay.service /etc/systemd/system/llmlab-relay.service",
            "sudo systemctl edit llmlab-relay   # [Service] Environment=LLMLAB_SERVER="
            + server
            + f" Environment=LLMLAB_PARALLEL={slots}"
            + (
                ""
                if endpoint.kind == LLMEndpoint.Kind.OLLAMA
                else " Environment=LLMLAB_KIND=openai_compatible Environment=LLMLAB_UPSTREAM=http://127.0.0.1:8000"
            ),
            "sudo systemctl enable --now llmlab-relay",
        ],
    )
    ollama_env = {}
    if endpoint.kind == LLMEndpoint.Kind.OLLAMA:
        ollama_env = {
            "OLLAMA_HOST": "127.0.0.1:11434",
            "OLLAMA_NUM_PARALLEL": str(slots),
            "OLLAMA_MAX_LOADED_MODELS": str(endpoint.max_resident_models or 1),
            "OLLAMA_KEEP_ALIVE": endpoint.keep_alive or "30m",
        }
    return {
        "server_url": server,
        "endpoint_slug": endpoint.slug,
        "kind": endpoint.kind,
        "relay_version": version,
        "script_url": script_url,
        "script_sha256": script_sha,
        "dockerfile_sha256": dockerfile_sha,
        "commands": {"python": python_cmd, "docker": docker_cmd, "systemd": systemd_cmd},
        "ollama_env": ollama_env,
    }
