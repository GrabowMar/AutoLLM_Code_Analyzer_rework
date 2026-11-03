"""Gemini ``generateContent`` front (Gemini CLI): translate-only, **wave 3**.

There is no OpenRouter-native Gemini endpoint, so this front always translates to canonical chat
(``thoughtSignature`` <-> OpenRouter ``reasoning_details`` must round-trip). Until the translator lands,
a config with ``front_protocol=gemini_generate`` is refused at gateway startup (``Policy.problems``).
This module holds the routes and the native error shape so the seam is fixed.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from typing import Any

from protocols import budget_rules

NAME = "gemini_generate"
UPSTREAM_PATH = None  # translate-only: the upstream is canonical chat
PING = b"\r\n"  # alt=sse keepalive (an empty line between events)
_STATUS = {400: "INVALID_ARGUMENT", 404: "NOT_FOUND", 429: "RESOURCE_EXHAUSTED", 500: "INTERNAL", 502: "UNAVAILABLE"}


class TranslatorUnavailableError(NotImplementedError):
    """The Gemini <-> canonical chat translator ships in wave 3."""


def route(method: str, path: str) -> str | None:
    clean = path.rstrip("/")
    if method == "POST" and clean.endswith(":countTokens"):
        return "count_tokens"
    if method == "POST" and clean.endswith((":generateContent", ":streamGenerateContent")):
        return "call"
    if method == "GET" and clean.endswith("/models"):
        return "models"
    return None


def error(kind: str, message: str, status: int = 400) -> tuple[int, dict[str, Any]]:
    return status, {"error": {"code": status, "message": message, "status": _STATUS.get(status, "UNKNOWN")}}


def count_tokens(body: Mapping[str, Any]) -> dict[str, Any]:
    return {"totalTokens": budget_rules.estimate_tokens(budget_rules.prompt_text(body)), "estimate": True}


def to_chat(request_json: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    raise TranslatorUnavailableError(NAME)


def from_chat(chat_response: Mapping[str, Any]) -> dict[str, Any]:
    raise TranslatorUnavailableError(NAME)


def stream_from_chat(chat_sse_iter: Iterable[bytes]) -> Iterator[bytes]:
    raise TranslatorUnavailableError(NAME)
