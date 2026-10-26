"""Outbound webhook notifications.

Flow: a terminal status change (``signals`` post_save, or the periodic
:func:`sweep` for transitions written with ``QuerySet.update()``) calls
:func:`emit`, which creates one :class:`WebhookDelivery` per matching
subscription (``UniqueConstraint(subscription, event_key)`` de-duplicates) and
dispatches ``tasks.deliver_webhook`` on commit. The task calls
:func:`send_delivery`, which formats the payload for the subscription's kind
and POSTs it through :func:`post` (SSRF-checked, connects to the vetted IP).

Secrets (target URLs, signing secrets, bot tokens) are never logged and never
appear in recorded errors.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import socket
import ssl
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.utils import timezone

from backend.common.crypto import DecryptionError
from backend.inference import security
from backend.inference.security import UnsafeTargetError
from backend.integrations.models import WebhookDelivery
from backend.integrations.models import WebhookKind
from backend.integrations.models import WebhookSubscription

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 64 * 1024
USER_AGENT = "LLMLab-Webhooks/1.0"
TELEGRAM_API = "https://api.telegram.org"
SWEEP_WINDOW = timedelta(minutes=30)
STUCK_PENDING_AFTER = timedelta(minutes=5)

# ── event catalog ─────────────────────────────────────────────────────

EVENT_TYPES: dict[str, str] = {
    "generation.completed": "A generation job finished successfully.",
    "generation.failed": "A generation job failed.",
    "generation.cancelled": "A generation job was cancelled.",
    "analysis.completed": "An analysis run finished (status completed, or partial when some tools failed).",
    "analysis.failed": "An analysis run failed (no tool completed).",
    "analysis.cancelled": "An analysis run was cancelled.",
    "experiment.completed": "Every job of an experiment reached a terminal status.",
    "experiment.cancelled": "An experiment was cancelled.",
    "report.completed": "A report finished generating.",
    "report.failed": "A report failed to generate.",
}
TEST_EVENT = "test"

# (app_label.ModelName) -> {status: event type}
_STATUS_EVENTS: dict[str, dict[str, str]] = {
    "generation.GenerationJob": {
        "completed": "generation.completed",
        "failed": "generation.failed",
        "cancelled": "generation.cancelled",
    },
    "analysis.AnalysisRun": {
        "completed": "analysis.completed",
        "partial": "analysis.completed",
        "failed": "analysis.failed",
        "cancelled": "analysis.cancelled",
    },
    "generation.Experiment": {
        "completed": "experiment.completed",
        "cancelled": "experiment.cancelled",
    },
    "reports.Report": {
        "completed": "report.completed",
        "failed": "report.failed",
    },
}
# Time columns the sweep looks at, per model.
_SWEEP_TIME_FIELDS: dict[str, tuple[str, ...]] = {
    "generation.GenerationJob": ("completed_at", "updated_at"),
    "analysis.AnalysisRun": ("completed_at",),
    "generation.Experiment": ("completed_at", "cancelled_at", "updated_at"),
    "reports.Report": ("completed_at",),
}


def model_key(instance_or_model) -> str:
    meta = instance_or_model._meta  # noqa: SLF001
    return f"{meta.app_label}.{meta.object_name}"


def event_type_for(instance) -> str | None:
    return _STATUS_EVENTS.get(model_key(instance), {}).get(getattr(instance, "status", None) or "")


def event_key_for(event_type: str, instance) -> str:
    return f"{event_type}:{instance.pk}:{instance.status}"


# ── payloads ──────────────────────────────────────────────────────────


def public_base_url() -> str:
    for name in ("LLMLAB_PUBLIC_URL", "FRONTEND_PUBLIC_ORIGIN"):
        configured = str(getattr(settings, name, "") or "").strip()
        if configured:
            return configured.rstrip("/")
    return f"https://{getattr(settings, 'DJANGO_DOMAIN', 'localhost')}"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _field(name: str, value: Any) -> dict[str, str] | None:
    if value in (None, ""):
        return None
    return {"name": name, "value": str(value)[:300]}


def _generation_payload(job) -> dict[str, Any]:
    model = job.model
    model_name = (model.model_name or model.model_id) if model else ""
    app = job.app_requirement
    title = (app.name if app else "") or (job.task_description or "").strip()[:80] or f"{job.get_mode_display()} job"
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    functional = metrics.get("functional") if isinstance(metrics.get("functional"), dict) else {}
    data: dict[str, Any] = {
        "model": model_name,
        "mode": job.mode,
        "app": app.name if app else "",
        "duration_seconds": job.duration_seconds,
        "experiment_id": str(job.experiment_id) if job.experiment_id else None,
    }
    if isinstance(metrics.get("cost"), (int, float)):
        data["cost_usd"] = round(float(metrics["cost"]), 4)
    if isinstance(metrics.get("total_tokens"), int):
        data["total_tokens"] = metrics["total_tokens"]
    # Only the conformance evaluator's rate; not_applicable / infra_error carry
    # none (missing data, never 0%), and other instruments are not this one.
    from backend.rankings.services.measured import functional_rate

    rate = functional_rate(functional)
    if rate is not None:
        data["pass_rate"] = rate
    if job.error_message:
        data["error"] = job.error_message[:500]
    fields = [
        _field("Model", model_name),
        _field("Mode", job.get_mode_display()),
        _field("Duration", _duration(job.duration_seconds)),
        _field("Cost", f"${data['cost_usd']:.4f}" if "cost_usd" in data else None),
        _field("Pass rate", f"{float(data['pass_rate']) * 100:.0f}%" if "pass_rate" in data else None),
        _field("Error", data.get("error", "")[:300]),
    ]
    return {
        "object": {"type": "generation_job", "id": str(job.pk), "title": title, "status": job.status},
        "url": f"{public_base_url()}/applications/{job.pk}",
        "fields": fields,
        "data": data,
        "occurred_at": _iso(job.completed_at),
    }


_SEVERITIES = ("critical", "high", "medium", "low", "info")


def _analysis_payload(run) -> dict[str, Any]:
    summary = run.summary if isinstance(run.summary, dict) else {}
    counts = summary.get("severity_counts") if isinstance(summary.get("severity_counts"), dict) else {}
    severity_text = " · ".join(f"{counts[s]} {s}" for s in _SEVERITIES if counts.get(s))
    title = run.name or (f"Analysis of job {str(run.generation_job_id)[:8]}" if run.generation_job_id else "Analysis")
    # summary schema 2: static counts are None when no static tool succeeded
    # (nothing was scanned, which is not "0 findings"); AI consensus is apart.
    static_measured = summary.get("static_measured")
    if static_measured is None:
        static_measured = summary.get("total_findings") is not None
    data: dict[str, Any] = {
        "total_findings": summary.get("total_findings"),
        "severity_counts": counts,
        "static_measured": bool(static_measured),
        "issues": summary.get("issues"),
        "ai_findings": summary.get("ai_findings"),
        "tools_run": summary.get("tools_run"),
        "tools_completed": summary.get("tools_completed"),
        "tools_failed": summary.get("tools_failed"),
        "is_complete": run.is_complete,
        "plan_hash": run.plan_hash or None,
        "cost_usd": summary.get("cost_usd"),
        "cost_complete": summary.get("cost_complete"),
        "duration_seconds": run.duration_seconds,
        "generation_job_id": str(run.generation_job_id) if run.generation_job_id else None,
    }
    if run.error_message:
        data["error"] = run.error_message[:500]
    tools = (
        f"{summary.get('tools_completed', 0)}/{summary.get('tools_run')} completed"
        if summary.get("tools_run")
        else None
    )
    fields = [
        _field(
            "Findings",
            summary.get("total_findings") if static_measured else "not measured (no static tool succeeded)",
        ),
        _field("Severity", severity_text),
        _field("AI consensus", summary.get("ai_findings") or None),
        _field("Tools", tools),
        _field("Duration", _duration(run.duration_seconds)),
        _field("Error", (run.error_message or "")[:300]),
    ]
    return {
        "object": {"type": "analysis_run", "id": str(run.pk), "title": title, "status": run.status},
        "url": f"{public_base_url()}/analysis/{run.pk}",
        "fields": fields,
        "data": data,
        "occurred_at": _iso(run.completed_at),
    }


def _experiment_payload(experiment) -> dict[str, Any]:
    counts = {row["status"]: row["n"] for row in experiment.jobs.order_by().values("status").annotate(n=Count("id"))}
    ended = experiment.completed_at or experiment.cancelled_at
    seconds = (ended - experiment.launched_at).total_seconds() if ended and experiment.launched_at else None
    data = {
        "jobs": counts,
        "duration_seconds": seconds,
        "mode": experiment.mode,
        "repeats": experiment.repeats,
    }
    jobs_text = " · ".join(f"{counts[s]} {s}" for s in ("completed", "failed", "cancelled") if counts.get(s))
    fields = [
        _field("Jobs", jobs_text),
        _field("Duration", _duration(seconds)),
        _field("Repeats", experiment.repeats),
    ]
    return {
        "object": {
            "type": "experiment",
            "id": str(experiment.pk),
            "title": experiment.name,
            "status": experiment.status,
        },
        "url": f"{public_base_url()}/experiments/{experiment.pk}",
        "fields": fields,
        "data": data,
        "occurred_at": _iso(ended),
    }


def _report_payload(report) -> dict[str, Any]:
    data: dict[str, Any] = {"report_type": report.report_type, "report_id": report.report_id}
    if report.error_message:
        data["error"] = report.error_message[:500]
    fields = [
        _field("Type", report.get_report_type_display()),
        _field("Error", (report.error_message or "")[:300]),
    ]
    return {
        "object": {"type": "report", "id": report.report_id, "title": report.title, "status": report.status},
        "url": f"{public_base_url()}/reports/{report.report_id}",
        "fields": fields,
        "data": data,
        "occurred_at": _iso(report.completed_at),
    }


_PAYLOAD_BUILDERS = {
    "generation.GenerationJob": _generation_payload,
    "analysis.AnalysisRun": _analysis_payload,
    "generation.Experiment": _experiment_payload,
    "reports.Report": _report_payload,
}

_HEADLINES = {
    "generation": "Generation",
    "analysis": "Analysis",
    "experiment": "Experiment",
    "report": "Report",
    "test": "Test notification",
}


def build_payload(event_type: str, instance, event_key: str) -> dict[str, Any]:
    body = _PAYLOAD_BUILDERS[model_key(instance)](instance)
    body["fields"] = [f for f in body["fields"] if f]
    verb = event_type.split(".", 1)[1] if "." in event_type else event_type
    if body["object"]["status"] == "partial":
        verb = "completed (partial)"
    headline = f"{_HEADLINES.get(event_type.split('.', 1)[0], 'Event')} {verb}"
    return {
        "event": event_type,
        "event_id": event_key,
        "occurred_at": body.pop("occurred_at") or timezone.now().isoformat(),
        "summary": f"{headline}: {body['object']['title']}",
        **body,
    }


def sample_payload() -> dict[str, Any]:
    now = timezone.now().isoformat()
    return {
        "event": TEST_EVENT,
        "event_id": f"test:{uuid.uuid4()}",
        "occurred_at": now,
        "summary": "Test notification: LLM Lab webhook is working",
        "object": {"type": "test", "id": "test", "title": "LLM Lab webhook test", "status": "completed"},
        "url": public_base_url(),
        "fields": [{"name": "Kind", "value": "test"}, {"name": "Sent at", "value": now}],
        "data": {},
    }


# ── emit (signal / sweep → deliveries) ────────────────────────────────


def _owner_id(instance) -> int | None:
    return getattr(instance, "created_by_id", None)


def _occurred_at(instance) -> datetime:
    for name in ("completed_at", "cancelled_at"):
        value = getattr(instance, name, None)
        if value:
            return value
    return timezone.now()


def _dispatch(delivery_id: int) -> None:
    from backend.integrations.tasks import deliver_webhook

    try:
        deliver_webhook.delay(delivery_id)
    except Exception:  # noqa: BLE001 — any broker/kombu error; must not break the saving caller
        # Broker down: the sweep re-dispatches stuck pending deliveries.
        logger.warning("Could not enqueue webhook delivery %s", delivery_id, exc_info=True)


def emit(instance, *, subscriptions=None) -> int:
    """Create (once) and dispatch the deliveries for ``instance``'s current terminal status.

    Never raises: webhook problems must not break the code that saved the
    object. Returns the number of new deliveries.
    """
    try:
        return _emit(instance, subscriptions)
    except Exception:
        logger.exception("Webhook emit failed for %s %s", model_key(instance), instance.pk)
        return 0


def _emit(instance, subscriptions) -> int:
    event_type = event_type_for(instance)
    owner_id = _owner_id(instance)
    if event_type is None or owner_id is None:
        return 0
    if subscriptions is None:
        subscriptions = list(
            WebhookSubscription.objects.filter(
                user_id=owner_id,
                enabled=True,
                events__contains=[event_type],
                created_at__lte=_occurred_at(instance),
            ),
        )
    else:
        subscriptions = [s for s in subscriptions if s.user_id == owner_id and event_type in (s.events or [])]
    if event_type.startswith("generation.") and getattr(instance, "experiment_id", None):
        subscriptions = [s for s in subscriptions if s.include_experiment_jobs]
    if not subscriptions:
        return 0
    event_key = event_key_for(event_type, instance)
    existing = set(
        WebhookDelivery.objects.filter(subscription__in=subscriptions, event_key=event_key).values_list(
            "subscription_id",
            flat=True,
        ),
    )
    todo = [s for s in subscriptions if s.pk not in existing]
    if not todo:
        return 0
    payload = build_payload(event_type, instance, event_key)
    created = 0
    with transaction.atomic():  # savepoint: a failure here never poisons the caller's transaction
        for sub in todo:
            delivery, was_created = WebhookDelivery.objects.get_or_create(
                subscription=sub,
                event_key=event_key,
                defaults={"event_type": event_type, "payload": payload},
            )
            if was_created:
                created += 1
                transaction.on_commit(lambda pk=delivery.pk: _dispatch(pk))
    return created


def sweep(now: datetime | None = None) -> dict[str, int]:
    """Emit events for terminal transitions that bypassed ``post_save`` (``QuerySet.update()`` / raw SQL).

    Looks back :data:`SWEEP_WINDOW`; idempotent through the delivery unique
    constraint. Also re-dispatches deliveries that never got a first attempt.
    """
    from django.apps import apps

    now = now or timezone.now()
    since = now - SWEEP_WINDOW
    subs = list(WebhookSubscription.objects.filter(enabled=True).exclude(events=[]))
    stats = {"emitted": 0, "redispatched": 0}
    if subs:
        by_owner: dict[int, list[WebhookSubscription]] = {}
        for sub in subs:
            by_owner.setdefault(sub.user_id, []).append(sub)
        wanted = {e for s in subs for e in (s.events or [])}
        for key, status_events in _STATUS_EVENTS.items():
            statuses = [st for st, ev in status_events.items() if ev in wanted]
            if not statuses:
                continue
            model = apps.get_model(key)
            recent = Q()
            for name in _SWEEP_TIME_FIELDS[key]:
                recent |= Q(**{f"{name}__gte": since})
            rows = model.objects.filter(recent, status__in=statuses, created_by_id__in=list(by_owner))
            for instance in rows.iterator(chunk_size=200):
                occurred = _occurred_at(instance)
                owner_subs = [s for s in by_owner[instance.created_by_id] if s.created_at <= occurred]
                stats["emitted"] += emit(instance, subscriptions=owner_subs)
    stuck = WebhookDelivery.objects.filter(
        status=WebhookDelivery.Status.PENDING,
        attempts=0,
        created_at__lt=now - STUCK_PENDING_AFTER,
        created_at__gte=now - timedelta(days=1),
    ).values_list("pk", flat=True)[:200]
    for pk in stuck:
        _dispatch(pk)
        stats["redispatched"] += 1
    return stats


# ── formatting ────────────────────────────────────────────────────────

_STATUS_COLORS = {
    "completed": 0x22C55E,
    "partial": 0xF59E0B,
    "failed": 0xEF4444,
    "cancelled": 0x6B7280,
}
_NTFY_TAGS = {
    "completed": ["white_check_mark"],
    "partial": ["warning"],
    "failed": ["x"],
    "cancelled": ["no_entry_sign"],
}


def _status(payload: dict[str, Any]) -> str:
    return str((payload.get("object") or {}).get("status") or "")


def _title(payload: dict[str, Any]) -> str:
    return str((payload.get("object") or {}).get("title") or payload.get("summary") or "LLM Lab")


def _fields(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [f for f in payload.get("fields") or [] if isinstance(f, dict) and f.get("name") and f.get("value")]


@dataclass
class Request:
    url: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


def _json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_slack(payload: dict[str, Any]) -> dict[str, Any]:
    summary = _slack_escape(payload.get("summary") or "")
    url = payload.get("url") or ""
    color = f"#{_STATUS_COLORS.get(_status(payload), 0x3B82F6):06x}"
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*<{url}|{summary}>*" if url else f"*{summary}*"}},
    ]
    fields = [
        {"type": "mrkdwn", "text": f"*{_slack_escape(f['name'])}*\n{_slack_escape(f['value'])}"}
        for f in _fields(payload)[:10]
    ]
    if fields:
        blocks.append({"type": "section", "fields": fields})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"LLM Lab · `{payload.get('event')}`"}]})
    return {"text": payload.get("summary") or "", "attachments": [{"color": color, "blocks": blocks}]}


def format_discord(payload: dict[str, Any]) -> dict[str, Any]:
    embed: dict[str, Any] = {
        "title": (payload.get("summary") or _title(payload))[:256],
        "color": _STATUS_COLORS.get(_status(payload), 0x3B82F6),
        "fields": [
            {"name": f["name"][:256], "value": f["value"][:1024], "inline": True} for f in _fields(payload)[:25]
        ],
        "footer": {"text": f"LLM Lab · {payload.get('event')}"},
    }
    if payload.get("url"):
        embed["url"] = payload["url"]
    if payload.get("occurred_at"):
        embed["timestamp"] = payload["occurred_at"]
    return {"username": "LLM Lab", "embeds": [embed], "allowed_mentions": {"parse": []}}


def format_teams(payload: dict[str, Any]) -> dict[str, Any]:
    status = _status(payload)
    color = {"completed": "Good", "partial": "Warning", "failed": "Attention"}.get(status, "Default")
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": payload.get("summary") or _title(payload),
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
            "color": color,
        },
    ]
    facts = [{"title": f["name"], "value": f["value"]} for f in _fields(payload)]
    if facts:
        body.append({"type": "FactSet", "facts": facts})
    body.append({"type": "TextBlock", "text": f"LLM Lab · {payload.get('event')}", "isSubtle": True, "size": "Small"})
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if payload.get("url"):
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open in LLM Lab", "url": payload["url"]}]
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card},
        ],
    }


def format_ntfy(payload: dict[str, Any], topic: str) -> dict[str, Any]:
    """ntfy JSON publish body (title / priority / tags / click; UTF-8 safe unlike headers)."""
    status = _status(payload)
    lines = [f"{f['name']}: {f['value']}" for f in _fields(payload)]
    message: dict[str, Any] = {
        "topic": topic,
        "title": (payload.get("summary") or "LLM Lab")[:250],
        "message": "\n".join(lines) or (payload.get("summary") or ""),
        "priority": 4 if status == "failed" else 3,
        "tags": _NTFY_TAGS.get(status, ["bell"]),
    }
    if payload.get("url"):
        message["click"] = payload["url"]
    return message


_MDV2_SPECIAL = set(r"_*[]()~`>#+-=|{}.!\\")


def md_escape(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters."""
    return "".join(f"\\{ch}" if ch in _MDV2_SPECIAL else ch for ch in str(text))


