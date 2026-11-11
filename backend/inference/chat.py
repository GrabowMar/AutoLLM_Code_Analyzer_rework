"""Generalised chat client (spec §D.2).

``chat`` is the single entry point for in-process LLM calls (AI-review
analysis, endpoint probes; agent runs go through ``proxy_server``):

- ``kind=openrouter``: delegates to ``OpenRouterClient(access.api_key).chat_completion``
  with today's behaviour and retries; ``OpenRouterError`` is re-raised unchanged.
- local: ``build_upstream_body`` → ``requests.enqueue`` (an ``LLMRequest``) →
  ``requests.wait_result`` → translation to the OpenAI ``chat.completion``
  shape (``usage.cost = 0.0``, except on the native Anthropic route; ``provider = endpoint.slug``,
  ``model = f"{served}@{digest[7:19]}"``). Adds ``leased_at - created_at`` to
  the run's ``execution.llm_queue_wait_s`` (``job`` = the ``GenerationRun``).

Errors raise ``inference.access.LLMAccessError`` (``upstream_error``,
``upstream_oom``, ``timeout``, ``lease_expired``, ``cancelled``, ...).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from backend.inference.ollama_native import ConversionError
from backend.inference.ollama_native import from_native_response
from backend.inference.ollama_native import from_openai_response
from backend.inference.ollama_native import served_model_label
from backend.inference.ollama_native import to_native_messages
from backend.inference.params import STATIC_PARAMS
from backend.inference.upstream import is_opencode

if TYPE_CHECKING:
    from backend.inference.access import Access
    from backend.inference.models import LLMRequest

logger = logging.getLogger(__name__)

NATIVE_CHAT_PATH = "/api/chat"
OPENAI_CHAT_PATH = "/v1/chat/completions"
ANTHROPIC_MESSAGES_PATH = "/v1/messages"  # == anthropic_native.MESSAGES_PATH
# OpenAI param name → Ollama native ``options`` key.
_NATIVE_OPTION_NAMES = {
    "max_tokens": "num_predict",
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "min_p": "min_p",
    "seed": "seed",
    "repetition_penalty": "repeat_penalty",
    "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
}
OOM_MARKER = "requires more system memory"
REASONING_REJECTED_HINT = (
    "OpenCode Go rejected the policy's reasoning_effort for this model: clear its effort flag with "
    "`manage.py go_reasoning_effort --unset --model <model_id>` (back to provider default) and relaunch"
)
OPENROUTER_DEFAULT_TEMPERATURE = 0.3
OPENROUTER_DEFAULT_MAX_TOKENS = 32000


@dataclass
class ChatResult:
    # OpenAI chat.completion shape (choices/message/usage/finish_reason/model/provider)
    response: dict[str, Any]
    # Keys: endpoint, model, digest, num_ctx, load_ms, upstream_ms, eval_ms, attempt, request_id.
    served: dict[str, Any] = field(default_factory=dict)


# ── Upstream bodies ─────────────────────────────────────────────────────


def _think_flag(reasoning: Any) -> bool:
    """OpenRouter-style ``reasoning`` → Ollama ``think``: off only when explicitly disabled."""
    if isinstance(reasoning, bool):
        return reasoning
    if isinstance(reasoning, dict):
        if reasoning.get("enabled") is False or reasoning.get("exclude") is True:
            return False
        return str(reasoning.get("effort") or "").lower() not in ("none", "off")
    return bool(reasoning)


def _native_format(response_format: Any) -> Any:
    if not isinstance(response_format, dict):
        return None
    kind = response_format.get("type")
    if kind == "json_object":
        return "json"
    if kind == "json_schema":
        schema = (response_format.get("json_schema") or {}).get("schema")
        return schema if isinstance(schema, dict) else "json"
    return None


def build_upstream_body(
    access: Access,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    seed: int | None,
    *,
    tools: list[dict[str, Any]] | None = None,
    stream: bool = False,
    tool_choice: Any = None,
    parallel_tool_calls: Any = None,
    anthropic_options: Any = None,
) -> tuple[str, dict[str, Any]]:
    """The exact upstream request ``(path, body)`` for a local endpoint.

    Ollama native → ``("/api/chat", {model, messages, stream: false, keep_alive,
    options: {num_ctx, num_predict, seed, ...}, tools, format, think})``;
    OpenAI flavor → ``("/v1/chat/completions", {model, messages, max_tokens, seed, ...})``;
    Anthropic flavor → ``("/v1/messages", <Messages request>)`` built by
    :func:`backend.inference.anthropic_native.to_messages_request` (it alone reads ``tool_choice``,
    ``parallel_tool_calls`` and ``anthropic_options``; it always streams upstream).
    Params outside ``STATIC_PARAMS[(kind, flavor)]`` are dropped; ``provider``,
    ``usage`` and ``tool_choice`` are never sent to the other flavors. Their upstream
    calls are non-streaming (``stream`` is accepted for signature compatibility).
    """
    if access.api_flavor == "anthropic":
        from backend.inference import anthropic_native

        merged = {**dict(params or {}), **({"seed": seed} if seed is not None else {})}
        body = anthropic_native.to_messages_request(
            access.served_model_name,
            messages,
            merged,
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            options=anthropic_options or anthropic_native.Options(),
        )
        return anthropic_native.MESSAGES_PATH, body
    params = dict(params or {})
    allowed = STATIC_PARAMS.get((access.kind, access.api_flavor), frozenset())
    if seed is not None:
        params["seed"] = seed
    tools = tools if tools is not None else params.get("tools")
    sampling = {k: v for k, v in params.items() if k in allowed and k != "tools" and v is not None}
    stop = params.get("stop")

    if access.api_flavor == "native":
        try:
            native_messages = to_native_messages(messages)
        except ConversionError as exc:
            from backend.inference.access import LLMAccessError

            raise LLMAccessError(code="upstream_error", message=str(exc), retryable=False, status_code=400) from exc
        options: dict[str, Any] = {}
        if access.num_ctx:
            options["num_ctx"] = int(access.num_ctx)
        for key, value in sampling.items():
            options[_NATIVE_OPTION_NAMES[key]] = value
        if stop:
            options["stop"] = list(stop) if isinstance(stop, (list, tuple)) else [stop]
        body: dict[str, Any] = {
            "model": access.served_model_name,
            "messages": native_messages,
            "stream": False,
            "options": options,
        }
        if access.keep_alive:
            body["keep_alive"] = access.keep_alive
        if tools and "tools" in allowed:
            body["tools"] = tools
        if (fmt := _native_format(params.get("response_format"))) is not None:
            body["format"] = fmt
        if params.get("reasoning") is not None and "thinking" in access.capabilities:
            body["think"] = _think_flag(params["reasoning"])
        return NATIVE_CHAT_PATH, body

    body = {"model": access.served_model_name, "messages": messages, "stream": False, **sampling}
    if stop:
        body["stop"] = stop
    if isinstance(params.get("response_format"), dict):
        body["response_format"] = params["response_format"]
    if tools and "tools" in allowed:
        body["tools"] = tools
    if isinstance(params.get("reasoning"), dict) and is_opencode(access.base_url):
        # OpenCode Go (C2): the canonical ``reasoning`` object is kept and ``upstream._provider_quirks`` sends its
        # effort as ``reasoning_effort``. No other OpenAI-flavor endpoint receives it (unchanged).
        body["reasoning"] = dict(params["reasoning"])
    return OPENAI_CHAT_PATH, body


# ── Result translation ──────────────────────────────────────────────────


def _error_text(payload: Any) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)
        if err:
            return str(err)
    return str(payload)[:2000] if payload is not None else ""


def raise_for_request(row: LLMRequest) -> None:
    """Map a finished, unsuccessful request to :class:`LLMAccessError` (§D.2 Errors)."""
    from backend.inference.access import LLMAccessError
    from backend.inference.models import LLMRequest

    Status = LLMRequest.Status  # noqa: N806
    slug = row.endpoint.slug
    if row.status == Status.CANCELLED:
        raise LLMAccessError(code="cancelled", message="The LLM request was cancelled", retryable=False)
    if row.status == Status.EXPIRED:
        raise LLMAccessError(
            code="timeout",
            message=f"{slug} did not answer before the request deadline",
            remediation="Check that the endpoint's relay or server is running.",
        )
    if row.status == Status.FAILED:
        kind = row.error_kind
        if kind in ("connect", "lease_expired", "timeout"):
            code = {"connect": "unavailable"}.get(kind, kind)
            raise LLMAccessError(code, f"{slug}: {row.error or kind}")
        if kind == "too_large":
            raise LLMAccessError(code="too_large", message=f"{slug}: response too large", retryable=False)
        raise LLMAccessError(code="upstream_error", message=f"{slug}: {row.error or kind}", retryable=False)
    status = int(row.response_status or 0)
    if status >= 400:
        text = _error_text(row.response) or row.error
        if status >= 500 and OOM_MARKER in text:
            raise LLMAccessError(
                code="upstream_oom",
                message=f"{slug}: {text[:500]}",
                status_code=status,
                remediation="Lower the endpoint's num_ctx cap or max resident models.",
            )
        message = f"{slug} answered {status}: {text[:1000]}"
        remediation = None
        if status < 500 and "effort" in text.lower() and is_opencode(row.endpoint.base_url):
            # C2: a Go model that refuses ``reasoning_effort`` fails loudly, with the way back to provider default.
            # Keyed on "effort" (the parameter only the policy's forced effort sends; ``row.body`` is cleared
            # by now), never on "reasoning" alone: DeepSeek's thinking-mode 400 ("The reasoning_content in the
            # thinking mode must be passed back") happens under provider default too and is not this.
            remediation = REASONING_REJECTED_HINT
            message = f"{message} ({REASONING_REJECTED_HINT})"
        raise LLMAccessError(
            code="upstream_error",
            message=message,
            retryable=status >= 500,
            status_code=status,
            remediation=remediation,
        )


def translate(row: LLMRequest, access: Access) -> dict[str, Any]:
    """A ``done`` request's response in OpenAI ``chat.completion`` shape."""
    payload = row.response if isinstance(row.response, dict) else {}
    label = served_model_label(access.served_model_name, access.digest)
    # Stamp from the stored request, so a re-delivered (deduped / reattached) response is byte-identical.
    created = int(row.completed_at.timestamp()) if row.completed_at else None
    if row.path == NATIVE_CHAT_PATH:
        return from_native_response(
            payload,
            request_id=str(row.pk),
            served_model=label,
            provider=access.endpoint.slug,
            created=created,
        )
    return from_openai_response(
        payload,
        request_id=str(row.pk),
        served_model=label,
        provider=access.endpoint.slug,
        created=created,
        # Anthropic's native route is real money priced from the list-price table: never a stamped $0.
        zero_cost=row.path != ANTHROPIC_MESSAGES_PATH,
    )


