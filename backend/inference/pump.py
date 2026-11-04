"""Direct-endpoint pumps (spec §C.2, §D.7, §D.8).

One thread per in-flight slot of each enabled direct non-OpenRouter endpoint
(Σ ``max_concurrency`` capped at :data:`MAX_PUMP_THREADS`): each claims
``LLMRequest`` rows under the per-endpoint advisory lock (``pump_owner =
executor_id``), calls ``base_url`` through :mod:`inference.upstream` and stores
the result exactly like the relay's result endpoint. A per-endpoint
inventory thread refreshes the model list every :data:`INVENTORY_INTERVAL_S`
(and at once when ``inventory_resync_requested``). A watcher shuts the socket
of any in-flight call whose request was cancelled.

Every thread closes its DB connection after each unit of work.
"""

from __future__ import annotations

import contextlib
import logging
import socket
import threading
import time
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime

from django.db import close_old_connections
from django.utils import timezone

from backend.inference import doorbell
from backend.inference.db import close_db
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest

logger = logging.getLogger(__name__)

MAX_PUMP_THREADS = 32
INVENTORY_INTERVAL_S = 120
MANAGER_TICK_S = 5.0
WATCH_TICK_S = 2.0
IDLE_WAIT_S = 2.0
SHOW_TIMEOUT_S = 30
# Non-stream OpenAI chat calls of these sources are streamed upstream under per-call limits (E2E-17).
STREAMED_SOURCES = frozenset({LLMRequest.Source.AGENT_PROXY, LLMRequest.Source.SLOT_CALL})
# The cut answer is due this long (at most 10 % of the timeout) before the gateway's per-call timeout.
CUT_MARGIN_S = 30
# Less than this left of a call's policy wall clock: the call fails at once, without an upstream call (E2E-20).
MIN_CALL_WALL_S = 1.0


@dataclass
class _EndpointState:
    endpoint_id: int
    slots: list[tuple[threading.Thread, threading.Event]] = field(default_factory=list)
    inventory: tuple[threading.Thread, threading.Event] | None = None


class _Inflight:
    """``{rid: socket}`` of calls in progress, for the cancel watcher."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sockets: dict[str, socket.socket] = {}

    def set(self, rid: str, sock: socket.socket | None) -> None:
        with self._lock:
            if sock is None:
                self._sockets.pop(rid, None)
            else:
                self._sockets[rid] = sock

    def snapshot(self) -> dict[str, socket.socket]:
        with self._lock:
            return dict(self._sockets)


def _direct_endpoints() -> list[LLMEndpoint]:
    return list(
        LLMEndpoint.objects.filter(enabled=True, connection=LLMEndpoint.Connection.DIRECT, is_system=False)
        .exclude(kind=LLMEndpoint.Kind.OPENROUTER)
        .order_by("pk"),
    )


def _api_key(endpoint: LLMEndpoint) -> str | None:
    from backend.inference.access import _decrypt_api_key

    try:
        return _decrypt_api_key(endpoint)
    except Exception:  # noqa: BLE001 — surfaced as last_error by the inventory thread
        return None


def run_one(endpoint_id: int, owner: str, inflight: _Inflight | None = None) -> bool:
    """Claim and execute at most one request of a direct endpoint; True if one was handled."""
    from backend.inference import requests as llm_requests
    from backend.inference import upstream

    rows = llm_requests.claim(endpoint_id, limit=1, pump_owner=owner)
    if not rows:
        return False
    row = rows[0]
    endpoint = LLMEndpoint.objects.get(pk=endpoint_id)
    rid = str(row.pk)
    try:
        limits = stream_limits(row, endpoint)
    except Exception:  # never lose the claimed row over the limits lookup
        logger.exception("Stream limits for request %s failed; calling without them", rid)
        limits = None
    # The policy's per-call wall clock runs from the row's creation (the gateway's call), across its
    # queue time and every attempt; once it is spent the gateway has given up, so neither call nor requeue.
    call_deadline = None
    if limits is not None and limits.call_wall_left_s is not None:
        call_deadline = time.monotonic() + limits.call_wall_left_s
        if limits.call_wall_left_s < MIN_CALL_WALL_S:
            llm_requests.complete(
                row.pk,
                row.claim_token,
                pump_owner=owner,
                ok=False,
                error_kind="timeout",
                error_message="the per-call wall clock was spent in the queue and earlier attempts",
                requeue=False,
            )
            close_db()
            return True
    close_db()

    def on_socket(sock):
        if inflight is not None:
            inflight.set(rid, sock)

    try:
        resp = upstream.call(
            endpoint.base_url,
            row.method,
            row.path,
            row.body,
            timeout=float(endpoint.request_timeout_s),
            api_key=_api_key(endpoint),
            on_socket=on_socket,
            stream_limits=limits,
        )
    except upstream.UpstreamError as exc:
        cancelled = LLMRequest.objects.filter(pk=row.pk, status=LLMRequest.Status.CANCELLED).exists()
        requeue = call_deadline is None or time.monotonic() + MIN_CALL_WALL_S < call_deadline
        llm_requests.complete(
            row.pk,
            row.claim_token,
            pump_owner=owner,
            ok=False,
            status_code=exc.status,
            error_kind="cancelled" if cancelled else exc.kind,
            error_message=str(exc) if requeue else f"{exc} (per-call wall clock spent; not retried)",
            requeue=requeue,
        )
    else:
        ok = 200 <= resp.status < 300
        llm_requests.complete(
            row.pk,
            row.claim_token,
            pump_owner=owner,
            ok=ok and resp.payload is not None,
            status_code=resp.status,
            payload=resp.payload if resp.payload is not None else {"error": {"message": resp.text}},
            error_kind="" if ok and resp.payload is not None else ("http" if not ok else "bad_json"),
            error_message="" if ok else resp.text,
            upstream_ms=resp.elapsed_ms,
        )
    finally:
        close_db()
    return True


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _policy_content(job_id) -> dict:
    """The run's frozen effective ``ResourcePolicyContent`` (``{}`` without a run or manifest)."""
    if job_id is None:
        return {}
    from backend.generation.models import RunManifest
    from backend.inference.proxy_server import _effective_content

    manifest = RunManifest._base_manager.filter(run_id=job_id).values_list("manifest", flat=True).first()  # noqa: SLF001
    return _effective_content(manifest) if isinstance(manifest, dict) else {}


