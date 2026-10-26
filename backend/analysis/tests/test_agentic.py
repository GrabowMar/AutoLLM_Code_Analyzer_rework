"""Agent engine end to end on a fake apps daemon: outcomes, repairs, budgets, usage, freezing."""

from __future__ import annotations

import hashlib
import json

import pytest

from backend.analysis.engine import sandbox as analysis_sandbox
from backend.analysis.engine.reviewer import agentic
from backend.analysis.engine.reviewer import client as client_mod
from backend.analysis.engine.reviewer import prompt
from backend.analysis.engine.reviewer.agentic_harness import FINDINGS_PATH
from backend.analysis.engine.reviewer.agentic_sandbox import LABEL_REVIEW
from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleFile
from backend.analysis.tests.agentic_fakes import Behaviour
from backend.analysis.tests.agentic_fakes import FakeClient
from backend.analysis.tests.agentic_fakes import untar

RUN_ID = "7d1c2f3e-aaaa-4bbb-8ccc-0123456789ab"
APP = (
    "import sqlite3\n"
    "\n"
    "SECRET_KEY = 'dev-secret-123'\n"
    "\n"
    "def find(db, name):\n"
    '    return db.execute("SELECT * FROM users WHERE name = \'" + name + "\'")\n'
)
API = "export async function load() {\n  return fetch('/api/todos').then((r) => r.json());\n}\n"
MODEL = {
    "slug": "deepseek/deepseek-v4.1-flash",
    "id": 71,
    "canonical_slug": "deepseek_deepseek-v4.1-flash-20260910",
    "provider": "deepseek",
    "context_window": 1_048_576,
    "max_output_tokens": 943_718,
    "pricing": {"input_per_token": 4.9e-08, "output_per_token": 5.25e-07, "snapshot_at": "2026-10-22T00:00:00Z"},
    "supports": {
        "structured_outputs": True,
        "response_format": True,
        "tools": True,
        "seed": True,
        "temperature": True,
        "reasoning": True,
        "reasoning_efforts": ["max", "high", "low", "none"],
    },
}
ROUTING = {"require_parameters": True, "quantizations": ["fp8", "fp16", "bf16", "fp32", "unknown"]}


def _file(path: str, text: str, *, origin: str = "added", kind: str = "source", language: str = "python"):
    data = text.encode()
    return SampleFile(
        path=path,
        content=data,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        language=language,
        kind=kind,
        origin=origin,
        sloc=text.count("\n"),
        measured=origin != "scaffold",
        measured_sloc=text.count("\n") if origin != "scaffold" else 0,
    )


def make_sample(*extra: SampleFile) -> Sample:
    files = [
        _file("app.py", APP, origin="modified"),
        _file("frontend/src/api.js", API, language="javascript"),
        _file("frontend/src/main.jsx", "import App from './App';\n", origin="scaffold", language="jsx"),
        *extra,
    ]
    return Sample(files=files, source="agent", sample_hash="ab" * 32)


def make_frozen(harness: str = "opencode", **overrides) -> dict:
    frozen = {
        "enabled": True,
        "required": True,
        "tool_slug": "ai_review",
        "engine": "agent",
        "harness": harness,
        "harness_options": {},
        "model": MODEL,
        "params": {
            "temperature": 0.2,
            "max_tokens": 16000,
            "reasoning": {"effort": "low"},
            "seed_base": 1,
            "seed_forwarded": True,
            "dropped": {},
        },
        "provider": ROUTING,
        "output_mode": "json_schema",
        "samples": 1,
        "prompt_version": prompt.PROMPT_VERSION,
        "prompt_sha256": prompt.prompt_sha256(),
        "schema_version": prompt.SCHEMA_VERSION,
        "schema_sha256": prompt.schema_sha256(),
        "include_origins": ["authored", "added", "modified"],
        "include_kinds": ["source", "config", "manifest", "env"],
        "budget": {"max_cost_usd": 0.5, "max_llm_requests": 40, "wall_clock_seconds": 900},
    }
    frozen.update(overrides)
    section = agentic.freeze_harness(frozen["harness"], frozen["harness_options"], model=MODEL)
    frozen["agent"] = section
    frozen["harness_version"] = section["version"]
    frozen["harness_images"] = section["images"]
    frozen["harness_options"] = section["options"]
    return frozen


def finding(**overrides) -> dict:
    item = {
        "file": "app.py",
        "start_line": 6,
        "end_line": 6,
        "evidence": '    return db.execute("SELECT * FROM users WHERE name = \'" + name + "\'")',
        "title": "SQL injection",
        "rationale": "User input is concatenated into SQL.",
        "category": "security",
        "cwe": "CWE-89",
        "severity": "high",
        "confidence": 0.9,
        "fix": "Use a parameterized query.",
    }
    item.update(overrides)
    return item


