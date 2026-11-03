"""Study results (port of v2 ``experiment_results``): leaderboard, paired comparisons, gate, ITT, attrition, EMMs.

Spec sections 6.13, 6.14, 9.4, 13.4 and Appendix A #11-#17. The single implementation behind
``GET /studies/{id}/results/`` and the study conditions report.

* **Sample.** The current run of every **main-stage** cell of the active plan (``runs.queries.current_runs``:
  the first usable attempt, never the latest; ``beyond_plan`` runs never count). Pilot cells are excluded.
* **Intention-to-treat (default).** A usable run is data: ``tested`` with a measured evaluation contributes
  its pass rate; ``build_fail`` / ``start_fail`` / ``output_invalid`` contribute 0 when the evaluation level
  measures functional checks; truncated runs (budget, wall clock, idle, max turns) are **included**.
  ``sensitivity=exclude_truncated`` repeats the analysis without them (``paired.sensitivity`` names the
  conditions whose direction or significance changes). A tested run whose evaluation was not measured is
  ``not_measured`` (never 0); ``infra_error`` and ``excluded`` runs are attrition (reported per condition).
* **Complete blocks.** Paired and by-condition figures use the cells of complete blocks only
  (``budget.report.complete_blocks``): a budget stop or an unfinished block never compares one condition's
  finished cells with another's missing ones (``n_missing.incomplete_block``); the gate embeds
  ``budget.report.budget_deviations`` (stop reason, exclusions, ``stopped_early`` groups).
* **Unit of analysis: the unit** (task version or slot target). Repeats of one (condition, unit) collapse to
  their mean; a condition's score is the mean of its unit means. Common cells (default on) keep only the
  ``(unit, repeat)`` cells every condition with data has, so an exclusion never selects on one condition.
* **Pairing.** A comparison with the baseline is ``paired_crn`` when both conditions forward the CRN seed
  (``manifest.sampling.seed_forwarded``): deltas are paired by ``(unit, repeat)`` then averaged per unit.
  Otherwise ``independent_repeats``: unit mean differences. The method is recorded per row. Tests run on the
  unit-level deltas: exact sign test and Wilcoxon signed-rank, plus the sign-flip permutation test, which is
  the primary test with fewer than 20 units (``few_clusters``; no cluster bootstrap CI then).
* **Gate.** p-values are confirmatory only for a locked confirmatory study whose plan declares the metric,
  with its pre-registered baseline and no deviation: ``exclusions_differ`` (truncation rule other than the
  plan's), ``fingerprint_filter`` (majority filter dropped cells), ``stopped_early`` (main cells excluded by
  budget, a rule or cancellation, or the study stopped), ``multiplicity_family`` (the plan corrects across
  several metrics), ``exclusion_rate`` (excluded + infra above the plan's threshold in a condition),
  ``differential_attrition`` (compared conditions differ by more than the plan's threshold). ``interim``
  withholds every p-value until the study is completed, stopped or cancelled. ``significance_unreachable``
  says when the planned units cannot reach significance under the plan's correction.
* **Flags.** ``mixed_config_fingerprint`` (more than one fingerprint inside a (condition, unit)),
  ``mixed_eval_suite`` (compared runs evaluated under different suites), ``few_clusters``.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.utils import timezone

from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import make_issue
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunStatus
from backend.generation.study import analysis_plan as plans
from backend.generation.study import stats

if TYPE_CHECKING:
    from backend.generation.models import Study

logger = logging.getLogger(__name__)

__all__ = ["DEVIATIONS", "METRICS", "ResultFilters", "compute", "study_results"]

METRICS: Final = ("pass_rate", "stated_pass_rate", "unstated_pass_rate", "common_pass_rate")
_EVAL_KEYS: Final = {
    "pass_rate": "agent_pass_rate",
    "stated_pass_rate": "stated_pass_rate",
    "unstated_pass_rate": "unstated_pass_rate",
    "common_pass_rate": "common_pass_rate",
}
SENSITIVITIES: Final = (None, "exclude_truncated")
_TERMINAL: Final = ("completed", "cancelled", "stopped")
_MODEL_FAILURES: Final = (Outcome.BUILD_FAIL, Outcome.START_FAIL, Outcome.OUTPUT_INVALID)
_STOP_REASONS: Final = ("budget_stop", "stopped_by_rule", "cancelled")
_FUNCTIONAL_LEVELS: Final = ("functional", "security", "full")
NOTE_INTERIM: Final = "Interim: the study has not ended; p-values are withheld until it completes"
NOTE_POST_HOC: Final = "Baseline chosen post hoc; p-values exploratory"
NOTE_NO_PREREGISTERED: Final = "No pre-registered baseline; the first condition is used (exploratory)"
NOTE_FEW_UNITS: Final = "Fewer than 6 units: treat p-values as descriptive"
NOTE_FEW_CLUSTERS: Final = "Fewer than 20 units: unit-level permutation tests, no cluster bootstrap CI"
NOTE_ONE_SIDED: Final = "Common cells off: exclusions may select on the outcome"
NOTE_NO_USABLE_PREFIX: Final = "Left out of the common cells (no graded cells yet): "
NOTE_ITT: Final = "Intention-to-treat: truncated and model-failed runs are in the sample (failed runs score 0)"
NOTE_CEILING: Final = "Every graded cell scored {value:g}: the outcome does not discriminate between conditions"
NOTE_DEVIATIONS_PREFIX: Final = "Not the pre-registered analysis, p-values exploratory: "
NOTE_SENSITIVITY_PREFIX: Final = "Sensitive to the truncation rule (direction or significance changes when truncated "
NOTE_UNREACHABLE: Final = "Significance is unreachable with {n} units under the plan's correction (needs {need})"
DEVIATIONS: Final = {
    "exclusions_differ": "truncation rule differs from the plan's",
    "common_cells_off": "common cells off",
    "fingerprint_filter": "fingerprint filter dropped cells",
    "stopped_early": "planned cells were stopped (budget, a stop rule or cancellation)",
    "multiplicity_family": "the plan corrects across several metrics (see the study report)",
    "exclusion_rate": "excluded + infra cells exceed the plan's exclusion rate in a condition",
    "differential_attrition": "attrition differs between compared conditions beyond the plan's threshold",
}
MIN_UNITS_FOR_INFERENCE: Final = 6


@dataclass(frozen=True)
class ResultFilters:
    metric: str = "pass_rate"
    sensitivity: str | None = None  # None (ITT, or the plan's rule) | "exclude_truncated"
    fingerprint: str = "majority"  # "majority" | "all"
    common_cells: bool = True


@dataclass
class _Cell:
    cell_id: int
    condition_id: int
    unit_id: int
    repeat: int
    block: int = 0
    run_id: str | None = None
    code: str = "p"  # c passed, f graded <1 / model failure, u not measured, x infra, k excluded, p pending
    value: float | None = None
    outcome: str = ""
    usable: bool | None = None
    truncated: bool = False
    termination: str | None = None
    fingerprint: str = ""
    suite: str = ""
    cost: float = 0.0
    retry_cost: float = 0.0
    tokens: float = 0.0
    seconds: float | None = None
    excluded: str = ""  # "" | "fingerprint"
    stopped: bool = False
    providers: tuple[str, ...] = ()
    eval_metrics: dict[str, Any] = field(default_factory=dict)


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _functional_measured(design: dict[str, Any]) -> bool:
    evaluation = design.get("evaluation") or {}
    override = (evaluation.get("overrides") or {}).get("functional")
    if override is not None:
        return bool(override)
    return evaluation.get("eval_level", "security") in _FUNCTIONAL_LEVELS


def _load(study: Study, metric: str) -> tuple[list[_Cell], dict[int, bool]]:
    """Main-stage cells with their current run; plus ``seed_forwarded`` per condition id."""
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunAttempt
    from backend.generation.models import RunEvaluation
    from backend.generation.models import RunManifest
    from backend.generation.runs.queries import cell_current_ids
    from backend.generation.runs.queries import cells_for_stage

    rows = list(
        cell_current_ids(cells_for_stage(study, "main")).values(
            "pk",
            "condition_id",
            "unit_id",
            "repeat_index",
            "block_index",
            "state",
            "exclusion_reason",
            "current_id",
        ),
    )
    ids = [row["current_id"] for row in rows if row["current_id"]]
    runs = {
        run["pk"]: run
        for run in GenerationRun.objects.filter(pk__in=ids).values(
            "pk",
            "status",
            "outcome",
            "outcome_reason",
            "usable",
            "validity",
            "metrics",
            "config_fingerprint",
            "execution",
            "duration_seconds",
            "attempt",
        )
    }
    evaluations: dict[Any, dict[str, Any]] = {}
    for row in (
        RunEvaluation.objects.filter(run_id__in=ids, retest_of__isnull=True)
        .order_by("run_id", "-created_at", "-pk")
        .values("run_id", "status", "result", "eval_suite__slug", "eval_suite__version")
    ):
        evaluations.setdefault(row["run_id"], row)
    providers: dict[Any, set[str]] = defaultdict(set)
    for run_id, served in RunAttempt.objects.filter(run_id__in=ids).values_list("run_id", "served"):
        providers[run_id] |= {p for p in ((served or {}).get("providers") or {}) if p != "unknown"}
    seed_forwarded: dict[int, bool] = {}
    by_condition_run = {row["condition_id"]: row["current_id"] for row in rows if row["current_id"]}
    manifests = dict(
        RunManifest.objects.filter(run_id__in=list(by_condition_run.values())).values_list("run_id", "manifest"),
    )
    for condition_id, run_id in by_condition_run.items():
        sampling = (manifests.get(run_id) or {}).get("sampling") or {}
        seed_forwarded[condition_id] = bool(sampling.get("seed_forwarded"))
    functional = _functional_measured(study.design or {})
    key = _EVAL_KEYS[metric]
    cells = []
    for row in rows:
        cell = _Cell(row["pk"], row["condition_id"], row["unit_id"], row["repeat_index"], row["block_index"])
        cell.stopped = row["exclusion_reason"] in _STOP_REASONS
        run = runs.get(row["current_id"])
        if run is None:
            cell.code = "k" if row["state"] == "excluded" else "p"
            cells.append(cell)
            continue
        cell.run_id = str(run["pk"])
        cell.outcome, cell.usable = run["outcome"], run["usable"]
        validity = run["validity"] if isinstance(run["validity"], dict) else {}
        cell.truncated = bool(validity.get("truncated"))
        cell.termination = validity.get("termination")
        cell.fingerprint = run["config_fingerprint"] or ""
        metrics = run["metrics"] if isinstance(run["metrics"], dict) else {}
        execution = run["execution"] if isinstance(run["execution"], dict) else {}
        cell.retry_cost = _float(execution.get("prior_attempts_cost_usd")) or 0.0
        cell.cost = (_float(metrics.get("cost_usd")) or 0.0) + cell.retry_cost
        cell.tokens = (_float(metrics.get("total_tokens")) or 0.0) + (
            _float(execution.get("prior_attempts_total_tokens")) or 0.0
        )
        cell.seconds = _float(metrics.get("wall_s")) or _float(run["duration_seconds"])
        cell.providers = tuple(sorted(providers.get(run["pk"], ())))
        evaluation = evaluations.get(run["pk"])
        if evaluation is not None:
            cell.suite = f"{evaluation['eval_suite__slug']}@{evaluation['eval_suite__version']}"
            result = evaluation["result"] if isinstance(evaluation["result"], dict) else {}
            if evaluation["status"] == "measured" and result.get("measured"):
                cell.eval_metrics = result.get("metrics") or {}
        if run["status"] in (RunStatus.PENDING, RunStatus.RUNNING):
            cell.code = "p"
        elif run["usable"] and run["outcome"] == Outcome.TESTED:
            summary = metrics.get("eval") if isinstance(metrics.get("eval"), dict) else {}
            value = _float(cell.eval_metrics.get(key))
            if value is None and key == "agent_pass_rate" and summary.get("measured"):
                value = _float(summary.get("agent_pass_rate"))
            cell.value = value
            cell.code = "u" if value is None else ("c" if value >= 1.0 else "f")
        elif run["usable"] and run["outcome"] in _MODEL_FAILURES:
            cell.value = 0.0 if functional else None  # ITT: a model failure scores 0 when functional is measured
            cell.code = "f" if functional else "u"
        elif run["outcome"] == Outcome.EXCLUDED:
            cell.code = "k"
            cell.stopped = cell.stopped or run["outcome_reason"] in _STOP_REASONS
        else:
            cell.code = "x"
        cells.append(cell)
    return cells, seed_forwarded


def _baseline(study: Study, conditions: list[Any], requested: Any) -> tuple[Any, str, list[str]]:
    by_id = {c.pk: c for c in conditions}
    preregistered = next((c for c in conditions if c.is_baseline), None)
    if requested is not None:
        try:
            requested_id = int(requested)
        except (TypeError, ValueError) as exc:
            msg = f"baseline {requested!r} is not a condition id"
            raise ValueError(msg) from exc
        if requested_id not in by_id:
            msg = f"Condition {requested} is not part of this study"
            raise ValueError(msg)
        if preregistered is not None and requested_id == preregistered.pk:
            return preregistered, "preregistered", []
        return by_id[requested_id], "post_hoc", [NOTE_POST_HOC]
    if preregistered is not None:
        return preregistered, "preregistered", []
    first = conditions[0] if conditions else None
    return first, "post_hoc", [NOTE_NO_PREREGISTERED] if first is not None else []


def _plan(study: Study) -> dict[str, Any]:
    plan = study.analysis_plan if isinstance(study.analysis_plan, dict) else {}
    return plan if plan and study.locked_at is not None else {}


def _test_plan(study: Study, plan: dict[str, Any], metric: str, baseline_source: str, deviations: list[str]):
    if (
        plan
        and study.kind == "confirmatory"
        and metric in plans.declared_metrics(plan)
        and baseline_source == "preregistered"
        and not deviations
    ):
        directions = {q.get("direction") for q in plan.get("research_questions") or [] if q.get("metric") == metric}
        alternative = directions.pop() if len(directions) == 1 else "two-sided"
        return {
            "status": "confirmatory",
            "correction": plan.get("correction", "holm"),
            "alpha": float(plan.get("alpha", 0.05)),
            "alternative": alternative if alternative in plans.DIRECTIONS else "two-sided",
            "deviations": [],
        }
    return {
        "status": "exploratory",
        "correction": "holm",
        "alpha": 0.05,
        "alternative": "two-sided",
        "deviations": deviations if plan else [],
    }


def _adjust(p_values: list[float | None], correction: str) -> list[float | None]:
    if correction == "bh":
        from backend.statistics.services.inference import benjamini_hochberg

        return benjamini_hochberg(p_values, digits=None)
    if correction == "none":
        return list(p_values)
    return stats.holm(p_values)


def _mixed_fingerprints(cells: list[_Cell], conditions: list[Any], majority: bool) -> tuple[dict, int]:
    """Per condition ``{count, units, minority_cells, majority_share}`` (None when uniform) and cells dropped."""
    dropped = 0
    out: dict[int, dict[str, Any] | None] = {}
    by_pair: dict[tuple[int, int], list[_Cell]] = defaultdict(list)
    for cell in cells:
        if cell.fingerprint:
            by_pair[(cell.condition_id, cell.unit_id)].append(cell)
    for condition in conditions:
        drifted = minority = total = 0
        most = 1
        for (cid, _), members in by_pair.items():
            if cid != condition.pk:
                continue
            prints = Counter(c.fingerprint for c in members)
            total += len(members)
            if len(prints) <= 1:
                continue
            drifted += 1
            most = max(most, len(prints))
            top_count = max(prints.values())
            winner = min(fp for fp, n in prints.items() if n == top_count)  # ties: the lowest fingerprint
            minority += len(members) - top_count
            if majority:
                for cell in members:
                    if cell.fingerprint != winner:
                        cell.excluded = "fingerprint"
                        dropped += 1
        out[condition.pk] = (
            {
                "count": most,
                "units": drifted,
                "minority_cells": minority,
                "majority_share": round(1 - minority / total, 4),
            }
            if drifted
            else None
        )
    return out, dropped


class _Selection:
    """Which cells are kept under one truncation rule (common cells across conditions)."""

    def __init__(
        self,
        cells: list[_Cell],
        condition_ids: list[int],
        *,
        exclude_truncated: bool,
        common: bool,
        blocks: set[int] | None = None,
    ):
        self.exclude_truncated = exclude_truncated
        self.common_on = common
        self.blocks = blocks
        self.usable_keys = {
            cid: {(c.unit_id, c.repeat) for c in cells if c.condition_id == cid and self.usable(c)}
            for cid in condition_ids
        }
        self.empty = [cid for cid in condition_ids if not self.usable_keys[cid]]
        populated = [keys for keys in self.usable_keys.values() if keys]
        self.common = set.intersection(*populated) if populated else set()

    def exclusion(self, cell: _Cell) -> str:
        if cell.excluded or cell.value is None:
            return cell.excluded
        if self.blocks is not None and cell.block not in self.blocks:
            return "incomplete_block"
        if self.exclude_truncated and cell.truncated:
            return "truncated"
        return ""

    def usable(self, cell: _Cell) -> bool:
        return cell.value is not None and not self.exclusion(cell)

    def kept(self, cell: _Cell) -> bool:
        return self.usable(cell) and (not self.common_on or (cell.unit_id, cell.repeat) in self.common)

    def values(self, cells: list[_Cell]) -> dict[int, dict[int, dict[int, float]]]:
        """condition -> unit -> repeat -> value of the kept cells."""
        out: dict[int, dict[int, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
        for cell in cells:
            if self.kept(cell):
                out[cell.condition_id][cell.unit_id][cell.repeat] = cell.value  # type: ignore[assignment]
        return out


def _unit_means(by_unit: dict[int, dict[int, float]]) -> dict[int, float]:
    return {unit: sum(v.values()) / len(v) for unit, v in by_unit.items() if v}


def _pair_deltas(treated: dict[int, dict[int, float]], base: dict[int, dict[int, float]], crn: bool):
    """(unit -> delta, unit -> (treated values, base values)) for one comparison."""
    deltas: dict[int, float] = {}
    pools: dict[int, tuple[list[float], list[float]]] = {}
    for unit in sorted(set(treated) & set(base)):
        t, b = treated[unit], base[unit]
        if crn:
            repeats = sorted(set(t) & set(b))
            if not repeats:
                continue
            deltas[unit] = sum(t[r] - b[r] for r in repeats) / len(repeats)
            pools[unit] = ([t[r] for r in repeats], [b[r] for r in repeats])
        elif t and b:
            deltas[unit] = sum(t.values()) / len(t) - sum(b.values()) / len(b)
            pools[unit] = (list(t.values()), list(b.values()))
    return deltas, pools


def _paired(
    conditions: list[Any],
    base: Any,
    values: dict[int, dict[int, dict[int, float]]],
    seed_forwarded: dict[int, bool],
    test_plan: dict[str, Any],
    *,
    interim: bool,
    seed: int,
    with_ci: bool,
) -> list[dict[str, Any]]:
    if base is None:
        return []
    rows = []
    for condition in conditions:
        if condition.pk == base.pk:
            continue
        crn = seed_forwarded.get(condition.pk, False) and seed_forwarded.get(base.pk, False)
        deltas, pools = _pair_deltas(values.get(condition.pk, {}), values.get(base.pk, {}), crn)
        series = list(deltas.values())
        wins = sum(1 for d in series if d > 0)
        losses = sum(1 for d in series if d < 0)
        few = len(series) < stats.FEW_CLUSTERS
        permutation = stats.sign_flip_p(series, seed=seed ^ condition.pk, alternative=test_plan["alternative"])
        wilcoxon = stats.wilcoxon_p(series, test_plan["alternative"])
        row = {
            "condition_id": condition.pk,
            "method": "paired_crn" if crn else "independent_repeats",
            "primary_test": "permutation" if few else "wilcoxon",
            "untested_reason": "no_pairs" if not series else ("all_ties" if wins + losses == 0 else None),
            "n_pairs": len(series),
            "mean_delta": round(stats.mean(series), 4) if series else None,
            "wins": wins,
            "losses": losses,
            "ties": len(series) - wins - losses,
            "sign_test_p": stats.sign_test_p(wins, losses),
            "wilcoxon_p": wilcoxon,
            "permutation_p": permutation,
            "p_value": permutation if few else wilcoxon,
            "ci95": None,
        }
        if with_ci and not few:
            row["ci95"] = stats.paired_bootstrap_ci(pools, seed ^ (condition.pk << 16) ^ base.pk)
        rows.append(row)
    raw = [row["p_value"] for row in rows]
    adjusted = _adjust(raw, test_plan["correction"])
    for row, p_holm, p_adj in zip(rows, stats.holm(raw), adjusted, strict=True):
        row["p_holm"] = p_holm
        row["p_adjusted"] = p_adj
        row["significant"] = (p_adj is not None and p_adj < test_plan["alpha"]) if row["n_pairs"] else None
        for key in ("sign_test_p", "wilcoxon_p", "permutation_p", "p_value", "p_holm", "p_adjusted"):
            if interim:
                row[key] = None
            elif row[key] is not None:
                row[key] = round(row[key], 6)
        if interim:
            row["significant"] = None
    return rows


def _attrition_flags(attrition: list[dict[str, Any]], plan: dict[str, Any], base_id: Any) -> dict[str, Any]:
    exclusions = plan.get("exclusions") or {}
    threshold = float(exclusions.get("exclusion_rate", plans.DEFAULT_EXCLUSION_RATE))
    gap = float(exclusions.get("differential_attrition", plans.DEFAULT_DIFFERENTIAL_ATTRITION))
    rates = {row["condition_id"]: row["attrition_rate"] for row in attrition if row["attrition_rate"] is not None}
    over = sorted(cid for cid, rate in rates.items() if rate > threshold)
    base_rate = rates.get(base_id)
    differential = sorted(
        cid for cid, rate in rates.items() if base_rate is not None and cid != base_id and abs(rate - base_rate) > gap
    )
    if base_rate is None and rates and max(rates.values()) - min(rates.values()) > gap:
        differential = sorted(rates)
    return {"threshold": threshold, "gap": gap, "rates": rates, "over": over, "differential": differential}


def _by_factor(study: Study, conditions: list[Any], means: dict[int, float | None], unbalanced: bool) -> dict:
    design = study.design or {}
    varying = [f["key"] for f in design.get("factors") or [] if f.get("role") != "fixed" and len(f["levels"]) > 1]
    out: dict[str, Any] = {"unbalanced": unbalanced, "method": "emm_of_condition_means", "factors": {}}
    for key in varying:
        levels: dict[str, list[float]] = defaultdict(list)
        n_conditions: Counter[str] = Counter()
        for condition in conditions:
            level = (condition.levels or {}).get(key)
            if level is None:
                continue
            n_conditions[level] += 1
            if means.get(condition.pk) is not None:
                levels[level].append(means[condition.pk])  # type: ignore[arg-type]
        out["factors"][key] = {
            level.split("#", 1)[0]: {
                "level": level,
                "emm": round(sum(levels[level]) / len(levels[level]), 4) if levels[level] else None,
                "n_conditions": n_conditions[level],
                "n_measured_conditions": len(levels[level]),
            }
            for level in n_conditions
        }
        if any(len(levels[level]) != n_conditions[level] for level in n_conditions):
            out["unbalanced"] = True
    return out


def _prose_variant_slices(conditions: list[Any], cells: list[_Cell], kept: Any) -> dict[str, Any]:
    by_variant: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"stated": [], "unstated": []})
    variant_of = {c.pk: (c.levels or {}).get("prose_variant") for c in conditions}
    for cell in cells:
        variant = variant_of.get(cell.condition_id)
        if variant is None or not kept(cell):
            continue
        for key, name in (("stated_pass_rate", "stated"), ("unstated_pass_rate", "unstated")):
            value = _float(cell.eval_metrics.get(key))
            if value is not None:
                by_variant[variant][name].append(value)
    return {
        variant: {name: (round(sum(values) / len(values), 4) if values else None) for name, values in parts.items()}
        | {"n": max(len(parts["stated"]), len(parts["unstated"]))}
        for variant, parts in sorted(by_variant.items())
    }


def _complete_blocks(study: Study) -> set[int] | None:
    """Main-stage block indices whose every cell finished (``budget.report.complete_blocks``, WP-11), or None.

    Paired and by-condition results use complete blocks only (4.5), so a budget stop or an unfinished block
    never compares a condition's finished cells with another's missing ones.
    """
    try:
        from backend.generation.budget.report import complete_blocks

        return {index for stage, index in complete_blocks(study, stage="main") if stage == "main"}
    except (ImportError, NotImplementedError):
        return None


def _budget_deviations(study: Study) -> dict[str, Any] | None:
    try:
        from backend.generation.budget.report import budget_deviations

        return budget_deviations(study)
    except (ImportError, NotImplementedError):
        return None


def _retest(study: Study) -> dict[str, Any]:
    try:
        from backend.generation.evaluation.retest import retest_agreement

        return retest_agreement(study)
    except NotImplementedError:
        return {"n": 0, "check_agreement": None, "run_agreement": None, "kappa": None, "status": "not_measured"}


def compute(study: Study, baseline: Any = None, filters: ResultFilters | None = None) -> dict[str, Any]:
    """The results payload of ``study`` (see the module docstring). ``ValueError`` for a bad metric/baseline."""
    from backend.generation.models import StudyCondition
    from backend.generation.runs.queries import attrition as attrition_table

    filters = filters or ResultFilters()
    if filters.metric not in METRICS:
        msg = f"Unknown metric {filters.metric!r} (supported: {', '.join(METRICS)})"
        raise ValueError(msg)
    if filters.fingerprint not in ("all", "majority"):
        msg = "fingerprint must be 'all' or 'majority'"
        raise ValueError(msg)
    if filters.sensitivity not in SENSITIVITIES:
        msg = "sensitivity must be empty or 'exclude_truncated'"
        raise ValueError(msg)
    now = timezone.now()
    kind = study.kind
    conditions = list(
        StudyCondition.objects.filter(plan_id=study.active_plan_id)
        .select_related("model", "harness_preset")
        .order_by(
            "index",
        )
        if study.active_plan_id
        else [],
    )
    base, baseline_source, notes = _baseline(study, conditions, baseline)
    interim = study.status not in _TERMINAL
    plan = _plan(study)
    plan_truncated = plans.plan_excludes_truncated(plan) if plan else False
    exclude_truncated = filters.sensitivity == "exclude_truncated" or (filters.sensitivity is None and plan_truncated)
    cells, seed_forwarded = _load(study, filters.metric)
    condition_ids = [c.pk for c in conditions]
    cells = [c for c in cells if c.condition_id in set(condition_ids)]
    mixed, dropped = _mixed_fingerprints(cells, conditions, filters.fingerprint == "majority")
    blocks = _complete_blocks(study)
    selection = _Selection(
        cells,
        condition_ids,
        exclude_truncated=exclude_truncated,
        common=filters.common_cells,
        blocks=blocks,
    )
    values = selection.values(cells)
    issues: list[Issue] = []
    if filters.common_cells and selection.empty and len(selection.empty) < len(conditions):
        labels = {c.pk: c.label for c in conditions}
        notes.append(NOTE_NO_USABLE_PREFIX + ", ".join(labels[cid] for cid in selection.empty))
    if not filters.common_cells:
        notes.append(NOTE_ONE_SIDED)
    if not exclude_truncated:
        notes.append(NOTE_ITT)
    seed = study.pk.int % (2**32)
    by_condition: dict[int, list[_Cell]] = defaultdict(list)
    for cell in cells:
        by_condition[cell.condition_id].append(cell)
    units = sorted({c.unit_id for c in cells})
    leaderboard = []
    means: dict[int, float | None] = {}
    for condition in conditions:
        mine = by_condition[condition.pk]
        unit_values = values.get(condition.pk, {})
        unit_means = _unit_means(unit_values)
        kept = [c for c in mine if selection.kept(c)]
        mean = round(stats.mean(list(unit_means.values())), 4) if unit_means else None
        means[condition.pk] = mean
        seconds = sorted(c.seconds for c in kept if c.seconds is not None)
        leaderboard.append(
            {
                "condition_id": condition.pk,
                "index": condition.index,
                "label": condition.label,
                "levels": condition.levels,
                "is_baseline": condition.is_baseline,
                "model": {"id": condition.model_id, "model_id": condition.model.model_id},
                "harness": condition.harness_preset.harness if condition.harness_preset_id else None,
                "n_units": len(unit_means),
                "repeats_per_unit": round(len(kept) / len(unit_means), 2) if unit_means else 0,
                "n_cells": len(mine),
                "n_graded": len(kept),
                "k_passed": sum(1 for c in kept if c.code == "c"),
                "n_missing": {
                    "pending": sum(1 for c in mine if c.code == "p"),
                    "infra": sum(1 for c in mine if c.code == "x"),
                    "excluded": sum(1 for c in mine if c.code == "k"),
                    "not_measured": sum(1 for c in mine if c.code == "u"),
                    "excluded_fingerprint": sum(1 for c in mine if c.excluded == "fingerprint"),
                    "excluded_truncated": sum(1 for c in mine if selection.exclusion(c) == "truncated"),
                    "incomplete_block": sum(1 for c in mine if selection.exclusion(c) == "incomplete_block"),
                    "not_common": sum(1 for c in mine if selection.usable(c) and not selection.kept(c)),
                },
                "model_failures_scored_zero": sum(1 for c in kept if c.outcome in _MODEL_FAILURES),
                "truncated_in_sample": sum(1 for c in kept if c.truncated),
                "pass_rate_mean": mean,
                "pass_rate_ci95": stats.cluster_bootstrap_ci(
                    {u: list(v.values()) for u, v in unit_values.items()},
                    seed ^ condition.pk,
                ),
                "ci_method": "cluster_bootstrap",
                "cost_usd": round(sum(c.cost for c in mine), 4),
                "retry_cost_usd": round(sum(c.retry_cost for c in mine), 4),
                "tokens_total": int(sum(c.tokens for c in mine)),
                "median_seconds": round(stats.percentile(seconds, 0.5), 1) if seconds else None,
                "terminations": dict(Counter(c.termination for c in mine if c.termination)),
                "mixed_fingerprints": mixed.get(condition.pk),
                "seed_forwarded": seed_forwarded.get(condition.pk),
            },
        )
    ranked = sorted(leaderboard, key=lambda r: (r["pass_rate_mean"] is None, -(r["pass_rate_mean"] or 0.0)))
    for position, row in enumerate(ranked, start=1):
        previous = ranked[position - 2] if position > 1 else None
        row["rank"] = (
            previous["rank"] if previous and previous["pass_rate_mean"] == row["pass_rate_mean"] else position
        )

    attrition = attrition_table(study, "main")
    flags = _attrition_flags(attrition, plan, base.pk if base is not None else None)
    budget = _budget_deviations(study)
    stopped_early = (
        study.status == "stopped" or any(c.stopped for c in cells) or bool(budget and budget.get("stopped_early"))
    )
    deviations = []
    if plan:
        if exclude_truncated != plan_truncated:
            deviations.append("exclusions_differ")
        if not filters.common_cells:
            deviations.append("common_cells_off")
        if dropped:
            deviations.append("fingerprint_filter")
        if stopped_early:
            deviations.append("stopped_early")
        if plans.plan_multiplicity(plan) == "all" and len(plans.declared_metrics(plan)) > 1:
            deviations.append("multiplicity_family")
        if flags["over"]:
            deviations.append("exclusion_rate")
        if flags["differential"]:
            deviations.append("differential_attrition")
    test_plan = _test_plan(study, plan, filters.metric, baseline_source, deviations)
    if test_plan["deviations"]:
        notes.append(NOTE_DEVIATIONS_PREFIX + "; ".join(DEVIATIONS[d] for d in test_plan["deviations"]))
    rows = _paired(conditions, base, values, seed_forwarded, test_plan, interim=interim, seed=seed, with_ci=True)

    sensitivity = None
    if base is not None and any(c.truncated and c.value is not None for c in cells):
        alt = _Selection(
            cells,
            condition_ids,
            exclude_truncated=not exclude_truncated,
            common=filters.common_cells,
            blocks=blocks,
        )
        alt_rows = _paired(
            conditions,
            base,
            alt.values(cells),
            seed_forwarded,
            test_plan,
            interim=interim,
            seed=seed,
            with_ci=False,
        )
        main = {r["condition_id"]: r for r in rows}
        changed = []
        for row in alt_rows:
            ref = main[row["condition_id"]]
            a, b = ref["mean_delta"], row["mean_delta"]
            row["direction_changed"] = a is not None and b is not None and a * b < 0
            row["significance_changed"] = (
                None
                if interim or ref["significant"] is None or row["significant"] is None
                else ref["significant"] != row["significant"]
            )
            if row["direction_changed"] or row["significance_changed"]:
                changed.append(row["condition_id"])
        sensitivity = {"exclude_truncated": not exclude_truncated, "rows": alt_rows, "changed": changed}
        if changed:
            labels = {c.pk: c.label for c in conditions}
            notes.append(
                NOTE_SENSITIVITY_PREFIX
                + ("output is kept): " if exclude_truncated else "runs are excluded): ")
                + ", ".join(labels[cid] for cid in changed),
            )

    n_units = len({c.unit_id for c in cells if selection.kept(c)})
    if n_units and n_units < stats.FEW_CLUSTERS:
        notes.append(NOTE_FEW_CLUSTERS)
        issues.append(make_issue("few_clusters", kind, message=NOTE_FEW_CLUSTERS, data={"n_units": n_units}))
    kept_values = {c.value for c in cells if selection.kept(c)}
    if len(kept_values) == 1 and sum(1 for c in cells if selection.kept(c)) >= 2:
        notes.append(NOTE_CEILING.format(value=next(iter(kept_values))))
    unreachable = plans.significance_check(n_units, len(rows), plan or study.analysis_plan or None) if rows else None
    if unreachable is not None:
        notes.append(NOTE_UNREACHABLE.format(n=n_units, need=unreachable["units_needed"]))
    if interim:
        notes.insert(0, NOTE_INTERIM)
    elif rows and n_units < MIN_UNITS_FOR_INFERENCE:
        notes.append(NOTE_FEW_UNITS)
    for condition in conditions:
        if mixed.get(condition.pk):
            issues.append(
                make_issue(
                    "mixed_config_fingerprint",
                    kind,
                    message=f"{condition.label}: more than one config_fingerprint inside a (condition, unit)",
                    data={"condition_id": condition.pk, **mixed[condition.pk]},  # type: ignore[dict-item]
                ),
            )
    suites = sorted({c.suite for c in cells if c.suite and selection.kept(c)})
    if len(suites) > 1:
        issues.append(make_issue("mixed_eval_suite", kind, message=f"evaluated under {', '.join(suites)}"))
    for cid in flags["over"]:
        issues.append(
            make_issue(
                "exclusion_rate",
                kind,
                message=f"condition {cid}: attrition {flags['rates'][cid]:.0%} > {flags['threshold']:.0%}",
                data={"condition_id": cid, "rate": flags["rates"][cid]},
            ),
        )
    if flags["differential"]:
        issues.append(
            make_issue(
                "differential_attrition",
                kind,
                message=f"attrition differs by more than {flags['gap']:.0%} between compared conditions",
                data={"conditions": flags["differential"], "rates": flags["rates"]},
            ),
        )

    matrix_cells = []
    for condition in conditions:
        per_unit: dict[int, list[_Cell]] = defaultdict(list)
        for cell in by_condition[condition.pk]:
            per_unit[cell.unit_id].append(cell)
        for unit in units:
            runs = sorted(per_unit.get(unit, []), key=lambda c: c.repeat)
            used = [c for c in runs if selection.kept(c)]
            matrix_cells.append(
                {
                    "condition_id": condition.pk,
                    "unit_id": unit,
                    "k": sum(1 for c in used if c.code == "c"),
                    "n": len(used),
                    "mean": round(sum(c.value for c in used) / len(used), 4) if used else None,  # type: ignore[misc]
                    "runs": [
                        {
                            "run_id": c.run_id,
                            "cell_id": c.cell_id,
                            "repeat_index": c.repeat,
                            "value": round(c.value, 4) if c.value is not None else None,
                            "outcome": c.outcome,
                            "termination": c.termination,
                            "truncated": c.truncated,
                            "code": c.code,
                        }
                        for c in runs
                    ],
                },
            )
    from backend.generation.models import StudyUnit

    unit_rows = {u.pk: u for u in StudyUnit.objects.filter(pk__in=units)}
    plan_row = study.active_plan
    plan_unbalanced = bool(
        plan_row is not None
        and (
            any(i.get("code") in ("unbalanced_grid", "combination_pruned") for i in plan_row.issues or [])
            or (study.design or {}).get("grid", {}).get("type") == "fractional"
        ),
    )
    common_rates = {
        row["condition_id"]: _common_rate(by_condition[row["condition_id"]], selection) for row in leaderboard
    }
    return {
        "metric": filters.metric,
        "sensitivity": filters.sensitivity,
        "itt": not exclude_truncated,
        "exclude_truncated": exclude_truncated,
        "planned_repeats": study.repeats,
        "stopped_early": stopped_early,
        "common_cells": filters.common_cells,
        "fingerprint_filter": filters.fingerprint,
        "baseline_condition_id": base.pk if base is not None else None,
        "baseline_source": baseline_source,
        "interim": interim,
        "balanced": len({frozenset(keys) for keys in selection.usable_keys.values()}) <= 1,
        "leaderboard": ranked,
        "matrix": {
            "units": [{"id": u, "unit_key": unit_rows[u].unit_key} for u in units if u in unit_rows],
            "cells": matrix_cells,
        },
        "paired": {
            "baseline_condition_id": base.pk if base is not None else None,
            "pairing": "unit_blocked",
            **test_plan,
            "n_units": n_units,
            "rows": rows,
            "sensitivity": sensitivity,
        },
        "gate": {
            "status": test_plan["status"],
            "locked": study.locked_at is not None,
            "deviations": deviations,
            "stopped_early": stopped_early,
            "planned_cells": sum(row["planned_cells"] for row in attrition),
            "multiplicity": {
                "family": plans.plan_multiplicity(plan) if plan else None,
                "correction": test_plan["correction"],
                "family_size": sum(1 for r in rows if r["p_value"] is not None)
                * (
                    max(1, len(plans.declared_metrics(plan))) if plan and plans.plan_multiplicity(plan) == "all" else 1
                ),
            },
            "significance_unreachable": unreachable,
            "exclusion_rate": {"threshold": flags["threshold"], "over": flags["over"], "rates": flags["rates"]},
            "differential_attrition": {"threshold": flags["gap"], "conditions": flags["differential"]},
            "budget": budget,
            "complete_blocks": None if blocks is None else sorted(blocks),
        },
        "attrition": attrition,
        "by_factor": _by_factor(study, conditions, means, plan_unbalanced),
        "by_prose_variant": _prose_variant_slices(conditions, cells, selection.kept),
        "common_pass_rate": common_rates,
        "retest_agreement": _retest(study),
        "issues": [issue.model_dump(mode="json") for issue in issues],
        "notes": notes,
        "generated_at": now.isoformat(),
    }


def _common_rate(cells: list[_Cell], selection: _Selection) -> float | None:
    values = [_float(c.eval_metrics.get("common_pass_rate")) for c in cells if selection.kept(c)]
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 4) if present else None


def study_results(
    study: Study,
    *,
    metric: str | None = None,
    fingerprint: str = "majority",
    sensitivity: str | None = None,
    baseline: Any = None,
    common_cells: bool = True,
) -> dict[str, Any]:
    """The results payload of ``GET /studies/{id}/results/``; intention-to-treat by default."""
    filters = ResultFilters(
        metric=metric or "pass_rate",
        sensitivity=sensitivity or None,
        fingerprint=fingerprint or "majority",
        common_cells=common_cells,
    )
    return compute(study, baseline, filters)