def stream_limits(row: LLMRequest, endpoint: LLMEndpoint):
    """The per-call limits a run's non-stream OpenAI chat call is streamed under (E2E-17), else None.

    Agent-proxy and slot calls only (the sandbox path and single-shot slot calls). The output cap is
    the body's policy-pinned ``max_tokens`` (``call.max_output_tokens`` plus the reasoning budget),
    else the same sum from the manifest; the wall clock is the policy's ``time.llm_call_timeout_s``
    less :data:`CUT_MARGIN_S` (so the cut answer reaches the run's gateway before its own per-call
    timeout fires), anchored at the row's ``created_at`` (time spent queued and in earlier attempts
    is already gone; E2E-20), never above the endpoint's ``request_timeout_s``.
    """
    from backend.inference import upstream
    from backend.inference.chat import OPENAI_CHAT_PATH
    from backend.inference.proxy_server import budget_rules

    body = row.body
    if (
        row.source not in STREAMED_SOURCES
        or str(row.method).upper() != "POST"
        or row.path != OPENAI_CHAT_PATH
        or not isinstance(body, dict)
        or body.get("stream")
    ):
        return None
    content = _policy_content(row.job_id)
    call = content.get("call") or {}
    reasoning = content.get("reasoning") or {}
    cap = _positive_int(body.get("max_tokens"))
    if cap is None and _positive_int(call.get("max_output_tokens")):
        extra = _positive_int(reasoning.get("max_tokens")) if reasoning.get("mode") == "max_tokens" else None
        cap = int(call["max_output_tokens"]) + (extra or 0)
    wall = float(endpoint.request_timeout_s)
    left = None
    policy_wall = _positive_int((content.get("time") or {}).get("llm_call_timeout_s"))
    if policy_wall:
        left = policy_wall - min(CUT_MARGIN_S, policy_wall * 0.1) - _age_s(row)
        wall = max(0.0, min(wall, left))
    rules = budget_rules()
    prompt = rules.estimate_tokens(rules.prompt_text(body))
    return upstream.StreamLimits(
        max_output_tokens=cap,
        wall_clock_s=wall,
        prompt_tokens_estimate=prompt,
        call_wall_left_s=left,
    )


