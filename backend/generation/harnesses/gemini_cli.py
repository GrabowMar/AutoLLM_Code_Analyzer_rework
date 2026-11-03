"""Gemini CLI (``@google/gemini-cli``): ``gemini --approval-mode=yolo -o stream-json`` (wave 3).

Gemini CLI speaks only the Gemini API (``POST {base}/v1beta/models/{model}:streamGenerateContent?alt=sse``,
``:generateContent``, ``:countTokens``, ``x-goog-api-key``), so the gateway needs WP-06's ``gemini_generate``
translator (``translate`` is the only mode: OpenRouter has no native Gemini endpoint). The CLI refuses a
plain-HTTP ``GOOGLE_GEMINI_BASE_URL`` unless it points at localhost, so the command starts a small Node
TCP forwarder (``/run/harness/loopback.js``, process title ``llmlab-loopback``) on
``127.0.0.1:18080 -> llm-gateway:8080`` first; it restarts when killed, and a dead forwarder ends the run as
``INFRA`` (zero successful calls with transport failures).

Settings (``~/.gemini/settings.json``): ``model.skipNextSpeakerCheck``, ``model.disableLoopDetection``,
explicit ``-m`` (no ``auto`` router), ``model.compressionThreshold`` (compaction), ``model.maxSessionTurns``
(turn cap), ``tools.exclude`` (web tools), telemetry, auto-update and checkpointing off,
``GEMINI_SANDBOX=false`` (yolo would otherwise enable the Docker sandbox). The system prompt is pinned with
``GEMINI_SYSTEM_MD`` (captured with ``GEMINI_WRITE_SYSTEM_MD``; it has model-dependent variants).

Disabled until wave 3: the translator does not exist yet, and the open questions below are unverified.
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
SETTINGS_PATH: Final = f"{HOME_DIR}/.gemini/settings.json"
SYSTEM_PROMPT_PATH: Final = f"{HARNESS_DIR}/gemini-system.md"
EXCLUDED_TOOLS: Final = ("web_fetch", "google_web_search")

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

FACTS: Final = HarnessFacts(
    facts_version=1,
    verified_against=f"@google/gemini-cli@{VERSION}: spec section 5.3 only (not yet verified), 2026-11-03",
    license="Apache-2.0",
    install=f'npm install -g "@google/gemini-cli@{VERSION}"',
    headless_cmd=f"gemini --approval-mode=yolo -o stream-json -m <slug> < /run/brief.md (loopback :{LOOPBACK_PORT})",
    front_protocol="gemini_generate",
    protocol_modes=("translate",),
    base_url_env={"GOOGLE_GEMINI_BASE_URL": f"http://127.0.0.1:{LOOPBACK_PORT}"},
    auth_env={"GEMINI_API_KEY": "{api_key}"},
    model_flags="-m <slug>",
    aux_model_knobs=["-m (no auto router)"],
    disable_knobs={
        "GEMINI_SANDBOX": "false",
        "telemetry.enabled": "false",
        "general.disableAutoUpdate": "true",
        "general.checkpointing.enabled": "false",
        "experimental.gemmaModelRouter.enabled": "false",
        "privacy.usageStatisticsEnabled": "false",
    },
    max_turns_flag="model.maxSessionTurns",
    compaction_knob="model.compressionThreshold",
    context_knob="model.compressionThreshold",
    output_cap_knob="generationConfig.maxOutputTokens (via the translator)",
    reasoning_knob="thinkingConfig (via the translator)",
    cache_knob=None,
    tool_policy_flags="tools.exclude",
    system_prompt_flags="GEMINI_SYSTEM_MD",
    path_restriction=None,
    system_prompt_capture="dump_env",
    request_class_source="default:main",
    min_call_output_tokens=4096,
    vendor_affinity="google",
    trajectory_source=f"stdout ({STDOUT_FILE}, stream-json)",
    exit_codes={},
    crash_codes=(),
    system_prompt_normalizers=(r"(?i)^.*today'?s date.*$", r"(?i)^.*current working directory.*$"),
    unsupported_knobs=("caching.prompt_cache", "time.command_timeout_s"),
    open_questions=(
        "WP-06 gemini_generate translator (thoughtSignature round trip, countTokens local) not available yet",
        "stdin-only prompt in headless mode (no -p) for the pinned version",
        "generic model config for non-Gemini slugs (the CLI validates model names)",
        "GEMINI_SANDBOX=false overriding the yolo default in the pinned version",
        "captured default prompt (GEMINI_WRITE_SYSTEM_MD) per model-dependent variant",
    ),
)


class GeminiCliAdapter(HarnessAdapter):
    name: ClassVar[str] = "gemini-cli"
    label: ClassVar[str] = "Gemini CLI"
    description: ClassVar[str] = "Google's Gemini CLI through the gateway's Gemini translator (wave 3)."
    protocol: ClassVar[WireProtocol] = "gemini_generate"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        agentic=True,
        requires_tool_calling=True,
        supports_max_turns=True,
        supports_system_prompt="override",
        supports_tool_policy=True,
        supports_compaction_control=True,
        supports_context_limit=True,
        supports_reasoning_effort_passthrough=False,
        supports_path_restriction=False,
        supports_request_class=False,
        headless_flag="-o stream-json --approval-mode=yolo",
    )
    version_setting: ClassVar[str] = "AGENT_GEMINI_CLI_VERSION"
    version_build_arg: ClassVar[str] = "GEMINI_CLI_VERSION"
    image_dir: ClassVar[str] = "agent-gemini-cli"
    facts: ClassVar[HarnessFacts] = FACTS
    versions: ClassVar[tuple[str, ...]] = (VERSION,)
    wants_enabled: ClassVar[bool] = False
    blocked_reason: ClassVar[str] = "wave 3: needs the WP-06 gemini_generate translator and the open questions closed"

    def image_spec(self, version: str) -> ImageSpec:
        return self.default_image_spec(version)

    def gateway_wiring(self, gateway_host: str) -> GatewayWiring:
        return GatewayWiring(
            protocol="gemini_generate",
            protocol_mode="translate",
            base_url=f"http://127.0.0.1:{LOOPBACK_PORT}",
            env={"LLMLAB_LOOPBACK_UPSTREAM": gateway_host},
        )

    def settings(self, ctx: HarnessContext) -> dict[str, Any]:
        k = knobs(ctx)
        model: dict[str, Any] = {"name": ctx.model_name, "skipNextSpeakerCheck": True, "disableLoopDetection": True}
        if k.max_turns is not None:
            model["maxSessionTurns"] = k.max_turns
        if k.compaction == "off":
            model["compressionThreshold"] = 1.0
        elif k.context_tokens is not None:
            model["compressionThreshold"] = round(min(k.context_tokens / k.context_window(ctx), 1.0), 3)
        return {
            "general": {"disableAutoUpdate": True, "checkpointing": {"enabled": False}},
            "privacy": {"usageStatisticsEnabled": False},
            "telemetry": {"enabled": False},
            "experimental": {"gemmaModelRouter": {"enabled": False}},
            "security": {"auth": {"selectedType": "gemini-api-key"}},
            "model": model,
            "tools": {"exclude": list(dict.fromkeys((*EXCLUDED_TOOLS, *k.tool_deny)))},
            "context": {"fileName": []},
        }

    def render(self, ctx: HarnessContext) -> RenderedHarness:
        self.normalize_options(ctx.preset.options)
        upstream = ctx.gateway.env.get("LLMLAB_LOOPBACK_UPSTREAM", GATEWAY_HOST)
        prompt = (
            ctx.system_prompt
            if ctx.system_prompt is not None
            else captured_prompt(self.name, ctx.preset.harness_version)
        )
        args = ["--approval-mode=yolo", "-o", "stream-json", "-m", ctx.model_name]
        forwarder = FORWARDER_JS.format(port=LOOPBACK_PORT, upstream=upstream)
        command = f"(while :; do node {FORWARDER_PATH}; sleep 0.2; done) &\nsleep 0.3\nexec gemini " + " ".join(
            shell_quote(a) for a in args
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
