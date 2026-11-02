"""Portal session API: join, resume, logout, me, task page, consent, pre/post answers, next, finish, withdraw."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import Attachment
from backend.human_eval.models import EvalInvite
from backend.human_eval.models import EvalTask
from backend.human_eval.models import FindingVerdict
from backend.human_eval.models import HttpExchange
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.models import Response
from backend.human_eval.services import access
from backend.human_eval.services import allocation
from backend.human_eval.services import design as design_service
from backend.human_eval.services import responses as responses_service
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import AttachmentFactory
from backend.human_eval.tests.factories import EvalInviteFactory
from backend.human_eval.tests.factories import EvaluatorProfileFactory
from backend.human_eval.tests.factories import FindingVerdictFactory
from backend.human_eval.tests.factories import HttpExchangeFactory
from backend.human_eval.tests.factories import ObservationFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import ResponseFactory
from backend.human_eval.tests.factories import make_open_task
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BASE = "/api/human-eval/portal"
CSRF_TOKEN = "b" * 32
PRE_ANSWERS = {"programming_years": 4, "web_dev_experience": 3, "security_testing_experience": 2}
CODE_RE = re.compile(r"^[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}(-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}){2}$")
PORTAL_TASK_KEYS = {
    "id",
    "title",
    "description",
    "instructions",
    "mode",
    "status",
    "accepting",
    "participant",
    "consent",
    "pre_questions",
    "pre_answers",
    "pre_answers_locked",
    "post_questions",
    "post_answers",
    "progress",
    "current_assignment_id",
    "blocks",
    "allow_skip",
}


def _fake_validate(questions, answers, *, strict):
    """Stand-in for B2's ``responses.validate_question_answers`` while it is a stub (same contract)."""
    answers = answers if isinstance(answers, dict) else {}
    cleaned, errors = {}, []
    for question in questions:
        question_id = question["id"]
        value = answers.get(question_id)
        if allocation.answer_missing(value):
            if strict and question.get("required", True):
                errors.append({"path": question_id, "message": "This question is required"})
            continue
        kind = question["kind"]
        ok = True
        if kind == "number":
            ok = isinstance(value, int | float) and not isinstance(value, bool)
            ok = ok and (question.get("min") is None or value >= question["min"])
            ok = ok and (question.get("max") is None or value <= question["max"])
        elif kind == "scale":
            ok = value in design_service.scale_values(question["scale"])
        elif kind in ("single", "attention"):
            ok = value in {option["value"] for option in question.get("options") or []}
        if not ok:
            errors.append({"path": question_id, "message": "Invalid answer"})
            continue
        cleaned[question_id] = value
    return cleaned, errors


@pytest.fixture(autouse=True)
def _question_validator(monkeypatch):
    try:
        responses_service.validate_question_answers([], {}, strict=True)
    except NotImplementedError:
        monkeypatch.setattr(responses_service, "validate_question_answers", _fake_validate)


@pytest.fixture(autouse=True)
def _clear_cache(settings):
    settings.TRUSTED_PROXY_CIDRS = ["10.0.0.0/8"]  # per-client throttles only, unless a test says otherwise
    cache.clear()
    yield
    cache.clear()


def _join(client: Client, invite: EvalInvite, *, consent: bool = True, **extra):
    return client.post(
        f"{BASE}/join/{invite.token}/",
        data={"consent": consent},
        content_type="application/json",
        **extra,
    )


def _resume(client: Client, code: str, **extra):
    return client.post(f"{BASE}/resume/", data={"code": code}, content_type="application/json", **extra)


def _post(client: Client, path: str, data=None, **extra):
    return client.post(f"{BASE}{path}", data=data or {}, content_type="application/json", **extra)


def _put(client: Client, path: str, data, **extra):
    return client.put(f"{BASE}{path}", data=data, content_type="application/json", **extra)


def _with_csrf(client: Client) -> dict:
    client.cookies[django_settings.CSRF_COOKIE_NAME] = CSRF_TOKEN
    return {"HTTP_X_CSRFTOKEN": CSRF_TOKEN}


def _code_participant(task: EvalTask, **kwargs) -> tuple[Participant, str]:
    raw, digest, hint = access.new_access_code()
    kwargs.setdefault("status", Participant.Status.INVITED)
    kwargs.setdefault("joined_at", None)
    participant = ParticipantFactory(
        task=task,
        kind=Participant.Kind.CODE,
        access_code_hash=digest,
        access_code_hint=hint,
        **kwargs,
    )
    return participant, raw


def _cookie_client(participant: Participant) -> Client:
    client = Client()
    client.cookies[access.cookie_name()] = access.issue_session(participant)
    return client


