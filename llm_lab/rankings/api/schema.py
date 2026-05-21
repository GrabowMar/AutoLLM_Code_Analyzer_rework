"""Ninja schemas for rankings."""

from __future__ import annotations

from typing import Any

from ninja import Schema


class FindingsRollup(Schema):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0


class RankingRow(Schema):
    model_id: str
    model_name: str
    provider: str
    is_free: bool = False
    context_length: int | None = None
    price_per_million_input: float | None = None
    price_per_million_output: float | None = None
    apps: int = 0
    apps_completed: int = 0
    avg_duration: float = 0.0
    findings: FindingsRollup
    benchmark_score: float
    cost_efficiency_score: float
    accessibility_score: float
    adoption_score: float
    mss_score: float
    composite_score: float

    # Allow extra benchmark fields to pass through.
    class Config:
        extra = "allow"


class Pagination(Schema):
    page: int
    per_page: int
    total: int
    total_pages: int
    has_next: bool
    has_prev: bool


class RankingStats(Schema):
    total: int
    with_benchmarks: int
    unique_providers: int


class RankingsResponse(Schema):
    count: int
    rankings: list[dict[str, Any]]
    pagination: Pagination
    statistics: RankingStats


class StatusResponse(Schema):
    benchmarks: dict[str, int]
    total_benchmark_rows: int
    models_with_benchmarks: int
    total_models: int


class TopModelsResponse(Schema):
    count: int
    models: list[dict[str, Any]]
