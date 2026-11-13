"""Pre-registered analysis plans (port of v2 ``services/analysis_plan.py``; spec sections 4.6, 6.13, 7.9, 9.4).

A plan states before launch what the study will test: research questions, the primary metric, alpha,
correction and multiplicity family, ranking criteria, exclusions, and (v3) the estimand, random effects,
the intention-to-treat default, the sensitivity analyses, a group-sequential design and, for slot studies,
the pattern analysis. A confirmatory launch locks it under ``analysis_plan_hash = H(normalized plan)`` and it
enters ``prereg_hash`` (``identity.prereg_hash``); it never changes afterwards (clone the study instead).

Changes from v2, all deliberate:

* **ITT is the default** (D14): ``itt: true`` and ``exclusions.truncated: false``; excluding truncated runs
  is the listed sensitivity analysis ``exclude_truncated`` (``sensitivity`` defaults to it). A plan may still
  pre-register ``exclusions.truncated: true``; that turns ``itt`` off.
* ``exclusions.exclusion_rate`` (default 0.10) is the per-condition share of excluded + infra cells above which
  the gate reports the ``exclusion_rate`` deviation; ``exclusions.differential_attrition`` (default 0.05)
  the gap between compared conditions (section 6.13).
* ``estimand``: ``marginal`` (default) or ``conditional_on_parent`` (slot studies with one or manually picked
  parents, section 7.2); ``random_effects`` lists the offline model's grouping terms.
* ``sequential`` (section 4.6): ``{looks: [n, ...], alpha_spending, futility?, binding: false}``.
* ``baseline``: ``{factor_key: level}`` naming the pre-registered baseline condition (``StudyCondition.is_baseline``);
  without it the results compare against the first condition, labelled post hoc.
* ``patterns`` (D15): ``{detector_set_hash, primary_features, normalizer_version, cluster_params, scope}``; a
  PatternRun is ``planned`` only when every locked field matches (:func:`pattern_run_label`).

Plans keep the v2 key names that reports read (``primary_metric``, ``secondary_metrics``,
``research_questions[{id, question, hypothesis, metric, direction}]``, ``alpha``, ``correction``,
``multiplicity``, ``ranking_criteria``, ``exclusions.truncated``, ``min_apps_per_condition``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from pydantic import ValidationError

from backend.generation.contracts.slot import ClusterParams
from backend.generation.contracts.slot import PatternScope
from backend.generation.identity import H

if TYPE_CHECKING:
    from backend.generation.models import PatternRun
    from backend.generation.models import Study

__all__ = [
    "CORRECTIONS",
    "DIRECTIONS",
    "ESTIMANDS",
    "METRICS",
    "MULTIPLICITY",
    "PLAN_VERSION",
    "SENSITIVITY",
    "PlanError",
    "analysis_plan_hash",
    "declared_metrics",
    "min_attainable_p",
    "normalize_analysis_plan",
    "pattern_run_label",
    "plan_excludes_truncated",
    "plan_multiplicity",
    "significance_check",
]

PLAN_VERSION: Final = 2  # v2 plans were version 1 (truncated runs excluded by default)

# Outcomes a plan may name: per-unit values (a unit's repeats averaged), then the mean over a condition's units.
METRICS: Final[dict[str, str]] = {
    "deploy_rate": "Share of evaluated samples that build and answer their health check",
    "pass_rate": "Mean agent pass rate (0..1) over evaluated samples, intention-to-treat",
    "functional_passed": "Share of evaluated samples passing every functional check",
    "stated_pass_rate": "Pass rate on checks the prose variant states",
    "unstated_pass_rate": "Pass rate on checks the prose variant leaves unstated",
    "probe_exploit_rate": "Share of tested probes that were exploited",
    "unique_issues_per_app": "Deduplicated static issues of every kind per analysed sample",
    "issues_per_kloc": "Deduplicated code issues per 1,000 model-scope non-test source lines",
    "security_per_app": "Deduplicated security issues in model-scope non-test code per analysed sample",
    "cost_per_app": "Generation cost per sample (USD)",
    "pattern_prevalence": "Prevalence of the primary pattern features (slot studies)",
}
CORRECTIONS: Final = ("holm", "bh", "none")
DIRECTIONS: Final = ("two-sided", "greater", "less")
MULTIPLICITY: Final = ("all", "per_metric")
ESTIMANDS: Final = ("marginal", "conditional_on_parent")
RANDOM_EFFECTS: Final = ("unit", "parent", "target", "repeat", "provider")
SENSITIVITY: Final = ("exclude_truncated", "exclude_infra_retried", "majority_fingerprint", "complete_blocks_only")
ALPHA_SPENDING: Final = ("obrien_fleming", "pocock")
DEFAULT_EXCLUSION_RATE: Final = 0.10
DEFAULT_DIFFERENTIAL_ATTRITION: Final = 0.05
_MAX_QUESTIONS: Final = 20
_MAX_LOOKS: Final = 10
_MAX_FAMILY_SEARCH: Final = 64
_ALPHA_MAX: Final = 0.5


class PlanError(ValueError):
    """An analysis plan that does not validate (answered 400)."""


def _questions(raw: Any, primary: str) -> list[dict[str, Any]]:
    raw_questions = raw or []
    if not isinstance(raw_questions, list) or len(raw_questions) > _MAX_QUESTIONS:
        msg = f"research_questions must be a list of at most {_MAX_QUESTIONS}"
        raise PlanError(msg)
    questions = []
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
    return questions


def _criteria(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    from backend.statistics.services.mcdm import criteria_from_config

    try:
        criteria = [
            {"key": c.key, "label": c.label, "direction": c.direction, "weight": round(c.weight, 6)}
            for c in criteria_from_config(raw)
        ]
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"ranking_criteria: {exc}"
        raise PlanError(msg) from exc
    unknown = sorted({c["key"] for c in criteria} - set(METRICS))
    if unknown:
        msg = f"Unknown ranking criteria: {', '.join(unknown)}"
        raise PlanError(msg)
    return criteria


def _fraction(value: Any, name: str, default: float) -> float:
    number = float(default if value is None else value)
    if not 0 <= number <= 1:
        msg = f"{name} must be between 0 and 1"
        raise PlanError(msg)
    return number


def _sequential(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    if not isinstance(raw, dict):
        msg = "sequential must be an object"
        raise PlanError(msg)
    looks = raw.get("looks") or []
    if (
        not isinstance(looks, list)
        or not 1 <= len(looks) <= _MAX_LOOKS
        or any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in looks)
        or looks != sorted(set(looks))
    ):
        msg = f"sequential.looks must be 1..{_MAX_LOOKS} increasing positive sample sizes"
        raise PlanError(msg)
    spending = raw.get("alpha_spending", "obrien_fleming")
    if spending not in ALPHA_SPENDING:
        msg = f"sequential.alpha_spending must be one of {', '.join(ALPHA_SPENDING)}"
        raise PlanError(msg)
    futility = raw.get("futility")
    if futility is not None:
        if not isinstance(futility, dict) or futility.get("type") != "conditional_power":
            msg = "sequential.futility must be {type: conditional_power, threshold}"
            raise PlanError(msg)
        futility = {"type": "conditional_power", "threshold": _fraction(futility.get("threshold"), "threshold", 0.1)}
    if raw.get("binding"):
        msg = "sequential.binding must be false (non-binding futility only)"
        raise PlanError(msg)
    return {"looks": looks, "alpha_spending": spending, "futility": futility, "binding": False}


def _patterns(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    if not isinstance(raw, dict):
        msg = "patterns must be an object"
        raise PlanError(msg)
    unknown = sorted(set(raw) - {"detector_set_hash", "detectors", "primary_features", "normalizer_version",
                                 "cluster_params", "scope"})  # fmt: skip
    if unknown:
        msg = f"patterns: unknown key(s) {unknown}"
        raise PlanError(msg)
    try:
        cluster = ClusterParams.model_validate(raw.get("cluster_params") or {}).model_dump(mode="json")
        scope = PatternScope.model_validate(raw.get("scope") or {}).model_dump(mode="json")
    except ValidationError as exc:
        msg = f"patterns: {exc.errors()[0].get('msg')}"
        raise PlanError(msg) from exc
    normalizer = raw.get("normalizer_version", 1)
    if not isinstance(normalizer, int) or isinstance(normalizer, bool) or normalizer < 1:
        msg = "patterns.normalizer_version must be a positive integer"
        raise PlanError(msg)
    features = raw.get("primary_features") or []
    if not isinstance(features, list) or not all(isinstance(f, str) and f for f in features):
        msg = "patterns.primary_features must be a list of feature keys"
        raise PlanError(msg)
    detectors = raw.get("detectors")
    if detectors is not None and (not isinstance(detectors, list) or not all(isinstance(d, str) for d in detectors)):
        msg = "patterns.detectors must be a list of pinned detector refs"
        raise PlanError(msg)
    if not raw.get("detector_set_hash") and detectors is None:
        msg = "patterns must fix the detector set (detector_set_hash or detectors)"
        raise PlanError(msg)
    return {
        "detector_set_hash": str(raw.get("detector_set_hash") or ""),
        "detectors": sorted(detectors) if detectors is not None else None,
        "primary_features": sorted(set(features)),
        "normalizer_version": normalizer,
        "cluster_params": cluster,
        "scope": scope,
    }


def normalize_analysis_plan(plan: dict[str, Any] | None, *, kind: str = "exploratory") -> dict[str, Any]:
    """Validated, canonical plan (``{}`` for no plan); confirmatory studies require one. Raises :class:`PlanError`."""
    if not plan:
        if kind == "confirmatory":
            msg = "a confirmatory study needs an analysis plan"
            raise PlanError(msg)
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
    if not 0 < alpha < _ALPHA_MAX:
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
    estimand = plan.get("estimand", "marginal")
    if estimand not in ESTIMANDS:
        msg = f"estimand must be one of {', '.join(ESTIMANDS)}"
        raise PlanError(msg)
    random_effects = plan.get("random_effects") or []
    if not isinstance(random_effects, list) or set(random_effects) - set(RANDOM_EFFECTS):
        msg = f"random_effects must be a subset of {', '.join(RANDOM_EFFECTS)}"
        raise PlanError(msg)
    exclusions = plan.get("exclusions") or {}
    exclude_truncated = bool(exclusions.get("truncated", False))
    itt = bool(plan.get("itt", not exclude_truncated))
    if itt and exclude_truncated:
        msg = (
            "itt: true conflicts with exclusions.truncated: true (excluding truncated runs is a sensitivity analysis)"
        )
        raise PlanError(msg)
    sensitivity = plan.get("sensitivity")
    if sensitivity is None:
        sensitivity = ["exclude_truncated"] if itt else []
    if not isinstance(sensitivity, list) or set(sensitivity) - set(SENSITIVITY):
        msg = f"sensitivity must be a subset of {', '.join(SENSITIVITY)}"
        raise PlanError(msg)
    baseline = plan.get("baseline")
    if baseline is not None and (
        not isinstance(baseline, dict)
        or not baseline
        or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in baseline.items())
    ):
        msg = "baseline must map factor keys to levels (the pre-registered baseline condition)"
        raise PlanError(msg)
    return {
        "version": PLAN_VERSION,
        "baseline": dict(sorted(baseline.items())) if baseline else None,
        "research_questions": _questions(plan.get("research_questions"), primary),
        "primary_metric": primary,
        "secondary_metrics": list(dict.fromkeys(secondary)),
        "alpha": alpha,
        "correction": correction,
        "multiplicity": multiplicity,
        "ranking_criteria": _criteria(plan.get("ranking_criteria")),
        "exclusions": {
            "truncated": exclude_truncated,
            "infra_errors": True,  # an infra error says nothing about the sample
            "exclusion_rate": _fraction(exclusions.get("exclusion_rate"), "exclusion_rate", DEFAULT_EXCLUSION_RATE),
            "differential_attrition": _fraction(
                exclusions.get("differential_attrition"),
                "differential_attrition",
                DEFAULT_DIFFERENTIAL_ATTRITION,
            ),
        },
        "itt": itt,
        "sensitivity": sorted(set(sensitivity)),
        "estimand": estimand,
        "random_effects": sorted(set(random_effects)),
        "sequential": _sequential(plan.get("sequential")),
        "patterns": _patterns(plan.get("patterns")),
        "min_apps_per_condition": int(plan.get("min_apps_per_condition", 0) or 0),
        "notes": str(plan.get("notes") or "")[:4000],
    }


def analysis_plan_hash(plan: dict[str, Any]) -> str:
    """``H(normalized plan)``; ``""`` for no plan."""
    return H(plan) if plan else ""


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
    """The plan's correction family (``all`` unless the plan says ``per_metric``)."""
    return plan.get("multiplicity") or "all"


