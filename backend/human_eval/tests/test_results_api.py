"""Researcher results API (api/views/results.py): browser, attachments, HTTP log, results, export routes."""

from __future__ import annotations

import copy
import csv
import io
import json
import zipfile
from unittest import mock

import pytest

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.services import export
from backend.human_eval.services import observations as observation_service
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.tests.factories import PNG_BYTES
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import AttachmentFactory
from backend.human_eval.tests.factories import FindingVerdictFactory
from backend.human_eval.tests.factories import HttpExchangeFactory
from backend.human_eval.tests.factories import ObservationFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import ResponseFactory
from backend.human_eval.tests.factories import make_open_task
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

S = Assignment.Status
COMPUTE = "backend.human_eval.services.analysis.compute_results"
SUMMARY_KEYS = {
    "id",
    "participant",
    "item_id",
    "apps",
    "status",
    "position",
    "swapped",
    "skip_reason",
    "started_at",
    "submitted_at",
    "active_seconds",
    "wall_seconds",
    "excluded",
    "exclusion_reason",
    "excluded_at",
    "observation_count",
    "http_request_count",
    "quality_flags",
}
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
    "participant",
    "app_id",
    "app_label",
}


def base(task) -> str:
    return f"/api/human-eval/tasks/{task.pk}"


@pytest.fixture
def world():
    """Owner task with 2 apps, 2 participants + a preview, plus a second task of the same owner."""
    task = make_open_task(n_apps=2)
    owner = task.created_by
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    p1 = ParticipantFactory(task=task, number=1)
    p2 = ParticipantFactory(task=task, number=2, kind=Participant.Kind.CODE)
    preview = ParticipantFactory(task=task, number=3, kind=Participant.Kind.PREVIEW, user=owner)
    a1 = AssignmentFactory(task=task, participant=p1, item=items[0], status=S.SUBMITTED, position=1, skip_note="")
    a2 = AssignmentFactory(task=task, participant=p1, item=items[1], status=S.IN_PROGRESS, position=2)
    a3 = AssignmentFactory(
        task=task,
        participant=p2,
        item=items[1],
        status=S.SKIPPED,
        position=1,
        skip_reason="app_broken",
        skip_note="white screen",
    )
    a4 = AssignmentFactory(task=task, participant=preview, item=items[0], status=S.SUBMITTED, position=1)
    ResponseFactory(assignment=a1, block_id="ratings", answers={"criteria": {"functionality": 5}})
    bug = ObservationFactory(assignment=a1, task_app=items[0].app_a, kind=Observation.Kind.BUG, title="Save fails")
    attachment = AttachmentFactory(observation=bug)
    note = ObservationFactory(assignment=a2, task_app=items[1].app_a, kind=Observation.Kind.NOTE, title="Slow")
    FindingVerdictFactory(
        assignment=a1,
        task_app=items[0].app_a,
        finding_key="42",
        note="real issue",
        finding_snapshot={"id": "42", "tool": "bandit", "tool_severity": "LOW"},
    )
    exchanges = [
        HttpExchangeFactory(assignment=a1, task_app=items[0].app_a, path="/api/items", request_headers={"X-A": 1}),
        HttpExchangeFactory(assignment=a2, task_app=items[1].app_a, path="/api/other", status_code=None, error="x"),
    ]
    other = make_open_task(owner=owner, n_apps=1)
    other_item = EvalItem.objects.get(task=other)
    other_participant = ParticipantFactory(task=other, number=1)
    other_assignment = AssignmentFactory(task=other, participant=other_participant, item=other_item)
    other_observation = ObservationFactory(assignment=other_assignment, task_app=other_item.app_a)
    return {
        "task": task,
        "owner": owner,
        "items": items,
        "participants": [p1, p2, preview],
        "assignments": [a1, a2, a3, a4],
        "bug": bug,
        "note": note,
        "attachment": attachment,
        "exchanges": exchanges,
        "other": {
            "task": other,
            "app": other_item.app_a,
            "participant": other_participant,
            "assignment": other_assignment,
            "attachment": AttachmentFactory(observation=other_observation),
        },
    }


