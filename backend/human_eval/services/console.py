"""HTTP console (owner C): send a participant's request to their app's container and log it.

Spec §5.12 and R2. The console exists only when a security block of the design
enables ``http_console``. Safety rules:

- **SSRF**: the URL is always ``runtime.proxy._upstream_base(container)`` (scheme,
  host and port from the DB row of a RUNNING ``llm-*`` container) followed by the
  participant's path, which must start with ``/``. ``//evil.com`` or ``/@evil``
  therefore stay paths on the app's host; the composed URL's authority is checked
  again before sending. ``requests.Session`` with ``trust_env = False`` (no proxy
  env), no redirects followed.
- **Which container**: only the one the participant is shown running
  (``hosting.serving_container``): no request reaches a container whose per-assignment
  reset is still due or in flight, or that is being stopped. A sent request records the
  assignment as a user of that container state (the next rater gets a fresh copy).
- **Bounds**: method allowlist; path ≤ 2048 chars without whitespace/control
  characters or ``#``; ≤ 30 headers (RFC 7230 token names ≤ 64, values ≤ 4096
  without CR/LF); host, hop-by-hop and length headers dropped; body ≤ 256 KiB
  UTF-8; response read ≤ 512 KiB (``truncated``) within a 25 s wall clock
  (``timeout=(5, 20)`` per connect/read, ``stream=True``). The wall clock is a
  watchdog that shuts the request's socket down at the deadline, so an upstream that
  trickles headers or a body (each byte within the read timeout) cannot hold the worker.
- **Rate limits**: 30 requests per minute and 2 concurrent requests per participant
  (cache) → 429 ``rate_limited``; ≤ 1000 logged exchanges per assignment → 409
  ``quota_exceeded``.
- **Log**: every sent request becomes an :class:`~backend.human_eval.models.HttpExchange`
  (request/response body excerpts ≤ 16 KiB, response headers), errors included
  (``status_code`` None + error text). NUL characters (binary bodies, ``\\u0000`` probes)
  are stored, and returned, as U+FFFD: Postgres text and JSONB cannot hold them. Error
  texts are generic: they never echo the internal upstream address.
"""

from __future__ import annotations

import contextlib
import logging
import re
import socket
import threading
import time
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import urlsplit

import requests
from django.core.cache import cache
from django.utils import timezone
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection
from urllib3.connection import HTTPSConnection
from urllib3.poolmanager import PoolManager

from backend.human_eval.models import Assignment
from backend.human_eval.models import HttpExchange
from backend.human_eval.services import design as design_service
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.errors import conflict
from backend.human_eval.services.errors import invalid
from backend.runtime.proxy import _upstream_base

if TYPE_CHECKING:
    from backend.human_eval.models import EvalTaskApp

logger = logging.getLogger(__name__)

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
#: Never forwarded: the upstream host is ours, hop-by-hop headers are connection-level, lengths are recomputed.
DROPPED_HEADERS = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "expect",
    },
)
MAX_PATH = 2048
MAX_HEADERS = 30
MAX_HEADER_VALUE = 4096
MAX_BODY_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
EXCERPT_CHARS = 16 * 1024
MAX_RESPONSE_HEADERS = 100
WALL_CLOCK_SECONDS = 25.0
TIMEOUT = (5, 20)
CHUNK_BYTES = 8192

RATE_LIMIT = 30
RATE_WINDOW_SECONDS = 60
MAX_CONCURRENT = 2
CONCURRENCY_TTL_SECONDS = 90
QUOTA_PER_ASSIGNMENT = 1000

ERROR_TIMEOUT = "timeout"
ERROR_CONNECTION = "connection failed"
ERROR_INVALID = "invalid response"
ERROR_FAILED = "request failed"


def _has_bad_char(value: str) -> bool:
    return any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def _db_text(value: str) -> str:
    """``value`` storable in Postgres text/JSONB, which reject NUL: U+0000 becomes U+FFFD (like other bad bytes)."""
    return value.replace("\x00", "\ufffd")


