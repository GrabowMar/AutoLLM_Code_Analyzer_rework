"""A part of a frozen parent, saved (``SlotTarget``) or prospective (a preview), plus its lazily loaded parent.

Every slot service works on a :class:`Part`: the address, defaults and allowlist of the target together with
a :class:`ParentContext` that loads the parent tree, the stored symbol index and the import graph once per
call chain (never cached across requests: a ``ParentContext`` lives as long as the caller holds it).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from functools import cached_property
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.contracts.slot import ContextPolicy
from backend.generation.contracts.slot import SlotAddress
from backend.generation.contracts.slot import SymbolIndex
from backend.generation.slot import index as slot_index
from backend.generation.slot.errors import SlotInvalidError
from backend.generation.slot.hole import HoleResult
from backend.generation.slot.hole import apply_hole
from backend.generation.slot.index.imports import ImportGraph
from backend.generation.slot.index.imports import build_graph
from backend.generation.slot.tree import ParentTree
from backend.generation.slot.tree import parent_tree

if TYPE_CHECKING:
    from backend.generation.models import SlotSet
    from backend.generation.models import SlotTarget

__all__ = ["ParentContext", "Part", "part_of"]


@dataclass
class ParentContext:
    """The parent of a slot set: its tree, stored symbol index and import graph (loaded on first use)."""

    slot_set: SlotSet

    @cached_property
    def tree(self) -> ParentTree:
        tree = parent_tree(self.slot_set.parent_run)
        if tree.tree_hash != self.slot_set.frozen_tree_hash:
            msg = "the parent's tree hash changed since it was frozen"
            raise SlotInvalidError(msg)
        return tree

    @cached_property
    def index(self) -> SymbolIndex:
        return slot_index.load(self.slot_set.index)

    @cached_property
    def texts(self) -> dict[str, str]:
        tree = self.tree
        return {path: tree.text(path) for path in tree.source_paths() if b"\x00" not in tree.read(path)}

    @cached_property
    def graph(self) -> ImportGraph:
        return build_graph(self.texts)

    @cached_property
    def task_content(self) -> dict[str, Any]:
        return dict(self.slot_set.task_version.content or {})

    def symbol(self, symbol_id: str | None) -> Any:
        if not symbol_id:
            return None
        return slot_index.symbol_by_id(self.index, symbol_id)


@dataclass
class Part:
    parent: ParentContext
    address: SlotAddress
    language: str
    default_hole_mode: str
    default_keep_docstring: bool
    default_context_policy: ContextPolicy
    edit_policy: str
    allowlist: list[str]
    whole_file_component: bool = False
    label: str = ""
    target_id: str | None = None
    linked_endpoints: list[str] = field(default_factory=list)

    @property
    def slot_set(self) -> SlotSet:
        return self.parent.slot_set

    @property
    def path(self) -> str:
        return self.address.path

    @property
    def source(self) -> bytes:
        return self.parent.tree.read(self.address.path)

    @property
    def original(self) -> bytes:
        if self.address.span is None:
            return self.source
        return self.source[self.address.span.start_byte : self.address.span.end_byte]

    @property
    def original_text(self) -> str:
        return self.original.decode("utf-8", errors="replace")

    def hole(self, hole_mode: str | None = None, keep_docstring: bool | None = None) -> HoleResult:
        return apply_hole(
            self.source,
            self.address,
            hole_mode or self.default_hole_mode,
            keep_docstring=self.default_keep_docstring if keep_docstring is None else keep_docstring,
            whole_file_component=self.whole_file_component,
        )


def _whole_file_component(parent: ParentContext, address: SlotAddress) -> bool:
    if address.kind != "component":
        return False
    if address.span is None:
        return True
    return any(
        component.whole_file and component.symbol == address.symbol_id and component.path == address.path
        for component in parent.index.components
    )


def part_of(target: SlotTarget, parent: ParentContext | None = None) -> Part:
    """The :class:`Part` of a saved target (``parent`` may be shared between targets of one slot set)."""
    context = parent or ParentContext(target.slot_set)
    address = SlotAddress.model_validate(target.address)
    return Part(
        parent=context,
        address=address,
        language=target.language,
        default_hole_mode=target.default_hole_mode,
        default_keep_docstring=target.default_keep_docstring,
        default_context_policy=ContextPolicy.model_validate(target.default_context_policy or {}),
        edit_policy=target.edit_policy,
        allowlist=list(target.allowlist or [address.path]),
        whole_file_component=_whole_file_component(context, address),
        label=target.label,
        target_id=str(target.pk),
        linked_endpoints=list(target.linked_endpoints or []),
    )


def whole_file_component(parent: ParentContext, address: SlotAddress) -> bool:
    return _whole_file_component(parent, address)
