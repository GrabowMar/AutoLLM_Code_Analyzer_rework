"""Batch submission of slot calls (behind ``BATCH_BACKENDS_VERIFIED``; spec section 7.6).

``run_batch(runs)`` takes the pending single-shot cells of one (condition, target) that the scheduler claimed
together, renders their requests exactly as ``single_shot`` would (same body, same seed per cell; the
scheduler's claim holds every cell's reservation, so the batch is submitted only when all of them do), submits
them as one batch, polls every ``BATCH_POLL_S`` (60 s) and parses each answer with the single-shot code path
(one ``slot_call_0001`` artifact per cell, ``serving_tier=batch``). A batch that has not completed after
``BATCH_EXPIRY_S`` (24 h) ends every unanswered cell as ``infra_error:batch_expired``; it is not retried
automatically (a retry at the standard tier changes the condition and is a study decision).

The batch id is written to each run's ``execution.batch`` before polling, so a restarted executor resumes the
same batch instead of submitting it twice.

Backends: only OpenRouter's batch API is in scope, and only once its documentation is verified
(``BATCH_BACKENDS_VERIFIED``, default False). Until then ``auto_batch`` resolves to ``standard`` (note
``batch_unsupported``, budget/policy) and ``run_batch`` refuses. Tests pass a fake :class:`BatchBackend`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from django.conf import settings
from django.utils import timezone

from backend.generation.models import GenerationRun
from backend.generation.models import RunManifest
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunKind
from backend.generation.models.choices import RunStatus
from backend.generation.slot.executors import single_shot
from backend.generation.slot.part import part_of

if TYPE_CHECKING:
    from backend.generation.slot.executors.single_shot import SlotRequest

__all__ = [
    "BatchBackend",
    "BatchItem",
    "BatchStatus",
    "BatchUnsupportedError",
    "OpenRouterBatchBackend",
    "backends_verified",
    "run_batch",
]

EXPIRED = "batch_expired"


class BatchUnsupportedError(RuntimeError):
    """Batch serving is not available (backends unverified, or the runs cannot form one batch)."""


@dataclass(frozen=True)
class BatchItem:
    custom_id: str
    body: dict[str, Any]


@dataclass
class BatchStatus:
    """``status`` is ``in_progress``, ``completed``, ``failed`` or ``expired``; ``results`` maps custom ids to
    an OpenAI ``chat.completion`` dict or ``{"error": {...}}``."""

    status: str
    results: dict[str, dict[str, Any]] = field(default_factory=dict)


class BatchBackend(Protocol):
    name: str

    def submit(self, items: list[BatchItem]) -> str: ...

    def poll(self, provider_batch_ref: str) -> BatchStatus: ...


class OpenRouterBatchBackend:
    """OpenRouter batches (``POST /api/beta/batches``): disabled until its documentation is verified."""

    name = "openrouter"

    def submit(self, items: list[BatchItem]) -> str:
        del items
        msg = "OpenRouter batch submission is not verified (BATCH_BACKENDS_VERIFIED=False); use the standard tier"
        raise BatchUnsupportedError(msg)

    def poll(self, provider_batch_ref: str) -> BatchStatus:
        del provider_batch_ref
        msg = "OpenRouter batch polling is not verified (BATCH_BACKENDS_VERIFIED=False)"
        raise BatchUnsupportedError(msg)


def backends_verified() -> bool:
    return bool(getattr(settings, "BATCH_BACKENDS_VERIFIED", False))


def _poll_s() -> float:
    return float(getattr(settings, "BATCH_POLL_S", 60))


def _expiry_s() -> float:
    return float(getattr(settings, "BATCH_EXPIRY_S", 24 * 3600))


def _check_runs(runs: list[GenerationRun]) -> None:
    if not runs:
        msg = "an empty batch"
        raise BatchUnsupportedError(msg)
    keys = {(run.condition_id, run.slot_target_id) for run in runs}
    if len(keys) != 1:
        msg = "a batch holds the cells of exactly one (condition, target)"
        raise BatchUnsupportedError(msg)
    for run in runs:
        if run.kind != RunKind.SLOT_SINGLE_SHOT:
            msg = f"run {run.pk} is {run.kind}; only single-shot slot calls are batched"
            raise BatchUnsupportedError(msg)
        if run.status != RunStatus.RUNNING or not run.lease_owner:
            msg = f"run {run.pk} is not claimed (the claim holds its budget reservation)"
            raise BatchUnsupportedError(msg)


def _save_execution(run: GenerationRun, **values: Any) -> None:
    from backend.execution import transitions

    execution = dict(run.execution or {})
    execution.update(values)
    run.execution = execution
    transitions.guarded_save(run, ["execution"])


def _expire(run: GenerationRun) -> None:
    from backend.execution import transitions

    run.outcome, run.outcome_reason = Outcome.INFRA_ERROR, EXPIRED
    run.error_message = f"the batch did not complete within {int(_expiry_s())} s"
    transitions.finish_stage(run, transitions.OUTCOME_FAILED)


def run_batch(
    runs: list[GenerationRun],
    *,
    backend: BatchBackend | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, str]:
    """Submit, poll and record one provider batch; returns ``{run id: stage outcome}``."""
    from backend.execution import transitions

    if not backends_verified():
        msg = "batch backends are not verified (BATCH_BACKENDS_VERIFIED=False); auto_batch resolves to standard"
        raise BatchUnsupportedError(msg)
    _check_runs(runs)
    backend = backend or OpenRouterBatchBackend()
    outcomes: dict[str, str] = {}
    prepared: dict[str, tuple[GenerationRun, Any, dict[str, Any], SlotRequest]] = {}
    for run in runs:
        manifest = RunManifest.objects.get(run=run).manifest
        part = part_of(run.slot_target)  # type: ignore[arg-type]
        request = single_shot.build_request(run, manifest, single_shot._stimuli_texts(run), part)  # noqa: SLF001
        refusal = single_shot._precall_refusal(manifest, request)  # noqa: SLF001
        if refusal is not None:
            single_shot._exclude(run, refusal)  # noqa: SLF001
            outcomes[str(run.pk)] = transitions.OUTCOME_DONE
            continue
        prepared[str(run.pk)] = (run, part, manifest, request)
    if not prepared:
        return outcomes
    existing = {(run.execution or {}).get("batch", {}).get("id") for run, *_ in prepared.values()}
    existing.discard(None)
    if len(existing) == 1:
        provider_batch_ref = str(existing.pop())
    else:
        items = [BatchItem(custom_id=run_id, body=entry[3].body) for run_id, entry in sorted(prepared.items())]
        provider_batch_ref = backend.submit(items)
        submitted_at = timezone.now().isoformat()
        for run, *_ in prepared.values():
            _save_execution(
                run,
                batch={"id": provider_batch_ref, "backend": backend.name, "submitted_at": submitted_at},
            )
    started = clock()
    while True:
        status = backend.poll(provider_batch_ref)
        if status.status in {"completed", "failed", "expired"}:
            break
        if clock() - started >= _expiry_s():
            status = BatchStatus("expired", status.results)
            break
        sleep(_poll_s())
    for run_id, (run, part, manifest, request) in sorted(prepared.items()):
        answer = status.results.get(run_id)
        if answer is not None and "error" not in answer and answer.get("choices"):
            call = single_shot.LLMCall(response=answer, served={"backend": backend.name})
            outcome = single_shot.finalize_answer(run, part, manifest, request, call, serving_tier="batch")
            transitions.finish_stage(run, outcome)
            outcomes[run_id] = outcome
        elif status.status == "expired":
            _expire(run)
            outcomes[run_id] = EXPIRED
        else:
            error = (answer or {}).get("error") or {"message": f"batch {status.status} without an answer"}
            transitions.infra_retry(run, transitions.REASON_INFRA, error=str(error)[:500])
            outcomes[run_id] = transitions.OUTCOME_INFRA_INTERRUPTED
    return outcomes
