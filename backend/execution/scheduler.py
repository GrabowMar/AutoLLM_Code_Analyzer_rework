"""The claim algorithm (spec §C.4): capacity snapshot → evaluation claims → lane heads → admission → apply.

Runs on the leader only. Lanes are keyed by ``(experiment or "adhoc:<user>",
endpoint)`` and are strict head-of-line in their frozen order (``sequence``
for experiments, enqueue order for ad-hoc jobs), so a lane's run order is
reproducible; the scheduler is work-conserving *across* lanes. Fair share is
per user first, then per experiment; priority orders lanes. Residency K is
always enforced, with a starvation guard (drain-for-swap) so model affinity
never starves another lane for more than ``EXECUTOR_STARVATION_S``.

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
EXPERIMENT_CAP = "experiment_cap"
CIRCUIT_OPEN = "circuit_open"
DRAINING = "draining"
RETRY_BACKOFF = "retry_backoff"

MAX_LANE_DEPTH = 64
WARMUP_VALID_S = 600  # a finished warm-up admits heads of its model for this long

_LANE_SQL = """
SELECT * FROM (
  SELECT j.id, j.lane, coalesce(j.endpoint_id, %(system_ep)s) AS ep, j.model_id, j.experiment_id, j.sequence,
         j.priority, j.queued_at, j.created_by_id, j.attempt, j.condition_id, j.app_requirement_id,
         j.repeat_index, j.batch_id,
         coalesce(j.resolved_bundle->'endpoint'->>'served_model_name', '') AS served_name,
         coalesce(j.resolved_bundle->'endpoint'->>'digest', '') AS snapshot_digest,
         x.launched_at, x.max_concurrency AS exp_max_concurrency, x.execution_options,
         row_number() OVER (
           PARTITION BY coalesce(j.experiment_id::text, 'adhoc:' || j.created_by_id::text),
                        coalesce(j.endpoint_id, %(system_ep)s)
           ORDER BY CASE WHEN j.experiment_id IS NULL THEN NULL ELSE j.sequence END NULLS LAST,
                    j.queued_at, j.sequence NULLS LAST, j.id
         ) AS rn
  FROM generation_generationjob j LEFT JOIN generation_experiment x ON x.id = j.experiment_id
  WHERE j.status = 'pending' AND j.queued_at IS NOT NULL
    AND (j.run_after IS NULL OR j.run_after <= now())
    AND (j.experiment_id IS NULL OR x.status = 'running')
    %(extra)s
) t WHERE rn <= %(depth)s
ORDER BY rn
"""

_EVAL_CLAIM_SQL = """
WITH c AS (
  SELECT id FROM generation_generationjob
  WHERE status = 'running' AND phase = 'eval_queued'
  ORDER BY priority DESC, stage_entered_at NULLS FIRST, id
  LIMIT %s FOR UPDATE SKIP LOCKED
)
UPDATE generation_generationjob j
SET phase = 'evaluating', lease_owner = %s, lease_expires_at = now() + %s * interval '1 second',
    heartbeat_at = now(), stage_entered_at = now(), updated_at = now()
FROM c WHERE j.id = c.id
RETURNING j.id, j.attempt, j.experiment_id, j.batch_id, j.endpoint_id, j.condition_id, j.app_requirement_id,
          j.repeat_index, j.status, j.phase
"""

_LOCK_SQL = """
SELECT j.id FROM generation_generationjob j
WHERE j.id = ANY(%s::uuid[]) AND j.status = 'pending' AND j.queued_at IS NOT NULL
  AND (j.experiment_id IS NULL OR EXISTS (
        SELECT 1 FROM generation_experiment x WHERE x.id = j.experiment_id AND x.status = 'running'))
