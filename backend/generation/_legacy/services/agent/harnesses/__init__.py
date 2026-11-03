"""Registry of agent harnesses (see ``base.py`` for the fairness rules)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.generation.services.agent.harnesses.aider import AiderHarness
from backend.generation.services.agent.harnesses.mini_swe_agent import MiniSweAgentHarness
from backend.generation.services.agent.harnesses.opencode import OpenCodeHarness

if TYPE_CHECKING:
    from backend.generation.services.agent.harnesses.base import Harness

HARNESSES: dict[str, Harness] = {h.name: h for h in (OpenCodeHarness(), MiniSweAgentHarness(), AiderHarness())}


def get_harness(name: str) -> Harness:
    try:
        return HARNESSES[name]
    except KeyError:
        msg = f"Unknown harness {name!r} (available: {', '.join(HARNESSES)})"
        raise ValueError(msg) from None


def default_harness_name() -> str:
    """The platform default harness (``AGENT_DEFAULT_HARNESS``)."""
    from django.conf import settings

    return settings.AGENT_DEFAULT_HARNESS


def harness_choices() -> list[dict[str, object]]:
    """What the API/UI offers: name, label, pinned version, tool-calling requirement."""
    return [
        {
            "name": h.name,
            "label": h.label,
            "version": h.version(),
            "requires_tool_calling": h.requires_tool_calling,
        }
        for h in HARNESSES.values()
    ]


def harness_details() -> list[dict[str, object]]:
    """``harness_choices`` plus description, default flag and the option schema."""
    default = default_harness_name()
    return [
        {
            **choice,
            "description": HARNESSES[str(choice["name"])].description,
            "is_default": choice["name"] == default,
            "option_schema": [f.to_dict() for f in HARNESSES[str(choice["name"])].option_schema()],
            "default_options": HARNESSES[str(choice["name"])].default_options(),
        }
        for choice in harness_choices()
    ]
