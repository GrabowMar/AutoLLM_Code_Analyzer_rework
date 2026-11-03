"""Variant evaluation (selection, modes, reuse, original) and the batch submitter (spec sections 7.6, 7.8)."""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest

from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import SpendLedgerEntry
from backend.generation.models.choices import RunPhase
from backend.generation.models.choices import RunStatus
from backend.generation.slot import selection
from backend.generation.slot import validate
from backend.generation.slot.assemble import assemble
from backend.generation.slot.evaluate import evaluate_original
from backend.generation.slot.evaluate import evaluate_variant
from backend.generation.slot.evaluate import selection_for
from backend.generation.slot.evaluate import slot_pass_rate
from backend.generation.slot.executors import batch_submitter
from backend.generation.slot.executors import single_shot
from backend.generation.slot.part import part_of
from backend.generation.testing.content import eval_result_content
from backend.generation.testing.fake_slot import FakeBatchBackend
from backend.generation.testing.fake_slot import chat_response
from backend.generation.tests.wp12_slot.helpers import slot_run

pytestmark = pytest.mark.django_db

PATCH = {"kind": "endpoint", "endpoint": {"method": "PATCH", "path": "/api/todos/{id}"}}
NEW = '@todos.patch("/<int:todo_id>")\n@login_required\ndef update_todo(todo_id: int):\n    return {}, 410'


def _planned(golden: Any, mode: str = "full", level: str = "security") -> dict[str, Any]:
    suite = golden.get("eval_suite", "eval-v2")
    return {
        "planned_evaluation": {
            "eval_suite": {"ref": f"{suite.slug}@{suite.version}", "content_hash": suite.content_hash},
            "eval_level": level,
            "mode": mode,
        },
    }


def _validated_target(slot_set: Any, user: Any, selection_in: dict[str, Any] = PATCH) -> Any:
    target = selection.create_target(slot_set, selection_in, user=user, validate=False)
    validate.validate_target(target, synchronous=True)
    target.refresh_from_db()
    return target


def _variant(target: Any, golden: Any, code: str = NEW, **planned: Any) -> GenerationRun:
    run = slot_run(target, manifest_updates=_planned(golden, **planned))
    assemble(run, code)
    run.save(update_fields=["tree_hash"])
    return run


def test_variant_and_original_run_the_discriminating_selection(
    slot_set: Any,
    researcher: Any,
    golden: Any,
    evaluator: Any,
) -> None:
    target = _validated_target(slot_set, researcher)
    run = _variant(target, golden)
    evaluator.calls.clear()
    evaluation = evaluate_variant(run)
    original = evaluate_original(target)
    variant_call, original_call = evaluator.calls
    assert variant_call.only_checks == original_call.only_checks == ["todos.update.ok"]
    assert variant_call.probes == original_call.probes == ["probe.todos.idor"]
    assert variant_call.mode == "full"
    assert original_call.run_id == str(target.original_run_id)
    assert slot_pass_rate(evaluation, target) == 1.0
    assert slot_pass_rate(original, target) == 1.0


def test_probes_only_from_functional_level(slot_set: Any, researcher: Any, golden: Any, evaluator: Any) -> None:
    target = _validated_target(slot_set, researcher)
    run = _variant(target, golden, level="build")
    evaluator.calls.clear()
    evaluate_variant(run)
    assert evaluator.calls[0].probes == []


def test_overlay_for_an_interpreted_backend_file_and_fallback_for_a_frontend_component(
    slot_set: Any,
    researcher: Any,
    golden: Any,
    evaluator: Any,
) -> None:
    target = _validated_target(slot_set, researcher)
    run = _variant(target, golden, mode="overlay")
    evaluator.calls.clear()
    evaluate_variant(run)
    call = evaluator.calls[0]
    assert call.mode == "overlay"
    assert call.image_override == slot_set.parent_eval.image_id
    assert list(call.overlay_files) == ["backend/todos.py"]
    component = _validated_target(
        slot_set,
        researcher,
        {"kind": "component", "component": "TodoForm", "default_hole_mode": "stub"},
    )
    component_run = slot_run(component, manifest_updates=_planned(golden, mode="overlay"))
    assemble(component_run, part_of(component).original_text)
    component_run.save(update_fields=["tree_hash"])
    evaluator.calls.clear()
    evaluate_variant(component_run)
    assert evaluator.calls[0].mode == "full"
    component_run.refresh_from_db()
    assert (
        component_run.execution["slot_eval"]["fallback_reason"] == "a slot file is a frontend file that needs a build"
    )


def test_identical_tree_reuses_the_evaluation(
    slot_set: Any,
    researcher: Any,
    golden: Any,
    evaluator: Any,
    monkeypatch: Any,
) -> None:
    target = _validated_target(slot_set, researcher)
    first = _variant(target, golden)
    first_eval = evaluate_variant(first)
    second = _variant(target, golden)
    assert second.tree_hash == first.tree_hash
    from backend.generation.evaluation import reuse

    seen: dict[str, Any] = {}

    def fake_find(run: Any, suite: Any, only_checks: Any = None, **kwargs: Any) -> Any:
        seen.update(kwargs, only_checks=only_checks)
        return first_eval

    monkeypatch.setattr(reuse, "find_reusable", fake_find)
    evaluator.calls.clear()
    reused = evaluate_variant(second)
    chosen = selection_for(second)
    assert seen == {
        "only_checks": chosen.only_checks,
        "probes": chosen.probes,
        "eval_level": chosen.eval_level,
        "mode": "full",
    }
    assert evaluator.calls == []
    assert reused.reused_from_id == first_eval.pk
    assert reused.result == first_eval.result
    second.refresh_from_db()
    assert {"code": "eval_reused", "level": "info"} in second.validity["notes"]