def document(*items: dict, complete: bool = True) -> dict:
    return {"findings": list(items), "files_reviewed": ["app.py", "frontend/src/api.js"], "review_complete": complete}


def call(*, content="Done.", finish="stop", status=200, cost=0.001, provider="InferenceNet", error=None, usage=True):
    response = {
        "id": "gen-1",
        "model": "deepseek/deepseek-v4.1-flash",
        "provider": provider,
        "content": content,
        "finish_reason": finish,
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200, "cost": cost}
        if usage
        else None,
        "error": error,
    }
    return {"seq": 1, "status": status, "response": response, "duration_s": 2.0, "upstream_retries": 0}


def gateway_logs(*calls, events=()) -> dict[str, bytes]:
    return {
        "calls.jsonl": "".join(json.dumps(c) + "\n" for c in calls).encode(),
        "gateway_events.jsonl": "".join(json.dumps(e) + "\n" for e in [{"event": "started"}, *events]).encode(),
    }


def outputs(exit_code=0, **files) -> dict[str, bytes]:
    out = {"exit.json": json.dumps({"exit_code": exit_code, "agent_seconds": 5}).encode()}
    for name, value in files.items():
        out[name.replace("__", ".")] = value if isinstance(value, bytes) else str(value).encode()
    return out


@pytest.fixture(autouse=True)
def capacity(monkeypatch):
    """Admission always succeeds unless a test says otherwise; records the requested memory."""
    requests: list[int] = []

    def wait_for_capacity(mem_bytes, *, is_cancelled, heartbeat, timeout_s):
        requests.append(mem_bytes)
        return True

    monkeypatch.setattr(analysis_sandbox, "wait_for_capacity", wait_for_capacity)
    monkeypatch.setattr(agentic.time, "sleep", lambda _s: None)
    return requests


def review(behaviour: Behaviour, *, frozen=None, sample=None, cancel=lambda: False):
    client = FakeClient(behaviour)
    outcome = agentic.run_sample(
        frozen=frozen or make_frozen(),
        sample=sample or make_sample(),
        index=0,
        api_key="sk-or-key",
        run_id=RUN_ID,
        is_cancelled=cancel,
        heartbeat=lambda: None,
        docker_client=client,
    )
    return outcome, client


def _assert_clean(client: FakeClient) -> None:
    assert client.container_list == []
    assert client.network_list == []


# --------------------------------------------------------------------------
# Successful reviews
# --------------------------------------------------------------------------


def test_opencode_review_succeeds_from_the_findings_file(capacity):
    doc = document(finding())
    behaviour = Behaviour(
        out_files=outputs(findings__json=json.dumps(doc)),
        gateway_logs=gateway_logs(call(finish="tool_calls"), call(content="Wrote the findings.")),
    )
    outcome, client = review(behaviour)

    assert outcome.status == "succeeded", outcome.error_message
    assert outcome.engine == "agent"
    assert outcome.harness == "opencode"
    assert outcome.harness_version == "1.18.35"
    assert outcome.model_canonical_slug == MODEL["canonical_slug"]
    assert outcome.prompt_version == "agent-brief-v1"
    assert len(outcome.prompt_sha256) == 64
    assert outcome.schema_sha256 == prompt.schema_sha256()
    assert outcome.repaired is False
    assert len(outcome.findings) == 1
    found = outcome.findings[0]
    assert (found.file_path, found.line, found.grounding, found.is_ai, found.counted) == (
        "app.py",
        6,
        "exact",
        True,
        False,
    )
    assert found.sample_index == 0
    assert outcome.grounding["exact"] == 1
    assert outcome.files_reviewed == ["app.py", "frontend/src/api.js"]
    # Usage from every gateway call; providers and finish reasons of successful ones.
    assert outcome.usage["llm_calls"] == 2
    assert outcome.usage["cost_usd"] == pytest.approx(0.002)
    assert outcome.usage["prompt_tokens"] == 2000
    assert outcome.providers == {"InferenceNet": 2}
    assert outcome.finish_reasons == {"tool_calls": 1, "stop": 1}
    # Coverage: the files under review (scaffold excluded) all reached the agent.
    assert outcome.coverage["files_sent"] == ["app.py", "frontend/src/api.js"]
    assert outcome.coverage["files_in_scope"] == 2
    assert outcome.coverage["out_of_scope"] == {"origin:scaffold": 1}
    assert outcome.coverage["review_complete"] is True
    assert outcome.coverage["agent"]["payload_source"] == "file"
    assert outcome.coverage["agent"]["termination"] == "completed"
    assert outcome.file_order_seed is not None
    # Transcript and provenance artifacts.
    for name in ("brief.md", "harness_section.json", "gateway_config.json", "calls.jsonl", "harness-findings.json"):
        assert name in outcome.artifacts
    # The workspace holds every included file (context too); the brief lists only the files under review.
    workspace = untar(client.harness.archives[0][1])
    assert {"app.py", "frontend/src/api.js", "frontend/src/main.jsx"} <= set(workspace)
    brief = outcome.artifacts["brief.md"].decode()
    assert "- `app.py`" in brief
    assert "- `frontend/src/main.jsx`" not in brief
    # The gateway forces the reviewer's frozen params and the replicate's seed.
    config = json.loads(outcome.artifacts["gateway_config.json"])
    assert config["forced_params"]["temperature"] == 0.2
    assert config["forced_params"]["reasoning"] == {"effort": "low"}
    assert isinstance(config["forced_params"]["seed"], int)
    assert config["forced_params"]["provider"] == ROUTING
    assert config["budget"] == {"max_requests": 40, "max_cost_usd": 0.5}
    assert "temperature" not in config["stripped_params"]
    assert "top_p" in config["stripped_params"]
    # Capacity was requested for the 1g harness + 256m gateway.
    assert capacity == [1024**3 + 256 * 1024**2]
    _assert_clean(client)


