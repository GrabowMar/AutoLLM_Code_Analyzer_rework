"""Sandboxed, deterministic Jinja renderer with source-span tracking (``jinja2-sandbox@1``).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-03 implements it.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.contracts.library import PromptSetContent
    from backend.generation.contracts.library import PromptTemplateContent
    from backend.generation.contracts.manifest import SourceSpan

_PENDING = "WP-03: not implemented yet"


@dataclass(frozen=True)
class RenderedText:
    """Rendered text plus the library entity that owns every span of it."""

    text: str
    sources: tuple[SourceSpan, ...] = ()


@dataclass(frozen=True)
class Stimulus:
    """One rendered prompt role of a cell (budget, seed and ids never appear in it)."""

    role: str
    template_ref: str
    text: str
    sources: tuple[SourceSpan, ...] = field(default_factory=tuple)


def render(template_content: PromptTemplateContent, context: dict[str, Any]) -> RenderedText:
    """Render one template (NFC/LF output, StrictUndefined, no attribute access)."""
    raise NotImplementedError(_PENDING)


def render_set(prompt_set: PromptSetContent, context: dict[str, Any]) -> list[Stimulus]:
    """Render every role of a prompt set."""
    raise NotImplementedError(_PENDING)
