"""services/responses.py: question validation, autosave, strict submit validation per block type, skip, verdicts."""

from __future__ import annotations

import pytest

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalTask
from backend.human_eval.models import FindingVerdict
from backend.human_eval.models import Observation
from backend.human_eval.models import Response
from backend.human_eval.services import allocation
from backend.human_eval.services import context
from backend.human_eval.services import responses
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.instruments import SUS_QUESTION_IDS
from backend.human_eval.tests.factories import ObservationFactory
from backend.human_eval.tests.factories import make_open_task
from backend.human_eval.tests.factories import workbench_assignment
from backend.human_eval.tests.factories import workbench_design
from backend.human_eval.tests.factories import workbench_findings

pytestmark = pytest.mark.django_db

PAIRWISE_DESIGN = {
    "blocks": [
        {
            "id": "pref",
            "type": "pairwise",
            "title": "Preference",
            "criteria": [{"id": "ux", "label": "Easier to use"}, {"id": "works", "label": "Works better"}],
            "scale": "ab_tie",
            "overall": True,
            "rationale": "required",
        },
        {"id": "bugs", "type": "bugs", "title": "Bugs", "min_reports": 1},
    ],
}


def _reload(assignment: Assignment) -> Assignment:
    return Assignment.objects.select_related("task", "participant", "item", "item__app_a", "item__app_b").get(
        pk=assignment.pk,
    )


def _open(n_findings: int = 2, **design_kwargs) -> Assignment:
    task = make_open_task(n_apps=1, design=workbench_design(**design_kwargs))
    if n_findings:
        workbench_findings(task.apps.get().job, "heval-sast-resp", n_findings)
    assignment = workbench_assignment(task)
    context.open_assignment(assignment)
    return _reload(assignment)


def _requirement_ids(assignment: Assignment) -> list[str]:
    return [item["id"] for item in assignment.context["blocks"]["reqs"]["requirements"]["single"]]


def _finding_ids(assignment: Assignment) -> list[str]:
    return [entry["id"] for entry in assignment.context["blocks"]["sec"]["findings"]["single"]]


def _complete_answers(assignment: Assignment) -> dict[str, dict]:
    return {
        "ratings": {"criteria": {"functionality": 5}, "comment": "Works fine"},
        "reqs": {"items": {item_id: {"verdict": "met"} for item_id in _requirement_ids(assignment)}},
        "sec": {"checklist": {"xss": {"outcome": "not_vulnerable"}, "idor": {"outcome": "not_tested"}}},
        "tasks": {
            "scenarios": {
                "create": {"outcome": "success", "seconds": 31.5, "ease": 6},
                "share": {"outcome": "not_attempted"},
            },
        },
        "sus": {"answers": dict.fromkeys(SUS_QUESTION_IDS, 3)},
        "extra": {"answers": {"liked": "yes", "tags": ["fast"], "age": 30, "attn": "a"}},
        "bugs": {"none_found": {"single": True}},
    }


def _save_all(assignment: Assignment, answers: dict[str, dict]) -> None:
    for block_id, block_answers in answers.items():
        responses.save_response(assignment, block_id, block_answers)


def _review_all(assignment: Assignment) -> None:
    for finding_id in _finding_ids(assignment):
        responses.set_finding_verdict(assignment, finding_id, "tp", "")


def _paths(errors: list[dict]) -> set[str]:
    return {error["path"] for error in errors}


def _submit_errors(assignment: Assignment, overrides: dict[str, dict]) -> set[str]:
    answers = _complete_answers(assignment)
    answers.update(overrides)
    _save_all(assignment, answers)
    _review_all(assignment)
    return _paths(responses.validate_submission(_reload(assignment)))


# --- questions -----------------------------------------------------------------


QUESTIONS = [
    {"id": "name", "kind": "text", "label": "Name", "required": True},
    {"id": "bio", "kind": "textarea", "label": "Bio", "required": False},
    {
        "id": "role",
        "kind": "single",
        "label": "Role",
        "required": True,
        "options": [{"value": "dev", "label": "Dev"}, {"value": "qa", "label": "QA"}],
    },
    {
        "id": "langs",
        "kind": "multi",
        "label": "Languages",
        "required": True,
        "options": [{"value": "py", "label": "Python"}, {"value": "js", "label": "JS"}],
    },
    {"id": "skill", "kind": "scale", "label": "Skill", "required": True, "scale": {"min": 0, "max": 100, "step": 5}},
    {"id": "years", "kind": "number", "label": "Years", "required": True, "min": 0, "max": 60},
    {
        "id": "attn",
        "kind": "attention",
        "label": "Pick QA",
        "required": True,
        "options": [{"value": "dev", "label": "Dev"}, {"value": "qa", "label": "QA"}],
        "expected": "qa",
    },
]


