"""Per-kind library lint (replaces ``data_lint.py``), including starter rules and check ``stated_in`` tagging.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-03 implements it. WP-01 owns
the content-only prompt-set rules below (required roles and fragment slots), because the fragment slots are
part of the ``PromptSetContent`` contract; WP-03 adds the rules that resolve refs (role / mode mismatch,
``lint_fragment_role``, unknown refs) and the other kinds.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from pydantic import ValidationError

from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.library import APP_REQUIRED_FRAGMENT_KEYS
from backend.generation.contracts.library import PromptSetContent

if TYPE_CHECKING:
    from backend.generation.contracts.issues import Issue

__all__ = ["SLOT_ROLE_SETS", "lint", "prompt_set_issues"]

_PENDING = "WP-03: not implemented yet"
# Slot prompt sets are single-shot (both slot_single_shot_* roles) or agent (slot_agent_brief).
SLOT_ROLE_SETS: Final = (
    frozenset({"slot_single_shot_system", "slot_single_shot_user"}),
    frozenset({"slot_agent_brief"}),
)


def prompt_set_issues(content: Mapping[str, Any]) -> list[Issue]:
    """Content-only prompt-set rules: content validity, the roles its mode requires, the app fragment slots.

    An app-mode set must pin a fragment for every ``auth_contract.<mode>`` and ``network.<policy>`` key, so the
    renderer never falls back to a "latest by slug" lookup (Appendix A.1); ``budget_neutral`` is optional.
    """
    try:
        parsed = PromptSetContent.model_validate(content)
    except ValidationError as exc:
        return [make_issue("lint_content_invalid", message=str(exc), path="content")]
    issues: list[Issue] = []
    roles = set(parsed.roles)
    if parsed.mode == "app":
        if "brief" not in roles:
            issues.append(
                make_issue("lint_prompt_role_missing", message="app prompt sets need a brief", path="roles.brief"),
            )
        issues += [
            make_issue(
                "lint_prompt_fragment_missing",
                message=f"app prompt sets need the {key} fragment",
                path=f"fragments.{key}",
                data={"key": key},
            )
            for key in APP_REQUIRED_FRAGMENT_KEYS
            if key not in parsed.fragments
        ]
    elif not any(required <= roles for required in SLOT_ROLE_SETS):
        issues.append(
            make_issue(
                "lint_prompt_role_missing",
                message="slot prompt sets need both slot_single_shot_* roles or slot_agent_brief",
                path="roles",
            ),
        )
    return issues


_CONTENT_RULES: Final = {"prompt_set": prompt_set_issues}


def lint(kind: str, content: dict[str, Any]) -> list[Issue]:
    """Registered issues for ``content`` of ``kind`` (codes from ``contracts.issues``)."""
    rule = _CONTENT_RULES.get(kind)
    if rule is None:
        raise NotImplementedError(_PENDING)
    return rule(content)
