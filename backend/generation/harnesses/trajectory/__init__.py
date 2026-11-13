"""Trajectory parsers: ``common.py`` plus one parser per harness (ported from the v2 parser).

``parser_for(name)`` returns a fresh incremental parser for a harness (``parse_line`` per output line,
``finish(calls)`` at the end); the adapters' ``parse_trajectory`` use the same classes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.harnesses.trajectory.common import IncrementalParser

__all__ = ["parser_for"]


def parser_for(name: str) -> IncrementalParser:
    """A fresh parser for the harness ``name`` (``KeyError`` when unknown)."""
    from backend.generation.harnesses.trajectory.aider import AiderParser
    from backend.generation.harnesses.trajectory.claude_stream import ClaudeStreamParser
    from backend.generation.harnesses.trajectory.codex import CodexParser
    from backend.generation.harnesses.trajectory.gemini_cli import GeminiParser
    from backend.generation.harnesses.trajectory.goose import GooseParser
    from backend.generation.harnesses.trajectory.mini_swe_agent import MiniSweAgentParser
    from backend.generation.harnesses.trajectory.opencode import OpenCodeParser

    parsers: dict[str, type[IncrementalParser]] = {
        "opencode": OpenCodeParser,
        "mini-swe-agent": MiniSweAgentParser,
        "aider": AiderParser,
        "claude-code": ClaudeStreamParser,
        "qwen-code": ClaudeStreamParser,
        "codex": CodexParser,
        "goose": GooseParser,
        "gemini-cli": GeminiParser,
    }
    return parsers[name]()
