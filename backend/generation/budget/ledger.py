"""Reservations, usage and settlement on ``SpendLedgerEntry`` (idempotent through ``dedupe_key``; section 4.5).

The ledger is append-only. Every write carries a ``dedupe_key``, so a retried or repeated write is a no-op:

==========================================  =======================================================
``res:{run}:{a}``                           reserve: the run reservation of attempt ``a``
``rel:{run}:{a}``                           release: the end of that reservation (negative)
``blk:{study}:{stage}:{block}:r``           reserve: a whole replicate block (``run`` empty)
``blk:{study}:{stage}:{block}:mv:{run}:{a}`` release: the slice moved from the block to a run
``blk:{study}:{stage}:{block}:x:{cell}``    release: the slice of a cell excluded before its claim
``blk:{study}:{stage}:{block}:close``       release: what is left of a finished block
``live:{run}:{a}:{seq}``                    usage: one live-poll delta (``budget.live.poll``)
``proxy:{llm_request_id}``                  usage: one completed local-proxy request
``adj:{run}:{a}``                           adjust: settled final cost minus the live deltas
==========================================  =======================================================

Attempts are numbered from 1: ``attempt_no(run) = run.attempt + 1`` (``run.attempt`` is the executor's
0-based retry counter). Reservations are written in the study's cap basis and mirrored into both amount
columns; usage rows carry billed (``amount_usd``) and list (``list_cost_usd``) cost separately.

Totals (3.10): ``spent = Σ usage + Σ adjust`` in the cap basis and ``open_reserved = Σ reserve + Σ
release``. Admission uses the tighter **exposure** ``spent + Σ_open-attempts max(0, reserved - used) +
Σ open blocks``: a running attempt's live usage is never counted twice. The invariant (tested) is
``exposure <= study cap`` at every instant.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Q
from django.db.models import Sum
from django.utils import timezone

from backend.generation.contracts.resources import ResourcePolicyContent
from backend.generation.contracts.resources import preset_content
from backend.generation.models import GenerationRun
from backend.generation.models import SpendLedgerEntry
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models.choices import LedgerKind
from backend.generation.models.choices import LedgerSource

__all__ = [
    "StudyTotals",
    "attempt_no",
    "block_key",
    "block_remainder",
    "close_block",
    "exposure",
    "final_usage",
    "reconcile",
    "record_usage",
    "refresh_caches",
    "release",
    "reservation",
    "reserve",
    "reserve_block",
    "run_policy",
    "settle_attempt",
    "study_totals",
    "to_decimal",
]

logger = logging.getLogger(__name__)

ZERO = Decimal(0)
_QUANT = Decimal("0.00000001")
USAGE_KINDS = (LedgerKind.USAGE, LedgerKind.ADJUST)
RESERVE_KINDS = (LedgerKind.RESERVE, LedgerKind.RELEASE)
_SPENT_Q = Q(kind__in=USAGE_KINDS)
_RESERVED_Q = Q(kind__in=RESERVE_KINDS)


def to_decimal(value: Any) -> Decimal:
    """USD as an 8-decimal ``Decimal`` (floats go through ``repr`` so 0.1 stays 0.1)."""
    if isinstance(value, Decimal):
        return value.quantize(_QUANT)
    return Decimal(repr(float(value or 0))).quantize(_QUANT)


def attempt_no(run: GenerationRun) -> int:
    """The 1-based attempt number of the run's current attempt (ledger, gateway config, evidence dir)."""
    return int(run.attempt or 0) + 1


def block_key(study_id: Any, stage: str, block_index: int) -> str:
    return f"blk:{study_id}:{stage}:{block_index}"


# =====================================================================================
# Policy of a run
# =====================================================================================


