"""Derived token metrics: the output rate of an LLM call and the reasoning-token total of an attempt.

One definition for every route (OpenRouter directly, or the lab proxy in front of the native Anthropic API,
OpenCode Go or a local endpoint). The agentic runner (``agentic.runner.call_telemetry`` / ``attempt_usage``),
the calls API (``runs.api.list_run_calls``), the provider statistics
(``services.call_telemetry.summarize_calls``) and ``manage.py rederive_token_metrics`` all derive through
this module; nothing here touches the database.

Output rate
-----------
``telemetry["tokens_per_s"]`` of one call::

    tokens_per_s = completion_tokens / (duration_s - rate_wait_s)

This is the **end-to-end output rate**: every output token the provider counted (reasoning/thinking
included) over the time the gateway spent on the call, from accepting the request to the end of the answer,
minus the time the call waited in the gateway's own rate limiter. It contains the provider's queueing, the
prefill and the time to first token, so it is the throughput the harness experienced, **not** a decode
speed. ``telemetry["tokens_per_s_basis"] == "e2e"`` marks a value derived this way.

It is measured only for a *clean* call: HTTP status < 400 with a usage block, no error, no transport error,
the client still connected, usage reported by the upstream (not estimated by the gateway), at least one
completion token, ``upstream_retries == 0`` and a positive time base. A retried call's duration contains
the failed attempts and the backoff sleeps, and an unrecorded retry count is unknown, not zero. Every other
call has **no** rate (``None`` / key absent), never 0.

``RunAttempt.usage["tokens_per_s_p50"]`` is the median of those per-call rates over the attempt
(``statistics.median``: the mean of the two middle values of an even count) and ``tokens_per_s_calls`` the
number of calls behind it; ``None`` and 0 when no call qualifies.

Why the gateway's own ``tokens_per_s`` is not used: on a stream the gateway divides by
``ended - first_byte``, and ``first_byte`` is the first upstream byte of any kind. The lab proxy answers a
streaming request with keep-alive comments while it waits for the complete answer and then writes that
answer in one burst, so the span is a few milliseconds (hundreds of thousands of "tokens per second") or
starts at a keep-alive; an SSE upstream may send a comment line first as well. For the same reason the
gateway's ``ttft_ms`` is stored as ``first_byte_ms`` (time to the first upstream byte, whatever it was):
it is not a time to first token and no statistic is derived from it.

Reasoning tokens
----------------
``RunAttempt.usage["reasoning_tokens"]`` (copied to ``GenerationRun.metrics``) is the sum of the per-call
reasoning-token counts **when every call that produced output reported one**, and ``None`` (not measured)
otherwise. An unreported count is never 0: the native Anthropic API, for one, bills thinking inside
``output_tokens`` and reports no separate count, so its runs carry ``None``. A partial sum would be a lower
bound presented as a total, which is why one unreporting call makes the whole attempt unmeasured (the
per-call values stay in ``telemetry["tokens"]["reasoning"]``).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any
from typing import Final

__all__ = [
    "RATE_BASIS",
    "flagged_unclean",
    "median_rate",
    "output_tokens_per_s",
    "rate_facts",
    "reasoning_total",
    "rederive_telemetry",
    "rederive_usage",
    "telemetry_reasoning_total",
    "telemetry_tokens_per_s",
]

# ``telemetry["tokens_per_s_basis"]`` of a rate derived by this module.
RATE_BASIS: Final = "e2e"
_RATE_DIGITS: Final = 3
_ERROR_STATUS: Final = 400
# Facts that make a call unclean for the rate; stored in the telemetry only when true.
_UNCLEAN_FLAGS: Final = ("usage_estimated", "transport_error", "client_gone")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def output_tokens_per_s(
    *,
    completion_tokens: Any,
    duration_s: Any,
    upstream_retries: Any,
    rate_wait_s: Any = None,
) -> float | None:
    """``completion_tokens / (duration_s - rate_wait_s)`` of one call, or ``None`` when it cannot be measured.

    ``None`` without completion tokens, without a duration, when the call was retried upstream or its retry
    count is unknown, and when the time base is not positive. An unrecorded rate-limiter wait counts as
    none. The caller decides whether the call is clean (:func:`telemetry_tokens_per_s` does).
    """
    completion = _count(completion_tokens)
    duration = _number(duration_s)
    if not completion or duration is None or _count(upstream_retries) != 0:
        return None
    seconds = duration - max(_number(rate_wait_s) or 0.0, 0.0)
    if seconds <= 0:
        return None
    return round(completion / seconds, _RATE_DIGITS)


def rate_facts(call: Mapping[str, Any]) -> dict[str, Any]:
    """What a gateway ``calls.jsonl`` record says that the rate needs, under its telemetry keys.

    ``upstream_retries`` / ``attempts`` and ``rate_wait_s`` when recorded; ``usage_estimated``,
    ``transport_error`` and ``client_gone`` only when true (absent = not flagged).
    """
    out: dict[str, Any] = {}
    retries = _count(call.get("upstream_retries"))
    if retries is not None:
        out["upstream_retries"] = retries
        out["attempts"] = retries + 1
    wait = _number(call.get("rate_wait_s"))
    if wait is not None:
        out["rate_wait_s"] = wait
    for flag in _UNCLEAN_FLAGS:
        if call.get(flag):
            out[flag] = True
    return out


def flagged_unclean(telemetry: Mapping[str, Any]) -> bool:
    """The telemetry says the call's usage was estimated, its transport failed or its client left."""
    return any(telemetry.get(flag) for flag in _UNCLEAN_FLAGS)