@pytest.fixture
def owner_client(client, world):
    client.force_login(world["owner"])
    return client


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/assignments/",
        "/observations/",
        "/http-log/",
        "/results/",
        "/export/bundle.zip",
        "/export/results.json",
        "/export/ratings.csv",
    ],
)
def test_other_users_and_anonymous_get_nothing(client, world, path):
    url = base(world["task"]) + path
    assert client.get(url).status_code == 401
    client.force_login(UserFactory())
    response = client.get(url)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_nested_ids_from_another_task_are_404(owner_client, world):
    task, other = world["task"], world["other"]
    url = base(task)
    assert owner_client.get(f"{url}/assignments/{other['assignment'].pk}/").status_code == 404
    patched = owner_client.patch(
        f"{url}/assignments/{other['assignment'].pk}/",
        data={"excluded": True},
        content_type="application/json",
    )
    assert patched.status_code == 404
    assert owner_client.get(f"{url}/attachments/{other['attachment'].pk}/").status_code == 404
    for path, key, value in (
        ("assignments", "participant_id", other["participant"].pk),
        ("assignments", "app_id", other["app"].pk),
        ("observations", "participant_id", other["participant"].pk),
        ("observations", "app_id", other["app"].pk),
        ("http-log", "participant_id", other["participant"].pk),
        ("http-log", "app_id", other["app"].pk),
        ("http-log", "assignment_id", other["assignment"].pk),
        ("assignments", "participant_id", "not-a-uuid"),
    ):
        response = owner_client.get(f"{url}/{path}/", {key: str(value)})
        assert response.status_code == 404, (path, key)
    # the other task's own routes still work for its owner
    assert owner_client.get(f"{base(other['task'])}/assignments/{other['assignment'].pk}/").status_code == 200


def test_staff_see_every_task(client, world):
    client.force_login(UserFactory(is_staff=True))
    assert client.get(f"{base(world['task'])}/assignments/").status_code == 200


# ---------------------------------------------------------------------------
# Assignments browser
# ---------------------------------------------------------------------------


def test_list_assignments_shape_and_counts(owner_client, world):
    response = owner_client.get(f"{base(world['task'])}/assignments/")
    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["page"], body["per_page"], body["pages"]) == (4, 1, 20, 1)
    rows = {row["id"]: row for row in body["items"]}
    a1, a2, a3, a4 = world["assignments"]
    first = rows[str(a1.pk)]
    assert set(first) == SUMMARY_KEYS
    assert first["participant"] == {"id": str(a1.participant_id), "label": "P001", "kind": "invite"}
    assert first["apps"] == [
        {"id": str(world["items"][0].app_a_id), "label": world["items"][0].app_a.label, "slot": "single"},
    ]
    assert (first["observation_count"], first["http_request_count"]) == (1, 1)
    assert rows[str(a3.pk)]["skip_reason"] == "app_broken"
    # the browser shows preview sessions (marked) so pilots can be checked
    assert rows[str(a4.pk)]["participant"]["kind"] == "preview"
    # newest first
    assert [row["id"] for row in body["items"]] == [str(a.pk) for a in (a4, a3, a2, a1)]


def test_list_assignments_filters_and_pagination(owner_client, world):
    url = f"{base(world['task'])}/assignments/"
    a1, a2, a3, _a4 = world["assignments"]
    p1 = world["participants"][0]
    by_participant = owner_client.get(url, {"participant_id": str(p1.pk)}).json()
    assert {row["id"] for row in by_participant["items"]} == {str(a1.pk), str(a2.pk)}
    by_app = owner_client.get(url, {"app_id": str(world["items"][1].app_a_id)}).json()
    assert {row["id"] for row in by_app["items"]} == {str(a2.pk), str(a3.pk)}
    by_status = owner_client.get(url, {"status": "skipped"}).json()
    assert [row["id"] for row in by_status["items"]] == [str(a3.pk)]
    page = owner_client.get(url, {"per_page": 3, "page": 2}).json()
    assert (page["total"], page["page"], page["per_page"], page["pages"], len(page["items"])) == (4, 2, 3, 2, 1)
    clamped = owner_client.get(url, {"per_page": 1000, "page": 99}).json()
    assert (clamped["per_page"], clamped["page"]) == (100, 1)
    bad = owner_client.get(url, {"status": "done"})
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid"
    assert bad.json()["errors"][0]["path"] == "status"


def test_assignment_quality_flags_come_from_quality(owner_client, world):
    a1 = world["assignments"][0]
    with mock.patch(
        "backend.human_eval.services.quality.assignment_flags",
        return_value={str(a1.pk): ["speeding", "straightlining"]},
    ) as flags:
        body = owner_client.get(f"{base(world['task'])}/assignments/").json()
    flags.assert_called_once()
    rows = {row["id"]: row for row in body["items"]}
    assert rows[str(a1.pk)]["quality_flags"] == ["speeding", "straightlining"]
    assert rows[str(world["assignments"][1].pk)]["quality_flags"] == []


