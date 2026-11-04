"""services/export.py: CSV contents and safety, codebook, bundle (spec §5.15, R1, R2, R3.15)."""

from __future__ import annotations

import copy
import csv
import datetime as dt
import io
import json
import zipfile

import pytest

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.services import analysis
from backend.human_eval.services import export
from backend.human_eval.services import instruments
from backend.human_eval.services import presets
from backend.human_eval.services.checklists import PLATFORM_CREDENTIALS_NOTE
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import AttachmentFactory
from backend.human_eval.tests.factories import EvalInviteFactory
from backend.human_eval.tests.factories import FindingVerdictFactory
from backend.human_eval.tests.factories import HttpExchangeFactory
from backend.human_eval.tests.factories import ObservationFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import ResponseFactory
from backend.human_eval.tests.factories import make_open_task
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

S = Assignment.Status
NOW = dt.datetime(2026, 11, 1, 10, 0, tzinfo=dt.UTC)
SUS_ANSWERS = {f"sus_{n}": (4 if n % 2 else 2) for n in range(1, 11)}  # SUS = (5 x 3 + 5 x 3) x 2.5 = 75
PII = {
    "email": "hidden.person@example.org",
    "hint": "ZQ7X",
    "completion": "K7QM4XT9",
    "device": "d" * 64,
}

SCENARIOS_BLOCK = {
    "id": "scenarios",
    "type": "scenarios",
    "title": "Usability tasks",
    "scenarios": [
        {"id": "login", "title": "Sign in", "description": "Register and sign in."},
        {"id": "create", "title": "Create an item", "description": "Create one item."},
    ],
    "timed": True,
    "ease_question": True,
}
FEEDBACK_BLOCK = {
    "id": "feedback",
    "type": "questionnaire",
    "title": "Feedback",
    "instrument": "custom",
    "questions": [
        {
            "id": "liked",
            "kind": "multi",
            "label": "What did you like?",
            "required": False,
            "options": [{"value": "ui", "label": "The UI"}, {"value": "speed", "label": "Speed"}],
        },
        {"id": "score", "kind": "number", "label": "Score from -5 to 5", "required": False, "min": -5, "max": 5},
    ],
}


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def _header(text: str) -> list[str]:
    return next(csv.reader(io.StringIO(text)))


def _context(review_finding_id: str = "101") -> dict:
    return {
        "blocks": {
            "requirements": {
                "requirements": {
                    "single": [
                        {"id": "f_0000000001", "source": "frontend", "text": "Users can register and log in"},
                        {"id": "ep_000000002", "source": "api", "text": "GET /api/items — List the user's items"},
                    ],
                },
            },
            "security": {
                "findings": {"single": [{"id": review_finding_id, "tool": "bandit", "tool_severity": "HIGH"}]},
            },
            "sus": {"questions": instruments.instrument_questions("sus")},
        },
        "hosting": {
            "single": {
                "container_id": "5b0c8c1e-0000-4000-8000-000000000001",
                "container_name": "llm-abc12345",
                "image": "backend/llm-abc12345:latest",
                "reset_at": "2026-11-01T09:58:00+00:00",
                "prior_users": 0,
                "first_running_at": "2026-11-01T10:00:05+00:00",
            },
        },
        "instructions_sha256": "ab" * 32,
    }


