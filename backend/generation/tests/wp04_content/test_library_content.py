"""WP-04 styles, tones, guidance and presets (spec 11.5, 11.6, 5.5)."""

from __future__ import annotations

import pytest

from backend.generation.contracts.library import validate_content
from backend.generation.tests.wp04_content import content

KINDS = ["style", "guidance", "prompt_set", "harness_preset", "sampling", "eval_suite"]


@pytest.mark.parametrize("kind", KINDS)
def test_entries_validate_and_reference_existing_templates(kind):
    found = content.entries(kind)
    assert found
    for slug, entry in found.items():
        validate_content(kind, _pinned(kind, entry.content))
        assert entry.name, slug
        assert entry.description, slug
        for key in ("fragment",):
            ref = entry.content.get(key)
            if ref:
                assert content.slug_of(ref) in content.prompts(), (slug, ref)
                assert content.prompts()[content.slug_of(ref)].content["role"] == "fragment"


def test_styles_cover_section_11_5():
    styles = content.entries("style")
    assert set(content.STYLE_SLUGS) <= set(styles)
    known = {"react", "vue", "svelte", "none", "free"}
    for slug, entry in styles.items():
        assert set(entry.content["compatible_frontends"]) <= known, slug
        assert entry.content[
            "fragment"
        ] == f"style-{slug.split('-', 1)[0] if slug.startswith('model-choice') else slug}@1" or slug.startswith(
            "model-choice",
        ), slug
    only = {"mui": ["react"], "shadcn": ["react"], "vuetify": ["vue"], "primevue": ["vue"], "skeleton": ["svelte"]}
    for slug, frontends in only.items():
        assert styles[slug].content["compatible_frontends"] == frontends
    assert "none" not in styles["daisyui"].content["compatible_frontends"]
    assert styles["model-choice"].content.get("tone") is None
    assert styles["model-choice"].content["system"] == "model-choice"


def test_tones_catalogue_and_toned_styles_agree():
    tones = content.tones()
    assert set(tones) == set(content.TONES)
    styles = content.entries("style")
    for slug, tone in tones.items():
        assert tone["tone_text"]
        assert tone["name"]
        toned = styles[f"model-choice-{slug}"].content
        assert toned["tone"] == slug
        assert toned["tone_text"] == tone["tone_text"]


def test_guidance_covers_section_11_5():
    guidance = content.entries("guidance")
    assert set(guidance) == set(content.GUIDANCE_SLUGS)
    assert guidance["none"].content["security_hint"] == "none"
    assert guidance["none"].content.get("fragment") is None
    assert guidance["security-generic"].content["security_hint"] == "generic"
    assert guidance["security-cwe"].content["security_hint"] == "cwe_specific"
    assert guidance["security-cwe"].content["cwe_source"] == "task_surfaces"
    for slug in ("libraries-prescribed-py", "libraries-prescribed-js"):
        assert len(guidance[slug].content["library_prescriptions"]) >= 4, slug


def test_harness_presets_native_and_controlled_for_every_harness():
    presets = content.entries("harness_preset")
    assert set(presets) == {f"{h}-{v}" for h in content.HARNESSES for v in ("native", "controlled")}
    for slug, entry in presets.items():
        harness = slug.rsplit("-", 1)[0]
        body = entry.content
        assert body["harness"] == harness
        assert entry.archived == (harness not in content.ENABLED_HARNESSES), slug
        if slug.endswith("-controlled"):
            assert body["controlled"] is True
            assert body.get("subagents", False) is False
            assert body.get("max_turns") is None, "controlled presets take the turn cap from the budget preset (5.5)"
            if harness != "aider":
                assert body["command_timeout_s"] == 300
        else:
            assert body.get("controlled", False) is False
    versions = {entry.content["harness"]: entry.content["harness_version"] for entry in presets.values()}
    assert versions["opencode"] == "1.18.35"
    assert versions["mini-swe-agent"] == "2.4.6"
    assert versions["aider"] == "0.86.2"
    for harness in content.HARNESSES:
        assert (
            presets[f"{harness}-native"].content["harness_version"]
            == presets[f"{harness}-controlled"].content["harness_version"]
        )


def test_harness_presets_never_set_harness_side_cost_caps():
    for slug, entry in content.entries("harness_preset").items():
        flat = repr(entry.content).lower()
        assert "budget" not in flat, slug
        assert "cost" not in flat, slug


def test_sampling_presets():
    presets = content.entries("sampling")
    assert set(presets) == set(content.SAMPLING_PRESETS)
    assert presets["provider-default"].content == {"policy": "provider_default"}
    for slug, temperature in (("t02", 0.2), ("t07", 0.7), ("t10", 1.0)):
        body = presets[slug].content
        assert body["policy"] == "explicit"
        assert body["params"] == {"temperature": temperature}, "top_p stays unset"


def test_eval_suite_v2():
    suites = content.entries("eval_suite")
    assert set(suites) == {"eval-v2"}
    body = suites["eval-v2"].content
    assert body["tester"]["version"] == 1
    assert body["adherence_checker"]["version"] == 1
    assert body["acceptance_compiler_version"] == 1
    assert body["probes"] == "task"
    assert body["baseline"] == "starter"


@pytest.mark.parametrize("kind", ["style", "guidance", "prompt_set", "harness_preset", "sampling", "eval_suite"])
def test_wp03_lint_is_clean(kind):
    from backend.generation.library import lint as library_lint

    def resolve(target_kind, ref):
        if target_kind == "prompt":
            entry = content.prompts().get(content.slug_of(ref))
            return entry.content if entry else None
        return None

    noisy = {}
    for slug, entry in content.entries(kind).items():
        try:
            issues = library_lint.lint(kind, _pinned(kind, entry.content), system=True, resolve=resolve)
        except NotImplementedError:
            pytest.skip(f"WP-03 lint of {kind} not implemented")
        found = [(i.code, i.message) for i in issues if i.severity in {"error", "warn"}]
        if found:
            noisy[slug] = found
    assert noisy == {}


def _pinned(kind, body):
    from backend.generation.contracts.library import pin_content_refs

    pinned, errors = pin_content_refs(kind, body, lambda _k, _s, _v: "0" * 64)
    assert not errors
    return pinned