def test_aider_review_takes_the_json_from_the_final_answer():
    answer = f"Here is my review.\n```json\n{json.dumps(document(finding()))}\n```"
    behaviour = Behaviour(out_files=outputs(), gateway_logs=gateway_logs(call(content=answer)))
    outcome, client = review(behaviour, frozen=make_frozen("aider"))

    assert outcome.status == "succeeded", outcome.error_message
    assert outcome.repaired is False
    assert outcome.coverage["agent"]["payload_source"] == "answer"
    assert outcome.coverage["agent"]["files_opened_method"] == "all_files_in_context"
    assert len(outcome.findings) == 1
    command = untar(client.harness.archives[1][1])["harness.sh"].decode()
    assert "--chat-mode ask" in command
    assert "--read app.py" in command
    _assert_clean(client)


def test_replicates_differ_in_seed_and_presentation_but_reruns_repeat():
    frozen = make_frozen()
    sample = make_sample()
    first = agentic._prepare(frozen=frozen, sample=sample, index=0, run_id=RUN_ID)
    again = agentic._prepare(frozen=frozen, sample=sample, index=0, run_id=RUN_ID)
    second = agentic._prepare(frozen=frozen, sample=sample, index=1, run_id=RUN_ID)
    assert first.seed == again.seed
    assert first.brief == again.brief
    assert first.seed != second.seed
    assert first.gateway_params["forced"]["seed"] != second.gateway_params["forced"]["seed"]


def test_seed_is_not_forwarded_when_the_model_does_not_honour_one():
    frozen = make_frozen(params={"temperature": None, "max_tokens": 8000, "reasoning": None, "seed_base": 1})
    prepared = agentic._prepare(frozen=frozen, sample=make_sample(), index=0, run_id=RUN_ID)
    forced = prepared.gateway_params["forced"]
    assert set(forced) == {"provider", "max_tokens"}
    assert forced["max_tokens"] == 8000
    assert {"seed", "temperature", "reasoning"} <= set(prepared.gateway_params["stripped"])


# --------------------------------------------------------------------------
# Repairs
# --------------------------------------------------------------------------


def test_missing_file_but_json_in_the_final_answer_is_a_repaired_sample():
    answer = json.dumps(document(finding()))
    behaviour = Behaviour(out_files=outputs(), gateway_logs=gateway_logs(call(content=answer)))
    outcome, _ = review(behaviour, frozen=make_frozen("mini-swe-agent"))
    assert outcome.status == "succeeded", outcome.error_message
    assert outcome.repaired is True
    assert outcome.coverage["agent"]["repair"] == "final_answer_json"


def _fake_chat(monkeypatch, result: client_mod.LLMCall) -> list[dict]:
    seen: list[dict] = []

    def chat(api_key, **kwargs):
        seen.append({"api_key": api_key, **kwargs})
        return result

    monkeypatch.setattr(client_mod, "chat", chat)
    return seen


