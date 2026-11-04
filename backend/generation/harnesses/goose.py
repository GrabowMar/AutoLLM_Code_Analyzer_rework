"""Goose (``aaif-goose/goose`` release binary): ``goose run --no-session --output-format stream-json`` (new).

Front protocol ``openai_chat`` (native; no gateway change). Verified against the v1.57.0 release binary
(``run --help``, env-name scan, ``ldd``) and a scripted-upstream smoke run:

* **Wiring.** ``GOOSE_PROVIDER=openai`` with ``OPENAI_HOST`` = the gateway and ``OPENAI_BASE_PATH``
  = ``api/v1/chat/completions``; ``GOOSE_MODEL`` (and ``GOOSE_SUBAGENT_MODEL``) = the run's model.
* **Extensions.** ``--no-profile --with-builtin developer``: only the developer extension (shell and file
  editing), never the user's profile extensions.
* **Traffic.** ``GOOSE_MODE=auto`` (no approvals), session naming off (removes an auxiliary call),
  telemetry and OTEL off, ``CONTEXT_FILE_NAMES='[]'`` (no AGENTS.md/.goosehints), keyring off.
* **Knobs.** ``--max-turns``; ``GOOSE_MAX_TOKENS`` (output cap); ``GOOSE_CONTEXT_LIMIT`` and
  ``GOOSE_AUTO_COMPACT_THRESHOLD`` (context / compaction; the threshold is a fraction of the limit, and
  ``0`` disables auto-compaction); ``GOOSE_SUBAGENT_MAX_TURNS``; ``GOOSE_SYSTEM_PROMPT_FILE_PATH``
  (system prompt override or pinned capture). ``GOOSE_CONTEXT_STRATEGY`` named in the spec does not exist
  in v1.57.0 (env scan), so it is not set.

Option: ``max_tool_repetitions`` (``--max-tool-repetitions``; identical tool calls in a row), checked in
the CLI source to apply to every provider and model alike; 0 leaves Goose's default (unlimited).
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
from backend.generation.harnesses.base import OptionField
from backend.generation.harnesses.base import captured_prompt
from backend.generation.harnesses.base import common_env
from backend.generation.harnesses.base import knobs
from backend.generation.harnesses.base import shell_quote
from backend.generation.harnesses.trajectory import common
from backend.generation.harnesses.trajectory.goose import GooseParser

VERSION: Final = "1.57.0"
SYSTEM_PROMPT_PATH: Final = f"{HARNESS_DIR}/goose-system.md"
COMPACT_THRESHOLD: Final = "0.8"

FACTS: Final = HarnessFacts(
    facts_version=2,
    verified_against=(
        f"goose v{VERSION} goose-x86_64-unknown-linux-gnu.tar.bz2 (sha256 e54036f3...): run --help, env scan, "
        "ldd, scripted-upstream smoke run, 2026-11-03"
    ),
    license="Apache-2.0",
    install="release tarball goose-x86_64-unknown-linux-gnu.tar.bz2 v1.57.0, sha256-verified in the Dockerfile",
    headless_cmd=(
        "goose run --no-session --no-profile --output-format stream-json --with-builtin developer "
        "--max-turns N -i /run/brief.md"
    ),
    front_protocol="openai_chat",
    protocol_modes=("native",),
    base_url_env={"OPENAI_HOST": "http://{gateway_host}", "OPENAI_BASE_PATH": "api/v1/chat/completions"},
    auth_env={"OPENAI_API_KEY": "{api_key}"},
    model_flags="GOOSE_PROVIDER=openai GOOSE_MODEL=<slug>",
    aux_model_knobs=["GOOSE_SUBAGENT_MODEL"],
    disable_knobs={
        "GOOSE_MODE": "auto",
        "GOOSE_DISABLE_SESSION_NAMING": "true",
        "GOOSE_TELEMETRY_ENABLED": "false",
        "GOOSE_TELEMETRY_OFF": "1",
        "OTEL_SDK_DISABLED": "true",
        "CONTEXT_FILE_NAMES": "[]",
        "GOOSE_DISABLE_KEYRING": "1",
        "--no-profile": "",
        "--no-session": "",
    },
    max_turns_flag="--max-turns",
    compaction_knob="GOOSE_AUTO_COMPACT_THRESHOLD",
    context_knob="GOOSE_CONTEXT_LIMIT",
    output_cap_knob="GOOSE_MAX_TOKENS",
    reasoning_knob=None,
    cache_knob=None,
    tool_policy_flags=None,
    system_prompt_flags="GOOSE_SYSTEM_PROMPT_FILE_PATH",
    path_restriction=None,
    system_prompt_capture="gateway_log",
    request_class_source="default:main",
    min_call_output_tokens=4096,
    vendor_affinity=None,
    trajectory_source=f"stdout ({STDOUT_FILE}, stream-json)",
    exit_codes={},
    crash_codes=(),
    system_prompt_normalizers=(
        r"(?i)^.*current date.*$",
        r"(?i)^.*\b\d{4}-\d{2}-\d{2}\b.*$",
        r"(?i)^\s*(cwd|working directory|current directory)\s*:.*$",
    ),
    unsupported_knobs=("reasoning", "caching.prompt_cache", "time.command_timeout_s", "tool_policy"),
    # E2E-13: the built-in skills question moved to GooseAdapter.exploratory_only (a tool-surface caveat
    # recorded with every cell, not a blocker for exploratory studies).
    open_questions=(),
)


class GooseAdapter(HarnessAdapter):
    name: ClassVar[str] = "goose"
    label: ClassVar[str] = "Goose"
    description: ClassVar[str] = "Block's open-source agent with the built-in developer extension only."
    protocol: ClassVar[WireProtocol] = "openai_chat"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        agentic=True,
        requires_tool_calling=True,
        supports_max_turns=True,
        supports_system_prompt="override",
        supports_tool_policy=False,
        supports_compaction_control=True,
        supports_context_limit=True,
        supports_reasoning_effort_passthrough=False,
        supports_path_restriction=False,
        supports_request_class=False,
        headless_flag="run --no-session --output-format stream-json",
    )
    version_setting: ClassVar[str] = "AGENT_GOOSE_VERSION"
    version_build_arg: ClassVar[str] = "GOOSE_VERSION"
    image_dir: ClassVar[str] = "agent-goose"
    facts: ClassVar[HarnessFacts] = FACTS
    versions: ClassVar[tuple[str, ...]] = (VERSION,)
    wants_enabled: ClassVar[bool] = True
    exploratory_only: ClassVar[str] = (
        "built-in skills (web-search via DuckDuckGo/Tavily/SearXNG, goose-doc-guide) and the load_skill tool are "
        "advertised even with --no-profile and no verified switch removes them (the sandbox has no web egress, so "
        "a web search fails); no real-model smoke run is logged yet"
    )
    option_fields: ClassVar[tuple[OptionField, ...]] = (
        OptionField(
            key="max_tool_repetitions",
            type="int",
            label="Max identical tool calls",
            help="Stop a run of identical consecutive tool calls after this many (0: Goose's default, unlimited).",
            default=0,
            min=0,
            max=100,
        ),
    )

    def image_spec(self, version: str) -> ImageSpec:
        return self.default_image_spec(version)

    def gateway_wiring(self, gateway_host: str) -> GatewayWiring:
        return GatewayWiring(
            protocol="openai_chat",
            protocol_mode="native",
            base_url=f"http://{gateway_host}/api/v1",
            env={"OPENAI_HOST": f"http://{gateway_host}", "OPENAI_BASE_PATH": "api/v1/chat/completions"},
        )

    def env(self, ctx: HarnessContext) -> dict[str, str]:
        k = knobs(ctx)
        host = ctx.gateway.env.get("OPENAI_HOST") or ctx.gateway.base_url.removesuffix("/api/v1")
        env = {
            **common_env(),
            "GOOSE_PROVIDER": "openai",
            "OPENAI_HOST": host,
            "OPENAI_BASE_PATH": ctx.gateway.env.get("OPENAI_BASE_PATH", "api/v1/chat/completions"),
            "OPENAI_API_KEY": ctx.gateway.api_key,
            "GOOSE_MODEL": ctx.model_name,
            "GOOSE_SUBAGENT_MODEL": ctx.model_name,
            "GOOSE_MODE": "auto",
            "GOOSE_DISABLE_SESSION_NAMING": "true",
            "GOOSE_TELEMETRY_ENABLED": "false",
            "GOOSE_TELEMETRY_OFF": "1",
            "OTEL_SDK_DISABLED": "true",
            "CONTEXT_FILE_NAMES": "[]",
            "GOOSE_DISABLE_KEYRING": "1",
            "GOOSE_MAX_TOKENS": str(k.max_output_tokens),
            "GOOSE_CONTEXT_LIMIT": str(k.context_window(ctx)),
        }
        if k.compaction == "off":
            env["GOOSE_AUTO_COMPACT_THRESHOLD"] = "0"
        elif k.compaction in {"auto", "auto_prune"}:
            env["GOOSE_AUTO_COMPACT_THRESHOLD"] = COMPACT_THRESHOLD
        if k.max_turns is not None:
            env["GOOSE_SUBAGENT_MAX_TURNS"] = str(k.max_turns)
        if ctx.system_prompt is not None or k.pin_system_prompt:
            env["GOOSE_SYSTEM_PROMPT_FILE_PATH"] = SYSTEM_PROMPT_PATH
        return env

    def render(self, ctx: HarnessContext) -> RenderedHarness:
        k = knobs(ctx)
        opts = self.effective_options(ctx.preset.options)
        files = {ctx.brief_path: ctx.brief.encode("utf-8")}
        if ctx.system_prompt is not None:
            files[SYSTEM_PROMPT_PATH] = ctx.system_prompt.encode("utf-8")
        elif k.pin_system_prompt:
            files[SYSTEM_PROMPT_PATH] = captured_prompt(self.name, ctx.preset.harness_version).encode("utf-8")
        args = [
            "run",
            "--no-session",
            "--no-profile",
            "--output-format", "stream-json",
            "--with-builtin", "developer",
        ]  # fmt: skip
        if k.max_turns is not None:
            args += ["--max-turns", str(k.max_turns)]
        if opts["max_tool_repetitions"]:
            args += ["--max-tool-repetitions", str(opts["max_tool_repetitions"])]
        args += ["-i", ctx.brief_path]
        return RenderedHarness(
            files=files,
            env=self.env(ctx),
            command="exec goose " + " ".join(shell_quote(a) for a in args),
        )

    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory:
        parser = GooseParser()
        parser.feed(outputs.get(STDOUT_FILE))
        return parser.finish(calls)

    def observed_inputs(self, calls: list[dict[str, Any]]) -> ObservedInputs:
        return common.observed_inputs(calls, self.facts.system_prompt_normalizers)
