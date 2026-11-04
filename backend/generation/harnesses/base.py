"""The ``HarnessAdapter`` ABC and the sandbox plumbing every adapter shares (spec sections 5.1, 5.2, 6.5).

A *harness* is the agent program that drives the model (OpenCode, mini-swe-agent, Aider, Claude Code,
Codex, Goose, Qwen Code, Gemini CLI). Results compare **harness bundles**: a harness with its native
system prompt, tool set and request behaviour (section 5.5). The rules that keep that comparison fair
(section 5.2) are implemented here once and applied by every adapter:

1. the brief reaches the harness through a file or stdin, never argv (``BRIEF_PATH``);
2. no auto-update, telemetry, sharing, web tools or user/project config discovery: every harness gets a
   fresh ``HOME`` (``HOME_DIR``) outside ``/workspace`` and its own disable switches (``facts.disable_knobs``);
3. the gateway pins the model; the adapter presents ``ctx.model_name`` and sets every auxiliary-model knob
   to the same name (``facts.aux_model_knobs``);
4. approvals are fully automatic (the container is the boundary);
5. turn caps, compaction and the context limit are mapped where ``capabilities`` says so; every knob the
   harness cannot map is reported by :meth:`HarnessAdapter.unsupported_knobs` for the manifest;
6. the native system prompt is pinned from ``harnesses/captured/<name>-<version>.md`` where the adapter
   says so, and is always *observed* from the gateway log (:meth:`HarnessAdapter.observed_inputs`);
7. harness-side cost caps are never set: the gateway is authoritative.

The data the ABC exchanges (``HarnessContext``, ``RenderedHarness``, ``HarnessFacts``, ...) lives in
``contracts.harness``. :func:`entry_script` / :func:`sandbox_payload` are the common entry script of
section 6.5, which ``agentic/sandbox.py`` (WP-08) runs in the agent container.
"""

from __future__ import annotations

import json
import re
import shlex
from abc import ABC
from abc import abstractmethod
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar
from typing import Final

from backend.generation import identity
from backend.generation.contracts.outcome import Termination

if TYPE_CHECKING:
    from backend.generation.contracts.harness import ExitInfo
    from backend.generation.contracts.harness import GatewayWiring
    from backend.generation.contracts.harness import HarnessCapabilities
    from backend.generation.contracts.harness import HarnessContext
    from backend.generation.contracts.harness import HarnessFacts
    from backend.generation.contracts.harness import ImageSpec
    from backend.generation.contracts.harness import ObservedInputs
    from backend.generation.contracts.harness import ProtocolMode
    from backend.generation.contracts.harness import RenderedHarness
    from backend.generation.contracts.harness import Trajectory
    from backend.generation.contracts.harness import WireProtocol
    from backend.generation.harnesses.trajectory.common import IncrementalParser

__all__ = [
    "BRIEF_PATH",
    "CAPTURED_DIR",
    "COMMAND_SCRIPT",
    "DUMMY_API_KEY",
    "ENTRY_SCRIPT",
    "EXIT_FILE",
    "GATEWAY_ALIAS",
    "GATEWAY_HOST",
    "GATEWAY_PORT",
    "HARNESS_DIR",
    "HOME_DIR",
    "KNOB_KEYS",
    "LOGS_DIR",
    "OPTION_TYPES",
    "SANDBOX_REVISION",
    "STDERR_FILE",
    "STDOUT_FILE",
    "WORKSPACE_DIR",
    "CapturedPromptMissingError",
    "HarnessAdapter",
    "Knobs",
    "OptionField",
    "SandboxPayload",
    "captured_path",
    "captured_prompt",
    "command_script",
    "common_env",
    "default_termination",
    "entry_script",
    "json_bytes",
    "knobs",
    "normalize_options",
    "sandbox_payload",
    "shell_quote",
]

# -- sandbox layout (section 6.5) -----------------------------------------------------------------------

