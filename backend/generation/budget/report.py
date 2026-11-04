"""Budget read models: the live study spend payload (4.7), the user's period totals, the header chip, block
status and the budget deviations the results show (``stop_reason``, ``budget_stop`` exclusions,
``stopped_early`` cells).

``complete_blocks(study)`` is what paired and by-condition results use (Appendix A #14: complete blocks
only). A block is complete when every cell of it finished with a run that is not a budget, rule or cancel
exclusion and no attempt of it is still active.
"""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Q
from django.db.models import Sum
from django.utils import timezone

from backend.generation.budget import claims
from backend.generation.budget import ledger
from backend.generation.contracts.outcome import USABLE_OUTCOMES
from backend.generation.contracts.resources import ResourcePolicyContent
from backend.generation.models import GenerationRun
from backend.generation.models import SpendCap
from backend.generation.models import SpendLedgerEntry
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models import StudyCondition
from backend.generation.models.choices import CapScope
from backend.generation.models.choices import CapUnit
from backend.generation.models.choices import LedgerKind

__all__ = [
    "BUDGET_EXCLUSIONS",
    "block_status",
    "budget_deviations",
    "complete_blocks",
    "header_payload",
    "me_payload",
    "study_payload",
]

ZERO = Decimal(0)
BUDGET_EXCLUSIONS = ("cell_budget", "budget_stop", "stopped_by_rule", "cancelled")
_ACTIVE = ("pending", "running")
_PAUSE_BADGES = ("budget", "quota", "spend_cap")


def _f(value: Decimal | float | None) -> float | None:
    return None if value is None else round(float(value), 8)


def _cells(study: Study) -> list[StudyCell]:
    """The cells of the active plan (every cell when no plan is active)."""
    qs = StudyCell.objects.filter(study=study)
    if study.active_plan_id and qs.filter(plan_id=study.active_plan_id).exists():
        qs = qs.filter(plan_id=study.active_plan_id)
    return list(qs)


def _planned_cells(study: Study) -> list[StudyCell]:
    """The planned-sample cells of the active plan: replicate cells (``beyond_plan=True``, added after launch)
    are not part of any block, so they never make a planned block incomplete nor add blocks to the total."""
    return [c for c in _cells(study) if not c.beyond_plan]


def block_status(study: Study) -> dict[str, Any]:
    """``{started, complete, total}`` over (stage, block) of the study's planned cells (replicates excluded)."""
    cells = _planned_cells(study)
    total = {(c.stage, c.block_index) for c in cells}
    started = set()
    for key in SpendLedgerEntry.objects.filter(
        study=study,
        run__isnull=True,
        kind=LedgerKind.RESERVE,
        dedupe_key__startswith=f"blk:{study.pk}:",
    ).values_list("dedupe_key", flat=True):
        parts = key.split(":")
        if len(parts) >= 4:
            started.add((parts[2], int(parts[3])))
    return {"started": len(started & total), "complete": len(complete_blocks(study, cells=cells)), "total": len(total)}


def complete_blocks(
    study: Study,
    *,
    cells: list[StudyCell] | None = None,
    stage: str | None = None,
) -> list[tuple[str, int]]:
    """``[(stage, block_index)]`` of blocks whose every planned cell finished with a non-excluded run.

    Replicate cells (``beyond_plan=True``) are ignored, also when passed in ``cells``."""
    cells = [c for c in (cells if cells is not None else _cells(study)) if not c.beyond_plan]
    if stage is not None:
        cells = [c for c in cells if c.stage == stage]
    runs = defaultdict(list)
    for run in GenerationRun.objects.filter(cell__in=cells, beyond_plan=False).only(
        "cell_id",
        "status",
        "outcome",
        "outcome_reason",
    ):
        runs[run.cell_id].append(run)
    by_block: dict[tuple[str, int], list[StudyCell]] = defaultdict(list)
    for cell in cells:
        by_block[(cell.stage, cell.block_index)].append(cell)
    out = []
    for block, members in sorted(by_block.items()):
        ok = True
        for cell in members:
            cell_runs = runs.get(cell.pk, [])
            if cell.state == "excluded" or not cell_runs or any(r.status in _ACTIVE for r in cell_runs):
                ok = False
                break
            if not any(
                r.outcome and not (r.outcome == "excluded" and r.outcome_reason in BUDGET_EXCLUSIONS)
                for r in cell_runs
            ):
                ok = False
                break
        if ok:
            out.append(block)
    return out


