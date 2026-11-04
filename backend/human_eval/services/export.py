"""Exports (owner D2): long-format CSVs, the codebook, the results JSON and the ZIP bundle.

Rules (spec §5.15, R1, R2, R3.15):

- Every CSV holds **every row** of the task's data, excluded ones and unfinished work
  included, with ``assignment_status``, ``excluded``, ``exclusion_reason``,
  ``participant_excluded``, ``quality_flags`` and ``rule_excluded`` columns so a
  re-analysis can reproduce or vary every exclusion. ``include_excluded`` only
  affects results. Researcher **preview** participants are never exported.
- Rows identify participants by pseudonymous label (``P001``) and apps by blinded
  label (``App 07``) plus slot; the de-blinding key is ``apps.csv`` (model,
  condition, study, task, stack, evaluation; all from the frozen snapshot, never
  live run data). Exports never contain an email address, user id, access-code
  hint, invite token, device hash, completion code or IP address.
- UTF-8 without BOM, ``\\n`` line ends, booleans ``true`` / ``false``, empty cell =
  missing, timestamps ISO 8601 UTC, lists joined with ``;``, JSON cells canonical.
- CSV-injection safety (R3.15): only *string* cells that start with ``=``, ``+``,
  ``-``, ``@``, TAB or CR and do not fully match a number get a leading ``'``;
  numbers (``-2``, ``-0.5``) are written as numbers.
- ``codebook.md`` documents every column of every CSV of the export (generated
  from the same headers), the design hash, seed, exclusion rules, value codings,
  how to de-blind and the out-of-scope platform admin login.

Quality flags come from ``services/quality.py`` (D1). Rule exclusions are not
stored on rows: ``rule_excluded`` applies the pre-registered
``design.analysis.exclusion_rules`` with D1's ``quality.rule_matches`` (the
function results use), so exports and results always agree on who is excluded.
The column marks every submitted row a rule hits, manually excluded ones included
(so a re-analysis can drop the manual exclusions but keep the rules); results.json
``rule_exclusions`` counts only the rows a rule removes on top of the manual
exclusions, i.e. ``rule_excluded and not excluded and not participant_excluded``.
Results come from ``services/analysis.compute_results`` (D1) via
:func:`task_results`, which turns any failure into a 500 with the detail.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import math
import re
import tempfile
import uuid
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property
from itertools import groupby
from operator import attrgetter
from typing import IO
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.text import slugify

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import FindingVerdict
from backend.human_eval.models import HttpExchange
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.models import Response
from backend.human_eval.services import analysis
from backend.human_eval.services import instruments
from backend.human_eval.services import quality
from backend.human_eval.services.checklists import PLATFORM_CREDENTIALS_NOTE
from backend.human_eval.services.counts import UNAVAILABLE_SKIP_REASONS
from backend.human_eval.services.counts import per_item_target
from backend.human_eval.services.design import canonical_json
from backend.human_eval.services.design import editable_after_freeze
from backend.human_eval.services.design import merged_settings
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.errors import invalid

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

#: TS ``ExportKind`` in export / codebook order.
EXPORT_KINDS: tuple[str, ...] = (
    "items",
    "responses",
    "ratings",
    "requirements",
    "security_checklist",
    "pairwise",
    "scenarios",
    "questionnaires",
    "observations",
    "finding_verdicts",
    "participants",
    "assignments",
    "apps",
    "http_log",
)
GROUP_BY_VALUES: tuple[str, ...] = ("condition", "model", "app")
#: ``design.analysis.exclusion_rules`` keys; each equals the quality flag it acts on.
EXCLUSION_RULES: tuple[str, ...] = ("speeding", "attention_failed", "straightlining", "app_not_viewed")
SPOOL_MAX_BYTES = 8 * 1024 * 1024

_PREVIEW = Participant.Kind.PREVIEW
_OPEN = (Assignment.Status.ASSIGNED, Assignment.Status.IN_PROGRESS)
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_REQUIREMENT_SCORES = {"met": 1, "partial": 0.5, "not_met": 0, "blocked": 0}
_CHECKLIST_SCORES = {"vulnerable": 1, "not_vulnerable": 0}
_SCENARIO_SUCCESS = {"success": 1, "partial": 0, "failure": 0}
_FIVE_POINT_MAX = 2


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def csv_cell(value: Any) -> str:
    """One CSV cell: numbers as numbers, booleans ``true``/``false``, None empty, formula-looking text escaped.

    A string that starts with ``= + - @ TAB CR`` and does not fully match a number gets a leading
    ``'`` (spreadsheet formula injection); ``"-2"`` or ``-2`` stays ``-2``.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else ""
    if isinstance(value, dt.datetime):
        text = (timezone.localtime(value, dt.UTC) if timezone.is_aware(value) else value).isoformat()
    elif isinstance(value, dt.date):
        text = value.isoformat()
    elif isinstance(value, uuid.UUID):
        text = str(value)
    elif isinstance(value, (dict, list, tuple)):
        text = canonical_json(value)
    else:
        text = str(value)
    if text.startswith(_FORMULA_PREFIXES) and not _NUMBER.fullmatch(text):
        return "'" + text
    return text


def _flag_list(values: Iterable[str]) -> str:
    return ";".join(sorted({str(value) for value in values}))


def _answer_cell(value: Any) -> Any:
    """A question answer as a cell value: lists joined with ``;``, numbers kept numeric."""
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    if isinstance(value, dict):
        return canonical_json(value)
    return value


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def export_slug(task: EvalTask) -> str:
    """``slugify(title)`` (at most 60 chars), ``task`` when the title has no slug characters."""
    return slugify(task.title or "")[:60].strip("-") or "task"


def export_filename(task: EvalTask, kind: str, extension: str = "csv") -> str:
    """``human-eval-<slug(title)>-<kind>.<extension>``."""
    return f"human-eval-{export_slug(task)}-{kind}.{extension}"


# ---------------------------------------------------------------------------
# Data loading (once per export)
# ---------------------------------------------------------------------------