def test_output_invalid_variants_are_never_built(slot_set: Any, researcher: Any, golden: Any, evaluator: Any) -> None:
    target = _validated_target(slot_set, researcher)
    run = slot_run(target)
    GenerationRun.objects.filter(pk=run.pk).update(outcome="output_invalid")
    run.refresh_from_db()
    with pytest.raises(ValueError, match="never built"):
        evaluate_variant(run)


def test_slot_pass_rate_is_never_zero_for_a_missing_measurement(slot_set: Any, researcher: Any, golden: Any) -> None:
    target = selection.create_target(slot_set, PATCH, user=researcher, validate=False)
    run = slot_run(target)
    suite = golden.get("eval_suite", "eval-v2")
    unmeasured = RunEvaluation.objects.create(
        run=run,
        eval_suite=suite,
        eval_level="security",
        status="not_measured",
        result=eval_result_content(measured=False),
    )
    assert slot_pass_rate(unmeasured, target) is None  # pending validation: n/a
    target.validation = {**target.validation, "status": "ok", "discriminating_checks": ["todos.update.ok"]}
    target.save(update_fields=["validation"])
    assert slot_pass_rate(unmeasured, target) is None
    assert slot_pass_rate(None, target) is None


class TestBatch:
    def _runs(self, slot_set: Any, user: Any, n: int = 3) -> list[GenerationRun]:
        target = selection.create_target(slot_set, PATCH, user=user, validate=False)
        return [slot_run(target, seed=seed) for seed in range(n)]

    def test_refused_until_backends_are_verified(self, slot_set: Any, researcher: Any, settings: Any) -> None:
        settings.BATCH_BACKENDS_VERIFIED = False
        with pytest.raises(batch_submitter.BatchUnsupportedError, match="not verified"):
            batch_submitter.run_batch(self._runs(slot_set, researcher, 1))

    def test_happy_path_renders_like_single_shot(self, slot_set: Any, researcher: Any, settings: Any) -> None:
        settings.BATCH_BACKENDS_VERIFIED = True
        runs = self._runs(slot_set, researcher)
        backend = FakeBatchBackend(
            answer=lambda cid, body: chat_response(json.dumps({"code": NEW})),
            polls_until_done=2,
        )
        sleeps: list[float] = []
        outcomes = batch_submitter.run_batch(runs, backend=backend, sleep=sleeps.append)
        assert set(outcomes.values()) == {"evaluate"}
        assert len(backend.submitted) == 1
        assert sleeps == [settings.BATCH_POLL_S]
        for run in runs:
            run.refresh_from_db()
            assert (run.status, run.phase) == (RunStatus.RUNNING, RunPhase.EVAL_QUEUED)
            artifact = GenerationArtifact.objects.get(run=run)
            assert artifact.telemetry["serving_tier"] == "batch"
            assert run.execution["batch"]["id"] == "batch-1"
            usage = SpendLedgerEntry.objects.get(dedupe_key=f"call:{run.pk}:{run.attempt + 1}:1")
            assert (usage.kind, usage.source, usage.requests) == ("usage", "batch", 1)
        item = next(i for i in backend.submitted[0] if i.custom_id == str(runs[0].pk))
        manifest = runs[0].manifest.manifest
        expected = single_shot.build_request(
            runs[0],
            manifest,
            single_shot._stimuli_texts(runs[0]),
            part_of(runs[0].slot_target),
        )
        assert item.body == expected.body
        assert {i.body["seed"] for i in backend.submitted[0]} == {0, 1, 2}

    def test_expiry_ends_cells_as_batch_expired(self, slot_set: Any, researcher: Any, settings: Any) -> None:
        settings.BATCH_BACKENDS_VERIFIED = True
        runs = self._runs(slot_set, researcher, 2)
        backend = FakeBatchBackend(answer=lambda cid, body: chat_response("{}"), polls_until_done=None)
        ticks = itertools.count(step=settings.BATCH_EXPIRY_S / 2)
        outcomes = batch_submitter.run_batch(runs, backend=backend, sleep=lambda _s: None, clock=lambda: next(ticks))
        assert set(outcomes.values()) == {"batch_expired"}
        for run in runs:
            run.refresh_from_db()
            assert (run.status, run.outcome, run.outcome_reason) == (RunStatus.FAILED, "infra_error", "batch_expired")

    def test_resume_polls_the_recorded_batch(self, slot_set: Any, researcher: Any, settings: Any) -> None:
        settings.BATCH_BACKENDS_VERIFIED = True
        runs = self._runs(slot_set, researcher, 2)
        backend = FakeBatchBackend(answer=lambda cid, body: chat_response(json.dumps({"code": NEW})))
        backend.submitted.append([batch_submitter.BatchItem(custom_id=str(r.pk), body={}) for r in runs])
        for run in runs:
            GenerationRun.objects.filter(pk=run.pk).update(execution={"batch": {"id": "batch-1", "backend": "fake"}})
            run.refresh_from_db()
        batch_submitter.run_batch(runs, backend=backend, sleep=lambda _s: None)
        assert len(backend.submitted) == 1  # nothing submitted twice

    def test_mixed_targets_are_refused(self, slot_set: Any, researcher: Any, settings: Any) -> None:
        settings.BATCH_BACKENDS_VERIFIED = True
        runs = self._runs(slot_set, researcher, 1)
        other = selection.create_target(
            slot_set,
            {"kind": "symbol", "symbol_id": "backend/db.py::init"},
            user=researcher,
            validate=False,
        )
        with pytest.raises(batch_submitter.BatchUnsupportedError, match="exactly one"):
            batch_submitter.run_batch([*runs, slot_run(other)])