def test_invalid_file_is_repaired_by_the_finalizer(monkeypatch):
    repaired = json.dumps(document(finding()))
    seen = _fake_chat(
        monkeypatch,
        client_mod.LLMCall(
            ok=True,
            content=repaired,
            finish_reason="stop",
            provider="Fireworks",
            model="deepseek/deepseek-v4.1-flash",
            usage={"prompt_tokens": 3000, "completion_tokens": 400, "cost": 0.0005},
            attempts=[{"attempt": 0}],
        ),
    )
    behaviour = Behaviour(
        out_files=outputs(findings__json="{not json"),
        gateway_logs=gateway_logs(call(content="I found a SQL injection in app.py line 6.")),
    )
    outcome, _ = review(behaviour)

    assert outcome.status == "succeeded", outcome.error_message
    assert outcome.repaired is True
    assert outcome.coverage["agent"]["repair"] == "finalizer"
    assert outcome.coverage["agent"]["finalizer"]["ok"] is True
    assert outcome.usage["llm_calls"] == 2
    assert outcome.usage["finalizer_calls"] == 1
    assert outcome.usage["cost_usd"] == pytest.approx(0.0015)
    assert outcome.providers == {"InferenceNet": 1, "Fireworks": 1}
    assert "finalizer.json" in outcome.artifacts
    request = seen[0]
    assert request["api_key"] == "sk-or-key"
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["provider"] == ROUTING
    assert request["params"]["temperature"] == 0.2
    assert request["params"]["max_tokens"] == 16000
    user = request["messages"][1]["content"]
    assert "SQL injection in app.py line 6" in user
    assert "{not json" in user


def test_finalizer_failure_is_invalid_output(monkeypatch):
    _fake_chat(monkeypatch, client_mod.LLMCall(ok=True, content='{"findings": "nope"}', finish_reason="stop"))
    behaviour = Behaviour(out_files=outputs(), gateway_logs=gateway_logs(call(content="Some prose review.")))
    outcome, _ = review(behaviour)
    assert outcome.status == "invalid_output"
    assert outcome.failure_reason == "invalid_output"
    assert outcome.findings == []
    assert "did not write the findings file" in outcome.error_message


def test_truncated_finalizer_answer_is_truncated(monkeypatch):
    _fake_chat(monkeypatch, client_mod.LLMCall(ok=False, content="", finish_reason="length", error="empty"))
    behaviour = Behaviour(out_files=outputs(), gateway_logs=gateway_logs(call(content="prose")))
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("truncated", "truncated")


def test_finalizer_can_be_switched_off(monkeypatch):
    seen = _fake_chat(monkeypatch, client_mod.LLMCall(ok=True, content="{}"))
    frozen = make_frozen(harness_options={"finalizer": False})
    behaviour = Behaviour(out_files=outputs(), gateway_logs=gateway_logs(call(content="prose")))
    outcome, _ = review(behaviour, frozen=frozen)
    assert outcome.status == "invalid_output"
    assert seen == []


def test_finalizer_respects_the_remaining_budget(monkeypatch):
    seen = _fake_chat(monkeypatch, client_mod.LLMCall(ok=True, content="{}"))
    frozen = make_frozen(budget={"max_cost_usd": 0.002, "max_llm_requests": 40, "wall_clock_seconds": 900})
    behaviour = Behaviour(out_files=outputs(), gateway_logs=gateway_logs(call(content="prose", cost=0.002)))
    outcome, _ = review(behaviour, frozen=frozen)
    assert outcome.status == "invalid_output"
    assert outcome.coverage["agent"]["finalizer"] == {"ok": False, "skipped": "no budget left"}
    assert seen == []


def test_truncated_final_answer_without_a_document_is_truncated(monkeypatch):
    seen = _fake_chat(monkeypatch, client_mod.LLMCall(ok=True, content="{}"))
    behaviour = Behaviour(
        out_files=outputs(),
        gateway_logs=gateway_logs(call(content='{"findings": [', finish="length")),
    )
    outcome, _ = review(behaviour, frozen=make_frozen("aider"))
    assert (outcome.status, outcome.failure_reason) == ("truncated", "truncated")
    assert seen == []


# --------------------------------------------------------------------------
# Budget, timeout, harness failures
# --------------------------------------------------------------------------


def test_budget_exhaustion_without_a_document_is_budget_exhausted(monkeypatch):
    seen = _fake_chat(monkeypatch, client_mod.LLMCall(ok=True, content="{}"))
    events = [{"event": "budget_exhausted", "reason": "max_requests"}]
    behaviour = Behaviour(out_files=outputs(), gateway_logs=gateway_logs(call(finish="tool_calls"), events=events))
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("budget_exhausted", "budget")
    assert "max_requests" in outcome.error_message
    assert outcome.usage["llm_calls"] == 1
    assert seen == []  # never repair a review the budget cut short


def test_budget_exhaustion_keeps_a_document_only_when_the_review_was_complete():
    events = [{"event": "budget_exhausted", "reason": "max_cost_usd"}]
    complete = Behaviour(
        out_files=outputs(findings__json=json.dumps(document(finding()))),
        gateway_logs=gateway_logs(call(), events=events),
    )
    outcome, _ = review(complete)
    assert outcome.status == "succeeded"

    partial = Behaviour(
        out_files=outputs(findings__json=json.dumps(document(finding(), complete=False))),
        gateway_logs=gateway_logs(call(), events=events),
    )
    outcome, _ = review(partial)
    assert outcome.status == "budget_exhausted"
    assert outcome.findings == []