def test_validate_question_answers_strict_accepts_complete_valid_answers():
    answers = {
        "name": "Ada",
        "role": "qa",
        "langs": ["py", "py", "js"],
        "skill": 35,
        "years": 2.5,
        "attn": "dev",  # a wrong attention answer is still a valid answer (quality flags judge it)
        "unknown": "dropped",
    }
    cleaned, errors = responses.validate_question_answers(QUESTIONS, answers, strict=True)
    assert errors == []
    assert cleaned == {"name": "Ada", "role": "qa", "langs": ["py", "js"], "skill": 35, "years": 2.5, "attn": "dev"}


def test_validate_question_answers_strict_errors_per_question():
    answers = {"name": "  ", "role": "boss", "langs": ["rust"], "skill": 33, "years": 61, "attn": None}
    cleaned, errors = responses.validate_question_answers(QUESTIONS, answers, strict=True)
    messages = {error["path"]: error["message"] for error in errors}
    assert set(messages) == {"name", "role", "langs", "skill", "years", "attn"}
    assert messages["name"] == "This question is required"
    assert messages["skill"] == "Must be a whole number from 0 to 100 in steps of 5"
    assert messages["years"] == "Must be at most 60"
    assert cleaned == {}
    _, errors = responses.validate_question_answers(QUESTIONS, {"years": -1}, strict=True)
    assert {"path": "years", "message": "Must be at least 0"} in errors


def test_validate_question_answers_loose_checks_types_only():
    cleaned, errors = responses.validate_question_answers(
        QUESTIONS,
        {"skill": 33, "years": 99, "role": "x"},
        strict=False,
    )
    assert errors == []
    assert cleaned == {"skill": 33, "years": 99, "role": "x"}
    _, errors = responses.validate_question_answers(
        QUESTIONS,
        {"name": 5, "langs": "py", "skill": "high", "years": True, "bio": "x" * 5001},
        strict=False,
    )
    assert _paths(errors) == {"name", "langs", "skill", "years", "bio"}
    assert responses.validate_question_answers(QUESTIONS, ["not", "a", "dict"], strict=False)[1] == [
        {"path": "", "message": "Answers must be an object"},
    ]


# --- submit: every block type -----------------------------------------------------


def test_submit_with_complete_answers_submits():
    assignment = _open()
    _save_all(assignment, _complete_answers(assignment))
    _review_all(assignment)
    assert responses.submit(assignment) == []
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.SUBMITTED
    assert assignment.submitted_at is not None
    assert assignment.last_activity_at == assignment.submitted_at


def test_submit_without_answers_lists_every_missing_answer():
    assignment = _open()
    errors = responses.submit(assignment)
    assert all(set(error) == {"path", "message"} for error in errors)
    expected = {
        "ratings.criteria.functionality",
        "ratings.comment",
        *(f"reqs.items.{item_id}.verdict" for item_id in _requirement_ids(assignment)),
        "sec.checklist.xss.outcome",
        "sec.checklist.idor.outcome",
        *(f"sec.findings.{finding_id}" for finding_id in _finding_ids(assignment)),
        "tasks.scenarios.create.outcome",
        "tasks.scenarios.share.outcome",
        *(f"sus.answers.{question_id}" for question_id in SUS_QUESTION_IDS),
        "extra.answers.liked",
        "extra.answers.tags",
        "extra.answers.age",
        "extra.answers.attn",
        "bugs.none_found.single",
    }
    assert _paths(errors) == expected
    assert len(_finding_ids(assignment)) == 2
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.IN_PROGRESS
    assert assignment.submitted_at is None


