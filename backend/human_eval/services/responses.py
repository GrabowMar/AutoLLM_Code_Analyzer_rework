"""Responses and submission (owner B2): autosave, strict validation per block type, submit, skip, verdicts.

Answers are stored per design block in :class:`~backend.human_eval.models.Response`
(TS ``BlockAnswers``). Two validation levels share one cleaner per block type:

- **autosave** (:func:`save_response`, ``strict=False``): drafts. Unknown keys and
  ids are dropped; values are type-checked (wrong type, unknown enum value or text
  over :data:`MAX_TEXT` → 400 ``invalid_answers``); ranges and completeness are not
  checked yet, so a half-filled form always saves.
- **submit** (:func:`submit`, ``strict=True``): every rule of spec §5.8 (+ R1):
  required criteria/questions answered and on their scale grid, every requirement
  item of the frozen context has a verdict (and a note unless ``met`` when the
  block says so), every checklist item an outcome, every sampled finding a
  :class:`~backend.human_eval.models.FindingVerdict`, every scenario an outcome (and
  an ease rating unless not attempted), bug reports per app slot (``none_found`` per
  slot when ``min_reports`` is 0), pairwise preferences and rationale.

Errors are ``[{"path": "<block_id>.<field path>", "message"}]`` where the field path
mirrors the answers JSON (``ratings.criteria.usability``, ``reqs.items.b_0123456789.note``,
``sec.findings.<finding id>``, ``bugs.none_found.A``); the view answers 400 ``incomplete``.

Every mutation locks the assignment row, refuses read-only assignments (409
``read_only``), validates before writing and runs in ``transaction.atomic()`` so a
refusal leaves nothing behind (spec R3.5). ``submit`` and ``skip`` lock the task row
first: the view then allocates the next item (:func:`next_after`, which locks the task
row), and every other path locks the task before assignment rows, so the order is the
same everywhere (no lock cycle with ``next`` or a task deletion).

Text never contains U+0000 (Postgres text and JSONB cannot store it): any answer
string, note, comment or rationale with one is a 400 like any other invalid value.
"""

from __future__ import annotations

import json
import logging
import math
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalTask
from backend.human_eval.models import FindingVerdict
from backend.human_eval.models import Observation
from backend.human_eval.models import Response
from backend.human_eval.services import access
from backend.human_eval.services import context as context_service
from backend.human_eval.services import design as design_service
from backend.human_eval.services.errors import conflict
from backend.human_eval.services.errors import invalid
from backend.human_eval.services.errors import not_found
from backend.human_eval.services.instruments import instrument_questions

if TYPE_CHECKING:
    from backend.human_eval.models import Participant

logger = logging.getLogger(__name__)

MAX_ANSWERS_BYTES = 64 * 1024
MAX_TEXT = 5000
MAX_OPTION_VALUE = design_service.MAX_OPTION_VALUE

REQUIREMENT_VERDICTS = ("met", "partial", "not_met", "blocked", "cannot_test")
CHECKLIST_OUTCOMES = ("vulnerable", "not_vulnerable", "not_tested")
SCENARIO_OUTCOMES = ("success", "partial", "failure", "not_attempted")
SKIP_REASONS = ("app_broken", "platform_unavailable", "cannot_evaluate", "conflict_of_interest", "other")
FINDING_VERDICTS = ("tp", "fp", "unsure")
AB_TIE_VALUES = ("A", "B", "tie")
FIVE_POINT_VALUES = (-2, -1, 0, 1, 2)
EASE_MIN = 1
EASE_MAX = 7
NUL = "\x00"
NUL_MESSAGE = "Must not contain the NUL character (U+0000)"


class _Errors:
    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix
        self.items: list[dict[str, str]] = []

    def add(self, path: str, message: str) -> None:
        full = ".".join(part for part in (self.prefix, path) if part)
        self.items.append({"path": full, "message": message})


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------