def test_timeout_is_timed_out():
    behaviour = Behaviour(out_files=outputs(124), gateway_logs=gateway_logs(call(finish="tool_calls")))
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("timed_out", "timeout")
    assert outcome.coverage["agent"]["termination"] == "timeout"


def test_harness_crash_fails_with_the_log_tail():
    log = b"Traceback (most recent call last):\nValueError: provider exploded\n"
    behaviour = Behaviour(out_files=outputs(1, harness__log=log), gateway_logs=gateway_logs(call(finish="tool_calls")))
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("failed", "harness_error")
    assert "provider exploded" in outcome.error_message
    assert "exit code 1" in outcome.error_message


def test_oom_kill_fails_with_oom():
    behaviour = Behaviour(
        out_files=outputs(137),
        gateway_logs=gateway_logs(call(finish="tool_calls")),
        exit_code=137,
        oom_killed=True,
    )
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("failed", "oom")


def test_harness_that_never_called_the_model_fails_with_its_log():
    behaviour = Behaviour(out_files=outputs(1, harness__log=b"opencode: config error"), gateway_logs=gateway_logs())
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("failed", "harness_error")
    assert "made no LLM call" in outcome.error_message
    assert "config error" in outcome.error_message
    assert outcome.usage["cost_usd"] == 0.0


def test_missing_gateway_log_is_an_infra_failure_with_unknown_spend():
    behaviour = Behaviour(out_files=outputs(1, harness__log=b"boom"), gateway_logs={})
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("failed", "infra_error")
    assert "call log could not be collected" in outcome.error_message
    assert outcome.usage["cost_usd"] is None
    assert outcome.usage["llm_calls"] is None
    assert outcome.usage["gateway_log_missing"] is True


def test_rejected_key_fails_with_a_fatal_reason():
    rejected = call(status=401, error={"message": "User not found."}, usage=False)
    behaviour = Behaviour(out_files=outputs(1), gateway_logs=gateway_logs(rejected, rejected))
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("failed", "auth")
    assert "User not found" in outcome.error_message
    assert outcome.usage["llm_calls_failed"] == 2
    assert outcome.usage["cost_usd"] is None  # spend of the failed calls is unknown, not zero


def test_valid_file_after_a_harness_error_counts_only_if_complete():
    behaviour = Behaviour(
        out_files=outputs(1, findings__json=json.dumps(document(finding()))),
        gateway_logs=gateway_logs(call()),
    )
    outcome, _ = review(behaviour)
    assert outcome.status == "succeeded"
    assert outcome.coverage["agent"]["termination"] == "harness_error"


def _mini_traj(exit_status: str, submission: str = "") -> str:
    return json.dumps(
        {
            "info": {"exit_status": exit_status, "submission": submission, "model_stats": {"api_calls": 40}},
            "messages": [
                {
                    "role": "assistant",
                    "content": "Reading the app.",
                    "tool_calls": [
                        {"function": {"name": "bash", "arguments": json.dumps({"command": "nl -ba app.py"})}},
                    ],
                },
            ],
        },
    )


def test_mini_step_limit_is_a_budget_stop_not_a_natural_end(monkeypatch):
    # Regression: mini raises LimitsExceeded before request max_llm_requests + 1 and exits 0, so the
    # gateway never logs budget_exhausted; a draft document written mid-review was accepted.
    seen = _fake_chat(monkeypatch, client_mod.LLMCall(ok=True, content="{}"))
    calls = [call(finish="tool_calls") for _ in range(40)]
    draft = Behaviour(
        out_files=outputs(
            findings__json=json.dumps(document(finding(), complete=False)),
            traj__json=_mini_traj("LimitsExceeded"),
        ),
        gateway_logs=gateway_logs(*calls),
    )
    outcome, _ = review(draft, frozen=make_frozen("mini-swe-agent"))
    assert (outcome.status, outcome.failure_reason) == ("budget_exhausted", "budget")
    assert "LimitsExceeded" in outcome.error_message
    assert outcome.findings == []
    assert outcome.coverage["agent"]["harness_stop"] == {"kind": "budget", "detail": "LimitsExceeded"}

    # Without any document: the same budget stop, never a finalizer repair.
    nothing = Behaviour(out_files=outputs(traj__json=_mini_traj("LimitsExceeded")), gateway_logs=gateway_logs(*calls))
    outcome, _ = review(nothing, frozen=make_frozen("mini-swe-agent"))
    assert (outcome.status, outcome.failure_reason) == ("budget_exhausted", "budget")
    assert seen == []

    # A review the agent declared complete before the limit still counts (as for the gateway's budget).
    complete = Behaviour(
        out_files=outputs(findings__json=json.dumps(document(finding())), traj__json=_mini_traj("LimitsExceeded")),
        gateway_logs=gateway_logs(*calls),
    )
    outcome, _ = review(complete, frozen=make_frozen("mini-swe-agent"))
    assert outcome.status == "succeeded", outcome.error_message