ORDER BY j.id
FOR UPDATE OF j SKIP LOCKED
"""

_CLAIM_SQL = """
UPDATE generation_generationjob
SET status = 'running', phase = 'running', started_at = now(), lease_owner = %(owner)s,
    lease_expires_at = now() + %(lease)s * interval '1 second', heartbeat_at = now(), stage_entered_at = now(),
    updated_at = now(),
    execution = coalesce(execution, '{}'::jsonb)
      || jsonb_build_object(
           'started_seq', nextval('execution_start_seq'),
           'queued_at', queued_at,
           'claimed_at', now(),
           'queue_seconds', round(extract(epoch FROM now() - queued_at)::numeric, 2),
           'executor', %(owner)s::text)
      || coalesce((%(extras)s::jsonb) -> (id::text), '{}'::jsonb)
WHERE id = ANY(%(ids)s::uuid[])
RETURNING id, attempt, lane, experiment_id, batch_id, endpoint_id, condition_id, app_requirement_id, repeat_index
"""


@dataclass
class LaneRow:
    id: Any
    lane: str
    ep: int
    model_id: int | None
    experiment_id: Any
    sequence: int | None
    priority: int
    queued_at: datetime
    user_id: int
    attempt: int
    condition_id: int | None
    app_id: int | None
    repeat_index: int | None
    batch_id: Any
    served_name: str
    digest: str
    launched_at: datetime | None
    exp_max_concurrency: int | None
    execution_options: dict[str, Any]

    @property
    def lane_key(self) -> tuple[str, int]:
        owner = str(self.experiment_id) if self.experiment_id else f"adhoc:{self.user_id}"
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
    job_id: Any
    lane: str  # "sandbox" | "llm" | "eval"
    attempt: int
    experiment_id: Any = None
    batch_id: Any = None
    endpoint_id: int | None = None
    condition_id: int | None = None
    app_id: int | None = None
    repeat_index: int | None = None


@dataclass
class LaneState:
    key: tuple[str, int]
    experiment_id: Any
    user_id: int
    endpoint_id: int
    pending_in_window: int
    head_job: Any
    reason: str | None = None


@dataclass
class TickResult:
    claimed: list[Claimed] = field(default_factory=list)
    lanes: dict[tuple[str, int], LaneState] = field(default_factory=dict)
    admitted_order: list[Any] = field(default_factory=list)  # dry runs: job ids in admission order
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
    """§C.4 step 3: the first ``depth`` pending jobs of every lane (no locks)."""
    params: dict[str, Any] = {"system_ep": system_ep, "depth": int(depth), "extra": ""}
    sql = _LANE_SQL
    if user_id is not None:
        sql = sql.replace("%(extra)s", "AND j.created_by_id = %(user_id)s")
        params["user_id"] = user_id
    else:
        sql = sql.replace("%(extra)s", "")
    params.pop("extra")
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
                experiment_id=row["experiment_id"],
                sequence=row["sequence"],
                priority=row["priority"],
                queued_at=row["queued_at"],
                user_id=row["created_by_id"],
                attempt=row["attempt"],
                condition_id=row["condition_id"],
                app_id=row["app_requirement_id"],
                repeat_index=row["repeat_index"],
                batch_id=row["batch_id"],
                served_name=row["served_name"],
                digest=row["snapshot_digest"],
                launched_at=row["launched_at"],
                exp_max_concurrency=row["exp_max_concurrency"],
                execution_options=options or {},
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
            if eval_free > 0:
                evaluations = self._claim_evaluations(eval_free)
                snap.eval_used += len(evaluations)
                result.claimed.extend(evaluations)
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
        return [
            Claimed(
                job_id=r[0],
                lane="eval",
                attempt=r[1],
                experiment_id=r[2],
                batch_id=r[3],
                endpoint_id=r[4],
                condition_id=r[5],
                app_id=r[6],
                repeat_index=r[7],
            )
            for r in rows
        ]

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
            result.lanes[key] = LaneState(key, head.experiment_id, head.user_id, head.ep, len(queue), head.id)
        for key in list(self.residency_wait_since):
            if key not in lanes:
                del self.residency_wait_since[key]
        if slots.draining:
            for state in result.lanes.values():
                state.reason = DRAINING
            return [], {}

        sandbox_slots = 0 if slots.exclusive_eval and snap.eval_used else slots.sandbox_slots
        free = {
            "sandbox": min(sandbox_slots - snap.sandbox_used, pool_free.get("sandbox", 0)),
            "llm": min(slots.llm_job_threads - snap.llm_used, pool_free.get("llm", 0)),
        }
        ep_active = Counter(snap.ep_active)
        exp_active = Counter(snap.exp_active)
        user_active = Counter(snap.user_active)
        resident = {ep: set(models) for ep, models in snap.resident.items()}
        running_models: dict[int, set[str]] = {}
        # Models of running jobs (resident minus outstanding warm-ups) are known to be loaded.
        for ep, models in resident.items():
            warming = {n for n, w in snap.warmups.get(ep, {}).items() if w.outstanding}
            running_models[ep] = models - warming
        swap = self._drain_for_swap(now, lanes)
        chosen: list[LaneRow] = []
        extras: dict[str, dict[str, Any]] = {}
        blocked: set[tuple[str, int]] = set()

        def sort_key(key: tuple[str, int]) -> tuple:
            head = lanes[key][0]
            ep_state = snap.endpoint(head.ep)
            affinity = 0 if head.model_key in resident.get(head.ep, set()) else 1
            warm = 0 if ep_state is not None and head.served_name in ep_state.loaded else 1
            started = head.launched_at or head.queued_at
            return (
                -head.priority,
                user_active[head.user_id],
                exp_active[head.experiment_id] if head.experiment_id else 0,
                affinity,
                warm,
                started,
                key,
            )

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
                exp_active,
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
                if reason == RESIDENCY_WAIT:
                    self.residency_wait_since.setdefault(key, now)
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
            if head.experiment_id:
                exp_active[head.experiment_id] += 1
            user_active[head.user_id] += 1
            if lanes[key]:
                state.head_job = lanes[key][0].id
            state.pending_in_window = len(lanes[key])
        return chosen, extras

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
        exp_active: Counter,
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
        # 5. experiment cap
        if (
            head.experiment_id
            and head.exp_max_concurrency
            and exp_active[head.experiment_id] >= (head.exp_max_concurrency)
        ):
            return EXPERIMENT_CAP, None
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
        from backend.generation.models import GenerationJob

        try:
            from backend.inference.access import resolve_access
            from backend.inference.requests import enqueue

            job = GenerationJob.objects.select_related("model__endpoint", "created_by").get(id=head.id)
            access = resolve_access(job.created_by, job.model)
            enqueue(
                access,
                "/api/generate",
                {"model": head.served_name, "prompt": "", "keep_alive": access.keep_alive or ep.keep_alive},
                job=None,
                source="warmup",
                priority=head.priority,
                digest=head.digest,
                model_name=head.served_name,
            )
        except Exception as exc:  # noqa: BLE001 — a failed warm-up is non-fatal: the job is admitted cold
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
        claimed = []
        for row in chosen:  # keep admission order
            r = by_id.get(str(row.id))
            if r is None:
                continue
            claimed.append(
                Claimed(
                    job_id=r[0],
                    lane=r[2],
                    attempt=r[1],
                    experiment_id=r[3],
                    batch_id=r[4],
                    endpoint_id=r[5],
                    condition_id=r[6],
                    app_id=r[7],
                    repeat_index=r[8],
                ),
            )
        return claimed


def _resident_limit(ep, head: LaneRow) -> int | None:
    limits = [ep.max_resident_models, (head.execution_options or {}).get("max_resident_models")]
    finite = [int(v) for v in limits if isinstance(v, int) and not isinstance(v, bool) and v > 0]
    return min(finite) if finite else None