def _ready(participant: Participant) -> Participant:
    participant.consent_at = timezone.now()
    participant.pre_answers = dict(PRE_ANSWERS)
    participant.save(update_fields=["consent_at", "pre_answers"])
    return participant


# --- join ----------------------------------------------------------------------------


def test_join_info_reports_task_consent_and_availability():
    task = make_open_task(n_apps=3, settings={"allocation": {"max_items_per_participant": 2}})
    invite = EvalInviteFactory(task=task)
    response = Client().get(f"{BASE}/join/{invite.token}/")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "task_title": task.title,
        "task_description": task.description,
        "availability": "open",
        "already_joined": None,
        "consent": {"enabled": True, "text": task.design["consent"]["text"]},
        "estimated_items": 2,
    }


def test_join_creates_participant_with_session_and_device_cookies(settings):
    task = make_open_task(n_apps=2)
    invite = EvalInviteFactory(task=task)
    client = Client()
    response = _join(client, invite)
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["task_id"] == str(task.id)
    assert body["participant_label"] == "P001"
    assert CODE_RE.fullmatch(body["access_code"])

    session = response.cookies[settings.HUMAN_EVAL_COOKIE_NAME]
    assert session["path"] == "/"
    assert session["httponly"] is True
    assert session["samesite"] == "Lax"
    assert bool(session["secure"]) is settings.HUMAN_EVAL_COOKIE_SECURE
    assert session["max-age"] == ""  # browser-session cookie
    assert session["domain"] == ""
    device = response.cookies[settings.HUMAN_EVAL_DEVICE_COOKIE_NAME]
    assert device["max-age"] == 365 * 24 * 60 * 60
    assert device["httponly"] is True
    assert device["path"] == "/"
    assert bool(device["secure"]) is settings.HUMAN_EVAL_COOKIE_SECURE

    participant = Participant.objects.get(task=task)
    assert participant.kind == Participant.Kind.INVITE
    assert participant.status == Participant.Status.ACTIVE
    assert participant.invite_id == invite.id
    assert participant.joined_at is not None
    assert participant.consent_at is not None
    assert participant.session_token_hash == access.hash_secret(session.value)
    assert participant.device_hash == access.hash_secret(device.value)
    assert participant.access_code_hash == access.access_code_hash(body["access_code"])
    assert participant.access_code_hint == body["access_code"][-4:]
    invite.refresh_from_db()
    assert invite.uses == 1

    # The browser now has a session for the task: GET says so, POST refuses a second join.
    info = client.get(f"{BASE}/join/{invite.token}/").json()
    assert info["already_joined"] == {"task_id": str(task.id), "participant_label": "P001"}
    again = _join(client, invite)
    assert again.status_code == 409
    assert again.json()["code"] == "already_joined"
    assert Participant.objects.filter(task=task).count() == 1


def test_join_requires_consent_when_enabled_and_leaves_nothing_behind():
    task = make_open_task(n_apps=1)
    invite = EvalInviteFactory(task=task)
    response = _join(Client(), invite, consent=False)
    assert response.status_code == 400
    assert response.json()["code"] == "consent_required"
    assert not Participant.objects.filter(task=task).exists()
    invite.refresh_from_db()
    assert invite.uses == 0

    design = design_service.default_design("single")
    design["consent"]["enabled"] = False
    no_consent = make_open_task(n_apps=1, design=design)
    response = _join(Client(), EvalInviteFactory(task=no_consent), consent=False)
    assert response.status_code == 200
    assert Participant.objects.get(task=no_consent).consent_at is None


@pytest.mark.parametrize(
    ("invite_kwargs", "task_changes", "expected"),
    [
        ({"is_active": False}, {}, "inactive"),
        ({}, {"settings.access.allow_invite_links": False}, "inactive"),
        ({"expires_at": timezone.now() - timedelta(minutes=1)}, {}, "expired"),
        ({"max_uses": 1, "uses": 1}, {}, "full"),
        ({}, {"status": EvalTask.Status.PAUSED}, "not_open"),
        ({}, {"status": EvalTask.Status.DRAFT}, "not_open"),
        ({}, {"opens_at": timezone.now() + timedelta(days=1)}, "not_open"),
        ({}, {"status": EvalTask.Status.CLOSED}, "closed"),
        ({}, {"status": EvalTask.Status.ARCHIVED}, "closed"),
        ({}, {"closes_at": timezone.now() - timedelta(minutes=1)}, "closed"),
    ],
)
def test_join_availability_values(invite_kwargs, task_changes, expected):
    task = make_open_task(n_apps=1)
    for key, value in task_changes.items():
        if key.startswith("settings."):
            _, section, field = key.split(".")
            task.settings[section][field] = value
        else:
            setattr(task, key, value)
    task.save()
    invite = EvalInviteFactory(task=task, **invite_kwargs)
    assert Client().get(f"{BASE}/join/{invite.token}/").json()["availability"] == expected
    response = _join(Client(), invite)
    assert response.status_code == 409
    assert response.json()["code"] == expected
    assert not Participant.objects.filter(task=task).exists()


