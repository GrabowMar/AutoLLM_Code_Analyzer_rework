"""Harness adapter data contracts (spec sections 5.1 and 5.3).

The ``HarnessAdapter`` ABC itself lives in ``harnesses/base.py`` (WP-07); the
plain data it exchanges lives here so the runner, the gateway config builder
and the tests share one definition:

* frozen dataclasses (in-process values, never stored as JSON):
  ``HarnessCapabilities``, ``ImageSpec``, ``GatewayWiring``, ``HarnessContext``,
  ``RenderedHarness``, ``ExitInfo``, ``HarnessFacts``, ``SlotAgentContext``;
* pydantic models (stored as ``trajectory.json`` and served by the runs API):
  ``Step``, ``Trajectory``, ``ObservedInputs``.

``Termination`` is defined in ``contracts.outcome`` together with the
Termination x outcome table and re-exported here.
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Literal

from pydantic import Field

from backend.generation.contracts._base import ContractModel
from backend.generation.contracts._base import NonNegInt
from backend.generation.contracts._base import Sha256
from backend.generation.contracts.library import HarnessPresetContent
from backend.generation.contracts.outcome import Termination
from backend.generation.contracts.resources import EffectivePolicy
from backend.generation.contracts.slot import SlotAgentTarget

__all__ = [
    "ExitInfo",
    "GatewayWiring",
    "HarnessCapabilities",
    "HarnessContext",
    "HarnessFacts",
    "ImageSpec",
    "ObservedInputs",
    "ProtocolMode",
    "RenderedHarness",
    "SlotAgentContext",
    "Step",
    "Termination",
    "Trajectory",
    "WireProtocol",
]

WireProtocol = Literal["openai_chat", "openai_responses", "anthropic_messages", "gemini_generate"]
ProtocolMode = Literal["native", "passthrough", "translate"]


@dataclass(frozen=True)
class HarnessCapabilities:
    agentic: bool  # can run shell / iterate (False: aider = editor)
    requires_tool_calling: bool
    supports_max_turns: bool
    supports_system_prompt: Literal["none", "append", "override", "both"]
    supports_tool_policy: bool  # can deny/allow tools by name
    supports_compaction_control: bool
    supports_context_limit: bool
    supports_reasoning_effort_passthrough: bool  # False: the gateway forces it anyway
    supports_path_restriction: bool  # can be told to edit only some paths (slot agent); else allowlist-diff only
    supports_request_class: bool  # tags its requests with a class header the gateway can read
    headless_flag: str  # documentation only


@dataclass(frozen=True)
class ImageSpec:
    image: str  # images/<dir>
    build_args: dict[str, str]  # version pins
    lock_files: tuple[str, ...]  # requirements.lock / package-lock.json / Cargo.lock inside the image dir
    base: str = "agent-base"


@dataclass(frozen=True)
class GatewayWiring:
    protocol: WireProtocol
    protocol_mode: ProtocolMode
    base_url: str  # e.g. http://gateway:8080/v1  (anthropic: http://gateway:8080)
    api_key: str = "sk-llmlab-dummy"
    env: dict[str, str] = field(default_factory=dict)  # harness-specific env (ANTHROPIC_BASE_URL, ...)


@dataclass(frozen=True)
class SlotAgentContext:
    """Slot-agent extras for ``HarnessContext.slot`` (built by ``slot/executors/restricted_agent.prepare``)."""

    target: SlotAgentTarget
    allowlist: tuple[str, ...]
    description: str = ""  # neutral description of the part, from the slot_agent_brief context


@dataclass(frozen=True, kw_only=True)
class HarnessContext:
    """Everything an adapter may read; built by ``agentic/runner.py``."""

    run_id: str
    workspace: str = "/workspace"
    brief_path: str = "/run/brief.md"
    brief: str  # rendered brief (never passed via argv)
    system_prompt: str | None = None  # when PromptSet.system_prompt_mode != harness_native
    model_name: str  # the name the harness must send (gateway pins the real one)
    gateway: GatewayWiring
    preset: HarnessPresetContent
    policy: EffectivePolicy
    slot: SlotAgentContext | None = None  # slot agent only
    model_context_window: int | None = None  # the model's context length (manifest); None = unknown


@dataclass(frozen=True)
class RenderedHarness:
    files: dict[str, bytes]  # absolute in-container paths -> bytes (configs, brief copy, prompt files)
    env: dict[str, str]
    command: str  # shell command run under `timeout -k 15 <wall>` by the common entry script
    stdin_path: str | None = None
    artifacts: tuple[str, ...] = ()  # extra paths to collect into evidence (session logs)


@dataclass(frozen=True)
class ExitInfo:
    exit_code: int | None
    elapsed_s: float
    wall_clock_s: int
    idle_fired: bool
    oom_killed: bool
    budget_exhausted: str | None
    cancelled: bool
    gateway_calls_ok: int
    gateway_calls_failed_transport: int


@dataclass(frozen=True)
class HarnessFacts:
    """Verified per-harness facts (section 5.3); ``open_questions`` MUST be empty before enabling."""

    facts_version: int
    verified_against: str  # "<package>@<version> source/commit + date"
    license: str
    install: str  # pinned install line used by the image Dockerfile
    headless_cmd: str  # frozen command (brief via stdin or file, never argv)
    front_protocol: WireProtocol
    protocol_modes: tuple[ProtocolMode, ...]  # supported, in preference order
    base_url_env: dict[str, str]  # env names -> value template
    auth_env: dict[str, str]
    model_flags: str
    aux_model_knobs: list[str]  # every small/fast/subagent model knob -> the run's model
    disable_knobs: dict[str, str]  # telemetry/update/web/config-discovery switches
    max_turns_flag: str | None
    compaction_knob: str | None
    context_knob: str | None
    output_cap_knob: str | None
    reasoning_knob: str | None
    cache_knob: str | None
    tool_policy_flags: str | None
    system_prompt_flags: str | None
    path_restriction: str | None
    system_prompt_capture: Literal["gateway_log", "dump_env", "pinned_file"]
    request_class_source: str  # header name, or "default:main"
    min_call_output_tokens: int  # the resolver errors when call.max_output_tokens is below it
    vendor_affinity: str | None  # e.g. "anthropic" for Claude Code; reported with results
    trajectory_source: str  # stdout jsonl / session file path
    exit_codes: dict[int, str]  # documented harness exit codes -> meaning
    crash_codes: tuple[int, ...]
    system_prompt_normalizers: tuple[str, ...]  # regexes (date, cwd, git status, model name)
    unsupported_knobs: tuple[str, ...]
    open_questions: tuple[str, ...]

    @property
    def enabled(self) -> bool:
        return not self.open_questions

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict for ``GET /harnesses/{name}/facts/``."""
        out: dict[str, Any] = {}
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, tuple):
                value = list(value)
            elif name == "exit_codes":
                value = {str(code): meaning for code, meaning in value.items()}
            out[name] = value
        return out


