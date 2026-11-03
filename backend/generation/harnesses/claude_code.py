"""Claude Code (``@anthropic-ai/claude-code``): ``claude --bare -p --output-format stream-json`` (new in v3).

Front protocol ``anthropic_messages``; the gateway runs it in ``passthrough`` to OpenRouter's native
``/api/v1/messages`` (remote endpoints, wave 2) and in ``translate`` for local endpoints once the WP-06
translator lands (wave 3). Verified against the 2.1.316 binary (``--help`` and an env-name scan) and a
scripted-upstream smoke run (``tests/wp07_harnesses/fixtures/claude-code-2.1.316/``):

* **Bare mode.** ``--bare`` skips CLAUDE.md discovery, hooks, plugins, MCP, auto-memory, LSP and
  background prefetches; under ``--bare`` Anthropic auth is strictly ``ANTHROPIC_API_KEY``, so the
  dummy key goes there (not ``ANTHROPIC_AUTH_TOKEN``). This behaviour choice is part of the bundle.
* **Model pinning.** ``--model`` and ``ANTHROPIC_MODEL`` plus every alias / auxiliary model knob
  (``ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL``, ``ANTHROPIC_SMALL_FAST_MODEL``,
  ``CLAUDE_CODE_SUBAGENT_MODEL``) are the run's model; the gateway pins the real one anyway.
* **Traffic.** ``CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC``, telemetry, error reporting, auto-update,
  experimental betas, auto-memory and feedback surveys are off; ``CLAUDE_CONFIG_DIR`` is in the fresh HOME.
* **Tools.** ``WebFetch`` / ``WebSearch`` are always denied; subagents (``Task``/``Agent``) are a preset
  option, default off; ``--tools`` takes the preset's allow-list (controlled presets).
* **Knobs.** ``--max-turns``; ``CLAUDE_CODE_MAX_OUTPUT_TOKENS``; ``MAX_THINKING_TOKENS`` (reasoning
  ``max_tokens`` mode) or ``--effort`` (``effort`` mode); ``BASH_DEFAULT_TIMEOUT_MS`` /
  ``BASH_MAX_TIMEOUT_MS``; ``DISABLE_PROMPT_CACHING`` (``caching.prompt_cache=off``);
  ``DISABLE_AUTO_COMPACT`` (compaction off) and ``CLAUDE_CODE_AUTO_COMPACT_WINDOW`` (context cap).
  ``--max-budget-usd`` is never used: the gateway is authoritative.
* **Request classes.** The binary knows ``x-claude-code-request-class`` values main / subagent / workflow /
  compaction / auxiliary / side_reply, but the smoke run showed **no** header on ``main`` calls: the gateway
  must count a missing header as ``main`` (unknown values as ``auxiliary``).

``vendor_affinity = "anthropic"``: Anthropic does not support Claude Code with non-Claude models; such
cells are legitimate but carry a harness-model affinity confound that results report.
"""

from __future__ import annotations

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
from backend.generation.harnesses.base import shell_quote
from backend.generation.harnesses.trajectory import common
from backend.generation.harnesses.trajectory.claude_stream import ClaudeStreamParser

VERSION: Final = "2.1.316"
DENIED_TOOLS: Final = ("WebFetch", "WebSearch")
SUBAGENT_TOOLS: Final = ("Task", "Agent")
SYSTEM_PROMPT_PATH: Final = f"{HARNESS_DIR}/claude-system.md"
# Claude Code's --effort levels; the policy's "none"/"minimal" map to the lowest one.
_EFFORT: Final = {"none": "low", "minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh"}
MODEL_ENV_KNOBS: Final = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
DISABLE_ENV: Final = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1",
    "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
    "DISABLE_COST_WARNINGS": "1",
}

