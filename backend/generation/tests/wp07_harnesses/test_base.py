"""``harnesses/base.py``: options, knob resolution, the default termination rules and the entry script."""

from __future__ import annotations

import itertools
import subprocess
import tarfile
from pathlib import Path

import pytest

from backend.generation.contracts.harness import ExitInfo
from backend.generation.contracts.harness import RenderedHarness
from backend.generation.contracts.harness import Trajectory
from backend.generation.contracts.outcome import Termination
from backend.generation.harnesses import registry
from backend.generation.harnesses.base import BRIEF_PATH
from backend.generation.harnesses.base import COMMAND_SCRIPT
from backend.generation.harnesses.base import ENTRY_SCRIPT
from backend.generation.harnesses.base import CapturedPromptMissingError
from backend.generation.harnesses.base import HarnessAdapter
from backend.generation.harnesses.base import OptionField
from backend.generation.harnesses.base import captured_prompt
from backend.generation.harnesses.base import default_termination
from backend.generation.harnesses.base import entry_script
from backend.generation.harnesses.base import knobs
from backend.generation.harnesses.base import normalize_options
from backend.generation.harnesses.base import sandbox_payload
from backend.generation.testing.fake_harness import FAKE_FACTS
from backend.generation.testing.fake_harness import FakeHarness

from .conftest import make_ctx

QWEN = registry.get("qwen-code").facts


def _exit(**overrides) -> ExitInfo:
    values = {
        "exit_code": 0,
        "elapsed_s": 100.0,
        "wall_clock_s": 1200,
        "idle_fired": False,
        "oom_killed": False,
        "budget_exhausted": None,
        "cancelled": False,
        "gateway_calls_ok": 5,
        "gateway_calls_failed_transport": 0,
    }
    values.update(overrides)
    return ExitInfo(**values)


TRAJ = Trajectory(parser="t", parser_version=1)


class TestDefaultTermination:
    @pytest.mark.parametrize(
        ("overrides", "trajectory", "expected"),
        [
            ({}, {}, Termination.COMPLETED),
            ({"cancelled": True, "oom_killed": True}, {}, Termination.CANCELLED),
            ({"oom_killed": True, "exit_code": 137}, {}, Termination.OOM),
            # Wall clock from elapsed time, never from the exit code:
            ({"exit_code": 137, "elapsed_s": 1198.5}, {}, Termination.WALL_CLOCK),
            ({"exit_code": 124, "elapsed_s": 1300.0}, {}, Termination.WALL_CLOCK),
            ({"exit_code": 0, "elapsed_s": 1199.0}, {}, Termination.WALL_CLOCK),
            ({"exit_code": 137, "elapsed_s": 50.0}, {}, Termination.HARNESS_CRASH),
            ({"exit_code": 143, "elapsed_s": 50.0, "idle_fired": True}, {}, Termination.IDLE),
            ({"exit_code": 1, "budget_exhausted": "requests"}, {}, Termination.BUDGET),
            (
                {"exit_code": 1, "gateway_calls_ok": 0, "gateway_calls_failed_transport": 3},
                {},
                Termination.INFRA,
            ),
            ({"exit_code": 1}, {"ended_on_llm_error": True}, Termination.LLM_ERROR),
            ({"exit_code": 1}, {"max_turns_hit": True}, Termination.MAX_TURNS),
            ({"exit_code": 2}, {}, Termination.HARNESS_ERROR),
            ({"exit_code": None}, {}, Termination.HARNESS_CRASH),
            ({"exit_code": -9}, {}, Termination.HARNESS_CRASH),
        ],
    )
    def test_rules(self, overrides, trajectory, expected):
        traj = TRAJ.model_copy(update=trajectory)
        assert default_termination(_exit(**overrides), traj, FAKE_FACTS) is expected

    def test_documented_exit_codes(self):
        assert default_termination(_exit(exit_code=53), TRAJ, QWEN) is Termination.MAX_TURNS
        assert default_termination(_exit(exit_code=55), TRAJ, QWEN) is Termination.BUDGET
        # A documented code never overrides the elapsed-time rule.
        assert default_termination(_exit(exit_code=53, elapsed_s=1300), TRAJ, QWEN) is Termination.WALL_CLOCK

    def test_fake_harness_classifies_with_the_base_rules(self):
        """WP-01's FakeHarness inherits ``HarnessAdapter.classify_termination`` (no copy of the rules to drift)
        and its facts (no documented exit codes) give the plain section 5.1 answer over the whole grid."""
        fake = FakeHarness()
        assert "classify_termination" not in vars(FakeHarness)
        assert FakeHarness.classify_termination is HarnessAdapter.classify_termination
        grid = itertools.product(
            (0, 1, 2, 124, 137, 143, None, -15),
            (10.0, 1199.0),
            (False, True),  # idle
            (None, "cost"),
            (0, 3),  # calls ok
            (0, 2),  # transport failures
            (False, True),  # llm error
        )
        for code, elapsed, idle, budget, ok, transport, llm_error in grid:
            info = _exit(
                exit_code=code,
                elapsed_s=elapsed,
                idle_fired=idle,
                budget_exhausted=budget,
                gateway_calls_ok=ok,
                gateway_calls_failed_transport=transport,
            )
            traj = TRAJ.model_copy(update={"ended_on_llm_error": llm_error})
            assert fake.classify_termination(info, traj) is default_termination(info, traj, FAKE_FACTS)

    def test_every_adapter_inherits_the_rules(self):
        info = _exit(exit_code=137, elapsed_s=1199.0)
        for adapter in registry.adapters():
            assert adapter.classify_termination(info, TRAJ) is Termination.WALL_CLOCK


