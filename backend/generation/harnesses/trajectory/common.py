"""Shared trajectory machinery: steps, incremental parsing and gateway-log observations (spec 5.1, 5.2).

Every harness parser is an :class:`IncrementalParser`: ``parse_line(line)`` turns one line of the
harness's own output into zero or more new :class:`Step`\\ s (the runner publishes them live as SSE
``trajectory_step`` events), and ``finish(calls)`` returns the :class:`Trajectory`. The gateway log
(``calls.jsonl``) is authoritative for LLM facts: steps of kind ``llm`` take their token counts from it
when the harness reported none, and :func:`observed_inputs` reads the native system prompt, the tools and
the first user message of the first ``main`` call from it.

Rules, ported from the v2 parser (``_legacy/services/agent/trajectory.py``):

* a value that could not be measured is ``None`` (``ok``, ``started``, ``duration_s``), never 0 or True;
* a line that is not valid JSON in a JSON stream is counted in ``unparsed_lines``, never dropped silently;
* commands are kept only as ``command_sha`` (the full text stays in the evidence files);
* bump the parser's ``version`` on any change to how a line maps to steps.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import ClassVar
from typing import Final
from typing import Literal
from typing import cast

from backend.generation import identity
from backend.generation.contracts.harness import ObservedInputs
from backend.generation.contracts.harness import Step
from backend.generation.contracts.harness import Trajectory

__all__ = [
    "FINAL_MESSAGE_CHARS",
    "REQUEST_CLASSES",
    "IncrementalParser",
    "StepKind",
    "call_failed",
    "calls_by_class",
    "classify_tool",
    "first_main_call",
    "first_user_message",
    "llm_error_tail",
    "native_system_prompt_text",
    "normalize_system_prompt",
    "observed_inputs",
    "parse_arguments",
    "parse_jsonl",
    "request_system_prompt",
    "request_tools",
    "text_of",
]

StepKind = Literal["llm", "tool", "edit", "shell", "message", "compaction", "subagent"]
RequestClass = Literal["main", "subagent", "compaction", "auxiliary"]
REQUEST_CLASSES: Final[tuple[RequestClass, ...]] = ("main", "subagent", "compaction", "auxiliary")
FINAL_MESSAGE_CHARS: Final = 8000

# Tool name -> step kind. Names are compared lower-cased and without an extension prefix
# (``developer__shell`` -> ``shell``).
SHELL_TOOLS: Final = frozenset(
    {"bash", "shell", "run_shell_command", "exec_command", "execute_bash", "shell_command", "local_shell", "exec"},
)
EDIT_TOOLS: Final = frozenset(
    {
        "write",
        "edit",
        "multiedit",
        "apply_patch",
        "patch",
        "str_replace_editor",
        "str_replace_based_edit_tool",
        "text_editor",
        "write_file",
        "replace",
        "edit_file",
        "create_file",
        "notebookedit",
        "file_change",
    },
)
SUBAGENT_TOOLS: Final = frozenset({"task", "agent", "subagent", "spawn_agent", "delegate"})
TOOL_KINDS: Final = frozenset({"tool", "edit", "shell", "subagent"})
_FILE_KEYS: Final = ("file_path", "filePath", "path", "absolute_path", "notebook_path", "target_file", "filename")


def classify_tool(name: str | None) -> StepKind:
    """The step kind of a tool call by name (``tool`` when it is neither a shell, edit nor subagent tool)."""
    short = (name or "").rsplit("__", 1)[-1].lower()
    if short in SHELL_TOOLS:
        return "shell"
    if short in EDIT_TOOLS:
        return "edit"
    if short in SUBAGENT_TOOLS:
        return "subagent"
    return "tool"


def tool_files(args: Mapping[str, Any] | None, workspace: str = "/workspace") -> list[str]:
    """Workspace-relative paths an edit tool call names (absolute paths under the workspace are stripped)."""
    if not isinstance(args, Mapping):
        return []
    out: list[str] = []
    for key in _FILE_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            rel = value[len(workspace) + 1 :] if value.startswith(workspace + "/") else value
            if rel not in out:
                out.append(rel)
    edits = args.get("edits") or args.get("changes")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, Mapping):
                for rel in tool_files(edit, workspace):
                    if rel not in out:
                        out.append(rel)
    return out


def command_sha(command: Any) -> str | None:
    """``text_sha`` of a shell command (a list is joined with spaces), None when there is none."""
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    if not isinstance(command, str) or not command:
        return None
    return identity.text_sha(command)


def text_of(content: Any) -> str:
    """Message content as text (OpenAI string or list of parts; Anthropic/Gemini blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, Mapping):
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    if isinstance(content, Mapping):
        return text_of(content.get("parts") or content.get("text"))
    return "" if content is None else str(content)


