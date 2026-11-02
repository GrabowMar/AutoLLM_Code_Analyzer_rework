"""services/observations.py: report validation, quotas, read-only rules, image sniffing, serving and serializers."""

from __future__ import annotations

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile

from backend.human_eval.models import Assignment
from backend.human_eval.models import Attachment
from backend.human_eval.models import EvalTask
from backend.human_eval.models import Observation
from backend.human_eval.services import context
from backend.human_eval.services import observations
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.tests.factories import PNG_BYTES
from backend.human_eval.tests.factories import AttachmentFactory
from backend.human_eval.tests.factories import ObservationFactory
from backend.human_eval.tests.factories import make_open_task
from backend.human_eval.tests.factories import workbench_assignment
from backend.human_eval.tests.factories import workbench_design

pytestmark = pytest.mark.django_db

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF_BYTES = b"GIF89a" + b"\x00" * 32
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
HTML_BYTES = b"<!doctype html><script>alert(document.cookie)</script>"

OBSERVATION_KEYS = {
    "id",
    "assignment_id",
    "app_slot",
    "block_id",
    "kind",
    "title",
    "description",
    "steps",
    "location",
    "category",
    "severity",
    "cwe",
    "checklist_item_id",
    "attachments",
    "created_at",
    "updated_at",
}


def _reload(assignment: Assignment) -> Assignment:
    return Assignment.objects.select_related("task", "participant", "item", "item__app_a", "item__app_b").get(
        pk=assignment.pk,
    )


def _assignment(**task_kwargs) -> Assignment:
    task = make_open_task(n_apps=1, design=task_kwargs.pop("design", workbench_design()), **task_kwargs)
    assignment = workbench_assignment(task)
    context.open_assignment(assignment)
    return _reload(assignment)


def _report(**overrides) -> dict:
    return {"slot": "single", "kind": "bug", "title": "Save does nothing", **overrides}


