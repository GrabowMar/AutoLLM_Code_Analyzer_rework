"""Single-shot engine: chunking/coverage, structured calls, repair, failures, budgets (mocked HTTP)."""

from __future__ import annotations

import json
import time

import pytest

from backend.analysis.engine.reviewer import prompt
from backend.analysis.engine.reviewer import single_shot
from backend.analysis.tests.reviewer_support import SECRET
from backend.analysis.tests.reviewer_support import SQLI
from backend.analysis.tests.reviewer_support import XSS
from backend.analysis.tests.reviewer_support import FakeResponse
from backend.analysis.tests.reviewer_support import FakeSession
from backend.analysis.tests.reviewer_support import answer
from backend.analysis.tests.reviewer_support import app_sample
from backend.analysis.tests.reviewer_support import chat_body
from backend.analysis.tests.reviewer_support import finding
from backend.analysis.tests.reviewer_support import frozen_section
from backend.analysis.tests.reviewer_support import make_file
from backend.analysis.tests.reviewer_support import make_sample
from backend.analysis.tests.reviewer_support import ok
from backend.analysis.tests.reviewer_support import patch_normalize

BOTH = ["backend/app.py", "frontend/src/App.jsx"]


@pytest.fixture(autouse=True)
def _normalize(monkeypatch):
    patch_normalize(monkeypatch)


class Hooks:
    def __init__(self, cancel_after: int | None = None) -> None:
        self.beats = 0
        self.checks = 0
        self.cancel_after = cancel_after

    def heartbeat(self) -> None:
        self.beats += 1

    def is_cancelled(self) -> bool:
        self.checks += 1
        return self.cancel_after is not None and self.checks > self.cancel_after


def run(session, frozen=None, sample=None, index=0, hooks=None):
    hooks = hooks or Hooks()
    return single_shot.run_sample(
        frozen=frozen or frozen_section(),
        sample=sample or app_sample(),
        index=index,
        api_key="sk-secret-key",
        is_cancelled=hooks.is_cancelled,
        heartbeat=hooks.heartbeat,
        session=session,
        sleep=lambda s: None,
    )


# --------------------------------------------------------------------------
# Successful replicate
# --------------------------------------------------------------------------


def test_success_one_chunk():
    session = FakeSession(ok(answer([SQLI, SECRET, XSS], BOTH)))
    hooks = Hooks()
    out = run(session, hooks=hooks)
    assert out.status == "succeeded", out.error_message
    assert out.ok
    assert out.failure_reason == ""
    assert out.engine == "single_shot"
    assert out.model_slug == "deepseek/deepseek-v4.1-flash"
    assert out.model_canonical_slug == "deepseek_deepseek-v4.1-flash-20260910"
    assert out.prompt_sha256
    assert out.schema_sha256
    assert [f.grounding for f in out.findings] == ["exact", "exact", "exact"]
    assert all(f.sample_index == 0 and f.is_ai and not f.counted for f in out.findings)
    assert out.grounding == {"exact": 3, "relocated": 0, "ungrounded": 0, "invalid_file": 0, "out_of_range": 0}
    assert out.files_reviewed == BOTH
    assert out.repaired is False
    assert out.coverage["files_in_scope"] == 2
    assert out.coverage["files_sent"] == BOTH
    assert out.coverage["files_not_sent"] == []
    assert out.coverage["chunks"] == 1
    assert out.coverage["chunks_completed"] == 1
    assert out.coverage["review_complete"] is True
    assert out.coverage["out_of_scope"] == {"excluded:scope": 1, "kind:doc": 1, "origin:scaffold": 1}
    assert out.coverage["chars_sent"] > 0
    assert out.usage["llm_calls"] == 1
    assert out.usage["llm_calls_failed"] == 0
    assert out.usage["cost_usd"] == pytest.approx(0.0012)
    assert out.usage["cost_estimated"] is False
    assert out.usage["prompt_tokens"] == 3000
    assert out.usage["reasoning_tokens"] == 200
    assert out.usage["served_models"] == {"deepseek/deepseek-v4.1-flash": 1}
    assert out.providers == {"InferenceNet": 1}
    assert out.finish_reasons == {"stop": 1}
    assert out.latency_seconds is not None
    assert hooks.beats >= 1