def run_policy(run: GenerationRun) -> ResourcePolicyContent:
    """The effective policy content a run was frozen with (manifest), else its preset, else the study's.

    A run without any policy (an ad-hoc test row) gets the frugal preset, the default for new studies.
    """
    manifest = getattr(run, "manifest", None) if _has_manifest(run) else None
    effective = ((manifest.manifest or {}).get("resources") or {}).get("effective") if manifest is not None else None
    if isinstance(effective, Mapping) and effective:
        content = effective.get("content", effective)
        try:
            return ResourcePolicyContent.model_validate(content)
        except Exception:  # noqa: BLE001 — a partial effective block falls back to the preset
            logger.debug("run %s: manifest resources.effective is not a full policy", run.pk)
    if run.resource_policy_id:
        return ResourcePolicyContent.model_validate(run.resource_policy.content)
    if run.study_id and run.study.resource_policy_id:
        return ResourcePolicyContent.model_validate(run.study.resource_policy.content)
    return preset_content("frugal")


def _has_manifest(run: GenerationRun) -> bool:
    try:
        return run.manifest is not None
    except Exception:  # noqa: BLE001 — RelatedObjectDoesNotExist
        return False


# =====================================================================================
# Writes
# =====================================================================================


def _write(**fields: Any) -> SpendLedgerEntry:
    """Create one row; an existing ``dedupe_key`` returns the stored row unchanged (idempotent)."""
    key = fields["dedupe_key"]
    existing = SpendLedgerEntry.objects.filter(dedupe_key=key).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return SpendLedgerEntry.objects.create(**fields)
    except IntegrityError:
        stored = SpendLedgerEntry.objects.filter(dedupe_key=key).first()
        if stored is None:
            raise
        return stored


def run_endpoint_id(run: GenerationRun) -> int | None:
    """The run's endpoint; NULL on the run means the system OpenRouter row (recorded explicitly here)."""
    if run.endpoint_id:
        return run.endpoint_id
    if run.model_id and run.model.endpoint_id:
        return run.model.endpoint_id
    try:
        from backend.inference.access import system_openrouter

        return system_openrouter().pk
    except Exception:  # noqa: BLE001 — accounting never fails a claim over the endpoint column
        return None


def _run_fields(run: GenerationRun) -> dict[str, Any]:
    return {
        "study_id": run.study_id,
        "run_id": run.pk,
        "user_id": run.created_by_id or (run.study.created_by_id if run.study_id else None),
        "endpoint_id": run_endpoint_id(run),
        "model_slug": run.model.model_id if run.model_id else "",
    }


def reserve(
    run: GenerationRun,
    *,
    attempt: int,
    amount_usd: Decimal | float,
    tokens: int = 0,
    source: str = LedgerSource.SCHEDULER,
) -> SpendLedgerEntry:
    """Reserve ``amount_usd`` (and ``tokens``) for one attempt: ``res:{run}:{a}``."""
    amount = to_decimal(amount_usd)
    return _write(
        kind=LedgerKind.RESERVE,
        attempt=attempt,
        amount_usd=amount,
        list_cost_usd=amount,
        tokens_in=int(tokens),
        source=source,
        dedupe_key=f"res:{run.pk}:{attempt}",
        **_run_fields(run),
    )


def reservation(run_id: Any, attempt: int) -> Decimal:
    """The open reservation of one attempt (``Σ reserve + Σ release``; 0 when none or released)."""
    total = SpendLedgerEntry.objects.filter(run_id=run_id, attempt=attempt, kind__in=RESERVE_KINDS).aggregate(
        s=Sum("amount_usd"),
    )["s"]
    return total or ZERO


def reserve_block(
    runs: list[GenerationRun],
    *,
    needs: Mapping[Any, Decimal] | None = None,
    tokens: Mapping[Any, int] | None = None,
) -> list[SpendLedgerEntry]:
    """Reserve one replicate block (all of ``runs`` share study, stage and block): ``blk:...:r``.

    ``needs`` gives each run's need (default ``min(run.max_cost_usd, cell.cap_usd)``). Returns the block
    reservation row (one element), or ``[]`` for no runs.
    """
    if not runs:
        return []
    first = runs[0]
    cell = first.cell
    amount = ZERO
    token_total = 0
    for run in runs:
        amount += needs[run.pk] if needs is not None else run_need(run)[0]
        token_total += tokens[run.pk] if tokens is not None else run_need(run)[1]
    return [
        _write(
            kind=LedgerKind.RESERVE,
            study_id=first.study_id,
            run_id=None,
            attempt=None,
            user_id=first.created_by_id,
            amount_usd=amount,
            list_cost_usd=amount,
            tokens_in=token_total,
            source=LedgerSource.SCHEDULER,
            dedupe_key=f"{block_key(first.study_id, cell.stage, cell.block_index)}:r",
        ),
    ]


