"""WP-04 prompt templates and prompt sets (spec 9.2, 11.5): they parse in the sandboxed engine, declare what they
use, carry no evaluation or budget text, and the brief renders for a sampled grid of
(task x stack x style x auth mode) cells."""

from __future__ import annotations

import itertools
import random
import re
from types import SimpleNamespace

import pytest

from backend.generation.contracts.library import APP_REQUIRED_FRAGMENT_KEYS
from backend.generation.contracts.library import AUTH_MODES
from backend.generation.contracts.library import PROSE_VARIANTS
from backend.generation.contracts.library import validate_content
from backend.generation.slot import prompt as slot_prompt
from backend.generation.tests.wp04_content import content

OWN = content.own_prompts()
DOCUMENTED = {
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
    "slot",
    "context",
}
EXPECTED = (
    {"brief-app-default", "brief-app-editor", "budget-neutral", "network-none", "network-packages"}
    | {"slot-single-shot-system", "slot-single-shot-user", "slot-agent-brief"}
    | {f"brief-app-paraphrase-{k}" for k in "abc"}
    | {f"auth-contract-{mode}" for mode in AUTH_MODES}
    | {f"style-{slug}" for slug in content.STYLE_SLUGS}
    | {f"guidance-{slug}" for slug in content.GUIDANCE_SLUGS if slug != "none"}
)  # fmt: skip


def test_the_section_11_5_templates_exist():
    assert set(OWN) == EXPECTED


@pytest.mark.parametrize("slug", sorted(OWN))
def test_template_validates_parses_and_declares_its_variables(slug):
    entry = OWN[slug]
    validate_content("prompt", entry.content)
    used = content.fallback_variables(entry.content["template"])
    declared = set(entry.content["variables"])
    assert used <= declared, f"{slug} uses undeclared {used - declared}"
    assert declared <= used, f"{slug} declares unused {declared - used}"
    assert declared <= DOCUMENTED, f"{slug} declares undocumented {declared - DOCUMENTED}"
    if entry.content["role"] == "fragment":
        assert not declared & {"auth_contract", "budget_neutral", "context"}, (
            "a fragment never reads another fragment's text"
        )


@pytest.mark.parametrize("slug", sorted(OWN))
def test_template_text_is_neutral(slug):
    template = OWN[slug].content["template"].lower()
    for phrase in content.FORBIDDEN_PROMPT_PHRASES:
        assert phrase not in template, f"{slug} mentions {phrase!r}"
    assert not re.search(
        r"budget of|\d+\s*(?:requests?|tokens?|minutes?|seconds?|dollars?|usd)\b|\bseed\b",
        template,
    ), slug


def test_wp03_prompt_lint_is_clean():
    from backend.generation.library import lint as library_lint

    issues = {
        slug: library_lint.lint("prompt", entry.content, system=True, resolve=False) for slug, entry in OWN.items()
    }
    noisy = {
        slug: [(i.code, i.message) for i in found if i.severity in {"error", "warn"}] for slug, found in issues.items()
    }
    assert {slug: found for slug, found in noisy.items() if found} == {}


def test_prompt_sets_are_complete_and_consistent():
    sets = content.entries("prompt_set")
    assert set(sets) == set(content.PROMPT_SETS)
    from backend.generation.library.lint import prompt_set_issues

    for slug, entry in sets.items():
        payload = dict(entry.content)
        payload["roles"] = {role: content.slug_of(ref) + "@1#000000000000" for role, ref in payload["roles"].items()}
        payload["fragments"] = {
            key: content.slug_of(ref) + "@1#000000000000" for key, ref in payload.get("fragments", {}).items()
        }
        assert prompt_set_issues(payload) == [], slug
        for role, ref in entry.content["roles"].items():
            template = content.prompts()[content.slug_of(ref)].content
            assert template["role"] == role, (slug, role)
            assert entry.content["mode"] in template["modes"], (slug, role)
        for key, ref in (entry.content.get("fragments") or {}).items():
            template = content.prompts()[content.slug_of(ref)].content
            assert template["role"] == "fragment", (slug, key)
            assert entry.content["mode"] in template["modes"], (slug, key)
        if entry.content["mode"] == "app":
            assert set(APP_REQUIRED_FRAGMENT_KEYS) <= set(entry.content["fragments"]), slug
    assert "budget_neutral" not in sets["app-default"].content["fragments"], (
        "the default brief carries no budget sentence"
    )
    assert sets["app-budget-neutral"].content["fragments"]["budget_neutral"] == "budget-neutral@1"
    editor = content.prompts()[content.slug_of(sets["app-editor"].content["roles"]["brief"])].content
    assert "editor" in editor["tags"]
    assert "network" not in editor["variables"], "the editor brief makes no claims about installing"


