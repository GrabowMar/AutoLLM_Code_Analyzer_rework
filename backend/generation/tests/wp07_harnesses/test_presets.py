"""The 16 seeded harness presets (``data/harness_presets``; values owned by WP-07, files by WP-04)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.generation.contracts.library import HarnessPresetContent
from backend.generation.harnesses import registry

from .conftest import ALL_HARNESSES

PRESETS = Path(__file__).resolve().parents[2] / "data" / "harness_presets"
WEB_TOOLS = {
    "opencode": {"webfetch", "websearch"},
    "claude-code": {"WebFetch", "WebSearch"},
    "qwen-code": {"web_fetch", "web_search"},
    "gemini-cli": {"web_fetch", "google_web_search"},
}


def _load(slug: str) -> dict:
    return yaml.safe_load((PRESETS / f"{slug}.yaml").read_text())


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_native_and_controlled_presets(harness):
    adapter = registry.get(harness)
    native, controlled = _load(f"{harness}-native"), _load(f"{harness}-controlled")
    for doc in (native, controlled):
        content = HarnessPresetContent.model_validate(doc["content"])
        assert content.harness == harness
        assert content.harness_version in adapter.versions
        assert content.harness_version == adapter.version()
        adapter.normalize_options(content.options)  # every option key exists and validates
        # Archived exactly while the adapter is not enabled (section 11.6).
        assert bool(doc.get("archived")) is (not registry.is_enabled(harness)), doc["name"]
    ctrl = HarnessPresetContent.model_validate(controlled["content"])
    assert ctrl.controlled
    assert ctrl.subagents is False
    assert ctrl.max_turns is None  # mapped from the budget preset's run.max_turns
    if adapter.capabilities.agentic:
        assert ctrl.command_timeout_s == 300
    if harness in WEB_TOOLS:
        assert WEB_TOOLS[harness] <= set(ctrl.tool_policy.deny)
    assert not HarnessPresetContent.model_validate(native["content"]).controlled


def test_presets_render(smoke_ctx):
    """Every preset renders with the frugal policy (controlled ones pin a captured prompt where they ask to)."""
    for harness in ALL_HARNESSES:
        for kind in ("native", "controlled"):
            content = _load(f"{harness}-{kind}")["content"]
            ctx = smoke_ctx(harness, **{k: v for k, v in content.items() if k not in {"harness", "harness_version"}})
            rendered = registry.get(harness).render(ctx)
            assert rendered.command