def run_need(run: GenerationRun) -> tuple[Decimal, int]:
    """``(USD, tokens)`` one attempt reserves: ``min(run.max_cost_usd, cell.cap_usd)`` and the run token cap."""
    policy = run_policy(run)
    need = to_decimal(policy.run.max_cost_usd)
    cell = run.cell if run.cell_id else None
    if cell is not None and cell.cap_usd and cell.cap_usd > 0:
        need = min(need, to_decimal(cell.cap_usd))
    return need, int(policy.run.max_total_tokens)


def release(entry: SpendLedgerEntry, *, dedupe_key: str | None = None) -> SpendLedgerEntry:
    """Release one reservation entry in full (a negative ``release`` row)."""
    return _write(
        kind=LedgerKind.RELEASE,
        study_id=entry.study_id,
        run_id=entry.run_id,
        attempt=entry.attempt,
        user_id=entry.user_id,
        endpoint_id=entry.endpoint_id,
        model_slug=entry.model_slug,
        amount_usd=-entry.amount_usd,
        list_cost_usd=-entry.list_cost_usd,
        tokens_in=-entry.tokens_in,
        source=entry.source,
        dedupe_key=dedupe_key
        or (f"rel:{entry.run_id}:{entry.attempt}" if entry.run_id else f"{entry.dedupe_key}:rel"),
    )


def block_remainder(study_id: Any, stage: str, block_index: int) -> tuple[Decimal, int, bool]:
    """``(USD, tokens, exists)`` still held by a block reservation."""
    prefix = f"{block_key(study_id, stage, block_index)}:"
    rows = SpendLedgerEntry.objects.filter(study_id=study_id, run__isnull=True, dedupe_key__startswith=prefix)
    agg = rows.aggregate(s=Sum("amount_usd"), t=Sum("tokens_in"))
    exists = rows.filter(kind=LedgerKind.RESERVE).exists()
    return agg["s"] or ZERO, int(agg["t"] or 0), exists


def move_from_block(run: GenerationRun, *, attempt: int, need: Decimal, tokens: int) -> SpendLedgerEntry:
    """Move ``need`` from the run's block reservation to the run (``mv`` release + ``res`` reserve)."""
    cell = run.cell
    _write(
        kind=LedgerKind.RELEASE,
        study_id=run.study_id,
        run_id=None,
        attempt=None,
        user_id=run.created_by_id,
        amount_usd=-need,
        list_cost_usd=-need,
        tokens_in=-tokens,
        source=LedgerSource.SCHEDULER,
        dedupe_key=f"{block_key(run.study_id, cell.stage, cell.block_index)}:mv:{run.pk}:{attempt}",
    )
    return reserve(run, attempt=attempt, amount_usd=need, tokens=tokens)


def release_block_slice(run: GenerationRun, *, need: Decimal, tokens: int) -> None:
    """Give back the slice of a cell that is excluded before it was claimed."""
    cell = run.cell
    _write(
        kind=LedgerKind.RELEASE,
        study_id=run.study_id,
        run_id=None,
        attempt=None,
        user_id=run.created_by_id,
        amount_usd=-need,
        list_cost_usd=-need,
        tokens_in=-tokens,
        source=LedgerSource.SCHEDULER,
        dedupe_key=f"{block_key(run.study_id, cell.stage, cell.block_index)}:x:{cell.pk}",
    )


