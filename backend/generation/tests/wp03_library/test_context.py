"""Render contexts on the golden library: app briefs, slot prompts, ownership and the budget invariant."""

from __future__ import annotations

import inspect

import pytest

from backend.generation.contracts.resources import preset_content
from backend.generation.prompts import context as prompt_context
from backend.generation.prompts.context import ContextError
from backend.generation.prompts.context import build_app_context
from backend.generation.prompts.context import build_slot_context
from backend.generation.prompts.engine import preview_prompt_hash
from backend.generation.prompts.engine import render_set
from backend.generation.tests.wp03_library.test_engine import assert_partition

pytestmark = pytest.mark.django_db


def _app(golden, **overrides):
    levels = {
        "task": golden.get("task", "crud_todo_list"),
        "variant": "standard",
        "stack": golden.get("stack", "flask-react"),
        "style": golden.get("style", "tailwind"),
        "guidance": golden.get("guidance", "generic"),
        "prompt_set": golden.get("prompt_set", "app-default"),
        "auth_mode": "contract_bearer",
        "starter": golden.get("starter", "flask-react-minimal"),
        "harness": golden.get("harness_preset", "opencode-native"),
    }
    levels.update(overrides)
    return build_app_context(**levels)


def _brief(golden, **overrides):
    context = _app(golden, **overrides)
    stimuli = render_set(golden.get("prompt_set", "app-default").content, context)
    assert [s.role for s in stimuli] == ["brief"]
    return stimuli[0]


def test_app_brief_renders_every_level(golden):
    brief = _brief(golden)
    task = golden.get("task", "crud_todo_list")
    assert brief.text.startswith("# Task: Todo list\n")
    assert task.content["prose"]["standard"].splitlines()[0] in brief.text
    assert "Use Flask (Python 3.13)" in brief.text
    assert "Tailwind CSS" in brief.text
    assert "Authorization: Bearer" in brief.text
    assert "listen on port 8000" in brief.text
    assert "package registries" in brief.text  # frugal network_policy = packages
    assert "Write secure code." in brief.text
    assert "fixed resource budget" in brief.text  # the prompt set's budget_neutral sentence
    assert brief.text.endswith("\n")
    assert not brief.text.endswith("\n\n")


def test_every_span_names_its_owner_and_the_spans_partition_the_text(golden):
    brief = _brief(golden)
    assert_partition(brief)
    owners = {(s.owner_kind, s.owner_ref) for s in brief.sources}
    assert ("task", golden.pinned_ref("task", "crud_todo_list")) in owners
    assert ("stack", golden.pinned_ref("stack", "flask-react")) in owners
    assert ("style", golden.pinned_ref("style", "tailwind")) in owners
    assert ("guidance", golden.pinned_ref("guidance", "generic")) in owners
    assert ("auth_fragment", golden.pinned_ref("prompt", "auth-contract-contract_bearer")) in owners
    assert ("prompt", golden.pinned_ref("prompt", "network-packages")) in owners
    assert ("prompt", golden.pinned_ref("prompt", "brief-app-default")) in owners
    stack_span = next(s for s in brief.sources if s.owner_kind == "stack")
    assert stack_span.template_ref == golden.pinned_ref("prompt", "stack-flask-react")
    assert brief.text[stack_span.char_start : stack_span.char_end].startswith("Use Flask")
    auth_span = next(s for s in brief.sources if s.owner_kind == "auth_fragment")
    assert auth_span.role == "contract_bearer"


def test_rendering_is_deterministic(golden):
    first, second = _brief(golden), _brief(golden)
    assert first == second
    assert preview_prompt_hash([first]) == preview_prompt_hash([second])


def test_prose_variant_changes_the_stimulus(golden):
    assert _brief(golden, variant="minimal").sha != _brief(golden, variant="exhaustive").sha