def test_assignment_detail(owner_client, world):
    a1 = world["assignments"][0]
    response = owner_client.get(f"{base(world['task'])}/assignments/{a1.pk}/")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == SUMMARY_KEYS | {"context", "responses", "observations", "finding_verdicts", "skip_note"}
    # never-opened context is normalized to the TS AssignmentContext shape
    assert body["context"] == {"blocks": {}, "hosting": {}, "instructions_sha256": ""}
    assert body["responses"]["ratings"]["answers"] == {"criteria": {"functionality": 5}}
    assert "updated_at" in body["responses"]["ratings"]
    (observation,) = body["observations"]
    assert set(observation) == OBSERVATION_KEYS
    assert (observation["id"], observation["app_slot"]) == (str(world["bug"].pk), "single")
    assert observation["participant"]["label"] == "P001"
    attachment_url = observation["attachments"][0]["url"]
    assert attachment_url == f"/api/human-eval/tasks/{world['task'].pk}/attachments/{world['attachment'].pk}/"
    (verdict,) = body["finding_verdicts"]
    assert verdict["finding_key"] == "42"
    assert (verdict["verdict"], verdict["note"]) == ("tp", "real issue")
    assert verdict["finding_snapshot"] == {"id": "42", "tool": "bandit", "tool_severity": "LOW"}
    skipped = owner_client.get(f"{base(world['task'])}/assignments/{world['assignments'][2].pk}/").json()
    assert skipped["skip_note"] == "white screen"


def test_assignment_detail_keeps_a_frozen_context(owner_client, world):
    a2 = world["assignments"][1]
    context = {
        "blocks": {"sus": {"questions": []}},
        "hosting": {"single": {"container_name": "llm-1", "prior_users": 1}},
        "instructions_sha256": "c" * 64,
        "skip_hosting": {"single": {"state": "failed", "error": "boom"}},
    }
    Assignment.objects.filter(pk=a2.pk).update(context=context)
    body = owner_client.get(f"{base(world['task'])}/assignments/{a2.pk}/").json()
    assert body["context"] == context


