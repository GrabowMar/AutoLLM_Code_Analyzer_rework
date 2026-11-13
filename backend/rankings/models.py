"""Rankings models: raw source snapshots and ranking / selection snapshots (MSS v2)."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class SourceSnapshot(models.Model):
    """One raw external response kept verbatim (append-only), with its provenance.

    The MSS v2 popularity import stores every OpenRouter Data API response it
    used here, so a selection can cite exactly what it read.
    """

    class Source(models.TextChoices):
        RANKINGS_DAILY = "openrouter_rankings_daily", "OpenRouter rankings-daily"
        TASK_CLASSIFICATIONS = "openrouter_task_classifications", "OpenRouter task classifications"
        MODELS_INDEX = "openrouter_models_index", "OpenRouter models index"
        POPULARITY_MARKET = "openrouter_popularity_market", "Derived popularity market (per day, per model)"

    source = models.CharField(max_length=64, choices=Source.choices, db_index=True)
    fetched_at = models.DateTimeField(db_index=True)
    as_of = models.CharField(max_length=64, blank=True, default="")
    window_start = models.DateField(null=True, blank=True)
    window_end = models.DateField(null=True, blank=True)
    params = models.JSONField(default=dict, blank=True)
    payload = models.JSONField()
    sha256 = models.CharField(max_length=64)
    license = models.CharField(max_length=64, blank=True, default="")
    citation = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ["-fetched_at", "-pk"]
        get_latest_by = "fetched_at"

    def __str__(self) -> str:
        return f"SourceSnapshot({self.source}, {self.fetched_at.isoformat()})"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            msg = "SourceSnapshot is append-only"
            raise ValueError(msg)
        super().save(*args, **kwargs)


class RankingSnapshot(models.Model):
    """A stored rankings payload or an MSS v2 study selection (draft -> confirmed -> superseded)."""

    class Kind(models.TextChoices):
        RANKINGS = "rankings", "Rankings"
        SELECTION = "selection", "Study selection"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        SUPERSEDED = "superseded", "Superseded"

    created_at = models.DateTimeField(auto_now_add=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.RANKINGS, db_index=True)
    payload = models.JSONField()
    version = models.CharField(max_length=32, default="v1")
    params = models.JSONField(default=dict, blank=True)
    inputs_sha256 = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # {final_ids, overrides: [{replace, with, reason}], notes, nominal_ids}
    confirmation = models.JSONField(default=dict, blank=True)
    code_version = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-pk"]
        get_latest_by = "created_at"

    def __str__(self) -> str:
        return f"RankingSnapshot({self.kind}, {self.status}, {self.created_at.isoformat()})"