def test_invite_max_uses_and_expiry_in_the_future():
    task = make_open_task(n_apps=1)
    invite = EvalInviteFactory(task=task, max_uses=2, expires_at=timezone.now() + timedelta(days=1))
    assert _join(Client(), invite).status_code == 200
    assert _join(Client(), invite).status_code == 200
    third = _join(Client(), invite)
    assert third.status_code == 409
    assert third.json()["code"] == "full"
    assert sorted(Participant.objects.filter(task=task).values_list("number", flat=True)) == [1, 2]
    invite.refresh_from_db()
    assert invite.uses == 2


def test_unknown_invite_token_is_404():
    response = Client().get(f"{BASE}/join/not-a-token/")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert _join(Client(), EvalInvite(token="x" * 65)).status_code == 404


def test_device_cookie_links_duplicate_joins():
    task = make_open_task(n_apps=1)
    invite = EvalInviteFactory(task=task)
    browser = Client()
    assert _join(browser, invite).status_code == 200
    assert _post(browser, "/logout/").status_code == 204
    assert _join(browser, invite).status_code == 200  # same device, new participant
    assert _join(Client(), invite).status_code == 200  # another device
    hashes = list(Participant.objects.filter(task=task).order_by("number").values_list("device_hash", flat=True))
    assert len(hashes) == 3
    assert hashes[0] == hashes[1] != hashes[2]
    assert all(len(value) == 64 for value in hashes)


def test_joining_another_task_ends_the_previous_cookie_session():
    first_task = make_open_task(n_apps=1)
    second_task = make_open_task(n_apps=1)
    browser = Client()
    assert _join(browser, EvalInviteFactory(task=first_task)).status_code == 200
    assert _join(browser, EvalInviteFactory(task=second_task)).status_code == 200
    first = Participant.objects.get(task=first_task)
    assert first.session_token_hash is None
    assert browser.get(f"{BASE}/tasks/{second_task.id}/").status_code == 200
    assert browser.get(f"{BASE}/tasks/{first_task.id}/").status_code == 404


# --- resume ----------------------------------------------------------------------------


def test_resume_rotates_the_session_token():
    task = make_open_task(n_apps=1)
    first_browser = Client()
    code = _join(first_browser, EvalInviteFactory(task=task)).json()["access_code"]
    second_browser = Client()
    response = _resume(second_browser, code.lower().replace("-", " "))
    assert response.status_code == 200
    assert response.json() == {"task_id": str(task.id), "participant_label": "P001"}
    cookie = response.cookies[access.cookie_name()]
    assert cookie["max-age"] == ""
    assert cookie["httponly"] is True
    participant = Participant.objects.get(task=task)
    assert participant.session_token_hash == access.hash_secret(cookie.value)
    assert second_browser.get(f"{BASE}/tasks/{task.id}/").status_code == 200
    assert first_browser.get(f"{BASE}/tasks/{task.id}/").status_code == 401


def test_resume_activates_a_code_participant_on_first_use():
    task = make_open_task(n_apps=1)
    participant, code = _code_participant(task)
    assert _resume(Client(), code).status_code == 200
    participant.refresh_from_db()
    assert participant.status == Participant.Status.ACTIVE
    assert participant.joined_at is not None


@pytest.mark.parametrize("case", ["unknown", "withdrawn", "draft", "empty", "too_long"])
def test_resume_failures_share_one_generic_error(case):
    task = make_open_task(n_apps=1)
    participant, code = _code_participant(task)
    if case == "unknown":
        code = "2222-3333-4444"
    elif case == "withdrawn":
        participant.status = Participant.Status.WITHDRAWN
        participant.save()
    elif case == "draft":
        task.status = EvalTask.Status.DRAFT
        task.save()
    elif case == "empty":
        code = " - "
    else:
        code = "A" * 65
    response = _resume(Client(), code)
    assert response.status_code == 404
    assert response.json() == {"detail": "Code not recognised", "code": "not_found", "errors": []}
    participant.refresh_from_db()
    assert participant.session_token_hash is None


