"""Trajectory parsers and observed inputs on the recorded smoke runs (section 5.1, 5.2 rules 6-7)."""

from __future__ import annotations

import json

import pytest

from backend.generation import identity
from backend.generation.contracts.harness import ExitInfo
from backend.generation.contracts.outcome import Termination
from backend.generation.harnesses import registry
from backend.generation.harnesses.base import STDOUT_FILE
from backend.generation.harnesses.base import captured_prompt
from backend.generation.harnesses.trajectory import common
from backend.generation.harnesses.trajectory import parser_for

from .conftest import FIXTURE_DIRS
from .conftest import SMOKED
from .conftest import load_fixture

EXPECTED_TOOLS = {
    "opencode": ["bash", "edit", "glob", "grep", "read", "todowrite", "write"],
    "mini-swe-agent": ["bash"],
    "aider": [],
    "goose": ["edit", "load_skill", "read_image", "shell", "tree", "write"],
    "claude-code": ["Bash", "Edit", "Read"],
    "codex": ["exec_command", "request_user_input", "view_image", "write_stdin"],
    "qwen-code": ["edit", "get_goal", "notebook_edit", "read_file", "run_shell_command", "update_goal"],
}
# Harnesses whose native prompt is pinned from harnesses/captured/: the model saw exactly the capture.
PINNED = {"opencode": "1.18.35", "codex": "0.166.0", "qwen-code": "0.26.6"}


def _exit(code: int, **kw) -> ExitInfo:
    values = {
        "exit_code": code,
        "elapsed_s": 5.0,
        "wall_clock_s": 1200,
        "idle_fired": False,
        "oom_killed": False,
        "budget_exhausted": None,
        "cancelled": False,
        "gateway_calls_ok": 2,
        "gateway_calls_failed_transport": 0,
    }
    values.update(kw)
    return ExitInfo(**values)


@pytest.mark.parametrize("harness", SMOKED)
def test_smoke_trajectory(harness):
    adapter = registry.get(harness)
    outputs, calls, root = load_fixture(FIXTURE_DIRS[harness])
    trajectory = adapter.parse_trajectory(outputs, calls)
    kinds = [s.kind for s in trajectory.steps]
    assert kinds.count("llm") >= 1
    assert [s.index for s in trajectory.steps] == list(range(len(trajectory.steps)))
    if harness == "aider":
        assert trajectory.edits == 1
        assert trajectory.steps[-1].files == ["hello.txt"]
    else:
        assert trajectory.shell_commands == 1
        shell = next(s for s in trajectory.steps if s.kind == "shell")
        assert shell.ok is True
        assert shell.command_sha is not None
        assert "Done." in (trajectory.final_message or "") or harness == "mini-swe-agent"
    assert not trajectory.ended_on_llm_error
    assert not trajectory.max_turns_hit
    exit_code = json.loads((root / "exit.json").read_text())["exit_code"]
    assert adapter.classify_termination(_exit(exit_code), trajectory) is Termination.COMPLETED


@pytest.mark.parametrize("harness", SMOKED)
def test_observed_inputs(harness):
    adapter = registry.get(harness)
    _, calls, _ = load_fixture(FIXTURE_DIRS[harness])
    observed = adapter.observed_inputs(calls)
    assert observed.tool_names == EXPECTED_TOOLS[harness]
    assert observed.calls_by_class == {"main": len(calls)}
    assert observed.system_prompt_sha is not None
    native = common.native_system_prompt_text(calls)
    assert observed.native_system_prompt_blob == identity.text_sha(native)  # what blobs.put_text returns
    if harness in PINNED:
        # Codex strips trailing whitespace from model_instructions_file; the text is otherwise verbatim.
        assert captured_prompt(harness, PINNED[harness]).strip() in native


