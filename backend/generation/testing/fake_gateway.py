"""An in-process fake of the LLM gateway sidecar (spec sections 4.5, 5.4 and 6.6).

``FakeGateway`` is a real HTTP server on ``127.0.0.1`` with the gateway's semantics and a scripted
upstream instead of OpenRouter:

* **front protocols**: ``openai_chat`` (``*/chat/completions``, streaming included, ``GET /v1/models``) and
  ``anthropic_messages`` (``/v1/messages``, non-streaming, translated to chat; ``/v1/messages/count_tokens``
  answered locally). Other routes get 404, ``HEAD /api/hello`` included;
* **policy** on every call: the model pin, forced and stripped sampling, the seed, ``max_tokens =
  call.max_output_tokens + reasoning.max_tokens`` clamped to the model limit, ``reasoning``, provider routing,
  ``:floor`` for flex serving, ``session_id`` and ``usage.include``. The upstream body is what the script sees;
* **request classes** from ``GatewayConfig.request_class_source`` (a header name, or ``default:<class>``);
  ``main`` calls count against ``run.max_llm_requests``, the other classes against ``run.max_aux_requests``;
* **pre-call reservation** (4.5): before forwarding, the worst case ``est_prompt x p_in + max_tokens x p_out``
  plus everything in flight must fit the cost cap, and likewise for the token caps; ``max_tokens`` is never
  clamped to make a call fit. A refusal is the protocol's native error shape, status 400, text
  "Run budget exhausted. Stop working and summarise.", and ``budget_exhausted:<kind>`` in ``state.json`` and
  ``gateway_events.jsonl`` (kinds: ``requests``, ``aux_requests``, ``attempts``, ``cost``, ``tokens``,
  ``uncached_input``, ``output_tokens``; ``attempts`` is the real gateway's loop guard on calls started,
  :func:`attempt_ceiling`). Exhaustion is sticky: every later call is refused too. A prompt estimate above
  ``call.max_input_tokens`` is refused with 400 ``context_budget`` and does not exhaust the run;
* **logs**: ``calls.jsonl`` (one record per forwarded call), ``gateway_events.jsonl`` and ``state.json``, which
  is rewritten atomically after every call start and end (``inflight``, ``last_call_end``, totals).

Not modelled: the ``rpm_per_model`` token bucket, ``cache_control`` injection, passthrough mode, the
Responses and Gemini fronts, and upstream retries (a scripted transport error is a 502 to the harness).

``gateway_state()``, ``write_state()`` and ``ScriptedStateFile`` script a ``state.json`` without a server
(for live-spend polling and idle-detection tests).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from typing import Final
from typing import Self

from backend.generation import identity
from backend.generation.contracts.resources import GatewayConfig

__all__ = [
    "BUDGET_MESSAGE",
    "REQUEST_CLASSES",
    "STATE_KEYS",
    "FakeGateway",
    "ScriptedReply",
    "ScriptedStateFile",
    "UpstreamCall",
    "attempt_ceiling",
    "canned_call",
    "estimate_tokens",
    "gateway_config",
    "gateway_state",
    "write_state",
]

REQUEST_CLASSES: Final = ("main", "subagent", "compaction", "auxiliary")
AUX_CLASSES: Final = frozenset(REQUEST_CLASSES[1:])
BUDGET_MESSAGE: Final = "Run budget exhausted. Stop working and summarise."
CHARS_PER_TOKEN: Final = 3.6
# Spec section 4.5: the keys of the gateway's state.json.
STATE_KEYS: Final = (
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
_MTOK: Final = 1_000_000
_ANTHROPIC_STOP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}


def estimate_tokens(text: str) -> int:
    """The gateway's local prompt estimate: ``ceil(chars / 3.6)``."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def attempt_ceiling(max_llm_requests: int, max_aux_requests: int) -> int:
    """The gateway's loop guard: calls started (successful or not) before ``budget_exhausted:attempts``.

    Same rule as ``images/llm-gateway/protocols/budget_rules.BudgetLimits.attempt_ceiling``: a harness that
    keeps re-sending a failing request cannot loop until the wall clock.
    """
    return 2 * (max_llm_requests + max_aux_requests) + 20