# ---------------------------------------------------------------------------
# Parsed harness output (ported from services/agent/trajectory.py)
# ---------------------------------------------------------------------------


class Step(ContractModel):
    index: NonNegInt
    kind: Literal["llm", "tool", "edit", "shell", "message", "compaction", "subagent"]
    tool: str | None = None
    ok: bool | None = None
    started: float | None = None
    duration_s: float | None = None
    tokens_in: NonNegInt = 0
    tokens_out: NonNegInt = 0
    files: list[str] = Field(default_factory=list)
    command_sha: Sha256 | None = None


class Trajectory(ContractModel):
    steps: list[Step] = Field(default_factory=list)
    final_message: str | None = None
    final_message_truncated: bool = False
    tool_calls: NonNegInt = 0
    edits: NonNegInt = 0
    shell_commands: NonNegInt = 0
    compactions: NonNegInt = 0
    subagent_calls: NonNegInt = 0
    ended_on_llm_error: bool = False
    max_turns_hit: bool = False
    parser: str
    parser_version: NonNegInt
    unparsed_lines: NonNegInt = 0


class ObservedInputs(ContractModel):
    system_prompt_sha: Sha256 | None = None  # normalized by facts.system_prompt_normalizers
    tool_names: list[str] = Field(default_factory=list)
    tool_specs_sha: Sha256 | None = None
    first_user_message_sha: Sha256 | None = None
    calls_by_class: dict[Literal["main", "subagent", "compaction", "auxiliary"], NonNegInt] = Field(
        default_factory=dict,
    )
    native_system_prompt_blob: Sha256 | None = None  # from the first main-class call in the gateway log