def test_auth_mode_and_network_policy_select_the_pinned_fragments(golden):
    cookie = _brief(golden, auth_mode="cookie_session")
    assert "HttpOnly session cookie" in cookie.text
    assert "Bearer" not in cookie.text
    offline = _brief(golden, network_policy="none")
    assert "no network access" in offline.text


def test_guidance_none_renders_no_fragment(golden):
    context = _app(golden, guidance=golden.get("guidance", "none"))
    assert context["guidance"]["fragment_text"] == ""
    assert context["guidance"]["cwes"] == []
    assert "Write secure code." not in _brief(golden, guidance=golden.get("guidance", "none")).text


def test_budget_never_in_prompts_two_policies_give_an_equal_prompt_hash(golden):
    """Section 13.4 (WP-03): rendering with two policies gives an equal prompt_hash."""
    frugal = preset_content("frugal").model_dump(mode="json")
    generous = preset_content("generous").model_dump(mode="json")
    assert frugal != generous
    a = _brief(golden, resource_policy=frugal)
    b = _brief(golden, resource_policy=generous)
    assert preview_prompt_hash([a]) == preview_prompt_hash([b])
    assert a.text == b.text


def test_the_context_holds_no_policy_numbers_seed_or_ids(golden):
    signature = inspect.signature(build_app_context)
    assert not {"seed", "budget", "run_id", "study"} & set(signature.parameters)
    policy = preset_content("generous").model_dump(mode="json")
    context = _app(golden, resource_policy=policy)
    assert set(context) == {
        "task",
        "stack",
        "stack_fragment",
        "run_contract",
        "style",
        "style_fragment",
        "guidance",
        "guidance_fragment",
        "starter",
        "harness",
        "network",
        "allowed_bases",
        "auth_contract",
        "budget_neutral",
    }
    numbers = set()

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            numbers.add(value)

    walk(context)
    budget_numbers = {
        policy["run"]["max_cost_usd"],
        policy["call"]["max_output_tokens"],
        policy["time"]["wall_clock_s"],
    }
    assert not numbers & budget_numbers - {8000}


def test_policy_and_explicit_network_policy_must_agree(golden):
    policy = {**preset_content("frugal").model_dump(mode="json"), "network_policy": "packages"}
    with pytest.raises(ContextError):
        _app(golden, network_policy="none", resource_policy=policy)


def test_missing_prose_variant_is_an_error(golden):
    from backend.generation.prompts.context import Level

    task = golden.get("task", "crud_todo_list")
    trimmed = Level("task", task.pinned_ref, {**task.content, "prose": {"standard": "x"}})
    with pytest.raises(ContextError):
        _app(golden, task=trimmed, variant="minimal")


def test_starter_tree_and_harness(golden):
    context = _app(golden)
    assert context["starter"]["level"] == "minimal"
    assert "Dockerfile" in context["starter"]["tree"]
    assert context["harness"] == {"name": "opencode", "agentic": True}
    assert _app(golden, harness="aider")["harness"]["agentic"] is False


def test_slot_context_renders_the_single_shot_prompts(golden):
    slot = {
        "kind": "symbol",
        "label": "the update_todo handler",
        "path": "backend/todos.py",
        "language": "python",
        "allowlist": ["backend/todos.py"],
        "context_files": [{"path": "backend/todos.py", "text": "def update_todo():\n    raise NotImplementedError\n"}],
    }
    context = build_slot_context(
        slot=slot,
        task=golden.get("task", "crud_todo_list"),
        stack=golden.get("stack", "flask-react"),
        style=golden.get("style", "model-choice"),
        guidance=golden.get("guidance", "none"),
        prompt_set=golden.get("prompt_set", "slot-single-shot-default"),
    )
    stimuli = render_set(golden.get("prompt_set", "slot-single-shot-default").content, context)
    assert [s.role for s in stimuli] == ["slot_single_shot_system", "slot_single_shot_user"]
    user = stimuli[1].text
    assert "File `backend/todos.py` (python) has a hole where the update_todo handler was." in user
    assert "### `backend/todos.py`\n```\ndef update_todo():" in user
    for stimulus in stimuli:
        assert_partition(stimulus)


