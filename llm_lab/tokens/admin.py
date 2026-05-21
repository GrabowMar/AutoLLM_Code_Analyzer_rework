"""Django admin for API tokens.

Secrets are never exposed: only the public ``prefix``, expiry and last-use
metadata are shown.
"""

from __future__ import annotations

from django.contrib import admin

from llm_lab.tokens.models import ApiToken


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "prefix",
        "expires_at",
        "last_used_at",
        "revoked_at",
        "created_at",
    )
    list_filter = ("revoked_at", "expires_at")
    search_fields = ("name", "prefix", "user__email")
    readonly_fields = (
        "id",
        "token_hash",
        "prefix",
        "last_used_at",
        "last_used_ip",
        "created_at",
        "updated_at",
    )
    exclude = ("token_hash",)