GATEWAY_ALIAS: Final = "llm-gateway"
GATEWAY_PORT: Final = 8080
GATEWAY_HOST: Final = f"{GATEWAY_ALIAS}:{GATEWAY_PORT}"
WORKSPACE_DIR: Final = "/workspace"
RUN_DIR: Final = "/run"
BRIEF_PATH: Final = f"{RUN_DIR}/brief.md"
HARNESS_DIR: Final = f"{RUN_DIR}/harness"  # rendered configs, prompt files, the command script
HOME_DIR: Final = f"{RUN_DIR}/home"  # the fresh HOME of every harness (no user config, no login)
LOGS_DIR: Final = "/logs"  # gateway logs, harness stdout/stderr, exit record, patch, workspace tar
STDOUT_FILE: Final = "harness.out"
STDERR_FILE: Final = "harness.err"
EXIT_FILE: Final = "exit.json"
COMMAND_SCRIPT: Final = f"{HARNESS_DIR}/command.sh"
ENTRY_SCRIPT: Final = f"{RUN_DIR}/entry.sh"
# The agent only ever sees this placeholder; the gateway holds the real key.
DUMMY_API_KEY: Final = "sk-llmlab-dummy"
# Bump whenever the environment an agent sees changes without an image change (entry script, layout, env).
SANDBOX_REVISION: Final = 5
# Anchored .git/info/exclude patterns (section 6.5 step 2). Nested directories are never excluded.
GIT_EXCLUDES: Final = ("/node_modules/", "/.venv/", "/venv/", "/data/*.db", "/.tmp/")
# Anchored tar excludes (section 6.5 step 5).
TAR_EXCLUDES: Final = ("./node_modules", "./.venv", "./venv", "./.git")

CAPTURED_DIR: Final = Path(__file__).resolve().parent / "captured"


class CapturedPromptMissingError(LookupError):
    """No captured native prompt for ``<harness>-<version>``: capture it before pinning that version."""


def captured_path(name: str, version: str, suffix: str = "") -> Path:
    """``harnesses/captured/<name>-<version><suffix>.md``."""
    return CAPTURED_DIR / f"{name}-{version}{suffix}.md"


def captured_prompt(name: str, version: str, suffix: str = "") -> str:
    """The captured native system prompt of ``name@version`` (verbatim, section 5.2 rule 6)."""
    path = captured_path(name, version, suffix)
    if not path.is_file():
        msg = (
            f"no captured native prompt {path.name}: capture it from a smoke run (docs/HARNESSES.md) before "
            f"pinning {name} {version}"
        )
        raise CapturedPromptMissingError(msg)
    return path.read_text(encoding="utf-8")


# -- options (ported from the v2 adapter base) -----------------------------------------------------------

OPTION_TYPES: Final = ("enum", "int", "bool", "multi_enum")


@dataclass(frozen=True)
class OptionField:
    """One user-settable harness option (rendered by the UI from ``to_dict``).

    An option qualifies only when it is verified, in the pinned release's source, to act identically on
    every model; the adapter docstring records that check.
    """

    key: str
    type: str  # one of OPTION_TYPES
    label: str
    help: str
    default: Any
    choices: tuple[tuple[str, str], ...] = ()  # (value, label) for enum / multi_enum
    min: int | None = None
    max: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "key": self.key,
            "type": self.type,
            "label": self.label,
            "help": self.help,
            "default": list(self.default) if isinstance(self.default, (list, tuple)) else self.default,
        }
        if self.choices:
            data["choices"] = [{"value": value, "label": label} for value, label in self.choices]
        if self.min is not None:
            data["min"] = self.min
        if self.max is not None:
            data["max"] = self.max
        return data

    def coerce(self, value: Any) -> tuple[Any, str | None]:
        """``(normalized value, error)`` for a user-supplied ``value``."""
        coercer = getattr(self, f"_coerce_{self.type}", None)
        if coercer is None:
            return None, f"{self.key} has an unknown option type {self.type!r}"
        return coercer(value)

    def _choice_values(self) -> list[str]:
        return [value for value, _label in self.choices]

    def _coerce_enum(self, value: Any) -> tuple[Any, str | None]:
        if value not in self._choice_values():
            return None, f"{self.key} must be one of {', '.join(self._choice_values())}"
        return value, None

    def _coerce_bool(self, value: Any) -> tuple[Any, str | None]:
        if not isinstance(value, bool):
            return None, f"{self.key} must be true or false"
        return value, None

    def _coerce_int(self, value: Any) -> tuple[Any, str | None]:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer()
        in_range = ok and (self.min is None or value >= self.min) and (self.max is None or value <= self.max)
        if not in_range:
            return None, f"{self.key} must be an integer between {self.min} and {self.max}"
        return int(value), None

    def _coerce_multi_enum(self, value: Any) -> tuple[Any, str | None]:
        values = self._choice_values()
        if not isinstance(value, list) or not all(isinstance(v, str) and v in values for v in value):
            return None, f"{self.key} must be a list of {', '.join(values)}"
        # Choice order, without duplicates: equal sets hash equal.
        return [v for v in values if v in value], None


