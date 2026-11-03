"""Render contexts for app briefs and slot prompts (the documented variables of ``/prompts/variables/``).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-03 implements it.
"""

from __future__ import annotations

from typing import Any

_PENDING = "WP-03: not implemented yet"


def build_render_context(*, mode: str, **levels: Any) -> dict[str, Any]:
    """The documented top-level variables for ``mode`` (``app`` or ``slot``)."""
    raise NotImplementedError(_PENDING)


def build_app_context(**levels: Any) -> dict[str, Any]:
    """Context of an app-mode brief: task prose, stack, style, auth contract, guidance."""
    raise NotImplementedError(_PENDING)


def build_slot_context(**levels: Any) -> dict[str, Any]:
    """Context of a slot prompt: hole, interface, resolved context and spec excerpt."""
    raise NotImplementedError(_PENDING)
