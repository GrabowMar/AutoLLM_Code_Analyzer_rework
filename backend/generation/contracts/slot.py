"""Slot-mode contracts (spec sections 3.9, 7.2-7.9).

``SlotAddress.identity()`` is what ``identity.target_hash`` hashes; hole mode
and context policy are condition-level (``SlotPolicy``) and enter
``treatment_hash`` through :meth:`SlotPolicy.identity`.
"""

from __future__ import annotations

from typing import Annotated
from typing import Any
from typing import Final
from typing import Literal

from pydantic import Field
from pydantic import StringConstraints
from pydantic import model_validator

from backend.generation.contracts._base import ContractModel
from backend.generation.contracts._base import Fraction
from backend.generation.contracts._base import NonEmptyStr
from backend.generation.contracts._base import NonNegInt
from backend.generation.contracts._base import PosInt
from backend.generation.contracts._base import RelPath
from backend.generation.contracts._base import Sha256
from backend.generation.identity import H

__all__ = [
    "INDEX_VERSION",
    "LEAK_VERSION",
    "Anchors",
    "AuditSample",
    "ClusterParams",
    "ComponentEntry",
    "ContextFile",
    "ContextPolicy",
    "DiffVsOriginal",
    "EndpointAddress",
    "EndpointEntry",
    "IndexedFile",
    "LeakMatch",
    "LeakReport",
    "LinkedChecksDiff",
    "OutOfSlotEdit",
    "ParentSelection",
    "PatternMatch",
    "PatternScope",
    "PoolQuery",
    "RelatedPolicy",
    "ResolvedContext",
    "SlotAddress",
    "SlotAgentTarget",
    "SlotCodeOutput",
    "SlotFilesOutput",
    "SlotIndexStats",
    "SlotInterface",
    "SlotKind",
    "SlotOutput",
    "SlotPolicy",
    "SlotSetSelection",
    "Span",
    "SymbolEntry",
    "SymbolIndex",
    "TargetValidation",
    "slot_output_model",
]

INDEX_VERSION: Final = 1
LEAK_VERSION: Final = 1

SlotKind = Literal["file", "region", "symbol", "endpoint", "component"]
HoleMode = Literal["stub", "remove", "rewrite"]
EditPolicy = Literal["revert", "fail"]
# text_sha of the 3 lines before/after a span, or "" at a file edge.
AnchorSha = Annotated[str, StringConstraints(pattern=r"^(?:[0-9a-f]{64})?$")]


class Span(ContractModel):
    """1-based inclusive lines plus byte offsets into the original file."""

    start_line: PosInt
    end_line: PosInt
    start_byte: NonNegInt
    end_byte: NonNegInt

    @model_validator(mode="after")
    def _ordered(self) -> Span:
        if self.end_line < self.start_line or self.end_byte < self.start_byte:
            msg = f"span ends before it starts: {self.model_dump()}"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# Symbol index (7.3)
# ---------------------------------------------------------------------------


class SymbolEntry(ContractModel):
    id: NonEmptyStr  # f"{path}::{qualname}" (stable per parent)
    path: RelPath
    language: NonEmptyStr
    kind: Literal["function", "method", "class", "component", "route_handler", "module", "block"]
    qualname: NonEmptyStr  # "TodoService.update", "TodoList" (component), "<module>"
    span: Span
    signature: str = ""
    decorators: list[str] = Field(default_factory=list)
    content_sha: Sha256
    parent: str | None = None  # enclosing symbol id


class EndpointEntry(ContractModel):
    method: NonEmptyStr
    path: NonEmptyStr
    framework: NonEmptyStr
    handler: NonEmptyStr  # symbol id
    spec_endpoint_id: str | None = None
    confidence: Literal["exact", "pattern", "heuristic"]


class ComponentEntry(ContractModel):
    name: NonEmptyStr
    path: RelPath
    framework: Literal["react", "vue", "svelte"]
    symbol: str | None = None
    whole_file: bool


class IndexedFile(ContractModel):
    path: RelPath
    language: str
    loc: NonNegInt
    parsed: bool


class SymbolIndex(ContractModel):
    """``RunSymbolIndex.blob`` payload."""

    index_version: PosInt = INDEX_VERSION
    files: list[IndexedFile] = Field(default_factory=list)
    symbols: list[SymbolEntry] = Field(default_factory=list)
    endpoints: list[EndpointEntry] = Field(default_factory=list)
    components: list[ComponentEntry] = Field(default_factory=list)


class SlotIndexStats(ContractModel):
    """``RunSymbolIndex.stats``."""

    files: NonNegInt
    symbols: NonNegInt
    endpoints: NonNegInt
    components: NonNegInt
    unparsed: list[RelPath] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Addresses, context and slot policy (7.4)