def test_request_shape():
    session = FakeSession(ok(answer([], BOTH)))
    out = run(session)
    body = session.requests[0]["json"]
    assert body["model"] == "deepseek/deepseek-v4.1-flash"
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 16000
    assert body["reasoning"] == {"effort": "low"}
    assert body["seed"] == single_shot.llm_seed(out.file_order_seed)
    assert body["provider"] == {"require_parameters": True, "allow_fallbacks": True}
    schema = body["response_format"]["json_schema"]
    assert body["response_format"]["type"] == "json_schema"
    assert schema["strict"] is True
    assert schema["schema"]["properties"]["findings"]["items"]["properties"]["file"]["enum"] == BOTH
    system, user = body["messages"]
    assert system["role"] == "system"
    assert "=== FILE: backend/app.py (python, 17 lines) ===" in user["content"]
    assert "  12|     rows = conn.execute(" in user["content"]
    assert "README.md" not in user["content"]
    assert "backend/scaffold.py" not in user["content"]


def test_artifacts_hold_request_and_response_without_the_key():
    session = FakeSession(ok(answer([SQLI], BOTH)))
    out = run(session)
    assert list(out.artifacts) == ["call-01.json"]
    raw = out.artifacts["call-01.json"]
    assert b"sk-secret-key" not in raw
    doc = json.loads(raw)
    assert doc["response"]["id"] == "gen-1"
    assert doc["request"]["messages"][1]["role"] == "user"
    assert doc["attempts"][0]["status_code"] == 200


def test_unseeded_models_get_no_seed_and_no_temperature_when_dropped():
    frozen = frozen_section()
    frozen["params"] = {**frozen["params"], "seed_forwarded": False, "temperature": None, "reasoning": None}
    session = FakeSession(ok(answer([], BOTH)))
    run(session, frozen=frozen)
    body = session.requests[0]["json"]
    assert "seed" not in body
    assert "temperature" not in body
    assert "reasoning" not in body


@pytest.mark.parametrize(("mode", "expected"), [("json_object", {"type": "json_object"}), ("prompt", None)])
def test_output_modes(mode, expected):
    session = FakeSession(ok(answer([SQLI], BOTH)))
    out = run(session, frozen=frozen_section(output_mode=mode))
    assert out.ok
    assert session.requests[0]["json"].get("response_format") == expected


def test_cost_is_estimated_from_tokens_when_not_reported():
    session = FakeSession(ok(answer([], BOTH), cost=None, prompt_tokens=1_000_000, completion_tokens=0))
    out = run(session)
    assert out.usage["cost_estimated"] is True
    assert out.usage["cost_usd"] == pytest.approx(0.049)


def test_model_claims_about_unseen_files_are_recorded():
    session = FakeSession(ok(answer([], [*BOTH, "backend/secret_service.py"])))
    out = run(session)
    assert out.coverage["files_claimed_not_shown"] == ["backend/secret_service.py"]


def test_incomplete_review_flag_is_recorded_not_failed():
    session = FakeSession(ok(answer([SQLI], BOTH, complete=False)))
    out = run(session)
    assert out.ok
    assert out.coverage["review_complete"] is False


# --------------------------------------------------------------------------
# Repair, truncation, failures
# --------------------------------------------------------------------------


def test_schema_failure_is_repaired_once():
    bad = answer([{**SQLI, "severity": "urgent"}], BOTH)
    session = FakeSession(ok(bad), ok(answer([SQLI], BOTH), gen_id="gen-2"))
    out = run(session)
    assert out.status == "succeeded"
    assert out.repaired is True
    assert len(out.findings) == 1
    assert out.usage["llm_calls"] == 2
    assert sorted(out.artifacts) == ["call-01-repair.json", "call-01.json"]
    repair_messages = session.requests[1]["json"]["messages"]
    assert [m["role"] for m in repair_messages] == ["system", "user", "assistant", "user"]
    assert repair_messages[2]["content"] == bad
    assert "findings/0/severity" in repair_messages[3]["content"]


def test_second_schema_failure_is_invalid_output():
    session = FakeSession(ok("not json"), ok('{"findings": "nope"}'))
    out = run(session)
    assert out.status == "invalid_output"
    assert out.failure_reason == "invalid_output"
    assert "invalid after one repair" in out.error_message
    assert out.findings == []
    assert out.usage["llm_calls"] == 2


def test_truncated_answer_is_a_failed_sample_never_salvaged():
    session = FakeSession(ok('{"findings": [{"file": "backend/app.py"', finish_reason="length"))
    out = run(session)
    assert out.status == "truncated"
    assert out.failure_reason == "truncated"
    assert out.findings == []
    assert out.finish_reasons == {"length": 1}
    assert len(session.requests) == 1  # no repair for a length stop


