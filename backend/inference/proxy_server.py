"""Internal LLM proxy for local agent runs (spec §D.3).

A stdlib ``ThreadingHTTPServer`` thread inside the executor, bound only to
``settings.LLM_PROXY_BIND`` (the executor's static address on the internal
``llmproxy`` network) after leadership is won. The ``llm-gateway-local``
overlay of every local agent job talks to it:

- ``POST /v1/chat/completions``, ``GET /v1/models``, ``GET /healthz``;
- auth: ``Bearer`` = ``gateway_wiring.proxy_token(job)``, re-checked against
  the job's live state (RUNNING, ``phase=running``, same ``attempt``, lease
  not expired; cached for 5 s);
- the body becomes an ``LLMRequest`` (``source=agent_proxy``) built from the
  job's **frozen** ``snapshot["endpoint"]``; identical bodies of the same
  attempt re-attach to one request by ``dedupe_key``;
- non-stream waits are capped at ``chain.proxy_wait_s`` and answer 504
  (the overlay retries 504 and re-attaches); streamed calls get synthesized
  SSE with ``: ping`` keep-alives;
- every 503/507 returned is appended to ``execution.infra_errors``.

Every handler closes its DB connection when it finishes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core import signing
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from backend.inference.db import close_db

logger = logging.getLogger(__name__)

AUTH_CACHE_S = 5.0
PING_INTERVAL_S = 15.0
# Proxy tokens live for the agent's wall clock plus this margin.
TOKEN_MARGIN_S = 1800
DEFAULT_WALL_CLOCK_S = 3600
# Body keys never forwarded upstream (OpenRouter-only or routing fields).
DROPPED_KEYS = ("usage", "stream_options", "provider", "route", "transforms", "models")
# Keys handled separately from sampling params.
_NON_PARAM_KEYS = ("model", "messages", "tools", "tool_choice", "stream", "parallel_tool_calls", "user", "n")


@dataclass
class JobAuth:
    job_id: str
    attempt: int
    status: str
    phase: str
    lease_expires_at: datetime | None
    snapshot: dict[str, Any]
    priority: int
    endpoint_id: int | None
    checked: float

    @property
    def model_slug(self) -> str:
        return str(((self.snapshot or {}).get("llm") or {}).get("model_slug") or "")

    def live(self) -> bool:
        from backend.generation.models import GenerationJob

        return (
            self.status == GenerationJob.Status.RUNNING
            and self.phase == GenerationJob.Phase.RUNNING
            and self.lease_expires_at is not None
            and self.lease_expires_at > timezone.now()
            and bool((self.snapshot or {}).get("endpoint"))
        )


class AuthCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[tuple[str, int], JobAuth] = {}

    def get(self, job_id: str, attempt: int, *, fresh: bool = False) -> JobAuth | None:
        key = (job_id, attempt)
        now = time.monotonic()
        with self._lock:
            cached = self._jobs.get(key)
        if cached is not None and not fresh and now - cached.checked < AUTH_CACHE_S:
            return cached
        from backend.generation.models import GenerationJob

        row = (
            GenerationJob.objects.filter(pk=job_id)
            .values("status", "phase", "attempt", "lease_expires_at", "resolved_bundle", "priority", "endpoint_id")
            .first()
        )
        if row is None or int(row["attempt"] or 0) != attempt:
            with self._lock:
                self._jobs.pop(key, None)
            return None
        auth = JobAuth(
            job_id=job_id,
            attempt=attempt,
            status=row["status"],
            phase=row["phase"],
            lease_expires_at=row["lease_expires_at"],
            snapshot=row["resolved_bundle"] if isinstance(row["resolved_bundle"], dict) else {},
            priority=int(row["priority"] or 0),
            endpoint_id=row["endpoint_id"],
            checked=now,
        )
        with self._lock:
            self._jobs[key] = auth
            if len(self._jobs) > 4096:
                self._jobs.clear()
        return auth


_auth_cache = AuthCache()


def record_infra_error(job_id: str, attempt: int, code: str, status: int) -> None:
    """Append ``{at, code, status}`` to ``execution.infra_errors`` of a running attempt (guarded jsonb UPDATE)."""
    from backend.generation.models import GenerationJob

    entry = json.dumps([{"at": timezone.now().isoformat(), "code": code, "status": status}])
    table = GenerationJob._meta.db_table  # noqa: SLF001
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {table}
               SET execution = jsonb_set(
                       COALESCE(execution, '{{}}'::jsonb),
                       '{{infra_errors}}',
                       COALESCE(execution->'infra_errors', '[]'::jsonb) || %s::jsonb
                   )
             WHERE id = %s AND attempt = %s AND status = %s
            """,  # noqa: S608 — table name from model meta, values parameterised
            [entry, job_id, attempt, GenerationJob.Status.RUNNING],
        )


def _error(code: str, message: str, status: int) -> dict[str, Any]:
    return {"error": {"message": message, "code": code, "type": code, "status": status}}


