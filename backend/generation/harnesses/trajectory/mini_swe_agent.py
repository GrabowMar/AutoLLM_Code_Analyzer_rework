"""mini-swe-agent ``traj.json`` -> Trajectory.

The trajectory file (``-o /logs/traj.json``) holds ``info{exit_status, submission, model_stats}`` and the
full ``messages`` list. One ``llm`` step per assistant message, one ``shell`` step per tool call (mini's
single ``bash`` tool; older text-mode replies carry the command in a fenced block, counted as one shell
step). ``exit_status == "LimitsExceeded"`` is the step limit (``max_turns_hit``).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from backend.generation.harnesses.trajectory.common import IncrementalParser
from backend.generation.harnesses.trajectory.common import parse_arguments

if TYPE_CHECKING:
    from backend.generation.contracts.harness import Trajectory

_FORMAT_ERROR = re.compile(r"^\s*(Tool call error|Format error)", re.IGNORECASE)
_FENCED = re.compile(r"```(?:bash|sh)?\s*\n(.*?)```", re.DOTALL)
SUBMIT_SENTINEL = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
LIMIT_STATUSES = frozenset({"LimitsExceeded", "StepLimitExceeded"})
ERROR_STATUSES = frozenset({"APIError", "ModelError", "RateLimitError", "ContextWindowExceeded"})


class MiniSweAgentParser(IncrementalParser):
    """Not a line stream: :meth:`load` takes the whole ``traj.json`` (``parse_line`` is unused)."""

    name: ClassVar[str] = "mini-swe-agent"
    version: ClassVar[int] = 1
    json_stream: ClassVar[bool] = False

    def __init__(self) -> None:
        super().__init__()
        self.exit_status = ""

    def on_text(self, line: str) -> None:
        """mini prints a human log to stdout; it is evidence only, never parsed."""

    def load(self, data: bytes | None) -> None:
        if not data:
            return
        try:
            traj = json.loads(data)
        except ValueError:
            self.unparsed_lines += 1
            return
        if not isinstance(traj, dict):
            self.unparsed_lines += 1
            return
        info = traj.get("info") if isinstance(traj.get("info"), dict) else {}
        status = str(info.get("exit_status") or "")
        self.max_turns_hit = status in LIMIT_STATUSES
        self.llm_error = status in ERROR_STATUSES
        last_text = None
        for message in traj.get("messages") or []:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "exit":
                extra = message.get("extra") if isinstance(message.get("extra"), dict) else {}
                status = status or str(extra.get("exit_status") or message.get("content") or "")
                continue
            if role == "user" and _FORMAT_ERROR.match(str(message.get("content") or "")):
                # mini drops a reply without a tool call and answers with a format error: the model
                # turn happened (the gateway logged it) but produced no action.
                self.add("llm", ok=False, started=_ts(message))
                continue
            if role == "tool":
                extra = message.get("extra") if isinstance(message.get("extra"), dict) else {}
                code = extra.get("returncode")
                self.resolve(str(message.get("tool_call_id") or ""), ok=code == 0 if isinstance(code, int) else None)
                continue
            if role != "assistant":
                continue
            self.add("llm", ok=True, started=_ts(message))
            calls = message.get("tool_calls") or []
            for call in calls:
                fn = call.get("function") if isinstance(call, dict) else None
                if isinstance(fn, dict) and SUBMIT_SENTINEL in str(fn.get("arguments") or ""):
                    # mini's own end-of-task command: never executed, so it is a message, not a shell step.
                    self.add("message", tool="submit", ok=True)
                elif isinstance(fn, dict):
                    self.add_tool_call(
                        fn.get("name") or "bash",
                        parse_arguments(fn.get("arguments")),
                        call_id=str(call.get("id") or ""),
                    )
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                last_text = content
                if not calls:
                    for block in _FENCED.findall(content)[:1]:
                        self.add("shell", tool="bash", command=block.strip())
        self.max_turns_hit = status in LIMIT_STATUSES
        self.llm_error = status in ERROR_STATUSES
        self.exit_status = status
        self.message(str(info.get("submission") or "") or last_text)

    def result(self, data: bytes | None, calls: list[dict[str, Any]]) -> Trajectory:
        self.load(data)
        return self.finish(calls)


def _ts(message: dict[str, Any]) -> float | None:
    extra = message.get("extra") if isinstance(message.get("extra"), dict) else {}
    value = extra.get("timestamp") or message.get("timestamp")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
