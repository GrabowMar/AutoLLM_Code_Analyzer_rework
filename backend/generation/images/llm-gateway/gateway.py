"""Per-run LLM gateway between a sandboxed coding agent and its model (spec 4.5, 5.4, 6.6).

The agent container sits on an internal Docker network whose only other member is this gateway, so
every byte it sends out goes through here. The gateway

* serves the harness's own **front protocol** (``GatewayConfig.front_protocol``): OpenAI chat completions
  natively (opencode, mini-swe-agent, aider, goose, qwen-code); Anthropic Messages (Claude Code) and
  OpenAI Responses (Codex) in **passthrough** to OpenRouter's native ``/messages`` and ``/responses``,
  with the policy applied on the native body, or in **translate** mode (any upstream, e.g. local
  endpoints); Gemini ``generateContent`` (Gemini CLI) in translate mode only. Translate maps the native
  body to canonical chat (``protocols/<front>.to_chat``), applies the chat policy, forwards to
  ``/chat/completions`` and maps the answer back (``from_chat`` / ``StreamTranslator``); budgets,
  request classes and the call record are the canonical ones, so they are identical across fronts;
* holds the real upstream credential (the agent has a dummy key) and pins the model, sampling, seed,
  ``max_tokens``, reasoning, provider routing, ``session_id`` and ``cache_control`` handling, so the
  harness cannot drift its parameters or switch to a cheaper "small model";
* enforces the run's budget with a **pre-call reservation** (``protocols/budget_rules.py``, shared with
  the executor's local proxy): a call that could pass a cap is refused, never clamped, with the
  protocol's native error shape, status 400 and "Run budget exhausted. Stop working and summarise.";
* counts requests per **request class** (``main`` vs ``subagent``/``compaction``/``auxiliary``);
* retries transient upstream failures before the first byte, so every harness sees the same provider
  reliability, and keeps streams alive with protocol-native pings during upstream silence;
* rate-limits calls per model (``rate_limit.rpm_per_model``, a token bucket that can be shared across a
  study's gateways through ``GATEWAY_RATE_DIR``);
* writes ``calls.jsonl`` (one record per forwarded call), ``gateway_events.jsonl`` and an atomically
  rewritten ``state.json`` (after every call start and end) under ``GATEWAY_LOG_DIR``, plus the native
  system prompt of the first ``main`` call as a blob;
* tunnels HTTPS ``CONNECT`` to an allowlist of package registries (``GATEWAY_CONNECT_ALLOWLIST``, port 443)
  and forward-proxies plain ``http://`` absolute-URI requests and ``CONNECT`` to exactly the dependency-cache
  ``host:port`` pairs in ``GATEWAY_PROXY_HOSTS``; everything else is refused.

Stdlib only. Environment: ``GATEWAY_CONFIG`` (the ``GatewayConfig`` JSON; the sandbox may add the runtime
extras ``listen: {port}``, ``connect_allowlist: [...]`` and ``proxy_hosts: [...]``), ``OPENROUTER_API_KEY``,
``GATEWAY_LOG_DIR`` (``/logs``), ``GATEWAY_PORT`` (8080), ``GATEWAY_CONNECT_ALLOWLIST`` (comma list),
``GATEWAY_PROXY_HOSTS`` (comma list of ``host:port``),
``GATEWAY_PING_INTERVAL_S`` (10), ``GATEWAY_RATE_DIR`` (unset: per-gateway bucket),
``GATEWAY_MAX_BODY_BYTES`` (64 MiB), ``GATEWAY_REFERER`` / ``GATEWAY_TITLE`` (OpenRouter attribution).
A config this version cannot run exits with status 78 after writing a ``config_invalid`` event.
"""

# Standalone script baked into its own image (no package).
# ruff: noqa: INP001

from __future__ import annotations

import contextlib
import fcntl
import http.client
import json
import math
import os
import queue
import re
import select
import socket
import ssl
import sys
import threading
import time
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

from protocols import anthropic_messages
from protocols import budget_rules
from protocols import gemini_generate
from protocols import openai_chat
from protocols import openai_responses
from protocols.common import ConfigError
from protocols.common import Policy
from protocols.common import contains_images
from protocols.common import json_or_none
from protocols.common import sha
from protocols.common import text_sha
from protocols.translation import chat_error_text
from protocols.translation import completion_sse

GATEWAY_VERSION = 3
EX_CONFIG = 78
FRONTS: dict[str, ModuleType] = {
    "openai_chat": openai_chat,
    "anthropic_messages": anthropic_messages,
    "openai_responses": openai_responses,
    "gemini_generate": gemini_generate,
}
DEFAULT_PING_INTERVAL_S = 10.0
DEFAULT_MAX_BODY_BYTES = 64 * 1024 * 1024
# Forward-proxy (dependency caches): hop-by-hop headers never forwarded in either direction.
HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "proxy-authorization",
        "proxy-authenticate",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    },
)
PROXY_METHODS = ("GET", "HEAD", "POST")
PROXY_TIMEOUT_S = 300


# =====================================================================================
# Logs
# =====================================================================================


