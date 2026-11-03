"""Request contracts of slot mode (``SlotTargetIn``; spec section 9.6), shared by the services and the API.

``SlotTargetIn`` is the spec's shape plus three optional fields the picker needs for stale detection and
component picking: ``component`` (a component name), ``content_sha`` (what the client believes the slice
hashes to) and ``anchors``. Every field except ``kind`` is optional; omitted defaults are filled by kind
(section 7.4 table) and reported as defaults in the preview.
"""

from typing import Literal

from pydantic import Field

from backend.generation.contracts._base import ContractModel
from backend.generation.contracts._base import NonNegInt
from backend.generation.contracts._base import PosInt
from backend.generation.contracts._base import RelPath
from backend.generation.contracts._base import Sha256
from backend.generation.contracts.slot import Anchors
from backend.generation.contracts.slot import ContextPolicy
from backend.generation.contracts.slot import EndpointAddress
from backend.generation.contracts.slot import SlotKind

__all__ = ["SlotTargetCreateIn", "SlotTargetIn", "SpanIn"]


class SpanIn(ContractModel):
    """A selection: lines are required (1-based, inclusive); byte offsets are checked when given."""

    start_line: PosInt
    end_line: PosInt
    start_byte: NonNegInt | None = None
    end_byte: NonNegInt | None = None


class SlotTargetIn(ContractModel):
    kind: SlotKind
    path: RelPath | None = None
    span: SpanIn | None = None
    symbol_id: str | None = None
    endpoint: EndpointAddress | None = None
    component: str | None = None
    content_sha: Sha256 | None = None
    anchors: Anchors | None = None
    default_hole_mode: Literal["stub", "remove", "rewrite"] | None = None
    default_keep_docstring: bool | None = None
    default_context_policy: ContextPolicy | None = None
    edit_policy: Literal["revert", "fail"] | None = None
    allowlist: list[RelPath] | None = None
    linked_checks: list[str] | None = None
    linked_probes: list[str] | None = None
    cost_model: str | None = None  # LLMModel.model_id for the cost hint


class SlotTargetCreateIn(SlotTargetIn):
    label: str = Field(default="", max_length=200)
