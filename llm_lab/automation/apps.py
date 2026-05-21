from django.apps import AppConfig


class AutomationConfig(AppConfig):
    default_auto_field = "django.db.models.UUIDField"
    name = "llm_lab.automation"
    verbose_name = "Automation"
