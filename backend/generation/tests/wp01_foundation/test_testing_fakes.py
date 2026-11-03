"""WP-01 testing infrastructure: the fake gateway, harness, sandbox and evaluator behave as specified.

Until WP-02a/WP-02b land, run with ``--ds=backend.generation.tests.wp01_foundation.settings_u1``.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from backend.generation.contracts.harness import ExitInfo
from backend.generation.contracts.harness import Trajectory
from backend.generation.contracts.outcome import Termination
from backend.generation.models import ImmutableVersionError
from backend.generation.models import RunEvaluation
from backend.generation.models import choices
from backend.generation.testing import factories
from backend.generation.testing.fake_eval import FakeEvaluator
from backend.generation.testing.fake_eval import canned_eval_result
from backend.generation.testing.fake_gateway import BUDGET_MESSAGE
from backend.generation.testing.fake_gateway import STATE_KEYS
from backend.generation.testing.fake_gateway import FakeGateway
from backend.generation.testing.fake_gateway import ScriptedReply
from backend.generation.testing.fake_gateway import ScriptedStateFile
from backend.generation.testing.fake_gateway import canned_call
from backend.generation.testing.fake_gateway import estimate_tokens
from backend.generation.testing.fake_gateway import gateway_config
from backend.generation.testing.fake_gateway import gateway_state
from backend.generation.testing.fake_harness import TRAJECTORY_FILE
from backend.generation.testing.fake_harness import FakeHarness
from backend.generation.testing.fake_harness import FakeScript
from backend.generation.testing.fake_harness import harness_context
from backend.generation.testing.fake_sandbox import FakeSandbox
from backend.generation.testing.fake_sandbox import tar_bytes


def post(url: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(  # noqa: S310 - a local test server
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def chat(gw: FakeGateway, text: str = "hello", headers: dict | None = None, **extra) -> tuple[int, dict]:
    body = {"model": "deepseek-v4.1-flash", "messages": [{"role": "user", "content": text}], **extra}
    return post(f"{gw.base_url}/chat/completions", body, headers)


# =====================================================================================
# Fake gateway
# =====================================================================================


def test_gateway_answers_and_logs_a_call(tmp_path):
    config = gateway_config(sampling={"forced": {"temperature": 0.2}, "stripped": ["top_p"], "seed": 7})
    with FakeGateway(config, replies=[ScriptedReply(content="hi", completion_tokens=40)], logs_dir=tmp_path) as gw:
        assert gw.state()["inflight"] == 0
        status, body = chat(gw, top_p=0.5, max_completion_tokens=99_999, system_fingerprint="x")
        assert status == 200
        assert body["choices"][0]["message"]["content"] == "hi"
        assert body["usage"]["completion_tokens"] == 40
        upstream = gw.upstream_calls[0].body
        assert upstream["model"] == "deepseek/deepseek-v4.1-flash"  # pinned
        assert upstream["temperature"] == 0.2
        assert "top_p" not in upstream
        assert "max_completion_tokens" not in upstream
        assert upstream["max_tokens"] == 1024
        assert upstream["seed"] == 7
        assert upstream["session_id"] == config.session_id
        assert upstream["usage"] == {"include": True}
        [record] = gw.calls()
        state = gw.state()
    assert set(record) == set(canned_call())
    assert record["requested_model"] == "deepseek-v4.1-flash"
    assert record["request_class"] == "main"
    assert set(state) == set(STATE_KEYS)
    assert state["requests_by_class"]["main"] == 1
    assert state["completion_tokens"] == 40
    assert state["prompt_tokens"] == estimate_tokens("hello")
    assert state["cost"] == pytest.approx((estimate_tokens("hello") * 1.0 + 40 * 2.0) / 1e6)
    assert state["last_call_end"] is not None
    assert (tmp_path / "calls.jsonl").exists()  # an explicit logs_dir is kept


def test_gateway_records_the_first_main_system_prompt_only():
    with FakeGateway() as gw:
        for _ in range(2):
            post(
                f"{gw.base_url}/chat/completions",
                {"messages": [{"role": "system", "content": "You are opencode."}, {"role": "user", "content": "x"}]},
            )
        prompts = [call["native_system_prompt"] for call in gw.calls()]
    assert prompts == ["You are opencode.", None]


def test_gateway_main_request_cap_exhausts_the_run_and_stays_exhausted():
    with FakeGateway(gateway_config(budget={"max_llm_requests": 2})) as gw:
        assert [chat(gw)[0] for _ in range(2)] == [200, 200]
        status, body = chat(gw)
        assert status == 400
        assert body["error"]["message"] == BUDGET_MESSAGE
        assert gw.state()["budget_exhausted"] == "budget_exhausted:requests"
        assert chat(gw)[0] == 400  # sticky
        assert [e["reason"] for e in gw.events() if e["event"] == "budget_exhausted"] == ["budget_exhausted:requests"]
        assert len(gw.calls()) == 2  # refused calls never reach the upstream


def test_gateway_request_classes_count_against_their_own_caps():
    config = gateway_config(request_class_source="x-claude-code-request-class", budget={"max_aux_requests": 1})
    header = "x-claude-code-request-class"
    with FakeGateway(config) as gw:
        assert chat(gw, headers={header: "compaction"})[0] == 200
        assert chat(gw, headers={header: "main"})[0] == 200
        status, _ = chat(gw, headers={header: "subagent"})
        state = gw.state()
        classes = [(c["request_class"], c["request_class_header"]) for c in gw.calls()]
    assert status == 400
    assert state["budget_exhausted"] == "budget_exhausted:aux_requests"
    assert state["requests_by_class"] == {"main": 1, "subagent": 0, "compaction": 1, "auxiliary": 0}
    assert classes == [("compaction", "compaction"), ("main", "main")]


def test_gateway_unknown_request_class_is_auxiliary():
    config = gateway_config(request_class_source="x-request-class")
    with FakeGateway(config) as gw:
        chat(gw, headers={"x-request-class": "title-generation"})
        assert gw.calls()[0]["request_class"] == "auxiliary"


def test_gateway_pre_call_reservation_counts_inflight_worst_cases():
    # Worst case per call: ~2 prompt tokens x 1 $/Mtok + 1024 output tokens x 2 $/Mtok ~= 0.00205 USD.
    hold = threading.Event()
    config = gateway_config(budget={"max_cost_usd": 0.003})
    replies = [ScriptedReply(hold=hold, completion_tokens=10)]
    results: list[int] = []
    with FakeGateway(config, replies=replies) as gw:
        first = threading.Thread(target=lambda: results.append(chat(gw)[0]))
        first.start()
        deadline = time.time() + 5
        while gw.state()["inflight"] != 1 and time.time() < deadline:
            time.sleep(0.01)
        assert gw.state()["inflight"] == 1
        status, body = chat(gw)  # would overshoot if the in-flight call used its whole cap
        hold.set()
        first.join(timeout=10)
        state = gw.state()
        upstream = [call.body["max_tokens"] for call in gw.upstream_calls]
    assert status == 400
    assert body["error"]["message"] == BUDGET_MESSAGE
    assert results == [200]
    assert state["budget_exhausted"] == "budget_exhausted:cost"
    assert state["inflight"] == 0
    assert upstream == [1024]  # max_tokens is never clamped to make a call fit


def test_gateway_billed_cost_and_list_cost():
    replies = [ScriptedReply(prompt_tokens=1000, completion_tokens=1000, cost=0.5)]
    with FakeGateway(gateway_config(budget={"max_cost_usd": 1.0}), replies=replies) as gw:
        chat(gw)
        state = gw.state()
    assert state["cost"] == 0.5
    assert state["list_cost"] == pytest.approx(0.003)


def test_gateway_token_caps():
    config = gateway_config(budget={"max_total_tokens": 1100}, call={"max_output_tokens": 1000})
    with FakeGateway(config, default_reply=ScriptedReply(prompt_tokens=2, completion_tokens=400)) as gw:
        assert chat(gw)[0] == 200  # 0 used + (2 + 1000) reserved <= 1100
        assert chat(gw)[0] == 400  # 402 used + (2 + 1000) reserved > 1100
        state = gw.state()
    assert state["budget_exhausted"] == "budget_exhausted:tokens"
    assert state["requests_by_class"]["main"] == 1


def test_gateway_uncached_input_cap():
    config = gateway_config(budget={"max_uncached_input_tokens": 1000})
    with FakeGateway(config, default_reply=ScriptedReply(prompt_tokens=1300, cached_tokens=200)) as gw:
        assert chat(gw)[0] == 200  # 0 used + 2 estimated <= 1000
        assert chat(gw)[0] == 400  # (1300 - 200) used + 2 estimated > 1000 (cached tokens do not count)
        state = gw.state()
    assert state["budget_exhausted"] == "budget_exhausted:uncached_input"


def test_gateway_output_token_cap_reserves_max_tokens():
    config = gateway_config(budget={"max_output_tokens": 1500}, call={"max_output_tokens": 1000})
    with FakeGateway(config, default_reply=ScriptedReply(completion_tokens=600)) as gw:
        assert chat(gw)[0] == 200  # 0 used + 1000 reserved <= 1500
        assert chat(gw)[0] == 400  # 600 used + 1000 reserved > 1500
        assert gw.state()["budget_exhausted"] == "budget_exhausted:output_tokens"


def test_gateway_reasoning_and_flex_policy():
    config = gateway_config(
        reasoning={"mode": "max_tokens", "effort": None, "max_tokens": 2000, "exclude": True},
        routing={"serving_tier": "flex", "provider": {"sort": "price"}},
        model={"max_output": 2500},
    )
    with FakeGateway(config) as gw:
        chat(gw)
        body = gw.upstream_calls[0].body
    assert body["max_tokens"] == 2500  # 1024 + 2000 clamped to the model limit
    assert body["reasoning"] == {"max_tokens": 2000, "exclude": True}
    assert body["model"] == "deepseek/deepseek-v4.1-flash:floor"
    assert body["provider"] == {"sort": "price"}


def test_gateway_context_budget_refuses_without_exhausting():
    with FakeGateway(gateway_config(call={"max_input_tokens": 10})) as gw:
        status, body = chat(gw, text="x" * 100)
        assert status == 400
        assert body["error"]["code"] == "context_budget"
        assert gw.state()["budget_exhausted"] is None
        assert chat(gw, text="short")[0] == 200
        assert [e["event"] for e in gw.events()] == ["context_budget"]


def test_gateway_truncations_and_upstream_failures():
    replies = [
        ScriptedReply(finish_reason="length"),
        ScriptedReply(status=429, error="rate limited"),
        ScriptedReply(transport_error=True),
    ]
    with FakeGateway(replies=replies) as gw:
        statuses = [chat(gw)[0] for _ in range(4)]
        calls = gw.calls()
        state = gw.state()
    assert statuses == [200, 429, 502, 200]
    assert state["call_truncations"] == 1
    assert state["requests_by_class"]["main"] == 2  # failed calls do not count
    assert [c["transport_error"] for c in calls] == [False, False, True, False]
    assert calls[1]["usage"] is None


def test_gateway_streams_chat_completions():
    with FakeGateway(replies=[ScriptedReply(content="streamed")]) as gw:
        request = urllib.request.Request(  # noqa: S310 - a local test server
            f"{gw.base_url}/chat/completions",
            data=json.dumps({"messages": [{"role": "user", "content": "x"}], "stream": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            lines = [line for line in response.read().decode().splitlines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line.removeprefix("data: ")) for line in lines[:-1]]
    assert chunks[0]["choices"][0]["delta"]["content"] == "streamed"
    assert chunks[-1]["usage"]["completion_tokens"] == 16


def test_gateway_anthropic_front():
    config = gateway_config(front_protocol="anthropic_messages", protocol_mode="translate")
    with FakeGateway(config, replies=[ScriptedReply(content="ok", finish_reason="length")]) as gw:
        assert gw.base_url == gw.origin
        body = {"model": "claude", "system": "be brief", "messages": [{"role": "user", "content": "hi"}]}
        status, reply = post(f"{gw.origin}/v1/messages?beta=true", body)
        assert (status, reply["stop_reason"], reply["content"][0]["text"]) == (200, "max_tokens", "ok")
        status, tokens = post(f"{gw.origin}/v1/messages/count_tokens", body)
        assert status == 200
        assert tokens["input_tokens"] == estimate_tokens("be brief\nhi")
        assert len(gw.calls()) == 1  # count_tokens is answered locally, not a call
        assert gw.upstream_calls[0].body["messages"][0] == {"role": "system", "content": "be brief"}
        status, error = post(f"{gw.origin}/v1/chat/completions", body)
        assert status == 404
        assert error["type"] == "error"


def test_gateway_routes():
    with FakeGateway() as gw:
        with urllib.request.urlopen(f"{gw.origin}/v1/models", timeout=5) as response:  # noqa: S310
            assert [m["id"] for m in json.load(response)["data"]] == ["deepseek-v4.1-flash"]
        head = urllib.request.Request(f"{gw.origin}/api/hello", method="HEAD")  # noqa: S310
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(head, timeout=5)  # noqa: S310
        assert exc.value.code == 404
        assert post(f"{gw.origin}/v1/responses", {})[0] == 404


def test_scripted_state_file(tmp_path):
    frames: list[dict[str, Any]] = [{"inflight": 1}, {"inflight": 0, "last_call_end": 5.0, "cost": 0.1}]
    script = ScriptedStateFile(tmp_path / "state.json", frames)
    assert script.advance()["inflight"] == 1
    assert json.loads((tmp_path / "state.json").read_text())["inflight"] == 1
    assert script.advance()["cost"] == 0.1
    assert script.advance()["cost"] == 0.1  # stays on the last frame
    assert set(gateway_state()) == set(STATE_KEYS)
    with pytest.raises(KeyError):
        gateway_state(spent=1)


# =====================================================================================
# Fake harness
# =====================================================================================


def test_harness_renders_the_brief_into_a_file_never_argv():
    ctx = harness_context(brief="SECRET BRIEF TEXT")
    rendered = FakeHarness(FakeScript(files={"app.py": "print(1)\n"})).render(ctx)
    assert rendered.files[ctx.brief_path] == b"SECRET BRIEF TEXT"
    assert "SECRET" not in rendered.command
    assert rendered.env["OPENAI_BASE_URL"] == "http://gateway:8080/v1"
    assert FakeHarness.facts.enabled
    assert FakeHarness().image_spec("1").build_args == {"FAKE_HARNESS_VERSION": "1"}


def test_harness_rejects_unsafe_fixture_paths():
    with pytest.raises(ValueError, match="segment"):
        FakeScript(files={"../escape.py": b""}).workspace_files()


def test_harness_reads_a_fixture_dir(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("A")
    script = FakeScript(fixture_dir=tmp_path, files={"pkg/a.py": "override", "b.txt": b"B"})
    assert script.workspace_files() == {"pkg/a.py": b"override", "b.txt": b"B"}


def test_harness_observed_inputs_and_trajectory_from_calls():
    calls = [
        canned_call(1, system_prompt="You are X.", tools=[{"type": "function", "function": {"name": "bash"}}]),
        canned_call(2, request_class="compaction"),
    ]
    harness = FakeHarness()
    observed = harness.observed_inputs(calls)
    assert observed.calls_by_class == {"main": 1, "compaction": 1}
    assert observed.tool_names == ["bash"]
    assert observed.system_prompt_sha is not None
    assert observed.first_user_message_sha is not None
    trajectory = harness.parse_trajectory({}, calls)
    assert [s.kind for s in trajectory.steps] == ["llm", "llm"]
    assert trajectory.parser == "fake"


def _exit(**overrides) -> ExitInfo:
    values: dict[str, Any] = {
        "exit_code": 0,
        "elapsed_s": 10.0,
        "wall_clock_s": 600,
        "idle_fired": False,
        "oom_killed": False,
        "budget_exhausted": None,
        "cancelled": False,
        "gateway_calls_ok": 3,
        "gateway_calls_failed_transport": 0,
    }
    values.update(overrides)
    return ExitInfo(**values)


@pytest.mark.parametrize(
    ("exit_overrides", "trajectory_overrides", "expected"),
    [
        ({}, {}, Termination.COMPLETED),
        ({"cancelled": True, "oom_killed": True}, {}, Termination.CANCELLED),
        ({"oom_killed": True, "exit_code": 137}, {}, Termination.OOM),
        ({"elapsed_s": 598.5, "exit_code": 0}, {}, Termination.WALL_CLOCK),
        ({"exit_code": 137, "elapsed_s": 30.0}, {}, Termination.HARNESS_CRASH),  # never wall clock by exit code
        ({"idle_fired": True}, {}, Termination.IDLE),
        ({"budget_exhausted": "budget_exhausted:cost", "exit_code": 1}, {}, Termination.BUDGET),
        ({"gateway_calls_ok": 0, "gateway_calls_failed_transport": 2, "exit_code": 1}, {}, Termination.INFRA),
        ({"exit_code": 1}, {"ended_on_llm_error": True}, Termination.LLM_ERROR),
        ({}, {"max_turns_hit": True}, Termination.MAX_TURNS),
        ({"exit_code": 2}, {}, Termination.HARNESS_ERROR),
    ],
)
def test_harness_classifies_termination(exit_overrides, trajectory_overrides, expected):
    trajectory = Trajectory(parser="fake", parser_version=1, **trajectory_overrides)
    assert FakeHarness().classify_termination(_exit(**exit_overrides), trajectory) == expected


def test_harness_normalize_options_drops_unset_values():
    assert FakeHarness().normalize_options({"a": 1, "b": None}) == {"a": 1}


# =====================================================================================
# Fake sandbox
# =====================================================================================


def test_sandbox_runs_the_entry_script_and_collects_the_workspace():
    script = FakeScript(
        files={
            "app.py": "print('model code')\n",
            "data/notes.txt": "nested data dirs are kept\n",
            "node_modules/pkg/index.js": "excluded at the root\n",
            "web/node_modules/kept.js": "nested node_modules are not excluded\n",
        },
        delete=["README.md"],
        stdout="harness says hi",
    )
    harness = FakeHarness(script)
    seed = {"README.md": "# starter\n", "Dockerfile": "FROM python:3.13-slim\n"}
    with FakeSandbox() as sandbox:
        run = sandbox.run(harness.render(harness_context()), seed=seed)
        files = run.workspace_files()
        patch = run.changes_patch.decode()
        outputs = run.outputs()
        calls = run.calls()
        stdout = run.stdout
        exit_info = run.exit_info
    assert exit_info.exit_code == 0
    assert exit_info.gateway_calls_ok == 1  # the canned calls.jsonl
    assert set(files) == {"Dockerfile", "app.py", "data/notes.txt", "web/node_modules/kept.js"}
    assert "+print('model code')" in patch
    assert "deleted file mode" in patch
    assert "node_modules/pkg/index.js" not in patch  # /node_modules/ is in .git/info/exclude
    assert "web/node_modules/kept.js" in patch
    assert stdout.strip() == "harness says hi"
    assert TRAJECTORY_FILE in outputs
    trajectory = harness.parse_trajectory(outputs, calls)
    assert trajectory.edits == 5
    assert trajectory.final_message == "Done."
    assert harness.classify_termination(exit_info, trajectory) == Termination.COMPLETED


def test_sandbox_with_gateway_reaches_budget_exhaustion():
    config = gateway_config(budget={"max_llm_requests": 2})
    script = FakeScript(call_gateway=True, requests=[{} for _ in range(5)], files={"main.py": "x = 1\n"})
    harness = FakeHarness(script)
    with FakeGateway(config) as gw, FakeSandbox(gateway=gw) as sandbox:
        run = sandbox.run(harness.render(harness_context(system_prompt="Custom system.")))
        outputs = run.outputs()
        calls = run.calls()
        upstream = gw.upstream_calls
    assert len(calls) == 2
    assert run.exit_info.budget_exhausted == "budget_exhausted:requests"
    assert run.exit_info.gateway_calls_ok == 2
    trajectory = harness.parse_trajectory(outputs, calls)
    assert [s.ok for s in trajectory.steps if s.kind == "llm"] == [True, True, False]
    assert harness.classify_termination(run.exit_info, trajectory) == Termination.BUDGET
    assert upstream[0].body["messages"][0] == {"role": "system", "content": "Custom system."}
    assert harness.observed_inputs(calls).system_prompt_sha is not None


def test_sandbox_unreachable_gateway_is_infra():
    harness = FakeHarness(FakeScript(call_gateway=True, requests=[{}], exit_code=1))
    with FakeGateway(replies=[ScriptedReply(transport_error=True)]) as gw, FakeSandbox(gateway=gw) as sandbox:
        run = sandbox.run(harness.render(harness_context()))
        trajectory = harness.parse_trajectory(run.outputs(), run.calls())
    assert run.exit_info.gateway_calls_failed_transport == 1
    assert harness.classify_termination(run.exit_info, trajectory) == Termination.INFRA


def test_sandbox_wall_clock_is_measured_not_inferred():
    harness = FakeHarness(FakeScript(sleep_s=30))
    with FakeSandbox() as sandbox:
        run = sandbox.run(harness.render(harness_context()), wall_clock_s=4)
    assert run.exit_info.exit_code == 124  # timeout(1)
    assert 3.5 <= run.exit_info.elapsed_s < 20
    trajectory = harness.parse_trajectory(run.outputs(), run.calls())
    assert harness.classify_termination(run.exit_info, trajectory) == Termination.WALL_CLOCK


def test_sandbox_cancellation_kills_the_run():
    cancel = threading.Event()
    harness = FakeHarness(FakeScript(sleep_s=30))
    timer = threading.Timer(1.0, cancel.set)
    timer.start()
    with FakeSandbox() as sandbox:
        run = sandbox.run(harness.render(harness_context()), cancel=cancel)
    timer.cancel()
    assert run.exit_info.cancelled is True
    assert run.exit_info.exit_code is None
    assert run.exit_info.elapsed_s < 10
    trajectory = Trajectory(parser="fake", parser_version=1)
    assert harness.classify_termination(run.exit_info, trajectory) == Termination.CANCELLED


def test_sandbox_truncates_a_workspace_over_the_limit_and_reports_errors():
    harness = FakeHarness(FakeScript(files={"big.bin": b"x" * 50_000}, exit_code=3))
    with FakeSandbox(max_workspace_bytes=10_000) as sandbox:
        run = sandbox.run(harness.render(harness_context()))
        manifest = (run.logs / "workspace.manifest").read_text()
        files = run.workspace_files()
    assert run.workspace_truncated is True
    assert files == {}
    assert "./big.bin" in manifest
    assert run.exit_info.exit_code == 3
    trajectory = harness.parse_trajectory(run.outputs(), run.calls())
    assert harness.classify_termination(run.exit_info, trajectory) == Termination.HARNESS_ERROR


def test_tar_bytes_round_trip(tmp_path):
    (tmp_path / "w.tar").write_bytes(tar_bytes({"a/b.txt": b"1"}))
    import tarfile

    with tarfile.open(tmp_path / "w.tar") as tar:
        assert tar.getnames() == ["./a/b.txt"]


# =====================================================================================
# Fake evaluator
# =====================================================================================


@pytest.mark.django_db
def test_fake_evaluator_writes_frozen_rows():
    run = factories.GenerationRunFactory.create(tested=True)
    other = factories.GenerationRunFactory.create(tested=True)
    suite = factories.EvalSuiteFactory.create()
    evaluator = FakeEvaluator(
        {str(run.pk): canned_eval_result(passed=1, failed=3), other.tree_hash: canned_eval_result(measured=False)},
    )
    evaluation = evaluator.evaluate(run, suite, only_checks=["check.0"])
    evaluation.full_clean()
    assert evaluation.status == choices.EvaluationStatus.MEASURED
    assert evaluation.result is not None
    assert evaluation.result["metrics"]["agent_pass_rate"] == 0.25
    missing = evaluator.evaluate(other, suite)
    assert missing.status == choices.EvaluationStatus.NOT_MEASURED
    assert missing.result is not None
    assert missing.adherence is not None
    assert missing.result["metrics"]["agent_pass_rate"] is None  # never a pass rate of 0
    assert missing.adherence["status"] == "not_measured"
    assert [c.only_checks for c in evaluator.calls] == [["check.0"], None]
    evaluation.status = choices.EvaluationStatus.ERROR
    with pytest.raises(ImmutableVersionError):
        evaluation.save()
    again = evaluator.evaluate(run, suite)  # append-only: a new row
    assert RunEvaluation.objects.filter(run=run).count() == 2
    assert again.pk != evaluation.pk


@pytest.mark.django_db
def test_fake_evaluator_default_callable_and_install(monkeypatch):
    from backend.generation.evaluation import suite as suite_module

    run = factories.GenerationRunFactory.create(tested=True)
    suite = factories.EvalSuiteFactory.create()
    evaluator = FakeEvaluator(lambda r: canned_eval_result(passed=2, failed=0)).install(monkeypatch)
    evaluation = suite_module.evaluate(run, suite, mode="overlay")
    assert evaluation.mode == "overlay"
    assert evaluation.result is not None
    assert evaluation.result["metrics"]["agent_pass_rate"] == 1.0
    assert len(evaluator.calls) == 1
    assert FakeEvaluator().result_for(run)["metrics"]["agent_pass_rate"] == 0.75


def test_canned_eval_result_rejects_impossible_probe_counts():
    with pytest.raises(ValueError, match="probes_exploited"):
        canned_eval_result(probes_tested=1, probes_exploited=2)