# =====================================================================================
# state.json
# =====================================================================================


def gateway_state(**overrides: Any) -> dict[str, Any]:
    """A ``state.json`` payload: the empty state of a fresh gateway with ``overrides`` applied."""
    state: dict[str, Any] = {
        "requests_by_class": dict.fromkeys(REQUEST_CLASSES, 0),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "cost": 0.0,
        "list_cost": 0.0,
        "inflight": 0,
        "last_call_end": None,
        "budget_exhausted": None,
        "call_truncations": 0,
        "updated": time.time(),
    }
    unknown = set(overrides) - set(STATE_KEYS)
    if unknown:
        msg = f"unknown state.json keys: {', '.join(sorted(unknown))}"
        raise KeyError(msg)
    state.update(overrides)
    return state


def write_state(path: Path | str, state: Mapping[str, Any]) -> None:
    """Write ``state`` atomically (temp file plus ``os.replace``), as the gateway does."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(state), handle, sort_keys=True)
        Path(tmp).replace(target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class ScriptedStateFile:
    """A ``state.json`` that steps through scripted frames (each a ``gateway_state`` override dict).

    ``advance()`` writes the next frame and returns it; after the last frame it keeps the last one.
    """

    def __init__(self, path: Path | str, frames: Sequence[Mapping[str, Any]]) -> None:
        if not frames:
            msg = "ScriptedStateFile needs at least one frame"
            raise ValueError(msg)
        self.path = Path(path)
        self.frames = [gateway_state(**frame) for frame in frames]
        self.position = -1

    def advance(self) -> dict[str, Any]:
        self.position = min(self.position + 1, len(self.frames) - 1)
        frame = dict(self.frames[self.position], updated=time.time())
        write_state(self.path, frame)
        return frame

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for _ in self.frames:
            yield self.advance()


# =====================================================================================
# Scripted upstream
# =====================================================================================


@dataclass
class ScriptedReply:
    """What the scripted upstream answers for one call.

    ``prompt_tokens=None`` uses the gateway's prompt estimate; ``cost=None`` prices the usage with the
    config's pricing (the billed cost then equals the list cost). ``hold`` blocks the reply until the event
    is set, which keeps the call in flight; ``transport_error`` simulates an unreachable upstream (502).
    """

    content: str = "Done."
    prompt_tokens: int | None = None
    completion_tokens: int = 16
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float | None = None
    finish_reason: str = "stop"
    status: int = 200
    error: str = "upstream error"
    provider: str = "FakeProvider"
    response_model: str | None = None
    system_fingerprint: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    delay_s: float = 0.0
    hold: threading.Event | None = None
    transport_error: bool = False


@dataclass(frozen=True)
class UpstreamCall:
    """One call as the scripted upstream sees it: the policy-applied canonical chat body."""

    index: int
    request_class: str
    body: dict[str, Any]


Script = Sequence[ScriptedReply] | Callable[[UpstreamCall], ScriptedReply]


def gateway_config(**overrides: Any) -> GatewayConfig:
    """A small ``GatewayConfig`` for tests (USD 1 cap, 10 main and 5 auxiliary requests); ``overrides`` are
    merged one level deep (``budget={"max_cost_usd": 0.01}`` keeps the other budget keys)."""
    base: dict[str, Any] = {
        "run_id": "00000000-0000-4000-8000-000000000001",
        "attempt": 1,
        "session_id": "00000000-0000-4000-8000-000000000001",
        "front_protocol": "openai_chat",
        "protocol_mode": "native",
        "upstream": {"kind": "openrouter", "base_url": "https://openrouter.invalid/api/v1"},
        "model": {
            "upstream_model": "deepseek/deepseek-v4.1-flash",
            "presented_names": ["deepseek-v4.1-flash"],
            "max_output": 32_000,
        },
        "pricing": {"input_per_mtok": 1.0, "output_per_mtok": 2.0, "source": "test", "as_of": "2026-11-01"},
        "reasoning": {"mode": "provider_default", "effort": None, "max_tokens": None, "exclude": False},
        "budget": {
            "max_cost_usd": 1.0,
            "cost_basis": "billed",
            "max_total_tokens": 1_000_000,
            "max_llm_requests": 10,
            "max_aux_requests": 5,
        },
        "call": {"max_output_tokens": 1024},
        "caching": {"prompt_cache": "off", "ttl": "5m", "sticky_session": True},
        "retries": {"upstream_retries": 0, "backoff_s": 0, "retry_on": ["5xx"], "llm_call_timeout_s": 60},
    }
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return GatewayConfig.model_validate(base)


# =====================================================================================
# The gateway
# =====================================================================================


class _Refusal(Exception):  # noqa: N818 - a control-flow signal, not an error
    def __init__(self, status: int, kind: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.message = message


@dataclass
class _Reservation:
    index: int
    request_class: str
    est_prompt: int
    max_tokens: int
    usd: float


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
    last_call_end: float | None = None
    budget_exhausted: str | None = None
    calls_started: int = 0
    native_system_prompt_logged: bool = False
    inflight: dict[int, _Reservation] = field(default_factory=dict)


class FakeGateway:
    """The gateway sidecar for one attempt, serving on ``127.0.0.1:<free port>``.

    Use it as a context manager (``with FakeGateway(config, replies=[...]) as gw:``) and point the harness
    at ``gw.base_url``. ``replies`` is a list consumed in order (``default_reply`` afterwards) or a callable
    ``UpstreamCall -> ScriptedReply``. Logs go to ``logs_dir`` (a temporary directory, removed on exit, when
    omitted).
    """

    def __init__(
        self,
        config: GatewayConfig | Mapping[str, Any] | None = None,
        *,
        replies: Script = (),
        default_reply: ScriptedReply | None = None,
        logs_dir: Path | str | None = None,
        host: str = "127.0.0.1",
    ) -> None:
        if config is None:
            config = gateway_config()
        self.config = config if isinstance(config, GatewayConfig) else GatewayConfig.model_validate(config)
        self._script = replies
        self._queue: list[ScriptedReply] = [] if callable(replies) else list(replies)
        self._default = default_reply or ScriptedReply()
        self._owns_logs = logs_dir is None
        self.logs_dir = Path(logs_dir) if logs_dir is not None else Path(tempfile.mkdtemp(prefix="fake-gateway-"))
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.upstream_calls: list[UpstreamCall] = []
        self._lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._totals = _Totals()
        self._host = host
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._write_state()

    # -- lifecycle ---------------------------------------------------------------------------------

    def start(self) -> Self:
        if self._server is not None:
            return self
        handler = type("FakeGatewayHandler", (_Handler,), {"gateway": self})
        self._server = ThreadingHTTPServer((self._host, 0), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="fake-gateway", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
        if self._owns_logs:
            shutil.rmtree(self.logs_dir, ignore_errors=True)

    # -- addresses -----------------------------------------------------------------------------------

    @property
    def origin(self) -> str:
        """``http://127.0.0.1:<port>`` (the stand-in for ``http://gateway:8080``)."""
        if self._server is None:
            msg = "the fake gateway is not running (use start() or a with-block)"
            raise RuntimeError(msg)
        return f"http://{self._host}:{self._server.server_port}"

    @property
    def base_url(self) -> str:
        """The base URL a harness is configured with for this front protocol."""
        return f"{self.origin}/v1" if self.config.front_protocol == "openai_chat" else self.origin

    # -- logs ----------------------------------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.logs_dir / "state.json"

    @property
    def calls_path(self) -> Path:
        return self.logs_dir / "calls.jsonl"

    @property
    def events_path(self) -> Path:
        return self.logs_dir / "gateway_events.jsonl"

    def state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def calls(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.calls_path)

    def events(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.events_path)

    def _append(self, path: Path, record: Mapping[str, Any]) -> None:
        with self._log_lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def _event(self, kind: str, **fields: Any) -> None:
        self._append(self.events_path, {"ts": time.time(), "event": kind, **fields})

    def _write_state(self) -> None:
        """Rewrite state.json; the caller holds ``self._lock`` (or is the constructor)."""
        totals = self._totals
        write_state(
            self.state_path,
            gateway_state(
                requests_by_class=dict(totals.requests_by_class),
                prompt_tokens=totals.prompt_tokens,
                completion_tokens=totals.completion_tokens,
                reasoning_tokens=totals.reasoning_tokens,
                cached_tokens=totals.cached_tokens,
                cache_write_tokens=totals.cache_write_tokens,
                cost=round(totals.cost, 10),
                list_cost=round(totals.list_cost, 10),
                inflight=len(totals.inflight),
                last_call_end=totals.last_call_end,
                budget_exhausted=totals.budget_exhausted and f"budget_exhausted:{totals.budget_exhausted}",
                call_truncations=totals.call_truncations,
            ),
        )

    # -- policy and budget -----------------------------------------------------------------------------

    def request_class(self, headers: Mapping[str, str]) -> tuple[str, str | None]:
        """``(class, raw header value)``; header names match case-insensitively, unknown values are ``auxiliary``."""
        source = self.config.request_class_source
        if source.startswith("default:"):
            return source.removeprefix("default:"), None
        lowered = {name.lower(): value for name, value in headers.items()}
        raw = lowered.get(source.lower())
        if raw is None:
            return "main", None
        value = raw.strip().lower()
        return (value if value in REQUEST_CLASSES else "auxiliary"), raw

    def upstream_max_tokens(self) -> int:
        cfg = self.config
        reasoning = cfg.reasoning.max_tokens if cfg.reasoning.mode == "max_tokens" else None
        wanted = cfg.call.max_output_tokens + (reasoning or 0)
        return min(wanted, cfg.model.max_output) if cfg.model.max_output else wanted

    def price(self, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0, cache_write: int = 0) -> float:
        """The list cost of a usage block under the config's pricing (USD)."""
        pricing = self.config.pricing
        cached_rate = pricing.cached_input_per_mtok if pricing.cached_input_per_mtok is not None else None
        write_rate = pricing.cache_write_per_mtok if pricing.cache_write_per_mtok is not None else None
        uncached = prompt_tokens - (cached_tokens if cached_rate is not None else 0)
        uncached -= cache_write if write_rate is not None else 0
        cost = max(uncached, 0) * pricing.input_per_mtok + completion_tokens * pricing.output_per_mtok
        if cached_rate is not None:
            cost += cached_tokens * cached_rate
        if write_rate is not None:
            cost += cache_write * write_rate
        return cost / _MTOK

    def apply_policy(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """The canonical chat body with the run's policy applied (what goes upstream)."""
        cfg = self.config
        out = {key: value for key, value in body.items() if key not in cfg.sampling.stripped}
        out.pop("max_completion_tokens", None)
        out.update(cfg.sampling.forced)
        model = cfg.model.upstream_model
        if cfg.routing.serving_tier == "flex" and not model.endswith(":floor"):
            model += ":floor"
        out["model"] = model
        if cfg.sampling.seed is not None and cfg.sampling.forward_seed:
            out["seed"] = cfg.sampling.seed
        out["max_tokens"] = self.upstream_max_tokens()
        reasoning = cfg.reasoning
        if reasoning.mode == "effort" and reasoning.effort is not None:
            out["reasoning"] = {"effort": reasoning.effort, "exclude": reasoning.exclude}
        elif reasoning.mode == "max_tokens" and reasoning.max_tokens is not None:
            out["reasoning"] = {"max_tokens": reasoning.max_tokens, "exclude": reasoning.exclude}
        if cfg.routing.provider:
            out["provider"] = dict(cfg.routing.provider)
        if cfg.caching.sticky_session and cfg.session_id:
            out["session_id"] = cfg.session_id
        out["usage"] = {"include": True}
        return out

    def _reserve(self, request_class: str, est_prompt: int) -> _Reservation:
        """Admit a call or raise ``_Refusal`` (spec section 4.5); the caller holds ``self._lock``."""
        cfg = self.config
        totals = self._totals
        if totals.budget_exhausted:
            raise _Refusal(400, totals.budget_exhausted, BUDGET_MESSAGE)
        if cfg.call.max_input_tokens is not None and est_prompt > cfg.call.max_input_tokens:
            msg = f"prompt estimate {est_prompt} exceeds the per-call input cap {cfg.call.max_input_tokens}"
            raise _Refusal(400, "context_budget", msg)
        max_tokens = self.upstream_max_tokens()
        worst = self.price(est_prompt, max_tokens) if cfg.budget.cost_basis != "free" else 0.0
        inflight = list(totals.inflight.values())
        budget = cfg.budget
        kind: str | None = None

        def used(classes: frozenset[str] | set[str]) -> int:
            done = sum(totals.requests_by_class[c] for c in classes)
            if budget.count_failed_calls:
                done += sum(totals.failed_by_class[c] for c in classes)
            return done + sum(1 for r in inflight if r.request_class in classes)

        if request_class == "main" and used({"main"}) >= budget.max_llm_requests:
            kind = "requests"
        elif request_class in AUX_CLASSES and used(AUX_CLASSES) >= budget.max_aux_requests:
            kind = "aux_requests"
        elif totals.calls_started >= attempt_ceiling(budget.max_llm_requests, budget.max_aux_requests):
            kind = "attempts"
        elif totals.cost + sum(r.usd for r in inflight) + worst > budget.max_cost_usd:
            kind = "cost"
        elif (
            totals.prompt_tokens
            + totals.completion_tokens
            + sum(r.est_prompt + r.max_tokens for r in inflight)
            + est_prompt
            + max_tokens
            > budget.max_total_tokens
        ):
            kind = "tokens"
        elif budget.max_uncached_input_tokens is not None and (
            totals.prompt_tokens - totals.cached_tokens + sum(r.est_prompt for r in inflight) + est_prompt
            > budget.max_uncached_input_tokens
        ):
            kind = "uncached_input"
        elif budget.max_output_tokens is not None and (
            totals.completion_tokens + sum(r.max_tokens for r in inflight) + max_tokens > budget.max_output_tokens
        ):
            kind = "output_tokens"
        if kind is not None:
            totals.budget_exhausted = kind
            self._write_state()
            self._event("budget_exhausted", reason=f"budget_exhausted:{kind}", request_class=request_class)
            raise _Refusal(400, kind, BUDGET_MESSAGE)
        totals.calls_started += 1
        reservation = _Reservation(totals.calls_started, request_class, est_prompt, max_tokens, worst)
        totals.inflight[reservation.index] = reservation
        self._write_state()
        return reservation

    def _next_reply(self, call: UpstreamCall) -> ScriptedReply:
        if callable(self._script):
            return self._script(call)
        with self._lock:
            return self._queue.pop(0) if self._queue else self._default

    # -- one call ------------------------------------------------------------------------------------------

    def handle_chat(
        self,
        native_body: Mapping[str, Any],
        chat_body: Mapping[str, Any],
        headers: Mapping[str, str],
        path: str,
    ) -> tuple[int, dict[str, Any] | None, ScriptedReply | None, _Refusal | None]:
        """Run one call through budget, policy and the script. Returns ``(status, usage, reply, refusal)``."""
        request_class, raw_class = self.request_class(headers)
        est_prompt = estimate_tokens(_prompt_text(chat_body))
        with self._lock:
            try:
                reservation = self._reserve(request_class, est_prompt)
            except _Refusal as refusal:
                if refusal.kind == "context_budget":
                    self._event("context_budget", request_class=request_class, est_prompt_tokens=est_prompt)
                return refusal.status, None, None, refusal
        started = time.time()
        upstream_body = self.apply_policy(chat_body)
        call = UpstreamCall(reservation.index, request_class, upstream_body)
        with self._lock:
            self.upstream_calls.append(call)
        reply = self._next_reply(call)
        if reply.hold is not None:
            reply.hold.wait(timeout=60)
        if reply.delay_s:
            time.sleep(reply.delay_s)
        ok = not reply.transport_error and reply.status < 400
        status = 502 if reply.transport_error else reply.status
        prompt_tokens = reply.prompt_tokens if reply.prompt_tokens is not None else est_prompt
        usage: dict[str, Any] | None = None
        list_cost = 0.0
        if ok:
            list_cost = self.price(
                prompt_tokens,
                reply.completion_tokens,
                reply.cached_tokens,
                reply.cache_write_tokens,
            )
            billed = reply.cost if reply.cost is not None else list_cost
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": reply.completion_tokens,
                "reasoning_tokens": reply.reasoning_tokens,
                "cached_tokens": reply.cached_tokens,
                "cache_write_tokens": reply.cache_write_tokens,
                "cost": billed,
            }
        ended = time.time()
        with self._lock:
            totals = self._totals
            totals.inflight.pop(reservation.index, None)
            totals.last_call_end = ended
            if usage is not None:
                totals.requests_by_class[request_class] += 1
                totals.prompt_tokens += usage["prompt_tokens"]
                totals.completion_tokens += usage["completion_tokens"]
                totals.reasoning_tokens += usage["reasoning_tokens"]
                totals.cached_tokens += usage["cached_tokens"]
                totals.cache_write_tokens += usage["cache_write_tokens"]
                totals.list_cost += list_cost
                basis = self.config.budget.cost_basis
                totals.cost += 0.0 if basis == "free" else (list_cost if basis == "list_price" else usage["cost"])
                if reply.finish_reason == "length":
                    totals.call_truncations += 1
            else:
                totals.failed_by_class[request_class] += 1
            first_main = request_class == "main" and not totals.native_system_prompt_logged
            if first_main:
                totals.native_system_prompt_logged = True
            self._write_state()
        duration = ended - started
        record = {
            "index": reservation.index,
            "started": started,
            "ended": ended,
            "duration_s": round(duration, 6),
            "ttft_ms": round(duration * 1000, 3),
            "tokens_per_s": round(reply.completion_tokens / duration, 3) if ok and duration > 0 else None,
            "path": path,
            "request_class": request_class,
            "request_class_header": raw_class,
            "front_protocol": self.config.front_protocol,
            "protocol_mode": self.config.protocol_mode,
            "requested_model": native_body.get("model"),
            "upstream_model": upstream_body["model"],
            "response_model": (reply.response_model or upstream_body["model"]) if ok else None,
            "served_provider": reply.provider if ok else None,
            "system_fingerprint": reply.system_fingerprint if ok else None,
            "serving_tier": self.config.routing.serving_tier,
            "status": status,
            "transport_error": reply.transport_error,
            "error": None if ok else ("upstream unreachable" if reply.transport_error else reply.error),
            "finish_reason": reply.finish_reason if ok else None,
            "usage": usage,
            "list_cost": list_cost,
            "reserved_usd": reservation.usd,
            "est_prompt_tokens": est_prompt,
            "translation_dropped": {},
            "native_request_sha": identity.H(_jsonable(native_body)),
            "native_system_prompt": _system_prompt(chat_body) if first_main else None,
            "request": upstream_body,
        }
        self._append(self.calls_path, record)
        return status, usage, reply, None


