"""Budget admission at claim time (spec section 4.5): blocks, cell caps, spend caps and quotas.

The scheduler calls :func:`admit` inside its claim transaction, after it locked the chosen run rows and
before it marks them running. Each study involved is locked (``SELECT ... FOR UPDATE`` on the Study row),
so concurrent claims of one study serialize and the reservation invariant holds:

    before claiming cell c (attempt a) of study s:
      need = min(run.max_cost_usd, c.cap_usd)
      if c.block not started:                      # block = all cells sharing (stage, block_index)
          block_need = Σ need over the block's unclaimed cells
          if exposure(s) + block_need > cap (USD, tokens, runs or wall hours):
              apply s.on_budget_exhausted          # pause | stop_balanced | stop
              refuse
          reserve block_need ("blk:...:r")
      if c.spent + need > c.cell_cap: exclude the cell (cell_budget); release its slice; refuse
      if a SpendCap (user / endpoint / model / global; usd, list_usd or tokens; 5h/day/week/month) would be
          exceeded and its action is "block": pause the study (quota for 5h/week model caps, else
          spend_cap); refuse (the run stays queued and is claimed again after the resume)
      move need from the block to the run ("res:{run}:{a}")

Spend caps are checked before the block is started (a refused claim must not hold block budget). An
infra retry (attempt > 1) never draws on its block: it reserves fresh budget from what is left.
Exclusions and study state changes publish their events after commit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from collections.abc import Hashable
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.db.models import Sum
from django.utils import timezone

from backend.generation.budget import ledger
from backend.generation.models import GenerationRun
from backend.generation.models import SpendCap
from backend.generation.models import SpendLedgerEntry
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models.choices import CapAction
from backend.generation.models.choices import CapPeriod
from backend.generation.models.choices import CapScope
from backend.generation.models.choices import CapUnit
from backend.generation.models.choices import CellState
from backend.generation.models.choices import ExclusionReason
from backend.generation.models.choices import LedgerKind
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunStatus

__all__ = [
    "BUDGET",
    "CELL_BUDGET",
    "QUOTA",
    "SPEND_CAP",
    "Decision",
    "admit",
    "apply_budget_exhausted",
    "cap_check",
    "exclude_runs",
    "matching_caps",
    "period_start",
    "study_cap",
]

logger = logging.getLogger(__name__)
ZERO = Decimal(0)

# Lane blocked reasons (the scheduler publishes them).
BUDGET = "budget"
CELL_BUDGET = "cell_budget"
SPEND_CAP = "spend_cap"
QUOTA = "quota"
BUDGET_STOPPED = "budget_stop"

_PERIODS = {
    CapPeriod.FIVE_HOURS: timedelta(hours=5),
    CapPeriod.DAY: timedelta(days=1),
    CapPeriod.WEEK: timedelta(days=7),
    CapPeriod.MONTH: timedelta(days=30),
}
_CLAIMABLE = ("piloting", "running")


@dataclass
class Decision:
    run_id: Any
    ok: bool
    reason: str = ""
    detail: str = ""
    reserved: Decimal = ZERO
    warnings: list[str] = field(default_factory=list)


def period_start(period: str, now: Any = None) -> Any:
    return (now or timezone.now()) - _PERIODS[period]


# =====================================================================================
# Study cap
# =====================================================================================


def study_cap(study: Study) -> Decimal | None:
    """The study cap in USD.

    ``Study.cap_usd`` first: the cap resolved at launch (preset, resolved ``auto`` or the LaunchDialog's
    ``study_cap``), which a later "raise cap" updates. When it is NULL: the policy's number, else the active
    plan's resolved ``auto`` cap. ``None`` means no USD cap is known; the other caps (tokens, runs, wall
    hours, spend caps) still apply.
    """
    if study.cap_usd is not None:
        return ledger.to_decimal(study.cap_usd)
    content = study.resource_policy.content if study.resource_policy_id else {}
    raw = ((content or {}).get("study") or {}).get("max_cost_usd")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return ledger.to_decimal(raw)
    plan = study.active_plan if study.active_plan_id else None
    estimate = plan.estimate if plan is not None and isinstance(plan.estimate, dict) else {}
    cap = estimate.get("cap_usd")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool):
        return ledger.to_decimal(cap)
    return None


def _study_limits(study: Study) -> dict[str, Any]:
    content = study.resource_policy.content if study.resource_policy_id else {}
    limits = (content or {}).get("study") or {}
    return {
        "tokens": study.cap_tokens if study.cap_tokens is not None else limits.get("max_total_tokens"),
        "runs": limits.get("max_runs"),
        "wall_hours": limits.get("max_wall_hours"),
        "on_exhausted": study.on_budget_exhausted or limits.get("on_budget_exhausted") or "stop_balanced",
    }


# =====================================================================================
# Spend caps
# =====================================================================================


def matching_caps(run: GenerationRun) -> list[SpendCap]:
    """Enabled caps that cover ``run``: its user, its endpoint, its model (on that endpoint, if set), global."""
    endpoint_id = ledger.run_endpoint_id(run)
    slug = run.model.model_id if run.model_id else ""
    user_id = run.created_by_id or (run.study.created_by_id if run.study_id else None)
    match = Q(scope=CapScope.GLOBAL)
    if user_id is not None:
        match |= Q(scope=CapScope.USER, user_id=user_id)
    if endpoint_id is not None:
        match |= Q(scope=CapScope.ENDPOINT, endpoint_id=endpoint_id)
    if slug:
        match |= Q(scope=CapScope.MODEL, model_slug=slug) & (Q(endpoint__isnull=True) | Q(endpoint_id=endpoint_id))
    return list(SpendCap.objects.filter(match, enabled=True).order_by("pk"))


def _cap_scope_filter(cap: SpendCap) -> Q:
    if cap.scope == CapScope.USER:
        return Q(user_id=cap.user_id)
    if cap.scope == CapScope.ENDPOINT:
        return Q(endpoint_id=cap.endpoint_id)
    if cap.scope == CapScope.MODEL:
        scope = Q(model_slug=cap.model_slug)
        return scope & Q(endpoint_id=cap.endpoint_id) if cap.endpoint_id else scope
    return Q()


def cap_usage(cap: SpendCap, *, now: Any = None) -> Decimal:
    """What counts against ``cap`` now: usage in its rolling window plus what open reservations still hold."""
    scope = _cap_scope_filter(cap)
    start = period_start(cap.period, now)
    window = SpendLedgerEntry.objects.filter(scope, kind__in=ledger.USAGE_KINDS, created_at__gte=start)
    if cap.unit == CapUnit.TOKENS:
        agg = window.aggregate(a=Sum("tokens_in"), b=Sum("tokens_out"))
        used = Decimal(int(agg["a"] or 0) + int(agg["b"] or 0))
        held = Decimal(ledger.scope_totals(scope).held_tokens)
    else:
        column = "list_cost_usd" if cap.unit == CapUnit.LIST_USD else "amount_usd"
        used = window.aggregate(s=Sum(column))["s"] or ZERO
        held = ledger.scope_totals(scope, cap_basis="list" if cap.unit == CapUnit.LIST_USD else "billed").held
    return used + max(ZERO, held)


def cap_check(run: GenerationRun, need: Decimal, tokens: int, *, now: Any = None) -> tuple[SpendCap | None, list[str]]:
    """``(blocking cap or None, warnings)`` for one more attempt of ``run``."""
    warnings: list[str] = []
    for cap in matching_caps(run):
        amount = Decimal(tokens) if cap.unit == CapUnit.TOKENS else need
        used = cap_usage(cap, now=now)
        if used + amount <= cap.limit:
            continue
        text = f"{cap}: {used} used + {amount} > {cap.limit}"
        if cap.action == CapAction.BLOCK:
            return cap, warnings
        warnings.append(text)
        logger.warning("spend cap warning for run %s: %s", run.pk, text)
    return None, warnings


def _cap_reason(cap: SpendCap) -> str:
    if cap.scope == CapScope.MODEL and cap.period in (CapPeriod.FIVE_HOURS, CapPeriod.WEEK):
        return QUOTA
    return SPEND_CAP


# =====================================================================================
# Exclusions and study state
# =====================================================================================


def exclude_runs(runs: Iterable[GenerationRun], reason: str) -> int:
    """End pending runs as ``excluded:<reason>`` (status cancelled, never claimed) and exclude their cells.

    Events, the cells' current run and block closing follow after commit.
    """
    items = [r for r in runs if r.status == RunStatus.PENDING]
    if not items:
        return 0
    now = timezone.now()
    ids = [r.pk for r in items]
    updated = GenerationRun.objects.filter(pk__in=ids, status=RunStatus.PENDING).update(
        status=RunStatus.CANCELLED,
        phase="",
        outcome=Outcome.EXCLUDED,
        outcome_reason=reason,
        usable=False,
        completed_at=now,
        updated_at=now,
        lease_owner="",
        lease_expires_at=None,
    )
    cell_ids = [r.cell_id for r in items if r.cell_id]
    if cell_ids:
        StudyCell.objects.filter(pk__in=cell_ids).exclude(state=CellState.DONE).update(
            state=CellState.EXCLUDED,
            exclusion_reason=reason,
        )

    def after_commit() -> None:
        from backend.execution import transitions

        for run in GenerationRun.objects.filter(pk__in=ids).select_related("cell"):
            transitions.refresh_cell(run.cell_id)
            transitions.publish_run(run)
            if run.cell_id:
                ledger.maybe_close_block(run.cell)
        for study_id in {r.study_id for r in items if r.study_id}:
            ledger.refresh_caches(study_id)
            transitions.maybe_complete_study(study_id)

    transaction.on_commit(after_commit)
    return updated


def _publish_after_commit(study_id: Any, event: dict[str, Any]) -> None:
    def publish() -> None:
        from backend.realtime.events import publish_study_event

        publish_study_event(study_id, event)

    transaction.on_commit(publish)


def pause_study(study: Study, reason: str, *, condition_id: int | None = None) -> bool:
    """Pause a claimable study (``paused``, ``pause_reason``); publishes ``paused`` and ``state`` after commit."""
    updated = Study.objects.filter(pk=study.pk, status__in=_CLAIMABLE).update(
        status="paused",
        pause_reason=reason[:40],
        updated_at=timezone.now(),
    )
    if updated:
        study.status, study.pause_reason = "paused", reason
        _publish_after_commit(study.pk, {"type": "paused", "reason": reason, "condition_id": condition_id})
        _publish_after_commit(study.pk, {"type": "state", "status": "paused", "pause_reason": reason})
    return bool(updated)


def _stop_reason(code: str, detail: str, rule: str | None = None) -> dict[str, Any]:
    return {"code": code, "detail": detail[:500], "at": timezone.now().isoformat(), "rule": rule}


def _unstarted_block_runs(study: Study) -> list[GenerationRun]:
    started = set(
        SpendLedgerEntry.objects.filter(
            study=study,
            run__isnull=True,
            kind=LedgerKind.RESERVE,
            dedupe_key__startswith=f"blk:{study.pk}:",
        ).values_list("dedupe_key", flat=True),
    )
    pending = GenerationRun.objects.filter(study=study, status=RunStatus.PENDING).select_related("cell")
    out = []
    for run in pending:
        cell = run.cell
        if cell is None:
            out.append(run)
            continue
        key = f"{ledger.block_key(study.pk, cell.stage, cell.block_index)}:r"
        if key not in started or run.attempt > 0:
            out.append(run)  # an unstarted block, or a retry that found no budget
    return out


def apply_budget_exhausted(study: Study, detail: str, *, action: str | None = None) -> str:
    """Apply ``study.on_budget_exhausted`` (4.5): ``pause`` | ``stop_balanced`` | ``stop``. Returns the action.

    ``stop_balanced`` claims no new block: the pending runs of unstarted blocks end ``excluded:budget_stop``
    now, started blocks finish, and the study becomes ``stopped`` (reason ``budget``) once idle
    (``transitions.maybe_complete_study``). ``stop`` excludes every pending run and stops the study now.
    """
    action = action or _study_limits(study)["on_exhausted"]
    if action == "pause":
        pause_study(study, "budget")
        return action
    if study.stop_reason:
        return action  # already stopping
    reason = _stop_reason("budget", detail)
    Study.objects.filter(pk=study.pk).update(stop_reason=reason, updated_at=timezone.now())
    study.stop_reason = reason
    if action == "stop":
        exclude_runs(GenerationRun.objects.filter(study=study, status=RunStatus.PENDING), ExclusionReason.BUDGET_STOP)
        Study.objects.filter(pk=study.pk, status__in=_CLAIMABLE).update(status="stopped", completed_at=timezone.now())
        study.status = "stopped"
        _publish_after_commit(study.pk, {"type": "stopped", "reason": "budget"})
        _publish_after_commit(study.pk, {"type": "state", "status": "stopped", "pause_reason": ""})
    else:
        exclude_runs(_unstarted_block_runs(study), ExclusionReason.BUDGET_STOP)
    return action


# =====================================================================================
# Admission
# =====================================================================================


@dataclass
class _StudyState:
    study: Study
    cap: Decimal | None
    limits: dict[str, Any]
    totals: ledger.StudyTotals
    exposure: Decimal
    exposure_tokens: int
    attempts: int
    refused: str = ""

    def over(self, usd: Decimal, tokens: int, runs: int, now: Any) -> str | None:
        if self.cap is not None and self.exposure + usd > self.cap:
            return f"cost: {self.exposure} committed + {usd} > cap {self.cap}"
        token_cap = self.limits["tokens"]
        if token_cap and self.exposure_tokens + tokens > int(token_cap):
            return f"tokens: {self.exposure_tokens} committed + {tokens} > cap {token_cap}"
        run_cap = self.limits["runs"]
        if run_cap and self.attempts + runs > int(run_cap):
            return f"runs: {self.attempts} claimed + {runs} > cap {run_cap}"
        hours = self.limits["wall_hours"]
        launched = self.study.launched_at
        if hours and launched and (now - launched).total_seconds() > float(hours) * 3600:
            return f"wall hours: launched {launched.isoformat()} > {hours} h"
        return None


def _state(study: Study) -> _StudyState:
    totals = ledger.study_totals(study)
    attempts = SpendLedgerEntry.objects.filter(
        study=study,
        kind=LedgerKind.RESERVE,
        run__isnull=False,
        dedupe_key__startswith="res:",
    ).count()
    return _StudyState(
        study=study,
        cap=study_cap(study),
        limits=_study_limits(study),
        totals=totals,
        exposure=totals.exposure,
        exposure_tokens=totals.exposure_tokens,
        attempts=attempts,
    )


def _block_runs(run: GenerationRun) -> list[GenerationRun]:
    cell = run.cell
    return list(
        GenerationRun.objects.filter(
            study_id=run.study_id,
            status=RunStatus.PENDING,
            attempt=0,
            cell__stage=cell.stage,
            cell__block_index=cell.block_index,
        ).select_related("cell", "model", "manifest", "resource_policy", "study__resource_policy"),
    )


def admit(
    run_ids: Iterable[Any],
    *,
    now: Any = None,
    lane_of: Callable[[str], Hashable] | None = None,
) -> dict[str, Decision]:
    """Budget decisions for runs about to be claimed, in admission order (inside the claim transaction).

    With ``lane_of`` (run id -> lane key) admission is strict head-of-line per lane: once a run is refused,
    the later runs of its lane get **no decision** and nothing is reserved for them, so every admitted run
    is one the caller claims (a reserved-but-unclaimed pending run would otherwise hold a stale reservation).
    """
    ids = [str(i) for i in run_ids]
    if not ids:
        return {}
    now = now or timezone.now()
    runs = {
        str(r.pk): r
        for r in GenerationRun.objects.filter(pk__in=ids).select_related(
            "cell",
            "model",
            "manifest",
            "resource_policy",
            "study__resource_policy",
            "study__active_plan",
        )
    }
    study_ids = sorted({str(r.study_id) for r in runs.values() if r.study_id})
    states: dict[str, _StudyState] = {}
    if study_ids:
        for study in (
            Study.objects.select_for_update(of=("self",))
            .select_related("resource_policy", "active_plan")
            .filter(pk__in=study_ids)
            .order_by("pk")
        ):
            states[str(study.pk)] = _state(study)
    decisions: dict[str, Decision] = {}
    stopped: set[Hashable] = set()
    for run_id in ids:
        lane = lane_of(run_id) if lane_of is not None else None
        if lane is not None and lane in stopped:
            continue
        run = runs.get(run_id)
        if run is None:
            decision = Decision(run_id, ok=False, reason="gone")
        else:
            state = states.get(str(run.study_id)) if run.study_id else None
            decision = _admit_one(run, state, now)
        decisions[run_id] = decision
        if not decision.ok and lane is not None:
            stopped.add(lane)
    return decisions


def _admit_one(run: GenerationRun, state: _StudyState | None, now: Any) -> Decision:  # noqa: PLR0911
    a = ledger.attempt_no(run)
    need, tokens = ledger.run_need(run)
    if state is not None:
        study = state.study
        if study.status not in _CLAIMABLE:
            return Decision(run.pk, ok=False, reason=study.pause_reason or BUDGET, detail=f"study {study.status}")
        if study.stop_reason and run.attempt > 0:
            # A stopping study does not start retries it has no reserved budget for.
            exclude_runs([run], ExclusionReason.BUDGET_STOP)
            return Decision(run.pk, ok=False, reason=BUDGET_STOPPED, detail="study stopping (budget)")
    held = ledger.reservation(run.pk, a)
    if held > 0:
        # The attempt already holds its reservation (admitted earlier, then not claimed): it is already in
        # the study's exposure and attempt count, so admitting it again must not reserve a second time.
        return Decision(run.pk, ok=True, reserved=held)
    cap, warnings = cap_check(run, need, tokens, now=now)
    if cap is not None:
        reason = _cap_reason(cap)
        if state is not None:
            pause_study(state.study, reason)
        return Decision(run.pk, ok=False, reason=reason, detail=str(cap), warnings=warnings)
    if state is None:
        # Ad-hoc run: only spend caps apply; reserve so the user's caps see it while it runs.
        ledger.reserve(run, attempt=a, amount_usd=need, tokens=tokens)
        return Decision(run.pk, ok=True, reserved=need, warnings=warnings)

    cell = run.cell
    from_block = False
    if cell is not None and run.attempt == 0:
        remainder, _tokens, started = ledger.block_remainder(run.study_id, cell.stage, cell.block_index)
        if not started:
            members = _block_runs(run) or [run]
            if run.pk not in {m.pk for m in members}:
                members.append(run)
            needs = {m.pk: ledger.run_need(m) for m in members}
            block_usd = sum((n[0] for n in needs.values()), ZERO)
            block_tokens = sum(n[1] for n in needs.values())
            over = state.over(block_usd, block_tokens, len(members), now)
            if over:
                apply_budget_exhausted(state.study, over)
                return Decision(run.pk, ok=False, reason=BUDGET, detail=over, warnings=warnings)
            ledger.reserve_block(
                members,
                needs={pk: n[0] for pk, n in needs.items()},
                tokens={pk: n[1] for pk, n in needs.items()},
            )
            state.exposure += block_usd
            state.exposure_tokens += block_tokens
            remainder = block_usd
        from_block = remainder >= need
    cell_cap = _cell_cap(run)
    if cell is not None and cell_cap is not None and ledger.to_decimal(cell.spent_usd) + need > cell_cap:
        if from_block:
            ledger.release_block_slice(run, need=need, tokens=tokens)
            state.exposure -= need
            state.exposure_tokens -= tokens
        exclude_runs([run], ExclusionReason.CELL_BUDGET)
        return Decision(
            run.pk,
            ok=False,
            reason=CELL_BUDGET,
            detail=f"cell {cell.pk}: spent {cell.spent_usd} + {need} > {cell_cap}",
        )
    if from_block:
        ledger.move_from_block(run, attempt=a, need=need, tokens=tokens)
    else:
        over = state.over(need, tokens, 1, now)
        if over:
            apply_budget_exhausted(state.study, over)
            if run.attempt > 0 or state.study.stop_reason:
                exclude_runs([run], ExclusionReason.BUDGET_STOP)
            return Decision(run.pk, ok=False, reason=BUDGET, detail=over, warnings=warnings)
        ledger.reserve(run, attempt=a, amount_usd=need, tokens=tokens)
        state.exposure += need
        state.exposure_tokens += tokens
    state.attempts += 1
    return Decision(run.pk, ok=True, reserved=need, warnings=warnings)


def _cell_cap(run: GenerationRun) -> Decimal | None:
    cell = run.cell
    if cell is None:
        return None
    if cell.cell_cap_usd and cell.cell_cap_usd > 0:
        return ledger.to_decimal(cell.cell_cap_usd)
    return ledger.to_decimal(ledger.run_policy(run).cell.max_cost_usd)


def resume_paused_for_caps(*, now: Any = None) -> int:
    """Resume studies paused for ``quota`` / ``spend_cap`` whose next run fits every cap again (reconciler).

    The study goes back to ``running`` when it has main-stage cells, else to ``piloting``.
    """
    resumed = 0
    for study in Study.objects.filter(status="paused", pause_reason__in=[QUOTA, SPEND_CAP]):
        head = (
            GenerationRun.objects.filter(study=study, status=RunStatus.PENDING, queued_at__isnull=False)
            .select_related("cell", "model", "manifest", "resource_policy", "study__resource_policy")
            .order_by("sequence", "queued_at")
            .first()
        )
        if head is None:
            continue
        need, tokens = ledger.run_need(head)
        cap, _warnings = cap_check(head, need, tokens, now=now)
        if cap is not None:
            continue
        status = "running" if StudyCell.objects.filter(study=study, stage="main").exists() else "piloting"
        with transaction.atomic():
            if Study.objects.filter(pk=study.pk, status="paused", pause_reason=study.pause_reason).update(
                status=status,
                pause_reason="",
                updated_at=timezone.now(),
            ):
                _publish_after_commit(study.pk, {"type": "state", "status": status, "pause_reason": ""})
                resumed += 1
    return resumed