def test_rating_rules():
    assignment = _open(n_findings=0)
    assert _submit_errors(assignment, {}) == set()
    assert _submit_errors(assignment, {"ratings": {"criteria": {"functionality": "na"}, "comment": "ok"}}) == set()
    assert _submit_errors(assignment, {"ratings": {"criteria": {"functionality": 8}, "comment": "ok"}}) == {
        "ratings.criteria.functionality",
    }
    # "looks" is optional, on 0..10 in steps of 2, without "can't judge".
    bad_looks = {"ratings": {"criteria": {"functionality": 4, "looks": 3}, "comment": "ok"}}
    assert _submit_errors(assignment, bad_looks) == {"ratings.criteria.looks"}
    na_looks = {"ratings": {"criteria": {"functionality": 4, "looks": "na"}, "comment": "ok"}}
    assert _submit_errors(assignment, na_looks) == {"ratings.criteria.looks"}
    good_looks = {"ratings": {"criteria": {"functionality": 4, "looks": 10}, "comment": "ok"}}
    assert _submit_errors(assignment, good_looks) == set()
    assert _submit_errors(assignment, {"ratings": {"criteria": {"functionality": 4}, "comment": "   "}}) == {
        "ratings.comment",
    }


def test_requirements_rules():
    assignment = _open(n_findings=0)
    first, second, *rest = _requirement_ids(assignment)
    items = {item_id: {"verdict": "met"} for item_id in rest}
    partial = {
        "reqs": {"items": {**items, first: {"verdict": "partial"}, second: {"verdict": "blocked", "note": "Crashes"}}},
    }
    assert _submit_errors(assignment, partial) == {f"reqs.items.{first}.note"}
    cannot = {"reqs": {"items": {**items, first: {"verdict": "cannot_test", "note": "No time"}}}}
    assert _submit_errors(assignment, cannot) == {f"reqs.items.{second}.verdict"}
    blocked = {"reqs": {"items": {**items, first: {"verdict": "met"}, second: {"verdict": "blocked"}}}}
    assert _submit_errors(assignment, blocked) == {f"reqs.items.{second}.note"}


def test_security_rules_need_checklist_outcomes_and_finding_verdicts():
    assignment = _open(n_findings=3)
    answers = _complete_answers(assignment)
    answers["sec"] = {"checklist": {"xss": {"outcome": "vulnerable", "note": "Stored XSS in comments"}}}
    _save_all(assignment, answers)
    finding_ids = _finding_ids(assignment)
    responses.set_finding_verdict(assignment, finding_ids[0], "fp", "Not reachable")
    errors = _paths(responses.validate_submission(_reload(assignment)))
    assert errors == {"sec.checklist.idor.outcome", f"sec.findings.{finding_ids[1]}", f"sec.findings.{finding_ids[2]}"}


def test_scenario_rules():
    assignment = _open(n_findings=0)
    no_ease = {"tasks": {"scenarios": {"create": {"outcome": "partial"}, "share": {"outcome": "not_attempted"}}}}
    assert _submit_errors(assignment, no_ease) == {"tasks.scenarios.create.ease"}
    bad = {
        "tasks": {
            "scenarios": {
                "create": {"outcome": "failure", "ease": 9, "seconds": -3},
                "share": {"outcome": "success", "ease": 1, "seconds": None},
            },
        },
    }
    assert _submit_errors(assignment, bad) == {"tasks.scenarios.create.ease", "tasks.scenarios.create.seconds"}
    missing = {"tasks": {"scenarios": {"create": {"outcome": "success", "ease": 7}}}}
    assert _submit_errors(assignment, missing) == {"tasks.scenarios.share.outcome"}


def test_questionnaire_rules():
    assignment = _open(n_findings=0)
    sus = dict.fromkeys(SUS_QUESTION_IDS, 3)
    sus["sus_4"] = 6
    del sus["sus_9"]
    assert _submit_errors(assignment, {"sus": {"answers": sus}}) == {"sus.answers.sus_4", "sus.answers.sus_9"}
    extra = {"extra": {"answers": {"liked": "maybe", "tags": [], "age": 12, "attn": "b", "notes": "fine"}}}
    assert _submit_errors(assignment, extra) == {"extra.answers.liked", "extra.answers.tags", "extra.answers.age"}


def test_bugs_rules_per_slot_in_single_mode():
    assignment = _open(n_findings=0)
    assert _submit_errors(assignment, {"bugs": {"none_found": {"single": False}}}) == {"bugs.none_found.single"}
    ObservationFactory(assignment=assignment, task_app=assignment.item.app_a, kind=Observation.Kind.BUG)
    assert _submit_errors(assignment, {"bugs": {"none_found": {}}}) == set()


