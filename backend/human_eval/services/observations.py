"""Observations and attachments (owner B2): bug / vulnerability / note reports and screenshots.

Participants log :class:`~backend.human_eval.models.Observation` rows against one
app slot of an assignment. Portal payloads (TS ``Observation``) name the app by
its display slot only — never the task-app id; ``researcher=True`` produces TS
``ResearcherObservation`` (participant, app id and label, researcher attachment URLs).

Rules (spec §5.9, R2):

- ``slot`` must be one of the assignment's slots and cannot change afterwards;
  ``vulnerability`` reports need a security block with ``reports: true``, ``bug``
  reports a bugs block (notes are always allowed); ``category`` is one of
  ``checklists.OBSERVATION_CATEGORIES`` for the kind; ``severity`` is the *tester's
  own* rating (a Severity value or ""); ``location`` is a relative path ("/...") or
  an http(s) URL; ``block_id`` / ``checklist_item_id`` must exist in the design; no
  text field may contain U+0000 (Postgres cannot store it: 400, not a 500 on save).
- At most :data:`MAX_OBSERVATIONS_PER_ASSIGNMENT` observations per assignment.
- Attachments: images only, sniffed from the magic bytes (PNG, JPEG, GIF, WEBP; the
  client's content type and file name are ignored), at most
  ``settings.HUMAN_EVAL_ATTACHMENT_MAX_BYTES`` each, :data:`MAX_ATTACHMENTS_PER_OBSERVATION`
  per observation and :data:`MAX_ATTACHMENT_BYTES_PER_PARTICIPANT` per participant. Stored
  as ``upload.<ext>`` (the model's ``upload_to`` makes the name random); served by
  :func:`file_response` with the sniffed type, ``nosniff``, a sandboxing CSP and no caching.
- Every mutation honours the parent assignment's read-only rule (409 ``read_only``),
  validates before writing and runs atomically; quotas are 409 ``quota_exceeded``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse

from backend.human_eval.models import Assignment
from backend.human_eval.models import Attachment
from backend.human_eval.models import Observation
from backend.human_eval.services import access
from backend.human_eval.services import context as context_service
from backend.human_eval.services import design as design_service
from backend.human_eval.services.checklists import OBSERVATION_CATEGORY_VALUES
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.errors import conflict
from backend.human_eval.services.errors import invalid

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

MAX_OBSERVATIONS_PER_ASSIGNMENT = 100
MAX_ATTACHMENTS_PER_OBSERVATION = 5
MAX_ATTACHMENT_BYTES_PER_PARTICIPANT = 50 * 1024 * 1024
MAX_TITLE = 200
MAX_LONG_TEXT = 20000
MAX_LOCATION = 500
MAX_ORIGINAL_NAME = 200

KINDS = tuple(Observation.Kind.values)
SEVERITIES = tuple(Observation.Severity.values)
_CWE = re.compile(r"^(?:CWE-)?(\d{1,7})$", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TEXT_FIELDS = ("title", "description", "steps", "location", "category", "severity", "cwe", "checklist_item_id")

#: Sniffed image types: content type → file extension.
IMAGE_TYPES: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


def portal_attachment_url(attachment_id: Any) -> str:
    return f"/api/human-eval/portal/attachments/{attachment_id}/"


def researcher_attachment_url(task_id: Any, attachment_id: Any) -> str:
    return f"/api/human-eval/tasks/{task_id}/attachments/{attachment_id}/"


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def serialize_attachment(att: Attachment, *, researcher: bool = False) -> dict[str, Any]:
    """TS ``ObservationAttachment`` with the portal or researcher URL."""
    if researcher:
        url = researcher_attachment_url(att.observation.assignment.task_id, att.id)
    else:
        url = portal_attachment_url(att.id)
    return {
        "id": str(att.id),
        "content_type": att.content_type,
        "size": att.size,
        "original_name": att.original_name,
        "url": url,
        "created_at": att.created_at.isoformat(),
    }


def _slot_of(obs: Observation) -> str:
    for slot, app in context_service.slot_apps(obs.assignment):
        if app is not None and app.pk == obs.task_app_id:
            return slot
    return ""


def serialize_observation(obs: Observation, *, researcher: bool = False) -> dict[str, Any]:
    """TS ``Observation`` (portal) or ``ResearcherObservation`` (``researcher=True``)."""
    payload: dict[str, Any] = {
        "id": str(obs.id),
        "assignment_id": str(obs.assignment_id),
        "app_slot": _slot_of(obs),
        "block_id": obs.block_id,
        "kind": obs.kind,
        "title": obs.title,
        "description": obs.description,
        "steps": obs.steps,
        "location": obs.location,
        "category": obs.category,
        "severity": obs.severity,
        "cwe": obs.cwe,
        "checklist_item_id": obs.checklist_item_id,
        "attachments": [serialize_attachment(att, researcher=researcher) for att in obs.attachments.all()],
        "created_at": obs.created_at.isoformat(),
        "updated_at": obs.updated_at.isoformat(),
    }
    if researcher:
        participant = obs.assignment.participant
        payload["participant"] = {"id": str(participant.id), "label": participant.label}
        payload["app_id"] = str(obs.task_app_id)
        payload["app_label"] = obs.task_app.label
    return payload


def list_observations(assignment: Assignment) -> list[dict[str, Any]]:
    """The assignment's observations (TS ``Observation[]``, oldest first)."""
    rows = (
        Observation.objects.filter(assignment=assignment)
        .select_related("assignment", "assignment__item", "assignment__item__app_a", "assignment__item__app_b")
        .prefetch_related("attachments")
        .order_by("created_at", "id")
    )
    return [serialize_observation(obs) for obs in rows]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def valid_location(value: str) -> bool:
    """A relative path ("/...", not protocol-relative) or an absolute http(s) URL, without whitespace."""
    if not value:
        return True
    if any(ch.isspace() for ch in value) or _CONTROL.search(value):
        return False
    if value.startswith("/"):
        return not value.startswith("//")
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _checklist_ids(design: dict[str, Any], block_id: str) -> set[str]:
    blocks = design_service.blocks_of_type(design, "security")
    if block_id:
        blocks = [block for block in blocks if block["id"] == block_id]
    return {item["id"] for block in blocks for item in block.get("checklist") or []}


