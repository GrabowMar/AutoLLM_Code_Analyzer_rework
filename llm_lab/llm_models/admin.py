from django.contrib import admin

from llm_lab.llm_models.models import LLMModel


@admin.register(LLMModel)
class LLMModelAdmin(admin.ModelAdmin):
    list_display = [
        "model_name",
        "provider",
        "canonical_slug",
        "is_free",
        "context_window",
        "input_price_per_token",
        "output_price_per_token",
        "created_at",
    ]
    list_filter = [
        "provider",
        "is_free",
        "supports_vision",
        "supports_function_calling",
    ]
    search_fields = ["model_name", "model_id", "canonical_slug", "provider"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-updated_at"]
