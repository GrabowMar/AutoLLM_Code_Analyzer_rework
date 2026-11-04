"""``harnesses/registry.py``: enabled flags, the refusal of unverified adapters and the plan issues."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from typing import ClassVar

import pytest

from backend.generation.harnesses import registry
from backend.generation.harnesses.gemini_cli import GeminiCliAdapter
from backend.generation.harnesses.opencode import OpenCodeAdapter
from backend.generation.harnesses.registry import HarnessRegistryError

from .conftest import ALL_HARNESSES

if TYPE_CHECKING:
    from backend.generation.contracts.harness import HarnessFacts


def test_every_harness_is_registered_once():
    names = [a.name for a in registry.adapters()]
    assert sorted(names) == sorted(ALL_HARNESSES)
    assert registry.DEFAULT_HARNESS == "opencode"


def test_wave_2_enablement(settings):
    settings.GATEWAY_TRANSLATORS = []
    enabled = {e.adapter.name for e in registry.entries() if e.enabled}
    # E2E-13: claude-code and goose are enabled with narrower gates (endpoint questions, exploratory only).
    assert enabled == {"opencode", "mini-swe-agent", "aider", "claude-code", "goose"}
    for item in registry.entries():
        if not item.enabled:
            assert item.blocked_reason, item.adapter.name
    gemini = registry.entry("gemini-cli").blocked_reason
    assert "real-model smoke run" in gemini
    assert "no gemini_generate translator yet" in gemini  # settings.GATEWAY_TRANSLATORS is empty


def test_enabled_adapters_have_no_open_questions():
    for adapter in registry.enabled_adapters():
        assert adapter.facts.open_questions == ()
        assert adapter.wants_enabled


def test_registry_refuses_an_enabled_adapter_with_open_questions():
    class Unverified(OpenCodeAdapter):
        name: ClassVar[str] = "unverified"
        facts: ClassVar[HarnessFacts] = dataclasses.replace(OpenCodeAdapter.facts, open_questions=("stream schema?",))
        wants_enabled: ClassVar[bool] = True

    with pytest.raises(HarnessRegistryError, match="open_questions"):
        registry.validate(Unverified())


def test_registry_refuses_bad_exit_code_meanings_and_silent_blocks():
    class BadCodes(OpenCodeAdapter):
        facts: ClassVar[HarnessFacts] = dataclasses.replace(OpenCodeAdapter.facts, exit_codes={3: "it broke"})

    with pytest.raises(HarnessRegistryError, match="Termination values"):
        registry.validate(BadCodes())

    class SilentBlock(OpenCodeAdapter):
        wants_enabled: ClassVar[bool] = False
        blocked_reason: ClassVar[str] = ""

    with pytest.raises(HarnessRegistryError, match="blocked_reason"):
        registry.validate(SilentBlock())


def test_translate_only_harness_waits_for_its_translator(settings):
    """Gemini CLI is translate-only: blocked while WP-06's translator is missing, whatever its facts say."""
    settings.GATEWAY_TRANSLATORS = []

    class Ready(GeminiCliAdapter):
        wants_enabled: ClassVar[bool] = True

    adapter = Ready()
    registry.validate(adapter)  # no open questions left: the registry accepts wants_enabled
    assert not registry.servable(adapter, frozenset())
    reason = registry._blocked_reason(adapter, (), frozenset())
    assert reason == "the gateway has no gemini_generate translator yet (settings.GATEWAY_TRANSLATORS)"
    assert registry._blocked_reason(adapter, (), frozenset({"gemini_generate"})) is None
    assert registry.servable(adapter, frozenset({"gemini_generate"}))
    # The real adapter keeps its own reason (no real-model smoke run yet) once the translator lands.
    settings.GATEWAY_TRANSLATORS = ["gemini_generate"]
    item = registry.entry("gemini-cli")
    assert not item.enabled
    assert "translator yet" not in item.blocked_reason
    assert "real-model smoke run" in item.blocked_reason


def test_passthrough_and_native_harnesses_are_servable_without_translators():
    for adapter in registry.adapters():
        if adapter.name != "gemini-cli":
            assert registry.servable(adapter, frozenset()), adapter.name


def test_operator_kill_switch(settings):
    settings.AGENT_HARNESSES_DISABLED = ["opencode"]
    item = registry.entry("opencode")
    assert not item.enabled
    assert "operator" in item.blocked_reason
    with pytest.raises(ValueError, match="not enabled"):
        registry.require_enabled("opencode")


def test_unknown_harness():
    with pytest.raises(KeyError, match="unknown harness"):
        registry.get("cursor")


def test_min_call_output_tokens_present_for_every_adapter():
    expected = {"opencode": 4096, "mini-swe-agent": 2048, "aider": 8192, "claude-code": 16384, "codex": 8192}
    for adapter in registry.adapters():
        assert adapter.facts.min_call_output_tokens >= 1
        if adapter.name in expected:
            assert adapter.facts.min_call_output_tokens == expected[adapter.name]