def normalize_options(
    fields: tuple[OptionField, ...],
    options: Mapping[str, Any] | None,
    *,
    harness: str,
) -> dict[str, Any]:
    """Validate ``options`` against ``fields``; returns only the keys that differ from the defaults.

    Raises ``ValueError`` listing every problem. Dropping default values means ``{}`` and
    ``{"edit_format": "diff"}`` are the same configuration (same preset content hash, same render).
    """
    if options is None:
        return {}
    if not isinstance(options, Mapping):
        msg = "harness options must be an object"
        raise ValueError(msg)  # noqa: TRY004 - one exception type for every validation problem
    by_key = {f.key: f for f in fields}
    errors: list[str] = []
    unknown = sorted(set(options) - set(by_key))
    if unknown:
        allowed = ", ".join(by_key) or "none"
        errors.append(f"Unknown {harness} options: {', '.join(unknown)} (allowed: {allowed})")
    normalized: dict[str, Any] = {}
    for key, spec in by_key.items():
        if key not in options or options[key] is None:
            continue
        value, error = spec.coerce(options[key])
        if error:
            errors.append(error)
            continue
        default = list(spec.default) if isinstance(spec.default, tuple) else spec.default
        if value != default:
            normalized[key] = value
    if errors:
        raise ValueError("; ".join(errors))
    return normalized


# -- knob resolution (section 5.3, "Budget knob mapping per harness") -----------------------------------


@dataclass(frozen=True)
class Knobs:
    """The harness-relevant view of ``ctx.preset`` + ``ctx.policy`` that ``render()`` maps.

    Precedence: the turn cap is the stricter of ``preset.max_turns`` and the policy's ``run.max_turns``, so a
    budget preset can only tighten a harness preset, never loosen it. The command timeout follows the 4 knob
    table: ``time.command_timeout_s`` overrides the preset's ``command_timeout_s`` when set (the researcher's
    budget choice wins); the preset's value applies only when the policy leaves it null.
    """

    max_turns: int | None
    max_llm_requests: int
    max_aux_requests: int
    max_output_tokens: int
    max_input_tokens: int | None
    context_tokens: int | None  # context.max_context_tokens (explicit) or None
    compaction: str  # harness_default | auto | auto_prune | off
    command_timeout_s: int | None
    reasoning_mode: str
    reasoning_effort: str | None
    reasoning_max_tokens: int | None
    prompt_cache: str  # auto | on | off
    wall_clock_s: int
    tool_deny: tuple[str, ...]
    tool_allow: tuple[str, ...] | None
    subagents: bool
    pin_system_prompt: bool
    controlled: bool
    network_policy: str

    def context_window(self, ctx: HarnessContext, default: int = 128_000) -> int:
        """A context size for harnesses that need one: the explicit cap, the model's window, the call cap."""
        model_window = getattr(ctx, "model_context_window", None)
        for value in (self.context_tokens, model_window, self.max_input_tokens):
            if isinstance(value, int) and value > 0:
                return value
        return default


def knobs(ctx: HarnessContext) -> Knobs:
    """Resolve the knobs ``render()`` maps from the preset and the effective policy."""
    policy = ctx.policy.content
    preset = ctx.preset
    turn_caps = [cap for cap in (preset.max_turns, policy.run.max_turns) if cap is not None]
    return Knobs(
        max_turns=min(turn_caps) if turn_caps else None,
        max_llm_requests=policy.run.max_llm_requests,
        max_aux_requests=policy.run.max_aux_requests,
        max_output_tokens=policy.call.max_output_tokens,
        max_input_tokens=policy.call.max_input_tokens,
        context_tokens=policy.context.max_context_tokens,
        compaction=policy.context.compaction,
        command_timeout_s=(
            policy.time.command_timeout_s if policy.time.command_timeout_s is not None else preset.command_timeout_s
        ),
        reasoning_mode=policy.reasoning.mode,
        reasoning_effort=policy.reasoning.effort,
        reasoning_max_tokens=policy.reasoning.max_tokens,
        prompt_cache=policy.caching.prompt_cache,
        wall_clock_s=policy.time.wall_clock_s,
        tool_deny=tuple(preset.tool_policy.deny),
        tool_allow=tuple(preset.tool_policy.allow) if preset.tool_policy.allow is not None else None,
        subagents=preset.subagents,
        pin_system_prompt=preset.pin_system_prompt,
        controlled=preset.controlled,
        network_policy=policy.network_policy,
    )