# ---------------------------------------------------------------------------


class EndpointAddress(ContractModel):
    method: NonEmptyStr
    path: NonEmptyStr
    spec_endpoint_id: str | None = None


class Anchors(ContractModel):
    before_sha: AnchorSha
    after_sha: AnchorSha


_SPAN_KINDS: Final = frozenset({"region", "symbol", "endpoint"})
_SYMBOL_KINDS: Final = frozenset({"symbol", "endpoint", "component"})


class SlotAddress(ContractModel):
    kind: SlotKind
    path: RelPath
    span: Span | None = None  # required for region/symbol/endpoint/component(non-whole-file)
    symbol_id: str | None = None  # for symbol/endpoint/component
    endpoint: EndpointAddress | None = None
    content_sha: Sha256  # text_sha of the exact original slice (whole file for kind=file)
    anchors: Anchors = Field(default_factory=lambda: Anchors(before_sha="", after_sha=""))

    @model_validator(mode="after")
    def _kind_shape(self) -> SlotAddress:
        if self.kind in _SPAN_KINDS and self.span is None:
            msg = f"slot address of kind {self.kind!r} requires a span"
            raise ValueError(msg)
        if self.kind == "file" and self.span is not None:
            msg = "slot address of kind 'file' must not carry a span"
            raise ValueError(msg)
        if self.kind in _SYMBOL_KINDS - {"component"} and not self.symbol_id:
            msg = f"slot address of kind {self.kind!r} requires symbol_id"
            raise ValueError(msg)
        if self.kind == "endpoint" and self.endpoint is None:
            msg = "slot address of kind 'endpoint' requires endpoint"
            raise ValueError(msg)
        return self

    def identity(self) -> dict[str, Any]:
        """``{kind, path, span, content_sha}``: the part of the address that ``target_hash`` uses."""
        return {
            "kind": self.kind,
            "path": self.path,
            "span": self.span.model_dump(mode="json") if self.span else None,
            "content_sha": self.content_sha,
        }


class RelatedPolicy(ContractModel):
    imports_depth: NonNegInt = 1
    include_models: bool = True
    include_routes_file: bool = True


class ContextPolicy(ContractModel):
    mode: Literal["slot_file_only", "related_files", "full_app", "interface_only"] = "related_files"
    max_context_tokens: PosInt = 24000  # files are added by priority until the budget is hit
    include_spec: Literal["none", "endpoint", "full"] = "endpoint"
    include_tests: bool = False  # parent-authored tests only if True (also for agent workspaces)
    related: RelatedPolicy = Field(default_factory=RelatedPolicy)

    def policy_hash(self) -> str:
        return H(self.model_dump(mode="json"))


class ContextFile(ContractModel):
    path: RelPath
    sha: Sha256
    tokens: NonNegInt


class ResolvedContext(ContractModel):
    files: list[ContextFile] = Field(default_factory=list)
    sha: Sha256
    tokens: NonNegInt
    truncated_paths: list[RelPath] = Field(default_factory=list)


class SlotPolicy(ContractModel):
    """Condition-level slot treatment; hashed into ``treatment_hash``."""

    hole_mode: HoleMode
    keep_docstring: bool = False
    context_policy: ContextPolicy
    edit_policy: EditPolicy

    def identity(self, resolved_context_sha: str) -> dict[str, Any]:
        """The ``treatment.slot_policy`` input of ``identity.compute_keys``."""
        return {
            "hole_mode": self.hole_mode,
            "keep_docstring": self.keep_docstring,
            "context_policy_hash": self.context_policy.policy_hash(),
            "resolved_context_sha": resolved_context_sha,
            "include_tests": self.context_policy.include_tests,
            "edit_policy": self.edit_policy,
        }


class SlotInterface(ContractModel):
    """``SlotTarget.interface``."""

    signature: str = ""
    decorators: list[str] = Field(default_factory=list)
    imports_in_scope: list[str] = Field(default_factory=list)
    exported_names: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation and leak scan (7.4)
# ---------------------------------------------------------------------------

ProbeRunStatus = Literal["exploited", "not_exploited", "error", "skipped"]


class TargetValidation(ContractModel):
    """``SlotTarget.validation``; a target is usable in studies only with status ok or uninformative."""

    status: Literal["pending", "ok", "uninformative", "error"] = "pending"
    original_pass: dict[str, list[bool]] = Field(default_factory=dict)  # check -> [bool, bool, bool]
    holed_fail: dict[str, bool] = Field(default_factory=dict)
    flaky_checks: list[str] = Field(default_factory=list)
    dropped_checks: list[str] = Field(default_factory=list)
    discriminating_checks: list[str] = Field(default_factory=list)
    probe_stability: dict[str, list[ProbeRunStatus]] = Field(default_factory=dict)
    runs: list[str] = Field(default_factory=list)  # reference run ids
    at: str | None = None
    error: str | None = None


