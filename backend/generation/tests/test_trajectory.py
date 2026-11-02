"""Agent trajectory analytics: rule table, outcomes, record building, ingestion hook, backfill, endpoints."""

from __future__ import annotations

import gzip
import hashlib
import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationJob
from backend.generation.services.agent import trajectory as traj
from backend.generation.services.agent.runner import assess_validity
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ── rule table ──────────────────────────────────────────────────────────


def _shell(command: str, workdir: str | None = None) -> tuple[str, list[str], list[traj.Write]]:
    category, labels, writes, _ = traj.classify_action("bash", {"command": command, "workdir": workdir})
    return category, labels, writes


class TestShellRules:
    def test_piped_install_and_build_is_a_build(self):
        category, labels, writes = _shell("cd /workspace/frontend && npm install && npm run build 2>&1 | tail -20")
        assert category == "build"
        assert {"build", "install"} <= set(labels)
        assert writes == []

    def test_heredoc_body_is_written_not_matched(self):
        command = "cd /workspace && cat > README.md <<'EOF'\nRun `npm run build` then `python app.py`\nEOF"
        category, labels, writes = _shell(command)
        assert category == "write_file"
        assert labels == ["write_file"]
        assert [(w.path, w.kind) for w in writes] == [("README.md", "write")]
        assert writes[0].content == "Run `npm run build` then `python app.py`"

    def test_cat_heredoc_after_cd_resolves_paths(self):
        command = "cd /workspace/frontend\nmkdir -p src\ncat <<'JS' > src/api.js\nexport const x = 1;\nJS\necho ok"
        _, labels, writes = _shell(command)
        assert "write_file" in labels
        assert "explore" not in labels
        assert [w.path for w in writes] == ["frontend/src/api.js"]

    def test_python_read_replace_write_is_an_edit(self):
        command = (
            "python3 - <<'PY'\np = 'src/App.jsx'\ns = open(p).read()\ns = s.replace('a', 'b')\n"
            "open(p, 'w').write(s)\nPY"
        )
        category, _, writes = _shell(command, workdir="/workspace/frontend")
        assert category == "edit"
        assert [(w.path, w.kind) for w in writes] == [("frontend/src/App.jsx", "edit")]

    def test_server_start_and_probe(self):
        command = (
            "pkill -f app.py; nohup python3 app.py > /tmp/server.log 2>&1 & sleep 2; curl -s localhost:8000/api/health"
        )
        category, labels, writes = _shell(command)
        assert category == "run_server"
        assert {"run_server", "probe_http", "process"} <= set(labels)
        assert writes == []  # logs under /tmp are not workspace writes

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("pip install Flask== 2>&1 | grep 'from versions'", "registry_lookup"),
            ("npm view react version", "registry_lookup"),
            ("pip install -r requirements.txt", "install"),
            ("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "submit"),
            ("grep -n route app.py | head", "explore"),
            ("sed -n '1,40p' app.py", "explore"),
            ("pytest -q", "test"),
            ("python3 /tmp/test_api.py 2>&1 | tail -3", "test"),
            ("cd frontend && npx vitest run", "test"),
            ("python3 -c \"import ast; ast.parse(open('app.py').read())\"", "syntax_check"),
            ("python3 -c 'import app'", "syntax_check"),
            ("git status --short", "git"),
            ("ps aux | grep python", "process"),
            ("cd /workspace && rm -rf data", "other"),
            ("npm ls react", "explore"),
            ("node -e \"console.log(require('vite/package.json').version)\"", "explore"),
        ],
    )
    def test_primary_categories(self, command, expected):
        assert _shell(command)[0] == expected

    def test_multiline_python_c_is_one_segment(self):
        command = (
            'cd /workspace && python3 -c "\nimport app\nc = app.app.test_client()\n'
            "print(c.get('/').status_code > 400)\n\""
        )
        category, labels, writes = _shell(command)
        assert category == "probe_http"
        assert writes == []
        assert "write_file" not in labels

    def test_pipes_inside_a_quoted_sed_expression(self):
        category, _, writes = _shell("sed -i 's|a(.x.)|b(\"x\")|g' .tmp/test_api.py && grep -n x .tmp/test_api.py")
        assert category == "edit"
        assert [w.path for w in writes] == [".tmp/test_api.py"]

    def test_mask_quotes_keeps_length_and_syntax(self):
        text = "echo 'a | b' && x \"c > d\" > out.txt"
        masked = traj.mask_quotes(text)
        assert len(masked) == len(text)
        assert "|" not in masked
        assert masked.count(">") == 1

    def test_installed_package_names_are_not_runs(self):
        category, labels, _ = _shell("pip install flask uvicorn httpx ruff && npm i -D vitest webpack")
        assert category == "install"
        assert labels == ["install"]
        assert _shell("npm install && npx vitest run")[0] == "test"

    def test_start_scripts_start_the_server(self):
        assert _shell("rm -f data/app.db && sh /workspace/run_server.sh")[0] == "run_server"

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            # quoted strings are data: killing or grepping for a server is not starting one
            ("pkill -f 'python3 app.py' 2>/dev/null; sleep 1; echo done", "process"),
            ('pgrep -af "python3 app.py" || true', "process"),
            ('grep -n "npm run build" README.md', "explore"),
            ('git commit -m "add pytest suite"', "git"),
            ('ps aux | grep -c "python app.py"', "process"),
            # ...but a command substitution inside double quotes runs
            ('echo "health: $(curl -s http://localhost:8000/api/health)"', "probe_http"),
            # writing or chmod-ing a script is not running it; running it is
            ("cat > /tmp/restart.sh <<'EOF'\npython3 app.py &\nEOF", "other"),
            ("chmod +x /tmp/start.sh", "other"),
            ("nohup /tmp/start.sh > /tmp/start.log 2>&1 &", "run_server"),
            ("cat > /tmp/test_api.sh <<'EOF'\ncurl -s localhost:8000\nEOF\nbash /tmp/test_api.sh", "test"),
            ("cd /workspace && ADMIN_PASSWORD=x bash scripts/smoke_test.sh", "test"),
            # FastAPI's in-process client is an HTTP probe
            ("python3 - <<'EOF'\nfrom fastapi.testclient import TestClient\nc = TestClient(app)\nEOF", "probe_http"),
        ],
    )
    def test_quoted_text_and_script_runs(self, command, expected):
        assert _shell(command)[0] == expected

    def test_writing_a_shell_script_is_a_write_not_a_run(self):
        # "restart.sh <<EOF" ends in "sh": the heredoc is the file's content, not a script run by sh
        category, labels, writes = _shell("cd /workspace && cat > run_server.sh <<'EOF'\npython3 app.py &\nEOF")
        assert category == "write_file"
        assert labels == ["write_file"]
        assert [(w.path, w.kind) for w in writes] == [("run_server.sh", "write")]
        # an interpreter given by path still reads its heredoc
        category, _, writes = _shell(".venv/bin/python - <<'EOF'\nopen('app.py', 'w').write('x')\nEOF")
        assert category == "write_file"
        assert [w.path for w in writes] == ["app.py"]

    def test_python_edit_script_literals_are_not_calls(self):
        command = (
            "cd /workspace/frontend && python3 - <<'PY'\nimport json\np='package.json'\nd=json.load(open(p))\n"
            "d['scripts']['test']='vitest run'\njson.dump(d, open(p,'w'), indent=2)\nPY"
        )
        category, labels, writes = _shell(command)
        assert category == "write_file"
        assert "test" not in labels
        assert [w.path for w in writes] == ["frontend/package.json"]

    def test_node_inline_write_is_a_write(self):
        command = (
            "cd /workspace/frontend && node -e \"const fs=require('fs');"
            "const p=JSON.parse(fs.readFileSync('package.json'));"
            "fs.writeFileSync('package.json', JSON.stringify(p))\""
        )
        category, _, writes = _shell(command)
        assert category == "write_file"
        assert [(w.path, w.kind) for w in writes] == [("frontend/package.json", "write")]
        # a read-only node -e stays a lookup
        assert _shell("node -e \"console.log(require('vite/package.json').version)\"")[0] == "explore"