def test_resume_throttle_counts_failures_only(monkeypatch):
    monkeypatch.setattr(access, "LOOKUP_FAILURE_LIMIT", 3)
    task = make_open_task(n_apps=1)
    _, code = _code_participant(task)
    browser = Client(REMOTE_ADDR="198.51.100.10")
    for _ in range(5):
        assert _resume(browser, code).status_code == 200  # successes are never counted
    for _ in range(3):
        assert _resume(browser, "2222-2222-2222").status_code == 404
    blocked = _resume(browser, code)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert browser.get(f"{BASE}/join/whatever/").status_code == 429  # one failure budget for join + resume
    assert _resume(Client(REMOTE_ADDR="198.51.100.11"), code).status_code == 200  # other clients unaffected


def test_global_failure_ceiling_when_client_ips_are_shared(settings, monkeypatch):
    settings.TRUSTED_PROXY_CIDRS = []
    monkeypatch.setattr(access, "GLOBAL_LOOKUP_FAILURE_LIMIT", 2)
    task = make_open_task(n_apps=1)
    _, code = _code_participant(task)
    assert _resume(Client(REMOTE_ADDR="198.51.100.20"), "2222-2222-2222").status_code == 404
    assert Client(REMOTE_ADDR="198.51.100.21").get(f"{BASE}/join/unknown/").status_code == 404
    assert _resume(Client(REMOTE_ADDR="198.51.100.22"), code).status_code == 429


def test_closed_task_resume_gives_read_only_access():
    task = make_open_task(n_apps=1)
    _, code = _code_participant(task, status=Participant.Status.COMPLETED, completion_code="ABCD2345")
    task.status = EvalTask.Status.CLOSED
    task.save()
    browser = Client()
    assert _resume(browser, code).status_code == 200
    page = browser.get(f"{BASE}/tasks/{task.id}/").json()
    assert page["accepting"] is False
    assert page["status"] == "closed"
    assert page["participant"]["completion_code"] == "ABCD2345"
    assert _post(browser, f"/tasks/{task.id}/next/").json() == {"assignment_id": None, "reason": "not_accepting"}
    assert _post(browser, f"/tasks/{task.id}/consent/", {"agree": True}).status_code == 409


# --- CSRF ------------------------------------------------------------------------------


def test_public_posts_require_a_csrf_token():
    task = make_open_task(n_apps=1)
    invite = EvalInviteFactory(task=task)
    _, code = _code_participant(task)
    strict = Client(enforce_csrf_checks=True)
    assert _join(strict, invite).status_code == 403
    assert _resume(strict, code).status_code == 403
    assert _post(strict, "/logout/").status_code == 403
    assert not Participant.objects.filter(task=task, kind=Participant.Kind.INVITE).exists()

    headers = _with_csrf(strict)
    assert _join(strict, invite, **headers).status_code == 200
    assert _resume(strict, code, **headers).status_code == 200
    assert _post(strict, "/logout/", **headers).status_code == 204


def test_cookie_authenticated_mutations_require_a_csrf_token():
    task = make_open_task(n_apps=1)
    participant = _ready(ParticipantFactory(task=task))
    strict = Client(enforce_csrf_checks=True)
    strict.cookies[access.cookie_name()] = access.issue_session(participant)
    assert _post(strict, f"/tasks/{task.id}/next/").status_code == 403
    assert _put(strict, f"/tasks/{task.id}/pre-answers/", {"answers": PRE_ANSWERS}).status_code == 403
    assert not Assignment.objects.filter(participant=participant).exists()
    assert strict.get(f"{BASE}/tasks/{task.id}/").status_code == 200  # safe methods need no token
    response = _post(strict, f"/tasks/{task.id}/next/", **_with_csrf(strict))
    assert response.status_code == 200
    assert response.json()["reason"] == "assigned"


# --- logout and me ------------------------------------------------------------------------------


def test_logout_ends_the_session_server_side():
    task = make_open_task(n_apps=1)
    participant = ParticipantFactory(task=task)
    browser = _cookie_client(participant)
    response = _post(browser, "/logout/")
    assert response.status_code == 204
    assert response.cookies[access.cookie_name()]["max-age"] == 0
    participant.refresh_from_db()
    assert participant.session_token_hash is None
    assert browser.get(f"{BASE}/tasks/{task.id}/").status_code == 401
    assert _post(Client(), "/logout/").status_code == 204  # idempotent without a cookie


def test_me_anonymous():
    assert Client().get(f"{BASE}/me/").json() == {
        "kind": "anonymous",
        "participant": None,
        "user": None,
        "participations": [],
    }


