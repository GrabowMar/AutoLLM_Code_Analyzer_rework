"""OpenCode (https://opencode.ai): headless ``opencode run --format json --pure`` (ported from v2).

Pinned on top of the release (all verified against the ``opencode-ai@1.18.35`` binary):

* **System prompt.** OpenCode picks a different base prompt per model family for the ``build`` agent and
  the ``general`` subagent, which would confound cross-model comparison. Both get OpenCode's own
  model-agnostic default prompt, captured verbatim (``captured/opencode-1.18.35.md``). A PromptSet with
  ``system_prompt_mode != harness_native`` overrides it (``ctx.system_prompt``).
* **Tools.** ``webfetch``, ``websearch`` and ``skill`` are disabled, plus every tool the preset denies;
  the ``task`` subagent tool is native and stays on unless the preset turns subagents off. The tool set
  varies by model id (GPT-family ids get ``apply_patch`` instead of ``edit``/``write``): the observed
  ``tool_names`` record it (note ``tool_set_varies``).
* **Auxiliary calls.** ``small_model`` is the run's model and a constant session title is passed in
  (``--title llm-lab-run``, no run id, so ``command_sha`` holds per cell), so no hidden request names the session.
* **Discovery.** Project/user config, AGENTS.md/CLAUDE.md, external skills and plugins are off.
* **Permissions.** Everything allowed inside the sandbox except the interactive ``doom_loop`` prompt
  (``deny``, explicit) and ``external_directory``.
* **Knobs.** ``limit.context`` / ``limit.output`` (context window, output cap), compaction through
  ``OPENCODE_DISABLE_AUTOCOMPACT`` / ``OPENCODE_DISABLE_PRUNE``, the shell timeout through
  ``OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS``. No turn cap (the gateway's ``max_llm_requests``
  applies), no reasoning knob (the gateway forces it).

Options: none (see the v2 adapter's note: nothing in OpenCode's config acts identically on every model
family in this release).
"""

from __future__ import annotations

import json
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
from backend.generation.harnesses.base import BRIEF_PATH
from backend.generation.harnesses.base import STDOUT_FILE
from backend.generation.harnesses.base import HarnessAdapter
from backend.generation.harnesses.base import captured_prompt
from backend.generation.harnesses.base import common_env
from backend.generation.harnesses.base import json_bytes
from backend.generation.harnesses.base import knobs
from backend.generation.harnesses.trajectory import common
from backend.generation.harnesses.trajectory.opencode import OpenCodeParser

DISABLED_TOOLS: Final = ("webfetch", "websearch", "skill")
SUBAGENT_TOOLS: Final = ("task",)
CONFIG_PATH: Final = "/run/harness/opencode.json"
# A constant session title: it skips OpenCode's hidden title-generation call and keeps the run id out of the
# command, so ``command_sha`` is equal for every run of a cell (plan-time render, WP-10 ask).
RUN_TITLE: Final = "llm-lab-run"
HARNESS_ENV: Final = {
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_SHARE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
}
VERSION: Final = "1.18.35"

FACTS: Final = HarnessFacts(
    facts_version=1,
    verified_against=f"opencode-ai@{VERSION} binary (v2 adapter + env-name scan of the binary), 2026-11-03",
    license="MIT",
    install=f'npm install -g "opencode-ai@{VERSION}"',
    headless_cmd=f"opencode run --format json --pure --title {RUN_TITLE} < {BRIEF_PATH}",
    front_protocol="openai_chat",
    protocol_modes=("native",),
    base_url_env={"OPENCODE_CONFIG_CONTENT": "provider.openrouter.options.baseURL = {base_url}"},
    auth_env={"OPENCODE_CONFIG_CONTENT": "provider.openrouter.options.apiKey = {api_key}"},
    model_flags="config model = small_model = openrouter/<slug>",
    aux_model_knobs=["small_model"],
    disable_knobs={
        **HARNESS_ENV,
        "config.autoupdate": "false",
        "config.share": "disabled",
        "config.snapshot": "false",
    },
    max_turns_flag=None,
    compaction_knob="OPENCODE_DISABLE_AUTOCOMPACT / OPENCODE_DISABLE_PRUNE",
    context_knob="provider.models.<slug>.limit.context",
    output_cap_knob="provider.models.<slug>.limit.output",
    reasoning_knob=None,
    cache_knob=None,
    tool_policy_flags="config tools.<name> = false (deny only)",
    system_prompt_flags="config agent.{build,general}.prompt",
    path_restriction=None,
    system_prompt_capture="pinned_file",
    request_class_source="default:main",
    min_call_output_tokens=4096,
    vendor_affinity=None,
    trajectory_source=f"stdout ({STDOUT_FILE}, --format json events)",
    exit_codes={},
    crash_codes=(),
    system_prompt_normalizers=(
        r"^You are powered by the model named .*$",
        r"^\s*Today's date: .*$",
        r"^\s*Working directory: .*$",
        r"^\s*Platform: .*$",
        r"^\s*Is directory a git repo: .*$",
    ),
    unsupported_knobs=("run.max_turns", "reasoning", "caching.prompt_cache"),
    open_questions=(),
)