class ExportData:
    """Everything the CSV builders read, loaded lazily once per export. Preview participants are left out."""

    def __init__(self, task: EvalTask) -> None:
        self.task = task
        self.task_id = str(task.pk)
        self.design: dict[str, Any] = task.design if isinstance(task.design, dict) else {}
        self.settings = merged_settings(task.settings)
        self.pairwise = task.mode == EvalTask.Mode.PAIRWISE
        self.slots: tuple[str, ...] = ("A", "B") if self.pairwise else ("single",)
        self.blocks: list[dict[str, Any]] = [
            block for block in self.design.get("blocks") or [] if isinstance(block, dict)
        ]
        self.block_order = {block.get("id"): index for index, block in enumerate(self.blocks)}

    # -- design ---------------------------------------------------------------

    def blocks_of(self, block_type: str) -> list[dict[str, Any]]:
        return [block for block in self.blocks if block.get("type") == block_type]

    def block(self, block_id: str) -> dict[str, Any] | None:
        for block in self.blocks:
            if block.get("id") == block_id:
                return block
        return None

    @cached_property
    def pre_questions(self) -> list[dict[str, Any]]:
        return [q for q in self.design.get("pre_questions") or [] if isinstance(q, dict) and q.get("id")]

    @cached_property
    def post_questions(self) -> list[dict[str, Any]]:
        return [q for q in self.design.get("post_questions") or [] if isinstance(q, dict) and q.get("id")]

    @cached_property
    def exclusion_rules(self) -> dict[str, Any]:
        """``design.analysis.exclusion_rules`` as stored."""
        return _dict(_dict(self.design.get("analysis")).get("exclusion_rules"))

    @cached_property
    def rules(self) -> list[str]:
        """Enabled pre-registered exclusion rules."""
        return [rule for rule in EXCLUSION_RULES if self.exclusion_rules.get(rule)]

    # -- rows -----------------------------------------------------------------

    @cached_property
    def apps(self) -> dict[Any, EvalTaskApp]:
        rows = EvalTaskApp.objects.filter(task=self.task).order_by("position", "label", "id")
        return {app.pk: app for app in rows}

    @cached_property
    def items(self) -> list[EvalItem]:
        return list(EvalItem.objects.filter(task=self.task).order_by("position", "id"))

    @cached_property
    def participants(self) -> dict[Any, Participant]:
        rows = (
            Participant.objects.filter(task=self.task)
            .exclude(kind=_PREVIEW)
            .select_related("invite")
            .order_by("number")
        )
        return {participant.pk: participant for participant in rows}

    @cached_property
    def assignments(self) -> list[Assignment]:
        rows = list(
            Assignment.objects.filter(task=self.task)
            .exclude(participant__kind=_PREVIEW)
            .select_related("item")
            .order_by("participant__number", "position", "created_at", "id"),
        )
        for assignment in rows:
            # Reuse the loaded rows (no per-row queries for participant / apps).
            assignment.participant = self.participants[assignment.participant_id]
            item = assignment.item
            item.app_a = self.apps[item.app_a_id]
            if item.app_b_id is not None:
                item.app_b = self.apps[item.app_b_id]
        return rows

    @cached_property
    def assignment_by_id(self) -> dict[Any, Assignment]:
        return {assignment.pk: assignment for assignment in self.assignments}

    @cached_property
    def quality_report(self) -> quality.QualityReport:
        """D1's flags of every assignment / participant (one pass; the same inputs results use)."""
        return quality.quality_report(self.task)

    @cached_property
    def assignment_flags(self) -> dict[str, list[str]]:
        return self.quality_report.assignments or {}

    @cached_property
    def participant_flags(self) -> dict[str, list[str]]:
        return self.quality_report.participants or {}

    @cached_property
    def observation_counts(self) -> dict[tuple[Any, Any, str], int]:
        """``{(assignment_id, task_app_id, kind): n}``."""
        rows = (
            Observation.objects.filter(assignment__task=self.task)
            .order_by()
            .values("assignment_id", "task_app_id", "kind")
            .annotate(n=Count("id"))
        )
        return {(row["assignment_id"], row["task_app_id"], row["kind"]): row["n"] for row in rows}

    @cached_property
    def observation_totals(self) -> dict[Any, int]:
        """``{assignment_id: n}`` over every observation kind and app."""
        totals: dict[Any, int] = defaultdict(int)
        for (assignment_id, _app_id, _kind), n in self.observation_counts.items():
            totals[assignment_id] += n
        return totals

    @cached_property
    def http_counts(self) -> dict[Any, int]:
        rows = (
            HttpExchange.objects.filter(assignment__task=self.task)
            .order_by()
            .values("assignment_id")
            .annotate(n=Count("id"))
        )
        return {row["assignment_id"]: row["n"] for row in rows}

    @cached_property
    def bugs_none_found(self) -> dict[Any, dict[str, Any]]:
        """``{assignment_id: {slot: bool}}`` from the bugs block answers (``BugsAnswers.none_found``)."""
        block_ids = [block.get("id") for block in self.blocks_of("bugs")]
        result: dict[Any, dict[str, Any]] = {}
        if not block_ids:
            return result
        rows = Response.objects.filter(assignment__task=self.task, block_id__in=block_ids).values_list(
            "assignment_id",
            "answers",
        )
        for assignment_id, answers in rows:
            none_found = _dict(_dict(answers).get("none_found"))
            entry = result.setdefault(assignment_id, {})
            for slot, value in none_found.items():
                entry[str(slot)] = bool(value) or bool(entry.get(str(slot)))
        return result

    def iter_responses(self, block_ids: Iterable[str] | None = None) -> Iterator[tuple[Assignment, Response]]:
        """``(assignment, response)`` in assignment order, then design block order (streamed)."""
        qs = Response.objects.filter(assignment__task=self.task).exclude(assignment__participant__kind=_PREVIEW)
        if block_ids is not None:
            qs = qs.filter(block_id__in=list(block_ids))
        qs = qs.order_by(
            "assignment__participant__number",
            "assignment__position",
            "assignment__created_at",
            "assignment_id",
            "block_id",
        )
        last = len(self.block_order)
        for assignment_id, group in groupby(qs.iterator(chunk_size=500), key=attrgetter("assignment_id")):
            assignment = self.assignment_by_id.get(assignment_id)
            if assignment is None:
                continue
            for response in sorted(group, key=lambda r: (self.block_order.get(r.block_id, last), r.block_id)):
                yield assignment, response

    # -- per-row helpers ------------------------------------------------------

    def label(self, app_id: Any) -> str:
        app = self.apps.get(app_id)
        return app.label if app is not None else ""

    def slot_apps(self, assignment: Assignment) -> list[tuple[str, EvalTaskApp | None]]:
        """Display slots of the assignment: ``[("single", app)]`` or ``[("A", ..), ("B", ..)]`` (honours swapped)."""
        item = assignment.item
        app_a = self.apps.get(item.app_a_id)
        if item.app_b_id is None:
            return [("single", app_a)]
        app_b = self.apps.get(item.app_b_id)
        return [("A", app_b), ("B", app_a)] if assignment.swapped else [("A", app_a), ("B", app_b)]

    def slot_of(self, assignment: Assignment, task_app_id: Any) -> str:
        for slot, app in self.slot_apps(assignment):
            if app is not None and app.pk == task_app_id:
                return slot
        return ""

    def first_slot(self, assignment: Assignment) -> tuple[str, EvalTaskApp | None]:
        return self.slot_apps(assignment)[0]

    def rule_hits(self, assignment: Assignment) -> list[str]:
        """Enabled exclusion rules that hit this submitted assignment (``quality.rule_matches``).

        Results only use submitted assignments, so other rows never carry a rule exclusion. Manually
        excluded rows are matched too (results.json ``rule_exclusions`` skips them: it counts what
        the rules remove beyond the manual exclusions).
        """
        if not self.rules or assignment.status != Assignment.Status.SUBMITTED:
            return []
        return list(quality.rule_matches(self.quality_report, self.exclusion_rules, assignment))

    def is_manually_excluded(self, assignment: Assignment) -> bool:
        return bool(assignment.excluded or assignment.participant.excluded)

    def common(self, assignment: Assignment) -> dict[str, Any]:
        """The columns every assignment-derived row starts with (:data:`COMMON_COLUMNS`)."""
        participant = assignment.participant
        hits = self.rule_hits(assignment)
        return {
            "task_id": self.task_id,
            "participant": participant.label,
            "participant_kind": participant.kind,
            "assignment_id": assignment.pk,
            "item_id": assignment.item_id,
            "position": assignment.position,
            "assignment_status": assignment.status,
            "excluded": assignment.excluded,
            "exclusion_reason": assignment.exclusion_reason,
            "participant_excluded": participant.excluded,
            "quality_flags": _flag_list(self.assignment_flags.get(str(assignment.pk), [])),
            "participant_quality_flags": _flag_list(self.participant_flags.get(str(participant.pk), [])),
            "rule_excluded": bool(hits),
            "rule_exclusion_rules": ";".join(hits),
        }

    def app_columns(self, slot: str, app: EvalTaskApp | None) -> dict[str, Any]:
        return {"slot": slot, "app_label": app.label if app is not None else ""}

    def pair_columns(self, assignment: Assignment) -> dict[str, Any]:
        item = assignment.item
        slots = dict(self.slot_apps(assignment))
        left, right = slots.get("A"), slots.get("B")
        return {
            "app_a_label": self.label(item.app_a_id),
            "app_b_label": self.label(item.app_b_id),
            "swapped": assignment.swapped,
            "displayed_left_label": left.label if left is not None else "",
            "displayed_right_label": right.label if right is not None else "",
        }

    def unit_columns(self, assignment: Assignment) -> dict[str, Any]:
        """Per-assignment rows: the pair columns (pairwise) or slot + app label (single)."""
        if self.pairwise:
            return self.pair_columns(assignment)
        return self.app_columns(*self.first_slot(assignment))

    def unit_column_names(self) -> list[str]:
        return list(PAIR_COLUMNS) if self.pairwise else list(APP_COLUMNS)


COMMON_COLUMNS: tuple[str, ...] = (
    "task_id",
    "participant",
    "participant_kind",
    "assignment_id",
    "item_id",
    "position",
    "assignment_status",
    "excluded",
    "exclusion_reason",
    "participant_excluded",
    "quality_flags",
    "participant_quality_flags",
    "rule_excluded",
    "rule_exclusion_rules",
)
APP_COLUMNS: tuple[str, ...] = ("slot", "app_label")
PAIR_COLUMNS: tuple[str, ...] = (
    "app_a_label",
    "app_b_label",
    "swapped",
    "displayed_left_label",
    "displayed_right_label",
)


# ---------------------------------------------------------------------------
# Builders: columns(data) -> headers, rows(data) -> iterable of {column: value}
# ---------------------------------------------------------------------------


def _items_columns(data: ExportData) -> list[str]:
    apps = ["app_a_label", "app_b_label"] if data.pairwise else ["app_label"]
    return [
        "task_id",
        "item_id",
        "item_position",
        *apps,
        "group_key",
        "is_active",
        "target",
        "assignments",
        "submitted",
        "submitted_included",
        "in_progress",
        "skipped",
        "unavailable_skips",
        "expired",
    ]


