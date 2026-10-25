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

import http.client
import json
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
    return f"{base_path}{path}"


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
) -> UpstreamResponse:
    """One request to a direct endpoint; raises :class:`UpstreamError` (``connect`` | ``timeout`` | ``http`` | ...).

    ``on_socket(sock)`` is called with the live socket once connected (and
    with ``None`` when done), so a canceller can ``shutdown()`` it.
    """
    if not path_allowed(method, path, body):
        raise UpstreamError(kind="forbidden_path", message=f"{method} {path} is not allowed")
    try:
        target = validate_direct_base_url(base_url)
    except ValueError as exc:
        raise UpstreamError(kind="connect", message=str(exc)) from exc
    started = time.monotonic()
    conn = _connect(target, timeout)
    if on_socket is not None:
        on_socket(conn.sock)
    try:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Host": target.host_header, "Accept": "application/json", "User-Agent": "llmlab-executor"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        conn.request(method.upper(), _join(target.base_path, path), body=data, headers=headers)
        resp = conn.getresponse()
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