def test_truncated_repair_is_truncated():
    session = FakeSession(ok("{}"), ok('{"findings": [', finish_reason="length"))
    out = run(session)
    assert out.status == "truncated"


@pytest.mark.parametrize(
    ("status", "reason"),
    [(400, "bad_request"), (401, "auth"), (402, "insufficient_credits"), (404, "model_unavailable")],
)
def test_http_errors_fail_the_sample(status, reason):
    session = FakeSession(FakeResponse(status, {"error": {"code": status, "message": "boom"}}))
    out = run(session)
    assert out.status == "failed"
    assert out.failure_reason == reason
    assert "boom" in out.error_message
    assert out.usage["llm_calls_failed"] == 1
    assert out.findings == []


def test_persistent_server_errors_fail_after_retries():
    session = FakeSession(*[FakeResponse(503, {"error": {"message": "overloaded"}})] * 4)
    out = run(session)
    assert out.status == "failed"
    assert out.failure_reason == "api_error"
    assert out.usage["http_attempts"] == 4
    assert out.usage["retries"] == 3


def test_empty_content_fails_the_sample():
    session = FakeSession(*[FakeResponse(200, chat_body(None))] * 4)
    out = run(session)
    assert out.status == "failed"
    assert out.failure_reason == "empty_output"


def test_error_body_with_http_200_fails_the_sample():
    session = FakeSession(FakeResponse(200, {"error": {"code": 400, "message": "provider rejected schema"}}))
    out = run(session)
    assert out.status == "failed"
    assert out.failure_reason == "bad_request"
    assert "provider rejected schema" in out.error_message


# --------------------------------------------------------------------------
# Chunking and coverage
# --------------------------------------------------------------------------


def big_sample():
    files = [
        make_file(f"backend/mod{i}.py", "".join(f"value_{i}_{n} = {n}\n" for n in range(1, 41))) for i in range(1, 6)
    ]
    files.append(make_file("backend/huge.py", "".join(f"line_{n} = {n}\n" for n in range(1, 401))))
    files.append(make_file("static/logo.bin", b"\x89PNG\xff\x00", kind="source"))
    files.append(make_file("backend/empty.py", "\n\n"))
    files.append(make_file("backend/minified.js", "x" * 9000 + "\n", language="javascript"))
    return make_sample(files)


def test_plan_chunks_is_deterministic_bounded_and_complete():
    sample = big_sample()
    files = list(sample.files)
    chunks, coverage = single_shot.plan_chunks(files, char_budget=4000, seed=123)
    again, _ = single_shot.plan_chunks(list(reversed(files)), char_budget=4000, seed=123)
    assert [c.paths for c in chunks] == [c.paths for c in again]
    assert all(c.chars <= 4000 for c in chunks)
    assert {"path": "static/logo.bin", "reason": "binary"} in coverage["files_not_sent"]
    assert {"path": "backend/empty.py", "reason": "empty"} in coverage["files_not_sent"]
    assert {"path": "backend/minified.js", "reason": "line_exceeds_chunk"} in coverage["files_not_sent"]
    split = {s["path"]: s["parts"] for s in coverage["files_split"]}
    assert split["backend/huge.py"] >= 2
    # Every line of the split file is sent exactly once, with its real number.
    huge_units = [u for c in chunks for u in c.units if u.path == "backend/huge.py"]
    covered = [n for u in sorted(huge_units, key=lambda u: u.start) for n in range(u.start, u.end + 1)]
    assert covered == list(range(1, 401))
    later = next(u for u in huge_units if u.start > 1)
    assert f"{later.start:>4}| line_{later.start} = {later.start}" in later.text
    assert f"lines {later.start}-{later.end} of 400" in later.text
    sent = {p for c in chunks for p in c.paths}
    assert sent == {f"backend/mod{i}.py" for i in range(1, 6)} | {"backend/huge.py"}
    assert coverage["files_planned"] == 6


def test_file_order_differs_by_replicate_but_is_reproducible():
    sample = big_sample()
    seeds = [single_shot.file_order_seed(1, sample.sample_hash, i) for i in range(3)]
    assert len(set(seeds)) == 3
    assert seeds[0] == single_shot.file_order_seed(1, sample.sample_hash, 0)
    assert seeds[0] < 2**48
    orders = [single_shot.plan_chunks(sample.files, char_budget=100_000, seed=s)[0][0].paths for s in seeds]
    assert len({tuple(o) for o in orders}) > 1