def test_mini_submitted_is_a_natural_end_and_other_exits_are_harness_errors(monkeypatch):
    seen = _fake_chat(monkeypatch, client_mod.LLMCall(ok=True, content="{}"))
    doc = json.dumps(document(finding()))
    submitted = Behaviour(
        out_files=outputs(findings__json=doc, traj__json=_mini_traj("Submitted", doc)),
        gateway_logs=gateway_logs(call()),
    )
    outcome, _ = review(submitted, frozen=make_frozen("mini-swe-agent"))
    assert outcome.status == "succeeded", outcome.error_message
    assert outcome.coverage["agent"]["harness_stop"] is None

    broken = Behaviour(
        out_files=outputs(traj__json=_mini_traj("RepeatedFormatError")),
        gateway_logs=gateway_logs(call(content="I will now read the file")),
    )
    outcome, _ = review(broken, frozen=make_frozen("mini-swe-agent"))
    assert (outcome.status, outcome.failure_reason) == ("failed", "harness_error")
    assert "RepeatedFormatError" in outcome.error_message
    assert seen == []


def test_absolute_workspace_paths_are_cited_files():
    # Regression: "/workspace/app.py" (how the agent's tools print paths) graded invalid_file.
    doc = {
        "findings": [finding(file="/workspace/app.py")],
        "files_reviewed": ["/workspace/app.py", "/workspace/frontend/src/api.js"],
        "review_complete": True,
    }
    behaviour = Behaviour(out_files=outputs(findings__json=json.dumps(doc)), gateway_logs=gateway_logs(call()))
    outcome, _ = review(behaviour)
    assert outcome.status == "succeeded", outcome.error_message
    assert outcome.grounding["exact"] == 1
    assert outcome.grounding["invalid_file"] == 0
    assert outcome.findings[0].file_path == "app.py"
    assert outcome.files_reviewed == ["app.py", "frontend/src/api.js"]
    assert outcome.coverage["files_claimed_not_shown"] == []


def test_an_exploring_agent_that_declares_its_review_incomplete_is_not_a_vote():
    # Regression: {findings: [], files_reviewed: [], review_complete: false} after an `ls` was a
    # successful replicate voting "nothing found".
    lazy = {"findings": [], "files_reviewed": [], "review_complete": False}
    behaviour = Behaviour(out_files=outputs(findings__json=json.dumps(lazy)), gateway_logs=gateway_logs(call()))
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("invalid_output", "incomplete_review")
    assert "review_complete=false" in outcome.error_message
    assert outcome.findings == []
    assert outcome.coverage["review_complete"] is False
    assert outcome.coverage["agent"]["payload_source"] == "file"

    partial = document(finding(), complete=False)
    behaviour = Behaviour(out_files=outputs(findings__json=json.dumps(partial)), gateway_logs=gateway_logs(call()))
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("invalid_output", "incomplete_review")
    assert outcome.coverage["discarded_findings"] == 1


def test_a_document_that_reviewed_no_file_under_review_is_not_a_vote():
    empty = {"findings": [], "files_reviewed": ["README.md"], "review_complete": True}
    behaviour = Behaviour(out_files=outputs(findings__json=json.dumps(empty)), gateway_logs=gateway_logs(call()))
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("invalid_output", "incomplete_review")
    # Observed reads are evidence enough when the document lists nothing (OpenCode read tool calls).
    read = json.dumps(
        {"type": "tool_use", "part": {"tool": "read", "state": {"input": {"filePath": "/workspace/app.py"}}}},
    )
    behaviour = Behaviour(
        out_files=outputs(findings__json=json.dumps({**empty, "files_reviewed": []}), events__jsonl=read),
        gateway_logs=gateway_logs(call()),
    )
    outcome, _ = review(behaviour)
    assert outcome.status == "succeeded", outcome.error_message
    assert outcome.coverage["agent"]["files_opened_in_scope"] == 1


def test_aider_sees_every_file_so_a_self_declared_incomplete_review_is_recorded():
    # Like single-shot: every file under review was in the model's context.
    answer = json.dumps(document(finding(), complete=False))
    behaviour = Behaviour(out_files=outputs(), gateway_logs=gateway_logs(call(content=answer)))
    outcome, _ = review(behaviour, frozen=make_frozen("aider"))
    assert outcome.status == "succeeded", outcome.error_message
    assert outcome.coverage["review_complete"] is False


