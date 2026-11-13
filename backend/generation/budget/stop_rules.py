"""Study stop and pause rules, early stopping and pilot gates (spec section 4.6).

``evaluate(study)`` runs every ``study.stop_rules`` entry of the study's budget preset and applies the
action of each rule that fires. It is called after every run completion (``execution.transitions``) and
every 60 s for every piloting or running study (the executor), so it is idempotent: a paused condition is
not paused again and a stopping study is not stopped twice.

Windows are the last ``rule.window`` *finished* runs in scope (status completed or failed; cancelled and
budget-excluded runs never ran and are not in any rate). A rate rule needs at least ``min(window, 5)``
runs before it can fire, so one early failure never pauses a study.

Actions: ``pause`` (study ``paused``, reason ``rule:<code>``), ``stop`` (pending runs in scope end
``excluded:stopped_by_rule``; a study-scope stop ends the study ``stopped`` once idle), ``pause_condition``
(``StudyCondition.paused``: only that condition stops being claimed) and ``alert`` (reported only).

``ci_width_reached`` (slot, exploratory only) stops a condition x target once every listed primary feature's
Wilson 95% half-width is below the threshold with ``n >= n_min`` (or ``n >= n_max``). Its cells get
``adaptive_n=True``; the number of looks is ``K = n_at_stop - n_min + 1`` (one look per completed variant
from ``n_min``), which WP-13 uses for the conservative interval (Wilson at alpha / K). Confirmatory studies
never apply it (the lint reports ``adaptive_stop_in_confirmatory``).
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from django.db import transaction
from django.utils import timezone

from backend.generation.contracts.outcome import USABLE_OUTCOMES
from backend.generation.contracts.resources import PilotGates
from backend.generation.contracts.resources import ResourcePolicyContent
from backend.generation.contracts.resources import StopRule
from backend.generation.contracts.resources import preset_content
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models import StudyCondition
from backend.generation.models.choices import ExclusionReason
from backend.generation.models.choices import RunStatus

__all__ = [
    "evaluate",
    "evaluate_active",
    "evaluate_by_id",
    "gates_pass",
    "pilot_gates",
    "pilot_observations",
    "wilson_half_width",
]

logger = logging.getLogger(__name__)

FINISHED = (RunStatus.COMPLETED, RunStatus.FAILED)
FAILURE_OUTCOMES = frozenset({"infra_error", "build_fail"})
_Z95 = 1.959963984540054
MIN_RATE_RUNS = 5


def wilson_half_width(k: int, n: int, z: float = _Z95) -> float:
    """Half-width of the Wilson score interval for ``k`` of ``n`` (1.0 for ``n = 0``)."""
    if n <= 0:
        return 1.0
    p = k / n
    denominator = 1 + z * z / n
    return z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator


def _policy(study: Study) -> ResourcePolicyContent | None:
    if not study.resource_policy_id:
        return None
    return ResourcePolicyContent.model_validate(study.resource_policy.content)


def _finished(study: Study, stages: Iterable[str]) -> list[GenerationRun]:
    """Finished runs, newest first, without attempts a top-up already replaced (a newer run on the same cell).

    A topped-up failure is acknowledged: counting it again would re-fire the rule on every evaluation after
    the researcher fixed the cause and resumed (the replacement attempt is what is counted from then on).
    """
    from django.db.models import Exists
    from django.db.models import OuterRef

    replaced = GenerationRun.objects.filter(
        cell=OuterRef("cell"),
        beyond_plan=False,
        created_at__gt=OuterRef("created_at"),
    )
    return list(
        GenerationRun.objects.filter(
            study=study,
            status__in=FINISHED,
            stage__in=list(stages),
            beyond_plan=False,
        )
        .exclude(kind="reference")
        .exclude(Exists(replaced))
        .select_related("cell")
        .order_by("-completed_at", "-created_at"),
    )


def _cost(run: GenerationRun, basis: str = "billed") -> float | None:
    """The run's cost in the study cap's unit: ``metrics.list_cost_usd`` for a list-basis study (the
    billed amount when no list amount was recorded), else ``metrics.cost_usd``."""
    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    value = metrics.get("cost_usd")
    if basis == "list" and _is_number(metrics.get("list_cost_usd")):
        value = metrics.get("list_cost_usd")
    return float(value) if _is_number(value) else None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _estimate(cell: StudyCell, basis: str) -> float:
    """The cell's expected cost in the study cap's unit (``estimate_list_usd`` for a list-basis study)."""
    return float(cell.estimate_list_usd if basis == "list" else cell.estimate_usd)


def _group(runs: list[GenerationRun], scope: str) -> dict[Any, list[GenerationRun]]:
    groups: dict[Any, list[GenerationRun]] = defaultdict(list)
    for run in runs:
        if scope == "condition":
            key = run.condition_id
        elif scope == "unit":
            key = run.cell.unit_id if run.cell_id else None
        else:
            key = "study"
        groups[key].append(run)
    return groups


def _rate(runs: list[GenerationRun], window: int, predicate) -> tuple[float | None, int]:
    sample = runs[:window]
    if len(sample) < min(window, MIN_RATE_RUNS):
        return None, len(sample)
    return sum(1 for r in sample if predicate(r)) / len(sample), len(sample)


def _no_provider(run: GenerationRun) -> bool:
    return run.outcome == "infra_error" and (run.outcome_reason or "").startswith("no_provider")


def _evaluate_rule(rule: StopRule, study: Study, runs: list[GenerationRun], now: Any) -> list[dict[str, Any]]:  # noqa: PLR0911
    code = rule.code
    out: list[dict[str, Any]] = []
    if code == "wall_clock_total":
        if not study.launched_at:
            return out
        hours = (now - study.launched_at).total_seconds() / 3600
        if hours >= rule.threshold:
            out.append({"value": round(hours, 3), "scope_key": "study"})
        return out
    if code == "spend_fraction":
        from backend.generation.budget import claims
        from backend.generation.budget import ledger

        cap = claims.study_cap(study)
        if not cap:
            return out
        fraction = float(ledger.study_totals(study).spent / cap)
        if fraction >= rule.threshold:
            out.append({"value": round(fraction, 4), "scope_key": "study"})
        return out
    if code == "ci_width_reached":
        return _ci_width(rule, study)
    if code == "output_invalid_rate" and study.kind == "confirmatory":
        return out  # futility only when pre-registered (exploratory only)
    for key, group in _group(runs, rule.scope).items():
        if key is None:
            continue
        value: float | None
        if code == "infra_error_rate":
            value, n = _rate(group, rule.window, lambda r: r.outcome == "infra_error")
            fired = value is not None and value > rule.threshold
        elif code == "no_provider_rate":
            value, n = _rate(group, rule.window, _no_provider)
            fired = value is not None and value > rule.threshold
        elif code == "output_invalid_rate":
            value, n = _rate(group, rule.window, lambda r: r.outcome == "output_invalid")
            fired = value is not None and value > rule.threshold
        elif code == "condition_error_rate":
            value, n = _rate(group, rule.window, lambda r: r.outcome not in USABLE_OUTCOMES)
            fired = value is not None and value > rule.threshold
        elif code == "consecutive_failures":
            streak = 0
            for run in group:
                if run.outcome not in FAILURE_OUTCOMES:
                    break
                streak += 1
            any_tested = any(r.outcome == "tested" for r in group)
            value, n = float(streak), len(group)
            fired = streak >= rule.threshold and not any_tested
        elif code == "cost_overrun":
            # Cost and estimate in the cap's unit (E2E-13): a list-basis study on a subscription endpoint
            # bills 0, so billed against billed would never measure.
            basis = study.cap_basis
            sample = [r for r in group[: rule.window] if _cost(r, basis) is not None]
            estimates = [_estimate(r.cell, basis) for r in sample if r.cell_id and _estimate(r.cell, basis)]
            n = len(sample)
            if n < rule.window or not estimates:
                continue
            mean_cost = sum(_cost(r, basis) or 0.0 for r in sample) / n
            mean_estimate = sum(estimates) / len(estimates)
            value = mean_cost / mean_estimate if mean_estimate else None
            fired = value is not None and value > rule.threshold
        else:
            continue
        if fired:
            out.append({"value": value, "scope_key": key, "n": n})
    return out


def _ci_width(rule: StopRule, study: Study) -> list[dict[str, Any]]:
    """Condition x target groups whose primary features all reached the precision (exploratory slot)."""
    if study.kind == "confirmatory" or study.mode != "slot" or not rule.features:
        return []
    from backend.generation.models import PatternHit
    from backend.generation.models import PatternRun

    live = PatternRun.objects.filter(study=study, is_live=True).order_by("-created_at").first()
    if live is None:
        return []
    features = set(rule.features)
    counts: dict[tuple[Any, Any, str], list[int]] = defaultdict(lambda: [0, 0])  # (cond, unit, feature) -> [k, n]
    hits = PatternHit.objects.filter(pattern_run=live, status__in=["present", "absent"]).select_related(
        "detector",
        "run__cell",
    )
    seen: set[tuple[Any, str]] = set()
    for hit in hits:
        detector = hit.detector
        names = {hit.feature_key, detector.ref, detector.pinned_ref, detector.slug}
        matched = names & features
        if not matched or hit.run.cell_id is None:
            continue
        feature = sorted(matched)[0]
        if (hit.run_id, feature) in seen:
            continue  # one observation per variant and feature (span and file scopes are one variant)
        seen.add((hit.run_id, feature))
        key = (hit.run.condition_id, hit.run.cell.unit_id, feature)
        counts[key][1] += 1
        counts[key][0] += 1 if hit.status == "present" else 0
    by_group: dict[tuple[Any, Any], dict[str, tuple[int, int]]] = defaultdict(dict)
    for (condition, unit, feature), (k, n) in counts.items():
        by_group[(condition, unit)][feature] = (k, n)
    n_max = rule.n_max or study.repeats
    out = []
    for (condition, unit), per_feature in by_group.items():
        if set(per_feature) != features:
            continue  # every listed feature must be scored
        widths = {f: wilson_half_width(k, n) for f, (k, n) in per_feature.items()}
        n_min_reached = all(n >= rule.n_min for _k, n in per_feature.values())
        precise = all(w < rule.threshold for w in widths.values())
        at_max = all(n >= n_max for _k, n in per_feature.values())
        if (n_min_reached and precise) or at_max:
            n_stop = min(n for _k, n in per_feature.values())
            out.append(
                {
                    "value": round(max(widths.values()), 6),
                    "scope_key": (condition, unit),
                    "n": n_stop,
                    "looks": max(1, n_stop - rule.n_min + 1),
                    "half_widths": {f: round(w, 6) for f, w in widths.items()},
                },
            )
    return out


# =====================================================================================
# Actions
# =====================================================================================


def _apply(rule: StopRule, study: Study, fired: dict[str, Any]) -> bool:  # noqa: PLR0911
    from backend.generation.budget import claims

    reason = f"rule:{rule.code}"
    key = fired["scope_key"]
    if rule.action == "alert":
        return False
    if rule.code == "ci_width_reached":
        condition_id, unit_id = key
        cells = StudyCell.objects.filter(study=study, condition_id=condition_id, unit_id=unit_id, stage="main")
        changed = cells.filter(adaptive_n=False).update(adaptive_n=True)
        pending = GenerationRun.objects.filter(cell__in=cells, status=RunStatus.PENDING)
        excluded = claims.exclude_runs(pending, ExclusionReason.STOPPED_BY_RULE)
        return bool(changed or excluded)
    if rule.action == "pause_condition":
        condition_ids = [key] if rule.scope == "condition" else _conditions_of(study, rule.scope, key)
        changed = StudyCondition.objects.filter(pk__in=condition_ids, paused=False).update(
            paused=True,
            pause_reason=reason[:40],
        )
        if changed:
            for condition_id in condition_ids:
                claims._publish_after_commit(  # noqa: SLF001
                    study.pk,
                    {"type": "paused", "reason": reason, "condition_id": condition_id},
                )
        return bool(changed)
    if rule.action == "pause":
        return claims.pause_study(study, reason)
    if rule.action == "stop":
        if rule.scope == "study":
            if study.stop_reason:
                return False
            stop = {
                "code": reason,
                "detail": f"{rule.code} = {fired.get('value')}",
                "at": timezone.now().isoformat(),
                "rule": rule.code,
            }
            Study.objects.filter(pk=study.pk).update(stop_reason=stop, updated_at=timezone.now())
            study.stop_reason = stop
            pending = GenerationRun.objects.filter(study=study, status=RunStatus.PENDING)
        elif rule.scope == "condition":
            pending = GenerationRun.objects.filter(study=study, condition_id=key, status=RunStatus.PENDING)
        else:
            pending = GenerationRun.objects.filter(study=study, cell__unit_id=key, status=RunStatus.PENDING)
        excluded = claims.exclude_runs(pending, ExclusionReason.STOPPED_BY_RULE)
        if rule.scope == "study":
            from backend.execution import transitions

            transaction.on_commit(lambda: transitions.maybe_complete_study(study.pk))
        return bool(excluded) or rule.scope == "study"
    return False


def _conditions_of(study: Study, scope: str, key: Any) -> list[int]:
    if scope == "study":
        return list(StudyCondition.objects.filter(study=study).values_list("pk", flat=True))
    return list(
        GenerationRun.objects.filter(study=study, cell__unit_id=key).values_list("condition_id", flat=True).distinct(),
    )


def evaluate(study: Study, *, apply: bool = True, now: Any = None) -> list[dict[str, Any]]:
    """The rules that fired and whether their action changed anything: ``[{code, scope, scope_key, value,
    threshold, action, applied}]``."""
    policy = _policy(study)
    if policy is None or study.status not in ("piloting", "running"):
        return []
    now = now or timezone.now()
    stage = "pilot" if study.status == "piloting" else "main"
    fired_rules: list[dict[str, Any]] = []
    runs_by_stages: dict[tuple[str, ...], list[GenerationRun]] = {}
    for rule in policy.study.stop_rules:
        if stage not in rule.stages:
            continue
        stages = tuple(rule.stages)
        if stages not in runs_by_stages:
            runs_by_stages[stages] = _finished(study, stages)
        for fired in _evaluate_rule(rule, study, runs_by_stages[stages], now):
            applied = False
            if apply:
                with transaction.atomic():
                    applied = _apply(rule, study, fired)
            entry = {
                "code": rule.code,
                "scope": rule.scope,
                "scope_key": _jsonable(fired["scope_key"]),
                "value": fired.get("value"),
                "threshold": rule.threshold,
                "action": rule.action,
                "applied": applied,
            }
            entry.update({k: v for k, v in fired.items() if k not in ("value", "scope_key")})
            fired_rules.append(entry)
            if applied:
                logger.info(
                    "Study %s: stop rule %s fired (%s) -> %s",
                    study.pk,
                    rule.code,
                    entry["value"],
                    rule.action,
                )
    return fired_rules


def _jsonable(key: Any) -> Any:
    if isinstance(key, tuple):
        return [str(part) if part is not None else None for part in key]
    return key if isinstance(key, (int, str)) or key is None else str(key)


def evaluate_by_id(study_id: Any) -> list[dict[str, Any]]:
    study = Study.objects.select_related("resource_policy").filter(pk=study_id).first()
    return evaluate(study) if study is not None else []


def evaluate_active() -> int:
    """Evaluate every piloting or running study; returns the number of rules applied."""
    applied = 0
    for study in Study.objects.select_related("resource_policy").filter(status__in=["piloting", "running"]):
        try:
            applied += sum(1 for row in evaluate(study) if row["applied"])
        except Exception:
            logger.exception("Stop rules of study %s failed", study.pk)
    return applied


# =====================================================================================
# Pilot gates (4.2, 4.6)
# =====================================================================================


def _note_codes(run: GenerationRun) -> list[str]:
    validity = run.validity if isinstance(run.validity, dict) else {}
    return [str(n.get("code", "")) for n in validity.get("notes") or [] if isinstance(n, dict)]


def _termination(run: GenerationRun) -> str:
    validity = run.validity if isinstance(run.validity, dict) else {}
    return str(validity.get("termination") or (run.result or {}).get("termination") or "")


def _pilot_runs(study: Study) -> list[GenerationRun]:
    """The pilot cells' current runs (first usable attempt, else the latest finished attempt)."""
    out = []
    cells = StudyCell.objects.filter(study=study, stage="pilot").select_related("current_run")
    for cell in cells:
        run = cell.current_run
        if run is None or run.status not in FINISHED:
            run = (
                GenerationRun.objects.filter(cell=cell, status__in=FINISHED, beyond_plan=False)
                .order_by("-usable", "created_at")
                .first()
            )
        if run is not None:
            out.append(run)
    return out


