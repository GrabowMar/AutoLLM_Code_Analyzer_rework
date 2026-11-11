"""Internal LLM proxy for local agent runs (spec §D.3).

A stdlib ``ThreadingHTTPServer`` thread inside the executor, bound only to
``settings.LLM_PROXY_BIND`` (the executor's static address on the internal
``llmproxy`` network) after leadership is won. The ``llm-gateway-local``
overlay of every local agent job talks to it:

- ``POST /v1/chat/completions``, ``GET /v1/models``, ``GET /healthz``;
- auth: ``Bearer`` = ``gateway_wiring.proxy_token(run)``, re-checked against
  the ``GenerationRun``'s live state (RUNNING, ``phase=running``, same
  ``attempt``, lease not expired; cached for 5 s);
- the body becomes an ``LLMRequest`` (``source=agent_proxy``) built from the
  run's **frozen** endpoint (``RunManifest.manifest["model"]["endpoint"]``,
  see :func:`frozen_endpoint`); identical bodies of the same attempt re-attach
  to one request by ``dedupe_key``;
- non-stream waits are capped at ``chain.proxy_wait_s`` and answer 504
  (the overlay retries 504 and re-attaches); streamed calls get synthesized
  SSE with ``: ping`` keep-alives;
- every 503/507 returned is appended to ``execution.infra_errors``;
- the run budget is enforced here too, by the gateway's own ``budget_rules.py`` loaded from
  ``generation/images/llm-gateway/protocols/`` (local proxy parity, spec 6.6): the same pre-call
  reservation, request classes and caps as the run's gateway, as a backstop behind it (see
  :class:`ProxyBudgets`).

Every handler closes its DB connection when it finishes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
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
    """A ``GenerationRun`` attempt as the proxy sees it (``job_*`` names follow ``LLMRequest.job``)."""

    job_id: str
    attempt: int
    status: str
    phase: str
    lease_expires_at: datetime | None
    # The run's frozen endpoint section (``RunManifest.manifest["model"]["endpoint"]``, see
    # :func:`frozen_endpoint`); None without a manifest or for OpenRouter runs (they never use the proxy).
    endpoint: dict[str, Any] | None
    model_slug: str
    wall_clock_s: int
    priority: int
    endpoint_id: int | None
    checked: float
    # The run's ``caching`` policy (manifest ``resources.effective``): only the native Anthropic route reads it
    # (``off`` sends no cache breakpoints; the TTL is recorded when it is not 5 minutes).
    prompt_cache: str = "auto"
    cache_ttl: str = "5m"
    # The run's frozen ``reasoning.mode`` (manifest ``resources.effective``): an OpenCode Go call carries the
    # gateway's ``reasoning`` object upstream only when the policy forces an effort (C2).
    reasoning_mode: str = "provider_default"

    def live(self) -> bool:
        from backend.generation.models.choices import RunPhase
        from backend.generation.models.choices import RunStatus

        return (
            self.status == RunStatus.RUNNING
            and self.phase == RunPhase.RUNNING
            and self.lease_expires_at is not None
            and self.lease_expires_at > timezone.now()
            and bool(self.endpoint)
        )


def frozen_endpoint(manifest: dict[str, Any], model) -> dict[str, Any] | None:
    """The endpoint section the proxy builds upstream bodies from; None for OpenRouter or no manifest.

    The manifest's ``model.endpoint`` (id, kind, served name, digest) is authoritative. Keys
    the v3 manifest does not freeze yet (``num_ctx``, ``keep_alive``, ``api_flavor``,
    ``capabilities``) come from :func:`backend.inference.snapshot.endpoint_section` of the
    run's model, so a local run never silently falls back to the server's default context.
    """
    from backend.inference.models import LLMEndpoint
    from backend.inference.snapshot import endpoint_section

    section = ((manifest or {}).get("model") or {}).get("endpoint") or {}
    if not section.get("id") or section.get("kind") == LLMEndpoint.Kind.OPENROUTER:
        return None
    live = (endpoint_section(model) if model is not None else None) or {}
    return {**live, **{key: value for key, value in section.items() if value is not None}}


def manifest_wall_clock_s(manifest: dict[str, Any]) -> int:
    """``time.wall_clock_s`` of the run's effective resource policy, else :data:`DEFAULT_WALL_CLOCK_S`."""
    effective = ((manifest or {}).get("resources") or {}).get("effective") or {}
    content = effective.get("content") or {}
    candidates = (
        (content.get("time") or {}).get("wall_clock_s"),  # EffectivePolicy dump
        (effective.get("time") or {}).get("wall_clock_s"),  # bare ResourcePolicyContent
        effective.get("time.wall_clock_s"),  # flat knob map
    )
    for value in candidates:
        if value:
            return int(value)
    return DEFAULT_WALL_CLOCK_S