def test_a_finalizer_document_must_show_a_complete_review(monkeypatch):
    # "I could not read the files" repaired into {findings: [], review_complete: false} is no review.
    _fake_chat(
        monkeypatch,
        client_mod.LLMCall(
            ok=True,
            content=json.dumps({"findings": [], "files_reviewed": [], "review_complete": False}),
            finish_reason="stop",
            usage={"prompt_tokens": 300, "completion_tokens": 20, "cost": 0.0001},
            attempts=[{"attempt": 0}],
        ),
    )
    behaviour = Behaviour(out_files=outputs(), gateway_logs=gateway_logs(call(content="I could not read the files.")))
    outcome, _ = review(behaviour)
    assert (outcome.status, outcome.failure_reason) == ("invalid_output", "incomplete_review")
    assert outcome.repaired is True
    assert outcome.usage["finalizer_calls"] == 1


def test_an_abandoned_finalizer_call_leaves_the_spend_a_lower_bound(monkeypatch):
    monkeypatch.setattr(agentic, "_call_waiting", lambda *a, **k: "timeout")
    behaviour = Behaviour(out_files=outputs(), gateway_logs=gateway_logs(call(content="prose")))
    outcome, _ = review(behaviour)
    assert outcome.status == "invalid_output"
    assert outcome.usage["calls_abandoned"] == 1
    assert outcome.usage["cost_complete"] is False
    assert outcome.usage["cost_usd"] == pytest.approx(0.001)


def test_a_sample_that_fails_before_its_sandbox_spent_nothing():
    frozen = make_frozen(budget={"max_cost_usd": 0, "max_llm_requests": 40, "wall_clock_seconds": 900})
    outcome, _ = review(Behaviour(), frozen=frozen)
    assert outcome.failure_reason == "invalid_config"
    assert outcome.usage["cost_usd"] == 0.0
    assert outcome.usage["llm_calls"] == 0


# --------------------------------------------------------------------------
# Cancellation, infrastructure, configuration
# --------------------------------------------------------------------------


def test_cancel_during_the_run_is_cancelled_and_keeps_the_spend():
    state = {"n": 0}

    def cancel() -> bool:
        state["n"] += 1
        return state["n"] > 3

    behaviour = Behaviour(never_exits=True, gateway_logs=gateway_logs(call(finish="tool_calls")))
    outcome, client = review(behaviour, cancel=cancel)
    assert (outcome.status, outcome.failure_reason) == ("cancelled", "cancelled")
    assert outcome.usage["llm_calls"] == 1
    assert "calls.jsonl" in outcome.artifacts
    assert client.harness.killed
    _assert_clean(client)


def test_infra_failure_before_start_is_retried_once_then_reported():
    outcome, client = review(Behaviour(fail_on="put_archive"))
    assert (outcome.status, outcome.failure_reason) == ("failed", "infra_error")
    assert "first attempt" in outcome.error_message
    assert len([c for c in client.created if c.is_gateway]) == 2  # two sandboxes, both torn down
    assert outcome.usage["cost_usd"] == 0.0
    _assert_clean(client)


def test_no_capacity_is_an_infra_failure_and_cancel_while_waiting_is_a_cancel(monkeypatch):
    monkeypatch.setattr(analysis_sandbox, "wait_for_capacity", lambda *a, **k: False)
    outcome, client = review(Behaviour())
    assert (outcome.status, outcome.failure_reason) == ("failed", "infra_error")
    assert client.created == []
    outcome, _ = review(Behaviour(), cancel=lambda: True)
    assert (outcome.status, outcome.failure_reason) == ("cancelled", "cancelled")


def test_unreachable_daemon_during_admission_is_an_infra_failure(monkeypatch):
    def broken(*args, **kwargs):
        msg = "daemon down"
        raise analysis_sandbox.SandboxError(msg)

    monkeypatch.setattr(analysis_sandbox, "wait_for_capacity", broken)
    outcome, _ = review(Behaviour())
    assert (outcome.status, outcome.failure_reason) == ("failed", "infra_error")
    assert "daemon down" in outcome.error_message


def test_changed_harness_definition_refuses_to_run_before_any_container():
    frozen = make_frozen()
    frozen["harness_images"] = {**frozen["harness_images"], "harness": "llmlab/agent-opencode:000000000000"}
    outcome, client = review(Behaviour(), frozen=frozen)
    assert (outcome.status, outcome.failure_reason) == ("failed", "invalid_config")
    assert "re-freeze" in outcome.error_message
    assert client.created == []

    frozen = make_frozen()
    frozen["agent"] = {**frozen["agent"], "review_revision": 0}
    outcome, _ = review(Behaviour(), frozen=frozen)
    assert outcome.failure_reason == "invalid_config"
    assert "review revision" in outcome.error_message


