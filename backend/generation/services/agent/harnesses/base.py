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
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import ClassVar

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
    image: ClassVar[str]  # images/<image>, built FROM images/agent-base
    version_setting: ClassVar[str]  # settings attribute holding the pinned release
    version_build_arg: ClassVar[str]
    requires_tool_calling: ClassVar[bool] = True
    # Files the harness itself writes into the workspace (caches, histories):
    # excluded from git and from the collected sample.
    workspace_artifacts: ClassVar[tuple[str, ...]] = ()

    def version(self) -> str:
        from django.conf import settings

        return str(getattr(settings, self.version_setting))

    def build_args(self, version: str) -> dict[str, str]:
        return {self.version_build_arg: version}

    def snapshot_section(
        self,
        *,
        model_slug: str,
        context_window: int,
        max_output_tokens: int,
        agent_config: dict[str, Any],
        title: str,
    ) -> dict[str, Any]:
        """Everything frozen about this harness for one job (enters run_fingerprint)."""
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
