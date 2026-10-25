"""Capacity snapshot, slot settings and the apps-dind memory rule (spec §C.4 step 1, §C.6, §C.10).

:func:`snapshot` is what the scheduler admits against: one grouped query
over the jobs a worker may still hold, plus the endpoint rows with their
liveness (live relay session / recent direct inventory / always for
OpenRouter), what each one serves and has loaded, and the outstanding model
warm-ups.

Jobs count against capacity whenever a worker may still hold them:
``lease_owner <> ''`` **whatever the status** (a cancelled job whose thread is
still tearing down keeps its slot until the worker's ``finally`` clears the
lease), plus every RUNNING executor-owned job.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

# Hard bounds of the runtime-editable slot settings (§B.4).
SLOT_BOUNDS = {"sandbox_slots": (0, 8), "eval_slots": (0, 4), "llm_job_threads": (1, 32), "post_run_slots": (0, 1)}
SANDBOX_OVERHEAD = "256m"  # gateway container + slack next to each agent sandbox
MEMORY_HEADROOM = 0.95
LOADED_MODELS_FRESH_S = 30  # /api/ps older than this is "unknown" (prefetch)
DIRECT_INVENTORY_FRESH_S = 600  # a direct endpoint without inventory for 10 min is offline
WARMUP_LOOKBACK_S = 1800

_MEM_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*$", re.IGNORECASE)
_MEM_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def parse_mem(value: str | int | None) -> int:
    """Docker-style memory size (``"4g"``, ``"1536m"``, bytes) → bytes."""
    if value is None or value == "":
        return 0
    if isinstance(value, int):
        return value
    match = _MEM_RE.match(str(value))
    if not match:
        msg = f"Unparseable memory size {value!r}"
        raise ValueError(msg)
    return int(float(match.group(1)) * _MEM_UNITS[match.group(2).lower()])


def fmt_mem(value: int) -> str:
    for unit, size in (("g", 1024**3), ("m", 1024**2)):
        if value >= size:
            amount = value / size
            return f"{amount:.2f}".rstrip("0").rstrip(".") + unit
    return f"{value}b"


# ── memory rule (§C.10) ────────────────────────────────────────────────


@dataclass(frozen=True)
class MemoryCheck:
    ok: bool  # the whole rule holds
    ok_without_post: bool  # holds when the post-run term is left out
    required: int
    limit: int
    arithmetic: str

    @property
    def post_only(self) -> bool:
        """Violated only because of the post-run term (allowed with a warning; post-runs then defer)."""
        return not self.ok and self.ok_without_post


def _terms() -> dict[str, int]:
    return {
        "sandbox": parse_mem(settings.AGENT_SANDBOX_MEM_LIMIT) + parse_mem(SANDBOX_OVERHEAD),
        "eval": parse_mem(settings.EVAL_SLOT_MEM),
        "post": parse_mem(settings.ANALYSIS_WORKSPACE_MEM),
        "reserve": parse_mem(settings.APPS_DIND_APP_RESERVE),
    }


def memory_limit() -> int:
    """``APPS_DIND_MEM_LIMIT`` is authoritative (``docker info`` inside apps-dind reports host RAM)."""
    return parse_mem(settings.APPS_DIND_MEM_LIMIT)


def check_memory(sandbox_slots: int, eval_slots: int, post_run_slots: int) -> MemoryCheck:
    """``sandbox x (AGENT_SANDBOX_MEM_LIMIT + 256m) + eval x EVAL_SLOT_MEM + post x ANALYSIS_WORKSPACE_MEM
    + APPS_DIND_APP_RESERVE ≤ APPS_DIND_MEM_LIMIT x 0.95``."""
    t = _terms()
    base = sandbox_slots * t["sandbox"] + eval_slots * t["eval"] + t["reserve"]
    required = base + post_run_slots * t["post"]
    limit = int(memory_limit() * MEMORY_HEADROOM)
    arithmetic = (
        f"{sandbox_slots} x {fmt_mem(t['sandbox'])} (sandbox + gateway) + {eval_slots} x {fmt_mem(t['eval'])} "
        f"(evaluation) + {post_run_slots} x {fmt_mem(t['post'])} (analysis) + {fmt_mem(t['reserve'])} (apps) "
        f"= {fmt_mem(required)} {'≤' if required <= limit else '>'} {fmt_mem(limit)} "
        f"(95% of APPS_DIND_MEM_LIMIT={settings.APPS_DIND_MEM_LIMIT})"
    )
    return MemoryCheck(required <= limit, base <= limit, required, limit, arithmetic)


def max_sandbox_slots_allowed(eval_slots: int, post_run_slots: int = 0) -> int:
    """The most sandbox slots the memory rule allows next to ``eval_slots`` (the post-run term is waivable)."""
    t = _terms()
    free = int(memory_limit() * MEMORY_HEADROOM) - eval_slots * t["eval"] - t["reserve"]
    return max(0, min(SLOT_BOUNDS["sandbox_slots"][1], free // t["sandbox"]))


@dataclass(frozen=True)
class EffectiveSlots:
    sandbox_slots: int
    eval_slots: int
    llm_job_threads: int
    post_run_slots: int
    draining: bool
    defer_post_runs: bool  # the memory rule holds only without the post-run term
    warnings: tuple[str, ...] = ()
    # Sandbox runs and evaluations do not fit side by side: they take turns (the pre-executor behaviour).
    exclusive_eval: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sandbox_slots": self.sandbox_slots,
            "eval_slots": self.eval_slots,
            "llm_job_threads": self.llm_job_threads,
            "post_run_slots": self.post_run_slots,
            "draining": self.draining,
            "defer_post_runs": self.defer_post_runs,
            "exclusive_eval": self.exclusive_eval,
        }


def minimal_slots(sandbox_slots: int, eval_slots: int) -> bool:
    """One sandbox and one evaluation slot (or fewer): the configuration the platform always runs with.

    The memory rule cannot refuse it; when even this does not fit, sandbox runs and
    evaluations take turns (``EffectiveSlots.exclusive_eval``) instead.
    """
    return sandbox_slots <= 1 and eval_slots <= 1


def effective_slots(row) -> EffectiveSlots:
    """Clamp an ``ExecutorSettings`` row to its bounds and the memory rule; never raises.

    Over the memory rule, sandbox slots are lowered (never below 1); if one
    sandbox and the evaluation slots still do not fit side by side, they take
    turns (``exclusive_eval``). A rule that fails only by the post-run term
    defers post-runs while sandbox and evaluation are busy.
    """
    warnings: list[str] = []
    values: dict[str, int] = {}
    for name, (low, high) in SLOT_BOUNDS.items():
        raw = int(getattr(row, name))
        values[name] = min(high, max(low, raw))
        if values[name] != raw:
            warnings.append(f"{name}={raw} is out of range {low}..{high}; using {values[name]}")
    check = check_memory(values["sandbox_slots"], values["eval_slots"], values["post_run_slots"])
    exclusive = False
    if not check.ok_without_post:
        allowed = max(1, max_sandbox_slots_allowed(values["eval_slots"]))
        if allowed < values["sandbox_slots"]:
            warnings.append(
                f"sandbox_slots={values['sandbox_slots']} breaks the apps-dind memory rule; using {allowed} "
                f"({check.arithmetic})",
            )
            values["sandbox_slots"] = allowed
        check = check_memory(values["sandbox_slots"], values["eval_slots"], values["post_run_slots"])
        if not check.ok_without_post and values["sandbox_slots"] and values["eval_slots"]:
            exclusive = True
            warnings.append(
                "sandbox runs and evaluations do not fit in apps-dind memory side by side; they take turns "
                f"({check.arithmetic})",
            )
    defer_post = check.post_only or exclusive
    if defer_post and values["post_run_slots"]:
        warnings.append("post-runs wait while sandbox and evaluation slots are busy (memory rule)")
    return EffectiveSlots(
        draining=bool(row.draining),
        defer_post_runs=defer_post,
        warnings=tuple(warnings),
        exclusive_eval=exclusive,
        **values,
    )


# ── code version (§C.2 staleness) ──────────────────────────────────────

_code_version_cache: tuple[float, str] | None = None
_code_version_lock = threading.Lock()


def compute_code_version(root: Path | None = None) -> str:
    """sha1 over ``(path, mtime_ns, size)`` of every ``backend/**/*.py`` (cheap; no file contents)."""
    root = root or Path(settings.BASE_DIR) / "backend"
    digest = hashlib.sha1(usedforsecurity=False)
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{path.relative_to(root)}\0{stat.st_mtime_ns}\0{stat.st_size}\n".encode())
    return digest.hexdigest()


def code_version(max_age_s: float = 60.0) -> str:
    """:func:`compute_code_version`, cached for ``max_age_s`` (the web process compares it to the executor's)."""
    global _code_version_cache  # noqa: PLW0603
    with _code_version_lock:
        now = time.monotonic()
        if _code_version_cache is None or now - _code_version_cache[0] > max_age_s:
            _code_version_cache = (now, compute_code_version())
        return _code_version_cache[1]


# ── capacity snapshot (§C.4 step 1) ────────────────────────────────────


@dataclass
class EndpointState:
    id: int
    slug: str
    name: str
    kind: str
    connection: str
    enabled: bool
    is_system: bool
    owner_id: int | None
    max_concurrency: int
    max_active_jobs: int | None
    max_resident_models: int | None
    keep_alive: str
    online: bool = False
    served: set[tuple[str, str]] | None = None  # None: serves everything (OpenRouter)
    loaded: set[str] = field(default_factory=set)  # names on fresh /api/ps
    loaded_fresh: bool = False

    @property
    def job_cap(self) -> int:
        """Active jobs admitted at once (both lanes): ``max_active_jobs`` or ``max_concurrency``."""
        return int(self.max_active_jobs if self.max_active_jobs is not None else self.max_concurrency)

    def serves(self, name: str, digest: str) -> bool:
        if self.served is None:
            return True
        if digest:
            return (name, digest) in self.served
        return any(served_name == name for served_name, _d in self.served)


@dataclass
class Warmup:
    request_id: str
    status: str
    load_ms: int | None
    error: str
    created_at: datetime
    completed_at: datetime | None

    @property
    def outstanding(self) -> bool:
        return self.status in ("queued", "leased")


@dataclass
class CapacitySnapshot:
    system_ep: int
    sandbox_used: int = 0
    eval_used: int = 0
    llm_used: int = 0
    ep_active: Counter = field(default_factory=Counter)  # phase running, both lanes
    ep_user_active: Counter = field(default_factory=Counter)  # (endpoint, user) -> running
    resident: dict[int, set[str]] = field(default_factory=dict)
    exp_active: Counter = field(default_factory=Counter)  # all phases
    user_active: Counter = field(default_factory=Counter)  # all phases
    endpoints: dict[int, EndpointState] = field(default_factory=dict)
    warmups: dict[int, dict[str, Warmup]] = field(default_factory=dict)
    taken_at: datetime = field(default_factory=timezone.now)

    def endpoint(self, ep_id: int | None) -> EndpointState | None:
        return self.endpoints.get(ep_id if ep_id is not None else self.system_ep)


def residency_key(served_name: str, model_id: int | None) -> str:
    """What residency counts: the served name on a local endpoint (``model:<pk>`` when unknown)."""
    return served_name or f"model:{model_id}"


_ACTIVE_SQL = """
SELECT lane, phase, status, coalesce(endpoint_id, %s) AS ep, experiment_id, created_by_id,
       coalesce(resolved_bundle->'endpoint'->>'served_model_name', '') AS served, model_id, count(*)
FROM generation_generationjob
WHERE queued_at IS NOT NULL AND (status = 'running' OR lease_owner <> '')
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
"""


def _loaded_names(endpoint, now: datetime) -> tuple[set[str], bool]:
    """Names on the endpoint's last ``/api/ps`` and whether that is ≤ 30 s old.

    ``loaded_models`` is a list of ``{name, ...}`` (freshness from an ``at`` key
    on the entries, else ``last_inventory_at``) or ``{"at": ..., "models": [...]}``.
    """
    raw = endpoint.loaded_models
    at: Any = endpoint.last_inventory_at
    entries: list[Any] = []
    if isinstance(raw, dict):
        entries = raw.get("models") or raw.get("loaded") or []
        at = raw.get("at") or at
    elif isinstance(raw, list):
        entries = raw
        stamps = [e.get("at") for e in raw if isinstance(e, dict) and e.get("at")]
        if stamps:
            at = max(stamps)
    names = {str(e.get("name") or e.get("model") or "") for e in entries if isinstance(e, dict)} - {""}
    if isinstance(at, str):
        from django.utils.dateparse import parse_datetime

        at = parse_datetime(at)
    fresh = isinstance(at, datetime) and (now - at).total_seconds() <= LOADED_MODELS_FRESH_S
    return names, fresh


def _endpoint_states(now: datetime) -> dict[int, EndpointState]:
    from backend.inference.models import LLMEndpoint
    from backend.inference.models import RelaySession
    from backend.llm_models.models import LLMModel

    states: dict[int, EndpointState] = {}
    for ep in LLMEndpoint.objects.all():
        loaded, fresh = _loaded_names(ep, now)
        states[ep.pk] = EndpointState(
            id=ep.pk,
            slug=ep.slug,
            name=ep.name,
            kind=ep.kind,
            connection=ep.connection,
            enabled=ep.enabled,
            is_system=ep.is_system,
            owner_id=ep.owner_id,
            max_concurrency=ep.max_concurrency,
            max_active_jobs=ep.max_active_jobs,
            max_resident_models=ep.max_resident_models,
            keep_alive=ep.keep_alive,
            loaded=loaded,
            loaded_fresh=fresh,
        )
        state = states[ep.pk]
        if ep.is_system:
            state.online = True
            state.served = None
        elif ep.connection == LLMEndpoint.Connection.DIRECT:
            state.online = bool(
                ep.last_inventory_at and (now - ep.last_inventory_at).total_seconds() <= DIRECT_INVENTORY_FRESH_S,
            )
            state.served = set()

    direct_ids = [s.id for s in states.values() if s.connection == "direct" and not s.is_system]
    if direct_ids:
        for ep_id, name, digest in LLMModel.objects.filter(
            endpoint_id__in=direct_ids,
            is_available=True,
        ).values_list("endpoint_id", "served_model_name", "digest"):
            states[ep_id].served.add((name, digest))

    ttl = timedelta(seconds=settings.RELAY_SESSION_TTL_S)
    live = RelaySession.objects.filter(
        ended_at__isnull=True,
        role=RelaySession.Role.ACTIVE,
        last_seen_at__gt=now - ttl,
    ).values_list("endpoint_id", "models")
    for ep_id, models in live:
        state = states.get(ep_id)
        if state is None or state.is_system:
            continue
        state.online = True
        served = state.served if state.served is not None else set()
        for entry in models or []:
            if isinstance(entry, dict) and entry.get("name"):
                served.add((str(entry["name"]), str(entry.get("digest") or "")))
        state.served = served
    for state in states.values():
        if not state.is_system and state.served is None:
            state.served = set()
    return states


def _warmups(now: datetime, endpoint_ids: list[int]) -> dict[int, dict[str, Warmup]]:
    from backend.inference.models import LLMRequest

    out: dict[int, dict[str, Warmup]] = {}
    if not endpoint_ids:
        return out
    rows = (
        LLMRequest.objects.filter(
            source=LLMRequest.Source.WARMUP,
            endpoint_id__in=endpoint_ids,
            created_at__gte=now - timedelta(seconds=WARMUP_LOOKBACK_S),
        )
        .order_by("created_at")
        .values_list("id", "endpoint_id", "model_name", "status", "load_ms", "error", "created_at", "completed_at")
    )
    for rid, ep_id, name, status, load_ms, error, created_at, completed_at in rows:
        out.setdefault(ep_id, {})[name] = Warmup(str(rid), status, load_ms, error, created_at, completed_at)
    return out


def snapshot(system_ep: int | None = None) -> CapacitySnapshot:
    """The capacity the scheduler admits against (one grouped job query + endpoints + warm-ups)."""
    if system_ep is None:
        from backend.inference.access import system_openrouter

        system_ep = system_openrouter().pk
    now = timezone.now()
    snap = CapacitySnapshot(system_ep=system_ep, taken_at=now)
    with connection.cursor() as cursor:
        cursor.execute(_ACTIVE_SQL, [system_ep])
        rows = cursor.fetchall()
    for lane, raw_phase, status, ep, experiment_id, user_id, served, model_id, count in rows:
        # A cancelled (or otherwise finished) row whose worker still holds it counts as running.
        phase = raw_phase if status == "running" else (raw_phase or "running")
        snap.user_active[user_id] += count
        if experiment_id is not None:
            snap.exp_active[experiment_id] += count
        if phase == "evaluating":
            snap.eval_used += count
        elif phase == "running":
            if lane == "sandbox":
                snap.sandbox_used += count
            else:
                snap.llm_used += count
            snap.ep_active[ep] += count
            snap.ep_user_active[(ep, user_id)] += count
            snap.resident.setdefault(ep, set()).add(residency_key(served, model_id))
    snap.endpoints = _endpoint_states(now)
    local_ids = [s.id for s in snap.endpoints.values() if not s.is_system]
    snap.warmups = _warmups(now, local_ids)
    for ep_id, by_name in snap.warmups.items():
        for name, warmup in by_name.items():
            if warmup.outstanding:
                snap.resident.setdefault(ep_id, set()).add(name)
    return snap
