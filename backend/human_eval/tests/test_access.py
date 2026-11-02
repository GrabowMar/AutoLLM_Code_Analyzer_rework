"""services/access.py: secrets, cookies, researcher scoping, numbering, accepting rules, portal lookups, throttles."""

from __future__ import annotations

import re
import threading
import uuid
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.db import close_old_connections
from django.db import connection
from django.db import transaction
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import timezone

from backend.human_eval.auth import PortalIdentity
from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalTask
from backend.human_eval.models import Participant
from backend.human_eval.services import access
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import AttachmentFactory
from backend.human_eval.tests.factories import EvalTaskFactory
from backend.human_eval.tests.factories import ObservationFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _request(*, auth=None, cookies=None, remote_addr="203.0.113.7", **headers):
    request = RequestFactory().get("/", REMOTE_ADDR=remote_addr, **headers)
    request.COOKIES.update(cookies or {})
    if auth is not None:
        request.auth = auth
    return request


# --- secrets ---------------------------------------------------------------


def test_normalize_and_hash_codes():
    assert access.normalize_code(" k7qm-4xt9 bw2r\n") == "K7QM4XT9BW2R"
    assert access.hash_secret("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert access.access_code_hash("k7qm-4xt9-bw2r") == access.hash_secret("K7QM4XT9BW2R")


def test_new_access_code_format():
    raw, digest, hint = access.new_access_code()
    assert re.fullmatch(r"[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}(-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}){2}", raw)
    assert digest == access.hash_secret(access.normalize_code(raw))
    assert hint == raw[-4:]
    assert len({access.new_access_code()[0] for _ in range(50)}) == 50


def test_session_and_completion_codes():
    raw, digest = access.new_session_token()
    assert len(raw) >= 40
    assert digest == access.hash_secret(raw)
    code = access.new_completion_code()
    assert re.fullmatch(r"[23456789ABCDEFGHJKMNPQRSTVWXYZ]{8}", code)


def test_issue_session_rotates_the_token():
    participant = ParticipantFactory()
    first = access.issue_session(participant)
    participant.refresh_from_db()
    assert participant.session_token_hash == access.hash_secret(first)
    assert participant.last_seen_at is not None
    second = access.issue_session(participant)
    participant.refresh_from_db()
    assert participant.session_token_hash == access.hash_secret(second) != access.hash_secret(first)
    access.end_session(participant)
    participant.refresh_from_db()
    assert participant.session_token_hash is None


# --- cookies ------------------------------------------------------------------


def test_session_cookie_attributes():
    response = HttpResponse()
    access.set_session_cookie(response, "tok")
    morsel = response.cookies[access.cookie_name()]
    assert morsel.value == "tok"
    assert morsel["path"] == "/"
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "Lax"
    assert bool(morsel["secure"]) is access.cookie_secure()
    assert morsel["max-age"] == ""
    assert morsel["domain"] == ""
    access.clear_session_cookie(response)
    assert response.cookies[access.cookie_name()]["max-age"] == 0


def test_device_cookie_lasts_a_year():
    response = HttpResponse()
    access.set_device_cookie(response, "dev")
    morsel = response.cookies[access.device_cookie_name()]
    assert morsel["max-age"] == 365 * 24 * 60 * 60
    assert morsel["httponly"] is True
    assert morsel["path"] == "/"


def test_cookie_names_follow_the_secure_setting(settings):
    if settings.HUMAN_EVAL_COOKIE_SECURE:
        assert access.cookie_name() == "__Host-llml_eval"
        assert access.device_cookie_name() == "__Host-llml_eval_device"
    else:
        assert access.cookie_name() == "llml_eval"
        assert access.device_cookie_name() == "llml_eval_device"
    assert settings.HUMAN_EVAL_COOKIE_SECURE is settings.FRONTEND_PUBLIC_ORIGIN.lower().startswith("https://")


# --- researcher scoping ---------------------------------------------------------


def test_researcher_task_scoping():
    owner = UserFactory()
    other = UserFactory()
    staff = UserFactory(is_staff=True)
    task = EvalTaskFactory(created_by=owner)
    assert access.researcher_task(_request(auth=owner), task.id) == task
    assert access.researcher_task(_request(auth=owner), str(task.id), for_update=True) == task
    assert access.researcher_task(_request(auth=staff), task.id) == task
    for request, task_id in (
        (_request(auth=other), task.id),
        (_request(auth=owner), "not-a-uuid"),
        (_request(auth=owner), uuid.uuid4()),
        (_request(), task.id),
    ):
        with pytest.raises(HumanEvalServiceError) as exc:
            access.researcher_task(request, task_id)
        assert exc.value.status == 404
        assert exc.value.code == "not_found"
    assert list(access.researcher_tasks_qs(other)) == []
    assert list(access.researcher_tasks_qs(None)) == []


def test_allocate_participant_number_is_max_plus_one():
    task = EvalTaskFactory()
    assert access.allocate_participant_number(task) == 1
    ParticipantFactory(task=task, number=1)
    ParticipantFactory(task=task, number=7)
    ParticipantFactory(number=50)  # another task
    assert access.allocate_participant_number(task) == 8


@pytest.mark.django_db(transaction=True)
def test_allocate_participant_number_serializes_concurrent_creators():
    task = EvalTaskFactory()
    first_allocated = threading.Event()
    results: dict[str, object] = {}

    def create(name: str, *, hold: bool) -> None:
        close_old_connections()
        try:
            with transaction.atomic():
                number = access.allocate_participant_number(task)
                if hold:
                    first_allocated.set()
                    threading.Event().wait(0.4)  # keep the task row locked for a moment
                Participant.objects.create(task=task, number=number, kind=Participant.Kind.CODE)
            results[name] = number
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
            results[name] = exc
        finally:
            connection.close()

    first = threading.Thread(target=create, args=("first",), kwargs={"hold": True})
    first.start()
    assert first_allocated.wait(5)
    second = threading.Thread(target=create, args=("second",), kwargs={"hold": False})
    second.start()
    first.join(10)
    second.join(10)
    assert results == {"first": 1, "second": 2}


# --- accepting / read-only -------------------------------------------------------


def test_is_accepting_status_and_window():
    now = timezone.now()
    task = EvalTaskFactory(status=EvalTask.Status.OPEN)
    assert access.is_accepting(task)
    task.opens_at = now + timedelta(hours=1)
    assert not access.is_accepting(task)
    task.opens_at = now - timedelta(hours=1)
    task.closes_at = now - timedelta(minutes=1)
    assert not access.is_accepting(task)
    task.closes_at = now + timedelta(hours=1)
    assert access.is_accepting(task)
    for status in (EvalTask.Status.DRAFT, EvalTask.Status.PAUSED, EvalTask.Status.CLOSED, EvalTask.Status.ARCHIVED):
        task.status = status
        assert not access.is_accepting(task)


def test_is_accepting_for_preview_and_withdrawn_participants():
    task = EvalTaskFactory(status=EvalTask.Status.PAUSED, closes_at=timezone.now() - timedelta(days=1))
    preview = ParticipantFactory(task=task, kind=Participant.Kind.PREVIEW)
    assert access.is_accepting(task, participant=preview)
    task.status = EvalTask.Status.DRAFT
    assert not access.is_accepting(task, participant=preview)
    task.status = EvalTask.Status.OPEN
    task.closes_at = None
    withdrawn = ParticipantFactory(task=task, status=Participant.Status.WITHDRAWN)
    assert not access.is_accepting(task, participant=withdrawn)


def test_assignment_read_only_rules():
    task = EvalTaskFactory(status=EvalTask.Status.OPEN)
    assignment = AssignmentFactory(participant=ParticipantFactory(task=task))
    assert not access.assignment_read_only(assignment)
    access.ensure_writable(assignment)
    for status in (Assignment.Status.SUBMITTED, Assignment.Status.SKIPPED, Assignment.Status.EXPIRED):
        assignment.status = status
        assert access.assignment_read_only(assignment)
    assignment.status = Assignment.Status.IN_PROGRESS
    assignment.participant.status = Participant.Status.WITHDRAWN
    assert access.assignment_read_only(assignment)
    assignment.participant.status = Participant.Status.ACTIVE
    task.status = EvalTask.Status.PAUSED
    assert access.assignment_read_only(assignment)
    with pytest.raises(HumanEvalServiceError) as exc:
        access.ensure_writable(assignment)
    assert (exc.value.status, exc.value.code) == (409, "read_only")


# --- portal lookups ----------------------------------------------------------------


def _cookie_for(participant):
    return {access.cookie_name(): access.issue_session(participant)}


def test_portal_participant_prefers_the_cookie_for_its_own_task():
    user = UserFactory()
    task = EvalTaskFactory(status=EvalTask.Status.OPEN)
    other_task = EvalTaskFactory(status=EvalTask.Status.OPEN)
    cookie_participant = ParticipantFactory(task=task, kind=Participant.Kind.INVITE)
    account = ParticipantFactory(task=other_task, kind=Participant.Kind.ACCOUNT, user=user)
    request = _request(cookies=_cookie_for(cookie_participant))
    request.user = user
    identity = access.portal_identity(request)
    assert identity == PortalIdentity(participant=cookie_participant, user=user)
    assert access.portal_participant(request, task.id) == cookie_participant
    assert access.portal_participant(request, str(other_task.id)) == account
    with pytest.raises(HumanEvalServiceError) as exc:
        access.portal_participant(request, EvalTaskFactory().id)
    assert exc.value.status == 404
    with pytest.raises(HumanEvalServiceError):
        access.portal_participant(request, "bad")


def test_portal_participant_ignores_non_portal_kinds_for_users():
    user = UserFactory()
    task = EvalTaskFactory()
    ParticipantFactory(task=task, kind=Participant.Kind.INVITE, user=user)
    request = _request(auth=PortalIdentity(participant=None, user=user))
    with pytest.raises(HumanEvalServiceError):
        access.portal_participant(request, task.id)


def test_portal_identity_without_cookie_or_user_is_none():
    request = _request()
    assert access.portal_identity(request) is None
    with pytest.raises(HumanEvalServiceError):
        access.portal_participant(request, uuid.uuid4())


def test_portal_assignment_observation_attachment_ownership():
    owner = ParticipantFactory()
    stranger = ParticipantFactory(task=owner.task)
    assignment = AssignmentFactory(participant=owner)
    observation = ObservationFactory(assignment=assignment)
    attachment = AttachmentFactory(observation=observation)
    mine = _request(auth=PortalIdentity(participant=owner, user=None))
    theirs = _request(auth=PortalIdentity(participant=stranger, user=None))
    assert access.portal_assignment(mine, assignment.id) == assignment
    assert access.portal_assignment(mine, assignment.id, for_update=True) == assignment
    assert access.portal_observation(mine, observation.id) == observation
    assert access.portal_attachment(mine, attachment.id) == attachment
    for lookup, object_id in (
        (access.portal_assignment, assignment.id),
        (access.portal_observation, observation.id),
        (access.portal_attachment, attachment.id),
    ):
        with pytest.raises(HumanEvalServiceError) as exc:
            lookup(theirs, object_id)
        assert exc.value.status == 404
        with pytest.raises(HumanEvalServiceError):
            lookup(mine, "malformed")


def test_portal_lookups_for_account_users():
    user = UserFactory()
    account = ParticipantFactory(kind=Participant.Kind.PREVIEW, user=user)
    assignment = AssignmentFactory(participant=account)
    request = _request(auth=PortalIdentity(participant=None, user=user))
    assert access.portal_assignment(request, assignment.id) == assignment
    with pytest.raises(HumanEvalServiceError):
        access.portal_assignment(_request(auth=PortalIdentity(participant=None, user=UserFactory())), assignment.id)


# --- client key and throttles -----------------------------------------------------


def test_client_ip_uses_the_relay_helper(settings):
    settings.TRUSTED_PROXY_CIDRS = []
    assert access.client_ip(_request(remote_addr="198.51.100.4")) == "198.51.100.4"
    settings.TRUSTED_PROXY_CIDRS = ["10.0.0.0/8"]
    request = _request(remote_addr="10.0.0.2", HTTP_X_FORWARDED_FOR="192.0.2.9")
    assert access.client_ip(request) == "192.0.2.9"
    assert not access.shared_client_ip()
    settings.TRUSTED_PROXY_CIDRS = []
    assert access.shared_client_ip()


def test_throttle_hit_and_blocked():
    for _ in range(3):
        assert not access.throttle_hit("k", 3, 60)
    assert access.throttle_blocked("k", 3, 60)
    assert access.throttle_hit("k", 3, 60)
    assert not access.throttle_blocked("other", 3, 60)
    assert not access.throttle_blocked("k", 10, 60)


def test_lookup_failures_block_per_client_and_globally(settings, monkeypatch):
    settings.TRUSTED_PROXY_CIDRS = ["10.0.0.0/8"]
    monkeypatch.setattr(access, "LOOKUP_FAILURE_LIMIT", 2)
    first = _request(remote_addr="192.0.2.1")
    second = _request(remote_addr="192.0.2.2")
    assert not access.lookup_failures_blocked(first)
    access.record_lookup_failure(first)
    access.record_lookup_failure(first)
    assert access.lookup_failures_blocked(first)
    assert not access.lookup_failures_blocked(second)

    cache.clear()
    settings.TRUSTED_PROXY_CIDRS = []
    monkeypatch.setattr(access, "GLOBAL_LOOKUP_FAILURE_LIMIT", 3)
    access.record_lookup_failure(first)
    access.record_lookup_failure(second)
    assert not access.lookup_failures_blocked(_request(remote_addr="192.0.2.3"))
    access.record_lookup_failure(_request(remote_addr="192.0.2.4"))
    assert access.lookup_failures_blocked(_request(remote_addr="192.0.2.3"))


# --- regressions: an explicit preview wins over a stray cookie participant (C32) --------------------


def test_end_task_cookie_session_lets_the_preview_participant_win():
    """A researcher who tested their own invite link in this browser, then clicks Preview, works as the preview."""
    researcher = UserFactory()
    task = EvalTaskFactory(status=EvalTask.Status.OPEN, created_by=researcher)
    stray = ParticipantFactory(task=task, kind=Participant.Kind.INVITE)
    preview = ParticipantFactory(task=task, kind=Participant.Kind.PREVIEW, user=researcher)
    cookies = _cookie_for(stray)

    before = _request(cookies=cookies)
    before.user = researcher
    assert access.portal_participant(before, task.id) == stray  # cookie first (spec)

    clicked = _request(cookies=cookies)
    clicked.user = researcher
    response = HttpResponse()
    assert access.end_task_cookie_session(clicked, task.id, response) is True
    assert response.cookies[access.cookie_name()]["max-age"] == 0
    assert access.portal_identity(clicked) == PortalIdentity(participant=None, user=researcher)
    assert access.portal_participant(clicked, task.id) == preview
    stray.refresh_from_db()
    assert stray.session_token_hash is None  # ended server-side: a browser that kept the cookie is ignored too

    later = _request(cookies=cookies)
    later.user = researcher
    assert access.portal_participant(later, task.id) == preview


def test_end_task_cookie_session_leaves_other_tasks_and_cookieless_browsers_alone():
    researcher = UserFactory()
    task = EvalTaskFactory(status=EvalTask.Status.OPEN, created_by=researcher)
    elsewhere = ParticipantFactory(kind=Participant.Kind.INVITE)  # a cookie of another task
    request = _request(cookies=_cookie_for(elsewhere))
    request.user = researcher
    response = HttpResponse()
    assert access.end_task_cookie_session(request, task.id, response) is False
    assert access.cookie_name() not in response.cookies
    elsewhere.refresh_from_db()
    assert elsewhere.session_token_hash is not None

    bare = _request()
    bare.user = researcher
    assert access.end_task_cookie_session(bare, task.id) is False
    assert access.end_task_cookie_session(bare, "not-a-uuid") is False
