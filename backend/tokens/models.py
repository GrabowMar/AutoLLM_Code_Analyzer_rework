from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class ApiToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="api_tokens",
    )
    name = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=128, unique=True)
    prefix = models.CharField(max_length=12)
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.CharField(max_length=45, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…)"

    def is_valid(self) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at >= timezone.now()

    def mark_used(self, ip: str = "") -> None:
        self.last_used_at = timezone.now()
        self.last_used_ip = ip or ""
        self.save(update_fields=["last_used_at", "last_used_ip", "updated_at"])