def _age_s(row: LLMRequest) -> float:
    """Seconds since the row was created (its queue time plus earlier attempts); 0 when unknown."""
    created = getattr(row, "created_at", None)
    if not isinstance(created, datetime):
        return 0.0
    return max(0.0, (timezone.now() - created).total_seconds())


def refresh_inventory(endpoint: LLMEndpoint) -> None:
    """Rebuild a direct endpoint's inventory from its own API and apply it (errors → ``last_error``)."""
    from backend.inference import upstream
    from backend.inference.inventory import apply_inventory

    api_key = _api_key(endpoint)
    timeout = 30.0

    def get(path, body=None, method="GET"):
        resp = upstream.call(endpoint.base_url, method, path, body, timeout=timeout, api_key=api_key)
        if resp.status >= 400 or resp.payload is None:
            raise upstream.UpstreamError(
                kind="http",
                message=f"{path} answered {resp.status}: {resp.text[:200]}",
                status=resp.status,
            )
        return resp.payload

    try:
        if endpoint.kind == LLMEndpoint.Kind.OLLAMA:
            version = str((get("/api/version") or {}).get("version") or "")
            tags = (get("/api/tags") or {}).get("models") or []
            loaded = (get("/api/ps") or {}).get("models") or []
            models = [_tag_entry(t) for t in tags if isinstance(t, dict) and t.get("name")]
            payload = {"version": version, "models": models, "show": {}, "loaded": _ps_entries(loaded)}
            result = apply_inventory(endpoint, None, payload)
            if result.need_show:
                show = {}
                for name in result.need_show:
                    info = get("/api/show", {"model": name}, method="POST")
                    show[name] = show_entry(info)
                payload["show"] = show
                apply_inventory(endpoint, None, payload)
        else:
            data = (get("/v1/models") or {}).get("data") or []
            models = [
                {"name": str(m.get("id")), "context_length": m.get("max_model_len")}
                for m in data
                if isinstance(m, dict) and m.get("id")
            ]
            apply_inventory(endpoint, None, {"models": models})
        LLMEndpoint.objects.filter(pk=endpoint.pk).update(last_error="")
    except Exception as exc:  # noqa: BLE001 — any failure is recorded as the endpoint's last_error
        logger.warning("Inventory of direct endpoint %s failed: %s", endpoint.slug, exc)
        LLMEndpoint.objects.filter(pk=endpoint.pk).update(
            last_error=f"inventory: {exc}"[:2000],
            inventory_resync_requested=False,
        )
    finally:
        close_db()


def _tag_entry(tag: dict) -> dict:
    details = tag.get("details") or {}
    return {
        "name": str(tag.get("name")),
        "digest": _digest(tag.get("digest")),
        "size": tag.get("size"),
        "family": details.get("family") or "",
        "parameter_size": details.get("parameter_size") or "",
        "quantization": details.get("quantization_level") or "",
    }


def _digest(value) -> str:
    text = str(value or "")
    return text if not text or text.startswith("sha256:") else f"sha256:{text}"


def _ps_entries(loaded) -> list[dict]:
    return [
        {
            "name": str(m.get("name")),
            "digest": _digest(m.get("digest")),
            "size": m.get("size"),
            "size_vram": m.get("size_vram"),
            "expires_at": str(m.get("expires_at") or ""),
            "context_length": m.get("context_length"),
        }
        for m in loaded
        if isinstance(m, dict) and m.get("name")
    ]


def show_entry(info: dict) -> dict:
    """``/api/show`` → ``{context_length, capabilities, template_sha}`` (the relay does the same)."""
    import hashlib

    model_info = info.get("model_info") or {}
    context = None
    for key, value in model_info.items():
        if str(key).endswith(".context_length") and isinstance(value, int):
            context = value
            break
    template = info.get("template") or ""
    return {
        "context_length": context,
        "capabilities": [c for c in info.get("capabilities") or [] if isinstance(c, str)][:16],
        "template_sha": hashlib.sha256(template.encode()).hexdigest()[:16] if template else "",
    }