def _layout_ok(text: str) -> None:
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.startswith(("#", "Pay particular attention", "This app has no user interface")) and index:
            assert lines[index - 1] == "", f"no blank line before {line!r}"
    assert "\n\n\n" not in text


def _grid(n: int = 200) -> list[tuple[str, str, str, str, str]]:
    """A deterministic sample of compatible (task, variant, stack, style, auth mode) cells."""
    styles = content.entries("style")
    cells = [
        (task, variant, stack, style, mode)
        for task, variant, (stack, profile), style, mode in itertools.product(
            content.task_slugs(), PROSE_VARIANTS, content.stacks().items(), sorted(styles), AUTH_MODES,
        )
        if profile["frontend"]["framework"] in styles[style].content["compatible_frontends"]
    ]  # fmt: skip
    rng = random.Random(20261103)  # noqa: S311 - a fixed sample, not security
    sample = rng.sample(cells, n)
    # Every task, stack and auth mode appears at least once.
    for task in content.task_slugs():
        if not any(cell[0] == task for cell in sample):
            sample.append(next(cell for cell in cells if cell[0] == task))
    return sample


GRID = _grid()


def test_grid_covers_tasks_stacks_styles_and_auth_modes():
    assert len(GRID) >= 200
    assert {cell[0] for cell in GRID} == set(content.task_slugs())
    assert {cell[4] for cell in GRID} == set(AUTH_MODES)
    assert len({cell[2] for cell in GRID}) >= min(10, len(content.stacks()))
    assert len({cell[3] for cell in GRID}) >= 10


@pytest.mark.parametrize(("task_slug", "variant", "stack_slug", "style_slug", "auth_mode"), GRID)
def test_brief_renders(task_slug, variant, stack_slug, style_slug, auth_mode):
    task = content.task(task_slug)
    text = content.render_app_brief(
        task_slug=task_slug,
        variant=variant,
        stack_slug=stack_slug,
        style_slug=style_slug,
        auth_mode=auth_mode,
    )
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert task["prose"][variant] in text
    assert "{{" not in text
    assert "{%" not in text
    assert not re.search(r"\bNone\b", text)
    _layout_ok(text)
    assert "8000" in text
    lowered = text.lower()
    for phrase in content.FORBIDDEN_PHRASES:
        assert phrase not in lowered
    has_auth = "auth" in task["features"]
    assert ("## Accounts" in text) == has_auth
    assert ("ADMIN_PASSWORD" in text) == ("admin" in task["features"])
    if has_auth and auth_mode == "spec_defined":
        login = next(e for e in task["endpoints"] if e["id"] == task["auth_flow"]["login"])
        assert f"`{login['method']} {login['path']}`" in text.split("## Accounts", 1)[1]
    if not task.get("has_frontend", True):
        assert "## Styling" not in text


def test_brief_variants_render_for_every_prompt_set_guidance_and_network():
    for prompt_set, guidance, network in itertools.product(
        [s for s in content.PROMPT_SETS if s.startswith("app-")], content.GUIDANCE_SLUGS, ("none", "packages"),
    ):  # fmt: skip
        text = content.render_app_brief(
            task_slug="crud_todo_list",
            variant="standard",
            stack_slug=next(iter(content.stacks())),
            style_slug="tailwind",
            guidance_slug=guidance, prompt_set_slug=prompt_set, network_policy=network,
        )  # fmt: skip
        assert "Todo list" in text
        _layout_ok(text)
        if guidance == "security-cwe":
            assert "CWE-639" in text
        if guidance.startswith("libraries-prescribed"):
            assert "password hashing" in text
        if prompt_set == "app-budget-neutral":
            assert "fixed resource budget" in text
        else:
            assert "budget" not in text.lower()
        if prompt_set != "app-editor":
            assert ("no network access" in text) == (network == "none")


@pytest.mark.parametrize("prompt_set", [s for s in content.PROMPT_SETS if s.startswith("app-")])
def test_every_brief_carries_the_same_information(prompt_set):
    """Paraphrases and the editor brief differ in wording only: the task, stack, accounts and run contract."""
    for task_slug in ("webhook_receiver_signed", "crud_todo_list"):
        text = content.render_app_brief(
            task_slug=task_slug, variant="exhaustive", stack_slug=next(iter(content.stacks())), style_slug="plain-css",
            guidance_slug="security-cwe", prompt_set_slug=prompt_set, starter_level="bare",
        )  # fmt: skip
        _layout_ok(text)
        task = content.task(task_slug)
        assert task["prose"]["exhaustive"] in text
        for needle in ("8000", "`/api`", "GET /api/health", "./data", "Dockerfile", "CWE-"):
            assert needle in text, (prompt_set, needle)
        assert ("POST /api/auth/register" in text.replace(task["prose"]["exhaustive"], "")) == (
            "auth" in task["features"]
        )
        assert ("no user interface" in text) == (not task.get("has_frontend", True))