def validate_request(
    method: Any,
    path: Any,
    headers: Any,
    body: Any,
) -> tuple[str, str, dict[str, str], bytes]:
    """Normalize and check a console request; raise 400 ``invalid`` with ``[{path, message}]`` errors.

    Returns ``(METHOD, path, headers to send, body bytes)``; dropped headers are removed silently.
    """
    errors: list[dict[str, str]] = []
    method_name = str(method or "").upper()
    if method_name not in METHODS:
        errors.append({"path": "method", "message": f"Method must be one of {', '.join(METHODS)}"})
    path_text = path if isinstance(path, str) else ""
    if not path_text.startswith("/"):
        errors.append({"path": "path", "message": 'The path must start with "/"'})
    elif len(path_text) > MAX_PATH:
        errors.append({"path": "path", "message": f"The path is longer than {MAX_PATH} characters"})
    elif _has_bad_char(path_text) or "#" in path_text:
        errors.append({"path": "path", "message": "The path may not contain spaces, control characters or #"})
    clean: dict[str, str] = {}
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        errors.append({"path": "headers", "message": "Headers must be an object"})
        headers = {}
    if len(headers) > MAX_HEADERS:
        errors.append({"path": "headers", "message": f"At most {MAX_HEADERS} headers"})
    for name, value in headers.items():
        key = str(name)
        if not HEADER_NAME_RE.match(key):
            errors.append({"path": f"headers.{key[:64]}", "message": "Invalid header name"})
            continue
        if not isinstance(value, str) or len(value) > MAX_HEADER_VALUE or any(ch in value for ch in "\r\n\0"):
            errors.append(
                {
                    "path": f"headers.{key}",
                    "message": f"Header values are text up to {MAX_HEADER_VALUE} characters without line breaks",
                },
            )
            continue
        if key.lower() in DROPPED_HEADERS:
            continue
        clean[key] = value
    body_text = body if isinstance(body, str) else ""
    if body is not None and not isinstance(body, str):
        errors.append({"path": "body", "message": "The body must be text"})
    body_bytes = body_text.encode("utf-8")
    if len(body_bytes) > MAX_BODY_BYTES:
        errors.append({"path": "body", "message": f"The body is larger than {MAX_BODY_BYTES // 1024} KiB"})
    if errors:
        msg = "The console request is not valid"
        raise invalid(msg, errors)
    return method_name, path_text, clean, body_bytes


def _slot_app(assignment: Assignment, slot: str) -> EvalTaskApp:
    from backend.human_eval.services import context

    for name, app in context.slot_apps(assignment):
        if name == slot and app is not None:
            return app
    msg = "The console request is not valid"
    raise invalid(msg, [{"path": "slot", "message": "This assignment has no app in that slot"}])


def _acquire_slot(participant_key: str) -> str | None:
    """Take one of the participant's concurrent-request slots: its cache key, "" if the cache is down, else None."""
    for index in range(MAX_CONCURRENT):
        key = f"human_eval:console:inflight:{participant_key}:{index}"
        try:
            if cache.add(key, 1, timeout=CONCURRENCY_TTL_SECONDS):
                return key
        except Exception:
            logger.exception("human_eval console concurrency cache unavailable")
            return ""
    return None


def _release_slot(key: str | None) -> None:
    if not key:
        return
    try:
        cache.delete(key)
    except Exception:
        logger.exception("human_eval console concurrency cache unavailable")


def _response_headers(raw: Any) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in list((raw or {}).items())[:MAX_RESPONSE_HEADERS]:
        headers[_db_text(str(name)[:256])] = _db_text(str(value)[:MAX_HEADER_VALUE])
    return headers


# ---------------------------------------------------------------------------
# Wall-clock watchdog
# ---------------------------------------------------------------------------


def _shutdown(sock: Any) -> None:
    # The plain socket call, also for an SSL socket: it wakes a recv blocked in another thread.
    with contextlib.suppress(OSError, TypeError, ValueError):
        socket.socket.shutdown(sock, socket.SHUT_RDWR)


class _Watchdog:
    """Shuts the request's sockets down once the wall-clock budget is spent.

    ``requests`` only applies its timeout to each socket read, and a read loop only sees the clock when a
    chunk arrives; an upstream that trickles bytes would hold the call (and the worker, DB connection
    and concurrency slot) indefinitely. Shutting the socket down wakes the blocked read in any phase
    (headers, sized, chunked or EOF-delimited body).
    """

    def __init__(self, seconds: float) -> None:
        self._sockets: list[Any] = []
        self._lock = threading.Lock()
        self._fired = threading.Event()
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    @property
    def fired(self) -> bool:
        return self._fired.is_set()

    def start(self) -> None:
        self._timer.start()

    def cancel(self) -> None:
        self._timer.cancel()

    def track(self, sock: Any) -> None:
        with self._lock:
            self._sockets.append(sock)
            fired = self._fired.is_set()
        if fired:
            _shutdown(sock)

    def _fire(self) -> None:
        with self._lock:
            self._fired.set()
            sockets = list(self._sockets)
        for sock in sockets:
            _shutdown(sock)


