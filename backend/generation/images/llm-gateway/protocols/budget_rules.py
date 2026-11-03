"""Run budget rules shared by the gateway sidecar and the executor's local LLM proxy (spec 4.5, 6.6).

One source file, two consumers: the ``llm-gateway`` image copies it as ``protocols/budget_rules.py``,
and ``backend/inference/proxy_server.py`` loads this same file by path, so the local proxy applies
exactly the checks the gateway applies (local proxy parity). Stdlib only, no imports from the rest of
the ``protocols`` package.

The model (all amounts per run attempt):

* **Request classes** (``main``, ``subagent``, ``compaction``, ``auxiliary``). ``run.max_llm_requests``
  counts successful ``main`` calls, ``run.max_aux_requests`` the other three. In-flight calls count
  too, and failed calls count only when ``count_failed_calls`` is set.
* **Pre-call reservation.** Before a call is forwarded its worst case is reserved: the prompt estimate
  (``ceil(chars / 3.6)``, scaled by the largest actual/estimate ratio seen so far in this run) plus the
  call's full upstream ``max_tokens``, priced at the manifest's list prices (prompt tokens at the dearer
  of input and cache-write). A call is refused when
  accumulated usage plus every in-flight reservation plus its own worst case would pass a cap. The call
  is **refused, never clamped**: ``max_tokens`` is not shrunk to make it fit.
* **Exhaustion is sticky.** Once a cap refuses a call, every later call of the attempt is refused with
  the same ``budget_exhausted:<kind>``. Kinds, checked in this order: ``requests``, ``aux_requests``,
  ``attempts`` (a loop guard on calls started), ``cost``, ``tokens``, ``uncached_input``,
  ``output_tokens``.
* **``call.max_input_tokens``** refuses a single over-long prompt (``context_budget``) without
  exhausting the run, so the harness can compact and continue.
* **Cost basis.** ``billed`` accrues the upstream's ``usage.cost`` (list price when the response has
  none, recorded as ``price_table``); ``list_price`` accrues list cost; ``free`` never trips the cost cap.
  ``list_cost`` always accrues.
* **No silent zero.** A successful call whose response carried no usage is settled at its reservation
  (prompt estimate + ``max_tokens``, worst-case cost) and flagged ``usage_estimated``; so is a failed
  call the upstream had already accepted (``upstream_billed``: a stream broken mid-way, an unreadable
  2xx body, a gateway error after forwarding). Reserved prompt tokens are priced at the dearest input
  rate (``max(input, cache write)``), so cache writes never take a call past its reservation.
"""

# Shared standalone module (copied into the gateway image; loaded by path by the executor proxy).

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from typing import Any

RULES_VERSION = 1
CHARS_PER_TOKEN = 3.6
REQUEST_CLASSES = ("main", "subagent", "compaction", "auxiliary")
AUX_CLASSES = frozenset(REQUEST_CLASSES[1:])
EXHAUSTION_KINDS = ("requests", "aux_requests", "attempts", "cost", "tokens", "uncached_input", "output_tokens")
# The exact reply text when a budget is spent. It deliberately reads nothing like a context-overflow
# message, so no harness mistakes it for "compact and retry".
BUDGET_MESSAGE = "Run budget exhausted. Stop working and summarise."
CONTEXT_BUDGET = "context_budget"
# The keys of state.json (spec 4.5), in a fixed order.
STATE_KEYS = (
    "requests_by_class",
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "cost",
    "list_cost",
    "inflight",
    "last_call_end",
    "budget_exhausted",
    "call_truncations",
    "updated",
)
# The prompt-estimate calibration never scales the estimate by more than this.
MAX_CALIBRATION = 3.0
_MTOK = 1_000_000
# Structural keys whose string values are protocol framing, not text the model reads as input.
_FRAMING_KEYS = frozenset({"role", "type", "cache_control"})
# Keys whose non-string value (a tool call's arguments, an Anthropic ``tool_use.input``, a Gemini
# ``functionCall.args``) is serialised for the model: counted as its JSON.
_JSON_KEYS = frozenset({"input", "arguments", "args", "parameters", "response", "functionCall", "functionResponse"})


