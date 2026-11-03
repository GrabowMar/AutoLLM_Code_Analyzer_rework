"""WP-01 round 2: pinned auth-contract, network and budget-neutral fragments on a prompt set.

The text of a closed enum level (``auth_mode``, ``network_policy``) and the optional budget sentence must live
in a pinned PromptTemplate that the prompt set names, so ``prompt_set.content_hash`` -- and ``treatment_hash``
-- commit to it (Appendix A.1); an ``auth_fragment`` override forks the prompt set and swaps its level.

Until WP-02a/WP-02b land, run with ``--ds=backend.generation.tests.wp01_foundation.settings_u1``.
"""

from __future__ import annotations

import copy
import dataclasses
from typing import get_args

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from pydantic import ValidationError

from backend.generation import identity
from backend.generation.contracts import library
from backend.generation.contracts.plan import FACTOR_KEYS
from backend.generation.contracts.resources import ResourcePolicyContent
from backend.generation.identity import compute_keys
from backend.generation.library import lint
from backend.generation.models import choices
from backend.generation.testing import content as samples
from backend.generation.testing import factories
from backend.generation.testing.golden_library import install_golden_library
from backend.generation.testing.golden_library import load_golden_library
from backend.generation.tests.wp01_foundation.test_identity_algebra import APP_INPUTS

LIBRARY = load_golden_library()
REQUIRED = {*library.AUTH_FRAGMENT_KEYS, *library.NETWORK_FRAGMENT_KEYS}


def _codes(issues):
    return [issue.code for issue in issues]


# -- contract ---------------------------------------------------------------------------------------


def test_fragment_keys_cover_every_auth_mode_and_network_policy():
    assert set(library.AUTH_MODES) == set(choices.AuthMode.values)
    network_field = ResourcePolicyContent.model_fields["network_policy"]
    assert set(library.NETWORK_POLICIES) == set(get_args(network_field.annotation))
    assert set(library.FRAGMENT_KEYS) == REQUIRED | {library.BUDGET_NEUTRAL_FRAGMENT}
    assert library.auth_fragment_key("cookie_session") == "auth_contract.cookie_session"
    assert library.network_fragment_key("packages") == "network.packages"
    with pytest.raises(ValueError, match="unknown auth mode"):
        library.auth_fragment_key("basic")
    with pytest.raises(ValueError, match="unknown network policy"):
        library.network_fragment_key("open")


def test_fragments_default_to_empty_and_reject_unknown_keys():
    content = samples.prompt_set_content(mode="slot")
    assert library.normalize_content("prompt_set", content)["fragments"] == {}
    bad = {**samples.prompt_set_content(), "fragments": {"auth_contract.basic": samples.fake_ref("x@1")}}
    with pytest.raises(ValidationError, match="fragments"):
        library.validate_content("prompt_set", bad)


def test_fragment_refs_must_be_pinned_and_are_pinned_by_pin_content_refs():
    content = samples.prompt_set_content()
    content["fragments"]["budget_neutral"] = "budget-neutral@1"
    with pytest.raises(ValidationError, match="fragments"):
        library.validate_content("prompt_set", content)
    locations = {location for location, _, _ in library.iter_content_refs("prompt_set", content)}
    assert locations == {"roles.brief"} | {f"fragments.{key}" for key in REQUIRED | {"budget_neutral"}}

    pinned, errors = library.pin_content_refs(
        "prompt_set",
        content,
        lambda kind, slug, version: "c" * 64 if (kind, slug, version) == ("prompt", "budget-neutral", 1) else None,
    )
    assert errors == {}
    assert pinned["fragments"]["budget_neutral"] == "budget-neutral@1#" + "c" * 12
    library.validate_content("prompt_set", pinned)


def test_unresolvable_bare_fragment_ref_is_reported_at_its_location():
    content = samples.prompt_set_content()
    content["fragments"]["network.none"] = "network-none@9"
    _, errors = library.pin_content_refs("prompt_set", content, lambda *_: None)
    assert set(errors) == {"fragments.network.none"}