def test_auth_contract_texts_follow_spec_8_4():
    common = {
        "task_slug": "crud_todo_list",
        "variant": "standard",
        "stack_slug": next(iter(content.stacks())),
        "style_slug": "model-choice",
    }
    bearer = content.render_app_brief(**common, auth_mode="contract_bearer")
    assert "Authorization: Bearer <token>" in bearer
    cookie = content.render_app_brief(**common, auth_mode="cookie_session")
    assert "X-CSRF-Token" in cookie
    assert "HttpOnly" in cookie
    assert "POST /api/auth/logout" in cookie
    admin = content.render_app_brief(**(common | {"task_slug": "api_url_shortener"}), auth_mode="contract_bearer")
    assert "ADMIN_PASSWORD" in admin
    assert "no built-in admin password" in admin
    public = content.render_app_brief(**(common | {"task_slug": "search_catalog_sql"}), auth_mode="contract_bearer")
    assert "## Accounts" not in public
    bank = content.render_app_brief(**(common | {"task_slug": "banking_transfers_cookie"}), auth_mode="spec_defined")
    assert "`GET /api/auth/csrf`" in bank
    assert "cookie" in bank


def test_tone_text_and_style_library_reach_the_brief():
    common = {"task_slug": "crud_todo_list", "variant": "minimal", "stack_slug": next(iter(content.stacks()))}
    toned = content.render_app_brief(**common, style_slug="model-choice-brutalist")
    assert "Visual tone: Brutalist" in toned
    plain = content.render_app_brief(**common, style_slug="model-choice")
    assert "Visual tone" not in plain


SLOT = {
    "kind": "endpoint",
    "label": "the handler of PATCH /api/todos/{id}",
    "path": "backend/app.py",
    "language": "python",
    "signature": "def update_todo(todo_id):",
    "decorators": ['@app.patch("/api/todos/<int:todo_id>")'],
    "hole_marker": "# __SLOT__",
    "context_files": [{"path": "backend/app.py", "text": "from flask import Flask\n# __SLOT__\n"}],
    "endpoint": {"method": "PATCH", "path": "/api/todos/{id}", "spec_endpoint_id": "todos.update"},
    "spec_excerpt": "`PATCH /api/todos/{id}` changes only the fields it is given.",
    "output_contract": slot_prompt.output_contract(
        SimpleNamespace(
            address=SimpleNamespace(kind="endpoint", span=object()),
            allowlist=["backend/app.py"],
            whole_file_component=False,
        ),
        "stub",
        slot_prompt.SINGLE_SHOT_EXECUTOR,
    ),
    "allowlist": ["backend/app.py"],
}


@pytest.mark.parametrize("template", ["slot-single-shot-system", "slot-single-shot-user", "slot-agent-brief"])
@pytest.mark.parametrize("variant", ["full", "bare"])
def test_slot_templates_render(template, variant):
    slot = dict(SLOT)
    if variant == "bare":
        slot |= {
            "kind": "file",
            "signature": "",
            "decorators": [],
            "endpoint": None,
            "spec_excerpt": "",
            "context_files": [],
            "output_contract": slot_prompt.output_contract(
                SimpleNamespace(
                    address=SimpleNamespace(kind="file", span=None),
                    allowlist=["backend/app.py"],
                    whole_file_component=False,
                ),
                "remove",
                slot_prompt.SINGLE_SHOT_EXECUTOR,
            ),
        }
    text = content.render_slot(
        template,
        slot,
        stack_slug=next(iter(content.stacks())),
        guidance_slug="security-generic",
    )
    assert "{{" not in text
    assert not re.search(r"\bNone\b", text)
    lowered = text.lower()
    for phrase in content.FORBIDDEN_PROMPT_PHRASES:
        assert phrase not in lowered, phrase
    if template != "slot-single-shot-system":
        assert "backend/app.py" in text
    if template == "slot-single-shot-system":
        contract = slot["output_contract"]
        assert f"Output contract: {contract['description']}" in text
        assert '"required": [' in text
        assert "'granularity'" not in text
        assert "{'" not in text
    if variant == "full" and template == "slot-single-shot-user":
        assert "def update_todo(todo_id):" in text
        assert "from flask import Flask" in text
