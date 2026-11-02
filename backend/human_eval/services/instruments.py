"""Standard usability / workload questionnaires: fixed questions and scoring.

A questionnaire block with ``instrument`` ``sus``, ``umux_lite`` or ``nasa_tlx``
has no questions of its own: the server supplies the questions below (frozen
into ``Assignment.context.blocks[id].questions`` when the assignment is opened)
and :func:`score` turns one participant's answers into the instrument score.

- **SUS** (Brooke, 1996): ten items, 1..5 from "Strongly disagree" to "Strongly
  agree"; score = (sum over odd items of (x - 1) + sum over even items of
  (5 - x)) x 2.5, range 0..100.
- **UMUX-Lite** (Lewis, Utesch & Maher, 2013): two items, 1..7; raw score =
  ((a - 1) + (b - 1)) / 12 x 100, range 0..100 (the regression-adjusted variant is
  not applied).
- **NASA-TLX, raw** (Hart & Staveland, 1988; Hart, 2006): six subscales, 0..100 in
  steps of 5 (stored as integers); performance is anchored "Perfect" (0) to
  "Failure" (100); raw TLX = mean of the six, range 0..100 (no pairwise weights).

Question dicts are in the normalized design format (``services/design.py``).
"""

from __future__ import annotations

import copy
from typing import Any

_AGREE_5 = {
    "min": 1,
    "max": 5,
    "step": 1,
    "min_label": "Strongly disagree",
    "max_label": "Strongly agree",
    "labels": {},
}
_AGREE_7 = {
    "min": 1,
    "max": 7,
    "step": 1,
    "min_label": "Strongly disagree",
    "max_label": "Strongly agree",
    "labels": {},
}
_TLX = {"min": 0, "max": 100, "step": 5, "min_label": "Very low", "max_label": "Very high", "labels": {}}
_TLX_PERFORMANCE = {"min": 0, "max": 100, "step": 5, "min_label": "Perfect", "max_label": "Failure", "labels": {}}


def _scale_question(question_id: str, label: str, scale: dict[str, Any], help_text: str = "") -> dict[str, Any]:
    return {
        "id": question_id,
        "kind": "scale",
        "label": label,
        "help": help_text,
        "required": True,
        "scale": copy.deepcopy(scale),
    }


_SUS_ITEMS = (
    "I think that I would like to use this system frequently.",
    "I found the system unnecessarily complex.",
    "I thought the system was easy to use.",
    "I think that I would need the support of a technical person to be able to use this system.",
    "I found the various functions in this system were well integrated.",
    "I thought there was too much inconsistency in this system.",
    "I would imagine that most people would learn to use this system very quickly.",
    "I found the system very cumbersome to use.",
    "I felt very confident using the system.",
    "I needed to learn a lot of things before I could get going with this system.",
)

_UMUX_LITE_ITEMS = (
    "This system's capabilities meet my requirements.",
    "This system is easy to use.",
)

_TLX_ITEMS = (
    ("tlx_mental", "Mental demand: How mentally demanding was the task?", _TLX),
    ("tlx_physical", "Physical demand: How physically demanding was the task?", _TLX),
    ("tlx_temporal", "Temporal demand: How hurried or rushed was the pace of the task?", _TLX),
    (
        "tlx_performance",
        "Performance: How successful were you in accomplishing what you were asked to do?",
        _TLX_PERFORMANCE,
    ),
    ("tlx_effort", "Effort: How hard did you have to work to accomplish your level of performance?", _TLX),
    ("tlx_frustration", "Frustration: How insecure, discouraged, irritated, stressed, and annoyed were you?", _TLX),
)

SUS_QUESTION_IDS = tuple(f"sus_{number}" for number in range(1, 11))
UMUX_LITE_QUESTION_IDS = ("umux_lite_1", "umux_lite_2")
NASA_TLX_QUESTION_IDS = tuple(item[0] for item in _TLX_ITEMS)

#: Instrument key → name, citation, score range and the fixed questions.
INSTRUMENTS: dict[str, dict[str, Any]] = {
    "sus": {
        "name": "System Usability Scale (SUS)",
        "citation": "Brooke, J. (1996). SUS: A 'quick and dirty' usability scale.",
        "score_min": 0,
        "score_max": 100,
        "questions": [
            _scale_question(question_id, text, _AGREE_5)
            for question_id, text in zip(SUS_QUESTION_IDS, _SUS_ITEMS, strict=True)
        ],
    },
    "umux_lite": {
        "name": "UMUX-Lite",
        "citation": "Lewis, J. R., Utesch, B. S., & Maher, D. E. (2013). UMUX-LITE: When there's no time for the SUS.",
        "score_min": 0,
        "score_max": 100,
        "questions": [
            _scale_question(question_id, text, _AGREE_7)
            for question_id, text in zip(UMUX_LITE_QUESTION_IDS, _UMUX_LITE_ITEMS, strict=True)
        ],
    },
    "nasa_tlx": {
        "name": "NASA Task Load Index (raw TLX)",
        "citation": "Hart, S. G., & Staveland, L. E. (1988); Hart, S. G. (2006). NASA-Task Load Index (NASA-TLX); "
        "20 years later.",
        "score_min": 0,
        "score_max": 100,
        "questions": [_scale_question(question_id, text, scale) for question_id, text, scale in _TLX_ITEMS],
    },
}


def instrument_questions(instrument: str) -> list[dict[str, Any]]:
    """A fresh copy of an instrument's fixed questions ([] for ``custom`` or unknown keys)."""
    entry = INSTRUMENTS.get(instrument)
    return copy.deepcopy(entry["questions"]) if entry else []


def environment_instruments() -> dict[str, dict[str, Any]]:
    """``HumanEvalEnvironment.instruments``: ``{key: {name, questions}}`` exactly as the TS type."""
    return {
        key: {"name": entry["name"], "questions": copy.deepcopy(entry["questions"])}
        for key, entry in INSTRUMENTS.items()
    }


def _value(answers: dict[str, Any], question: dict[str, Any]) -> int | None:
    """The answer to one scale question as an int on its grid, else None."""
    value = answers.get(question["id"])
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        return None
    scale = question["scale"]
    low, high, step = scale["min"], scale["max"], scale.get("step") or 1
    if value < low or value > high or (value - low) % step:
        return None
    return value


def score(instrument: str, answers: dict[str, Any] | None) -> float | None:
    """The instrument score (0..100) of one complete response, or None.

    ``answers`` maps question id → value; a ``QuestionnaireAnswers`` wrapper
    (``{"answers": {...}}``) is accepted too. Any missing, non-integer, out-of-range or
    off-grid item makes the response unscorable (None): there is no imputation.
    """
    entry = INSTRUMENTS.get(instrument)
    if entry is None or not isinstance(answers, dict):
        return None
    inner = answers.get("answers")
    if isinstance(inner, dict):
        answers = inner
    values: list[int] = []
    for question in entry["questions"]:
        value = _value(answers, question)
        if value is None:
            return None
        values.append(value)
    if instrument == "sus":
        odd = sum(value - 1 for value in values[0::2])
        even = sum(5 - value for value in values[1::2])
        return (odd + even) * 2.5
    if instrument == "umux_lite":
        return (values[0] - 1 + values[1] - 1) / 12 * 100
    if instrument == "nasa_tlx":
        return sum(values) / len(values)
    return None
