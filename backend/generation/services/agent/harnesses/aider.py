"""Aider (https://aider.chat): edit-format pair programmer, headless single turn.

Aider does not need tool calling: the model answers with edit blocks that
Aider applies, then Aider lints the result and asks the model to fix lint
errors (its native reflection loop). Headless, that is one ``--message``
turn. Pinned on top of the release:

* **Edit format** ``diff`` (by default) for every model. Aider normally
  picks the format per model from its model database (diff / whole / udiff
  / ...), which would make the editing protocol a hidden per-model variable.
* **Weak/editor model** = the run's model, so no hidden second model is used
  for summaries.
* **Repo map** 1024 tokens (by default) for every model.
* **Model metadata.** Every model reaches Aider as ``openai/<slug>`` through
  the gateway, which Aider's database does not know ("~13k of 0 tokens"), so
  every model gets the same metadata file built from the model catalog
  (context window, the run's output cap) instead of running blind.
* All scaffold source files (except platform-owned build files and lock
  files) are added to the chat, the way a user would start a session on an
  existing project.
* No git commits, no .gitignore edits, no update checks or analytics, and no
  shell-command suggestions (with ``--yes-always`` Aider would otherwise run
  them unattended, e.g. a dev server that never exits).
* Chat/LLM histories go to /out, so the workspace only holds the app.

Options (checked against the aider-chat 0.86.2 source in the pinned lock):

* ``edit_format`` -- ``--edit-format`` picks the coder class directly
  (``Coder.create``: an explicit format wins over the model's own default),
  so every model gets the same editing protocol. Offered: ``diff``,
  ``whole``, ``udiff``, ``diff-fenced``; the architect/editor formats would
  bring in a second model call per turn.
* ``map_tokens`` -- ``--map-tokens`` sets ``RepoMap.max_map_tokens`` for
  every model (0 disables the repo map); Aider only scales it by the
  context window, which is a derived per-model field already.

Known per-model behaviour inside the pinned release, independent of these
options: Aider applies generic settings by model-name substring (reminder
placement, lazy/overeager prompt additions, ``use_temperature``). Every
model reaches it as ``openai/<vendor>/<model>``, so those still apply.
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.generation.services.agent.harnesses.base import BRIEF_PATH
from backend.generation.services.agent.harnesses.base import DUMMY_API_KEY
from backend.generation.services.agent.harnesses.base import GATEWAY_BASE_URL
from backend.generation.services.agent.harnesses.base import OUTPUT_DIR
from backend.generation.services.agent.harnesses.base import Harness
from backend.generation.services.agent.harnesses.base import HarnessSummary
from backend.generation.services.agent.harnesses.base import OptionField

AIDER = "/opt/harness/venv/bin/aider"
_ERROR_LINE = re.compile(r"^(litellm\.\w+|Traceback \(most recent call last\)|[A-Z]\w*(Error|Exception):|ERROR\b)")
EDIT_FORMAT = "diff"
MAP_TOKENS = 1024
EDIT_FORMATS = (
    ("diff", "Search/replace blocks (diff)"),
    ("whole", "Whole files (whole)"),
    ("udiff", "Unified diffs (udiff)"),
    ("diff-fenced", "Fenced search/replace blocks (diff-fenced)"),
)
# Files never handed to Aider for editing: the platform's build files and
# lock files (machine-generated; editing them by hand breaks installs).
NOT_ADDED = r"^(Dockerfile|\.dockerignore|docker-compose\.yml)$|(^|/)package-lock\.json$"


class AiderHarness(Harness):
    name = "aider"
    label = "Aider"
    image = "agent-aider"
    version_setting = "AGENT_AIDER_VERSION"
    version_build_arg = "AIDER_VERSION"
    requires_tool_calling = False
    description = "Edit-format pair programmer: the model replies with edit blocks that Aider applies (no tools)."
    workspace_artifacts = (".aider*",)
    option_fields = (
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

    def snapshot_section(
        self,
        *,
        model_slug: str,
        context_window: int,
        max_output_tokens: int,
        agent_config: dict[str, Any],
        title: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        version = self.version()
        opts = self.effective_options(options)
        edit_format = opts["edit_format"]
        model = f"openai/{model_slug}"
        args = [
            "--model", model,
            "--weak-model", model,
            "--editor-model", model,
            "--openai-api-base", GATEWAY_BASE_URL,
            "--openai-api-key", DUMMY_API_KEY,
            "--edit-format", edit_format,
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
            "--chat-history-file", f"{OUTPUT_DIR}/aider.chat.md",
            "--input-history-file", f"{OUTPUT_DIR}/aider.input.md",
            "--llm-history-file", f"{OUTPUT_DIR}/aider.llm.md",
            "--model-metadata-file", f"{OUTPUT_DIR}/aider-model-metadata.json",
            "--message-file", BRIEF_PATH,
        ]  # fmt: skip
        metadata = {
            model: {
                "max_input_tokens": int(context_window),
                "max_output_tokens": int(max_output_tokens),
                "max_tokens": int(max_output_tokens),
                "litellm_provider": "openai",
                "mode": "chat",
            },
        }
        return {
            "name": self.name,
            "version": version,
            "system_prompt_source": f"Aider {version} built-in prompts for edit format {edit_format!r} (native)",
            "edit_format": edit_format,
            "args": args,
            "model_metadata": metadata,
            "added_files_rule": f"git ls-files minus /{NOT_ADDED}/",
            # LiteLLM would otherwise download its model price map at start-up
            # (a network-dependent step; blocked by the egress policy anyway).
            "env": {"AIDER_ANALYTICS": "false", "TERM": "dumb", "LITELLM_LOCAL_MODEL_COST_MAP": "True"},
            "title": title,
        }

    def files(self, section: dict[str, Any], brief: str) -> dict[str, bytes]:
        return {
            **super().files(section, brief),
            "aider-model-metadata.json": json.dumps(section["model_metadata"]).encode(),
        }

    def command(self, section: dict[str, Any]) -> str:
        args = " ".join(_quote(a) for a in section["args"])
        return f"{AIDER} {args} $(git ls-files | grep -vE '{NOT_ADDED}')"

    def parse_report(self, outputs: dict[str, bytes]) -> HarnessSummary:
        summary = HarnessSummary()
        text = outputs.get("events.jsonl", b"").decode("utf-8", errors="replace")
        summary.steps = sum(1 for line in text.splitlines() if line.startswith("Tokens:"))
        edits = sum(1 for line in text.splitlines() if line.startswith("Applied edit to "))
        if edits:
            summary.tool_calls["edit"] = edits
        summary.final_message = text[-4000:]
        # Aider echoes the model's code to stdout, so only lines that look like
        # Aider/LiteLLM errors (never indented code) count as errors.
        log = outputs.get("harness.log", b"").decode("utf-8", errors="replace")
        summary.errors = [line[:500] for line in (text + "\n" + log).splitlines() if _ERROR_LINE.match(line)][:20]
        return summary


def _quote(arg: str) -> str:
    safe = all(c.isalnum() or c in "-_./:=@" for c in arg)
    return arg if safe and arg else "'" + arg.replace("'", "'\"'\"'") + "'"