def test_patch_assignment_exclusion(owner_client, world):
    a1 = world["assignments"][0]
    url = f"{base(world['task'])}/assignments/{a1.pk}/"
    response = owner_client.patch(
        url,
        data={"excluded": True, "exclusion_reason": "  saw the model name "},
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == SUMMARY_KEYS
    assert (body["excluded"], body["exclusion_reason"]) == (True, "saw the model name")
    a1.refresh_from_db()
    assert a1.excluded_by == world["owner"]
    first_at = a1.excluded_at
    assert first_at is not None
    # a new reason keeps the original exclusion time
    owner_client.patch(url, data={"excluded": True, "exclusion_reason": "other"}, content_type="application/json")
    a1.refresh_from_db()
    assert (a1.exclusion_reason, a1.excluded_at) == ("other", first_at)
    # re-including clears everything
    response = owner_client.patch(url, data={"excluded": False}, content_type="application/json")
    assert response.json()["excluded"] is False
    a1.refresh_from_db()
    assert (a1.excluded, a1.exclusion_reason, a1.excluded_at, a1.excluded_by) == (False, "", None, None)


def test_patch_assignment_rejects_long_reason_without_writing(owner_client, world):
    a1 = world["assignments"][0]
    response = owner_client.patch(
        f"{base(world['task'])}/assignments/{a1.pk}/",
        data={"excluded": True, "exclusion_reason": "x" * 2001},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["path"] == "exclusion_reason"
    a1.refresh_from_db()
    assert (a1.excluded, a1.excluded_at) == (False, None)


# ---------------------------------------------------------------------------
# Observations, attachments, HTTP log
# ---------------------------------------------------------------------------


def test_list_observations_with_filters(owner_client, world):
    url = f"{base(world['task'])}/observations/"
    body = owner_client.get(url).json()
    assert body["total"] == 2
    assert [row["id"] for row in body["items"]] == [str(world["note"].pk), str(world["bug"].pk)]  # newest first
    for row in body["items"]:
        assert set(row) == OBSERVATION_KEYS
    bug = body["items"][1]
    assert (bug["app_id"], bug["app_label"]) == (str(world["items"][0].app_a_id), world["items"][0].app_a.label)
    assert bug["attachments"][0]["url"].startswith(f"/api/human-eval/tasks/{world['task'].pk}/attachments/")
    assert [row["id"] for row in owner_client.get(url, {"kind": "bug"}).json()["items"]] == [str(world["bug"].pk)]
    by_app = owner_client.get(url, {"app_id": str(world["items"][1].app_a_id)}).json()
    assert [row["id"] for row in by_app["items"]] == [str(world["note"].pk)]
    by_participant = owner_client.get(url, {"participant_id": str(world["participants"][1].pk)}).json()
    assert by_participant["items"] == []
    bad = owner_client.get(url, {"kind": "complaint"})
    assert (bad.status_code, bad.json()["code"]) == (400, "invalid")


def test_list_observations_asks_for_researcher_payloads(owner_client, world):
    real = observation_service.serialize_observation
    with mock.patch.object(observation_service, "serialize_observation", wraps=real) as serialize:
        response = owner_client.get(f"{base(world['task'])}/observations/", {"kind": "bug"})
        detail = owner_client.get(f"{base(world['task'])}/assignments/{world['assignments'][0].pk}/")
    assert (response.status_code, detail.status_code) == (200, 200)
    assert [call.kwargs for call in serialize.call_args_list] == [{"researcher": True}, {"researcher": True}]


def test_attachment_download_headers(owner_client, world):
    attachment = world["attachment"]
    response = owner_client.get(f"{base(world['task'])}/attachments/{attachment.pk}/")
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PNG_BYTES
    assert response["Content-Type"] == "image/png"
    assert response["Content-Disposition"] == f'inline; filename="{attachment.pk}.png"'
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "private, max-age=0"
    assert response["Content-Security-Policy"] == "default-src 'none'; sandbox"


def test_attachment_with_unexpected_type_downloads_as_octet_stream(owner_client, world):
    attachment = world["attachment"]
    type(attachment).objects.filter(pk=attachment.pk).update(content_type="text/html")
    response = owner_client.get(f"{base(world['task'])}/attachments/{attachment.pk}/")
    assert response["Content-Type"] == "application/octet-stream"
    assert response["Content-Disposition"] == f'attachment; filename="{attachment.pk}.bin"'


def test_attachment_missing_file_is_404(owner_client, world):
    attachment = world["attachment"]
    attachment.file.storage.delete(attachment.file.name)
    response = owner_client.get(f"{base(world['task'])}/attachments/{attachment.pk}/")
    assert response.status_code == 404


def test_http_log(owner_client, world):
    url = f"{base(world['task'])}/http-log/"
    body = owner_client.get(url).json()
    assert body["total"] == 2
    newest, oldest = body["items"]
    assert newest["id"] == str(world["exchanges"][1].pk)
    assert set(oldest) == {
        "id",
        "assignment_id",
        "participant_label",
        "app_label",
        "method",
        "path",
        "request_headers",
        "request_body",
        "status_code",
        "response_headers",
        "response_body",
        "response_truncated",
        "elapsed_ms",
        "error",
        "created_at",
    }
    assert (oldest["participant_label"], oldest["app_label"]) == ("P001", world["items"][0].app_a.label)
    assert oldest["request_headers"] == {"X-A": "1"}  # values sent as strings
    assert (newest["status_code"], newest["error"]) == (None, "x")
    by_app = owner_client.get(url, {"app_id": str(world["items"][0].app_a_id)}).json()
    assert [row["id"] for row in by_app["items"]] == [str(world["exchanges"][0].pk)]
    by_assignment = owner_client.get(url, {"assignment_id": str(world["assignments"][1].pk)}).json()
    assert [row["id"] for row in by_assignment["items"]] == [str(world["exchanges"][1].pk)]
    by_participant = owner_client.get(url, {"participant_id": str(world["participants"][1].pk)}).json()
    assert by_participant["total"] == 0


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def _ci(low=0.1, high=0.9):
    return {"low": low, "high": high}


def _agreement(level="ordinal"):
    return {"alpha": 0.71, "level": level, "percent_agreement": 0.8, "n_units": 5, "n_values": 12}


def _group_score(group="1"):
    return {
        "group": group,
        "label": "Condition 1",
        "n_apps": 3,
        "n_ratings": 9,
        "mean": 5.5,
        "ci": _ci(4.9, 6.1),
        "floor": {"mean": 5.0, "ci": None, "n_apps": 4},
    }


def _app_score(app_id):
    return {
        "app_id": app_id,
        "label": "App 01",
        "group": "1",
        "n": 3,
        "mean": 6,
        "sd": 0.5,
        "median": 6,
        "na_count": 1,
    }


def _precision(key):
    return {
        "key": key,
        "label": key,
        "tp": 3,
        "fp": 1,
        "unsure": 0,
        "n_findings": 4,
        "n_apps": 2,
        "n_split": 1,
        "precision": 0.75,
        "ci": None,
        "majority_precision": 0.5,
        "weighted": True,
    }


def _bt(key):
    return {
        "key": key,
        "label": key,
        "component": "c1",
        "strength": 1.4,
        "log_strength": 0.336,
        "ci": _ci(-0.2, 0.8),
        "wins": 3.5,
        "losses": 1,
        "ties": 1,
        "comparisons": 5,
        "rank": 1,
    }


def sample_results(task) -> dict:
    """A TaskResults payload with every block type and optional keys both set and unset (TS contract)."""
    app_id = str(task.apps.first().pk)
    return {
        "task_id": str(task.pk),
        "generated_at": "2026-11-02T10:00:00+00:00",
        "provisional": True,
        "p_values_withheld": True,
        "confirmatory": False,
        "deviations": ["provisional"],
        "rule_exclusions": {"speeding": 2},
        "group_by": "condition",
        "include_excluded": False,
        "counts": {
            "participants": 3,
            "participants_included": 2,
            "assignments_submitted": 6,
            "assignments_included": 4,
            "assignments_skipped": 1,
            "assignments_excluded": 2,
        },
        "warnings": [
            {"code": "provisional", "message": "The task is still open."},
            {"code": "low_agreement", "message": "Alpha below 0.667.", "block_id": "ratings"},
        ],
        "groups": [{"key": "1", "label": "Condition 1", "n_apps_pool": 4, "n_apps_rated": 3, "n_apps_unevaluable": 1}],
        "apps": [
            {
                "app_id": app_id,
                "label": "App 01",
                "group": "1",
                "model_name": "Model A",
                "condition_label": "Condition 1",
                "app_slug": "todo",
                "job_id": None,
                "n_submitted": 3,
                "n_unavailable": 0,
            },
        ],
        "blocks": [
            {
                "type": "rating",
                "block_id": "ratings",
                "title": "Quality",
                "criteria": [
                    {
                        "criterion_id": "overall",
                        "label": "Overall",
                        "scale": {
                            "min": 1,
                            "max": 7,
                            "step": 1,
                            "min_label": "Bad",
                            "max_label": "Good",
                            "labels": {},
                        },
                        "per_app": [_app_score(app_id)],
                        "per_group": [_group_score()],
                        "agreement": _agreement(),
                    },
                ],
                "composite": {"per_app": [_app_score(app_id)], "per_group": [{**_group_score(), "floor": None}]},
            },
            {
                "type": "requirements",
                "block_id": "requirements",
                "title": "Requirements",
                "per_app": [
                    {
                        "app_id": app_id,
                        "label": "App 01",
                        "group": "1",
                        "n_raters": 2,
                        "compliance": 0.75,
                        "compliance_strict": 0.6,
                        "by_source": {"backend": 1, "api": None},
                        "blocked": 1,
                        "cannot_test": 1,
                        "conformance_pass_rate": 0.8,
                        "conformance_endpoint_pass_rate": None,
                        "conformance_status": "failed",
                    },
                ],
                "per_group": [_group_score()],
                "per_requirement": [
                    {
                        "req_id": "b_1",
                        "source": "backend",
                        "text": "Users can log in",
                        "n_ratings": 2,
                        "met": 1,
                        "partial": 0,
                        "not_met": 0,
                        "blocked": 1,
                        "cannot_test": 0,
                    },
                ],
                "agreement": _agreement("nominal"),
                "vs_conformance": [
                    {"scope": "endpoints", "rho": 0.5, "ci": _ci(), "n": 6, "excluded_apps": ["App 02"], "note": ""},
                    {"scope": "overall", "rho": None, "ci": None, "n": 2, "excluded_apps": [], "note": "different"},
                ],
                "endpoint_agreement": {
                    "n": 10,
                    "kappa": -0.25,
                    "both_pass": 4,
                    "human_only": 1,
                    "auto_only": 2,
                    "both_fail": 3,
                    "n_unmatched": 1,
                },
            },
            {
                "type": "security",
                "block_id": "security",
                "title": "Security",
                "checklist": [
                    {
                        "item_id": "xss",
                        "label": "XSS",
                        "category": "A03",
                        "per_group": [
                            {
                                "group": "1",
                                "label": "Condition 1",
                                "n_apps": 3,
                                "n": 6,
                                "vulnerable": 2,
                                "not_vulnerable": 3,
                                "not_tested": 1,
                                "vulnerable_share": 0.4,
                                "ci": None,
                            },
                        ],
                        "agreement": _agreement("nominal"),
                    },
                ],
                "reports": {
                    "per_app": [
                        {
                            "app_id": app_id,
                            "label": "App 01",
                            "group": "1",
                            "total": 2,
                            "by_severity": {"high": 2},
                            "by_category": {"A01": 2},
                        },
                    ],
                    "per_group": [
                        {
                            "group": "1",
                            "label": "Condition 1",
                            "n_apps": 3,
                            "total": 2,
                            "per_rater_mean": 0.33,
                            "ci": None,
                            "by_severity": {"high": 2},
                        },
                    ],
                },
                "finding_review": {
                    "n_findings": 4,
                    "n_verdicts": 8,
                    "overall": _precision("overall"),
                    "per_tool": [_precision("bandit")],
                    "per_tool_severity": [_precision("unrated")],
                    "per_group": [_precision("1")],
                    "agreement": _agreement("nominal"),
                },
                "console": {
                    "requests": 7,
                    "participants_using": 2,
                    "per_app": [{"app_id": app_id, "label": "App 01", "requests": 7}],
                },
            },
            {
                "type": "security",
                "block_id": "security_2",
                "title": "No review",
                "checklist": [],
                "reports": {"per_app": [], "per_group": []},
                "finding_review": None,
                "console": None,
            },
            {
                "type": "scenarios",
                "block_id": "scenarios",
                "title": "Tasks",
                "scenarios": [
                    {
                        "scenario_id": "login",
                        "title": "Sign in",
                        "n": 4,
                        "success": 2,
                        "partial": 1,
                        "failure": 1,
                        "not_attempted": 0,
                        "success_rate": 0.5,
                        "median_seconds": 42.5,
                        "ease_mean": 5.25,
                        "per_group": [
                            {
                                "group": "1",
                                "label": "Condition 1",
                                "n_apps": 2,
                                "n": 4,
                                "success_rate": 0.5,
                                "ci": None,
                                "median_seconds": 40,
                            },
                        ],
                    },
                ],
            },
            {
                "type": "questionnaire",
                "block_id": "sus",
                "title": "SUS",
                "instrument": "sus",
                "score": {"per_app": [_app_score(app_id)], "per_group": [_group_score()]},
                "questions": [
                    {
                        "question_id": "sus_1",
                        "label": "I would use it",
                        "kind": "scale",
                        "n": 4,
                        "mean": 3.5,
                        "distribution": {"3": 2, "4": 2},
                    },
                ],
            },
            {
                "type": "questionnaire",
                "block_id": "feedback",
                "title": "Feedback",
                "instrument": "custom",
                "score": None,
                "questions": [],
            },
            {
                "type": "bugs",
                "block_id": "bugs",
                "title": "Bugs",
                "per_app": [
                    {
                        "app_id": app_id,
                        "label": "App 01",
                        "group": "1",
                        "n_raters": 2,
                        "total": 3,
                        "per_rater_mean": 1.5,
                        "by_severity": {"": 1, "low": 2},
                        "none_found": 0,
                    },
                ],
                "per_group": [_group_score()],
            },
            {
                "type": "pairwise",
                "block_id": "preference",
                "title": "Which is better?",
                "criteria": [
                    {
                        "criterion_id": "overall",
                        "label": "Overall",
                        "n_comparisons": 5,
                        "by_group": [_bt("1")],
                        "by_app": [{**_bt(app_id), "strength": None, "log_strength": None, "ci": None, "rank": None}],
                        "position_bias": {"left": 3, "right": 1, "ties": 1, "p_value": None},
                        "agreement": _agreement("nominal"),
                    },
                ],
            },
        ],
        "quality": [
            {
                "participant_id": str(task.pk),
                "label": "P001",
                "kind": "invite",
                "excluded": False,
                "submitted": 3,
                "skipped": 0,
                "median_seconds": 300.5,
                "speeding": 0,
                "straightlining": 1,
                "attention_failed": 0,
                "flags": ["straightlining"],
            },
        ],
        "contrasts": [
            {
                "block_id": "ratings",
                "metric": "composite",
                "label": "Composite",
                "group": "2",
                "group_label": "Condition 2",
                "baseline": "1",
                "baseline_label": "Condition 1",
                "diff": -0.12,
                "ci": _ci(-0.3, 0.05),
                "paired": True,
                "n_cells": 3,
                "p_value": None,
                "p_adjusted": None,
                "primary": True,
            },
        ],
        "human_vs_automated": [
            {"metric": "conformance", "label": "Composite vs conformance", "rho": 0.4, "ci": None, "n": 4, "note": ""},
        ],
    }


def test_results_round_trip_the_full_contract(owner_client, world):
    task = world["task"]
    payload = sample_results(task)
    with mock.patch(COMPUTE, return_value=copy.deepcopy(payload)) as compute:
        response = owner_client.get(f"{base(task)}/results/", {"group_by": "model", "include_excluded": "true"})
    assert response.status_code == 200
    assert response.json() == payload  # nothing dropped, nothing added (unset optional keys stay unset)
    assert compute.call_args.kwargs == {"group_by": "model", "include_excluded": True}
    assert compute.call_args.args[0].pk == task.pk


def test_results_defaults_and_ids_as_strings(owner_client, world):
    task = world["task"]
    payload = sample_results(task)
    payload["apps"][0]["app_id"] = task.apps.first().pk  # a UUID object
    payload["groups"][0]["key"] = 7  # an int condition id
    with mock.patch(COMPUTE, return_value=payload) as compute:
        body = owner_client.get(f"{base(task)}/results/").json()
    assert compute.call_args.kwargs == {"group_by": "condition", "include_excluded": False}
    assert body["apps"][0]["app_id"] == str(task.apps.first().pk)
    assert body["groups"][0]["key"] == "7"


def test_results_invalid_group_by_is_400(owner_client, world):
    with mock.patch(COMPUTE) as compute:
        response = owner_client.get(f"{base(world['task'])}/results/", {"group_by": "provider"})
    assert response.status_code == 400
    assert response.json()["errors"][0]["path"] == "group_by"
    compute.assert_not_called()


def test_results_failure_is_a_500_with_the_detail(owner_client, world):
    with mock.patch(COMPUTE, side_effect=RuntimeError("bootstrap exploded")):
        response = owner_client.get(f"{base(world['task'])}/results/")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "results_failed"
    assert "RuntimeError: bootstrap exploded" in body["detail"]


def test_results_service_errors_pass_through(owner_client, world):
    error = HumanEvalServiceError(409, "Not enough data", "no_data")
    with mock.patch(COMPUTE, side_effect=error):
        response = owner_client.get(f"{base(world['task'])}/results/")
    assert response.status_code == 409
    assert response.json() == {"detail": "Not enough data", "code": "no_data", "errors": []}


def test_results_breaking_the_contract_is_a_500(owner_client, world):
    payload = sample_results(world["task"])
    del payload["blocks"][0]["composite"]
    payload["groups"][0]["n_apps_rated"] = "many"
    with mock.patch(COMPUTE, return_value=payload):
        response = owner_client.get(f"{base(world['task'])}/results/")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "results_invalid"
    paths = {error["path"] for error in body["errors"]}
    assert "groups.0.n_apps_rated" in paths
    assert "blocks.0.rating.composite" in paths
    assert "2 problems" in body["detail"]


def test_results_with_the_real_analysis_match_the_contract(owner_client):
    """Integration: D1's compute_results on a small open task validates against TaskResultsSchema."""
    task = make_open_task(n_apps=3)
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    for number in (1, 2):
        participant = ParticipantFactory(task=task, number=number)
        for position, item in enumerate(items, start=1):
            assignment = AssignmentFactory(
                task=task,
                participant=participant,
                item=item,
                status=S.SUBMITTED,
                position=position,
                active_seconds=600,
                wall_seconds=700,
            )
            criteria = {"functionality": position + number, "usability": 4, "overall": 3 + number}
            ResponseFactory(assignment=assignment, block_id="ratings", answers={"criteria": criteria})
    owner_client.force_login(task.created_by)
    for group_by in ("condition", "model", "app"):
        response = owner_client.get(f"{base(task)}/results/", {"group_by": group_by})
        assert response.status_code == 200, response.json()
        body = response.json()
        assert (body["task_id"], body["group_by"]) == (str(task.pk), group_by)
        assert body["blocks"][0]["type"] == "rating"


# ---------------------------------------------------------------------------
# Export routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", export.EXPORT_KINDS)
def test_export_csv_route(owner_client, world, kind):
    task = world["task"]
    response = owner_client.get(f"{base(task)}/export/{kind}.csv")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"] == f'attachment; filename="{export.export_filename(task, kind)}"'
    assert response["Cache-Control"] == "private, no-store"
    text = b"".join(response.streaming_content).decode("utf-8")
    assert text == export.csv_text(task, kind)
    assert next(csv.reader(io.StringIO(text))) == export.TABLES[kind].columns(export.ExportData(task))
    # include_excluded never filters CSV rows
    again = owner_client.get(f"{base(task)}/export/{kind}.csv", {"include_excluded": "true"})
    assert b"".join(again.streaming_content).decode("utf-8") == text


def test_export_csv_rows_include_excluded_but_not_preview(owner_client, world):
    task = world["task"]
    Assignment.objects.filter(pk=world["assignments"][0].pk).update(excluded=True, exclusion_reason="x")
    text = b"".join(owner_client.get(f"{base(task)}/export/assignments.csv").streaming_content).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert {row["assignment_id"] for row in rows} == {str(a.pk) for a in world["assignments"][:3]}
    assert {row["assignment_id"]: row["excluded"] for row in rows}[str(world["assignments"][0].pk)] == "true"


def test_export_unknown_kind_is_404(owner_client, world):
    response = owner_client.get(f"{base(world['task'])}/export/secrets.csv")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_export_bundle_route(owner_client, world):
    task = world["task"]
    payload = sample_results(task)
    with mock.patch(COMPUTE, return_value=copy.deepcopy(payload)) as compute:
        response = owner_client.get(f"{base(task)}/export/bundle.zip")
    assert response.status_code == 200
    assert compute.call_args.kwargs == {"group_by": "condition", "include_excluded": False}
    assert response["Content-Type"] == "application/zip"
    assert response["Content-Disposition"] == f'attachment; filename="{export.export_filename(task, "bundle", "zip")}"'
    archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
    root = f"human-eval-{export.export_slug(task)}"
    names = set(archive.namelist())
    assert {f"{root}/{kind}.csv" for kind in export.EXPORT_KINDS} <= names
    assert {f"{root}/codebook.md", f"{root}/design.json", f"{root}/settings.json", f"{root}/results.json"} <= names
    assert json.loads(archive.read(f"{root}/results.json")) == payload


def test_export_bundle_fails_loudly(owner_client, world):
    with mock.patch(COMPUTE, side_effect=ValueError("no groups")):
        response = owner_client.get(f"{base(world['task'])}/export/bundle.zip")
    assert response.status_code == 500
    assert "ValueError: no groups" in response.json()["detail"]


def test_export_results_json_route(owner_client, world):
    task = world["task"]
    payload = sample_results(task)
    with mock.patch(COMPUTE, return_value=copy.deepcopy(payload)) as compute:
        response = owner_client.get(f"{base(task)}/export/results.json", {"group_by": "app"})
    assert response.status_code == 200
    assert compute.call_args.kwargs == {"group_by": "app", "include_excluded": False}
    assert response["Content-Type"] == "application/json; charset=utf-8"
    assert (
        response["Content-Disposition"] == f'attachment; filename="{export.export_filename(task, "results", "json")}"'
    )
    assert response.json() == payload


def _answers(block: dict, position: int, number: int) -> dict:
    """Plausible complete answers for one block (only what the analysis reads)."""
    block_type = block["type"]
    if block_type == "rating":
        return {"criteria": {c["id"]: 1 + (position + number) % 7 for c in block["criteria"]}}
    if block_type == "security":
        return {"checklist": {item["id"]: {"outcome": "not_vulnerable"} for item in block["checklist"]}}
    if block_type == "bugs":
        return {"none_found": {"single": True, "A": True, "B": False}}
    if block_type == "questionnaire":
        return {"answers": {f"sus_{n}": 1 + (n + number) % 5 for n in range(1, 11)}}
    if block_type == "pairwise":
        return {"criteria": {c["id"]: ("A" if position % 2 else "tie") for c in block["criteria"]}, "overall": "B"}
    return {"items": {}}


def _rate_everything(task, *, pairwise: bool) -> None:
    """Two raters submit every item with answers for every block of the design."""
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    for number in (1, 2):
        participant = ParticipantFactory(task=task, number=number, post_answers={"attention_check": "neutral"})
        for position, item in enumerate(items, start=1):
            assignment = AssignmentFactory(
                task=task,
                participant=participant,
                item=item,
                status=S.SUBMITTED,
                position=position,
                swapped=pairwise and number == 2,
                active_seconds=1000,
                wall_seconds=1100,
            )
            for block in task.design["blocks"]:
                ResponseFactory(assignment=assignment, block_id=block["id"], answers=_answers(block, position, number))
            ObservationFactory(assignment=assignment, task_app=item.app_a, kind=Observation.Kind.BUG, severity="low")


@pytest.mark.parametrize(("slug", "n_apps"), [("full-evaluation", 3), ("ab-comparison", 4)])
def test_real_analysis_of_every_block_type_matches_the_contract(client, slug, n_apps):
    """Integration: D1's compute_results over every block type validates; the bundle builds from it."""
    from backend.human_eval.api.views.results import results_document
    from backend.human_eval.services import presets

    preset = presets.get_preset(slug)
    task = make_open_task(n_apps=n_apps, mode=preset["mode"], design=preset["design"], settings=preset["settings"])
    _rate_everything(task, pairwise=preset["mode"] == "pairwise")
    document = results_document(task)
    assert {block["type"] for block in document["blocks"]} == {block["type"] for block in task.design["blocks"]}
    client.force_login(task.created_by)
    response = client.get(f"{base(task)}/export/bundle.zip")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
    root = f"human-eval-{export.export_slug(task)}"
    results = json.loads(archive.read(f"{root}/results.json"))
    assert results["task_id"] == str(task.pk)
    assert results["rule_exclusions"] == document["rule_exclusions"]