@pytest.fixture
def study():
    """A single-mode task with every block type, 4 participants + a preview and rows of every kind."""
    preset = presets.get_preset("full-evaluation")
    design = copy.deepcopy(preset["design"])
    design["blocks"] += [copy.deepcopy(SCENARIOS_BLOCK), copy.deepcopy(FEEDBACK_BLOCK)]
    design["analysis"]["exclusion_rules"] = {"speeding": True, "attention_failed": True}
    task = make_open_task(n_apps=4, design=design, settings=preset["settings"])  # speeding below 900 s
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    invite = EvalInviteFactory(task=task, label="Class A")
    pre = {"programming_years": 5, "web_dev_experience": 4, "security_testing_experience": 2}
    p1 = ParticipantFactory(
        task=task,
        number=1,
        invite=invite,
        device_hash=PII["device"],
        pre_answers=pre,
        post_answers={"attention_check": "somewhat_disagree", "final_comments": "=cmd|' /C calc'!A0"},
        consent_at=NOW,
    )
    p2 = ParticipantFactory(
        task=task,
        number=2,
        kind=Participant.Kind.CODE,
        status=Participant.Status.COMPLETED,
        access_code_hint=PII["hint"],
        completion_code=PII["completion"],
        excluded=True,
        exclusion_reason="duplicate account",
        excluded_at=NOW,
    )
    p3 = ParticipantFactory(
        task=task,
        number=3,
        kind=Participant.Kind.ACCOUNT,
        user=UserFactory(email=PII["email"]),
        device_hash=PII["device"],
    )
    p4 = ParticipantFactory(task=task, number=4, post_answers={"attention_check": "neutral"})
    preview = ParticipantFactory(task=task, number=5, kind=Participant.Kind.PREVIEW, user=task.created_by)

    def submitted(participant, item, **extra):
        values = {"submitted_at": NOW, "started_at": NOW - dt.timedelta(minutes=30)}
        values.update({"active_seconds": 1000, "wall_seconds": 1200, **extra})
        return AssignmentFactory(task=task, participant=participant, item=item, status=S.SUBMITTED, **values)

    views = {"single": {"viewed_seconds": 400, "first_running_at": "2026-11-01T10:00:05+00:00"}}
    a1 = submitted(p1, items[0], position=1, context=_context(), app_views=views)
    a2 = AssignmentFactory(task=task, participant=p1, item=items[1], status=S.IN_PROGRESS, position=2)
    a3 = submitted(p1, items[2], position=3, excluded=True, exclusion_reason="saw the model name", excluded_at=NOW)
    a4 = submitted(p2, items[0], position=1, app_views=views)
    a5 = submitted(p3, items[0], position=1, active_seconds=10, wall_seconds=20, app_views=views)
    a6 = submitted(p4, items[1], position=1, app_views=views)
    a7 = AssignmentFactory(
        task=task,
        participant=p4,
        item=items[2],
        status=S.SKIPPED,
        position=2,
        skip_reason="app_broken",
        skip_note="blank page",
        context={"blocks": {}, "hosting": {}, "skip_hosting": {"single": {"state": "failed", "error": "exit 1"}}},
    )
    a8 = submitted(preview, items[0], position=1)

    ratings = {
        "criteria": {
            "functionality": 6,
            "completeness": "na",
            "usability": 5,
            "visual_design": 4,
            "robustness": 3,
            "overall": 5,
        },
        "comment": '=HYPERLINK("http://evil.example")',
    }
    ResponseFactory(assignment=a1, block_id="ratings", answers=ratings)
    ResponseFactory(
        assignment=a1,
        block_id="requirements",
        answers={
            "items": {
                "f_0000000001": {"verdict": "met"},
                "ep_000000002": {"verdict": "cannot_test", "note": "endpoint missing"},
                "zz_stale": {"verdict": "partial"},
            },
        },
    )
    ResponseFactory(
        assignment=a1,
        block_id="security",
        answers={
            "checklist": {
                "access_control": {"outcome": "vulnerable", "note": "IDOR on /api/items/2"},
                "xss": {"outcome": "not_vulnerable"},
                "csrf": {"outcome": "not_tested"},
            },
        },
    )
    ResponseFactory(assignment=a1, block_id="bugs", answers={"none_found": {"single": True}})
    ResponseFactory(assignment=a1, block_id="sus", answers={"answers": SUS_ANSWERS})
    ResponseFactory(
        assignment=a1,
        block_id="scenarios",
        answers={
            "scenarios": {
                "login": {"outcome": "success", "seconds": 42, "ease": 6},
                "create": {"outcome": "partial", "seconds": None, "ease": 3, "note": "+1 slow"},
            },
        },
    )
    ResponseFactory(assignment=a1, block_id="feedback", answers={"answers": {"liked": ["ui", "speed"], "score": -3}})
    ResponseFactory(assignment=a2, block_id="ratings", answers={"criteria": {"functionality": 2}})
    ResponseFactory(assignment=a4, block_id="ratings", answers={"criteria": {"functionality": 7}})
    ResponseFactory(assignment=a8, block_id="ratings", answers={"criteria": {"functionality": 1}})

    bug = ObservationFactory(
        assignment=a1,
        task_app=items[0].app_a,
        kind=Observation.Kind.BUG,
        title="-1+2 crashes the list",
        severity="high",
        block_id="bugs",
    )
    AttachmentFactory(observation=bug)
    ObservationFactory(
        assignment=a1,
        task_app=items[0].app_a,
        kind=Observation.Kind.VULNERABILITY,
        title="-3",
        severity="critical",
        category="A01",
        checklist_item_id="access_control",
    )
    ObservationFactory(assignment=a8, task_app=items[0].app_a, title="preview-only bug")
    snapshot = {
        "id": "101",
        "tool": "bandit",
        "tool_name": "Bandit",
        "tool_severity": "HIGH",
        "category": "security",
        "rule_id": "B105",
        "title": "Hardcoded password",
        "file_path": "backend/app.py",
        "line": 3,
    }
    FindingVerdictFactory(assignment=a1, task_app=items[0].app_a, finding_key="101", finding_snapshot=snapshot)
    FindingVerdictFactory(assignment=a1, task_app=items[0].app_a, finding_key="999", verdict="fp")
    HttpExchangeFactory(
        assignment=a1,
        task_app=items[0].app_a,
        path="/api/items?x=1",
        request_headers={"Authorization": "Bearer abc"},
        response_body="@media print {}",
    )
    HttpExchangeFactory(assignment=a8, task_app=items[0].app_a, path="/preview-only")
    return {
        "task": task,
        "items": items,
        "participants": [p1, p2, p3, p4, preview],
        "assignments": [a1, a2, a3, a4, a5, a6, a7, a8],
    }


