"""Live spend: gateway ``state.json`` polling, local-proxy usage and SSE spend events (spec 4.5, 4.7).

The sandbox supervisor (WP-08) calls :func:`poll` every :func:`poll_interval_s` seconds for a running
attempt, passing the parsed ``state.json`` (or a reader such as :func:`docker_state_reader`). ``poll``
turns the gateway's running totals into ledger ``usage`` deltas: the delta is the state minus what the
ledger already holds for that attempt's live and proxy rows (a gateway call through the local proxy is
booked by the proxy too), written as ``live:{run}:{a}:{seq}``. Two concurrent
polls compute the same ``seq`` and the second write is a no-op, so usage is never counted twice. The
final settlement (``ledger.settle_attempt``) replaces the live deltas with the billed total.

Local endpoints report per request instead: :func:`record_proxy_usage` (``proxy:{llm_request_id}``).
Subscription (``list_price``) and local (``free``) endpoints bill nothing: their ledger rows carry 0 billed
USD and the list-price cost, which is what list-basis caps and quotas count.

Events (validated by ``contracts.events``): ``study:<id>`` ``spend`` ``{spent, spent_list, reserved, delta,
run_id}`` and, on crossing a ``study.alert_at`` fraction of the cap, ``budget_alert {fraction}``;
``run:<id>`` ``usage``.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django.db.models import Sum

from backend.generation.budget import constants as k
from backend.generation.budget import ledger
from backend.generation.models import GenerationRun
from backend.generation.models import SpendLedgerEntry
from backend.generation.models.choices import LedgerKind
from backend.generation.models.choices import LedgerSource

__all__ = [
    "docker_state_reader",
    "poll",
    "poll_interval_s",
    "publish_spend",
    "record_live",
    "record_proxy_usage",
]

logger = logging.getLogger(__name__)
ZERO = Decimal(0)


def poll_interval_s(running: int) -> float:
    """``BUDGET_LIVE_POLL_S x ceil(running / 8)`` (at least the base interval; the setting is read now)."""
    from django.conf import settings

    base = float(getattr(settings, "BUDGET_LIVE_POLL_S", k.BUDGET_LIVE_POLL_S))
    return base * max(1, math.ceil(max(0, running) / k.LIVE_POLL_FANOUT))


def docker_state_reader(container: Any) -> Callable[[GenerationRun], dict[str, Any]]:
    """A reader that runs ``cat /logs/state.json`` in the gateway container (a docker SDK container)."""

    def read(_run: GenerationRun) -> dict[str, Any]:
        result = container.exec_run(["cat", "/logs/state.json"], demux=False)
        output = result.output if hasattr(result, "output") else result[1]
        return json.loads(output.decode("utf-8") if isinstance(output, bytes) else output)

    return read


def _cost_basis(run: GenerationRun) -> str:
    if not ledger._has_manifest(run):  # noqa: SLF001
        return "billed"
    endpoint = ((run.manifest.manifest or {}).get("model") or {}).get("endpoint") or {}
    return str(endpoint.get("cost_basis") or "billed")


def _num(state: dict[str, Any], key: str) -> float:
    value = state.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def record_live(run: GenerationRun, *, attempt: int, state: dict[str, Any], seq: int) -> dict[str, Any]:
    """The supervisor's hook (WP-08): one ``state.json`` poll of ``attempt`` as poll number ``seq``.

    Same delta rule as :func:`poll`, with the caller's ``attempt`` and ``seq`` in the dedupe key
    (``live:{run}:{attempt}:{seq}``); publishes the study ``spend`` event (the supervisor publishes ``usage``).
    """
    return _record(run, attempt=attempt, state=state, seq=seq, publish_usage=False)


def poll(
    run: GenerationRun,
    *,
    state: dict[str, Any] | None = None,
    read: Callable[[GenerationRun], dict[str, Any]] | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    """Write the usage delta of one live poll; returns ``{delta_usd, delta_list_usd, seq, state}``.

    ``state`` is the gateway's ``state.json``; without it ``read(run)`` is called. A poll with no new usage
    writes nothing.
    """
    if state is None:
        if read is None:
            msg = "poll needs the gateway state or a reader"
            raise ValueError(msg)
        state = read(run)
    return _record(run, attempt=ledger.attempt_no(run), state=state, seq=None, publish_usage=publish, publish=publish)


def _record(
    run: GenerationRun,
    *,
    attempt: int,
    state: dict[str, Any],
    seq: int | None,
    publish_usage: bool,
    publish: bool = True,
) -> dict[str, Any]:
    a = attempt
    live = SpendLedgerEntry.objects.filter(run=run, attempt=a, kind=LedgerKind.USAGE, source=LedgerSource.GATEWAY_LIVE)
    # The gateway's totals include the calls it sent through the local LLM proxy, which the proxy already
    # booked per request (``proxy:{id}``, :func:`record_proxy_usage`): the delta is the state minus every
    # usage row of the attempt, so a proxied call is never counted twice while the run is in flight.
    booked = SpendLedgerEntry.objects.filter(
        run=run,
        attempt=a,
        kind=LedgerKind.USAGE,
        source__in=(LedgerSource.GATEWAY_LIVE, LedgerSource.PROXY),
    )
    agg = booked.aggregate(
        s=Sum("amount_usd"),
        lst=Sum("list_cost_usd"),
        tin=Sum("tokens_in"),
        tout=Sum("tokens_out"),
        n=Sum("requests"),
    )
    basis = _cost_basis(run)
    list_total = ledger.to_decimal(_num(state, "list_cost") or _num(state, "cost"))
    billed_total = ZERO if basis in ("list_price", "free") else ledger.to_decimal(_num(state, "cost"))
    requests = sum(v for v in (state.get("requests_by_class") or {}).values() if isinstance(v, int))
    delta: dict[str, Any] = {
        "usd": billed_total - (agg["s"] or ZERO),
        "list": list_total - (agg["lst"] or ZERO),
        "tin": int(_num(state, "prompt_tokens")) - int(agg["tin"] or 0),
        "tout": int(_num(state, "completion_tokens")) - int(agg["tout"] or 0),
        "n": requests - int(agg["n"] or 0),
    }
    seq = seq if seq is not None else live.count() + 1
    out: dict[str, Any] = {"delta_usd": 0.0, "delta_list_usd": 0.0, "seq": seq - 1, "state": state}
    if all(v <= 0 for v in delta.values()):
        return out
    before = ledger.study_totals(run.study_id) if run.study_id else None
    ledger.record_usage(
        run,
        attempt=a,
        source=LedgerSource.GATEWAY_LIVE,
        amount_usd=max(ZERO, delta["usd"]),
        list_cost_usd=max(ZERO, delta["list"]),
        tokens_in=max(0, delta["tin"]),
        tokens_out=max(0, delta["tout"]),
        requests=max(0, delta["n"]),
        dedupe_key=f"live:{run.pk}:{a}:{seq}",
    )
    out.update(delta_usd=float(max(ZERO, delta["usd"])), delta_list_usd=float(max(ZERO, delta["list"])), seq=seq)
    if publish:
        publish_spend(
            run,
            delta=float(max(ZERO, delta["usd"] if (before and before.cap_basis == "billed") else delta["list"])),
            before=before,
        )
        if publish_usage:
            _publish_usage(run, a, state)
    return out


def record_proxy_usage(llm_request: Any, *, publish: bool = True) -> SpendLedgerEntry | None:
    """Usage of one completed local-proxy request (``proxy:{id}``): the list price per the manifest pricing
    (cache-aware when it freezes cache prices), billed for a ``billed`` endpoint and 0 billed USD for
    ``free`` / ``list_price`` ones, tokens against token caps. ``None`` for requests without a run."""
    run_id = getattr(llm_request, "job_id", None)
    if run_id is None:
        return None
    run = GenerationRun.objects.select_related("manifest", "model", "study", "cell").filter(pk=run_id).first()
    if run is None:
        return None
    prompt = int(getattr(llm_request, "prompt_tokens", 0) or 0)
    completion = int(getattr(llm_request, "completion_tokens", 0) or 0)
    list_cost = _proxy_list_cost(run, llm_request, prompt, completion)
    basis = _cost_basis(run)
    billed = 0.0 if basis in ("free", "list_price") else list_cost
    before = ledger.study_totals(run.study_id) if run.study_id else None
    entry = ledger.record_usage(
        run,
        attempt=int(getattr(llm_request, "job_attempt", 0) or 0) + 1,
        source=LedgerSource.PROXY,
        amount_usd=billed,
        list_cost_usd=list_cost,
        tokens_in=prompt,
        tokens_out=completion,
        requests=1,
        dedupe_key=f"proxy:{llm_request.pk}",
    )
    if publish:
        publish_spend(run, delta=billed if (before and before.cap_basis == "billed") else list_cost, before=before)
    return entry


def _proxy_list_cost(run: GenerationRun, llm_request: Any, prompt: int, completion: int) -> float:
    """The list cost of one proxied request at the manifest's frozen pricing, cache-aware.

    With cache-read / cache-write prices in the manifest (the native Anthropic route), the response's
    ``prompt_tokens_details`` split is priced by the gateway's own ``budget_rules.Pricing``; without them
    this is ``prompt * input + completion * output``, as before.
    """
    pricing = ledger._pricing(run)  # noqa: SLF001
    if not pricing:
        return 0.0
    frozen = ((run.manifest.manifest or {}).get("model") or {}).get("pricing") or {}
    cached_price = frozen.get("cached_input_per_mtok")
    write_price = frozen.get("cache_write_per_mtok")
    response = getattr(llm_request, "response", None)
    usage = response.get("usage") if isinstance(response, dict) else None
    if (cached_price is None and write_price is None) or not isinstance(usage, dict):
        return (prompt * pricing[0] + completion * pricing[1]) / 1_000_000
    from backend.inference.proxy_server import budget_rules

    rules = budget_rules()
    split = rules.chat_usage(usage) or {}
    return rules.Pricing(
        input_per_mtok=pricing[0],
        output_per_mtok=pricing[1],
        cached_input_per_mtok=None if cached_price is None else float(cached_price),
        cache_write_per_mtok=None if write_price is None else float(write_price),
    ).price(
        int(split.get("prompt_tokens") or prompt),
        int(split.get("completion_tokens") or completion),
        int(split.get("cached_tokens") or 0),
        int(split.get("cache_write_tokens") or 0),
    )


def publish_spend(run: GenerationRun, *, delta: float, before: ledger.StudyTotals | None = None) -> None:
    """``spend`` on ``study:<id>``, plus ``budget_alert`` when a ``study.alert_at`` fraction was crossed."""
    if not run.study_id:
        return
    from backend.generation.budget import claims
    from backend.realtime.events import publish_study_event

    try:
        totals = ledger.study_totals(run.study_id)
        publish_study_event(
            run.study_id,
            {
                "type": "spend",
                "spent": float(totals.spent_billed),
                "spent_list": float(totals.spent_list),
                "reserved": float(max(ZERO, totals.held)),
                "delta": float(delta),
                "run_id": str(run.pk),
            },
        )
        study = run.study
        cap = claims.study_cap(study)
        if not cap or before is None:
            return
        policy = ledger.run_policy(run)
        for fraction in sorted(policy.study.alert_at):
            threshold = cap * ledger.to_decimal(fraction)
            if before.spent < threshold <= totals.spent:
                publish_study_event(run.study_id, {"type": "budget_alert", "fraction": float(fraction)})
    except Exception:
        logger.exception("Publishing the spend of run %s failed", run.pk)


def _publish_usage(run: GenerationRun, attempt: int, state: dict[str, Any]) -> None:
    from backend.realtime.events import publish_run_event

    requests = {key: int(v) for key, v in (state.get("requests_by_class") or {}).items() if isinstance(v, int)}
    publish_run_event(
        run.pk,
        {
            "type": "usage",
            "run_id": str(run.pk),
            "attempt": attempt,
            "requests_by_class": requests,
            "prompt_tokens": int(_num(state, "prompt_tokens")),
            "completion_tokens": int(_num(state, "completion_tokens")),
            "reasoning_tokens": int(_num(state, "reasoning_tokens")),
            "cached_tokens": int(_num(state, "cached_tokens")),
            "cost_usd": _num(state, "cost"),
            "list_cost_usd": _num(state, "list_cost"),
            "inflight": int(_num(state, "inflight")),
            "budget_exhausted": state.get("budget_exhausted"),
        },
    )
