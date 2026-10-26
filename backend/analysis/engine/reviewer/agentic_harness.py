"""Review variants of generation's harness adapters (opencode, mini-swe-agent, aider).

Generation's adapters (``backend.generation.services.agent.harnesses``) are
built for a coding task: write permissions, "solve this issue" prompts,
every file added for editing. This module reuses them read-only -- pinned
release, image, native prompts, model wiring through the gateway, report
parsers -- and changes only what a review needs, per harness:

* **opencode**: OpenCode's own pinned default prompt and tools, with a
  review permission set. Headless ``opencode run`` auto-rejects every
  permission that resolves to ``ask`` (verified in 1.18.35: it prints
  "permission requested ...; auto-rejecting"), so every rule is explicit:
  ``read`` allow (incl. ``*.env``, which defaults to ask), ``edit`` deny
  except the findings file, ``bash`` allow (grep/ls), web tools, external
  directories, ``question`` and ``doom_loop`` deny. Rules are evaluated
  last-match-wins and the edit tools ask with the path relative to the
  worktree, so the allow rule is ``*.llmlab-review/findings.json``
  (OpenCode wildcards are ``.*``-anchored regexes: the leading ``*`` matches
  whatever prefix the worktree adds). A tool is only removed from the model
  when its last rule is ``"*": deny``, so ``write`` stays offered.
* **mini-swe-agent**: native ``mini.yaml`` system template and loop; the
  edit-oriented ``agent.instance_template`` ("Please solve this issue ...
  edit the source code") is replaced by ``data/review/agent-mini-instance-v1.md``,
  which keeps mini's command-execution rules verbatim and ends with
  ``echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat <findings>`` (mini
  submits whatever follows that first output line). ``-o /out/traj.json``
  is passed explicitly: the CLI's ``--output`` default otherwise overrides
  ``agent.output_path`` and the trajectory never reaches /out.
* **aider**: ``--chat-mode ask`` (aider's native "expert code analyst"
  prompt, no edits), ``--no-git``, repo map off, every workspace file passed
  with ``--read``; the answer is one JSON block, taken from the gateway's
  record of the final completion. Aider shows files without line numbers,
  so its line citations lean on grounding (evidence relocation).

Everything else (gateway URL, dummy key, model metadata, env switches)
comes from the generation section unchanged, so a harness reviews with the
same pinned behaviour it generates with.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import ClassVar

from backend.analysis.engine.reviewer import agentic_usage
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.harnesses.base import BRIEF_PATH
from backend.generation.services.agent.harnesses.base import DUMMY_API_KEY
from backend.generation.services.agent.harnesses.base import GATEWAY_BASE_URL
from backend.generation.services.agent.harnesses.base import OUTPUT_DIR
from backend.generation.services.agent.harnesses.base import WORKSPACE_DIR
from backend.generation.services.agent.harnesses.base import HarnessSummary
from backend.generation.services.agent.harnesses.base import OptionField
from backend.generation.services.agent.harnesses.base import harness_script
from backend.generation.services.agent.harnesses.base import normalize_options
from backend.generation.services.agent.images import harness_image_plan
from backend.generation.services.agent.images import image_tag

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "review"
BRIEF_VERSION = "agent-brief-v1"
BRIEF_TEMPLATE = f"{BRIEF_VERSION}.md"
OUTPUT_FILE_TEMPLATE = "agent-output-file-v1.md"
OUTPUT_ANSWER_TEMPLATE = "agent-output-answer-v1.md"
MINI_INSTANCE_TEMPLATE = "agent-mini-instance-v1.md"
FINALIZER_TEMPLATE = "agent-finalizer-v1.md"

# Bump whenever the review variant of any harness changes (permissions,
# templates wiring, CLI flags, entry script) without a harness release or
# image change: it is frozen into the plan and checked before each sample.
REVIEW_HARNESS_REVISION = 1
REVIEW_TITLE = "llm-lab-review"
FINDINGS_REL = ".llmlab-review/findings.json"
FINDINGS_DIR = f"{WORKSPACE_DIR}/.llmlab-review"
FINDINGS_PATH = f"{WORKSPACE_DIR}/{FINDINGS_REL}"
# Same fallbacks as generation's snapshot.resolve_llm.
FALLBACK_CONTEXT_WINDOW = 128_000
DEFAULT_OUTPUT_CAP = 16_000

_PLACEHOLDER = re.compile(r"@@([a-z_]+)@@")

FINALIZER_OPTION = OptionField(
    key="finalizer",
    type="bool",
    label="Finalizer",
    help=(
        "When the harness ends without a valid findings document, convert its final answer into the schema "
        "with one structured call (the sample is marked repaired)."
    ),
    default=True,
)


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------


def template_bytes(name: str) -> bytes:
    path = DATA_DIR / name
    if not path.is_file():
        msg = f"Review template {name} is missing from {DATA_DIR}"
        raise ValueError(msg)
    return path.read_bytes()


def template_sha256(name: str) -> str:
    return hashlib.sha256(template_bytes(name)).hexdigest()


def render(template: str, values: dict[str, str]) -> str:
    """Fill ``@@key@@`` placeholders; every placeholder must be given (and every value used).

    ``@@`` never occurs in Jinja (mini's templates) or JSON, so harness-native
    placeholders such as ``{{task}}`` pass through untouched.
    """
    found = set(_PLACEHOLDER.findall(template))
    missing = sorted(found - set(values))
    unused = sorted(set(values) - found)
    if missing or unused:
        msg = f"Template placeholders do not match (missing {missing}, unused {unused})"
        raise ValueError(msg)
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


# --------------------------------------------------------------------------
# Model facts a section needs (from an LLMModel at freeze, from the plan at run time)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelInfo:
    slug: str
    context_window: int
    output_cap: int
    supports_tools: bool | None  # None = unknown

    @classmethod
    def from_any(cls, model: Any, *, max_output_tokens: int | None = None) -> ModelInfo:
        """From an ``LLMModel`` row or a frozen ``reviewer.model`` dict.

        Both routes must give identical values for the same model, because a
        sample re-derives the harness section from the frozen plan.
        """
        if isinstance(model, dict):
            slug = str(model.get("slug") or model.get("model_id") or "")
            context = model.get("context_window")
            model_cap = model.get("max_output_tokens")
            supports = model.get("supports") or {}
            tools = supports.get("tools") if isinstance(supports, dict) else None
        else:
            slug = str(model.model_id)
            context = model.context_window
            model_cap = model.max_output_tokens
            tools = bool(model.supports_function_calling)
        if not slug:
            msg = "The reviewer model has no slug"
            raise ValueError(msg)
        requested = int(max_output_tokens or DEFAULT_OUTPUT_CAP)
        cap = min(requested, int(model_cap)) if model_cap else requested
        return cls(
            slug=slug,
            context_window=int(context or FALLBACK_CONTEXT_WINDOW),
            output_cap=cap,
            supports_tools=None if tools is None else bool(tools),
        )


# --------------------------------------------------------------------------
# Review harnesses
# --------------------------------------------------------------------------


class ReviewHarness:
    """One harness in review mode (wraps the generation adapter of the same name)."""

    name: ClassVar[str]
    output_channel: ClassVar[str] = "file"  # file | answer
    # The agent decides what it reads (opencode, mini-swe-agent), so whether
    # the files under review were reviewed is its own declaration; aider is
    # shown every file in one turn, like the single-shot engine.
    explores: ClassVar[bool] = True
    harness_templates: ClassVar[tuple[str, ...]] = ()
    # Options of the generation adapter that still mean something in review.
    adapter_option_keys: ClassVar[tuple[str, ...]] = ()
    notes: ClassVar[tuple[str, ...]] = ()
    review_description: ClassVar[str] = ""

    @property
    def adapter(self):
        return get_harness(self.name)

    @property
    def label(self) -> str:
        return self.adapter.label

    @property
    def requires_tool_calling(self) -> bool:
        return self.adapter.requires_tool_calling

    def version(self) -> str:
        return self.adapter.version()

    def description(self) -> str:
        return self.review_description

    # -- options -----------------------------------------------------------

    def option_fields(self) -> tuple[OptionField, ...]:
        fields = tuple(f for f in self.adapter.option_fields if f.key in self.adapter_option_keys)
        return (*fields, FINALIZER_OPTION)

    def normalize_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        """Validated options minus defaults (``ValueError`` listing every problem)."""
        return normalize_options(self.option_fields(), options, harness=self.name)

    def effective_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        defaults = {
            f.key: list(f.default) if isinstance(f.default, tuple) else f.default for f in self.option_fields()
        }
        return {**defaults, **self.normalize_options(options)}

    def adapter_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        """The sparse subset handed to the generation adapter (its own validation applies)."""
        return {k: v for k, v in self.normalize_options(options).items() if k in self.adapter_option_keys}

    # -- identity ------------------------------------------------------------

    def templates(self) -> tuple[str, ...]:
        output = OUTPUT_ANSWER_TEMPLATE if self.output_channel == "answer" else OUTPUT_FILE_TEMPLATE
        return (BRIEF_TEMPLATE, output, *self.harness_templates, FINALIZER_TEMPLATE)

    def template_hashes(self) -> dict[str, str]:
        return {name: template_sha256(name) for name in self.templates()}

    def prompt_sha256(self) -> str:
        """One hash over every template this harness's review depends on (name + bytes, in order)."""
        digest = hashlib.sha256()
        for name in self.templates():
            digest.update(name.encode())
            digest.update(b"\0")
            digest.update(template_bytes(name))
        return digest.hexdigest()

    def images(self, version: str) -> dict[str, Any]:
        """Content-addressed tags (``image_dir`` + ``buildargs`` for building) of the sandbox chain."""
        adapter = self.adapter
        args = adapter.build_args(version)
        plan = harness_image_plan(adapter.image, args)
        return {
            "base": plan[0][2],
            "harness": plan[1][2],
            "gateway": image_tag("llm-gateway"),
            "harness_dir": adapter.image,
            "harness_buildargs": args,
        }

    # -- section / files / command -------------------------------------------

    def section(self, *, model: ModelInfo, options: dict[str, Any] | None, max_llm_requests: int) -> dict[str, Any]:
        """Everything about how this harness runs one review (recorded per sample, hashed into the plan)."""
        section = self.adapter.snapshot_section(
            model_slug=model.slug,
            context_window=model.context_window,
            max_output_tokens=model.output_cap,
            agent_config={"max_llm_requests": int(max_llm_requests)},
            title=REVIEW_TITLE,
            options=self.adapter_options(options),
        )
        section["review"] = {
            "revision": REVIEW_HARNESS_REVISION,
            "output_channel": self.output_channel,
            "findings_path": FINDINGS_PATH if self.output_channel == "file" else None,
        }
        return self._review_section(section)

    def _review_section(self, section: dict[str, Any]) -> dict[str, Any]:
        return section

    def output_instructions(self) -> str:
        if self.output_channel == "answer":
            return render(template_bytes(OUTPUT_ANSWER_TEMPLATE).decode(), {}).strip()
        return render(template_bytes(OUTPUT_FILE_TEMPLATE).decode(), {"findings_path": FINDINGS_PATH}).strip()

    def files(self, section: dict[str, Any], brief: str, workspace_paths: list[str]) -> dict[str, bytes]:
        """What goes into /out before start: the brief, the harness command, configs."""
        return {
            "brief.md": brief.encode("utf-8"),
            "harness.sh": harness_script(self.command(section, workspace_paths)),
        }

    def command(self, section: dict[str, Any], workspace_paths: list[str]) -> str:
        return self.adapter.command(section)

    def env(self, section: dict[str, Any]) -> dict[str, str]:
        return dict(section.get("env") or {})

    # -- results -------------------------------------------------------------

    def report(self, outputs: dict[str, bytes]) -> HarnessSummary:
        """The harness's self-report (generation's parser), never raising."""
        try:
            return self.adapter.parse_report(outputs)
        except Exception as exc:  # noqa: BLE001 - a broken self-report must not lose the sample
            summary = HarnessSummary()
            summary.errors.append(f"harness report unparseable: {exc}")
            return summary

    def final_answer(self, outputs: dict[str, bytes], summary: HarnessSummary, last_call_content: str) -> str:
        """The agent's final message (input to the finalizer)."""
        return summary.final_message or last_call_content

    def files_opened(self, outputs: dict[str, bytes], workspace_paths: list[str]) -> tuple[list[str] | None, str]:
        """``(paths the agent demonstrably looked at, method)``; ``None`` when not observable."""
        return None, "not_observable"

    def self_stop(self, summary: HarnessSummary) -> tuple[str, str] | None:
        """How the harness says it stopped when that was not a normal finish: ``(kind, detail)``.

        *kind* is ``budget`` (its own step/cost limit), ``timeout`` (its own
        wall clock) or ``error``; None = a normal finish, or the harness
        reports nothing about it. A limit stop never reaches the gateway
        (the harness stops before sending the request over its limit), so
        without this it would look like the agent finished by itself.
        """
        return None


class OpenCodeReview(ReviewHarness):
    name = "opencode"
    review_description = (
        "OpenCode's headless agent explores the app with its native tools (read, grep, glob, bash) and writes "
        "the findings file; every other edit is denied."
    )
    notes = (
        "OpenCode's pinned model-agnostic prompt and native tools; edits denied except the findings file.",
        "Headless runs auto-reject any permission that would ask, so every rule is explicit.",
    )

    def _review_section(self, section: dict[str, Any]) -> dict[str, Any]:
        config = section["config"]
        config["permission"] = {
            "read": "allow",
            "edit": {"*": "deny", f"*{FINDINGS_REL}": "allow"},
            "bash": "allow",
            "webfetch": "deny",
            "websearch": "deny",
            "external_directory": "deny",
            "question": "deny",
            "plan_enter": "deny",
            "doom_loop": "deny",
        }
        section["env"]["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        return section

    def report(self, outputs: dict[str, bytes]) -> HarnessSummary:
        # Generation's event parser splits with str.splitlines, which cuts a
        # JSON row at a raw U+2028/U+2029/U+0085 (Node's JSON.stringify leaves
        # them unescaped in strings): the agent's final message would be
        # lost. Escaping them is lossless (they only occur inside strings).
        events = outputs.get("events.jsonl")
        if events:
            outputs = {**outputs, "events.jsonl": json_line_safe(events)}
        return super().report(outputs)

    def files_opened(self, outputs: dict[str, bytes], workspace_paths: list[str]) -> tuple[list[str] | None, str]:
        known = set(workspace_paths)
        opened: set[str] = set()
        # agentic_usage.jsonl rows end at "\n" only (see its docstring).
        for event in agentic_usage.jsonl(outputs.get("events.jsonl")):
            part = event.get("part")
            if not isinstance(part, dict) or event.get("type") != "tool_use" or part.get("tool") != "read":
                continue
            tool_input = (part.get("state") or {}).get("input") or {}
            path = tool_input.get("filePath") or tool_input.get("filepath") or tool_input.get("path") or ""
            rel = _workspace_relative(str(path))
            if rel in known:
                opened.add(rel)
        return sorted(opened), "read_tool_calls"


class MiniSweAgentReview(ReviewHarness):
    name = "mini-swe-agent"
    review_description = (
        "The SWE-agent team's minimal bash-only loop reads the code one shell command per step and writes "
        "the findings file with a command."
    )
    harness_templates = (MINI_INSTANCE_TEMPLATE,)
    adapter_option_keys = ("command_timeout_seconds",)
    notes = (
        "mini.yaml's native loop and system template; the edit-oriented instance template is replaced by a "
        "review template that keeps mini's command rules.",
        "Bash only: the agent writes the findings file with a shell command and submits it with cat.",
    )

    def _review_section(self, section: dict[str, Any]) -> dict[str, Any]:
        template = render(template_bytes(MINI_INSTANCE_TEMPLATE).decode(), {"findings_path": FINDINGS_PATH})
        section["config"]["agent"]["instance_template"] = template
        section["system_prompt_source"] = (
            f"mini-swe-agent {section['version']} built-in mini.yaml system template (native); "
            f"instance template {MINI_INSTANCE_TEMPLATE}"
        )
        section["trajectory"] = f"{OUTPUT_DIR}/traj.json"
        return section

    def command(self, section: dict[str, Any], workspace_paths: list[str]) -> str:
        return f"{self.adapter.command(section)} -o {section['trajectory']}"

    def files(self, section: dict[str, Any], brief: str, workspace_paths: list[str]) -> dict[str, bytes]:
        files = self.adapter.files(section, brief)
        files["harness.sh"] = harness_script(self.command(section, workspace_paths))
        return files

    def final_answer(self, outputs: dict[str, bytes], summary: HarnessSummary, last_call_content: str) -> str:
        # The submission (what followed COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT) is
        # the findings document when the agent followed the template.
        return summary.final_message or last_call_content

    def self_stop(self, summary: HarnessSummary) -> tuple[str, str] | None:
        # mini's trajectory records why its loop ended: "Submitted" is the
        # agent's own finish; LimitsExceeded is raised *before* the request
        # over step_limit (= the review's max_llm_requests) is sent, and the
        # run still exits 0, so the gateway never sees the budget run out.
        status = summary.exit_status
        if not status or status == "Submitted":
            return None
        if status == "TimeExceeded":
            return "timeout", status
        if status == "LimitsExceeded":
            return "budget", status
        return "error", status

    def files_opened(self, outputs: dict[str, bytes], workspace_paths: list[str]) -> tuple[list[str] | None, str]:
        try:
            traj = json.loads(outputs.get("traj.json") or b"{}")
        except ValueError:
            return None, "not_observable"
        messages = traj.get("messages") if isinstance(traj, dict) else None
        commands: list[str] = []
        for message in messages if isinstance(messages, list) else []:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            for call in message.get("tool_calls") or []:
                arguments = (call.get("function") or {}).get("arguments")
                try:
                    command = json.loads(arguments).get("command", "") if isinstance(arguments, str) else ""
                except (ValueError, AttributeError):
                    command = str(arguments or "")
                commands.append(str(command))
        text = "\n".join(commands)
        return sorted(p for p in workspace_paths if p in text), "command_mentions"


class AiderReview(ReviewHarness):
    name = "aider"
    review_description = (
        "Aider in ask mode: every file is shown read-only in one turn and the model answers with the findings "
        "document (no tools, no exploration)."
    )
    output_channel = "answer"
    explores = False
    notes = (
        "Ask mode (no edits): one turn over every workspace file passed read-only; no exploration loop.",
        "Aider shows files without line numbers, so line citations rely on evidence-based grounding.",
        "The whole review is one completion: reasoning models need a large max_output_tokens (observed: "
        "DeepSeek V4.1 Flash spent 16k tokens reasoning at effort low and was truncated; 32k sufficed).",
    )

    def section(self, *, model: ModelInfo, options: dict[str, Any] | None, max_llm_requests: int) -> dict[str, Any]:
        base = self.adapter.snapshot_section(
            model_slug=model.slug,
            context_window=model.context_window,
            max_output_tokens=model.output_cap,
            agent_config={"max_llm_requests": int(max_llm_requests)},
            title=REVIEW_TITLE,
            options={},
        )
        llm = f"openai/{model.slug}"
        # Built here rather than edited from generation's list: generation pins
        # an edit format, a repo map and git-based file selection, none of
        # which apply to a read-only, git-less review.
        args = [
            "--model", llm,
            "--weak-model", llm,
            "--editor-model", llm,
            "--openai-api-base", GATEWAY_BASE_URL,
            "--openai-api-key", DUMMY_API_KEY,
            "--chat-mode", "ask",
            "--map-tokens", "0",
            "--no-git",
            "--yes-always",
            "--no-auto-commits",
            "--no-dirty-commits",
            "--no-gitignore",
            "--no-auto-lint",
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
        return {
            "name": self.name,
            "version": base["version"],
            "system_prompt_source": f"Aider {base['version']} built-in prompts for chat mode 'ask' (native)",
            "chat_mode": "ask",
            "args": args,
            "model_metadata": base["model_metadata"],
            "added_files_rule": "every workspace file as --read (read-only)",
            "env": dict(base["env"]),
            "title": REVIEW_TITLE,
            "review": {"revision": REVIEW_HARNESS_REVISION, "output_channel": "answer", "findings_path": None},
        }

    def command(self, section: dict[str, Any], workspace_paths: list[str]) -> str:
        args = [*section["args"]]
        for path in sorted(workspace_paths):
            args += ["--read", path]
        return " ".join(["/opt/harness/venv/bin/aider", *(shlex.quote(a) for a in args)])

    def files(self, section: dict[str, Any], brief: str, workspace_paths: list[str]) -> dict[str, bytes]:
        return {
            "brief.md": brief.encode("utf-8"),
            "harness.sh": harness_script(self.command(section, workspace_paths)),
            "aider-model-metadata.json": json.dumps(section["model_metadata"]).encode(),
        }

    def final_answer(self, outputs: dict[str, bytes], summary: HarnessSummary, last_call_content: str) -> str:
        # Aider's stdout interleaves its own banner with the reply; the gateway
        # recorded the completion itself.
        return last_call_content or summary.final_message

    def files_opened(self, outputs: dict[str, bytes], workspace_paths: list[str]) -> tuple[list[str] | None, str]:
        return sorted(workspace_paths), "all_files_in_context"


REVIEW_HARNESSES: dict[str, ReviewHarness] = {
    h.name: h for h in (OpenCodeReview(), MiniSweAgentReview(), AiderReview())
}


def get_review_harness(name: str) -> ReviewHarness:
    try:
        return REVIEW_HARNESSES[name]
    except KeyError:
        msg = f"Unknown review harness {name!r} (available: {', '.join(REVIEW_HARNESSES)})"
        raise ValueError(msg) from None


# --------------------------------------------------------------------------
# Brief + payload extraction
# --------------------------------------------------------------------------


def render_brief(harness: ReviewHarness, *, scope_paths: list[str], schema: dict[str, Any]) -> str:
    """The review brief for one sample (*scope_paths* in the sample's presentation order)."""
    file_list = "\n".join(f"- `{path}`" for path in scope_paths)
    return render(
        template_bytes(BRIEF_TEMPLATE).decode(),
        {
            "file_list": file_list,
            "output_instructions": harness.output_instructions(),
            "schema": json.dumps(brief_schema(schema), indent=2),
        },
    )


def brief_schema(schema: Any) -> Any:
    """The findings schema as the agent is shown it: field ``description`` texts removed.

    The shared schema's descriptions speak of the single-shot rendering
    ("FILE header", "the number before '|'"), which an agent reading files on
    disk never sees; the brief states the agent's own rules instead. Only
    annotations are dropped: types, enums, required keys and patterns stay.
    """
    if isinstance(schema, dict):
        # A string "description" is an annotation; a property *named* description would be a dict.
        return {k: brief_schema(v) for k, v in schema.items() if not (k == "description" and isinstance(v, str))}
    if isinstance(schema, list):
        return [brief_schema(v) for v in schema]
    return schema


def finalizer_prompt() -> str:
    return template_bytes(FINALIZER_TEMPLATE).decode().strip()


_FENCE = re.compile(r"```(?:json|JSON)?[ \t]*\n(.*?)\n[ \t]*```", re.DOTALL)
_OBJECT_START = re.compile(r"\{\s*\"")
_MAX_DECODE_ATTEMPTS = 2000


def extract_json_document(text: str) -> str | None:
    """The findings JSON inside a free-text answer, or None.

    Preference order: the whole answer, the last fenced block that parses as
    an object, the last top-level object in the text that has a ``findings``
    key. Only ever returns text that is itself a JSON object; schema checks
    happen later.
    """
    stripped = text.strip()
    if not stripped:
        return None
    if _is_object(stripped):
        return stripped
    for block in reversed(_FENCE.findall(text)):
        if _is_object(block.strip()):
            return block.strip()
    decoder = json.JSONDecoder()
    best: str | None = None
    for attempts, match in enumerate(_OBJECT_START.finditer(text)):
        if attempts >= _MAX_DECODE_ATTEMPTS:
            break
        try:
            value, end = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict) and "findings" in value:
            best = text[match.start() : end]
    return best


def _is_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False


# Line terminators for str.splitlines that JSON leaves raw inside strings.
_JSON_RAW_SEPARATORS = (
    ("\u2028".encode(), b"\\u2028"),
    ("\u2029".encode(), b"\\u2029"),
    ("\u0085".encode(), b"\\u0085"),
)


def json_line_safe(data: bytes) -> bytes:
    """JSON-lines bytes with raw U+2028/U+2029/U+0085 written as JSON escapes.

    Each parses back to the same string, but no ``str.splitlines`` reader
    can cut a row at one any more. In valid JSON these characters can only
    occur inside strings, where the escape is equivalent.
    """
    for raw, escaped in _JSON_RAW_SEPARATORS:
        data = data.replace(raw, escaped)
    return data


def _workspace_relative(path: str) -> str:
    path = path.strip()
    prefix = WORKSPACE_DIR.rstrip("/") + "/"
    if path.startswith(prefix):
        path = path[len(prefix) :]
    while path.startswith("./"):
        path = path[2:]
    return path


def section_sha256(section: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(section, sort_keys=True, default=str).encode()).hexdigest()
