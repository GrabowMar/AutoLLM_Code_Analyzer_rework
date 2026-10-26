"""/api/integrations/webhooks/ — outbound notification subscriptions.

Everything is scoped to ``request.auth``; target URLs, signing secrets and
bot tokens are write-only (responses carry masked hints).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.shortcuts import get_object_or_404
from ninja import Field
from ninja import Router
from ninja import Schema
from ninja.errors import HttpError
from ninja.responses import Status

from backend.inference.security import UnsafeTargetError
from backend.integrations.models import WebhookDelivery
from backend.integrations.models import WebhookKind
from backend.integrations.models import WebhookSubscription
from backend.integrations.services import webhooks

router = Router(tags=["integrations"])

MAX_SUBSCRIPTIONS_PER_USER = 25
RECENT_DELIVERIES = 50


class SubscriptionOut(Schema):
    id: int
    name: str
    kind: str
    url_hint: str
    has_url: bool
    secret_hint: str
    has_secret: bool
    telegram_chat_id: str
    events: list[str]
    include_experiment_jobs: bool
    enabled: bool
    last_success_at: datetime | None
    last_error: str
    last_error_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def resolve_has_url(obj: WebhookSubscription) -> bool:
        return bool(obj.encrypted_url)


class SubscriptionIn(Schema):
    name: str = Field(..., min_length=1, max_length=120)
    kind: str
    url: str | None = Field(None, description="Target URL (all kinds except telegram). Write-only.")
    secret: str | None = Field(
        None,
        description="generic: HMAC signing secret; telegram: bot token (required); ntfy: access token. Write-only.",
    )
    telegram_chat_id: str | None = None
    events: list[str] = []
    include_experiment_jobs: bool = False
    enabled: bool = True


class SubscriptionPatch(Schema):
    name: str | None = Field(None, min_length=1, max_length=120)
    url: str | None = None
    secret: str | None = Field(None, description='"" clears the secret (not allowed for telegram).')
    telegram_chat_id: str | None = None
    events: list[str] | None = None
    include_experiment_jobs: bool | None = None
    enabled: bool | None = None


class DeliveryOut(Schema):
    id: int
    event_type: str
    event_key: str
    status: str
    attempts: int
    response_code: int | None
    error: str
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None


class TestResultOut(Schema):
    ok: bool
    status_code: int | None
    error: str
    delivery: DeliveryOut


class EventTypeOut(Schema):
    id: str
    description: str


def _validate_events(events: list[str]) -> list[str]:
    unknown = sorted(set(events) - set(webhooks.EVENT_TYPES))
    if unknown:
        raise HttpError(400, f"Unknown event type(s): {', '.join(unknown)}")
    return sorted(set(events), key=list(webhooks.EVENT_TYPES).index)


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise HttpError(400, "A target URL is required.")
    if len(url) > 2000:
        raise HttpError(400, "The URL is too long.")
    try:
        webhooks.validate_webhook_url(url)
    except UnsafeTargetError as exc:
        raise HttpError(400, f"URL rejected: {exc}") from exc
    return url


def _validate_chat_id(chat_id: str) -> str:
    chat_id = (chat_id or "").strip()
    if not chat_id or len(chat_id) > 64 or " " in chat_id:
        raise HttpError(400, "Telegram needs a chat id (e.g. 123456789, -100123… or @channel).")
    return chat_id


def _get(request, sub_id: int) -> WebhookSubscription:
    return get_object_or_404(WebhookSubscription, pk=sub_id, user=request.auth)


@router.get("/event-types/", response=list[EventTypeOut])
def list_event_types(request):
    return [{"id": key, "description": text} for key, text in webhooks.EVENT_TYPES.items()]


@router.get("/", response=list[SubscriptionOut])
def list_subscriptions(request):
    return list(WebhookSubscription.objects.filter(user=request.auth))


@router.post("/", response={201: SubscriptionOut})
def create_subscription(request, payload: SubscriptionIn):
    if payload.kind not in WebhookKind.values:
        raise HttpError(400, f"Unknown kind '{payload.kind}'; one of {', '.join(WebhookKind.values)}.")
    if WebhookSubscription.objects.filter(user=request.auth).count() >= MAX_SUBSCRIPTIONS_PER_USER:
        raise HttpError(400, f"At most {MAX_SUBSCRIPTIONS_PER_USER} webhooks per account.")
    sub = WebhookSubscription(
        user=request.auth,
        name=payload.name.strip(),
        kind=payload.kind,
        events=_validate_events(payload.events),
        include_experiment_jobs=payload.include_experiment_jobs,
        enabled=payload.enabled,
    )
    if payload.kind == WebhookKind.TELEGRAM:
        if not (payload.secret or "").strip():
            raise HttpError(400, "Telegram needs a bot token (secret).")
        sub.telegram_chat_id = _validate_chat_id(payload.telegram_chat_id or "")
    else:
        sub.set_url(_validate_url(payload.url or ""))
    if payload.secret:
        sub.set_secret(payload.secret)
    sub.save()
    return Status(201, sub)


@router.patch("/{sub_id}/", response=SubscriptionOut)
def update_subscription(request, sub_id: int, payload: SubscriptionPatch):
    sub = _get(request, sub_id)
    data = payload.dict(exclude_unset=True)
    if data.get("name") is not None:
        sub.name = data["name"].strip()
    if data.get("events") is not None:
        sub.events = _validate_events(data["events"])
    for flag in ("include_experiment_jobs", "enabled"):
        if data.get(flag) is not None:
            setattr(sub, flag, data[flag])
    if data.get("url") is not None:
        if sub.kind == WebhookKind.TELEGRAM:
            raise HttpError(400, "Telegram webhooks have no URL; set the bot token and chat id.")
        sub.set_url(_validate_url(data["url"]))
    if data.get("telegram_chat_id") is not None:
        if sub.kind != WebhookKind.TELEGRAM:
            raise HttpError(400, "telegram_chat_id only applies to telegram webhooks.")
        sub.telegram_chat_id = _validate_chat_id(data["telegram_chat_id"])
    if "secret" in data and data["secret"] is not None:
        if sub.kind == WebhookKind.TELEGRAM and not data["secret"].strip():
            raise HttpError(400, "The Telegram bot token cannot be cleared.")
        sub.set_secret(data["secret"])
    sub.save()
    return sub


@router.delete("/{sub_id}/", response={204: None})
def delete_subscription(request, sub_id: int):
    _get(request, sub_id).delete()
    return Status(204, None)


@router.post("/{sub_id}/test/", response=TestResultOut)
def test_subscription(request, sub_id: int):
    """Send a sample ``test`` event now (one attempt, 10 s timeout) and return the outcome."""
    sub = _get(request, sub_id)
    delivery, result = webhooks.send_test(sub)
    return {"ok": result.ok, "status_code": result.status_code, "error": result.error, "delivery": delivery}


@router.get("/{sub_id}/deliveries/", response=list[DeliveryOut])
def list_deliveries(request, sub_id: int):
    sub = _get(request, sub_id)
    return list(WebhookDelivery.objects.filter(subscription=sub).order_by("-created_at")[:RECENT_DELIVERIES])
