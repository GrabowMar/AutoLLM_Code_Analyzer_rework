"""``FakeHarness``: a harness adapter that edits the workspace from a script (spec sections 5.1 and 12.2).

Its rendered command runs a small, dependency-free Python script inside the sandbox (``FakeSandbox`` or a
real container with ``python3``). The script follows the adapter rules of section 5.2: the brief arrives as
a file, never in argv. It then:

1. optionally calls the gateway (``FakeScript.call_gateway``) with the scripted chat bodies, stopping at the
   first budget refusal, the way a harness stops when the gateway says the run budget is spent;
2. otherwise writes a canned ``/logs/calls.jsonl`` (``FakeScript.calls``; one ``canned_call`` by default);
3. writes and deletes the scripted workspace files (from ``FakeScript.files`` and ``FakeScript.fixture_dir``);
4. writes its trajectory to ``/logs/fake-harness.json`` and exits with ``FakeScript.exit_code``.

Paths are prefixed with ``$LLMLAB_ROOT`` (empty in a container), which is how ``FakeSandbox`` maps the
in-container paths into a temporary directory. ``harness_context()`` builds a ``HarnessContext`` for tests.
Termination is classified by the inherited ``HarnessAdapter.classify_termination`` (the section 5.1 rules,
``harnesses.base.default_termination``), like every real adapter.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import ClassVar
from typing import Final
from typing import Literal
from typing import cast

from backend.generation import identity
from backend.generation.contracts.harness import GatewayWiring
from backend.generation.contracts.harness import HarnessCapabilities
from backend.generation.contracts.harness import HarnessContext
from backend.generation.contracts.harness import HarnessFacts
from backend.generation.contracts.harness import ImageSpec
from backend.generation.contracts.harness import ObservedInputs
from backend.generation.contracts.harness import RenderedHarness
from backend.generation.contracts.harness import Step
from backend.generation.contracts.harness import Trajectory
from backend.generation.contracts.harness import WireProtocol
from backend.generation.contracts.library import HarnessPresetContent
from backend.generation.contracts.resources import EffectivePolicy
from backend.generation.contracts.resources import preset_content
from backend.generation.harnesses.base import HarnessAdapter
from backend.generation.testing.fake_gateway import REQUEST_CLASSES
from backend.generation.testing.fake_gateway import canned_call

__all__ = [
    "FAKE_FACTS",
    "TRAJECTORY_FILE",
    "FakeHarness",
    "FakeScript",
    "harness_context",
]

RequestClass = Literal["main", "subagent", "compaction", "auxiliary"]
RUN_DIR: Final = "/run/fake-harness"
TRAJECTORY_FILE: Final = "fake-harness.json"
PARSER_VERSION: Final = 1

# Runs inside the sandbox: stdlib only, no access to Django.
_APPLY_SOURCE: Final = r"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.environ.get("LLMLAB_ROOT", "")
plan = json.load(open(ROOT + "/run/fake-harness/plan.json", encoding="utf-8"))
workspace = ROOT + plan["workspace"]
logs = ROOT + "/logs"
os.makedirs(logs, exist_ok=True)
brief = open(ROOT + plan["brief_path"], encoding="utf-8").read()
steps = []
llm_error = False


def step(kind, **fields):
    entry = {"index": len(steps), "kind": kind, "started": time.time()}
    entry.update(fields)
    steps.append(entry)


if plan["call_gateway"]:
    base = os.environ["OPENAI_BASE_URL"].rstrip("/")
    for number, call in enumerate(plan["requests"]):
        headers = {"Content-Type": "application/json"}
        headers["Authorization"] = "Bearer " + os.environ.get("OPENAI_API_KEY", "")
        headers.update(call["headers"])
        data = json.dumps(call["body"]).encode()
        request = urllib.request.Request(base + "/chat/completions", data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
            usage = payload.get("usage") or {}
            step("llm", ok=True, tokens_in=usage.get("prompt_tokens", 0), tokens_out=usage.get("completion_tokens", 0))
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            step("llm", ok=False)
            if "Run budget exhausted" in text:
                break
            if exc.code >= 500:
                llm_error = True
                break
        except (urllib.error.URLError, OSError):
            step("llm", ok=False)
            llm_error = True
            break
elif plan["calls"] is not None:
    with open(logs + "/calls.jsonl", "w", encoding="utf-8") as handle:
        for record in plan["calls"]:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    for record in plan["calls"]:
        usage = record.get("usage") or {}
        step("llm", ok=record.get("status", 200) < 400, tokens_in=usage.get("prompt_tokens", 0),
             tokens_out=usage.get("completion_tokens", 0))

for rel, entry in sorted(plan["files"].items()):
    path = os.path.join(workspace, rel)
    os.makedirs(os.path.dirname(path) or workspace, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(base64.b64decode(entry["data"]))
    if entry.get("mode") is not None:
        os.chmod(path, entry["mode"])
    step("edit", tool="write", ok=True, files=[rel])
for rel in plan["delete"]:
    path = os.path.join(workspace, rel)
    if os.path.exists(path):
        os.remove(path)
    step("edit", tool="delete", ok=True, files=[rel])

if plan["final_message"] is not None:
    step("message", ok=True)
trajectory = {
    "steps": steps,
    "final_message": plan["final_message"],
    "llm_error": llm_error or plan["llm_error"],
    "max_turns_hit": plan["max_turns_hit"],
    "brief_chars": len(brief),
}
with open(logs + "/fake-harness.json", "w", encoding="utf-8") as handle:
    json.dump(trajectory, handle, sort_keys=True)
if plan["stdout"]:
    print(plan["stdout"])
if plan["sleep_s"]:
    time.sleep(plan["sleep_s"])
sys.exit(plan["exit_code"])
"""


