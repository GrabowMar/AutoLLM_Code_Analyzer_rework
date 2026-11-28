"""Django admin for the co-authors models: read-only.

An invite made in admin would have no token, and the activity trail must not be
editable, so nothing can be added or changed here. Token hashes are excluded.
Deleting stays possible, otherwise deleting a user in admin would be blocked.
"""

from __future__ import annotations

from django.contrib import admin

from backend.coauthors.models import CoauthorAction
from backend.coauthors.models import CoauthorInvite
from backend.coauthors.models import Coauthorship


class _ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(CoauthorInvite)
class CoauthorInviteAdmin(_ReadOnlyAdmin):
    list_display = (
        "token_prefix",
        "created_by",
        "label",
        "uses",
        "max_uses",
        "expires_at",
        "revoked_at",
        "created_at",
    )
    search_fields = ("token_prefix", "label", "created_by__email")
    exclude = ("token_hash",)
    readonly_fields = (
        "created_by",
        "token_prefix",
        "label",
        "max_uses",
        "uses",
        "signup_uses",
        "expires_at",
        "revoked_at",
        "created_at",
    )


@admin.register(Coauthorship)
class CoauthorshipAdmin(_ReadOnlyAdmin):
    list_display = ("owner", "member", "created_at", "revoked_at", "last_active_at")
    list_filter = (("revoked_at", admin.EmptyFieldListFilter),)
    search_fields = ("owner__email", "member__email")
    readonly_fields = (
        "owner",
        "member",
        "invite",
        "created_at",
        "revoked_at",
        "revoked_by",
        "last_active_at",
        "last_selected_at",
    )


@admin.register(CoauthorAction)
class CoauthorActionAdmin(_ReadOnlyAdmin):
    list_display = ("created_at", "coauthorship", "method", "path", "status_code")
    list_filter = ("method",)
    search_fields = ("path", "coauthorship__member__email", "coauthorship__owner__email")
    readonly_fields = ("coauthorship", "method", "path", "status_code", "created_at")
