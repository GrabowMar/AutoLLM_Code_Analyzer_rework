"""Guards around LLM output: token-budget clamping and truncation recovery."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from backend.generation.services.openrouter_client import OpenRouterClient

logger = logging.getLogger(__name__)

# Rough chars-per-token estimate for prompt-budget clamping. Deliberately
# conservative — underestimating prompt size risks a 4xx from the provider.
_CHARS_PER_TOKEN = 3
_RESPONSE_MARGIN_TOKENS = 512
_MIN_MAX_TOKENS = 256

CONTINUATION_PROMPT = (
    "Your previous message was cut off at the token limit. Continue EXACTLY "
    "where you left off, starting with the very next character. Do not repeat "
    "anything already written, do not add commentary, and do not open a new "
    "code fence unless you were between files."
)


def clamped_max_tokens(
    requested: int,
    messages: list[dict],
    llm_config: dict[str, Any] | None,
) -> int:
    """Clamp a job's ``max_tokens`` to what the model can actually emit.

    Uses the pricing/context snapshot frozen into ``resolved_bundle.llm`` —
    catalog values current at job creation, not whatever a later sync says.
    """
    limits = [requested]
    config = llm_config or {}
    max_output = int(config.get("max_output_tokens") or 0)
    if max_output > 0:
        limits.append(max_output)
    context_window = int(config.get("context_window") or 0)
    if context_window > 0:
        prompt_chars = sum(len(m.get("content") or "") for m in messages)
        prompt_estimate = prompt_chars // _CHARS_PER_TOKEN
        limits.append(context_window - prompt_estimate - _RESPONSE_MARGIN_TOKENS)
    clamped = max(_MIN_MAX_TOKENS, min(limits))
    if clamped < requested:
        logger.info("Clamped max_tokens %d -> %d", requested, clamped)
    return clamped


def call_with_continuations(
    call: Callable[[list[dict], str], dict],
    messages: list[dict],
    *,
    stage: str,
    limit: int,
) -> tuple[str, dict, int, dict]:
    """Call the LLM, requesting continuations while output ends at the token limit.

    ``call(messages, stage)`` performs one LLM round-trip (and records its
    artifact). Returns ``(stitched_content, last_response, continuations_used,
    total_usage)``. The truncation flag callers should store is
    ``is_truncated(last_response)`` — True only when the budget ran out even
    after every allowed continuation.
    """
    response = call(messages, stage)
    content = OpenRouterClient.extract_content(response)
    usage = OpenRouterClient.extract_usage(response)
    used = 0

    while OpenRouterClient.is_truncated(response) and used < limit and content:
        used += 1
        logger.info("Stage %s truncated; requesting continuation %d/%d", stage, used, limit)
        continuation_messages = [
            *messages,
            {"role": "assistant", "content": content},
            {"role": "user", "content": CONTINUATION_PROMPT},
        ]
        response = call(continuation_messages, f"{stage}_cont_{used}")
        chunk = OpenRouterClient.extract_content(response)
        chunk_usage = OpenRouterClient.extract_usage(response)
        for key in usage:
            usage[key] += chunk_usage.get(key, 0)
        if not chunk:
            break
        content += chunk

    return content, response, used, usage
