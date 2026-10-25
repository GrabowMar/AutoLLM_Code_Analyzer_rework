"""LLM endpoints (OpenRouter, Ollama, OpenAI-compatible), relay credentials and the request queue.

Spec §B.1. ``LLMEndpoint`` rows describe where a model runs; the single
``is_system`` row is OpenRouter. ``RelayToken``/``RelaySession`` authenticate
and track relay processes on remote GPU boxes. ``LLMRequest`` is the durable
per-endpoint request queue (and semaphore): every non-OpenRouter LLM call is
one row, claimed by a relay poll or a direct pump.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

SYSTEM_ENDPOINT_SLUG = "openrouter"
RESERVED_ENDPOINT_SLUGS = frozenset({"openrouter", "local"})
ENDPOINT_SLUG_RE = r"^[a-z0-9][a-z0-9-]{1,38}$"

SYSTEM_ENDPOINT_DEFAULTS = {
    "slug": SYSTEM_ENDPOINT_SLUG,
    "name": "OpenRouter",
    "kind": "openrouter",
    "connection": "direct",
    "api_flavor": "openai",
    "base_url": "https://openrouter.ai/api/v1",
    "max_concurrency": 64,
    "max_active_jobs": 64,
    "visibility": "shared",
    "owner": None,
}


class LLMEndpoint(models.Model):
    """A place models run: the OpenRouter system row or a user's Ollama / OpenAI-compatible server."""

    class Kind(models.TextChoices):
        OPENROUTER = "openrouter", _("OpenRouter")
        OLLAMA = "ollama", _("Ollama")
        OPENAI_COMPATIBLE = "openai_compatible", _("OpenAI-compatible")

    class Connection(models.TextChoices):
        DIRECT = "direct", _("Direct")
        RELAY = "relay", _("Relay")

    class ApiFlavor(models.TextChoices):
        # native is valid only for kind=ollama (constraint below)
        NATIVE = "native", _("Native")
        OPENAI = "openai", _("OpenAI")

    class Visibility(models.TextChoices):
        PRIVATE = "private", _("Private")
        SHARED = "shared", _("Shared")

    id = models.BigAutoField(primary_key=True)
    # ^[a-z0-9][a-z0-9-]{1,38}$; reserved: openrouter, local; immutable after create
    # (it is embedded in local LLMModel.model_id, a fingerprint input).
    slug = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    connection = models.CharField(max_length=12, choices=Connection.choices, default=Connection.RELAY)
    api_flavor = models.CharField(max_length=12, choices=ApiFlavor.choices, default=ApiFlavor.NATIVE)
    base_url = models.CharField(max_length=500, blank=True, default="")  # direct only
    # direct only; backend.common.crypto; never serialized
    encrypted_api_key = models.TextField(blank=True, default="")
    api_key_prefix = models.CharField(max_length=12, blank=True, default="")
    # in-flight upstream requests (1..256)
    max_concurrency = models.PositiveSmallIntegerField(default=1)
    # NULL: max_concurrency in both lanes (system row: 64)
    max_active_jobs = models.PositiveSmallIntegerField(null=True, blank=True)
    # The resident-model limit K (NULL = unlimited).
    max_resident_models = models.PositiveSmallIntegerField(null=True, blank=True)
    # per upstream call (30..7200)
    request_timeout_s = models.PositiveIntegerField(default=900)
    keep_alive = models.CharField(max_length=16, blank=True, default="30m")
    # ollama: effective num_ctx = min(model ctx, cap)
    num_ctx_cap = models.PositiveIntegerField(default=32768)
    # openai_compatible: tool support is unknown otherwise
    assume_tools = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="llm_endpoints",
    )
    # SHARED settable by staff only
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PRIVATE)

    # Observed (written by inventory / pumps / relay)
    server_version = models.CharField(max_length=64, blank=True, default="")
    last_inventory_at = models.DateTimeField(null=True, blank=True)
    # The last /api/ps listing: name, digest, size, size_vram, expires_at,
    # context_length per model (plus an "at" timestamp).
    loaded_models = models.JSONField(default=list, blank=True)
    last_error = models.TextField(blank=True, default="")
    inventory_resync_requested = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("LLM endpoint")
        verbose_name_plural = _("LLM endpoints")
        ordering = ["-is_system", "name"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(kind="openrouter") | Q(connection="direct"),
                name="llmendpoint_openrouter_direct",
            ),
            models.CheckConstraint(
                condition=Q(api_flavor="openai") | Q(kind="ollama"),
                name="llmendpoint_native_only_ollama",
            ),
            models.CheckConstraint(
                condition=Q(max_concurrency__gte=1) & Q(max_concurrency__lte=256),
                name="llmendpoint_concurrency_range",
            ),
            models.CheckConstraint(
                condition=Q(is_system=True) | Q(owner__isnull=False),
                name="llmendpoint_owner_unless_system",
            ),
            models.UniqueConstraint(
                fields=["is_system"],
                condition=Q(is_system=True),
                name="llmendpoint_single_system",
            ),
        ]
        indexes = [models.Index(fields=["owner", "enabled"], name="llmendpoint_owner_enabled")]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"