FACTS: Final = HarnessFacts(
    facts_version=1,
    verified_against=(
        f"@anthropic-ai/claude-code@{VERSION} linux-x64 binary: --help, env-name scan, scripted-upstream smoke "
        "run, 2026-11-03"
    ),
    license="proprietary (Anthropic commercial terms)",
    install=f'npm install -g "@anthropic-ai/claude-code@{VERSION}" (platform binary via optionalDependencies)',
    headless_cmd=(
        "claude --bare -p --output-format stream-json --verbose --no-session-persistence "
        '--permission-mode bypassPermissions --max-turns N --model "$SLUG" --disallowedTools '
        '"WebFetch,WebSearch,Task,Agent" < /run/brief.md'
    ),
    front_protocol="anthropic_messages",
    protocol_modes=("passthrough", "translate"),
    base_url_env={"ANTHROPIC_BASE_URL": "{base_url}"},
    auth_env={"ANTHROPIC_API_KEY": "{api_key}"},
    model_flags="--model <slug> + ANTHROPIC_MODEL",
    aux_model_knobs=list(MODEL_ENV_KNOBS),
    disable_knobs={**DISABLE_ENV, "--bare": "", "--no-session-persistence": ""},
    max_turns_flag="--max-turns",
    compaction_knob="DISABLE_AUTO_COMPACT",
    context_knob="CLAUDE_CODE_AUTO_COMPACT_WINDOW",
    output_cap_knob="CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    reasoning_knob="MAX_THINKING_TOKENS / --effort",
    cache_knob="DISABLE_PROMPT_CACHING",
    tool_policy_flags="--disallowedTools / --tools",
    system_prompt_flags="--system-prompt-file / --append-system-prompt-file",
    path_restriction=None,
    system_prompt_capture="gateway_log",
    request_class_source="x-claude-code-request-class",
    min_call_output_tokens=16384,
    vendor_affinity="anthropic",
    trajectory_source=f"stdout ({STDOUT_FILE}, stream-json)",
    exit_codes={},
    crash_codes=(),
    system_prompt_normalizers=(
        r"^x-anthropic-billing-header: .*$",
        r"^Date: .*$",
        r"^CWD: .*$",
        r"^Today's date is .*$",
        r"^\s*Today's date: .*$",
        r"^\s*Working directory: .*$",
        r"^\s*Platform: .*$",
        r"^\s*OS Version: .*$",
        r"^\s*Is directory a git repo: .*$",
        r"^You are powered by the model .*$",
        r"(?s)gitStatus: .*?(?=\n\n|\Z)",
    ),
    unsupported_knobs=(),
    open_questions=(
        "OpenRouter /api/v1/messages: acceptance of provider routing fields and usage.cost in the native "
        "response (WP-06 passthrough; else /generation backfill + routing_partial)",
        "real-model smoke run through the WP-06 gateway on three model families (no paid calls in wave 2)",
    ),
)


