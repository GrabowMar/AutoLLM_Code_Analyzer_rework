"""Minimal, strict OpenRouter chat client for the reviewer.

No implicit sampling params; usage accounting on; provider routing with
require_parameters so structured outputs are honoured; HTTP-200 error bodies,
empty choices and null content are failures; Retry-After respected.

``chat`` never raises for a model/provider/network problem: it returns an
:class:`LLMCall` with ``ok=False`` and a classified ``error_kind``, so a
failed exchange can never be mistaken for an empty review. Transient
failures (429, 408, 5xx, network errors, HTTP-200 error bodies with a
transient code, empty answers) are retried with exponential backoff, honouring
``Retry-After``; client errors (bad request, auth, credits, unknown model,
refusals) are not. ``usage`` sums every attempt that reported usage, because
a failed attempt can still be billed.

Conventions follow ``generation.services.openrouter_client`` (endpoint,
referer/title headers) without its implicit temperature or silent
empty-content handling.

Local models (an Ollama / OpenAI-compatible ``LLMEndpoint``) go through the
platform's inference path instead: :func:`resolve_review_access` resolves
the frozen model with ``inference.access.resolve_access`` (the same checks as
generation: endpoint enabled and usable, model available, the frozen weights
still served), and :func:`chat` given that :class:`~backend.inference.access.Access`
sends the call as a queued ``LLMRequest`` (``source="analysis"``, which
claims after experiment traffic) through ``inference.chat``. The answer is
checked by the same rules as an OpenRouter body; ``usage.cost`` is the
inference layer's 0.0 (local compute is not billed: known, not missing).
"""

from __future__ import annotations

import email.utils
import logging
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any

import requests
from django.conf import settings

if TYPE_CHECKING:
    from backend.inference.access import Access

logger = logging.getLogger(__name__)

OPENROUTER_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
X_TITLE = "LLM Eval Lab reviewer"

BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 60.0
# A provider asking for more than this is treated as down for this call.
RETRY_AFTER_CAP_S = 120.0

# error_kind values
RETRYABLE_KINDS = frozenset({"rate_limited", "server", "network", "provider_error", "empty", "invalid_body"})
# auth | credits | not_found | bad_request | refusal | content_filter are final.

# Upper bound for a local-model call made on a connection inside a transaction
# (it then runs on its own autocommit connection in a helper thread, because
# a queued LLMRequest can only be claimed once it is committed).
INLINE_LOCAL_TIMEOUT_S = 180

# inference.access.LLMAccessError code -> error_kind of a local call. Final
# kinds reuse the OpenRouter vocabulary (auth/not_found stop the review: they
# would recur); queue outcomes get their own kinds. Only an upstream 5xx is
# retried here: the queue already retried deliveries before reporting
# unavailable / timeout / lease_expired.
_LOCAL_ERROR_KINDS = {
    "missing_api_key": "auth",
    "endpoint_disabled": "auth",
    "endpoint_forbidden": "auth",
    "model_unavailable": "not_found",
    "endpoint_offline": "unavailable",
    "unavailable": "unavailable",
    "lease_expired": "unavailable",
    "timeout": "timeout",
    "cancelled": "cancelled",
    "upstream_oom": "bad_request",
    "too_large": "bad_request",
}

_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "cached_tokens", "cost")


@dataclass
class LLMCall:
    ok: bool
    status_code: int | None = None
    content: str = ""
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    generation_id: str = ""
    latency_s: float = 0.0
    error: str = ""
    retries: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    # Added in v2: classification of the final failure (see RETRYABLE_KINDS),
    # the provider's native finish reason, the request sent (without
    # credentials) and one record per HTTP attempt.
    error_kind: str = ""
    native_finish_reason: str = ""
    request: dict[str, Any] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)


def _usage(body: dict[str, Any]) -> dict[str, Any] | None:
    """Token/cost usage of one response; missing values stay None (never 0)."""
    u = body.get("usage")
    if not isinstance(u, dict):
        return None
    completion_details = u.get("completion_tokens_details") or {}
    prompt_details = u.get("prompt_tokens_details") or {}
    cost = u.get("cost")
    return {
        "prompt_tokens": u.get("prompt_tokens"),
        "completion_tokens": u.get("completion_tokens"),
        "total_tokens": u.get("total_tokens"),
        "reasoning_tokens": completion_details.get("reasoning_tokens"),
        "cached_tokens": prompt_details.get("cached_tokens"),
        "cost": float(cost) if isinstance(cost, (int, float)) else None,
    }