def test_bugs_min_reports_are_enforced_per_app_in_pairwise_mode():
    task = make_open_task(n_apps=2, mode="pairwise", design=PAIRWISE_DESIGN)
    assignment = workbench_assignment(task)
    context.open_assignment(assignment)
    assignment = _reload(assignment)
    slot_a = context.app_for_slot(assignment, "A")
    answers = {"criteria": {"ux": "A", "works": "tie"}, "overall": "B", "rationale": "B was faster"}
    responses.save_response(assignment, "pref", answers)
    responses.save_response(assignment, "bugs", {"none_found": {"A": True, "B": True}})
    assert _paths(responses.validate_submission(assignment)) == {"bugs.reports.A", "bugs.reports.B"}
    ObservationFactory(assignment=assignment, task_app=slot_a, kind=Observation.Kind.BUG)
    ObservationFactory(assignment=assignment, task_app=slot_a, kind=Observation.Kind.NOTE)
    assert _paths(responses.validate_submission(assignment)) == {"bugs.reports.B"}
    ObservationFactory(assignment=assignment, task_app=context.app_for_slot(assignment, "B"), kind="bug")
    assert responses.submit(assignment) == []


def test_pairwise_rules_for_both_scales():
    task = make_open_task(n_apps=2, mode="pairwise", design=PAIRWISE_DESIGN)
    assignment = workbench_assignment(task)
    context.open_assignment(assignment)
    assignment = _reload(assignment)
    responses.save_response(assignment, "pref", {"criteria": {"ux": "A"}, "rationale": " "})
    errors = _paths(responses.validate_submission(assignment))
    assert {"pref.criteria.works", "pref.overall", "pref.rationale"} <= errors

    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.save_response(assignment, "pref", {"criteria": {"ux": "left"}, "overall": 1})
    assert excinfo.value.code == "invalid_answers"
    assert _paths(excinfo.value.errors) == {"pref.criteria.ux", "pref.overall"}

    design = {**PAIRWISE_DESIGN, "blocks": [{**PAIRWISE_DESIGN["blocks"][0], "scale": "five_point"}]}
    five = make_open_task(n_apps=2, mode="pairwise", design=design)
    other = workbench_assignment(five)
    context.open_assignment(other)
    other = _reload(other)
    responses.save_response(other, "pref", {"criteria": {"ux": -2, "works": 0}, "overall": 2.0, "rationale": "x"})
    assert responses.validate_submission(other) == []
    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.save_response(other, "pref", {"criteria": {"ux": 3, "works": "A"}})
    assert _paths(excinfo.value.errors) == {"pref.criteria.ux", "pref.criteria.works"}


# --- autosave --------------------------------------------------------------------


def test_save_response_cleans_and_opens_the_assignment():
    task = make_open_task(n_apps=1, design=workbench_design())
    assignment = workbench_assignment(task)
    assert assignment.status == Assignment.Status.ASSIGNED
    response = responses.save_response(
        assignment,
        "ratings",
        {"criteria": {"functionality": 9, "ghost": 3, "looks": None}, "comment": "draft", "extra_key": 1},
    )
    assert response.answers == {"criteria": {"functionality": 9}, "comment": "draft"}  # range checked at submit
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.IN_PROGRESS
    assert assignment.started_at is not None
    assert assignment.last_activity_at is not None
    assert assignment.context["blocks"]

    again = responses.save_response(_reload(assignment), "ratings", {"criteria": {"functionality": 4}})
    assert again.pk == response.pk
    assert Response.objects.get(pk=response.pk).answers == {"criteria": {"functionality": 4}}
    reqs = responses.save_response(_reload(assignment), "reqs", {"items": {"unknown_id": {"verdict": "met"}}})
    assert reqs.answers == {"items": {}}


def test_save_response_errors_leave_nothing_behind():
    task = make_open_task(n_apps=1, design=workbench_design())
    assignment = workbench_assignment(task)

    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.save_response(assignment, "nope", {})
    assert excinfo.value.status == 404

    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.save_response(assignment, "ratings", ["list"])
    assert (excinfo.value.status, excinfo.value.code) == (400, "invalid_answers")

    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.save_response(assignment, "ratings", {"comment": "x" * (70 * 1024)})
    assert excinfo.value.code == "invalid_answers"

    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.save_response(assignment, "ratings", {"criteria": {"functionality": "great"}, "comment": 5})
    assert excinfo.value.status == 400
    assert excinfo.value.errors == [
        {"path": "ratings.criteria.functionality", "message": 'Must be a whole number on the scale or "na"'},
        {"path": "ratings.comment", "message": "Must be text"},
    ]
    with pytest.raises(HumanEvalServiceError):
        responses.save_response(assignment, "tasks", {"scenarios": {"create": {"outcome": "won", "ease": "x"}}})
    with pytest.raises(HumanEvalServiceError):
        responses.save_response(assignment, "bugs", {"none_found": {"single": "yes"}})

    # The refused saves rolled back: no response rows, the assignment was not even opened.
    assert not Response.objects.filter(assignment=assignment).exists()
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.ASSIGNED
    assert assignment.context == {}