def _whole(value: Any) -> int | None:
    """``value`` as an int when it is a whole number (not a bool), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, list) and not value)


def _text(errors: _Errors, path: str, value: Any, *, limit: int = MAX_TEXT) -> str | None:
    """Cleaned text or None (absent / invalid; invalid adds an error)."""
    if value is None:
        return None
    if not isinstance(value, str):
        errors.add(path, "Must be text")
        return None
    if len(value) > limit:
        errors.add(path, f"At most {limit} characters")
        return None
    if NUL in value:
        errors.add(path, NUL_MESSAGE)
        return None
    return value


def _object(errors: _Errors, path: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.add(path, "Must be an object")
        return {}
    return value


def _range_message(scale: dict[str, Any]) -> str:
    step = int(scale.get("step") or 1)
    suffix = f" in steps of {step}" if step != 1 else ""
    return f"Must be a whole number from {scale['min']} to {scale['max']}{suffix}"


# ---------------------------------------------------------------------------
# Questions (questionnaire blocks, pre/post questionnaires)
# ---------------------------------------------------------------------------


_Checked = tuple[Any, "str | None"]


def _options(question: dict[str, Any]) -> list[str]:
    return [option["value"] for option in question.get("options") or []]


def _text_answer(question: dict[str, Any], value: Any, strict: bool) -> _Checked:
    if not isinstance(value, str):
        return None, "Must be text"
    if len(value) > MAX_TEXT:
        return None, f"At most {MAX_TEXT} characters"
    if NUL in value:
        return None, NUL_MESSAGE
    return value, None


def _option_value(value: Any) -> bool:
    """A string that can be an option value (stored as given while drafting, so never with U+0000)."""
    return isinstance(value, str) and len(value) <= MAX_OPTION_VALUE and NUL not in value


def _choice_answer(question: dict[str, Any], value: Any, strict: bool) -> _Checked:
    if not _option_value(value):
        return None, "Must be one of the options"
    if strict and value not in _options(question):
        return None, "Pick one of the options"
    return value, None


def _multi_answer(question: dict[str, Any], value: Any, strict: bool) -> _Checked:
    if not isinstance(value, list) or not all(_option_value(v) for v in value):
        return None, "Must be a list of options"
    picked = list(dict.fromkeys(value))
    options = _options(question)
    if strict and any(v not in options for v in picked):
        return None, "Pick only listed options"
    return picked, None


def _scale_answer(question: dict[str, Any], value: Any, strict: bool) -> _Checked:
    number = _whole(value)
    if number is None:
        return None, "Must be a whole number"
    scale = question.get("scale") or {}
    if strict and scale and number not in design_service.scale_values(scale):
        return None, _range_message(scale)
    return number, None


def _number_answer(question: dict[str, Any], value: Any, strict: bool) -> _Checked:
    number = _number(value)
    if number is None:
        return None, "Must be a number"
    low, high = question.get("min"), question.get("max")
    message = None
    if strict and low is not None and number < low:
        message = f"Must be at least {low}"
    elif strict and high is not None and number > high:
        message = f"Must be at most {high}"
    return (None, message) if message else (number, None)


_QUESTION_CHECKS = {
    "text": _text_answer,
    "textarea": _text_answer,
    "single": _choice_answer,
    "attention": _choice_answer,
    "multi": _multi_answer,
    "scale": _scale_answer,
    "number": _number_answer,
}


def _question_value(question: dict[str, Any], value: Any, *, strict: bool) -> _Checked:
    """``(cleaned value, error message or None)`` for one non-missing answer."""
    check = _QUESTION_CHECKS.get(question.get("kind", ""))
    if check is None:
        return None, "Unsupported question"
    return check(question, value, strict)


def validate_question_answers(
    questions: list[dict[str, Any]],
    answers: Any,
    *,
    strict: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Clean ``answers`` against design questions: ``(cleaned, errors)``; ``strict`` requires required ones.

    ``answers`` maps question id → TS ``QuestionAnswer``. Unknown ids are dropped; ``None``, blank
    text and empty lists count as unanswered (and are dropped). Error paths are the question ids
    (callers prefix them). Loose mode checks types and lengths only; strict mode also checks
    options, scale grids, number bounds and required questions.
    """
    errors = _Errors()
    if answers is None:
        answers = {}
    if not isinstance(answers, dict):
        return {}, [{"path": "", "message": "Answers must be an object"}]
    cleaned: dict[str, Any] = {}
    for question in questions or []:
        question_id = question["id"]
        value = answers.get(question_id)
        if _missing(value):
            if strict and question.get("required"):
                errors.add(question_id, "This question is required")
            continue
        clean, message = _question_value(question, value, strict=strict)
        if message is not None:
            errors.add(question_id, message)
            continue
        cleaned[question_id] = clean
    return cleaned, errors.items


# ---------------------------------------------------------------------------
# Block cleaners
# ---------------------------------------------------------------------------


