from django.apps import AppConfig


class SystemConfig(AppConfig):
    name = "backend.system"
    verbose_name = "System"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from backend.system import signals

        signals.connect(self)
