"""Qwen Code (``@qwen-code/qwen-code``): ``qwen --output-format stream-json`` with the brief on stdin (new).

Front protocol ``openai_chat`` (native; no gateway change). Verified against the 0.26.6 bundle (``--help``,
settings schema scan) and a scripted-upstream smoke run:

* **Wiring.** ``--auth-type openai`` and ``security.auth.selectedType = "openai"``; ``OPENAI_BASE_URL`` =
  the gateway, ``OPENAI_API_KEY`` = the dummy key, ``OPENAI_MODEL`` / ``-m`` = the run's model.
* **System prompt.** Qwen selects model-specific prompt profiles, so the prompt is **pinned** with
  ``QWEN_SYSTEM_MD`` (the captured default, ``captured/qwen-code-0.26.6.md``) in every preset; a PromptSet
  override replaces it.
* **Bare mode.** ``--bare`` skips implicit startup auto-discovery (context files, extensions, skills,
  MCP servers, memory) **and settings files**: the smoke run showed ``~/.qwen/settings.json`` ignored under
  ``--bare`` (``max_tokens`` stayed at Qwen's 64000 default), while without ``--bare`` the pinned prompt
  is extended (32.7k vs 15.9k chars), skills / tool_search tools appear and an extra auxiliary call is
  made. So the bundle is ``--bare`` with CLI flags only; chat recording is off by flag.
* **Knobs.** ``--max-session-turns`` (turn cap) and ``--max-tool-calls`` (4 x the request cap, a
  harness-side backstop). Output cap, context window, compaction and prompt caching have no CLI flag
  under ``--bare``: they are recorded as unsupported knobs and the gateway's limits apply
  (``call.max_output_tokens`` is forced on every request by the gateway).
* **Exit codes.** 53: the session turn cap (``MAX_TURNS``); 55: ``--max-tool-calls`` / ``--max-wall-time``
  exceeded. ``--max-wall-time`` is never passed (the entry script's timeout is the wall clock), so 55 is
  the tool-call backstop: a budget stop (``BUDGET``).
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
from backend.generation.harnesses.base import STDOUT_FILE
from backend.generation.harnesses.base import HarnessAdapter
from backend.generation.harnesses.base import captured_prompt
from backend.generation.harnesses.base import common_env
from backend.generation.harnesses.base import knobs
from backend.generation.harnesses.base import shell_quote
from backend.generation.harnesses.trajectory import common
from backend.generation.harnesses.trajectory.claude_stream import ClaudeStreamParser

VERSION: Final = "0.26.6"
SYSTEM_PROMPT_PATH: Final = f"{HARNESS_DIR}/qwen-system.md"
EXCLUDED_TOOLS: Final = ("web_fetch", "web_search")
SUBAGENT_TOOLS: Final = ("task", "agent")

FACTS: Final = HarnessFacts(
    facts_version=2,
    verified_against=f"@qwen-code/qwen-code@{VERSION}: --help, settings schema scan, scripted smoke run, 2026-11-03",
    license="Apache-2.0",
    install=f'npm install -g "@qwen-code/qwen-code@{VERSION}" (Node >= 22)',
    headless_cmd=(
        "qwen --bare --approval-mode yolo --auth-type openai --output-format stream-json -m <slug> "
        "--max-session-turns N --max-tool-calls M --exclude-tools web_fetch,web_search < /run/brief.md"
    ),
    front_protocol="openai_chat",
    protocol_modes=("native",),
    base_url_env={"OPENAI_BASE_URL": "{base_url}"},
    auth_env={"OPENAI_API_KEY": "{api_key}"},
    model_flags="-m <slug> + OPENAI_MODEL",
    aux_model_knobs=["OPENAI_MODEL", "--advisor off"],
    disable_knobs={"--bare": "", "--chat-recording": "false", "QWEN_SANDBOX": "false"},
    max_turns_flag="--max-session-turns",
    compaction_knob=None,
    context_knob=None,
    output_cap_knob=None,
    reasoning_knob=None,
    cache_knob=None,
    tool_policy_flags="--exclude-tools / --core-tools",
    system_prompt_flags="QWEN_SYSTEM_MD",
    path_restriction=None,
    system_prompt_capture="pinned_file",
    request_class_source="default:main",
    min_call_output_tokens=4096,
    vendor_affinity=None,
    trajectory_source=f"stdout ({STDOUT_FILE}, stream-json)",
    exit_codes={53: "max_turns", 55: "budget_exhausted"},
    crash_codes=(),
    system_prompt_normalizers=(
        r"(?i)^.*today'?s date.*$",
        r"(?i)^.*current working directory.*$",
        r"(?i)^\s*(platform|os|my operating system)\s*:.*$",
    ),
    unsupported_knobs=(
        "call.max_output_tokens",
        "context.max_context_tokens",
        "context.compaction",
        "reasoning",
        "caching.prompt_cache",
        "time.command_timeout_s",
    ),
    open_questions=(
        "usage statistics are sent even with --bare: the egress probe (HTTPS proxy, 2026-11-03) saw CONNECTs to "
        "*.rum.aliyuncs.com (Aliyun RUM) and models.dev; privacy.usageStatisticsEnabled lives in settings.json, "
        "which --bare ignores, and no verified flag or env var switches it off",
    ),
)


class QwenCodeAdapter(HarnessAdapter):
    name: ClassVar[str] = "qwen-code"
    label: ClassVar[str] = "Qwen Code"
    description: ClassVar[str] = "Qwen's Gemini-CLI-derived agent, headless stream-json, system prompt pinned."
    protocol: ClassVar[WireProtocol] = "openai_chat"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        agentic=True,
        requires_tool_calling=True,
        supports_max_turns=True,
        supports_system_prompt="override",
        supports_tool_policy=True,
        supports_compaction_control=False,
        supports_context_limit=False,
        supports_reasoning_effort_passthrough=False,
        supports_path_restriction=False,
        supports_request_class=False,
        headless_flag="--bare --output-format stream-json (stdin prompt)",
    )
    version_setting: ClassVar[str] = "AGENT_QWEN_CODE_VERSION"
    version_build_arg: ClassVar[str] = "QWEN_CODE_VERSION"
    image_dir: ClassVar[str] = "agent-qwen-code"
    facts: ClassVar[HarnessFacts] = FACTS
    versions: ClassVar[tuple[str, ...]] = (VERSION,)
    wants_enabled: ClassVar[bool] = False
    blocked_reason: ClassVar[str] = "facts verified offline; awaiting a real-model smoke run through the WP-06 gateway"

    def image_spec(self, version: str) -> ImageSpec:
        return self.default_image_spec(version)

    def gateway_wiring(self, gateway_host: str) -> GatewayWiring:
        return GatewayWiring(protocol="openai_chat", protocol_mode="native", base_url=f"http://{gateway_host}/api/v1")

    def _denied(self, ctx: HarnessContext) -> tuple[str, ...]:
        k = knobs(ctx)
        return (*k.tool_deny, *(() if k.subagents else SUBAGENT_TOOLS))

    def render(self, ctx: HarnessContext) -> RenderedHarness:
        self.normalize_options(ctx.preset.options)
        k = knobs(ctx)
        prompt = (
            ctx.system_prompt
            if ctx.system_prompt is not None
            else captured_prompt(self.name, ctx.preset.harness_version)
        )
        args = [
            "--bare",
            "--approval-mode", "yolo",
            "--auth-type", "openai",
            "--output-format", "stream-json",
            "--chat-recording", "false",
            "--telemetry", "false",
            "-m", ctx.model_name,
            "--max-tool-calls", str(k.max_llm_requests * 4),
            "--exclude-tools", ",".join(dict.fromkeys((*EXCLUDED_TOOLS, *self._denied(ctx)))),
        ]  # fmt: skip
        if k.max_turns is not None:
            args += ["--max-session-turns", str(k.max_turns)]
        if k.tool_allow is not None:
            args += ["--core-tools", ",".join(k.tool_allow)]
        return RenderedHarness(
            files={
                ctx.brief_path: ctx.brief.encode("utf-8"),
                SYSTEM_PROMPT_PATH: prompt.encode("utf-8"),
            },
            env={
                **common_env(),
                "OPENAI_BASE_URL": ctx.gateway.base_url,
                "OPENAI_API_KEY": ctx.gateway.api_key,
                "OPENAI_MODEL": ctx.model_name,
                "QWEN_SYSTEM_MD": SYSTEM_PROMPT_PATH,
                "QWEN_SANDBOX": "false",
            },
            command="exec qwen " + " ".join(shell_quote(a) for a in args),
            stdin_path=ctx.brief_path,
        )

    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory:
        parser = ClaudeStreamParser()
        parser.feed(outputs.get(STDOUT_FILE))
        return parser.finish(calls).model_copy(update={"parser": self.name})

    def observed_inputs(self, calls: list[dict[str, Any]]) -> ObservedInputs:
        return common.observed_inputs(calls, self.facts.system_prompt_normalizers)