class _Work:
    """What the cleaners need to know about the assignment (lazy, cached)."""

    def __init__(self, assignment: Assignment) -> None:
        self.assignment = assignment
        self.context = assignment.context if isinstance(assignment.context, dict) else {}
        self.slots = context_service.slot_apps(assignment)
        self._bug_counts: dict[Any, int] | None = None

    def block_context(self, block_id: str) -> dict[str, Any]:
        return (self.context.get("blocks") or {}).get(block_id) or {}

    def requirement_ids(self, block_id: str) -> list[str]:
        ids: list[str] = []
        for items in (self.block_context(block_id).get("requirements") or {}).values():
            ids.extend(item["id"] for item in items or [] if isinstance(item, dict) and item.get("id"))
        return list(dict.fromkeys(ids))

    def questions(self, block: dict[str, Any]) -> list[dict[str, Any]]:
        if block.get("instrument") == "custom":
            return block.get("questions") or []
        frozen = self.block_context(block["id"]).get("questions")
        return frozen if isinstance(frozen, list) and frozen else instrument_questions(block.get("instrument", ""))

    def bug_counts(self) -> dict[Any, int]:
        if self._bug_counts is None:
            counts: dict[Any, int] = {}
            for app_id in Observation.objects.filter(
                assignment=self.assignment,
                kind=Observation.Kind.BUG,
            ).values_list("task_app_id", flat=True):
                counts[app_id] = counts.get(app_id, 0) + 1
            self._bug_counts = counts
        return self._bug_counts


def _clean_rating(block: dict[str, Any], answers: dict[str, Any], errors: _Errors, work: _Work, *, strict: bool):
    raw = _object(errors, "criteria", answers.get("criteria"))
    criteria: dict[str, Any] = {}
    for criterion in block.get("criteria") or []:
        criterion_id = criterion["id"]
        path = f"criteria.{criterion_id}"
        value = raw.get(criterion_id)
        if value is None:
            if strict and criterion.get("required", True):
                errors.add(path, "Rate this criterion")
            continue
        if value == "na":
            if strict and not criterion.get("allow_na"):
                errors.add(path, '"Can\'t judge" is not offered for this criterion')
                continue
            criteria[criterion_id] = "na"
            continue
        number = _whole(value)
        if number is None:
            errors.add(path, 'Must be a whole number on the scale or "na"')
            continue
        if strict and number not in design_service.scale_values(criterion["scale"]):
            errors.add(path, _range_message(criterion["scale"]))
            continue
        criteria[criterion_id] = number
    cleaned: dict[str, Any] = {"criteria": criteria}
    mode = block.get("comment", "optional")
    if mode != "off":
        comment = _text(errors, "comment", answers.get("comment"))
        if comment is not None:
            cleaned["comment"] = comment
        if strict and mode == "required" and not (comment or "").strip():
            errors.add("comment", "A comment is required")
    return cleaned


def _clean_requirements(block: dict[str, Any], answers: dict[str, Any], errors: _Errors, work: _Work, *, strict: bool):
    raw = _object(errors, "items", answers.get("items"))
    note_mode = block.get("note", "optional")
    items: dict[str, Any] = {}
    for item_id in work.requirement_ids(block["id"]):
        path = f"items.{item_id}"
        entry = raw.get(item_id)
        if entry is None:
            if strict:
                errors.add(f"{path}.verdict", "Choose a verdict")
            continue
        if not isinstance(entry, dict):
            errors.add(path, "Must be an object")
            continue
        clean: dict[str, Any] = {}
        verdict = entry.get("verdict")
        if verdict is not None:
            if verdict in REQUIREMENT_VERDICTS:
                clean["verdict"] = verdict
            else:
                errors.add(f"{path}.verdict", "Unknown verdict")
        note = _text(errors, f"{path}.note", entry.get("note")) if note_mode != "off" else None
        if note is not None:
            clean["note"] = note
        if strict:
            if verdict is None:
                errors.add(f"{path}.verdict", "Choose a verdict")
            elif note_mode == "required_unless_met" and verdict != "met" and not (note or "").strip():
                errors.add(f"{path}.note", "Explain this verdict in a note")
        if clean:
            items[item_id] = clean
    return {"items": items}