def _items_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    target = per_item_target(data.task) if data.items else None
    stats: dict[Any, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for assignment in data.assignments:
        entry = stats[assignment.item_id]
        entry["assignments"] += 1
        status = assignment.status
        if status == Assignment.Status.SUBMITTED:
            entry["submitted"] += 1
            if not data.is_manually_excluded(assignment) and not data.rule_hits(assignment):
                entry["submitted_included"] += 1
        elif status in _OPEN:
            entry["in_progress"] += 1
        elif status == Assignment.Status.SKIPPED:
            entry["skipped"] += 1
            if assignment.skip_reason in UNAVAILABLE_SKIP_REASONS:
                entry["unavailable_skips"] += 1
        elif status == Assignment.Status.EXPIRED:
            entry["expired"] += 1
    for item in data.items:
        entry = stats.get(item.pk, {})
        apps = (
            {"app_a_label": data.label(item.app_a_id), "app_b_label": data.label(item.app_b_id)}
            if data.pairwise
            else {"app_label": data.label(item.app_a_id)}
        )
        yield {
            "task_id": data.task_id,
            "item_id": item.pk,
            "item_position": item.position,
            **apps,
            "group_key": item.group_key,
            "is_active": item.is_active,
            "target": target if item.is_active else None,
            **{
                key: entry.get(key, 0)
                for key in (
                    "assignments",
                    "submitted",
                    "submitted_included",
                    "in_progress",
                    "skipped",
                    "unavailable_skips",
                    "expired",
                )
            },
        }


_SLOT_FIELDS: tuple[str, ...] = (
    "viewed_seconds",
    "view_first_running_at",
    "container_id",
    "container_name",
    "image",
    "reset_at",
    "prior_users",
    "hosting_first_running_at",
    "skip_hosting_state",
    "skip_hosting_error",
    "bug_reports",
    "bugs_none_found",
)


def _assignments_columns(data: ExportData) -> list[str]:
    per_slot = [f"slot_{slot}_{field}" for slot in data.slots for field in _SLOT_FIELDS]
    return [
        *COMMON_COLUMNS,
        *data.unit_column_names(),
        "created_at",
        "started_at",
        "last_activity_at",
        "submitted_at",
        "skip_reason",
        "skip_note",
        "active_seconds",
        "wall_seconds",
        "excluded_at",
        "observation_count",
        "http_request_count",
        "instructions_sha256",
        *per_slot,
    ]


def _assignments_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    has_bugs_block = bool(data.blocks_of("bugs"))
    for assignment in data.assignments:
        context = _dict(assignment.context)
        hosting = _dict(context.get("hosting"))
        skip_hosting = _dict(context.get("skip_hosting"))
        views = _dict(assignment.app_views)
        none_found = data.bugs_none_found.get(assignment.pk, {})
        row = {
            **data.common(assignment),
            **data.unit_columns(assignment),
            "created_at": assignment.created_at,
            "started_at": assignment.started_at,
            "last_activity_at": assignment.last_activity_at,
            "submitted_at": assignment.submitted_at,
            "skip_reason": assignment.skip_reason,
            "skip_note": assignment.skip_note,
            "active_seconds": assignment.active_seconds,
            "wall_seconds": assignment.wall_seconds,
            "excluded_at": assignment.excluded_at,
            "observation_count": data.observation_totals.get(assignment.pk, 0),
            "http_request_count": data.http_counts.get(assignment.pk, 0),
            "instructions_sha256": context.get("instructions_sha256") or "",
        }
        slot_map = dict(data.slot_apps(assignment))
        for slot in data.slots:
            app = slot_map.get(slot)
            view = _dict(views.get(slot))
            provenance = _dict(hosting.get(slot))
            skipped = _dict(skip_hosting.get(slot))
            prefix = f"slot_{slot}_"
            row[prefix + "viewed_seconds"] = _number(view.get("viewed_seconds"))
            row[prefix + "view_first_running_at"] = view.get("first_running_at")
            row[prefix + "container_id"] = provenance.get("container_id")
            row[prefix + "container_name"] = provenance.get("container_name")
            row[prefix + "image"] = provenance.get("image")
            row[prefix + "reset_at"] = provenance.get("reset_at")
            row[prefix + "prior_users"] = _number(provenance.get("prior_users"))
            row[prefix + "hosting_first_running_at"] = provenance.get("first_running_at")
            row[prefix + "skip_hosting_state"] = skipped.get("state")
            row[prefix + "skip_hosting_error"] = skipped.get("error")
            row[prefix + "bug_reports"] = (
                data.observation_counts.get((assignment.pk, app.pk, Observation.Kind.BUG), 0) if app is not None else 0
            )
            row[prefix + "bugs_none_found"] = bool(none_found.get(slot)) if has_bugs_block else None
        yield row


def _responses_columns(data: ExportData) -> list[str]:
    return [
        *COMMON_COLUMNS,
        *data.unit_column_names(),
        "block_id",
        "block_type",
        "created_at",
        "updated_at",
        "answers_json",
    ]


def _responses_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    for assignment, response in data.iter_responses():
        block = data.block(response.block_id) or {}
        yield {
            **data.common(assignment),
            **data.unit_columns(assignment),
            "block_id": response.block_id,
            "block_type": block.get("type", ""),
            "created_at": response.created_at,
            "updated_at": response.updated_at,
            "answers_json": canonical_json(response.answers if isinstance(response.answers, dict) else {}),
        }


def _per_app_columns(*extra: str) -> Callable[[ExportData], list[str]]:
    def columns(data: ExportData) -> list[str]:
        return [*COMMON_COLUMNS, *APP_COLUMNS, *extra]

    return columns


def _block_responses(data: ExportData, block_type: str) -> Iterator[tuple[Assignment, Response, dict[str, Any]]]:
    blocks = {block.get("id"): block for block in data.blocks_of(block_type)}
    if not blocks:
        return
    for assignment, response in data.iter_responses(blocks):
        yield assignment, response, blocks[response.block_id]


def _ratings_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    for assignment, response, block in _block_responses(data, "rating"):
        answers = _dict(response.answers)
        values = _dict(answers.get("criteria"))
        base = {**data.common(assignment), **data.app_columns(*data.first_slot(assignment))}
        for criterion in block.get("criteria") or []:
            value = values.get(criterion.get("id"))
            scale = _dict(criterion.get("scale"))
            yield {
                **base,
                "block_id": block.get("id"),
                "criterion_id": criterion.get("id"),
                "criterion_label": criterion.get("label", ""),
                "scale_min": _number(scale.get("min")),
                "scale_max": _number(scale.get("max")),
                "scale_step": _number(scale.get("step")) or 1,
                "value": _number(value),
                "na": value == "na",
                "comment": answers.get("comment") or "",
                "response_updated_at": response.updated_at,
            }


def _context_block(assignment: Assignment, block_id: str) -> dict[str, Any]:
    return _dict(_dict(_dict(assignment.context).get("blocks")).get(block_id))


def _requirements_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    for assignment, response, block in _block_responses(data, "requirements"):
        slot, app = data.first_slot(assignment)
        verdicts = _dict(_dict(response.answers).get("items"))
        context_items = _dict(_context_block(assignment, block["id"]).get("requirements")).get(slot) or []
        items = [item for item in context_items if isinstance(item, dict) and item.get("id")]
        known = {item["id"] for item in items}
        items += [{"id": req_id, "source": "", "text": ""} for req_id in sorted(verdicts) if req_id not in known]
        base = {**data.common(assignment), **data.app_columns(slot, app)}
        for item in items:
            answer = _dict(verdicts.get(item["id"]))
            verdict = answer.get("verdict") or ""
            yield {
                **base,
                "block_id": block["id"],
                "req_id": item["id"],
                "source": item.get("source", ""),
                "text": item.get("text", ""),
                "verdict": verdict,
                "score": _REQUIREMENT_SCORES.get(verdict),
                "score_strict": 0 if verdict == "cannot_test" else _REQUIREMENT_SCORES.get(verdict),
                "note": answer.get("note") or "",
                "in_context": item["id"] in known,
            }


def _security_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    for assignment, response, block in _block_responses(data, "security"):
        outcomes = _dict(_dict(response.answers).get("checklist"))
        base = {**data.common(assignment), **data.app_columns(*data.first_slot(assignment))}
        for item in block.get("checklist") or []:
            answer = _dict(outcomes.get(item.get("id")))
            outcome = answer.get("outcome") or ""
            yield {
                **base,
                "block_id": block["id"],
                "checklist_item_id": item.get("id"),
                "checklist_item_label": item.get("label", ""),
                "checklist_item_category": item.get("category", ""),
                "outcome": outcome,
                "vulnerable": _CHECKLIST_SCORES.get(outcome),
                "note": answer.get("note") or "",
            }


def canonical_pairwise_value(raw: Any, scale: str, *, swapped: bool) -> int | None:
    """A pairwise answer as displayed → canonical orientation (+ = ``item.app_a`` better).

    ab_tie: "A" / "B" / "tie" → +1 / -1 / 0 for the displayed sides; five_point: -2 (displayed A much
    better) .. 2 (displayed B much better) → negated. Displayed A is ``item.app_b`` when swapped, so the
    sign flips. Missing or malformed answers → None.
    """
    if scale == "five_point":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != int(raw) or abs(raw) > _FIVE_POINT_MAX:
            return None
        displayed = -int(raw)
    else:
        displayed = {"A": 1, "B": -1, "tie": 0}.get(raw) if isinstance(raw, str) else None
        if displayed is None:
            return None
    return -displayed if swapped else displayed


def _pairwise_columns(data: ExportData) -> list[str]:
    return [
        *COMMON_COLUMNS,
        *PAIR_COLUMNS,
        "block_id",
        "criterion_id",
        "criterion_label",
        "scale",
        "raw_value",
        "canonical_value",
        "winner_label",
        "rationale",
    ]


def _pairwise_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    for assignment, response, block in _block_responses(data, "pairwise"):
        answers = _dict(response.answers)
        values = _dict(answers.get("criteria"))
        scale = block.get("scale") or "ab_tie"
        pair = data.pair_columns(assignment)
        base = {**data.common(assignment), **pair}
        criteria = [(c.get("id"), c.get("label", ""), values.get(c.get("id"))) for c in block.get("criteria") or []]
        if block.get("overall"):
            criteria.append(("overall", "Overall preference", answers.get("overall")))
        for criterion_id, label, raw in criteria:
            canonical = canonical_pairwise_value(raw, scale, swapped=assignment.swapped)
            if canonical is None:
                winner = ""
            elif canonical > 0:
                winner = pair["app_a_label"]
            elif canonical < 0:
                winner = pair["app_b_label"]
            else:
                winner = "tie"
            yield {
                **base,
                "block_id": block["id"],
                "criterion_id": criterion_id,
                "criterion_label": label,
                "scale": scale,
                "raw_value": raw if isinstance(raw, (str, int, float)) and not isinstance(raw, bool) else None,
                "canonical_value": canonical,
                "winner_label": winner,
                "rationale": answers.get("rationale") or "",
            }


def _scenarios_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    for assignment, response, block in _block_responses(data, "scenarios"):
        answers = _dict(_dict(response.answers).get("scenarios"))
        base = {**data.common(assignment), **data.app_columns(*data.first_slot(assignment))}
        for scenario in block.get("scenarios") or []:
            answer = _dict(answers.get(scenario.get("id")))
            outcome = answer.get("outcome") or ""
            yield {
                **base,
                "block_id": block["id"],
                "scenario_id": scenario.get("id"),
                "scenario_title": scenario.get("title", ""),
                "outcome": outcome,
                "success": _SCENARIO_SUCCESS.get(outcome),
                "seconds": _number(answer.get("seconds")),
                "ease": _number(answer.get("ease")),
                "note": answer.get("note") or "",
            }


def _questionnaire_questions(assignment: Assignment, block: dict[str, Any]) -> list[dict[str, Any]]:
    instrument = block.get("instrument") or "custom"
    if instrument == "custom":
        return [q for q in block.get("questions") or [] if isinstance(q, dict)]
    frozen = _context_block(assignment, block["id"]).get("questions")
    if isinstance(frozen, list) and frozen:
        return [q for q in frozen if isinstance(q, dict)]
    return instruments.instrument_questions(instrument)


def _questionnaire_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    for assignment, response, block in _block_responses(data, "questionnaire"):
        instrument = block.get("instrument") or "custom"
        answers = _dict(_dict(response.answers).get("answers"))
        score = instruments.score(instrument, answers) if instrument != "custom" else None
        base = {**data.common(assignment), **data.app_columns(*data.first_slot(assignment))}
        for question in _questionnaire_questions(assignment, block):
            yield {
                **base,
                "block_id": block["id"],
                "instrument": instrument,
                "question_id": question.get("id"),
                "question_label": question.get("label", ""),
                "question_kind": question.get("kind", ""),
                "value": _answer_cell(answers.get(question.get("id"))),
                "instrument_score": score,
            }


def _observations_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    rows = (
        Observation.objects.filter(assignment__task=data.task)
        .exclude(assignment__participant__kind=_PREVIEW)
        .annotate(attachment_count=Count("attachments"))
        .order_by("assignment__participant__number", "assignment__position", "created_at", "id")
    )
    for observation in rows.iterator(chunk_size=500):
        assignment = data.assignment_by_id.get(observation.assignment_id)
        if assignment is None:
            continue
        app = data.apps.get(observation.task_app_id)
        yield {
            **data.common(assignment),
            **data.app_columns(data.slot_of(assignment, observation.task_app_id), app),
            "observation_id": observation.pk,
            "block_id": observation.block_id,
            "kind": observation.kind,
            "title": observation.title,
            "description": observation.description,
            "steps": observation.steps,
            "location": observation.location,
            "category": observation.category,
            "tester_severity": observation.severity,
            "cwe": observation.cwe,
            "checklist_item_id": observation.checklist_item_id,
            "attachment_count": observation.attachment_count,
            "created_at": observation.created_at,
            "updated_at": observation.updated_at,
        }


_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "tool",
    "tool_name",
    "tool_severity",
    "category",
    "rule_id",
    "title",
    "file_path",
    "line",
    "cwe",
    "url",
    "method",
    "param",
)


