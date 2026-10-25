"""LLM Model — capabilities, pricing and metadata of OpenRouter catalog rows and local endpoint models."""

from __future__ import annotations

import math

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class LLMModelQuerySet(models.QuerySet):
    def visible_to(self, user) -> LLMModelQuerySet:
        """Models ``user`` may see and use: the OpenRouter catalog plus models of visible endpoints.

        Staff see every row. This is the only way views, the ModelPicker
        catalog, condition creation and job creation resolve a model pk
        (spec §B.2); an invisible pk is a 404.
        """
        if user is not None and getattr(user, "is_staff", False):
            return self.all()
        from backend.inference.access import visible_endpoints

        return self.filter(Q(endpoint__isnull=True) | Q(endpoint__in=visible_endpoints(user)))


LLMModelManager = models.Manager.from_queryset(LLMModelQuerySet)


class LLMModel(models.Model):
    """An AI model: an OpenRouter catalog row (``endpoint`` NULL) or a model discovered on a local endpoint.

    ``model_id`` stays globally unique; local rows use
    ``local-<endpoint_slug>/<sanitized served name>``.
    """

    model_id = models.CharField(
        _("Model ID"),
        max_length=300,
        unique=True,
        db_index=True,
        help_text="e.g. openai/gpt-4o",
    )
    canonical_slug = models.SlugField(
        _("URL slug"),
        max_length=300,
        unique=True,
        db_index=True,
        allow_unicode=True,
        help_text="URL-safe version: openai_gpt-4o",
    )
    provider = models.CharField(_("provider"), max_length=100, db_index=True)
    model_name = models.CharField(_("display name"), max_length=300)
    description = models.TextField(_("description"), blank=True, default="")

    # Capabilities
    is_free = models.BooleanField(_("free"), default=False)
    context_window = models.IntegerField(_("context window (tokens)"), default=0)
    max_output_tokens = models.IntegerField(_("max output tokens"), default=0)
    supports_function_calling = models.BooleanField(default=False)
    supports_vision = models.BooleanField(default=False)
    supports_streaming = models.BooleanField(default=False)
    supports_json_mode = models.BooleanField(default=False)

    # Pricing (per token)
    input_price_per_token = models.FloatField(_("input price/token"), default=0.0)
    output_price_per_token = models.FloatField(_("output price/token"), default=0.0)

    # Computed scores
    cost_efficiency = models.FloatField(_("cost efficiency (0-1)"), default=0.0)

    # Full OpenRouter payload + structured metadata
    capabilities_json = models.JSONField(
        _("raw OpenRouter payload"),
        default=dict,
        blank=True,
    )
    metadata = models.JSONField(_("structured metadata"), default=dict, blank=True)

    # Where the model runs. NULL = the OpenRouter catalog (every pre-existing row).
    endpoint = models.ForeignKey(
        "inference.LLMEndpoint",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="models",
    )
    # Exact name on the endpoint (Ollama tag, colon kept), e.g. "qwen2.5-coder:32b".
    served_model_name = models.CharField(max_length=300, blank=True, default="")
    digest = models.CharField(max_length=80, blank=True, default="")
    quantization = models.CharField(max_length=32, blank=True, default="")
    parameter_size = models.CharField(max_length=32, blank=True, default="")
    family = models.CharField(max_length=64, blank=True, default="")
    num_ctx_override = models.PositiveIntegerField(null=True, blank=True)
    is_available = models.BooleanField(default=True)
    is_hidden = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LLMModelManager()

    class Meta:
        verbose_name = _("LLM Model")
        verbose_name_plural = _("LLM Models")
        ordering = ["provider", "model_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["endpoint", "served_model_name"],
                condition=Q(endpoint__isnull=False),
                name="llmmodel_endpoint_served_unique",
            ),
        ]
        indexes = [models.Index(fields=["endpoint", "is_available"], name="llmmodel_endpoint_avail")]

    def __str__(self) -> str:
        return f"{self.provider}/{self.model_name}"

    @staticmethod
    def calculate_cost_efficiency(
        context_window: int,
        input_price: float,
        output_price: float,
    ) -> float:
        """Cost efficiency score (0-1). Free models get 1.0."""
        if input_price == 0 and output_price == 0:
            return 1.0
        if context_window <= 0 or (input_price <= 0 and output_price <= 0):
            return 0.0

        input_per_1m = input_price * 1_000_000
        output_per_1m = output_price * 1_000_000
        avg_price = (input_per_1m + output_per_1m) / 2
        context_norm = context_window / 128_000

        raw = context_norm / avg_price if avg_price > 0 else 0
        scaled = math.log(raw + 1) / math.log(101)
        return max(0.0, min(1.0, scaled))

    @property
    def is_local(self) -> bool:
        """True for models on a non-system (Ollama / OpenAI-compatible) endpoint."""
        return self.endpoint_id is not None and not self.endpoint.is_system

    def get_capabilities_list(self) -> list[str]:
        """Return human-readable list of supported capabilities."""
        caps: list[str] = []
        if self.supports_function_calling:
            caps.append("Function Calling")
        if self.supports_vision:
            caps.append("Vision")
        if self.supports_streaming:
            caps.append("Streaming")
        if self.supports_json_mode:
            caps.append("JSON Mode")
        # Always include Code for all models in this platform
        caps.insert(0, "Code")
        return caps