def _clean_security(block: dict[str, Any], answers: dict[str, Any], errors: _Errors, work: _Work, *, strict: bool):
    raw = _object(errors, "checklist", answers.get("checklist"))
    checklist: dict[str, Any] = {}
    for item in block.get("checklist") or []:
        item_id = item["id"]
        path = f"checklist.{item_id}"
        entry = raw.get(item_id)
        if entry is None:
            if strict:
                errors.add(f"{path}.outcome", "Choose an outcome")
            continue
        if not isinstance(entry, dict):
            errors.add(path, "Must be an object")
            continue
        clean: dict[str, Any] = {}
        outcome = entry.get("outcome")
        if outcome is not None:
            if outcome in CHECKLIST_OUTCOMES:
                clean["outcome"] = outcome
            else:
                errors.add(f"{path}.outcome", "Unknown outcome")
        note = _text(errors, f"{path}.note", entry.get("note"))
        if note is not None:
            clean["note"] = note
        if strict and outcome is None:
            errors.add(f"{path}.outcome", "Choose an outcome")
        if clean:
            checklist[item_id] = clean
    if strict:
        finding_ids = context_service.context_finding_ids(work.assignment, block["id"])
        if finding_ids:
            reviewed = set(
                FindingVerdict.objects.filter(assignment=work.assignment, finding_key__in=finding_ids).values_list(
                    "finding_key",
                    flat=True,
                ),
            )
            for finding_id in finding_ids:
                if finding_id not in reviewed:
                    errors.add(f"findings.{finding_id}", "Review this finding (true/false positive or unsure)")
    return {"checklist": checklist}


def _clean_scenarios(block: dict[str, Any], answers: dict[str, Any], errors: _Errors, work: _Work, *, strict: bool):
    raw = _object(errors, "scenarios", answers.get("scenarios"))
    timed = bool(block.get("timed", True))
    ease_question = bool(block.get("ease_question", True))
    scenarios: dict[str, Any] = {}
    for scenario in block.get("scenarios") or []:
        scenario_id = scenario["id"]
        path = f"scenarios.{scenario_id}"
        entry = raw.get(scenario_id)
        if entry is None:
            if strict:
                errors.add(f"{path}.outcome", "Choose an outcome")
            continue
        if not isinstance(entry, dict):
            errors.add(path, "Must be an object")
            continue
        clean: dict[str, Any] = {}
        outcome = entry.get("outcome")
        if outcome is not None:
            if outcome in SCENARIO_OUTCOMES:
                clean["outcome"] = outcome
            else:
                errors.add(f"{path}.outcome", "Unknown outcome")
        seconds = entry.get("seconds")
        if timed and seconds is not None:
            number = _number(seconds)
            if number is None:
                errors.add(f"{path}.seconds", "Must be a number of seconds")
            elif strict and number < 0:
                errors.add(f"{path}.seconds", "Must be 0 or more")
            else:
                clean["seconds"] = number
        ease = entry.get("ease")
        if ease_question and ease is not None:
            number = _whole(ease)
            if number is None:
                errors.add(f"{path}.ease", "Must be a whole number")
            elif strict and not EASE_MIN <= number <= EASE_MAX:
                errors.add(f"{path}.ease", f"Must be from {EASE_MIN} to {EASE_MAX}")
            else:
                clean["ease"] = number
        note = _text(errors, f"{path}.note", entry.get("note"))
        if note is not None:
            clean["note"] = note
        if strict:
            if outcome is None:
                errors.add(f"{path}.outcome", "Choose an outcome")
            elif ease_question and outcome != "not_attempted" and clean.get("ease") is None and ease is None:
                errors.add(f"{path}.ease", f"Rate how easy this was ({EASE_MIN}-{EASE_MAX})")
        if clean:
            scenarios[scenario_id] = clean
    return {"scenarios": scenarios}


def _clean_questionnaire(
    block: dict[str, Any],
    answers: dict[str, Any],
    errors: _Errors,
    work: _Work,
    *,
    strict: bool,
):
    raw = answers.get("answers")
    if raw is not None and not isinstance(raw, dict):
        errors.add("answers", "Must be an object")
        raw = {}
    cleaned, question_errors = validate_question_answers(work.questions(block), raw or {}, strict=strict)
    for error in question_errors:
        errors.add(f"answers.{error['path']}" if error["path"] else "answers", error["message"])
    return {"answers": cleaned}


def _slot_name(slot: str) -> str:
    return "this app" if slot == "single" else f"app {slot}"