class TestOptions:
    FIELDS = (
        OptionField(
            key="fmt",
            type="enum",
            label="F",
            help="",
            default="diff",
            choices=(("diff", "d"), ("whole", "w")),
        ),
        OptionField(key="n", type="int", label="N", help="", default=5, min=0, max=10),
        OptionField(key="flag", type="bool", label="B", help="", default=False),
        OptionField(key="m", type="multi_enum", label="M", help="", default=(), choices=(("a", "a"), ("b", "b"))),
    )

    def test_defaults_are_dropped_and_values_normalized(self):
        assert normalize_options(self.FIELDS, {"fmt": "diff", "n": 5.0}, harness="x") == {}
        assert normalize_options(self.FIELDS, {"n": 7.0, "m": ["b", "a", "b"]}, harness="x") == {
            "n": 7,
            "m": ["a", "b"],
        }
        assert normalize_options(self.FIELDS, None, harness="x") == {}

    def test_every_problem_is_reported(self):
        with pytest.raises(ValueError, match="Unknown x options: zz") as exc:
            normalize_options(self.FIELDS, {"zz": 1, "n": 11, "flag": "yes", "fmt": "udiff"}, harness="x")
        message = str(exc.value)
        assert "n must be an integer between 0 and 10" in message
        assert "flag must be true or false" in message
        assert "fmt must be one of diff, whole" in message

    def test_adapter_options(self):
        aider = registry.get("aider")
        assert aider.normalize_options({"edit_format": "whole", "map_tokens": 1024}) == {"edit_format": "whole"}
        with pytest.raises(ValueError, match="Unknown opencode options"):
            registry.get("opencode").normalize_options({"edit_format": "whole"})
        schema = [f.to_dict() for f in aider.option_schema()]
        assert schema[0]["choices"][0] == {"value": "diff", "label": "Search/replace blocks (diff)"}


class TestKnobs:
    def test_turn_cap_is_the_stricter_of_preset_and_policy(self):
        assert knobs(make_ctx("goose", max_turns=10)).max_turns == 10
        assert knobs(make_ctx("goose", max_turns=500)).max_turns == knobs(make_ctx("goose")).max_turns
        ctx = make_ctx("goose", policy_updates={"run.max_turns": None})
        assert knobs(ctx).max_turns is None

    def test_command_timeout_policy_overrides_preset(self):
        # Spec 4 knob table: time.command_timeout_s "overrides the preset when set".
        ctx = make_ctx("goose", command_timeout_s=300, policy_updates={"time.command_timeout_s": 120})
        assert knobs(ctx).command_timeout_s == 120
        ctx = make_ctx("goose", command_timeout_s=300, policy_updates={"time.command_timeout_s": None})
        assert knobs(ctx).command_timeout_s == 300
        ctx = make_ctx("goose", policy_updates={"time.command_timeout_s": 45})
        assert knobs(ctx).command_timeout_s == 45
        ctx = make_ctx("goose", policy_updates={"time.command_timeout_s": None})
        assert knobs(ctx).command_timeout_s is None

    def test_context_window_fallbacks(self):
        ctx = make_ctx("opencode")
        assert knobs(ctx).context_window(ctx) == knobs(ctx).context_tokens  # the explicit cap first
        ctx = make_ctx("opencode", policy_updates={"context.max_context_tokens": None})
        assert knobs(ctx).context_window(ctx) == knobs(ctx).max_input_tokens  # then the call cap
        ctx = make_ctx("opencode", policy_updates={"context.max_context_tokens": None, "call.max_input_tokens": None})
        assert knobs(ctx).context_window(ctx) == 128_000

    def test_unsupported_knobs_are_reported_only_when_active(self):
        opencode = registry.get("opencode")
        active = opencode.unsupported_knobs(make_ctx("opencode"))
        assert "run.max_turns" in active  # frugal sets a turn cap OpenCode cannot map
        inactive = opencode.unsupported_knobs(make_ctx("opencode", policy_updates={"run.max_turns": None}))
        assert "run.max_turns" not in inactive
        allow = opencode.unsupported_knobs(make_ctx("opencode", tool_policy={"allow": ["bash"]}))
        assert "tool_policy.allow" in allow