def budget_deviations(study: Study) -> dict[str, Any]:
    """What a budget or rule stop did to the design: the stop reason, the exclusions per condition and the
    ``stopped_early`` (condition, unit) groups the confirmatory gate reports as deviations."""
    excluded: dict[Any, Counter[str]] = defaultdict(Counter)
    stopped_early: dict[tuple[Any, Any], int] = defaultdict(int)
    for cell in StudyCell.objects.filter(study=study, state="excluded").values(
        "condition_id",
        "unit_id",
        "exclusion_reason",
    ):
        excluded[cell["condition_id"]][cell["exclusion_reason"]] += 1
        if cell["exclusion_reason"] in ("budget_stop", "stopped_by_rule"):
            stopped_early[(cell["condition_id"], cell["unit_id"])] += 1
    paused = list(StudyCondition.objects.filter(study=study, paused=True).values("pk", "pause_reason"))
    if study.kind == "confirmatory":
        # A paused condition stops claiming: its unfinished cells are stopped early in the gate.
        for row in paused:
            for cell in (
                StudyCell.objects.filter(study=study, condition_id=row["pk"], stage="main")
                .exclude(
                    state__in=["done", "excluded"],
                )
                .values("unit_id")
            ):
                stopped_early[(row["pk"], cell["unit_id"])] += 1
    adaptive = StudyCell.objects.filter(study=study, adaptive_n=True).values_list("condition_id", "unit_id").distinct()
    return {
        "stop_reason": study.stop_reason,
        "exclusions": [
            {"condition_id": cid, **{reason: counts.get(reason, 0) for reason in BUDGET_EXCLUSIONS}}
            for cid, counts in sorted(excluded.items(), key=lambda kv: str(kv[0]))
        ],
        "stopped_early": [
            {"condition_id": c, "unit_id": u, "cells": n} for (c, u), n in sorted(stopped_early.items(), key=str)
        ],
        "paused_conditions": [{"condition_id": r["pk"], "reason": r["pause_reason"]} for r in paused],
        "adaptive_n": [{"condition_id": c, "unit_id": u} for c, u in adaptive],
    }


def _burn_rate(study: Study, now: Any) -> float | None:
    since = now - timedelta(hours=1)
    column = "list_cost_usd" if study.cap_basis == "list" else "amount_usd"
    recent = SpendLedgerEntry.objects.filter(
        study=study,
        kind__in=ledger.USAGE_KINDS,
        created_at__gte=since,
    ).aggregate(s=Sum(column))["s"]
    if study.launched_at and now - study.launched_at < timedelta(hours=1):
        hours = max((now - study.launched_at).total_seconds() / 3600, 1 / 60)
        total = SpendLedgerEntry.objects.filter(study=study, kind__in=ledger.USAGE_KINDS).aggregate(s=Sum(column))["s"]
        return float(total or 0) / hours
    return float(recent or 0)