def _clean_bugs(block: dict[str, Any], answers: dict[str, Any], errors: _Errors, work: _Work, *, strict: bool):
    raw = _object(errors, "none_found", answers.get("none_found"))
    none_found: dict[str, bool] = {}
    for slot, _app in work.slots:
        value = raw.get(slot)
        if value is None:
            continue
        if not isinstance(value, bool):
            errors.add(f"none_found.{slot}", "Must be true or false")
            continue
        none_found[slot] = value
    if strict:
        minimum = int(block.get("min_reports") or 0)
        counts = work.bug_counts()
        for slot, app in work.slots:
            reported = counts.get(app.pk, 0)
            if minimum > 0 and reported < minimum:
                noun = "bug" if minimum == 1 else "bugs"
                errors.add(f"reports.{slot}", f"Report at least {minimum} {noun} for {_slot_name(slot)}")
            elif minimum == 0 and reported == 0 and not none_found.get(slot):
                errors.add(f"none_found.{slot}", f"Report a bug for {_slot_name(slot)} or confirm that you found none")
    return {"none_found": none_found}


def _pairwise_value(scale: str, value: Any) -> tuple[Any, str | None]:
    if scale == "five_point":
        number = _whole(value)
        if number is None or number not in FIVE_POINT_VALUES:
            return None, "Must be a whole number from -2 to 2"
        return number, None
    if value not in AB_TIE_VALUES:
        return None, 'Must be "A", "B" or "tie"'
    return value, None


def _clean_pairwise(block: dict[str, Any], answers: dict[str, Any], errors: _Errors, work: _Work, *, strict: bool):
    scale = block.get("scale", "ab_tie")
    raw = _object(errors, "criteria", answers.get("criteria"))
    criteria: dict[str, Any] = {}
    for criterion in block.get("criteria") or []:
        criterion_id = criterion["id"]
        path = f"criteria.{criterion_id}"
        value = raw.get(criterion_id)
        if value is None:
            if strict:
                errors.add(path, "Choose a preference")
            continue
        clean, message = _pairwise_value(scale, value)
        if message is not None:
            errors.add(path, message)
            continue
        criteria[criterion_id] = clean
    cleaned: dict[str, Any] = {"criteria": criteria}
    if block.get("overall", True):
        overall = answers.get("overall")
        if overall is None:
            if strict:
                errors.add("overall", "Choose an overall preference")
        else:
            clean, message = _pairwise_value(scale, overall)
            if message is not None:
                errors.add("overall", message)
            else:
                cleaned["overall"] = clean
    mode = block.get("rationale", "optional")
    if mode != "off":
        rationale = _text(errors, "rationale", answers.get("rationale"))
        if rationale is not None:
            cleaned["rationale"] = rationale
        if strict and mode == "required" and not (rationale or "").strip():
            errors.add("rationale", "Explain your preference")
    return cleaned


_CLEANERS = {
    "rating": _clean_rating,
    "requirements": _clean_requirements,
    "security": _clean_security,
    "scenarios": _clean_scenarios,
    "questionnaire": _clean_questionnaire,
    "bugs": _clean_bugs,
    "pairwise": _clean_pairwise,
}


