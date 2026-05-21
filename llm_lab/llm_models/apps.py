from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LlmModelsConfig(AppConfig):
    name = "llm_lab.llm_models"
    verbose_name = _("LLM Models")