def _gate(code: str, value: float | None, threshold: float, *, upper: bool, n: int, **extra: Any) -> dict[str, Any]:
    if value is None:
        ok = code not in ("min_build_rate",)  # nothing usable to build is a failed pilot, never a pass
        return {"code": code, "ok": ok, "value": None, "threshold": threshold, "n": n, "measured": False, **extra}
    ok = value <= threshold if upper else value >= threshold
    return {
        "code": code,
        "ok": ok,
        "value": round(value, 6),
        "threshold": threshold,
        "n": n,
        "measured": True,
        **extra,
    }


def pilot_gates(study: Study, *, runs: list[GenerationRun] | None = None) -> list[dict[str, Any]]:
    """``[{code, ok, value, threshold, n, condition_id?, stack?}]`` for the pilot gates, plus one
    ``cap_hits_by_kind`` row (``value = {kind: runs}``; informational, never blocking)."""
    policy = _policy(study)
    gates: PilotGates = (policy or preset_content("frugal")).pilot.gates
    runs = runs if runs is not None else _pilot_runs(study)
    by_condition: dict[Any, list[GenerationRun]] = defaultdict(list)
    for run in runs:
        by_condition[run.condition_id].append(run)
    out: list[dict[str, Any]] = []
    cap_hits: Counter[str] = Counter()
    tokens_basis = study.budget_basis == "tokens"
    basis = study.cap_basis  # max_cost_overrun compares cost and estimate in the cap's unit
    for condition_id, group in sorted(by_condition.items(), key=lambda kv: str(kv[0])):
        n = len(group)
        usable = [r for r in group if r.outcome in USABLE_OUTCOMES]
        built = [r for r in usable if r.outcome in ("tested", "start_fail")]
        costs = [c for c in (_cost(r, basis) for r in group) if c is not None]
        estimates = [_estimate(r.cell, basis) for r in group if r.cell_id and _estimate(r.cell, basis)]
        notes = [_note_codes(r) for r in group]
        hit = [
            any(c.startswith("budget_exhausted") or c in ("wall_clock", "idle_timeout") for c in codes)
            for codes in notes
        ]
        for codes in notes:
            for c in codes:
                if c.startswith("budget_exhausted") or c in ("wall_clock", "idle_timeout", "max_turns"):
                    cap_hits[c] += 1
        call_trunc = sum(
            1
            for r, codes in zip(group, notes, strict=True)
            if any(c.startswith("call_truncations:") and c.split(":", 1)[1] not in ("", "0") for c in codes)
            or ((r.metrics or {}).get("call_truncations") or 0) > 0
        )
        extra = {"condition_id": condition_id}
        out.append(
            _gate(
                "max_infra_error_rate",
                sum(r.outcome == "infra_error" for r in group) / n,
                gates.max_infra_error_rate,
                upper=True,
                n=n,
                **extra,
            ),
        )
        out.append(
            _gate(
                "min_build_rate",
                (len(built) / len(usable)) if usable else None,
                gates.min_build_rate,
                upper=False,
                n=len(usable),
                **extra,
            ),
        )
        overrun = None
        if costs and estimates and sum(estimates) > 0:
            overrun = (sum(costs) / len(costs)) / (sum(estimates) / len(estimates))
        out.append(
            _gate(
                "max_cost_overrun",
                overrun,
                gates.max_cost_overrun,
                upper=True,
                n=len(costs),
                cap_basis=basis,
                **extra,
            ),
        )
        out.append(_gate("max_cap_hit_rate", sum(hit) / n, gates.max_cap_hit_rate, upper=True, n=n, **extra))
        truncated = sum(1 for r in group if (r.validity or {}).get("truncated"))
        out.append(_gate("max_truncation_rate", truncated / n, gates.max_truncation_rate, upper=True, n=n, **extra))
        call_rate = call_trunc / n
        out.append(
            _gate("max_call_truncation_rate", call_rate, gates.max_call_truncation_rate, upper=True, n=n, **extra),
        )
        wall = sum(1 for r in group if _termination(r) == "wall_clock")
        out.append(_gate("max_wall_clock_rate", wall / n, gates.max_wall_clock_rate, upper=True, n=n, **extra))
        if tokens_basis:
            binding = sum(1 for codes in notes if "budget_exhausted:cost" in codes)
            out.append(_gate("usd_cap_binding", binding / n, gates.usd_cap_binding, upper=True, n=n, **extra))
    by_stack: dict[Any, list[GenerationRun]] = defaultdict(list)
    for run in runs:
        by_stack[run.stack_profile_id].append(run)
    for stack_id, group in sorted(by_stack.items(), key=lambda kv: str(kv[0])):
        oom = sum(1 for r in group if _termination(r) == "oom_killed" or r.outcome_reason == "resource_limit")
        out.append(
            _gate("max_oom_rate", oom / len(group), gates.max_oom_rate, upper=True, n=len(group), stack=stack_id),
        )
    out.append({"code": "cap_hits_by_kind", "ok": True, "value": dict(cap_hits), "threshold": None, "n": len(runs)})
    if not runs:
        out.append({"code": "pilot_runs", "ok": False, "value": 0, "threshold": None, "n": 0, "measured": False})
    return out


def gates_pass(gates: list[dict[str, Any]]) -> bool:
    return all(g["ok"] for g in gates)


def pilot_observations(study: Study) -> dict[int, list[float]]:
    """``{condition_index: [cost of each finished pilot run]}`` for ``estimate.reestimate_after_pilot``
    (in the study cap's unit, like the estimate's ``cap_basis``)."""
    out: dict[int, list[float]] = defaultdict(list)
    for run in _pilot_runs(study):
        cost = _cost(run, study.cap_basis)
        if cost is not None and run.condition_id:
            out[run.condition.index].append(cost)
    return dict(out)