class _WatchedConnectionMixin:
    """urllib3 connection that hands its socket to the call's watchdog once connected."""

    def __init__(self, *args: Any, watchdog: _Watchdog | None = None, **kwargs: Any) -> None:
        self._watchdog = watchdog
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        super().connect()  # type: ignore[misc]
        sock = getattr(self, "sock", None)
        if self._watchdog is not None and sock is not None:
            self._watchdog.track(sock)


class _WatchedHTTPConnection(_WatchedConnectionMixin, HTTPConnection):
    pass


class _WatchedHTTPSConnection(_WatchedConnectionMixin, HTTPSConnection):
    pass


class _WatchedPoolManager(PoolManager):
    def __init__(self, *args: Any, watchdog: _Watchdog, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.watchdog = watchdog

    def _new_pool(self, scheme: str, host: str, port: int, request_context: dict[str, Any] | None = None) -> Any:
        pool = super()._new_pool(scheme, host, port, request_context)
        # Per pool instance (never in the pool key): the call's connections report to its watchdog.
        pool.ConnectionCls = _WatchedHTTPSConnection if scheme == "https" else _WatchedHTTPConnection
        pool.conn_kw = {**pool.conn_kw, "watchdog": self.watchdog}
        return pool


class _WatchedAdapter(HTTPAdapter):
    def __init__(self, watchdog: _Watchdog) -> None:
        self.watchdog = watchdog
        super().__init__(max_retries=0)

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any) -> None:
        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block
        self.poolmanager = _WatchedPoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            watchdog=self.watchdog,
            **pool_kwargs,
        )


def _transport_error(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout) or "timed out" in str(exc).lower():
        return ERROR_TIMEOUT
    if isinstance(exc, requests.ConnectionError):
        return ERROR_CONNECTION
    if isinstance(exc, (requests.exceptions.ContentDecodingError, requests.exceptions.ChunkedEncodingError)):
        return ERROR_INVALID
    return ERROR_FAILED