# -- lint -------------------------------------------------------------------------------------------


def test_lint_requires_every_auth_and_network_fragment_in_app_mode():
    content = samples.prompt_set_content(fragments={})
    issues = lint.lint("prompt_set", content)
    assert _codes(issues) == ["lint_prompt_fragment_missing"] * len(REQUIRED)
    assert {issue.data["key"] for issue in issues} == REQUIRED
    assert {issue.path for issue in issues} == {f"fragments.{key}" for key in REQUIRED}
    assert all(issue.severity == "error" for issue in issues)

    partial = samples.prompt_set_content()
    del partial["fragments"]["auth_contract.spec_defined"]
    assert [issue.data["key"] for issue in lint.lint("prompt_set", partial)] == ["auth_contract.spec_defined"]


def test_lint_accepts_a_complete_app_set_with_or_without_the_budget_sentence():
    content = samples.prompt_set_content()
    assert lint.lint("prompt_set", content) == []
    content["fragments"]["budget_neutral"] = samples.fake_ref("budget-neutral@1")
    assert lint.lint("prompt_set", content) == []


def test_lint_does_not_require_fragments_in_slot_mode_but_checks_roles():
    assert lint.lint("prompt_set", samples.prompt_set_content(mode="slot")) == []
    agent = samples.prompt_set_content(mode="slot", roles={"slot_agent_brief": samples.fake_ref("a@1")})
    assert lint.lint("prompt_set", agent) == []
    half = samples.prompt_set_content(mode="slot", roles={"slot_single_shot_user": samples.fake_ref("u@1")})
    assert _codes(lint.lint("prompt_set", half)) == ["lint_prompt_role_missing"]
    no_brief = samples.prompt_set_content(roles={"system": samples.fake_ref("s@1")})
    assert _codes(lint.lint("prompt_set", no_brief)) == ["lint_prompt_role_missing"]


def test_lint_reports_invalid_content_instead_of_raising():
    assert _codes(lint.lint("prompt_set", {"mode": "app"})) == ["lint_content_invalid"]


def test_lint_of_other_kinds_is_still_wp03s():
    with pytest.raises(NotImplementedError):
        lint.lint("task", {})


# -- golden fixture ---------------------------------------------------------------------------------


@pytest.mark.parametrize("entry", LIBRARY.of("prompt_set"), ids=lambda e: e.slug)
def test_golden_prompt_sets_lint_clean(entry):
    assert lint.lint("prompt_set", entry.content) == []


def test_golden_app_set_pins_every_fragment_to_a_fragment_template():
    app = LIBRARY.get("prompt_set", "app-default")
    assert set(app.content["fragments"]) == set(library.FRAGMENT_KEYS)
    prompts = {entry.slug: entry for entry in LIBRARY.of("prompt")}
    for key, ref in app.content["fragments"].items():
        slug, rest = ref.split("@", 1)
        assert rest.split("#", 1)[1] == prompts[slug].content_hash()[:12], key
        assert prompts[slug].content["role"] == "fragment", key
    brief = prompts["brief-app-default"].content
    assert {"auth_contract", "network", "budget_neutral"} <= set(brief["variables"])
    assert not any(char.isdigit() for char in prompts["budget-neutral"].content["template"])  # section 4.1


@pytest.mark.django_db
def test_installed_app_set_keeps_its_pinned_fragments(user):
    installed = install_golden_library(created_by=user)
    row = installed.get("prompt_set", "app-default")
    for key in library.FRAGMENT_KEYS:
        prompt = installed.get("prompt", row.content["fragments"][key].split("@", 1)[0])
        assert row.content["fragments"][key] == prompt.pinned_ref


# -- identity: treatment_hash commits to the fragment text ------------------------------------------


def _set_hash(content):
    normalized = library.normalize_content("prompt_set", content)
    return identity.library_content_hash("prompt_set", library.SCHEMA_VERSIONS["prompt_set"], normalized)