class TestTestOutcome:
    @pytest.mark.parametrize(
        "output",
        [
            "PASS unknown api JSON 404\n\nTOTAL: 31, FAILED: 0\n",
            "backend up (pid 375)\npassed: 69  failed: 0\nui smoke: passed 24, failed 0\n",
            "PASS spa fallback\nPASS api 404 json\n\nFAILURES: none\n",
            "ALL CHECKS PASSED (0 FAILED)",
            "FAILURES: 0\ncreated: 25 failed: []",
            "5 passed in 0.31s",
        ],
    )
    def test_clean_summaries_are_not_failures(self, output):
        assert traj._tests_outcome(output) is not False

    @pytest.mark.parametrize(
        "output",
        [
            " Test Files  1 failed | 1 passed (2)\n      Tests  1 failed | 2 passed (3)",
            "PASS add todo via UI\nFAIL toggle completed persisted\n1 INTERACTIVE CHECKS FAILED",
            "TOTAL: 31, FAILED: 2",
            " FAIL  src/__tests__/app.test.jsx > admin overview",
            "2437: FAIL  src/__tests__/app.test.jsx > admin overview",
            "== summary ==\n1 FAILURES\n - GET /api/x: expected 404, got 200",
            "IMPORT FAILED ReferenceError: MutationObserver is not defined",
            "FAILED (failures=1)",
            "AssertionError: POST /api/auth/login -> 401 (expected 200)",
        ],
    )
    def test_failure_summaries_are_failures(self, output):
        assert traj._tests_outcome(output) is False
        assert _shell("./restart.sh >/dev/null && curl -s localhost:8000")[0] == "run_server"

    def test_sed_inplace_records_an_edit(self):
        category, _, writes = _shell("sed -i 's/a/b/' app.py")
        assert category == "edit"
        assert [(w.path, w.kind) for w in writes] == [("app.py", "edit")]

    def test_comparisons_inside_quotes_are_not_redirects(self):
        _, labels, writes = _shell('python3 -c "def f() -> int: return 1 if 2 > 1 else 0"')
        assert writes == []
        assert "write_file" not in labels

    def test_redirect_into_workspace_counts_but_logs_do_not(self):
        _, _, writes = _shell("echo 'x' > notes.txt; python3 app.py > flask.log 2>&1 &")
        assert [w.path for w in writes] == ["notes.txt"]

    def test_dependency_dirs_are_not_workspace_files(self):
        assert _shell("echo x > node_modules/.cache/a.js")[2] == []

    def test_native_tools(self):
        write = traj.classify_action("write", {"filePath": "/workspace/app.py", "content": "print(1)\n"})
        assert write[0] == "write_file"
        assert write[2][0].path == "app.py"
        assert write[2][0].content == "print(1)\n"
        assert traj.classify_action("read", {"filePath": "/workspace/app.py"})[0] == "explore"
        assert traj.classify_action("edit", {"filePath": "/workspace/app.py"})[2][0].kind == "edit"
        assert traj.classify_action("todowrite", {"todos": []})[0] == "todo"
        assert traj.classify_action("task", {"prompt": "x"})[0] == "other"

    def test_rule_table_is_published(self):
        table = traj.rule_table()
        assert table["parser_version"] == traj.PARSER_VERSION
        assert set(table["categories"]) == set(traj.CATEGORY_PRIORITY)
        assert {r["label"] for r in table["shell_rules"]} >= {"build", "test", "probe_http", "install"}


