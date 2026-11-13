"""Gemini CLI ``-o stream-json`` -> Trajectory.

Events (``@google/gemini-cli@0.66.0``): ``init{session_id, model}``, ``message{role, content, delta}``,
``tool_use{tool_name, tool_id, parameters}``, ``tool_result{tool_id, status, output}``, ``error`` and the
final ``result{status, stats}``. The gateway log supplies the ``llm`` steps (one per main call).
"""

from __future__ import annotations

from typing import Any
from typing import ClassVar

from backend.generation.harnesses.trajectory.common import IncrementalParser


class GeminiParser(IncrementalParser):
    name: ClassVar[str] = "gemini-cli"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []

    def on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "tool_use":
            params = event.get("parameters") if isinstance(event.get("parameters"), dict) else {}
            self.add_tool_call(event.get("tool_name"), params, call_id=str(event.get("tool_id") or ""))
        elif kind == "tool_result":
            self.resolve(str(event.get("tool_id") or ""), ok=event.get("status") == "success")
        elif kind == "message" and event.get("role") == "assistant":
            content = event.get("content")
            if isinstance(content, str):
                if event.get("delta"):
                    self._text.append(content)
                    self.message("".join(self._text))
                else:
                    self._text = [content]
                    self.message(content)
        elif kind == "message":
            self._text = []
        elif kind == "result":
            status = str(event.get("status") or "")
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            if "turn" in str(error.get("type") or "").lower():
                self.max_turns_hit = True
            elif status == "error" and error:
                self.llm_error = "api" in str(error.get("type") or "").lower()