def test_system_prompt_normalizers_ignore_run_specific_lines():
    adapter = registry.get("claude-code")
    _, calls, _ = load_fixture(FIXTURE_DIRS["claude-code"])
    other = json.loads(json.dumps(calls))
    first = other[0]
    system = first["request"]["system"]
    system[-1]["text"] = system[-1]["text"].replace("2026-11-03", "2027-01-15")
    first["native_system_prompt"] = first["native_system_prompt"].replace("2026-11-03", "2027-01-15")
    assert adapter.observed_inputs(other).system_prompt_sha == adapter.observed_inputs(calls).system_prompt_sha
    system[1]["text"] = "You are a different agent."
    first["native_system_prompt"] = "\n".join(part["text"] for part in system)
    assert adapter.observed_inputs(other).system_prompt_sha != adapter.observed_inputs(calls).system_prompt_sha


def test_max_turns_fixtures():
    claude = registry.get("claude-code")
    outputs, calls, root = load_fixture("claude-code-2.1.316-max-turns")
    trajectory = claude.parse_trajectory(outputs, calls)
    assert trajectory.max_turns_hit
    code = json.loads((root / "exit.json").read_text())["exit_code"]
    assert code == 1
    assert claude.classify_termination(_exit(code), trajectory) is Termination.MAX_TURNS

    qwen = registry.get("qwen-code")
    outputs, calls, root = load_fixture("qwen-code-0.26.6-max-turns")
    trajectory = qwen.parse_trajectory(outputs, calls)
    code = json.loads((root / "exit.json").read_text())["exit_code"]
    assert code == 53  # documented: session turn cap
    assert qwen.classify_termination(_exit(code), trajectory) is Termination.MAX_TURNS


@pytest.mark.parametrize("harness", [h for h in SMOKED if h != "mini-swe-agent"])
def test_incremental_parse_line_equals_batch(harness):
    outputs, calls, _ = load_fixture(FIXTURE_DIRS[harness])
    parser = parser_for(harness)
    streamed = []
    for line in outputs[STDOUT_FILE].decode().splitlines():
        streamed.extend(parser.parse_line(line))
    trajectory = parser.finish(calls)
    batch = registry.get(harness).parse_trajectory(outputs, calls)
    assert [s.kind for s in trajectory.steps] == [s.kind for s in batch.steps]
    assert len(streamed) == len(parser.steps)


def test_unparsable_lines_are_counted_not_dropped():
    outputs, calls, _ = load_fixture(FIXTURE_DIRS["goose"])
    trajectory = registry.get("goose").parse_trajectory(outputs, calls)
    assert trajectory.unparsed_lines == 3  # goose's start-up banner on stdout


def test_llm_error_tail_is_not_a_budget_refusal():
    ok = {"request_class": "main", "status": 200}
    assert not common.llm_error_tail([ok])
    assert common.llm_error_tail([ok, {"request_class": "main", "status": 502, "transport_error": True}])
    assert not common.llm_error_tail([ok, {"request_class": "main", "status": 400, "error": "Run budget exhausted."}])
    assert not common.llm_error_tail([ok, {"request_class": "auxiliary", "status": 500}])


def test_gateway_tokens_fill_llm_steps():
    outputs, calls, _ = load_fixture(FIXTURE_DIRS["codex"])
    trajectory = registry.get("codex").parse_trajectory(outputs, calls)
    llm = [s for s in trajectory.steps if s.kind == "llm"]
    assert [s.tokens_in for s in llm] == [c["usage"]["prompt_tokens"] for c in calls]


def test_no_calls_and_no_output_is_an_empty_trajectory_not_a_clean_one():
    for harness in SMOKED:
        trajectory = registry.get(harness).parse_trajectory({}, [])
        assert trajectory.steps == []
        assert trajectory.final_message is None


def test_codex_turn_failed_is_not_an_llm_error_by_itself():
    lines = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {"type": "turn.failed", "error": {"message": "Run budget exhausted. Stop working and summarise."}},
    ]
    parser = parser_for("codex")
    for line in lines:
        parser.parse_line(json.dumps(line))
    trajectory = parser.finish([{"request_class": "main", "status": 200}])
    assert not trajectory.ended_on_llm_error
    assert parser.turn_failed
    assert "budget" in parser.turn_failed
