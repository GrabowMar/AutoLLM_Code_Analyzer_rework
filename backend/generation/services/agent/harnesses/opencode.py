"""OpenCode (https://opencode.ai): headless ``opencode run --format json``.

Pinned on top of the release itself:

* **System prompt.** OpenCode picks a different base prompt per model family
  (Claude, GPT, Gemini, ... each get their own text) for the ``build`` agent
  and the ``general`` subagent, which would confound any cross-model
  comparison. Both get OpenCode's own model-agnostic default prompt,
  captured verbatim from this release (``data/agent/harness_prompts``). The
  ``explore`` and ``compaction`` agents already carry fixed built-in prompts.
* **Tools.** Web fetch/search and skills are disabled (the task must be
  solved from the brief, not looked up); everything else is native.
* **Auxiliary calls.** The session title is passed in, so no hidden request
  names the session; the small model is the run's model.
* **Discovery.** Project/user config, AGENTS.md/CLAUDE.md, external skills
  and plugins are off, so the agent cannot reconfigure its own harness.
* **Permissions.** Everything allowed inside the sandbox (the container is
  the boundary) except the interactive ``doom_loop`` prompt, which a
  headless run auto-rejects anyway -- set to ``deny`` so it is explicit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.generation.services.agent.harnesses.base import BRIEF_PATH
from backend.generation.services.agent.harnesses.base import DUMMY_API_KEY
from backend.generation.services.agent.harnesses.base import GATEWAY_BASE_URL
from backend.generation.services.agent.harnesses.base import Harness
from backend.generation.services.agent.harnesses.base import HarnessSummary

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "data" / "agent" / "harness_prompts"
DISABLED_TOOLS = ("webfetch", "websearch", "skill")

# Behaviour switches read by OpenCode at startup (see the opencode binary).
HARNESS_ENV = {
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_SHARE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
}


def default_prompt(version: str) -> str:
    """OpenCode's model-agnostic default prompt for *version* (verbatim capture)."""
    path = PROMPTS_DIR / f"opencode-{version}-default.md"
    if not path.is_file():
        msg = (
            f"No captured default prompt for OpenCode {version}; capture it into {path.name} "
            "before switching AGENT_OPENCODE_VERSION (see docs/GENERATION_PROCESS.md)"
        )
        raise ValueError(msg)
    return path.read_text(encoding="utf-8")


class OpenCodeHarness(Harness):
    name = "opencode"
    label = "OpenCode"
    image = "agent-opencode"
    version_setting = "AGENT_OPENCODE_VERSION"
    version_build_arg = "OPENCODE_VERSION"
    requires_tool_calling = True

    def snapshot_section(
        self,
        *,
        model_slug: str,
        context_window: int,
        max_output_tokens: int,
        agent_config: dict[str, Any],
        title: str,
    ) -> dict[str, Any]:
        version = self.version()
        prompt = default_prompt(version)
        model_ref = f"openrouter/{model_slug}"
        config = {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            # Per-step undo snapshots copy the worktree into a shadow git repo:
            # useless headless and costly once dependencies are installed.
            "snapshot": False,
            "model": model_ref,
            "small_model": model_ref,
            "enabled_providers": ["openrouter"],
            "provider": {
                "openrouter": {
                    "options": {"baseURL": GATEWAY_BASE_URL, "apiKey": DUMMY_API_KEY},
                    "models": {
                        model_slug: {
                            "name": model_slug,
                            "tool_call": True,
                            "reasoning": True,
                            "limit": {"context": context_window, "output": max_output_tokens},
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
            "tools": dict.fromkeys(DISABLED_TOOLS, False),
        }
        return {
            "name": self.name,
            "version": version,
            "system_prompt": prompt,
            "system_prompt_source": f"OpenCode {version} built-in default prompt (model-agnostic), captured verbatim",
            "disabled_tools": list(DISABLED_TOOLS),
            "config": config,
            "env": {**HARNESS_ENV, "OPENCODE_CONFIG_CONTENT": json.dumps(config)},
            "title": title,
        }

    def command(self, section: dict[str, Any]) -> str:
        return f"opencode run --format json --pure --title {json.dumps(section['title'])} < {BRIEF_PATH}"

    def parse_report(self, outputs: dict[str, bytes]) -> HarnessSummary:
        return parse_events(outputs.get("events.jsonl", b"").decode("utf-8", errors="replace"))


def parse_events(text: str) -> HarnessSummary:
    """Summarise OpenCode's ``--format json`` event stream."""
    summary = HarnessSummary()
    last_text = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            summary.unparsed_lines += 1
            continue
        if not isinstance(event, dict):
            continue
        summary.session_id = summary.session_id or event.get("sessionID", "")
        part = event.get("part") or {}
        kind = event.get("type")
        if kind == "step_finish":
            summary.steps += 1
            reason = part.get("reason") or "unknown"
            summary.finish_reasons[reason] = summary.finish_reasons.get(reason, 0) + 1
        elif kind == "tool_use":
            tool = part.get("tool") or "unknown"
            summary.tool_calls[tool] = summary.tool_calls.get(tool, 0) + 1
            if (part.get("state") or {}).get("status") == "error":
                summary.tool_errors += 1
        elif kind == "text" and part.get("text"):
            last_text = part["text"]
        elif kind == "error":
            err = event.get("error") or part
            summary.errors.append(json.dumps(err)[:1000] if not isinstance(err, str) else err[:1000])
    summary.final_message = last_text
    return summary