def study_payload(study: Study, *, now: Any = None) -> dict[str, Any]:
    """``GET /budget/studies/{id}/`` (4.7)."""
    now = now or timezone.now()
    totals = ledger.study_totals(study)
    cap = claims.study_cap(study)
    limits = claims._study_limits(study)  # noqa: SLF001
    cells = _cells(study)
    remaining_cells = [c for c in cells if c.state not in ("done", "excluded")]
    # In the cap's unit, like the burn rate it is divided by (list for a list-basis study).
    estimate_field = "estimate_list_usd" if study.cap_basis == "list" else "estimate_usd"
    expected_remaining = sum((Decimal(getattr(c, estimate_field) or 0) for c in remaining_cells), ZERO)
    burn = _burn_rate(study, now)
    eta = (float(expected_remaining) / burn * 3600) if burn and expected_remaining else None
    spent_by_run = dict(
        SpendLedgerEntry.objects.filter(study=study, kind__in=ledger.USAGE_KINDS, run__isnull=False)
        .values("run_id")
        .annotate(s=Sum("amount_usd"))
        .values_list("run_id", "s"),
    )
    runs = list(
        GenerationRun.objects.filter(study=study)
        .exclude(kind="reference")
        .values(
            "pk",
            "condition_id",
            "model__model_id",
            "outcome",
            "outcome_reason",
        ),
    )
    excluded_cells = Counter((c.condition_id, c.exclusion_reason) for c in cells if c.state == "excluded")
    by_condition = []
    labels = dict(StudyCondition.objects.filter(study=study).values_list("pk", "label"))
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        groups[run["condition_id"]].append(run)
    for condition_id in sorted({*groups, *labels}, key=str):
        members = groups.get(condition_id, [])
        spent = sum((spent_by_run.get(r["pk"]) or ZERO for r in members), ZERO)
        usable = sum(1 for r in members if r["outcome"] in USABLE_OUTCOMES)
        tested = sum(1 for r in members if r["outcome"] == "tested")
        by_condition.append(
            {
                "condition": condition_id,
                "label": labels.get(condition_id, ""),
                "spent": _f(spent),
                "runs": len(members),
                "usable": usable,
                "tested": tested,
                "cost_per_usable_sample": _f(spent / usable) if usable else None,
                "cost_per_tested_sample": _f(spent / tested) if tested else None,
                "excluded_cell_budget": excluded_cells.get((condition_id, "cell_budget"), 0),
                "excluded_budget_stop": excluded_cells.get((condition_id, "budget_stop"), 0),
                "excluded_stopped_by_rule": excluded_cells.get((condition_id, "stopped_by_rule"), 0),
            },
        )
    models: dict[str, dict[str, Any]] = defaultdict(lambda: {"spent": ZERO, "runs": 0, "usable": 0})
    for run in runs:
        row = models[run["model__model_id"] or ""]
        row["spent"] += spent_by_run.get(run["pk"]) or ZERO
        row["runs"] += 1
        row["usable"] += 1 if run["outcome"] in USABLE_OUTCOMES else 0
    by_model = [
        {"model": slug, "spent": _f(v["spent"]), "runs": v["runs"], "usable": v["usable"]}
        for slug, v in sorted(models.items())
    ]
    policy = ResourcePolicyContent.model_validate(study.resource_policy.content) if study.resource_policy_id else None
    alerts = []
    if cap:
        fraction = float(totals.spent / cap)
        for threshold in sorted(policy.study.alert_at if policy else []):
            alerts.append({"fraction": threshold, "crossed": fraction >= threshold})
    from backend.generation.budget import stop_rules

    fired = stop_rules.evaluate(study, apply=False, now=now) if study.status in ("piloting", "running") else []
    return {
        "study_id": str(study.pk),
        "status": study.status,
        "pause_reason": study.pause_reason,
        "cap": _f(cap),
        "cap_basis": study.cap_basis,
        "budget_basis": study.budget_basis,
        "on_budget_exhausted": limits["on_exhausted"],
        "spent": _f(totals.spent),
        "spent_billed": _f(totals.spent_billed),
        "spent_list": _f(totals.spent_list),
        "reserved": _f(max(ZERO, totals.held)),
        "open_reserved": _f(totals.open_reserved),
        "remaining": _f(cap - totals.exposure) if cap is not None else None,
        "expected_remaining": _f(expected_remaining),
        "tokens": {"spent": totals.spent_tokens, "reserved": max(0, totals.held_tokens), "cap": limits["tokens"]},
        "burn_rate_usd_per_h": round(burn, 6) if burn is not None else None,
        "eta_s": round(eta, 1) if eta is not None else None,
        "by_condition": by_condition,
        "by_model": by_model,
        "alerts": alerts,
        "rules": fired,
        "blocks": block_status(study),
        "deviations": budget_deviations(study),
    }


