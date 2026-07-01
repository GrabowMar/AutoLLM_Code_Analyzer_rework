from django.contrib import admin

from backend.credentials.models import UserApiCredential


@admin.register(UserApiCredential)
class UserApiCredentialAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "provider",
        "key_prefix",
        "last_validation_status",
        "last_validated_at",
        "updated_at",
    )
    list_filter = ("provider", "last_validation_status")
    search_fields = ("user__email", "key_prefix")
    readonly_fields = (
        "encrypted_secret",
        "key_prefix",
        "last_validated_at",
        "last_validation_status",
        "last_validation_message",
        "created_at",
        "updated_at",
    )
