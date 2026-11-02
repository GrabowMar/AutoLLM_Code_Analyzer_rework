"""Per-call LLM telemetry: ``GenerationArtifact.telemetry`` (shared contract C1).

One normalized dict per LLM call, whatever recorded it. Every key is optional;
an absent key (or ``None``) means *unknown*, never zero::

    {"schema": 1, "source": "gateway" | "orchestrator" | "llm_request",
     "started_at": iso, "ended_at": iso, "duration_s": float,
     "attempts": int, "upstream_retries": int, "status": int,
     "provider": str, "served_model": str, "finish_reason": str,
     "native_finish_reason": str, "system_fingerprint": str,
     "tokens": {"prompt", "completion", "reasoning", "cached", "cache_write"},
     "cost": float | None, "list_cost": float | None, "billing_source": str | None,
     "error": {"status", "code", "message"},          # failed calls only
     "failed_attempts": [{"status", "kind"}],          # template calls, retried attempts
     "local": {...},                                   # proxied/local calls (LLMRequest)
     "openrouter": {...}}                              # services.openrouter_stats

Sources:

* ``from_gateway_call`` -- an agent run's gateway ``llm_calls`` line. ``ts`` is
  the time the gateway started forwarding; ``duration_s`` spans every upstream
  attempt *including the retry backoff sleeps* (2, 4, 8, 16 s), so latency
  statistics must use calls with ``upstream_retries == 0``.
* ``from_chat_response`` / ``from_failed_call`` -- a template-mode call timed
  by the orchestrator around ``chat_completion`` (all client retries
  included; attempts are counted by :class:`OpenRouterAttemptRecorder`).
* ``local_from_llm_request`` / ``from_llm_request`` -- the executor proxy's
  ``LLMRequest`` row of a local/proxied call (OpenCode Go, Ollama, ...).
  :func:`link_llm_requests` pairs agent gateway calls with those rows.

``cost`` is the billed amount the response reported (``usage.cost``: 0.0 on
subscription/local endpoints, whose list price, when the provider reports one,
is ``list_cost``). Nothing here ever overwrites measured data: writers merge
with :func:`merge_additive` (existing values win).
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC
from datetime import datetime
from typing import Any
from typing import Self

SCHEMA = 1
# Retry slack when pairing a gateway call with its proxy request (seconds).
_LINK_SLACK_S = 5.0
_MESSAGE_LIMIT = 500


# ── small helpers ───────────────────────────────────────────────────────


def iso(epoch: float | None) -> str | None:
    """Epoch seconds → ISO-8601 UTC (``None`` stays ``None``)."""
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=UTC).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _put(out: dict[str, Any], key: str, value: Any) -> None:
    """Set ``out[key]`` only for a known (non-empty) value."""
    if value is None or value == "":
        return
    out[key] = value


def _details(usage: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = usage.get(key)
    return value if isinstance(value, dict) else None


def tokens_from_usage(usage: dict[str, Any]) -> dict[str, int | None]:
    """``usage`` → ``{"prompt", "completion", "reasoning", "cached", "cache_write"}``.

    ``reasoning``/``cached``/``cache_write`` are ``None`` when the response
    carried no such detail (unknown), not 0.
    """
    prompt_details = _details(usage, "prompt_tokens_details") or {}
    completion_details = _details(usage, "completion_tokens_details") or {}
    cache_write = _int(prompt_details.get("cache_write_tokens"))
    opencode_tokens = ((usage.get("opencode") or {}) if isinstance(usage.get("opencode"), dict) else {}).get("tokens")
    if cache_write is None and isinstance(opencode_tokens, dict):
        parts = [_int(opencode_tokens.get(k)) for k in ("cache_write_5m", "cache_write_1h")]
        if any(p is not None for p in parts):
            cache_write = sum(p or 0 for p in parts)
    return {
        "prompt": _int(usage.get("prompt_tokens")),
        "completion": _int(usage.get("completion_tokens")),
        "reasoning": _int(completion_details.get("reasoning_tokens")),
        "cached": _int(prompt_details.get("cached_tokens")),
        "cache_write": cache_write,
    }


def _billing(out: dict[str, Any], usage: dict[str, Any]) -> None:
    if "cost" in usage:
        out["cost"] = _num(usage.get("cost"))
    opencode = usage.get("opencode")
    if isinstance(opencode, dict):
        # Subscription endpoints (OpenCode Go): billed cost is 0, the provider
        # reports what the call would cost at list price.
        out["list_cost"] = _num(opencode.get("list_cost"))
        _put(out, "billing_source", opencode.get("billing_source"))


def short_error(err: Any, status: int | None = None) -> dict[str, Any]:
    """An error (dict/str) → ``{"status", "code", "message"}`` (message truncated)."""
    out: dict[str, Any] = {}
    if status is not None:
        out["status"] = status
    if isinstance(err, dict):
        code = err.get("code")
        if code is not None:
            out["code"] = str(code)[:80]
        message = err.get("message") or err.get("error") or ""
        out["message"] = str(message)[:_MESSAGE_LIMIT]
    elif err:
        out["message"] = str(err)[:_MESSAGE_LIMIT]
    return out


def _is_error_status(status: int | None) -> bool:
    return status is not None and status >= 400


# ── builders ────────────────────────────────────────────────────────────


def from_gateway_call(call: dict[str, Any]) -> dict[str, Any]:
    """One agent-gateway ``llm_calls`` line → C1 telemetry (``source: gateway``)."""
    response = call.get("response") if isinstance(call.get("response"), dict) else {}
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    out: dict[str, Any] = {"schema": SCHEMA, "source": "gateway"}
    ts = _num(call.get("ts"))
    duration = _num(call.get("duration_s"))
    _put(out, "started_at", iso(ts))
    if ts is not None and duration is not None:
        _put(out, "ended_at", iso(ts + duration))
    _put(out, "duration_s", duration)
    retries = _int(call.get("upstream_retries"))  # absent on lines from an older gateway
    if retries is not None:
        out["upstream_retries"] = retries
        out["attempts"] = retries + 1
    status = _int(call.get("status"))
    _put(out, "status", status)
    _put(out, "provider", response.get("provider"))
    _put(out, "served_model", response.get("model"))
    _put(out, "finish_reason", response.get("finish_reason"))
    _put(out, "system_fingerprint", response.get("system_fingerprint"))
    if usage:
        out["tokens"] = tokens_from_usage(usage)
        _billing(out, usage)
    err = response.get("error") or call.get("error")
    if err or _is_error_status(status):
        out["error"] = short_error(err, status)
    return out


def local_from_served(served: dict[str, Any] | None) -> dict[str, Any] | None:
    """``ChatResult.served`` of a local/proxied template call → the ``local`` sub-dict."""
    if not isinstance(served, dict) or not served.get("request_id"):
        return None
    out: dict[str, Any] = {"request_id": str(served["request_id"])}
    for key in ("endpoint", "digest", "num_ctx", "upstream_ms", "load_ms", "eval_ms"):
        _put(out, key, served.get(key))
    attempt = _int(served.get("attempt"))
    if attempt is not None:
        out["relay_attempts"] = attempt + 1
    return out


def from_chat_response(
    response: dict[str, Any],
    *,
    started_at: float | None,
    ended_at: float | None,
    attempts: int | None = None,
    failed_attempts: list[dict[str, Any]] | None = None,
    served: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A template-mode chat response (OpenAI/OpenRouter shape) + orchestrator timing → C1."""
    response = response if isinstance(response, dict) else {}
    choices = response.get("choices") if isinstance(response.get("choices"), list) else []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    out: dict[str, Any] = {"schema": SCHEMA, "source": "orchestrator"}
    _timing(out, started_at, ended_at, attempts, failed_attempts)
    out["status"] = 200
    _put(out, "provider", response.get("provider"))
    _put(out, "served_model", response.get("model"))
    _put(out, "finish_reason", choice.get("finish_reason"))
    _put(out, "native_finish_reason", choice.get("native_finish_reason"))
    _put(out, "system_fingerprint", response.get("system_fingerprint"))
    _put(out, "service_tier", response.get("service_tier"))
    if usage:
        out["tokens"] = tokens_from_usage(usage)
        _billing(out, usage)
    local = local_from_served(served)
    if local:
        out["local"] = local
    return out