def test_mutations_on_read_only_assignments_are_409():
    assignment = _open(n_findings=1)
    finding_id = _finding_ids(assignment)[0]
    EvalTask.objects.filter(pk=assignment.task_id).update(status=EvalTask.Status.PAUSED)
    paused = _reload(assignment)
    for call in (
        lambda: responses.save_response(paused, "ratings", {}),
        lambda: responses.submit(paused),
        lambda: responses.skip(paused, "other", ""),
        lambda: responses.set_finding_verdict(paused, finding_id, "tp", ""),
    ):
        with pytest.raises(HumanEvalServiceError) as excinfo:
            call()
        assert (excinfo.value.status, excinfo.value.code) == (409, "read_only")

    EvalTask.objects.filter(pk=assignment.task_id).update(status=EvalTask.Status.OPEN)
    Assignment.objects.filter(pk=assignment.pk).update(status=Assignment.Status.SUBMITTED)
    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.save_response(_reload(assignment), "ratings", {})
    assert excinfo.value.code == "read_only"
    assert not Response.objects.filter(assignment=assignment).exists()
    assert not FindingVerdict.objects.filter(assignment=assignment).exists()


# --- skip ---------------------------------------------------------------------------


def test_skip_records_the_reason_and_hosting_state():
    task = make_open_task(n_apps=1, design=workbench_design())
    assignment = workbench_assignment(task)
    responses.skip(assignment, "app_broken", "Blank page")
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.SKIPPED
    assert assignment.skip_reason == "app_broken"
    assert assignment.skip_note == "Blank page"
    assert set(assignment.context["skip_hosting"]) == {"single"}
    assert set(assignment.context["skip_hosting"]["single"]) == {"state", "error"}
    assert assignment.context["blocks"]  # the context was built too


def test_skip_refusals():
    task = make_open_task(n_apps=1, design=workbench_design(), settings={"allocation": {"allow_skip": False}})
    assignment = workbench_assignment(task)
    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.skip(assignment, "other", "")
    assert (excinfo.value.status, excinfo.value.code) == (409, "skip_not_allowed")

    allowed = make_open_task(n_apps=1, design=workbench_design())
    other = workbench_assignment(allowed)
    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.skip(other, "bored", "x" * 6000)
    assert excinfo.value.status == 400
    assert _paths(excinfo.value.errors) == {"reason", "note"}
    other.refresh_from_db()
    assert other.status == Assignment.Status.ASSIGNED
    assert other.context == {}


# --- finding verdicts ---------------------------------------------------------------


def test_set_finding_verdict_upserts_with_the_context_snapshot():
    assignment = _open(n_findings=2)
    finding_id = _finding_ids(assignment)[0]
    record = responses.set_finding_verdict(assignment, finding_id, "fp", "Dead code")
    assert record.finding_key == finding_id
    assert str(record.finding_id) == finding_id
    assert record.task_app_id == assignment.item.app_a_id
    entry = next(e for e in assignment.context["blocks"]["sec"]["findings"]["single"] if e["id"] == finding_id)
    assert record.finding_snapshot == entry
    assert "severity" not in record.finding_snapshot
    again = responses.set_finding_verdict(assignment, finding_id, "tp", "")
    assert again.pk == record.pk
    assert FindingVerdict.objects.get(pk=record.pk).verdict == "tp"
    assert responses.verdicts_payload(assignment) == {finding_id: {"verdict": "tp", "note": ""}}


def test_set_finding_verdict_refuses_findings_outside_the_context():
    assignment = _open(n_findings=1)
    stray = workbench_findings(assignment.item.app_a.job, "heval-stray", 1)[0]  # drawn after the sample
    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.set_finding_verdict(assignment, str(stray.id), "tp", "")
    assert excinfo.value.status == 404
    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.set_finding_verdict(assignment, _finding_ids(assignment)[0], "maybe", "")
    assert excinfo.value.status == 400
    assert not FindingVerdict.objects.filter(assignment=assignment).exists()


