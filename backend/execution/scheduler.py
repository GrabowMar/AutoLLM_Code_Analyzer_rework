"""The claim algorithm (spec §C.4): capacity snapshot → evaluation claims → lane heads → admission → apply.

Runs on the leader only. Lanes are keyed by ``(study or "adhoc:<user>",
endpoint)`` and are strict head-of-line in their frozen order (``sequence``
for studies, enqueue order for ad-hoc runs), so a lane's run order is
reproducible; the scheduler is work-conserving *across* lanes. Fair share is
per user first, then per study; priority orders lanes. Residency K is
always enforced, with a starvation guard (drain-for-swap) so model affinity
never starves another lane for more than ``EXECUTOR_STARVATION_S``.

Runs of a study are claimed while the study is ``piloting`` or ``running``; its
``max_parallel`` caps its active runs and ``execution_options`` carries the
policy's concurrency knobs (``max_resident_models``, ``warmup``). Evaluation
is claimed by phase (``status=running, phase=eval_queued``), never by lane, so
``reference`` runs created in that state are claimed like any other.

Only the chosen rows are locked (``FOR UPDATE SKIP LOCKED``), then claimed
with one conditional UPDATE; rows a concurrent cancel or pause holds are
reconsidered on the next tick.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections import deque
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import connection
from django.db import transaction
from django.utils import timezone

from backend.execution import capacity
from backend.execution.capacity import CapacitySnapshot
from backend.execution.capacity import EffectiveSlots
from backend.execution.capacity import residency_key

logger = logging.getLogger(__name__)

# Blocked reasons (published in tick events and the queue API).
SANDBOX_FULL = "sandbox_full"
LLM_THREADS_FULL = "llm_threads_full"
ENDPOINT_OFFLINE = "endpoint_offline"
ENDPOINT_FULL = "endpoint_full"
MODEL_NOT_SERVED = "model_not_served"
RESIDENCY_WAIT = "residency_wait"
WARMING_MODEL = "warming_model"
STUDY_CAP = "study_cap"
CIRCUIT_OPEN = "circuit_open"
DRAINING = "draining"
RETRY_BACKOFF = "retry_backoff"

MAX_LANE_DEPTH = 64
WARMUP_VALID_S = 600  # a finished warm-up admits heads of its model for this long

# Study statuses whose runs may be claimed (a pilot runs its pilot cells).
CLAIMABLE_STUDY_STATUSES = ("piloting", "running")

_LANE_SQL = """
SELECT * FROM (
  SELECT j.id, j.lane, coalesce(j.endpoint_id, %(system_ep)s) AS ep, j.model_id, j.study_id, j.sequence,
         j.priority, j.queued_at, j.created_by_id, j.attempt, j.condition_id, j.cell_id, j.task_version_id,
         j.slot_target_id, j.repeat_index, j.pinned_served_name AS served_name, j.pinned_digest AS snapshot_digest,
         x.launched_at, x.max_parallel AS study_max_parallel, x.execution_options, x.scheduling,
         row_number() OVER (
           PARTITION BY coalesce(j.study_id::text, 'adhoc:' || j.created_by_id::text),
                        coalesce(j.endpoint_id, %(system_ep)s)
           ORDER BY CASE WHEN j.study_id IS NULL THEN NULL ELSE j.sequence END NULLS LAST,
                    j.queued_at, j.sequence NULLS LAST, j.id
         ) AS rn
  FROM generation_run j LEFT JOIN generation_study x ON x.id = j.study_id
  WHERE j.status = 'pending' AND j.queued_at IS NOT NULL
    AND (j.run_after IS NULL OR j.run_after <= now())
    AND (j.study_id IS NULL OR x.status IN ('piloting', 'running'))
    %(extra)s
) t WHERE rn <= %(depth)s
ORDER BY rn
"""

# Both claims return the same columns, in the order Claimed.from_row reads them.
_EVAL_CLAIM_SQL = """
WITH c AS (
  SELECT id FROM generation_run
  WHERE status = 'running' AND phase = 'eval_queued'
  ORDER BY priority DESC, stage_entered_at NULLS FIRST, id
  LIMIT %s FOR UPDATE SKIP LOCKED
)
UPDATE generation_run j
SET phase = 'evaluating', lease_owner = %s, lease_expires_at = now() + %s * interval '1 second',
    heartbeat_at = now(), stage_entered_at = now(), updated_at = now()