class LeakMatch(ContractModel):
    path: RelPath
    shingles: NonNegInt
    verbatim: bool = False


class LeakReport(ContractModel):
    """``SlotTarget.leak``: 8-token shingles of the original vs context and workspace."""

    score: Fraction
    matches: list[LeakMatch] = Field(default_factory=list)
    scope: list[Literal["context", "workspace"]] = Field(default_factory=list)
    version: PosInt = LEAK_VERSION


# ---------------------------------------------------------------------------
# Executors and variants: sections 7.6 to 7.9
# ---------------------------------------------------------------------------


class SlotCodeOutput(ContractModel):
    """Single-shot output for kinds region, symbol, endpoint and component(stub): the full span replacement."""

    code: str


class SlotFilesOutput(ContractModel):
    """Single-shot output for kind file: exactly the allowlisted paths (checked by the executor)."""

    files: dict[RelPath, str]


SlotOutput = SlotCodeOutput | SlotFilesOutput


def slot_output_model(kind: SlotKind, *, whole_file_component: bool = False) -> type[ContractModel]:
    """The strict output schema for a slot kind (``component`` with ``remove`` on a whole file uses files)."""
    if kind == "file" or (kind == "component" and whole_file_component):
        return SlotFilesOutput
    return SlotCodeOutput


class OutOfSlotEdit(ContractModel):
    path: NonEmptyStr  # "<path>" or "<path>#outside_span"
    kind: Literal["added", "modified", "deleted"]
    lines: NonNegInt = 0


class DiffVsOriginal(ContractModel):
    lines_added: NonNegInt
    lines_removed: NonNegInt
    similarity: Fraction
    ast_edit_distance: NonNegInt | None = None
    normalized_equal: bool | None = None


class SlotAgentTarget(ContractModel):
    """What a restricted slot agent is told about its part (built by slot/executors/restricted_agent.py)."""

    kind: SlotKind
    label: str
    path: RelPath
    language: str
    span: Span | None = None
    hole_mode: HoleMode
    allowlist: list[RelPath]
    include_tests: bool = False


# ---------------------------------------------------------------------------
# Parent selection (7.2) and pattern-run JSON (3.9)
# ---------------------------------------------------------------------------


class PoolQuery(ContractModel):
    task: NonEmptyStr  # TaskSpecVersion ref
    stack: str | None = None
    models: list[str] | None = None
    harnesses: list[str] | None = None
    studies: list[str] | None = None
    min_pass_rate: Fraction | None = None


class ParentSelection(ContractModel):
    rule: Literal["random", "all", "manual"] = "random"
    pool_query: PoolQuery
    k: PosInt = 3  # parents to draw (random)
    stratify_by: Literal["none", "parent_model"] = "parent_model"
    seed: NonNegInt | None = None  # derived from base_seed when omitted


class SlotSetSelection(ContractModel):
    """``SlotSet.selection``: how this parent was chosen."""

    rule: Literal["random", "all", "manual"]
    pool_query: PoolQuery | None = None
    pool_size: NonNegInt | None = None
    seed: NonNegInt | None = None
    picked_by: str | None = None
    at: str | None = None


class ClusterParams(ContractModel):
    method: Literal["exact", "minhash"] = "minhash"
    threshold: Fraction = 0.8
    shingle: PosInt = 5
    num_perm: PosInt = 128


class PatternScope(ContractModel):
    features: list[Literal["span", "file"]] = ["span", "file"]  # pydantic copies mutable defaults
    targets: list[str] | Literal["all"] = "all"


class PatternMatch(ContractModel):
    path: RelPath
    start_line: PosInt
    end_line: PosInt
    snippet_sha: Sha256 | None = None
    message: str = ""


# ---------------------------------------------------------------------------
# Small model columns (sections 3.9 and 7.9). Added by WP-01 A3 (models).
# ---------------------------------------------------------------------------


class LinkedChecksDiff(ContractModel):
    """``SlotTarget.linked_checks_diff``: the researcher's edits against the automatic check links."""

    added: list[NonEmptyStr] = Field(default_factory=list)
    removed: list[NonEmptyStr] = Field(default_factory=list)


class AuditSample(ContractModel):
    """``DetectorAudit.sample``: the stratified, seeded runs a human codes (about 20 hits and 20 non-hits)."""

    hits: list[NonEmptyStr] = Field(default_factory=list)  # run ids
    non_hits: list[NonEmptyStr] = Field(default_factory=list)
    seed: NonNegInt
