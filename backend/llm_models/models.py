"""LLM Model — stores AI model capabilities, pricing, and metadata from OpenRouter."""

import math

from django.db import models
from django.utils.translation import gettext_lazy as _


class LLMModel(models.Model):
    """AI model fetched from OpenRouter with capabilities, pricing, and metadata."""

    model_id = models.CharField(
        _("OpenRouter model ID"),
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("LLM Model")
        verbose_name_plural = _("LLM Models")
        ordering = ["provider", "model_name"]

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