def test_me_with_cookie_and_session_identities():
    user = UserFactory(name="Ada")
    cookie_task = make_open_task(n_apps=2, settings={"allocation": {"strategy": "all"}})
    account_task = make_open_task(n_apps=3, settings={"allocation": {"max_items_per_participant": 1}})
    preview_task = make_open_task(n_apps=1)
    cookie_participant = ParticipantFactory(task=cookie_task)
    AssignmentFactory(
        participant=cookie_participant,
        item=cookie_task.items.first(),
        status=Assignment.Status.SUBMITTED,
    )
    account = ParticipantFactory(task=account_task, kind=Participant.Kind.ACCOUNT, user=user)
    ParticipantFactory(task=preview_task, kind=Participant.Kind.PREVIEW, user=user)
    ParticipantFactory(task=preview_task, kind=Participant.Kind.INVITE, user=None)  # not the user's

    browser = _cookie_client(cookie_participant)
    browser.force_login(user)
    body = browser.get(f"{BASE}/me/").json()
    assert body["kind"] == "participant"
    assert body["participant"] == {"task_id": str(cookie_task.id), "label": cookie_participant.label}
    assert body["user"] == {"email": user.email, "name": "Ada", "is_evaluator": False}
    by_task = {row["task_id"]: row for row in body["participations"]}
    assert set(by_task) == {str(cookie_task.id), str(account_task.id), str(preview_task.id)}
    assert body["participations"][0]["task_id"] == str(cookie_task.id)
    assert by_task[str(cookie_task.id)] == {
        "task_id": str(cookie_task.id),
        "task_title": cookie_task.title,
        "task_status": "open",
        "participant_label": cookie_participant.label,
        "participant_status": "active",
        "kind": "invite",
        "submitted": 1,
        "target": 2,
    }
    assert by_task[str(account_task.id)]["participant_label"] == account.label
    assert by_task[str(account_task.id)]["target"] == 1
    assert by_task[str(preview_task.id)]["kind"] == "preview"
    assert by_task[str(preview_task.id)]["target"] is None

    session_only = Client()
    session_only.force_login(user)
    body = session_only.get(f"{BASE}/me/").json()
    assert body["kind"] == "account"
    assert body["participant"] is None
    assert {row["task_id"] for row in body["participations"]} == {str(account_task.id), str(preview_task.id)}


def test_me_marks_restricted_evaluators():
    user = UserFactory()
    EvaluatorProfileFactory(user=user, is_restricted=True)
    browser = Client()
    browser.force_login(user)
    assert browser.get(f"{BASE}/me/").json()["user"]["is_evaluator"] is True


# --- task page ---------------------------------------------------------------------------------


def _design_with_attention():
    design = design_service.default_design("single")
    design["pre_questions"].append(
        {
            "id": "attention_pick_b",
            "kind": "attention",
            "label": "Please pick B",
            "required": True,
            "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
            "expected": "b",
        },
    )
    design["post_questions"] = [
        {"id": "comments", "kind": "textarea", "label": "Anything else?", "required": True},
    ]
    return design


def test_task_page_shape_and_blinding():
    task = make_open_task(n_apps=2, design=_design_with_attention())
    participant = ParticipantFactory(task=task)
    response = _cookie_client(participant).get(f"{BASE}/tasks/{task.id}/")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == PORTAL_TASK_KEYS
    assert body["id"] == str(task.id)
    assert body["accepting"] is True
    assert body["participant"] == {
        "label": participant.label,
        "kind": "invite",
        "status": "active",
        "consent_at": None,
        "completion_code": "",
    }
    assert body["consent"]["enabled"] is True
    assert [q["id"] for q in body["pre_questions"]][-1] == "attention_pick_b"
    assert all("expected" not in question for question in body["pre_questions"] + body["post_questions"])
    assert body["post_questions"][0]["id"] == "comments"
    assert body["progress"] == {"submitted": 0, "skipped": 0, "limit": None, "remaining_available": 2}
    assert body["current_assignment_id"] is None
    assert body["blocks"] == [{"id": "ratings", "type": "rating", "title": "Quality ratings"}]
    assert body["allow_skip"] is True


def test_task_page_scoping_and_account_activation():
    user = UserFactory()
    task = make_open_task(n_apps=1)
    account = ParticipantFactory(
        task=task,
        kind=Participant.Kind.ACCOUNT,
        user=user,
        status=Participant.Status.INVITED,
        joined_at=None,
    )
    browser = Client()
    browser.force_login(user)
    assert browser.get(f"{BASE}/tasks/{task.id}/").status_code == 200
    account.refresh_from_db()
    assert account.status == Participant.Status.ACTIVE
    assert account.joined_at is not None
    assert browser.get(f"{BASE}/tasks/{make_open_task(n_apps=1).id}/").status_code == 404
    assert Client().get(f"{BASE}/tasks/{task.id}/").status_code == 401
    stranger = _cookie_client(ParticipantFactory(task=make_open_task(n_apps=1)))
    assert stranger.get(f"{BASE}/tasks/{task.id}/").status_code == 404


