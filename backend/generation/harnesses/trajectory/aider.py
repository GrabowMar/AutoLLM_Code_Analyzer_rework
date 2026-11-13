"""Aider stdout (``--no-pretty --no-stream``) -> Trajectory.

Aider has no tool calls: the model answers with edit blocks. One ``edit`` step per ``Applied edit to <path>``
line; the ``llm`` steps come from the gateway log (one per main call). Lines that look like Aider/LiteLLM
errors (never indented code) are counted, and a ``litellm.*Error`` at the end marks ``ended_on_llm_error``.
"""

from __future__ import annotations

import re
from typing import ClassVar

from backend.generation.harnesses.trajectory.common import IncrementalParser

_APPLIED = re.compile(r"^Applied edit to (.+?)\s*$")
_LLM_ERROR = re.compile(r"^litellm\.\w+(Error|Exception)\b")


class AiderParser(IncrementalParser):
    name: ClassVar[str] = "aider"
    version: ClassVar[int] = 1
    json_stream: ClassVar[bool] = False

    def __init__(self) -> None:
        super().__init__()
        self._tail: list[str] = []

    def on_text(self, line: str) -> None:
        match = _APPLIED.match(line)
        if match:
            self.add("edit", tool="edit_block", ok=True, files=[match.group(1)])
        if _LLM_ERROR.match(line):
            self.llm_error = True
        self._tail.append(line)
        if len(self._tail) > 200:
            del self._tail[:-200]
        self.final_message = "\n".join(self._tail)
