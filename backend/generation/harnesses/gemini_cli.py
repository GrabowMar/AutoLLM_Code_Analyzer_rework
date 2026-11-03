"""Gemini CLI (``@google/gemini-cli``): ``gemini --skip-trust --approval-mode=yolo -o stream-json`` (wave 3).

Gemini CLI speaks only the Gemini API (``POST {base}/v1beta/models/{model}:streamGenerateContent?alt=sse``,
``:generateContent``, ``:countTokens``, ``x-goog-api-key``), so the gateway needs WP-06's ``gemini_generate``
translator (``translate`` is the only mode: OpenRouter has no native Gemini endpoint). Until the translator
is listed in ``settings.GATEWAY_TRANSLATORS`` the registry keeps the adapter blocked and the plan resolver
reports ``harness_protocol_unavailable_for_endpoint``.

The CLI refuses a plain-HTTP ``GOOGLE_GEMINI_BASE_URL`` unless it points at localhost, so the command starts a
small Node TCP forwarder (``/run/harness/loopback.js``, process title ``llmlab-loopback``) on
``127.0.0.1:18080 -> llm-gateway:8080`` first; a shell loop restarts it when it exits, and a dead forwarder
ends the run as ``INFRA`` (zero successful calls with transport failures).

Verified against the pinned release in its image (scripted-upstream smoke runs, 2026-11-03; docs/HARNESSES.md):

* **Headless.** Piped stdin is the prompt (no ``-p``; the brief never reaches argv). ``--skip-trust`` is
  required: without it the CLI exits 55 (``FatalUntrustedWorkspaceError``). ``GEMINI_SANDBOX=false`` keeps
  yolo from starting its Docker sandbox. Exit codes: 53 = ``model.maxSessionTurns`` reached
  (``FatalTurnLimitedError``); 41/42/44/52/54/55 are fatal auth/input/sandbox/config/tool/trust errors.
* **Settings.** The release reads a *system* settings file (highest precedence) only from a root-owned
  directory, and the sandbox writes every rendered file as the sandbox user. So the run-independent switches
  are baked into the image as ``/etc/gemini-cli/settings.json`` (``images/agent-gemini-cli/system-settings.json``,
  equal to :data:`SYSTEM_SETTINGS`; a test pins the two) and the per-run values (model, turn cap,
  compression, model overrides, tool policy) are rendered as user settings (``~/.gemini/settings.json``).
  Off: auto-update and its notice, checkpointing, session retention, usage statistics, telemetry, IDE mode,
  plan mode (its exit needs an interactive approval), subagents (``experimental.enableAgents``), agent
  skills, hooks, MCP servers and extensions (``admin.*``), the Gemma router, auto-memory and local ``.env``
  files; ``model.skipNextSpeakerCheck`` and ``model.disableLoopDetection`` remove two auxiliary calls;
  ``tools.disableLLMCorrection`` keeps edit failures from calling the model; ``general.maxAttempts = 1``
  leaves retries to the gateway.
* **Project config.** Workspace settings sit between user and system settings, and ``GEMINI.md`` cannot be
  switched off as a context file (verified: a workspace ``GEMINI.md`` lands in the first user message). The
  command therefore refuses to start (exit 52, the CLI's config-error code, with a message on stderr) when
  the workspace carries ``.gemini/settings.json``, ``.gemini/.env`` or any ``GEMINI.md`` (section 5.2 rule 2;
  starters carry none, a parent app in slot mode might). Files the agent writes during its own run are its
  own notes, not project config.
* **Models.** ``-m <slug>`` and ``model.name`` present the run's model. Vendor-prefixed slugs (every
  OpenRouter id, e.g. ``deepseek/...`` or ``google/gemini-3.1-pro-preview``) are unknown to the CLI and get its
  generic ``chat-base`` config (temperature 1, topP 0.95, topK 64, ``includeThoughts``) and the same tool set.
  Bare CLI model names are not: ``gemini-3-pro-preview`` is rewritten to ``gemini-3.1-pro-preview`` (thinking
  level HIGH), and ``gemini-2.5-*`` names get a legacy prompt variant and the ``write_todos`` tool. Every
  internal auxiliary model alias of the release (compression, summarizers, edit fixers, classifier, ...) is
  pinned to the run's model with ``modelConfigs.overrides``; the main model's override carries
  ``maxOutputTokens`` (verified in the request's ``generationConfig``).
* **Compaction does not fire headless.** The CLI only splits history at user messages that are not tool
  results, and a single-prompt headless run has none after the brief, so compression is a no-op for the whole
  run (verified with a 1,000-token cap over five calls). ``context.compaction`` and
  ``context.max_context_tokens`` are therefore unsupported knobs (the gateway's input cap applies). The
  threshold is still rendered, as the native 50 % rule scaled to the real window (the CLI assumes 1,048,576
  tokens for every model): ``0.5 x window / 1,048,576``, ``compaction=off`` -> 1.0.
* **System prompt** pinned with ``GEMINI_SYSTEM_MD``: the release's default captured with
  ``GEMINI_WRITE_SYSTEM_MD`` under the same settings (identical for vendor-prefixed and bare Gemini-3 names;
  bare Gemini-2.5 names get another variant, which the pin removes).
* **Egress.** With every switch above, a run behind an HTTPS proxy makes no request other than its model
  calls; the positive control (system settings removed) contacts ``play.googleapis.com`` (usage statistics).
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
from backend.generation.harnesses.base import GATEWAY_HOST
from backend.generation.harnesses.base import HARNESS_DIR
from backend.generation.harnesses.base import HOME_DIR
from backend.generation.harnesses.base import STDOUT_FILE
from backend.generation.harnesses.base import HarnessAdapter
from backend.generation.harnesses.base import captured_prompt
from backend.generation.harnesses.base import common_env
from backend.generation.harnesses.base import json_bytes
from backend.generation.harnesses.base import knobs
from backend.generation.harnesses.base import shell_quote
from backend.generation.harnesses.trajectory import common
from backend.generation.harnesses.trajectory.gemini_cli import GeminiParser

VERSION: Final = "0.66.0"
LOOPBACK_PORT: Final = 18080
FORWARDER_PATH: Final = f"{HARNESS_DIR}/loopback.js"
SETTINGS_PATH: Final = f"{HOME_DIR}/.gemini/settings.json"  # user settings (per run)
SYSTEM_SETTINGS_PATH: Final = "/etc/gemini-cli/settings.json"  # baked into the image (root-owned)
SYSTEM_PROMPT_PATH: Final = f"{HARNESS_DIR}/gemini-system.md"
EXCLUDED_TOOLS: Final = ("web_fetch", "google_web_search")
# Declared even with skills and subagents off, but inert (no skills, no agents): removed from the tool list
# so every model sees the same working tool set.
INERT_TOOLS: Final = ("activate_skill", "invoke_agent")
# The CLI's token limit for every non-Gemma model (``tokenLimit()`` in 0.66.0); compressionThreshold is a
# fraction of it, whatever the real model's window is.
CLI_TOKEN_LIMIT: Final = 1_048_576
NATIVE_COMPRESSION_FRACTION: Final = 0.5  # DEFAULT_COMPRESSION_TOKEN_THRESHOLD
# Every internal model alias of 0.66.0 that is not a chat model (``DEFAULT_MODEL_CONFIGS.aliases``): each is
# pinned to the run's model (section 5.2 rule 3). Most are unreachable with the settings above; pinning them
# all keeps a stray auxiliary call on the run's model.
AUX_MODEL_ALIASES: Final = (
    "classifier",
    "prompt-completion",
    "fast-ack-helper",
    "edit-corrector",
    "summarizer-default",
    "summarizer-shell",
    "web-search",
    "web-fetch",
    "web-fetch-fallback",
    "loop-detection",
    "loop-detection-double-check",
    "llm-edit-fixer",
    "next-speaker-checker",
    "context-snapshotter",
    "chat-compression-3-pro",
    "chat-compression-3-flash",
    "chat-compression-3.1-flash-lite",
    "chat-compression-2.5-pro",
    "chat-compression-2.5-flash",
    "chat-compression-2.5-flash-lite",
    "chat-compression-default",
    "agent-history-provider-summarizer",
)

# ~20 lines of Node: a byte-level TCP relay. A neutral process title keeps agents that kill "node"
# servers by name from matching it by accident; the shell loop restarts it when it exits anyway.
FORWARDER_JS: Final = """'use strict';
// llm-lab loopback relay: 127.0.0.1:{port} -> {upstream} (harnesses/gemini_cli.py)
process.title = 'llmlab-loopback';
const net = require('net');
const [host, port] = '{upstream}'.split(':');
const server = net.createServer((client) => {{
  const upstream = net.connect(Number(port), host);
  client.pipe(upstream);
  upstream.pipe(client);
  const close = () => {{ client.destroy(); upstream.destroy(); }};
  client.on('error', close);
  upstream.on('error', close);
}});
server.listen({port}, '127.0.0.1');
"""

# Run-independent switches: images/agent-gemini-cli/system-settings.json, copied to SYSTEM_SETTINGS_PATH
# (the CLI skips a system settings file whose directory is not root-owned, so it cannot be rendered per run).
SYSTEM_SETTINGS: Final[dict[str, Any]] = {
    "general": {
        "enableAutoUpdate": False,
        "enableAutoUpdateNotification": False,
        "checkpointing": {"enabled": False},
        "sessionRetention": {"enabled": False},
        "plan": {"enabled": False},
        "maxAttempts": 1,
        "retryFetchErrors": False,
    },
    "privacy": {"usageStatisticsEnabled": False},
    "telemetry": {"enabled": False, "logPrompts": False},
    "ide": {"enabled": False},
    "security": {"auth": {"selectedType": "gemini-api-key"}},
    "model": {"skipNextSpeakerCheck": True, "disableLoopDetection": True},
    "tools": {"disableLLMCorrection": True},
    "experimental": {"enableAgents": False, "gemmaModelRouter": {"enabled": False}, "autoMemory": False},
    "skills": {"enabled": False},
    "hooksConfig": {"enabled": False},
    "admin": {"extensions": {"enabled": False}, "mcp": {"enabled": False}, "skills": {"enabled": False}},
    "advanced": {"ignoreLocalEnv": True},
}
# Refuse a workspace whose own Gemini project config the CLI would read (exit 52, its config-error code):
# ``.gemini/settings.json`` / ``.gemini/.env`` sit above the per-run user settings, and ``GEMINI.md`` is always
# a context file name in 0.66.0 (``context.fileName`` only adds names; it is loaded at start and, in
# subdirectories, when a file next to it is read). Starters never carry these; a parent app might.
# find's output is never word-split (a ``GEMINI.md`` under a path with whitespace must still refuse): the two
# fixed paths are tested directly and any GEMINI.md hit is only checked for non-emptiness.
WORKSPACE_GUARD: Final = (
    "for f in .gemini/settings.json .gemini/.env; do\n"
    '  if [ -e "$f" ] || [ -L "$f" ]; then\n'
    '    echo "llm-lab: workspace $f is Gemini CLI project config; refusing to run" >&2; exit 52\n'
    "  fi\n"
    "done\n"
    "f=$(find . -name node_modules -prune -o -name GEMINI.md -print -quit)\n"
    'if [ -n "$f" ]; then\n'
    '  echo "llm-lab: workspace $f is Gemini CLI project config; refusing to run" >&2; exit 52\n'
    "fi"
)

FACTS: Final = HarnessFacts(
    facts_version=2,
    verified_against=(
        f"@google/gemini-cli@{VERSION} (bundle and bundled docs in llmlab-v3-wt/agent-gemini-cli:dev; "
        "scripted-upstream smoke runs), 2026-11-03"
    ),
    license="Apache-2.0",
    install=f'npm install -g "@google/gemini-cli@{VERSION}"',
    headless_cmd=(
        "gemini --skip-trust --approval-mode=yolo -o stream-json -m <slug> < /run/brief.md "
        f"(loopback :{LOOPBACK_PORT} first)"
    ),
    front_protocol="gemini_generate",
    protocol_modes=("translate",),
    base_url_env={"GOOGLE_GEMINI_BASE_URL": f"http://127.0.0.1:{LOOPBACK_PORT}"},
    auth_env={"GEMINI_API_KEY": "{api_key}"},
    model_flags="-m <slug> + model.name",
    aux_model_knobs=[f"modelConfigs.overrides[{alias}].model" for alias in AUX_MODEL_ALIASES],
    disable_knobs={
        "--skip-trust": "",
        "GEMINI_SANDBOX": "false",
        "GEMINI_TELEMETRY_ENABLED": "false",
        "GEMINI_CLI_NO_RELAUNCH": "true",
        "telemetry.enabled": "false",
        "privacy.usageStatisticsEnabled": "false",
        "general.enableAutoUpdate": "false",
        "general.enableAutoUpdateNotification": "false",
        "general.checkpointing.enabled": "false",
        "general.sessionRetention.enabled": "false",
        "general.plan.enabled": "false",
        "ide.enabled": "false",
        "experimental.enableAgents": "false",
        "experimental.gemmaModelRouter.enabled": "false",
        "experimental.autoMemory": "false",
        "skills.enabled": "false",
        "hooksConfig.enabled": "false",
        "admin.extensions.enabled": "false",
        "admin.mcp.enabled": "false",
        "admin.skills.enabled": "false",
        "advanced.ignoreLocalEnv": "true",
    },
    max_turns_flag="model.maxSessionTurns",
    compaction_knob=None,  # model.compressionThreshold is rendered but never fires headless
    context_knob=None,
    output_cap_knob="modelConfigs.overrides[<slug>].generateContentConfig.maxOutputTokens",
    reasoning_knob=None,
    cache_knob=None,
    tool_policy_flags="tools.exclude / tools.core",
    system_prompt_flags="GEMINI_SYSTEM_MD",
    path_restriction=None,
    system_prompt_capture="dump_env",
    request_class_source="default:main",
    min_call_output_tokens=4096,
    vendor_affinity="google",
    trajectory_source=f"stdout ({STDOUT_FILE}, stream-json)",
    exit_codes={53: "max_turns"},
    crash_codes=(),
    system_prompt_normalizers=(
        r"(?i)^.*today'?s date.*$",
        r"(?i)^.*current working directory.*$",
        r"(?i)^.*operating system.*$",
        r"(?i)^.*temporary directory.*$",
    ),
    unsupported_knobs=(
        "context.max_context_tokens",
        "context.compaction",
        "reasoning",
        "caching.prompt_cache",
        "time.command_timeout_s",
        "subagents",
    ),
    open_questions=(),
)


class GeminiCliAdapter(HarnessAdapter):
    name: ClassVar[str] = "gemini-cli"
    label: ClassVar[str] = "Gemini CLI"
    description: ClassVar[str] = "Google's Gemini CLI through the gateway's Gemini translator, system prompt pinned."
    protocol: ClassVar[WireProtocol] = "gemini_generate"
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
        headless_flag="--skip-trust --approval-mode=yolo -o stream-json (stdin prompt)",
    )
    version_setting: ClassVar[str] = "AGENT_GEMINI_CLI_VERSION"
    version_build_arg: ClassVar[str] = "GEMINI_CLI_VERSION"
    image_dir: ClassVar[str] = "agent-gemini-cli"
    facts: ClassVar[HarnessFacts] = FACTS
    versions: ClassVar[tuple[str, ...]] = (VERSION,)
    wants_enabled: ClassVar[bool] = False
    blocked_reason: ClassVar[str] = (
        "facts verified offline (scripted smoke runs); awaiting a real-model smoke run through the WP-06 "
        "gemini_generate translator"
    )

    def image_spec(self, version: str) -> ImageSpec:
        return self.default_image_spec(version)

    def gateway_wiring(self, gateway_host: str) -> GatewayWiring:
        return GatewayWiring(
            protocol="gemini_generate",
            protocol_mode="translate",
            base_url=f"http://127.0.0.1:{LOOPBACK_PORT}",
            env={"LLMLAB_LOOPBACK_UPSTREAM": gateway_host},
        )

    @staticmethod
    def compression_threshold(ctx: HarnessContext) -> float:
        """``model.compressionThreshold``: the native 50 % rule scaled to the real window (1.0 = off)."""
        k = knobs(ctx)
        if k.compaction == "off":
            return 1.0
        window = k.context_window(ctx)
        return round(min(NATIVE_COMPRESSION_FRACTION * window / CLI_TOKEN_LIMIT, 1.0), 6)

    def settings(self, ctx: HarnessContext) -> dict[str, Any]:
        """The per-run user settings (``~/.gemini/settings.json``); the static switches are SYSTEM_SETTINGS."""
        k = knobs(ctx)
        model: dict[str, Any] = {"name": ctx.model_name, "compressionThreshold": self.compression_threshold(ctx)}
        if k.max_turns is not None:
            model["maxSessionTurns"] = k.max_turns
        overrides: list[dict[str, Any]] = [
            {"match": {"model": alias}, "modelConfig": {"model": ctx.model_name}} for alias in AUX_MODEL_ALIASES
        ]
        overrides.append(
            {
                "match": {"model": ctx.model_name},
                "modelConfig": {"generateContentConfig": {"maxOutputTokens": k.max_output_tokens}},
            },
        )
        tools: dict[str, Any] = {"exclude": list(dict.fromkeys((*EXCLUDED_TOOLS, *INERT_TOOLS, *k.tool_deny)))}
        if k.tool_allow is not None:
            tools["core"] = list(k.tool_allow)
        return {"model": model, "modelConfigs": {"overrides": overrides}, "tools": tools}

    def render(self, ctx: HarnessContext) -> RenderedHarness:
        self.normalize_options(ctx.preset.options)
        upstream = ctx.gateway.env.get("LLMLAB_LOOPBACK_UPSTREAM", GATEWAY_HOST)
        prompt = (
            ctx.system_prompt
            if ctx.system_prompt is not None
            else captured_prompt(self.name, ctx.preset.harness_version)
        )
        args = ["--skip-trust", "--approval-mode=yolo", "-o", "stream-json", "-m", ctx.model_name]
        forwarder = FORWARDER_JS.format(port=LOOPBACK_PORT, upstream=upstream)
        command = (
            f"{WORKSPACE_GUARD}\n(while :; do node {FORWARDER_PATH}; sleep 0.2; done) &\nsleep 0.3\nexec gemini "
            + " ".join(shell_quote(a) for a in args)
        )
        return RenderedHarness(
            files={
                ctx.brief_path: ctx.brief.encode("utf-8"),
                FORWARDER_PATH: forwarder.encode("utf-8"),
                SETTINGS_PATH: json_bytes(self.settings(ctx)),
                SYSTEM_PROMPT_PATH: prompt.encode("utf-8"),
            },
            env={
                **common_env(),
                "GOOGLE_GEMINI_BASE_URL": ctx.gateway.base_url,
                "GEMINI_API_KEY": ctx.gateway.api_key,
                "GEMINI_SANDBOX": "false",
                "GEMINI_SYSTEM_MD": SYSTEM_PROMPT_PATH,
                "GEMINI_TELEMETRY_ENABLED": "false",
                "GEMINI_CLI_NO_RELAUNCH": "true",
            },
            command=command,
            stdin_path=ctx.brief_path,
        )

    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory:
        parser = GeminiParser()
        parser.feed(outputs.get(STDOUT_FILE))
        return parser.finish(calls)

    def observed_inputs(self, calls: list[dict[str, Any]]) -> ObservedInputs:
        return common.observed_inputs(calls, self.facts.system_prompt_normalizers)
