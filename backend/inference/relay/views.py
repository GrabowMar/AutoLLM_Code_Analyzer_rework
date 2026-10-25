"""Relay protocol v1 (spec §E): plain async Django views under ``/api/relay/v1/``.

The relay on a GPU box dials out over HTTPS, authenticates with an
endpoint-scoped token (``Authorization: Bearer llr_…``) and long-polls for
work. Rules for every view here:

- ``@csrf_exempt`` and ``@transaction.non_atomic_requests`` (``ATOMIC_REQUESTS``
  is on; a transaction is never held across an ``await``);
- database work runs in short ``sync_to_async`` steps that close their
  connection at the end, so a waiting poll holds no Postgres connection;
- responses are never compressed (Caddy compresses the internet leg; the
  SvelteKit proxy must not forward a stale ``Content-Encoding``);
- request bodies are capped at ``RELAY_MAX_BODY_BYTES`` on the wire and 32 MB
  after gunzip, read manually (bypassing ``DATA_UPLOAD_MAX_MEMORY_SIZE``);
- per-token rate limit ``RELAY_RATE_LIMIT_PER_S`` (429, fails open without
  Redis).
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import ipaddress
import json
import logging
import re
import time
import uuid
import zlib
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from backend.inference import doorbell
from backend.inference import requests as llm_requests
from backend.inference import tokens as relay_tokens
from backend.inference.db import close_db
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.inference.models import RelaySession
from backend.inference.models import RelayToken
from backend.inference.relay.redis import get_async_redis

logger = logging.getLogger(__name__)

DECOMPRESSED_LIMIT = 32 * 1024 * 1024
SESSION_TOUCH_S = 10
INVENTORY_INTERVAL_S = 60
DOORBELL_SLICE_S = 5
SESSION_HEADER = "HTTP_X_RELAY_SESSION"
RELAY_DIR = Path(settings.BASE_DIR) / "relay"
_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


class RelayHTTPError(Exception):
    def __init__(self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None):
        super().__init__(payload)
        self.status = status
        self.payload = payload
        self.headers = headers or {}


def _json(payload: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    for key, value in (headers or {}).items():
        response[key] = value
    return response


async def db(fn, *args, **kwargs):
    """Run a short DB step off the event loop and drop its connection afterwards."""

    def run():
        try:
            return fn(*args, **kwargs)
        finally:
            close_db()

    return await sync_to_async(run)()


# ── Request plumbing ────────────────────────────────────────────────────


def _trusted_networks():
    networks = []
    for cidr in getattr(settings, "TRUSTED_PROXY_CIDRS", []) or []:
        try:
            networks.append(ipaddress.ip_network(str(cidr).strip(), strict=False))
        except ValueError:
            continue
    return networks


def client_ip(request) -> str | None:
    """The right-most ``X-Forwarded-For`` hop (plus ``REMOTE_ADDR``) that is not a trusted proxy."""
    hops = [h.strip() for h in (request.headers.get("x-forwarded-for") or "").split(",") if h.strip()]
    remote = request.META.get("REMOTE_ADDR")
    if remote:
        hops.append(remote)
    trusted = _trusted_networks()
    parsed: list[str] = []
    for hop in hops:
        try:
            parsed.append(str(ipaddress.ip_address(hop)))
        except ValueError:
            continue
    for hop in reversed(parsed):
        address = ipaddress.ip_address(hop)
        if not any(address in net for net in trusted):
            return hop
    return parsed[0] if parsed else None


def read_json(request) -> Any:
    """The JSON body (gzip accepted), enforcing the wire and decompression caps."""
    limit = int(settings.RELAY_MAX_BODY_BYTES)
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > limit:
        raise RelayHTTPError(413, {"error": "too_large"})
    raw = request.read(limit + 1)
    if len(raw) > limit:
        raise RelayHTTPError(413, {"error": "too_large"})
    if (request.headers.get("content-encoding") or "").lower() == "gzip":
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            raw = inflater.decompress(raw, DECOMPRESSED_LIMIT + 1)
        except zlib.error as exc:
            raise RelayHTTPError(400, {"error": "bad_gzip"}) from exc
        if len(raw) > DECOMPRESSED_LIMIT or inflater.unconsumed_tail:
            raise RelayHTTPError(413, {"error": "too_large"})
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise RelayHTTPError(400, {"error": "bad_json"}) from exc


async def _rate_limited(token_id: int) -> bool:
    limit = int(settings.RELAY_RATE_LIMIT_PER_S)
    key = doorbell.rate_key(token_id, int(time.time()))
    try:
        client = get_async_redis()
        async with client.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, 2)
            count, _ = await pipe.execute()
    except Exception:  # noqa: BLE001 — fail open
        return False
    return int(count) > limit


def relay_view(view):
    """Decorate an authenticated relay POST view ``view(request, token, data, ...)``."""

    @functools.wraps(view)
    async def wrapper(request, *args, **kwargs):
        if request.method != "POST":
            return _json({"error": "method_not_allowed"}, status=405)
        raw_token = relay_tokens.bearer(request.headers.get("authorization"))
        token = await db(relay_tokens.authenticate, raw_token)
        if token is None:
            return _json({"error": "unauthorized"}, status=401)
        if await _rate_limited(token.pk):
            return _json({"error": "rate_limited"}, status=429, headers={"Retry-After": "1"})
        try:
            data = read_json(request)
            if not isinstance(data, dict):
                raise RelayHTTPError(400, {"error": "bad_json"})
            return await view(request, token, data, *args, **kwargs)
        except RelayHTTPError as exc:
            return _json(exc.payload, status=exc.status, headers=exc.headers)

    return transaction.non_atomic_requests(csrf_exempt(wrapper))


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for piece in str(version or "0").split(".")[:3]:
        digits = re.match(r"\d+", piece)
        parts.append(int(digits.group(0)) if digits else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def _endpoint_doc(endpoint: LLMEndpoint) -> dict[str, Any]:
    return {
        "slug": endpoint.slug,
        "kind": endpoint.kind,
        "api_flavor": endpoint.api_flavor,
        "max_concurrency": endpoint.max_concurrency,
        "request_timeout_s": endpoint.request_timeout_s,
        "num_ctx_cap": endpoint.num_ctx_cap,
    }


def _session_for(token: RelayToken, request) -> RelaySession | None:
    raw = request.META.get(SESSION_HEADER) or ""
    try:
        session_id = uuid.UUID(raw)
    except ValueError:
        return None
    return (
        RelaySession.objects.select_related("endpoint")
        .filter(pk=session_id, token=token, ended_at__isnull=True)
        .first()
    )


def _touch(session: RelaySession) -> None:
    now = timezone.now()
    if session.last_seen_at is None or session.last_seen_at < now - timedelta(seconds=SESSION_TOUCH_S):
        RelaySession.objects.filter(pk=session.pk, ended_at__isnull=True).update(last_seen_at=now)
        session.last_seen_at = now


# ── hello ───────────────────────────────────────────────────────────────


def _hello(token: RelayToken, data: dict[str, Any], ip: str | None) -> dict[str, Any]:
    endpoint = LLMEndpoint.objects.get(pk=token.endpoint_id)
    if _version_tuple(str(data.get("relay_version") or "0")) < _version_tuple(settings.RELAY_MIN_VERSION):
        raise RelayHTTPError(
            426,
            {"error": "relay_outdated", "min_relay_version": settings.RELAY_MIN_VERSION},
        )
    upstream = data.get("upstream") or {}
    kind = str(upstream.get("kind") or endpoint.kind)
    if kind != endpoint.kind:
        raise RelayHTTPError(
            409,
            {"error": "kind_mismatch", "detail": f"endpoint {endpoint.slug} is {endpoint.kind}, relay serves {kind}"},
        )
    doc = {
        "session_id": None,
        "session_valid": True,
        "role": None,
        "endpoint_enabled": endpoint.enabled,
        "endpoint": _endpoint_doc(endpoint),
        "poll_timeout_s": int(settings.RELAY_POLL_TIMEOUT_S),
        "inventory_interval_s": INVENTORY_INTERVAL_S,
        "max_result_bytes": int(settings.RELAY_MAX_BODY_BYTES),
        "min_relay_version": settings.RELAY_MIN_VERSION,
        "server_time": timezone.now().isoformat(),
    }
    if data.get("probe"):
        return doc
    relay_id = data.get("relay_id")
    try:
        relay_id = str(uuid.UUID(str(relay_id))) if relay_id else None
    except ValueError as exc:
        raise RelayHTTPError(400, {"error": "bad_relay_id"}) from exc
    session = relay_tokens.start_session(token, hello={**data, "relay_id": relay_id}, client_ip=ip)
    doc["session_id"] = str(session.pk)
    doc["role"] = session.role
    return doc


@relay_view
async def hello(request, token, data):
    return _json(await db(_hello, token, data, client_ip(request)))


# ── inventory ───────────────────────────────────────────────────────────


def _inventory(token: RelayToken, request, data: dict[str, Any]) -> dict[str, Any]:
    from backend.inference.inventory import InventoryError
    from backend.inference.inventory import apply_inventory

    session = _session_for(token, request)
    if session is None:
        return {"ok": False, "session_valid": False}
    _touch(session)
    try:
        result = apply_inventory(session.endpoint, session, data)
    except InventoryError as exc:
        raise RelayHTTPError(exc.status, {"error": "invalid_inventory", "detail": str(exc)}) from exc
    return {
        "ok": True,
        "session_valid": True,
        "accepted_models": result.accepted_models,
        "need_show": result.need_show,
    }


@relay_view
async def inventory(request, token, data):
    return _json(await db(_inventory, token, request, data))


# ── poll ────────────────────────────────────────────────────────────────


def _request_doc(row: LLMRequest, timeout_s: int) -> dict[str, Any]:
    return {
        "id": str(row.pk),
        "claim_token": row.claim_token,
        "model": {"name": row.model_name, "digest": row.digest},
        "upstream": {"method": row.method, "path": row.path, "json": row.body},
        "timeout_s": timeout_s,
        "deadline": row.deadline_at.isoformat(),
    }


def _poll_start(token: RelayToken, request, data: dict[str, Any], polled_at) -> dict[str, Any] | None:
    from backend.inference.inventory import InventoryError
    from backend.inference.inventory import update_loaded

    session = _session_for(token, request)
    if session is None:
        return None
    _touch(session)
    endpoint = LLMEndpoint.objects.get(pk=session.endpoint_id)
    if session.role == RelaySession.Role.STANDBY:
        promoted = relay_tokens.promote_standby(endpoint.pk)
        if promoted is not None and promoted.pk == session.pk:
            session.role = RelaySession.Role.ACTIVE
    inflight = data.get("inflight") or []
    if not isinstance(inflight, list):
        inflight = []
    outcome = llm_requests.process_inflight(
        session,
        inflight,
        polled_at=polled_at,
        request_timeout_s=endpoint.request_timeout_s,
    )
    if data.get("loaded") is not None and session.role == RelaySession.Role.ACTIVE:
        try:
            update_loaded(endpoint, data.get("loaded"))
        except InventoryError:
            logger.debug("ignoring invalid loaded list from relay session %s", session.pk)
    try:
        free = max(0, int(data.get("free_slots") or 0))
    except (TypeError, ValueError):
        free = 0
    return {
        "session": session,
        "endpoint_id": endpoint.pk,
        "enabled": endpoint.enabled,
        "timeout_s": endpoint.request_timeout_s,
        "resync": endpoint.inventory_resync_requested,
        "cancel": outcome.cancel,
        "free": free,
        "inflight": inflight,
    }


def _poll_claim(state: dict[str, Any]) -> list[dict[str, Any]]:
    from backend.inference.live import served_pairs

    session: RelaySession = state["session"]
    session.refresh_from_db(fields=["models", "capacity", "role", "ended_at"])
    if session.ended_at is not None or session.role != RelaySession.Role.ACTIVE:
        return []
    llm_requests.requeue_expired(endpoint_id=state["endpoint_id"])
    rows = llm_requests.claim(
        state["endpoint_id"],
        limit=state["free"],
        session=session,
        served=served_pairs(session.endpoint, session),
    )
    state["free"] -= len(rows)
    return [_request_doc(row, state["timeout_s"]) for row in rows]


def _poll_recheck(state: dict[str, Any]) -> tuple[list[dict[str, str]], bool]:
    session: RelaySession = state["session"]
    alive = RelaySession.objects.filter(pk=session.pk, ended_at__isnull=True).exists()
    known = {c["id"] for c in state["cancel"]}
    fresh = [c for c in llm_requests.cancelled_among(session, state["inflight"]) if c["id"] not in known]
    return fresh, alive


async def _wait_doorbell(endpoint_id: int, seconds: float) -> None:
    try:
        await get_async_redis().blpop([doorbell.wake_key(endpoint_id)], timeout=max(1, int(seconds)))
    except Exception:  # noqa: BLE001 — Redis down: plain sleep, the DB re-check follows
        await asyncio.sleep(min(seconds, DOORBELL_SLICE_S))


@relay_view
async def poll(request, token, data):
    polled_at = timezone.now()
    deadline = time.monotonic() + float(settings.RELAY_POLL_TIMEOUT_S)
    state = await db(_poll_start, token, request, data, polled_at)
    if state is None:
        return _json({"session_valid": False, "endpoint_enabled": False, "requests": [], "cancel": []})
    session: RelaySession = state["session"]

    def response(claimed, *, valid=True):
        return _json(
            {
                "session_valid": valid,
                "endpoint_enabled": state["enabled"],
                "role": session.role,
                "requests": claimed,
                "cancel": state["cancel"],
                "resync_inventory": bool(state["resync"]),
            },
        )

    if not state["enabled"]:
        return response([])
    claimed: list[dict[str, Any]] = []
    while True:
        if state["free"] > 0:
            claimed = await db(_poll_claim, state)
        if claimed or state["cancel"]:
            return response(claimed)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return response([])
        await _wait_doorbell(state["endpoint_id"], min(DOORBELL_SLICE_S, remaining))
        fresh_cancel, alive = await db(_poll_recheck, state)
        if not alive:
            return response([], valid=False)
        state["cancel"].extend(fresh_cancel)


# ── result ──────────────────────────────────────────────────────────────


def _result(token: RelayToken, request, rid: str, data: dict[str, Any]) -> dict[str, Any]:
    session = _session_for(token, request)
    if session is None:
        return {"accepted": False, "reason": "stale_claim", "session_valid": False}
    _touch(session)
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    timings = data.get("timings") if isinstance(data.get("timings"), dict) else {}
    status_code = data.get("status_code")
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None
    outcome = llm_requests.complete(
        rid,
        str(data.get("claim_token") or ""),
        session=session,
        ok=bool(data.get("ok")),
        status_code=status_code,
        payload=data.get("json"),
        error_kind=str(error.get("kind") or ""),
        error_message=str(error.get("message") or "")[:2000],
        upstream_ms=timings.get("upstream_ms"),
    )
    doc: dict[str, Any] = {"accepted": outcome.accepted}
    if not outcome.accepted:
        doc["reason"] = outcome.reason
    return doc


@relay_view
async def result(request, token, data, rid):
    return _json(await db(_result, token, request, str(rid), data))


# ── bye ─────────────────────────────────────────────────────────────────


def _bye(token: RelayToken, request) -> dict[str, Any]:
    session = _session_for(token, request)
    if session is None:
        return {"ok": True, "session_valid": False}
    requeued = relay_tokens.end_session(session, reason="bye")
    return {"ok": True, "requeued": requeued}


@relay_view
async def bye(request, token, data):
    return _json(await db(_bye, token, request))


# ── Script and Dockerfile downloads ─────────────────────────────────────


@lru_cache(maxsize=4)
def relay_file(name: str) -> tuple[bytes, str, str]:
    """``(content, sha256, relay version)`` of a file shipped in ``relay/`` (cached for the process)."""
    content = (RELAY_DIR / name).read_bytes()
    version_match = _VERSION_RE.search((RELAY_DIR / "llmlab_relay.py").read_text(encoding="utf-8"))
    return content, hashlib.sha256(content).hexdigest(), version_match.group(1) if version_match else ""


def _download(name: str, content_type: str):
    @transaction.non_atomic_requests
    @csrf_exempt
    async def view(request):
        if request.method not in ("GET", "HEAD"):
            return _json({"error": "method_not_allowed"}, status=405)
        try:
            content, sha, version = relay_file(name)
        except OSError:
            return _json({"error": "not_found"}, status=404)
        response = HttpResponse(content if request.method == "GET" else b"", content_type=content_type)
        response["X-Relay-Version"] = version
        response["X-Relay-Sha256"] = sha
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["Cache-Control"] = "no-cache"
        return response

    view.__name__ = f"download_{name.replace('.', '_')}"
    return view


relay_script = _download("llmlab_relay.py", "text/x-python; charset=utf-8")
relay_dockerfile = _download("Dockerfile", "text/plain; charset=utf-8")
relay_systemd_unit = _download("llmlab-relay.service", "text/plain; charset=utf-8")