def _reasoning_mode(manifest: dict[str, Any]) -> str:
    """``reasoning.mode`` of the run's effective policy (``provider_default`` when absent)."""
    reasoning = _effective_content(manifest).get("reasoning")
    mode = reasoning.get("mode") if isinstance(reasoning, dict) else None
    return str(mode or "provider_default")


def _caching(manifest: dict[str, Any]) -> dict[str, str]:
    """``{prompt_cache, cache_ttl}`` of the run's effective ``caching`` policy (defaults: auto, 5m)."""
    caching = _effective_content(manifest).get("caching") or {}
    if not isinstance(caching, dict):
        caching = {}
    return {
        "prompt_cache": str(caching.get("prompt_cache") or "auto"),
        "cache_ttl": str(caching.get("ttl") or "5m"),
    }


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
        from backend.generation.models import GenerationRun
        from backend.generation.models import RunManifest

        run = GenerationRun.objects.filter(pk=job_id).select_related("manifest", "model__endpoint").first()
        if run is None or int(run.attempt or 0) != attempt:
            with self._lock:
                self._jobs.pop(key, None)
            return None
        try:
            manifest = run.manifest.manifest if isinstance(run.manifest.manifest, dict) else {}
        except RunManifest.DoesNotExist:
            manifest = {}
        auth = JobAuth(
            job_id=job_id,
            attempt=attempt,
            status=run.status,
            phase=run.phase,
            lease_expires_at=run.lease_expires_at,
            endpoint=frozen_endpoint(manifest, run.model),
            model_slug=str((manifest.get("model") or {}).get("slug") or ""),
            wall_clock_s=manifest_wall_clock_s(manifest),
            priority=int(run.priority or 0),
            endpoint_id=run.endpoint_id,
            checked=now,
            reasoning_mode=_reasoning_mode(manifest),
            **_caching(manifest),
        )
        with self._lock:
            self._jobs[key] = auth
            if len(self._jobs) > 4096:
                self._jobs.clear()
        return auth


_auth_cache = AuthCache()


def record_infra_error(job_id: str, attempt: int, code: str, status: int) -> None:
    """Append ``{at, code, status}`` to ``execution.infra_errors`` of a running attempt (guarded jsonb UPDATE)."""
    from backend.generation.models import GenerationRun
    from backend.generation.models.choices import RunStatus

    entry = json.dumps([{"at": timezone.now().isoformat(), "code": code, "status": status}])
    table = GenerationRun._meta.db_table  # noqa: SLF001
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
            [entry, job_id, attempt, RunStatus.RUNNING],
        )


def _error(code: str, message: str, status: int) -> dict[str, Any]:
    return {"error": {"message": message, "code": code, "type": code, "status": status}}