class PumpManager:
    """Owns the slot, inventory and watcher threads; reconciles them with the endpoint rows."""

    def __init__(self, stop: threading.Event, executor_id: str) -> None:
        self.stop = stop
        self.owner = executor_id
        self.inflight = _Inflight()
        self.endpoints: dict[int, _EndpointState] = {}

    # -- threads ---------------------------------------------------------

    def _slot_loop(self, endpoint_id: int, stop: threading.Event) -> None:
        while not (stop.is_set() or self.stop.is_set()):
            try:
                handled = run_one(endpoint_id, self.owner, self.inflight)
            except Exception:
                logger.exception("Direct pump for endpoint %s failed", endpoint_id)
                handled = False
                time.sleep(IDLE_WAIT_S)
            finally:
                close_db()
            if not handled:
                doorbell.wait_endpoint(endpoint_id, IDLE_WAIT_S)

    def _inventory_loop(self, endpoint_id: int, stop: threading.Event) -> None:
        last = 0.0
        while not (stop.is_set() or self.stop.is_set()):
            try:
                endpoint = LLMEndpoint.objects.filter(pk=endpoint_id).first()
                if endpoint is None:
                    return
                if endpoint.inventory_resync_requested or time.monotonic() - last >= INVENTORY_INTERVAL_S:
                    refresh_inventory(endpoint)
                    last = time.monotonic()
            except Exception:
                logger.exception("Inventory loop for endpoint %s failed", endpoint_id)
            finally:
                close_db()
            stop.wait(MANAGER_TICK_S)

    def _watch_cancellations(self) -> None:
        while not self.stop.wait(WATCH_TICK_S):
            current = self.inflight.snapshot()
            if not current:
                continue
            try:
                cancelled = {
                    str(rid)
                    for rid in LLMRequest.objects.filter(
                        pk__in=list(current),
                        status=LLMRequest.Status.CANCELLED,
                    ).values_list("pk", flat=True)
                }
            except Exception:
                logger.exception("Pump cancel watcher failed")
                cancelled = set()
            finally:
                close_db()
            for rid in cancelled:
                sock = current.get(rid)
                if sock is not None:
                    with contextlib.suppress(OSError):
                        sock.shutdown(socket.SHUT_RDWR)

    @staticmethod
    def _start(target, *args, name: str) -> tuple[threading.Thread, threading.Event]:
        stop = threading.Event()
        thread = threading.Thread(target=target, args=(*args, stop), name=name, daemon=True)
        thread.start()
        return thread, stop

    # -- reconciliation ----------------------------------------------------

    def reconcile(self) -> None:
        wanted: dict[int, int] = {}
        budget = MAX_PUMP_THREADS
        for endpoint in _direct_endpoints():
            n = min(int(endpoint.max_concurrency), budget)
            if n <= 0:
                logger.warning("Pump thread budget exhausted; endpoint %s gets no pump", endpoint.slug)
                continue
            wanted[endpoint.pk] = n
            budget -= n
        for endpoint_id in list(self.endpoints):
            if endpoint_id not in wanted:
                self._stop_endpoint(endpoint_id)
        for endpoint_id, n in wanted.items():
            state = self.endpoints.setdefault(endpoint_id, _EndpointState(endpoint_id))
            state.slots = [(t, s) for t, s in state.slots if t.is_alive()]
            while len(state.slots) > n:
                _thread, stop = state.slots.pop()
                stop.set()
            while len(state.slots) < n:
                state.slots.append(
                    self._start(self._slot_loop, endpoint_id, name=f"pump-{endpoint_id}-{len(state.slots)}"),
                )
            if state.inventory is None or not state.inventory[0].is_alive():
                state.inventory = self._start(self._inventory_loop, endpoint_id, name=f"pump-inv-{endpoint_id}")

    def _stop_endpoint(self, endpoint_id: int) -> None:
        state = self.endpoints.pop(endpoint_id, None)
        if state is None:
            return
        for _thread, stop in state.slots:
            stop.set()
        if state.inventory is not None:
            state.inventory[1].set()

    def run(self) -> None:
        watcher = threading.Thread(target=self._watch_cancellations, name="pump-cancel-watch", daemon=True)
        watcher.start()
        while not self.stop.is_set():
            try:
                close_old_connections()
                self.reconcile()
            except Exception:
                logger.exception("Pump manager tick failed")
            finally:
                close_db()
            self.stop.wait(MANAGER_TICK_S)
        for endpoint_id in list(self.endpoints):
            self._stop_endpoint(endpoint_id)


def run_direct_pumps(stop: threading.Event, executor_id: str) -> None:
    """Run the pump threads until ``stop`` is set; picks up endpoint changes (enable/disable/caps) as they happen."""
    PumpManager(stop, executor_id).run()