def canned_call(
    index: int = 1,
    *,
    request_class: str = "main",
    prompt_tokens: int = 1200,
    completion_tokens: int = 300,
    reasoning_tokens: int = 0,
    cached_tokens: int = 0,
    cost: float | None = None,
    finish_reason: str = "stop",
    status: int = 200,
    model: str = "deepseek/deepseek-v4.1-flash",
    provider: str = "FakeProvider",
    system_prompt: str | None = None,
    user_message: str = "Build the app described in /run/brief.md.",
    tools: Sequence[Mapping[str, Any]] = (),
    started: float = 1_762_128_000.0,
    duration_s: float = 2.0,
) -> dict[str, Any]:
    """One ``calls.jsonl`` record with the same keys as the ones ``FakeGateway`` writes.

    ``cost=None`` prices the usage at 1 / 2 USD per Mtok (the ``gateway_config()`` pricing).
    """
    ok = status < 400
    list_cost = (prompt_tokens * 1.0 + completion_tokens * 2.0) / _MTOK
    messages: list[dict[str, Any]] = []
    if system_prompt is not None:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})
    request: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": 1024, "usage": {"include": True}}
    if tools:
        request["tools"] = [dict(tool) for tool in tools]
    return {
        "index": index,
        "started": started,
        "ended": started + duration_s,
        "duration_s": duration_s,
        "ttft_ms": duration_s * 1000,
        "tokens_per_s": completion_tokens / duration_s if ok else None,
        "path": "/v1/chat/completions",
        "request_class": request_class,
        "request_class_header": None,
        "front_protocol": "openai_chat",
        "protocol_mode": "native",
        "requested_model": model,
        "upstream_model": model,
        "response_model": model if ok else None,
        "served_provider": provider if ok else None,
        "system_fingerprint": None,
        "serving_tier": "standard",
        "status": status,
        "transport_error": status == 502,
        "error": None if ok else "upstream error",
        "finish_reason": finish_reason if ok else None,
        "usage": (
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "reasoning_tokens": reasoning_tokens,
                "cached_tokens": cached_tokens,
                "cache_write_tokens": 0,
                "cost": list_cost if cost is None else cost,
            }
            if ok
            else None
        ),
        "list_cost": list_cost if ok else 0.0,
        "reserved_usd": 0.0,
        "est_prompt_tokens": prompt_tokens,
        "translation_dropped": {},
        "native_request_sha": identity.H(request),
        "native_system_prompt": system_prompt if request_class == "main" and index == 1 else None,
        "request": request,
    }


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, Mapping):
                parts.append(str(part.get("text") or part.get("content") or ""))
            else:
                parts.append(str(part))
        return "\n".join(parts)
    return "" if content is None else json.dumps(content, sort_keys=True)


