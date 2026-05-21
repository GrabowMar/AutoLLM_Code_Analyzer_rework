from __future__ import annotations

from django.apps import AppConfig


class AnalysisConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "llm_lab.analysis"
    verbose_name = "Analysis"

    def ready(self) -> None:
        import llm_lab.analysis.services.ai_analyzers
        import llm_lab.analysis.services.dynamic_analyzers
        import llm_lab.analysis.services.performance
        import llm_lab.analysis.services.static_analyzers  # noqa: F401
