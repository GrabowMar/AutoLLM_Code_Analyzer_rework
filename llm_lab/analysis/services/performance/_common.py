"""Shared constants and helpers for performance analyzers."""

from __future__ import annotations

LIGHTHOUSE_CATEGORY_MAP: dict[str, str] = {
    "performance": "performance",
    "accessibility": "accessibility",
    "best-practices": "best_practice",
    "seo": "seo",
}

SCORE_SEVERITY_THRESHOLDS: list[tuple[float, str]] = [
    (0.5, "high"),
    (0.75, "medium"),
    (0.9, "low"),
]

GRADE_THRESHOLDS: list[tuple[float, str]] = [
    (0.9, "A"),
    (0.8, "B"),
    (0.7, "C"),
    (0.5, "D"),
]

_PASS_THRESHOLD = 0.9
_CODE_SPLIT_MIN_SIZE = 2000

_INLINE_BLOCK_THRESHOLD = 500

_SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 0.15,
    "high": 0.10,
    "medium": 0.05,
    "low": 0.02,
    "info": 0.0,
}

_FRONTEND_EXTS = (
    ".html",
    ".htm",
    ".svelte",
    ".vue",
    ".jsx",
    ".tsx",
    ".js",
    ".ts",
    ".css",
    ".scss",
)
_FRONTEND_DIRS = (
    "frontend",
    "src",
    "static",
    "templates",
    "public",
)

_BACKEND_EXTS = (".py", ".rb", ".java", ".go", ".rs", ".php")
_BACKEND_DIRS = (
    "backend",
    "server",
    "api",
    "views",
    "models",
)


def _score_to_severity(score: float | None) -> str:
    if score is None:
        return "info"
    for threshold, severity in SCORE_SEVERITY_THRESHOLDS:
        if score < threshold:
            return severity
    return "info"


def _average_score_to_grade(avg: float) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if avg >= threshold:
            return grade
    return "F"