@pytest.fixture
def pairwise_study():
    """A pairwise task with an ab_tie block (+ overall), a five-point block and a bugs block; one pair, 2 raters."""
    design = {
        "version": 1,
        "blocks": [
            {
                "id": "pref",
                "type": "pairwise",
                "title": "Which is better?",
                "criteria": [{"id": "functionality", "label": "Works better"}],
                "scale": "ab_tie",
                "overall": True,
                "rationale": "optional",
            },
            {
                "id": "strength",
                "type": "pairwise",
                "title": "How much better?",
                "criteria": [{"id": "usability", "label": "Easier to use"}],
                "scale": "five_point",
                "overall": False,
                "rationale": "off",
            },
            {"id": "bugs", "type": "bugs", "title": "Bugs", "min_reports": 0},
        ],
        "analysis": {"primary": {"block_id": "pref", "metric": "overall"}},
    }
    task = make_open_task(n_apps=2, mode="pairwise", design=design)
    item = EvalItem.objects.get(task=task)
    p1 = ParticipantFactory(task=task, number=1)
    p2 = ParticipantFactory(task=task, number=2)
    straight = AssignmentFactory(task=task, participant=p1, item=item, status=S.SUBMITTED, swapped=False)
    swapped = AssignmentFactory(task=task, participant=p2, item=item, status=S.SUBMITTED, swapped=True)
    ResponseFactory(
        assignment=straight,
        block_id="pref",
        answers={"criteria": {"functionality": "A"}, "overall": "tie", "rationale": "same"},
    )
    ResponseFactory(assignment=straight, block_id="strength", answers={"criteria": {"usability": -2}})
    ResponseFactory(assignment=straight, block_id="bugs", answers={"none_found": {"A": True, "B": False}})
    ResponseFactory(assignment=swapped, block_id="pref", answers={"criteria": {"functionality": "A"}, "overall": "B"})
    ResponseFactory(assignment=swapped, block_id="strength", answers={"criteria": {"usability": 1}})
    ObservationFactory(assignment=straight, task_app=item.app_b, kind=Observation.Kind.BUG)
    return {"task": task, "item": item, "straight": straight, "swapped": swapped}


# ---------------------------------------------------------------------------
# Cells (R3.15)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=SUM(A1:A2)", "'=SUM(A1:A2)"),
        ("+cmd", "'+cmd"),
        ("-1+2", "'-1+2"),
        ("@SUM(1)", "'@SUM(1)"),
        ("\tx", "'\tx"),
        ("\rx", "'\rx"),
        ("-2", "-2"),
        ("+5", "+5"),
        ("-0.5", "-0.5"),
        ("-1e-3", "-1e-3"),
        ("-", "'-"),
        ("-inf", "'-inf"),
        ("plain text", "plain text"),
        (" =not first", " =not first"),
        (-2, "-2"),
        (-0.25, "-0.25"),
        (3, "3"),
        (float("nan"), ""),
        (True, "true"),
        (False, "false"),
        (None, ""),
        ([1, "a"], '[1,"a"]'),
        ({"b": 1, "a": "=x"}, '{"a":"=x","b":1}'),
        (dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=2))), "2026-11-01T10:00:00+00:00"),
    ],
)
def test_csv_cell(value, expected):
    assert export.csv_cell(value) == expected


@pytest.mark.parametrize(
    ("raw", "scale", "swapped", "expected"),
    [
        ("A", "ab_tie", False, 1),
        ("B", "ab_tie", False, -1),
        ("tie", "ab_tie", False, 0),
        ("A", "ab_tie", True, -1),
        ("B", "ab_tie", True, 1),
        (-2, "five_point", False, 2),
        (-2, "five_point", True, -2),
        (1, "five_point", False, -1),
        (0, "five_point", True, 0),
        (3, "five_point", False, None),
        (1.5, "five_point", False, None),
        (True, "five_point", False, None),
        ("A", "five_point", False, None),
        (None, "ab_tie", False, None),
        ("left", "ab_tie", False, None),
    ],
)
def test_canonical_pairwise_value(raw, scale, swapped, expected):
    assert export.canonical_pairwise_value(raw, scale, swapped=swapped) == expected


