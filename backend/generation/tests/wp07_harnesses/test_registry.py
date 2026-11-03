"""``harnesses/registry.py``: enabled flags, the refusal of unverified adapters and the plan issues."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from typing import ClassVar

import pytest

from backend.generation.harnesses import registry
from backend.generation.harnesses.opencode import OpenCodeAdapter
from backend.generation.harnesses.registry import HarnessRegistryError

from .conftest import ALL_HARNESSES

if TYPE_CHECKING:
    from backend.generation.contracts.harness import HarnessFacts


def test_every_harness_is_registered_once():
    names = [a.name for a in registry.adapters()]
    assert sorted(names) == sorted(ALL_HARNESSES)
    assert registry.DEFAULT_HARNESS == "opencode"


def test_wave_2_enablement():
    enabled = {e.adapter.name for e in registry.entries() if e.enabled}
    assert enabled == {"opencode", "mini-swe-agent", "aider"}
    for item in registry.entries():
        if not item.enabled:
            assert item.blocked_reason, item.adapter.name
    assert "wave 3" in registry.entry("gemini-cli").blocked_reason


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
        issues = self.codes("goose", model_slug="x/y", cells=["c0-u0-r0-main"])
        assert issues["harness_not_enabled"].severity == "error"
        assert issues["harness_not_enabled"].cells == ["c0-u0-r0-main"]

    def test_passthrough_needs_a_remote_endpoint(self, settings):
        issues = self.codes("claude-code", model_slug="anthropic/claude-x", endpoint_kind="local_proxy")
        assert "harness_protocol_unavailable_for_endpoint" in issues
        assert "harness_protocol_unavailable_for_endpoint" not in self.codes("claude-code", model_slug="anthropic/x")
        settings.GATEWAY_TRANSLATORS = ["anthropic_messages"]
        local = self.codes("claude-code", model_slug="anthropic/claude-x", endpoint_kind="local_proxy")
        assert "harness_protocol_unavailable_for_endpoint" not in local

    def test_gemini_needs_the_translator_everywhere(self):
        assert "harness_protocol_unavailable_for_endpoint" in self.codes("gemini-cli", model_slug="google/gemini-x")

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
