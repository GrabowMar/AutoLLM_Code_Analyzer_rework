from django.apps import AppConfig


class RuntimeConfig(AppConfig):
    name = "llm_lab.runtime"
    verbose_name = "Runtime"
    default_auto_field = "django.db.models.BigAutoField"