def test_filenames_use_title_slug():
    task = make_open_task(n_apps=1)
    task.title = "Spring 2026: A/B study!"
    assert export.export_filename(task, "ratings") == "human-eval-spring-2026-ab-study-ratings.csv"
    assert export.export_filename(task, "bundle", "zip") == "human-eval-spring-2026-ab-study-bundle.zip"
    task.title = "!!!"
    assert export.export_filename(task, "apps") == "human-eval-task-apps.csv"


def test_unknown_kind_is_404():
    task = make_open_task(n_apps=1)
    with pytest.raises(export.HumanEvalServiceError) as caught:
        export.csv_text(task, "secrets")
    assert caught.value.status == 404


# ---------------------------------------------------------------------------
# Every kind: rows, flags, preview exclusion, documentation
# ---------------------------------------------------------------------------


def _all_csvs(task) -> dict[str, str]:
    data = export.ExportData(task)
    return {kind: export.csv_text(task, kind, data) for kind in export.EXPORT_KINDS}


def test_every_kind_exports_with_documented_headers(study, pairwise_study):
    for task in (study["task"], pairwise_study["task"]):
        data = export.ExportData(task)
        for kind in export.EXPORT_KINDS:
            text = export.csv_text(task, kind, data)
            header = _header(text)
            assert header == export.TABLES[kind].columns(data)
            assert len(header) == len(set(header)), kind
            undocumented = [column for column in header if not export.describe_column(data, kind, column)]
            assert undocumented == [], f"{kind}.csv: {undocumented}"
            assert "\r\n" not in text.replace('"\r\n', "")  # \n line ends
            assert not text.startswith("﻿")


def test_preview_participants_are_never_exported(study):
    csvs = _all_csvs(study["task"])
    for kind, text in csvs.items():
        assert "P005" not in text, kind
        assert "preview-only" not in text, kind
    preview_assignment = study["assignments"][7]
    assert str(preview_assignment.pk) not in "".join(csvs.values())


def test_exports_never_contain_pii(study):
    csvs = _all_csvs(study["task"])
    invite_token = study["participants"][0].invite.token
    blob = "".join(csvs.values())
    for value in (*PII.values(), invite_token, study["task"].created_by.email):
        assert value not in blob
    participants_header = _header(csvs["participants"])
    for forbidden in ("email", "user_id", "access_code_hint", "device_hash", "completion_code", "ip"):
        assert forbidden not in participants_header


def test_assignments_csv_keeps_every_row_with_exclusion_columns(study):
    rows = {row["assignment_id"]: row for row in _rows(export.csv_text(study["task"], "assignments"))}
    a1, a2, a3, a4, a5, a6, a7, _preview = study["assignments"]
    assert set(rows) == {str(a.pk) for a in (a1, a2, a3, a4, a5, a6, a7)}

    first = rows[str(a1.pk)]
    assert first["participant"] == "P001"
    assert first["participant_kind"] == "invite"
    assert first["assignment_status"] == "submitted"
    assert first["slot"] == "single"
    assert first["app_label"] == study["items"][0].app_a.label
    assert (first["excluded"], first["participant_excluded"], first["rule_excluded"]) == ("false", "false", "false")
    assert first["active_seconds"] == "1000"
    assert first["wall_seconds"] == "1200"
    assert first["observation_count"] == "2"
    assert first["http_request_count"] == "1"
    assert first["instructions_sha256"] == "ab" * 32
    assert first["slot_single_viewed_seconds"] == "400"
    assert first["slot_single_container_name"] == "llm-abc12345"
    assert first["slot_single_image"] == "backend/llm-abc12345:latest"
    assert first["slot_single_prior_users"] == "0"
    assert first["slot_single_reset_at"] == "2026-11-01T09:58:00+00:00"
    assert first["slot_single_bug_reports"] == "1"
    assert first["slot_single_bugs_none_found"] == "true"

    assert rows[str(a2.pk)]["assignment_status"] == "in_progress"
    assert rows[str(a2.pk)]["slot_single_bugs_none_found"] == "false"
    manual = rows[str(a3.pk)]
    assert (manual["excluded"], manual["exclusion_reason"]) == ("true", "saw the model name")
    assert manual["excluded_at"].startswith("2026-11-01T10:00:00")
    assert rows[str(a4.pk)]["participant_excluded"] == "true"

    speeding = rows[str(a5.pk)]
    assert "speeding" in speeding["quality_flags"].split(";")
    assert (speeding["rule_excluded"], speeding["rule_exclusion_rules"]) == ("true", "speeding")
    attention = rows[str(a6.pk)]
    assert (attention["rule_excluded"], attention["rule_exclusion_rules"]) == ("true", "attention_failed")
    assert "attention_failed" in attention["participant_quality_flags"].split(";")

    skipped = rows[str(a7.pk)]
    assert (skipped["skip_reason"], skipped["skip_note"]) == ("app_broken", "blank page")
    assert (skipped["slot_single_skip_hosting_state"], skipped["slot_single_skip_hosting_error"]) == (
        "failed",
        "exit 1",
    )
    # P004 failed the attention check, but rules only act on submitted work
    assert (skipped["rule_excluded"], skipped["rule_exclusion_rules"]) == ("false", "")