def _prompt_text(chat_body: Mapping[str, Any]) -> str:
    messages = chat_body.get("messages") or []
    text = "\n".join(_content_text(m.get("content")) for m in messages if isinstance(m, Mapping))
    if chat_body.get("tools"):
        text += json.dumps(chat_body["tools"], sort_keys=True)
    return text


def _system_prompt(chat_body: Mapping[str, Any]) -> str | None:
    for message in chat_body.get("messages") or []:
        if isinstance(message, Mapping) and message.get("role") == "system":
            return _content_text(message.get("content"))
    return None


def _anthropic_to_chat(body: Mapping[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if body.get("system"):
        messages.append({"role": "system", "content": _content_text(body["system"])})
    messages += [
        {"role": m.get("role", "user"), "content": _content_text(m.get("content"))}
        for m in body.get("messages") or []
        if isinstance(m, Mapping)
    ]
    chat: dict[str, Any] = {"model": body.get("model"), "messages": messages}
    if "max_tokens" in body:
        chat["max_tokens"] = body["max_tokens"]
    for key in ("temperature", "top_p", "top_k"):
        if key in body:
            chat[key] = body[key]
    return chat


class _Handler(BaseHTTPRequestHandler):
    gateway: FakeGateway
    server_version = "FakeGateway/1"

    def log_message(self, format: str, *args: Any) -> None:
        return  # quiet

    # -- responses ---------------------------------------------------------------------------------------

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str, code: str) -> None:
        if self.gateway.config.front_protocol == "anthropic_messages":
            kind = "invalid_request_error" if status == 400 else "api_error"
            self._send_json(status, {"type": "error", "error": {"type": kind, "message": message}})
        else:
            self._send_json(status, {"error": {"message": message, "type": code, "code": code}})

    def _read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    # -- routes --------------------------------------------------------------------------------------------

    def do_HEAD(self) -> None:
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.rstrip("/") != "/v1/models":
            self._error(404, f"not found: {path}", "not_found")
            return
        cfg = self.gateway.config
        names = cfg.model.presented_names or [cfg.model.upstream_model]
        self._send_json(
            200,
            {"object": "list", "data": [{"id": name, "object": "model", "type": "model"} for name in names]},
        )

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        front = self.gateway.config.front_protocol
        body = self._read_json()
        if body is None:
            self._error(400, "request body is not a JSON object", "bad_request")
            return
        if front == "openai_chat" and path.endswith("/chat/completions"):
            self._chat(body, path)
        elif front == "anthropic_messages" and path == "/v1/messages/count_tokens":
            text = _prompt_text(_anthropic_to_chat(body))
            self._send_json(200, {"input_tokens": estimate_tokens(text), "estimate": True})
        elif front == "anthropic_messages" and path == "/v1/messages":
            self._messages(body, path)
        else:
            self._error(404, f"the gateway does not serve {path} for {front}", "not_found")

    def _chat(self, body: dict[str, Any], path: str) -> None:
        status, usage, reply, refusal = self.gateway.handle_chat(body, body, dict(self.headers.items()), path)
        if refusal is not None:
            self._error(refusal.status, refusal.message, refusal.kind)
            return
        assert reply is not None
        if usage is None:
            self._error(status, "upstream unreachable" if reply.transport_error else reply.error, "upstream_error")
            return
        model = reply.response_model or self.gateway.config.model.upstream_model
        message: dict[str, Any] = {"role": "assistant", "content": reply.content}
        if reply.tool_calls:
            message["tool_calls"] = reply.tool_calls
        usage_block = {
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "total_tokens": usage["prompt_tokens"] + usage["completion_tokens"],
            "prompt_tokens_details": {"cached_tokens": usage["cached_tokens"]},
            "completion_tokens_details": {"reasoning_tokens": usage["reasoning_tokens"]},
            "cost": usage["cost"],
        }
        if body.get("stream"):
            self._stream(model, reply, message, usage_block)
            return
        self._send_json(
            200,
            {
                "id": "gen-fake",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "provider": reply.provider,
                "system_fingerprint": reply.system_fingerprint,
                "choices": [{"index": 0, "message": message, "finish_reason": reply.finish_reason}],
                "usage": usage_block,
            },
        )

    def _stream(self, model: str, reply: ScriptedReply, message: dict[str, Any], usage: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        base = {"id": "gen-fake", "object": "chat.completion.chunk", "model": model, "provider": reply.provider}
        delta = {key: value for key, value in message.items() if key != "role"}
        chunks = [
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant", **delta}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": reply.finish_reason}]},
            {**base, "choices": [], "usage": usage},
        ]
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _messages(self, body: dict[str, Any], path: str) -> None:
        if body.get("stream"):
            self._error(400, "the fake gateway does not stream anthropic_messages", "unsupported")
            return
        chat = _anthropic_to_chat(body)
        status, usage, reply, refusal = self.gateway.handle_chat(body, chat, dict(self.headers.items()), path)
        if refusal is not None:
            self._error(refusal.status, refusal.message, refusal.kind)
            return
        assert reply is not None
        if usage is None:
            self._error(status, "upstream unreachable" if reply.transport_error else reply.error, "upstream_error")
            return
        self._send_json(
            200,
            {
                "id": "msg_fake",
                "type": "message",
                "role": "assistant",
                "model": reply.response_model or self.gateway.config.model.upstream_model,
                "content": [{"type": "text", "text": reply.content}],
                "stop_reason": _ANTHROPIC_STOP.get(reply.finish_reason, "end_turn"),
                "stop_sequence": None,
                "usage": {
                    "input_tokens": usage["prompt_tokens"] - usage["cached_tokens"],
                    "output_tokens": usage["completion_tokens"],
                    "cache_read_input_tokens": usage["cached_tokens"],
                    "cache_creation_input_tokens": usage["cache_write_tokens"],
                },
            },
        )