def format_telegram(payload: dict[str, Any], chat_id: str) -> dict[str, Any]:
    lines = [f"*{md_escape(payload.get('summary') or _title(payload))}*"]
    lines += [f"{md_escape(f['name'])}: {md_escape(f['value'])}" for f in _fields(payload)]
    if payload.get("url"):
        url = str(payload["url"]).replace("\\", "\\\\").replace(")", "\\)")
        lines.append(f"[Open in LLM Lab]({url})")
    return {
        "chat_id": chat_id,
        "text": "\n".join(lines)[:4096],
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def build_request(sub: WebhookSubscription, payload: dict[str, Any], *, delivery_id: Any = None) -> Request:
    """The HTTP request for ``payload`` in ``sub``'s format (decrypts the subscription's secrets)."""
    kind = sub.kind
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if kind == WebhookKind.TELEGRAM:
        token = sub.get_secret()
        if not token or not sub.telegram_chat_id:
            msg = "Telegram needs a bot token and a chat id"
            raise ValueError(msg)
        return Request(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            _json(format_telegram(payload, sub.telegram_chat_id)),
            headers,
        )
    url = sub.get_url()
    if not url:
        msg = "the subscription has no target URL"
        raise ValueError(msg)
    if kind == WebhookKind.SLACK:
        return Request(url, _json(format_slack(payload)), headers)
    if kind == WebhookKind.DISCORD:
        return Request(url, _json(format_discord(payload)), headers)
    if kind == WebhookKind.TEAMS:
        return Request(url, _json(format_teams(payload)), headers)
    if kind == WebhookKind.NTFY:
        parts = urlsplit(url)
        topic = parts.path.strip("/").rsplit("/", 1)[-1]
        if not topic:
            msg = "the ntfy URL must end with the topic, e.g. https://ntfy.sh/my-topic"
            raise ValueError(msg)
        base_path = parts.path.rstrip("/")[: -len(topic)] or "/"
        root = urlunsplit((parts.scheme, parts.netloc, base_path, "", ""))
        token = sub.get_secret()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return Request(root, _json(format_ntfy(payload, topic)), headers)
    # generic
    body = _json(payload)
    headers["X-LLMLab-Event"] = str(payload.get("event") or "")
    headers["X-LLMLab-Delivery"] = str(delivery_id or payload.get("event_id") or "")
    secret = sub.get_secret()
    if secret:
        headers["X-LLMLab-Signature"] = sign(secret, body)
    return Request(url, body, headers)


# ── SSRF-safe transport ───────────────────────────────────────────────


@dataclass(frozen=True)
class WebhookTarget:
    scheme: str
    host: str
    port: int
    path: str  # path + query
    addresses: tuple[str, ...]

    @property
    def host_header(self) -> str:
        default = 443 if self.scheme == "https" else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == default else f"{host}:{self.port}"


def validate_webhook_url(url: str) -> WebhookTarget:
    """https only (http too when DEBUG), no credentials, every resolved address public (raises ``UnsafeTargetError``).

    Address checks are :func:`backend.inference.security.check_address`
    (loopback / link-local / private / platform networks / blocked ports).
    Error messages name the host only, never the path (it holds the secret).
    """
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        msg = "invalid URL"
        raise UnsafeTargetError(msg) from exc
    allowed = ("https", "http") if settings.DEBUG else ("https",)
    if parts.scheme not in allowed:
        msg = "the URL must start with https://"
        raise UnsafeTargetError(msg)
    if parts.username or parts.password or "@" in parts.netloc:
        msg = "credentials in the URL are not allowed"
        raise UnsafeTargetError(msg)
    if parts.fragment:
        msg = "the URL must not have a fragment"
        raise UnsafeTargetError(msg)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        msg = "the URL has no host"
        raise UnsafeTargetError(msg)
    blocked = {h.lower() for h in getattr(settings, "INFERENCE_BLOCKED_HOSTS", [])}
    if host in blocked or host == "localhost" or host.endswith((".localhost", ".internal", ".local")):
        msg = f"{host} is an internal host"
        raise UnsafeTargetError(msg)
    port = port or (443 if parts.scheme == "https" else 80)
    addresses = security.resolve(host, port)
    for address in addresses:
        security.check_address(ipaddress.ip_address(address), port)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return WebhookTarget(parts.scheme, host, port, path, addresses)


class DeliveryError(Exception):
    """A request failed; ``retryable`` says whether trying again may help."""

    def __init__(self, message: str, *, retryable: bool, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass
class Response:
    status: int
    text: str


def _connect(target: WebhookTarget, timeout: float) -> http.client.HTTPConnection:
    last_error: OSError | None = None
    for address in target.addresses:
        context = ssl.create_default_context() if target.scheme == "https" else None
        try:
            sock = socket.create_connection((address, target.port), timeout=timeout)
            if context is not None:
                sock = context.wrap_socket(sock, server_hostname=target.host)
        except ssl.SSLError as exc:
            msg = f"TLS error with {target.host}: {exc.__class__.__name__}"
            raise DeliveryError(msg, retryable=False) from exc
        except OSError as exc:
            last_error = exc
            continue
        if context is not None:
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                target.host,
                target.port,
                timeout=timeout,
                context=context,
            )
        else:
            conn = http.client.HTTPConnection(target.host, target.port, timeout=timeout)
        conn.sock = sock
        return conn
    reason = last_error.__class__.__name__ if last_error else "no address"
    msg = f"cannot connect to {target.host}:{target.port} ({reason})"
    raise DeliveryError(msg, retryable=True)


def post(url: str, body: bytes, headers: dict[str, str], *, timeout: float = TIMEOUT_SECONDS) -> Response:
    """POST ``body`` to ``url``: re-validated and re-resolved per call, connects to the vetted IP, no redirects."""
    try:
        target = validate_webhook_url(url)
    except UnsafeTargetError as exc:
        msg = f"unsafe target: {exc}"
        raise DeliveryError(msg, retryable=False) from exc
    conn = _connect(target, timeout)
    try:
        send_headers = {"Host": target.host_header, "User-Agent": USER_AGENT, "Accept": "*/*", **headers}
        conn.request("POST", target.path, body=body, headers=send_headers)
        resp = conn.getresponse()
        raw = resp.read(MAX_RESPONSE_BYTES)
    except TimeoutError as exc:
        msg = f"no answer from {target.host} within {timeout:.0f}s"
        raise DeliveryError(msg, retryable=True) from exc
    except (OSError, http.client.HTTPException) as exc:
        msg = f"connection to {target.host} failed ({exc.__class__.__name__})"
        raise DeliveryError(msg, retryable=True) from exc
    finally:
        conn.close()
    return Response(status=resp.status, text=raw.decode("utf-8", errors="replace"))


# ── delivery ──────────────────────────────────────────────────────────


@dataclass
class Result:
    ok: bool
    retryable: bool = False
    status_code: int | None = None
    error: str = ""


def _scrub(text: str, sub: WebhookSubscription) -> str:
    """Remove any secret that might be echoed back in a response body."""
    for secret in (sub.get_url(), sub.get_secret()):
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***")
    return text


def attempt(sub: WebhookSubscription, payload: dict[str, Any], *, delivery_id: Any = None) -> Result:
    """One HTTP attempt; never raises."""
    try:
        request = build_request(sub, payload, delivery_id=delivery_id)
    except (ValueError, DecryptionError) as exc:
        return Result(ok=False, error=f"configuration: {exc}"[:500])
    try:
        response = post(request.url, request.body, request.headers)
    except DeliveryError as exc:
        return Result(ok=False, retryable=exc.retryable, status_code=exc.status, error=str(exc)[:500])
    except Exception as exc:  # noqa: BLE001 — defensive; keeps secrets out of tracebacks in logs
        return Result(ok=False, retryable=True, error=f"unexpected error ({exc.__class__.__name__})")
    if 200 <= response.status < 300:
        return Result(ok=True, status_code=response.status)
    if 300 <= response.status < 400:
        return Result(ok=False, status_code=response.status, error=f"redirect ({response.status}) not followed")
    detail = _scrub(response.text.strip().replace("\n", " "), sub)[:300]
    retryable = response.status == 429 or response.status >= 500
    return Result(
        ok=False,
        retryable=retryable,
        status_code=response.status,
        error=f"HTTP {response.status}: {detail}",
    )


def send_delivery(delivery: WebhookDelivery, *, final: bool = True) -> Result:
    """Attempt ``delivery`` once and record the outcome (stays ``pending`` when a retry follows)."""
    sub = delivery.subscription
    result = attempt(sub, delivery.payload, delivery_id=delivery.pk)
    now = timezone.now()
    delivery.attempts += 1
    delivery.response_code = result.status_code
    delivery.error = result.error[:500]
    if result.ok:
        delivery.status = WebhookDelivery.Status.SENT
        delivery.sent_at = now
    elif final or not result.retryable:
        delivery.status = WebhookDelivery.Status.FAILED
    delivery.save(update_fields=["attempts", "response_code", "error", "status", "sent_at", "updated_at"])
    record_outcome(sub, result, now)
    if not result.ok:
        logger.info(
            "Webhook delivery %s (%s, sub %s) attempt %s failed: %s",
            delivery.pk,
            delivery.event_type,
            sub.pk,
            delivery.attempts,
            result.error,
        )
    return result


def record_outcome(sub: WebhookSubscription, result: Result, now: datetime | None = None) -> None:
    now = now or timezone.now()
    if result.ok:
        WebhookSubscription.objects.filter(pk=sub.pk).update(last_success_at=now)
        sub.last_success_at = now
    else:
        WebhookSubscription.objects.filter(pk=sub.pk).update(last_error=result.error[:500], last_error_at=now)
        sub.last_error, sub.last_error_at = result.error[:500], now


def send_test(sub: WebhookSubscription) -> tuple[WebhookDelivery, Result]:
    """Send a sample event synchronously (single attempt) and record it as a delivery."""
    payload = sample_payload()
    delivery = WebhookDelivery.objects.create(
        subscription=sub,
        event_key=payload["event_id"],
        event_type=TEST_EVENT,
        payload=payload,
    )
    result = send_delivery(delivery, final=True)
    return delivery, result