def test_rule_excluded_column_reconciles_with_results_rule_exclusions(study):
    """C42: rule_excluded also marks manually excluded rows; results count only the rows rules add."""
    task = study["task"]
    manual = study["assignments"][2]  # a3: manually excluded
    Assignment.objects.filter(pk=manual.pk).update(active_seconds=5, wall_seconds=5)  # ... and speeding
    rows = {row["assignment_id"]: row for row in _rows(export.csv_text(task, "assignments"))}
    flagged = rows[str(manual.pk)]
    assert (flagged["excluded"], flagged["rule_excluded"], flagged["rule_exclusion_rules"]) == (
        "true",
        "true",
        "speeding",
    )
    results = analysis.compute_results(task)
    assert results["rule_exclusions"] == {"speeding": 1, "attention_failed": 1}
    for rule, count in results["rule_exclusions"].items():
        rebuilt = sum(
            1
            for row in rows.values()
            if rule in row["rule_exclusion_rules"].split(";")
            and row["excluded"] == "false"
            and row["participant_excluded"] == "false"
        )
        assert rebuilt == count, rule
    text = export.codebook(export.ExportData(task), generated_at=NOW, results=_results(task))
    assert "also set on manually excluded rows" in text
    assert "counts per rule only the rows a rule removes beyond the manual exclusions" in text


def test_ratings_csv(study):
    rows = _rows(export.csv_text(study["task"], "ratings"))
    a1 = study["assignments"][0]
    first = [row for row in rows if row["assignment_id"] == str(a1.pk)]
    assert [row["criterion_id"] for row in first] == [
        "functionality",
        "completeness",
        "usability",
        "visual_design",
        "robustness",
        "overall",
    ]
    by_criterion = {row["criterion_id"]: row for row in first}
    assert (by_criterion["functionality"]["value"], by_criterion["functionality"]["na"]) == ("6", "false")
    assert (by_criterion["completeness"]["value"], by_criterion["completeness"]["na"]) == ("", "true")
    assert (by_criterion["overall"]["scale_min"], by_criterion["overall"]["scale_max"]) == ("1", "7")
    assert by_criterion["overall"]["comment"] == '\'=HYPERLINK("http://evil.example")'
    # every response row, drafts and excluded participants included; the preview never
    statuses = {(row["participant"], row["assignment_status"]) for row in rows}
    assert statuses == {("P001", "submitted"), ("P001", "in_progress"), ("P002", "submitted")}
    draft = [row for row in rows if row["assignment_status"] == "in_progress"]
    assert {row["criterion_id"]: row["value"] for row in draft}["functionality"] == "2"
    assert {row["criterion_id"]: row["value"] for row in draft}["usability"] == ""


def test_requirements_csv_scores_and_context(study):
    rows = {row["req_id"]: row for row in _rows(export.csv_text(study["task"], "requirements"))}
    assert set(rows) == {"f_0000000001", "ep_000000002", "zz_stale"}
    met = rows["f_0000000001"]
    assert (met["source"], met["verdict"], met["score"], met["score_strict"], met["in_context"]) == (
        "frontend",
        "met",
        "1",
        "1",
        "true",
    )
    cannot = rows["ep_000000002"]
    assert (cannot["score"], cannot["score_strict"], cannot["note"]) == ("", "0", "endpoint missing")
    assert cannot["text"] == "GET /api/items — List the user's items"
    stale = rows["zz_stale"]
    assert (stale["in_context"], stale["score"], stale["source"]) == ("false", "0.5", "")


def test_security_checklist_csv(study):
    rows = _rows(export.csv_text(study["task"], "security_checklist"))
    assert len(rows) == 10  # every checklist item of the block, answered or not
    by_item = {row["checklist_item_id"]: row for row in rows}
    assert {row["item_id"] for row in rows} == {str(study["items"][0].pk)}
    assert (by_item["access_control"]["outcome"], by_item["access_control"]["vulnerable"]) == ("vulnerable", "1")
    assert by_item["access_control"]["note"] == "IDOR on /api/items/2"
    assert by_item["access_control"]["checklist_item_category"].startswith("A01")
    assert (by_item["xss"]["vulnerable"], by_item["csrf"]["vulnerable"]) == ("0", "")
    assert by_item["injection"]["outcome"] == ""


