"""Task progress and the responses browser of the researcher (owner D2).

**Progress** (TS ``TaskProgress``) is descriptive in the sense of
``services/counts.py``: researcher preview participants and their assignments
are left out; manually excluded participants and assignments still count
(exclusions only matter for results).

- ``assignments_by_status`` / ``participants_by_status`` always carry every status key.
- ``coverage_histogram``: for every *active* item, its number of submitted assignments;
  dense bins ``0..max`` (``[]`` before items exist).
- ``timeline``: submitted assignments per UTC calendar day, days without submissions
  filled with 0 between the first and the last day (sparse when that span exceeds
  :data:`MAX_DENSE_TIMELINE_DAYS`).
- ``median_minutes_per_item``: median time of the submitted assignments, from the
  heartbeat ``active_seconds`` (visible-tab time) and, for assignments without
  heartbeat time, ``submitted_at - started_at``; None when nothing is timed.

**Responses browser** (TS ``AssignmentSummary`` / ``AssignmentDetail``,
``ResearcherObservation``, ``HttpExchangeRecord``, attachments): every nested
object is looked up with ``task=task`` (attachments through
``observation__assignment__task``) and a mismatch, an unknown id or a malformed
id is a 404 (spec R2). Filter ids given in the query string follow the same
rule. The browser lists researcher preview participants too (marked by
``participant.kind == "preview"``) so a researcher can check what a pilot run
recorded; progress, results and exports never include them.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.db.models import Count
from django.db.models import IntegerField
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models import Subquery
from django.db.models.functions import Coalesce
from django.db.models.functions import TruncDate
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import Attachment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import HttpExchange
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.services import observations as observation_service
from backend.human_eval.services import quality
from backend.human_eval.services.access import parse_uuid
from backend.human_eval.services.context import slot_apps
from backend.human_eval.services.counts import counted_assignments
from backend.human_eval.services.counts import counted_participants
from backend.human_eval.services.counts import task_counts
from backend.human_eval.services.errors import invalid
from backend.human_eval.services.errors import not_found

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from backend.human_eval.models import EvalTask

MAX_DENSE_TIMELINE_DAYS = 400
MAX_EXCLUSION_REASON = 2000


def _by_status(rows, choices) -> dict[str, int]:
    result = dict.fromkeys(choices, 0)
    for row in rows:
        result[row["status"]] = result.get(row["status"], 0) + row["n"]
    return result


def assignments_by_status(task: EvalTask) -> dict[str, int]:
    """``{status: count}`` over every :class:`Assignment` status (preview excluded)."""
    rows = counted_assignments(task).order_by().values("status").annotate(n=Count("id"))
    return _by_status(rows, Assignment.Status.values)


def participants_by_status(task: EvalTask) -> dict[str, int]:
    """``{status: count}`` over every :class:`Participant` status (preview excluded)."""
    rows = counted_participants(task).order_by().values("status").annotate(n=Count("id"))
    return _by_status(rows, Participant.Status.values)


def coverage_histogram(task: EvalTask) -> list[dict[str, int]]:
    """``[{ratings, items}]``: how many active items have 0, 1, 2, ... submitted assignments."""
    submitted: dict[Any, int] = dict.fromkeys(
        EvalItem.objects.filter(task=task, is_active=True).values_list("id", flat=True),
        0,
    )
    if not submitted:
        return []
    rows = (
        counted_assignments(task)
        .filter(status=Assignment.Status.SUBMITTED, item_id__in=list(submitted))
        .order_by()
        .values("item_id")
        .annotate(n=Count("id"))
    )
    for row in rows:
        submitted[row["item_id"]] = row["n"]
    bins = [0] * (max(submitted.values()) + 1)
    for ratings in submitted.values():
        bins[ratings] += 1
    return [{"ratings": ratings, "items": items} for ratings, items in enumerate(bins)]


def timeline(task: EvalTask) -> list[dict[str, Any]]:
    """``[{date: "YYYY-MM-DD", submitted}]`` per UTC day, oldest first."""
    rows = (
        counted_assignments(task)
        .filter(status=Assignment.Status.SUBMITTED, submitted_at__isnull=False)
        .annotate(day=TruncDate("submitted_at", tzinfo=dt.UTC))
        .order_by()
        .values("day")
        .annotate(n=Count("id"))
    )
    per_day: dict[dt.date, int] = {row["day"]: row["n"] for row in rows if row["day"] is not None}
    if not per_day:
        return []
    first, last = min(per_day), max(per_day)
    if (last - first).days <= MAX_DENSE_TIMELINE_DAYS:
        days = [first + dt.timedelta(days=offset) for offset in range((last - first).days + 1)]
    else:
        days = sorted(per_day)
    return [{"date": day.isoformat(), "submitted": per_day.get(day, 0)} for day in days]


def assignment_seconds(active_seconds: int, started_at: Any, submitted_at: Any) -> float | None:
    """Time spent on one submitted assignment: heartbeat active seconds, else submitted - started, else None."""
    if active_seconds and active_seconds > 0:
        return float(active_seconds)
    if started_at is not None and submitted_at is not None and submitted_at >= started_at:
        return (submitted_at - started_at).total_seconds()
    return None


def median_minutes_per_item(task: EvalTask) -> float | None:
    """Median minutes per submitted assignment (rounded to 0.1), or None when none is timed."""
    rows = (
        counted_assignments(task)
        .filter(status=Assignment.Status.SUBMITTED)
        .values_list("active_seconds", "started_at", "submitted_at")
    )
    seconds = [value for value in (assignment_seconds(*row) for row in rows) if value is not None]
    if not seconds:
        return None
    return round(statistics.median(seconds) / 60, 1)


def task_progress(task: EvalTask) -> dict[str, Any]:
    """TS ``TaskProgress`` for the task."""
    return {
        "counts": task_counts(task),
        "assignments_by_status": assignments_by_status(task),
        "participants_by_status": participants_by_status(task),
        "coverage_histogram": coverage_histogram(task),
        "timeline": timeline(task),
        "median_minutes_per_item": median_minutes_per_item(task),
    }


# ---------------------------------------------------------------------------
# Task-scoped lookups
# ---------------------------------------------------------------------------


def _task_object(model, task: EvalTask, object_id: Any, what: str, *, task_path: str = "task"):
    """``model`` row ``object_id`` belonging to ``task`` (404 for unknown, foreign or malformed ids)."""
    pk = parse_uuid(object_id)
    row = model.objects.filter(pk=pk, **{task_path: task}).first() if pk is not None else None
    if row is None:
        msg = f"{what} not found"
        raise not_found(msg)
    return row


def task_participant(task: EvalTask, participant_id: Any) -> Participant:
    """A participant of the task (404 otherwise)."""
    return _task_object(Participant, task, participant_id, "Participant")


def task_app(task: EvalTask, app_id: Any) -> EvalTaskApp:
    """A task app of the task (404 otherwise)."""
    return _task_object(EvalTaskApp, task, app_id, "App")


def _assignment_queryset(task: EvalTask) -> QuerySet[Assignment]:
    return Assignment.objects.filter(task=task).select_related(
        "participant",
        "item",
        "item__app_a",
        "item__app_b",
    )


def researcher_assignment(task: EvalTask, assignment_id: Any, *, for_update: bool = False) -> Assignment:
    """An assignment of the task (404 otherwise); ``for_update`` locks its row."""
    pk = parse_uuid(assignment_id)
    qs = _assignment_queryset(task)
    if for_update:
        qs = qs.select_for_update(of=("self",))
    assignment = qs.filter(pk=pk).first() if pk is not None else None
    if assignment is None:
        msg = "Assignment not found"
        raise not_found(msg)
    return assignment


def researcher_attachment(task: EvalTask, attachment_id: Any) -> Attachment:
    """An attachment of an observation of the task (404 otherwise)."""
    return _task_object(Attachment, task, attachment_id, "Attachment", task_path="observation__assignment__task")


# ---------------------------------------------------------------------------
# Assignments browser
# ---------------------------------------------------------------------------


def _count_subquery(model, field: str = "assignment") -> Coalesce:
    rows = model.objects.filter(**{field: OuterRef("pk")}).order_by().values(field).annotate(n=Count("id")).values("n")
    return Coalesce(Subquery(rows, output_field=IntegerField()), 0)


def assignment_rows(
    task: EvalTask,
    *,
    participant_id: Any = None,
    app_id: Any = None,
    status: str | None = None,
) -> QuerySet[Assignment]:
    """The task's assignments for the browser, newest first, with observation / HTTP request counts.

    ``participant_id`` and ``app_id`` must belong to the task (404 otherwise); ``app_id`` matches either
    app of an item; an unknown ``status`` is a 400.
    """
    if status and status not in Assignment.Status.values:
        msg = "Unknown assignment status"
        raise invalid(msg, [{"path": "status", "message": f"must be one of {', '.join(Assignment.Status.values)}"}])
    qs = _assignment_queryset(task)
    if participant_id:
        qs = qs.filter(participant=task_participant(task, participant_id))
    if app_id:
        app = task_app(task, app_id)
        qs = qs.filter(Q(item__app_a=app) | Q(item__app_b=app))
    if status:
        qs = qs.filter(status=status)
    return qs.annotate(
        observation_count=_count_subquery(Observation),
        http_request_count=_count_subquery(HttpExchange),
    ).order_by("-created_at", "-id")


def _counts_of(assignment: Assignment) -> tuple[int, int]:
    observations = getattr(assignment, "observation_count", None)
    if observations is None:
        observations = assignment.observations.count()
    requests = getattr(assignment, "http_request_count", None)
    if requests is None:
        requests = assignment.http_exchanges.count()
    return int(observations), int(requests)


def serialize_assignment_summary(assignment: Assignment, *, flags: dict[str, list[str]]) -> dict[str, Any]:
    """TS ``AssignmentSummary``; ``flags`` is ``quality.assignment_flags(task)``."""
    participant = assignment.participant
    observation_count, http_request_count = _counts_of(assignment)
    return {
        "id": assignment.pk,
        "participant": {"id": participant.pk, "label": participant.label, "kind": participant.kind},
        "item_id": assignment.item_id,
        "apps": [{"id": app.pk, "label": app.label, "slot": slot} for slot, app in slot_apps(assignment)],
        "status": assignment.status,
        "position": assignment.position,
        "swapped": assignment.swapped,
        "skip_reason": assignment.skip_reason,
        "started_at": assignment.started_at,
        "submitted_at": assignment.submitted_at,
        "active_seconds": assignment.active_seconds,
        "wall_seconds": assignment.wall_seconds,
        "excluded": assignment.excluded,
        "exclusion_reason": assignment.exclusion_reason,
        "excluded_at": assignment.excluded_at,
        "observation_count": observation_count,
        "http_request_count": http_request_count,
        "quality_flags": list(flags.get(str(assignment.pk), [])),
    }


def assignment_flags(task: EvalTask) -> dict[str, list[str]]:
    """``quality.assignment_flags(task)`` (``{}`` when it returns nothing)."""
    return quality.assignment_flags(task) or {}


def normalized_context(context: Any) -> dict[str, Any]:
    """TS ``AssignmentContext`` from a stored context (``{}`` for assignments never opened)."""
    stored = context if isinstance(context, dict) else {}
    result = dict(stored)
    result["blocks"] = stored.get("blocks") if isinstance(stored.get("blocks"), dict) else {}
    result["hosting"] = stored.get("hosting") if isinstance(stored.get("hosting"), dict) else {}
    result["instructions_sha256"] = str(stored.get("instructions_sha256") or "")
    return result


def assignment_detail(task: EvalTask, assignment: Assignment) -> dict[str, Any]:
    """TS ``AssignmentDetail``: summary + frozen context, responses, observations and finding verdicts."""
    flags = assignment_flags(task)
    detail = serialize_assignment_summary(assignment, flags=flags)
    observations = (
        Observation.objects.filter(assignment=assignment)
        .select_related("assignment", "assignment__participant", "task_app")
        .prefetch_related("attachments")
        .order_by("created_at", "id")
    )
    for observation in observations:
        observation.assignment = assignment  # reuse the loaded item / apps for the slot lookup
    detail.update(
        {
            "context": normalized_context(assignment.context),
            "responses": {
                response.block_id: {"answers": response.answers, "updated_at": response.updated_at}
                for response in assignment.responses.order_by("block_id")
            },
            "observations": [
                observation_service.serialize_observation(observation, researcher=True) for observation in observations
            ],
            "finding_verdicts": [
                {
                    "finding_key": verdict.finding_key,
                    "verdict": verdict.verdict,
                    "note": verdict.note,
                    "finding_snapshot": verdict.finding_snapshot if isinstance(verdict.finding_snapshot, dict) else {},
                    "updated_at": verdict.updated_at,
                }
                for verdict in assignment.finding_verdicts.order_by("created_at", "id")
            ],
            "skip_note": assignment.skip_note,
        },
    )
    return detail


def set_assignment_exclusion(
    task: EvalTask,
    assignment_id: Any,
    *,
    excluded: bool,
    reason: str = "",
    user: Any = None,
) -> Assignment:
    """Exclude / re-include an assignment manually (validated before any write).

    Excluding stamps ``excluded_at`` / ``excluded_by`` (kept when an excluded assignment only gets a new
    reason); re-including clears them and the reason. Manual exclusions after the task opened make
    the results non-confirmatory (``post_hoc_exclusions``).
    """
    reason = (reason or "").strip()
    if len(reason) > MAX_EXCLUSION_REASON:
        msg = "Exclusion reason is too long"
        raise invalid(msg, [{"path": "exclusion_reason", "message": f"at most {MAX_EXCLUSION_REASON} characters"}])
    with transaction.atomic():
        assignment = researcher_assignment(task, assignment_id, for_update=True)
        if excluded:
            if not assignment.excluded:
                assignment.excluded_at = timezone.now()
                assignment.excluded_by = user if getattr(user, "is_authenticated", False) else None
            assignment.excluded = True
            assignment.exclusion_reason = reason
        else:
            assignment.excluded = False
            assignment.exclusion_reason = ""
            assignment.excluded_at = None
            assignment.excluded_by = None
        assignment.save(update_fields=["excluded", "exclusion_reason", "excluded_at", "excluded_by"])
    return assignment


# ---------------------------------------------------------------------------
# Observations and HTTP log
# ---------------------------------------------------------------------------


def observation_rows(
    task: EvalTask,
    *,
    kind: str | None = None,
    app_id: Any = None,
    participant_id: Any = None,
) -> QuerySet[Observation]:
    """The task's observations, newest first (filters as TS ``ObservationQuery``; foreign ids → 404)."""
    if kind and kind not in Observation.Kind.values:
        msg = "Unknown observation kind"
        raise invalid(msg, [{"path": "kind", "message": f"must be one of {', '.join(Observation.Kind.values)}"}])
    qs = (
        Observation.objects.filter(assignment__task=task)
        .select_related(
            "assignment",
            "assignment__participant",
            "assignment__item",
            "assignment__item__app_a",
            "assignment__item__app_b",
            "task_app",
        )
        .prefetch_related("attachments")
    )
    if kind:
        qs = qs.filter(kind=kind)
    if app_id:
        qs = qs.filter(task_app=task_app(task, app_id))
    if participant_id:
        qs = qs.filter(assignment__participant=task_participant(task, participant_id))
    return qs.order_by("-created_at", "-id")


