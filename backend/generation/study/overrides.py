"""Inline prompt edits from the builder and the cell drawer (``StudyPromptOverride``; spec sections 3.6, 9.4).

An edit never changes library text in place: ``library.service.fork_for_override`` (WP-03) saves it as a new
user-owned version of the entity that owns the text (``derived_from`` = the base) and forks the entities that
pin it (``contracts.library.OVERRIDE_SWAPS``). The study then swaps the factor level that pinned the base to
the fork (adding a fixed factor when the design used the default) and records the override with its diff.
Only an unlocked draft or planned study takes overrides (the frozen plan becomes stale).

``fork_for_override`` gets ``prompt_set_ref`` for every ``prompt``/``auth_fragment`` owner (WP-03 ask): the
caller's explicit ``prompt_set_ref`` (it must be one of the study's prompt-set levels), else the study's single
prompt-set level (the mode's default set when the design has no ``prompt_set`` factor), else the one level whose
roles or fragments name the edited template; several (or no) candidates are 400 ``prompt_set_ambiguous`` with
``data.candidates``. ``role`` (the auth mode) is passed for ``auth_fragment``. It returns an object or
dict with ``owner_ref_base`` / ``owner_ref_new`` (pinned refs of the base and the forked owner),
``level_ref_base`` and ``level_ref_new`` (pinned refs of the swapped factor level) and ``diff`` (unified
diff of the template text); see the CHANGES entry "WP-10 → WP-03".
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction

from backend.generation.contracts.library import OVERRIDE_SWAPS
from backend.generation.models import Study
from backend.generation.models import StudyPromptOverride
from backend.generation.models.choices import StudyStatus
from backend.generation.study.errors import StudyError

if TYPE_CHECKING:
    from backend.users.models import User

__all__ = ["apply_override", "prompt_set_for_override", "swap_level"]


def _field(result: Any, name: str) -> Any:
    return result.get(name) if isinstance(result, dict) else getattr(result, name, None)


def _unpinned(ref: str) -> str:
    return ref.split("#", 1)[0]


def _factor_levels(design: dict[str, Any], key: str) -> list[str]:
    return next((list(f["levels"]) for f in design.get("factors") or [] if f["key"] == key), [])


def _strip_kind(ref: str) -> str:
    return ref.split(":", 1)[1] if ":" in ref.split("@", 1)[0] else ref


def _same_level(level: str, ref: str) -> bool:
    """``level`` (bare ``slug``, ``slug@v`` or pinned) names the entity ``ref`` (``slug@v[#hash12]``)."""
    level, ref = _strip_kind(level), _strip_kind(ref)
    if "@" not in level:
        return level == ref.split("@", 1)[0]
    return _unpinned(level) == _unpinned(ref)


def _prompt_set_levels(design: dict[str, Any]) -> tuple[list[str], bool]:
    """The study's prompt-set levels and whether they come from a ``prompt_set`` factor (else the defaults)."""
    levels = _factor_levels(design, "prompt_set")
    if levels:
        return levels, True
    from backend.generation.study.design import SLOT_EXECUTORS
    from backend.generation.study.plan import APP_DEFAULTS
    from backend.generation.study.plan import SLOT_DEFAULTS
    from backend.generation.study.plan import SLOT_PROMPT_SET_DEFAULTS

    if design.get("mode") != "slot":
        return [APP_DEFAULTS["prompt_set"]], False
    executors = _factor_levels(design, "slot_executor") or [SLOT_DEFAULTS["slot_executor"]]
    defaults = [SLOT_PROMPT_SET_DEFAULTS[SLOT_EXECUTORS[e]] for e in executors if e in SLOT_EXECUTORS]
    return list(dict.fromkeys(defaults)), False


def _resolve_level(level: str, user: User | None) -> Any:
    from backend.generation.library import service

    if "@" in _strip_kind(level):
        return service.resolve_ref(level, kind="prompt_set", user=user)
    return service.latest("prompt_set", _strip_kind(level), user=user)


def _uses_template(prompt_set: Any, template_ref: str) -> bool:
    content = prompt_set.content or {}
    refs = [*(content.get("roles") or {}).values(), *(content.get("fragments") or {}).values()]
    return any(isinstance(ref, str) and _same_level(ref, template_ref) for ref in refs)


def prompt_set_for_override(
    design: dict[str, Any],
    owner_ref: str,
    *,
    user: User | None,
    prompt_set_ref: str | None = None,
) -> str:
    """The prompt-set level an override of the template ``owner_ref`` forks (see the module docstring)."""
    from backend.generation.library import service

    levels, from_factor = _prompt_set_levels(design)
    if prompt_set_ref:
        match = next((level for level in levels if _same_level(level, prompt_set_ref)), None)
        if match is None:
            msg = f"prompt_set_ref {prompt_set_ref} is not one of the study's prompt sets"
            raise StudyError(400, "prompt_set_ambiguous", msg, {"candidates": levels})
        return match
    if len(levels) == 1:
        return levels[0]
    template = service.resolve_ref(_strip_kind(owner_ref), kind="prompt", user=user)
    using = [level for level in levels if _uses_template(_resolve_level(level, user), template.pinned_ref)]
    if len(using) == 1 and from_factor:
        return using[0]
    if len(using) == 1:
        msg = "the slot executors use different default prompt sets; add a prompt_set factor to edit one"
    else:
        msg = f"{len(using)} of the study's prompt sets use {template.pinned_ref}; pass prompt_set_ref"
    raise StudyError(400, "prompt_set_ambiguous", msg, {"candidates": using or levels})


def _pinned_level(level: str, user: User | None) -> str:
    """``level`` as a ``slug@v`` ref ``fork_for_override`` accepts (a bare slug takes the latest version)."""
    return level if "@" in _strip_kind(level) else _resolve_level(level, user).pinned_ref


def swap_level(design: dict[str, Any], factor: str, base: str, new: str) -> dict[str, Any]:
    """``design`` with every level of ``factor`` naming ``base`` (pinned or not) replaced by ``new``."""
    out = dict(design)
    factors = [dict(f) for f in design.get("factors") or []]
    targets = {base, _unpinned(base), base.split("@", 1)[0]}
    found = False
    for spec in factors:
        if spec["key"] != factor:
            continue
        found = True
        spec["levels"] = [new if level in targets else level for level in spec["levels"]]
    if not found:
        factors.append({"key": factor, "role": "fixed", "levels": [new]})
    out["factors"] = factors
    return out


def apply_override(
    study: Study,
    *,
    owner_kind: str,
    owner_ref: str,
    template: str,
    role: str = "",
    user: User | None,
    prompt_set_ref: str | None = None,
) -> StudyPromptOverride:
    """Fork the owner of the edited text, swap the study's factor level and record the override."""
    if owner_kind not in OVERRIDE_SWAPS:
        raise StudyError(400, "invalid_owner_kind", f"owner_kind must be one of {sorted(OVERRIDE_SWAPS)}")
    from backend.generation.library.service import fork_for_override
    from backend.generation.study.launch import update_study

    with transaction.atomic():
        locked = Study.objects.select_for_update(of=("self",)).get(pk=study.pk)
        if locked.locked_at is not None or locked.status not in (StudyStatus.DRAFT, StudyStatus.PLANNED):
            raise StudyError(409, "study_locked", "prompt overrides are only possible before launch")
        factor = OVERRIDE_SWAPS[owner_kind][1]
        kwargs: dict[str, Any] = {"user": user}
        if owner_kind in ("prompt", "auth_fragment"):
            level = prompt_set_for_override(locked.design, owner_ref, user=user, prompt_set_ref=prompt_set_ref)
            kwargs["prompt_set_ref"] = _pinned_level(level, user)
        if owner_kind == "auth_fragment":
            kwargs["role"] = role
        result = fork_for_override(owner_kind, owner_ref, template, **kwargs)
        owner_new = _field(result, "owner_ref_new")
        level_base = _field(result, "level_ref_base")
        level_new = _field(result, "level_ref_new")
        if not (owner_new and level_base and level_new):
            msg = "fork_for_override must return owner_ref_new, level_ref_base and level_ref_new"
            raise StudyError(500, "override_fork_incomplete", msg)
        update_study(locked, design=swap_level(locked.design, factor, level_base, level_new))
        override = StudyPromptOverride(
            study=locked,
            owner_kind=owner_kind,
            owner_ref_base=_field(result, "owner_ref_base") or owner_ref,
            owner_ref_new=owner_new,
            role=role or "",
            diff=_field(result, "diff") or "",
            created_by=user,
        )
        override.full_clean()
        override.save()
    return override
