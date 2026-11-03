"""Codex CLI (``@openai/codex``): ``codex exec --json --ephemeral`` (new in v3).

Front protocol ``openai_responses`` (``wire_api="chat"`` was removed in early 2026); the gateway runs it in
``passthrough`` to OpenRouter's stateless Responses beta (remote endpoints, wave 2) and in ``translate``
for local endpoints once the WP-06 translator lands (wave 3). Verified against the 0.166.0 binary
(``exec --help``, ``--strict-config`` with the rendered config) and a scripted-upstream smoke run.

``CODEX_HOME`` is a fresh directory in the sandbox HOME holding the rendered ``config.toml``:

* the ``llmlab`` model provider (``base_url`` = gateway, ``env_key``, ``wire_api = "responses"``, no
  client retries: the gateway owns retries), ``approval_policy = "never"``,
  ``sandbox_mode = "danger-full-access"`` (the container is the boundary), web search off, update checks,
  history, analytics and OTEL exporters off, ``project_doc_max_bytes = 0`` (no AGENTS.md);
* fairness: Codex picks instructions **and tools** per model family by slug prefix, so the instructions are
  pinned with ``model_instructions_file`` (the captured default) and the family resolution is observed in
  the gateway log (``tool_names``);
* knobs: ``model_context_window`` (context), ``model_auto_compact_token_limit`` (compaction),
  ``model_reasoning_effort`` (reasoning effort mode), ``model_reasoning_summary = "none"``,
  ``tool_output_token_limit``. Codex has no turn cap: the gateway's ``max_llm_requests`` ends the turn
  with ``turn.failed``, a clean ``BUDGET`` stop.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from typing import ClassVar
from typing import Final

from backend.generation.contracts.harness import GatewayWiring
from backend.generation.contracts.harness import HarnessCapabilities
from backend.generation.contracts.harness import HarnessContext
from backend.generation.contracts.harness import HarnessFacts
from backend.generation.contracts.harness import ImageSpec
from backend.generation.contracts.harness import ObservedInputs
from backend.generation.contracts.harness import RenderedHarness
from backend.generation.contracts.harness import Trajectory
from backend.generation.contracts.harness import WireProtocol
from backend.generation.harnesses.base import HARNESS_DIR
from backend.generation.harnesses.base import HOME_DIR
from backend.generation.harnesses.base import STDOUT_FILE
from backend.generation.harnesses.base import HarnessAdapter
from backend.generation.harnesses.base import captured_prompt
from backend.generation.harnesses.base import common_env
from backend.generation.harnesses.base import knobs
from backend.generation.harnesses.trajectory import common
from backend.generation.harnesses.trajectory.codex import CodexParser

VERSION: Final = "0.166.0"
CODEX_HOME: Final = f"{HOME_DIR}/.codex"
CONFIG_PATH: Final = f"{CODEX_HOME}/config.toml"
INSTRUCTIONS_PATH: Final = f"{HARNESS_DIR}/codex-instructions.md"
ENV_KEY: Final = "LLMLAB_GATEWAY_KEY"
TOOL_OUTPUT_TOKEN_LIMIT: Final = 10_000
# Features switched off in every preset (``codex features list`` of the pinned binary): remote apps,
# plugins, hooks, image generation, browser/computer use and goal tools are outside the sandbox task, and
# client-side connection retries belong to the gateway. ``multi_agent`` is the subagent tool
# (``multi_agent_v1``); it follows the preset's ``subagents`` switch.
DISABLED_FEATURES: Final = (
    "apps",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "goals",
    "hooks",
    "image_generation",
    "plugins",
    "remote_plugin",
    "plugin_discovery_prefetch",
    "tool_suggest",
    "skill_search",
    "skill_mcp_dependency_install",
    "unbounded_connection_retries",
)

FACTS: Final = HarnessFacts(
    facts_version=1,
    verified_against=f"@openai/codex@{VERSION} linux-x64 binary: exec --help, --strict-config, smoke run, 2026-11-03",
    license="Apache-2.0",
    install=f'npm install -g "@openai/codex@{VERSION}" (prebuilt Rust binary via optionalDependencies)',
    headless_cmd=(
        "codex exec --json --skip-git-repo-check --ephemeral --ignore-rules --strict-config "
        "--sandbox danger-full-access - < /run/brief.md"
    ),
    front_protocol="openai_responses",
    protocol_modes=("passthrough", "translate"),
    base_url_env={"model_providers.llmlab.base_url": "{base_url}"},
    auth_env={ENV_KEY: "{api_key}"},
    model_flags='config model = "<slug>", model_provider = "llmlab"',
    aux_model_knobs=["model (review_model unused in exec)"],
    disable_knobs={
        "web_search": "disabled",
        "check_for_update_on_startup": "false",
        "history.persistence": "none",
        "analytics.enabled": "false",
        "otel.exporter": "none",
        "project_doc_max_bytes": "0",
        "--ephemeral": "",
        "--ignore-rules": "",
    },
    max_turns_flag=None,
    compaction_knob="model_auto_compact_token_limit",
    context_knob="model_context_window",
    output_cap_knob=None,
    reasoning_knob="model_reasoning_effort",
    cache_knob=None,
    tool_policy_flags=None,
    system_prompt_flags="model_instructions_file",
    path_restriction=None,
    system_prompt_capture="pinned_file",
    request_class_source="default:main",
    min_call_output_tokens=8192,
    vendor_affinity="openai",
    trajectory_source=f"stdout ({STDOUT_FILE}, --json events)",
    exit_codes={},
    crash_codes=(),
    system_prompt_normalizers=(r"<cwd>.*?</cwd>", r"<current_date>.*?</current_date>", r"<shell>.*?</shell>"),
    # time.command_timeout_s: 0.166.0 has no config key or flag for a default exec/shell timeout (the model
    # picks ``timeout_ms`` per call), so an active per-command timeout is recorded, never silently dropped.
    unsupported_knobs=(
        "run.max_turns",
        "call.max_output_tokens",
        "caching.prompt_cache",
        "time.command_timeout_s",
        "tool_policy",
        "subagents",
    ),
    open_questions=(
        "OpenRouter /api/v1/responses (stateless): usage/cost fields and provider routing in passthrough (WP-06)",
        "real-model smoke run through the WP-06 gateway on three model families (no paid calls in wave 2)",
        "unknown model slugs get fallback metadata without an apply_patch tool although the pinned instructions "
        "tell the model to use one (no config key forces it; --strict-config refuses include_apply_patch_tool): "
        "force function tools or keep the harness disabled (fairness, 5.2)",
        "bundled system skills stay listed in a developer message with no switch to remove them (5.2 rule: "
        "no hidden capabilities beyond the declared tool set)",
    ),
)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)  # TOML basic strings use JSON's escapes
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    msg = f"no TOML encoding for {type(value).__name__}"
    raise TypeError(msg)


def toml_dumps(data: Mapping[str, Any]) -> str:
    """A minimal TOML writer: top-level keys, then one ``[table]`` per nested mapping (any depth)."""
    lines: list[str] = []

    def emit(table: Mapping[str, Any], prefix: str) -> None:
        scalars = {k: v for k, v in table.items() if not isinstance(v, Mapping)}
        if prefix and scalars:
            lines.append(f"\n[{prefix}]")
        for key, value in scalars.items():
            lines.append(f"{key} = {_toml_value(value)}")
        for key, value in table.items():
            if isinstance(value, Mapping):
                emit(value, f"{prefix}.{key}" if prefix else key)

    emit(data, "")
    return "\n".join(lines).lstrip("\n") + "\n"


class CodexAdapter(HarnessAdapter):
    name: ClassVar[str] = "codex"
    label: ClassVar[str] = "Codex CLI"
    description: ClassVar[str] = "OpenAI's Codex CLI in exec mode (Responses protocol), instructions pinned."
    protocol: ClassVar[WireProtocol] = "openai_responses"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        agentic=True,
        requires_tool_calling=True,
        supports_max_turns=False,
        supports_system_prompt="override",
        supports_tool_policy=False,
        supports_compaction_control=True,
        supports_context_limit=True,
        supports_reasoning_effort_passthrough=True,
        supports_path_restriction=False,
        supports_request_class=False,
        headless_flag="exec --json",
    )
    version_setting: ClassVar[str] = "AGENT_CODEX_VERSION"
    version_build_arg: ClassVar[str] = "CODEX_VERSION"
    image_dir: ClassVar[str] = "agent-codex"
    facts: ClassVar[HarnessFacts] = FACTS
    versions: ClassVar[tuple[str, ...]] = (VERSION,)
    wants_enabled: ClassVar[bool] = False
    blocked_reason: ClassVar[str] = (
        "passthrough needs WP-06's /v1/responses route verified against OpenRouter, and a real-model smoke run"
    )

    def image_spec(self, version: str) -> ImageSpec:
        return self.default_image_spec(version)

    def gateway_wiring(self, gateway_host: str) -> GatewayWiring:
        return GatewayWiring(
            protocol="openai_responses",
            protocol_mode="passthrough",
            base_url=f"http://{gateway_host}/api/v1",
        )

    def config(self, ctx: HarnessContext) -> dict[str, Any]:
        k = knobs(ctx)
        config: dict[str, Any] = {
            "model": ctx.model_name,
            "model_provider": "llmlab",
            "approval_policy": "never",
            "sandbox_mode": "danger-full-access",
            "web_search": "disabled",
            "check_for_update_on_startup": False,
            "model_reasoning_summary": "none",
            "project_doc_max_bytes": 0,
            "model_instructions_file": INSTRUCTIONS_PATH,
            "tool_output_token_limit": TOOL_OUTPUT_TOKEN_LIMIT,
            "model_context_window": k.context_window(ctx),
        }
        if k.compaction == "off":
            config["model_auto_compact_token_limit"] = 2**31 - 1
        elif k.context_tokens is not None:
            config["model_auto_compact_token_limit"] = int(k.context_tokens * 0.9)
        if k.reasoning_mode == "effort" and k.reasoning_effort:
            config["model_reasoning_effort"] = k.reasoning_effort
        features = dict.fromkeys(DISABLED_FEATURES, False)
        features["multi_agent"] = k.subagents
        config["features"] = features
        config["history"] = {"persistence": "none"}
        config["analytics"] = {"enabled": False}
        config["otel"] = {"exporter": "none"}
        config["model_providers"] = {
            "llmlab": {
                "name": "llm-lab gateway",
                "base_url": ctx.gateway.base_url,
                "env_key": ENV_KEY,
                "wire_api": "responses",
                "request_max_retries": 0,
                "stream_max_retries": 0,
                "stream_idle_timeout_ms": 900_000,
            },
        }
        return config

    def render(self, ctx: HarnessContext) -> RenderedHarness:
        self.normalize_options(ctx.preset.options)
        instructions = (
            ctx.system_prompt
            if ctx.system_prompt is not None
            else captured_prompt(self.name, ctx.preset.harness_version)
        )
        return RenderedHarness(
            files={
                ctx.brief_path: ctx.brief.encode("utf-8"),
                CONFIG_PATH: toml_dumps(self.config(ctx)).encode("utf-8"),
                INSTRUCTIONS_PATH: instructions.encode("utf-8"),
            },
            env={**common_env(), "CODEX_HOME": CODEX_HOME, ENV_KEY: ctx.gateway.api_key, "RUST_LOG": "error"},
            command=(
                "exec codex exec --json --skip-git-repo-check --ephemeral --ignore-rules --strict-config "
                "--color never --sandbox danger-full-access -"
            ),
            stdin_path=ctx.brief_path,
        )

    def unsupported_knobs(self, ctx: HarnessContext) -> tuple[str, ...]:
        # Only reasoning *effort* maps (model_reasoning_effort); Codex has no reasoning token budget, so
        # reasoning.mode=max_tokens is an active knob this harness cannot apply.
        extra = ("reasoning",) if knobs(ctx).reasoning_mode == "max_tokens" else ()
        return (*super().unsupported_knobs(ctx), *extra)

    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory:
        parser = CodexParser()
        parser.feed(outputs.get(STDOUT_FILE))
        return parser.finish(calls)

    def observed_inputs(self, calls: list[dict[str, Any]]) -> ObservedInputs:
        return common.observed_inputs(calls, self.facts.system_prompt_normalizers)