def parse_jsonl(data: bytes | str | None) -> tuple[list[dict[str, Any]], int]:
    """JSON objects of a JSONL stream and the number of non-empty lines that were not JSON objects."""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else (data or "")
    rows: list[dict[str, Any]] = []
    unparsed = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            unparsed += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            unparsed += 1
    return rows, unparsed


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Tool-call arguments as a dict (OpenAI sends a JSON string; unparsable text is kept as ``_raw``)."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {"_raw": raw}
    return value if isinstance(value, dict) else {"_raw": raw}


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(int(value), 0)


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# -- gateway log -----------------------------------------------------------------------------------------


def _request_class(call: Mapping[str, Any]) -> RequestClass:
    raw = str(call.get("request_class") or "main").lower()
    return cast("RequestClass", raw if raw in REQUEST_CLASSES else "auxiliary")


def calls_by_class(calls: Iterable[Mapping[str, Any]]) -> dict[RequestClass, int]:
    """Calls per request class (unknown classes count as ``auxiliary``, as in the gateway)."""
    counts: dict[RequestClass, int] = {}
    for call in calls:
        cls = _request_class(call)
        counts[cls] = counts.get(cls, 0) + 1
    return counts


def first_main_call(calls: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return next((c for c in calls if _request_class(c) == "main"), None)


def call_failed(call: Mapping[str, Any]) -> bool:
    status = call.get("status")
    return not isinstance(status, int) or status >= 400 or bool(call.get("transport_error"))


_BUDGET_TEXT: Final = re.compile(r"budget exhausted|context_budget", re.IGNORECASE)


def llm_error_tail(calls: Sequence[Mapping[str, Any]]) -> bool:
    """True when the run's last main call failed upstream (after the gateway's retries), not on a budget.

    Budget refusals and ``call.max_input_tokens`` refusals are policy, not LLM errors (they are never
    written to ``calls.jsonl`` by the gateway, and are excluded here by their text when they are).
    """
    main = [c for c in calls if _request_class(c) == "main"]
    if not main:
        return False
    last = main[-1]
    if not call_failed(last):
        return False
    return not _BUDGET_TEXT.search(str(last.get("error") or ""))


def _detect_protocol(call: Mapping[str, Any]) -> str:
    front = call.get("front_protocol")
    if isinstance(front, str) and front:
        return front
    request = call.get("request") or {}
    if "contents" in request:
        return "gemini_generate"
    if "input" in request and "messages" not in request:
        return "openai_responses"
    if "system" in request or request.get("anthropic_version"):
        return "anthropic_messages"
    return "openai_chat"


def request_system_prompt(request: Mapping[str, Any], protocol: str) -> str | None:
    """The system prompt of a native request body (None when it has none)."""
    if protocol == "anthropic_messages":
        system = request.get("system")
        return text_of(system) if system else None
    if protocol == "openai_responses":
        parts = [text_of(request.get("instructions"))] if request.get("instructions") else []
        for item in request.get("input") or []:
            if isinstance(item, Mapping) and item.get("role") in {"system", "developer"}:
                parts.append(text_of(item.get("content")))
        return "\n".join(parts) if parts else None
    if protocol == "gemini_generate":
        inst = request.get("systemInstruction") or request.get("system_instruction")
        return text_of(inst) if inst else None
    messages = [m for m in request.get("messages") or [] if isinstance(m, Mapping)]
    parts = [text_of(m.get("content")) for m in messages if m.get("role") in {"system", "developer"}]
    return "\n".join(parts) if parts else None


def request_tools(request: Mapping[str, Any], protocol: str) -> list[Any]:
    """The tool specs of a request body, flattened to one entry per tool."""
    tools = request.get("tools") or []
    if protocol != "gemini_generate":
        return [t for t in tools if isinstance(t, Mapping)]
    flat: list[Any] = []
    for tool in tools:
        if isinstance(tool, Mapping):
            flat.extend(tool.get("functionDeclarations") or tool.get("function_declarations") or [])
    return flat


def _tool_name(tool: Mapping[str, Any]) -> str:
    fn = tool.get("function")
    if isinstance(fn, Mapping) and fn.get("name"):
        return str(fn["name"])
    return str(tool.get("name") or tool.get("type") or "")


def first_user_message(request: Mapping[str, Any], protocol: str) -> str | None:  # noqa: PLR0911 - per protocol
    if protocol == "openai_responses":
        items = request.get("input")
        if isinstance(items, str):
            return items
        for item in items or []:
            if isinstance(item, Mapping) and item.get("role") == "user":
                return text_of(item.get("content"))
        return None
    if protocol == "gemini_generate":
        for content in request.get("contents") or []:
            if isinstance(content, Mapping) and content.get("role", "user") == "user":
                return text_of(content.get("parts"))
        return None
    for message in request.get("messages") or []:
        if isinstance(message, Mapping) and message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else text_of(content)
    return None


def normalize_system_prompt(prompt: str, normalizers: Iterable[str]) -> str:
    """Replace run-specific spans (date, cwd, git status, model name) before hashing (section 5.2 rule 7).

    Each normalizer is a regex (multi-line mode); every match is replaced by ``<var>``, so two runs that
    differ only in those spans hash equal and a change anywhere else changes the hash.
    """
    for pattern in normalizers:
        prompt = re.sub(pattern, "<var>", prompt, flags=re.MULTILINE)
    return prompt


def native_system_prompt_text(calls: Sequence[Mapping[str, Any]]) -> str | None:
    """The native system prompt of the first ``main`` call, verbatim (what the runner stores as a blob).

    The gateway logs it on that record (``native_system_prompt``); older records without the field fall
    back to the request body. ``blobs.put_text`` of this text has the sha that
    :func:`observed_inputs` reports as ``native_system_prompt_blob``.
    """
    first = first_main_call(calls)
    if first is None:
        return None
    logged = first.get("native_system_prompt")
    if isinstance(logged, str):
        return logged
    request = first.get("request")
    if isinstance(request, Mapping):
        return request_system_prompt(request, _detect_protocol(first))
    return None


def observed_inputs(calls: Sequence[Mapping[str, Any]], normalizers: Iterable[str] = ()) -> ObservedInputs:
    """``ObservedInputs`` from the gateway log: the first ``main`` call's system prompt, tools, user message.

    ``system_prompt_sha`` hashes the prompt after ``normalizers``; ``native_system_prompt_blob`` is the
    gateway's blob sha when it logged one, else ``text_sha`` of the verbatim prompt (the runner stores the
    text with ``blobs.put_text``, which yields the same sha).
    """
    by_class = calls_by_class(calls)
    first = first_main_call(calls)
    if first is None:
        return ObservedInputs(calls_by_class=by_class)
    protocol = _detect_protocol(first)
    request = first.get("request") if isinstance(first.get("request"), Mapping) else {}
    request = cast("Mapping[str, Any]", request)
    prompt = native_system_prompt_text(calls)
    tools = request_tools(request, protocol)
    user = first_user_message(request, protocol)
    blob = first.get("native_system_prompt_blob")
    if not isinstance(blob, str) or not blob:
        blob = identity.text_sha(prompt) if prompt is not None else None
    return ObservedInputs(
        system_prompt_sha=identity.text_sha(normalize_system_prompt(prompt, normalizers)) if prompt else None,
        tool_names=sorted(_tool_name(t) for t in tools),
        tool_specs_sha=identity.H(json.loads(json.dumps(tools, default=str))) if tools else None,
        first_user_message_sha=identity.text_sha(user) if user is not None else None,
        calls_by_class=by_class,
        native_system_prompt_blob=blob,
    )


# -- incremental parsing ------------------------------------------------------------------------------------


class IncrementalParser:
    """Base parser: subclasses implement :meth:`on_event` (JSON streams) or override :meth:`parse_line`.

    ``parse_line`` returns the steps the line completed, so the runner can stream them; ``finish(calls)``
    fills token counts of ``llm`` steps from the gateway log where the harness reported none, adds one
    ``llm`` step per main call when the harness reported no LLM turns at all, and builds the Trajectory.
    """

    name: ClassVar[str] = "common"
    version: ClassVar[int] = 1
    json_stream: ClassVar[bool] = True

    def __init__(self) -> None:
        self.steps: list[Step] = []
        self.final_message: str | None = None
        self.max_turns_hit = False
        self.llm_error = False
        self.unparsed_lines = 0
        self._pending: dict[str, Step] = {}

    # -- building blocks --------------------------------------------------------------------------------

    def add(
        self,
        kind: StepKind,
        *,
        tool: str | None = None,
        ok: bool | None = None,
        started: float | None = None,
        duration_s: float | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        files: Sequence[str] = (),
        command: Any = None,
        call_id: str | None = None,
    ) -> Step:
        step = Step(
            index=len(self.steps),
            kind=kind,
            tool=tool,
            ok=ok,
            started=started,
            duration_s=duration_s if duration_s is None else max(duration_s, 0.0),
            tokens_in=_int(tokens_in),
            tokens_out=_int(tokens_out),
            files=list(files),
            command_sha=command_sha(command),
        )
        self.steps.append(step)
        if call_id:
            self._pending[call_id] = step
        return step

    def add_tool_call(
        self,
        name: str | None,
        args: Mapping[str, Any] | None,
        *,
        call_id: str | None = None,
        ok: bool | None = None,
        started: float | None = None,
        duration_s: float | None = None,
    ) -> Step:
        """A tool call step, classified by name; shell commands keep only their sha, edits their files."""
        kind = classify_tool(name)
        args = args if isinstance(args, Mapping) else {}
        command = args.get("command") or args.get("cmd") if kind == "shell" else None
        files = tool_files(args) if kind == "edit" else []
        return self.add(
            kind,
            tool=name,
            ok=ok,
            started=started,
            duration_s=duration_s,
            files=files,
            command=command,
            call_id=call_id,
        )

    def resolve(self, call_id: str | None, *, ok: bool | None, ended: float | None = None) -> Step | None:
        """Record the result of a pending tool call (``ok`` and, with ``ended``, the duration)."""
        if not call_id or call_id not in self._pending:
            return None
        step = self._pending.pop(call_id)
        update: dict[str, Any] = {"ok": ok}
        if ended is not None and step.started is not None:
            update["duration_s"] = max(ended - step.started, 0.0)
        new = step.model_copy(update=update)
        self.steps[step.index] = new
        return new

    def message(self, text: str | None) -> None:
        if text and text.strip():
            self.final_message = text

    # -- the parser protocol --------------------------------------------------------------------------------

    def parse_line(self, line: str) -> list[Step]:
        """Feed one output line; returns the steps it added."""
        if not line.strip():
            return []
        before = len(self.steps)
        if self.json_stream:
            try:
                event = json.loads(line)
            except ValueError:
                self.unparsed_lines += 1
                return []
            if not isinstance(event, dict):
                self.unparsed_lines += 1
                return []
            self.on_event(event)
        else:
            self.on_text(line)
        return self.steps[before:]

    def on_event(self, event: dict[str, Any]) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def on_text(self, line: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def feed(self, data: bytes | str | None) -> None:
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else (data or "")
        for line in text.splitlines():
            self.parse_line(line)

    def finish(self, calls: Sequence[Mapping[str, Any]] = ()) -> Trajectory:
        steps = self._with_gateway_tokens(list(self.steps), calls)
        final = self.final_message
        truncated = final is not None and len(final) > FINAL_MESSAGE_CHARS
        return Trajectory(
            steps=steps,
            final_message=final[:FINAL_MESSAGE_CHARS] if final is not None else None,
            final_message_truncated=truncated,
            tool_calls=sum(1 for s in steps if s.kind in TOOL_KINDS),
            edits=sum(1 for s in steps if s.kind == "edit"),
            shell_commands=sum(1 for s in steps if s.kind == "shell"),
            compactions=sum(1 for s in steps if s.kind == "compaction"),
            subagent_calls=sum(1 for s in steps if s.kind == "subagent"),
            ended_on_llm_error=self.llm_error or llm_error_tail(calls),
            max_turns_hit=self.max_turns_hit,
            parser=self.name,
            parser_version=self.version,
            unparsed_lines=self.unparsed_lines,
        )

    @staticmethod
    def _with_gateway_tokens(steps: list[Step], calls: Sequence[Mapping[str, Any]]) -> list[Step]:
        main = [c for c in calls if _request_class(c) == "main"]
        llm_steps = [s for s in steps if s.kind == "llm"]
        if not llm_steps and main:
            # The harness reported no LLM turns: one llm step per main call, in call order, first.
            gateway_steps = [
                Step(
                    index=i,
                    kind="llm",
                    ok=not call_failed(call),
                    started=_float(call.get("started")),
                    duration_s=_float(call.get("duration_s")),
                    tokens_in=_int((call.get("usage") or {}).get("prompt_tokens")),
                    tokens_out=_int((call.get("usage") or {}).get("completion_tokens")),
                )
                for i, call in enumerate(main)
            ]
            offset = len(gateway_steps)
            return gateway_steps + [s.model_copy(update={"index": s.index + offset}) for s in steps]
        out = list(steps)
        for ordinal, step in enumerate(llm_steps):
            if ordinal >= len(main) or step.tokens_in or step.tokens_out:
                continue
            usage = main[ordinal].get("usage") or {}
            out[step.index] = step.model_copy(
                update={
                    "tokens_in": _int(usage.get("prompt_tokens")),
                    "tokens_out": _int(usage.get("completion_tokens")),
                },
            )
        return out