class TestCapturedPrompts:
    def test_pinned_versions_have_captures(self):
        for name in ("opencode", "codex", "qwen-code", "goose", "claude-code"):
            adapter = registry.get(name)
            assert captured_prompt(name, adapter.version()).strip()

    def test_missing_capture_is_an_error_not_an_empty_prompt(self):
        with pytest.raises(CapturedPromptMissingError, match=r"opencode-0\.0\.1"):
            captured_prompt("opencode", "0.0.1")
        ctx = make_ctx("opencode", harness_version="0.0.1")
        with pytest.raises(CapturedPromptMissingError):
            registry.get("opencode").render(ctx)


class TestEntryScript:
    def test_anchored_excludes_and_timeout(self):
        script = entry_script(wall_clock_s=1200, stdin_path=BRIEF_PATH, max_workspace_bytes=1024)
        assert "timeout -s TERM -k 15 1200 sh /run/harness/command.sh < /run/brief.md" in script
        assert "git config core.excludesFile /dev/null" in script
        for pattern in ("/node_modules/", "/.venv/", "/venv/", "/data/*.db", "/.tmp/"):
            assert pattern in script
        assert "--exclude=./node_modules" in script
        assert "--exclude=./.git" in script
        assert "dist" not in script  # nested build output is never excluded here (the collector decides)
        assert "git tag -f base" in script

    def test_payload_adds_the_scripts(self):
        rendered = RenderedHarness(
            files={BRIEF_PATH: b"x"},
            env={"A": "1"},
            command="exec true",
            stdin_path=BRIEF_PATH,
        )
        payload = sandbox_payload(rendered, wall_clock_s=60)
        assert set(payload.files) == {BRIEF_PATH, COMMAND_SCRIPT, ENTRY_SCRIPT}
        assert payload.entrypoint == ("sh", ENTRY_SCRIPT)
        assert payload.files[COMMAND_SCRIPT].endswith(b"exec true\n")

    def test_runs_and_keeps_nested_build_dirs(self, tmp_path: Path):
        """Run the real script against a temp workspace (paths rewritten): nested data/dist/build survive."""
        ws = tmp_path / "workspace"
        logs = tmp_path / "logs"
        harness_dir = tmp_path / "harness"
        for rel in ("app.py", "data/seed.json", "frontend/dist/index.js", "pkg/build/x.txt", "node_modules/a.js"):
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws / rel).write_text(rel)
        harness_dir.mkdir()
        (harness_dir / "command.sh").write_text(
            'cd "$WS"\necho new > new.txt\nmkdir -p api/data && echo 1 > api/data/x\n',
        )
        script = entry_script(wall_clock_s=30, workspace=str(ws))
        script = script.replace("/logs", str(logs)).replace("/run/harness/command.sh", str(harness_dir / "command.sh"))
        (tmp_path / "entry.sh").write_text(script)
        env = {"HOME": str(tmp_path / "home"), "WS": str(ws), "PATH": "/usr/bin:/bin"}
        subprocess.run(["sh", str(tmp_path / "entry.sh")], check=True, env=env, timeout=60)  # noqa: S603, S607
        exit_record = (logs / "exit.json").read_text()
        assert '"exit_code": 0' in exit_record
        with tarfile.open(logs / "workspace.tar") as tar:
            names = {n.removeprefix("./") for n in tar.getnames()}
        assert {
            "app.py",
            "data/seed.json",
            "frontend/dist/index.js",
            "pkg/build/x.txt",
            "new.txt",
            "api/data/x",
        } <= names
        assert not any(n.startswith(("node_modules", ".git")) for n in names)
        status = (logs / "name_status.txt").read_text()
        assert "new.txt" in status
        assert "api/data/x" in status

    def test_oversized_workspace_becomes_a_manifest(self, tmp_path: Path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "big.bin").write_bytes(b"x" * 50_000)
        logs = tmp_path / "logs"
        harness_dir = tmp_path / "harness"
        harness_dir.mkdir()
        (harness_dir / "command.sh").write_text("true\n")
        script = entry_script(wall_clock_s=30, workspace=str(ws), max_workspace_bytes=10_000)
        script = script.replace("/logs", str(logs)).replace("/run/harness/command.sh", str(harness_dir / "command.sh"))
        (tmp_path / "entry.sh").write_text(script)
        subprocess.run(  # noqa: S603
            ["sh", str(tmp_path / "entry.sh")],  # noqa: S607
            check=True,
            env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
            timeout=60,
        )
        assert not (logs / "workspace.tar").exists()
        assert "big.bin" in (logs / "workspace.manifest").read_text()
        assert '"workspace_too_large": true' in (logs / "exit.json").read_text()
