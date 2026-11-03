"""Single-shot executor: strict parsing (no repair, no retry on invalid), recording, refusal of T=0 (7.6, D13)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.generation import blobs
from backend.generation.models import GenerationArtifact
from backend.generation.models import RunAttempt
from backend.generation.models import RunFile
from backend.generation.models import SlotVariantResult
from backend.generation.models import SpendLedgerEntry
from backend.generation.models.choices import RunPhase
from backend.generation.models.choices import RunStatus
from backend.generation.slot import selection
from backend.generation.slot.executors import single_shot
from backend.generation.slot.executors.single_shot import SlotRequest
from backend.generation.slot.executors.single_shot import TransportError
from backend.generation.slot.executors.single_shot import parse_output
from backend.generation.testing.fake_slot import FakeSlotTransport
from backend.generation.testing.fake_slot import chat_response
from backend.generation.tests.wp12_slot.helpers import parent_text
from backend.generation.tests.wp12_slot.helpers import slot_run

CODE_REQUEST = SlotRequest(
    body={},
    files_mode=False,
    allowlist=("a.py",),
    max_output_tokens=1000,
    structured_output=False,
    est_input_tokens=10,
)
FILES_REQUEST = SlotRequest(
    body={},
    files_mode=True,
    allowlist=("a.py",),
    max_output_tokens=None,
    structured_output=False,
    est_input_tokens=10,
)

NEW_UPDATE = (
    '@todos.patch("/<int:todo_id>")\n@login_required\ndef update_todo(todo_id: int):\n'
    '    return {"error": "gone"}, 410\n'
)


@pytest.mark.parametrize(
    ("content", "kwargs", "request_", "status"),
    [
        ('{"code": "x = 1"}', {}, CODE_REQUEST, "ok"),
        ('```json\n{"code": "x = 1"}\n```', {}, CODE_REQUEST, "ok"),
        ('```\n{"code": "x = 1"}\n```', {}, CODE_REQUEST, "ok"),
        ('Here you go:\n{"code": "x = 1"}', {}, CODE_REQUEST, "invalid_json"),
        ('{"code": "x = 1"} hope this helps', {}, CODE_REQUEST, "invalid_json"),
        ('```json\n{"code": "x = 1"}\n```\n```json\n{"code": "y"}\n```', {}, CODE_REQUEST, "invalid_json"),
        ('{"code": "x = 1", "explanation": "..."}', {}, CODE_REQUEST, "schema_violation"),
        ('["x = 1"]', {}, CODE_REQUEST, "invalid_json"),
        ('{"code": 5}', {}, CODE_REQUEST, "schema_violation"),
        ('{"code": "   "}', {}, CODE_REQUEST, "empty"),
        ("", {}, CODE_REQUEST, "empty"),
        (None, {}, CODE_REQUEST, "empty"),
        ('{"code": "x = 1', {"finish_reason": "length"}, CODE_REQUEST, "truncated"),
        ('{"code": "x = 1"}', {"completion_tokens": 5000}, CODE_REQUEST, "truncated"),
        ("I'm sorry, but I can't help with that.", {}, CODE_REQUEST, "refusal"),
        (None, {"refusal": "I cannot comply"}, CODE_REQUEST, "refusal"),
        ('{"files": {"a.py": "x = 1\\n"}}', {}, FILES_REQUEST, "ok"),
        ('{"files": {"a.py": "x", "b.py": "y"}}', {}, FILES_REQUEST, "schema_violation"),
        ('{"files": {}}', {}, FILES_REQUEST, "schema_violation"),
        ('{"files": {"a.py": ""}}', {}, FILES_REQUEST, "empty"),
        ('{"code": "x"}', {}, FILES_REQUEST, "schema_violation"),
    ],
)
def test_parse_output(content: str | None, kwargs: dict[str, Any], request_: SlotRequest, status: str) -> None:
    result = parse_output(chat_response(content, **kwargs), request_)
    assert result.status == status
    if status == "ok":
        assert result.replacement in ("x = 1", {"a.py": "x = 1\n"})
    else:
        assert result.replacement is None


@pytest.mark.django_db
class TestRunStage:
    def _target(self, slot_set: Any, user: Any) -> Any:
        return selection.create_target(
            slot_set,
            {"kind": "endpoint", "endpoint": {"method": "PATCH", "path": "/api/todos/{id}"}},
            user=user,
            validate=False,
        )

    def test_valid_answer_is_assembled_and_queued_for_evaluation(
        self,
        slot_set: Any,
        researcher: Any,
        monkeypatch: Any,
    ) -> None:
        target = self._target(slot_set, researcher)
        run = slot_run(target)
        transport = FakeSlotTransport([chat_response(json.dumps({"code": NEW_UPDATE}))]).install(monkeypatch)
        single_shot.run_stage(run)
        run.refresh_from_db()
        assert (run.status, run.phase, run.outcome) == (RunStatus.RUNNING, RunPhase.EVAL_QUEUED, "")
        assert run.lease_owner == ""
        (row,) = RunFile.objects.filter(run=run)
        assert (row.path, row.origin) == ("backend/todos.py", "slot")
        assembled = blobs.get_text(row.blob)
        original = parent_text(slot_set.parent_run, "backend/todos.py")
        start = target.address["span"]["start_byte"]
        end = target.address["span"]["end_byte"]
        assert assembled == original[:start] + NEW_UPDATE.rstrip("\n") + original[end:]
        assert run.tree_hash
        assert run.tree_hash != slot_set.frozen_tree_hash
        result = SlotVariantResult.objects.get(run=run)
        assert result.parse_status == "ok"
        assert result.diff_vs_original["lines_added"] >= 1
        assert result.new_dependencies == []
        artifact = GenerationArtifact.objects.get(run=run)
        assert artifact.stage == "slot_call_0001"
        assert artifact.telemetry["serving_tier"] == "standard"
        attempt = RunAttempt.objects.get(run=run)
        assert attempt.attempt == run.attempt + 1
        assert attempt.usage["requests_by_class"] == {"main": 1}
        assert run.metrics["cost_usd"] == pytest.approx(0.00021)
        usage = SpendLedgerEntry.objects.get(dedupe_key=f"call:{run.pk}:{attempt.attempt}:1")
        assert (usage.kind, usage.source, usage.requests) == ("usage", "gateway_final", 1)
        assert float(usage.amount_usd) == pytest.approx(0.00021)
        assert usage.tokens_in == attempt.usage["prompt_tokens"]
        assert usage.tokens_out == attempt.usage["completion_tokens"]
        adjust = SpendLedgerEntry.objects.get(dedupe_key=f"adj:{run.pk}:{attempt.attempt}")
        assert float(adjust.amount_usd) == 0  # settled with the same final usage
        assert SpendLedgerEntry.objects.filter(run=run, kind="usage").count() == 1
        body = transport.requests[0]
        assert body["temperature"] == 0.7
        assert body["seed"] == run.seed
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        assert "response_format" not in body

    def test_invalid_answer_is_output_invalid_and_never_retried(
        self,
        slot_set: Any,
        researcher: Any,
        monkeypatch: Any,
    ) -> None:
        run = slot_run(self._target(slot_set, researcher))
        transport = FakeSlotTransport([chat_response('Sure! {"code": "pass"}')]).install(monkeypatch)
        single_shot.run_stage(run)
        run.refresh_from_db()
        assert (run.status, run.outcome, run.outcome_reason) == (RunStatus.COMPLETED, "output_invalid", "invalid_json")
        assert run.usable is True  # model-caused bad output is data
        assert len(transport.requests) == 1
        assert not RunFile.objects.filter(run=run).exists()
        assert SlotVariantResult.objects.get(run=run).parse_status == "invalid_json"
        assert SpendLedgerEntry.objects.filter(run=run, kind="usage", source="gateway_final").count() == 1

    def test_truncated_answer_has_the_note(self, slot_set: Any, researcher: Any, monkeypatch: Any) -> None:
        run = slot_run(self._target(slot_set, researcher))
        FakeSlotTransport([chat_response('{"code": "def f(', finish_reason="length")]).install(monkeypatch)
        single_shot.run_stage(run)
        run.refresh_from_db()
        assert (run.outcome, run.outcome_reason) == ("output_invalid", "truncated")
        assert [note["code"] for note in run.validity["notes"]] == ["truncated"]
        assert run.metrics["call_truncations"] == 1

    @pytest.mark.parametrize(
        "sampling",
        [
            {"policy": "provider_default", "forced_params": {}},
            {"policy": "explicit", "forced_params": {"temperature": 0}},
        ],
    )
    def test_provider_default_and_zero_temperature_are_refused(
        self,
        slot_set: Any,
        researcher: Any,
        monkeypatch: Any,
        sampling: dict[str, Any],
    ) -> None:
        run = slot_run(self._target(slot_set, researcher), manifest_updates={"sampling": sampling})
        transport = FakeSlotTransport([]).install(monkeypatch)
        single_shot.run_stage(run)
        run.refresh_from_db()
        assert (run.outcome, run.outcome_reason) == ("excluded", "config_invalid")
        assert transport.requests == []

    def test_cost_cap_refuses_before_calling(self, slot_set: Any, researcher: Any, monkeypatch: Any) -> None:
        effective = {"run": {"max_cost_usd": 0.0000001}, "call": {"max_output_tokens": 4000, "max_input_tokens": None}}
        run = slot_run(self._target(slot_set, researcher), manifest_updates={"resources": {"effective": effective}})
        transport = FakeSlotTransport([]).install(monkeypatch)
        single_shot.run_stage(run)
        run.refresh_from_db()
        assert (run.outcome, run.outcome_reason) == ("excluded", "config_invalid")
        assert run.validity["notes"] == [{"code": "budget_exhausted:cost", "level": "warn"}]
        assert transport.requests == []

    def test_transport_errors_are_retried_by_policy(self, slot_set: Any, researcher: Any, monkeypatch: Any) -> None:
        run = slot_run(self._target(slot_set, researcher))
        transport = FakeSlotTransport(
            [chat_response(json.dumps({"code": NEW_UPDATE}))],
            errors=[TransportError("429", status=429), TransportError("502", status=502)],
        ).install(monkeypatch)
        single_shot.run_stage(run)
        run.refresh_from_db()
        assert len(transport.requests) == 3
        assert run.phase == RunPhase.EVAL_QUEUED

    def test_non_retryable_transport_error_is_infra_never_a_model_outcome(
        self,
        slot_set: Any,
        researcher: Any,
        monkeypatch: Any,
    ) -> None:
        run = slot_run(self._target(slot_set, researcher))
        transport = FakeSlotTransport([], errors=[TransportError("bad key", status=401, retryable=False)]).install(
            monkeypatch,
        )
        single_shot.run_stage(run)
        run.refresh_from_db()
        assert len(transport.requests) == 1
        assert run.outcome in {"", "infra_error"}
        assert run.outcome != "output_invalid"
        assert not SlotVariantResult.objects.filter(run=run).exists()

    def test_structured_output_and_caps_come_from_the_manifest(
        self,
        slot_set: Any,
        researcher: Any,
        monkeypatch: Any,
    ) -> None:
        effective = {"slot": {"max_output_tokens": 900}, "call": {"max_output_tokens": 4000}}
        run = slot_run(
            self._target(slot_set, researcher),
            manifest_updates={
                "resources": {"effective": effective},
                "sampling": {"forced_params": {"temperature": 0.7, "response_format": "json_schema"}},
            },
        )
        transport = FakeSlotTransport([chat_response(json.dumps({"code": NEW_UPDATE}))]).install(monkeypatch)
        single_shot.run_stage(run)
        body = transport.requests[0]
        assert body["max_tokens"] == 900
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["response_format"]["json_schema"]["schema"]["required"] == ["code"]
        assert GenerationArtifact.objects.get(run=run).telemetry["structured_output"] is True

    def test_file_target_uses_the_files_contract(self, slot_set: Any, researcher: Any, monkeypatch: Any) -> None:
        target = selection.create_target(
            slot_set,
            {"kind": "file", "path": "backend/db.py"},
            user=researcher,
            validate=False,
        )
        run = slot_run(target)
        FakeSlotTransport(
            [chat_response(json.dumps({"files": {"backend/db.py": "import sqlite3\nimport requests\n"}}))],
        ).install(monkeypatch)
        single_shot.run_stage(run)
        run.refresh_from_db()
        assert run.phase == RunPhase.EVAL_QUEUED
        (row,) = RunFile.objects.filter(run=run)
        assert blobs.get_text(row.blob) == "import sqlite3\nimport requests\n"
        result = SlotVariantResult.objects.get(run=run)
        assert result.new_imports == ["requests"]
        assert result.new_dependencies == ["requests"]


def test_sampling_issues() -> None:
    assert [i.code for i in single_shot.sampling_issues({"policy": "provider_default"})] == [
        "slot_sampling_provider_default",
    ]
    assert single_shot.sampling_issues({"policy": "explicit", "params": {"temperature": 0.0}})
    assert single_shot.sampling_issues({"policy": "explicit", "params": {"temperature": 0.7}}) == []