def _clean_fields(
    assignment: Assignment,
    data: dict[str, Any],
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Validate a complete observation state; returns the model field values (400 with field errors).

    ``errors`` may carry problems the caller found already (they are reported together).
    """
    design = assignment.task.design or {}
    errors = list(errors or [])

    def text(field: str, limit: int, *, required: bool = False) -> str:
        value = data.get(field)
        if value is None:
            value = ""
        if not isinstance(value, str):
            errors.append({"path": field, "message": "Must be text"})
            return ""
        if "\x00" in value:  # Postgres text cannot store U+0000: a 400, never a 500 on save
            errors.append({"path": field, "message": "Must not contain the NUL character (U+0000)"})
            return ""
        if field in ("title", "location", "category", "severity", "cwe", "checklist_item_id", "block_id"):
            value = value.strip()
        if required and not value:
            errors.append({"path": field, "message": "Required"})
        elif len(value) > limit:
            errors.append({"path": field, "message": f"At most {limit} characters"})
        return value

    kind = data.get("kind")
    if kind not in KINDS:
        errors.append({"path": "kind", "message": "Choose bug, vulnerability or note"})
    elif kind == Observation.Kind.VULNERABILITY and not any(
        block.get("reports") for block in design_service.blocks_of_type(design, "security")
    ):
        errors.append({"path": "kind", "message": "Vulnerability reports are not part of this task"})
    elif kind == Observation.Kind.BUG and not design_service.blocks_of_type(design, "bugs"):
        errors.append({"path": "kind", "message": "Bug reports are not part of this task"})

    fields: dict[str, Any] = {
        "kind": kind,
        "block_id": text("block_id", 40),
        "title": text("title", MAX_TITLE, required=True),
        "description": text("description", MAX_LONG_TEXT),
        "steps": text("steps", MAX_LONG_TEXT),
        "location": text("location", MAX_LOCATION),
        "category": text("category", 60),
        "severity": text("severity", 20),
        "cwe": text("cwe", 20),
        "checklist_item_id": text("checklist_item_id", 40),
    }
    if fields["block_id"] and design_service.block_by_id(design, fields["block_id"]) is None:
        errors.append({"path": "block_id", "message": "No block with this id"})
    if not valid_location(fields["location"]):
        errors.append({"path": "location", "message": "Use a path starting with / or an http(s) URL"})
    if fields["category"] and kind in KINDS and fields["category"] not in OBSERVATION_CATEGORY_VALUES.get(kind, ()):
        errors.append({"path": "category", "message": "Not a category for this kind of report"})
    if fields["severity"] and fields["severity"] not in SEVERITIES:
        errors.append({"path": "severity", "message": "Choose critical, high, medium, low or info"})
    if fields["cwe"]:
        match = _CWE.match(fields["cwe"])
        if match is None:
            errors.append({"path": "cwe", "message": "Use a CWE number such as CWE-79"})
        else:
            fields["cwe"] = f"CWE-{int(match.group(1))}"
    if fields["checklist_item_id"] and fields["checklist_item_id"] not in _checklist_ids(design, fields["block_id"]):
        errors.append({"path": "checklist_item_id", "message": "No checklist item with this id"})
    if errors:
        msg = "The report is not valid"
        raise invalid(msg, errors)
    return fields


def _lock_assignment(assignment_id: Any) -> Assignment:
    return (
        Assignment.objects.select_for_update(of=("self",))
        .select_related("task", "participant", "item", "item__app_a", "item__app_b")
        .get(pk=assignment_id)
    )


# ---------------------------------------------------------------------------
# Observation mutations
# ---------------------------------------------------------------------------


def create_observation(assignment: Assignment, data: dict[str, Any]) -> Observation:
    """Create an observation from TS ``ObservationInput`` (409 read_only / quota_exceeded, 400 invalid)."""
    with transaction.atomic():
        locked = _lock_assignment(assignment.pk)
        access.ensure_writable(locked)
        slot = data.get("slot")
        task_app = next((app for name, app in context_service.slot_apps(locked) if name == slot), None)
        slot_errors = [] if task_app is not None else [{"path": "slot", "message": "Not an app of this assignment"}]
        fields = _clean_fields(locked, data, slot_errors)
        if Observation.objects.filter(assignment=locked).count() >= MAX_OBSERVATIONS_PER_ASSIGNMENT:
            msg = f"At most {MAX_OBSERVATIONS_PER_ASSIGNMENT} reports per item"
            raise conflict(msg, "quota_exceeded")
        context_service.mark_activity(locked)
        return Observation.objects.create(assignment=locked, task_app=task_app, **fields)


def update_observation(obs: Observation, data: dict[str, Any]) -> Observation:
    """Apply a partial TS ``ObservationInput`` (the slot cannot change; same rules as create)."""
    with transaction.atomic():
        locked = _lock_assignment(obs.assignment_id)
        access.ensure_writable(locked)
        current_slot = _slot_of(obs)
        if data.get("slot") is not None and data["slot"] != current_slot:
            msg = "The report is not valid"
            raise invalid(msg, [{"path": "slot", "message": "The app of a report cannot be changed"}])
        merged = {"kind": obs.kind, "block_id": obs.block_id, **{field: getattr(obs, field) for field in _TEXT_FIELDS}}
        merged.update({key: value for key, value in data.items() if key != "slot" and value is not None})
        fields = _clean_fields(locked, merged)
        for field, value in fields.items():
            setattr(obs, field, value)
        obs.save()
        context_service.mark_activity(locked)
    return obs


def delete_observation(obs: Observation) -> None:
    """Delete an observation and its attachments (files are removed on commit by the post_delete signal)."""
    with transaction.atomic():
        locked = _lock_assignment(obs.assignment_id)
        access.ensure_writable(locked)
        obs.delete()
        context_service.mark_activity(locked)


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


def sniff_image(head: bytes) -> str | None:
    """The image content type from the file's magic bytes (PNG, JPEG, GIF, WEBP), else None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def max_attachment_bytes() -> int:
    return int(getattr(settings, "HUMAN_EVAL_ATTACHMENT_MAX_BYTES", 5 * 1024 * 1024))


def _original_name(name: Any) -> str:
    """The client's file name for display only: base name, control characters removed, ≤ 200 chars."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return _CONTROL.sub("", base).strip()[:MAX_ORIGINAL_NAME]


def add_attachment(obs: Observation, upload: UploadedFile) -> Attachment:
    """Attach an uploaded screenshot to the observation (sniffed type, size and count quotas)."""
    limit = max_attachment_bytes()
    with transaction.atomic():
        locked = _lock_assignment(obs.assignment_id)
        access.ensure_writable(locked)
        size = int(upload.size or 0)
        if size <= 0:
            msg = "The file is empty"
            raise HumanEvalServiceError(400, msg, "unsupported_file", [{"path": "file", "message": msg}])
        if size > limit:
            msg = f"Files can be at most {limit // (1024 * 1024)} MiB"
            raise HumanEvalServiceError(400, msg, "file_too_large", [{"path": "file", "message": msg}])
        upload.seek(0)
        data = upload.read(limit + 1)
        if len(data) > limit:
            msg = f"Files can be at most {limit // (1024 * 1024)} MiB"
            raise HumanEvalServiceError(400, msg, "file_too_large", [{"path": "file", "message": msg}])
        content_type = sniff_image(data[:16])
        if content_type is None:
            msg = "Only PNG, JPEG, GIF or WEBP images can be attached"
            raise HumanEvalServiceError(400, msg, "unsupported_file", [{"path": "file", "message": msg}])
        if Attachment.objects.filter(observation=obs).count() >= MAX_ATTACHMENTS_PER_OBSERVATION:
            msg = f"At most {MAX_ATTACHMENTS_PER_OBSERVATION} files per report"
            raise conflict(msg, "quota_exceeded")
        used = (
            Attachment.objects.filter(observation__assignment__participant_id=locked.participant_id).aggregate(
                total=Sum("size"),
            )["total"]
            or 0
        )
        if used + len(data) > MAX_ATTACHMENT_BYTES_PER_PARTICIPANT:
            msg = f"Your files may take at most {MAX_ATTACHMENT_BYTES_PER_PARTICIPANT // (1024 * 1024)} MiB in total"
            raise conflict(msg, "quota_exceeded")
        attachment = Attachment(
            observation=obs,
            content_type=content_type,
            size=len(data),
            original_name=_original_name(getattr(upload, "name", "")),
        )
        attachment.file.save(f"upload.{IMAGE_TYPES[content_type]}", ContentFile(data), save=False)
        attachment.save()
        context_service.mark_activity(locked)
    return attachment


def delete_attachment(att: Attachment) -> None:
    """Delete an attachment (its file is removed on commit by the post_delete signal)."""
    with transaction.atomic():
        locked = _lock_assignment(att.observation.assignment_id)
        access.ensure_writable(locked)
        att.delete()
        context_service.mark_activity(locked)


def file_response(att: Attachment) -> HttpResponse:
    """The attachment's bytes with safe headers (sniffed type, inline, nosniff, sandbox CSP, no caching)."""
    content_type = att.content_type if att.content_type in IMAGE_TYPES else "application/octet-stream"
    extension = IMAGE_TYPES.get(content_type, "bin")
    try:
        with att.file.open("rb") as handle:
            data = handle.read()
    except (FileNotFoundError, ValueError, OSError) as exc:
        msg = "Attachment not found"
        raise HumanEvalServiceError(404, msg, "not_found") from exc
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = f'inline; filename="{att.id}.{extension}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=0"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
