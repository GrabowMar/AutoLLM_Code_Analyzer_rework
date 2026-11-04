"""Prompts API (spec section 9.2), mounted at ``/api/generation/`` without a prefix.

* ``GET /prompts/variables/?mode=app|slot``: the documented render context;
* ``POST /prompts/render/``: rendered stimuli with their source spans, ``prompt_hash`` and issues, for a
  prompt set (and/or draft templates) on the levels of one cell (``context_from``);
* ``POST /prompts/diff/``: a unified diff per role between two renders.

Levels in ``context_from`` are refs the caller can see: ``slug@v``, ``slug@v#hash12`` or a bare ``slug``
(its latest visible version; the response's sources carry the pinned refs actually used). Nothing here reads a
budget: the only policy value a context takes is ``network_policy`` (section 9.2). Operation ids
(``prompts_*``) are published in the CHANGES file.
"""

from __future__ import annotations

import difflib
import functools
from collections.abc import Callable
from typing import Any
from typing import Final

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpRequest  # noqa: TC002 - ninja reads the annotations at runtime
from ninja import Router
from ninja import Schema
from ninja.responses import Status

from backend.generation.contracts import ErrorOut
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import make_issue
from backend.generation.library import service
from backend.generation.models import Starter
from backend.generation.prompts import context as prompt_context
from backend.generation.prompts.engine import ROLE_ORDER
from backend.generation.prompts.engine import RenderError
from backend.generation.prompts.engine import Stimulus
from backend.generation.prompts.engine import preview_prompt_hash
from backend.generation.prompts.engine import render_set

router = Router(tags=["generation: prompts"])

ERRORS: Final = {400: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}


class VariableOut(Schema):
    name: str
    type: str
    description: str
    example: Any = None
    modes: list[str]


class DraftTemplateIn(Schema):
    template: str
    engine: str = "jinja2-sandbox@1"
    variables: list[str] | None = None


class RenderIn(Schema):
    prompt_set: str | None = None
    templates: dict[str, DraftTemplateIn] | None = None
    context_from: dict[str, Any]


class SourceOut(Schema):
    template_ref: str
    owner_kind: str
    owner_ref: str
    role: str | None = None
    char_start: int
    char_end: int


class StimulusOut(Schema):
    role: str
    template_ref: str
    text: str
    sha: str
    chars: int
    est_tokens: int
    sources: list[SourceOut]


class RenderOut(Schema):
    stimuli: list[StimulusOut]
    prompt_hash: str
    issues: list[dict[str, Any]]


class DiffIn(Schema):
    a: RenderIn
    b: RenderIn


class RoleDiffOut(Schema):
    role: str
    unified: str


class DiffOut(Schema):
    per_role: list[RoleDiffOut]


