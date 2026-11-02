"""Standard instruments: fixed questions and scoring on known inputs."""

from __future__ import annotations

import pytest

from backend.human_eval.services import design
from backend.human_eval.services import instruments
from backend.human_eval.services.checklists import DEFAULT_SECURITY_CHECKLIST
from backend.human_eval.services.checklists import OBSERVATION_CATEGORIES
from backend.human_eval.services.checklists import OBSERVATION_CATEGORY_VALUES
from backend.human_eval.services.checklists import PLATFORM_CREDENTIALS_NOTE

pytestmark = pytest.mark.django_db


def _sus(values):
    return dict(zip(instruments.SUS_QUESTION_IDS, values, strict=True))


def _tlx(values):
    return dict(zip(instruments.NASA_TLX_QUESTION_IDS, values, strict=True))


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([5, 1, 5, 1, 5, 1, 5, 1, 5, 1], 100.0),
        ([1, 5, 1, 5, 1, 5, 1, 5, 1, 5], 0.0),
        ([3] * 10, 50.0),
        # odd: 4,5,4,5,4 -> 3+4+3+4+3 = 17; even: 1,1,2,1,1 -> 4+4+3+4+4 = 19; (17 + 19) x 2.5
        ([4, 1, 5, 1, 4, 2, 5, 1, 4, 1], 90.0),
        # odd: 2,3,2,3,2 -> 1+2+1+2+1 = 7; even: 4,3,4,3,4 -> 1+2+1+2+1 = 7
        ([2, 4, 3, 3, 2, 4, 3, 3, 2, 4], 35.0),
    ],
)
def test_sus_score(values, expected):
    assert instruments.score("sus", _sus(values)) == expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [((7, 7), 100.0), ((1, 1), 0.0), ((4, 7), 75.0), ((4, 4), 50.0)],
)
def test_umux_lite_score(values, expected):
    answers = dict(zip(instruments.UMUX_LITE_QUESTION_IDS, values, strict=True))
    assert instruments.score("umux_lite", answers) == pytest.approx(expected)


def test_raw_tlx_is_the_mean_of_six_subscales():
    assert instruments.score("nasa_tlx", _tlx([0, 0, 0, 0, 0, 0])) == 0.0
    assert instruments.score("nasa_tlx", _tlx([100] * 6)) == 100.0
    assert instruments.score("nasa_tlx", _tlx([50, 10, 30, 20, 40, 60])) == 35.0


def test_questionnaire_answers_wrapper_is_accepted():
    assert instruments.score("sus", {"answers": _sus([3] * 10)}) == 50.0


def test_incomplete_or_invalid_answers_are_unscorable():
    answers = _sus([3] * 10)
    del answers["sus_10"]
    assert instruments.score("sus", answers) is None
    assert instruments.score("sus", _sus([3] * 9 + [6])) is None  # out of range
    assert instruments.score("sus", _sus([3] * 9 + ["3"])) is None  # not an int
    assert instruments.score("sus", _sus([3] * 9 + [True])) is None  # booleans are not scale values
    assert instruments.score("nasa_tlx", _tlx([50, 10, 30, 20, 40, 62])) is None  # off the step-5 grid
    assert instruments.score("sus", _sus([3.0] * 10)) == 50.0  # integral floats from JSON are fine
    assert instruments.score("custom", {"a": 1}) is None
    assert instruments.score("sus", None) is None


def test_instrument_questions_are_valid_design_questions():
    for key, entry in instruments.INSTRUMENTS.items():
        block = {
            "id": "q",
            "type": "questionnaire",
            "title": "Q",
            "instrument": "custom",
            "questions": entry["questions"],
        }
        normalized, errors = design.validate_design("single", {"blocks": [block]})
        assert errors == [], key
        assert normalized["blocks"][0]["questions"] == entry["questions"], key
    assert len(instruments.INSTRUMENTS["sus"]["questions"]) == 10
    assert len(instruments.INSTRUMENTS["umux_lite"]["questions"]) == 2
    tlx = instruments.INSTRUMENTS["nasa_tlx"]["questions"]
    assert len(tlx) == 6
    assert all(question["scale"]["step"] == 5 for question in tlx)
    performance = next(question for question in tlx if question["id"] == "tlx_performance")
    assert (performance["scale"]["min_label"], performance["scale"]["max_label"]) == ("Perfect", "Failure")


def test_instrument_questions_and_environment_payload_are_copies():
    questions = instruments.instrument_questions("sus")
    questions[0]["label"] = "changed"
    assert instruments.INSTRUMENTS["sus"]["questions"][0]["label"] != "changed"
    assert instruments.instrument_questions("custom") == []
    env = instruments.environment_instruments()
    assert set(env) == {"sus", "umux_lite", "nasa_tlx"}
    assert set(env["sus"]) == {"name", "questions"}


def test_default_security_checklist_has_ten_valid_items():
    items = DEFAULT_SECURITY_CHECKLIST
    assert len(items) == 10
    assert len({item["id"] for item in items}) == 10
    block = {"id": "s", "type": "security", "title": "S", "checklist": items}
    normalized, errors = design.validate_design("single", {"blocks": [block]})
    assert errors == []
    assert normalized["blocks"][0]["checklist"] == items
    for item in items:
        assert item["category"].startswith("A0") or item["category"].startswith("A10")
        assert len(item["help"]) > 100
    # admin/admin is a platform artifact, stated where testers look for weak credentials
    flagged = [item["id"] for item in items if PLATFORM_CREDENTIALS_NOTE in item["help"]]
    assert {"authentication_session", "misconfiguration"} <= set(flagged)


def test_observation_categories():
    assert [option["value"] for option in OBSERVATION_CATEGORIES["bug"]] == [
        "functional",
        "ui",
        "crash",
        "performance",
        "data",
        "other",
    ]
    vulnerability = [option["value"] for option in OBSERVATION_CATEGORIES["vulnerability"]]
    assert vulnerability == [f"A{n:02d}" for n in range(1, 11)] + ["other"]
    assert OBSERVATION_CATEGORIES["note"] == []
    assert "A03" in OBSERVATION_CATEGORY_VALUES["vulnerability"]