def from_failed_call(
    *,
    started_at: float | None,
    ended_at: float | None,
    status: int | None,
    message: str,
    attempts: int | None = None,
    failed_attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A template-mode call that raised (after the client's own retries) → C1."""
    out: dict[str, Any] = {"schema": SCHEMA, "source": "orchestrator"}
    _timing(out, started_at, ended_at, attempts, failed_attempts)
    _put(out, "status", status)
    out["error"] = short_error(message, status)
    return out


def _timing(
    out: dict[str, Any],
    started_at: float | None,
    ended_at: float | None,
    attempts: int | None,
    failed_attempts: list[dict[str, Any]] | None,
) -> None:
    _put(out, "started_at", iso(started_at))
    _put(out, "ended_at", iso(ended_at))
    if started_at is not None and ended_at is not None:
        out["duration_s"] = round(max(0.0, ended_at - started_at), 3)
    if attempts is not None:
        out["attempts"] = attempts
        out["upstream_retries"] = max(0, attempts - 1)
    if failed_attempts:
        out["failed_attempts"] = failed_attempts[:20]


def local_from_llm_request(row: Any) -> dict[str, Any]:
    """An executor-proxy ``LLMRequest`` row → the ``local`` sub-dict (timings in ms / s)."""
    out: dict[str, Any] = {"request_id": str(row.pk)}
    endpoint = getattr(row, "endpoint", None)
    _put(out, "endpoint", getattr(endpoint, "slug", None))
    for key in ("upstream_ms", "load_ms", "eval_ms", "response_status"):
        _put(out, key, getattr(row, key, None))
    _put(out, "error_kind", getattr(row, "error_kind", "") or None)
    attempt = _int(getattr(row, "attempt", None))
    if attempt is not None:
        out["relay_attempts"] = attempt + 1
    created, leased = getattr(row, "created_at", None), getattr(row, "leased_at", None)
    completed, delivered = getattr(row, "completed_at", None), getattr(row, "delivered_at", None)
    if created and leased:
        out["queue_wait_s"] = round(max(0.0, (leased - created).total_seconds()), 3)
    if completed and delivered:
        out["delivery_s"] = round(max(0.0, (delivered - completed).total_seconds()), 3)
    eval_ms = _num(getattr(row, "eval_ms", None))
    completion = _int(getattr(row, "completion_tokens", None))
    if eval_ms and eval_ms > 0 and completion:
        # Ollama-native eval_duration: server-side decode speed.
        out["eval_tps"] = round(completion / (eval_ms / 1000), 2)
    return out


def from_llm_request(row: Any) -> dict[str, Any]:
    """A standalone ``LLMRequest`` row → C1 (``source: llm_request``)."""
    out: dict[str, Any] = {"schema": SCHEMA, "source": "llm_request"}
    created, completed = getattr(row, "created_at", None), getattr(row, "completed_at", None)
    if created:
        out["started_at"] = created.isoformat()
    if completed:
        out["ended_at"] = completed.isoformat()
    if created and completed:
        out["duration_s"] = round(max(0.0, (completed - created).total_seconds()), 3)
    attempt = _int(getattr(row, "attempt", None))
    if attempt is not None:
        out["attempts"] = attempt + 1
    status = _int(getattr(row, "response_status", None))
    _put(out, "status", status)
    endpoint = getattr(row, "endpoint", None)
    _put(out, "provider", getattr(endpoint, "slug", None))
    _put(out, "served_model", getattr(row, "model_name", "") or None)
    out["tokens"] = {
        "prompt": _int(getattr(row, "prompt_tokens", None)),
        "completion": _int(getattr(row, "completion_tokens", None)),
        "reasoning": None,
        "cached": None,
        "cache_write": None,
    }
    if _is_error_status(status) or getattr(row, "error", ""):
        out["error"] = short_error(getattr(row, "error", "") or getattr(row, "error_kind", ""), status)
    out["local"] = local_from_llm_request(row)
    return out


def from_artifact_payload(stage: str, response_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Telemetry derivable from an artifact's stored ``response_payload`` alone.

    Agent rows (``agent_call_NNNN``) hold the gateway summary (status,
    duration_s, provider, model, finish_reason, usage) but not the start time
    or retries (those come from the run record: ``backfill_call_telemetry
    --from-logs``). Template rows hold the full chat response but no timing.
    """
    payload = response_payload if isinstance(response_payload, dict) else {}
    if stage.startswith("agent_call_"):
        return from_gateway_call(
            {
                "status": payload.get("status"),
                "duration_s": payload.get("duration_s"),
                "response": payload,
                "error": payload.get("error"),
            },
        )
    if not payload.get("choices") and payload.get("error"):
        out: dict[str, Any] = {"schema": SCHEMA, "source": "orchestrator"}
        out["error"] = short_error(payload.get("error"))
        return out
    out = from_chat_response(payload, started_at=None, ended_at=None)
    if not payload.get("choices"):
        out.pop("status", None)
    return out


def safe(builder: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """``builder(*args, **kwargs)``, or ``{}`` if it fails: telemetry must never break an LLM call's bookkeeping."""
    try:
        return builder(*args, **kwargs)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("Building call telemetry failed", exc_info=True)
        return {}


# ── merging / reading ──────────────────────────────────────────────────


def merge_additive(existing: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """``existing`` with the keys of ``new`` it lacks (or holds as ``None``); nested dicts merge the same way.

    Existing values always win: measured or harness-reported data is never overwritten.
    """
    out = dict(existing or {})
    for key, value in (new or {}).items():
        if value is None:
            continue
        current = out.get(key)
        if current is None:
            out[key] = value
        elif isinstance(current, dict) and isinstance(value, dict):
            out[key] = merge_additive(current, value)
    return out


def effective(stage: str, telemetry: dict[str, Any] | None, response_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Stored telemetry, completed by what the stored payload already says (stored values win)."""
    return merge_additive(telemetry or {}, from_artifact_payload(stage, response_payload))


def is_openrouter_id(value: Any) -> bool:
    """OpenRouter generation ids (``gen-...``): the only ids ``/api/v1/generation`` knows."""
    return isinstance(value, str) and value.startswith("gen-")


# ── linking agent gateway calls to proxy LLMRequest rows ───────────────


def link_llm_requests(calls: list[dict[str, Any]], rows: list[Any]) -> dict[int, Any]:
    """``{seq: LLMRequest}`` for gateway calls that went through the executor proxy.

    The proxy row carries no gateway seq, so a call is paired with the first
    unused row (in creation order) with the same prompt/completion token
    counts whose creation falls inside the call's time window (± a few
    seconds). Unmatched calls get no ``local`` block rather than a guess.
    """
    remaining = sorted(rows, key=lambda r: r.created_at)
    used: set[Any] = set()
    links: dict[int, Any] = {}
    for call in sorted(calls, key=lambda c: int(c.get("seq") or 0)):
        response = call.get("response") if isinstance(call.get("response"), dict) else {}
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        prompt, completion = _int(usage.get("prompt_tokens")), _int(usage.get("completion_tokens"))
        if prompt is None or completion is None or call.get("status") != 200:
            continue
        ts, duration = _num(call.get("ts")), _num(call.get("duration_s")) or 0.0
        for row in remaining:
            if row.pk in used or row.prompt_tokens != prompt or row.completion_tokens != completion:
                continue
            if ts is not None and row.created_at is not None:
                created = row.created_at.timestamp()
                if not (ts - _LINK_SLACK_S <= created <= ts + duration + _LINK_SLACK_S):
                    continue
            used.add(row.pk)
            links[int(call.get("seq") or 0)] = row
            break
    return links


def proxy_requests(job: Any, attempt: int | None = None) -> list[Any]:
    """The executor-proxy ``LLMRequest`` rows of ``job``'s agent run (``attempt`` defaults to the job's)."""
    from backend.inference.models import LLMRequest

    return list(
        LLMRequest.objects.filter(
            job_id=job.pk,
            job_attempt=job.attempt if attempt is None else attempt,
            source=LLMRequest.Source.AGENT_PROXY,
        ).select_related("endpoint"),
    )


def telemetry_for_calls(job: Any, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """C1 telemetry for each gateway call of ``job``'s run (same order as ``calls``).

    Best effort: a failure here leaves the calls without telemetry, never
    fails the ingestion of the run.
    """
    try:
        rows = proxy_requests(job)
        links = link_llm_requests(calls, rows) if rows else {}
        out = []
        for call in calls:
            telemetry = from_gateway_call(call)
            row = links.get(int(call.get("seq") or 0))
            if row is not None:
                telemetry["local"] = local_from_llm_request(row)
            out.append(telemetry)
    except Exception:  # noqa: BLE001 - telemetry must never break run ingestion
        logging.getLogger(__name__).warning("Call telemetry for job %s failed", job.pk, exc_info=True)
        return [{} for _ in calls]
    return out


# ── counting template-mode OpenRouter attempts ─────────────────────────

_OPENROUTER_LOGGER = "backend.generation.services.openrouter_client"
_RETRY_LOGGED_STATUSES = frozenset({408, 429})
_SERVER_ERROR = 500


class OpenRouterAttemptRecorder(logging.Handler):
    """Counts ``OpenRouterClient.chat_completion`` retries on the current thread.

    The client logs one WARNING per failed-and-retried attempt (429, 5xx,
    timeout, connection error) and returns or raises without exposing its
    attempt count; this handler listens to those records for the calling
    thread only (the executor runs several jobs in parallel threads).
    ``attempts`` is ``None`` when the logger would drop WARNING records.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.thread_id = threading.get_ident()
        self.failed: list[dict[str, Any]] = []
        self.enabled = False
        self._logger = logging.getLogger(_OPENROUTER_LOGGER)

    def __enter__(self) -> Self:
        self.enabled = self._logger.isEnabledFor(logging.WARNING) and not self._logger.disabled
        self._logger.addHandler(self)
        return self

    def __exit__(self, *exc: object) -> None:
        self._logger.removeHandler(self)

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.thread_id:
            return
        msg = str(record.msg)
        args = record.args if isinstance(record.args, tuple) else ()
        if msg.startswith("Rate limited"):
            self.failed.append({"status": 429, "kind": "rate_limited"})
        elif msg.startswith("Server error"):
            self.failed.append({"status": _int(args[0]) if args else None, "kind": "server_error"})
        elif msg.startswith("Timeout"):
            self.failed.append({"status": 408, "kind": "timeout"})
        elif msg.startswith("Connection error"):
            self.failed.append({"status": None, "kind": "connection"})

    def attempts(self, *, succeeded: bool, error: Exception | None = None) -> int | None:
        """Total attempts: the logged retries plus the final one (when it was not itself logged)."""
        if not self.enabled:
            return None
        if succeeded:
            return len(self.failed) + 1
        # A final retryable failure (429/408/5xx/connection) was logged like the
        # retries before it; a non-retryable error (4xx) raised unlogged.
        status = getattr(error, "status_code", None)
        logged = status in _RETRY_LOGGED_STATUSES or (status or 0) >= _SERVER_ERROR
        logged = logged or str(error).startswith("Connection error")
        if logged and self.failed:
            return len(self.failed)
        return len(self.failed) + 1


# ── reading calls back (endpoints, CSV export) ────────────────────────

# Payload keys read through JSON key transforms (never the content/reasoning text).
_PAYLOAD_KEYS = (
    "status",
    "duration_s",
    "id",
    "provider",
    "model",
    "finish_reason",
    "usage",
    "error",
    "system_fingerprint",
    "service_tier",
)
_CHOICE_KEYS = ("finish_reason", "native_finish_reason")


def call_values(artifacts: Any):
    """``artifacts`` as light dicts (telemetry + the few payload keys it is derived from)."""
    fields = ["id", "job_id", "stage", "telemetry", "created_at"]
    fields += [f"response_payload__{k}" for k in _PAYLOAD_KEYS]
    fields += [f"response_payload__choices__0__{k}" for k in _CHOICE_KEYS]
    return artifacts.values(*fields)


def _light_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = {k: row.get(f"response_payload__{k}") for k in _PAYLOAD_KEYS}
    choice = {k: row.get(f"response_payload__choices__0__{k}") for k in _CHOICE_KEYS}
    if any(v is not None for v in choice.values()):
        payload["choices"] = [choice]
    return {k: v for k, v in payload.items() if v is not None}


def iter_calls(artifacts: Any, *, chunk_size: int = 500):
    """Yield one dict per call: ids, stage, ``response_id`` and the effective telemetry."""
    for row in call_values(artifacts).iterator(chunk_size=chunk_size):
        payload = _light_payload(row)
        yield {
            "artifact_id": row["id"],
            "job_id": row["job_id"],
            "stage": row["stage"],
            "created_at": row["created_at"],
            "response_id": payload.get("id"),
            "telemetry": effective(row["stage"], row["telemetry"], payload),
        }


def call_seq(stage: str) -> int | None:
    """The gateway seq of an agent call artifact (``agent_call_0042`` → 42)."""
    if stage.startswith("agent_call_"):
        try:
            return int(stage.rsplit("_", 1)[1])
        except ValueError:
            return None
    return None


def _ok(t: dict[str, Any]) -> bool:
    status = t.get("status")
    return not t.get("error") and (status is None or status < 400)


def _quantiles(values: list[float], *, p90: bool = False, digits: int = 1) -> dict[str, Any]:
    from backend.statistics.services.inference import percentile

    out: dict[str, Any] = {"n": len(values), "median": None}
    if p90:
        out["p90"] = None
    if values:
        out["median"] = round(percentile(values, 0.5), digits)
        if p90:
            out["p90"] = round(percentile(values, 0.9), digits)
    return out


def _ratio(numerator: float, denominator: float, n: int) -> dict[str, Any]:
    return {"value": round(numerator / denominator, 4) if denominator > 0 else None, "n": n}


def summarize_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Descriptive per-call statistics for one group of calls (no tests, n everywhere)."""
    ttft, gen_ms, decode, durations, e2e = [], [], [], [], []
    cached_sum = cached_prompt = cached_n = 0
    reasoning_sum = reasoning_completion = reasoning_n = 0
    retried = retry_known = errors = fallbacks_n = fallback_known = 0
    eligible = covered = unavailable = 0
    finish: dict[str, int] = {}
    modes: dict[str, int] = {}
    served: dict[str, int] = {}
    permaslugs: dict[str, int] = {}
    jobs: set[Any] = set()
    for call in calls:
        t = call["telemetry"]
        jobs.add(call["job_id"])
        mode = call.get("mode")
        if mode:
            modes[mode] = modes.get(mode, 0) + 1
        orouter = t.get("openrouter") if isinstance(t.get("openrouter"), dict) else {}
        if is_openrouter_id(call.get("response_id")):
            eligible += 1
        if orouter.get("status") == "ok":
            covered += 1
            if (v := _num(orouter.get("latency_ms"))) is not None:
                ttft.append(v)
            if (v := _num(orouter.get("generation_time_ms"))) is not None:
                gen_ms.append(v)
            if (v := _num(orouter.get("decode_tps"))) is not None:
                decode.append(v)
            if (f := _int(orouter.get("fallbacks"))) is not None:
                fallback_known += 1
                fallbacks_n += 1 if f > 0 else 0
        elif orouter.get("status") == "unavailable":
            unavailable += 1
        retries = _int(t.get("upstream_retries"))
        if retries is not None:
            retry_known += 1
            retried += 1 if retries > 0 else 0
        if not _ok(t):
            errors += 1
            continue
        if t.get("served_model"):
            served[t["served_model"]] = served.get(t["served_model"], 0) + 1
        if orouter.get("model_permaslug"):
            slug = orouter["model_permaslug"]
            permaslugs[slug] = permaslugs.get(slug, 0) + 1
        if t.get("finish_reason"):
            finish[t["finish_reason"]] = finish.get(t["finish_reason"], 0) + 1
        tokens = t.get("tokens") if isinstance(t.get("tokens"), dict) else {}
        prompt, completion = _int(tokens.get("prompt")), _int(tokens.get("completion"))
        if (cached := _int(tokens.get("cached"))) is not None and prompt:
            cached_sum, cached_prompt, cached_n = cached_sum + cached, cached_prompt + prompt, cached_n + 1
        if (reasoning := _int(tokens.get("reasoning"))) is not None and completion:
            reasoning_sum += reasoning
            reasoning_completion += completion
            reasoning_n += 1
        duration = _num(t.get("duration_s"))
        if duration is not None and retries == 0:
            # Retried calls are left out (their duration includes backoff sleeps),
            # and so are calls whose retry count was not recorded (older gateway).
            durations.append(duration)
            if completion and duration > 0:
                e2e.append(completion / duration)
    n = len(calls)
    return {
        "n_calls": n,
        "n_jobs": len(jobs),
        "modes": modes,
        "ttft_ms": _quantiles(ttft, p90=True, digits=0),
        "generation_time_ms": _quantiles(gen_ms, digits=0),
        "decode_tps": _quantiles(decode, digits=1),
        "duration_s": _quantiles(durations, p90=True, digits=3),
        "e2e_output_tps": _quantiles(e2e, digits=1),
        "cache_hit_ratio": _ratio(cached_sum, cached_prompt, cached_n),
        "reasoning_share": _ratio(reasoning_sum, reasoning_completion, reasoning_n),
        "retry_rate": _ratio(retried, retry_known, retry_known),
        "error_rate": _ratio(errors, n, n),
        "fallback_rate": _ratio(fallbacks_n, fallback_known, fallback_known),
        "finish_reasons": dict(sorted(finish.items(), key=lambda kv: -kv[1])),
        "served_models": dict(sorted(served.items(), key=lambda kv: -kv[1])),
        # Dated model versions OpenRouter reports (calls with stats only).
        "model_permaslugs": dict(sorted(permaslugs.items(), key=lambda kv: -kv[1])),
        "coverage": {
            "openrouter_ok": covered,
            "openrouter_unavailable": unavailable,
            "openrouter_eligible": eligible,
            "share": round(covered / n, 4) if n else None,
            "share_of_eligible": round(covered / eligible, 4) if eligible else None,
        },
    }


PERFORMANCE_NOTES = (
    "TTFT is OpenRouter's own measurement (its /generation record): time to first token including the "
    "provider's queueing and prefill. Decode tok/s = completion tokens / (generation time - TTFT); n/a when "
    "that span is under 250 ms (the provider delivered the output in one burst, which cannot be timed).",
    "Call duration is measured by the platform (gateway or orchestrator) around one call; calls with "
    "upstream retries, or whose retry count was not recorded (older gateway logs), are left out of duration "
    "and end-to-end tok/s because a retried call's time includes backoff.",
    "End-to-end output tok/s = completion tokens (reasoning included) / call duration: it includes "
    "prefill and queueing, so it is not a decode speed.",
    "Cache-hit ratio = cached prompt tokens / prompt tokens; reasoning share = reasoning tokens / "
    "completion tokens; both over calls whose response reported the breakdown.",
    "OpenCode Go and local endpoints have no OpenRouter record: their TTFT/decode values are n/a, "
    "not zero. Descriptive only; providers are not randomized, so differences are associations.",
)


def provider_performance(jobs: Any, *, mode: str | None = None) -> dict[str, Any]:
    """Per upstream provider (and overall) call statistics over ``jobs``' LLM calls."""
    from backend.generation.models import GenerationArtifact

    job_modes = dict(jobs.values_list("id", "mode"))
    artifacts = GenerationArtifact.objects.filter(job_id__in=list(job_modes))
    if mode:
        artifacts = artifacts.filter(job__mode=mode)
    calls: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for call in iter_calls(artifacts.order_by("job_id", "id")):
        call["mode"] = job_modes.get(call["job_id"])
        t = call["telemetry"]
        provider = t.get("provider") or (t.get("openrouter") or {}).get("provider_name") or "unknown"
        calls.append(call)
        groups.setdefault(str(provider), []).append(call)
    providers = [{"provider": name, **summarize_calls(group)} for name, group in groups.items()]
    providers.sort(key=lambda p: (-p["n_calls"], p["provider"]))
    return {
        "mode": mode or None,
        "overall": summarize_calls(calls),
        "providers": providers,
        "notes": list(PERFORMANCE_NOTES),
    }


CSV_COLUMNS = (
    "job_id",
    "experiment_id",
    "condition_label",
    "repeat_index",
    "model_id",
    "mode",
    "harness",
    "seq",
    "stage",
    "started_at",
    "duration_s",
    "attempts",
    "upstream_retries",
    "status",
    "provider",
    "served_model",
    "model_permaslug",
    "ttft_ms",
    "generation_time_ms",
    "decode_tps",
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "cost",
    "list_cost",
    "catalog_cost",
    "billing_source",
    "finish_reason",
    "native_finish_reason",
    "fallbacks",
    "router_retries",
    "openrouter_status",
    "telemetry_source",
    "response_id",
    "error_code",
)


def _job_meta(jobs: Any) -> dict[Any, dict[str, Any]]:
    rows = jobs.values(
        "id",
        "experiment_id",
        "condition__label",
        "repeat_index",
        "model__model_id",
        "mode",
        "resolved_bundle__harness__name",
        "resolved_bundle__llm__input_price_per_token",
        "resolved_bundle__llm__output_price_per_token",
    )
    return {
        r["id"]: {
            "job_id": str(r["id"]),
            "experiment_id": str(r["experiment_id"]) if r["experiment_id"] else None,
            "condition_label": r["condition__label"] or None,
            "repeat_index": r["repeat_index"],
            "model_id": r["model__model_id"],
            "mode": r["mode"],
            "harness": r["resolved_bundle__harness__name"],
            "_prices": (
                _num(r["resolved_bundle__llm__input_price_per_token"]),
                _num(r["resolved_bundle__llm__output_price_per_token"]),
            ),
        }
        for r in rows
    }


def catalog_cost(tokens: dict[str, Any], prices: tuple[float | None, float | None]) -> float | None:
    """Prompt/completion tokens at the catalog headline prices frozen in the job snapshot.

    ``None`` when a price is missing or 0 (local/subscription endpoints are not
    priced) or a token count is unknown. It ignores cache discounts and the
    serving provider's own prices: compare it with ``cost`` as a ratio.
    """
    price_in, price_out = prices
    prompt, completion = _int(tokens.get("prompt")), _int(tokens.get("completion"))
    if not price_in or not price_out or prompt is None or completion is None:
        return None
    return round(prompt * price_in + completion * price_out, 10)


def csv_rows(jobs: Any):
    """Yield the header, then one row per LLM call of ``jobs`` (no message content)."""
    from backend.generation.models import GenerationArtifact

    meta = _job_meta(jobs)
    yield list(CSV_COLUMNS)
    order: dict[Any, int] = {}
    artifacts = GenerationArtifact.objects.filter(job_id__in=list(meta)).order_by("job_id", "id")
    for call in iter_calls(artifacts):
        job = meta[call["job_id"]]
        t = call["telemetry"]
        orouter = t.get("openrouter") if isinstance(t.get("openrouter"), dict) else {}
        tokens = t.get("tokens") if isinstance(t.get("tokens"), dict) else {}
        order[call["job_id"]] = order.get(call["job_id"], 0) + 1
        seq = call_seq(call["stage"]) or order[call["job_id"]]
        error = t.get("error") if isinstance(t.get("error"), dict) else {}
        values = {
            **job,
            "seq": seq,
            "stage": call["stage"],
            "started_at": t.get("started_at"),
            "duration_s": t.get("duration_s"),
            "attempts": t.get("attempts"),
            "upstream_retries": t.get("upstream_retries"),
            "status": t.get("status"),
            "provider": t.get("provider") or orouter.get("provider_name"),
            "served_model": t.get("served_model"),
            "model_permaslug": orouter.get("model_permaslug"),
            "ttft_ms": orouter.get("latency_ms"),
            "generation_time_ms": orouter.get("generation_time_ms"),
            "decode_tps": orouter.get("decode_tps"),
            "prompt_tokens": tokens.get("prompt"),
            "completion_tokens": tokens.get("completion"),
            "reasoning_tokens": tokens.get("reasoning"),
            "cached_tokens": tokens.get("cached"),
            "cache_write_tokens": tokens.get("cache_write"),
            "cost": t.get("cost"),
            "list_cost": t.get("list_cost"),
            "catalog_cost": catalog_cost(tokens, job["_prices"]),
            "billing_source": t.get("billing_source"),
            "finish_reason": t.get("finish_reason"),
            "native_finish_reason": t.get("native_finish_reason") or orouter.get("native_finish_reason"),
            "fallbacks": orouter.get("fallbacks"),
            "router_retries": orouter.get("router_retries"),
            "openrouter_status": orouter.get("status"),
            "telemetry_source": t.get("source"),
            "response_id": call["response_id"],
            "error_code": error.get("code") or (error.get("status") if error else None),
        }
        yield ["" if values[c] is None else values[c] for c in CSV_COLUMNS]
