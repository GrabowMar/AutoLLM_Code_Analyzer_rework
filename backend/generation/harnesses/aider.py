"""Aider (https://aider.chat): edit-format pair programmer, headless single turn (ported from v2).

Aider needs no tool calling: the model answers with edit blocks that Aider applies, then Aider lints the
result and asks the model to fix lint errors (its native reflection loop). Headless, that is one
``--message-file`` turn. ``agentic=False``: a PromptSet must use the ``brief`` variant tagged ``editor``
(lint ``editor_harness_agent_brief``), and crossing Aider with agentic harnesses is flagged
``harness_brief_differs``. Pinned on top of the release:

* **Edit format** ``diff`` (by default) for every model; Aider otherwise picks it per model;
* **weak/editor model** = the run's model, so no hidden second model is used;
* **repo map** 1024 tokens (by default) for every model;
* **model metadata**: every model reaches Aider as ``openai/<slug>``, which Aider's database does not
  know, so every model gets the same metadata file (context window, the run's output cap);
* every seed source file except platform build files and lock files is added to the chat;
* no git commits, no .gitignore edits, no update checks or analytics, no shell-command suggestions
  (with ``--yes-always`` Aider would run them unattended), no URL detection;
* chat/LLM histories go to ``/logs``, so the workspace holds only the app.

Options (checked against the aider-chat 0.86.2 source in the pinned lock):

* ``edit_format``: ``--edit-format`` picks the coder class directly (an explicit format wins over the
  model's default), so every model gets the same editing protocol. ``whole`` resends entire files and
  needs large outputs (``min_call_output_tokens = 8192``).
* ``map_tokens``: ``--map-tokens`` sets ``RepoMap.max_map_tokens`` for every model (0 disables the map);
  the budget presets use 512 / 1024 / 2048 (frugal / standard / generous) as a C-class harness option.
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
from backend.generation.harnesses.base import STDOUT_FILE
from backend.generation.harnesses.base import HarnessAdapter
from backend.generation.harnesses.base import OptionField
from backend.generation.harnesses.base import common_env
from backend.generation.harnesses.base import json_bytes
from backend.generation.harnesses.base import knobs
from backend.generation.harnesses.base import shell_quote
from backend.generation.harnesses.trajectory import common
from backend.generation.harnesses.trajectory.aider import AiderParser

AIDER: Final = "/opt/harness/venv/bin/aider"
EDIT_FORMAT: Final = "diff"
MAP_TOKENS: Final = 1024
EDIT_FORMATS: Final = (
    ("diff", "Search/replace blocks (diff)"),
    ("whole", "Whole files (whole)"),
    ("udiff", "Unified diffs (udiff)"),
    ("diff-fenced", "Fenced search/replace blocks (diff-fenced)"),
)
# Files never handed to Aider for editing: the platform's build files and lock files.
NOT_ADDED: Final = (
    r"^(Dockerfile|\.dockerignore|docker-compose\.yml)$|(^|/)(package-lock\.json|uv\.lock|poetry\.lock)$"
)
TAGS_CACHE: Final = ".aider.tags.cache.v4"
METADATA_PATH: Final = f"{HARNESS_DIR}/aider-model-metadata.json"
VERSION: Final = "0.86.2"

FACTS: Final = HarnessFacts(
    facts_version=1,
    verified_against=f"aider-chat=={VERSION} (requirements.lock) source, v2 adapter, 2026-11-03",
    license="Apache-2.0",
    install="pip install --no-deps -r requirements.lock (aider-chat==0.86.2 on CPython 3.12)",
    headless_cmd=f"{AIDER} --message-file /run/brief.md --yes-always ... $(git ls-files | grep -vE <build files>)",
    front_protocol="openai_chat",
    protocol_modes=("native",),
    base_url_env={"--openai-api-base": "{base_url}"},
    auth_env={"--openai-api-key": "{api_key}"},
    model_flags="--model openai/<slug>",
    aux_model_knobs=["--weak-model", "--editor-model"],
    disable_knobs={
        "--no-check-update": "",
        "--analytics-disable": "",
        "--no-auto-commits": "",
        "--no-gitignore": "",
        "--no-suggest-shell-commands": "",
        "--no-detect-urls": "",
        "AIDER_ANALYTICS": "false",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    },
    max_turns_flag=None,
    compaction_knob=None,
    context_knob="model metadata max_input_tokens",
    output_cap_knob="model metadata max_output_tokens",
    reasoning_knob=None,
    cache_knob=None,
    tool_policy_flags=None,
    system_prompt_flags=None,
    path_restriction="files added to the chat (positional arguments)",
    system_prompt_capture="gateway_log",
    request_class_source="default:main",
    min_call_output_tokens=8192,
    vendor_affinity=None,
    trajectory_source=f"stdout ({STDOUT_FILE}) + {LOGS_DIR}/aider.chat.md",
    exit_codes={},
    crash_codes=(),
    system_prompt_normalizers=(),
    unsupported_knobs=(
        "run.max_turns",
        "context.compaction",
        "reasoning",
        "caching.prompt_cache",
        "time.command_timeout_s",
        "tool_policy",
        "subagents",
        "pin_system_prompt",
        "system_prompt",
    ),
    open_questions=(),
)


class AiderAdapter(HarnessAdapter):
    name: ClassVar[str] = "aider"
    label: ClassVar[str] = "Aider"
    description: ClassVar[str] = "Edit-format pair programmer: the model replies with edit blocks Aider applies."
    protocol: ClassVar[WireProtocol] = "openai_chat"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        agentic=False,
        requires_tool_calling=False,
        supports_max_turns=False,
        supports_system_prompt="none",
        supports_tool_policy=False,
        supports_compaction_control=False,
        supports_context_limit=True,
        supports_reasoning_effort_passthrough=False,
        supports_path_restriction=True,
        supports_request_class=False,
        headless_flag="--message-file (single turn)",
    )
    version_setting: ClassVar[str] = "AGENT_AIDER_VERSION"
    version_build_arg: ClassVar[str] = "AIDER_VERSION"
    image_dir: ClassVar[str] = "agent-aider"
    lock_files: ClassVar[tuple[str, ...]] = ("requirements.lock",)
    facts: ClassVar[HarnessFacts] = FACTS
    versions: ClassVar[tuple[str, ...]] = (VERSION,)
    wants_enabled: ClassVar[bool] = True
    option_fields: ClassVar[tuple[OptionField, ...]] = (
        OptionField(
            key="edit_format",
            type="enum",
            label="Edit format",
            help="How the model writes changes. 'whole' resends entire files, so it needs a large output cap.",
            default=EDIT_FORMAT,
            choices=EDIT_FORMATS,
        ),
        OptionField(
            key="map_tokens",
            type="int",
            label="Repo map tokens",
            help="Token budget for Aider's repository map; 0 disables the map.",
            default=MAP_TOKENS,
            min=0,
            max=8192,
        ),
    )

    def image_spec(self, version: str) -> ImageSpec:
        return self.default_image_spec(version)

    def gateway_wiring(self, gateway_host: str) -> GatewayWiring:
        return GatewayWiring(protocol="openai_chat", protocol_mode="native", base_url=f"http://{gateway_host}/api/v1")

    def args(self, ctx: HarnessContext) -> list[str]:
        opts = self.effective_options(ctx.preset.options)
        model = f"openai/{ctx.model_name}"
        return [
            "--model", model,
            "--weak-model", model,
            "--editor-model", model,
            "--openai-api-base", ctx.gateway.base_url,
            "--openai-api-key", ctx.gateway.api_key,
            "--edit-format", str(opts["edit_format"]),
            "--map-tokens", str(opts["map_tokens"]),
            "--yes-always",
            "--no-auto-commits",
            "--no-dirty-commits",
            "--no-gitignore",
            "--no-check-update",
            "--analytics-disable",
            "--no-show-model-warnings",
            "--no-show-release-notes",
            "--no-suggest-shell-commands",
            "--no-detect-urls",
            "--no-pretty",
            "--no-stream",
            "--no-fancy-input",
            "--chat-history-file", f"{LOGS_DIR}/aider.chat.md",
            "--input-history-file", f"{LOGS_DIR}/aider.input.md",
            "--llm-history-file", f"{LOGS_DIR}/aider.llm.md",
            "--model-metadata-file", METADATA_PATH,
            "--message-file", ctx.brief_path,
        ]  # fmt: skip

    def render(self, ctx: HarnessContext) -> RenderedHarness:
        k = knobs(ctx)
        model = f"openai/{ctx.model_name}"
        metadata = {
            model: {
                "max_input_tokens": k.context_window(ctx),
                "max_output_tokens": k.max_output_tokens,
                "max_tokens": k.max_output_tokens,
                "litellm_provider": "openai",
                "mode": "chat",
            },
        }
        args = " ".join(shell_quote(a) for a in self.args(ctx))
        files_to_add = ctx.slot.allowlist if ctx.slot is not None else None
        added = (
            " ".join(shell_quote(p) for p in files_to_add)
            if files_to_add is not None
            else f"$(git ls-files | grep -vE {shell_quote(NOT_ADDED)})"
        )
        return RenderedHarness(
            files={ctx.brief_path: ctx.brief.encode("utf-8"), METADATA_PATH: json_bytes(metadata)},
            env={**common_env(), "AIDER_ANALYTICS": "false", "LITELLM_LOCAL_MODEL_COST_MAP": "True"},
            # Not exec'd: Aider's repo-map tag cache lands in the workspace, so it is removed afterwards
            # (``timeout`` signals the whole process group, Aider included).
            command=f"{AIDER} {args} {added}\nstatus=$?\nrm -rf {TAGS_CACHE}\nexit $status",
            artifacts=(f"{LOGS_DIR}/aider.chat.md", f"{LOGS_DIR}/aider.llm.md"),
        )

    def restrict_paths(self, ctx: HarnessContext) -> RenderedHarness | None:
        """Aider edits only the files added to the chat: the slot allowlist (other paths stay read-only)."""
        return self.render(ctx) if ctx.slot is not None else None

    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory:
        parser = AiderParser()
        parser.feed(outputs.get(STDOUT_FILE))
        return parser.finish(calls)

    def observed_inputs(self, calls: list[dict[str, Any]]) -> ObservedInputs:
        return common.observed_inputs(calls, self.facts.system_prompt_normalizers)