def test_invalid_frozen_budget_is_invalid_config():
    frozen = make_frozen(budget={"max_cost_usd": 0, "max_llm_requests": 40, "wall_clock_seconds": 900})
    outcome, _ = review(Behaviour(), frozen=frozen)
    assert outcome.failure_reason == "invalid_config"
    assert "max_cost_usd" in outcome.error_message


def test_nothing_reviewable_is_no_files():
    sample = Sample(
        files=[_file("app.py", "   \n", origin="added"), _file("main.jsx", "x\n", origin="scaffold")],
        source="agent",
        sample_hash="cd" * 32,
    )
    outcome, client = review(Behaviour(), sample=sample)
    assert (outcome.status, outcome.failure_reason) == ("failed", "no_files")
    assert outcome.coverage["files_not_sent"] == [{"path": "app.py", "reason": "empty"}]
    assert client.created == []


def test_out_of_scope_citations_grade_as_invalid_file():
    doc = document(finding(), finding(file="frontend/src/main.jsx", start_line=1, end_line=1, evidence="import App"))
    behaviour = Behaviour(out_files=outputs(findings__json=json.dumps(doc)), gateway_logs=gateway_logs(call()))
    outcome, _ = review(behaviour)
    assert outcome.status == "succeeded"
    assert outcome.grounding["exact"] == 1
    assert outcome.grounding["invalid_file"] == 1


# --------------------------------------------------------------------------
# Freezing and options
# --------------------------------------------------------------------------


def test_freeze_harness_records_identity_and_hashes_the_section_when_given_the_inputs():
    frozen = agentic.freeze_harness("mini-swe-agent", {"command_timeout_seconds": 30}, model=MODEL)
    assert frozen["harness"] == "mini-swe-agent"
    assert frozen["version"] == "2.4.6"
    assert frozen["options"] == {}  # defaults dropped: equal configs hash equal
    assert frozen["effective_options"] == {"command_timeout_seconds": 30, "finalizer": True}
    assert set(frozen["images"]) == {"base", "harness", "gateway"}
    assert frozen["brief"]["version"] == "agent-brief-v1"
    assert frozen["findings_path"] == FINDINGS_PATH
    assert frozen["sandbox"]["mem_limit"] == "1g"
    assert "section_sha256" not in frozen
    full = agentic.freeze_harness(
        "mini-swe-agent",
        {},
        model=MODEL,
        max_output_tokens=16000,
        budget={"max_llm_requests": 30},
    )
    assert len(full["section_sha256"]) == 64
    assert full["section_inputs"]["max_llm_requests"] == 30


def test_freeze_harness_checks_tool_calling_and_options():
    no_tools = {**MODEL, "supports": {"tools": False}}
    with pytest.raises(ValueError, match="tool-calling"):
        agentic.freeze_harness("opencode", {}, model=no_tools)
    assert agentic.freeze_harness("aider", {}, model=no_tools)["harness"] == "aider"
    with pytest.raises(ValueError, match="Unknown"):
        agentic.freeze_harness("opencode", {"edit_format": "diff"}, model=MODEL)


def test_section_drift_is_detected_when_the_inputs_match():
    frozen = make_frozen()
    params = frozen["params"]
    frozen["agent"] = agentic.freeze_harness(
        "opencode",
        {},
        model=MODEL,
        max_output_tokens=params["max_tokens"],
        budget=frozen["budget"],
    )
    agentic._prepare(frozen=frozen, sample=make_sample(), index=0, run_id=RUN_ID)  # unchanged: fine
    frozen["agent"] = {**frozen["agent"], "section_sha256": "0" * 64}
    with pytest.raises(agentic.HarnessDriftError, match="harness section changed"):
        agentic._prepare(frozen=frozen, sample=make_sample(), index=0, run_id=RUN_ID)


def test_harness_review_options_cover_every_harness():
    options = agentic.harness_review_options()
    assert [o["name"] for o in options] == list(agentic.REVIEW_HARNESSES)
    for option in options:
        assert {"label", "description", "version", "option_schema", "default_options", "notes"} <= set(option)
        assert option["default_options"]["finalizer"] is True
    assert next(o for o in options if o["name"] == "aider")["requires_tool_calling"] is False


def test_review_labels_carry_the_run_id():
    behaviour = Behaviour(
        out_files=outputs(findings__json=json.dumps(document())),
        gateway_logs=gateway_logs(call()),
    )
    _, client = review(behaviour)
    assert {c.labels[LABEL_REVIEW] for c in client.created} == {RUN_ID}