def test_task_page_reports_progress_and_current_assignment():
    task = make_open_task(n_apps=3, settings={"allocation": {"max_items_per_participant": 2}})
    participant = _ready(ParticipantFactory(task=task))
    browser = _cookie_client(participant)
    first = _post(browser, f"/tasks/{task.id}/next/").json()
    assert first["reason"] == "assigned"
    page = browser.get(f"{BASE}/tasks/{task.id}/").json()
    assert page["current_assignment_id"] == first["assignment_id"]
    assert page["progress"] == {"submitted": 0, "skipped": 0, "limit": 2, "remaining_available": 2}
    Assignment.objects.filter(pk=first["assignment_id"]).update(status=Assignment.Status.SUBMITTED)
    page = browser.get(f"{BASE}/tasks/{task.id}/").json()
    assert page["current_assignment_id"] is None
    assert page["progress"] == {"submitted": 1, "skipped": 0, "limit": 2, "remaining_available": 1}


# --- consent, pre/post answers, next -------------------------------------------------------------


def test_consent_pre_answers_and_next_flow():
    task = make_open_task(n_apps=2, design=_design_with_attention())
    participant, code = _code_participant(task)
    browser = Client()
    assert _resume(browser, code).status_code == 200
    path = f"/tasks/{task.id}"
    assert _post(browser, f"{path}/next/").json() == {"assignment_id": None, "reason": "consent_required"}
    assert _post(browser, f"{path}/consent/", {"agree": False}).json()["code"] == "consent_required"
    consented = _post(browser, f"{path}/consent/", {"agree": True})
    assert consented.status_code == 200
    first_consent = consented.json()["participant"]["consent_at"]
    assert first_consent is not None
    assert _post(browser, f"{path}/consent/", {"agree": True}).json()["participant"]["consent_at"] == first_consent
    assert _post(browser, f"{path}/next/").json()["reason"] == "pre_questions_required"

    incomplete = _put(browser, f"{path}/pre-answers/", {"answers": dict(PRE_ANSWERS)})
    assert incomplete.status_code == 400
    assert incomplete.json()["code"] == "invalid_answers"
    assert incomplete.json()["errors"]
    participant.refresh_from_db()
    assert participant.pre_answers == {}

    answers = {**PRE_ANSWERS, "attention_pick_b": "a"}  # a wrong attention answer is still a valid answer
    saved = _put(browser, f"{path}/pre-answers/", {"answers": answers})
    assert saved.status_code == 200
    assert saved.json()["pre_answers"] == answers
    result = _post(browser, f"{path}/next/").json()
    assert result["reason"] == "assigned"
    assert _post(browser, f"{path}/next/").json() == {"assignment_id": result["assignment_id"], "reason": "resumed"}
    locked = _put(browser, f"{path}/pre-answers/", {"answers": answers})
    assert locked.status_code == 409
    assert locked.json()["code"] == "read_only"


def test_answers_are_size_limited():
    task = make_open_task(n_apps=1)
    browser = _cookie_client(ParticipantFactory(task=task))
    response = _put(browser, f"/tasks/{task.id}/pre-answers/", {"answers": {"programming_years": "x" * 70000}})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_answers"


def test_participant_writes_are_refused_when_not_live():
    task = make_open_task(n_apps=1)
    participant = ParticipantFactory(task=task)
    browser = _cookie_client(participant)
    task.status = EvalTask.Status.CLOSED
    task.save()
    for response in (
        _post(browser, f"/tasks/{task.id}/consent/", {"agree": True}),
        _put(browser, f"/tasks/{task.id}/pre-answers/", {"answers": PRE_ANSWERS}),
        _put(browser, f"/tasks/{task.id}/post-answers/", {"answers": {}}),
        _post(browser, f"/tasks/{task.id}/finish/"),
    ):
        assert response.status_code == 409
        assert response.json()["code"] == "read_only"
    task.status = EvalTask.Status.PAUSED
    task.save()
    assert _post(browser, f"/tasks/{task.id}/consent/", {"agree": True}).status_code == 200


# --- finish -----------------------------------------------------------------------------------