def upstream_request(auth: JobAuth, body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate the gateway's OpenAI body into the upstream ``(path, body)`` using the frozen snapshot."""
    from backend.inference.access import access_from_snapshot
    from backend.inference.chat import build_upstream_body
    from backend.inference.models import LLMEndpoint

    section = auth.snapshot["endpoint"]
    endpoint = LLMEndpoint.objects.get(pk=section["id"])
    access = access_from_snapshot(section, endpoint)
    clean = {k: v for k, v in body.items() if k not in DROPPED_KEYS}
    params = {k: v for k, v in clean.items() if k not in _NON_PARAM_KEYS}
    seed = params.pop("seed", None)
    path, upstream_body = build_upstream_body(
        access,
        clean.get("messages") or [],
        params,
        seed,
        tools=clean.get("tools"),
    )
    return path, upstream_body


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "llmlab-llm-proxy"

    def log_message(self, fmt: str, *args) -> None:
        logger.debug("llm-proxy: " + fmt, *args)  # noqa: G003

    # -- helpers ---------------------------------------------------------

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authenticate(self) -> JobAuth | None:
        header = self.headers.get("Authorization") or ""
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        from backend.inference.gateway_wiring import read_proxy_token

        try:
            # Unsign without age first to learn the job, then check the age
            # against that job's own wall clock.
            job_id, attempt = read_proxy_token(token.strip(), max_age=None)
        except signing.BadSignature:
            return None
        auth = _auth_cache.get(job_id, attempt)
        if auth is None or not auth.live():
            return None
        wall = int(((auth.snapshot.get("agent") or {}).get("wall_clock_seconds")) or DEFAULT_WALL_CLOCK_S)
        try:
            read_proxy_token(token.strip(), max_age=wall + TOKEN_MARGIN_S)
        except signing.BadSignature:
            return None
        return auth

    def _deny(self) -> None:
        self._send_json(401, _error("proxy_auth", "invalid or expired proxy token", 401))

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:
        try:
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._send_json(200, {"ok": True})
                return
            if path.rstrip("/") == "/v1/models":
                auth = self._authenticate()
                if auth is None:
                    self._deny()
                    return
                self._send_json(200, {"object": "list", "data": [{"id": auth.model_slug, "object": "model"}]})
                return
            self._send_json(404, _error("not_found", "not found", 404))
        finally:
            close_db()

    def do_POST(self) -> None:
        try:
            self._post()
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("llm-proxy client went away")
        except Exception:
            logger.exception("llm-proxy request failed")
            with contextlib.suppress(OSError):
                self._send_json(500, _error("proxy_error", "internal proxy error", 500))
        finally:
            close_db()

    def _post(self) -> None:
        path = urlsplit(self.path).path
        if path.rstrip("/") != "/v1/chat/completions":
            self._send_json(404, _error("not_found", "not found", 404))
            return
        auth = self._authenticate()
        if auth is None:
            self._deny()
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            self._send_json(400, _error("bad_request", "request body is not JSON", 400))
            return
        if not isinstance(body, dict):
            self._send_json(400, _error("bad_request", "request body must be an object", 400))
            return
        rid = self._attach_or_enqueue(auth, body, raw)
        if rid is None:
            return
        if body.get("stream"):
            self._stream(auth, rid)
        else:
            self._non_stream(auth, rid)

    def _read_body(self) -> bytes | None:
        """The request body, or None after answering 400 (bad length) / 413 (over the cap)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            self._send_json(400, _error("bad_request", "a valid Content-Length is required", 400))
            return None
        if length > int(settings.LLM_PROXY_MAX_BODY_BYTES):
            self.close_connection = True
            self._send_json(413, _error("too_large", "request body too large", 413))
            return None
        return self.rfile.read(length) if length else b""

    def _attach_or_enqueue(self, auth: JobAuth, body: dict[str, Any], raw: bytes):
        from backend.inference.access import LLMAccessError
        from backend.inference.access import access_from_snapshot
        from backend.inference.models import LLMEndpoint
        from backend.inference.models import LLMRequest
        from backend.inference.requests import enqueue

        dedupe = hashlib.sha256(f"{auth.job_id}|{auth.attempt}|".encode() + raw).hexdigest()
        # Re-attach to a pending or answered request; a 5xx answer is not
        # re-served, so the gateway's retry of a 503 really retries upstream.
        reusable = Q(status__in=[LLMRequest.Status.QUEUED, LLMRequest.Status.LEASED]) | Q(
            Q(response_status__isnull=True) | Q(response_status__lt=500),
            status=LLMRequest.Status.DONE,
        )
        existing = (
            LLMRequest.objects.filter(reusable, job_id=auth.job_id, job_attempt=auth.attempt, dedupe_key=dedupe)
            .order_by("-created_at")
            .values_list("pk", flat=True)
            .first()
        )
        if existing is not None:
            return existing
        section = auth.snapshot["endpoint"]
        try:
            path, upstream_body = upstream_request(auth, body)
        except LLMAccessError as exc:
            self._send_json(400, _error("bad_request", str(exc), 400))
            return None
        except LLMEndpoint.DoesNotExist:
            self._send_json(503, _error("endpoint_unavailable", "the job's endpoint no longer exists", 503))
            record_infra_error(auth.job_id, auth.attempt, "endpoint_unavailable", 503)
            return None
        endpoint = LLMEndpoint.objects.get(pk=section["id"])
        from backend.generation.models import GenerationJob

        job = GenerationJob(pk=auth.job_id, attempt=auth.attempt)
        return enqueue(
            access_from_snapshot(section, endpoint),
            path,
            upstream_body,
            job=job,
            source=LLMRequest.Source.AGENT_PROXY,
            priority=auth.priority,
            digest=str(section.get("digest") or ""),
            dedupe_key=dedupe,
        )

    def _cancel_check(self, auth: JobAuth):
        def check() -> bool:
            fresh = _auth_cache.get(auth.job_id, auth.attempt)
            from backend.generation.models import GenerationJob

            return fresh is None or fresh.status == GenerationJob.Status.CANCELLED

        return check

    def _outcome(self, auth: JobAuth, row) -> tuple[int, dict[str, Any], str | None]:
        """``(status, payload, infra_code)`` for a finished request."""
        from backend.inference.access import LLMAccessError
        from backend.inference.access import access_from_snapshot
        from backend.inference.chat import add_queue_wait
        from backend.inference.chat import raise_for_request
        from backend.inference.chat import translate

        if row.first_delivery:
            from backend.generation.models import GenerationJob

            add_queue_wait(GenerationJob(pk=auth.job_id), row)
        try:
            if row.status != row.Status.DONE or int(row.response_status or 200) >= 400:
                raise_for_request(row)
        except LLMAccessError as exc:
            if exc.code == "cancelled":
                return 400, _error("cancelled", "the run was cancelled", 400), None
            if exc.code == "upstream_oom":
                return 507, _error("upstream_oom", str(exc), 507), "upstream_oom"
            if exc.code == "upstream_error" and exc.status_code and exc.status_code < 500:
                return exc.status_code, _error("upstream_error", str(exc), exc.status_code), None
            return 503, _error("endpoint_unavailable", str(exc), 503), "endpoint_unavailable"
        section = auth.snapshot["endpoint"]
        access = access_from_snapshot(section, row.endpoint)
        return 200, translate(row, access), None

    def _non_stream(self, auth: JobAuth, rid) -> None:
        from backend.inference.requests import WaitTimeout
        from backend.inference.requests import wait_result
        from backend.inference.timeouts import chain

        endpoint_id = auth.snapshot["endpoint"]["id"]
        from backend.inference.models import LLMEndpoint

        endpoint = LLMEndpoint.objects.filter(pk=endpoint_id).first()
        try:
            row = wait_result(rid, cancel_check=self._cancel_check(auth), max_wait=chain(endpoint).proxy_wait_s)
        except WaitTimeout:
            self._send_json(504, _error("proxy_wait", "still waiting for the endpoint; retry to re-attach", 504))
            return
        status, payload, infra = self._outcome(auth, row)
        if infra:
            record_infra_error(auth.job_id, auth.attempt, infra, status)
        self._send_json(status, payload)

    def _write_chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _stream(self, auth: JobAuth, rid) -> None:
        from backend.inference.ollama_native import stream_chunks
        from backend.inference.requests import WaitTimeout
        from backend.inference.requests import wait_result

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.flush()
        check = self._cancel_check(auth)
        while True:
            try:
                row = wait_result(rid, cancel_check=check, max_wait=PING_INTERVAL_S)
                break
            except WaitTimeout:
                self._write_chunk(b": ping\n\n")
        status, payload, infra = self._outcome(auth, row)
        if infra:
            record_infra_error(auth.job_id, auth.attempt, infra, status)
        if status == 200:
            for chunk in stream_chunks(payload):
                self._write_chunk(f"data: {json.dumps(chunk)}\n\n".encode())
        else:
            self._write_chunk(f"data: {json.dumps(payload)}\n\n".encode())
        self._write_chunk(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(bind: str | None = None) -> ProxyServer:
    """A proxy server bound to ``bind`` (``host:port``; default ``settings.LLM_PROXY_BIND``)."""
    host, _, port = (bind or settings.LLM_PROXY_BIND).rpartition(":")
    return ProxyServer((host, int(port)), ProxyHandler)


def serve(stop: threading.Event, *, bind: str | None = None) -> None:
    """Serve until ``stop`` is set; then stop accepting and return (every handler closes its DB connection)."""
    server = make_server(bind)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, name="llm-proxy")
    thread.daemon = True
    thread.start()
    logger.info("LLM proxy listening on %s:%s", *server.server_address[:2])
    try:
        stop.wait()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