def _upload(data: bytes, name: str = "shot.png", content_type: str = "image/png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=content_type)


# --- create / update / delete -------------------------------------------------------


def test_create_observation_stores_the_report():
    assignment = _assignment()
    obs = observations.create_observation(
        assignment,
        _report(
            kind="vulnerability",
            block_id="sec",
            title="  Stored XSS  ",
            description="Comments render HTML",
            steps="1. Post <b>x</b>",
            location="/comments?id=1",
            category="A03",
            severity="high",
            cwe="79",
            checklist_item_id="xss",
        ),
    )
    assert obs.task_app_id == assignment.item.app_a_id
    assert obs.title == "Stored XSS"
    assert obs.cwe == "CWE-79"
    assert obs.category == "A03"
    payload = observations.serialize_observation(obs)
    assert set(payload) == OBSERVATION_KEYS
    assert payload["app_slot"] == "single"
    assert payload["severity"] == "high"  # the tester's own rating
    assert "app_id" not in payload
    assert str(obs.task_app_id) not in str(payload)
    assignment.refresh_from_db()
    assert assignment.last_activity_at is not None


def test_create_observation_validation_errors_are_reported_together():
    assignment = _assignment()
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.create_observation(
            assignment,
            _report(
                slot="A",
                title=" ",
                location="javascript:alert(1)",
                category="A03",  # a vulnerability category on a bug
                severity="urgent",
                cwe="CWE-abc",
                block_id="missing",
                checklist_item_id="nope",
            ),
        )
    assert excinfo.value.status == 400
    paths = {error["path"] for error in excinfo.value.errors}
    assert paths == {"slot", "title", "location", "category", "severity", "cwe", "block_id", "checklist_item_id"}
    assert not Observation.objects.exists()


@pytest.mark.parametrize(
    ("location", "ok"),
    [
        ("", True),
        ("/items/1", True),
        ("https://llm-1234abcd.apps.example.com/x?y=1", True),
        ("http://localhost:8000/", True),
        ("//evil.example.com/x", False),
        ("javascript:alert(1)", False),
        ("data:text/html,hi", False),
        ("ftp://host/file", False),
        ("items/1", False),
        ("/with space", False),
        ("/a\nb", False),
    ],
)
def test_location_must_be_a_relative_path_or_http_url(location, ok):
    assert observations.valid_location(location) is ok


def test_report_kinds_need_their_blocks():
    design = workbench_design()
    design["blocks"] = [block for block in design["blocks"] if block["type"] not in ("bugs",)]
    for block in design["blocks"]:
        if block["type"] == "security":
            block["reports"] = False
    assignment = _assignment(design=design)
    for kind in ("bug", "vulnerability"):
        with pytest.raises(HumanEvalServiceError) as excinfo:
            observations.create_observation(assignment, _report(kind=kind))
        assert [error["path"] for error in excinfo.value.errors] == ["kind"]
    note = observations.create_observation(assignment, _report(kind="note", title="General remark"))
    assert note.kind == "note"
    with pytest.raises(HumanEvalServiceError):
        observations.create_observation(assignment, _report(kind="note", category="ui"))
    with pytest.raises(HumanEvalServiceError):
        observations.create_observation(assignment, _report(kind="incident"))


def test_observation_quota_per_assignment():
    assignment = _assignment()
    Observation.objects.bulk_create(
        [
            Observation(assignment=assignment, task_app=assignment.item.app_a, kind="bug", title=f"Bug {i}")
            for i in range(observations.MAX_OBSERVATIONS_PER_ASSIGNMENT)
        ],
    )
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.create_observation(assignment, _report())
    assert (excinfo.value.status, excinfo.value.code) == (409, "quota_exceeded")
    assert Observation.objects.filter(assignment=assignment).count() == observations.MAX_OBSERVATIONS_PER_ASSIGNMENT


def test_update_observation_is_partial_and_keeps_the_slot():
    assignment = _assignment()
    obs = observations.create_observation(assignment, _report(description="old"))
    updated = observations.update_observation(obs, {"title": "Renamed", "severity": "low", "description": ""})
    assert (updated.title, updated.severity, updated.description, updated.kind) == ("Renamed", "low", "", "bug")
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.update_observation(obs, {"slot": "A"})
    assert excinfo.value.errors[0]["path"] == "slot"
    assert observations.update_observation(obs, {"slot": "single"}).title == "Renamed"
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.update_observation(obs, {"location": "//evil"})
    assert excinfo.value.errors[0]["path"] == "location"
    obs.refresh_from_db()
    assert obs.location == ""


def test_observation_mutations_on_read_only_assignments_are_409():
    assignment = _assignment()
    obs = observations.create_observation(assignment, _report())
    att = AttachmentFactory(observation=obs)
    Assignment.objects.filter(pk=assignment.pk).update(status=Assignment.Status.SUBMITTED)
    obs = Observation.objects.select_related("assignment").get(pk=obs.pk)
    calls = (
        lambda: observations.create_observation(_reload(assignment), _report()),
        lambda: observations.update_observation(obs, {"title": "x"}),
        lambda: observations.delete_observation(obs),
        lambda: observations.add_attachment(obs, _upload(PNG_BYTES)),
        lambda: observations.delete_attachment(att),
    )
    for call in calls:
        with pytest.raises(HumanEvalServiceError) as excinfo:
            call()
        assert (excinfo.value.status, excinfo.value.code) == (409, "read_only")
    assert Observation.objects.filter(assignment=assignment).count() == 1
    assert Attachment.objects.filter(observation=obs).count() == 1

    # The task stopped accepting: read-only too.
    other = _assignment()
    EvalTask.objects.filter(pk=other.task_id).update(status=EvalTask.Status.CLOSED)
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.create_observation(_reload(other), _report())
    assert excinfo.value.code == "read_only"


def test_delete_observation_removes_attachment_files_on_commit(django_capture_on_commit_callbacks):
    assignment = _assignment()
    obs = observations.create_observation(assignment, _report())
    att = observations.add_attachment(obs, _upload(PNG_BYTES))
    name = att.file.name
    assert default_storage.exists(name)
    with django_capture_on_commit_callbacks(execute=True):
        observations.delete_observation(obs)
    assert not Observation.objects.filter(pk=obs.pk).exists()
    assert not Attachment.objects.filter(pk=att.pk).exists()
    assert not default_storage.exists(name)


# --- attachments ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "content_type"),
    [(PNG_BYTES, "image/png"), (JPEG_BYTES, "image/jpeg"), (GIF_BYTES, "image/gif"), (WEBP_BYTES, "image/webp")],
)
def test_sniff_image_recognises_the_allowed_types(data, content_type):
    assert observations.sniff_image(data[:16]) == content_type


def test_sniff_image_rejects_everything_else():
    for data in (HTML_BYTES, b"%PDF-1.7", b"RIFF\x00\x00\x00\x00WAVE", b"", b"\x89PN"):
        assert observations.sniff_image(data) is None


def test_add_attachment_sniffs_and_stores_under_a_random_name():
    assignment = _assignment()
    obs = observations.create_observation(assignment, _report())
    att = observations.add_attachment(
        obs,
        _upload(JPEG_BYTES, name="../../etc/My\x00 shot.png", content_type="image/png"),
    )
    assert att.content_type == "image/jpeg"  # sniffed, the client's type is ignored
    assert att.size == len(JPEG_BYTES)
    assert att.original_name == "My shot.png"
    assert att.file.name.startswith("human_eval/")
    assert att.file.name.endswith(".jpg")
    assert "shot" not in att.file.name
    payload = observations.serialize_attachment(att)
    assert payload == {
        "id": str(att.id),
        "content_type": "image/jpeg",
        "size": len(JPEG_BYTES),
        "original_name": "My shot.png",
        "url": f"/api/human-eval/portal/attachments/{att.id}/",
        "created_at": att.created_at.isoformat(),
    }
    researcher = observations.serialize_attachment(att, researcher=True)
    assert researcher["url"] == f"/api/human-eval/tasks/{assignment.task_id}/attachments/{att.id}/"


