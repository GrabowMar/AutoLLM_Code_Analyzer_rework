from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backend.integrations"
    verbose_name = "Integrations"

    def ready(self) -> None:
        from backend.integrations import signals

        signals.connect(self)
