"""OpenCode ``run --format json`` event stream -> Trajectory.

Events (one JSON object per line, ``opencode-ai@1.18.35``): ``step_start``; ``tool_use`` with
``part.{tool, callID, state{status, input, time{start, end}}}`` (milliseconds); ``text`` with
``part.text``; ``step_finish`` with ``part.{reason, tokens{input, output, reasoning, cache}}``; ``error``.
One ``llm`` step per model turn (opened at ``step_start``, so it precedes the tool calls it produced;
tokens and ``ok`` filled at ``step_finish``), one tool step per completed ``tool_use``.
"""

from __future__ import annotations

from typing import Any
from typing import ClassVar

from backend.generation.harnesses.trajectory.common import IncrementalParser


def _seconds(ms: Any) -> float | None:
    return float(ms) / 1000 if isinstance(ms, (int, float)) and not isinstance(ms, bool) else None


class OpenCodeParser(IncrementalParser):
    name: ClassVar[str] = "opencode"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self._turn: int | None = None  # index of the open llm step

    def on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        if kind == "step_start":
            self._turn = self.add("llm", started=_seconds(event.get("timestamp"))).index
        elif kind == "step_finish":
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            if self._turn is None:
                self._turn = self.add("llm", started=_seconds(event.get("timestamp"))).index
            step = self.steps[self._turn]
            ended = _seconds(event.get("timestamp"))
            self.steps[self._turn] = step.model_copy(
                update={
                    "ok": True,
                    "tokens_in": max(int(tokens.get("input") or 0), 0),
                    "tokens_out": max(int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0), 0),
                    "duration_s": (
                        max(ended - step.started, 0.0) if ended is not None and step.started is not None else None
                    ),
                },
            )
            self._turn = None
        elif kind == "tool_use":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            times = state.get("time") if isinstance(state.get("time"), dict) else {}
            start, end = _seconds(times.get("start")), _seconds(times.get("end"))
            status = state.get("status")
            self.add_tool_call(
                part.get("tool"),
                state.get("input"),
                ok=None if status not in {"completed", "error"} else status == "completed",
                started=start,
                duration_s=end - start if start is not None and end is not None else None,
            )
        elif kind == "text":
            self.message(part.get("text"))
        elif kind == "error":
            self.llm_error = True
