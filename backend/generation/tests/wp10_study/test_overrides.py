"""Inline prompt edits (spec sections 3.6, 9.4): fork the owner, swap the factor level, record the diff."""

from __future__ import annotations

import pytest

from backend.generation.models import StudyPromptOverride
from backend.generation.models import StyleDirective
from backend.generation.study import launch as svc
from backend.generation.study import plan as planner
from backend.generation.study.errors import StudyError
from backend.generation.study.overrides import apply_override
from backend.generation.study.overrides import swap_level

pytestmark = pytest.mark.django_db


def test_swap_level_replaces_pinned_or_bare_levels_and_adds_a_default():
    design = {"factors": [{"key": "style", "role": "crossed", "levels": ["tailwind@1", "plain@1"]}]}
    swapped = swap_level(design, "style", "tailwind@1#0123456789ab", "tailwind--abcd1234@1#ffffffffffff")
    assert swapped["factors"][0]["levels"] == ["tailwind--abcd1234@1#ffffffffffff", "plain@1"]
    added = swap_level({"factors": []}, "prompt_set", "app-default@1#aaaaaaaaaaaa", "app-default--x@1#bbbbbbbbbbbb")
    assert added["factors"] == [{"key": "prompt_set", "role": "fixed", "levels": ["app-default--x@1#bbbbbbbbbbbb"]}]


def test_style_override_forks_and_swaps_the_level(make_study, fake_study, user):
    study = make_study()
    tailwind = StyleDirective.objects.get(slug="tailwind", version=1)
    template_ref = tailwind.content["fragment"]
    override = apply_override(
        study,
        owner_kind="style",
        owner_ref=tailwind.pinned_ref,
        template="Style everything with Tailwind utility classes only.\n",
        user=user,
    )
    study.refresh_from_db()
    assert StudyPromptOverride.objects.filter(study=study).count() == 1
    assert override.owner_ref_base.startswith("tailwind@1#")
    levels = study.design["factors"][2]["levels"]
    assert tailwind.pinned_ref not in levels
    assert "tailwind@1" not in levels
    assert template_ref  # the fragment owner was forked by the library service
    # The plan now pins the fork: the rendered prompts of tailwind cells change.
    plan = planner.resolve(study)
    assert any(level.split("@")[0] != "tailwind" for level in {c.levels["style"] for c in plan.conditions})


def test_overrides_are_refused_after_launch(make_study, fake_study, user):
    study = make_study()
    svc.launch(study, planner.resolve(study).plan_hash)
    tailwind = StyleDirective.objects.get(slug="tailwind", version=1)
    with pytest.raises(StudyError) as refused:
        apply_override(study, owner_kind="style", owner_ref=tailwind.pinned_ref, template="x\n", user=user)
    assert refused.value.code == "study_locked"


# -- prompt_set_ref for prompt / auth_fragment overrides (WP-03 ask) -------------------------------------


def _slot_design(**extra):
    return {"mode": "slot", "factors": [], **extra}


def test_the_prompt_set_is_the_single_level_or_the_mode_default(golden, user):
    from backend.generation.study.overrides import prompt_set_for_override

    one = {"mode": "app", "factors": [{"key": "prompt_set", "role": "fixed", "levels": ["app-default@1"]}]}
    assert prompt_set_for_override(one, "brief-app-default@1", user=user) == "app-default@1"
    assert prompt_set_for_override({"mode": "app", "factors": []}, "brief-app-default@1", user=user) == "app-default"
    assert prompt_set_for_override(_slot_design(), "slot-single-shot-user@1", user=user) == "slot-single-shot-default"


def test_the_prompt_set_using_the_template_is_picked_among_several_levels(golden, user):
    from backend.generation.study.overrides import prompt_set_for_override

    levels = ["slot-single-shot-default@1", "slot-agent-default"]
    design = _slot_design(factors=[{"key": "prompt_set", "role": "crossed", "levels": levels}])
    assert prompt_set_for_override(design, "slot-agent-brief@1", user=user) == "slot-agent-default"
    assert prompt_set_for_override(design, "slot-single-shot-user@1", user=user) == "slot-single-shot-default@1"
    # An explicit ref must be one of the levels (pinned or not).
    assert (
        prompt_set_for_override(design, "slot-agent-brief@1", user=user, prompt_set_ref="slot-agent-default@1")
        == "slot-agent-default"
    )
    with pytest.raises(StudyError) as refused:
        prompt_set_for_override(design, "slot-agent-brief@1", user=user, prompt_set_ref="app-default@1")
    assert refused.value.code == "prompt_set_ambiguous"
    # No level uses the template: the caller must say which set it means.
    with pytest.raises(StudyError) as none:
        prompt_set_for_override(design, "brief-app-default@1", user=user)
    assert none.value.code == "prompt_set_ambiguous"
    assert none.value.data["candidates"] == levels


def test_two_slot_executors_without_a_prompt_set_factor_are_refused(golden, user):
    from backend.generation.study.overrides import prompt_set_for_override

    design = _slot_design(
        factors=[{"key": "slot_executor", "role": "crossed", "levels": ["single_shot", "restricted_agent"]}],
    )
    with pytest.raises(StudyError) as refused:
        prompt_set_for_override(design, "slot-agent-brief@1", user=user)
    assert refused.value.code == "prompt_set_ambiguous"


def test_a_prompt_override_passes_the_default_prompt_set(make_study, fake_study, user, monkeypatch):
    from backend.generation.library import service

    seen = {}
    real = service.fork_for_override

    def spy(owner_kind, owner_ref, template, **kwargs):
        seen.update(kwargs)
        return real(owner_kind, owner_ref, template, **kwargs)

    monkeypatch.setattr(service, "fork_for_override", spy)
    study = make_study()
    apply_override(study, owner_kind="prompt", owner_ref="brief-app-default@1", template="Build it.\n", user=user)
    assert seen["prompt_set_ref"].startswith("app-default@1")
    study.refresh_from_db()
    factor = next(f for f in study.design["factors"] if f["key"] == "prompt_set")
    assert factor["role"] == "fixed"
    assert not factor["levels"][0].startswith("app-default@1")