def close_block(study_id: Any, stage: str, block_index: int, *, user_id: Any) -> SpendLedgerEntry | None:
    """Release what is left of a block reservation (``...:close``); ``None`` when nothing is left."""
    remainder, tokens, exists = block_remainder(study_id, stage, block_index)
    if not exists or (remainder <= 0 and tokens <= 0):
        return None
    return _write(
        kind=LedgerKind.RELEASE,
        study_id=study_id,
        run_id=None,
        attempt=None,
        user_id=user_id,
        amount_usd=-remainder,
        list_cost_usd=-remainder,
        tokens_in=-tokens,
        source=LedgerSource.SCHEDULER,
        dedupe_key=f"{block_key(study_id, stage, block_index)}:close",
    )


def record_usage(
    run: GenerationRun,
    *,
    attempt: int,
    source: str,
    amount_usd: Decimal | float,
    list_cost_usd: Decimal | float,
    tokens_in: int,
    tokens_out: int,
    requests: int,
    dedupe_key: str,
    kind: str = LedgerKind.USAGE,
    refresh: bool = True,
) -> SpendLedgerEntry:
    """Append one usage row (idempotent by ``dedupe_key``) and refresh the study and cell caches."""
    entry = _write(
        kind=kind,
        attempt=attempt,
        amount_usd=to_decimal(amount_usd),
        list_cost_usd=to_decimal(list_cost_usd),
        tokens_in=int(tokens_in),
        tokens_out=int(tokens_out),
        requests=int(requests),
        source=source,
        dedupe_key=dedupe_key,
        **_run_fields(run),
    )
    if refresh:
        refresh_caches(run.study_id, cell_ids=[run.cell_id] if run.cell_id else ())
    return entry


# =====================================================================================
# Totals and caches
# =====================================================================================


@dataclass(frozen=True)
class StudyTotals:
    spent_billed: Decimal
    spent_list: Decimal
    open_reserved: Decimal  # Σ reserve + Σ release (spec 3.10)
    held: Decimal  # what reservations still hold beyond recorded usage (exposure - spent)
    spent_tokens: int
    held_tokens: int
    cap_basis: str

    @property
    def spent(self) -> Decimal:
        return self.spent_list if self.cap_basis == "list" else self.spent_billed

    @property
    def exposure(self) -> Decimal:
        return self.spent + self.held

    @property
    def exposure_tokens(self) -> int:
        return self.spent_tokens + self.held_tokens


def _rows(filters: Q) -> list[dict[str, Any]]:
    return list(
        SpendLedgerEntry.objects.filter(filters)
        .values("kind", "run_id", "attempt", "dedupe_key")
        .annotate(
            usd=Sum("amount_usd"),
            lst=Sum("list_cost_usd"),
            tin=Sum("tokens_in"),
            tout=Sum("tokens_out"),
        ),
    )


def _totals(filters: Q, cap_basis: str) -> StudyTotals:
    spent_billed = spent_list = open_reserved = ZERO
    spent_tokens = 0
    reserved: dict[tuple[Any, Any], Decimal] = defaultdict(lambda: ZERO)
    reserved_tokens: dict[tuple[Any, Any], int] = defaultdict(int)
    used: dict[tuple[Any, Any], Decimal] = defaultdict(lambda: ZERO)
    used_tokens: dict[tuple[Any, Any], int] = defaultdict(int)
    block_usd = ZERO
    block_tokens = 0
    for row in _rows(filters):
        usd, lst = row["usd"] or ZERO, row["lst"] or ZERO
        tin, tout = int(row["tin"] or 0), int(row["tout"] or 0)
        key = (row["run_id"], row["attempt"])
        if row["kind"] in USAGE_KINDS:
            spent_billed += usd
            spent_list += lst
            spent_tokens += tin + tout
            used[key] += lst if cap_basis == "list" else usd
            used_tokens[key] += tin + tout
        else:
            open_reserved += usd
            if row["run_id"] is None:
                block_usd += usd
                block_tokens += tin
            else:
                reserved[key] += usd
                reserved_tokens[key] += tin
    held = block_usd + sum((max(ZERO, r - used[key]) for key, r in reserved.items() if r > 0), ZERO)
    held_tokens = block_tokens + sum(
        max(0, t - used_tokens[key]) for key, t in reserved_tokens.items() if reserved[key] > 0 or t > 0
    )
    return StudyTotals(spent_billed, spent_list, open_reserved, held, spent_tokens, held_tokens, cap_basis)


