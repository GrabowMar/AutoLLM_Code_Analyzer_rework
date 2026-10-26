from django.contrib import admin

from backend.integrations.models import WebhookDelivery
from backend.integrations.models import WebhookSubscription


@admin.register(WebhookSubscription)
class WebhookSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "user", "enabled", "url_hint", "last_success_at", "last_error_at", "created_at")
    list_filter = ("kind", "enabled")
    search_fields = ("name", "user__email")
    raw_id_fields = ("user",)
    # Ciphertexts are never shown; secrets are set through the API.
    exclude = ("encrypted_url", "encrypted_secret")
    readonly_fields = (
        "url_hint",
        "secret_hint",
        "last_success_at",
        "last_error",
        "last_error_at",
        "created_at",
        "updated_at",
    )


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ("event_key", "event_type", "subscription", "status", "attempts", "response_code", "created_at")
    list_filter = ("status", "event_type")
    search_fields = ("event_key", "subscription__name")
    raw_id_fields = ("subscription",)
    readonly_fields = ("created_at", "updated_at", "sent_at")