class RelayToken(models.Model):
    """An endpoint-scoped relay credential ``llr_<prefix8>.<secret>``; only the secret's sha256 is stored."""

    id = models.BigAutoField(primary_key=True)
    endpoint = models.ForeignKey(LLMEndpoint, on_delete=models.CASCADE, related_name="relay_tokens")
    label = models.CharField(max_length=80, blank=True, default="")
    prefix = models.CharField(max_length=16, unique=True)  # "llr_" + 8 base32 chars, shown in UI
    secret_hash = models.CharField(max_length=64)  # sha256 hex of the secret part
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)  # throttled to 1/min
    expires_at = models.DateTimeField(null=True, blank=True)  # rotation grace
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("relay token")
        verbose_name_plural = _("relay tokens")
        indexes = [models.Index(fields=["endpoint", "revoked_at"], name="relaytoken_endpoint_active")]

    def __str__(self) -> str:
        return f"{self.prefix} ({self.endpoint_id})"


class RelaySession(models.Model):
    """One connected relay process. v1: at most one ``active`` session per endpoint; others are standby."""

    class Role(models.TextChoices):
        ACTIVE = "active", _("Active")
        STANDBY = "standby", _("Standby")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    endpoint = models.ForeignKey(LLMEndpoint, on_delete=models.CASCADE, related_name="relay_sessions")
    token = models.ForeignKey(RelayToken, on_delete=models.CASCADE, related_name="sessions")
    # persisted in relay --state-dir; identifies the box across restarts
    relay_id = models.UUIDField(null=True)
    hostname = models.CharField(max_length=255, blank=True, default="")
    relay_version = models.CharField(max_length=32)
    upstream_kind = models.CharField(max_length=24)
    upstream_hint = models.CharField(max_length=255, blank=True, default="")
    # The smaller of the relay's --parallel and endpoint.max_concurrency.
    capacity = models.PositiveSmallIntegerField(default=1)
    role = models.CharField(max_length=8, choices=Role.choices, default=Role.ACTIVE)
    client_ip = models.GenericIPAddressField(null=True)
    started_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_reason = models.CharField(max_length=40, blank=True, default="")
    # [{name, digest}] this session can serve (<= 500 entries). Declared last:
    # the attribute shadows the ``models`` module for the rest of the class body.
    models = models.JSONField(default=list)

    class Meta:
        verbose_name = _("relay session")
        verbose_name_plural = _("relay sessions")
        indexes = [
            models.Index(fields=["endpoint", "ended_at", "last_seen_at"], name="relaysession_live"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["endpoint"],
                condition=Q(ended_at__isnull=True, role="active"),
                name="relaysession_one_active",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.hostname or self.id} ({self.role})"


class LLMRequest(models.Model):
    """One upstream LLM call queued for an endpoint (relay poll or direct pump claims it).

    Body policy (§B.1): ``body`` is cleared on completion; ``response`` is
    kept after delivery (several waiters may attach via ``dedupe_key``) and
    cleared by ``finish_stage`` or the daily purge.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        LEASED = "leased", _("Leased")
        DONE = "done", _("Done")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")

    class Source(models.TextChoices):
        SCAFFOLDING = "scaffolding", _("Scaffolding")
        AGENT_PROXY = "agent_proxy", _("Agent proxy")
        ANALYSIS = "analysis", _("Analysis")
        WARMUP = "warmup", _("Warm-up")
        PROBE = "probe", _("Probe")
        INVENTORY = "inventory", _("Inventory")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    endpoint = models.ForeignKey(LLMEndpoint, on_delete=models.PROTECT, related_name="requests")
    job = models.ForeignKey(
        "generation.GenerationJob",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="llm_requests",
    )
    job_attempt = models.PositiveSmallIntegerField(default=0)
    source = models.CharField(max_length=16, choices=Source.choices)
    dedupe_key = models.CharField(max_length=64, blank=True, default="")
    model_name = models.CharField(max_length=300, blank=True, default="")  # served name ("" for inventory)
    digest = models.CharField(max_length=80, blank=True, default="")  # required digest; "" = any
    priority = models.SmallIntegerField(default=0)
    method = models.CharField(max_length=8, default="POST")
    path = models.CharField(max_length=64)  # "/api/chat" | "/v1/chat/completions" | ...
    body = models.JSONField(null=True)  # cleared on completion
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    attempt = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    session = models.ForeignKey(RelaySession, null=True, blank=True, on_delete=models.SET_NULL)
    pump_owner = models.CharField(max_length=80, blank=True, default="")
    claim_token = models.CharField(max_length=32, blank=True, default="")
    # relay: leased_at + RELAY_ACK_LEASE_S until acked
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    # relay echoed (id, claim_token) in inflight
    acked_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField()
    # kept after delivery; cleared by finish_stage/purge
    response = models.JSONField(null=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error_kind = models.CharField(max_length=24, blank=True, default="")
    error = models.TextField(blank=True, default="")
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    upstream_ms = models.PositiveIntegerField(null=True, blank=True)
    load_ms = models.PositiveIntegerField(null=True, blank=True)
    eval_ms = models.PositiveIntegerField(null=True, blank=True)  # ollama eval_duration -> tokens/s
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    leased_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("LLM request")
        verbose_name_plural = _("LLM requests")
        indexes = [
            models.Index(
                fields=["endpoint", "-priority", "created_at"],
                condition=Q(status="queued"),
                name="llmreq_queued",
            ),
            models.Index(
                fields=["endpoint", "session"],
                condition=Q(status="leased"),
                name="llmreq_leased",
            ),
            models.Index(
                fields=["lease_expires_at"],
                condition=Q(status="leased"),
                name="llmreq_lease",
            ),
            models.Index(
                fields=["job", "dedupe_key"],
                condition=Q(status__in=["queued", "leased", "done"]) & Q(delivered_at__isnull=True),
                name="llmreq_dedupe",
            ),
            models.Index(fields=["endpoint", "completed_at"], name="llmreq_metrics"),
        ]

    def __str__(self) -> str:
        return f"{self.source} {self.path} ({self.status})"
