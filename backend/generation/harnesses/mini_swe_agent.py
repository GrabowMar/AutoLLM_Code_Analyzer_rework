"""mini-swe-agent (https://mini-swe-agent.com): the SWE-agent team's minimal bash-only loop (ported from v2).

It runs with its native ``mini.yaml`` prompts and loop; only the integration points are set:

* the model via LiteLLM's OpenAI-compatible client pointed at the gateway (``model_kwargs.api_base``);
* ``mode: yolo`` / ``confirm_exit: false`` (no human to confirm);
* ``step_limit`` = the run's turn cap, never more than ``run.max_llm_requests`` (the gateway enforces the
  request cap too); ``cost_limit = 0`` (off: the gateway owns cost);
* ``environment.timeout`` = the command timeout (``time.command_timeout_s``, which overrides the
  preset's ``command_timeout_s`` when set; mini's own default of 30 s when neither is set). Checked against the
  2.4.6 source: the CLI deep-merges every ``-c`` file and the ``LocalEnvironment`` is built from the
  merged ``environment`` mapping, so the value applies to every command of every model;
* the brief goes in as a config file (``run.task``), never argv.

No compaction (history grows linearly), so ``context_knob`` is None and the policy lint
``call_cap_without_compaction`` applies. Its prompts never vary by model (``mini.yaml`` is one template),
so the native prompt is already pinned; a PromptSet override replaces ``agent.system_template``.
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
from backend.generation.harnesses.base import LOGS_DIR
from backend.generation.harnesses.base import HarnessAdapter
from backend.generation.harnesses.base import common_env
from backend.generation.harnesses.base import json_bytes
from backend.generation.harnesses.base import knobs
from backend.generation.harnesses.trajectory import common
from backend.generation.harnesses.trajectory.mini_swe_agent import MiniSweAgentParser

MINI: Final = "/opt/harness/venv/bin/mini"
TRAJECTORY_FILE: Final = "traj.json"
CONFIG_PATH: Final = f"{HARNESS_DIR}/mini-llmlab.yaml"
TASK_PATH: Final = f"{HARNESS_DIR}/mini-task.yaml"
VERSION: Final = "2.4.6"

FACTS: Final = HarnessFacts(
    facts_version=1,
    verified_against=f"mini-swe-agent=={VERSION} (requirements.lock) source, v2 adapter, 2026-11-03",
    license="MIT",
    install="pip install --no-deps -r requirements.lock (mini-swe-agent==2.4.6 + pinned transitive deps)",
    headless_cmd=f"{MINI} -c mini.yaml -c {CONFIG_PATH} -c {TASK_PATH} --exit-immediately -o {LOGS_DIR}/traj.json",
    front_protocol="openai_chat",
    protocol_modes=("native",),
    base_url_env={"model.model_kwargs.api_base": "{base_url}"},
    auth_env={"model.model_kwargs.api_key": "{api_key}"},
    model_flags="model.model_name = openai/<slug>",
    aux_model_knobs=[],
    disable_knobs={
        "MSWEA_CONFIGURED": "true",
        "MSWEA_SILENT_STARTUP": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "model.cost_tracking": "ignore_errors",
    },
    max_turns_flag="agent.step_limit",
    compaction_knob=None,
    context_knob=None,
    output_cap_knob="model.model_kwargs.max_tokens",
    reasoning_knob=None,
    cache_knob=None,
    tool_policy_flags=None,
    system_prompt_flags="agent.system_template",
    path_restriction=None,
    system_prompt_capture="gateway_log",
    request_class_source="default:main",
    min_call_output_tokens=2048,
    vendor_affinity=None,
    trajectory_source=f"{LOGS_DIR}/{TRAJECTORY_FILE}",
    exit_codes={},
    crash_codes=(),
    system_prompt_normalizers=(),
    unsupported_knobs=(
        "context.max_context_tokens",
        "context.compaction",
        "reasoning",
        "caching.prompt_cache",
        "tool_policy",
        "subagents",
    ),
    open_questions=(),
)


class MiniSweAgentAdapter(HarnessAdapter):
    name: ClassVar[str] = "mini-swe-agent"
    label: ClassVar[str] = "mini-swe-agent"
    description: ClassVar[str] = "The SWE-agent team's minimal bash-only loop: one shell command per step."
    protocol: ClassVar[WireProtocol] = "openai_chat"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        agentic=True,
        requires_tool_calling=True,
        supports_max_turns=True,
        supports_system_prompt="override",
        supports_tool_policy=False,
        supports_compaction_control=False,
        supports_context_limit=False,
        supports_reasoning_effort_passthrough=False,
        supports_path_restriction=False,
        supports_request_class=False,
        headless_flag="--exit-immediately (yolo mode)",
    )
    version_setting: ClassVar[str] = "AGENT_MINI_SWE_AGENT_VERSION"
    version_build_arg: ClassVar[str] = "MINI_SWE_AGENT_VERSION"
    image_dir: ClassVar[str] = "agent-mini-swe-agent"
    lock_files: ClassVar[tuple[str, ...]] = ("requirements.lock",)
    facts: ClassVar[HarnessFacts] = FACTS
    versions: ClassVar[tuple[str, ...]] = (VERSION,)
    wants_enabled: ClassVar[bool] = True
    brief_copies: ClassVar[tuple[str, ...]] = (TASK_PATH,)

    def image_spec(self, version: str) -> ImageSpec:
        return self.default_image_spec(version)

    def gateway_wiring(self, gateway_host: str) -> GatewayWiring:
        return GatewayWiring(protocol="openai_chat", protocol_mode="native", base_url=f"http://{gateway_host}/api/v1")

    def config(self, ctx: HarnessContext) -> dict[str, Any]:
        k = knobs(ctx)
        step_limit = min(k.max_turns, k.max_llm_requests) if k.max_turns is not None else k.max_llm_requests
        agent: dict[str, Any] = {
            "mode": "yolo",
            "confirm_exit": False,
            "step_limit": int(step_limit),
            "cost_limit": 0,
            "output_path": f"{LOGS_DIR}/{TRAJECTORY_FILE}",
        }
        if ctx.system_prompt is not None:
            agent["system_template"] = ctx.system_prompt
        config: dict[str, Any] = {
            "agent": agent,
            "model": {
                "model_name": f"openai/{ctx.model_name}",
                "cost_tracking": "ignore_errors",
                "model_kwargs": {
                    "api_base": ctx.gateway.base_url,
                    "api_key": ctx.gateway.api_key,
                    "drop_params": True,
                    "max_tokens": k.max_output_tokens,
                },
            },
        }
        if k.command_timeout_s is not None:
            config["environment"] = {"timeout": int(k.command_timeout_s)}
        return config

    def render(self, ctx: HarnessContext) -> RenderedHarness:
        self.normalize_options(ctx.preset.options)
        return RenderedHarness(
            files={
                ctx.brief_path: ctx.brief.encode("utf-8"),
                # JSON is YAML: both files are data, never on a command line.
                CONFIG_PATH: json_bytes(self.config(ctx)),
                TASK_PATH: json_bytes({"run": {"task": ctx.brief}}),
            },
            env={
                **common_env(),
                "MSWEA_CONFIGURED": "true",
                "MSWEA_SILENT_STARTUP": "1",
                "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            },
            # ``-o`` explicitly: mini's CLI merges its own ``--output`` default over ``agent.output_path``.
            command=(
                f"exec {MINI} -c mini.yaml -c {CONFIG_PATH} -c {TASK_PATH} --exit-immediately "
                f"-o {LOGS_DIR}/{TRAJECTORY_FILE}"
            ),
            artifacts=(f"{LOGS_DIR}/{TRAJECTORY_FILE}",),
        )

    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory:
        return MiniSweAgentParser().result(outputs.get(TRAJECTORY_FILE), calls)

    def observed_inputs(self, calls: list[dict[str, Any]]) -> ObservedInputs:
        return common.observed_inputs(calls, self.facts.system_prompt_normalizers)