def test_scenarios_and_questionnaires_csv(study):
    scenarios = {row["scenario_id"]: row for row in _rows(export.csv_text(study["task"], "scenarios"))}
    assert (scenarios["login"]["outcome"], scenarios["login"]["success"]) == ("success", "1")
    assert (scenarios["login"]["seconds"], scenarios["login"]["ease"]) == ("42", "6")
    assert (scenarios["create"]["success"], scenarios["create"]["seconds"]) == ("0", "")
    assert scenarios["create"]["note"] == "'+1 slow"

    rows = _rows(export.csv_text(study["task"], "questionnaires"))
    sus = [row for row in rows if row["block_id"] == "sus"]
    assert [row["question_id"] for row in sus] == [f"sus_{n}" for n in range(1, 11)]
    assert {row["instrument_score"] for row in sus} == {"75.0"}
    assert sus[0]["value"] == "4"
    feedback = {row["question_id"]: row for row in rows if row["block_id"] == "feedback"}
    assert feedback["liked"]["value"] == "ui;speed"
    assert feedback["score"]["value"] == "-3"  # a negative number stays numeric
    assert feedback["score"]["instrument"] == "custom"
    assert feedback["score"]["instrument_score"] == ""


def test_observations_verdicts_and_http_log_csv(study):
    observations = {row["kind"]: row for row in _rows(export.csv_text(study["task"], "observations"))}
    assert set(observations) == {"bug", "vulnerability"}
    bug = observations["bug"]
    assert (bug["title"], bug["tester_severity"], bug["attachment_count"]) == ("'-1+2 crashes the list", "high", "1")
    assert (bug["slot"], bug["block_id"]) == ("single", "bugs")
    vulnerability = observations["vulnerability"]
    assert (vulnerability["title"], vulnerability["checklist_item_id"]) == ("-3", "access_control")
    assert "severity" not in [
        c for c in _header(export.csv_text(study["task"], "observations")) if c != "tester_severity"
    ]

    verdicts = {row["finding_key"]: row for row in _rows(export.csv_text(study["task"], "finding_verdicts"))}
    assert (verdicts["101"]["in_context"], verdicts["101"]["verdict"]) == ("true", "tp")
    assert (verdicts["101"]["tool"], verdicts["101"]["tool_severity"], verdicts["101"]["line"]) == (
        "bandit",
        "HIGH",
        "3",
    )
    assert (verdicts["999"]["in_context"], verdicts["999"]["verdict"], verdicts["999"]["tool"]) == ("false", "fp", "")

    (exchange,) = _rows(export.csv_text(study["task"], "http_log"))
    assert (exchange["method"], exchange["path"], exchange["status_code"]) == ("GET", "/api/items?x=1", "200")
    assert json.loads(exchange["request_headers_json"]) == {"Authorization": "Bearer abc"}
    assert exchange["response_body"] == "'@media print {}"


def test_participants_csv_answers_and_attention(study):
    header = _header(export.csv_text(study["task"], "participants"))
    assert header[-6:] == [
        "pre_programming_years",
        "pre_web_dev_experience",
        "pre_security_testing_experience",
        "post_attention_check",
        "post_final_comments",
        "attention_attention_check_passed",
    ]
    rows = {row["participant"]: row for row in _rows(export.csv_text(study["task"], "participants"))}
    assert set(rows) == {"P001", "P002", "P003", "P004"}
    first = rows["P001"]
    assert (first["participant_kind"], first["invite_label"], first["status"]) == ("invite", "Class A", "active")
    assert (first["pre_programming_years"], first["pre_web_dev_experience"]) == ("5", "4")
    assert first["post_final_comments"] == "'=cmd|' /C calc'!A0"
    assert first["attention_attention_check_passed"] == "true"
    assert (first["assignments"], first["submitted"], first["in_progress"]) == ("3", "2", "1")
    assert "possible_duplicate" in first["quality_flags"].split(";")
    assert rows["P004"]["attention_attention_check_passed"] == "false"
    assert "attention_failed" in rows["P004"]["quality_flags"].split(";")
    assert rows["P003"]["attention_attention_check_passed"] == ""
    assert (rows["P002"]["excluded"], rows["P002"]["exclusion_reason"]) == ("true", "duplicate account")
    assert rows["P004"]["skipped"] == "1"


