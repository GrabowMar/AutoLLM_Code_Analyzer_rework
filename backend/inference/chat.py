"""Generalised chat client (spec §D.2). Contract stub (Foundation) → Track B.

``chat`` is the single entry point for scaffolding, repairs and AI-review
analysis calls:

- ``kind=openrouter``: delegates to ``OpenRouterClient(access.api_key).chat_completion``
  with today's behaviour and retries; ``OpenRouterError`` is re-raised unchanged.
- local: ``build_upstream_body`` → ``requests.enqueue`` (an ``LLMRequest``) →
  ``requests.wait_result`` → translation to the OpenAI ``chat.completion``
  shape (``usage.cost = 0.0``, ``provider = endpoint.slug``,
  ``model = f"{served}@{digest[7:19]}"``). Adds ``leased_at - created_at`` to
  ``job.execution.llm_queue_wait_s``.

Errors raise ``inference.access.LLMAccessError`` (``upstream_error``,
``upstream_oom``, ``timeout``, ``lease_expired``, ``cancelled``, ...).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.inference.access import Access


@dataclass
class ChatResult:
    # OpenAI chat.completion shape (choices/message/usage/finish_reason/model/provider)
    response: dict[str, Any]
    # Keys: endpoint, model, digest, num_ctx, load_ms, upstream_ms, eval_ms, attempt, request_id.
    served: dict[str, Any] = field(default_factory=dict)


def chat(
    access: Access,
    *,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    seed: int | None,
    job=None,
    source: str = "scaffolding",
    cancel_check: Callable[[], bool] | None = None,
    priority: int = 0,
) -> ChatResult:
    """Run one chat completion for ``access`` and return it in OpenAI shape.

    ``params`` are the resolved LLM params (max_tokens, temperature, ...);
    unsupported ones are dropped per ``params.STATIC_PARAMS``.
    ``cancel_check`` is polled every 5 s while waiting; True cancels the
    request and raises ``LLMAccessError("cancelled")``.
    """
    msg = "inference.chat.chat: Track B"
    raise NotImplementedError(msg)


def build_upstream_body(
    access: Access,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    seed: int | None,
    *,
    tools: list[dict[str, Any]] | None = None,
    stream: bool = False,
) -> tuple[str, dict[str, Any]]:
    """The exact upstream request ``(path, body)`` for a local endpoint.

    Ollama native → ``("/api/chat", {model, messages, stream: false, keep_alive,
    options: {num_ctx, num_predict, seed, ...}, tools, format, think})``;
    OpenAI flavor → ``("/v1/chat/completions", {model, messages, max_tokens, seed, ...})``.
    """
    msg = "inference.chat.build_upstream_body: Track B"
    raise NotImplementedError(msg)
