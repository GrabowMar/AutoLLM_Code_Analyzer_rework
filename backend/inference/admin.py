"""Django admin registrations for the inference app.

Secrets (``encrypted_api_key``, token hashes) are excluded or read-only.
"""

from __future__ import annotations

from django.contrib import admin

from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.inference.models import RelaySession
from backend.inference.models import RelayToken


@admin.register(LLMEndpoint)
class LLMEndpointAdmin(admin.ModelAdmin):
    list_display = (
        "slug",
        "name",
        "kind",
        "connection",
        "enabled",
        "is_system",
        "owner",
        "visibility",
        "max_concurrency",
    )
    list_filter = ("kind", "connection", "enabled", "is_system", "visibility")
    search_fields = ("slug", "name")
    exclude = ("encrypted_api_key",)
    readonly_fields = ("created_at", "updated_at", "last_inventory_at", "server_version")


@admin.register(RelayToken)
class RelayTokenAdmin(admin.ModelAdmin):
    list_display = ("prefix", "endpoint", "label", "created_at", "last_used_at", "expires_at", "revoked_at")
    search_fields = ("prefix", "label")
    readonly_fields = ("prefix", "secret_hash", "created_at", "last_used_at")


@admin.register(RelaySession)
class RelaySessionAdmin(admin.ModelAdmin):
    list_display = ("id", "endpoint", "role", "hostname", "relay_version", "capacity", "last_seen_at", "ended_at")
    list_filter = ("role",)
    search_fields = ("hostname",)


@admin.register(LLMRequest)
class LLMRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "endpoint", "source", "status", "model_name", "attempt", "created_at", "completed_at")
    list_filter = ("status", "source")
    search_fields = ("model_name", "dedupe_key")
    raw_id_fields = ("job", "session")
