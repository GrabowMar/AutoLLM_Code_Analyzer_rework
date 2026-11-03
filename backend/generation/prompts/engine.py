"""Sandboxed, deterministic Jinja renderer with source-span tracking (engine ``jinja2-sandbox@1``, section 9.2).

Rules (spec section 9.2):

* ``jinja2.sandbox.SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, trim_blocks=True,
  lstrip_blocks=True, keep_trailing_newline=True)``;
* filters are restricted to the whitelist ``FILTERS`` (implemented here, so their output does not change with
  the Jinja release), and the only global is ``range``;
* no attribute access: ``a.b`` is an item lookup on mappings only; anything else (``"".__class__``,
  ``task.title.upper``) raises ``SecurityError``. There is no loader, so ``include``/``import``/``extends``
  fail;
* the output is NFC- and LF-normalized and ends with exactly one newline.

**Source spans.** Every string the render context takes from a library entity is a :class:`SourcedText`
(a ``str`` that remembers the entity owning it and, for a rendered fragment, the owned spans inside it). The
environment's ``finalize`` hook wraps each such value in private-use sentinels as it is written; after
rendering the sentinels are parsed out into :class:`~backend.generation.contracts.manifest.SourceSpan` rows.
Template literal text, and any value that is not sourced, belongs to the template's own owner. The spans of a
result partition the text: contiguous, never overlapping, covering every character. A whitelisted filter
applied to sourced input keeps the owner when all of the input shares one owner.

Rendering is deterministic: the same template and context give byte-identical text and spans.
"""

from __future__ import annotations

import functools
import json
import math
import re
import textwrap
import unicodedata
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from jinja2 import StrictUndefined
from jinja2 import TemplateError
from jinja2 import Undefined
from jinja2 import meta as jinja_meta
from jinja2 import nodes
from jinja2.exceptions import SecurityError
from jinja2.exceptions import UndefinedError
from jinja2.runtime import LoopContext
from jinja2.sandbox import SandboxedEnvironment
from jinja2.sandbox import safe_range

from backend.generation import identity
from backend.generation.contracts.manifest import SourceSpan

if TYPE_CHECKING:
    from backend.generation.contracts.library import PromptSetContent
    from backend.generation.contracts.library import PromptTemplateContent

__all__ = [
    "ENGINE",
    "FILTERS",
    "ROLE_ORDER",
    "Owner",
    "RenderError",
    "RenderedText",
    "SourcedText",
    "Stimulus",
    "TemplateLoader",
    "estimate_tokens",
    "find_variables",
    "parse_template",
    "preview_prompt_hash",
    "render",
    "render_set",
    "render_text",
    "used_filters",
]

ENGINE: Final = "jinja2-sandbox@1"
# Sentinels written around sourced values while rendering (Unicode private-use characters).
_OPEN, _SEP, _CLOSE = "", "", ""
_SENTINEL_RE: Final = re.compile(f"[{_OPEN}{_SEP}{_CLOSE}]")
# Stimulus order of a prompt set (deterministic output; each harness picks the roles it uses).
ROLE_ORDER: Final = (
    "system",
    "brief",
    "user",
    "slot_single_shot_system",
    "slot_single_shot_user",
    "slot_agent_brief",
)
# Characters per token of the preview estimate (CHANGES WP-01 A1: the tokenizer extra is optional).
_CHARS_PER_TOKEN: Final = 3.6
_MAX_NESTING: Final = 32