def test_multi_chunk_replicate_calls_once_per_chunk_with_chunk_enums():
    sample = big_sample()
    frozen = frozen_section(chunking={"effective_chars": 4000})

    def respond(body):
        enum = body["response_format"]["json_schema"]["schema"]["properties"]["findings"]["items"]["properties"]
        paths = enum["file"]["enum"]
        assert "part " in body["messages"][1]["content"]
        return chat_body(answer([], paths))

    chunks, _ = single_shot.plan_chunks(
        list(sample.files),
        char_budget=4000,
        seed=single_shot.file_order_seed(1, sample.sample_hash, 0),
    )
    session = FakeSession(*[respond] * len(chunks))
    out = run(session, frozen=frozen, sample=sample)
    assert out.status == "succeeded", out.error_message
    assert out.coverage["chunks"] == len(chunks) >= 3
    assert out.coverage["chunks_completed"] == len(chunks)
    assert len(session.requests) == len(chunks)
    assert out.coverage["files_sent"] == sorted({p for c in chunks for p in c.paths})
    assert out.coverage["chunk_files"] == [c.paths for c in chunks]


def test_finding_citing_a_file_from_another_chunk_is_invalid_file():
    a_text = "".join(f"alpha_{n} = {n}\n" for n in range(60))
    sample = make_sample([make_file("a.py", a_text), make_file("b.py", "beta = eval(input())\n")])
    a_chars = len(prompt.render_lines("a.py", prompt.split_lines(a_text), language="python"))
    # Room for a.py alone: the two files must land in separate chunks.
    frozen = frozen_section(chunking={"effective_chars": a_chars + 10})

    def respond(body):
        props = body["response_format"]["json_schema"]["schema"]["properties"]["findings"]["items"]["properties"]
        paths = props["file"]["enum"]
        if paths == ["b.py"]:
            return chat_body(answer([finding("a.py", 1, 1, "alpha_0 = 0", category="bug", cwe=None)], paths))
        return chat_body(answer([], paths))

    session = FakeSession(respond, respond)
    out = run(session, frozen=frozen, sample=sample)
    assert out.ok, out.error_message
    assert out.coverage["chunks"] == 2
    assert [f.grounding for f in out.findings] == ["invalid_file"]


def test_failure_mid_way_records_unreached_files():
    sample = big_sample()
    frozen = frozen_section(chunking={"effective_chars": 4000})
    session = FakeSession(ok(answer([], [])), FakeResponse(400, {"error": {"message": "bad"}}))
    out = run(session, frozen=frozen, sample=sample)
    assert out.status == "failed"
    assert out.coverage["chunks_completed"] == 1
    reasons = {e["reason"] for e in out.coverage["files_not_sent"]}
    assert "not_reached" in reasons
    assert out.findings == []


def test_no_reviewable_files():
    sample = make_sample([make_file("README.md", "# hi\n", kind="doc")])
    out = run(FakeSession(), sample=sample)
    assert out.status == "failed"
    assert out.failure_reason == "no_files"
    assert single_shot.reviewable(frozen_section(), sample) is False
    assert single_shot.reviewable(frozen_section(), app_sample()) is True


# --------------------------------------------------------------------------
# Budgets, cancellation, wall clock, heartbeat
# --------------------------------------------------------------------------


def test_more_chunks_than_requests_fails_before_spending():
    sample = big_sample()
    frozen = frozen_section(
        chunking={"effective_chars": 4000},
        budget={"max_cost_usd": 0.5, "max_llm_requests": 2, "wall_clock_seconds": 900},
    )
    session = FakeSession()
    out = run(session, frozen=frozen, sample=sample)
    assert out.status == "budget_exhausted"
    assert out.failure_reason == "budget"
    assert session.requests == []


def test_repair_beyond_the_request_budget_is_budget_exhausted():
    frozen = frozen_section(budget={"max_cost_usd": 0.5, "max_llm_requests": 1, "wall_clock_seconds": 900})
    session = FakeSession(ok("not json"))
    out = run(session, frozen=frozen)
    assert out.status == "budget_exhausted"
    assert len(session.requests) == 1


def test_cost_budget_is_enforced_before_the_next_call():
    frozen = frozen_section(budget={"max_cost_usd": 0.001, "max_llm_requests": 40, "wall_clock_seconds": 900})
    session = FakeSession(ok("not json", cost=0.0015))
    out = run(session, frozen=frozen)
    assert out.status == "budget_exhausted"
    assert "max_cost_usd" in out.error_message
    assert out.usage["cost_usd"] == pytest.approx(0.0015)


