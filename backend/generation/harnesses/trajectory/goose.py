"""Goose ``run --output-format stream-json`` -> Trajectory.

Events (``goose`` v1.57.0): ``{"type": "message", "message": {role, content[...]}}`` where assistant
content items are ``text`` or ``toolRequest{id, toolCall{value{name, arguments}}}`` and user items are
``toolResponse{id, toolResult{status|isError}}``; a final ``{"type": "complete", "total_tokens"}``;
``{"type": "error", "error"}``. One ``llm`` step per assistant message.
"""

from __future__ import annotations

from typing import Any
from typing import ClassVar

from backend.generation.harnesses.trajectory.common import IncrementalParser
from backend.generation.harnesses.trajectory.common import parse_arguments


class GooseParser(IncrementalParser):
    name: ClassVar[str] = "goose"
    version: ClassVar[int] = 1

    def on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "message":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            role = message.get("role")
            content = message.get("content") if isinstance(message.get("content"), list) else []
            if role == "assistant":
                self.add("llm", ok=True)
                for item in content:
                    self._assistant_item(item)
            elif role == "user":
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "toolResponse":
                        result = item.get("toolResult") if isinstance(item.get("toolResult"), dict) else {}
                        failed = result.get("status") == "error" or bool(result.get("isError"))
                        value = result.get("value") if isinstance(result.get("value"), dict) else {}
                        failed = failed or bool(value.get("isError"))
                        self.resolve(str(item.get("id") or ""), ok=not failed)
        elif kind == "error":
            self.llm_error = True

    def _assistant_item(self, item: Any) -> None:
        if not isinstance(item, dict):
            return
        if item.get("type") == "text":
            self.message(item.get("text"))
        elif item.get("type") == "toolRequest":
            call = item.get("toolCall") if isinstance(item.get("toolCall"), dict) else {}
            value = call.get("value") if isinstance(call.get("value"), dict) else call
            self.add_tool_call(
                value.get("name"),
                parse_arguments(value.get("arguments")),
                call_id=str(item.get("id") or ""),
            )
