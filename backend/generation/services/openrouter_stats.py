"""OpenRouter per-call generation stats → ``GenerationArtifact.telemetry["openrouter"]``.

``GET https://openrouter.ai/api/v1/generation?id=gen-...`` (a free metadata
query, not a completion) returns OpenRouter's own record of one call. It is
fetched with the job owner's key (``credentials.services.resolver``, global
fallback rules included) after the job ran, and for older calls by
``manage.py backfill_call_telemetry --openrouter``. Only ``gen-`` ids exist
there: OpenCode Go / local calls get no ``openrouter`` block (the UI shows n/a).

What the fields measure (verified on live records, 2026-11):

* ``latency_ms`` -- OpenRouter-measured time to first token, *including* the
  provider's queueing and prefill (not a pure model latency).
* ``generation_time_ms`` -- from request to last token at OpenRouter.
* ``decode_tps`` (derived) -- ``native_tokens_completion /
  ((generation_time_ms - latency_ms) / 1000)`` when that first-to-last-token
  span is at least :data:`MIN_DECODE_SPAN_MS`: output speed after the first
  token. ``None`` otherwise -- a shorter span means the provider delivered
  the output in one burst (real records: 49-1124 tokens in 1-87 ms, i.e.
  1.9k-312k tok/s), which cannot be timed.
* ``native_tokens_*`` -- counts in the model's own tokenizer;
  ``tokens_prompt``/``tokens_completion`` are OpenRouter's normalized counts.
* ``provider_attempts`` -- every upstream attempt OpenRouter made for the
  call (``provider_responses``); ``router_retries = len - 1`` (all extra
  attempts) and ``fallbacks`` = how often it switched to *another* provider
  (a 429 retried on the same provider is a retry, not a fallback).

The derived keys are recomputed from the stored measured fields by
:func:`derived` (``backfill_call_telemetry --rederive``).

Privacy: account/workspace/app identifiers and client headers
(:data:`PRIVACY_FIELDS`) are dropped; only the allow-listed fields are kept.
A 404 (unknown or expired record) is stored as ``status: "unavailable"`` and
never retried; transient failures as ``status: "error"`` (retried by the next
fetch). Requests are throttled (default 5/s) with backoff on 429/5xx.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from collections.abc import Iterable
from itertools import pairwise
from typing import Any

import requests
from django.db import transaction
from django.utils import timezone

from backend.generation.services.call_telemetry import SCHEMA
from backend.generation.services.call_telemetry import is_openrouter_id

logger = logging.getLogger(__name__)

GENERATION_URL = "https://openrouter.ai/api/v1/generation"
DEFAULT_RATE_PER_S = 5.0
MAX_RETRIES = 3
BACKOFF_S = 2.0
TIMEOUT_S = 20
HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_TOO_MANY = 429
HTTP_SERVER_ERROR = 500
_AUTH_STATUSES = frozenset({401, 403})
# Shortest first-to-last-token span (ms) a decode speed is derived from.
MIN_DECODE_SPAN_MS = 250

# Never stored, even if a future normalizer copied more of the record.
PRIVACY_FIELDS = frozenset(
    {
        "workspace_id",
        "app_id",
        "user_agent",
        "origin",
        "http_referer",
        "external_user",
        "session_id",
        "api_key_id",
        "user_id",
        "api_key",
    },
)

# Record field → telemetry key (values copied as-is).
_COPIED = {
    "provider_name": "provider_name",
    "model": "model_permaslug",
    "latency": "latency_ms",
    "generation_time": "generation_time_ms",
    "moderation_latency": "moderation_latency_ms",
    "native_tokens_prompt": "native_tokens_prompt",
    "native_tokens_completion": "native_tokens_completion",
    "native_tokens_reasoning": "native_tokens_reasoning",
    "native_tokens_cached": "native_tokens_cached",
    "tokens_prompt": "tokens_prompt",
    "tokens_completion": "tokens_completion",
    "finish_reason": "finish_reason",
    "native_finish_reason": "native_finish_reason",
    "cache_discount": "cache_discount",
    "total_cost": "total_cost",
    "upstream_inference_cost": "upstream_inference_cost",
    "upstream_id": "upstream_id",
    "request_id": "request_id",
    "streamed": "streamed",
    "cancelled": "cancelled",
    "is_byok": "is_byok",
    "service_tier": "service_tier",
    "data_region": "data_region",
    "api_type": "api_type",
    "created_at": "created_at",
}


class StatsAuthError(Exception):
    """OpenRouter rejected the key (401/403): stop the batch instead of marking every call."""


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize(data: dict[str, Any], *, fetched_at: str | None = None) -> dict[str, Any]:
    """One ``/generation`` record → the C1 ``openrouter`` dict (``status: "ok"``)."""
    out: dict[str, Any] = {"status": "ok", "fetched_at": fetched_at or timezone.now().isoformat()}
    for source, key in _COPIED.items():
        if source in PRIVACY_FIELDS:
            continue
        out[key] = data.get(source)
    attempts_raw = data.get("provider_responses")
    if isinstance(attempts_raw, list):
        out["provider_attempts"] = [
            {
                "provider_name": a.get("provider_name"),
                "status": a.get("status"),
                "latency_ms": a.get("latency"),
                "model_permaslug": a.get("model_permaslug"),
            }
            for a in attempts_raw
            if isinstance(a, dict)
        ]
    else:
        out["provider_attempts"] = None
    out.update(derived(out))
    return out


def derived(block: dict[str, Any]) -> dict[str, Any]:
    """``decode_tps`` / ``fallbacks`` / ``router_retries`` from a block's measured fields (``None`` = unknown)."""
    latency, generation = _num(block.get("latency_ms")), _num(block.get("generation_time_ms"))
    completion = _num(block.get("native_tokens_completion"))
    decode_tps = None
    if completion and latency is not None and generation is not None and generation - latency >= MIN_DECODE_SPAN_MS:
        decode_tps = round(completion / ((generation - latency) / 1000), 2)
    fallbacks = router_retries = None
    attempts = block.get("provider_attempts")
    if isinstance(attempts, list) and attempts:
        names = [a.get("provider_name") if isinstance(a, dict) else None for a in attempts]
        router_retries = len(attempts) - 1
        fallbacks = sum(1 for prev, cur in pairwise(names) if cur != prev)
    return {"decode_tps": decode_tps, "fallbacks": fallbacks, "router_retries": router_retries}


