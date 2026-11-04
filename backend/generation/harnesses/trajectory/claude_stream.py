"""Claude-Code-style ``stream-json`` -> Trajectory (Claude Code and Qwen Code share the schema).

Events: ``system`` (``subtype`` ``init`` with the tool list and model, ``api_retry``, ``compact_boundary``),
``assistant`` (``message.content`` blocks: ``text``, ``thinking``, ``tool_use{id, name, input}``;
``message.usage``), ``user`` (``tool_result{tool_use_id, is_error}`` blocks) and the final ``result``
(``subtype`` ``success`` / ``error_max_turns`` / ``error_during_execution``, ``num_turns``, ``usage``,
``is_error``). ``total_cost_usd`` is ignored: the gateway is authoritative for cost. Assistant messages
are emitted per content block in some versions, so one ``llm`` step is counted per distinct
``message.id``.
"""

from __future__ import annotations

from typing import Any
from typing import ClassVar

from backend.generation.harnesses.trajectory.common import IncrementalParser

MAX_TURNS_SUBTYPES = frozenset({"error_max_turns"})
ERROR_SUBTYPES = frozenset({"error_during_execution"})


class ClaudeStreamParser(IncrementalParser):
    name: ClassVar[str] = "claude-stream-json"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self._seen_messages: set[str] = set()
        self.result_subtype: str | None = None
        self.num_turns: int | None = None

    def on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "assistant":
            self._assistant(event)
        elif kind == "user":
            self._user(event)
        elif kind == "system":
            subtype = event.get("subtype")
            if subtype in {"compact_boundary", "compaction", "chat_compression"}:
                self.add("compaction", ok=True)
        elif kind == "result":
            self.result_subtype = str(event.get("subtype") or "")
            self.num_turns = event.get("num_turns") if isinstance(event.get("num_turns"), int) else None
            self.max_turns_hit = self.result_subtype in MAX_TURNS_SUBTYPES
            text = event.get("result")
            if isinstance(text, str):
                self.message(text)
            if self.result_subtype in ERROR_SUBTYPES and _api_error(event):
                self.llm_error = True

    def _assistant(self, event: dict[str, Any]) -> None:
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        msg_id = str(message.get("id") or "")
        if not msg_id or msg_id not in self._seen_messages:
            if msg_id:
                self._seen_messages.add(msg_id)
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            self.add(
                "llm",
                ok=True,
                tokens_in=(usage.get("input_tokens") or 0)
                + (usage.get("cache_read_input_tokens") or 0)
                + (usage.get("cache_creation_input_tokens") or 0),
                tokens_out=usage.get("output_tokens") or 0,
            )
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                self.add_tool_call(block.get("name"), block.get("input"), call_id=str(block.get("id") or ""))
            elif block.get("type") == "text":
                self.message(block.get("text"))
        if event.get("error"):
            self.llm_error = True

    def _user(self, event: dict[str, Any]) -> None:
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                self.resolve(str(block.get("tool_use_id") or ""), ok=not block.get("is_error"))


def _api_error(event: dict[str, Any]) -> bool:
    errors = event.get("errors")
    text = " ".join(str(e) for e in errors) if isinstance(errors, list) else str(event.get("result") or "")
    return "api" in text.lower() or "overloaded" in text.lower() or "5" in text[:4]