@dataclass
class FakeScript:
    """What one ``FakeHarness`` run does. ``fixture_dir`` files are read first; ``files`` override them."""

    files: Mapping[str, bytes | str] = field(default_factory=dict)
    fixture_dir: Path | str | None = None
    modes: Mapping[str, int] = field(default_factory=dict)
    delete: Sequence[str] = ()
    calls: Sequence[Mapping[str, Any]] | None = None
    call_gateway: bool = False
    requests: Sequence[Mapping[str, Any]] = ()
    request_headers: Sequence[Mapping[str, str]] = ()
    final_message: str | None = "Done."
    exit_code: int = 0
    sleep_s: float = 0.0
    stdout: str = ""
    llm_error: bool = False
    max_turns_hit: bool = False

    def workspace_files(self) -> dict[str, bytes]:
        """The files the script writes, as relative POSIX paths."""
        out: dict[str, bytes] = {}
        if self.fixture_dir is not None:
            root = Path(self.fixture_dir)
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    out[path.relative_to(root).as_posix()] = path.read_bytes()
        for rel, data in self.files.items():
            out[rel] = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        for rel in out:
            identity.tree_hash([(rel, 0o644, "0" * 64)])  # raises on absolute, empty or ".." paths
        return out


FAKE_FACTS: Final = HarnessFacts(
    facts_version=1,
    verified_against="backend.generation.testing.fake_harness (test double)",
    license="MIT",
    install="(none: the script is rendered into the sandbox)",
    headless_cmd='"${PYTHON:-python3}" /run/fake-harness/apply.py',
    front_protocol="openai_chat",
    protocol_modes=("native",),
    base_url_env={"OPENAI_BASE_URL": "{base_url}"},
    auth_env={"OPENAI_API_KEY": "{api_key}"},
    model_flags="(model name in the request body)",
    aux_model_knobs=[],
    disable_knobs={},
    max_turns_flag=None,
    compaction_knob=None,
    context_knob=None,
    output_cap_knob=None,
    reasoning_knob=None,
    cache_knob=None,
    tool_policy_flags=None,
    system_prompt_flags=None,
    path_restriction=None,
    system_prompt_capture="gateway_log",
    request_class_source="default:main",
    min_call_output_tokens=1,
    vendor_affinity=None,
    trajectory_source=f"/logs/{TRAJECTORY_FILE}",
    exit_codes={},
    crash_codes=(),
    system_prompt_normalizers=(),
    unsupported_knobs=("max_turns", "compaction", "context_limit"),
    open_questions=(),
)