# Every knob key ``unsupported_knobs`` may report, with the test "is this knob active for the run?".
_ACTIVE_KNOB: Final[Mapping[str, Callable[[Knobs, HarnessContext], bool]]] = {
    "run.max_turns": lambda k, ctx: k.max_turns is not None,
    "call.max_output_tokens": lambda k, ctx: True,
    "context.max_context_tokens": lambda k, ctx: k.context_tokens is not None,
    "context.compaction": lambda k, ctx: k.compaction != "harness_default",
    "reasoning": lambda k, ctx: k.reasoning_mode != "provider_default",
    "caching.prompt_cache": lambda k, ctx: k.prompt_cache == "off",
    "time.command_timeout_s": lambda k, ctx: k.command_timeout_s is not None,
    "tool_policy": lambda k, ctx: bool(k.tool_deny) or k.tool_allow is not None,
    "subagents": lambda k, ctx: k.subagents,
    "pin_system_prompt": lambda k, ctx: k.pin_system_prompt,
    "system_prompt": lambda k, ctx: ctx.system_prompt is not None,
}
KNOB_KEYS: Final = tuple(_ACTIVE_KNOB)


def common_env(*, home: str = HOME_DIR) -> dict[str, str]:
    """Environment every harness gets: a fresh HOME/XDG tree outside /workspace and a dumb terminal."""
    return {
        "HOME": home,
        "XDG_CONFIG_HOME": f"{home}/.config",
        "XDG_DATA_HOME": f"{home}/.local/share",
        "XDG_STATE_HOME": f"{home}/.local/state",
        "XDG_CACHE_HOME": f"{home}/.cache",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "LANG": "C.UTF-8",
        "DO_NOT_TRACK": "1",
    }


def json_bytes(obj: Any) -> bytes:
    """Deterministic JSON for rendered config files (sorted keys, so equal configs are equal bytes)."""
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


_SAFE_ARG = re.compile(r"^[A-Za-z0-9_\-./:=@,+%]+$")


def shell_quote(arg: str) -> str:
    """POSIX quoting that leaves plain arguments readable (``shlex.quote`` for the rest)."""
    return arg if _SAFE_ARG.match(arg) else shlex.quote(arg)


def default_termination(  # noqa: PLR0911 - one branch per row of the section 5.1 rule list
    exit_info: ExitInfo,
    trajectory: Trajectory,
    facts: HarnessFacts,
) -> Termination:
    """The section 5.1 default rules: wall clock from elapsed time and gateway facts, never exit codes alone.

    cancelled -> CANCELLED; oom_killed -> OOM; elapsed >= wall_clock - 2 -> WALL_CLOCK (never by exit code
    137/143); idle_fired -> IDLE; budget_exhausted -> BUDGET; zero ok calls with transport failures ->
    INFRA; trajectory.ended_on_llm_error -> LLM_ERROR; trajectory.max_turns_hit -> MAX_TURNS; a documented
    exit code -> its mapped termination (qwen 53 -> MAX_TURNS); exit 0 -> COMPLETED; a crash code or a
    signal -> HARNESS_CRASH; anything else -> HARNESS_ERROR.
    """
    if exit_info.cancelled:
        return Termination.CANCELLED
    if exit_info.oom_killed:
        return Termination.OOM
    if exit_info.elapsed_s >= exit_info.wall_clock_s - 2:
        return Termination.WALL_CLOCK
    if exit_info.idle_fired:
        return Termination.IDLE
    if exit_info.budget_exhausted:
        return Termination.BUDGET
    if exit_info.gateway_calls_ok == 0 and exit_info.gateway_calls_failed_transport:
        return Termination.INFRA
    if trajectory.ended_on_llm_error:
        return Termination.LLM_ERROR
    if trajectory.max_turns_hit:
        return Termination.MAX_TURNS
    code = exit_info.exit_code
    if code is not None and code in facts.exit_codes:
        return Termination(facts.exit_codes[code])
    if code == 0:
        return Termination.COMPLETED
    if code is None or code < 0 or code > 128 or code in facts.crash_codes:
        return Termination.HARNESS_CRASH
    return Termination.HARNESS_ERROR