def test_finish_needs_no_open_assignment_and_the_post_answers():
    task = make_open_task(n_apps=1, design=_design_with_attention())
    participant = _ready(ParticipantFactory(task=task))
    participant.pre_answers["attention_pick_b"] = "b"
    participant.save()
    browser = _cookie_client(participant)
    path = f"/tasks/{task.id}"
    assignment_id = _post(browser, f"{path}/next/").json()["assignment_id"]
    ResponseFactory(assignment=Assignment.objects.get(pk=assignment_id))  # saved work: no silent release
    blocked = _post(browser, f"{path}/finish/")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "assignment_open"
    assert Assignment.objects.get(pk=assignment_id).status == Assignment.Status.ASSIGNED
    Assignment.objects.filter(pk=assignment_id).update(status=Assignment.Status.SUBMITTED)

    missing = _post(browser, f"{path}/finish/")
    assert missing.status_code == 400
    assert missing.json()["code"] == "post_questions_required"
    assert missing.json()["errors"] == [{"path": "comments", "message": "This question is required"}]
    assert _put(browser, f"{path}/post-answers/", {"answers": {"comments": "Nice apps"}}).status_code == 200

    finished = _post(browser, f"{path}/finish/")
    assert finished.status_code == 200
    body = finished.json()
    assert body["participant"]["status"] == "completed"
    assert re.fullmatch(r"[23456789ABCDEFGHJKMNPQRSTVWXYZ]{8}", body["participant"]["completion_code"])
    participant.refresh_from_db()
    assert participant.completed_at is not None
    again = _post(browser, f"{path}/finish/").json()
    assert again["participant"]["completion_code"] == body["participant"]["completion_code"]
    assert _post(browser, f"{path}/next/").json() == {"assignment_id": None, "reason": "done"}
    assert _put(browser, f"{path}/post-answers/", {"answers": {"comments": "x"}}).status_code == 409


def test_finish_expires_a_timed_out_assignment_first():
    task = make_open_task(n_apps=1, settings={"allocation": {"assignment_timeout_minutes": 5}})
    participant = _ready(ParticipantFactory(task=task))
    AssignmentFactory(
        participant=participant,
        item=task.items.first(),
        last_activity_at=timezone.now() - timedelta(minutes=10),
    )
    response = _post(_cookie_client(participant), f"/tasks/{task.id}/finish/")
    assert response.status_code == 200
    assert response.json()["participant"]["status"] == "completed"


def test_finish_releases_the_app_handed_out_after_the_last_submit():
    """C02: submit allocates the next app at once; a participant who wants to stop can still finish."""
    task = make_open_task(n_apps=3, settings={"allocation": {"allow_skip": False}})
    participant = _ready(ParticipantFactory(task=task))
    browser = _cookie_client(participant)
    path = f"/tasks/{task.id}"
    first = Assignment.objects.get(pk=_post(browser, f"{path}/next/").json()["assignment_id"])
    first.status = Assignment.Status.SUBMITTED
    first.save(update_fields=["status"])
    # The auto-assigned next app, already opened in the workbench (in progress) but with nothing saved.
    handed_out = Assignment.objects.get(pk=_post(browser, f"{path}/next/").json()["assignment_id"])
    Assignment.objects.filter(pk=handed_out.pk).update(status=Assignment.Status.IN_PROGRESS)
    HttpExchangeFactory(assignment=handed_out)  # console traffic is kept, but is not saved work

    response = _post(browser, f"{path}/finish/")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["participant"]["status"] == "completed"
    assert body["current_assignment_id"] is None
    handed_out.refresh_from_db()
    assert handed_out.status == Assignment.Status.EXPIRED
    assert HttpExchange.objects.filter(assignment=handed_out).exists()
    assert Assignment.objects.get(pk=first.pk).status == Assignment.Status.SUBMITTED
    # The released item adds no load any more.
    assert allocation._item_loads(task).get(handed_out.item_id) is None


def test_refused_finish_keeps_the_untouched_assignment():
    task = make_open_task(n_apps=1, design=_design_with_attention())
    participant = _ready(ParticipantFactory(task=task))
    participant.pre_answers["attention_pick_b"] = "b"
    participant.save()
    browser = _cookie_client(participant)
    assignment_id = _post(browser, f"/tasks/{task.id}/next/").json()["assignment_id"]
    refused = _post(browser, f"/tasks/{task.id}/finish/")
    assert refused.json()["code"] == "post_questions_required"
    assert Assignment.objects.get(pk=assignment_id).status == Assignment.Status.ASSIGNED
    participant.refresh_from_db()
    assert participant.status == Participant.Status.ACTIVE