def _errors(view: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return view(*args, **kwargs)
        except service.LibraryError as exc:
            return Status(exc.status_code, exc.payload())

    return wrapper


def _user(request: HttpRequest) -> Any:
    return getattr(request, "auth", None) or request.user


def _level(kind: str, ref: Any, user: Any) -> Any:
    """A visible library row for ``ref`` (``slug@v[#h]`` or a bare slug = its latest visible version)."""
    if ref in (None, ""):
        return None
    text = str(ref)
    if "@" in text:
        return service.resolve_ref(text, kind=kind, user=user)
    return service.latest(kind, text, user=user)


def _starter(stack: Any, level: str | None, user: Any) -> Any:
    if stack is None or not level:
        return None
    candidates = Starter.objects.filter(stack_profile=stack, level=level, is_archived=False).order_by(
        "slug",
        "-version",
    )
    visible = [row for row in candidates if service.can_view(row, user)]
    return visible[0] if visible else None


def _render_issue(code: str, message: str, **data: Any) -> Issue:
    return make_issue(code, message=message, data=data)


def _render(payload: RenderIn, user: Any) -> tuple[list[Stimulus], list[Issue]]:
    """Resolve the levels, build the context and render; template failures become 422."""
    context_from = dict(payload.context_from or {})
    prompt_set = _level("prompt_set", payload.prompt_set, user)
    issues: list[Issue] = []
    if prompt_set is None and not payload.templates:
        msg = "give a prompt_set or draft templates"
        raise service.LibraryError(msg, status=400, code="invalid")
    shared = {
        "stack": _level("stack", context_from.get("stack"), user),
        "style": _level("style", context_from.get("style"), user),
        "guidance": _level("guidance", context_from.get("guidance"), user),
        "harness": _level("harness_preset", context_from.get("harness_preset"), user),
    }
    policy = _level("resource_policy", context_from.get("resource_policy"), user)
    network = context_from.get("network_policy")
    mode = (prompt_set.content.get("mode") if prompt_set else None) or (
        "slot" if "slot_target" in context_from else "app"
    )
    stack, style = shared["stack"], shared["style"]
    if stack is not None and style is not None:
        frontend = (stack.content.get("frontend") or {}).get("framework")
        if frontend not in (style.content.get("compatible_frontends") or []):
            issues.append(
                _render_issue(
                    "incompatible_style",
                    f"{style.ref} does not support the {frontend} frontend of {stack.ref}",
                ),
            )
    try:
        if mode == "slot":
            context = _slot_context(context_from, prompt_set, shared, policy, network, user)
        else:
            task = _level("task", context_from.get("task"), user)
            if task is None:
                msg = "context_from.task is required in app mode"
                raise service.LibraryError(msg, status=400, code="invalid")
            auth_mode = str(context_from.get("auth_mode") or "contract_bearer")
            if auth_mode == "spec_defined" and not task.content.get("auth_flow"):
                issues.append(_render_issue("auth_flow_missing", f"{task.ref} defines no auth_flow"))
            level = context_from.get("starter_level")
            starter = _starter(stack, level, user)
            if level and stack is not None and starter is None:
                issues.append(_render_issue("starter_missing", f"{stack.ref} has no {level} starter"))
            context = prompt_context.build_app_context(
                task=task,
                variant=str(context_from.get("variant") or "standard"),
                stack=stack,
                style=style,
                guidance=shared["guidance"],
                prompt_set=prompt_set,
                auth_mode=auth_mode,
                starter=starter,
                starter_level=level,
                harness=shared["harness"],
                network_policy=network,
                resource_policy=policy.content if policy is not None else None,
                allowed_bases=context_from.get("allowed_bases"),
            )
        drafts = {role: draft.model_dump(exclude_none=True) for role, draft in (payload.templates or {}).items()}
        stimuli = render_set(prompt_set.content if prompt_set else {"roles": {}}, context, templates=drafts)
    except (RenderError, prompt_context.ContextError, ValueError) as exc:
        if isinstance(exc, service.LibraryError):
            raise
        issue = make_issue("lint_template_render_error", message=str(exc))
        msg = f"rendering failed: {exc}"
        raise service.LibraryError(msg, status=422, code="invalid", issues=[issue]) from exc
    return stimuli, issues


def _slot_context(
    context_from: dict[str, Any],
    prompt_set: Any,
    shared: dict[str, Any],
    policy: Any,
    network: Any,
    user: Any,
) -> dict[str, Any]:
    """The slot render context of ``context_from.slot_target`` (slot variables from WP-12's ``slot/``)."""
    from backend.generation.contracts.slot import ContextPolicy
    from backend.generation.contracts.slot import SlotPolicy
    from backend.generation.models import SlotTarget

    try:
        target = SlotTarget.objects.select_related("slot_set").get(pk=context_from.get("slot_target"))
    except (SlotTarget.DoesNotExist, ValueError, TypeError, DjangoValidationError) as exc:
        msg = "no such slot target"
        raise service.LibraryError(msg, status=404, code="not_found") from exc
    owner_id = getattr(target.slot_set, "created_by_id", None)
    if not getattr(user, "is_staff", False) and owner_id not in (None, getattr(user, "pk", None)):
        msg = "no such slot target"
        raise service.LibraryError(msg, status=404, code="not_found")
    try:
        from backend.generation.slot import context as slot_context_module

        context_policy = ContextPolicy.model_validate(
            context_from.get("context_policy") or target.default_context_policy or {},
        )
        slot_policy = SlotPolicy(
            hole_mode=str(context_from.get("hole_mode") or target.default_hole_mode),
            keep_docstring=bool(context_from.get("keep_docstring", target.default_keep_docstring)),
            context_policy=context_policy,
            edit_policy=str(target.edit_policy or "revert"),
        )
        resolved = slot_context_module.resolve_context(target, context_policy)
        return prompt_context.build_slot_context(
            slot_target=target,
            slot_policy=slot_policy,
            resolved_context=resolved,
            executor=context_from.get("executor"),
            task=_level("task", context_from.get("task"), user),
            variant=context_from.get("variant"),
            stack=shared["stack"],
            style=shared["style"],
            guidance=shared["guidance"],
            prompt_set=prompt_set,
            auth_mode=context_from.get("auth_mode"),
            harness=shared["harness"],
            network_policy=network,
            resource_policy=policy.content if policy is not None else None,
        )
    except NotImplementedError as exc:
        msg = f"slot prompt previews are not available yet: {exc}"
        raise service.LibraryError(msg, status=422, code="invalid") from exc


@router.get("/prompts/variables/", response={200: list[VariableOut], **ERRORS}, operation_id="prompts_variables")
@_errors
def prompts_variables(request: HttpRequest, mode: str | None = None) -> Any:
    """``[{name, type, description, example, modes}]``: the documented render context of ``mode``."""
    if mode not in (None, "", "app", "slot"):
        msg = "mode must be app or slot"
        raise service.LibraryError(msg, status=400, code="invalid")
    return prompt_context.documented_variables(mode or None)


@router.post("/prompts/render/", response={200: RenderOut, **ERRORS}, operation_id="prompts_render")
@_errors
def prompts_render(request: HttpRequest, payload: RenderIn) -> Any:
    """Rendered stimuli (text, sha, chars, est_tokens, sources), ``prompt_hash`` and issues."""
    stimuli, issues = _render(payload, _user(request))
    return {
        "stimuli": [stimulus.as_dict() for stimulus in stimuli],
        "prompt_hash": preview_prompt_hash(stimuli),
        "issues": [issue.model_dump(mode="json") for issue in issues],
    }


@router.post("/prompts/diff/", response={200: DiffOut, **ERRORS}, operation_id="prompts_diff")
@_errors
def prompts_diff(request: HttpRequest, payload: DiffIn) -> Any:
    """A unified diff per role between the renders ``a`` and ``b`` (roles in render order)."""
    user = _user(request)
    left = {stimulus.role: stimulus.text for stimulus in _render(payload.a, user)[0]}
    right = {stimulus.role: stimulus.text for stimulus in _render(payload.b, user)[0]}
    per_role = []
    for role in ROLE_ORDER:
        if role not in left and role not in right:
            continue
        unified = "".join(
            difflib.unified_diff(
                left.get(role, "").splitlines(keepends=True),
                right.get(role, "").splitlines(keepends=True),
                fromfile=f"a/{role}",
                tofile=f"b/{role}",
            ),
        )
        per_role.append({"role": role, "unified": unified})
    return {"per_role": per_role}
