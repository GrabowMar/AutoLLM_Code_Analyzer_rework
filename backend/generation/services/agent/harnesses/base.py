"""What every harness adapter provides, and the sandbox plumbing they share.

A *harness* is the agent program that drives the model (OpenCode, mini-swe-
agent, Aider, ...). Benchmarks compare (harness, model) pairs, so the rules
are:

* **Within a harness**, every model gets byte-identical treatment: the same
  frozen config, prompts, tools and flags. Anything a harness would normally
  switch per model family (prompts, edit formats, temperatures) is pinned.
* **Across harnesses**, each runs with its own native, frozen prompts -- the
  harness's design is the thing being compared -- but on the same sandbox
  image (``images/agent-base``), the same brief on stdin/file (never argv),
  the same gateway policy and the same budgets.

An adapter turns the frozen snapshot section into a shell command, an
environment and a parser for the harness's own report. The entry script
around it (scaffold commit, timeout, diff, workspace archive) is common.

**Options** (``option_schema``) are the few harness knobs a run may change.
Each one must apply identically to every model (checked against the pinned
release's source, recorded in the adapter docstring) and is rendered only
into values the section already carries (CLI args, config), so a section
built with the default options is byte-identical to one built without any.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import ClassVar

OPTION_TYPES = ("enum", "int", "bool", "multi_enum")


@dataclass(frozen=True)
class OptionField:
    """One user-settable harness option (rendered by the UI from ``to_dict``)."""

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
        """``(normalized value, error)`` for a user-supplied *value*."""
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
    options: dict[str, Any] | None,
    *,
    harness: str,
) -> dict[str, Any]:
    """Validate *options* against *fields*; returns only the keys that differ from the defaults.

    Raises ``ValueError`` listing every problem. Dropping default values means
    ``{}`` and ``{"edit_format": "diff"}`` are the same configuration (same
    content hash, same method key, same section).
    """
    if options is None:
        return {}
    if not isinstance(options, dict):
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


@dataclass(frozen=True)
class LocalRender:
    """Render inputs for a model on a local (non-OpenRouter) endpoint (spec §D.5).

    Passed to ``snapshot_section`` only for local models, so OpenRouter
    sections stay byte-identical. ``client_timeout_s`` is the harness HTTP
    client timeout from ``inference.timeouts.chain`` (long enough never to
    cut the gateway's 504 re-attach chain); ``capabilities`` are the
    model's discovered capabilities (e.g. ``"thinking"``).
    """

    client_timeout_s: int
    capabilities: tuple[str, ...] = ()


GATEWAY_ALIAS = "llm-gateway"
GATEWAY_PORT = 8080
GATEWAY_BASE_URL = f"http://{GATEWAY_ALIAS}:{GATEWAY_PORT}/api/v1"
WORKSPACE_DIR = "/workspace"
OUTPUT_DIR = "/out"
BRIEF_PATH = f"{OUTPUT_DIR}/brief.md"
# The agent only ever sees this placeholder; the gateway holds the real key.
DUMMY_API_KEY = "sk-sandbox-no-real-key"

# Bump whenever the sandbox environment an agent sees changes (env vars,
# proxy setup, entry script, workspace layout) without an image change.
SANDBOX_REVISION = 4

# Dependency folders, build output and caches: never collected (workspace.py
# filters the rest) and kept out of git via .git/info/exclude, so neither the
# recorded patch nor the archive grows with node_modules. The exclude file
# lives inside .git, so the agent's workspace itself carries no extra file.
IGNORED_DIRS = (
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    ".cache",
    ".npm",
    ".local",
    ".config",
    # Mirrors the stacks' .dockerignore: the built app starts from an empty
    # data/ (the brief says so), so nothing in it can be part of the sample.
    "data",
)
IGNORED_FILE_GLOBS = ("*.pyc", "*.db", "*.sqlite", "*.sqlite3", "*.log")


@dataclass
class HarnessSummary:
    """What the harness reported about its own run (self-reported; the gateway log is authoritative)."""

    session_id: str = ""
    steps: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)
    tool_errors: int = 0
    final_message: str = ""
    finish_reasons: dict[str, int] = field(default_factory=dict)
    exit_status: str = ""
    errors: list[str] = field(default_factory=list)
    unparsed_lines: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "tool_call_total": sum(self.tool_calls.values()),
            "tool_errors": self.tool_errors,
            "final_message": self.final_message[:8000],
            "finish_reasons": self.finish_reasons,
            "exit_status": self.exit_status,
            "errors": self.errors[:20],
            "unparsed_lines": self.unparsed_lines,
        }


class Harness:
    """Base adapter; subclasses fill in the class attributes and the hooks."""

    name: ClassVar[str]
    label: ClassVar[str]
    # One sentence for pickers: what kind of agent this is.
    description: ClassVar[str] = ""
    image: ClassVar[str]  # images/<image>, built FROM images/agent-base
    version_setting: ClassVar[str]  # settings attribute holding the pinned release
    version_build_arg: ClassVar[str]
    requires_tool_calling: ClassVar[bool] = True
    # Files the harness itself writes into the workspace (caches, histories):
    # excluded from git and from the collected sample.
    workspace_artifacts: ClassVar[tuple[str, ...]] = ()
    # User-settable options (see the module docstring for what qualifies).
    option_fields: ClassVar[tuple[OptionField, ...]] = ()

    def version(self) -> str:
        from django.conf import settings

        return str(getattr(settings, self.version_setting))

    def build_args(self, version: str) -> dict[str, str]:
        return {self.version_build_arg: version}

    def option_schema(self) -> list[OptionField]:
        return list(self.option_fields)

    def default_options(self) -> dict[str, Any]:
        return {f.key: list(f.default) if isinstance(f.default, tuple) else f.default for f in self.option_fields}

    def normalize_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        """Validated options minus the defaults (``ValueError`` on bad input)."""
        return normalize_options(self.option_fields, options, harness=self.name)

    def effective_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        """Every option's value for a run: the defaults overlaid with *options*."""
        return {**self.default_options(), **self.normalize_options(options)}

    def snapshot_section(
        self,
        *,
        model_slug: str,
        context_window: int,
        max_output_tokens: int,
        agent_config: dict[str, Any],
        title: str,
        options: dict[str, Any] | None = None,
        local: LocalRender | None = None,
    ) -> dict[str, Any]:
        """Everything frozen about this harness for one job (enters run_fingerprint).

        *options* are the run's harness options (sparse; validated here);
        with none, or only defaults, the section is the harness's frozen
        default configuration. *local* is set only for models on a local
        endpoint (see :class:`LocalRender`).
        """
        raise NotImplementedError

    def files(self, section: dict[str, Any], brief: str) -> dict[str, bytes]:
        """Files dropped into /out before start: the brief, the command, configs."""
        return {"brief.md": brief.encode("utf-8"), "harness.sh": harness_script(self.command(section))}

    def command(self, section: dict[str, Any]) -> str:
        """Shell snippet that runs the harness; stdout/stderr already redirected by the caller."""
        raise NotImplementedError

    def env(self, section: dict[str, Any]) -> dict[str, str]:
        return dict(section.get("env") or {})

    def parse_report(self, outputs: dict[str, bytes]) -> HarnessSummary:
        raise NotImplementedError


def proxy_env(network_policy: str) -> dict[str, str]:
    """Egress through the gateway's CONNECT allowlist (policy ``packages`` only).

    HTTPS only: the registries are HTTPS, and an HTTP proxy would also capture
    the agent's own requests to the app it is testing (observed: the agent
    concluded its port was taken). Loopback is always direct.
    """
    if network_policy != "packages":
        return {}
    proxy = f"http://{GATEWAY_ALIAS}:{GATEWAY_PORT}"
    no_proxy = f"{GATEWAY_ALIAS},localhost,127.0.0.1,::1,0.0.0.0"
    return {
        "HTTPS_PROXY": proxy,
        "https_proxy": proxy,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
        "npm_config_https_proxy": proxy,
    }


def harness_script(command: str) -> bytes:
    """``/out/harness.sh``: the harness command, exec'd so ``timeout`` signals it directly."""
    return f"cd {WORKSPACE_DIR}\nexec {command}\n".encode()


def run_script(*, wall_clock_seconds: int, artifacts: tuple[str, ...] = ()) -> str:
    """Container entry script: commit the scaffold, run the harness, record the diff.

    The harness command itself lives in ``/out/harness.sh`` (written by the
    adapter), so no harness argument is ever shell-quoted twice.

    The brief reaches every harness through a file or stdin, never argv:
    agents routinely restart their dev server with "kill every process whose
    command line mentions app.py", and a brief in argv made the harness
    itself match (observed with DeepSeek V4.1 Flash on OpenCode).
    """
    excludes = [f"{d}/" for d in IGNORED_DIRS if d != ".git"] + list(IGNORED_FILE_GLOBS) + list(artifacts)
    tar_excludes = " ".join(f"--exclude={json.dumps(p.rstrip('/'))}" for p in [*IGNORED_DIRS, *artifacts])
    git_excludes = " ".join(json.dumps(p) for p in excludes)
    return f"""
set -u
cd {WORKSPACE_DIR}
git init -q -b main .
printf '%s\\n' {git_excludes} > .git/info/exclude
git add -A && git -c user.name=llm-lab -c user.email=agent@llm-lab.local commit -qm scaffold
start=$(date +%s)
timeout -s TERM -k 15 {int(wall_clock_seconds)} sh {OUTPUT_DIR}/harness.sh \\
  > {OUTPUT_DIR}/events.jsonl 2> {OUTPUT_DIR}/harness.log
code=$?
end=$(date +%s)
echo "{{\\"exit_code\\": $code, \\"agent_seconds\\": $((end - start))}}" > {OUTPUT_DIR}/exit.json
git add -A >/dev/null 2>&1
git diff --cached --stat=200 > {OUTPUT_DIR}/diffstat.txt 2>/dev/null
git diff --cached --binary > {OUTPUT_DIR}/changes.patch 2>/dev/null
git diff --cached --name-status > {OUTPUT_DIR}/name_status.txt 2>/dev/null
tar {tar_excludes} -cf {OUTPUT_DIR}/workspace.tar -C {WORKSPACE_DIR} . 2>> {OUTPUT_DIR}/harness.log
exit 0
"""