@pytest.mark.parametrize("work", ["response", "observation", "verdict"])
def test_finish_still_refuses_an_open_assignment_with_saved_work(work):
    task = make_open_task(n_apps=2)
    participant = _ready(ParticipantFactory(task=task))
    items = list(task.items.order_by("position"))
    with_work = AssignmentFactory(participant=participant, item=items[0])
    untouched = AssignmentFactory(participant=participant, item=items[1], status=Assignment.Status.ASSIGNED)
    factory = {"response": ResponseFactory, "observation": ObservationFactory, "verdict": FindingVerdictFactory}[work]
    factory(assignment=with_work)
    response = _post(_cookie_client(participant), f"/tasks/{task.id}/finish/")
    assert response.status_code == 409
    assert response.json()["code"] == "assignment_open"
    statuses = dict(Assignment.objects.filter(participant=participant).values_list("id", "status"))
    assert statuses == {with_work.id: Assignment.Status.IN_PROGRESS, untouched.id: Assignment.Status.ASSIGNED}


def test_post_answers_with_a_nul_character_are_a_400():
    """C22: Postgres JSONB cannot store U+0000; the answer is refused, never a 500 on save."""
    task = make_open_task(n_apps=1, design=_design_with_attention())
    participant = _ready(ParticipantFactory(task=task))
    browser = _cookie_client(participant)
    response = _put(browser, f"/tasks/{task.id}/post-answers/", {"answers": {"comments": "nice\u0000apps"}})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_answers"
    assert [error["path"] for error in response.json()["errors"]] == ["comments"]
    participant.refresh_from_db()
    assert participant.post_answers == {}


# --- withdraw ---------------------------------------------------------------------------------


def test_withdraw_keeps_data_and_ends_the_session():
    task = make_open_task(n_apps=1)
    participant = _ready(ParticipantFactory(task=task))
    browser = _cookie_client(participant)
    assignment_id = _post(browser, f"/tasks/{task.id}/next/").json()["assignment_id"]
    response = _post(browser, f"/tasks/{task.id}/withdraw/", {"delete_data": False})
    assert response.status_code == 204
    assert response.cookies[access.cookie_name()]["max-age"] == 0
    participant.refresh_from_db()
    assert participant.status == Participant.Status.WITHDRAWN
    assert participant.withdrawn_at is not None
    assert participant.session_token_hash is None
    assert participant.pre_answers == PRE_ANSWERS
    assert Assignment.objects.get(pk=assignment_id).status == Assignment.Status.EXPIRED
    assert browser.get(f"{BASE}/tasks/{task.id}/").status_code == 401


def test_withdraw_with_delete_data_removes_everything(django_capture_on_commit_callbacks):
    task = make_open_task(n_apps=2)
    participant = _ready(ParticipantFactory(task=task))
    participant.post_answers = {"comments": "bye"}
    participant.save()
    items = list(task.items.all())
    done = AssignmentFactory(participant=participant, item=items[0], status=Assignment.Status.SUBMITTED)
    AssignmentFactory(participant=participant, item=items[1])
    ResponseFactory(assignment=done)
    observation = ObservationFactory(assignment=done)
    attachment = AttachmentFactory(observation=observation)
    FindingVerdictFactory(assignment=done)
    HttpExchangeFactory(assignment=done)
    other = AssignmentFactory(participant=ParticipantFactory(task=task), item=items[0])
    storage, name = attachment.file.storage, attachment.file.name

    browser = _cookie_client(participant)
    with django_capture_on_commit_callbacks(execute=True):
        response = _post(browser, f"/tasks/{task.id}/withdraw/", {"delete_data": True})
    assert response.status_code == 204
    participant.refresh_from_db()
    assert participant.status == Participant.Status.WITHDRAWN
    assert (participant.pre_answers, participant.post_answers) == ({}, {})
    assert not Assignment.objects.filter(participant=participant).exists()
    for model in (Response, Observation, FindingVerdict, HttpExchange):
        assert not model.objects.filter(assignment__task=task).exists()
    assert not Attachment.objects.filter(observation__assignment__task=task).exists()
    assert not storage.exists(name)
    assert Assignment.objects.filter(pk=other.pk).exists()


def test_withdrawn_account_participant_can_still_see_the_task_but_not_work():
    user = UserFactory()
    task = make_open_task(n_apps=1)
    account = _ready(ParticipantFactory(task=task, kind=Participant.Kind.ACCOUNT, user=user))
    browser = Client()
    browser.force_login(user)
    assert _post(browser, f"/tasks/{task.id}/withdraw/", {"delete_data": False}).status_code == 204
    page = browser.get(f"{BASE}/tasks/{task.id}/").json()
    assert page["participant"]["status"] == "withdrawn"
    assert page["accepting"] is False
    assert page["progress"]["remaining_available"] == 0
    assert _post(browser, f"/tasks/{task.id}/next/").json()["reason"] == "not_accepting"
    assert _post(browser, f"/tasks/{task.id}/consent/", {"agree": True}).status_code == 409
    account.refresh_from_db()
    assert account.status == Participant.Status.WITHDRAWN