def clean_block_answers(
    assignment: Assignment,
    block: dict[str, Any],
    answers: Any,
    *,
    strict: bool,
    work: _Work | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """``(cleaned answers, errors)`` of one block (error paths prefixed with the block id)."""
    errors = _Errors(block["id"])
    if answers is None:
        answers = {}
    if not isinstance(answers, dict):
        errors.add("", "Answers must be an object")
        return {}, errors.items
    cleaner = _CLEANERS.get(block.get("type", ""))
    if cleaner is None:
        return {}, errors.items
    cleaned = cleaner(block, answers, errors, work or _Work(assignment), strict=strict)
    return cleaned, errors.items


def validate_submission(assignment: Assignment) -> list[dict[str, str]]:
    """Every strict error of the assignment's saved answers, in design order (empty = complete)."""
    stored = {response.block_id: response.answers for response in Response.objects.filter(assignment=assignment)}
    work = _Work(assignment)
    errors: list[dict[str, str]] = []
    for block in (assignment.task.design or {}).get("blocks") or []:
        _, block_errors = clean_block_answers(assignment, block, stored.get(block["id"]), strict=True, work=work)
        errors.extend(block_errors)
    return errors


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def _lock(assignment: Assignment) -> Assignment:
    return (
        Assignment.objects.select_for_update(of=("self",))
        .select_related("task", "participant", "item", "item__app_a", "item__app_b")
        .get(pk=assignment.pk)
    )


def _lock_task(assignment: Assignment) -> None:
    """Lock the assignment's task row (before the assignment row: the lock order of the module doc).

    404 when the task is gone (deleted while the request waited for its lock).
    """
    if not list(EvalTask.objects.select_for_update().filter(pk=assignment.task_id).values_list("pk", flat=True)):
        msg = "Assignment not found"
        raise not_found(msg)


def _sync(target: Assignment, source: Assignment) -> None:
    for field in ("status", "started_at", "last_activity_at", "submitted_at", "skip_reason", "skip_note", "context"):
        setattr(target, field, getattr(source, field))


def save_response(assignment: Assignment, block_id: str, answers: Any) -> Response:
    """Autosave one block's answers (loose type checks, unknown keys dropped, ≤ 64 KB).

    404 for a block the design does not have, 409 ``read_only``, 400 ``invalid_answers``.
    Opens the assignment if needed and records activity.
    """
    block = design_service.block_by_id(assignment.task.design or {}, block_id)
    if block is None:
        msg = "Block not found"
        raise not_found(msg)
    with transaction.atomic():
        locked = _lock(assignment)
        access.ensure_writable(locked)
        if not isinstance(answers, dict):
            msg = "Answers must be an object"
            raise invalid(msg, [{"path": block_id, "message": msg}], code="invalid_answers")
        if len(json.dumps(answers, ensure_ascii=False, default=str).encode("utf-8")) > MAX_ANSWERS_BYTES:
            msg = f"Answers are too large (at most {MAX_ANSWERS_BYTES // 1024} KB)"
            raise invalid(msg, [{"path": block_id, "message": msg}], code="invalid_answers")
        context_service.open_assignment(locked)
        cleaned, errors = clean_block_answers(locked, block, answers, strict=False)
        if errors:
            msg = "Some answers are not valid"
            raise invalid(msg, errors, code="invalid_answers")
        response, _ = Response.objects.update_or_create(
            assignment=locked,
            block_id=block_id,
            defaults={"answers": cleaned},
        )
        now = timezone.now()
        Assignment.objects.filter(pk=locked.pk).update(last_activity_at=now)
        locked.last_activity_at = now
    _sync(assignment, locked)
    return response


def submit(assignment: Assignment) -> list[dict[str, Any]]:
    """Validate every block strictly and submit; returns the errors (empty list = submitted).

    409 ``read_only`` when the assignment cannot change. Nothing but the first-open bookkeeping is
    written when there are errors.
    """
    with transaction.atomic():
        _lock_task(assignment)
        locked = _lock(assignment)
        access.ensure_writable(locked)
        context_service.open_assignment(locked)
        errors = validate_submission(locked)
        if not errors:
            now = timezone.now()
            locked.status = Assignment.Status.SUBMITTED
            locked.submitted_at = now
            locked.last_activity_at = now
            locked.save(update_fields=["status", "submitted_at", "last_activity_at"])
    _sync(assignment, locked)
    return errors


def _hosting_state(task_app: Any, assignment: Assignment) -> dict[str, str]:
    """The app's hosting state and error as this participant sees it (``context.skip_hosting[slot]``)."""
    from backend.human_eval.services import hosting

    try:
        with transaction.atomic():  # a savepoint: a failure here must not break the skip's transaction
            info = hosting.participant_hosting_info(task_app, assignment=assignment) or {}
    except Exception:  # the skip must not fail because the hosting state is unreadable
        logger.exception("Could not read hosting state of task app %s", task_app.pk)
        return {"state": "unavailable", "error": "hosting state unavailable"}
    return {"state": str(info.get("state") or "idle"), "error": str(info.get("error") or "")}


def skip(assignment: Assignment, reason: str, note: str) -> None:
    """Skip the assignment with a TS ``SkipReason`` (records ``context.skip_hosting``).

    409 ``read_only`` / ``skip_not_allowed`` (``allocation.allow_skip`` off), 400 for an unknown
    reason or an over-long note. ``context.skip_hosting[slot] = {state, error}`` keeps the app's
    hosting state at skip time so app defects and platform problems can be told apart.
    """
    with transaction.atomic():
        _lock_task(assignment)
        locked = _lock(assignment)
        access.ensure_writable(locked)
        if not design_service.merged_settings(locked.task.settings)["allocation"]["allow_skip"]:
            msg = "Skipping is not allowed in this task"
            raise conflict(msg, "skip_not_allowed")
        errors = _Errors()
        if reason not in SKIP_REASONS:
            errors.add("reason", "Unknown skip reason")
        clean_note = _text(errors, "note", note if note is not None else "")
        if errors.items:
            msg = "The skip request is not valid"
            raise invalid(msg, errors.items)
        context_service.open_assignment(locked)
        context = dict(locked.context) if isinstance(locked.context, dict) else {}
        context["skip_hosting"] = {
            slot: _hosting_state(app, locked) for slot, app in context_service.slot_apps(locked)
        }
        now = timezone.now()
        locked.context = context
        locked.status = Assignment.Status.SKIPPED
        locked.skip_reason = reason
        locked.skip_note = clean_note or ""
        locked.last_activity_at = now
        locked.save(update_fields=["context", "status", "skip_reason", "skip_note", "last_activity_at"])
    _sync(assignment, locked)


def set_finding_verdict(assignment: Assignment, finding_id: str, verdict: Any, note: Any) -> FindingVerdict:
    """Record the participant's TP/FP/unsure verdict on a finding of the assignment's frozen context.

    The finding must be in ``context.blocks[*].findings[slot]`` (404 otherwise); the verdict stores
    the task app of that slot and the context entry as ``finding_snapshot``. 409 ``read_only``,
    400 for an unknown verdict or an over-long note.
    """
    from backend.analysis.models import Finding

    finding_key = str(finding_id)
    with transaction.atomic():
        locked = _lock(assignment)
        access.ensure_writable(locked)
        context_service.open_assignment(locked)
        slot, entry = context_service.finding_entry(locked, finding_key)
        errors = _Errors()
        if verdict not in FINDING_VERDICTS:
            errors.add("verdict", "Choose true positive, false positive or unsure")
        clean_note = _text(errors, "note", note if note is not None else "")
        if errors.items:
            msg = "The verdict is not valid"
            raise invalid(msg, errors.items)
        task_app = context_service.app_for_slot(locked, slot)
        record, _ = FindingVerdict.objects.update_or_create(
            assignment=locked,
            finding_key=finding_key,
            defaults={
                "task_app": task_app,
                "finding": Finding.objects.filter(pk=access.parse_uuid(finding_key)).first(),
                "finding_snapshot": entry,
                "verdict": verdict,
                "note": clean_note or "",
            },
        )
        now = timezone.now()
        Assignment.objects.filter(pk=locked.pk).update(last_activity_at=now)
        locked.last_activity_at = now
    _sync(assignment, locked)
    return record


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def response_payload(response: Response) -> dict[str, Any]:
    """``{block_id, answers, updated_at}`` (the autosave response)."""
    return {"block_id": response.block_id, "answers": response.answers, "updated_at": response.updated_at.isoformat()}


def responses_payload(assignment: Assignment) -> dict[str, dict[str, Any]]:
    """``PortalAssignment.responses``: ``{block_id: {answers, updated_at}}``."""
    return {
        response.block_id: {"answers": response.answers, "updated_at": response.updated_at.isoformat()}
        for response in Response.objects.filter(assignment=assignment).order_by("block_id")
    }


def verdicts_payload(assignment: Assignment) -> dict[str, dict[str, str]]:
    """``PortalAssignment.finding_verdicts``: ``{finding_key: {verdict, note}}``."""
    return {
        verdict.finding_key: {"verdict": verdict.verdict, "note": verdict.note}
        for verdict in FindingVerdict.objects.filter(assignment=assignment).order_by("finding_key")
    }


def next_after(participant: Participant) -> dict[str, Any] | None:
    """TS ``NextResult`` of the participant's next assignment after a submit/skip, or None (back to the task page).

    Runs ``allocation.next_assignment`` in its own savepoint: a failure there never undoes the
    submission that just happened (it is logged and the participant returns to the task page).
    """
    from backend.human_eval.services import allocation

    try:
        with transaction.atomic():
            assignment, reason = allocation.next_assignment(participant)
    except NotImplementedError:
        return None
    except Exception:  # the submission stands even if handing out the next item fails
        logger.exception("next_assignment failed for participant %s", participant.pk)
        return None
    if assignment is None:
        return None
    return {"assignment_id": str(assignment.id), "reason": reason}