class HarnessAdapter(ABC):
    """One coding-agent harness: image, gateway wiring, rendering, trajectory parsing and termination.

    Class attributes beyond the spec's: ``description`` (one sentence for pickers), ``image_dir``
    (``images/<image_dir>``), ``version_build_arg`` (the Dockerfile ARG carrying the pin), ``lock_files``
    (inside the image dir) and ``wants_enabled`` / ``blocked_reason``: an adapter asks to be enabled with
    ``wants_enabled = True``, and the registry refuses that while ``facts.open_questions`` is non-empty.
    """

    name: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str] = ""
    protocol: ClassVar[WireProtocol]
    capabilities: ClassVar[HarnessCapabilities]
    version_setting: ClassVar[str]  # settings.AGENT_<NAME>_VERSION
    version_build_arg: ClassVar[str] = ""
    image_dir: ClassVar[str] = ""
    lock_files: ClassVar[tuple[str, ...]] = ()
    option_fields: ClassVar[tuple[OptionField, ...]] = ()
    facts: ClassVar[HarnessFacts]  # section 5.3; open_questions MUST be empty before the adapter is enabled
    versions: ClassVar[tuple[str, ...]] = ()  # releases whose facts (and captured prompt) are verified
    wants_enabled: ClassVar[bool] = False
    blocked_reason: ClassVar[str] = ""
    # Open questions that concern one endpoint kind only ("openrouter" / "local_proxy", e.g. a passthrough
    # route): cells on that kind get ``harness_not_enabled``, the other kinds may run (E2E-13).
    endpoint_open_questions: ClassVar[Mapping[str, tuple[str, ...]]] = MappingProxyType({})
    # Non-empty: enabled, but not yet verified for confirmatory use (no real-model smoke run, or a known
    # tool-surface caveat). Cells get ``harness_exploratory_only`` (warn exploratory, error confirmatory).
    exploratory_only: ClassVar[str] = ""
    brief_copies: ClassVar[tuple[str, ...]] = ()  # rendered files that embed the brief (stimulus, not config)

    # -- abstract surface (section 5.1) ---------------------------------------------------------------

    @abstractmethod
    def image_spec(self, version: str) -> ImageSpec: ...

    @abstractmethod
    def gateway_wiring(self, gateway_host: str) -> GatewayWiring: ...

    @abstractmethod
    def render(self, ctx: HarnessContext) -> RenderedHarness: ...

    @abstractmethod
    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory: ...

    @abstractmethod
    def observed_inputs(self, calls: list[dict[str, Any]]) -> ObservedInputs: ...

    # -- shared behaviour -------------------------------------------------------------------------------

    def classify_termination(self, exit_info: ExitInfo, trajectory: Trajectory) -> Termination:
        """Section 5.1 default rules (see :func:`default_termination`); adapters inherit them."""
        return default_termination(exit_info, trajectory, self.facts)

    def normalize_options(self, options: dict[str, Any]) -> dict[str, Any]:
        """Validated, sparse harness options (``ValueError`` on bad input; defaults dropped)."""
        return normalize_options(self.option_fields, options, harness=self.name)

    def restrict_paths(self, ctx: HarnessContext) -> RenderedHarness | None:
        """Slot agent: a render limited to ``ctx.slot.allowlist``, or None when unsupported.

        None means the harness cannot be told which paths to edit; the allowlist-diff check of the slot
        executor still applies (section 7.6).
        """
        return None

    def version(self) -> str:
        """The pinned release (``settings.<version_setting>``, read at call time)."""
        from django.conf import settings

        return str(getattr(settings, self.version_setting))

    def option_schema(self) -> list[OptionField]:
        return list(self.option_fields)

    def default_options(self) -> dict[str, Any]:
        return {f.key: list(f.default) if isinstance(f.default, tuple) else f.default for f in self.option_fields}

    def effective_options(self, options: Mapping[str, Any] | None) -> dict[str, Any]:
        """Every option's value for a run: the defaults overlaid with ``options``."""
        return {**self.default_options(), **normalize_options(self.option_fields, options, harness=self.name)}

    def default_image_spec(self, version: str) -> ImageSpec:
        """``images/<image_dir>`` with the version build arg (what most adapters' ``image_spec`` returns)."""
        from backend.generation.contracts.harness import ImageSpec

        return ImageSpec(
            image=self.image_dir,
            build_args={self.version_build_arg: version},
            lock_files=self.lock_files,
        )

    def protocol_mode(
        self,
        *,
        endpoint_kind: str = "openrouter",
        translators: frozenset[str] = frozenset(),
    ) -> ProtocolMode | None:
        """The first supported mode available for an endpoint, or None (``harness_protocol_unavailable...``).

        ``native`` needs nothing; ``passthrough`` needs a remote endpoint with the harness's native API
        (OpenRouter); ``translate`` needs the gateway translator for the front protocol (``translators``).
        """
        for mode in self.facts.protocol_modes:
            if mode == "native":
                return mode
            if mode == "passthrough" and endpoint_kind == "openrouter":
                return mode
            if mode == "translate" and self.protocol in translators:
                return mode
        return None

    def unsupported_knobs(self, ctx: HarnessContext) -> tuple[str, ...]:
        """Active knobs this harness cannot map (``facts.unsupported_knobs`` filtered to the run's knobs).

        Recorded in the manifest's harness section (``unsupported_knobs``) and as validity notes
        ``unsupported_knob:<key>``; the gateway limits still apply to every one of them.
        """
        values = knobs(ctx)
        return tuple(
            key for key in self.facts.unsupported_knobs if key in _ACTIVE_KNOB and _ACTIVE_KNOB[key](values, ctx)
        )

    def render_record(self, rendered: RenderedHarness, *, brief_path: str = BRIEF_PATH) -> dict[str, Any]:
        """``config_files_sha`` and ``command_sha`` for the manifest's harness section.

        The brief (and any file that embeds it, ``brief_copies``) is left out: it is a stimulus, recorded by
        sha in ``manifest.stimuli``.
        """
        skip = {brief_path, *self.brief_copies}
        return {
            "config_files_sha": {
                path: identity.bytes_sha(data) for path, data in sorted(rendered.files.items()) if path not in skip
            },
            "command_sha": identity.text_sha(rendered.command),
        }

    def parser(self) -> IncrementalParser:
        """A fresh incremental trajectory parser for live ``trajectory_step`` events (``parse_line``)."""
        from backend.generation.harnesses.trajectory import parser_for

        return parser_for(self.name)

    def to_dict(self) -> dict[str, Any]:
        """Static description for the API (the registry adds ``enabled``, ``blocked_reason``, ``versions``)."""
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "protocol": self.protocol,
            "capabilities": {
                field: getattr(self.capabilities, field) for field in self.capabilities.__dataclass_fields__
            },
            "option_schema": [f.to_dict() for f in self.option_fields],
            "default_options": self.default_options(),
            "facts_version": self.facts.facts_version,
            "vendor_affinity": self.facts.vendor_affinity,
            "min_call_output_tokens": self.facts.min_call_output_tokens,
        }