class RenderError(ValueError):
    """A template that does not parse, reads an undefined variable or breaks the sandbox."""

    def __init__(self, message: str, *, kind: str = "render", line: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind  # "syntax" | "undefined" | "security" | "render"
        self.line = line


@dataclass(frozen=True)
class Owner:
    """The library entity that owns a span of rendered text (a ``SourceSpan`` without positions)."""

    template_ref: str
    owner_kind: str
    owner_ref: str
    role: str | None = None

    def span(self, start: int, end: int) -> SourceSpan:
        return SourceSpan.model_validate(
            {
                "template_ref": self.template_ref,
                "owner_kind": self.owner_kind,
                "owner_ref": self.owner_ref,
                "role": self.role,
                "char_start": start,
                "char_end": end,
            },
        )

    @classmethod
    def of(cls, span: SourceSpan) -> Owner:
        return cls(span.template_ref, span.owner_kind, span.owner_ref, span.role)


class SourcedText(str):  # noqa: SLOT000 - carries its owner as instance attributes
    """A ``str`` owned by a library entity, optionally with owned sub-spans (a rendered fragment)."""

    owner: Owner
    spans: tuple[tuple[int, int, Owner], ...]

    def __new__(cls, text: str, owner: Owner, spans: Iterable[tuple[int, int, Owner]] = ()) -> SourcedText:  # noqa: PYI034
        raw = str(text)
        clean = _SENTINEL_RE.sub("", raw)
        obj = super().__new__(cls, clean)
        obj.owner = owner
        # Sub-spans are positions in ``text``; they are dropped when the sentinel scrub moved characters.
        obj.spans = tuple(spans) if clean == raw else ()
        return obj

    def __reduce__(self) -> Any:
        return (SourcedText, (str(self), self.owner, self.spans))

    @classmethod
    def from_rendered(cls, rendered: RenderedText, owner: Owner, *, strip_final_newline: bool = True) -> SourcedText:
        """A rendered fragment as a context value: its trailing newline dropped, its spans kept."""
        text = rendered.text
        if strip_final_newline and text.endswith("\n"):
            text = text[:-1]
        spans = [
            (span.char_start, min(span.char_end, len(text)), Owner.of(span))
            for span in rendered.sources
            if min(span.char_end, len(text)) > span.char_start
        ]
        return cls(text, owner, spans)

    def pieces(self) -> list[tuple[str, Owner]]:
        """``(text, owner)`` pieces covering the whole string."""
        text = str(self)
        if not self.spans:
            return [(text, self.owner)] if text else []
        out: list[tuple[str, Owner]] = []
        cursor = 0
        for start, end, owner in sorted(self.spans, key=lambda item: item[0]):
            begin = max(start, cursor)
            if begin > cursor:
                out.append((text[cursor:begin], self.owner))
            if end > begin:
                out.append((text[begin:end], owner))
            cursor = max(cursor, end)
        if cursor < len(text):
            out.append((text[cursor:], self.owner))
        return [(piece, owner) for piece, owner in out if piece]


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

    @property
    def sha(self) -> str:
        return identity.text_sha(self.text)

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def est_tokens(self) -> int:
        return estimate_tokens(self.text)

    def as_dict(self) -> dict[str, Any]:
        """``{role, text, sha, chars, est_tokens, template_ref, sources}`` (the render API's stimulus)."""
        return {
            "role": self.role,
            "template_ref": self.template_ref,
            "text": self.text,
            "sha": self.sha,
            "chars": self.chars,
            "est_tokens": self.est_tokens,
            "sources": [span.model_dump(mode="json") for span in self.sources],
        }


def estimate_tokens(text: str) -> int:
    """Preview token estimate ``ceil(chars / 3.6)`` (WP-11's estimator may use a real tokenizer)."""
    return math.ceil(len(text) / _CHARS_PER_TOKEN) if text else 0


# =====================================================================================
# Filters (whitelist; each keeps the owner of sourced input)
# =====================================================================================


def _collect_owners(value: Any, found: set[Owner], depth: int = 0) -> bool:
    """Collect the owners of every string leaf; ``False`` when a non-empty unsourced string is found."""
    if depth > _MAX_NESTING:
        return False
    if isinstance(value, SourcedText):
        found.add(value.owner)
        found.update(owner for _, _, owner in value.spans)
        return True
    if isinstance(value, str):
        return not value
    if isinstance(value, Mapping):
        return all(_collect_owners(item, found, depth + 1) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_collect_owners(item, found, depth + 1) for item in value)
    return True


def _common_owner(value: Any) -> Owner | None:
    found: set[Owner] = set()
    if not _collect_owners(value, found):
        return None
    return next(iter(found)) if len(found) == 1 else None


def _keeps_owner(func: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(func)
    def wrapper(value: Any, *args: Any, **kwargs: Any) -> Any:
        result = func(value, *args, **kwargs)
        if isinstance(result, str) and not isinstance(result, SourcedText) and result:
            owner = _common_owner(value)
            if owner is not None:
                return SourcedText(result, owner)
        return result

    return wrapper


def _text(value: Any) -> str:
    if isinstance(value, Undefined):
        return str(value)  # StrictUndefined raises here
    return "" if value is None else str(value)


def _seq(value: Any) -> list[Any]:
    if isinstance(value, Undefined):
        return list(value)  # StrictUndefined raises here
    if isinstance(value, (Mapping, str)):
        return list(value)
    return list(value or [])


def _item(value: Any, attribute: str | None) -> Any:
    if attribute is None:
        return value
    for part in str(attribute).split("."):
        value = value.get(part) if isinstance(value, Mapping) else None
    return value


@_keeps_owner
def _f_indent(value: Any, width: int | str = 4, first: bool = False, blank: bool = False) -> str:
    prefix = width if isinstance(width, str) else " " * int(width)
    lines = _text(value).split("\n")
    out = []
    for index, line in enumerate(lines):
        if (index == 0 and not first) or (not line and not blank):
            out.append(line)
        else:
            out.append(prefix + line)
    return "\n".join(out)


@_keeps_owner
def _f_join(value: Any, separator: str = "", attribute: str | None = None) -> str:
    return str(separator).join(_text(_item(item, attribute)) for item in _seq(value))


@_keeps_owner
def _f_upper(value: Any) -> str:
    return _text(value).upper()


@_keeps_owner
def _f_lower(value: Any) -> str:
    return _text(value).lower()


@_keeps_owner
def _f_title(value: Any) -> str:
    return _text(value).title()


@_keeps_owner
def _f_trim(value: Any, chars: str | None = None) -> str:
    return _text(value).strip(chars)


@_keeps_owner
def _f_wordwrap(value: Any, width: int = 79, break_long_words: bool = True, wrapstring: str = "\n") -> str:
    lines = _text(value).split("\n")
    wrapped = [
        textwrap.fill(line, width=int(width), break_long_words=bool(break_long_words), break_on_hyphens=False)
        if line.strip()
        else line
        for line in lines
    ]
    return str(wrapstring).join(part.replace("\n", str(wrapstring)) for part in wrapped)


@_keeps_owner
def _f_replace(value: Any, old: str, new: str, count: int | None = None) -> str:
    text = _text(value)
    if count is None:
        return text.replace(str(old), str(new))
    return text.replace(str(old), str(new), int(count))


def _f_length(value: Any) -> int:
    return len(value) if isinstance(value, str) else len(_seq(value))


def _f_sort(
    value: Any,
    reverse: bool = False,
    case_sensitive: bool = False,
    attribute: str | None = None,
) -> list[Any]:
    def key(item: Any) -> tuple[int, Any, str]:
        target = _item(item, attribute)
        if isinstance(target, str):
            return (0, target if case_sensitive else target.lower(), str(target))
        if isinstance(target, (int, float)) and not isinstance(target, bool):
            return (1, float(target), "")
        return (2, json.dumps(target, sort_keys=True, default=str), "")

    return sorted(_seq(value), key=key, reverse=bool(reverse))


def _f_default(value: Any, default_value: Any = "", boolean: bool = False) -> Any:
    if isinstance(value, Undefined) or (boolean and not value):
        return default_value
    return value


@_keeps_owner
def _f_tojson(value: Any, indent: int | None = None) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=indent)


@_keeps_owner
def _f_bullet_list(value: Any, marker: str = "- ", attribute: str | None = None) -> str:
    return "\n".join(f"{marker}{_text(_item(item, attribute))}" for item in _seq(value))


_ACCESS: Final = {"public": "anyone", "user": "signed-in users", "owner": "the owner", "admin": "administrators"}
_COLUMN_TITLES: Final = {"method": "Method", "path": "Path", "auth": "Access", "id": "Id", "status": "Success"}


@_keeps_owner
def _f_endpoint_table(value: Any, columns: Sequence[str] = ("method", "path", "auth")) -> str:
    """A Markdown table of task endpoints (columns from ``method``, ``path``, ``auth``, ``id``, ``status``)."""
    cols = [col for col in columns if col in _COLUMN_TITLES]
    rows = [
        "| " + " | ".join(_COLUMN_TITLES[col] for col in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for endpoint in _seq(value):
        if not isinstance(endpoint, Mapping):
            continue
        cells = []
        for col in cols:
            if col == "auth":
                auth = _text(endpoint.get("auth"))
                cells.append(_ACCESS.get(auth, auth))
            elif col == "status":
                cells.append(_text(endpoint.get("success_status")))
            else:
                cells.append(_text(endpoint.get(col)).replace("|", "\\|"))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


FILTERS: Final[Mapping[str, Callable[..., Any]]] = {
    "indent": _f_indent,
    "join": _f_join,
    "upper": _f_upper,
    "lower": _f_lower,
    "title": _f_title,
    "trim": _f_trim,
    "wordwrap": _f_wordwrap,
    "replace": _f_replace,
    "length": _f_length,
    "sort": _f_sort,
    "default": _f_default,
    "tojson": _f_tojson,
    "bullet_list": _f_bullet_list,
    "endpoint_table": _f_endpoint_table,
}


# =====================================================================================
# Environment
# =====================================================================================

_REGISTRY: ContextVar[list[SourcedText] | None] = ContextVar("prompt_render_registry", default=None)


def _finalize(value: Any) -> Any:
    registry = _REGISTRY.get()
    if registry is not None and isinstance(value, SourcedText) and value:
        registry.append(value)
        return f"{_OPEN}{len(registry) - 1}{_SEP}{value}{_CLOSE}"
    return value


class _PromptEnvironment(SandboxedEnvironment):
    """The ``jinja2-sandbox@1`` environment: item lookup only, whitelisted filters, ``range`` only."""

    def getattr(self, obj: Any, attribute: str) -> Any:
        if isinstance(obj, Undefined):
            return self.undefined(obj=obj, name=attribute)
        if isinstance(obj, LoopContext) and not attribute.startswith("_"):
            return super().getattr(obj, attribute)  # loop.index, loop.first, ... (sandbox-checked)
        if isinstance(obj, Mapping):
            try:
                return obj[attribute]
            except (KeyError, TypeError):
                return self.undefined(obj=obj, name=attribute)
        return self.unsafe_undefined(obj, attribute)

    def getitem(self, obj: Any, argument: Any) -> Any:  # noqa: PLR0911 - one return per lookup case
        if isinstance(obj, Undefined):
            return self.undefined(obj=obj, name=argument)
        if isinstance(obj, Mapping):
            try:
                return obj[argument]
            except (KeyError, TypeError):
                return self.undefined(obj=obj, name=argument)
        if isinstance(obj, (list, tuple, str)) and isinstance(argument, (int, slice)):
            try:
                return obj[argument]
            except IndexError:
                return self.undefined(obj=obj, name=argument)
        if isinstance(argument, str):
            return self.unsafe_undefined(obj, argument)
        return self.undefined(obj=obj, name=argument)


@functools.cache
def _environment() -> _PromptEnvironment:
    env = _PromptEnvironment(
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        finalize=_finalize,
        loader=None,
    )
    env.filters = dict(FILTERS)
    env.globals = {"range": safe_range}
    return env


@functools.lru_cache(maxsize=1024)
def parse_template(template: str) -> nodes.Template:
    """The parsed AST of ``template`` (raises :class:`RenderError` of kind ``syntax``)."""
    try:
        return _environment().parse(template)
    except TemplateError as exc:
        raise RenderError(str(exc), kind="syntax", line=getattr(exc, "lineno", None)) from exc


@functools.lru_cache(maxsize=1024)
def _compiled(template: str) -> Any:
    parse_template(template)  # syntax errors become RenderError
    unknown = used_filters(template) - set(FILTERS)
    if unknown:
        msg = f"filter(s) not allowed: {', '.join(sorted(unknown))} (whitelist: {', '.join(FILTERS)})"
        raise RenderError(msg, kind="syntax")
    try:
        return _environment().from_string(template)
    except TemplateError as exc:
        raise RenderError(str(exc), kind="syntax", line=getattr(exc, "lineno", None)) from exc


def find_variables(template: str) -> set[str]:
    """Top-level context variables the template reads (Jinja's "undeclared variables")."""
    try:
        found = jinja_meta.find_undeclared_variables(parse_template(template))
    except TemplateError as exc:  # e.g. an unknown filter, found while compiling
        raise RenderError(str(exc), kind="syntax", line=getattr(exc, "lineno", None)) from exc
    return set(found) - set(_environment().globals)


def used_filters(template: str) -> set[str]:
    """The filter names the template applies."""
    return {node.name for node in parse_template(template).find_all(nodes.Filter)}


# =====================================================================================
# Rendering
# =====================================================================================


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def _parse_output(raw: str, registry: list[SourcedText], root: Owner) -> list[tuple[str, Owner]]:
    pieces: list[tuple[str, Owner]] = []
    cursor = 0
    while (start := raw.find(_OPEN, cursor)) >= 0:
        if start > cursor:
            pieces.append((raw[cursor:start], root))
        sep = raw.find(_SEP, start)
        end = raw.find(_CLOSE, sep) if sep >= 0 else -1
        if sep < 0 or end < 0:
            msg = "internal error: malformed source sentinels in the rendered output"
            raise RenderError(msg)
        value = registry[int(raw[start + 1 : sep])]
        if raw[sep + 1 : end] != str(value):
            msg = "internal error: a sourced value was altered while rendering"
            raise RenderError(msg)
        pieces.extend(value.pieces())
        cursor = end + 1
    if cursor < len(raw):
        pieces.append((raw[cursor:], root))
    if any(_SENTINEL_RE.search(piece) for piece, _ in pieces):
        msg = "internal error: stray source sentinel in the rendered output"
        raise RenderError(msg)
    return pieces


def _assemble(pieces: list[tuple[str, Owner]], root: Owner) -> RenderedText:
    normalized = [(text, owner) for text, owner in ((_normalize(t), o) for t, o in pieces) if text]
    joined = "".join(text for text, _ in normalized)
    if unicodedata.normalize("NFC", joined) != joined:
        # A combining character at a piece boundary: the pieces cannot keep separate owners.
        normalized = [(unicodedata.normalize("NFC", joined), root)]
    # Exactly one trailing newline, owned by the template.
    while normalized and normalized[-1][0].endswith("\n"):
        text, owner = normalized[-1]
        stripped = text.rstrip("\n")
        if stripped:
            normalized[-1] = (stripped, owner)
            break
        normalized.pop()
    normalized.append(("\n", root))
    merged: list[tuple[str, Owner]] = []
    for text, owner in normalized:
        if merged and merged[-1][1] == owner:
            merged[-1] = (merged[-1][0] + text, owner)
        else:
            merged.append((text, owner))
    spans = []
    offset = 0
    for text, owner in merged:
        spans.append(owner.span(offset, offset + len(text)))
        offset += len(text)
    return RenderedText(text="".join(text for text, _ in merged), sources=tuple(spans))


def _wrap_error(exc: Exception) -> RenderError:
    if isinstance(exc, RenderError):
        return exc
    if isinstance(exc, SecurityError):
        return RenderError(str(exc), kind="security")
    if isinstance(exc, UndefinedError):
        return RenderError(str(exc), kind="undefined")
    return RenderError(f"{type(exc).__name__}: {exc}", kind="render")


def render_text(template: str, context: Mapping[str, Any], *, owner: Owner) -> RenderedText:
    """Render raw template text; literal text belongs to ``owner``."""
    if _SENTINEL_RE.search(template):
        msg = "the template contains reserved private-use characters (U+E000-U+E002)"
        raise RenderError(msg, kind="syntax")
    compiled = _compiled(template)
    registry: list[SourcedText] = []
    token = _REGISTRY.set(registry)
    try:
        raw = compiled.render(dict(context))
    except Exception as exc:  # every template failure becomes a RenderError
        raise _wrap_error(exc) from exc
    finally:
        _REGISTRY.reset(token)
    return _assemble(_parse_output(raw, registry, owner), owner)


def _content_dict(template_content: PromptTemplateContent | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(template_content, Mapping):
        return dict(template_content)
    return template_content.model_dump(mode="json")


def render(
    template_content: PromptTemplateContent | Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    template_ref: str = "draft",
    owner: Owner | None = None,
) -> RenderedText:
    """Render one template (NFC/LF output, StrictUndefined, no attribute access).

    Only the template's declared ``variables`` are passed in, so a template that reads an undeclared
    variable fails with ``StrictUndefined``, as lint reports it. Literal text belongs to ``owner`` (default:
    the template itself, ``owner_kind="prompt"``).
    """
    content = _content_dict(template_content)
    engine = content.get("engine", ENGINE)
    if engine != ENGINE:
        msg = f"unsupported template engine {engine!r} (expected {ENGINE})"
        raise RenderError(msg, kind="syntax")
    scoped = {name: context[name] for name in content.get("variables") or () if name in context}
    root = owner or Owner(template_ref, "prompt", template_ref, content.get("role"))
    return render_text(str(content.get("template", "")), scoped, owner=root)


# pinned PromptTemplate ref -> its content
TemplateLoader = Callable[[str], Mapping[str, Any]]


def _library_loader(ref: str) -> Mapping[str, Any]:
    from backend.generation.library import service

    return service.resolve_ref(ref, kind="prompt").content


def render_set(
    prompt_set: PromptSetContent | Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    loader: TemplateLoader | None = None,
    templates: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[Stimulus]:
    """Render every role of a prompt set, in ``ROLE_ORDER``.

    ``loader`` maps a pinned template ref to its content (default: the library, exact pinned version).
    ``templates`` replaces a role's template with a draft ``{template, engine?, variables?}`` (the editor
    preview; a draft without ``variables`` may read any context variable); a draft renders with
    ``template_ref="draft:<role>"``. A draft role the set does not define is rendered too.
    """
    content = prompt_set if isinstance(prompt_set, Mapping) else prompt_set.model_dump(mode="json")
    roles: dict[str, str] = dict(content.get("roles") or {})
    drafts = dict(templates or {})
    load = loader or _library_loader
    stimuli: list[Stimulus] = []
    for role in ROLE_ORDER:
        if role in drafts:
            draft = {"role": role, "engine": ENGINE, **dict(drafts[role])}
            if "variables" not in draft:
                draft["variables"] = sorted(find_variables(str(draft.get("template", ""))))
            ref = f"draft:{role}"
        elif role in roles:
            ref = roles[role]
            draft = dict(load(ref))
        else:
            continue
        rendered = render(draft, context, template_ref=ref, owner=Owner(ref, "prompt", ref, role))
        stimuli.append(Stimulus(role=role, template_ref=ref, text=rendered.text, sources=rendered.sources))
    return stimuli


def preview_prompt_hash(stimuli: Iterable[Stimulus | tuple[str, str]]) -> str:
    """``prompt_hash`` of rendered stimuli, computed by ``identity.compute_keys`` (its only implementation).

    ``stimuli`` are :class:`Stimulus` values or ``(role, text)`` pairs. The other identity inputs are fixed
    placeholders: ``prompt_hash`` depends on the stimuli alone.
    """
    pairs = [
        (item.role, item.sha) if isinstance(item, Stimulus) else (str(item[0]), identity.text_sha(str(item[1])))
        for item in stimuli
    ]
    placeholder = "0" * 64
    inputs = identity.IdentityInputs(
        protocol="ref@1",
        kind="reference",
        task={"structured_hash": placeholder, "variant": "standard", "prose_sha": placeholder},
        target=None,
        treatment=dict.fromkeys(identity.TREATMENT_KEYS),
        stimuli=pairs,
        model={"slug": "preview", "endpoint_kind": None, "pinned_digest": None},
        sampling_hash=placeholder,
        policy_treatment_view={},
        images={"harness": None, "gateway": None, "starter_base": []},
        seed=0,
    )
    return identity.compute_keys(inputs).prompt_hash
