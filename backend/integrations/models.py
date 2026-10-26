"""Integration models (webhook subscriptions and deliveries).

Target URLs (Slack/Discord/Teams URLs embed their credential), signing
secrets and Telegram bot tokens are encrypted at rest with
:mod:`backend.common.crypto`; the API only ever exposes masked hints.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from backend.common import crypto


class WebhookKind(models.TextChoices):
    SLACK = "slack", "Slack"
    DISCORD = "discord", "Discord"
    TEAMS = "teams", "Microsoft Teams"
    NTFY = "ntfy", "ntfy"
    TELEGRAM = "telegram", "Telegram"
    GENERIC = "generic", _("Generic JSON (signed)")


def mask_url(url: str) -> str:
    """``https://hooks.slack.com/…/x9Qa`` — scheme, host and the last 4 characters only."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "…"
    tail = url.rstrip("/")[-4:]
    return f"{parts.scheme}://{parts.hostname or ''}/…{tail}"


def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    return f"…{secret[-4:]}" if len(secret) >= 12 else "…"


class WebhookSubscription(models.Model):
    """One outbound notification channel of a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="webhook_subscriptions",
    )
    name = models.CharField(_("name"), max_length=120)
    kind = models.CharField(_("kind"), max_length=16, choices=WebhookKind)
    # Target URL (empty for telegram, whose URL is derived from the bot token).
    encrypted_url = models.TextField(_("encrypted target URL"), blank=True, default="")
    url_hint = models.CharField(_("URL hint"), max_length=200, blank=True, default="")
    # generic: HMAC signing secret; telegram: bot token; ntfy: optional access token.
    encrypted_secret = models.TextField(_("encrypted secret"), blank=True, default="")
    secret_hint = models.CharField(_("secret hint"), max_length=16, blank=True, default="")
    telegram_chat_id = models.CharField(_("Telegram chat id"), max_length=64, blank=True, default="")
    events = models.JSONField(_("events"), default=list, blank=True)
    include_experiment_jobs = models.BooleanField(
        _("include experiment jobs"),
        default=False,
        help_text=_("Also send generation.* events for the individual jobs of an experiment."),
    )
    enabled = models.BooleanField(_("enabled"), default=True)
    last_success_at = models.DateTimeField(_("last success at"), null=True, blank=True)
    last_error = models.CharField(_("last error"), max_length=500, blank=True, default="")
    last_error_at = models.DateTimeField(_("last error at"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Webhook subscription")
        verbose_name_plural = _("Webhook subscriptions")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "enabled"], name="integ_webhook_user_enabled")]

    def __str__(self) -> str:
        return f"{self.name} ({self.kind})"

    # ── secret helpers ────────────────────────────────────────────────

    def set_url(self, url: str) -> None:
        url = (url or "").strip()
        self.encrypted_url = crypto.encrypt(url) if url else ""
        self.url_hint = mask_url(url)[:200]

    def get_url(self) -> str:
        return crypto.decrypt(self.encrypted_url) if self.encrypted_url else ""

    def set_secret(self, secret: str) -> None:
        secret = (secret or "").strip()
        self.encrypted_secret = crypto.encrypt(secret) if secret else ""
        self.secret_hint = mask_secret(secret)

    def get_secret(self) -> str:
        return crypto.decrypt(self.encrypted_secret) if self.encrypted_secret else ""

    @property
    def has_secret(self) -> bool:
        return bool(self.encrypted_secret)


class WebhookDelivery(models.Model):
    """One event sent (or to be sent) to one subscription."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")

    subscription = models.ForeignKey(
        WebhookSubscription,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    event_key = models.CharField(_("event key"), max_length=200)
    event_type = models.CharField(_("event type"), max_length=64)
    payload = models.JSONField(_("payload"), default=dict, blank=True)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    response_code = models.PositiveSmallIntegerField(_("response code"), null=True, blank=True)
    error = models.CharField(_("error"), max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)

    class Meta:
        verbose_name = _("Webhook delivery")
        verbose_name_plural = _("Webhook deliveries")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["subscription", "event_key"], name="integ_webhook_delivery_once"),
        ]
        indexes = [models.Index(fields=["subscription", "-created_at"], name="integ_delivery_sub_recent")]

    def __str__(self) -> str:
        return f"{self.event_key} → {self.subscription_id} ({self.status})"
