"""App config of the co-authors module (invite links and "work as" workspaces)."""

from __future__ import annotations

from django.apps import AppConfig


class CoauthorsConfig(AppConfig):
    name = "backend.coauthors"
    label = "coauthors"
    verbose_name = "Co-authors"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from backend.coauthors import signals

        signals.connect()