def _context_finding_ids(assignment: Assignment, slot: str) -> set[str]:
    ids: set[str] = set()
    for entry in _dict(_dict(assignment.context).get("blocks")).values():
        findings = _dict(_dict(entry).get("findings")).get(slot) or []
        ids.update(str(finding.get("id")) for finding in findings if isinstance(finding, dict))
    return ids


def _finding_verdicts_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    rows = (
        FindingVerdict.objects.filter(assignment__task=data.task)
        .exclude(assignment__participant__kind=_PREVIEW)
        .order_by("assignment__participant__number", "assignment__position", "created_at", "id")
    )
    for verdict in rows.iterator(chunk_size=500):
        assignment = data.assignment_by_id.get(verdict.assignment_id)
        if assignment is None:
            continue
        slot = data.slot_of(assignment, verdict.task_app_id)
        snapshot = _dict(verdict.finding_snapshot)
        yield {
            **data.common(assignment),
            **data.app_columns(slot, data.apps.get(verdict.task_app_id)),
            "finding_key": verdict.finding_key,
            "in_context": verdict.finding_key in _context_finding_ids(assignment, slot),
            "verdict": verdict.verdict,
            "note": verdict.note,
            **{field: snapshot.get(field) for field in _SNAPSHOT_FIELDS},
            "created_at": verdict.created_at,
            "updated_at": verdict.updated_at,
        }


def _question_columns(data: ExportData) -> list[str]:
    columns = [f"pre_{q['id']}" for q in data.pre_questions]
    columns += [f"attention_{q['id']}_passed" for q in data.pre_questions if q.get("kind") == "attention"]
    columns += [f"post_{q['id']}" for q in data.post_questions]
    columns += [f"attention_{q['id']}_passed" for q in data.post_questions if q.get("kind") == "attention"]
    return columns


_PARTICIPANT_COLUMNS: tuple[str, ...] = (
    "task_id",
    "participant",
    "participant_kind",
    "status",
    "invite_label",
    "excluded",
    "exclusion_reason",
    "excluded_at",
    "quality_flags",
    "consent_at",
    "joined_at",
    "completed_at",
    "withdrawn_at",
    "assignments",
    "submitted",
    "skipped",
    "in_progress",
    "expired",
)


def _participants_columns(data: ExportData) -> list[str]:
    return [*_PARTICIPANT_COLUMNS, *_question_columns(data)]


def _attention_passed(question: dict[str, Any], answers: dict[str, Any]) -> bool | None:
    value = answers.get(question["id"])
    if value in (None, "", []):
        return None
    return value == question.get("expected")