def served_info(row: LLMRequest, access: Access) -> dict[str, Any]:
    return {
        "endpoint": access.endpoint.slug,
        "model": access.served_model_name,
        "digest": access.digest,
        "num_ctx": access.num_ctx,
        "load_ms": row.load_ms,
        "upstream_ms": row.upstream_ms,
        "eval_ms": row.eval_ms,
        "attempt": row.attempt,
        "request_id": str(row.pk),
    }


def add_queue_wait(job, row: LLMRequest) -> None:
    """Add ``leased_at - created_at`` to the run's ``execution.llm_queue_wait_s`` (one guarded jsonb UPDATE).

    ``job`` is the ``GenerationRun`` (or any object with its ``pk``) the request belongs to.
    """
    if job is None or row.leased_at is None or row.created_at is None:
        return
    seconds = max(0.0, (row.leased_at - row.created_at).total_seconds())
    from django.db import connection

    from backend.generation.models import GenerationRun

    table = GenerationRun._meta.db_table  # noqa: SLF001
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {table}
               SET execution = jsonb_set(
                       COALESCE(execution, '{{}}'::jsonb),
                       '{{llm_queue_wait_s}}',
                       to_jsonb(round((COALESCE((execution->>'llm_queue_wait_s')::numeric, 0) + %s)::numeric, 3))
                   )
             WHERE id = %s
            """,  # noqa: S608 — table name from model meta, values parameterised
            [seconds, str(job.pk)],
        )


# ── Entry point ─────────────────────────────────────────────────────────


def chat(
    access: Access,
    *,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    seed: int | None,
    source: str,
    job=None,
    cancel_check: Callable[[], bool] | None = None,
    priority: int = 0,
) -> ChatResult:
    """Run one chat completion for ``access`` and return it in OpenAI shape.

    ``source`` is the ``LLMRequest.Source`` value the queued row records
    (required: there is no default caller kind in v3). ``job`` is the
    ``GenerationRun`` the call belongs to, if any.
    ``params`` are the resolved LLM params (max_tokens, temperature, ...);
    unsupported ones are dropped per ``params.STATIC_PARAMS``.
    ``cancel_check`` is polled every 5 s while waiting; True cancels the
    request and raises ``LLMAccessError("cancelled")``.
    """
    if access.is_openrouter:
        from backend.generation.services.openrouter_client import OpenRouterClient

        params = dict(params or {})
        response = OpenRouterClient(api_key=access.api_key).chat_completion(
            model=access.served_model_name,
            messages=messages,
            temperature=params.get("temperature", OPENROUTER_DEFAULT_TEMPERATURE),
            max_tokens=params.get("max_tokens") or OPENROUTER_DEFAULT_MAX_TOKENS,
            seed=seed,
            params=params,
        )
        return ChatResult(
            response=response,
            served={"endpoint": access.endpoint.slug, "model": access.served_model_name},
        )

    from backend.inference import requests as llm_requests

    path, body = build_upstream_body(access, messages, params, seed)
    rid = llm_requests.enqueue(
        access,
        path,
        body,
        job=job,
        source=source,
        priority=priority,
        digest=access.digest,
    )
    row = llm_requests.wait_result(rid, cancel_check=cancel_check)
    if row.first_delivery:
        add_queue_wait(job, row)
    if row.status != row.Status.DONE or int(row.response_status or 200) >= 400:
        raise_for_request(row)
    return ChatResult(response=translate(row, access), served=served_info(row, access))