def study_totals(study: Study | Any) -> StudyTotals:
    """The ledger totals of a study (``cap_basis`` from the study row)."""
    study_id = getattr(study, "pk", study)
    basis = (
        getattr(study, "cap_basis", None)
        or Study.objects.filter(pk=study_id).values_list("cap_basis", flat=True).first()
    )
    return _totals(Q(study_id=study_id), basis or "billed")


def exposure(study: Study) -> StudyTotals:
    return study_totals(study)


def scope_totals(filters: Q, *, cap_basis: str = "billed") -> StudyTotals:
    """Totals over any ledger scope (a user, an endpoint and model, a time window)."""
    return _totals(filters, cap_basis)


def refresh_caches(study_id: Any, *, cell_ids: Iterable[Any] = ()) -> StudyTotals | None:
    """Rewrite ``Study.spent_*`` / ``reserved_*`` and the cells' ``spent_*`` from the ledger (authoritative)."""
    for cell_id in {c for c in cell_ids if c}:
        agg = SpendLedgerEntry.objects.filter(_SPENT_Q, run__cell_id=cell_id).aggregate(
            s=Sum("amount_usd"),
            lst=Sum("list_cost_usd"),
        )
        StudyCell.objects.filter(pk=cell_id).update(
            spent_usd=(agg["s"] or ZERO).quantize(Decimal("0.000001")),
            spent_list_usd=(agg["lst"] or ZERO).quantize(Decimal("0.000001")),
        )
    if not study_id:
        return None
    totals = study_totals(study_id)
    Study.objects.filter(pk=study_id).update(
        spent_usd=totals.spent_billed.quantize(Decimal("0.000001")),
        spent_list_usd=totals.spent_list.quantize(Decimal("0.000001")),
        reserved_usd=max(ZERO, totals.held).quantize(Decimal("0.000001")),
        spent_tokens=totals.spent_tokens,
        reserved_tokens=max(0, totals.held_tokens),
        updated_at=timezone.now(),
    )
    return totals


# =====================================================================================
# Settlement
# =====================================================================================


@dataclass(frozen=True)
class FinalUsage:
    cost: Decimal
    list_cost: Decimal
    tokens_in: int
    tokens_out: int
    requests: int
    source: str


def _evidence_dir(run: GenerationRun, attempt: int) -> Path:
    return Path(settings.GENERATION_RUNS_ROOT) / str(run.pk) / f"a{attempt}"


def _pricing(run: GenerationRun) -> tuple[float, float] | None:
    if not _has_manifest(run):
        return None
    pricing = ((run.manifest.manifest or {}).get("model") or {}).get("pricing") or {}
    try:
        return float(pricing["input_per_mtok"]), float(pricing["output_per_mtok"])
    except (KeyError, TypeError, ValueError):
        return None


def _from_calls(path: Path, pricing: tuple[float, float] | None) -> FinalUsage | None:
    if not path.is_file():
        return None
    cost = list_cost = ZERO
    tokens_in = tokens_out = requests = 0
    priced_from_table = False
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()  # noqa: PLW2901
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = record.get("usage")
            if not isinstance(usage, dict):
                continue  # a failed call without usage costs nothing billable
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            tokens_in += prompt
            tokens_out += completion
            requests += 1
            table = (prompt * pricing[0] + completion * pricing[1]) / 1_000_000 if pricing else None
            billed = usage.get("cost")
            accrued = record.get("cost")  # what the gateway charged toward the cap (estimate when unreported)
            if isinstance(billed, (int, float)) and not isinstance(billed, bool):
                cost += to_decimal(billed)
            elif isinstance(accrued, (int, float)) and not isinstance(accrued, bool):
                cost += to_decimal(accrued)
                priced_from_table = True
            elif table is not None:
                cost += to_decimal(table)
                priced_from_table = True
            listed = record.get("list_cost")
            if isinstance(listed, (int, float)) and not isinstance(listed, bool):
                list_cost += to_decimal(listed)
            elif table is not None:
                list_cost += to_decimal(table)
    source = LedgerSource.PRICE_TABLE if priced_from_table else LedgerSource.GATEWAY_FINAL
    return FinalUsage(cost, list_cost, tokens_in, tokens_out, requests, source)