def _participants_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    per_participant: dict[Any, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for assignment in data.assignments:
        entry = per_participant[assignment.participant_id]
        entry["assignments"] += 1
        if assignment.status in _OPEN:
            entry["in_progress"] += 1
        else:
            entry[assignment.status] += 1
    for participant in data.participants.values():
        counts = per_participant.get(participant.pk, {})
        pre, post = _dict(participant.pre_answers), _dict(participant.post_answers)
        row = {
            "task_id": data.task_id,
            "participant": participant.label,
            "participant_kind": participant.kind,
            "status": participant.status,
            "invite_label": participant.invite.label if participant.invite is not None else "",
            "excluded": participant.excluded,
            "exclusion_reason": participant.exclusion_reason,
            "excluded_at": participant.excluded_at,
            "quality_flags": _flag_list(data.participant_flags.get(str(participant.pk), [])),
            "consent_at": participant.consent_at,
            "joined_at": participant.joined_at,
            "completed_at": participant.completed_at,
            "withdrawn_at": participant.withdrawn_at,
            "assignments": counts.get("assignments", 0),
            "submitted": counts.get(Assignment.Status.SUBMITTED, 0),
            "skipped": counts.get(Assignment.Status.SKIPPED, 0),
            "in_progress": counts.get("in_progress", 0),
            "expired": counts.get(Assignment.Status.EXPIRED, 0),
        }
        for questions, answers, prefix in ((data.pre_questions, pre, "pre"), (data.post_questions, post, "post")):
            for question in questions:
                row[f"{prefix}_{question['id']}"] = _answer_cell(answers.get(question["id"]))
                if question.get("kind") == "attention":
                    row[f"attention_{question['id']}_passed"] = _attention_passed(question, answers)
        yield row


_APPS_COLUMNS: tuple[str, ...] = (
    "task_id",
    "app_id",
    "app_label",
    "position",
    "is_active",
    "group_key",
    "run_id",
    "model_id",
    "model_name",
    "provider",
    "condition_id",
    "condition_label",
    "study_id",
    "study_name",
    "app_slug",
    "app_name",
    "task_ref",
    "prose_variant",
    "stack",
    "auth_mode",
    "unit_key",
    "repeat_index",
    "run_kind",
    "harness",
    "run_created_at",
    "spec_content_hash",
    "run_fingerprint",
    "functional_status",
    "functional_pass_rate",
    "functional_deployed",
    "functional_source",
    "evaluator_version",
    "tester_version",
    "conformance_endpoint_checks",
    "conformance_endpoint_checks_passed",
    "conformance_endpoint_pass_rate",
    "static_findings_count",
    "static_findings_loc",
    "static_findings_loc_model",
    "static_findings_tools",
    "finding_sample_size",
    "finding_frame_json",
    "assignments",
    "submitted",
    "skipped",
    "unavailable_skips",
)


def _apps_columns(data: ExportData) -> list[str]:
    return list(_APPS_COLUMNS)


def _apps_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    per_app: dict[Any, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for assignment in data.assignments:
        for _slot, app in data.slot_apps(assignment):
            if app is None:
                continue
            entry = per_app[app.pk]
            entry["assignments"] += 1
            if assignment.status == Assignment.Status.SUBMITTED:
                entry["submitted"] += 1
            elif assignment.status == Assignment.Status.SKIPPED:
                entry["skipped"] += 1
                if assignment.skip_reason in UNAVAILABLE_SKIP_REASONS:
                    entry["unavailable_skips"] += 1
    for app in data.apps.values():
        snapshot = _dict(app.snapshot)
        functional = _dict(snapshot.get("functional"))
        endpoint_checks = [
            check
            for check in snapshot.get("checks") or []
            if isinstance(check, dict) and check.get("category") in ("api", "admin")
        ]
        passed = sum(1 for check in endpoint_checks if check.get("passed"))
        static = _dict(snapshot.get("static_findings"))
        tools = static.get("tools")
        counts = per_app.get(app.pk, {})
        yield {
            "task_id": data.task_id,
            "app_id": app.pk,
            "app_label": app.label,
            "position": app.position,
            "is_active": app.is_active,
            "group_key": app.group_key,
            "run_id": app.job_id,
            "model_id": snapshot.get("model_id"),
            "model_name": snapshot.get("model_name"),
            "provider": snapshot.get("provider"),
            "condition_id": snapshot.get("condition_id"),
            "condition_label": snapshot.get("condition_label"),
            "study_id": snapshot.get("study_id"),
            "study_name": snapshot.get("study_name"),
            "app_slug": snapshot.get("app_slug"),
            "app_name": snapshot.get("app_name"),
            "task_ref": snapshot.get("task_ref"),
            "prose_variant": snapshot.get("prose_variant"),
            "stack": snapshot.get("stack"),
            "auth_mode": snapshot.get("auth_mode"),
            "unit_key": snapshot.get("unit_key"),
            "repeat_index": snapshot.get("repeat_index"),
            "run_kind": snapshot.get("kind"),
            "harness": snapshot.get("harness"),
            "run_created_at": snapshot.get("created_at"),
            "spec_content_hash": snapshot.get("spec_content_hash"),
            "run_fingerprint": snapshot.get("run_fingerprint"),
            "functional_status": functional.get("status"),
            "functional_pass_rate": _number(functional.get("pass_rate")),
            "functional_deployed": functional.get("deployed"),
            "functional_source": functional.get("source"),
            "evaluator_version": snapshot.get("evaluator_version"),
            "tester_version": snapshot.get("tester_version"),
            "conformance_endpoint_checks": len(endpoint_checks) if endpoint_checks else None,
            "conformance_endpoint_checks_passed": passed if endpoint_checks else None,
            "conformance_endpoint_pass_rate": round(passed / len(endpoint_checks), 4) if endpoint_checks else None,
            "static_findings_count": _number(static.get("count")),
            "static_findings_loc": _number(static.get("loc")),
            "static_findings_loc_model": _number(static.get("loc_model")),
            "static_findings_tools": ";".join(str(tool) for tool in tools) if isinstance(tools, list) else None,
            "finding_sample_size": len(app.finding_sample) if isinstance(app.finding_sample, list) else 0,
            "finding_frame_json": canonical_json(app.finding_frame) if app.finding_frame else "",
            "assignments": counts.get("assignments", 0),
            "submitted": counts.get("submitted", 0),
            "skipped": counts.get("skipped", 0),
            "unavailable_skips": counts.get("unavailable_skips", 0),
        }


def _http_log_rows(data: ExportData) -> Iterator[dict[str, Any]]:
    rows = (
        HttpExchange.objects.filter(assignment__task=data.task)
        .exclude(assignment__participant__kind=_PREVIEW)
        .order_by("assignment__participant__number", "assignment__position", "created_at", "id")
    )
    for exchange in rows.iterator(chunk_size=500):
        assignment = data.assignment_by_id.get(exchange.assignment_id)
        if assignment is None:
            continue
        yield {
            **data.common(assignment),
            **data.app_columns(data.slot_of(assignment, exchange.task_app_id), data.apps.get(exchange.task_app_id)),
            "exchange_id": exchange.pk,
            "created_at": exchange.created_at,
            "method": exchange.method,
            "path": exchange.path,
            "request_headers_json": canonical_json(_dict(exchange.request_headers)),
            "request_body": exchange.request_body,
            "status_code": exchange.status_code,
            "response_headers_json": canonical_json(_dict(exchange.response_headers)),
            "response_body": exchange.response_body,
            "response_truncated": exchange.response_truncated,
            "elapsed_ms": exchange.elapsed_ms,
            "error": exchange.error,
        }


@dataclass(frozen=True)
class ExportTable:
    """One CSV kind: its headers (from the task's design) and rows."""

    summary: str
    columns: Callable[[ExportData], list[str]]
    rows: Callable[[ExportData], Iterable[dict[str, Any]]]


TABLES: dict[str, ExportTable] = {
    "items": ExportTable(
        "Every evaluation item (one app, or a pair of apps), including inactive and never-rated ones.",
        _items_columns,
        _items_rows,
    ),
    "responses": ExportTable(
        "Raw answers: one row per assignment and design block (JSON as stored, sides as displayed).",
        _responses_columns,
        _responses_rows,
    ),
    "ratings": ExportTable(
        "Rating blocks: one row per assignment and criterion.",
        _per_app_columns(
            "block_id",
            "criterion_id",
            "criterion_label",
            "scale_min",
            "scale_max",
            "scale_step",
            "value",
            "na",
            "comment",
            "response_updated_at",
        ),
        _ratings_rows,
    ),
    "requirements": ExportTable(
        "Requirements blocks: one row per assignment and requirement item shown to the participant.",
        _per_app_columns(
            "block_id",
            "req_id",
            "source",
            "text",
            "verdict",
            "score",
            "score_strict",
            "note",
            "in_context",
        ),
        _requirements_rows,
    ),
    "security_checklist": ExportTable(
        "Security blocks: one row per assignment and checklist item.",
        _per_app_columns(
            "block_id",
            "checklist_item_id",
            "checklist_item_label",
            "checklist_item_category",
            "outcome",
            "vulnerable",
            "note",
        ),
        _security_rows,
    ),
    "pairwise": ExportTable(
        "Pairwise blocks: one row per assignment and criterion (plus the overall preference).",
        _pairwise_columns,
        _pairwise_rows,
    ),
    "scenarios": ExportTable(
        "Scenario blocks: one row per assignment and usability scenario.",
        _per_app_columns(
            "block_id",
            "scenario_id",
            "scenario_title",
            "outcome",
            "success",
            "seconds",
            "ease",
            "note",
        ),
        _scenarios_rows,
    ),
    "questionnaires": ExportTable(
        "Questionnaire blocks: one row per assignment and question (standard instruments with their score).",
        _per_app_columns(
            "block_id",
            "instrument",
            "question_id",
            "question_label",
            "question_kind",
            "value",
            "instrument_score",
        ),
        _questionnaire_rows,
    ),
    "observations": ExportTable(
        "Bug, vulnerability and note reports logged by participants (severity is the tester's own).",
        _per_app_columns(
            "observation_id",
            "block_id",
            "kind",
            "title",
            "description",
            "steps",
            "location",
            "category",
            "tester_severity",
            "cwe",
            "checklist_item_id",
            "attachment_count",
            "created_at",
            "updated_at",
        ),
        _observations_rows,
    ),
    "finding_verdicts": ExportTable(
        "True/false-positive verdicts on automated analysis findings, with the finding as it was shown.",
        _per_app_columns(
            "finding_key",
            "in_context",
            "verdict",
            "note",
            *_SNAPSHOT_FIELDS,
            "created_at",
            "updated_at",
        ),
        _finding_verdicts_rows,
    ),
    "participants": ExportTable(
        "One row per participant (pseudonymous), with the pre/post questionnaire answers.",
        _participants_columns,
        _participants_rows,
    ),
    "assignments": ExportTable(
        "One row per assignment: status, timing, exclusions, hosting provenance and viewing per app slot.",
        _assignments_columns,
        _assignments_rows,
    ),
    "apps": ExportTable(
        "De-blinding key: one row per app with model, condition, spec and conformance (frozen snapshot).",
        _apps_columns,
        _apps_rows,
    ),
    "http_log": ExportTable(
        "Every request participants sent through the HTTP console, with the response excerpt.",
        _per_app_columns(
            "exchange_id",
            "created_at",
            "method",
            "path",
            "request_headers_json",
            "request_body",
            "status_code",
            "response_headers_json",
            "response_body",
            "response_truncated",
            "elapsed_ms",
            "error",
        ),
        _http_log_rows,
    ),
}


def check_kind(kind: str) -> str:
    """``kind`` when it is a TS ``ExportKind``; 404 otherwise."""
    if kind not in TABLES:
        msg = f"Unknown export kind '{kind}'"
        raise HumanEvalServiceError(404, msg, "not_found")
    return kind


def write_csv(data: ExportData, kind: str, stream: IO[str]) -> int:
    """Write one CSV kind to a text stream; returns the number of data rows."""
    table = TABLES[check_kind(kind)]
    headers = table.columns(data)
    allowed = set(headers)
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow([csv_cell(header) for header in headers])
    count = 0
    for row in table.rows(data):
        unknown = set(row) - allowed
        if unknown:  # a builder bug: never silently drop a value
            msg = f"{kind}.csv row has columns missing from its headers: {sorted(unknown)}"
            raise ValueError(msg)
        writer.writerow([csv_cell(row.get(header)) for header in headers])
        count += 1
    return count


def csv_text(task: EvalTask, kind: str, data: ExportData | None = None) -> str:
    """One CSV kind as a string (small exports and tests)."""
    buffer = io.StringIO()
    write_csv(data or ExportData(task), kind, buffer)
    return buffer.getvalue()


def csv_file(task: EvalTask, kind: str) -> IO[bytes]:
    """One CSV kind in a spooled temporary file (rewound), for a streaming download."""
    check_kind(kind)
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES)  # noqa: SIM115 - returned to the response
    text = io.TextIOWrapper(spool, encoding="utf-8", newline="")
    write_csv(ExportData(task), kind, text)
    text.flush()
    text.detach()
    spool.seek(0)
    return spool


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def task_results(task: EvalTask, *, group_by: str = "condition", include_excluded: bool = False) -> dict[str, Any]:
    """``analysis.compute_results`` for the task (TS ``TaskResults``).

    An unknown ``group_by`` is a 400. Any failure of the computation is a 500 ``results_failed`` carrying
    the error (logged with its traceback); it is never replaced by partial or empty results.
    """
    if group_by not in GROUP_BY_VALUES:
        msg = "Unknown group_by"
        raise invalid(msg, [{"path": "group_by", "message": f"must be one of {', '.join(GROUP_BY_VALUES)}"}])
    try:
        with transaction.atomic():
            results = analysis.compute_results(task, group_by=group_by, include_excluded=bool(include_excluded))
    except HumanEvalServiceError:
        raise
    except Exception as exc:
        logger.exception("human_eval: computing results of task %s failed", task.pk)
        msg = f"Results could not be computed: {type(exc).__name__}: {exc}"
        raise HumanEvalServiceError(500, msg, "results_failed") from exc
    if not isinstance(results, dict):
        msg = f"Results could not be computed: compute_results returned {type(results).__name__}, not a dict"
        raise HumanEvalServiceError(500, msg, "results_failed")
    return results


def results_json_bytes(results: dict[str, Any]) -> bytes:
    """The results document as pretty-printed UTF-8 JSON."""
    return json.dumps(results, indent=2, ensure_ascii=False, default=str).encode("utf-8")


# ---------------------------------------------------------------------------
# Codebook
# ---------------------------------------------------------------------------

_COLUMN_DOCS: dict[str, str] = {
    "task_id": "Task UUID (identical on every row).",
    "participant": "Pseudonymous participant label (P001, P002, ...): the only participant identifier exported.",
    "participant_kind": "How the participant joined: invite (link), code (pre-generated access code), account.",
    "assignment_id": "Assignment UUID (one participant evaluating one item); joins every per-assignment file.",
    "item_id": "Item UUID (one app, or one pair of apps); joins items.csv.",
    "position": "Order of this assignment within the participant's work (1 = first).",
    "assignment_status": "assigned | in_progress | submitted | skipped | expired. Results use submitted only.",
    "excluded": "The researcher excluded this assignment manually (true/false).",
    "exclusion_reason": "The researcher's reason for a manual exclusion (assignment or participant row).",
    "participant_excluded": "The researcher excluded the whole participant manually (true/false).",
    "quality_flags": "Data-quality flags of the row's assignment (only submitted assignments are checked), ';'-list.",
    "participant_quality_flags": "Flags of the participant: their own plus every flag of their submitted assignments.",
    "rule_excluded": (
        "A pre-registered exclusion rule hits this submitted assignment (computed; also set on manually excluded "
        "rows). results.json rule_exclusions counts only rows not already manually excluded: rule_excluded and "
        "not excluded and not participant_excluded."
    ),
    "rule_exclusion_rules": "The enabled exclusion rules that hit this assignment, ';'-separated.",
    "slot": "Display slot of the app: single (single mode), A = shown left / first, B = shown right / second.",
    "app_label": "Blinded app label (App 01, ...); de-blind through apps.csv.",
    "app_a_label": "Blinded label of the item's canonical first app (item.app_a; canonical orientation +).",
    "app_b_label": "Blinded label of the item's canonical second app (item.app_b).",
    "swapped": "Pairwise: true when app_b was displayed as A (left). Randomised and seeded.",
    "displayed_left_label": "Pairwise: blinded label of the app shown as A (left).",
    "displayed_right_label": "Pairwise: blinded label of the app shown as B (right).",
    "block_id": "Design block id (see Design blocks).",
    "created_at": "When the row was created (ISO 8601, UTC).",
    "updated_at": "When the row was last changed (ISO 8601, UTC).",
    "note": "The participant's free-text note.",
}

_KIND_COLUMN_DOCS: dict[str, dict[str, str]] = {
    "items": {
        "item_position": "Seeded order of the item in the task.",
        "app_label": "Blinded label of the item's app.",
        "group_key": "Spec group: '<app slug>:<first 12 hex of the spec content hash>' (pairs are formed within one).",
        "is_active": "The item can still be handed out (false after its app was deactivated).",
        "target": "Ratings wanted per item (balanced: ratings_per_item; all: number of raters); empty when inactive.",
        "assignments": "Assignments of the item (every status).",
        "submitted": "Submitted assignments (manual / rule exclusions included).",
        "submitted_included": "Submitted assignments not excluded manually (assignment or participant) or by a rule.",
        "in_progress": "Open assignments (assigned or in progress).",
        "skipped": "Skipped assignments (any reason).",
        "unavailable_skips": "Skips with reason app_broken or platform_unavailable.",
        "expired": "Assignments that expired without a submission.",
    },
    "assignments": {
        "created_at": "When the assignment was handed out.",
        "started_at": "When the participant first opened it (its context was frozen then).",
        "last_activity_at": "Last heartbeat or answer.",
        "submitted_at": "When it was submitted (empty otherwise).",
        "skip_reason": "app_broken | platform_unavailable | cannot_evaluate | conflict_of_interest | other.",
        "skip_note": "The participant's explanation of the skip.",
        "active_seconds": "Visible-tab time from heartbeats (each beat capped at 60 s).",
        "wall_seconds": "Workbench-open time from heartbeats, visible or not (gaps over 5 minutes removed).",
        "excluded_at": "When the researcher excluded it (empty when not excluded).",
        "observation_count": "Bug / vulnerability / note reports logged in this assignment (all slots).",
        "http_request_count": "Requests sent through the HTTP console in this assignment.",
        "instructions_sha256": "sha256 of the task instructions the participant saw (changes are listed below).",
    },
    "responses": {
        "block_type": "rating | requirements | security | scenarios | questionnaire | bugs | pairwise.",
        "answers_json": "The block's answers exactly as stored (canonical JSON; pairwise sides as displayed).",
    },
    "ratings": {
        "criterion_id": "Rating criterion id.",
        "criterion_label": "Criterion label as shown.",
        "scale_min": "Lowest scale value.",
        "scale_max": "Highest scale value.",
        "scale_step": "Distance between allowed values.",
        "value": "The rating (integer on the scale); empty when not answered or can't judge.",
        "na": "true when the participant chose 'can't judge' (value is then empty).",
        "comment": "The block comment (same on every criterion row of the response).",
        "response_updated_at": "Last save of the block's answers.",
    },
    "requirements": {
        "req_id": "Stable requirement id (same spec = same id across apps; custom items c_<id>).",
        "source": "backend | frontend | admin | api | admin_api | custom (empty: not in the frozen context).",
        "text": "Requirement text as shown (endpoints: 'METHOD /path - description').",
        "verdict": "met | partial | not_met | blocked | cannot_test (empty: not answered).",
        "score": "met 1, partial 0.5, not_met 0, blocked 0; cannot_test empty (excluded from compliance).",
        "score_strict": "As score, but cannot_test counts as 0 (compliance_strict).",
        "in_context": "The item was in the list frozen for this assignment (false: answered id not in the context).",
    },
    "security_checklist": {
        "checklist_item_id": "Checklist item id.",
        "checklist_item_label": "Checklist item label as shown.",
        "checklist_item_category": "OWASP Top 10 2021 category of the checklist item.",
        "outcome": "vulnerable | not_vulnerable | not_tested (empty: not answered).",
        "vulnerable": "1 vulnerable, 0 not vulnerable, empty for not_tested / unanswered.",
    },
    "pairwise": {
        "criterion_id": "Pairwise criterion id; 'overall' is the overall preference.",
        "criterion_label": "Criterion label as shown.",
        "scale": "ab_tie (A / B / tie) or five_point (-2..2).",
        "raw_value": "The answer as displayed: ab_tie 'A' | 'B' | 'tie'; five_point -2 (A much better) .. 2 (B).",
        "canonical_value": "Canonical orientation, + = app_a better: ab_tie +1 / 0 / -1; five_point +2..-2.",
        "winner_label": "app_a_label or app_b_label of the preferred app, 'tie', empty when not answered.",
        "rationale": "The participant's rationale (same on every row of the response).",
    },
    "scenarios": {
        "scenario_id": "Scenario id.",
        "scenario_title": "Scenario title as shown.",
        "outcome": "success | partial | failure | not_attempted (empty: not answered).",
        "success": "1 success; 0 partial or failure; empty for not_attempted / unanswered.",
        "seconds": "Timed duration in seconds (empty when not timed).",
        "ease": "Single Ease Question 1 (very difficult) .. 7 (very easy).",
    },
    "questionnaires": {
        "instrument": "sus | umux_lite | nasa_tlx | custom.",
        "question_id": "Question id (standard instruments: sus_1..sus_10, umux_lite_1..2, tlx_*).",
        "question_label": "Question text as shown.",
        "question_kind": "text | textarea | single | multi | scale | number | attention.",
        "value": "The answer: numbers numeric, choices by option value, multiple choices ';'-separated.",
        "instrument_score": "The response's instrument score 0..100 (empty for custom or incomplete responses).",
    },
    "observations": {
        "observation_id": "Observation UUID.",
        "block_id": "Design block the report was filed from (may be empty).",
        "kind": "bug | vulnerability | note.",
        "title": "Report title.",
        "description": "Report description.",
        "steps": "Steps to reproduce.",
        "location": "Page URL or endpoint the participant gave.",
        "category": "Report category (bugs: functional, ui, ...; vulnerabilities: OWASP A01..A10, other).",
        "tester_severity": "The participant's OWN severity judgement (critical..info, empty), never a tool's.",
        "cwe": "CWE id the participant gave.",
        "checklist_item_id": "Security checklist item the report belongs to (may be empty).",
        "attachment_count": "Screenshots attached (files are not part of the export).",
    },
    "finding_verdicts": {
        "finding_key": "Id of the automated finding (kept if the finding is deleted later).",
        "in_context": "The finding was in the sample frozen for this assignment (results ignore other rows).",
        "verdict": "tp (true positive) | fp (false positive) | unsure.",
        "tool": "Analyzer tool slug.",
        "tool_name": "Analyzer tool name.",
        "tool_severity": "Severity label exactly as the tool printed it (empty: the tool rates nothing).",
        "category": "Finding category (security, secrets, ...).",
        "rule_id": "The tool's rule id.",
        "title": "Finding title as shown.",
        "file_path": "File the finding points at.",
        "line": "Line number.",
        "cwe": "CWE given by the tool.",
        "url": "DAST: request URL.",
        "method": "DAST: HTTP method.",
        "param": "DAST: parameter.",
    },
    "participants": {
        "status": "invited | active | completed | withdrawn.",
        "quality_flags": "Flags of the participant (attention_failed, possible_duplicate) plus every flag of their "
        "submitted assignments, ';'-separated.",
        "invite_label": "Label the researcher gave the invite link the participant joined through.",
        "excluded_at": "When the researcher excluded the participant (empty when not excluded).",
        "consent_at": "When the participant gave consent.",
        "joined_at": "When the participant joined (or first used their code / account).",
        "completed_at": "When the participant finished.",
        "withdrawn_at": "When the participant withdrew.",
        "assignments": "Assignments handed to the participant (every status).",
        "submitted": "Submitted assignments.",
        "skipped": "Skipped assignments.",
        "in_progress": "Open assignments.",
        "expired": "Expired assignments.",
    },
    "apps": {
        "app_id": "Task app UUID.",
        "app_label": "Blinded label shown to participants (join key of every other file).",
        "position": "Position of the app in the pool.",
        "is_active": "The app still receives assignments.",
        "group_key": "Task group: '<task slug>:<first 12 hex of the task hash>'.",
        "run_id": "GenerationRun UUID (empty when the run was deleted).",
        "model_id": "Model id of the generating LLM (e.g. provider/name).",
        "model_name": "Display name of the model.",
        "provider": "Model provider.",
        "condition_id": "Study condition id (empty without a study).",
        "condition_label": "Study condition label.",
        "study_id": "Study UUID.",
        "study_name": "Study name.",
        "app_slug": "Task slug.",
        "app_name": "Task title.",
        "task_ref": "Task version (slug@version) the app was generated from.",
        "prose_variant": "Prose variant of the task the model was given (minimal, standard, exhaustive).",
        "stack": "Stack profile (slug@version); empty for a free stack.",
        "auth_mode": "Auth contract of the run (contract_bearer, cookie_session, spec_defined).",
        "unit_key": "Study unit (task or slot target) of the run.",
        "repeat_index": "Repeat index of the run within its study cell.",
        "run_kind": "Run kind (app_agentic, slot_single_shot, slot_agent).",
        "harness": "Agent harness that generated the app.",
        "run_created_at": "When the run was created.",
        "spec_content_hash": "Task hash of the run (task version and prose variant).",
        "run_fingerprint": "Run fingerprint (identity of the generation).",
        "functional_status": "Evaluation status: tested, not_measured, error, pending, or the run outcome "
        "(build_fail, start_fail, infra_error, ...) when the app was never evaluated.",
        "functional_pass_rate": "Functional pass rate 0..1 of the latest evaluation (empty when not measured).",
        "functional_deployed": "The app could be built and started (true / false / empty unknown).",
        "functional_source": "eval_suite (automated evaluation suite), empty when never evaluated.",
        "evaluator_version": "Evaluation suite (slug@version; compare only within one version).",
        "tester_version": "Eval tester version.",
        "conformance_endpoint_checks": "Number of task endpoints (api + admin) the tester checked.",
        "conformance_endpoint_checks_passed": "Endpoints whose checks all passed.",
        "conformance_endpoint_pass_rate": "Passed / checked endpoints (rounded to 4 decimals).",
        "static_findings_count": "Static-analysis findings at snapshot time (non-AI, non-dynamic, no dependencies).",
        "static_findings_loc": "App-scope source lines of code (the per-kLOC denominator; empty when unknown).",
        "static_findings_loc_model": "Source lines of code of the model's own files (empty when unknown).",
        "static_findings_tools": "Static tools that contributed, ';'-separated.",
        "finding_sample_size": "Findings drawn for TP/FP review of this app.",
        "finding_frame_json": "Sampling frame {tool: {population, sampled}} (inverse-probability weights).",
        "assignments": "Assignments that included the app (every status; previews excluded).",
        "submitted": "Submitted assignments that included the app.",
        "skipped": "Skipped assignments that included the app.",
        "unavailable_skips": "Skips with reason app_broken or platform_unavailable.",
    },
    "http_log": {
        "exchange_id": "HTTP exchange UUID.",
        "created_at": "When the request was sent.",
        "method": "HTTP method.",
        "path": "Request path and query string.",
        "request_headers_json": "Request headers as sent (canonical JSON).",
        "request_body": "Request body excerpt (at most 16 KiB).",
        "status_code": "Response status code (empty on a connection error or timeout).",
        "response_headers_json": "Response headers (canonical JSON).",
        "response_body": "Response body excerpt (at most 16 KiB).",
        "response_truncated": "The response was longer than the console reads.",
        "elapsed_ms": "Round-trip time in milliseconds.",
        "error": "Connection / timeout error text (empty on success).",
    },
}

_SLOT_FIELD_DOCS: dict[str, str] = {
    "viewed_seconds": "Seconds the slot's app pane was shown (heartbeats).",
    "view_first_running_at": "When the app was first seen running while shown (heartbeats).",
    "container_id": "Hosting provenance: container record id the participant used.",
    "container_name": "Hosting provenance: container name (random llm-<hex>).",
    "image": "Hosting provenance: container image.",
    "reset_at": "Hosting provenance: when the app was last reset to its built state before this assignment.",
    "prior_users": "Hosting provenance: earlier assignments that used this app state since its last reset/build.",
    "hosting_first_running_at": "Hosting provenance: when the app was first seen running for this assignment.",
    "skip_hosting_state": "Skips only: the app's hosting state at skip time.",
    "skip_hosting_error": "Skips only: the hosting error at skip time.",
    "bug_reports": "Bug reports logged against the slot's app in this assignment.",
    "bugs_none_found": "The participant confirmed finding no bugs in the slot's app (empty without a bugs block).",
}

_SLOT_COLUMN = re.compile(r"^slot_(single|A|B)_(.+)$")


def _question_label(data: ExportData, question_id: str, *, post: bool | None) -> str:
    sources = (
        [data.pre_questions, data.post_questions]
        if post is None
        else [data.post_questions if post else data.pre_questions]
    )
    for questions in sources:
        for question in questions:
            if question["id"] == question_id:
                return str(question.get("label", ""))
    return ""


def describe_column(data: ExportData, kind: str, column: str) -> str:
    """The codebook description of one column of one CSV ("" when undocumented)."""
    kind_docs = _KIND_COLUMN_DOCS.get(kind, {})
    if column in kind_docs:
        return kind_docs[column]
    if column in _COLUMN_DOCS:
        return _COLUMN_DOCS[column]
    match = _SLOT_COLUMN.match(column)
    if match and match.group(2) in _SLOT_FIELD_DOCS:
        return f"Slot {match.group(1)}: {_SLOT_FIELD_DOCS[match.group(2)]}"
    if kind == "participants":
        if column.startswith("attention_") and column.endswith("_passed"):
            question_id = column[len("attention_") : -len("_passed")]
            label = _question_label(data, question_id, post=None)
            return f"Attention check '{question_id}' ({label}): true passed, false failed, empty unanswered."
        for prefix, post in (("pre_", False), ("post_", True)):
            if column.startswith(prefix):
                question_id = column[len(prefix) :]
                stage = "Post" if post else "Pre"
                return (
                    f"{stage}-questionnaire answer to '{question_id}': {_question_label(data, question_id, post=post)}"
                )
    return ""


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _iso(value: Any) -> str:
    return csv_cell(value) if value is not None else "-"


_RULE_DOCS: dict[str, str] = {
    "speeding": "submitted with BOTH active_seconds and wall_seconds below quality.min_seconds_per_item "
    "({threshold} s; 0 = off); without heartbeat wall time, submitted_at - started_at stands in for wall_seconds",
    "attention_failed": "a pre/post attention-check question answered with another option than expected (removes "
    "all of the participant's assignments), or an attention question inside a questionnaire block answered wrongly "
    "(removes that assignment); unanswered questions are not failures",
    "straightlining": "a rating block with at least 4 numeric answers that are all identical (can't-judge ignored)",
    "app_not_viewed": "submitted with an app slot that was never on screen (0 viewed seconds and never seen running)",
}
_FLAG_DOCS: dict[str, str] = {
    **_RULE_DOCS,
    "possible_duplicate": "another participant of the task joined from the same browser (device cookie); flag only, "
    "never a rule",
}


def _rating_details(block: dict[str, Any]) -> str:
    parts = []
    for criterion in block.get("criteria") or []:
        scale = _dict(criterion.get("scale"))
        na = ", can't judge allowed" if criterion.get("allow_na") else ""
        req = "" if criterion.get("required", True) else ", optional"
        parts.append(f"`{criterion.get('id')}` {scale.get('min')}..{scale.get('max')}{na}{req}")
    return "criteria: " + "; ".join(parts)


def _requirements_details(block: dict[str, Any]) -> str:
    custom = len(block.get("custom") or [])
    return f"sources: {', '.join(block.get('sources') or [])}; custom items: {custom}; note: {block.get('note')}"


def _security_details(block: dict[str, Any]) -> str:
    review = _dict(block.get("finding_review"))
    sample = (
        f"finding review: up to {review.get('max_findings')} per app" if review.get("enabled") else "no finding review"
    )
    console = "HTTP console" if block.get("http_console") else "no HTTP console"
    reports = "vulnerability reports" if block.get("reports") else "no reports"
    return f"{len(block.get('checklist') or [])} checklist items; {reports}; {sample}; {console}"


def _scenarios_details(block: dict[str, Any]) -> str:
    ids = ", ".join(f"`{s.get('id')}`" for s in block.get("scenarios") or [])
    return f"scenarios: {ids}; timed: {bool(block.get('timed'))}; ease question: {bool(block.get('ease_question'))}"


def _questionnaire_details(block: dict[str, Any]) -> str:
    instrument = block.get("instrument")
    if instrument == "custom":
        questions = ", ".join(f"`{q.get('id')}` ({q.get('kind')})" for q in block.get("questions") or [])
        return f"custom questions: {questions}"
    return f"standard instrument: {instruments.INSTRUMENTS.get(instrument, {}).get('name', instrument)}"


def _bugs_details(block: dict[str, Any]) -> str:
    return f"minimum bug reports per app: {block.get('min_reports', 0)} (0: 'none found' may be confirmed instead)"


def _pairwise_details(block: dict[str, Any]) -> str:
    ids = ", ".join(f"`{c.get('id')}`" for c in block.get("criteria") or [])
    overall = "; overall preference" if block.get("overall") else ""
    return f"criteria: {ids}{overall}; scale: {block.get('scale')}; rationale: {block.get('rationale')}"


_BLOCK_DETAILS: dict[str, Callable[[dict[str, Any]], str]] = {
    "rating": _rating_details,
    "requirements": _requirements_details,
    "security": _security_details,
    "scenarios": _scenarios_details,
    "questionnaire": _questionnaire_details,
    "bugs": _bugs_details,
    "pairwise": _pairwise_details,
}


def _block_details(block: dict[str, Any]) -> str:
    details = _BLOCK_DETAILS.get(str(block.get("type")))
    return details(block) if details else ""


def _plan_lines(data: ExportData) -> list[str]:
    plan = _dict(data.design.get("analysis"))
    primary = _dict(plan.get("primary"))
    threshold = _dict(data.settings.get("quality")).get("min_seconds_per_item", 0)
    lines = [
        "## Pre-registered analysis plan",
        "",
        f"- Primary metric: `{primary.get('block_id')}` / `{primary.get('metric')}`"
        if primary
        else "- Primary metric: none (results are not confirmatory)",
        f"- Planned ratings per item: {plan.get('planned_ratings_per_item') or '-'}; "
        f"planned participants: {plan.get('planned_participants') or '-'}",
        f"- Unevaluable apps (no included submission and at least one app_broken skip): "
        f"`{plan.get('unevaluable_policy') or 'exclude'}` (exclude = complete-case; floor = at the scale minimum, "
        "compliance 0)",
        "",
        "## Exclusions",
        "",
        "Nothing is removed from these files. Results (results.json) use submitted assignments of non-preview "
        "participants and drop, unless computed with include_excluded:",
        "",
        "- manual exclusions: `excluded` (assignment) or `participant_excluded` (whole participant), with "
        "`exclusion_reason`;",
        "- rule exclusions: `rule_excluded`, from the pre-registered rules below applied to the quality flags.",
        "",
        "`rule_excluded` is set on every submitted row a rule hits, manually excluded rows included, so a "
        "re-analysis can vary the manual and the rule exclusions separately. results.json `rule_exclusions` "
        "counts per rule only the rows a rule removes beyond the manual exclusions (`rule_excluded` and not "
        "`excluded` and not `participant_excluded`).",
        "",
        "| Rule | Enabled | Definition |",
        "| --- | --- | --- |",
    ]
    for rule in EXCLUSION_RULES:
        enabled = "yes" if rule in data.rules else "no"
        lines.append(f"| `{rule}` | {enabled} | {_md(_RULE_DOCS[rule].format(threshold=threshold))} |")
    lines += ["", "Quality flags (`quality_flags`, `participant_quality_flags`):", ""]
    lines += [f"- `{flag}`: {text.format(threshold=threshold)}" for flag, text in _FLAG_DOCS.items()]
    return lines


def _codings_lines() -> list[str]:
    return [
        "## Value codings",
        "",
        "- Requirements: met = 1, partial = 0.5, not_met = 0, blocked = 0 (an app defect prevents the test); "
        "cannot_test is excluded from compliance (counted as 0 in compliance_strict).",
        "- Security checklist: vulnerable = 1, not_vulnerable = 0, not_tested excluded.",
        "- Pairwise: `raw_value` is as displayed (A = left). `canonical_value` has + = app_a better: ab_tie +1 / 0 / "
        "-1, five_point +2..-2 (the displayed scale runs -2 = A much better .. 2 = B much better, so it is "
        "negated, and the sign flips again when `swapped`).",
        "- Scenarios: success = 1; partial and failure = 0; not_attempted excluded. Ease = Single Ease Question 1..7.",
        "- Questionnaires: SUS = (sum of odd items - 1 + sum of 5 - even items) x 2.5; UMUX-Lite = ((a - 1) + "
        "(b - 1)) / 12 x 100; raw NASA-TLX = mean of the six subscales (0..100, step 5). Incomplete responses "
        "have no score.",
        "- Observation severity (`tester_severity`) is the participant's own judgement. Automated findings carry "
        "only the tool's own label (`tool_severity`); no normalized severity is exported.",
    ]


def _hosting_lines(data: ExportData) -> list[str]:
    hosting = _dict(data.settings.get("hosting"))
    reset = hosting.get("reset")
    isolation = hosting.get("isolation")
    state = (
        "each new assignment got a fresh copy of the app (recreated from its image)"
        if reset == "per_assignment"
        else "apps were NOT reset between participants: later raters may have seen earlier raters' data"
    )
    return [
        "## Hosting and app state",
        "",
        f"- Isolation: `{isolation}`; reset: `{reset}` ({state}).",
        "- Provenance per slot is in assignments.csv (`slot_<slot>_container_*`, `_image`, `_reset_at`, "
        "`_prior_users`, `_hosting_first_running_at`): what the participant actually used.",
        f"- {PLATFORM_CREDENTIALS_NOTE}",
    ]


def _deleted_participant_label(entry: dict[str, Any]) -> str:
    """``P003`` from a ``participant_deleted:<number>`` protocol-log field (``"unknown"`` without a number)."""
    number = str(entry.get("field") or "").partition(":")[2].strip()
    return f"P{int(number):03d}" if number.isdigit() else "unknown"


def _protocol_lines(task: EvalTask) -> list[str]:
    log = [entry for entry in task.protocol_log or [] if isinstance(entry, dict)]
    deletions = [entry for entry in log if analysis.is_participant_deletion(entry)]
    edits = [entry for entry in log if not analysis.is_participant_deletion(entry)]
    lines = ["## Changes after opening", ""]
    if not log:
        return [*lines, "None recorded."]
    if edits:
        lines += [
            "Edits of participant-facing text after the task opened (`instructions_sha256` in assignments.csv "
            "shows which version each participant saw):",
            "",
            "| At | Field | Old sha256 | New sha256 |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| {_md(entry.get('at'))} | {_md(entry.get('field'))} | {_md(entry.get('old_sha256'))} | "
            f"{_md(entry.get('new_sha256'))} |"
            for entry in edits
        ]
    if deletions:
        if edits:
            lines.append("")
        lines += [
            "Participants deleted after opening, with all their data (their rows are absent from every file; a "
            "post-hoc exclusion, so results are not confirmatory):",
            "",
            "| At | Participant |",
            "| --- | --- |",
        ]
        lines += [f"| {_md(entry.get('at'))} | {_deleted_participant_label(entry)} |" for entry in deletions]
    return lines


def codebook(
    data: ExportData,
    *,
    generated_at: dt.datetime,
    kinds: Iterable[str] = EXPORT_KINDS,
    results: dict[str, Any] | None = None,
) -> str:
    """``codebook.md``: study metadata, conventions, plan, exclusions, codings and every CSV's columns."""
    task = data.task
    design_keys = sorted(editable_after_freeze(data.design))
    provisional = (
        "" if task.status == EvalTask.Status.CLOSED else " (results are provisional until the task is closed)"
    )
    lines = [
        f"# Codebook: {_md(task.title)}",
        "",
        "Human-evaluation export from LLM Lab. This file documents every file and column of the export.",
        "",
        "## Study",
        "",
        "| | |",
        "| --- | --- |",
        f"| Task id | `{task.pk}` |",
        f"| Mode | {task.mode} |",
        f"| Status at export | {task.status}{provisional} |",
        f"| Generated at | {_iso(generated_at)} |",
        f"| Seed | {task.seed} (allocation order, pair sides, finding samples, bootstrap draws) |",
        f"| Design hash | `{task.design_hash or '-'}` |",
        f"| Design frozen at | {_iso(task.frozen_at)} |",
        f"| First opened at | {_iso(task.opened_at)} |",
        f"| Closed at | {_iso(task.closed_at)} |",
        f"| First closed at | {_iso(task.first_closed_at)} |",
        f"| Apps / items | {len(data.apps)} / {len(data.items)} |",
        f"| Participants / assignments exported | {len(data.participants)} / {len(data.assignments)} |",
        "",
        "The design hash is the sha256 of the canonical JSON of `{mode, design, settings}` without the settings that "
        f"may change after opening ({', '.join(f'`{key}`' for key in design_keys)}). design.json and settings.json "
        "hold the documents as stored.",
    ]
    if results is not None:
        flags = {key: csv_cell(bool(results.get(key))) for key in ("include_excluded", "provisional", "confirmatory")}
        lines += [
            "",
            f"results.json: computed at {_md(results.get('generated_at'))} with group_by = "
            f"`{_md(results.get('group_by'))}`, include_excluded = {flags['include_excluded']}; "
            f"provisional = {flags['provisional']}, confirmatory = {flags['confirmatory']}.",
        ]
    lines += [
        "",
        "## Conventions",
        "",
        "- CSV: UTF-8 without BOM, comma-separated, `\\n` line ends, RFC 4180 quoting.",
        "- Booleans `true` / `false`; an empty cell is missing or not applicable; timestamps ISO 8601 in UTC; "
        "lists are `;`-separated; JSON cells are canonical (sorted keys).",
        "- Every row is exported, including excluded ones and unfinished work (`assignment_status`). Researcher "
        "preview sessions are never exported.",
        "- Spreadsheet safety: a text cell that starts with `=`, `+`, `-`, `@`, tab or carriage return and is not a "
        "number has a leading apostrophe (`'`) added; strip one leading `'` from such text when re-analysing. "
        "Numbers (including negative ones) are written as numbers.",
        "- Participants are pseudonymous (`P001`); no email address, user id, access code (or hint), invite token, "
        "device hash, completion code or IP address is exported.",
        "",
        "## Blinding and de-blinding",
        "",
        "Participants saw blinded app labels only (`App 01`, ...). Every file uses those labels; apps.csv is the "
        "de-blinding key (model, condition, study, task, stack, evaluation, all from the snapshot frozen when "
        "the task opened): join any file to apps.csv on `app_label` (pairwise: `app_a_label` / `app_b_label` / "
        "`displayed_left_label`). Keep apps.csv away from raters.",
        "",
        *_plan_lines(data),
        "",
        "## Design blocks",
        "",
        "| Block id | Type | Title | Details |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| `{_md(block.get('id'))}` | {_md(block.get('type'))} | {_md(block.get('title'))} | "
        f"{_md(_block_details(block))} |"
        for block in data.blocks
    ]
    lines += ["", *_codings_lines(), "", *_hosting_lines(data), "", *_protocol_lines(task), "", "## Files", ""]
    lines += [f"- `{kind}.csv`: {TABLES[kind].summary}" for kind in kinds]
    lines += [
        "- `design.json`: the task design (blocks, analysis plan, consent, pre/post questions) as frozen.",
        "- `settings.json`: the task settings (blinding, allocation, pairing, access, hosting, quality).",
        "- `results.json`: the computed results (TaskResults).",
        "- `codebook.md`: this file.",
    ]
    for kind in kinds:
        lines += ["", f"### {kind}.csv", "", TABLES[kind].summary, "", "| Column | Description |", "| --- | --- |"]
        lines += [
            f"| `{_md(column)}` | {_md(describe_column(data, kind, column))} |"
            for column in TABLES[kind].columns(data)
        ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


def write_bundle(task: EvalTask, results: dict[str, Any], fileobj: IO[bytes]) -> None:
    """Write ``bundle.zip`` (every CSV, design.json, settings.json, results.json, codebook.md) to ``fileobj``.

    Entries live under ``human-eval-<slug>/``; CSVs are streamed into the archive.
    """
    data = ExportData(task)
    generated_at = timezone.now()
    root = f"human-eval-{export_slug(task)}"
    with zipfile.ZipFile(fileobj, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for kind in EXPORT_KINDS:
            with archive.open(f"{root}/{kind}.csv", "w", force_zip64=True) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                write_csv(data, kind, text)
                text.flush()
                text.detach()
        archive.writestr(f"{root}/design.json", json.dumps(data.design, indent=2, ensure_ascii=False))
        archive.writestr(f"{root}/settings.json", json.dumps(data.settings, indent=2, ensure_ascii=False))
        archive.writestr(f"{root}/results.json", results_json_bytes(results))
        archive.writestr(f"{root}/codebook.md", codebook(data, generated_at=generated_at, results=results))


def bundle_file(task: EvalTask, results: dict[str, Any]) -> IO[bytes]:
    """``bundle.zip`` in a spooled temporary file (rewound), for a streaming download."""
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES)  # noqa: SIM115 - returned to the response
    write_bundle(task, results, spool)
    spool.seek(0)
    return spool