# -- the common entry script (section 6.5) -----------------------------------------------------------------


@dataclass(frozen=True)
class SandboxPayload:
    """What ``agentic/sandbox.py`` puts into the agent container and runs there."""

    files: dict[str, bytes]  # absolute in-container paths -> bytes (rendered files + command and entry script)
    env: dict[str, str]
    entrypoint: tuple[str, ...]


def entry_script(
    *,
    wall_clock_s: int,
    stdin_path: str | None = None,
    workspace: str = WORKSPACE_DIR,
    max_workspace_bytes: int | None = None,
) -> str:
    """The container entry script (section 6.5 steps 1-5), run as ``sh /run/entry.sh``.

    1. ``git init`` and commit the seed as ``base``; 2. ``core.excludesFile=/dev/null`` and anchored
    ``.git/info/exclude`` patterns only (nested directories are never excluded); 3. run the harness command
    script under ``timeout -s TERM -k 15 <wall>``, stdout/stderr to ``/logs``; 4. ``git add -A`` and the
    binary diff against ``base``; 5. the anchored workspace tar. A tar larger than ``max_workspace_bytes``
    is replaced by a path manifest (``workspace.manifest``) and recorded in ``exit.json``
    (``workspace_too_large``), which the collector turns into ``output_invalid:workspace_too_large``.
    """
    excludes = " ".join(shlex.quote(p) for p in GIT_EXCLUDES)
    tar_excludes = " ".join(f"--exclude={shlex.quote(p)}" for p in TAR_EXCLUDES)
    stdin = shlex.quote(stdin_path) if stdin_path else "/dev/null"
    limit = int(max_workspace_bytes) if max_workspace_bytes else 0
    ws = shlex.quote(workspace)
    git = "git -c user.name=llm-lab -c user.email=agent@llm-lab.local -c commit.gpgsign=false"
    return f"""#!/bin/sh
# llm-lab sandbox entry script (harnesses/base.py, SANDBOX_REVISION {SANDBOX_REVISION})
set -u
mkdir -p "$HOME" {LOGS_DIR}
cd {ws} || exit 70
git init -q -b main . >/dev/null 2>&1
git config core.excludesFile /dev/null
printf '%s\\n' {excludes} > .git/info/exclude
git add -A >/dev/null 2>&1
{git} commit -q --allow-empty -m base >/dev/null 2>&1
git tag -f base >/dev/null 2>&1
touch {LOGS_DIR}/.idle_mark
start=$(date +%s.%N)
timeout -s TERM -k 15 {int(wall_clock_s)} sh {COMMAND_SCRIPT} < {stdin} \\
  > {LOGS_DIR}/{STDOUT_FILE} 2> {LOGS_DIR}/{STDERR_FILE}
code=$?
end=$(date +%s.%N)
cd {ws} || exit 70
git add -A >/dev/null 2>&1
git diff --cached --binary base > {LOGS_DIR}/changes.patch 2>/dev/null
git diff --cached --name-status base > {LOGS_DIR}/name_status.txt 2>/dev/null
tar --anchored {tar_excludes} -C {ws} -cf {LOGS_DIR}/workspace.tar . 2>> {LOGS_DIR}/{STDERR_FILE}
too_large=false
size=$(wc -c < {LOGS_DIR}/workspace.tar)
if [ {limit} -gt 0 ] && [ "$size" -gt {limit} ]; then
  tar -tf {LOGS_DIR}/workspace.tar > {LOGS_DIR}/workspace.manifest
  rm -f {LOGS_DIR}/workspace.tar
  too_large=true
fi
printf '{{"exit_code": %s, "started": %s, "ended": %s, "workspace_bytes": %s, "workspace_too_large": %s}}\\n' \\
  "$code" "$start" "$end" "$size" "$too_large" > {LOGS_DIR}/{EXIT_FILE}
exit 0
"""