def serialize_researcher_observation(observation: Observation) -> dict[str, Any]:
    """TS ``ResearcherObservation`` (B2's serializer with researcher attachment URLs)."""
    return observation_service.serialize_observation(observation, researcher=True)


def http_exchange_rows(
    task: EvalTask,
    *,
    participant_id: Any = None,
    app_id: Any = None,
    assignment_id: Any = None,
) -> QuerySet[HttpExchange]:
    """The task's HTTP console log, newest first (foreign filter ids → 404)."""
    qs = HttpExchange.objects.filter(assignment__task=task).select_related("assignment__participant", "task_app")
    if participant_id:
        qs = qs.filter(assignment__participant=task_participant(task, participant_id))
    if app_id:
        qs = qs.filter(task_app=task_app(task, app_id))
    if assignment_id:
        qs = qs.filter(assignment=researcher_assignment(task, assignment_id))
    return qs.order_by("-created_at", "-id")


def _headers(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): "" if item is None else str(item) for key, item in value.items()}


def serialize_http_exchange(exchange: HttpExchange) -> dict[str, Any]:
    """TS ``HttpExchangeRecord``."""
    return {
        "id": exchange.pk,
        "assignment_id": exchange.assignment_id,
        "participant_label": exchange.assignment.participant.label,
        "app_label": exchange.task_app.label,
        "method": exchange.method,
        "path": exchange.path,
        "request_headers": _headers(exchange.request_headers),
        "request_body": exchange.request_body,
        "status_code": exchange.status_code,
        "response_headers": _headers(exchange.response_headers),
        "response_body": exchange.response_body,
        "response_truncated": exchange.response_truncated,
        "elapsed_ms": exchange.elapsed_ms,
        "error": exchange.error,
        "created_at": exchange.created_at,
    }
