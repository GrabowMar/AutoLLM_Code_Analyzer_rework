"""Codex ``exec --json`` events -> Trajectory.

Events (``@openai/codex@0.166.0``): ``thread.started``, ``turn.started``, ``item.started`` /
``item.updated`` / ``item.completed`` with ``item.type`` in ``command_execution`` (``command``,
``exit_code``, ``status``), ``file_change`` (``changes[{path, kind}]``), ``agent_message`` (``text``),
``reasoning``, ``mcp_tool_call``, ``web_search``, ``todo_list``, ``error``; ``turn.completed{usage}``
(cumulative ``input_tokens``, ``cached_input_tokens``, ``output_tokens``), ``turn.failed{error}`` and a
top-level ``error``. Codex runs one turn in exec mode, so the gateway log supplies the per-call ``llm``
steps; ``turn.failed`` after a budget refusal is a clean stop, never an LLM error (the gateway decides).
"""

from __future__ import annotations

from typing import Any
from typing import ClassVar

from backend.generation.harnesses.trajectory.common import IncrementalParser


class CodexParser(IncrementalParser):
    name: ClassVar[str] = "codex"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self.turn_failed: str | None = None
        self.usage: dict[str, Any] = {}

    def on_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = item.get("type")
        if kind == "item.started" and item_type == "command_execution":
            self.add_tool_call("shell", {"command": item.get("command")}, call_id=str(item.get("id") or ""))
        elif kind == "item.completed":
            self._completed(item)
        elif kind == "turn.completed":
            self.usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        elif kind == "turn.failed":
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            self.turn_failed = str(error.get("message") or "turn failed")

    def _completed(self, item: dict[str, Any]) -> None:
        item_type = item.get("type")
        item_id = str(item.get("id") or "")
        if item_type == "command_execution":
            ok = item.get("exit_code") == 0 if item.get("exit_code") is not None else None
            if self.resolve(item_id, ok=ok) is None:
                self.add_tool_call("shell", {"command": item.get("command")}, ok=ok)
        elif item_type == "file_change":
            files = [str(c.get("path")) for c in item.get("changes") or [] if isinstance(c, dict) and c.get("path")]
            files = [f[len("/workspace/") :] if f.startswith("/workspace/") else f for f in files]
            self.add("edit", tool="apply_patch", ok=item.get("status") != "failed", files=files)
        elif item_type == "agent_message":
            self.message(item.get("text"))
        elif item_type in {"mcp_tool_call", "web_search", "todo_list"}:
            self.add("tool", tool=str(item_type), ok=item.get("status") != "failed")