class ClaudeCodeAdapter(HarnessAdapter):
    name: ClassVar[str] = "claude-code"
    label: ClassVar[str] = "Claude Code"
    description: ClassVar[str] = "Anthropic's agentic CLI in bare headless mode (Anthropic Messages protocol)."
    protocol: ClassVar[WireProtocol] = "anthropic_messages"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        agentic=True,
        requires_tool_calling=True,
        supports_max_turns=True,
        supports_system_prompt="both",
        supports_tool_policy=True,
        supports_compaction_control=True,
        supports_context_limit=True,
        supports_reasoning_effort_passthrough=True,
        supports_path_restriction=False,
        supports_request_class=True,
        headless_flag="--bare -p --output-format stream-json",
    )
    version_setting: ClassVar[str] = "AGENT_CLAUDE_CODE_VERSION"
    version_build_arg: ClassVar[str] = "CLAUDE_CODE_VERSION"
    image_dir: ClassVar[str] = "agent-claude-code"
    facts: ClassVar[HarnessFacts] = FACTS
    versions: ClassVar[tuple[str, ...]] = (VERSION,)
    wants_enabled: ClassVar[bool] = False
    blocked_reason: ClassVar[str] = (
        "passthrough needs WP-06's /v1/messages route verified against OpenRouter, and a real-model smoke run"
    )

    def image_spec(self, version: str) -> ImageSpec:
        return self.default_image_spec(version)

    def gateway_wiring(self, gateway_host: str) -> GatewayWiring:
        base_url = f"http://{gateway_host}"
        return GatewayWiring(
            protocol="anthropic_messages",
            protocol_mode="passthrough",
            base_url=base_url,
            env={"ANTHROPIC_BASE_URL": base_url},
        )

    def env(self, ctx: HarnessContext) -> dict[str, str]:
        k = knobs(ctx)
        env = {
            **common_env(),
            **DISABLE_ENV,
            "ANTHROPIC_BASE_URL": ctx.gateway.base_url,
            "ANTHROPIC_API_KEY": ctx.gateway.api_key,
            "CLAUDE_CONFIG_DIR": f"{HOME_DIR}/.claude",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(k.max_output_tokens),
            **dict.fromkeys(MODEL_ENV_KNOBS, ctx.model_name),
        }
        if k.reasoning_mode == "max_tokens" and k.reasoning_max_tokens:
            env["MAX_THINKING_TOKENS"] = str(k.reasoning_max_tokens)
        if k.command_timeout_s is not None:
            env["BASH_DEFAULT_TIMEOUT_MS"] = env["BASH_MAX_TIMEOUT_MS"] = str(k.command_timeout_s * 1000)
        if k.prompt_cache == "off":
            env["DISABLE_PROMPT_CACHING"] = "1"
        if k.compaction == "off":
            env["DISABLE_AUTO_COMPACT"] = "1"
        if k.context_tokens is not None:
            env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(k.context_tokens)
        return env

    def args(self, ctx: HarnessContext) -> list[str]:
        k = knobs(ctx)
        denied = [*DENIED_TOOLS, *([] if k.subagents else SUBAGENT_TOOLS), *k.tool_deny]
        args = [
            "--bare",
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--permission-mode", "bypassPermissions",
            "--model", ctx.model_name,
            "--disallowedTools", ",".join(dict.fromkeys(denied)),
        ]  # fmt: skip
        if k.max_turns is not None:
            args += ["--max-turns", str(k.max_turns)]
        if k.tool_allow is not None:
            args += ["--tools", ",".join(k.tool_allow)]
        if k.reasoning_mode == "effort" and k.reasoning_effort:
            args += ["--effort", _EFFORT[k.reasoning_effort]]
        if ctx.system_prompt is not None or k.pin_system_prompt:
            args += ["--system-prompt-file", SYSTEM_PROMPT_PATH]
        return args

    def render(self, ctx: HarnessContext) -> RenderedHarness:
        self.normalize_options(ctx.preset.options)
        files = {ctx.brief_path: ctx.brief.encode("utf-8")}
        if ctx.system_prompt is not None:
            files[SYSTEM_PROMPT_PATH] = ctx.system_prompt.encode("utf-8")
        elif knobs(ctx).pin_system_prompt:
            files[SYSTEM_PROMPT_PATH] = captured_prompt(self.name, ctx.preset.harness_version).encode("utf-8")
        args = " ".join(shell_quote(a) for a in self.args(ctx))
        return RenderedHarness(
            files=files,
            env=self.env(ctx),
            command=f"exec claude {args}",
            stdin_path=ctx.brief_path,
        )

    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory:
        parser = ClaudeStreamParser()
        parser.feed(outputs.get(STDOUT_FILE))
        trajectory = parser.finish(calls)
        return trajectory.model_copy(update={"parser": self.name})

    def observed_inputs(self, calls: list[dict[str, Any]]) -> ObservedInputs:
        return common.observed_inputs(calls, self.facts.system_prompt_normalizers)