def command_script(rendered: RenderedHarness, *, workspace: str = WORKSPACE_DIR) -> bytes:
    """``/run/harness/command.sh``: the adapter's command, run from the workspace.

    Single-command adapters render ``exec <harness> ...`` so ``timeout`` signals the harness itself;
    multi-statement commands (Gemini CLI's loopback forwarder) run in the process group that ``timeout``
    signals as a whole.
    """
    return f"#!/bin/sh\ncd {shlex.quote(workspace)} || exit 70\n{rendered.command}\n".encode()


def sandbox_payload(
    rendered: RenderedHarness,
    *,
    wall_clock_s: int,
    max_workspace_bytes: int | None = None,
    workspace: str = WORKSPACE_DIR,
) -> SandboxPayload:
    """The rendered files plus ``/run/harness/command.sh`` and ``/run/entry.sh``, and the entrypoint."""
    files = dict(rendered.files)
    files[COMMAND_SCRIPT] = command_script(rendered, workspace=workspace)
    files[ENTRY_SCRIPT] = entry_script(
        wall_clock_s=wall_clock_s,
        stdin_path=rendered.stdin_path,
        workspace=workspace,
        max_workspace_bytes=max_workspace_bytes,
    ).encode()
    return SandboxPayload(files=files, env=dict(rendered.env), entrypoint=("sh", ENTRY_SCRIPT))
