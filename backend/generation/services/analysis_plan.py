"""Pre-registered analysis plans for experiments.

A plan states before launch what the experiment will test: its research
questions, the primary metric, the significance level and correction, the
ranking criteria and weights, and the exclusion rules. Launch freezes it
(``analysis_plan_frozen_at``) under a sha256 of its canonical JSON
(``analysis_plan_hash``), and it cannot change afterwards — clone the
experiment instead. The study report then labels every analysis the plan
declared *confirmatory* and everything else *exploratory*, and uses the
plan's alpha, correction and ranking weights.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

PLAN_VERSION = 1

# Outcomes a plan may name (per-app values, aggregated per condition/model).
METRICS: dict[str, str] = {
    "deploy_rate": "Share of evaluated apps that build and answer their health check",
    "pass_rate": "Mean conformance pass rate (0..1) over evaluated apps",
    "functional_passed": "Share of evaluated apps passing every conformance check",
    "unique_issues_per_app": "Deduplicated static issues per analyzed app",
    "issues_per_kloc": "Deduplicated static issues per 1,000 lines",
    "security_per_app": "Deduplicated security/secret issues per analyzed app",
    "cost_per_app": "Generation cost per app (USD)",
}
CORRECTIONS = ("holm", "bh", "none")
DIRECTIONS = ("two-sided", "greater", "less")
_MAX_QUESTIONS = 20


class PlanError(ValueError):
    """An analysis plan that does not validate."""


def normalize_analysis_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Validated, canonical plan ({} for no plan); raises :class:`PlanError`."""
    if not plan:
        return {}
    if not isinstance(plan, dict):
        msg = "analysis_plan must be an object"
        raise PlanError(msg)
    primary = plan.get("primary_metric")
    if primary not in METRICS:
        msg = f"primary_metric must be one of {', '.join(METRICS)}"
        raise PlanError(msg)
    secondary = [m for m in plan.get("secondary_metrics") or [] if m != primary]
    unknown = sorted(set(secondary) - set(METRICS))
    if unknown:
        msg = f"Unknown secondary metric(s): {', '.join(unknown)}"
        raise PlanError(msg)
    alpha = float(plan.get("alpha", 0.05))
    if not 0 < alpha < 0.5:
        msg = "alpha must be between 0 and 0.5"
        raise PlanError(msg)
    correction = plan.get("correction", "holm")
    if correction not in CORRECTIONS:
        msg = f"correction must be one of {', '.join(CORRECTIONS)}"
        raise PlanError(msg)

    questions = []
    raw_questions = plan.get("research_questions") or []
    if len(raw_questions) > _MAX_QUESTIONS:
        msg = f"At most {_MAX_QUESTIONS} research questions"
        raise PlanError(msg)
    for i, q in enumerate(raw_questions, start=1):
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            msg = f"Research question {i} needs a question"
            raise PlanError(msg)
        metric = q.get("metric") or primary
        if metric not in METRICS:
            msg = f"Research question {i}: unknown metric {metric!r}"
            raise PlanError(msg)
        direction = q.get("direction", "two-sided")
        if direction not in DIRECTIONS:
            msg = f"Research question {i}: direction must be one of {', '.join(DIRECTIONS)}"
            raise PlanError(msg)
        questions.append(
            {
                "id": str(q.get("id") or f"RQ{i}")[:20],
                "question": str(q["question"]).strip()[:1000],
                "hypothesis": str(q.get("hypothesis") or "").strip()[:1000],
                "metric": metric,
                "direction": direction,
            },
        )

    criteria = plan.get("ranking_criteria") or []
    if criteria:
        from backend.statistics.services.mcdm import criteria_from_config

        try:
            criteria = [
                {"key": c.key, "label": c.label, "direction": c.direction, "weight": round(c.weight, 6)}
                for c in criteria_from_config(criteria)
            ]
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"ranking_criteria: {exc}"
            raise PlanError(msg) from exc
        unknown = sorted({c["key"] for c in criteria} - set(METRICS))
        if unknown:
            msg = f"Unknown ranking criteria: {', '.join(unknown)}"
            raise PlanError(msg)

    exclusions = plan.get("exclusions") or {}
    return {
        "version": PLAN_VERSION,
        "research_questions": questions,
        "primary_metric": primary,
        "secondary_metrics": list(dict.fromkeys(secondary)),
        "alpha": alpha,
        "correction": correction,
        "ranking_criteria": criteria,
        "exclusions": {
            "truncated": bool(exclusions.get("truncated", True)),
            "infra_errors": True,  # an infra error says nothing about the sample
        },
        "min_apps_per_condition": int(plan.get("min_apps_per_condition", 0) or 0),
        "notes": str(plan.get("notes") or "")[:4000],
    }


def plan_hash(plan: dict[str, Any]) -> str:
    """sha256 of the plan's canonical JSON (sorted keys, no whitespace)."""
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def declared_metrics(plan: dict[str, Any]) -> set[str]:
    """Metrics a plan makes confirmatory (primary, secondary, research questions)."""
    if not plan:
        return set()
    return {
        plan["primary_metric"],
        *plan.get("secondary_metrics", []),
        *(q["metric"] for q in plan.get("research_questions", [])),
    }