def _period_totals(user: Any, period: str, now: Any) -> dict[str, float]:
    since = claims.period_start(period, now)
    agg = SpendLedgerEntry.objects.filter(user=user, kind__in=ledger.USAGE_KINDS, created_at__gte=since).aggregate(
        s=Sum("amount_usd"),
        lst=Sum("list_cost_usd"),
        tin=Sum("tokens_in"),
        tout=Sum("tokens_out"),
    )
    return {
        "spent": float(agg["s"] or 0),
        "spent_list": float(agg["lst"] or 0),
        "tokens": int(agg["tin"] or 0) + int(agg["tout"] or 0),
    }


def _user_caps(user: Any) -> list[SpendCap]:
    return list(SpendCap.objects.filter(Q(scope=CapScope.USER, user=user) | Q(scope=CapScope.GLOBAL), enabled=True))


def me_payload(user: Any, *, now: Any = None) -> dict[str, Any]:
    """``GET /budget/me/``: the user's period totals against their caps, and their active studies."""
    now = now or timezone.now()
    periods = []
    caps = _user_caps(user)
    covered = set()
    for cap in caps:
        totals = _period_totals(user, cap.period, now)
        covered.add(cap.period)
        periods.append(
            {
                "period": cap.period,
                "unit": cap.unit,
                "scope": cap.scope,
                "spent": totals["spent"],
                "spent_list": totals["spent_list"],
                "tokens": totals["tokens"],
                "used": claims.cap_usage(cap, now=now) if cap.scope == CapScope.USER else None,
                "cap": float(cap.limit),
                "action": cap.action,
                "cap_id": cap.pk,
            },
        )
    for period in ("day", "week", "month"):
        if period not in covered:
            totals = _period_totals(user, period, now)
            periods.append(
                {
                    "period": period,
                    "unit": CapUnit.USD,
                    "spent": totals["spent"],
                    "spent_list": totals["spent_list"],
                    "tokens": totals["tokens"],
                    "cap": None,
                    "action": None,
                },
            )
    studies = []
    for study in Study.objects.filter(created_by=user, status__in=["piloting", "running", "paused"]).select_related(
        "resource_policy",
        "active_plan",
    ):
        cap = claims.study_cap(study)
        studies.append(
            {
                "id": str(study.pk),
                "name": study.name,
                "status": study.status,
                "spent": float(study.spent_usd),
                "spent_list": float(study.spent_list_usd),
                "cap": _f(cap),
                # The cap's unit: a list-basis cap is measured against spent_list (budget page "Used").
                "cap_basis": study.cap_basis,
            },
        )
    for row in periods:
        if row.get("used") is not None:
            row["used"] = float(row["used"])
    return {"periods": periods, "studies": studies}


def header_payload(user: Any, *, now: Any = None) -> dict[str, Any]:
    """``GET /budget/header/``: the tightest user period's spend vs its cap, and studies paused for money."""
    now = now or timezone.now()
    caps = [c for c in _user_caps(user) if c.unit != CapUnit.TOKENS]
    order = {"5h": 0, "day": 1, "week": 2, "month": 3}
    cap = min(caps, key=lambda c: (order.get(c.period, 9), c.limit), default=None)
    period = cap.period if cap is not None else "day"
    totals = _period_totals(user, period, now)
    spend = totals["spent_list"] if cap is not None and cap.unit == CapUnit.LIST_USD else totals["spent"]
    paused = [
        {"id": str(pk), "reason": reason}
        for pk, reason in Study.objects.filter(
            created_by=user,
            status="paused",
            pause_reason__in=_PAUSE_BADGES,
        ).values_list(
            "pk",
            "pause_reason",
        )
    ]
    return {
        "period": period,
        "unit": cap.unit if cap is not None else CapUnit.USD,
        "period_spend": round(spend, 6),
        "period_cap": float(cap.limit) if cap is not None else None,
        "paused_studies": paused,
    }
