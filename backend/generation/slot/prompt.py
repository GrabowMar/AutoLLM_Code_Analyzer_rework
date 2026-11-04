"""Slot prompt and brief construction (spec section 7.5).

``build_slot_context(target, hole_mode=..., keep_docstring=..., context=...)`` returns the ``slot`` render
variables (documented in ``/prompts/variables/?mode=slot``):

``slot.kind``, ``slot.label``, ``slot.path``, ``slot.language``, ``slot.signature``, ``slot.decorators``,
``slot.hole_marker``, ``slot.hole_mode``, ``slot.hole`` (the text standing in for the part),
``slot.context_files: [{path, text}]`` (the resolved context; the slot file with the hole applied),
``slot.endpoint`` (``{method, path, spec_endpoint_id}`` or ``None``), ``slot.spec_excerpt``,
``slot.allowlist``, ``slot.imports`` and ``slot.output_contract`` (``{granularity, schema, description}``,
generated from the executor and the part).

The wording is neutral: nothing here mentions security, tests or evaluation, and no budget, seed or id ever
enters the context (appendix A #2). ``render_stimuli`` renders a slot PromptSet through the prompt engine
(WP-03) with this context merged into the common slot context.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from backend.generation.contracts.slot import ContextPolicy
from backend.generation.contracts.slot import ResolvedContext
from backend.generation.contracts.slot import slot_output_model
from backend.generation.slot.context import MaterializedContext
from backend.generation.slot.context import estimate_tokens
from backend.generation.slot.context import materialize
from backend.generation.slot.errors import SlotInvalidError
from backend.generation.slot.index.languages import hole_marker
from backend.generation.slot.part import Part
from backend.generation.slot.part import part_of

if TYPE_CHECKING:
    from backend.generation.models import SlotTarget

__all__ = [
    "AGENT_EXECUTOR",
    "SINGLE_SHOT_EXECUTOR",
    "build_slot_context",
    "output_contract",
    "render_stimuli",
    "spec_excerpt",
    "uses_files_output",
]

SINGLE_SHOT_EXECUTOR: Final = "single_shot"
AGENT_EXECUTOR: Final = "restricted_agent"


def uses_files_output(part: Part, hole_mode: str) -> bool:
    """Single-shot answers with ``{"files": ...}`` for ``file`` and removed whole-file components."""
    return (
        part.address.kind == "file"
        or (part.address.kind == "component" and part.whole_file_component and hole_mode == "remove")
        or (part.address.kind == "component" and part.address.span is None)
    )


def output_contract(part: Part, hole_mode: str, executor: str) -> dict[str, Any]:
    """What the model must return (the strict JSON schema for single shot; the allowlist for the agent)."""
    if executor == AGENT_EXECUTOR:
        return {
            "granularity": "workspace",
            "schema": None,
            "description": f"Edit only these files: {', '.join(part.allowlist)}.",
        }
    files = uses_files_output(part, hole_mode)
    model = slot_output_model(part.address.kind, whole_file_component=files and part.address.kind == "component")
    schema = model.model_json_schema()
    if files:
        schema = {
            "type": "object",
            "properties": {
                "files": {
                    "type": "object",
                    "properties": {path: {"type": "string"} for path in part.allowlist},
                    "required": list(part.allowlist),
                    "additionalProperties": False,
                },
            },
            "required": ["files"],
            "additionalProperties": False,
        }
        description = (
            'Answer with exactly one JSON object {"files": {...}} whose keys are exactly these paths and whose '
            f"values are the complete file contents: {', '.join(part.allowlist)}."
        )
    else:
        schema = {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
            "additionalProperties": False,
        }
        description = (
            'Answer with exactly one JSON object {"code": "..."} holding the complete replacement for the '
            "missing part, including its signature and decorators."
        )
    return {"granularity": "files" if files else "span", "schema": schema, "description": description}


def _endpoints(part: Part) -> list[dict[str, Any]]:
    wanted = set(part.linked_endpoints)
    if part.address.endpoint is not None and part.address.endpoint.spec_endpoint_id:
        wanted.add(part.address.endpoint.spec_endpoint_id)
    return [e for e in part.parent.task_content.get("endpoints", []) if e["id"] in wanted]


def spec_excerpt(part: Part, include_spec: str) -> str:
    """The task prose the prompt may quote: none, the paragraphs about the linked endpoints, or all of it."""
    if include_spec == "none":
        return ""
    prose_by_variant: dict[str, str] = part.parent.task_content.get("prose") or {}
    variant = part.slot_set.parent_run.prose_variant or "standard"
    prose = prose_by_variant.get(variant) or next(iter(prose_by_variant.values()), "")
    if include_spec == "full":
        return prose
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", prose) if p.strip()]
    chosen: list[str] = []
    for endpoint in _endpoints(part):
        literal = f"{endpoint['method']} {endpoint['path']}"
        found = [p for p in paragraphs if literal in p or f"`{endpoint['path']}`" in p]
        chosen += [p for p in found if p not in chosen] or [f"`{literal}`"]
    return "\n\n".join(chosen)


def build_slot_context(
    target: SlotTarget | Part,
    *,
    hole_mode: str,
    keep_docstring: bool,
    context: ResolvedContext | MaterializedContext,
    context_policy: ContextPolicy | None = None,
    executor: str = SINGLE_SHOT_EXECUTOR,
) -> dict[str, Any]:
    """The ``slot`` render variables of one slot prompt.

    ``context`` is the resolved context of the condition. A bare ``ResolvedContext`` is re-materialised with
    ``context_policy`` (default: the target's) and must hash to the same ``sha`` (it is the condition's
    frozen context; a mismatch means the parent or the resolver changed and is refused).
    """
    part = target if isinstance(target, Part) else part_of(target)
    if isinstance(context, MaterializedContext):
        materialized = context
    else:
        policy = context_policy or part.default_context_policy
        materialized = materialize(part, policy, hole_mode=hole_mode, keep_docstring=keep_docstring)
        if materialized.resolved.sha != context.sha:
            msg = "the resolved context does not match the frozen one (parent or resolver changed)"
            raise SlotInvalidError(msg)
    policy = context_policy or part.default_context_policy
    symbol = part.parent.symbol(part.address.symbol_id)
    endpoint = part.address.endpoint.model_dump(mode="json") if part.address.endpoint is not None else None
    interface_imports = [
        line.strip()
        for line in part.parent.texts.get(part.path, "").splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    return {
        "kind": part.address.kind,
        "label": part.label or part.path,
        "path": part.path,
        "language": part.language,
        "signature": symbol.signature if symbol is not None else "",
        "decorators": list(symbol.decorators) if symbol is not None else [],
        "hole_marker": hole_marker(part.language),
        "hole_mode": hole_mode,
        "hole": materialized.hole.hole.decode("utf-8", errors="replace"),
        "removed_file": materialized.hole.removed,
        "context_files": [{"path": f.path, "text": f.text} for f in materialized.files],
        "endpoint": endpoint,
        "spec_excerpt": spec_excerpt(part, policy.include_spec),
        "allowlist": list(part.allowlist),
        "imports": interface_imports,
        "output_contract": output_contract(part, hole_mode, executor),
    }


def render_stimuli(
    target: SlotTarget,
    prompt_set: Any,
    *,
    executor: str = SINGLE_SHOT_EXECUTOR,
    hole_mode: str | None = None,
    keep_docstring: bool | None = None,
    context_policy: ContextPolicy | None = None,
    guidance: Any = None,
    style: Any = None,
) -> dict[str, Any]:
    """Render a slot ``PromptSet`` for one target and condition: ``{stimuli, prompt_hash, est_tokens}``."""
    from backend.generation.contracts.library import PromptSetContent
    from backend.generation.prompts import context as prompt_context
    from backend.generation.prompts import engine

    part = part_of(target)
    mode = hole_mode or part.default_hole_mode
    keep = part.default_keep_docstring if keep_docstring is None else keep_docstring
    policy = context_policy or part.default_context_policy
    materialized = materialize(part, policy, hole_mode=mode, keep_docstring=keep)
    slot_vars = build_slot_context(
        part,
        hole_mode=mode,
        keep_docstring=keep,
        context=materialized,
        context_policy=policy,
        executor=executor,
    )
    parent = part.slot_set.parent_run
    render_context = prompt_context.build_slot_context(
        slot=slot_vars,
        task=part.slot_set.task_version,
        variant=parent.prose_variant or None,
        stack=parent.stack_profile,
        auth_mode=parent.auth_mode or None,
        style=style,
        guidance=guidance,
        prompt_set=prompt_set,
    )
    content = prompt_set.content if hasattr(prompt_set, "content") else prompt_set
    stimuli = engine.render_set(PromptSetContent.model_validate(content).model_dump(mode="json"), render_context)
    rendered = [
        {
            "role": s.role,
            "template_ref": s.template_ref,
            "text": s.text,
            "chars": len(s.text),
            "est_tokens": estimate_tokens(s.text),
            "sources": [
                span.model_dump(mode="json") if hasattr(span, "model_dump") else dict(span) for span in s.sources
            ],
        }
        for s in stimuli
    ]
    return {
        "stimuli": rendered,
        "prompt_hash": engine.preview_prompt_hash(stimuli),
        "est_tokens": sum(item["est_tokens"] for item in rendered),
        "resolved_context": materialized.resolved.model_dump(mode="json"),
    }