def test_apps_csv_is_the_deblinding_key(study):
    task = study["task"]
    rows = {row["app_label"]: row for row in _rows(export.csv_text(task, "apps"))}
    assert len(rows) == 4
    app = study["items"][0].app_a
    row = rows[app.label]
    snapshot = app.snapshot
    assert row["run_id"] == str(app.job_id)
    assert (row["model_id"], row["model_name"]) == (snapshot["model_id"], snapshot["model_name"])
    assert (row["condition_id"], row["condition_label"]) == (
        str(snapshot["condition_id"]),
        snapshot["condition_label"],
    )
    assert row["spec_content_hash"] == snapshot["spec_content_hash"]
    assert (row["functional_status"], row["functional_pass_rate"]) == (
        snapshot["functional"]["status"],
        repr(snapshot["functional"]["pass_rate"]),
    )
    assert row["conformance_endpoint_checks"] == "6"  # every task endpoint was checked
    assert row["static_findings_loc"] == "120"
    assert (row["study_id"], row["run_kind"], row["harness"]) == (snapshot["study_id"], "app_agentic", "opencode")
    assert row["task_ref"] == snapshot["task_ref"]
    # assignments counted per app (preview excluded): P001, P002, P003 rated this app
    assert (row["assignments"], row["submitted"]) == ("3", "3")
    # no other file names a model
    csvs = _all_csvs(task)
    for kind, text in csvs.items():
        if kind != "apps":
            assert snapshot["model_name"] not in text, kind
            assert snapshot["model_id"] not in text, kind


def test_items_csv_lists_never_rated_items(study):
    items = study["items"]
    EvalItem.objects.filter(pk=items[3].pk).update(is_active=False)
    rows = {row["item_id"]: row for row in _rows(export.csv_text(study["task"], "items"))}
    assert set(rows) == {str(item.pk) for item in items}
    first = rows[str(items[0].pk)]
    # P001 + P002 (participant excluded) + P003 (rule: speeding) submitted; only P001 is included
    assert (first["submitted"], first["submitted_included"], first["assignments"]) == ("3", "1", "3")
    assert first["target"] == "2"
    third = rows[str(items[2].pk)]
    assert (third["skipped"], third["unavailable_skips"], third["submitted_included"]) == ("1", "1", "0")
    never = rows[str(items[3].pk)]
    assert (never["assignments"], never["is_active"], never["target"]) == ("0", "false", "")


def test_responses_csv_keeps_raw_answers(study):
    rows = _rows(export.csv_text(study["task"], "responses"))
    a1 = study["assignments"][0]
    first = [row for row in rows if row["assignment_id"] == str(a1.pk)]
    # design block order, not alphabetical
    assert [row["block_id"] for row in first] == [
        "requirements",
        "ratings",
        "security",
        "bugs",
        "sus",
        "scenarios",
        "feedback",
    ]
    feedback = first[-1]
    assert feedback["block_type"] == "questionnaire"
    assert json.loads(feedback["answers_json"]) == {"answers": {"liked": ["ui", "speed"], "score": -3}}


def test_pairwise_csv_uses_canonical_orientation(pairwise_study):
    task, item = pairwise_study["task"], pairwise_study["item"]
    text = export.csv_text(task, "pairwise")
    rows = _rows(text)
    label_a, label_b = item.app_a.label, item.app_b.label
    straight = {
        (row["block_id"], row["criterion_id"]): row
        for row in rows
        if row["assignment_id"] == str(pairwise_study["straight"].pk)
    }
    swapped = {
        (row["block_id"], row["criterion_id"]): row
        for row in rows
        if row["assignment_id"] == str(pairwise_study["swapped"].pk)
    }
    row = straight["pref", "functionality"]
    assert (row["app_a_label"], row["app_b_label"], row["swapped"]) == (label_a, label_b, "false")
    assert (row["displayed_left_label"], row["displayed_right_label"]) == (label_a, label_b)
    assert (row["raw_value"], row["canonical_value"], row["winner_label"]) == ("A", "1", label_a)
    assert (straight["pref", "overall"]["canonical_value"], straight["pref", "overall"]["winner_label"]) == (
        "0",
        "tie",
    )
    assert straight["pref", "overall"]["rationale"] == "same"
    strong = straight["strength", "usability"]
    assert (strong["scale"], strong["raw_value"], strong["canonical_value"], strong["winner_label"]) == (
        "five_point",
        "-2",
        "2",
        label_a,
    )

    row = swapped["pref", "functionality"]
    assert (row["swapped"], row["displayed_left_label"]) == ("true", label_b)
    assert (row["raw_value"], row["canonical_value"], row["winner_label"]) == ("A", "-1", label_b)
    assert (swapped["pref", "overall"]["canonical_value"], swapped["pref", "overall"]["winner_label"]) == (
        "1",
        label_a,
    )
    assert (swapped["strength", "usability"]["canonical_value"], swapped["strength", "usability"]["winner_label"]) == (
        "1",
        label_a,
    )
    assert "'-" not in text  # negative numbers are numbers, never escaped