# =====================================================================================
# Estimates
# =====================================================================================


def estimate_tokens(text: str) -> int:
    """The local prompt estimate, ``ceil(chars / 3.6)``."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _collect_text(value: Any, out: list[str]) -> None:
    """Every string leaf of ``value``; non-string tool inputs and arguments as their JSON.

    Generic on purpose: a key-allowlist walk misses whatever it does not name (chat
    ``tool_calls[].function.arguments``, ``tool_use.input.command`` / ``old_string``), and an
    under-estimate breaks the zero-overshoot reservation (spec 4.5).
    """
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, int | float):
        out.append(str(value))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if key in _FRAMING_KEYS and isinstance(item, str | Mapping):
                continue
            if key in _JSON_KEYS and isinstance(item, Mapping | list | tuple):
                out.append(_json(item))
            else:
                _collect_text(item, out)
    elif isinstance(value, list | tuple):
        for item in value:
            _collect_text(item, out)
    else:
        out.append(str(value))


def prompt_text(body: Mapping[str, Any]) -> str:
    """Everything a request sends the model as input, as one string (for the token estimate).

    Works on every front protocol's body: chat ``messages`` (with ``tool_calls``), Anthropic
    ``system`` + ``messages`` (with ``tool_use`` / ``tool_result``), Responses ``instructions`` +
    ``input`` (with ``function_call`` / ``function_call_output``), Gemini ``systemInstruction`` +
    ``contents`` (with ``functionCall`` / ``functionResponse``). Every string leaf counts; tool
    definitions count as their JSON.
    """
    out: list[str] = []
    for key in ("system", "instructions", "systemInstruction", "system_instruction"):
        if key in body:
            _collect_text(body[key], out)
    for key in ("messages", "input", "contents"):
        if key in body:
            _collect_text(body[key], out)
    for key in ("tools", "functions"):
        if body.get(key):
            out.append(_json(body[key]))
    return "\n".join(out)


def request_class(source: str, headers: Mapping[str, str]) -> tuple[str, str | None]:
    """``(request class, raw header value)`` for a call.

    ``source`` is ``HarnessFacts.request_class_source``: a header name, or ``default:<class>``. Header
    names match case-insensitively; a missing header is ``main``; an unknown value is ``auxiliary``.
    """
    if source.startswith("default:"):
        cls = source.removeprefix("default:")
        return (cls if cls in REQUEST_CLASSES else "main"), None
    lowered = {str(name).lower(): value for name, value in headers.items()}
    raw = lowered.get(source.lower())
    if raw is None:
        return "main", None
    value = str(raw).strip().lower()
    return (value if value in REQUEST_CLASSES else "auxiliary"), str(raw)


# =====================================================================================
# Limits and pricing
# =====================================================================================


@dataclass(frozen=True)
class Pricing:
    """USD per million tokens (the manifest's frozen pricing)."""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    cached_input_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None

    def price(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """List cost in USD. ``prompt_tokens`` includes cached reads and cache writes."""
        uncached = prompt_tokens
        cost = completion_tokens * self.output_per_mtok
        if self.cached_input_per_mtok is not None:
            uncached -= cached_tokens
            cost += cached_tokens * self.cached_input_per_mtok
        if self.cache_write_per_mtok is not None:
            uncached -= cache_write_tokens
            cost += cache_write_tokens * self.cache_write_per_mtok
        cost += max(uncached, 0) * self.input_per_mtok
        return cost / _MTOK

    @property
    def worst_input_per_mtok(self) -> float:
        """The dearest price a prompt token can be billed at: plain input, or a cache write (Anthropic
        bills 1.25x for 5-minute and 2x for 1-hour writes, and the gateway itself may add the markers)."""
        return max(self.input_per_mtok, self.cache_write_per_mtok or 0.0, self.cached_input_per_mtok or 0.0)

    def worst_case(self, prompt_tokens: int, max_tokens: int) -> float:
        """The reservation price: every prompt token at its dearest rate, the whole output cap used."""
        return (prompt_tokens * self.worst_input_per_mtok + max_tokens * self.output_per_mtok) / _MTOK


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


@dataclass(frozen=True)
class BudgetLimits:
    """The run caps one ledger enforces (the GW knobs of spec 4.2)."""

    max_cost_usd: float
    max_total_tokens: int
    max_llm_requests: int
    max_aux_requests: int
    cost_basis: str = "billed"  # billed | list_price | free
    max_uncached_input_tokens: int | None = None
    max_output_tokens: int | None = None
    count_failed_calls: bool = False
    call_max_input_tokens: int | None = None
    max_attempts: int | None = None
    pricing: Pricing = field(default_factory=Pricing)

    @property
    def attempt_ceiling(self) -> int:
        """Hard ceiling on calls started (successful or not), so a harness stuck re-sending a failing
        request cannot loop until the wall clock."""
        if self.max_attempts is not None:
            return self.max_attempts
        return 2 * (self.max_llm_requests + self.max_aux_requests) + 20

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> BudgetLimits:
        """Build from a ``GatewayConfig``-shaped mapping (its ``budget``, ``call`` and ``pricing``)."""
        budget = config.get("budget") or {}
        call = config.get("call") or {}
        pricing = config.get("pricing") or {}
        return cls(
            max_cost_usd=float(budget["max_cost_usd"]),
            max_total_tokens=int(budget["max_total_tokens"]),
            max_llm_requests=int(budget["max_llm_requests"]),
            max_aux_requests=int(budget.get("max_aux_requests") or 0),
            cost_basis=str(budget.get("cost_basis") or "billed"),
            max_uncached_input_tokens=_opt_int(budget.get("max_uncached_input_tokens")),
            max_output_tokens=_opt_int(budget.get("max_output_tokens")),
            count_failed_calls=bool(budget.get("count_failed_calls")),
            call_max_input_tokens=_opt_int(call.get("max_input_tokens")),
            max_attempts=_opt_int(budget.get("max_attempts")),
            pricing=Pricing(
                input_per_mtok=float(pricing.get("input_per_mtok") or 0.0),
                output_per_mtok=float(pricing.get("output_per_mtok") or 0.0),
                cached_input_per_mtok=(
                    None if pricing.get("cached_input_per_mtok") is None else float(pricing["cached_input_per_mtok"])
                ),
                cache_write_per_mtok=(
                    None if pricing.get("cache_write_per_mtok") is None else float(pricing["cache_write_per_mtok"])
                ),
            ),
        )


def upstream_max_tokens(config: Mapping[str, Any]) -> int:
    """``call.max_output_tokens + reasoning.max_tokens`` (budget mode only), clamped to the model limit."""
    call = config.get("call") or {}
    reasoning = config.get("reasoning") or {}
    model = config.get("model") or {}
    extra = int(reasoning.get("max_tokens") or 0) if reasoning.get("mode") == "max_tokens" else 0
    wanted = int(call["max_output_tokens"]) + extra
    limit = model.get("max_output")
    return min(wanted, int(limit)) if limit else wanted


# =====================================================================================
# Usage
# =====================================================================================

USAGE_KEYS = ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens", "cache_write_tokens")


def normalized_usage(
    prompt_tokens: Any = None,
    completion_tokens: Any = None,
    reasoning_tokens: Any = None,
    cached_tokens: Any = None,
    cache_write_tokens: Any = None,
    cost: Any = None,
) -> dict[str, Any]:
    """The protocol-independent usage record. ``None`` means *not reported*, never zero."""

    def _int(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _float(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    return {
        "prompt_tokens": _int(prompt_tokens),
        "completion_tokens": _int(completion_tokens),
        "reasoning_tokens": _int(reasoning_tokens),
        "cached_tokens": _int(cached_tokens),
        "cache_write_tokens": _int(cache_write_tokens),
        "cost": _float(cost),
    }


def chat_usage(usage: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """An OpenAI/OpenRouter chat ``usage`` block, normalized (None when absent)."""
    if not isinstance(usage, Mapping) or not usage:
        return None
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    if not isinstance(prompt_details, Mapping):
        prompt_details = {}
    if not isinstance(completion_details, Mapping):
        completion_details = {}
    return normalized_usage(
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        reasoning_tokens=completion_details.get("reasoning_tokens"),
        cached_tokens=prompt_details.get("cached_tokens"),
        cache_write_tokens=prompt_details.get("cache_write_tokens"),
        cost=usage.get("cost"),
    )


# =====================================================================================
# The ledger
# =====================================================================================


class Refusal(Exception):  # noqa: N818 - a control-flow signal, not an error
    """A call the budget does not admit. ``kind`` is an exhaustion kind or ``context_budget``."""

    def __init__(self, kind: str, message: str, *, status: int = 400, est_prompt_tokens: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.est_prompt_tokens = est_prompt_tokens

    @property
    def exhausted(self) -> bool:
        return self.kind != CONTEXT_BUDGET

    @property
    def reason(self) -> str:
        return f"budget_exhausted:{self.kind}" if self.exhausted else self.kind


@dataclass
class Reservation:
    """One admitted call: what it holds against the caps until it settles."""

    index: int
    request_class: str
    est_prompt_tokens: int  # the raw estimate
    prompt_tokens: int  # the calibrated estimate the reservation holds
    max_tokens: int
    usd: float
    started: float


@dataclass
class Settlement:
    """How a finished call was accounted."""

    usage: dict[str, Any]
    cost: float  # what accrued toward the cost cap
    list_cost: float
    cost_source: str  # usage.cost | price_table | list_price | free | reservation
    usage_estimated: bool
    truncated: bool


@dataclass
class _Totals:
    requests_by_class: dict[str, int] = field(default_factory=lambda: dict.fromkeys(REQUEST_CLASSES, 0))
    failed_by_class: dict[str, int] = field(default_factory=lambda: dict.fromkeys(REQUEST_CLASSES, 0))
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0
    list_cost: float = 0.0
    call_truncations: int = 0
    calls_started: int = 0
    refused: int = 0
    context_refusals: int = 0
    usage_estimated: int = 0
    last_call_end: float | None = None
    budget_exhausted: str | None = None
    calibration: float = 1.0


class Ledger:
    """The budget state of one run attempt. Thread-safe.

    ``on_change(snapshot)`` is called with the :func:`snapshot` after every reservation, refusal and
    settlement, while the ledger lock is held, so state files are written in order.
    """

    def __init__(
        self,
        limits: BudgetLimits,
        *,
        on_change: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.limits = limits
        self._on_change = on_change
        self._clock = clock
        self._lock = threading.RLock()
        self._totals = _Totals()
        self._inflight: dict[int, Reservation] = {}

    # -- reads -------------------------------------------------------------------------------------

    @property
    def exhausted(self) -> str | None:
        with self._lock:
            return self._totals.budget_exhausted

    @property
    def inflight(self) -> int:
        with self._lock:
            return len(self._inflight)

    def snapshot(self) -> dict[str, Any]:
        """The ``state.json`` payload (exactly :data:`STATE_KEYS`)."""
        with self._lock:
            t = self._totals
            return {
                "requests_by_class": dict(t.requests_by_class),
                "prompt_tokens": t.prompt_tokens,
                "completion_tokens": t.completion_tokens,
                "reasoning_tokens": t.reasoning_tokens,
                "cached_tokens": t.cached_tokens,
                "cache_write_tokens": t.cache_write_tokens,
                "cost": round(t.cost, 10),
                "list_cost": round(t.list_cost, 10),
                "inflight": len(self._inflight),
                "last_call_end": t.last_call_end,
                "budget_exhausted": t.budget_exhausted and f"budget_exhausted:{t.budget_exhausted}",
                "call_truncations": t.call_truncations,
                "updated": self._clock(),
            }

    def stats(self) -> dict[str, Any]:
        """The snapshot plus internal counters (for ``GET /stats`` and the final event)."""
        with self._lock:
            t = self._totals
            return {
                **self.snapshot(),
                "failed_by_class": dict(t.failed_by_class),
                "calls_started": t.calls_started,
                "refused": t.refused,
                "context_refusals": t.context_refusals,
                "usage_estimated": t.usage_estimated,
                "calibration": t.calibration,
                "inflight_reserved_usd": round(sum(r.usd for r in self._inflight.values()), 10),
                "rules_version": RULES_VERSION,
            }

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change(self.snapshot())

    # -- admission ---------------------------------------------------------------------------------

    def _used_requests(self, classes: frozenset[str] | set[str]) -> int:
        t = self._totals
        done = sum(t.requests_by_class[c] for c in classes)
        if self.limits.count_failed_calls:
            done += sum(t.failed_by_class[c] for c in classes)
        return done + sum(1 for r in self._inflight.values() if r.request_class in classes)

    def _exhaustion_kind(self, request_class: str, prompt: int, max_tokens: int, usd: float) -> str | None:  # noqa: PLR0911
        lim = self.limits
        t = self._totals
        held = list(self._inflight.values())
        if request_class == "main" and self._used_requests({"main"}) >= lim.max_llm_requests:
            return "requests"
        if request_class in AUX_CLASSES and self._used_requests(AUX_CLASSES) >= lim.max_aux_requests:
            return "aux_requests"
        if t.calls_started >= lim.attempt_ceiling:
            return "attempts"
        if lim.cost_basis != "free" and t.cost + sum(r.usd for r in held) + usd > lim.max_cost_usd:
            return "cost"
        reserved_tokens = sum(r.prompt_tokens + r.max_tokens for r in held)
        if t.prompt_tokens + t.completion_tokens + reserved_tokens + prompt + max_tokens > lim.max_total_tokens:
            return "tokens"
        if lim.max_uncached_input_tokens is not None:
            uncached = t.prompt_tokens - t.cached_tokens + sum(r.prompt_tokens for r in held) + prompt
            if uncached > lim.max_uncached_input_tokens:
                return "uncached_input"
        if lim.max_output_tokens is not None:
            if t.completion_tokens + sum(r.max_tokens for r in held) + max_tokens > lim.max_output_tokens:
                return "output_tokens"
        return None

    def reserve(self, request_class: str, est_prompt_tokens: int, max_tokens: int) -> Reservation:
        """Admit one call or raise :class:`Refusal` (never shrinks ``max_tokens``)."""
        if request_class not in REQUEST_CLASSES:
            request_class = "auxiliary"
        with self._lock:
            t = self._totals
            if t.budget_exhausted:
                t.refused += 1
                self._changed()
                raise Refusal(t.budget_exhausted, BUDGET_MESSAGE, est_prompt_tokens=est_prompt_tokens)
            cap = self.limits.call_max_input_tokens
            if cap is not None and est_prompt_tokens > cap:
                t.context_refusals += 1
                self._changed()
                msg = f"prompt is too long: {est_prompt_tokens} tokens > {cap} maximum"
                raise Refusal(CONTEXT_BUDGET, msg, est_prompt_tokens=est_prompt_tokens)
            prompt = min(math.ceil(est_prompt_tokens * t.calibration), 2**62)
            usd = 0.0 if self.limits.cost_basis == "free" else self.limits.pricing.worst_case(prompt, max_tokens)
            kind = self._exhaustion_kind(request_class, prompt, max_tokens, usd)
            if kind is not None:
                t.budget_exhausted = kind
                t.refused += 1
                self._changed()
                raise Refusal(kind, BUDGET_MESSAGE, est_prompt_tokens=est_prompt_tokens)
            t.calls_started += 1
            reservation = Reservation(
                index=t.calls_started,
                request_class=request_class,
                est_prompt_tokens=est_prompt_tokens,
                prompt_tokens=prompt,
                max_tokens=max_tokens,
                usd=usd,
                started=self._clock(),
            )
            self._inflight[reservation.index] = reservation
            self._changed()
            return reservation

    # -- settlement --------------------------------------------------------------------------------

    def settle(
        self,
        reservation: Reservation,
        usage: Mapping[str, Any] | None,
        *,
        ok: bool,
        finish_reason: str | None = None,
        truncated: bool = False,
        upstream_billed: bool = False,
    ) -> Settlement:
        """Account a finished call (idempotent per reservation: a second settle is a no-op).

        ``usage`` is a :func:`normalized_usage` mapping. A call without usage is settled at its
        reservation (``usage_estimated``, cost source ``reservation``) when it succeeded, or when it
        failed after the upstream had accepted it (``upstream_billed``: a 2xx/3xx answer whose stream
        broke, whose body could not be read, or that the gateway failed on after forwarding), because
        the upstream bills such calls; it still counts as a failed call. Only a call the upstream
        refused or never answered (no usage, not billed) costs nothing.
        """
        with self._lock:
            if self._inflight.pop(reservation.index, None) is None:
                return Settlement(dict(usage or {}), 0.0, 0.0, "duplicate", usage_estimated=False, truncated=False)
            t = self._totals
            t.last_call_end = self._clock()
            estimated = False
            known = usage is not None and (usage.get("prompt_tokens") is not None or usage.get("completion_tokens"))
            if (ok or upstream_billed) and not known:
                estimated = True
                usage = normalized_usage(
                    prompt_tokens=reservation.prompt_tokens,
                    completion_tokens=reservation.max_tokens,
                    cost=None,
                )
            settlement = self._account(reservation, usage if (known or estimated) else None, estimated=estimated)
            if ok:
                t.requests_by_class[reservation.request_class] += 1
                truncated = truncated or finish_reason == "length"
                if truncated:
                    t.call_truncations += 1
                settlement.truncated = truncated
            else:
                t.failed_by_class[reservation.request_class] += 1
            if estimated:
                t.usage_estimated += 1
            self._changed()
            return settlement

    def _account(self, reservation: Reservation, usage: Mapping[str, Any] | None, *, estimated: bool) -> Settlement:
        t = self._totals
        lim = self.limits
        if usage is None:
            return Settlement({}, 0.0, 0.0, "none", usage_estimated=False, truncated=False)
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        cached = int(usage.get("cached_tokens") or 0)
        cache_write = int(usage.get("cache_write_tokens") or 0)
        t.prompt_tokens += prompt
        t.completion_tokens += completion
        t.reasoning_tokens += int(usage.get("reasoning_tokens") or 0)
        t.cached_tokens += cached
        t.cache_write_tokens += cache_write
        if estimated:
            list_cost = reservation.usd if lim.cost_basis != "free" else lim.pricing.worst_case(prompt, completion)
        else:
            list_cost = lim.pricing.price(prompt, completion, cached, cache_write)
        billed = usage.get("cost")
        if lim.cost_basis == "free":
            cost, source = 0.0, "free"
        elif lim.cost_basis == "list_price":
            cost, source = list_cost, "list_price"
        elif estimated:
            cost, source = reservation.usd, "reservation"
        elif billed is not None:
            cost, source = float(billed), "usage.cost"
        else:
            cost, source = list_cost, "price_table"
        t.cost += cost
        t.list_cost += list_cost
        if not estimated and prompt and reservation.est_prompt_tokens:
            ratio = prompt / reservation.est_prompt_tokens
            t.calibration = min(max(t.calibration, ratio), MAX_CALIBRATION)
        return Settlement(dict(usage), cost, list_cost, source, usage_estimated=estimated, truncated=False)

    def release(self, reservation: Reservation) -> None:
        """Drop a reservation whose call never reached the upstream (no usage, no failure counted)."""
        with self._lock:
            if self._inflight.pop(reservation.index, None) is not None:
                self._changed()
