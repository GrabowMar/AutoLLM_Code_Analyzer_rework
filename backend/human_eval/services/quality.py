"""Data-quality flags (owner D1): speeding, straightlining, attention checks, app not viewed, duplicates.

Flags describe the data; they never change rows. The pre-registered
``design.analysis.exclusion_rules`` turn some of them into rule exclusions that
``services/analysis.py`` applies when computing results (:func:`rule_matches`).

Assignment flags (submitted assignments only):

- ``speeding`` — BOTH ``active_seconds`` (visible-tab time) and ``wall_seconds``
  (workbench-open time) are below ``settings.quality.min_seconds_per_item`` (0 turns
  the check off). Without any heartbeat wall time (``wall_seconds == 0``) the elapsed
  ``submitted_at - started_at`` stands in for it.
- ``straightlining`` — a rating block with at least four numeric answers that are all
  identical ("na" answers are ignored).
- ``attention_failed`` — an attention question inside a custom questionnaire block was
  answered with something other than its ``expected`` option.
- ``app_not_viewed`` — a slot of the assignment has 0 ``app_views[slot].viewed_seconds``
  and no ``first_running_at`` (the rater never had that app on screen).

Participant flags: ``attention_failed`` when a pre/post-questionnaire attention
question was answered wrongly (unanswered questions are not failures),
``possible_duplicate`` when another non-preview participant of the task shares the
device hash, plus every flag of the participant's submitted assignments.

Public API: :func:`participant_flags`, :func:`assignment_flags` (``{str(id): [flag]}`` for
every row of the task), :func:`rule_excluded_assignments` (which submitted assignments the
pre-registered rules remove beyond the manual exclusions, as results count them) and
:func:`quality_report` / :func:`rule_matches` for callers that already loaded the rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from backend.human_eval.models import Assignment
from backend.human_eval.models import Participant
from backend.human_eval.models import Response
from backend.human_eval.services.design import blocks_of_type
from backend.human_eval.services.design import merged_settings

if TYPE_CHECKING:
    from collections.abc import Iterable

    from backend.human_eval.models import EvalTask

SPEEDING = "speeding"
STRAIGHTLINING = "straightlining"
ATTENTION_FAILED = "attention_failed"
APP_NOT_VIEWED = "app_not_viewed"
POSSIBLE_DUPLICATE = "possible_duplicate"
#: Display / storage order of flags.
FLAG_ORDER = (SPEEDING, STRAIGHTLINING, ATTENTION_FAILED, APP_NOT_VIEWED, POSSIBLE_DUPLICATE)
#: ``design.analysis.exclusion_rules`` keys (each names the flag it excludes on).
RULES = (SPEEDING, ATTENTION_FAILED, STRAIGHTLINING, APP_NOT_VIEWED)
STRAIGHTLINE_MIN_CRITERIA = 4


@dataclass
class QualityReport:
    """Flags of every assignment / participant of a task (keys are ``str(id)``)."""

    assignments: dict[str, list[str]] = field(default_factory=dict)
    participants: dict[str, list[str]] = field(default_factory=dict)
    #: participant id → failed pre/post attention questions (excludes all their assignments under the rule)
    participant_attention_failures: dict[str, int] = field(default_factory=dict)
    #: assignment id → failed attention questions inside custom questionnaire blocks
    assignment_attention_failures: dict[str, int] = field(default_factory=dict)


def _ordered(flags: Iterable[str]) -> list[str]:
    present = set(flags)
    return [flag for flag in FLAG_ORDER if flag in present]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def assignment_seconds(assignment: Assignment) -> tuple[int, int]:
    """``(active, wall)`` seconds; wall falls back to ``submitted_at - started_at`` without heartbeat wall time."""
    active = int(assignment.active_seconds or 0)
    wall = int(assignment.wall_seconds or 0)
    if not wall and assignment.started_at and assignment.submitted_at:
        wall = max(0, int((assignment.submitted_at - assignment.started_at).total_seconds()))
    return active, wall


def item_seconds(assignment: Assignment) -> int:
    """Time spent on an item for medians: visible-tab seconds, else the wall time."""
    active, wall = assignment_seconds(assignment)
    return active or wall


def is_speeding(assignment: Assignment, threshold: int) -> bool:
    """Both active and wall seconds below ``threshold`` (never with ``threshold <= 0``)."""
    if threshold <= 0:
        return False
    active, wall = assignment_seconds(assignment)
    return active < threshold and wall < threshold


def straightlined(rating_blocks: list[dict[str, Any]], answers_by_block: dict[str, dict[str, Any]]) -> bool:
    """Whether any rating block got ≥ 4 numeric answers that are all identical."""
    for block in rating_blocks:
        criteria = (answers_by_block.get(block["id"]) or {}).get("criteria")
        if not isinstance(criteria, dict):
            continue
        values = []
        for criterion in block.get("criteria") or []:
            value = _number(criteria.get(criterion["id"]))
            if value is not None:
                values.append(value)
        if len(values) >= STRAIGHTLINE_MIN_CRITERIA and len(set(values)) == 1:
            return True
    return False


def _answered(value: Any) -> bool:
    return value is not None and value not in ("", [])


def failed_attention(questions: Iterable[dict[str, Any]], answers: dict[str, Any] | None) -> int:
    """How many answered attention questions got a value other than their ``expected`` option."""
    if not isinstance(answers, dict):
        return 0
    failed = 0
    for question in questions:
        if question.get("kind") != "attention" or not question.get("expected"):
            continue
        value = answers.get(question.get("id"))
        if _answered(value) and value != question["expected"]:
            failed += 1
    return failed


def assignment_slots(assignment: Assignment) -> list[str]:
    """The display slots of an assignment: ``["single"]`` or ``["A", "B"]``."""
    return ["single"] if assignment.item.app_b_id is None else ["A", "B"]


def not_viewed(assignment: Assignment) -> bool:
    """Whether some slot has no viewed seconds and was never seen running."""
    views = assignment.app_views if isinstance(assignment.app_views, dict) else {}
    for slot in assignment_slots(assignment):
        entry = views.get(slot)
        entry = entry if isinstance(entry, dict) else {}
        seconds = _number(entry.get("viewed_seconds")) or 0
        if seconds <= 0 and not entry.get("first_running_at"):
            return True
    return False


def _custom_attention_blocks(design: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block
        for block in blocks_of_type(design, "questionnaire")
        if block.get("instrument") == "custom"
        and any(question.get("kind") == "attention" for question in block.get("questions") or [])
    ]


def quality_report(
    task: EvalTask,
    *,
    participants: Iterable[Participant] | None = None,
    assignments: Iterable[Assignment] | None = None,
    responses: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> QualityReport:
    """Every flag of the task's participants and assignments (preview participants included).

    Callers that already loaded the rows pass them: ``assignments`` with ``item`` loaded,
    ``responses`` as ``{assignment_id: {block_id: answers}}`` (at least the rating and custom
    questionnaire blocks of submitted assignments).
    """
    design = task.design if isinstance(task.design, dict) else {}
    threshold = int(merged_settings(task.settings)["quality"].get("min_seconds_per_item") or 0)
    rating_blocks = blocks_of_type(design, "rating")
    attention_blocks = _custom_attention_blocks(design)
    task_questions = [*(design.get("pre_questions") or []), *(design.get("post_questions") or [])]

    participant_rows = list(participants) if participants is not None else list(Participant.objects.filter(task=task))
    if assignments is None:
        assignment_rows = list(Assignment.objects.filter(task=task).select_related("item"))
    else:
        assignment_rows = list(assignments)
    submitted = [row for row in assignment_rows if row.status == Assignment.Status.SUBMITTED]
    if responses is None:
        block_ids = [block["id"] for block in (*rating_blocks, *attention_blocks)]
        responses = {}
        if block_ids and submitted:
            rows = Response.objects.filter(assignment__in=[row.pk for row in submitted], block_id__in=block_ids)
            for assignment_id, block_id, answers in rows.values_list("assignment_id", "block_id", "answers"):
                responses.setdefault(str(assignment_id), {})[block_id] = answers if isinstance(answers, dict) else {}

    report = QualityReport()
    for row in assignment_rows:
        report.assignments[str(row.pk)] = []
    for row in submitted:
        key = str(row.pk)
        answers = responses.get(key) or {}
        flags = []
        if is_speeding(row, threshold):
            flags.append(SPEEDING)
        if straightlined(rating_blocks, answers):
            flags.append(STRAIGHTLINING)
        failures = sum(
            failed_attention(block.get("questions") or [], (answers.get(block["id"]) or {}).get("answers"))
            for block in attention_blocks
        )
        if failures:
            report.assignment_attention_failures[key] = failures
            flags.append(ATTENTION_FAILED)
        if not_viewed(row):
            flags.append(APP_NOT_VIEWED)
        report.assignments[key] = _ordered(flags)

    devices: dict[str, int] = {}
    for participant in participant_rows:
        if participant.kind != Participant.Kind.PREVIEW and participant.device_hash:
            devices[participant.device_hash] = devices.get(participant.device_hash, 0) + 1
    by_participant: dict[str, set[str]] = {str(participant.pk): set() for participant in participant_rows}
    for row in submitted:
        by_participant.setdefault(str(row.participant_id), set()).update(report.assignments[str(row.pk)])
    for participant in participant_rows:
        key = str(participant.pk)
        failures = failed_attention(
            task_questions,
            {
                **(participant.pre_answers if isinstance(participant.pre_answers, dict) else {}),
                **(participant.post_answers if isinstance(participant.post_answers, dict) else {}),
            },
        )
        if failures:
            report.participant_attention_failures[key] = failures
            by_participant[key].add(ATTENTION_FAILED)
        is_counted = participant.kind != Participant.Kind.PREVIEW
        if is_counted and participant.device_hash and devices.get(participant.device_hash, 0) > 1:
            by_participant[key].add(POSSIBLE_DUPLICATE)
    report.participants = {key: _ordered(flags) for key, flags in by_participant.items()}
    return report


def rule_matches(report: QualityReport, rules: dict[str, Any], assignment: Assignment) -> list[str]:
    """The enabled exclusion rules (in :data:`RULES` order) that exclude this submitted assignment.

    ``attention_failed`` excludes every assignment of a participant who failed a pre/post
    attention question, and single assignments that failed one inside a block.
    """
    flags = set(report.assignments.get(str(assignment.pk)) or [])
    if report.participant_attention_failures.get(str(assignment.participant_id)):
        flags.add(ATTENTION_FAILED)
    return [rule for rule in RULES if (rules or {}).get(rule) and rule in flags]


def rule_excluded_assignments(task: EvalTask, report: QualityReport | None = None) -> dict[str, list[str]]:
    """``{str(assignment_id): [rule, ...]}`` for submitted assignments the analysis plan's rules exclude.

    Only non-preview assignments that are not manually excluded (assignment or participant)
    are considered, exactly as ``compute_results`` applies the rules and counts
    ``rule_exclusions``. Exports do not use this: their ``rule_excluded`` column also marks
    manually excluded rows a rule hits (``rule_matches`` on every submitted row), so a
    re-analysis can vary both kinds of exclusion. Assignments no rule excludes are absent.
    """
    design = task.design if isinstance(task.design, dict) else {}
    rules = (design.get("analysis") or {}).get("exclusion_rules") or {}
    if not any(rules.get(rule) for rule in RULES):
        return {}
    report = report or quality_report(task)
    rows = (
        Assignment.objects.filter(task=task, status=Assignment.Status.SUBMITTED, excluded=False)
        .exclude(participant__kind=Participant.Kind.PREVIEW)
        .exclude(participant__excluded=True)
    )
    result = {}
    for row in rows.only("id", "participant_id"):
        matched = rule_matches(report, rules, row)
        if matched:
            result[str(row.pk)] = matched
    return result


def participant_flags(task: EvalTask) -> dict[str, list[str]]:
    """``{str(participant_id): [flag, ...]}`` for every participant of the task (see the module docstring)."""
    return quality_report(task).participants


def assignment_flags(task: EvalTask) -> dict[str, list[str]]:
    """``{str(assignment_id): [flag, ...]}`` for every assignment of the task (``[]`` unless submitted)."""
    return quality_report(task).assignments