class TestPlanIssues:
    def codes(self, *args, **kwargs):
        return {i.code: i for i in registry.plan_issues(*args, **kwargs)}

    def test_enabled_harness_has_none(self):
        assert self.codes("opencode", model_slug="deepseek/deepseek-v4.1-flash", max_output_tokens=8192) == {}

    def test_not_enabled(self):
        issues = self.codes("qwen-code", model_slug="x/y", cells=["c0-u0-r0-main"])
        assert issues["harness_not_enabled"].severity == "error"
        assert issues["harness_not_enabled"].cells == ["c0-u0-r0-main"]

    def test_endpoint_open_questions_block_only_their_endpoint_kind(self):
        """E2E-13: Claude Code's OpenRouter passthrough question blocks OpenRouter cells only; a local endpoint
        (OpenCode Go: openai_compatible, served through the local proxy and the anthropic translator) runs."""
        remote = self.codes("claude-code", model_slug="deepseek/deepseek-v4.1-flash", max_output_tokens=16384)
        assert remote["harness_not_enabled"].severity == "error"
        assert remote["harness_not_enabled"].data["reason"] == "endpoint_open_questions"
        assert "/api/v1/messages" in remote["harness_not_enabled"].message
        for kind in ("openai_compatible", "ollama"):
            local = self.codes(
                "claude-code",
                model_slug="local-opencode-go/deepseek-v4.1-flash",
                endpoint_kind=kind,
                max_output_tokens=16384,
            )
            assert "harness_not_enabled" not in local, kind
            assert "harness_protocol_unavailable_for_endpoint" not in local, kind

    @pytest.mark.parametrize("harness", ["claude-code", "goose"])
    def test_exploratory_only_harness_warns_in_exploratory_and_blocks_confirmatory(self, harness):
        args = {"model_slug": "local-opencode-go/qwen3.8-flash", "endpoint_kind": "openai_compatible"}
        exploratory = self.codes(harness, **args, max_output_tokens=16384)
        assert exploratory["harness_exploratory_only"].severity == "warn"
        assert not [i for i in exploratory.values() if i.severity == "error"]
        confirmatory = self.codes(harness, **args, max_output_tokens=16384, study_kind="confirmatory")
        assert confirmatory["harness_exploratory_only"].severity == "error"
        assert "harness_exploratory_only" not in self.codes("opencode", **args)

    def test_endpoint_question_keys_must_be_endpoint_kinds(self):
        class Typo(OpenCodeAdapter):
            endpoint_open_questions: ClassVar = {"open_router": ("?",)}

        with pytest.raises(HarnessRegistryError, match="endpoint kinds"):
            registry.validate(Typo())

    def test_passthrough_needs_a_remote_endpoint(self, settings):
        settings.GATEWAY_TRANSLATORS = []
        issues = self.codes("claude-code", model_slug="anthropic/claude-x", endpoint_kind="local_proxy")
        assert "harness_protocol_unavailable_for_endpoint" in issues
        assert "harness_protocol_unavailable_for_endpoint" not in self.codes("claude-code", model_slug="anthropic/x")
        settings.GATEWAY_TRANSLATORS = ["anthropic_messages"]
        local = self.codes("claude-code", model_slug="anthropic/claude-x", endpoint_kind="local_proxy")
        assert "harness_protocol_unavailable_for_endpoint" not in local

    def test_gemini_needs_the_translator_everywhere(self, settings):
        settings.GATEWAY_TRANSLATORS = []
        assert "harness_protocol_unavailable_for_endpoint" in self.codes("gemini-cli", model_slug="google/gemini-x")
        settings.GATEWAY_TRANSLATORS = ["gemini_generate"]
        for kind in ("openrouter", "local_proxy"):
            issues = self.codes("gemini-cli", model_slug="google/gemini-x", endpoint_kind=kind)
            assert "harness_protocol_unavailable_for_endpoint" not in issues
            assert "harness_not_enabled" in issues  # still awaiting the real-model smoke run

    def test_output_cap_below_harness_minimum(self):
        issues = self.codes("aider", model_slug="m", max_output_tokens=4096)
        assert issues["output_cap_below_harness_min"].data["min_call_output_tokens"] == 8192

    def test_model_without_tools(self):
        assert "model_lacks_tools" in self.codes("opencode", model_slug="m", model_supports_tools=False)
        assert "model_lacks_tools" not in self.codes("aider", model_slug="m", model_supports_tools=False)

    def test_vendor_affinity(self):
        assert (
            self.codes("claude-code", model_slug="deepseek/deepseek-v4.1-flash")["vendor_affinity"].severity == "info"
        )
        assert "vendor_affinity" not in self.codes("claude-code", model_slug="anthropic/claude-sonnet-5")
        assert "vendor_affinity" in self.codes("codex", model_slug="qwen/qwen3-coder")
        assert "vendor_affinity" not in self.codes("codex", model_slug="openai/gpt-5.5")