def telemetry_tokens_per_s(telemetry: Mapping[str, Any]) -> float | None:
    """The end-to-end output rate of one call from its per-call telemetry (``None`` for an unclean call)."""
    status = telemetry.get("status")
    if isinstance(status, bool) or not isinstance(status, int) or status >= _ERROR_STATUS:
        return None
    if telemetry.get("error") or flagged_unclean(telemetry):
        return None
    tokens = telemetry.get("tokens")
    if not isinstance(tokens, Mapping):
        return None
    return output_tokens_per_s(
        completion_tokens=tokens.get("completion"),
        duration_s=telemetry.get("duration_s"),
        upstream_retries=telemetry.get("upstream_retries"),
        rate_wait_s=telemetry.get("rate_wait_s"),
    )


def median_rate(rates: Iterable[float]) -> float | None:
    """The median of per-call rates (``None`` when there is none)."""
    values = list(rates)
    return round(statistics.median(values), _RATE_DIGITS) if values else None


def reasoning_total(calls: Iterable[tuple[Any, Any]]) -> int | None:
    """The reasoning-token total over ``(completion_tokens, reasoning_tokens)`` pairs, one per usage block.

    ``None`` when no call reported a count, or when a call that produced output did not: the total is then
    not measured (never 0, never a partial sum).
    """
    total = reported = 0
    for completion, reasoning in calls:
        value = _count(reasoning)
        if value is not None:
            total += value
            reported += 1
        elif _count(completion):
            return None
    return total if reported else None


def telemetry_reasoning_total(telemetries: Iterable[Mapping[str, Any]]) -> int | None:
    """:func:`reasoning_total` over the stored per-call telemetry of one attempt."""
    return reasoning_total(
        (tokens.get("completion"), tokens.get("reasoning"))
        for telemetry in telemetries
        if isinstance(tokens := telemetry.get("tokens"), Mapping)
    )


def rederive_telemetry(
    telemetry: Mapping[str, Any],
    evidence_call: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One stored per-call telemetry under the current definitions (a new dict; nothing else changes).

    Rate facts the row lacks are taken from the call's gateway record when one is given (stored values
    win), the gateway's ``ttft_ms`` becomes ``first_byte_ms`` and ``tokens_per_s`` is replaced by the
    end-to-end rate (removed when the call has none). Idempotent.
    """
    out = dict(telemetry)
    if evidence_call is not None:
        for key, value in rate_facts(evidence_call).items():
            out.setdefault(key, value)
    if "ttft_ms" in out:
        first_byte = out.pop("ttft_ms")
        out.setdefault("first_byte_ms", first_byte)
    out.pop("tokens_per_s", None)
    out.pop("tokens_per_s_basis", None)
    rate = telemetry_tokens_per_s(out)
    if rate is not None:
        out["tokens_per_s"] = rate
        out["tokens_per_s_basis"] = RATE_BASIS
    return out


def rederive_usage(usage: Mapping[str, Any], telemetries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """``RunAttempt.usage`` with the derived keys recomputed from the attempt's per-call telemetry.

    Only ``tokens_per_s_p50``, ``tokens_per_s_calls`` and ``reasoning_tokens`` change; ``telemetries`` are
    expected under the current definitions (:func:`rederive_telemetry`).
    """
    rows = list(telemetries)
    rates = [rate for telemetry in rows if (rate := telemetry_tokens_per_s(telemetry)) is not None]
    out = dict(usage)
    out["tokens_per_s_p50"] = median_rate(rates)
    out["tokens_per_s_calls"] = len(rates)
    out["reasoning_tokens"] = telemetry_reasoning_total(rows)
    return out