def final_usage(run: GenerationRun, attempt: int, *, calls_path: Path | None = None) -> FinalUsage | None:
    """The settled usage of one attempt: ``calls.jsonl`` (billed ``usage.cost``, else the manifest price table),
    else ``RunAttempt.usage``, else the run's metrics (current attempt only); ``None`` when nothing recorded it."""
    from backend.generation.models import RunAttempt

    found = _from_calls(calls_path or _evidence_dir(run, attempt) / "calls.jsonl", _pricing(run))
    if found is not None:
        return found
    row = RunAttempt.objects.filter(run=run, attempt=attempt).values_list("usage", flat=True).first()
    if isinstance(row, dict) and row.get("cost_usd") is not None:
        requests = sum(v for v in (row.get("requests_by_class") or {}).values() if isinstance(v, int))
        return FinalUsage(
            to_decimal(row.get("cost_usd")),
            to_decimal(row.get("list_cost_usd") if row.get("list_cost_usd") is not None else row.get("cost_usd")),
            int(row.get("prompt_tokens") or 0),
            int(row.get("completion_tokens") or 0),
            requests,
            LedgerSource.GATEWAY_FINAL,
        )
    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    if attempt == attempt_no(run) and metrics.get("cost_usd") is not None:
        requests = sum(v for v in (metrics.get("requests_by_class") or {}).values() if isinstance(v, int))
        return FinalUsage(
            to_decimal(metrics.get("cost_usd")),
            to_decimal(
                metrics.get("list_cost_usd") if metrics.get("list_cost_usd") is not None else metrics["cost_usd"],
            ),
            int(metrics.get("prompt_tokens") or 0),
            int(metrics.get("completion_tokens") or 0),
            requests,
            LedgerSource.GATEWAY_FINAL,
        )
    return None


def settle_attempt(
    run: GenerationRun,
    *,
    attempt: int | None = None,
    calls_path: Path | None = None,
    final: FinalUsage | None = None,
) -> None:
    """Settle one attempt: ``adjust = final - Σ live`` and ``release = -reserve``; refresh the caches.

    Idempotent (``adj:`` / ``rel:`` keys). Without any final record the live deltas are the final usage
    (adjust 0). Afterwards the run's block is closed when every cell of it has finished.
    """
    a = attempt if attempt is not None else attempt_no(run)
    released = SpendLedgerEntry.objects.filter(dedupe_key=f"rel:{run.pk}:{a}").exists()
    if released and SpendLedgerEntry.objects.filter(dedupe_key=f"adj:{run.pk}:{a}").exists():
        return  # already settled
    before = study_totals(run.study_id) if run.study_id else None
    if final is None:
        final = final_usage(run, a, calls_path=calls_path)
    if (
        released
        and final is None
        and not SpendLedgerEntry.objects.filter(run=run, attempt=a, kind=LedgerKind.USAGE).exists()
    ):
        return  # released earlier and still nothing recorded: nothing to settle yet
    live = SpendLedgerEntry.objects.filter(run=run, attempt=a, kind__in=USAGE_KINDS).exclude(
        dedupe_key=f"adj:{run.pk}:{a}",
    )
    agg = live.aggregate(
        s=Sum("amount_usd"),
        lst=Sum("list_cost_usd"),
        tin=Sum("tokens_in"),
        tout=Sum("tokens_out"),
        n=Sum("requests"),
    )
    if final is not None:
        _write(
            kind=LedgerKind.ADJUST,
            attempt=a,
            amount_usd=final.cost - (agg["s"] or ZERO),
            list_cost_usd=final.list_cost - (agg["lst"] or ZERO),
            tokens_in=final.tokens_in - int(agg["tin"] or 0),
            tokens_out=final.tokens_out - int(agg["tout"] or 0),
            requests=final.requests - int(agg["n"] or 0),
            source=final.source,
            dedupe_key=f"adj:{run.pk}:{a}",
            **_run_fields(run),
        )
    held = SpendLedgerEntry.objects.filter(run=run, attempt=a, kind__in=RESERVE_KINDS).aggregate(
        s=Sum("amount_usd"),
        t=Sum("tokens_in"),
    )
    if (held["s"] or ZERO) > 0 or int(held["t"] or 0) > 0:
        _write(
            kind=LedgerKind.RELEASE,
            attempt=a,
            amount_usd=-(held["s"] or ZERO),
            list_cost_usd=-(held["s"] or ZERO),
            tokens_in=-int(held["t"] or 0),
            source=LedgerSource.SCHEDULER,
            dedupe_key=f"rel:{run.pk}:{a}",
            **_run_fields(run),
        )
    refresh_caches(run.study_id, cell_ids=[run.cell_id] if run.cell_id else ())
    if run.cell_id and run.study_id:
        maybe_close_block(run.cell)
    if final is not None and run.study_id:
        from backend.generation.budget.live import publish_spend

        delta = (
            final.list_cost - (agg["lst"] or ZERO)
            if before and before.cap_basis == "list"
            else final.cost - (agg["s"] or ZERO)
        )
        publish_spend(run, delta=float(delta), before=before)