def test_add_attachment_rejects_disguised_html_and_empty_files():
    assignment = _assignment()
    obs = observations.create_observation(assignment, _report())
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.add_attachment(obs, _upload(HTML_BYTES, name="cute-cat.png", content_type="image/png"))
    assert (excinfo.value.status, excinfo.value.code) == (400, "unsupported_file")
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.add_attachment(obs, _upload(b""))
    assert excinfo.value.status == 400
    assert not Attachment.objects.exists()


def test_add_attachment_size_limit(settings):
    settings.HUMAN_EVAL_ATTACHMENT_MAX_BYTES = 1024
    assignment = _assignment()
    obs = observations.create_observation(assignment, _report())
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.add_attachment(obs, _upload(PNG_BYTES + b"\x00" * 2048))
    assert (excinfo.value.status, excinfo.value.code) == (400, "file_too_large")
    assert observations.add_attachment(obs, _upload(PNG_BYTES)).size == len(PNG_BYTES)


def test_add_attachment_count_and_participant_quotas(monkeypatch):
    assignment = _assignment()
    obs = observations.create_observation(assignment, _report())
    for _ in range(observations.MAX_ATTACHMENTS_PER_OBSERVATION):
        observations.add_attachment(obs, _upload(PNG_BYTES))
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.add_attachment(obs, _upload(PNG_BYTES))
    assert (excinfo.value.status, excinfo.value.code) == (409, "quota_exceeded")

    # The participant's total across reports (and assignments) is capped too.
    monkeypatch.setattr(observations, "MAX_ATTACHMENT_BYTES_PER_PARTICIPANT", len(PNG_BYTES) * 6)
    second = observations.create_observation(assignment, _report(title="Another bug"))
    observations.add_attachment(second, _upload(PNG_BYTES))
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.add_attachment(second, _upload(PNG_BYTES))
    assert excinfo.value.code == "quota_exceeded"
    assert Attachment.objects.filter(observation__assignment=assignment).count() == 6


def test_file_response_headers():
    assignment = _assignment()
    obs = observations.create_observation(assignment, _report())
    att = observations.add_attachment(obs, _upload(PNG_BYTES))
    response = observations.file_response(att)
    assert response.status_code == 200
    assert response.content == PNG_BYTES
    assert response["Content-Type"] == "image/png"
    assert response["Content-Disposition"] == f'inline; filename="{att.id}.png"'
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "private, max-age=0"
    assert response["Content-Security-Policy"] == "default-src 'none'; sandbox"

    # A stored type outside the allowlist is never served as such.
    Attachment.objects.filter(pk=att.pk).update(content_type="text/html")
    att.refresh_from_db()
    assert observations.file_response(att)["Content-Type"] == "application/octet-stream"


# --- serializers --------------------------------------------------------------------


def test_serialize_observation_slots_and_researcher_variant():
    task = make_open_task(n_apps=2, mode="pairwise")
    item = task.items.get()
    assignment = workbench_assignment(task, item=item, swapped=True)
    obs = ObservationFactory(assignment=assignment, task_app=item.app_a, kind="note")
    AttachmentFactory(observation=obs)
    portal = observations.serialize_observation(obs)
    assert portal["app_slot"] == "B"  # app_a is shown on the right when swapped
    assert set(portal) == OBSERVATION_KEYS
    assert str(item.app_a_id) not in str(portal)
    researcher = observations.serialize_observation(obs, researcher=True)
    assert researcher["app_id"] == str(item.app_a_id)
    assert researcher["app_label"] == item.app_a.label
    assert researcher["participant"] == {"id": str(assignment.participant_id), "label": assignment.participant.label}
    assert researcher["attachments"][0]["url"].startswith(f"/api/human-eval/tasks/{task.id}/attachments/")
    listed = observations.list_observations(assignment)
    assert [entry["id"] for entry in listed] == [str(obs.id)]


# --- regressions -----------------------------------------------------------------------


def test_text_fields_with_a_nul_character_are_a_400_not_a_database_error():
    """C22: Postgres text cannot store U+0000; refuse it in validation instead of failing on save."""
    assignment = _assignment()
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.create_observation(
            assignment,
            _report(title="Null\x00byte", description="see\x00below", steps="1.\x00", location="/a\x00"),
        )
    assert excinfo.value.status == 400
    assert {error["path"] for error in excinfo.value.errors} == {"title", "description", "steps", "location"}
    assert not Observation.objects.exists()

    obs = observations.create_observation(assignment, _report(description="fine"))
    with pytest.raises(HumanEvalServiceError) as excinfo:
        observations.update_observation(obs, {"steps": "a\x00b"})
    assert excinfo.value.status == 400
    assert [error["path"] for error in excinfo.value.errors] == ["steps"]
    obs.refresh_from_db()
    assert obs.steps == ""