FROM c WHERE j.id = c.id
RETURNING j.id, j.attempt, j.lane, j.study_id, j.endpoint_id, j.condition_id, j.cell_id, j.repeat_index
"""

_LOCK_SQL = """
SELECT j.id FROM generation_run j
WHERE j.id = ANY(%s::uuid[]) AND j.status = 'pending' AND j.queued_at IS NOT NULL
  AND (j.study_id IS NULL OR EXISTS (
        SELECT 1 FROM generation_study x WHERE x.id = j.study_id AND x.status IN ('piloting', 'running')))
ORDER BY j.id
FOR UPDATE OF j SKIP LOCKED
"""

_CLAIM_SQL = """
UPDATE generation_run j
SET status = 'running', phase = 'running', started_at = now(), lease_owner = %(owner)s,
    lease_expires_at = now() + %(lease)s * interval '1 second', heartbeat_at = now(), stage_entered_at = now(),
    updated_at = now(),
    execution = coalesce(j.execution, '{}'::jsonb)
      || jsonb_build_object(
           'started_seq', nextval('execution_start_seq'),
           'queued_at', j.queued_at,
           'claimed_at', now(),
           'queue_seconds', round(extract(epoch FROM now() - (
             CASE WHEN j.attempt > 0
                  -- a retried attempt waits from when it became eligible again (requeue + backoff),
                  -- not from the first attempt's queued_at
                  THEN greatest(coalesce((j.execution->>'requeued_at')::timestamptz, j.queued_at),
                                coalesce(j.run_after, j.queued_at))
                  ELSE j.queued_at END))::numeric, 2),
           'executor', %(owner)s::text)
      || coalesce((%(extras)s::jsonb) -> (j.id::text), '{}'::jsonb)