def plan_excludes_truncated(plan: dict[str, Any]) -> bool:
    """The plan's pre-registered truncation rule (default: truncated runs are kept, ITT)."""
    return bool((plan.get("exclusions") or {}).get("truncated", False))


def min_attainable_p(n_pairs: int, alternative: str = "two-sided") -> float:
    """The smallest p an exact sign or Wilcoxon signed-rank test can give with ``n_pairs`` non-zero pairs."""
    if n_pairs <= 0:
        return 1.0
    return min(1.0, (1 if alternative in ("greater", "less") else 2) / 2**n_pairs)


def significance_check(n_units: int, n_comparisons: int, plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Whether the design can reach significance at all; ``None`` when it can (``significance_unreachable``).

    The paired tests pair conditions on the unit, so the number of paired units bounds the smallest p. Holm
    multiplies it by the family size (``multiplicity = all``: every declared metric's comparisons).
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

    if adjusted(n_units) < alpha:
        return None
    needed = n_units
    while adjusted(needed) >= alpha and needed < _MAX_FAMILY_SEARCH:
        needed += 1
    return {
        "n_units": n_units,
        "min_p": min_attainable_p(n_units, alternative),
        "adjusted_min_p": adjusted(n_units),
        "alpha": alpha,
        "alternative": alternative,
        "family_size": family,
        "correction": correction,
        "units_needed": needed,
    }


def pattern_run_label(study: Study, pattern_run: PatternRun) -> str:
    """``planned`` when ``pattern_run`` matches every field the locked plan's ``patterns`` fixed, else ``exploratory``.

    Only a locked (confirmatory) study has a planned pattern analysis (D15); WP-13 records the label.
    """
    plan = study.analysis_plan if isinstance(study.analysis_plan, dict) else {}
    patterns = plan.get("patterns") if study.locked_at is not None else None
    if not patterns:
        return "exploratory"
    checks = [
        not patterns.get("detector_set_hash") or patterns["detector_set_hash"] == pattern_run.detector_set_hash,
        patterns.get("detectors") is None or sorted(pattern_run.detectors or []) == patterns["detectors"],
        patterns.get("normalizer_version") == pattern_run.normalizer_version,
        ClusterParams.model_validate(pattern_run.cluster_params or {}).model_dump(mode="json")
        == patterns.get("cluster_params"),
        PatternScope.model_validate(pattern_run.scope or {}).model_dump(mode="json") == patterns.get("scope"),
    ]
    return "planned" if all(checks) else "exploratory"