class LogWriter:
    """``calls.jsonl``, ``gateway_events.jsonl``, ``state.json`` and ``blobs/`` under one directory."""

    def __init__(self, log_dir: Path) -> None:
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.calls_path = self.dir / "calls.jsonl"
        self.events_path = self.dir / "gateway_events.jsonl"
        self.state_path = self.dir / "state.json"
        self.blobs_dir = self.dir / "blobs"
        self._lock = threading.Lock()

    def _append(self, path: Path, record: Mapping[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock, path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def call(self, record: Mapping[str, Any]) -> None:
        self._append(self.calls_path, record)

    def event(self, kind: str, **fields: Any) -> None:
        self._append(self.events_path, {"ts": time.time(), "event": kind, **fields})

    def write_state(self, state: Mapping[str, Any]) -> None:
        """Atomic rewrite: a reader sees the old or the new file, never a partial one."""
        tmp = self.dir / f".state.json.{os.getpid()}.{threading.get_ident()}.tmp"
        data = json.dumps(dict(state), sort_keys=True)
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(self.state_path)

    def blob(self, text: str) -> str:
        """Store ``text`` content-addressed (``blobs/<text_sha>.txt``); returns the sha."""
        digest = text_sha(text)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        target = self.blobs_dir / f"{digest}.txt"
        if not target.exists():
            tmp = target.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(target)
        return digest


# =====================================================================================
# Rate limit (concurrency.rpm_per_model)
# =====================================================================================


class RateLimiter:
    """A token bucket of ``rpm`` calls per minute with a burst of five seconds' worth (at least 1).

    With ``shared_dir`` the bucket lives in ``<shared_dir>/<bucket_key>.bucket`` under ``flock``, so
    every gateway of a study that mounts the directory draws from one bucket; otherwise it is local.
    """

    def __init__(
        self,
        rpm: int | None,
        bucket_key: str | None = None,
        shared_dir: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rpm = rpm
        self.rate = (rpm or 0) / 60.0
        self.burst = max(1, math.ceil((rpm or 0) / 12)) if rpm else 0
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._tokens = float(self.burst)
        self._updated = clock()
        self._path: Path | None = None
        if rpm and shared_dir is not None:
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", bucket_key or "default")
            shared_dir.mkdir(parents=True, exist_ok=True)
            self._path = shared_dir / f"{safe}.bucket"

    def _take(self) -> float:
        """Take one token; returns 0 on success or the seconds until one is available."""
        now = self._clock()
        if self._path is None:
            with self._lock:
                self._tokens = min(self.burst, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return 0.0
                return (1 - self._tokens) / self.rate
        with self._lock, self._path.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                state = json_or_none(fh.read()) or {}
                tokens = float(state.get("tokens", self.burst))
                updated = float(state.get("updated", now))
                tokens = min(self.burst, tokens + max(0.0, now - updated) * self.rate)
                wait = 0.0
                if tokens >= 1:
                    tokens -= 1
                else:
                    wait = (1 - tokens) / self.rate
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps({"tokens": tokens, "updated": now}))
                fh.flush()
                return wait
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def acquire(self, timeout_s: float) -> tuple[float, bool]:
        """Wait for a token for at most ``timeout_s``. Returns ``(seconds waited, timed_out)``."""
        if not self.rpm:
            return 0.0, False
        started = self._clock()
        while True:
            wait = self._take()
            if wait <= 0:
                return self._clock() - started, False
            if self._clock() - started + wait > timeout_s:
                return self._clock() - started, True
            self._sleep(min(wait, 1.0))


# =====================================================================================
# Upstreams
# =====================================================================================


class UpstreamUnavailableError(Exception):
    """The upstream could not be reached (after retries)."""

    def __init__(self, message: str, retries: int) -> None:
        super().__init__(message)
        self.retries = retries


class OpenRouterUpstream:
    """OpenRouter over HTTPS (any URL scheme works, for tests). Holds the run owner's key."""

    kind = "openrouter"

    def __init__(self, policy: Policy, *, api_key: str, token: str = "", referer: str = "", title: str = "") -> None:
        self.policy = policy
        self.api_key = api_key
        self.token = token
        self.base = urlsplit(policy.upstream_base_url)
        self.referer = referer or "https://llm-lab.local"
        self.title = title or f"LLM Lab agent run {policy.run_id[:8]}"

    def retry_statuses(self) -> set[int]:
        return self.policy.retry_statuses()

    def prepare(self, body: dict[str, Any]) -> dict[str, Any]:
        """Last body changes specific to this upstream (OpenRouter: none)."""
        return body

    def headers(self, *, stream: bool, native: Mapping[str, str], reservation: budget_rules.Reservation) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "HTTP-Referer": self.referer,
            "X-Title": self.title,
        }
        headers.update(native)
        return headers

    def connect(self) -> http.client.HTTPConnection:
        timeout = self.policy.llm_call_timeout_s
        if self.base.scheme == "https":
            return http.client.HTTPSConnection(
                self.base.hostname,
                self.base.port or 443,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        return http.client.HTTPConnection(self.base.hostname, self.base.port or 80, timeout=timeout)

    def path(self, front_path: str) -> str:
        return self.base.path.rstrip("/") + front_path


UPSTREAMS: dict[str, type[OpenRouterUpstream]] = {"openrouter": OpenRouterUpstream}


# =====================================================================================
# One call's outcome
# =====================================================================================


@dataclass
class Outcome:
    status: int
    summary: dict[str, Any]
    retries: int = 0
    transport_error: bool = False
    error: Any = None
    first_byte: float | None = None
    client_gone: bool = False
    stream: bool = False
    # The status the upstream answered with, once it answered (None: never reached or never answered).
    # A 2xx/3xx answer means the upstream accepted, and bills, the call even if it then failed.
    upstream_status: int | None = None

    @property
    def upstream_billed(self) -> bool:
        return self.upstream_status is not None and self.upstream_status < 400


def _is_boundary(tail: bytes) -> bool:
    return not tail or tail.endswith((b"\n\n", b"\r\n\r\n"))


@dataclass
class Exchange:
    """A translated call: the native request, what its URL says (``request_options``) and the
    ``translation_dropped`` counts, filled by ``to_chat`` and then by the answer's translator."""

    native_body: Mapping[str, Any]
    options: dict[str, Any]
    dropped: dict[str, int]


# =====================================================================================
# The gateway
# =====================================================================================


class Gateway:
    """The gateway for one run attempt: policy, budget ledger, upstream, logs."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        log_dir: Path,
        api_key: str = "",
        upstream_token: str = "",
        connect_allowlist: tuple[str, ...] = (),
        proxy_hosts: tuple[str, ...] = (),
        ping_interval_s: float = DEFAULT_PING_INTERVAL_S,
        rate_dir: Path | None = None,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        referer: str = "",
        title: str = "",
    ) -> None:
        self.policy = Policy.from_config(config)
        problems = self.policy.problems()
        upstream_cls = UPSTREAMS.get(self.policy.upstream_kind)
        if upstream_cls is None:
            problems.append(f"no upstream adapter for kind {self.policy.upstream_kind!r} in this image")
        if problems:
            msg = "; ".join(problems)
            raise ConfigError(msg)
        assert upstream_cls is not None
        self.front = FRONTS[self.policy.front]
        # The wire protocol to the upstream: the front itself, or canonical chat when translating.
        self.translate = self.policy.mode == "translate"
        self.wire: ModuleType = openai_chat if self.translate else self.front
        self.logs = LogWriter(log_dir)
        self.upstream = upstream_cls(self.policy, api_key=api_key, token=upstream_token, referer=referer, title=title)
        self.limits = budget_rules.BudgetLimits.from_config(self.policy.raw)
        self.ledger = budget_rules.Ledger(self.limits, on_change=self.logs.write_state)
        self.rate = RateLimiter(self.policy.rpm_per_model, self.policy.bucket_key, rate_dir)
        self.connect_allowlist = frozenset(h.lower() for h in connect_allowlist)
        self.proxy_hosts = frozenset(_host_port_key(h) for h in proxy_hosts if h)
        self.ping_interval_s = ping_interval_s
        self.max_body_bytes = max_body_bytes
        self._flags = threading.Lock()
        self._system_prompt_logged = False
        self._exhaustion_logged = False
        self.logs.write_state(self.ledger.snapshot())

    @property
    def model_names(self) -> list[str]:
        return list(self.policy.presented_names) or [self.policy.upstream_model]

    def started_event(self) -> None:
        p = self.policy
        self.logs.event(
            "started",
            gateway_version=GATEWAY_VERSION,
            rules_version=budget_rules.RULES_VERSION,
            run_id=p.run_id,
            attempt=p.attempt,
            front_protocol=p.front,
            protocol_mode=p.mode,
            upstream_kind=p.upstream_kind,
            model=p.upstream_model,
            upstream_max_tokens=p.upstream_max_tokens,
            budget=dict(p.raw.get("budget") or {}),
            connect_allowlist=sorted(self.connect_allowlist),
            proxy_hosts=sorted(self.proxy_hosts),
            config_sha=sha(dict(p.raw)),
        )

    def make_server(self, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:  # noqa: S104
        server = ThreadingHTTPServer((host, port), Handler)
        server.daemon_threads = True
        server.gateway = self  # type: ignore[attr-defined]
        return server

    # -- the call path ---------------------------------------------------------------------------

    def _native_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        wanted = getattr(self.front, "FORWARD_HEADERS", ())
        lowered = {k.lower(): v for k, v in headers.items()}
        return {name: lowered[name] for name in wanted if name in lowered}

    def _attribution(self, headers: Mapping[str, str]) -> dict[str, str]:
        wanted = {*getattr(self.front, "ATTRIBUTION_HEADERS", ()), self.policy.request_class_source.lower()}
        return {k.lower(): v for k, v in headers.items() if k.lower() in wanted}

    def refuse(self, handler: Handler, kind: str, message: str, status: int = 400) -> None:
        self.logs.event("refused", reason=kind, status=status)
        handler.send_json(*self.front.error(kind, message, status))

    def call(self, handler: Handler, path: str, body: dict[str, Any]) -> None:
        policy = self.policy
        headers = dict(handler.headers.items())
        if contains_images(body):
            self.refuse(handler, "unsupported_content", "images and files are not supported by this gateway")
            return
        check = getattr(self.front, "check_request", None)
        if check is not None:
            try:
                check(body)
            except ValueError as exc:
                self.refuse(handler, "unsupported_request", str(exc))
                return
        request_class, raw_class = budget_rules.request_class(policy.request_class_source, headers)
        exchange: Exchange | None = None
        if self.translate:
            options_of = getattr(self.front, "request_options", None)
            target = urlsplit(handler.path)
            options = options_of(target.path, target.query) if options_of else {}
            try:
                chat_body, dropped = self.front.to_chat(body, **options)
            except ValueError as exc:
                self.refuse(handler, "unsupported_request", str(exc))
                return
            exchange = Exchange(body, options, dict(dropped))
            # The canonical policy on the canonical body: identical for every translated front.
            upstream_body, diff = openai_chat.apply_policy_native(chat_body, policy)
            est_prompt = budget_rules.estimate_tokens(openai_chat.prompt_text(chat_body))
        else:
            upstream_body, diff = self.front.apply_policy_native(body, policy)
            est_prompt = budget_rules.estimate_tokens(self.front.prompt_text(body))
        upstream_body = self.upstream.prepare(upstream_body)
        try:
            reservation = self.ledger.reserve(request_class, est_prompt, policy.upstream_max_tokens)
        except budget_rules.Refusal as refusal:
            self._refused(handler, refusal, request_class, est_prompt)
            return
        started = time.time()
        rate_wait = 0.0
        first_main = False
        outcome = Outcome(status=502, summary={}, transport_error=True, error="gateway error before forwarding")
        progress: dict[str, Any] = {}
        try:
            rate_wait, rate_timeout = self.rate.acquire(policy.llm_call_timeout_s)
            if rate_timeout:
                self.logs.event("rate_limit_timeout", index=reservation.index, waited_s=round(rate_wait, 3))
            if request_class == "main":
                with self._flags:
                    first_main = not self._system_prompt_logged
                    self._system_prompt_logged = True
            try:
                outcome = self._forward(handler, upstream_body, headers, reservation, progress, exchange)
            except BaseException as exc:
                # A gateway failure after the upstream answered is still a call the upstream bills.
                outcome = Outcome(
                    status=502,
                    summary={},
                    transport_error=True,
                    error=f"gateway error while forwarding: {exc!r}",
                    upstream_status=progress.get("upstream_status"),
                    stream=bool(upstream_body.get("stream")),
                )
                raise
        finally:
            self._finish(
                outcome,
                reservation=reservation,
                path=path,
                native_body=body,
                upstream_body=upstream_body,
                diff=diff,
                headers=headers,
                raw_class=raw_class,
                started=started,
                rate_wait=rate_wait,
                first_main=first_main,
                exchange=exchange,
            )

    def _refused(self, handler: Handler, refusal: budget_rules.Refusal, request_class: str, est: int) -> None:
        if refusal.exhausted:
            with self._flags:
                first, self._exhaustion_logged = not self._exhaustion_logged, True
            if first:
                self.logs.event(
                    "budget_exhausted",
                    reason=refusal.reason,
                    request_class=request_class,
                    state=self.ledger.stats(),
                )
            self.logs.event("refused", reason=refusal.reason, request_class=request_class, est_prompt_tokens=est)
            handler.send_json(*self.front.error("budget_exhausted", budget_rules.BUDGET_MESSAGE, 400))
            return
        cap = self.limits.call_max_input_tokens or 0
        self.logs.event("context_budget", request_class=request_class, est_prompt_tokens=est, max_input_tokens=cap)
        handler.send_json(*self.front.context_error(est, cap))

    def _open(self, data: bytes, headers: dict[str, str], stream: bool) -> tuple[Any, Any, int]:
        """Open the upstream request, retrying transient failures before any byte reaches the harness."""
        retries = 0
        statuses = self.upstream.retry_statuses()
        path = self.upstream.path(self.wire.UPSTREAM_PATH)
        while True:
            conn = None
            try:
                conn = self.upstream.connect()
                conn.request("POST", path, body=data, headers=headers)
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                if conn is not None:
                    conn.close()
                if retries >= self.policy.upstream_retries:
                    msg = f"upstream unreachable: {exc!r}"
                    raise UpstreamUnavailableError(msg, retries) from exc
            else:
                if resp.status not in statuses or retries >= self.policy.upstream_retries:
                    return conn, resp, retries
                with contextlib.suppress(OSError, http.client.HTTPException):
                    resp.read()
                conn.close()
            retries += 1
            self.logs.event("upstream_retry", attempt=retries, stream=stream)
            time.sleep(self.policy.backoff_s * (2 ** (retries - 1)))

    def _forward(
        self,
        handler: Handler,
        body: dict[str, Any],
        harness_headers: Mapping[str, str],
        reservation: budget_rules.Reservation,
        progress: dict[str, Any] | None = None,
        exchange: Exchange | None = None,
    ) -> Outcome:
        progress = {} if progress is None else progress
        stream = bool(body.get("stream"))
        headers = self.upstream.headers(
            stream=stream,
            native=self._native_headers(harness_headers),
            reservation=reservation,
        )
        data = json.dumps(body).encode()
        if not stream:
            return self._non_stream(handler, data, headers, progress, exchange)
        return self._stream(handler, data, headers, progress, exchange)

    def _summary(self, raw: bytes) -> dict[str, Any]:
        payload = json_or_none(raw)
        if isinstance(payload, Mapping):
            return self.wire.summarize(payload)
        return {"error": raw[:2000].decode(errors="replace") or "empty upstream response"}

    # -- translate mode: native answers from canonical ones ------------------------------------------

    def _native_error(self, status: int, payload: Any, raw: bytes) -> tuple[int, dict[str, Any]]:
        """An upstream chat error (or an unusable answer) in the front's native error shape."""
        if isinstance(payload, Mapping):
            message, code = chat_error_text(payload)
        else:
            message, code = (raw[:500].decode(errors="replace") or "empty upstream response"), None
        return self.front.upstream_error(status if status >= 400 else 502, message, code)

    @staticmethod
    def _is_answer(status: int, payload: Any) -> bool:
        return (
            status < 400 and isinstance(payload, Mapping) and not (payload.get("error") and not payload.get("choices"))
        )

    def _send_translated(self, handler: Handler, status: int, raw: bytes, exchange: Exchange) -> bool:
        payload = json_or_none(raw)
        if self._is_answer(status, payload):
            native = self.front.from_chat(payload, exchange.native_body, dropped=exchange.dropped, **exchange.options)
            return handler.send_json(status, native)
        return handler.send_json(*self._native_error(status, payload, raw))

    def _translator(self, exchange: Exchange | None) -> Any:
        if exchange is None:
            return None
        return self.front.StreamTranslator(exchange.native_body, dropped=exchange.dropped, **exchange.options)

    def _non_stream(
        self,
        handler: Handler,
        data: bytes,
        headers: dict[str, str],
        progress: dict[str, Any],
        exchange: Exchange | None = None,
    ) -> Outcome:
        try:
            conn, resp, retries = self._open(data, headers, stream=False)
        except UpstreamUnavailableError as exc:
            handler.send_json(*self.front.error("upstream_error", str(exc), 502))
            return Outcome(502, {}, exc.retries, transport_error=True, error=str(exc))
        first_byte = time.time()
        progress["upstream_status"] = resp.status
        try:
            raw = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            handler.send_json(*self.front.error("upstream_error", f"upstream read failed: {exc!r}", 502))
            return Outcome(
                502,
                {},
                retries,
                transport_error=True,
                error=repr(exc),
                first_byte=first_byte,
                upstream_status=resp.status,
            )
        conn.close()
        ctype = resp.getheader("Content-Type", "application/json")
        if exchange is not None:
            client_gone = not self._send_translated(handler, resp.status, raw, exchange)
        else:
            client_gone = not handler.send_raw(resp.status, ctype, raw)
        return Outcome(
            resp.status,
            self._summary(raw),
            retries,
            first_byte=first_byte,
            client_gone=client_gone,
            upstream_status=resp.status,
        )

    def _stream(
        self,
        handler: Handler,
        data: bytes,
        headers: dict[str, str],
        progress: dict[str, Any],
        exchange: Exchange | None = None,
    ) -> Outcome:
        translator = self._translator(exchange)
        native_ctype = getattr(translator, "content_type", "text/event-stream")
        box: dict[str, Any] = {}
        ready = threading.Event()

        def opener() -> None:
            try:
                box["result"] = self._open(data, headers, stream=True)
            except UpstreamUnavailableError as exc:
                box["error"] = exc
            except Exception as exc:  # noqa: BLE001 - surfaced to the harness as a native 502
                box["error"] = UpstreamUnavailableError(repr(exc), 0)
            finally:
                ready.set()

        threading.Thread(target=opener, name="upstream-open", daemon=True).start()
        started_stream = False
        alive = True
        while not ready.wait(self.ping_interval_s):
            # The upstream is silent before its first byte (queueing, retries, a long think): keep the
            # harness's stream alive with protocol-native pings.
            if not started_stream:
                alive = handler.start_stream(native_ctype)
                started_stream = True
            if alive:
                alive = handler.write_chunk(self.front.PING)
        if "error" in box:
            exc = box["error"]
            status, payload = self.front.error("upstream_error", str(exc), 502)
            if not started_stream:
                handler.send_json(status, payload)
            elif alive:
                handler.write_chunk(
                    translator.fail(status, payload) if translator else self.front.stream_error(status, payload),
                )
                handler.end_stream()
            return Outcome(502, {}, exc.retries, transport_error=True, error=str(exc), stream=True)
        conn, resp, retries = box["result"]
        progress["upstream_status"] = resp.status
        ctype = resp.getheader("Content-Type", "application/json")
        if resp.status < 400 and "text/event-stream" in ctype:
            if not started_stream:
                alive = handler.start_stream(native_ctype if translator else ctype)
            return self._relay(handler, conn, resp, retries, alive=alive, translator=translator)
        # An error (or a non-SSE body) where a stream was asked for.
        try:
            raw = resp.read()
        except (OSError, http.client.HTTPException):
            raw = b""
        finally:
            conn.close()
        gone = not alive
        if translator is not None:
            gone = not self._stream_translated(
                handler,
                translator,
                resp.status,
                raw,
                started=started_stream,
                alive=alive,
            )
        elif not started_stream:
            gone = not handler.send_raw(resp.status, ctype, raw)
        elif alive:
            payload = json_or_none(raw)
            if resp.status >= 400:
                if not isinstance(payload, Mapping):
                    payload = self.front.error("upstream_error", raw[:500].decode(errors="replace"), resp.status)[1]
                handler.write_chunk(self.front.stream_error(resp.status, payload))
            else:
                handler.write_chunk(b"data: " + raw + b"\n\n")
            handler.end_stream()
        return Outcome(
            resp.status,
            self._summary(raw),
            retries,
            first_byte=time.time(),
            client_gone=gone,
            stream=True,
            upstream_status=resp.status,
        )

    def _stream_translated(
        self,
        handler: Handler,
        translator: Any,
        status: int,
        raw: bytes,
        *,
        started: bool,
        alive: bool,
    ) -> bool:
        """A non-SSE upstream answer where the harness asked for a stream: a whole completion is
        re-encoded as the native stream, an error as the native error (in-stream once started)."""
        payload = json_or_none(raw)
        if self._is_answer(status, payload):
            if not started:
                alive = handler.start_stream(translator.content_type)
            if alive:
                alive = handler.write_chunk(translator.feed(completion_sse(payload)) + translator.finish())
                handler.end_stream()
            return alive
        err_status, native = self._native_error(status, payload, raw)
        if not started:
            return handler.send_json(err_status, native)
        if alive:
            alive = handler.write_chunk(translator.fail(err_status, native))
            handler.end_stream()
        return alive

    def _relay(
        self,
        handler: Handler,
        conn: Any,
        resp: Any,
        retries: int,
        *,
        alive: bool,
        translator: Any = None,
    ) -> Outcome:
        """Relay upstream SSE bytes unchanged (or through the front's translator), adding pings at event
        boundaries while the upstream is silent. A translator only ever writes whole native events."""
        accumulator = self.wire.StreamAccumulator()
        chunks: queue.Queue[bytes | BaseException | None] = queue.Queue()

        def reader() -> None:
            try:
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    chunks.put(chunk)
            except BaseException as exc:  # noqa: BLE001 - handed to the relay loop
                chunks.put(exc)
            finally:
                chunks.put(None)

        threading.Thread(target=reader, name="upstream-read", daemon=True).start()
        first_byte: float | None = None
        tail = b""
        error: Any = None
        try:
            while True:
                try:
                    item = chunks.get(timeout=self.ping_interval_s)
                except queue.Empty:
                    if alive and (translator is not None or _is_boundary(tail)):
                        alive = handler.write_chunk(self.front.PING)
                    if not alive:
                        break
                    continue
                if item is None:
                    break
                if isinstance(item, BaseException):
                    error = f"upstream stream broke: {item!r}"
                    continue
                if first_byte is None:
                    first_byte = time.time()
                accumulator.feed(item)
                tail = (tail + item)[-4:]
                out = translator.feed(item) if translator is not None else item
                if alive and out:
                    alive = handler.write_chunk(out)
                if not alive:
                    break  # the harness went away: closing the upstream connection ends the call
        finally:
            conn.close()
        if alive and translator is not None:
            closing = translator.finish(error)
            if closing:
                alive = handler.write_chunk(closing)
        if alive:
            handler.end_stream()
        summary = accumulator.summary()
        if error and not summary.get("error"):
            summary["error"] = error
        return Outcome(
            resp.status,
            summary,
            retries,
            error=error,
            first_byte=first_byte,
            client_gone=not alive,
            stream=True,
            upstream_status=resp.status,
        )

    # -- accounting and the call record --------------------------------------------------------------

    def _finish(
        self,
        outcome: Outcome,
        *,
        reservation: budget_rules.Reservation,
        path: str,
        native_body: Mapping[str, Any],
        upstream_body: Mapping[str, Any],
        diff: Mapping[str, Any],
        headers: Mapping[str, str],
        raw_class: str | None,
        started: float,
        rate_wait: float,
        first_main: bool,
        exchange: Exchange | None = None,
    ) -> None:
        summary = outcome.summary
        usage = summary.get("normalized_usage")
        ok = outcome.status < 400 and not outcome.transport_error and not summary.get("error")
        finish = summary.get("finish_reason")
        completion = (usage or {}).get("completion_tokens") or 0
        empty = not summary.get("content") and not summary.get("tool_calls")
        # An empty answer that used the whole cap (all of it on reasoning) is a truncated call.
        truncated = finish == "length" or (ok and empty and completion >= reservation.max_tokens)
        settlement = self.ledger.settle(
            reservation,
            usage,
            ok=ok,
            finish_reason=finish,
            truncated=truncated,
            upstream_billed=outcome.upstream_billed,
        )
        ended = time.time()
        duration = ended - started
        ttft_ms = round((outcome.first_byte - started) * 1000, 3) if outcome.first_byte else None
        gen_s = (ended - outcome.first_byte) if (outcome.stream and outcome.first_byte) else duration
        system_prompt = self.front.system_prompt(native_body) if first_main else None
        system_sha = None
        if system_prompt and self.policy.log_native_system_prompt:
            system_sha = self.logs.blob(system_prompt)
        error = outcome.error or summary.get("error")
        record = {
            "index": reservation.index,
            "seq": reservation.index,
            "ts": started,
            "started": started,
            "ended": ended,
            "duration_s": round(duration, 6),
            "ttft_ms": ttft_ms,
            "tokens_per_s": round(completion / gen_s, 3) if ok and completion and gen_s > 0 else None,
            "path": path,
            "stream": outcome.stream,
            "request_class": reservation.request_class,
            "request_class_header": raw_class,
            "attribution": self._attribution(headers),
            "front_protocol": self.policy.front,
            "protocol_mode": self.policy.mode,
            "requested_model": native_body.get("model") or (exchange.options.get("model") if exchange else None),
            "requested_reasoning": self.front.requested_reasoning(native_body),
            "upstream_model": upstream_body.get("model"),
            "response_model": summary.get("model") if ok else None,
            "response_id": summary.get("id"),
            "served_provider": summary.get("provider") if ok else None,
            "system_fingerprint": summary.get("system_fingerprint") if ok else None,
            "serving_tier": self.policy.serving_tier,
            "status": outcome.status,
            "transport_error": outcome.transport_error,
            "client_gone": outcome.client_gone,
            "error": error if (error or not ok) else None,
            "finish_reason": finish if ok else None,
            "native_finish_reason": summary.get("native_finish_reason"),
            "truncated": settlement.truncated,
            "usage": usage,
            "usage_estimated": settlement.usage_estimated,
            "cost": round(settlement.cost, 10),
            "cost_source": settlement.cost_source,
            "list_cost": round(settlement.list_cost, 10),
            "reserved_usd": round(reservation.usd, 10),
            "est_prompt_tokens": reservation.est_prompt_tokens,
            "reserved_prompt_tokens": reservation.prompt_tokens,
            "upstream_max_tokens": reservation.max_tokens,
            "upstream_retries": outcome.retries,
            "rate_wait_s": round(rate_wait, 3),
            "translation_dropped": self._translation_dropped(exchange),
            "policy_diff": dict(diff),
            "native_request_sha": sha(native_body),
            "native_system_prompt": system_prompt,
            "native_system_prompt_sha": system_sha,
            "request": upstream_body,
            "response": {k: v for k, v in summary.items() if k != "normalized_usage"},
        }
        self.logs.call(record)

    def _translation_dropped(self, exchange: Exchange | None) -> dict[str, int]:
        """What translation could not carry. ``cache_control`` markers it lost do not count when the
        policy strips every marker anyway (``prompt_cache`` ``off`` / ``auto``)."""
        if exchange is None:
            return {}
        out = {key: int(n) for key, n in exchange.dropped.items() if n}
        if self.policy.prompt_cache in ("off", "auto"):
            out.pop("cache_control", None)
        return dict(sorted(out.items()))

    def connect_allowed(self, host: str, port: int) -> bool:
        """Registries on 443, or an exact dependency-cache ``host:port``."""
        if f"{host.lower()}:{port}" in self.proxy_hosts:
            return True
        return host.lower() in self.connect_allowlist and port == 443

    def proxy_allowed(self, host: str, port: int) -> bool:
        """Plain-HTTP forward proxying: exact dependency-cache ``host:port`` pairs only."""
        return f"{host.lower()}:{port}" in self.proxy_hosts


def _host_port_key(value: str) -> str:
    host, _, port = value.strip().rpartition(":")
    if not host or not port.isdigit():
        msg = f"proxy host {value!r} must be host:port"
        raise ConfigError(msg)
    return f"{host.lower()}:{int(port)}"


# =====================================================================================
# HTTP handler
# =====================================================================================


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "llm-gateway"

    @property
    def gateway(self) -> Gateway:
        return self.server.gateway  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("gateway: " + (fmt % args) + "\n")

    # -- writing --------------------------------------------------------------------------------

    def send_json(self, status: int, payload: Mapping[str, Any]) -> bool:
        return self.send_raw(status, "application/json", json.dumps(dict(payload)).encode())

    def send_raw(self, status: int, ctype: str, data: bytes) -> bool:
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
        except OSError:
            self.close_connection = True
            return False
        return True

    def start_stream(self, ctype: str) -> bool:
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.flush()
        except OSError:
            self.close_connection = True
            return False
        return True

    def write_chunk(self, data: bytes) -> bool:
        try:
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()
        except OSError:
            self.close_connection = True
            return False
        return True

    def end_stream(self) -> None:
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            self.close_connection = True

    # -- routes ---------------------------------------------------------------------------------

    def do_HEAD(self) -> None:
        if self.path.startswith("http://"):
            self._forward_proxy("HEAD")
            return
        # Startup probes (Claude Code's HEAD /api/hello) are rejected; nothing is served by HEAD.
        self.gateway.logs.event("rejected_path", method="HEAD", path=urlsplit(self.path).path)
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("http://"):
            self._forward_proxy("GET")
            return
        gw = self.gateway
        path = urlsplit(self.path).path
        if path == "/healthz":
            self.send_json(200, {"ok": True, "front_protocol": gw.policy.front, "version": GATEWAY_VERSION})
        elif path == "/stats":
            self.send_json(200, gw.ledger.stats())
        elif gw.front.route("GET", path) == "models":
            self.send_json(200, gw.front.models_payload(gw.model_names))
        else:
            gw.logs.event("rejected_path", method="GET", path=path)
            self.send_json(*gw.front.error("not_found", f"not found: {path}", 404))

    def do_POST(self) -> None:
        if self.path.startswith("http://"):
            self._forward_proxy("POST")
            return
        gw = self.gateway
        path = urlsplit(self.path).path
        kind = gw.front.route("POST", path)
        if kind is None:
            gw.logs.event("rejected_path", method="POST", path=path)
            self.send_json(*gw.front.error("not_found", f"the gateway does not serve {path}", 404))
            return
        body = self._read_body()
        if body is None:
            return
        if kind == "count_tokens":
            self.send_json(200, gw.front.count_tokens(body))
            return
        try:
            gw.call(self, path, body)
        except Exception as exc:  # noqa: BLE001 - logged; the harness gets a native 500
            gw.logs.event("internal_error", path=path, error=repr(exc))
            with contextlib.suppress(OSError):
                self.send_json(*gw.front.error("internal_error", "gateway internal error", 500))

    def _read_body(self) -> dict[str, Any] | None:
        gw = self.gateway
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > gw.max_body_bytes:
            self.close_connection = True
            self.send_json(*gw.front.error("bad_request", "missing, invalid or too large Content-Length", 413))
            return None
        raw = self.rfile.read(length) if length else b""
        body = json_or_none(raw or b"{}")
        if not isinstance(body, dict):
            self.send_json(*gw.front.error("bad_request", "request body is not a JSON object", 400))
            return None
        return body

    def _forward_proxy(self, method: str) -> None:
        """Forward one plain-HTTP request to an allowed dependency cache, streaming the answer back."""
        gw = self.gateway
        target = urlsplit(self.path)
        host = target.hostname or ""
        port = target.port or 80
        if method not in PROXY_METHODS or not gw.proxy_allowed(host, port):
            gw.logs.event("rejected_proxy", method=method, target=f"{host}:{port}")
            self.send_json(
                403,
                {"error": {"message": f"egress to {host}:{port} is not allowed", "code": "egress_denied"}},
            )
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        path = target.path or "/"
        if target.query:
            path += "?" + target.query
        conn = http.client.HTTPConnection(host, port, timeout=PROXY_TIMEOUT_S)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            gw.logs.event("proxy", method=method, target=f"{host}:{port}", status=502, error=repr(exc))
            self.send_json(502, {"error": {"message": f"cannot reach {host}:{port}: {exc}", "code": "upstream_error"}})
            return
        try:
            self.send_response(resp.status)
            length_header = resp.getheader("Content-Length")
            for name, value in resp.getheaders():
                if name.lower() not in HOP_HEADERS and name.lower() != "content-length":
                    self.send_header(name, value)
            chunked = length_header is None and method != "HEAD"
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            elif length_header is not None:
                self.send_header("Content-Length", length_header)
            self.end_headers()
            if method != "HEAD":
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    if chunked:
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    else:
                        self.wfile.write(chunk)
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            self.close_connection = True
        finally:
            conn.close()
        gw.logs.event("proxy", method=method, target=f"{host}:{port}", path=target.path, status=resp.status)

    def do_CONNECT(self) -> None:
        gw = self.gateway
        host, _, port_text = self.path.partition(":")
        try:
            port = int(port_text or 443)
        except ValueError:
            port = -1
        if not gw.connect_allowed(host, port):
            gw.logs.event("rejected_connect", target=self.path)
            self.send_json(
                403,
                {"error": {"message": f"egress to {self.path} is not allowed", "code": "egress_denied"}},
            )
            return
        try:
            upstream = socket.create_connection((host, port), timeout=30)
        except OSError as exc:
            self.send_json(502, {"error": {"message": f"cannot reach {self.path}: {exc}", "code": "upstream_error"}})
            return
        gw.logs.event("connect", target=self.path)
        self.send_response(200, "Connection Established")
        self.end_headers()
        _tunnel(self.connection, upstream)
        self.close_connection = True


def _tunnel(client: socket.socket, upstream: socket.socket) -> None:
    sockets = [client, upstream]
    try:
        while True:
            readable, _, errored = select.select(sockets, [], sockets, 300)
            if errored or not readable:
                return
            for sock in readable:
                data = sock.recv(65536)
                if not data:
                    return
                (upstream if sock is client else client).sendall(data)
    except OSError:
        return
    finally:
        upstream.close()


# =====================================================================================
# Entry point
# =====================================================================================


def from_env(env: Mapping[str, str] | None = None) -> Gateway:
    env = os.environ if env is None else env
    config = json.loads(env.get("GATEWAY_CONFIG") or "{}")
    if not isinstance(config, dict):
        msg = "GATEWAY_CONFIG is not a JSON object"
        raise ConfigError(msg)
    allow = tuple(h.strip() for h in (env.get("GATEWAY_CONNECT_ALLOWLIST") or "").split(",") if h.strip())
    if not allow:
        # Sandbox-owned runtime extras next to the GatewayConfig keys (never part of the policy).
        allow = tuple(str(h) for h in config.get("connect_allowlist") or ())
    proxy_hosts = tuple(h.strip() for h in (env.get("GATEWAY_PROXY_HOSTS") or "").split(",") if h.strip())
    if not proxy_hosts:
        proxy_hosts = tuple(str(h) for h in config.get("proxy_hosts") or ())
    rate_dir = env.get("GATEWAY_RATE_DIR")
    return Gateway(
        config,
        log_dir=Path(env.get("GATEWAY_LOG_DIR") or "/logs"),
        api_key=env.get("OPENROUTER_API_KEY", ""),
        upstream_token=env.get("LLMLAB_UPSTREAM_TOKEN", ""),
        connect_allowlist=allow,
        proxy_hosts=proxy_hosts,
        ping_interval_s=float(env.get("GATEWAY_PING_INTERVAL_S") or DEFAULT_PING_INTERVAL_S),
        rate_dir=Path(rate_dir) if rate_dir else None,
        max_body_bytes=int(env.get("GATEWAY_MAX_BODY_BYTES") or DEFAULT_MAX_BODY_BYTES),
        referer=env.get("GATEWAY_REFERER", ""),
        title=env.get("GATEWAY_TITLE", ""),
    )


def main(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    try:
        gateway = from_env(env)
    except (ConfigError, ValueError) as exc:
        log_dir = Path(env.get("GATEWAY_LOG_DIR") or "/logs")
        with contextlib.suppress(OSError):
            LogWriter(log_dir).event("config_invalid", error=str(exc))
        sys.stderr.write(f"gateway: refusing to start: {exc}\n")
        return EX_CONFIG
    port = int(env.get("GATEWAY_PORT") or (gateway.policy.raw.get("listen") or {}).get("port") or 8080)
    server = gateway.make_server("0.0.0.0", port)  # noqa: S104 - internal network only
    gateway.started_event()
    p = gateway.policy
    sys.stderr.write(f"gateway: {p.front}/{p.mode} on :{port} for {p.upstream_model}\n")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