def upstream_request(auth: JobAuth, body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate the gateway's OpenAI body into the upstream ``(path, body)`` using the frozen endpoint."""
    from backend.inference.access import access_from_snapshot
    from backend.inference.anthropic_native import Options
    from backend.inference.chat import build_upstream_body
    from backend.inference.models import LLMEndpoint
    from backend.inference.upstream import is_opencode

    section = auth.endpoint
    endpoint = LLMEndpoint.objects.get(pk=section["id"])
    access = access_from_snapshot(section, endpoint)
    clean = {k: v for k, v in body.items() if k not in DROPPED_KEYS}
    params = {k: v for k, v in clean.items() if k not in _NON_PARAM_KEYS}
    seed = params.pop("seed", None)
    if is_opencode(access.base_url) and auth.reasoning_mode != "effort":
        # Only the policy's forced effort reaches OpenCode Go (as ``reasoning_effort``, C2): a harness's own
        # ``reasoning`` object under ``provider_default`` is dropped, as it always was for this endpoint.
        params.pop("reasoning", None)
    path, upstream_body = build_upstream_body(
        access,
        clean.get("messages") or [],
        params,
        seed,
        tools=clean.get("tools"),
        # Read by the native Anthropic builder only; the other flavors never send them.
        tool_choice=clean.get("tool_choice"),
        parallel_tool_calls=clean.get("parallel_tool_calls"),
        anthropic_options=Options(prompt_cache=auth.prompt_cache, cache_ttl=auth.cache_ttl),
    )
    return path, upstream_body


# =====================================================================================
# Run budget backstop (WP-06): the gateway's budget rules, one source file for both
# =====================================================================================

BUDGET_RULES_PATH = (
    Path(__file__).resolve().parents[1] / "generation" / "images" / "llm-gateway" / "protocols" / "budget_rules.py"
)
_BUDGET_RULES_MODULE = "llmlab_gateway_budget_rules"
_budget_rules_lock = threading.Lock()
# Headers the llm-gateway-local overlay sends with every call.
REQUEST_CLASS_HEADER = "X-LLMLab-Request-Class"
CALL_INDEX_HEADER = "X-LLMLab-Call-Index"
MAX_TRACKED_ATTEMPTS = 1024


def budget_rules() -> ModuleType:
    """The gateway image's ``protocols/budget_rules.py``, loaded once by path (it is stdlib-only)."""
    module = sys.modules.get(_BUDGET_RULES_MODULE)
    if module is not None:
        return module
    with _budget_rules_lock:
        module = sys.modules.get(_BUDGET_RULES_MODULE)
        if module is None:
            # Compiled from source, never cached: no __pycache__ appears in the image build context.
            module = ModuleType(_BUDGET_RULES_MODULE)
            module.__file__ = str(BUDGET_RULES_PATH)
            sys.modules[_BUDGET_RULES_MODULE] = module
            try:
                code = compile(BUDGET_RULES_PATH.read_text(encoding="utf-8"), str(BUDGET_RULES_PATH), "exec")
                exec(code, module.__dict__)  # noqa: S102 - the repo's own stdlib-only rules file
            except BaseException:
                sys.modules.pop(_BUDGET_RULES_MODULE, None)
                raise
    return module


def _effective_content(manifest: dict[str, Any]) -> dict[str, Any]:
    """The effective ``ResourcePolicyContent`` from ``manifest.resources.effective`` (an
    ``EffectivePolicy`` dump under ``content``, or a bare content dict)."""
    effective = ((manifest or {}).get("resources") or {}).get("effective") or {}
    content = effective.get("content")
    return content if isinstance(content, dict) else effective


def budget_config_from_manifest(manifest: dict[str, Any]) -> dict[str, Any] | None:
    """The ``GatewayConfig``-shaped budget sections (``budget``, ``call``, ``pricing``, ``reasoning``)
    derived from the frozen manifest, or None when the manifest carries no run caps."""
    content = _effective_content(manifest)
    run = content.get("run") or {}
    call = content.get("call") or {}
    if not run.get("max_llm_requests") or not run.get("max_total_tokens") or not call.get("max_output_tokens"):
        return None
    model = (manifest or {}).get("model") or {}
    endpoint = model.get("endpoint") or {}
    retries = content.get("retries") or {}
    return {
        "budget": {
            "max_cost_usd": float(run.get("max_cost_usd") or 0.0),
            "cost_basis": str(endpoint.get("cost_basis") or "free"),
            "max_total_tokens": int(run["max_total_tokens"]),
            "max_uncached_input_tokens": run.get("max_uncached_input_tokens"),
            "max_output_tokens": run.get("max_output_tokens"),
            "max_llm_requests": int(run["max_llm_requests"]),
            "max_aux_requests": int(run.get("max_aux_requests") or 0),
            "count_failed_calls": bool(retries.get("count_failed_calls")),
        },
        "call": {
            "max_output_tokens": int(call["max_output_tokens"]),
            "max_input_tokens": call.get("max_input_tokens"),
        },
        "pricing": dict(model.get("pricing") or {}),
        "reasoning": dict(content.get("reasoning") or {"mode": "provider_default"}),
        "model": {},
    }


def _run_budget_config(job_id: str) -> dict[str, Any] | None:
    """The run's gateway budget: WP-11's ``gateway_config.build`` when available, else the manifest."""
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunManifest

    run = GenerationRun.objects.filter(pk=job_id).select_related("manifest").first()
    if run is None:
        return None
    try:
        from backend.generation.budget.gateway_config import build

        return build(run).model_dump(mode="json")
    except NotImplementedError:
        pass
    except Exception:
        logger.exception("llm-proxy: gateway_config.build failed for run %s; using the manifest", job_id)
    try:
        manifest = run.manifest.manifest if isinstance(run.manifest.manifest, dict) else {}
    except RunManifest.DoesNotExist:
        return None
    return budget_config_from_manifest(manifest)


@dataclass
class _AttemptBudget:
    ledger: Any  # budget_rules.Ledger
    config: dict[str, Any]
    pending: dict[str, Any]  # call key -> budget_rules.Reservation


class ProxyBudgets:
    """Per-attempt budget ledgers of local agent runs, with the gateway's exact rules.

    A call is keyed by the overlay's ``X-LLMLab-Call-Index`` (else by its dedupe key), so a gateway
    retry or a 504 re-attach of the same call reuses its reservation instead of reserving twice. The
    ledgers live in the leader executor's memory: the run's gateway stays authoritative; this is the
    backstop that makes the proxy refuse at the same point.
    """

    def __init__(self, config_loader=_run_budget_config) -> None:
        self._lock = threading.Lock()
        self._attempts: dict[tuple[str, int], _AttemptBudget | None] = {}
        self._load = config_loader

    def clear(self) -> None:
        with self._lock:
            self._attempts.clear()

    def for_attempt(self, job_id: str, attempt: int) -> _AttemptBudget | None:
        key = (job_id, attempt)
        with self._lock:
            if key in self._attempts:
                return self._attempts[key]
        config = self._load(job_id)
        rules = budget_rules()
        entry = None
        if config is not None:
            entry = _AttemptBudget(rules.Ledger(rules.BudgetLimits.from_config(config)), config, {})
        else:
            logger.warning("llm-proxy: run %s has no budget caps in its manifest; the gateway alone enforces", job_id)
        with self._lock:
            if len(self._attempts) >= MAX_TRACKED_ATTEMPTS:
                self._attempts.clear()
            return self._attempts.setdefault(key, entry)

    def admit(self, job_id: str, attempt: int, body: dict[str, Any], headers, dedupe: str):
        """``(call_key, refusal)``: the call's key once admitted, or the ``budget_rules.Refusal``."""
        entry = self.for_attempt(job_id, attempt)
        if entry is None:
            return None, None
        rules = budget_rules()
        index = headers.get(CALL_INDEX_HEADER)
        call_key = f"i:{index}" if index else f"d:{dedupe}"
        with self._lock:
            if call_key in entry.pending:
                return call_key, None
        request_class, _raw = rules.request_class(REQUEST_CLASS_HEADER, headers)
        est = rules.estimate_tokens(rules.prompt_text(body))
        max_tokens = int(body.get("max_tokens") or rules.upstream_max_tokens(entry.config))
        try:
            reservation = entry.ledger.reserve(request_class, est, max_tokens)
        except rules.Refusal as refusal:
            return None, refusal
        with self._lock:
            entry.pending[call_key] = reservation
        return call_key, None

    def settle(self, job_id: str, attempt: int, call_key: str | None, status: int, payload: Any) -> None:
        if call_key is None:
            return
        with self._lock:
            entry = self._attempts.get((job_id, attempt))
            reservation = entry.pending.pop(call_key, None) if entry is not None else None
        if entry is None or reservation is None:
            return
        rules = budget_rules()
        ok = status < 400
        usage = rules.chat_usage(payload.get("usage")) if ok and isinstance(payload, dict) else None
        choices = (payload or {}).get("choices") if isinstance(payload, dict) else None
        finish = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None
        entry.ledger.settle(reservation, usage, ok=ok, finish_reason=finish)

    def state(self, job_id: str, attempt: int) -> dict[str, Any] | None:
        with self._lock:
            entry = self._attempts.get((job_id, attempt))
        return entry.ledger.snapshot() if entry is not None else None


_budgets = ProxyBudgets()


def record_ledger_usage(row) -> None:
    """Book a completed proxied request in the spend ledger (WP-11 ``budget.live.record_proxy_usage``):
    tokens count against the run's token caps and SpendCaps live. Only DONE rows with a non-error
    answer; best effort (the ledger dedupes ``proxy:{id}``, so a re-attached row is booked once)."""
    if row is None or getattr(row, "job_id", None) is None:
        return
    if row.status != row.Status.DONE or int(row.response_status or 200) >= 400:
        return
    try:
        from backend.generation.budget.live import record_proxy_usage

        record_proxy_usage(row)
    except Exception:  # never fail the agent's call over the ledger
        logger.exception("proxy: ledger usage for request %s failed", getattr(row, "pk", None))


def _budget_refusal_payload(refusal) -> tuple[int, dict[str, Any]]:
    """The refusal in the OpenAI shape the gateway's ``openai_chat`` front uses."""
    if refusal.exhausted:
        return 400, _error("budget_exhausted", refusal.message, 400)
    return 400, _error("context_length_exceeded", refusal.message, 400)


def _transient_exhausted(row) -> bool:
    """The row's answer is OpenCode Go's transient error after the pump's retries (``pump.RETRIES_KEY``)."""
    from backend.inference.pump import RETRIES_KEY

    response = row.response if isinstance(row.response, dict) else {}
    info = response.get(RETRIES_KEY)
    return isinstance(info, dict) and bool(info.get("exhausted"))


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
            # against that run's own wall clock.
            job_id, attempt = read_proxy_token(token.strip(), max_age=None)
        except signing.BadSignature:
            return None
        auth = _auth_cache.get(job_id, attempt)
        if auth is None or not auth.live():
            return None
        try:
            read_proxy_token(token.strip(), max_age=auth.wall_clock_s + TOKEN_MARGIN_S)
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
        self._serve(auth, body, raw)

    def _serve(self, auth: JobAuth, body: dict[str, Any], raw: bytes) -> None:
        """Admit the call against the run budget, enqueue or re-attach it, answer, then settle it."""
        dedupe = hashlib.sha256(f"{auth.job_id}|{auth.attempt}|".encode() + raw).hexdigest()
        call_key, refusal = _budgets.admit(auth.job_id, auth.attempt, body, self.headers, dedupe)
        if refusal is not None:
            self._send_json(*_budget_refusal_payload(refusal))
            return
        settled = False
        try:
            rid = self._attach_or_enqueue(auth, body, raw)
            if rid is None:
                return
            if body.get("stream"):
                status, payload = self._stream(auth, rid)
            else:
                status, payload = self._non_stream(auth, rid)
            if status == 504:  # still waiting: the gateway re-attaches with the same call index
                settled = True
            else:
                _budgets.settle(auth.job_id, auth.attempt, call_key, status, payload)
                settled = True
        finally:
            if not settled:
                # No answer reached the gateway (an error or a lost client): a failed call, as the
                # gateway counts it. A 504 keeps its reservation for the re-attach.
                _budgets.settle(auth.job_id, auth.attempt, call_key, 502, None)

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
        section = auth.endpoint
        try:
            path, upstream_body = upstream_request(auth, body)
        except LLMAccessError as exc:
            self._send_json(400, _error("bad_request", str(exc), 400))
            return None
        except LLMEndpoint.DoesNotExist:
            self._send_json(503, _error("endpoint_unavailable", "the run's endpoint no longer exists", 503))
            record_infra_error(auth.job_id, auth.attempt, "endpoint_unavailable", 503)
            return None
        endpoint = LLMEndpoint.objects.get(pk=section["id"])
        from backend.generation.models import GenerationRun

        job = GenerationRun(pk=auth.job_id, attempt=auth.attempt)
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
            from backend.generation.models.choices import RunStatus

            return fresh is None or fresh.status == RunStatus.CANCELLED

        return check

    def _outcome(self, auth: JobAuth, row) -> tuple[int, dict[str, Any], str | None]:
        """``(status, payload, infra_code)`` for a finished request."""
        from backend.inference.access import LLMAccessError
        from backend.inference.access import access_from_snapshot
        from backend.inference.chat import add_queue_wait
        from backend.inference.chat import raise_for_request
        from backend.inference.chat import translate

        if row.first_delivery:
            from backend.generation.models import GenerationRun

            add_queue_wait(GenerationRun(pk=auth.job_id), row)
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
            if _transient_exhausted(row):
                # C1: still failing after the pump's retries; 503 lets the gateway's own retry re-submit it.
                return 503, _error("upstream_transient", str(exc), 503), "upstream_transient"
            return 503, _error("endpoint_unavailable", str(exc), 503), "endpoint_unavailable"
        section = auth.endpoint
        access = access_from_snapshot(section, row.endpoint)
        return 200, translate(row, access), None

    def _non_stream(self, auth: JobAuth, rid) -> tuple[int, dict[str, Any] | None]:
        from backend.inference.requests import WaitTimeout
        from backend.inference.requests import wait_result
        from backend.inference.timeouts import chain

        endpoint_id = auth.endpoint["id"]
        from backend.inference.models import LLMEndpoint

        endpoint = LLMEndpoint.objects.filter(pk=endpoint_id).first()
        try:
            row = wait_result(rid, cancel_check=self._cancel_check(auth), max_wait=chain(endpoint).proxy_wait_s)
        except WaitTimeout:
            self._send_json(504, _error("proxy_wait", "still waiting for the endpoint; retry to re-attach", 504))
            return 504, None
        record_ledger_usage(row)
        status, payload, infra = self._outcome(auth, row)
        if infra:
            record_infra_error(auth.job_id, auth.attempt, infra, status)
        self._send_json(status, payload)
        return status, payload

    def _write_chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _stream(self, auth: JobAuth, rid) -> tuple[int, dict[str, Any] | None]:
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
        record_ledger_usage(row)
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
        return status, payload


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