def rederive_stored(artifact_id: int, *, dry_run: bool = False) -> str:
    """Recompute the derived keys of a stored ``ok`` block (row-locked); measured fields stay as fetched."""
    from backend.generation.models import GenerationArtifact

    with transaction.atomic():
        row = GenerationArtifact.objects.select_for_update().only("id", "telemetry").filter(pk=artifact_id).first()
        block = (row.telemetry or {}).get("openrouter") if row is not None else None
        if not isinstance(block, dict) or block.get("status") != "ok":
            return "skipped"
        new = {**block, **derived(block)}
        if new == block:
            return "unchanged"
        if not dry_run:
            telemetry = dict(row.telemetry)
            telemetry["openrouter"] = new
            GenerationArtifact.objects.filter(pk=artifact_id).update(telemetry=telemetry)
        return "rederived"


class Throttle:
    """At most ``rate`` calls per second for this process' fetch loop."""

    def __init__(
        self,
        rate: float = DEFAULT_RATE_PER_S,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval = 1.0 / rate if rate > 0 else 0.0
        self.clock = clock
        self.sleep = sleep
        self._next = 0.0

    def wait(self) -> None:
        now = self.clock()
        if now < self._next:
            self.sleep(self._next - now)
            now = self._next
        self._next = now + self.interval


def fetch_one(
    session: requests.Session,
    api_key: str,
    gen_id: str,
    *,
    throttle: Throttle,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """The ``openrouter`` dict for ``gen_id``: ``ok`` / ``unavailable`` (404) / ``error``.

    Raises :class:`StatsAuthError` on 401/403. The key goes only to openrouter.ai.
    """
    last: dict[str, Any] = {}
    for attempt in range(MAX_RETRIES + 1):
        throttle.wait()
        fetched_at = timezone.now().isoformat()
        try:
            resp = session.get(
                GENERATION_URL,
                params={"id": gen_id},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=TIMEOUT_S,
            )
        except requests.RequestException as exc:
            last = {"status": "error", "fetched_at": fetched_at, "message": type(exc).__name__}
        else:
            if resp.status_code == HTTP_OK:
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                data = body.get("data") if isinstance(body, dict) else None
                if isinstance(data, dict):
                    return normalize(data, fetched_at=fetched_at)
                return {"status": "error", "fetched_at": fetched_at, "http_status": HTTP_OK, "message": "no data"}
            if resp.status_code == HTTP_NOT_FOUND:
                return {"status": "unavailable", "fetched_at": fetched_at, "http_status": HTTP_NOT_FOUND}
            if resp.status_code in _AUTH_STATUSES:
                msg = f"OpenRouter rejected the key (HTTP {resp.status_code})"
                raise StatsAuthError(msg)
            last = {"status": "error", "fetched_at": fetched_at, "http_status": resp.status_code}
            if resp.status_code != HTTP_TOO_MANY and resp.status_code < HTTP_SERVER_ERROR:
                return last
        if attempt < MAX_RETRIES:
            sleep(BACKOFF_S * (2**attempt))
    return last


def needs_stats(telemetry: dict[str, Any] | None) -> bool:
    """True unless the call already has stats (``ok``) or is known to have none (``unavailable``)."""
    block = (telemetry or {}).get("openrouter")
    return not (isinstance(block, dict) and block.get("status") in ("ok", "unavailable"))


def _store(artifact_id: int, block: dict[str, Any]) -> None:
    """Set ``telemetry["openrouter"]`` (row-locked read-modify-write; the rest of telemetry untouched)."""
    from backend.generation.models import GenerationArtifact

    with transaction.atomic():
        row = GenerationArtifact.objects.select_for_update().only("id", "telemetry").filter(pk=artifact_id).first()
        if row is None or not needs_stats(row.telemetry):
            return
        telemetry = dict(row.telemetry or {})
        telemetry.setdefault("schema", SCHEMA)
        telemetry["openrouter"] = block
        GenerationArtifact.objects.filter(pk=artifact_id).update(telemetry=telemetry)


def candidate_artifacts(job_ids: Iterable[Any] | None = None, since: Any = None):
    """Artifacts with a ``gen-`` response id (optionally of ``job_ids`` / created since ``since``)."""
    from backend.generation.models import GenerationArtifact

    qs = GenerationArtifact.objects.filter(response_payload__id__startswith="gen-")
    if job_ids is not None:
        qs = qs.filter(job_id__in=list(job_ids))
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    return qs.order_by("job_id", "id")


def fetch_for_artifacts(
    rows: Iterable[tuple[int, str, dict[str, Any] | None]],
    api_key: str,
    *,
    not_found: str = "unavailable",
    dry_run: bool = False,
    session: requests.Session | None = None,
    throttle: Throttle | None = None,
    sleep: Callable[[float], None] = time.sleep,
    limit: int | None = None,
) -> Counter:
    """Fetch and store stats for ``(artifact_id, gen_id, telemetry)`` rows that still need them.

    ``not_found="defer"`` leaves a 404 unstored (counted as ``pending``):
    right after a run the record may not exist yet. Returns outcome counts.
    """
    counts: Counter = Counter()
    session = session or requests.Session()
    throttle = throttle or Throttle(sleep=sleep)
    for artifact_id, gen_id, telemetry in rows:
        if not is_openrouter_id(gen_id) or not needs_stats(telemetry):
            counts["skipped"] += 1
            continue
        if limit is not None and counts["fetched"] >= limit:
            counts["over_limit"] += 1
            continue
        counts["fetched"] += 1
        if dry_run:
            continue
        block = fetch_one(session, api_key, gen_id, throttle=throttle, sleep=sleep)
        if block["status"] == "unavailable" and not_found == "defer":
            counts["pending"] += 1
            continue
        _store(artifact_id, block)
        counts[block["status"]] += 1
    return counts


def fetch_for_job(job_id: Any, *, final: bool = True, dry_run: bool = False) -> dict[str, Any]:
    """Fetch stats for one job's OpenRouter calls with its owner's key. Never raises for missing keys."""
    from backend.credentials.services.resolver import MissingApiKeyError
    from backend.credentials.services.resolver import get_openrouter_key
    from backend.generation.models import GenerationJob

    job = GenerationJob.objects.select_related("created_by").filter(pk=job_id).first()
    if job is None:
        return {"skipped": "no_job"}
    rows = list(candidate_artifacts([job.pk]).values_list("id", "response_payload__id", "telemetry"))
    if not any(needs_stats(t) for _, _, t in rows):
        return {"skipped": "nothing_to_fetch"}
    try:
        api_key = get_openrouter_key(job.created_by)
    except MissingApiKeyError:
        return {"skipped": "no_key"}
    try:
        counts = fetch_for_artifacts(rows, api_key, not_found="unavailable" if final else "defer", dry_run=dry_run)
    except StatsAuthError as exc:
        logger.warning("OpenRouter stats for job %s: %s", job_id, exc)
        return {"skipped": "key_rejected"}
    return dict(counts)


def schedule_for_job(job_id: Any, *, countdown: int = 90) -> bool:
    """Queue :func:`backend.generation.tasks.fetch_call_stats_task` after commit (best effort).

    Only for jobs with ``gen-`` calls; off under tests unless
    ``settings.CALL_TELEMETRY_AUTO_FETCH`` is set. Never raises.
    """
    from django.conf import settings

    enabled = getattr(settings, "CALL_TELEMETRY_AUTO_FETCH", not getattr(settings, "TESTING", False))
    if not enabled:
        return False
    try:
        from backend.generation.models import GenerationArtifact

        if not GenerationArtifact.objects.filter(job_id=job_id, response_payload__id__startswith="gen-").exists():
            return False
        from backend.generation.tasks import fetch_call_stats_task

        def _send() -> None:
            try:
                fetch_call_stats_task.apply_async(args=[str(job_id)], countdown=countdown)
            except Exception:  # noqa: BLE001 - a broker outage must not affect the job
                logger.warning("Could not queue OpenRouter stats for job %s", job_id, exc_info=True)

        transaction.on_commit(_send)
    except Exception:  # noqa: BLE001 - best effort, never raises
        logger.warning("Could not schedule OpenRouter stats for job %s", job_id, exc_info=True)
        return False
    return True