def _add_usage(total: dict[str, Any], usage: dict[str, Any] | None) -> None:
    if not usage:
        return
    for key in _USAGE_KEYS:
        value = usage.get(key)
        if value is None:
            continue
        total[key] = (total.get(key) or 0) + value


def _retry_after(headers: Any) -> float | None:
    """Seconds from a ``Retry-After`` header (delta-seconds or HTTP date)."""
    value = (headers or {}).get("Retry-After") if headers is not None else None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        when = email.utils.parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter: 2, 4, 8, ... seconds (capped)."""
    base = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2**attempt))
    return base + random.uniform(0, base / 4)  # noqa: S311 - jitter, not cryptography


def _error_message(body: Any, fallback: str) -> tuple[str, Any]:
    """(message, code) of an OpenRouter error object."""
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        message = str(err.get("message") or fallback)
        meta = err.get("metadata") or {}
        if isinstance(meta, dict) and meta.get("provider_name"):
            message = f"{message} (provider {meta['provider_name']})"
        return message[:1000], err.get("code")
    if isinstance(err, str):
        return err[:1000], None
    return fallback[:1000], None


def _kind_for_status(status: int) -> str:
    if status == 429:
        return "rate_limited"
    if status in (408,) or status >= 500:
        return "server"
    if status in (401, 403):
        return "auth"
    if status == 402:
        return "credits"
    if status == 404:
        return "not_found"
    return "bad_request"


def _interpret(body: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:  # noqa: PLR0911 - one return per failure
    """Check an HTTP-200 body -> (error_kind, error, fields); error_kind "" = usable answer."""
    fields: dict[str, Any] = {
        "provider": str(body.get("provider") or ""),
        "model": str(body.get("model") or ""),
        "generation_id": str(body.get("id") or ""),
    }
    if body.get("error"):
        message, code = _error_message(body, "error body")
        kind = _kind_for_status(int(code)) if isinstance(code, int) else "provider_error"
        # A transient upstream code inside a 200 is still a provider failure worth a retry.
        return ("provider_error" if kind in RETRYABLE_KINDS else kind), f"error in HTTP 200 body: {message}", fields
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "empty", "response has no choices", fields
    choice = choices[0]
    fields["finish_reason"] = str(choice.get("finish_reason") or "")
    fields["native_finish_reason"] = str(choice.get("native_finish_reason") or "")
    if choice.get("error"):
        message, _ = _error_message(choice, "error in choice")
        return "provider_error", f"provider error during generation: {message}", fields
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        return "empty", "choice has no message", fields
    if message.get("refusal"):
        return "refusal", f"model refused: {str(message['refusal'])[:500]}", fields
    if fields["finish_reason"] == "error":
        return "provider_error", "generation ended with finish_reason=error", fields
    if fields["finish_reason"] == "content_filter":
        return "content_filter", "generation stopped by a content filter", fields
    content = message.get("content")
    if isinstance(content, list):
        # Some providers return content parts; only text parts are an answer.
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    if not isinstance(content, str) or not content.strip():
        return "empty", f"empty content (finish_reason={fields['finish_reason'] or 'none'})", fields
    fields["content"] = content
    return "", "", fields


def chat(
    api_key: str | Access,
    *,
    model: str,
    messages: list[dict[str, Any]],
    params: dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
    provider: dict[str, Any] | None = None,
    timeout_s: float = 300,
    max_retries: int = 3,
    session: Any = None,
    sleep: Callable[[float], None] = time.sleep,
    title: str = X_TITLE,
    cancel_check: Callable[[], bool] | None = None,
) -> LLMCall:
    """One logical chat completion (``max_retries`` retries after the first attempt).

    *api_key* is the OpenRouter key, or the
    :class:`~backend.inference.access.Access` from :func:`resolve_review_access`:
    an OpenRouter access is sent here with its key, a local one through the
    inference queue (:func:`_chat_local`; *provider*, *session* and
    *timeout_s* do not apply, *cancel_check* cancels the queued request).
    *params* are sent as given (``None`` values dropped): temperature,
    max_tokens, seed, reasoning, ... — nothing is added implicitly. *session*
    (anything with ``requests.Session.post``'s signature) and *sleep* are
    injectable for tests.
    """
    if not isinstance(api_key, str):
        access = api_key
        if not access.is_openrouter:
            return _chat_local(
                access,
                messages=messages,
                params=params,
                response_format=response_format,
                max_retries=max_retries,
                sleep=sleep,
                cancel_check=cancel_check,
            )
        api_key = access.api_key or ""
    payload: dict[str, Any] = {"model": model, "messages": messages}
    payload.update({k: v for k, v in (params or {}).items() if v is not None})
    if response_format is not None:
        payload["response_format"] = response_format
    if provider is not None:
        payload["provider"] = provider
    payload["usage"] = {"include": True}
    payload["stream"] = False
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": getattr(settings, "OPENROUTER_HTTP_REFERER", "") or "",
        "X-Title": title,
    }
    poster = session.post if session is not None else requests.post

    call = LLMCall(ok=False, request=payload)
    total_usage: dict[str, Any] = {}
    started = time.monotonic()
    for attempt in range(max_retries + 1):
        t0 = time.monotonic()
        record: dict[str, Any] = {"attempt": attempt, "status_code": None, "error_kind": "", "error": ""}
        retry_after: float | None = None
        body: Any = None
        # The call's fields describe its final attempt; earlier ones live in ``attempts``.
        call.raw, call.content, call.provider, call.model, call.generation_id = {}, "", "", "", ""
        call.finish_reason = call.native_finish_reason = ""
        try:
            response = poster(OPENROUTER_COMPLETIONS_URL, json=payload, headers=headers, timeout=timeout_s)
        except requests.exceptions.RequestException as exc:
            kind, error = "network", f"{type(exc).__name__}: {str(exc)[:300]}"
        else:
            record["status_code"] = response.status_code
            try:
                body = response.json()
            except ValueError:
                body = None
            if response.status_code != 200:
                message, _ = _error_message(body, (getattr(response, "text", "") or "")[:500])
                kind, error = _kind_for_status(response.status_code), f"HTTP {response.status_code}: {message}"
                retry_after = _retry_after(getattr(response, "headers", None))
            elif not isinstance(body, dict):
                kind, error = "invalid_body", "HTTP 200 with a non-JSON body"
            else:
                kind, error, fields = _interpret(body)
                call.raw = body
                call.provider = fields.get("provider", "")
                call.model = fields.get("model", "")
                call.generation_id = fields.get("generation_id", "")
                call.finish_reason = fields.get("finish_reason", "")
                call.native_finish_reason = fields.get("native_finish_reason", "")
                record.update(
                    provider=call.provider,
                    generation_id=call.generation_id,
                    finish_reason=call.finish_reason,
                )
                if not kind:
                    call.content = fields["content"]
        call.status_code = record["status_code"]
        usage = _usage(body) if isinstance(body, dict) else None
        _add_usage(total_usage, usage)
        record.update(error_kind=kind, error=error, usage=usage, latency_s=round(time.monotonic() - t0, 3))
        call.attempts.append(record)
        call.retries = attempt
        if not kind:
            call.ok = True
            call.error = call.error_kind = ""
            break
        call.error, call.error_kind = error, kind
        # A length stop is the answer's budget, not a transient fault: retrying
        # would just pay for the same truncation again.
        retryable = kind in RETRYABLE_KINDS and not (kind == "empty" and call.finish_reason == "length")
        if not retryable or attempt >= max_retries:
            break
        if retry_after is not None and retry_after > RETRY_AFTER_CAP_S:
            call.error = f"{error} (Retry-After {retry_after:.0f}s exceeds {RETRY_AFTER_CAP_S:.0f}s)"
            break
        delay = retry_after if retry_after is not None else _backoff(attempt)
        logger.warning(
            "OpenRouter %s for %s (attempt %d/%d), retrying in %.1fs",
            kind,
            model,
            attempt + 1,
            max_retries + 1,
            delay,
        )
        sleep(delay)
    call.usage = total_usage
    call.latency_s = round(time.monotonic() - started, 3)
    return call


# --------------------------------------------------------------------------
# Local models (inference queue)
# --------------------------------------------------------------------------


def resolve_review_access(user, frozen_model: dict[str, Any]) -> Access:
    """How *user* reaches the frozen reviewer model; raises ``inference.access.LLMAccessError``.

    *frozen_model* is the plan's ``reviewer["model"]`` section. A local model
    resolves through ``inference.access.resolve_access`` among the models
    *user* may see, and must still serve the weights the plan froze (another
    digest is another instrument: ``model_unavailable``). An OpenRouter model
    resolves with the user's key (``missing_api_key`` when none), also when
    its catalog row has since been removed (the frozen slug is the id sent).
    """
    from backend.inference.access import LLMAccessError
    from backend.inference.access import resolve_access
    from backend.llm_models.models import LLMModel

    frozen_model = frozen_model if isinstance(frozen_model, dict) else {}
    local = frozen_model.get("local") if isinstance(frozen_model.get("local"), dict) else None
    slug = str(frozen_model.get("slug") or "")
    visible = LLMModel.objects.visible_to(user).select_related("endpoint")
    row = None
    if frozen_model.get("id") is not None:
        row = visible.filter(pk=frozen_model["id"], model_id=slug).first()
    if row is None and slug:
        row = visible.filter(model_id=slug).first()
    if local is None:
        if row is None or not row.is_local:
            # The catalog row only carries the OpenRouter id: build the access from the frozen slug.
            placeholder = LLMModel(model_id=slug)
            return resolve_access(user, placeholder)
        raise LLMAccessError(
            code="model_unavailable",
            message=f"{slug} is now a local model; the plan froze an OpenRouter model",
            retryable=False,
        )
    if row is None or not row.is_local:
        raise LLMAccessError(
            code="model_unavailable",
            message=f"local model {slug} is not available to the run's owner",
            retryable=False,
            remediation="Check that its endpoint still exists and is shared with you.",
        )
    frozen_digest = str(local.get("digest") or "")
    if frozen_digest and row.digest != frozen_digest:
        raise LLMAccessError(
            code="model_unavailable",
            message=(
                f"{slug}: the endpoint now serves other weights ({row.digest[7:19] or 'unknown'}) than the plan "
                f"froze ({frozen_digest[7:19]})"
            ),
            retryable=False,
            remediation="Re-freeze the plan to review with the new weights.",
        )
    return resolve_access(user, row)


def _local_kind(exc: Exception) -> tuple[str, bool]:
    """(error_kind, retry) of an LLMAccessError raised by a local call."""
    code = str(getattr(exc, "code", "") or "")
    if code == "upstream_error":
        status = getattr(exc, "status_code", None)
        if isinstance(status, int):
            kind = _kind_for_status(status)
            return kind, kind == "server"
        return "provider_error", False
    return _LOCAL_ERROR_KINDS.get(code, "provider_error"), False


def _dispatch_local(
    access: Access,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    cancel_check: Callable[[], bool] | None,
):
    """One ``inference.chat`` call (``source="analysis"``) -> ``ChatResult``; raises ``LLMAccessError``.

    A local call is a queued ``LLMRequest`` that a relay or pump can claim
    only once it is committed. Outside a transaction it runs on this thread
    (the review engine already calls from a worker thread with its own
    connection, closed afterwards). Inside one (a call made from a request
    under ``ATOMIC_REQUESTS``) it runs on its own autocommit connection in a
    helper thread, bounded by :data:`INLINE_LOCAL_TIMEOUT_S` instead of the
    request's long deadline.
    """
    from django.db import connection

    from backend.inference import chat as inference_chat
    from backend.inference.db import close_db

    if not connection.in_atomic_block:
        try:
            return inference_chat.chat(
                access,
                messages=messages,
                params=params,
                seed=None,
                source="analysis",
                cancel_check=cancel_check,
            )
        finally:
            # A worker thread's connection would otherwise outlive the call; the
            # main thread's (it may hold the run's locks) is left alone.
            if threading.current_thread() is not threading.main_thread():
                close_db()

    from backend.inference.access import LLMAccessError

    deadline = time.monotonic() + INLINE_LOCAL_TIMEOUT_S

    def stop() -> bool:
        return time.monotonic() > deadline or bool(cancel_check and cancel_check())

    def call():
        try:
            return inference_chat.chat(
                access,
                messages=messages,
                params=params,
                seed=None,
                source="analysis",
                cancel_check=stop,
            )
        except LLMAccessError as exc:
            if exc.code == "cancelled" and time.monotonic() > deadline:
                msg = f"{access.endpoint.slug} did not answer within {INLINE_LOCAL_TIMEOUT_S} s"
                raise LLMAccessError(code="timeout", message=msg) from exc
            raise
        finally:
            close_db()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-review-local") as pool:
        return pool.submit(call).result()


def _chat_local(
    access: Access,
    *,
    messages: list[dict[str, Any]],
    params: dict[str, Any] | None,
    response_format: dict[str, Any] | None,
    max_retries: int,
    sleep: Callable[[float], None],
    cancel_check: Callable[[], bool] | None,
) -> LLMCall:
    """:func:`chat` for a local model: same result contract, answers checked by :func:`_interpret`.

    The request recorded is the exact upstream body (``build_upstream_body``:
    unsupported params dropped, ``response_format`` as Ollama ``format``,
    ``reasoning`` as ``think`` when the model thinks), with the endpoint slug.
    """
    from backend.inference.access import LLMAccessError
    from backend.inference.chat import build_upstream_body

    sent: dict[str, Any] = {k: v for k, v in (params or {}).items() if v is not None}
    if response_format is not None:
        sent["response_format"] = response_format
    call = LLMCall(ok=False)
    try:
        path, body = build_upstream_body(access, messages, sent, None)
    except LLMAccessError as exc:
        call.error, call.error_kind = str(exc)[:1000], "bad_request"
        call.request = {"endpoint": access.endpoint.slug, "model": access.served_model_name}
        call.attempts.append({"attempt": 0, "status_code": None, "error_kind": "bad_request", "error": call.error})
        return call
    call.request = {"endpoint": access.endpoint.slug, "path": path, "body": body}
    total_usage: dict[str, Any] = {}
    started = time.monotonic()
    for attempt in range(max_retries + 1):
        t0 = time.monotonic()
        record: dict[str, Any] = {"attempt": attempt, "status_code": None, "error_kind": "", "error": ""}
        call.raw, call.content, call.provider, call.model, call.generation_id = {}, "", "", "", ""
        call.finish_reason = call.native_finish_reason = ""
        usage = None
        retry = False
        try:
            result = _dispatch_local(access, messages, sent, cancel_check)
        except LLMAccessError as exc:
            kind, retry = _local_kind(exc)
            error = f"{exc.code}: {str(exc)[:500]}"
            record["status_code"] = getattr(exc, "status_code", None)
        else:
            response = result.response if isinstance(result.response, dict) else {}
            record["status_code"] = 200
            record["served"] = dict(result.served or {})
            kind, error, fields = _interpret(response)
            call.raw = response
            call.provider = fields.get("provider", "")
            call.model = fields.get("model", "")
            call.generation_id = fields.get("generation_id", "")
            call.finish_reason = fields.get("finish_reason", "")
            call.native_finish_reason = fields.get("native_finish_reason", "")
            record.update(provider=call.provider, generation_id=call.generation_id, finish_reason=call.finish_reason)
            if not kind:
                call.content = fields["content"]
            usage = _usage(response)
            retry = kind in RETRYABLE_KINDS and not (kind == "empty" and call.finish_reason == "length")
        call.status_code = record["status_code"]
        _add_usage(total_usage, usage)
        record.update(error_kind=kind, error=error, usage=usage, latency_s=round(time.monotonic() - t0, 3))
        call.attempts.append(record)
        call.retries = attempt
        if not kind:
            call.ok = True
            call.error = call.error_kind = ""
            break
        call.error, call.error_kind = error, kind
        if not retry or attempt >= max_retries or (cancel_check is not None and cancel_check()):
            break
        delay = _backoff(attempt)
        logger.warning(
            "Local model %s on %s: %s (attempt %d/%d), retrying in %.1fs",
            access.served_model_name,
            access.endpoint.slug,
            kind,
            attempt + 1,
            max_retries + 1,
            delay,
        )
        sleep(delay)
    call.usage = total_usage
    call.latency_s = round(time.monotonic() - started, 3)
    return call
