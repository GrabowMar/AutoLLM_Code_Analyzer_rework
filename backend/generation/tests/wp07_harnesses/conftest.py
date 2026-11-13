"""Shared fixtures for the WP-07 harness tests: contexts, smoke-run fixtures and the ``docker`` marker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.generation.contracts.library import HarnessPresetContent
from backend.generation.contracts.library import ToolPolicy
from backend.generation.contracts.resources import EffectivePolicy
from backend.generation.contracts.resources import preset_content
from backend.generation.harnesses import registry
from backend.generation.harnesses.base import GATEWAY_HOST
from backend.generation.testing.fake_harness import harness_context

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SMOKE_MODEL = "deepseek/deepseek-v4.1-flash"
# The brief render.py (smoke/) used; the snapshot tests render with the same context.
SMOKE_BRIEF = (
    "Create a file named hello.txt in the workspace root that contains a greeting, then stop.\n"
    "Do not start any server.\n"
)
FIXTURE_DIRS = {
    "opencode": "opencode-1.18.35",
    "mini-swe-agent": "mini-swe-agent-2.4.6",
    "aider": "aider-0.86.2",
    "goose": "goose-1.57.0",
    "claude-code": "claude-code-2.1.316",
    "codex": "codex-0.166.0",
    "qwen-code": "qwen-code-0.26.6",
    "gemini-cli": "gemini-cli-0.66.0",
}
SMOKED = tuple(FIXTURE_DIRS)
ALL_HARNESSES = SMOKED  # every adapter has a scripted smoke run (gemini-cli since wave 3)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "docker: needs a docker daemon and the llmlab-v3-wt agent images")


def make_ctx(
    harness: str,
    *,
    tier: str = "frugal",
    model: str = SMOKE_MODEL,
    brief: str = SMOKE_BRIEF,
    system_prompt: str | None = None,
    policy_updates: dict[str, Any] | None = None,
    **preset: Any,
):
    """A ``HarnessContext`` for ``harness`` with the tier's policy (``policy_updates``: dotted keys)."""
    adapter = registry.get(harness)
    content = preset_content(tier)
    if policy_updates:
        data = content.model_dump(mode="json")
        for dotted, value in policy_updates.items():
            node = data
            *parents, leaf = dotted.split(".")
            for key in parents:
                node = node[key]
            node[leaf] = value
        content = type(content).model_validate(data)
    preset_values = {"harness": adapter.name, "harness_version": adapter.version(), **preset}
    if "tool_policy" in preset_values and isinstance(preset_values["tool_policy"], dict):
        preset_values["tool_policy"] = ToolPolicy(**preset_values["tool_policy"])
    return harness_context(
        brief=brief,
        model_name=model,
        system_prompt=system_prompt,
        gateway=adapter.gateway_wiring(GATEWAY_HOST),
        preset=HarnessPresetContent(**preset_values),
        policy=EffectivePolicy(content=content, budget_basis="usd", cap_basis="billed", serving_tier="standard"),
    )


def load_fixture(name: str) -> tuple[dict[str, bytes], list[dict[str, Any]], Path]:
    """``(outputs, calls, dir)`` of a smoke-run fixture (outputs keyed by file basename, as the runner does)."""
    root = FIXTURES / name
    outputs = {p.name: p.read_bytes() for p in root.iterdir() if p.name in {"harness.out", "traj.json"}}
    calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines() if line.strip()]
    return outputs, calls, root


@pytest.fixture
def smoke_ctx():
    return make_ctx
