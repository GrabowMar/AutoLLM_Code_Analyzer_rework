"""HTTP calls from the executor to direct endpoints (spec §D.8).

``http.client`` directly, never ``requests``:

- the target is re-validated (:func:`security.validate_direct_base_url`) and
  re-resolved on **every** connection, and the socket connects to the vetted
  IP, so DNS rebinding cannot redirect a call;
- redirects are never followed (a 3xx is an ``http`` error);
- for https the TLS handshake uses ``server_hostname=<original host>``, so
  the certificate is checked against the hostname, not the IP;
- responses are capped at :data:`MAX_RESPONSE_BYTES`.

The relay script implements the same path allowlist for its side
(:data:`ALLOWED_PATHS`; a test asserts the two agree).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.inference.security import Target
from backend.inference.security import validate_direct_base_url

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
# (method, path) pairs the platform may ask a model server for. Pull, delete,
# create, push and copy are never allowed. ``/api/generate`` only with an empty
# prompt (warm-up / unload); checked by :func:`path_allowed`.
ALLOWED_PATHS = frozenset(
    {
        ("POST", "/api/chat"),
        ("POST", "/api/generate"),
        ("POST", "/api/show"),
        ("GET", "/api/tags"),
        ("GET", "/api/ps"),
        ("GET", "/api/version"),
        ("POST", "/v1/chat/completions"),
        ("GET", "/v1/models"),
    },
)


def path_allowed(method: str, path: str, body: Any) -> bool:
    if (method.upper(), path) not in ALLOWED_PATHS:
        return False
    if path == "/api/generate":
        return isinstance(body, dict) and not body.get("prompt")
    return True


class UpstreamError(Exception):
    """A call failed before a usable HTTP response: ``kind`` is a relay result error kind."""

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


@dataclass
class UpstreamResponse:
    status: int
    payload: Any  # parsed JSON, or None
    text: str
    elapsed_ms: int


def _join(base_path: str, path: str) -> str:
    # base_url is the server root; tolerate a configured ".../v1" for OpenAI-style paths.
    if base_path.endswith("/v1") and path.startswith("/v1/"):
        base_path = base_path[: -len("/v1")]
    # Gemini's OpenAI layer (".../v1beta/openai") serves "/chat/completions" and "/models" without "/v1".
    elif base_path.endswith("/openai") and path.startswith("/v1/"):
        path = path[len("/v1") :]
    return f"{base_path}{path}"


# Claude API (OpenAI-compatible layer): /v1/models needs a version header, and
# current Claude models reject the sampling knobs with a 400 instead of ignoring them.
ANTHROPIC_HOST = "api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_UNSUPPORTED_PARAMS = ("temperature", "top_p", "top_k")


# OpenCode Zen / Go (base URL https://opencode.ai/zen/go): requests must name
# their client and carry a stable ``x-opencode-session`` per conversation, or
# chat completions are refused ("MissingSessionID").
OPENCODE_HOST = "opencode.ai"
OPENCODE_USER_AGENT = "llmlab/1.0"


def _opencode_session_id(body: Any) -> str:
    """A stable id per conversation: the model plus its first two messages.

    Continuations and repair rounds of one generation resend the same opening
    messages, so they share a session (and its prompt cache); separate jobs differ.
    """
    head: Any = None
    if isinstance(body, dict):
        head = [body.get("model"), (body.get("messages") or [])[:2]]
    digest = hashlib.sha256(json.dumps(head, sort_keys=True, default=str).encode()).hexdigest()
    return f"llmlab-{digest[:32]}"


def _provider_quirks(host: str, body: Any, headers: dict[str, str]) -> Any:
    if host == OPENCODE_HOST:
        headers["User-Agent"] = OPENCODE_USER_AGENT
        headers["x-opencode-session"] = _opencode_session_id(body)
        return body
    if host != ANTHROPIC_HOST:
        return body
    headers["anthropic-version"] = ANTHROPIC_VERSION
    if isinstance(body, dict) and any(k in body for k in ANTHROPIC_UNSUPPORTED_PARAMS):
        body = {k: v for k, v in body.items() if k not in ANTHROPIC_UNSUPPORTED_PARAMS}
    return body


def _connect(target: Target, timeout: float) -> http.client.HTTPConnection:
    last_error: OSError | None = None
    for address in target.addresses:
        context = ssl.create_default_context() if target.scheme == "https" else None
        try:
            sock = socket.create_connection((address, target.port), timeout=timeout)
            if context is not None:
                sock = context.wrap_socket(sock, server_hostname=target.host)
        except ssl.SSLCertVerificationError as exc:
            msg = f"TLS certificate of {target.host} is not valid: {exc}"
            raise UpstreamError(kind="connect", message=msg) from exc
        except OSError as exc:
            last_error = exc
            continue
        if context is not None:
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                target.host,
                target.port,
                timeout=timeout,
                context=context,
            )
        else:
            conn = http.client.HTTPConnection(target.host, target.port, timeout=timeout)
        conn.sock = sock
        return conn
    msg = f"cannot connect to {target.host}:{target.port}: {last_error}"
    raise UpstreamError(kind="connect", message=msg)


def call(
    base_url: str,
    method: str,
    path: str,
    body: Any = None,
    *,
    timeout: float,
    api_key: str | None = None,
    on_socket: Callable[[socket.socket | None], None] | None = None,
    stream_limits: StreamLimits | None = None,
) -> UpstreamResponse:
    """One request to a direct endpoint; raises :class:`UpstreamError` (``connect`` | ``timeout`` | ``http`` | ...).

    ``on_socket(sock)`` is called with the live socket once connected (and
    with ``None`` when done), so a canceller can ``shutdown()`` it.

    With ``stream_limits``, a non-stream OpenAI chat call is made as a **stream** upstream and
    re-assembled into the non-stream ``chat.completion`` its caller expects, cut at the per-call
    output cap or wall clock with ``finish_reason=length`` (see :class:`StreamLimits`).
    """
    if not path_allowed(method, path, body):
        raise UpstreamError(kind="forbidden_path", message=f"{method} {path} is not allowed")
    try:
        target = validate_direct_base_url(base_url)
    except ValueError as exc:
        raise UpstreamError(kind="connect", message=str(exc)) from exc
    streamed = (
        stream_limits is not None
        and method.upper() == "POST"
        and path == CHAT_COMPLETIONS_PATH
        and isinstance(body, dict)
        and not body.get("stream")
    )
    if streamed:
        body = {**body, "stream": True, "stream_options": {"include_usage": True}}
        if stream_limits.wall_clock_s:
            timeout = min(timeout, stream_limits.wall_clock_s)
    started = time.monotonic()
    conn = _connect(target, timeout)
    if on_socket is not None:
        on_socket(conn.sock)
    try:
        headers = {"Host": target.host_header, "Accept": "application/json", "User-Agent": "llmlab-executor"}
        if streamed:
            headers["Accept"] = "text/event-stream"
        body = _provider_quirks(target.host, body, headers)
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        conn.request(method.upper(), _join(target.base_path, path), body=data, headers=headers)
        resp = conn.getresponse()
        if streamed and resp.status == 200 and "text/event-stream" in (resp.getheader("Content-Type") or ""):
            assert stream_limits is not None
            payload = _read_chat_stream(resp, conn.sock, stream_limits, timeout=timeout, started=started)
            elapsed = int((time.monotonic() - started) * 1000)
            status = 502 if payload.get("llmlab_stream_error") else 200
            return UpstreamResponse(
                status=status,
                payload=payload,
                text=json.dumps(payload)[:2000],
                elapsed_ms=elapsed,
            )
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except TimeoutError as exc:
        raise UpstreamError(kind="timeout", message=f"no answer within {timeout:.0f}s") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise UpstreamError(kind="connect", message=f"connection to {target.host} failed: {exc}") from exc
    finally:
        if on_socket is not None:
            on_socket(None)
        conn.close()
    if len(raw) > MAX_RESPONSE_BYTES:
        raise UpstreamError(kind="too_large", message="upstream response exceeds 32 MB")
    elapsed = int((time.monotonic() - started) * 1000)
    text = raw.decode("utf-8", errors="replace")
    if 300 <= resp.status < 400:
        raise UpstreamError(kind="http", message=f"redirect ({resp.status}) not followed", status=resp.status)
    try:
        payload = json.loads(text) if text else None
    except ValueError:
        payload = None
    return UpstreamResponse(status=resp.status, payload=payload, text=text[:2000], elapsed_ms=elapsed)


# =====================================================================================
# Streamed non-stream calls: per-call output cap and wall clock (E2E-17)
# =====================================================================================

CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
# Output tokens of a cut stream (or one without a usage chunk) are estimated like the gateway's prompt
# estimate (``budget_rules.CHARS_PER_TOKEN``): ceil(chars / 3.6) over content, reasoning and tool-call text.
OUTPUT_CHARS_PER_TOKEN = 3.6
# The cap cut is only a runaway backstop (the wall clock is the main bound): a stream is cut once its
# output text reaches ``max_output_tokens * CAP_CUT_CHARS_PER_TOKEN`` characters. Real text runs from
# ~3 (qwen reasoning) to well over 4 chars per token (prose, long identifiers), so a cut at the 3.6
# estimate (even with 10 % headroom) pre-empted providers that bound the answer themselves and recorded a
# truncation that never happened (E2E-20; live: deepseek-v4.1-flash ended on its own ``length`` at 100
# tokens after 591 chars, 5.9 chars per token). At 8 chars per token a stream still running has run past
# its ``max_tokens``; the cut then comes at ~1.4-2.5x the cap in real tokens.
CAP_CUT_CHARS_PER_TOKEN = 8.0
MAX_SSE_LINE_BYTES = 4 * 1024 * 1024
REASONING_KEYS = ("reasoning_content", "reasoning")
CUT_MAX_OUTPUT = "max_output_tokens"
CUT_WALL_CLOCK = "wall_clock"


@dataclass(frozen=True)
class StreamLimits:
    """The per-call limits of a call whose client did not stream (spec 6.6; E2E-17).

    Some providers do not bound reasoning by ``max_tokens`` (OpenCode Go's qwen3.8-flash reasons
    on far past it), so a non-stream call could only end at the timeout chain, recorded as an infra
    failure. Streamed upstream, the call is cut once its estimated output reaches
    ``max_output_tokens`` (the policy-pinned ``max_tokens``: ``call.max_output_tokens`` plus the
    reasoning budget) is clearly overrun (:data:`CAP_CUT_CHARS_PER_TOKEN`) or ``wall_clock_s`` has passed
    with output flowing, and answered as a well-formed ``chat.completion`` with ``finish_reason=length``:
    a model truncation. ``prompt_tokens_estimate`` is the usage a cut call (or one whose stream carried
    no usage chunk) reports for its prompt.
    ``call_wall_left_s`` is what was left of the policy's per-call wall clock when the limits were made
    (the call's queue time and earlier attempts already subtracted; E2E-20), None when the call has no
    policy wall clock; ``wall_clock_s`` is never above it.
    """

    max_output_tokens: int | None
    wall_clock_s: float | None
    prompt_tokens_estimate: int = 0
    call_wall_left_s: float | None = None


class ChatStream:
    """Re-assembles an OpenAI ``chat.completion.chunk`` stream into one ``chat.completion``."""

    def __init__(self) -> None:
        self.meta: dict[str, Any] = {}
        self.role = "assistant"
        self.content: list[str] = []
        self.reasoning: dict[str, list[str]] = {}
        self.reasoning_details: list[Any] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None
        self.native_finish_reason: str | None = None
        self.usage: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None
        self.chars = 0
        self.reasoning_chars = 0
        self.done = False

    @property
    def has_output(self) -> bool:
        return self.chars > 0

    def output_tokens(self) -> int:
        return math.ceil(self.chars / OUTPUT_CHARS_PER_TOKEN)

    def reasoning_tokens(self) -> int:
        return math.ceil(self.reasoning_chars / OUTPUT_CHARS_PER_TOKEN)

    def feed(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        if event.get("error") and not event.get("choices"):
            self.error = event
            return
        for key in ("id", "object", "created", "model", "system_fingerprint", "provider"):
            if event.get(key) is not None and key not in self.meta:
                self.meta[key] = event[key]
        if isinstance(event.get("usage"), dict) and event["usage"]:
            self.usage = event["usage"]
        for choice in event.get("choices") or []:
            if not isinstance(choice, dict) or int(choice.get("index") or 0) != 0:
                continue
            self._delta(choice.get("delta") or {})
            if choice.get("finish_reason"):
                self.finish_reason = str(choice["finish_reason"])
            if choice.get("native_finish_reason"):
                self.native_finish_reason = str(choice["native_finish_reason"])

    def _delta(self, delta: Any) -> None:
        if not isinstance(delta, dict):
            return
        if delta.get("role"):
            self.role = str(delta["role"])
        if isinstance(delta.get("content"), str) and delta["content"]:
            self.content.append(delta["content"])
            self.chars += len(delta["content"])
        for key in REASONING_KEYS:
            text = delta.get(key)
            if isinstance(text, str) and text:
                self.reasoning.setdefault(key, []).append(text)
                self.chars += len(text)
                self.reasoning_chars += len(text)
        if isinstance(delta.get("reasoning_details"), list):
            self.reasoning_details.extend(delta["reasoning_details"])
        for call in delta.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            slot = self.tool_calls.setdefault(
                int(call.get("index") or 0),
                {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if call.get("id"):
                slot["id"] = call["id"]
            if call.get("type"):
                slot["type"] = call["type"]
            function = call.get("function") or {}
            for part in ("name", "arguments"):
                text = function.get(part)
                if isinstance(text, str) and text:
                    slot["function"][part] += text
                    self.chars += len(text)

    def completion(self, limits: StreamLimits, cut: str | None = None, elapsed_s: float = 0.0) -> dict[str, Any]:
        """The ``chat.completion``; ``cut`` (``max_output_tokens`` | ``wall_clock``) ends it at ``length``."""
        calls = [self.tool_calls[i] for i in sorted(self.tool_calls)]
        dropped = 0
        if cut:
            # A tool call cut mid-arguments is not a call the harness could run.
            complete = [c for c in calls if _json_object(c["function"]["arguments"] or "{}")]
            dropped, calls = len(calls) - len(complete), complete
        message: dict[str, Any] = {"role": self.role, "content": "".join(self.content) or (None if calls else "")}
        for key, parts in self.reasoning.items():
            message[key] = "".join(parts)
        if self.reasoning_details:
            message["reasoning_details"] = self.reasoning_details
        if calls:
            message["tool_calls"] = [{k: v for k, v in c.items() if v is not None} for c in calls]
        choice: dict[str, Any] = {
            "index": 0,
            "message": message,
            "finish_reason": "length" if cut else (self.finish_reason or "stop"),
        }
        if cut:
            choice["native_finish_reason"] = f"llmlab_cut:{cut}"
        elif self.native_finish_reason:
            choice["native_finish_reason"] = self.native_finish_reason
        payload: dict[str, Any] = {**self.meta, "object": "chat.completion", "choices": [choice]}
        if cut:
            # No upstream usage reaches a cut stream: the estimate is marked, never passed off as billed.
            payload["usage"] = self.estimated_usage(limits)
            payload["llmlab_cut"] = {
                "reason": cut,
                "elapsed_s": round(elapsed_s, 3),
                "max_output_tokens": limits.max_output_tokens,
                "wall_clock_s": limits.wall_clock_s,
                "est_completion_tokens": payload["usage"]["completion_tokens"],
                "output_chars": self.chars,
                "dropped_tool_calls": dropped,
            }
        elif self.usage is not None:
            payload["usage"] = self.usage
        else:
            # A provider that ignores ``stream_options.include_usage``: without an estimate the call would
            # book 0 tokens and $0 and slip past the token and spend caps (E2E-20).
            payload["usage"] = self.estimated_usage(limits)
        return payload

    def estimated_usage(self, limits: StreamLimits) -> dict[str, Any]:
        """Usage estimated from the prompt estimate and the output text, marked ``llmlab_estimated``."""
        completion = self.output_tokens()
        prompt = int(limits.prompt_tokens_estimate or 0)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "completion_tokens_details": {"reasoning_tokens": self.reasoning_tokens()},
            "llmlab_estimated": True,
        }


def _json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False


def _read_chat_stream(
    resp: http.client.HTTPResponse,
    sock: socket.socket | None,
    limits: StreamLimits,
    *,
    timeout: float,
    started: float,
) -> dict[str, Any]:
    """Read an SSE chat stream into one ``chat.completion``, cutting it at ``limits``.

    Usage comes from the stream's own usage chunk (``stream_options.include_usage``), so a stream
    that finishes with one is accounted exactly; a cut one, or one that never sent a usage chunk,
    carries an estimate (``llmlab_estimated``). Once the provider has sent its own finish the answer
    is never cut: the reader only waits for the usage chunk / ``[DONE]`` until the wall clock or a
    read timeout, then returns the finished answer.
    A stream silent for ``timeout`` (the endpoint's per-read timeout), or one that reaches the wall
    clock before any output, is an upstream ``timeout`` as before; one that ends without a finish is
    a broken connection; an in-stream error object is an HTTP 502 error answer.
    """
    acc = ChatStream()
    deadline = started + limits.wall_clock_s if limits.wall_clock_s else None
    cut_chars = limits.max_output_tokens * CAP_CUT_CHARS_PER_TOKEN if limits.max_output_tokens else None
    data: list[bytes] = []
    total = 0
    cut: str | None = None
    while cut is None and not acc.done:
        now = time.monotonic()
        if deadline is not None and now >= deadline:
            if acc.finish_reason is None:
                cut = CUT_WALL_CLOCK
            break
        if sock is not None:
            sock.settimeout(timeout if deadline is None else max(0.01, min(timeout, deadline - now)))
        try:
            line = resp.readline(MAX_SSE_LINE_BYTES)
        except TimeoutError:
            if acc.finish_reason is not None:
                break  # finished; only the usage chunk / [DONE] did not come
            if deadline is not None and time.monotonic() >= deadline - 0.02:
                cut = CUT_WALL_CLOCK
                break
            raise
        if not line:
            _dispatch(acc, data)
            break
        total += len(line)
        if total > MAX_RESPONSE_BYTES:
            raise UpstreamError(kind="too_large", message="upstream stream exceeds 32 MB")
        line = line.rstrip(b"\r\n")
        if not line:
            _dispatch(acc, data)
            data = []
        elif line.startswith(b"data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(b" ") else value)
        if cut_chars is not None and acc.finish_reason is None and acc.chars >= cut_chars:
            cut = CUT_MAX_OUTPUT
    if acc.error is not None:
        return {"error": acc.error.get("error"), "llmlab_stream_error": True}
    if cut == CUT_WALL_CLOCK and not acc.has_output:
        msg = f"no output within the {limits.wall_clock_s:.0f}s per-call wall clock"
        raise UpstreamError(kind="timeout", message=msg)
    if cut is None and acc.finish_reason is None and not acc.done:
        raise UpstreamError(kind="connect", message="upstream stream ended before the answer finished")
    return acc.completion(limits, cut, time.monotonic() - started)


def _dispatch(acc: ChatStream, data: list[bytes]) -> None:
    if not data:
        return
    text = b"\n".join(data).strip()
    if text == b"[DONE]":
        acc.done = True
        return
    try:
        acc.feed(json.loads(text))
    except ValueError:
        return
