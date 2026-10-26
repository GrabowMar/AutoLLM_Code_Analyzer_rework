"""/api/integrations/providers/ — one-click cloud LLM provider connections.

Listing presets is open to every user; testing a key and connecting are
staff-only (a provider connection is a direct endpoint). API keys are never
echoed back.
"""

from __future__ import annotations

from django.db.models import Count
from django.db.models import Q
from ninja import Field
from ninja import Router
from ninja import Schema
from ninja.responses import Status

from backend.integrations.services import providers as svc

router = Router(tags=["integrations"])

SAMPLE_SIZE = 10


class ConnectedEndpoint(Schema):
    id: int
    slug: str
    name: str
    enabled: bool
    model_count: int  # available, not hidden
    last_error: str


class ProviderOut(Schema):
    id: str
    name: str
    description: str
    base_url: str
    docs_url: str
    keys_url: str
    max_concurrency: int
    request_timeout_s: int
    can_connect: bool
    connected_endpoints: list[ConnectedEndpoint]


class ApiKeyIn(Schema):
    api_key: str = Field(..., min_length=1, max_length=svc.MAX_KEY_LENGTH)


class ConnectIn(ApiKeyIn):
    name: str | None = Field(None, max_length=120)


class KeyTestOut(Schema):
    ok: bool
    message: str
    model_count: int  # chat/code models the preset keeps
    total_models: int  # everything /models listed
    sample_model_ids: list[str]


class ConnectOut(Schema):
    endpoint_id: int
    slug: str
    name: str
    model_count: int  # visible chat/code models
    hidden_count: int
    total_models: int
    inventory_error: str


def _connected(user, preset: svc.ProviderPreset) -> list[dict]:
    endpoints = svc.connected_endpoints(user, preset)
    if not endpoints:
        return []
    from backend.llm_models.models import LLMModel

    counts = dict(
        LLMModel.objects.filter(endpoint__in=endpoints)
        .values("endpoint")
        .annotate(n=Count("pk", filter=Q(is_available=True, is_hidden=False)))
        .values_list("endpoint", "n"),
    )
    return [
        {
            "id": e.pk,
            "slug": e.slug,
            "name": e.name,
            "enabled": e.enabled,
            "model_count": counts.get(e.pk, 0),
            "last_error": e.last_error,
        }
        for e in endpoints
    ]


@router.get("/", response=list[ProviderOut])
def list_providers(request):
    user = request.auth
    staff = bool(getattr(user, "is_staff", False))
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "base_url": p.base_url,
            "docs_url": p.docs_url,
            "keys_url": p.keys_url,
            "max_concurrency": p.max_concurrency,
            "request_timeout_s": p.request_timeout_s,
            "can_connect": staff,
            "connected_endpoints": _connected(user, p),
        }
        for p in svc.PRESETS
    ]


@router.post("/{preset_id}/test/", response=KeyTestOut)
def test_key(request, preset_id: str, data: ApiKeyIn):
    preset = svc.get_preset(preset_id)
    svc.require_staff(request.auth)
    ok, message, ids = svc.validate_key(preset, data.api_key)
    chat = preset.chat_models(ids)
    return {
        "ok": ok,
        "message": message,
        "model_count": len(chat),
        "total_models": len(ids),
        "sample_model_ids": chat[:SAMPLE_SIZE],
    }


@router.post("/{preset_id}/connect/", response={201: ConnectOut})
def connect(request, preset_id: str, data: ConnectIn):
    preset = svc.get_preset(preset_id)
    result = svc.connect(request.auth, preset, data.api_key, name=data.name)
    return Status(
        201,
        {
            "endpoint_id": result.endpoint.pk,
            "slug": result.endpoint.slug,
            "name": result.endpoint.name,
            "model_count": result.visible_models,
            "hidden_count": result.hidden_models,
            "total_models": result.total_models,
            "inventory_error": result.inventory_error,
        },
    )