def test_editing_a_fragment_changes_prompt_set_hash_and_treatment_hash():
    base = copy.deepcopy(LIBRARY.get("prompt_set", "app-default").content)
    edited = library.swap_prompt_set_fragment(
        base,
        "auth_contract.cookie_session",
        "auth-contract-cookie_session@2#" + "d" * 12,
    )
    assert _set_hash(base) != _set_hash(edited)

    def keys(set_hash):
        treatment = {**APP_INPUTS.treatment, "prompt_set": set_hash}
        return compute_keys(dataclasses.replace(APP_INPUTS, treatment=treatment))

    a, b = keys(_set_hash(base)), keys(_set_hash(edited))
    assert a.treatment_hash != b.treatment_hash
    assert a.config_fingerprint != b.config_fingerprint


# -- overrides --------------------------------------------------------------------------------------


def test_override_swaps_cover_every_owner_kind_and_name_real_factors():
    assert set(library.OVERRIDE_SWAPS) == set(choices.OverrideOwnerKind.values)
    for owner_kind, (forks, factor) in library.OVERRIDE_SWAPS.items():
        assert factor in FACTOR_KEYS, owner_kind
        assert all(kind in library.CONTENT_MODELS for kind in forks), owner_kind
    assert library.OVERRIDE_SWAPS["auth_fragment"] == (("prompt", "prompt_set"), "prompt_set")


def test_swap_prompt_set_fragment_copies_and_refuses_new_slots():
    base = samples.prompt_set_content()
    new_ref = samples.fake_ref("auth-contract-spec-defined@2")
    swapped = library.swap_prompt_set_fragment(base, "auth_contract.spec_defined", new_ref)
    assert swapped["fragments"]["auth_contract.spec_defined"] == new_ref
    assert base["fragments"]["auth_contract.spec_defined"] != new_ref  # the input is not modified
    assert {k: v for k, v in swapped["fragments"].items() if k != "auth_contract.spec_defined"} == {
        k: v for k, v in base["fragments"].items() if k != "auth_contract.spec_defined"
    }
    with pytest.raises(ValueError, match="no 'budget_neutral' fragment"):
        library.swap_prompt_set_fragment(base, "budget_neutral", new_ref)
    with pytest.raises(ValueError, match="unknown fragment key"):
        library.swap_prompt_set_fragment(base, "auth_contract.basic", new_ref)


@pytest.mark.django_db
def test_auth_fragment_override_names_its_mode_and_swaps_the_prompt_set():
    override = factories.StudyPromptOverrideFactory.create(
        owner_kind=choices.OverrideOwnerKind.AUTH_FRAGMENT,
        owner_ref_base=samples.fake_ref("auth-contract-cookie_session@1"),
        owner_ref_new=samples.fake_ref("auth-contract-cookie_session@2"),
        role="cookie_session",
    )
    assert override.fragment_key == "auth_contract.cookie_session"
    assert override.swapped_factor == "prompt_set"
    prompt = factories.StudyPromptOverrideFactory.create()
    assert prompt.fragment_key is None
    assert prompt.swapped_factor == "prompt_set"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"owner_kind": choices.OverrideOwnerKind.AUTH_FRAGMENT, "role": "fragment"}, "role"),
        ({"owner_ref_base": "brief-app-default@1"}, "owner_ref_base"),
        ({"owner_ref_new": "brief-app-default@latest"}, "owner_ref_new"),
        ({"owner_ref_new": samples.fake_ref("brief-app-default@1")}, "owner_ref_new"),
    ],
)
def test_override_rows_are_validated(changes, field):
    attrs = {"owner_ref_base": samples.fake_ref("brief-app-default@1")} | changes
    with pytest.raises(DjangoValidationError) as exc:
        factories.StudyPromptOverrideFactory.create(**attrs)
    assert field in exc.value.message_dict
