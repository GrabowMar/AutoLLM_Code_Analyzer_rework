"""Stack profiles (phase A and B) and the WP-05 prompt fragments (spec section 11.4, 11.5, Appendix A #2/#3)."""

from __future__ import annotations

import re

import pytest
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from backend.generation.build import dockerfiles
from backend.generation.build import seeding
from backend.generation.build import starters
from backend.generation.contracts.library import PromptTemplateContent
from backend.generation.contracts.library import StackProfileContent
from backend.generation.contracts.library import pin_content_refs

BACKENDS = ("flask", "fastapi", "django", "express")
FRONTENDS = ("react", "vue", "svelte", "none")
PHASE_A = {f"{b}-{f}" for b in BACKENDS for f in FRONTENDS}
# Phase B (wave 3): go (stdlib net/http) and spring-boot x 4 frontends, postgres variants of three React stacks.
PHASE_B_BACKENDS = {"go": "go-stdlib", "spring-boot": "spring-boot"}
PHASE_B = {f"{b}-{f}" for b in PHASE_B_BACKENDS for f in FRONTENDS} | {
    f"{b}-react-postgres" for b in ("fastapi", "django", "express")
}
# Words a stimulus must never carry (evaluation, budget, hidden security nudges; spec 11.2, D7, A.2/A.3).
FORBIDDEN = re.compile(
    r"evaluat|test suite|graded|budget|\$|token|security|secure|vulnerab|admin/admin|default password",
    re.IGNORECASE,
)


def _pinned(content: dict) -> dict:
    pinned, errors = pin_content_refs("stack", content, lambda _kind, _slug, _v: "a" * 64)
    assert not errors
    return pinned


@pytest.fixture(scope="module")
def stacks() -> dict[str, dict]:
    return {entry.slug: entry.content for entry in seeding.stack_profile_entries()}


@pytest.fixture(scope="module")
def fragments() -> dict[str, dict]:
    return {entry.slug: entry.content for entry in seeding.stack_prompt_entries()}


def _expected(slug: str) -> tuple[str, str, str]:
    """(backend framework, frontend framework, database) a profile slug stands for."""
    database = "postgres" if slug.endswith("-postgres") else "sqlite"
    base = slug.removesuffix("-postgres")
    backend, frontend = base.rsplit("-", 1)
    return PHASE_B_BACKENDS.get(backend, backend), frontend, database


def test_profiles_are_phase_a_plus_phase_b_plus_free_choice(stacks):
    assert set(stacks) == PHASE_A | PHASE_B | {"free-choice"}
    assert len(PHASE_A) == 16
    assert len(PHASE_B) == 11
    for slug, content in stacks.items():
        parsed = StackProfileContent.model_validate(_pinned(content))
        if slug == "free-choice":
            assert parsed.choice_mode == "free"
            assert parsed.brief_fragment.startswith("run-contract-free@1#")
            continue
        backend, frontend, database = _expected(slug)
        assert parsed.choice_mode == "fixed"
        assert parsed.backend.framework == backend
        assert parsed.frontend.framework == frontend
        assert parsed.database == database
        assert parsed.brief_fragment.startswith(f"stack-{slug}@1#")


def test_every_profile_declares_resources_and_the_run_contract(stacks):
    for slug, content in stacks.items():
        parsed = StackProfileContent.model_validate(_pinned(content))
        assert content["resources"]["min_memory"], slug
        assert parsed.resources.min_cpus >= 1
        assert parsed.run_contract.port == 8000
        assert parsed.run_contract.health_path == "/api/health"
        assert parsed.run_contract.data_dir == "./data"
    assert stacks["free-choice"]["resources"] == {"min_memory": "3g", "min_cpus": 2.0}
    for slug in PHASE_B:
        _backend, _frontend, database = _expected(slug)
        if slug.startswith("spring-boot-"):
            assert stacks[slug]["resources"] == {"min_memory": "3g", "min_cpus": 2.0}, slug
        elif database == "postgres":
            assert stacks[slug]["resources"]["min_memory"] == "3g", slug  # the sidecar shares the sandbox


def test_compose_plan_covers_every_profile(stacks):
    plan = starters.compose_plan()
    phase_a = {(e.stack, e.level) for e in plan if e.phase == "A"}
    expected = {(slug, level) for slug in PHASE_A for level in ("bare", "minimal")} | {("free-choice", "bare")}
    assert phase_a == expected
    phase_b = {(e.stack, e.level) for e in plan if e.phase == "B"}
    assert phase_b == {(slug, level) for slug in PHASE_B for level in ("bare", "minimal")}
    assert {e.stack for e in plan} == set(stacks)
    assert set(starters.ACTIVE_PHASES) == {e.phase for e in plan}  # nothing listed is left unseeded


def test_every_profile_has_its_fragment(stacks, fragments):
    for slug, content in stacks.items():
        fragment_slug = content["brief_fragment"].split("@")[0]
        assert fragment_slug in fragments, slug
        parsed = PromptTemplateContent.model_validate(fragments[fragment_slug])
        assert parsed.role == "fragment"
        assert set(parsed.modes) == {"app", "slot"}


def test_fragments_are_neutral(fragments):
    for slug, content in fragments.items():
        assert not FORBIDDEN.search(content["template"]), slug


def test_free_choice_fragment_renders_the_allowlist(fragments):
    content = fragments["run-contract-free"]
    assert content["variables"] == ["allowed_bases"]
    env = SandboxedEnvironment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    refs = dockerfiles.allowed_bases_record()["refs"]
    text = env.from_string(content["template"]).render(allowed_bases=refs)
    for ref in refs:
        assert "@sha256:" in ref
        assert f"- `{ref}`" in text  # the model is shown the digest-pinned reference
    assert "@sha256:" in text
    assert "Dockerfile" in text
    assert "/api/health" in text


def test_fixed_fragments_take_no_variables(fragments):
    for slug, content in fragments.items():
        if slug.startswith("stack-"):
            assert content["variables"] == [], slug
            assert "{{" not in content["template"], slug