def _first_user_text(request: Mapping[str, Any]) -> str | None:
    for message in request.get("messages") or []:
        if isinstance(message, Mapping) and message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else json.dumps(content, sort_keys=True)
    return None


class FakeHarness(HarnessAdapter):
    """A scripted adapter (``name="fake"``) speaking ``openai_chat`` in ``native`` mode."""

    name: ClassVar[str] = "fake"
    label: ClassVar[str] = "Fake harness (tests)"
    protocol: ClassVar[WireProtocol] = "openai_chat"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        agentic=True,
        requires_tool_calling=False,
        supports_max_turns=False,
        supports_system_prompt="override",
        supports_tool_policy=False,
        supports_compaction_control=False,
        supports_context_limit=False,
        supports_reasoning_effort_passthrough=False,
        supports_path_restriction=False,
        supports_request_class=False,
        headless_flag="(always headless)",
    )
    version_setting: ClassVar[str] = "AGENT_FAKE_VERSION"
    facts: ClassVar[HarnessFacts] = FAKE_FACTS

    def __init__(self, script: FakeScript | None = None) -> None:
        self.script = script or FakeScript()

    # -- environment -------------------------------------------------------------------------------

    def image_spec(self, version: str) -> ImageSpec:
        return ImageSpec(image="agent-fake", build_args={"FAKE_HARNESS_VERSION": version}, lock_files=())

    def gateway_wiring(self, gateway_host: str) -> GatewayWiring:
        base_url = f"http://{gateway_host}/v1"
        return GatewayWiring(
            protocol="openai_chat",
            protocol_mode="native",
            base_url=base_url,
            env={"OPENAI_BASE_URL": base_url},
        )

    def render(self, ctx: HarnessContext) -> RenderedHarness:
        script = self.script
        requests = []
        for number, body in enumerate(script.requests):
            request = dict(body)
            request.setdefault("model", ctx.model_name)
            messages = list(request.get("messages") or [{"role": "user", "content": "Read /run/brief.md."}])
            if ctx.system_prompt is not None and not any(m.get("role") == "system" for m in messages):
                messages.insert(0, {"role": "system", "content": ctx.system_prompt})
            request["messages"] = messages
            headers = dict(script.request_headers[number]) if number < len(script.request_headers) else {}
            requests.append({"body": request, "headers": headers})
        calls = None
        if not script.call_gateway:
            calls = [dict(call) for call in script.calls] if script.calls is not None else [canned_call(1)]
        files = {
            rel: {"data": base64.b64encode(data).decode("ascii"), "mode": script.modes.get(rel)}
            for rel, data in script.workspace_files().items()
        }
        plan = {
            "workspace": ctx.workspace,
            "brief_path": ctx.brief_path,
            "files": files,
            "delete": list(script.delete),
            "calls": calls,
            "call_gateway": script.call_gateway,
            "requests": requests,
            "final_message": script.final_message,
            "exit_code": script.exit_code,
            "sleep_s": script.sleep_s,
            "stdout": script.stdout,
            "llm_error": script.llm_error,
            "max_turns_hit": script.max_turns_hit,
        }
        return RenderedHarness(
            files={
                ctx.brief_path: ctx.brief.encode("utf-8"),
                f"{RUN_DIR}/apply.py": _APPLY_SOURCE.lstrip().encode("utf-8"),
                f"{RUN_DIR}/plan.json": json.dumps(plan, sort_keys=True).encode("utf-8"),
            },
            env={
                **ctx.gateway.env,
                "OPENAI_BASE_URL": ctx.gateway.base_url,
                "OPENAI_API_KEY": ctx.gateway.api_key,
                "FAKE_HARNESS_MODEL": ctx.model_name,
            },
            command=f'"${{PYTHON:-python3}}" "${{LLMLAB_ROOT:-}}{RUN_DIR}/apply.py"',
            artifacts=(f"/logs/{TRAJECTORY_FILE}",),
        )

    # -- results -------------------------------------------------------------------------------------

    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory:
        """Steps from ``/logs/fake-harness.json``; without it, one ``llm`` step per gateway call."""
        raw = outputs.get(TRAJECTORY_FILE)
        if raw is None:
            steps = [
                Step(
                    index=number,
                    kind="llm",
                    ok=(call.get("status") or 200) < 400,
                    tokens_in=(call.get("usage") or {}).get("prompt_tokens", 0),
                    tokens_out=(call.get("usage") or {}).get("completion_tokens", 0),
                )
                for number, call in enumerate(calls)
            ]
            return Trajectory(steps=steps, parser=self.name, parser_version=PARSER_VERSION)
        data = json.loads(raw)
        steps = [Step.model_validate(entry) for entry in data.get("steps") or []]
        edits = sum(1 for s in steps if s.kind == "edit")
        return Trajectory(
            steps=steps,
            final_message=data.get("final_message"),
            tool_calls=sum(1 for s in steps if s.kind in {"tool", "edit", "shell"}),
            edits=edits,
            shell_commands=sum(1 for s in steps if s.kind == "shell"),
            ended_on_llm_error=bool(data.get("llm_error")),
            max_turns_hit=bool(data.get("max_turns_hit")),
            parser=self.name,
            parser_version=PARSER_VERSION,
        )

    def observed_inputs(self, calls: list[dict[str, Any]]) -> ObservedInputs:
        """System prompt, tools and first user message of the first ``main`` call, and calls by class."""
        by_class: dict[RequestClass, int] = {}
        for call in calls:
            raw = call.get("request_class") or "main"
            request_class = cast("RequestClass", raw if raw in REQUEST_CLASSES else "auxiliary")
            by_class[request_class] = by_class.get(request_class, 0) + 1
        first = next((c for c in calls if (c.get("request_class") or "main") == "main"), None)
        if first is None:
            return ObservedInputs(calls_by_class=by_class)
        request = first.get("request") or {}
        system_prompt = first.get("native_system_prompt")
        tools = request.get("tools") or []
        user = _first_user_text(request)
        return ObservedInputs(
            system_prompt_sha=identity.text_sha(system_prompt) if system_prompt is not None else None,
            tool_names=sorted(str((t.get("function") or t).get("name", "")) for t in tools if isinstance(t, Mapping)),
            tool_specs_sha=identity.H(tools) if tools else None,
            first_user_message_sha=identity.text_sha(user) if user is not None else None,
            calls_by_class=by_class,
        )

    def normalize_options(self, options: dict[str, Any]) -> dict[str, Any]:
        """The fake has no options: unset values are dropped and the rest is kept as given."""
        return {key: value for key, value in options.items() if value is not None}


def harness_context(
    *,
    brief: str = "Build the todo app described in the task.",
    model_name: str = "deepseek-v4.1-flash",
    gateway_host: str = "gateway:8080",
    system_prompt: str | None = None,
    run_id: str = "00000000-0000-4000-8000-000000000001",
    **overrides: Any,
) -> HarnessContext:
    """A ``HarnessContext`` for the fake harness with the frugal policy (``overrides`` replace fields)."""
    values: dict[str, Any] = {
        "run_id": run_id,
        "brief": brief,
        "system_prompt": system_prompt,
        "model_name": model_name,
        "gateway": FakeHarness().gateway_wiring(gateway_host),
        "preset": HarnessPresetContent(harness=FakeHarness.name, harness_version="1"),
        "policy": EffectivePolicy(
            content=preset_content("frugal"),
            budget_basis="usd",
            cap_basis="billed",
            serving_tier="standard",
        ),
    }
    values.update(overrides)
    return HarnessContext(**values)