def _session(watchdog: _Watchdog) -> requests.Session:
    """A proxy-env-free session whose connections report their sockets to ``watchdog``."""
    session = requests.Session()
    session.trust_env = False
    adapter = _WatchedAdapter(watchdog)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def perform(base: str, method: str, path: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    """Send the request to ``base + path`` and read the bounded response (never raises).

    Returns ``{status_code, reason, headers, body, truncated, elapsed_ms, error}``. The whole call ends
    by ``WALL_CLOCK_SECONDS`` (watchdog): past it the error is ``timeout`` and a body read so far is
    returned ``truncated``. The body has NUL replaced (see :func:`_db_text`).
    """
    url = f"{base}{path}"
    target, origin = urlsplit(url), urlsplit(base)
    result: dict[str, Any] = {
        "status_code": None,
        "reason": "",
        "headers": {},
        "body": "",
        "truncated": False,
        "elapsed_ms": None,
        "error": "",
    }
    if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
        result["error"] = "invalid path"
        return result
    started = time.monotonic()
    chunks: list[bytes] = []
    size = 0
    watchdog = _Watchdog(WALL_CLOCK_SECONDS)
    session = _session(watchdog)
    response = None
    watchdog.start()
    try:
        response = session.request(
            method,
            url,
            data=body or None,
            headers=headers,
            allow_redirects=False,
            timeout=TIMEOUT,
            stream=True,
        )
        result["status_code"] = response.status_code
        result["reason"] = _db_text(response.reason or "")
        result["headers"] = _response_headers(response.headers)
        if method != "HEAD":
            for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                if watchdog.fired or time.monotonic() - started > WALL_CLOCK_SECONDS:
                    result["error"] = ERROR_TIMEOUT
                    result["truncated"] = True
                    break
                if not chunk:
                    continue
                room = MAX_RESPONSE_BYTES - size
                if len(chunk) > room:
                    chunks.append(chunk[:room])
                    size += room
                    result["truncated"] = True
                    break
                chunks.append(chunk)
                size += len(chunk)
    except requests.RequestException as exc:
        result["error"] = _transport_error(exc)
        if result["status_code"] is not None:
            result["truncated"] = True
    except Exception:
        if not watchdog.fired:
            logger.exception("human_eval console request failed unexpectedly")
        result["error"] = ERROR_FAILED
    finally:
        watchdog.cancel()
        if response is not None:
            response.close()
        session.close()
    if watchdog.fired:
        # The deadline cut the call short, whatever the read reported (EOF, broken connection or protocol
        # error): it is a timeout, and any response read so far is incomplete.
        result["error"] = ERROR_TIMEOUT
        result["truncated"] = result["status_code"] is not None
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    result["body"] = _db_text(b"".join(chunks).decode("utf-8", errors="replace"))
    return result


def send(
    assignment: Assignment,
    slot: str,
    method: str,
    path: str,
    headers: dict[str, str],
    body: str,
) -> dict[str, Any]:
    """Send one console request for the assignment's ``slot`` app; returns TS ``ConsoleResponse``.

    Errors: 409 ``read_only`` (assignment closed / task not accepting), 409 ``console_disabled``
    (no security block with ``http_console``), 400 ``invalid`` (slot, method, path, headers, body),
    409 ``quota_exceeded``, 429 ``rate_limited`` (30/min or 2 concurrent per participant),
    409 ``app_not_running`` (also while the participant is not shown the app running: its
    per-assignment reset is due or in flight, or it is stopping) / ``app_not_reachable``. Transport
    errors are logged and returned with ``status_code`` None.
    """
    from backend.human_eval.services import access
    from backend.human_eval.services import hosting

    access.ensure_writable(assignment)
    if not design_service.has_http_console(assignment.task.design or {}):
        msg = "The HTTP console is not enabled for this task"
        raise conflict(msg, "console_disabled")
    task_app = _slot_app(assignment, slot)
    method_name, path_text, clean_headers, body_bytes = validate_request(method, path, headers, body)
    if HttpExchange.objects.filter(assignment=assignment).count() >= QUOTA_PER_ASSIGNMENT:
        msg = f"This assignment reached the limit of {QUOTA_PER_ASSIGNMENT} console requests"
        raise conflict(msg, "quota_exceeded")
    participant_key = str(assignment.participant_id)
    if access.throttle_hit(f"console:{participant_key}", RATE_LIMIT, RATE_WINDOW_SECONDS):
        msg = f"Too many console requests (at most {RATE_LIMIT} per minute); wait a moment"
        raise HumanEvalServiceError(429, msg, "rate_limited")
    # The same gates as the participant's hosting state: never an earlier rater's un-reset state.
    container = hosting.serving_container(task_app, assignment)
    if container is None or not container.name.startswith(hosting.APP_PREFIX):
        msg = "The app is not running; start it first"
        raise conflict(msg, "app_not_running")
    base = _upstream_base(container)
    if not base:
        msg = "The server cannot reach this app"
        raise conflict(msg, "app_not_reachable")
    slot_key = _acquire_slot(participant_key)
    if slot_key is None:
        msg = f"At most {MAX_CONCURRENT} console requests at a time; wait for the others to finish"
        raise HumanEvalServiceError(429, msg, "rate_limited")
    try:
        result = perform(base, method_name, path_text, clean_headers, body_bytes)
    finally:
        _release_slot(slot_key)
    # The app received the raw body (NUL included); the log keeps a storable excerpt.
    body_excerpt = _db_text(body_bytes.decode("utf-8", errors="replace")[:EXCERPT_CHARS])
    exchange = HttpExchange.objects.create(
        assignment=assignment,
        task_app=task_app,
        method=method_name,
        path=path_text,
        request_headers=clean_headers,
        request_body=body_excerpt,
        status_code=result["status_code"],
        response_headers=result["headers"],
        response_body=result["body"][:EXCERPT_CHARS],
        response_truncated=bool(result["truncated"]),
        elapsed_ms=result["elapsed_ms"],
        error=result["error"],
    )
    now = timezone.now()
    Assignment.objects.filter(pk=assignment.pk).update(last_activity_at=now)
    # The request may have changed the app's state: this assignment is one of its users (after the
    # assignment row lock above: lock order assignment → container).
    hosting.note_served(assignment, slot, task_app, container)
    hosting.touch([task_app])
    return {
        "id": str(exchange.pk),
        "status_code": result["status_code"],
        "reason": result["reason"],
        "headers": result["headers"],
        "body": result["body"],
        "truncated": bool(result["truncated"]),
        "elapsed_ms": result["elapsed_ms"],
        "error": result["error"],
    }