_ACTIVE_STATUSES = ("pending", "running")


def maybe_close_block(cell: StudyCell) -> SpendLedgerEntry | None:
    """Close the cell's block reservation once no cell of the block has an active run."""
    active = GenerationRun.objects.filter(
        study_id=cell.study_id,
        cell__stage=cell.stage,
        cell__block_index=cell.block_index,
        status__in=_ACTIVE_STATUSES,
    ).filter(Q(status="pending") | Q(status="running", phase="running"))
    if active.exists():
        return None
    user_id = Study.objects.filter(pk=cell.study_id).values_list("created_by_id", flat=True).first()
    if user_id is None:
        user_id = GenerationRun.objects.filter(cell=cell).values_list("created_by_id", flat=True).first()
    if user_id is None:
        return None
    return close_block(cell.study_id, cell.stage, cell.block_index, user_id=user_id)


# =====================================================================================
# Reconciler (4.5 idempotence)
# =====================================================================================


def reconcile() -> dict[str, int]:
    """Settle ended attempts that still hold a reservation; close blocks whose cells all finished."""
    open_attempts = (
        SpendLedgerEntry.objects.filter(kind__in=RESERVE_KINDS, run__isnull=False)
        .values("run_id", "attempt")
        .annotate(s=Sum("amount_usd"), t=Sum("tokens_in"))
        .filter(Q(s__gt=0) | Q(t__gt=0))
    )
    settled = 0
    for row in open_attempts:
        run = GenerationRun.objects.select_related("cell", "model", "study").filter(pk=row["run_id"]).first()
        if run is None:
            continue
        current = attempt_no(run)
        in_generation = run.status == "running" and run.phase == "running" and row["attempt"] == current
        # A pending run whose open reservation is for its current attempt has not run yet (admitted, not yet
        # claimed): the reservation is the one its claim uses, so releasing it would claim it with nothing held.
        waiting = run.status == "pending" and row["attempt"] == current
        if in_generation or waiting:
            continue
        settle_attempt(run, attempt=row["attempt"])
        settled += 1
    closed = 0
    blocks = (
        SpendLedgerEntry.objects.filter(run__isnull=True, dedupe_key__startswith="blk:")
        .values_list("dedupe_key", "study_id")
        .distinct()
    )
    seen: set[tuple[Any, str, int]] = set()
    for key, study_id in blocks:
        try:
            _prefix, _sid, stage, index = key.split(":")[:4]
            block = (study_id, stage, int(index))
        except ValueError:
            continue
        if block in seen:
            continue
        seen.add(block)
        cell = StudyCell.objects.filter(study_id=study_id, stage=stage, block_index=int(index)).first()
        if cell is not None and maybe_close_block(cell) is not None:
            closed += 1
            refresh_caches(study_id)
    return {"settled": settled, "blocks_closed": closed}