# ── synthetic runs ──────────────────────────────────────────────────────


def _tc(call_id: str, command: str | None = None, *, name: str = "bash", args: dict | None = None) -> dict:
    payload = args if args is not None else {"command": command}
    return {"id": call_id, "name": name, "arguments": json.dumps(payload)}


def _call(
    seq: int,
    *,
    tool_calls: list[dict] | None = None,
    messages: list[dict] | None = None,
    ts: float | None = None,
    duration: float = 2.0,
    prompt: int = 1000,
    completion: int = 100,
    cached: int | None = None,
    reasoning: int | None = None,
    usage_extra: dict | None = None,
    system: str = "You are a helpful assistant that can interact with a computer.",
    tools: list[dict] | None = None,
    status: int = 200,
    finish: str = "tool_calls",
) -> dict:
    usage: dict = {"prompt_tokens": prompt, "completion_tokens": completion, "cost": 0.001, **(usage_extra or {})}
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    if reasoning is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return {
        "seq": seq,
        "ts": ts,
        "status": status,
        "request": {
            "model": "m",
            "messages": [{"role": "system", "content": system}, *(messages or [{"role": "user", "content": "task"}])],
            "tools": tools if tools is not None else [{"type": "function", "function": {"name": "bash"}}],
        },
        "response": {
            "id": f"gen-{seq}",
            "provider": "Novita",
            "model": "m",
            "content": "",
            "reasoning": "",
            "tool_calls": tool_calls or [],
            "finish_reason": finish,
            "usage": usage,
            "error": None,
        },
        "duration_s": duration,
        "upstream_retries": 0,
    }