class OpenCodeAdapter(HarnessAdapter):
    name: ClassVar[str] = "opencode"
    label: ClassVar[str] = "OpenCode"
    description: ClassVar[str] = "OpenCode's headless coding agent: native tool set, pinned model-agnostic prompt."
    protocol: ClassVar[WireProtocol] = "openai_chat"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        agentic=True,
        requires_tool_calling=True,
        supports_max_turns=False,
        supports_system_prompt="override",
        supports_tool_policy=True,
        supports_compaction_control=True,
        supports_context_limit=True,
        supports_reasoning_effort_passthrough=False,
        supports_path_restriction=False,
        supports_request_class=False,
        headless_flag="run --format json --pure",
    )
    version_setting: ClassVar[str] = "AGENT_OPENCODE_VERSION"
    version_build_arg: ClassVar[str] = "OPENCODE_VERSION"
    image_dir: ClassVar[str] = "agent-opencode"
    facts: ClassVar[HarnessFacts] = FACTS
    versions: ClassVar[tuple[str, ...]] = (VERSION,)
    wants_enabled: ClassVar[bool] = True

    def image_spec(self, version: str) -> ImageSpec:
        return self.default_image_spec(version)

    def gateway_wiring(self, gateway_host: str) -> GatewayWiring:
        return GatewayWiring(protocol="openai_chat", protocol_mode="native", base_url=f"http://{gateway_host}/api/v1")

    def config(self, ctx: HarnessContext) -> dict[str, Any]:
        """The ``opencode.json`` content (also passed as ``OPENCODE_CONFIG_CONTENT``)."""
        k = knobs(ctx)
        prompt = (
            ctx.system_prompt
            if ctx.system_prompt is not None
            else captured_prompt(self.name, ctx.preset.harness_version)
        )
        model_ref = f"openrouter/{ctx.model_name}"
        tools = dict.fromkeys((*DISABLED_TOOLS, *k.tool_deny), False)
        if not k.subagents:
            tools.update(dict.fromkeys(SUBAGENT_TOOLS, False))
        return {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            # Per-step undo snapshots copy the worktree into a shadow git repo: useless headless.
            "snapshot": False,
            "model": model_ref,
            "small_model": model_ref,
            "enabled_providers": ["openrouter"],
            "provider": {
                "openrouter": {
                    "options": {"baseURL": ctx.gateway.base_url, "apiKey": ctx.gateway.api_key},
                    "models": {
                        ctx.model_name: {
                            "name": ctx.model_name,
                            "tool_call": True,
                            "reasoning": True,
                            "limit": {"context": k.context_window(ctx), "output": k.max_output_tokens},
                        },
                    },
                },
            },
            "agent": {"build": {"prompt": prompt}, "general": {"prompt": prompt}},
            "permission": {
                "edit": "allow",
                "bash": "allow",
                "webfetch": "deny",
                "external_directory": "deny",
                "doom_loop": "deny",
            },
            "tools": tools,
        }

    def render(self, ctx: HarnessContext) -> RenderedHarness:
        self.normalize_options(ctx.preset.options)  # no options exist: rejects any
        k = knobs(ctx)
        config = self.config(ctx)
        env = {**common_env(), **HARNESS_ENV, "OPENCODE_CONFIG_CONTENT": json.dumps(config, sort_keys=True)}
        if k.compaction == "off":
            env.update({"OPENCODE_DISABLE_AUTOCOMPACT": "1", "OPENCODE_DISABLE_PRUNE": "1"})
        elif k.compaction == "auto":
            env["OPENCODE_DISABLE_PRUNE"] = "1"
        if k.command_timeout_s is not None:
            env["OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS"] = str(k.command_timeout_s * 1000)
        return RenderedHarness(
            files={ctx.brief_path: ctx.brief.encode("utf-8"), CONFIG_PATH: json_bytes(config)},
            env=env,
            command=f"exec opencode run --format json --pure --title {RUN_TITLE}",
            stdin_path=ctx.brief_path,
        )

    def unsupported_knobs(self, ctx: HarnessContext) -> tuple[str, ...]:
        extra = ("tool_policy.allow",) if knobs(ctx).tool_allow is not None else ()
        return (*super().unsupported_knobs(ctx), *extra)

    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory:
        parser = OpenCodeParser()
        parser.feed(outputs.get(STDOUT_FILE))
        return parser.finish(calls)

    def observed_inputs(self, calls: list[dict[str, Any]]) -> ObservedInputs:
        return common.observed_inputs(calls, self.facts.system_prompt_normalizers)
