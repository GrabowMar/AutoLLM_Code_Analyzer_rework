"""Render contexts for app briefs and slot prompts (the documented variables of ``GET /prompts/variables/``).

``build_app_context(...)`` and ``build_slot_context(...)`` return a pure dict (spec section 9.2). Every string
taken from a library entity is a :class:`~backend.generation.prompts.engine.SourcedText`, so each span of a
rendered stimulus can be traced to the entity that owns it. Fragment texts (``stack.fragment_text``,
``style.fragment_text``, ``guidance.fragment_text``, ``auth_contract``, ``network.sentence``,
``budget_neutral``) are rendered here from the **pinned** templates the entities and the prompt set name;
there is never a latest-by-slug lookup (Appendix A.1).

The context **never** contains numbers from the ResourcePolicy, the seed, or any id (Appendix A.2). The only
value read from a policy is the enum ``network_policy``, which selects the network sentence.

Top-level aliases kept for templates written against the golden fixture: ``stack_fragment``,
``style_fragment``, ``guidance_fragment`` (the ``fragment_text`` of each) and ``run_contract``
(``stack.run_contract``).
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from typing import Final

from backend.generation.contracts.library import BUDGET_NEUTRAL_FRAGMENT
from backend.generation.contracts.library import auth_fragment_key
from backend.generation.contracts.library import network_fragment_key
from backend.generation.contracts.resources import preset_content
from backend.generation.prompts.engine import Owner
from backend.generation.prompts.engine import RenderError
from backend.generation.prompts.engine import SourcedText
from backend.generation.prompts.engine import TemplateLoader
from backend.generation.prompts.engine import render

__all__ = [
    "CONTEXT_VERSION",
    "FRAGMENT_VARIABLES",
    "LEVEL_ALIASES",
    "VARIABLES",
    "ContextError",
    "Level",
    "as_level",
    "build_app_context",
    "build_render_context",
    "build_slot_context",
    "documented_variables",
    "variable_names",
]

CONTEXT_VERSION: Final = 1
# Editors (aider) are not agentic: their brief makes no claims about running or installing (section 5.3).
_NON_AGENTIC_HARNESSES: Final = frozenset({"aider"})


class ContextError(ValueError):
    """A render context cannot be built (missing prose variant, missing fragment, bad level)."""


@dataclass(frozen=True)
class Level:
    """One resolved library level: its kind, pinned ref and stored content."""

    kind: str
    ref: str
    content: Mapping[str, Any]


def as_level(obj: Any, kind: str | None = None) -> Level | None:
    """A :class:`Level` from a library row (``KIND``, ``pinned_ref``, ``content``) or a ``Level``."""
    if obj is None or isinstance(obj, Level):
        return obj
    try:
        return Level(kind or obj.KIND, obj.pinned_ref, obj.content)
    except AttributeError as exc:
        msg = f"not a library level: {obj!r}"
        raise ContextError(msg) from exc


# =====================================================================================
# Documentation of the variables (GET /prompts/variables/)
# =====================================================================================


def _var(name: str, type_: str, description: str, example: Any, modes: tuple[str, ...] = ("app", "slot")) -> dict:
    return {"name": name, "type": type_, "description": description, "example": example, "modes": list(modes)}


VARIABLES: Final[tuple[dict[str, Any], ...]] = (
    _var(
        "task",
        "object",
        "The task: {title, summary, category, prose (the condition's prose variant), entities, endpoints, ui, "
        "features, surfaces, has_frontend, auth_flow}. Owned by the task version.",
        {"title": "Todo list", "summary": "Personal todo lists.", "prose": "Build a todo app ..."},
    ),
    _var(
        "stack",
        "object",
        "The stack profile: {backend, frontend, database, run_contract, fragment_text}; fragment_text is the "
        "stack's brief fragment rendered from its pinned template.",
        {"backend": {"framework": "flask"}, "frontend": {"framework": "react"}, "database": "sqlite"},
    ),
    _var(
        "starter",
        "object",
        "The starter: {level, tree: [paths]} (tree is empty when no starter is resolved).",
        {"level": "minimal", "tree": ["Dockerfile", "backend/app.py"]},
        ("app",),
    ),
    _var(
        "style",
        "object",
        "The style directive: {system, library, tone, tone_text, accessibility, fragment_text}.",
        {"system": "tailwind", "fragment_text": "Style the frontend with Tailwind CSS utility classes."},
    ),
    _var(
        "auth_contract",
        "string",
        "The auth contract text of the cell's auth mode (the prompt set's auth_contract.<mode> fragment).",
        "Authentication: POST /api/auth/register ...",
    ),
    _var(
        "guidance",
        "object",
        "The guidance: {security_hint, cwes, prescriptions: [{purpose, library}], fragment_text}.",
        {"security_hint": "generic", "cwes": [], "fragment_text": "Write secure code."},
    ),
    _var(
        "harness",
        "object",
        "The harness: {name, agentic}; agentic is false for editors such as aider.",
        {"name": "opencode", "agentic": True},
    ),
    _var(
        "network",
        "object",
        "The sandbox network policy: {policy: none|packages, sentence} (the prompt set's network.<policy> fragment).",
        {"policy": "packages", "sentence": "The sandbox can reach package registries only."},
    ),
    _var(
        "allowed_bases",
        "list[string]",
        "Allowed Docker base images (free-choice stacks: the allowlist of data/starters/allowed_bases.yaml unless "
        "given; empty otherwise).",
        ["python:3.13-slim@sha256:..."],
        ("app",),
    ),
    _var(
        "budget_neutral",
        "string",
        "The prompt set's budget_neutral fragment (a fixed sentence with no numbers), or an empty string.",
        "The run has a fixed resource budget; work efficiently.",
    ),
    _var("stack_fragment", "string", "Alias of stack.fragment_text.", "Use Flask (Python 3.13) ..."),
    _var("style_fragment", "string", "Alias of style.fragment_text.", "Style the frontend as you see fit."),
    _var("guidance_fragment", "string", "Alias of guidance.fragment_text (empty for no guidance).", ""),
    _var("run_contract", "object", "Alias of stack.run_contract.", {"port": 8000, "health_path": "/api/health"}),
    _var(
        "slot",
        "object",
        "The slot (section 7.5): {kind, label, path, language, signature, decorators, hole_marker, context_files: "
        "[{path, text}], endpoint, spec_excerpt, output_contract, allowlist}.",
        {"kind": "symbol", "label": "the update_todo handler", "path": "backend/todos.py", "language": "python"},
        ("slot",),
    ),
    _var(
        "context",
        "string",
        "slot.context_files rendered as fenced Markdown blocks (one per file, in resolved-context order).",
        "### `backend/todos.py`\n```\n...\n```",
        ("slot",),
    ),
)
# Variables a fragment template may read (fragments are rendered before the fragment texts exist).
FRAGMENT_VARIABLES: Final = frozenset(
    {"task", "stack", "starter", "style", "guidance", "harness", "network", "allowed_bases", "run_contract", "slot"},
)


def documented_variables(mode: str | None = None) -> list[dict[str, Any]]:
    """``[{name, type, description, example, modes}]`` for ``mode`` (every mode when ``None``)."""
    return [dict(item) for item in VARIABLES if mode is None or mode in item["modes"]]


def variable_names(modes: Sequence[str]) -> frozenset[str]:
    """Names of the variables available in any of ``modes``."""
    return frozenset(item["name"] for item in VARIABLES if set(item["modes"]) & set(modes))


# =====================================================================================
# Building
# =====================================================================================


def _owned(value: Any, owner: Owner) -> Any:
    """``value`` with every string leaf owned by ``owner`` (dicts and lists copied)."""
    if isinstance(value, str):
        return SourcedText(value, owner) if value else ""
    if isinstance(value, Mapping):
        return {str(key): _owned(item, owner) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_owned(item, owner) for item in value]
    return value


def _plain(value: Any) -> Any:
    """JSON-like copy of ``value`` with plain strings (values not owned by a library entity)."""
    if isinstance(value, str):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _owner(level: Level, owner_kind: str, *, role: str | None = None, template_ref: str | None = None) -> Owner:
    return Owner(template_ref or level.ref, owner_kind, level.ref, role)


def _load(loader: TemplateLoader | None, ref: str) -> Mapping[str, Any]:
    if loader is not None:
        return loader(ref)
    from backend.generation.library import service

    return service.resolve_ref(ref, kind="prompt").content


def _fragment(
    ref: str | None,
    owner: Owner,
    base: Mapping[str, Any],
    loader: TemplateLoader | None,
) -> str:
    if not ref:
        return ""
    content = _load(loader, ref)
    if content.get("role") != "fragment":
        msg = f"{ref} is a {content.get('role')!r} template, not a fragment"
        raise ContextError(msg)
    try:
        rendered = render(content, base, template_ref=ref, owner=owner)
    except RenderError as exc:
        msg = f"fragment {ref}: {exc}"
        raise ContextError(msg) from exc
    return SourcedText.from_rendered(rendered, owner)


def _network_policy(network_policy: str | None, resource_policy: Mapping[str, Any] | None) -> str:
    from_policy = resource_policy.get("network_policy") if resource_policy else None
    if network_policy and from_policy and network_policy != from_policy:
        msg = f"network_policy {network_policy!r} disagrees with the resource policy ({from_policy!r})"
        raise ContextError(msg)
    return str(network_policy or from_policy or preset_content("frugal").network_policy)


def _harness(harness: Any) -> dict[str, Any]:
    """``{name, agentic}`` from a harness preset level, a name, or ``None``."""
    if harness is None:
        return {"name": "", "agentic": True}
    if isinstance(harness, str):
        name = harness
    else:
        level = as_level(harness, "harness_preset")
        name = str(level.content.get("harness", "")) if level else ""
    return {"name": name, "agentic": _is_agentic(name)}


def _is_agentic(name: str) -> bool:
    try:
        from backend.generation.harnesses import registry

        adapter = registry.get(name)
        agentic = getattr(getattr(adapter, "capabilities", None), "agentic", None)
        if isinstance(agentic, bool):
            return agentic
    except (NotImplementedError, KeyError, ImportError, AttributeError):
        pass
    return name not in _NON_AGENTIC_HARNESSES


def _free_choice_bases() -> list[str]:
    """The free-choice base-image allowlist (``build/dockerfiles.py``, WP-05), rendered by ``run-contract-free``."""
    from backend.generation.build import dockerfiles

    return list(dockerfiles.allowed_bases_record()["refs"])


def _task_value(task: Level, variant: str | None, *, require_prose: bool) -> dict[str, Any]:
    content = task.content
    prose = (content.get("prose") or {}).get(variant) if variant else None
    if prose is None and require_prose:
        msg = f"task {task.ref} has no {variant!r} prose variant"
        raise ContextError(msg)
    owner = _owner(task, "task")
    return _owned(
        {
            "title": content.get("title", ""),
            "summary": content.get("summary", ""),
            "prose": prose or "",
            "entities": content.get("entities", []),
            "endpoints": content.get("endpoints", []),
            "ui": content.get("ui", []),
            "features": content.get("features", []),
            "has_frontend": content.get("has_frontend", True),
            "auth_flow": content.get("auth_flow"),
            "surfaces": content.get("surfaces", []),
            "category": content.get("category", ""),
        },
        owner,
    )


def _guidance_cwes(guidance: Level, task: Level | None) -> list[Any]:
    content = guidance.content
    if content.get("security_hint") == "none":
        return []
    if content.get("cwe_source") == "explicit":
        return _owned(list(content.get("cwes") or []), _owner(guidance, "guidance"))
    if task is None:
        return []
    surfaces = [surface for surface in task.content.get("surfaces") or [] if str(surface).startswith("cwe-")]
    return _owned(surfaces, _owner(task, "task"))


def _common_levels(
    *,
    task: Level | None,
    variant: str | None,
    stack: Level | None,
    style: Level | None,
    guidance: Level | None,
    require_prose: bool,
) -> dict[str, Any]:
    base: dict[str, Any] = {}
    if task is not None:
        base["task"] = _task_value(task, variant, require_prose=require_prose)
    if stack is not None:
        owner = _owner(stack, "stack")
        content = stack.content
        base["stack"] = _owned(
            {
                "backend": content.get("backend", {}),
                "frontend": content.get("frontend", {}),
                "database": content.get("database", ""),
                "run_contract": content.get("run_contract", {}),
            },
            owner,
        )
        base["run_contract"] = base["stack"]["run_contract"]
    if style is not None:
        owner = _owner(style, "style")
        content = style.content
        base["style"] = _owned(
            {
                "system": content.get("system", ""),
                "library": content.get("library"),
                "tone": content.get("tone"),
                "tone_text": content.get("tone_text"),
                "accessibility": content.get("accessibility", "unspecified"),
            },
            owner,
        )
    if guidance is not None:
        owner = _owner(guidance, "guidance")
        content = guidance.content
        base["guidance"] = {
            "security_hint": _owned(content.get("security_hint", "none"), owner),
            "cwes": _guidance_cwes(guidance, task),
            "prescriptions": _owned(content.get("library_prescriptions") or [], owner),
        }
    return base


def _add_fragments(
    context: dict[str, Any],
    *,
    stack: Level | None,
    style: Level | None,
    guidance: Level | None,
    prompt_set: Level | None,
    auth_mode: str | None,
    network_policy: str,
    loader: TemplateLoader | None,
) -> None:
    """Render the fragment texts against the base ``context`` (which they may read) and add them."""
    base = {key: value for key, value in context.items() if key in FRAGMENT_VARIABLES}
    fragments: Mapping[str, str] = (prompt_set.content.get("fragments") or {}) if prompt_set else {}
    if stack is not None:
        ref = stack.content.get("brief_fragment")
        text = _fragment(ref, _owner(stack, "stack", role="fragment", template_ref=ref), base, loader)
        context["stack"]["fragment_text"] = text
        context["stack_fragment"] = text
    if style is not None:
        ref = style.content.get("fragment")
        text = _fragment(ref, _owner(style, "style", role="fragment", template_ref=ref), base, loader)
        context["style"]["fragment_text"] = text
        context["style_fragment"] = text
    if guidance is not None:
        ref = guidance.content.get("fragment")
        text = _fragment(ref, _owner(guidance, "guidance", role="fragment", template_ref=ref), base, loader)
        context["guidance"]["fragment_text"] = text
        context["guidance_fragment"] = text
    if auth_mode is not None:
        key = auth_fragment_key(auth_mode)
        ref = fragments.get(key)
        if prompt_set is not None and not ref:
            msg = f"prompt set {prompt_set.ref} defines no {key} fragment"
            raise ContextError(msg)
        context["auth_contract"] = _fragment(
            ref,
            Owner(ref or "", "auth_fragment", ref or "", auth_mode),
            base,
            loader,
        )
    key = network_fragment_key(network_policy)
    ref = fragments.get(key)
    context["network"]["sentence"] = _fragment(ref, Owner(ref or "", "prompt", ref or "", "fragment"), base, loader)
    ref = fragments.get(BUDGET_NEUTRAL_FRAGMENT)
    context["budget_neutral"] = _fragment(ref, Owner(ref or "", "prompt", ref or "", "fragment"), base, loader)


# Level names the study resolver (WP-10) and the slot module (WP-12) pass, mapped to the parameters here.
LEVEL_ALIASES: Final = {
    "task_version": "task",
    "prose_variant": "variant",
    "stack_profile": "stack",
    "harness_preset": "harness",
}


def _resolve_aliases(given: dict[str, Any], aliases: Mapping[str, Any]) -> dict[str, Any]:
    for alias, name in LEVEL_ALIASES.items():
        value = aliases.pop(alias, None)
        if value is None:
            continue
        if given.get(name) is not None and given[name] is not value:
            msg = f"both {name!r} and its alias {alias!r} were given"
            raise ContextError(msg)
        given[name] = value
    return given


def build_app_context(
    *,
    task: Any = None,
    variant: str | None = None,
    stack: Any = None,
    style: Any = None,
    guidance: Any = None,
    prompt_set: Any = None,
    auth_mode: str = "contract_bearer",
    starter: Any = None,
    starter_level: str | None = None,
    harness: Any = None,
    network_policy: str | None = None,
    resource_policy: Mapping[str, Any] | None = None,
    allowed_bases: Sequence[str] | None = None,
    loader: TemplateLoader | None = None,
    **aliases: Any,
) -> dict[str, Any]:
    """Context of an app-mode brief (task prose, stack, style, auth contract, network, guidance).

    Levels are library rows (``TaskSpecVersion``, ``StackProfile``, ...) or :class:`Level` values with
    pinned refs; ``task_version``, ``prose_variant``, ``stack_profile`` and ``harness_preset`` are accepted
    as aliases. ``harness`` is a harness preset level or a harness name. ``resource_policy`` (content)
    contributes its ``network_policy`` only. ``loader`` maps a pinned template ref to its content (default:
    the library).
    """
    levels = _resolve_aliases(
        {"task": task, "variant": variant, "stack": stack, "harness": harness},
        aliases,
    )
    if aliases:
        msg = f"unknown app context levels: {', '.join(sorted(aliases))}"
        raise ContextError(msg)
    if levels["task"] is None or not levels["variant"]:
        msg = "an app context needs a task and a prose variant"
        raise ContextError(msg)
    task_level = as_level(levels["task"], "task")
    stack_level = as_level(levels["stack"], "stack")
    style_level = as_level(style, "style")
    guidance_level = as_level(guidance, "guidance")
    prompt_set_level = as_level(prompt_set, "prompt_set")
    starter_level_obj = as_level(starter, "starter")
    policy = _network_policy(network_policy, resource_policy)
    context = _common_levels(
        task=task_level,
        variant=levels["variant"],
        stack=stack_level,
        style=style_level,
        guidance=guidance_level,
        require_prose=True,
    )
    if starter_level_obj is not None:
        owner = _owner(starter_level_obj, "starter")
        context["starter"] = {
            "level": _owned(starter_level_obj.content.get("level", starter_level or ""), owner),
            "tree": _owned(sorted(starter_level_obj.content.get("files") or {}), owner),
        }
    else:
        context["starter"] = {"level": starter_level or "", "tree": []}
    context["harness"] = _harness(levels["harness"])
    context["network"] = {"policy": policy, "sentence": ""}
    if allowed_bases is None and stack_level is not None and stack_level.content.get("choice_mode") == "free":
        allowed_bases = _free_choice_bases()
    context["allowed_bases"] = [str(ref) for ref in allowed_bases or ()]
    _add_fragments(
        context,
        stack=stack_level,
        style=style_level,
        guidance=guidance_level,
        prompt_set=prompt_set_level,
        auth_mode=auth_mode,
        network_policy=policy,
        loader=loader,
    )
    return context


def _fence(text: str) -> str:
    longest = 0
    run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _context_text(files: Sequence[Mapping[str, Any]]) -> str:
    blocks = []
    for item in files:
        text = str(item.get("text", ""))
        fence = _fence(text)
        body = text if text.endswith("\n") or not text else text + "\n"
        blocks.append(f"### `{item.get('path', '')}`\n{fence}\n{body}{fence}")
    return "\n\n".join(blocks)


def _slot_vars(
    target: Any,
    *,
    slot_policy: Any,
    resolved_context: Any,
    executor: str | None,
) -> dict[str, Any]:
    """The ``slot`` variables of a target, built by ``slot/prompt.py`` (WP-12)."""
    from backend.generation.contracts.slot import SlotPolicy
    from backend.generation.slot import prompt as slot_prompt

    policy = SlotPolicy.model_validate(slot_policy) if slot_policy is not None else None
    kwargs: dict[str, Any] = {
        "hole_mode": policy.hole_mode if policy else target.default_hole_mode,
        "keep_docstring": policy.keep_docstring if policy else bool(getattr(target, "default_keep_docstring", False)),
        "context": resolved_context,
        "context_policy": policy.context_policy if policy else None,
    }
    if executor:
        kwargs["executor"] = executor
    return slot_prompt.build_slot_context(target, **kwargs)


def build_slot_context(
    *,
    slot: Mapping[str, Any] | None = None,
    task: Any = None,
    variant: str | None = None,
    stack: Any = None,
    style: Any = None,
    guidance: Any = None,
    prompt_set: Any = None,
    auth_mode: str | None = None,
    harness: Any = None,
    network_policy: str | None = None,
    resource_policy: Mapping[str, Any] | None = None,
    loader: TemplateLoader | None = None,
    slot_target: Any = None,
    slot_set: Any = None,
    parent_run: Any = None,
    slot_policy: Any = None,
    resolved_context: Any = None,
    executor: str | None = None,
    **aliases: Any,
) -> dict[str, Any]:
    """Context of a slot prompt (section 7.5): the ``slot`` variables plus the shared levels.

    ``slot`` is the dict of ``slot/prompt.py``; without it, ``slot_target`` (with ``slot_policy``,
    ``resolved_context`` and ``executor``) is turned into one by WP-12's ``slot.prompt.build_slot_context``,
    and the task, prose variant, stack and auth mode default to the parent run's. ``slot`` holds the parent's
    code (hole applied) and is not a library entity, so its text belongs to the template in the source spans.
    ``task.prose`` is the ``variant`` prose when given, else empty (``slot.spec_excerpt`` is the excerpt).
    """
    levels = _resolve_aliases(
        {"task": task, "variant": variant, "stack": stack, "harness": harness},
        aliases,
    )
    if aliases:
        msg = f"unknown slot context levels: {', '.join(sorted(aliases))}"
        raise ContextError(msg)
    if slot is None:
        if slot_target is None:
            msg = "a slot context needs the slot variables or a slot_target"
            raise ContextError(msg)
        slot = _slot_vars(slot_target, slot_policy=slot_policy, resolved_context=resolved_context, executor=executor)
        slot_set = slot_set or getattr(slot_target, "slot_set", None)
    parent = parent_run or (getattr(slot_set, "parent_run", None) if slot_set is not None else None)
    if parent is not None:
        levels["task"] = levels["task"] or getattr(slot_set, "task_version", None) or parent.task_version
        levels["variant"] = levels["variant"] or parent.prose_variant or None
        levels["stack"] = levels["stack"] or parent.stack_profile
        auth_mode = auth_mode or parent.auth_mode or None
    task_level = as_level(levels["task"], "task")
    stack_level = as_level(levels["stack"], "stack")
    style_level = as_level(style, "style")
    guidance_level = as_level(guidance, "guidance")
    prompt_set_level = as_level(prompt_set, "prompt_set")
    policy = _network_policy(network_policy, resource_policy)
    context = _common_levels(
        task=task_level,
        variant=levels["variant"],
        stack=stack_level,
        style=style_level,
        guidance=guidance_level,
        require_prose=False,
    )
    slot_value = _plain(slot)
    context["slot"] = slot_value
    context["context"] = _context_text(slot_value.get("context_files") or [])
    context["harness"] = _harness(levels["harness"])
    context["network"] = {"policy": policy, "sentence": ""}
    fragments = (prompt_set_level.content.get("fragments") or {}) if prompt_set_level else {}
    _add_fragments(
        context,
        stack=stack_level,
        style=style_level,
        guidance=guidance_level,
        prompt_set=prompt_set_level,
        # Slot prompt sets need no auth fragment; render it only when the set pins one.
        auth_mode=auth_mode if auth_mode and auth_fragment_key(auth_mode) in fragments else None,
        network_policy=policy,
        loader=loader,
    )
    context.setdefault("auth_contract", "")
    return context


def build_render_context(*, mode: str, **levels: Any) -> dict[str, Any]:
    """The documented top-level variables for ``mode`` (``app`` or ``slot``)."""
    if mode == "app":
        return build_app_context(**levels)
    if mode == "slot":
        return build_slot_context(**levels)
    msg = f"unknown prompt mode {mode!r}; expected app or slot"
    raise ContextError(msg)
