from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class GenerationConfig(AppConfig):
    name = "llm_lab.generation"
    verbose_name = _("Generation")