# --- next ---------------------------------------------------------------------------


def test_next_after_reports_only_an_assignment_to_go_to(monkeypatch):
    assignment = _open(n_findings=0)
    participant = assignment.participant
    monkeypatch.setattr(allocation, "next_assignment", lambda p: (assignment, "assigned"))
    assert responses.next_after(participant) == {"assignment_id": str(assignment.id), "reason": "assigned"}
    monkeypatch.setattr(allocation, "next_assignment", lambda p: (None, "limit_reached"))
    assert responses.next_after(participant) is None

    def broken(p):
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(allocation, "next_assignment", broken)
    assert responses.next_after(participant) is None


# --- regressions: lock order (C41), NUL characters (C22) ---------------------------------


def _locked_tables(queries: list[dict]) -> list[str]:
    """The table each ``SELECT ... FOR UPDATE`` of a captured query list reads from, in order."""
    tables = []
    for query in queries:
        sql = query["sql"]
        if "FOR UPDATE" in sql:
            for model in (EvalTask, Assignment):
                if f'FROM "{model._meta.db_table}"' in sql:
                    tables.append(model._meta.db_table)
    return tables


@pytest.mark.parametrize("action", ["submit", "skip"])
def test_submit_and_skip_lock_the_task_row_before_the_assignment_row(action):
    """next_after() locks the task row after a submit/skip; the task row must come first (as everywhere)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    assignment = _open(n_findings=0)
    with CaptureQueriesContext(connection) as captured:
        if action == "submit":
            responses.submit(assignment)
        else:
            responses.skip(assignment, "other", "")
    tables = _locked_tables(captured.captured_queries)
    assert tables[:2] == [EvalTask._meta.db_table, Assignment._meta.db_table], tables


def test_submit_of_an_assignment_whose_task_is_gone_is_404():
    assignment = _open(n_findings=0)
    EvalTask.objects.filter(pk=assignment.task_id).delete()
    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.submit(assignment)
    assert excinfo.value.status == 404


def test_question_answers_with_a_nul_character_are_invalid():
    answers = {"name": "Ann\x00", "bio": "x\x00y", "role": "dev\x00", "langs": ["py", "js\x00"]}
    for strict in (False, True):
        cleaned, errors = responses.validate_question_answers(QUESTIONS, answers, strict=strict)
        assert {"name", "bio", "role", "langs"} <= _paths(errors)
        assert not {"name", "bio", "role", "langs"} & set(cleaned)


def test_autosave_skip_and_verdict_notes_refuse_nul_characters():
    """C22: Postgres text / JSONB cannot store U+0000 — a 400 and nothing written, never a 500."""
    assignment = _open(n_findings=1)
    for block_id, answers, path in (
        ("ratings", {"criteria": {"functionality": 5}, "comment": "ok\x00"}, "ratings.comment"),
        ("reqs", {"items": {_requirement_ids(assignment)[0]: {"verdict": "met", "note": "\x00"}}}, None),
        ("extra", {"answers": {"notes": "a\x00b", "liked": "yes\x00"}}, "extra.answers.notes"),
        ("tasks", {"scenarios": {"create": {"outcome": "success", "note": "\x00"}}}, "tasks.scenarios.create.note"),
    ):
        with pytest.raises(HumanEvalServiceError) as excinfo:
            responses.save_response(assignment, block_id, answers)
        assert (excinfo.value.status, excinfo.value.code) == (400, "invalid_answers")
        if path is not None:
            assert path in _paths(excinfo.value.errors)
    assert not Response.objects.filter(assignment=assignment).exists()

    finding_id = _finding_ids(assignment)[0]
    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.set_finding_verdict(assignment, finding_id, "tp", "see\x00here")
    assert excinfo.value.status == 400
    assert _paths(excinfo.value.errors) == {"note"}
    assert not FindingVerdict.objects.filter(assignment=assignment).exists()

    with pytest.raises(HumanEvalServiceError) as excinfo:
        responses.skip(assignment, "other", "blank\x00page")
    assert excinfo.value.status == 400
    assert _paths(excinfo.value.errors) == {"note"}
    assert _reload(assignment).status == Assignment.Status.IN_PROGRESS
