"""Address resolution, target creation, allowlists and cross-parent matching (spec section 7.4).

* ``resolve(slot_set, target_in)`` turns a picker selection into a :class:`~backend.generation.slot.part.Part`
  (a verified ``SlotAddress`` plus defaults). The parent is immutable, so ``(path, span, content_sha)`` is
  authoritative; when the client's ``content_sha`` does not match the slice it names, the part is relocated
  by ``symbol_id`` and then by ``anchors`` (3 lines before/after) in the same file; if that fails the request
  is refused with 409 ``address_stale``. Region spans are snapped to whole lines.
* ``preview(slot_set, target_in)``: hole, resolved context, linked checks/probes, leak report, target hash,
  cost hint and issues of a prospective target (writes nothing).
* ``create_target(slot_set, target_in, user=...)``: saves the target and queues its validation on commit
  (original 3x and holed tree 1x on the eval lane). Creating the same part twice returns the existing row.
* ``match(target, slot_sets)``: the same part (same ``match_key``: spec endpoint id or component name) in other
  parents of the same task, created with the same defaults.

Defaults by kind (section 7.4 table): file ``remove``, region/symbol/endpoint ``stub``, component ``remove`` for
a whole-file component else ``stub``; allowlist ``[path]``; linked endpoints by kind; linked checks from
``evaluation.acceptance.checks_for_endpoints``; linked probes are the task's probes on the linked endpoints.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.db import transaction

from backend.generation import blobs
from backend.generation import identity
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.slot import Anchors
from backend.generation.contracts.slot import ContextPolicy
from backend.generation.contracts.slot import EndpointAddress
from backend.generation.contracts.slot import EndpointEntry
from backend.generation.contracts.slot import LinkedChecksDiff
from backend.generation.contracts.slot import SlotAddress
from backend.generation.contracts.slot import SlotInterface
from backend.generation.contracts.slot import Span
from backend.generation.contracts.slot import SymbolEntry
from backend.generation.contracts.slot import TargetValidation
from backend.generation.models import SlotSet
from backend.generation.models import SlotTarget
from backend.generation.slot.errors import AddressStaleError
from backend.generation.slot.errors import SlotInvalidError
from backend.generation.slot.index.languages import language_for
from backend.generation.slot.index.routes import route_key
from backend.generation.slot.part import ParentContext
from backend.generation.slot.part import Part
from backend.generation.slot.part import whole_file_component
from backend.generation.slot.types import SlotTargetCreateIn
from backend.generation.slot.types import SlotTargetIn

if TYPE_CHECKING:
    from backend.users.models import User

__all__ = [
    "ANCHOR_LINES",
    "LinkSet",
    "anchors_for",
    "auto_links",
    "create_target",
    "interface_of",
    "match",
    "match_key_of",
    "overlapping_targets",
    "preview",
    "resolve",
    "target_issues",
]

ANCHOR_LINES: Final = 3
PROMPT_OVERHEAD_TOKENS: Final = 400  # templates + output contract, for the cost hint only
OUTPUT_FACTOR: Final = 1.6  # expected output tokens per original token (the 4.3 slot_output_cap_tight rule)
_DEFAULT_HOLE: Final = {"file": "remove", "region": "stub", "symbol": "stub", "endpoint": "stub"}
_IMPORT_LINE_RE: Final = re.compile(r"^(?:import\s|from\s+\S+\s+import\s|const\s+\w+\s*=\s*require\()")
_METHOD_WORD_RE: Final = r"""["'`]{m}["'`]|\.{lm}\s*\("""
_HANDLER_KINDS: Final = frozenset({"route_handler", "function", "method"})
_TOP_KINDS: Final = frozenset({"function", "method", "class", "component", "route_handler"})


# ---------------------------------------------------------------------------
# Address resolution
# ---------------------------------------------------------------------------


def _lines(source: bytes) -> list[tuple[int, int]]:
    """``[(start, end_without_newline)]`` byte ranges of every line."""
    ranges: list[tuple[int, int]] = []
    start = 0
    while start <= len(source):
        newline = source.find(b"\n", start)
        if newline < 0:
            if start < len(source) or not ranges:
                ranges.append((start, len(source)))
            break
        end = newline - 1 if newline > start and source[newline - 1 : newline] == b"\r" else newline
        ranges.append((start, end))
        start = newline + 1
    return ranges


def _line_span(source: bytes, start_line: int, end_line: int) -> Span:
    lines = _lines(source)
    if start_line > end_line or end_line > len(lines):
        msg = f"lines {start_line}-{end_line} are outside the file ({len(lines)} lines)"
        raise SlotInvalidError(msg)
    start = lines[start_line - 1][0]
    end = lines[end_line - 1][1]
    return Span(start_line=start_line, end_line=end_line, start_byte=start, end_byte=max(start, end))


def _text_of(source: bytes, span: Span | None) -> str:
    data = source if span is None else source[span.start_byte : span.end_byte]
    return data.decode("utf-8", errors="replace")


def anchors_for(source: bytes, span: Span | None) -> Anchors:
    """``text_sha`` of the 3 lines before and after the span (``""`` at a file edge)."""
    if span is None:
        return Anchors(before_sha="", after_sha="")
    lines = _lines(source)
    before = lines[max(0, span.start_line - 1 - ANCHOR_LINES) : span.start_line - 1]
    after = lines[span.end_line : span.end_line + ANCHOR_LINES]

    def sha(chunk: list[tuple[int, int]]) -> str:
        if not chunk:
            return ""
        return identity.text_sha("\n".join(source[a:b].decode("utf-8", errors="replace") for a, b in chunk))

    return Anchors(before_sha=sha(before), after_sha=sha(after))


def _relocate(
    source: bytes,
    path: str,
    expected: str,
    anchors: Anchors | None,
    symbol: SymbolEntry | None,
    parent: ParentContext,
) -> Span | None:
    """Find the slice of ``source`` that hashes to ``expected`` (by symbol, then by anchors)."""
    candidates = [symbol] if symbol is not None else []
    candidates += [s for s in parent.index.symbols if s.path == path and s.content_sha == expected]
    for candidate in candidates:
        if candidate is not None and identity.text_sha(_text_of(source, candidate.span)) == expected:
            return candidate.span
    if anchors is None or not (anchors.before_sha or anchors.after_sha):
        return None
    total = len(_lines(source))
    for start_line in range(1, total + 1):
        span_probe = Span(start_line=start_line, end_line=start_line, start_byte=0, end_byte=0)
        if anchors_for(source, span_probe).before_sha != anchors.before_sha:
            continue
        for end_line in range(start_line, total + 1):
            span = _line_span(source, start_line, end_line)
            if anchors_for(source, span).after_sha != anchors.after_sha:
                continue
            if identity.text_sha(_text_of(source, span)) == expected:
                return span
    return None


def _endpoint_entry(parent: ParentContext, target_in: SlotTargetIn) -> EndpointEntry:
    endpoints = parent.index.endpoints
    wanted = target_in.endpoint
    found: EndpointEntry | None = None
    if wanted is not None and wanted.spec_endpoint_id:
        found = next((e for e in endpoints if e.spec_endpoint_id == wanted.spec_endpoint_id), None)
    if found is None and wanted is not None:
        key = (wanted.method.upper(), route_key(wanted.path))
        found = next((e for e in endpoints if (e.method, route_key(e.path)) == key), None)
    if found is None and target_in.symbol_id:
        found = next((e for e in endpoints if e.handler == target_in.symbol_id), None)
    if found is None:
        msg = "no endpoint of the parent matches the selection"
        raise SlotInvalidError(msg)
    return found


def _require_path(parent: ParentContext, path: str | None) -> str:
    if not path:
        msg = "path is required"
        raise SlotInvalidError(msg)
    if path not in parent.tree or not parent.tree.files[path].is_source:
        msg = f"{path} is not a source file of the parent"
        raise SlotInvalidError(msg)
    return path


def _address(parent: ParentContext, target_in: SlotTargetIn) -> tuple[SlotAddress, SymbolEntry | None, bool]:
    """``(address, symbol, whole_file_component)`` with ``content_sha`` computed from the parent's bytes."""
    kind = target_in.kind
    symbol: SymbolEntry | None = None
    endpoint: EndpointAddress | None = None
    span: Span | None = None
    if kind == "endpoint":
        entry = _endpoint_entry(parent, target_in)
        symbol = parent.symbol(entry.handler)
        if symbol is None or symbol.kind == "module":
            msg = f"the handler of {entry.method} {entry.path} could not be resolved to a symbol"
            raise SlotInvalidError(msg)
        endpoint = EndpointAddress(method=entry.method, path=entry.path, spec_endpoint_id=entry.spec_endpoint_id)
        path, span = symbol.path, symbol.span
    elif kind == "symbol":
        symbol = parent.symbol(target_in.symbol_id)
        if symbol is None:
            msg = f"unknown symbol {target_in.symbol_id!r}"
            raise SlotInvalidError(msg)
        if symbol.kind == "module":
            msg = "select the file (kind=file) instead of its <module> symbol"
            raise SlotInvalidError(msg)
        path, span = symbol.path, symbol.span
    elif kind == "component":
        component = next(
            (
                c
                for c in parent.index.components
                if (
                    target_in.component
                    and c.name == target_in.component
                    and (not target_in.path or c.path == target_in.path)
                )
                or (target_in.symbol_id and c.symbol == target_in.symbol_id)
                or (not target_in.component and not target_in.symbol_id and c.path == target_in.path)
            ),
            None,
        )
        if component is None:
            msg = "no component of the parent matches the selection"
            raise SlotInvalidError(msg)
        path = component.path
        symbol = parent.symbol(component.symbol)
        span = symbol.span if symbol is not None else None
    elif kind == "region":
        path = _require_path(parent, target_in.path)
        if target_in.span is None:
            msg = "a region needs a span"
            raise SlotInvalidError(msg)
        span = _line_span(parent.tree.read(path), target_in.span.start_line, target_in.span.end_line)
    else:
        path = _require_path(parent, target_in.path)
    source = parent.tree.read(path)
    content_sha = identity.text_sha(_text_of(source, span))
    if target_in.content_sha and target_in.content_sha != content_sha:
        relocated = _relocate(source, path, target_in.content_sha, target_in.anchors, symbol, parent)
        if relocated is None:
            msg = "the selection does not match the parent's content any more; reload the file and select again"
            raise AddressStaleError(msg, data={"path": path, "expected": target_in.content_sha})
        if kind == "region":
            span = relocated
        else:
            symbol = next((s for s in parent.index.symbols if s.path == path and s.span == relocated), symbol)
            span = relocated
        content_sha = target_in.content_sha
    address = SlotAddress(
        kind=kind,
        path=path,
        span=span,
        symbol_id=symbol.id if symbol is not None and kind in {"symbol", "endpoint", "component"} else None,
        endpoint=endpoint,
        content_sha=content_sha,
        anchors=anchors_for(source, span),
    )
    return address, symbol, whole_file_component(parent, address)


# ---------------------------------------------------------------------------
# Links: endpoints, checks, probes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkSet:
    endpoints: list[str]
    checks: list[str]
    probes: list[str]


def _overlaps(a: Span, b: Span) -> bool:
    return a.start_byte < b.end_byte and b.start_byte < a.end_byte


def _linked_endpoints(parent: ParentContext, address: SlotAddress, symbol: SymbolEntry | None) -> list[str]:
    endpoints = [e for e in parent.index.endpoints if e.spec_endpoint_id]
    handlers = {s.id: s for s in parent.index.symbols if s.id in {e.handler for e in endpoints}}
    found: set[str] = set()
    if address.kind == "file":
        found = {e.spec_endpoint_id for e in endpoints if e.handler.split("::", 1)[0] == address.path}  # type: ignore[misc]
    elif address.kind == "region" and address.span is not None:
        for e in endpoints:
            handler = handlers.get(e.handler)
            if handler is not None and handler.path == address.path and _overlaps(handler.span, address.span):
                found.add(e.spec_endpoint_id)  # type: ignore[arg-type]
    elif address.kind == "endpoint":
        found = {e.spec_endpoint_id for e in endpoints if e.handler == address.symbol_id}  # type: ignore[misc]
        if address.endpoint is not None and address.endpoint.spec_endpoint_id:
            found.add(address.endpoint.spec_endpoint_id)
    elif address.kind == "symbol" and symbol is not None:
        direct = {e.spec_endpoint_id for e in endpoints if e.handler == symbol.id}
        if direct:
            found = direct  # type: ignore[assignment]
        else:
            found = _call_graph_endpoints(parent, symbol, endpoints, handlers)
    elif address.kind == "component":
        found = _component_endpoints(parent, address)
    return sorted(e for e in found if e)


def _call_graph_endpoints(
    parent: ParentContext,
    symbol: SymbolEntry,
    endpoints: list[EndpointEntry],
    handlers: dict[str, SymbolEntry],
) -> set[str]:
    """One hop: handlers that call the symbol (``name(``) or are decorated with it (``@name``)."""
    name = re.escape(symbol.qualname.rsplit(".", 1)[-1])
    pattern = re.compile(rf"(?<![\w.])(?:\w+\.)?{name}\s*\(|@(?:\w+\.)?{name}\b")
    found: set[str] = set()
    for endpoint in endpoints:
        handler = handlers.get(endpoint.handler)
        if handler is None or handler.id == symbol.id:
            continue
        text = _text_of(parent.tree.read(handler.path), handler.span)
        decorated = any(re.search(rf"@(?:\w+\.)?{name}\b", d) for d in handler.decorators)
        if decorated or pattern.search(text):
            found.add(endpoint.spec_endpoint_id)  # type: ignore[arg-type]
    return found


def _component_endpoints(parent: ParentContext, address: SlotAddress) -> set[str]:
    """Spec endpoints the component calls (heuristic, section 7.4).

    An endpoint counts when its static path prefix (up to the first parameter) appears as a string literal in
    the component, with or without the stack's API prefix (components often call ``api("/todos")`` through a
    helper that prepends ``/api``); a non-GET endpoint also needs its method name in the component.
    """
    text = _text_of(parent.tree.read(address.path), address.span)
    stack = parent.slot_set.parent_run.stack_profile
    api_prefix = (((stack.content if stack is not None else {}) or {}).get("run_contract") or {}).get(
        "api_prefix",
    ) or "/api"
    found: set[str] = set()
    for endpoint in parent.task_content.get("endpoints", []):
        path = endpoint["path"]
        static = path.split("{", 1)[0]
        if not static or static == "/":
            continue
        variants = {static}
        if api_prefix and static.startswith(api_prefix + "/"):
            variants.add(static[len(api_prefix) :])
        follow = "" if "{" in path else r"(?=[?\"'`])"
        patterns = [re.compile(r"[\"'`]" + re.escape(v) + follow) for v in variants]
        if not any(p.search(text) for p in patterns):
            continue
        method = endpoint["method"].upper()
        if method != "GET":
            word = re.compile(_METHOD_WORD_RE.format(m=method, lm=method.lower()), re.I)
            if not word.search(text):
                continue
        found.add(endpoint["id"])
    return found


def _functional_only(parent: ParentContext, checks: list[str]) -> list[str]:
    categories = {c["id"]: c.get("category") for c in parent.task_content.get("acceptance", [])}
    return [c for c in checks if categories.get(c, "functional") == "functional"]


def auto_links(parent: ParentContext, address: SlotAddress, symbol: SymbolEntry | None) -> LinkSet:
    """The automatic links of a part (before validation)."""
    from backend.generation.evaluation import acceptance

    endpoints = _linked_endpoints(parent, address, symbol)
    checks = list(acceptance.checks_for_endpoints(parent.slot_set.task_version, endpoints)) if endpoints else []
    if address.kind == "component":
        checks = _functional_only(parent, checks)
    probes = sorted(
        probe["id"] for probe in parent.task_content.get("probes", []) if probe.get("endpoint") in set(endpoints)
    )
    return LinkSet(endpoints=endpoints, checks=sorted(dict.fromkeys(checks)), probes=probes)


# ---------------------------------------------------------------------------
# Interface, match key, overlap
# ---------------------------------------------------------------------------


def interface_of(parent: ParentContext, address: SlotAddress, symbol: SymbolEntry | None) -> SlotInterface:
    text = parent.tree.text(address.path)
    imports = [line.strip() for line in text.splitlines() if _IMPORT_LINE_RE.match(line.strip())]
    in_file = [s for s in parent.index.symbols if s.path == address.path and s.kind in _TOP_KINDS and s.parent is None]
    if address.kind == "file":
        exported = [s.qualname for s in in_file]
    elif address.kind == "region" and address.span is not None:
        exported = [
            s.qualname
            for s in in_file
            if address.span.start_byte <= s.span.start_byte and s.span.end_byte <= address.span.end_byte
        ]
    elif symbol is not None:
        exported = [symbol.qualname.rsplit(".", 1)[-1]]
    else:
        exported = [s.qualname for s in in_file]
    return SlotInterface(
        signature=symbol.signature if symbol is not None else "",
        decorators=list(symbol.decorators) if symbol is not None else [],
        imports_in_scope=imports,
        exported_names=exported,
    )


def match_key_of(parent: ParentContext, address: SlotAddress, symbol: SymbolEntry | None) -> str:
    """``endpoint:<spec endpoint id>`` or ``component:<name>`` (``""`` when the part has neither)."""
    if address.kind == "endpoint" and address.endpoint is not None and address.endpoint.spec_endpoint_id:
        return f"endpoint:{address.endpoint.spec_endpoint_id}"
    if address.kind == "symbol" and symbol is not None:
        spec_ids = sorted(
            {e.spec_endpoint_id for e in parent.index.endpoints if e.handler == symbol.id and e.spec_endpoint_id},
        )
        if len(spec_ids) == 1:
            return f"endpoint:{spec_ids[0]}"
    if address.kind == "component":
        component = next(
            (c for c in parent.index.components if c.path == address.path and c.symbol == address.symbol_id),
            None,
        )
        if component is not None:
            return f"component:{component.name}"
    return ""


def _covers(address: SlotAddress) -> tuple[str, int, int]:
    if address.span is None:
        return address.path, 0, 2**62
    return address.path, address.span.start_byte, address.span.end_byte


def overlapping_targets(slot_set: SlotSet, address: SlotAddress, *, exclude: str | None = None) -> list[str]:
    """Ids of the slot set's other targets whose part overlaps ``address``."""
    path, start, end = _covers(address)
    found: list[str] = []
    for target in slot_set.targets.all().order_by("created_at", "id"):
        if exclude and str(target.pk) == exclude:
            continue
        other_path, other_start, other_end = _covers(SlotAddress.model_validate(target.address))
        if other_path == path and start < other_end and other_start < end:
            found.append(str(target.pk))
    return found


# ---------------------------------------------------------------------------
# Resolve, preview, create, match
# ---------------------------------------------------------------------------


@dataclass
class Resolved:
    part: Part
    symbol: SymbolEntry | None
    links: LinkSet
    interface: SlotInterface
    match_key: str
    defaults: list[str]  # names of the fields filled with kind defaults

    @property
    def checks_source(self) -> str:
        return "auto"


def resolve(
    slot_set: SlotSet,
    target_in: SlotTargetIn | dict[str, Any],
    parent: ParentContext | None = None,
) -> Resolved:
    """Resolve a selection into a verified part with its defaults and automatic links."""
    data = target_in if isinstance(target_in, SlotTargetIn) else SlotTargetIn.model_validate(target_in)
    context = parent or ParentContext(slot_set)
    address, symbol, whole = _address(context, data)
    defaults: list[str] = []
    hole_mode = data.default_hole_mode
    if hole_mode is None:
        defaults.append("default_hole_mode")
        hole_mode = ("remove" if whole else "stub") if address.kind == "component" else _DEFAULT_HOLE[address.kind]
    if address.kind == "file" and hole_mode == "stub" and not context.symbol(f"{address.path}::<module>"):
        hole_mode = "remove"
    policy = data.default_context_policy
    if policy is None:
        defaults.append("default_context_policy")
        policy = ContextPolicy()
    if data.allowlist is None:
        defaults.append("allowlist")
    if data.edit_policy is None:
        defaults.append("edit_policy")
    if data.default_keep_docstring is None:
        defaults.append("default_keep_docstring")
    allowlist = sorted(dict.fromkeys(data.allowlist or [address.path]))
    if address.path not in allowlist:
        msg = "the allowlist must contain the slot file"
        raise SlotInvalidError(msg)
    part = Part(
        parent=context,
        address=address,
        language=language_for(address.path) or (symbol.language if symbol else ""),
        default_hole_mode=hole_mode,
        default_keep_docstring=bool(data.default_keep_docstring),
        default_context_policy=policy,
        edit_policy=data.edit_policy or "revert",
        allowlist=allowlist,
        whole_file_component=whole,
    )
    links = auto_links(context, address, symbol)
    part.linked_endpoints = links.endpoints
    return Resolved(
        part=part,
        symbol=symbol,
        links=links,
        interface=interface_of(context, address, symbol),
        match_key=match_key_of(context, address, symbol),
        defaults=defaults,
    )


def _target_hash(slot_set: SlotSet, resolved: Resolved) -> str:
    return identity.target_hash(
        parent_tree=slot_set.frozen_tree_hash,
        address=resolved.part.address.identity(),
        original_sha=resolved.part.address.content_sha,
        allowlist=resolved.part.allowlist,
        interface=resolved.interface.model_dump(mode="json"),
    )


def _cost_hint(resolved: Resolved, context_tokens: int, model_id: str | None) -> dict[str, Any]:
    from backend.generation.slot.context import estimate_tokens
    from backend.llm_models.models import LLMModel

    tokens_in = context_tokens + PROMPT_OVERHEAD_TOKENS
    tokens_out = math.ceil(OUTPUT_FACTOR * estimate_tokens(resolved.part.original_text))
    hint: dict[str, Any] = {"model": model_id, "tokens_in": tokens_in, "tokens_out": tokens_out}
    model = LLMModel.objects.filter(model_id=model_id).first() if model_id else None
    if model is None:
        hint.update({"per_variant_usd": None, "per_30_usd": None})
        return hint
    per_variant = tokens_in * model.input_price_per_token + tokens_out * model.output_price_per_token
    hint.update({"per_variant_usd": round(per_variant, 6), "per_30_usd": round(per_variant * 30, 4)})
    return hint


def _checks_and_probes(resolved: Resolved, data: SlotTargetIn) -> tuple[list[str], str, LinkedChecksDiff, list[str]]:
    auto = resolved.links.checks
    checks = sorted(dict.fromkeys(data.linked_checks)) if data.linked_checks is not None else auto
    diff = LinkedChecksDiff(added=sorted(set(checks) - set(auto)), removed=sorted(set(auto) - set(checks)))
    source = "edited" if diff.added or diff.removed else "auto"
    probes = sorted(dict.fromkeys(data.linked_probes)) if data.linked_probes is not None else resolved.links.probes
    return checks, source, diff, probes


def preview(slot_set: SlotSet, target_in: SlotTargetIn | dict[str, Any]) -> dict[str, Any]:
    """Hole, context, leak and cost hint of a prospective target."""
    from backend.generation.slot import leak
    from backend.generation.slot.context import materialize

    data = target_in if isinstance(target_in, SlotTargetIn) else SlotTargetIn.model_validate(target_in)
    resolved = resolve(slot_set, data)
    part = resolved.part
    context = materialize(part, part.default_context_policy)
    report = leak.scan(part)
    checks, source, diff, probes = _checks_and_probes(resolved, data)
    issues = leak.leak_issues(report)
    overlapping = overlapping_targets(slot_set, part.address)
    if overlapping:
        issues.append(
            make_issue(
                "overlapping_targets",
                message=f"overlaps {len(overlapping)} other target(s) of this parent",
                data={"targets": overlapping},
            ),
        )
    return {
        "address": part.address.model_dump(mode="json"),
        "kind": part.address.kind,
        "language": part.language,
        "match_key": resolved.match_key,
        "default_hole_mode": part.default_hole_mode,
        "default_keep_docstring": part.default_keep_docstring,
        "default_context_policy": part.default_context_policy.model_dump(mode="json"),
        "edit_policy": part.edit_policy,
        "allowlist": part.allowlist,
        "defaults": resolved.defaults,
        "interface": resolved.interface.model_dump(mode="json"),
        "original_text": part.original_text,
        "hole_applied_text": context.hole.holed_text,
        "hole_removes_file": context.hole.removed,
        "resolved_context": context.resolved.model_dump(mode="json"),
        "linked_endpoints": resolved.links.endpoints,
        "linked_checks": checks,
        "linked_checks_source": source,
        "linked_checks_diff": diff.model_dump(mode="json"),
        "linked_probes": probes,
        "leak": report.model_dump(mode="json"),
        "target_hash": _target_hash(slot_set, resolved),
        "cost_hint": _cost_hint(resolved, context.resolved.tokens, data.cost_model),
        "issues": [issue.model_dump(mode="json") for issue in issues],
    }


def create_target(
    slot_set: SlotSet,
    target_in: SlotTargetIn | dict[str, Any],
    *,
    user: User | None,
    validate: bool = True,
) -> SlotTarget:
    """Create a target and queue its validation (the same part twice returns the existing target)."""
    from backend.generation.slot import leak
    from backend.generation.slot import validate as slot_validate

    data = (
        target_in
        if isinstance(target_in, SlotTargetCreateIn)
        else SlotTargetCreateIn.model_validate(
            target_in if isinstance(target_in, dict) else target_in.model_dump(),
        )
    )
    resolved = resolve(slot_set, data)
    part = resolved.part
    target_hash = _target_hash(slot_set, resolved)
    existing = SlotTarget.objects.filter(slot_set=slot_set, target_hash=target_hash).first()
    if existing is not None:
        return existing
    checks, source, diff, probes = _checks_and_probes(resolved, data)
    report = leak.scan(part)
    original_blob = blobs.put_text(part.original_text)
    label = data.label or _default_label(part, resolved.symbol)
    with transaction.atomic():
        target = SlotTarget(
            slot_set=slot_set,
            label=label[:200],
            kind=part.address.kind,
            address=part.address.model_dump(mode="json"),
            match_key=resolved.match_key,
            language=part.language[:24],
            original_sha=part.address.content_sha,
            original_blob=original_blob,
            default_hole_mode=part.default_hole_mode,
            default_keep_docstring=part.default_keep_docstring,
            default_context_policy=part.default_context_policy.model_dump(mode="json"),
            edit_policy=part.edit_policy,
            allowlist=part.allowlist,
            interface=resolved.interface.model_dump(mode="json"),
            linked_endpoints=resolved.links.endpoints,
            linked_checks=checks,
            linked_checks_source=source,
            linked_checks_diff=diff.model_dump(mode="json"),
            linked_probes=probes,
            validation=TargetValidation(status="pending").model_dump(mode="json"),
            leak=report.model_dump(mode="json"),
            created_by=user,
        )
        target.save()
        if validate:
            slot_validate.queue_validation(target)
    return target


def _default_label(part: Part, symbol: SymbolEntry | None) -> str:
    address = part.address
    if address.kind == "endpoint" and address.endpoint is not None:
        return f"{address.endpoint.method} {address.endpoint.path}"
    if address.kind == "component":
        return f"component {address.symbol_id.split('::', 1)[-1] if address.symbol_id else address.path}"
    if address.kind == "symbol" and symbol is not None:
        return f"{symbol.qualname} in {address.path}"
    if address.kind == "region" and address.span is not None:
        return f"{address.path}:{address.span.start_line}-{address.span.end_line}"
    return address.path


def _counterpart(parent: ParentContext, target: SlotTarget) -> dict[str, Any] | None:
    """The selection of the same part in another parent (``None`` when it has none)."""
    kind, _, key = target.match_key.partition(":")
    if kind == "endpoint":
        entry = next((e for e in parent.index.endpoints if e.spec_endpoint_id == key), None)
        if entry is None:
            return None
        if target.kind == "symbol":
            return {"kind": "symbol", "symbol_id": entry.handler}
        return {"kind": "endpoint", "endpoint": {"method": entry.method, "path": entry.path, "spec_endpoint_id": key}}
    if kind == "component":
        component = next((c for c in parent.index.components if c.name == key), None)
        if component is None:
            return None
        return {"kind": "component", "component": component.name, "path": component.path}
    return None


def match(
    target: SlotTarget,
    slot_sets: list[SlotSet] | str,
    *,
    user: User | None = None,
) -> dict[str, Any]:
    """The same part (by ``match_key``) in other parents, created with the same defaults."""
    if slot_sets == "same_task":
        candidates = list(
            SlotSet.objects.filter(task_version=target.slot_set.task_version)
            .exclude(pk=target.slot_set_id)
            .order_by(
                "created_at",
                "id",
            ),
        )
    else:
        candidates = [s for s in slot_sets if s.pk != target.slot_set_id]  # type: ignore[union-attr]
    created: list[SlotTarget] = []
    unmatched: list[dict[str, str]] = []
    for slot_set in candidates:
        if not target.match_key:
            unmatched.append({"slot_set": str(slot_set.pk), "reason": "no_match_key"})
            continue
        if slot_set.task_version_id != target.slot_set.task_version_id:
            unmatched.append({"slot_set": str(slot_set.pk), "reason": "different_task"})
            continue
        selection = _counterpart(ParentContext(slot_set), target)
        if selection is None:
            unmatched.append({"slot_set": str(slot_set.pk), "reason": "not_found"})
            continue
        own_path = SlotAddress.model_validate(target.address).path
        extra = [p for p in (target.allowlist or []) if p != own_path]
        selection.update(
            {
                "default_hole_mode": target.default_hole_mode,
                "default_keep_docstring": target.default_keep_docstring,
                "default_context_policy": target.default_context_policy or None,
                "edit_policy": target.edit_policy,
                "label": target.label,
            },
        )
        if extra:
            unmatched.append({"slot_set": str(slot_set.pk), "reason": "allowlist_not_mappable"})
            continue
        try:
            created.append(create_target(slot_set, selection, user=user))
        except SlotInvalidError as exc:
            unmatched.append({"slot_set": str(slot_set.pk), "reason": f"invalid: {exc.message}"})
    return {"created": created, "unmatched": unmatched}


def target_issues(target: SlotTarget, study_kind: str = "exploratory") -> list[Issue]:
    """The issues a target carries now: leaks, uninformative validation, overlaps."""
    from backend.generation.slot import leak
    from backend.generation.slot.validate import validation_issues

    issues = leak.leak_issues(target.leak, study_kind)
    issues += validation_issues(target, study_kind)
    overlapping = overlapping_targets(
        target.slot_set,
        SlotAddress.model_validate(target.address),
        exclude=str(target.pk),
    )
    if overlapping:
        issues.append(
            make_issue(
                "overlapping_targets",
                study_kind,  # type: ignore[arg-type]
                message=f"overlaps {len(overlapping)} other target(s) of this parent",
                data={"targets": overlapping},
            ),
        )
    return issues
