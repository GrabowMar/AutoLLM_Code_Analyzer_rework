from django.apps import AppConfig


class RuntimeConfig(AppConfig):
    name = "backend.runtime"
    verbose_name = "Runtime"
    default_auto_field = "django.db.models.BigAutoField"
