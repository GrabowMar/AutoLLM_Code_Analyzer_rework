"""Models for cached benchmark results & ranking snapshots."""

from __future__ import annotations

from django.db import models


class BenchmarkResult(models.Model):
    """A score for one (model, benchmark) pair, fetched or seeded."""

    class Source(models.TextChoices):
        SEED = "seed", "Seed fixture"
        OPENROUTER = "openrouter", "OpenRouter"
        HUGGINGFACE = "huggingface", "Hugging Face"
        MANUAL = "manual", "Manual entry"

    model_id = models.CharField(max_length=200, db_index=True)
    benchmark = models.CharField(max_length=64, db_index=True)
    score = models.FloatField()
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        default=Source.SEED,
    )
    fetched_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = [("model_id", "benchmark")]
        ordering = ["model_id", "benchmark"]
        indexes = [models.Index(fields=["benchmark", "-score"])]

    def __str__(self) -> str:
        return f"{self.model_id}/{self.benchmark}={self.score}"


class RankingSnapshot(models.Model):
    """Cached rankings payload (full computed list) for fast reads."""

    created_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField()
    version = models.CharField(max_length=32, default="v1")

    class Meta:
        ordering = ["-created_at"]
        get_latest_by = "created_at"

    def __str__(self) -> str:
        return f"RankingSnapshot({self.created_at.isoformat()})"