def test_pairwise_assignments_csv_slots(pairwise_study):
    task, item = pairwise_study["task"], pairwise_study["item"]
    rows = {row["assignment_id"]: row for row in _rows(export.csv_text(task, "assignments"))}
    straight = rows[str(pairwise_study["straight"].pk)]
    assert (straight["slot_A_bugs_none_found"], straight["slot_B_bugs_none_found"]) == ("true", "false")
    assert (straight["slot_A_bug_reports"], straight["slot_B_bug_reports"]) == ("0", "1")  # bug on app_b = slot B
    assert straight["displayed_left_label"] == item.app_a.label
    swapped = rows[str(pairwise_study["swapped"].pk)]
    assert (swapped["displayed_left_label"], swapped["displayed_right_label"]) == (item.app_b.label, item.app_a.label)
    assert "slot_single_viewed_seconds" not in straight
    header = _header(export.csv_text(task, "items"))
    assert "app_a_label" in header
    assert "app_b_label" in header


# ---------------------------------------------------------------------------
# Codebook and bundle
# ---------------------------------------------------------------------------


def _results(task) -> dict:
    return {"task_id": str(task.pk), "generated_at": "2026-11-02T10:00:00+00:00", "group_by": "condition"}


def test_codebook_documents_the_study(study):
    task = study["task"]
    data = export.ExportData(task)
    text = export.codebook(data, generated_at=NOW, results={**_results(task), "provisional": True})
    assert task.design_hash in text
    assert str(task.seed) in text
    assert PLATFORM_CREDENTIALS_NOTE in text
    assert "apps.csv" in text
    assert "de-blinding key" in text
    assert "| `speeding` | yes |" in text
    assert "| `attention_failed` | yes |" in text
    assert "| `straightlining` | no |" in text
    assert "900 s" in text  # the speeding threshold of this task
    assert "Primary metric: `ratings` / `composite`" in text
    for kind in export.EXPORT_KINDS:
        assert f"### {kind}.csv" in text
        for column in export.TABLES[kind].columns(data):
            assert f"| `{column}` |" in text
    assert "results are provisional" in text
    assert "provisional = true" in text


def test_codebook_lists_participants_deleted_after_opening(study):
    """C45: a deletion logged in protocol_log is reported as such, without the researcher's address."""
    task = study["task"]
    task.protocol_log = [
        {
            "at": "2026-11-01T11:00:00+00:00",
            "by": "researcher@example.org",
            "field": "instructions",
            "old_sha256": "a" * 64,
            "new_sha256": "b" * 64,
        },
        {
            "at": "2026-11-01T12:00:00+00:00",
            "by": "researcher@example.org",
            "field": f"{analysis.PROTOCOL_PARTICIPANT_DELETED}:7",
            "old_sha256": "",
            "new_sha256": "",
        },
    ]
    task.save(update_fields=["protocol_log"])
    text = export.codebook(export.ExportData(task), generated_at=NOW, results=_results(task))
    section = text.split("## Changes after opening", 1)[1].split("\n## ", 1)[0]
    assert "| 2026-11-01T11:00:00+00:00 | instructions |" in section
    assert "Participants deleted after opening" in section
    assert "| 2026-11-01T12:00:00+00:00 | P007 |" in section
    assert "participant_deleted" not in section.split("Participants deleted after opening", 1)[0]
    assert "researcher@example.org" not in text


def test_bundle_contents(study):
    task = study["task"]
    results = _results(task)
    handle = export.bundle_file(task, results)
    with zipfile.ZipFile(handle) as archive:
        names = archive.namelist()
        root = f"human-eval-{export.export_slug(task)}"
        assert sorted(names) == sorted(
            [f"{root}/{kind}.csv" for kind in export.EXPORT_KINDS]
            + [f"{root}/{name}" for name in ("design.json", "settings.json", "results.json", "codebook.md")],
        )
        assert json.loads(archive.read(f"{root}/design.json")) == task.design
        settings = json.loads(archive.read(f"{root}/settings.json"))
        assert settings["quality"]["min_seconds_per_item"] == 900
        assert json.loads(archive.read(f"{root}/results.json")) == results
        assert archive.read(f"{root}/ratings.csv").decode("utf-8") == export.csv_text(task, "ratings")
        codebook = archive.read(f"{root}/codebook.md").decode("utf-8")
        assert "computed at 2026-11-02T10:00:00+00:00" in codebook
        blob = b"".join(archive.read(name) for name in names).decode("utf-8")
    for value in (*PII.values(), study["participants"][0].invite.token):
        assert value not in blob
    assert "P005" not in blob
