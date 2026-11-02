"""App config of the human-evaluation module."""

from __future__ import annotations

from django.apps import AppConfig


class HumanEvalConfig(AppConfig):
    name = "backend.human_eval"
    label = "human_eval"
    verbose_name = "Human evaluation"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from backend.human_eval import signals

        signals.connect(self)
