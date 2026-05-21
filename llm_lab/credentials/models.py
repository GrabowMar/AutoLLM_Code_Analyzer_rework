"""Per-user, per-provider third-party API credentials.

Secrets are encrypted at rest using :mod:`llm_lab.common.crypto`. The raw
secret never leaves the database except through the resolver service that
needs it to make upstream API calls. The API layer only ever returns the
``key_prefix`` for display.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from llm_lab.common import crypto


class Provider(models.TextChoices):
    OPENROUTER = "openrouter", "OpenRouter"
    HUGGINGFACE = "huggingface", "Hugging Face"


class ValidationStatus(models.TextChoices):
    UNVALIDATED = "unvalidated", "Not yet validated"
    VALID = "valid", "Valid"
    INVALID = "invalid", "Invalid"
    RATE_LIMITED = "rate_limited", "Rate limited"
    NETWORK_ERROR = "network_error", "Network error"


PREFIX_LENGTH = 12


class UserApiCredential(models.Model):
    """A user's API credential for a single external provider."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_credentials",
    )
    provider = models.CharField(max_length=32, choices=Provider.choices)
    encrypted_secret = models.TextField()
    key_prefix = models.CharField(max_length=PREFIX_LENGTH, blank=True, default="")
    last_validated_at = models.DateTimeField(null=True, blank=True)
    last_validation_status = models.CharField(
        max_length=32,
        choices=ValidationStatus.choices,
        default=ValidationStatus.UNVALIDATED,
    )
    last_validation_message = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "provider")]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.provider} ({self.key_prefix}…)"

    # ── secret helpers ────────────────────────────────────────────────

    def set_secret(self, raw: str) -> None:
        """Encrypt and store ``raw``, updating ``key_prefix``."""
        raw = (raw or "").strip()
        if not raw:
            msg = "API key cannot be empty"
            raise ValueError(msg)
        self.encrypted_secret = crypto.encrypt(raw)
        self.key_prefix = raw[:PREFIX_LENGTH]

    def get_secret(self) -> str:
        """Decrypt and return the stored secret."""
        return crypto.decrypt(self.encrypted_secret)

    def mark_validation(self, status: str, message: str = "") -> None:
        self.last_validation_status = status
        self.last_validation_message = message[:500]
        self.last_validated_at = timezone.now()
        self.save(
            update_fields=[
                "last_validation_status",
                "last_validation_message",
                "last_validated_at",
                "updated_at",
            ],
        )