def test_documented_variables_cover_the_spec_and_the_golden_templates(golden):
    names = {item["name"] for item in prompt_context.documented_variables("app")}
    assert {
        "task",
        "stack",
        "starter",
        "style",
        "auth_contract",
        "guidance",
        "harness",
        "network",
        "allowed_bases",
        "budget_neutral",
    } <= names
    assert "slot" not in names
    assert {"slot", "context"} <= {item["name"] for item in prompt_context.documented_variables("slot")}
    for row in golden.rows["prompt"].values():
        declared = set(row.content["variables"])
        assert declared <= prompt_context.variable_names(row.content["modes"]), row.slug


def test_level_aliases_used_by_the_study_resolver(golden):
    """WP-10 passes task_version / prose_variant / harness_preset; the result equals the canonical names."""
    canonical = _app(golden)
    aliased = build_app_context(
        task_version=golden.get("task", "crud_todo_list"),
        prose_variant="standard",
        stack=golden.get("stack", "flask-react"),
        style=golden.get("style", "tailwind"),
        guidance=golden.get("guidance", "generic"),
        prompt_set=golden.get("prompt_set", "app-default"),
        auth_mode="contract_bearer",
        starter=golden.get("starter", "flask-react-minimal"),
        harness_preset=golden.get("harness_preset", "opencode-native"),
    )
    assert aliased == canonical
    with pytest.raises(ContextError):
        build_app_context(task=golden.get("task", "crud_todo_list"), variant="standard", bogus=1)


def test_slot_context_from_a_target_uses_the_slot_module(golden, monkeypatch):
    from types import SimpleNamespace

    from backend.generation.slot import prompt as slot_prompt

    calls = {}

    def fake_slot_vars(target, **kwargs):
        calls.update(kwargs)
        return {"path": "backend/todos.py", "language": "python", "label": "x", "context_files": []}

    monkeypatch.setattr(slot_prompt, "build_slot_context", fake_slot_vars)
    parent = SimpleNamespace(
        task_version=golden.get("task", "crud_todo_list"),
        prose_variant="standard",
        stack_profile=golden.get("stack", "flask-react"),
        auth_mode="contract_bearer",
    )
    target = SimpleNamespace(slot_set=SimpleNamespace(parent_run=parent, task_version=parent.task_version))
    policy = {"hole_mode": "stub", "keep_docstring": True, "context_policy": {}, "edit_policy": "revert"}
    context = build_slot_context(slot_target=target, slot_policy=policy, resolved_context="ctx", executor="slot_agent")
    assert calls["hole_mode"] == "stub"
    assert calls["keep_docstring"] is True
    assert calls["executor"] == "slot_agent"
    assert context["task"]["prose"] == golden.get("task", "crud_todo_list").content["prose"]["standard"]
    assert context["stack"]["backend"]["framework"] == "flask"
    assert context["auth_contract"] == ""


def test_free_choice_stacks_get_the_allowlist(golden, monkeypatch):
    from backend.generation.build import dockerfiles
    from backend.generation.prompts.context import Level

    monkeypatch.setattr(
        dockerfiles,
        "allowed_bases_record",
        lambda: {"sha": "x", "refs": ["python:3.13-slim@sha256:" + "a" * 64]},
    )
    stack = golden.get("stack", "flask-react")
    free = Level("stack", stack.pinned_ref, {**stack.content, "choice_mode": "free"})
    assert _app(golden, stack=free, starter=None)["allowed_bases"] == ["python:3.13-slim@sha256:" + "a" * 64]
    assert _app(golden)["allowed_bases"] == []
    assert _app(golden, stack=free, starter=None, allowed_bases=["given"])["allowed_bases"] == ["given"]