def _mini_result(call_id: str, rc: int, output: str = "") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps({"returncode": rc, "output": output})}


def _mini_run() -> list[dict]:
    """A small mini-swe-agent run: explore, write, piped build that failed, kill, format error, submit."""
    t0 = 1_000_000.0
    return [
        _call(1, tool_calls=[_tc("a", "ls -la")], ts=t0, prompt=2000, cached=0, reasoning=10),
        _call(
            2,
            tool_calls=[_tc("b", "cat > app.py <<'EOF'\nprint('hi')\nEOF")],
            messages=[{"role": "user", "content": "task"}, _mini_result("a", 0, "app.py")],
            ts=t0 + 10,
            prompt=3000,
            cached=2000,
            reasoning=20,
        ),
        _call(
            3,
            tool_calls=[_tc("c", "cd frontend && npm run build 2>&1 | tail -5")],
            messages=[_mini_result("b", 0)],
            ts=t0 + 20,
            prompt=4000,
            cached=3000,
            reasoning=30,
        ),
        _call(
            4,
            tool_calls=[_tc("d", "python3 app.py")],
            messages=[_mini_result("c", 0, "error during build:\nCould not resolve './x'")],
            ts=t0 + 30,
            prompt=5000,
            cached=4000,
            reasoning=40,
        ),
        _call(
            5,
            tool_calls=[],
            messages=[_mini_result("d", -15, "")],
            ts=t0 + 70,
            prompt=6000,
            cached=5000,
            reasoning=50,
            finish="stop",
        ),
        _call(
            6,
            tool_calls=[_tc("e", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")],
            messages=[
                {"role": "user", "content": "Tool call error:\n\n<error>\nNo tool calls found in the response."},
            ],
            ts=t0 + 80,
            prompt=6500,
            cached=6000,
            reasoning=60,
        ),
    ]


class TestRecord:
    def test_mini_run_record(self):
        record = traj.build_record(
            _mini_run(),
            harness="mini-swe-agent",
            harness_version="2.4.6",
            gateway_events=[{"event": "started", "ts": 999_995.0}],
            wall_seconds=100.0,
            harness_report={"steps": 0, "tool_call_total": 0},
            final_files={"app.py": "print('hi')\n", "README.md": "x"},
        )
        assert record["schema_version"] == traj.TRAJECTORY_SCHEMA_VERSION
        assert record["comparable"] is True
        assert record["tool_calls_gateway"] == 5
        assert record["actions_total"] == 4  # the submit command is not work
        assert record["action_categories"] == {"explore": 1, "write_file": 1, "build": 1, "run_server": 1}
        # the piped build exits 0 (tail's code) but its output shows the failure
        assert record["failure_kinds"] == {"output_error": 1, "killed": 1}
        assert record["failed_actions"] == 2
        assert record["killed_actions"] == 1
        assert record["format_errors"] == 1
        assert record["no_tool_responses"] == 1
        verification = record["verification"]
        assert verification["built"] is True
        assert verification["n_builds"] == 1
        assert verification["builds_failed"] == 1
        assert verification["last_build_ok"] is False
        assert verification["first_build_action"] == 3
        assert verification["ran_server"] is True
        assert verification["probed_http"] is False
        assert record["files_written"] == 1
        assert record["writes_after_first_build"] == 0
        assert record["first_pass"]["files_with_first_version"] == 1
        assert record["first_pass"]["unchanged"] == 1
        time = record["time"]
        assert time["llm_s"] == 12.0
        assert time["between_calls_s"] == 70.0  # 6 gaps of 8 s plus the 38 s killed command
        assert time["head_s"] == 5.0
        assert time["tail_s"] == pytest.approx(13.0)
        context = record["context"]
        assert context["series"] == [2000, 3000, 4000, 5000, 6000, 6500]
        assert context["prompt_tokens_first"] == 2000
        assert context["prompt_tokens_max"] == 6500
        assert record["cache_hit_ratio"] == pytest.approx(20000 / 26500, abs=1e-4)
        assert record["reasoning_share"] == pytest.approx(210 / 600, abs=1e-4)
        assert record["harness_counters"]["report_missing"] is True
        assert record["call_roles"] == {"main": 6}
        assert record["list_cost"] is None
        observed = record["observed_inputs"]
        assert observed["tools_offered"] == ["bash"]
        assert observed["system_prompt_chars"] == len("You are a helpful assistant that can interact with a computer.")

    def test_behaviour_summary_matches_contract(self):
        record = traj.build_record(_mini_run(), harness="mini-swe-agent")
        behaviour = traj.behaviour_summary(record)
        assert tuple(behaviour) == traj.BEHAVIOUR_KEYS
        assert behaviour["builds"] == 1
        assert behaviour["first_build_action_index"] == 3
        assert behaviour["comparable"] is True
        assert behaviour["between_calls_seconds"] == 70.0

    def test_unreported_fields_are_none_not_zero(self):
        calls = [_call(1, tool_calls=[_tc("a", "ls")]), _call(2, finish="stop")]
        record = traj.build_record(calls, harness="mini-swe-agent")
        assert record["cache_hit_ratio"] is None
        assert record["reasoning_share"] is None
        assert record["time"]["between_calls_s"] is None  # no timestamps logged
        assert record["verification"]["first_build_action"] is None
        assert record["writes_after_first_build"] is None  # never built
        behaviour = traj.behaviour_summary(record)
        assert behaviour["first_build_action_index"] is None

    def test_unreported_token_counts_are_left_out(self):
        calls = [
            _call(1, tool_calls=[_tc("a", "ls")], prompt=1000, cached=500, reasoning=10, completion=100),
            _call(2, tool_calls=[_tc("b", "ls")], prompt=3000, completion=300),  # no cached/reasoning details
            _call(3, finish="stop", prompt=4000, cached=1000, reasoning=30, completion=100),
        ]
        calls[1]["response"]["usage"].pop("prompt_tokens")  # usage without prompt tokens
        record = traj.build_record(calls, harness="mini-swe-agent")
        # never a 0-token context point
        assert record["context"]["series"] == [1000, 4000]
        assert record["context"]["prompt_tokens_first"] == 1000
        # denominators: only the calls that reported the detail
        assert record["cache_hit_ratio"] == pytest.approx(1500 / 5000)
        assert record["reasoning_share"] == pytest.approx(40 / 200)

    def test_aider_is_not_comparable(self):
        main = "Act as an expert software developer. Take requests for changes to the supplied code."
        summary = "*Briefly* summarize this partial conversation about programming."
        calls = [
            _call(1, system=main, tools=[], finish="stop"),
            _call(2, system=summary, tools=[], finish="stop"),
            _call(3, system=summary, tools=[], finish="stop"),
        ]
        record = traj.build_record(calls, harness="aider")
        assert record["comparable"] is False
        assert "edit format" in record["comparable_reason"]
        assert record["call_roles"] == {"main": 1, "summarizer": 2}
        assert record["model_turns"] == 1
        assert record["actions_total"] is None
        # No native tool calls to count: unmeasured, not a measured 0 (as actions_total).
        assert record["tool_calls_gateway"] is None
        assert record["failed_actions"] is None
        assert record["verification"]["ran_server"] is None
        behaviour = traj.behaviour_summary(record)
        assert behaviour["tool_calls_gateway"] is None
        assert behaviour["actions_total"] is None
        assert behaviour["comparable"] is False
        # A record stored by parser 1.1 kept a 0 for Aider: the summary reads it as unmeasured too.
        old = {**record, "parser_version": "1.1", "tool_calls_gateway": 0}
        assert traj.behaviour_summary(old)["tool_calls_gateway"] is None
        # A tool-calling harness with no tool calls at all is a measured 0.
        idle = traj.build_record([_call(1, finish="stop")], harness="mini-swe-agent")
        assert (idle["tool_calls_gateway"], traj.behaviour_summary(idle)["tool_calls_gateway"]) == (0, 0)

    def test_opencode_events_give_outcomes_and_tool_time(self):
        denial = "The user has specified a rule which prevents you from using this specific tool call. Here are"
        timeout = "(no output)\n\n<shell_metadata>\nshell tool terminated command after exceeding timeout 120000 ms."
        calls = [
            _call(
                1,
                system="You are opencode, an interactive CLI tool",
                tool_calls=[
                    _tc("x1", "mkdir -p /tmp/opencode/x"),
                    _tc("x2", "nohup python3 app.py &"),
                    _tc("x3", name="edit", args={"filePath": "/workspace/app.py", "oldString": "a", "newString": "b"}),
                    _tc("x4", "curl -s localhost:8000 | tail -1"),
                ],
                usage_extra={"opencode": {"list_cost": 0.01, "billing_source": "go"}},
            ),
            _call(2, system="You are opencode", finish="stop", usage_extra={"opencode": {"list_cost": 0.02}}),
        ]
        events = [
            {
                "type": "tool_use",
                "part": {"tool": "bash", "callID": "x1", "state": {"status": "error", "error": denial}},
            },
            {
                "type": "tool_use",
                "part": {
                    "tool": "bash",
                    "callID": "x2",
                    "state": {
                        "status": "completed",
                        "output": timeout,
                        "metadata": {"exit": None},
                        "time": {"start": 1000, "end": 121000},
                    },
                },
            },
            {
                "type": "tool_use",
                "part": {
                    "tool": "edit",
                    "callID": "x3",
                    "state": {"status": "error", "error": "Could not find oldString"},
                },
            },
            {
                "type": "tool_use",
                "part": {
                    "tool": "bash",
                    "callID": "x4",
                    "state": {
                        "status": "completed",
                        "output": "ok",
                        "metadata": {"exit": 0},
                        "time": {"start": 0, "end": 500},
                    },
                },
            },
        ]
        record = traj.build_record(calls, harness="opencode", harness_events=events)
        assert record["source"]["harness_events"] is True
        assert record["policy_denials"] == 1
        assert record["killed_actions"] == 1
        assert record["edit_no_match"] == 1
        # killed + edit no-match; the policy denial is the sandbox's rule, counted apart.
        assert record["failed_actions"] == 2
        assert record["failure_kinds"]["policy_denial"] == 1
        assert record["outcomes"]["ok"] == 1
        assert record["time"]["tool_exec_s"] == 120.5
        assert record["list_cost"] == {"list_cost": 0.03, "billing_source": "go", "calls_priced": 2}

    def test_template_hash_ignores_model_and_date_lines(self):
        def run(model, date):
            prompt = (
                f"You are opencode.\nYou are powered by the model named {model}. X\n"
                f"<env>\n  Today's date: {date}\n</env>"
            )
            return traj.build_record([_call(1, system=prompt, finish="stop")], harness="opencode")["observed_inputs"]

        a, b = run("a/b", "Mon Nov 02 2026"), run("c/d", "Thu Oct 22 2026")
        assert a["system_prompt_sha256"] != b["system_prompt_sha256"]
        assert a["system_prompt_template_sha256"] == b["system_prompt_template_sha256"]

    def test_steps_and_detail(self):
        calls = _mini_run()
        parsed = traj.parse_run(calls)
        rows, truncated = traj.step_rows(parsed, limit=4)
        assert truncated is True
        assert len(rows) == 4
        assert rows[2]["tool_calls"][0]["category"] == "build"
        assert rows[2]["tool_calls"][0]["failed"] is True
        assert rows[1]["offset_s"] == 10.0
        raw = "\n".join(json.dumps(c) for c in calls).encode()
        detail = traj.step_detail(raw, 4)
        assert detail["response"]["tool_calls"][0]["outcome"] == "killed"
        assert detail["response"]["tool_calls"][0]["result_source"] == "gateway"
        assert detail["request"]["new_messages"][-1]["role"] == "tool"
        assert detail["request"]["system_prompt"].startswith("You are a helpful assistant")
        assert traj.step_detail(raw, 99) is None


# ── ingestion hook, backfill, endpoints ─────────────────────────────────


def _agent_job(tmp_path, *, user=None, model=None, harness="mini-swe-agent", calls=None, with_record=True):
    runs = tmp_path / "runs"
    job = GenerationJob.objects.create(
        mode=GenerationJob.Mode.AGENT,
        status=GenerationJob.Status.COMPLETED,
        created_by=user or UserFactory(),
        model=model or LLMModelFactory(),
    )
    record: dict = {}
    if with_record:
        root = runs / str(job.id)
        root.mkdir(parents=True)
        data = gzip.compress("\n".join(json.dumps(c) for c in (calls or _mini_run())).encode())
        (root / "llm_calls.jsonl.gz").write_bytes(data)
        record = {"llm_calls.jsonl.gz": {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}}
        record["_dir"] = str(root)
    job.result_data = {
        "files": {"app.py": "print('hi')\n"},
        "agent": {
            "harness": harness,
            "harness_version": "2.4.6",
            "wall_seconds": 100.0,
            "harness_report": {"steps": 0, "tool_call_total": 0, "tool_calls": {}},
            "run_record": record,
            "validity": {"valid": True, "notes": []},
        },
    }
    job.metrics = {
        "tool_calls": 0,
        "harness_steps": 0,
        "cost": 0.5,
        "functional": {"source": "conformance", "status": "passed", "pass_rate": 0.9},
    }
    job.save()
    return job


@pytest.fixture
def runs_root(settings, tmp_path):
    settings.GENERATION_RUNS_ROOT = str(tmp_path / "runs")
    return tmp_path


class TestIngestion:
    def test_attach_writes_record_and_keeps_self_reports(self, runs_root):
        job = _agent_job(runs_root)
        record = traj.attach_trajectory(job, _mini_run(), gateway_events=[])
        assert record is not None
        assert job.result_data["agent"]["trajectory"]["actions_total"] == 4
        assert job.metrics["behaviour"]["tool_calls_gateway"] == 5
        assert job.metrics["tool_calls"] == 0
        assert job.metrics["harness_steps"] == 0  # never overwritten
        assert job.metrics["cost"] == 0.5
        assert "list_cost" not in job.metrics

    def test_analytics_errors_never_raise(self, runs_root, monkeypatch):
        job = _agent_job(runs_root)

        def boom(*args, **kwargs):
            msg = "parser exploded"
            raise RuntimeError(msg)

        monkeypatch.setattr(traj, "build_record", boom)
        assert traj.attach_trajectory(job, _mini_run()) is None
        assert job.result_data["agent"]["trajectory_error"] == "RuntimeError: parser exploded"
        assert "behaviour" not in job.metrics

    def test_policy_denials_become_an_info_note(self):
        verdict = assess_validity(
            {"gateway": {"dropped_params": {}}, "llm": {"model_slug": "m"}},
            {"agent": {"trajectory": {"policy_denials": 3}}, "evaluation": {"status": "passed"}},
        )
        notes = {n["code"]: n["severity"] for n in verdict["notes"]}
        assert notes["harness_policy_denials"] == "info"
        assert verdict["valid"] is True


class TestBackfill:
    def test_dry_run_writes_nothing(self, runs_root):
        job = _agent_job(runs_root)
        out = StringIO()
        call_command("backfill_agent_trajectories", "--job", str(job.id), "--dry-run", stdout=out)
        job.refresh_from_db()
        assert "trajectory" not in job.result_data["agent"]
        assert "would_update 1" in out.getvalue()

    def test_backfill_is_additive_and_idempotent(self, runs_root):
        job = _agent_job(runs_root)
        call_command("backfill_agent_trajectories", "--job", str(job.id)[:8], stdout=StringIO())
        job.refresh_from_db()
        assert job.result_data["agent"]["trajectory"]["parser_version"] == traj.PARSER_VERSION
        assert job.metrics["behaviour"]["actions_total"] == 4
        assert job.metrics["functional"] == {"source": "conformance", "status": "passed", "pass_rate": 0.9}
        assert job.result_data["agent"]["harness_report"]["steps"] == 0
        out = StringIO()
        call_command("backfill_agent_trajectories", "--job", str(job.id), stdout=out)
        assert "current 1" in out.getvalue()

    def test_missing_or_changed_record_is_reported(self, runs_root):
        missing = _agent_job(runs_root, with_record=False)
        changed = _agent_job(runs_root)
        path = Path(changed.result_data["agent"]["run_record"]["_dir"]) / "llm_calls.jsonl.gz"
        path.write_bytes(gzip.compress(b"{}"))
        out = StringIO()
        call_command("backfill_agent_trajectories", "--job", str(missing.id), "--job", str(changed.id), stdout=out)
        assert "unavailable 2" in out.getvalue()
        assert "sha256" in out.getvalue()


class TestEndpoints:
    @pytest.fixture
    def client_user(self, client):
        user = UserFactory()
        client.force_login(user)
        return client, user

    def test_trajectory_and_step(self, client_user, runs_root):
        client, user = client_user
        job = _agent_job(runs_root, user=user)
        res = client.get(f"/api/generation/jobs/{job.id}/trajectory/")
        assert res.status_code == 200
        body = res.json()
        assert body["source"] == "llm_calls"
        assert body["record_source"] == "computed"  # nothing stored yet: computed, not saved
        assert len(body["steps"]) == 6
        assert body["truncated"] is False
        assert body["record"]["actions_total"] == 4
        job.refresh_from_db()
        assert "trajectory" not in job.result_data["agent"]
        step = client.get(f"/api/generation/jobs/{job.id}/trajectory/steps/3/")
        assert step.status_code == 200
        assert step.json()["response"]["tool_calls"][0]["category"] == "build"
        assert client.get(f"/api/generation/jobs/{job.id}/trajectory/steps/42/").status_code == 404

    def test_other_users_job_is_404(self, client_user, runs_root):
        client, _ = client_user
        job = _agent_job(runs_root)
        assert client.get(f"/api/generation/jobs/{job.id}/trajectory/").status_code == 404
        assert client.get(f"/api/generation/jobs/{job.id}/trajectory/steps/1/").status_code == 404

    def test_artifact_fallback_without_run_record(self, client_user, runs_root):
        client, user = client_user
        job = _agent_job(runs_root, user=user, with_record=False)
        GenerationArtifact.objects.create(
            job=job,
            stage="agent_call_0001",
            request_payload={"model": "m", "last_message": "{}", "message_count": 2},
            response_payload={
                "status": 200,
                "duration_s": 1.0,
                "tool_calls": [{"id": "a", "name": "bash", "arguments": '{"command": "npm run build"}'}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )
        body = client.get(f"/api/generation/jobs/{job.id}/trajectory/").json()
        assert body["source"] == "artifacts"
        assert body["steps"][0]["tool_calls"][0]["category"] == "build"
        step = client.get(f"/api/generation/jobs/{job.id}/trajectory/steps/1/").json()
        assert step["source"] == "artifact"

    def test_model_behaviour_aggregates(self, client_user, runs_root):
        client, user = client_user
        model = LLMModelFactory()
        for _ in range(2):
            job = _agent_job(runs_root, user=user, model=model)
            traj.attach_trajectory(job, _mini_run())
            job.save()
        invalid = _agent_job(runs_root, user=user, model=model)
        invalid.result_data["agent"]["validity"] = {"valid": False, "notes": []}
        invalid.save()
        _agent_job(runs_root, user=user, model=model, harness="aider")  # no record
        _agent_job(runs_root, model=model)  # someone else's job: not pooled
        res = client.get(f"/api/generation/models/{model.pk}/behaviour/")
        assert res.status_code == 200
        groups = {h["harness"]: h for h in res.json()["harnesses"]}
        mini = groups["mini-swe-agent"]
        assert mini["n_jobs"] == 3
        assert mini["n_invalid_excluded"] == 1
        assert mini["n"] == 2
        assert mini["metrics"]["actions_total"] == {"n": 2, "mean": 4.0, "median": 4.0, "min": 4.0, "max": 4.0}
        assert mini["rates"]["ran_server"] == {"n": 2, "k": 2, "share": 1.0}
        assert mini["pass_rate"]["n"] == 2
        assert groups["aider"]["n_without_record"] == 1
        assert groups["aider"]["n"] == 0
        assert client.get("/api/generation/models/999999/behaviour/").status_code == 404

    def test_model_behaviour_pass_rate_is_conformance_only_and_budgets_are_labelled(self, client_user, runs_root):
        client, user = client_user
        model = LLMModelFactory()
        functional = {
            "graded": {"source": "conformance", "status": "passed", "pass_rate": 0.8},
            "smoke": {"passed": True, "pass_rate": 1.0, "endpoints_ok": 2, "endpoints_checked": 2},
            "infra": {"source": "conformance", "status": "infra_error", "pass_rate": 0.0},
        }
        for budget, kind in ((30, "graded"), (100, "smoke"), (100, "infra")):
            job = _agent_job(runs_root, user=user, model=model)
            job.result_data["agent"]["budget"] = {"max_llm_requests": budget}
            traj.attach_trajectory(job, _mini_run())
            job.metrics["functional"] = functional[kind]
            job.save()
        body = client.get(f"/api/generation/models/{model.pk}/behaviour/").json()
        mini = body["harnesses"][0]
        # the smoke check's 1.0 and the ungraded infra_error 0.0 are not conformance pass rates
        assert mini["pass_rate"] == {"n": 1, "mean": 0.8, "median": 0.8, "min": 0.8, "max": 0.8}
        assert mini["max_llm_requests"] == [30, 100]
        assert any("different LLM-request budgets" in note for note in body["notes"])