WHERE j.id = ANY(%(ids)s::uuid[])
RETURNING j.id, j.attempt, j.lane, j.study_id, j.endpoint_id, j.condition_id, j.cell_id, j.repeat_index
"""


@dataclass
class LaneRow:
    id: Any
    lane: str
    ep: int
    model_id: int | None
    study_id: Any
    sequence: int | None
    priority: int
    queued_at: datetime
    user_id: int
    attempt: int
    condition_id: int | None
    cell_id: int | None
    task_version_id: int | None
    slot_target_id: Any
    repeat_index: int | None
    served_name: str
    digest: str
    launched_at: datetime | None
    study_max_parallel: int | None
    execution_options: dict[str, Any]
    scheduling: str | None = None

    @property
    def lane_key(self) -> tuple[str, int]:
        owner = str(self.study_id) if self.study_id else f"adhoc:{self.user_id}"
        return (owner, self.ep)

    @property
    def model_key(self) -> str:
        return residency_key(self.served_name, self.model_id)

    @property
    def warmup_enabled(self) -> bool:
        options = self.execution_options if isinstance(self.execution_options, dict) else {}
        return bool(options.get("warmup", True))


@dataclass
class Claimed:
    run_id: Any
    lane: str  # "sandbox" | "llm" | "eval"
    attempt: int
    study_id: Any = None
    endpoint_id: int | None = None
    condition_id: int | None = None
    cell_id: int | None = None
    repeat_index: int | None = None

    @classmethod
    def from_row(cls, row: tuple, lane: str | None = None) -> Claimed:
        """From a claim's RETURNING row (``id, attempt, lane, study_id, endpoint_id, condition_id, cell_id,
        repeat_index``)."""
        return cls(
            run_id=row[0],
            attempt=row[1],
            lane=lane or row[2],
            study_id=row[3],
            endpoint_id=row[4],
            condition_id=row[5],
            cell_id=row[6],
            repeat_index=row[7],
        )


@dataclass
class LaneState:
    key: tuple[str, int]
    study_id: Any
    user_id: int
    endpoint_id: int
    pending_in_window: int
    head_run: Any
    reason: str | None = None


@dataclass
class TickResult:
    claimed: list[Claimed] = field(default_factory=list)
    preview_builds: list[Any] = field(default_factory=list)
    lanes: dict[tuple[str, int], LaneState] = field(default_factory=dict)
    admitted_order: list[Any] = field(default_factory=list)  # dry runs: run ids in admission order
    snapshot: CapacitySnapshot | None = None

    def blocked(self) -> dict[tuple[str, int], str]:
        return {k: s.reason for k, s in self.lanes.items() if s.reason}

    def endpoint_reasons(self) -> dict[int, str]:
        """One reason per endpoint: the most common blocked reason among its lanes."""
        counts: dict[int, Counter] = {}
        for state in self.lanes.values():
            if state.reason:
                counts.setdefault(state.endpoint_id, Counter())[state.reason] += 1
        return {ep: c.most_common(1)[0][0] for ep, c in counts.items()}


def lane_key_str(key: tuple[str, int]) -> str:
    return f"{key[0]}|{key[1]}"


def read_lane_heads(system_ep: int, depth: int, *, user_id: int | None = None) -> list[LaneRow]:
    """§C.4 step 3: the first ``depth`` pending runs of every lane (no locks)."""
    params: dict[str, Any] = {"system_ep": system_ep, "depth": int(depth)}
    if user_id is not None:
        sql = _LANE_SQL.replace("%(extra)s", "AND j.created_by_id = %(user_id)s")
        params["user_id"] = user_id
    else:
        sql = _LANE_SQL.replace("%(extra)s", "")
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c.name for c in cursor.description]
        rows = [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]
    out = []
    for row in rows:
        options = row["execution_options"]
        if isinstance(options, str):
            options = json.loads(options)
        out.append(
            LaneRow(
                id=row["id"],
                lane=row["lane"],
                ep=row["ep"],
                model_id=row["model_id"],
                study_id=row["study_id"],
                sequence=row["sequence"],
                priority=row["priority"],
                queued_at=row["queued_at"],
                user_id=row["created_by_id"],
                attempt=row["attempt"],
                condition_id=row["condition_id"],
                cell_id=row["cell_id"],
                task_version_id=row["task_version_id"],
                slot_target_id=row["slot_target_id"],
                repeat_index=row["repeat_index"],
                served_name=row["served_name"] or "",
                digest=row["snapshot_digest"] or "",
                launched_at=row["launched_at"],
                study_max_parallel=row["study_max_parallel"],
                execution_options=options or {},
                scheduling=row["scheduling"],
            ),
        )
    return out


class Scheduler:
    """Leader-side admission with the in-memory state the spec keeps between ticks."""

    def __init__(
        self,
        executor_id: str,
        *,
        breaker=None,
        lease_seconds: int | None = None,
        starvation_s: float | None = None,
    ) -> None:
        from backend.execution.breaker import breaker as default_breaker

        self.executor_id = executor_id
        self.breaker = breaker or default_breaker
        self.lease_seconds = int(lease_seconds or settings.EXECUTOR_LEASE_SECONDS)
        self.starvation_s = float(settings.EXECUTOR_STARVATION_S if starvation_s is None else starvation_s)
        self.residency_wait_since: dict[tuple[str, int], datetime] = {}
        self.last: TickResult = TickResult()

    # ── public ─────────────────────────────────────────────────────────

    def tick(
        self,
        slots: EffectiveSlots,
        pool_free: dict[str, int],
        *,
        dry_run: bool = False,
        open_circuits: set[int] | None = None,
        user_id: int | None = None,
    ) -> TickResult:
        """One scheduling pass. ``pool_free`` = really free worker threads per pool (sandbox / eval / llm).

        ``dry_run`` simulates admission (no locks, no writes, no warm-ups): the
        queue API uses it.
        """
        snap = capacity.snapshot()
        result = TickResult(snapshot=snap)
        if not dry_run and not slots.draining:
            eval_free = min(slots.eval_slots - snap.eval_used, pool_free.get("eval", 0))
            if slots.exclusive_eval and snap.sandbox_used:
                eval_free = 0  # sandbox and evaluation take turns (memory rule)
            if slots.defer_post_runs and pool_free.get("post", slots.post_run_slots) < slots.post_run_slots:
                eval_free = 0
            if eval_free > 0:
                evaluations = self._claim_evaluations(eval_free)
                snap.eval_used += len(evaluations)
                result.claimed.extend(evaluations)
                remaining = eval_free - len(evaluations)
                if remaining:
                    from backend.execution.runtime_builds import claim

                    result.preview_builds = claim(self.executor_id, remaining)
                    snap.eval_used += len(result.preview_builds)
        # One past the largest per-tick admission, so a lane that filled its endpoint still reports why it waits.
        depth = min(MAX_LANE_DEPTH, max([s.job_cap for s in snap.endpoints.values()] + [1]) + 1)
        rows = read_lane_heads(snap.system_ep, depth, user_id=user_id)
        chosen, extras = self._select(snap, rows, slots, pool_free, result, dry_run, open_circuits)
        if dry_run:
            result.admitted_order = [row.id for row in chosen]
        elif chosen:
            result.claimed.extend(self._apply(chosen, extras))
        if not dry_run:
            self.last = result
        return result

    # ── step 2: evaluations first ──────────────────────────────────────

    def _claim_evaluations(self, limit: int) -> list[Claimed]:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(_EVAL_CLAIM_SQL, [limit, self.executor_id, self.lease_seconds])
            rows = cursor.fetchall()
        return [Claimed.from_row(r, lane="eval") for r in rows]

    # ── step 4: selection ──────────────────────────────────────────────

    def _select(
        self,
        snap: CapacitySnapshot,
        rows: list[LaneRow],
        slots: EffectiveSlots,
        pool_free: dict[str, int],
        result: TickResult,
        dry_run: bool,
        open_circuits: set[int] | None,
    ) -> tuple[list[LaneRow], dict[str, dict[str, Any]]]:
        now = timezone.now()
        lanes: dict[tuple[str, int], deque[LaneRow]] = {}
        for row in rows:  # rows come ordered by rn: each lane stays in its own order
            lanes.setdefault(row.lane_key, deque()).append(row)
        for key, queue in lanes.items():
            head = queue[0]
            result.lanes[key] = LaneState(key, head.study_id, head.user_id, head.ep, len(queue), head.id)
        for key in list(self.residency_wait_since):
            if key not in lanes:
                del self.residency_wait_since[key]
        if slots.draining:
            for state in result.lanes.values():
                state.reason = DRAINING
            return [], {}

        post_busy = pool_free.get("post", slots.post_run_slots) < slots.post_run_slots
        memory_busy = (slots.exclusive_eval and snap.eval_used) or (slots.defer_post_runs and post_busy)
        sandbox_slots = 0 if memory_busy else slots.sandbox_slots
        free = {
            "sandbox": min(sandbox_slots - snap.sandbox_used, pool_free.get("sandbox", 0)),
            "llm": min(slots.llm_job_threads - snap.llm_used, pool_free.get("llm", 0)),
        }
        ep_active = Counter(snap.ep_active)
        study_active = Counter(snap.study_active)
        user_active = Counter(snap.user_active)
        resident = {ep: set(models) for ep, models in snap.resident.items()}
        running_models: dict[int, set[str]] = {}
        # Models of running runs (resident minus outstanding warm-ups) are known to be loaded.
        for ep, models in resident.items():
            warming = {n for n, w in snap.warmups.get(ep, {}).items() if w.outstanding}
            running_models[ep] = models - warming
        swap = self._drain_for_swap(now, lanes)
        chosen: list[LaneRow] = []
        extras: dict[str, dict[str, Any]] = {}
        blocked: set[tuple[str, int]] = set()

        lane_threads = {"llm": slots.llm_job_threads, "sandbox": slots.sandbox_slots}

        def endpoint_load(head: LaneRow) -> float:
            """Share of what this endpoint can really take (its job cap, at most the lane's threads).

            Lanes of one study tie on priority and fair share forever (each
            admission raises both together), so without this the lane with the
            largest cap (the OpenRouter row, cap 64 > llm threads) would take
            every free thread while local endpoints sit idle.
            """
            ep_state = snap.endpoint(head.ep)
            cap = ep_state.job_cap if ep_state is not None else 1
            threads = lane_threads.get(head.lane, 0)
            usable = max(1, min(cap, threads) if threads > 0 else cap)
            return ep_active[head.ep] / usable

        def sort_key(key: tuple[str, int]) -> tuple:
            head = lanes[key][0]
            ep_state = snap.endpoint(head.ep)
            affinity = 0 if head.model_key in resident.get(head.ep, set()) else 1
            warm = 0 if ep_state is not None and head.served_name in ep_state.loaded else 1
            started = head.launched_at or head.queued_at
            return (
                -head.priority,
                user_active[head.user_id],
                study_active[head.study_id] if head.study_id else 0,
                endpoint_load(head),
                affinity,
                warm,
                started,
                key,
            )

        held_by_swap: set[tuple[str, int]] = set()

        while True:
            candidates = [k for k, q in lanes.items() if q and k not in blocked]
            if not candidates:
                break
            key = min(candidates, key=sort_key)
            head = lanes[key][0]
            reason, extra = self._admission(
                head,
                snap,
                free,
                ep_active,
                study_active,
                resident,
                running_models,
                swap,
                now,
                dry_run,
                open_circuits,
            )
            state = result.lanes[key]
            if reason is not None:
                blocked.add(key)
                state.reason = reason
                self._note_block(key, head, reason, now, swap, blocked, held_by_swap)
                continue
            lanes[key].popleft()
            self.residency_wait_since.pop(key, None)
            chosen.append(head)
            if extra:
                extras[str(head.id)] = extra
            free[head.lane] = free.get(head.lane, 0) - 1
            ep_active[head.ep] += 1
            resident.setdefault(head.ep, set()).add(head.model_key)
            running_models.setdefault(head.ep, set()).add(head.model_key)
            if head.study_id:
                study_active[head.study_id] += 1
            user_active[head.user_id] += 1
            if lanes[key]:
                state.head_run = lanes[key][0].id
            state.pending_in_window = len(lanes[key])
        return chosen, extras

    def _note_block(
        self,
        key: tuple[str, int],
        head: LaneRow,
        reason: str,
        now: datetime,
        swap: dict[int, tuple[tuple[str, int], str]],
        blocked: set[tuple[str, int]],
        held_by_swap: set[tuple[str, int]],
    ) -> None:
        """Starvation bookkeeping for a blocked head (spec §C.4 rule 4).

        - Only a lane blocked by residency itself records ``residency_wait_since``;
          a lane held back by another lane's drain-for-swap does not start a clock
          (else, after a drain longer than the guard, the drained lane is "starved"
          the moment the swap lands and the endpoint swaps back after one batch).
        - A lane whose model is not served is not waiting for residency: its clock goes.
        - When the starved head itself cannot run for another reason (model not
          served, its study's cap), the swap is abandoned for this tick and the
          lanes it held are re-evaluated, so the endpoint never idles on it.
        """
        starved = swap.get(head.ep)
        if reason == RESIDENCY_WAIT:
            if starved is not None and starved[0] != key and starved[1] != head.model_key:
                held_by_swap.add(key)
            else:
                self.residency_wait_since.setdefault(key, now)
            return
        if reason == MODEL_NOT_SERVED:
            self.residency_wait_since.pop(key, None)
        if starved is not None and starved[0] == key and reason in (MODEL_NOT_SERVED, STUDY_CAP):
            del swap[head.ep]
            for other in [k for k in held_by_swap if k[1] == head.ep]:
                held_by_swap.discard(other)
                blocked.discard(other)

    def _drain_for_swap(self, now: datetime, lanes) -> dict[int, tuple[tuple[str, int], str]]:
        """Endpoints in drain-for-swap: ``{endpoint: (starved lane, its head's model)}``."""
        swap: dict[int, tuple[tuple[str, int], str]] = {}
        limit = timedelta(seconds=self.starvation_s)
        for key, since in sorted(self.residency_wait_since.items(), key=lambda kv: kv[1]):
            queue = lanes.get(key)
            if not queue or now - since < limit:
                continue
            ep = key[1]
            if ep not in swap:
                swap[ep] = (key, queue[0].model_key)
        return swap

    def _admission(  # noqa: PLR0911 — one return per admission rule, in spec order
        self,
        head: LaneRow,
        snap: CapacitySnapshot,
        free: dict[str, int],
        ep_active: Counter,
        study_active: Counter,
        resident: dict[int, set[str]],
        running_models: dict[int, set[str]],
        swap: dict[int, tuple[tuple[str, int], str]],
        now: datetime,
        dry_run: bool,
        open_circuits: set[int] | None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        # 1. a real worker thread and a slot
        if free.get(head.lane, 0) <= 0:
            return (SANDBOX_FULL if head.lane == "sandbox" else LLM_THREADS_FULL), None
        # 2. endpoint up and below its job cap
        ep = snap.endpoint(head.ep)
        if ep is None or not ep.enabled or not ep.online:
            return ENDPOINT_OFFLINE, None
        circuit_open = ep.id in open_circuits if open_circuits is not None else self.breaker.is_open(ep.id)
        if circuit_open:
            return CIRCUIT_OPEN, None
        if ep_active[ep.id] >= ep.job_cap:
            return ENDPOINT_FULL, None
        # 3. the live session / pump serves the frozen (name, digest)
        if not ep.is_system and not ep.serves(head.served_name, head.digest):
            return MODEL_NOT_SERVED, None
        # 4. residency (every policy) with the starvation guard
        model = head.model_key
        on_ep = resident.setdefault(ep.id, set())
        if not ep.is_system:
            k = _resident_limit(ep, head)
            starved = swap.get(ep.id)
            if starved is not None and starved[1] != model:
                return RESIDENCY_WAIT, None
            if k is not None and model not in on_ep and len(on_ep) >= k:
                return RESIDENCY_WAIT, None
        # 5. study cap (Study.max_parallel)
        if head.study_id and head.study_max_parallel and study_active[head.study_id] >= head.study_max_parallel:
            return STUDY_CAP, None
        # 6. prefetch (Ollama, warm-up on): admit once the model is loaded
        extra: dict[str, Any] | None = None
        if ep.kind == "ollama" and not ep.is_system and head.warmup_enabled:
            loaded = model in running_models.get(ep.id, set()) or (ep.loaded_fresh and head.served_name in ep.loaded)
            if not loaded:
                warmup = snap.warmups.get(ep.id, {}).get(head.served_name)
                if warmup is not None and warmup.outstanding:
                    return WARMING_MODEL, None
                finished_at = (warmup.completed_at or warmup.created_at) if warmup is not None else None
                if warmup is not None and finished_at and (now - finished_at).total_seconds() <= WARMUP_VALID_S:
                    if warmup.status == "done":
                        extra = {"prefetch_load_s": round((warmup.load_ms or 0) / 1000, 3)}
                    else:
                        extra = {"prefetch_error": (warmup.error or warmup.status)[:300]}
                elif dry_run:
                    return WARMING_MODEL, None
                else:
                    error = self._enqueue_warmup(head, ep)
                    if error is None:
                        on_ep.add(model)  # an outstanding warm-up counts as resident
                        snap.warmups.setdefault(ep.id, {})[head.served_name] = capacity.Warmup(
                            "",
                            "queued",
                            None,
                            "",
                            now,
                            None,
                        )
                        return WARMING_MODEL, None
                    extra = {"prefetch_error": error}
        return None, extra

    def _enqueue_warmup(self, head: LaneRow, ep) -> str | None:
        """Queue one ``warmup`` LLMRequest for the head's model; returns an error text when that failed."""
        from backend.generation.models import GenerationRun

        try:
            from backend.inference.access import resolve_access
            from backend.inference.requests import enqueue

            run = GenerationRun.objects.select_related("model__endpoint", "created_by").get(id=head.id)
            access = resolve_access(run.created_by, run.model)
            body = warmup_body(head.served_name, access, ep)
            enqueue(
                access,
                "/api/generate",
                body,
                job=None,
                source="warmup",
                priority=head.priority,
                digest=head.digest,
                model_name=head.served_name,
            )
        except Exception as exc:  # noqa: BLE001 — a failed warm-up is non-fatal: the run is admitted cold
            logger.warning("Warm-up of %s on endpoint %s failed: %s", head.served_name, ep.slug, exc)
            return f"{type(exc).__name__}: {exc}"[:300]
        logger.info("Warming %s on endpoint %s", head.served_name, ep.slug)
        return None

    # ── step 5: apply ──────────────────────────────────────────────────

    def _apply(self, chosen: list[LaneRow], extras: dict[str, dict[str, Any]]) -> list[Claimed]:
        ids = [str(row.id) for row in chosen]
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(_LOCK_SQL, [ids])
            locked = [str(r[0]) for r in cursor.fetchall()]
            if not locked:
                return []
            cursor.execute(
                _CLAIM_SQL,
                {
                    "owner": self.executor_id,
                    "lease": self.lease_seconds,
                    "extras": json.dumps(extras),
                    "ids": locked,
                },
            )
            rows = cursor.fetchall()
        by_id = {str(r[0]): r for r in rows}
        # Keep admission order.
        return [Claimed.from_row(by_id[str(row.id)]) for row in chosen if str(row.id) in by_id]


def warmup_body(served_name: str, access, ep) -> dict[str, Any]:
    """The ``/api/generate`` warm-up body: the ``num_ctx`` (and keep-alive) the run's chat calls send.

    Ollama reloads a runner whose options differ from the request's (``num_ctx`` among them), so a
    warm-up at the server's default context would be thrown away by the first ``/api/chat`` and the
    real load would land on the run's wall clock. The values come from the live access (the v3
    manifest pins no per-endpoint context; WP-11's gateway config should match them).
    """
    num_ctx = access.num_ctx
    keep_alive = access.keep_alive or ep.keep_alive
    body: dict[str, Any] = {"model": served_name, "prompt": "", "keep_alive": keep_alive}
    if num_ctx:
        body["options"] = {"num_ctx": int(num_ctx)}
    return body


def _resident_limit(ep, head: LaneRow) -> int | None:
    limits = [ep.max_resident_models, (head.execution_options or {}).get("max_resident_models")]
    finite = [int(v) for v in limits if isinstance(v, int) and not isinstance(v, bool) and v > 0]
    return min(finite) if finite else None