def test_projected_input_cost_blocks_an_unaffordable_call():
    frozen = frozen_section(budget={"max_cost_usd": 0.001, "max_llm_requests": 40, "wall_clock_seconds": 900})
    frozen["model"] = {**frozen["model"], "pricing": {"input_per_token": 1e-3, "output_per_token": 1e-3}}
    session = FakeSession()
    out = run(session, frozen=frozen)
    assert out.status == "budget_exhausted"
    assert session.requests == []


def test_cancelled_before_the_first_call():
    session = FakeSession()
    out = run(session, hooks=Hooks(cancel_after=0))
    assert out.status == "cancelled"
    assert out.failure_reason == "cancelled"
    assert session.requests == []


def test_wall_clock_exhausted(monkeypatch):
    clock = iter([0.0, 10_000.0, 10_000.0, 10_000.0, 10_000.0, 10_000.0])
    monkeypatch.setattr(single_shot, "_now", lambda: next(clock, 10_000.0))
    session = FakeSession()
    out = run(session)
    assert out.status == "timed_out"
    assert out.failure_reason == "timeout"
    assert session.requests == []


def test_call_waiting_heartbeats_and_honours_cancel(monkeypatch):
    monkeypatch.setattr(single_shot, "HEARTBEAT_INTERVAL_S", 0.02)
    beats = []

    def slow():
        time.sleep(0.15)
        return "done"

    result = single_shot._call_waiting(
        slow,
        heartbeat=lambda: beats.append(1),
        is_cancelled=lambda: False,
        deadline=time.monotonic() + 60,
    )
    assert result == "done"
    assert len(beats) >= 2
    cancelled = single_shot._call_waiting(
        slow,
        heartbeat=lambda: None,
        is_cancelled=lambda: True,
        deadline=time.monotonic() + 60,
    )
    assert cancelled == "cancelled"
    timed_out = single_shot._call_waiting(
        slow,
        heartbeat=lambda: None,
        is_cancelled=lambda: False,
        deadline=time.monotonic() - 1,
    )
    assert timed_out == "timeout"


# --------------------------------------------------------------------------
# Spend completeness: unknown is never 0
# --------------------------------------------------------------------------


def test_attempts_without_a_response_leave_the_spend_unknown(monkeypatch):
    import requests

    from backend.analysis.engine.reviewer import client

    monkeypatch.setattr(client, "_backoff", lambda attempt: 0.0)
    # Regression: four read timeouts (the provider may have run and billed them) reported $0.00.
    session = FakeSession(*[requests.exceptions.ReadTimeout("read timed out")] * 4)
    out = run(session)
    assert (out.status, out.failure_reason) == ("failed", "api_error")
    assert out.usage["cost_usd"] is None
    assert out.usage["cost_complete"] is False
    assert out.usage["attempts_without_usage"] == 4

    # Some attempts priced, some not: a lower bound, flagged.
    session = FakeSession(requests.exceptions.ReadTimeout("read timed out"), ok(answer([SQLI], BOTH)))
    out = run(session)
    assert out.status == "succeeded", out.error_message
    assert out.usage["cost_usd"] == pytest.approx(0.0012)
    assert out.usage["cost_complete"] is False


def test_http_error_responses_are_known_unbilled_and_nothing_sent_is_zero():
    out = run(FakeSession(FakeResponse(401, {"error": {"code": 401, "message": "bad key"}})))
    assert (out.usage["cost_usd"], out.usage["cost_complete"]) == (0.0, True)
    out = run(FakeSession(), sample=make_sample([make_file("README.md", "# hi\n", kind="doc")]))
    assert out.failure_reason == "no_files"
    assert (out.usage["cost_usd"], out.usage["cost_complete"], out.usage["llm_calls"]) == (0.0, True, 0)


@pytest.mark.parametrize(("result", "status"), [("timeout", "timed_out"), ("cancelled", "cancelled")])
def test_an_abandoned_call_was_sent_and_its_spend_is_unknown(monkeypatch, result, status):
    # Regression: the in-flight call abandoned at the wall clock or a cancel left no trace: no cost
    # flag, and its chunk was reported "not_reached" although it had been sent.
    monkeypatch.setattr(single_shot, "_call_waiting", lambda *a, **k: result)
    out = run(FakeSession())
    assert out.status == status
    assert out.usage["calls_abandoned"] == 1
    assert out.usage["cost_usd"] is None
    assert out.usage["cost_complete"] is False
    assert out.coverage["files_sent"] == BOTH
    assert out.coverage["files_unanswered"] == BOTH
    assert not any(e["reason"] == "not_reached" for e in out.coverage["files_not_sent"])
    assert json.loads(out.artifacts["call-01.json"])["abandoned"] == result
