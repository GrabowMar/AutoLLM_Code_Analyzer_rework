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

# Outcomes a plan may name: per-app values (an app's repeats averaged), then
# the mean over a condition's (or model's) apps. Plans store only the keys, so
# these descriptions can be sharpened without touching a frozen plan.
METRICS: dict[str, str] = {
    "deploy_rate": "Share of evaluated apps that build and answer their health check",
    "pass_rate": "Mean conformance pass rate (0..1) over evaluated apps",
    "functional_passed": "Share of evaluated apps passing every conformance check",
    "unique_issues_per_app": "Deduplicated static issues of every kind per analyzed app",
    "issues_per_kloc": (
        "Deduplicated code issues per 1,000 non-test source lines (dependency advisories, runtime (DAST) issues "
        "and test code excluded)"
    ),
    "security_per_app": (
        "Deduplicated security/secret issues in the app's own non-test code per analyzed app (dependency "
        "advisories and runtime (DAST) issues excluded)"
    ),
    "cost_per_app": "Generation cost per app (USD)",
}
CORRECTIONS = ("holm", "bh", "none")
DIRECTIONS = ("two-sided", "greater", "less")
# Which tests one correction covers: every declared (metric, condition) test
# together ("all", the default), or each metric's tests on their own
# ("per_metric", the only behaviour of plans frozen before this setting
# existed: those keep it, so a frozen plan's meaning never changes).
MULTIPLICITY = ("all", "per_metric")
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
    multiplicity = plan.get("multiplicity", "all")
    if multiplicity not in MULTIPLICITY:
        msg = f"multiplicity must be one of {', '.join(MULTIPLICITY)}"
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
        "multiplicity": multiplicity,
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


def plan_multiplicity(plan: dict[str, Any]) -> str:
    """The plan's correction family; plans frozen without the key corrected per metric."""
    return plan.get("multiplicity") or "per_metric"


def plan_excludes_truncated(plan: dict[str, Any]) -> bool:
    """The plan's pre-registered truncation rule (default: excluded)."""
    return bool((plan.get("exclusions") or {}).get("truncated", True))


def min_attainable_p(n_pairs: int, alternative: str = "two-sided") -> float:
    """The smallest p an exact sign or Wilcoxon signed-rank test can give with ``n_pairs`` non-zero pairs.

    Both reach it only when every pair moves the same way: 2^-n one-sided, 2^(1-n) two-sided.
    """
    if n_pairs <= 0:
        return 1.0
    return min(1.0, (1 if alternative in ("greater", "less") else 2) / 2**n_pairs)


def significance_check(
    n_apps: int,
    n_comparisons: int,
    plan: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Whether the design can reach significance at all; None when it can.

    The tests pair conditions on the app, so the number of apps bounds the
    smallest p. Holm (and Bonferroni) multiply the smallest p by the number of
    tests in the family; BH and no correction leave it as is. With the plan's
    ``multiplicity = "all"`` the family is every declared metric's
    comparisons. Returns ``{n_apps, min_p, adjusted_min_p, alpha, family_size,
    apps_needed}`` when no outcome could be significant.
    """
    if n_comparisons < 1:
        return None
    plan = plan or {}
    alpha = float(plan.get("alpha", 0.05))
    correction = plan.get("correction", "holm")
    directions = {q.get("direction") for q in plan.get("research_questions") or []}
    alternative = directions.pop() if len(directions) == 1 else "two-sided"
    family = n_comparisons
    if plan and plan_multiplicity(plan) == "all":
        family *= max(1, len(declared_metrics(plan)))
    factor = family if correction == "holm" else 1

    def adjusted(n: int) -> float:
        return min(1.0, factor * min_attainable_p(n, alternative))

    if adjusted(n_apps) < alpha:
        return None
    needed = n_apps
    while adjusted(needed) >= alpha and needed < 64:
        needed += 1
    return {
        "n_apps": n_apps,
        "min_p": min_attainable_p(n_apps, alternative),
        "adjusted_min_p": adjusted(n_apps),
        "alpha": alpha,
        "alternative": alternative,
        "family_size": family,
        "correction": correction,
        "apps_needed": needed,
    }
