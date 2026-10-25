from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ExecutionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backend.execution"
    verbose_name = _("Execution")
