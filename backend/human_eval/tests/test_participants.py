"""services/participants.py and the participant API: codes, accounts + setup links, invites, participant edits,
deletion, listing, scoping and rollback on 4xx."""

from __future__ import annotations

import datetime as dt
import re
import threading

import pytest
from allauth.account.forms import UserTokenForm
from allauth.account.models import EmailAddress
from django.conf import settings as django_settings
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.db.models.signals import post_save
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalInvite
from backend.human_eval.models import EvaluatorProfile
from backend.human_eval.models import Participant
from backend.human_eval.models import Response
from backend.human_eval.services import access
from backend.human_eval.services import participants as participants_service
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import EvalInviteFactory
from backend.human_eval.tests.factories import EvaluatorProfileFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import ResponseFactory
from backend.human_eval.tests.factories import make_open_task
from backend.tokens.models import ApiToken
from backend.tokens.services import generate_token
from backend.users.models import User
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/human-eval"
JSON = "application/json"
CODE_FORMAT = re.compile(r"^[2-9A-HJKMNP-TV-Z]{4}-[2-9A-HJKMNP-TV-Z]{4}-[2-9A-HJKMNP-TV-Z]{4}$")
PARTICIPANT_KEYS = {
    "id",
    "label",
    "kind",
    "status",
    "email",
    "invite",
    "access_code_hint",
    "consent_at",
    "joined_at",
    "last_seen_at",
    "completed_at",
    "submitted",
    "skipped",
    "in_progress",
    "excluded",
    "exclusion_reason",
    "excluded_at",
    "note",
    "quality_flags",
    "created_at",
}


@pytest.fixture(autouse=True)
def _fresh_quota_counters():
    """The daily quota counters live in the (process-wide) cache: start every test from zero."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def researcher():
    return UserFactory()


@pytest.fixture
def api(client, researcher):
    client.force_login(researcher)
    return client


@pytest.fixture
def task(researcher):
    return make_open_task(researcher, n_apps=2)


def _url(task, suffix: str = "") -> str:
    return f"{API}/tasks/{task.id}/participants/{suffix}"


def _accounts(api, task, emails, **extra):
    return api.post(_url(task, "accounts/"), {"emails": emails, **extra}, content_type=JSON)


def _disable(task, key: str) -> None:
    task.settings = {**task.settings, "access": {**task.settings["access"], key: False}}
    task.save(update_fields=["settings"])


# --- access codes -----------------------------------------------------------------------------


def test_codes_are_returned_once_and_only_their_hash_is_stored(api, task):
    ParticipantFactory(task=task, number=3)
    res = api.post(_url(task, "codes/"), {"count": 3}, content_type=JSON)
    assert res.status_code == 200, res.content
    codes = res.json()["codes"]
    assert [row["label"] for row in codes] == ["P004", "P005", "P006"]
    for row in codes:
        assert CODE_FORMAT.match(row["code"])
        participant = Participant.objects.get(pk=row["participant_id"])
        assert (participant.kind, participant.status) == (Participant.Kind.CODE, Participant.Status.INVITED)
        assert participant.access_code_hash == access.access_code_hash(row["code"])
        assert participant.access_code_hint == row["code"][-4:]
    listing = api.get(_url(task)).content.decode()
    assert all(row["code"] not in listing for row in codes)


@pytest.mark.parametrize("count", [0, 501, -1])
def test_codes_count_is_bounded(api, task, count):
    res = api.post(_url(task, "codes/"), {"count": count}, content_type=JSON)
    assert res.status_code == 400
    assert res.json()["errors"][0]["path"] == "count"
    assert not Participant.objects.filter(task=task).exists()


def test_codes_need_access_codes_enabled(api, task):
    _disable(task, "allow_access_codes")
    res = api.post(_url(task, "codes/"), {"count": 2}, content_type=JSON)
    assert res.status_code == 409
    assert res.json()["code"] == "access_disabled"
    assert not Participant.objects.filter(task=task).exists()


def test_regenerate_code_replaces_the_hash_and_ends_sessions(api, task):
    [created] = api.post(_url(task, "codes/"), {"count": 1}, content_type=JSON).json()["codes"]
    participant = Participant.objects.get(pk=created["participant_id"])
    access.issue_session(participant)
    res = api.post(_url(task, f"{participant.id}/regenerate-code/"))
    assert res.status_code == 200
    fresh = res.json()
    assert fresh["code"] != created["code"]
    participant.refresh_from_db()
    assert participant.access_code_hash == access.access_code_hash(fresh["code"])
    assert participant.access_code_hash != access.access_code_hash(created["code"])
    assert participant.session_token_hash is None

    account = ParticipantFactory(task=task, number=9, kind=Participant.Kind.ACCOUNT, user=UserFactory())
    res = api.post(_url(task, f"{account.id}/regenerate-code/"))
    assert res.status_code == 409
    assert res.json()["code"] == "forbidden"

    other = make_open_task(task.created_by, n_apps=1)
    stranger = ParticipantFactory(task=other, number=1, kind=Participant.Kind.CODE)
    assert api.post(_url(task, f"{stranger.id}/regenerate-code/")).status_code == 404


# --- accounts ----------------------------------------------------------------------------------


def test_new_accounts_are_restricted_verified_and_get_a_working_setup_link(api, researcher, task):
    res = _accounts(api, task, ["New.Person@Example.org", "not-an-email", "new.person@example.org"])
    assert res.status_code == 200, res.content
    created, bad, repeated = res.json()["results"]
    assert created["email"] == "new.person@example.org"
    assert created["user_created"] is True
    assert created["error"] is None
    assert set(created["participant"]) == PARTICIPANT_KEYS
    assert created["participant"]["kind"] == "account"
    assert created["participant"]["status"] == "invited"
    assert created["participant"]["email"] == "new.person@example.org"
    assert bad["error"]
    assert bad["participant"] is None
    assert repeated["error"] == "Listed more than once"

    user = User.objects.get(email="new.person@example.org")
    assert not user.has_usable_password()
    assert not user.is_staff
    assert EmailAddress.objects.filter(user=user, email=user.email, verified=True, primary=True).exists()
    profile = EvaluatorProfile.objects.get(user=user)
    assert profile.is_restricted
    assert profile.created_by_id == researcher.pk

    path = created["setup_path"]
    assert path.startswith("/auth/password/reset/")
    uidb36, _, key = path.rsplit("/", 1)[1].partition("-")
    form = UserTokenForm(data={"uidb36": uidb36, "key": key})
    assert form.is_valid(), form.errors
    assert form.reset_user == user


def test_existing_users_are_linked_but_never_modified(api, task):
    existing = UserFactory(email="known@example.org")
    _, token = generate_token(existing, "cli")
    password = existing.password
    staff = UserFactory(email="staff@example.org", is_staff=True)
    res = _accounts(api, task, ["KNOWN@example.org", staff.email])
    linked, staff_row = res.json()["results"]
    assert linked["user_created"] is False
    assert linked["setup_path"] is None
    assert linked["participant"]["email"] == "known@example.org"
    assert staff_row["participant"] is not None
    existing.refresh_from_db()
    assert existing.password == password
    assert not EvaluatorProfile.objects.filter(user__in=[existing, staff]).exists()
    assert ApiToken.objects.get(pk=token.pk).revoked_at is None

    again = _accounts(api, task, ["known@example.org"]).json()["results"][0]
    assert again["error"] == "Already a participant of this task"
    assert Participant.objects.filter(task=task, user=existing).count() == 1


def test_restricted_false_only_links_existing_users(api, task):
    UserFactory(email="exists@example.org")
    results = _accounts(api, task, ["exists@example.org", "missing@example.org"], restricted=False).json()["results"]
    assert results[0]["participant"] is not None
    assert results[1]["error"] == "No account uses this email address"
    assert not User.objects.filter(email="missing@example.org").exists()


def test_staff_promotion_rolls_back_only_that_email(api, task):
    def promote(sender, instance, created, **kwargs):
        if created and instance.email == "promote.me@example.org":
            User.objects.filter(pk=instance.pk).update(is_staff=True, is_superuser=True)

    post_save.connect(promote, sender=User, dispatch_uid="human_eval_test_promote")
    try:
        promoted, fine = _accounts(api, task, ["promote.me@example.org", "fine@example.org"]).json()["results"]
    finally:
        post_save.disconnect(sender=User, dispatch_uid="human_eval_test_promote")
    assert promoted["error"] == "Cannot create this account"
    assert promoted["setup_path"] is None
    assert not User.objects.filter(email="promote.me@example.org").exists()
    assert not EmailAddress.objects.filter(email="promote.me@example.org").exists()
    assert fine["user_created"] is True
    assert Participant.objects.filter(task=task).count() == 1


def test_account_quotas_refuse_the_whole_request(api, researcher, task, monkeypatch):
    monkeypatch.setattr(participants_service, "NEW_USERS_PER_DAY", 1)
    res = _accounts(api, task, ["one@example.org", "two@example.org"])
    assert res.status_code == 409
    assert res.json()["code"] == "quota_exceeded"
    assert not User.objects.filter(email__in=["one@example.org", "two@example.org"]).exists()
    assert not Participant.objects.filter(task=task).exists()

    monkeypatch.setattr(participants_service, "NEW_USERS_PER_DAY", 200)
    monkeypatch.setattr(participants_service, "EMAILS_PER_DAY", 1)
    UserFactory(email="a@example.org")
    UserFactory(email="b@example.org")
    res = _accounts(api, task, ["a@example.org", "b@example.org"], send_email=True)
    assert res.status_code == 409
    assert not Participant.objects.filter(task=task).exists()
    assert participants_service.users_created_today(researcher) == 0


def test_every_looked_up_address_counts_against_a_daily_lookup_quota(api, researcher, task, monkeypatch):
    """C30: the per-email results reveal which addresses have an account, so lookups are capped per day."""
    monkeypatch.setattr(participants_service, "LOOKUPS_PER_DAY", 4)
    UserFactory(email="known@example.org")
    res = _accounts(api, task, ["known@example.org", "missing.one@example.org", "not-an-email"], restricted=False)
    assert res.status_code == 200, res.content
    assert participants_service.lookups_today(researcher) == 2  # invalid addresses are never looked up

    # A new-user quota refusal still tells "this address has no account": its lookup counts too.
    monkeypatch.setattr(participants_service, "NEW_USERS_PER_DAY", 0)
    assert _accounts(api, task, ["fresh@example.org"]).json()["code"] == "quota_exceeded"
    assert participants_service.lookups_today(researcher) == 3
    monkeypatch.setattr(participants_service, "NEW_USERS_PER_DAY", 200)

    res = _accounts(api, task, ["missing.two@example.org", "missing.three@example.org"], restricted=False)
    assert res.status_code == 409
    body = res.json()
    assert body["code"] == "quota_exceeded"
    assert "look up at most 4 email addresses" in body["detail"]
    assert "results" not in body  # nothing about either address is answered
    assert participants_service.lookups_today(researcher) == 3  # a refused request is not counted

    # restricted:true lookups count the same way; this one fills the quota exactly.
    assert _accounts(api, task, ["fresh@example.org"]).status_code == 200
    assert participants_service.lookups_today(researcher) == 4
    assert _accounts(api, task, ["known@example.org"]).json()["code"] == "quota_exceeded"
    assert sorted(Participant.objects.filter(task=task).values_list("user__email", flat=True)) == [
        "fresh@example.org",
        "known@example.org",
    ]

    # Per researcher: someone else's lookups are not limited by this researcher's.
    other = UserFactory()
    other_task = make_open_task(other, n_apps=1)
    assert participants_service.add_accounts(other_task, other, {"emails": ["known@example.org"]})["results"][0][
        "participant"
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cap", ["new_users", "emails"])
def test_concurrent_requests_on_different_tasks_share_the_daily_caps(monkeypatch, cap):
    """C31: requests of one researcher on two tasks are serialized, so the second sees the first's
    committed accounts / counted emails and cannot pass the cap alongside it."""
    researcher = UserFactory()
    first_task = make_open_task(researcher, n_apps=1, conditions=1)
    second_task = make_open_task(researcher, n_apps=1, conditions=1)
    if cap == "new_users":
        monkeypatch.setattr(participants_service, "NEW_USERS_PER_DAY", 1)
        emails, payload, hook = ("first@example.org", "second@example.org"), {}, "_create_evaluator"
    else:
        monkeypatch.setattr(participants_service, "EMAILS_PER_DAY", 1)
        for email in ("first@example.org", "second@example.org"):
            UserFactory(email=email)
        emails, payload, hook = ("first@example.org", "second@example.org"), {"send_email": True}, "_email_message"
    monkeypatch.setattr(participants_service, "_dispatch_emails", lambda messages: None)

    inside, release = threading.Event(), threading.Event()
    real = getattr(participants_service, hook)

    def paused(*args):
        """The first request stops past its cap checks, before it commits (or counts its email)."""
        value = real(*args)
        if "first@example.org" in args:
            inside.set()
            release.wait(10)
        return value

    monkeypatch.setattr(participants_service, hook, paused)
    outcomes: dict[str, object] = {}

    def add(key, task, email):
        try:
            outcomes[key] = participants_service.add_accounts(task, researcher, {"emails": [email], **payload})
        except HumanEvalServiceError as exc:
            outcomes[key] = exc
        finally:
            connection.close()

    first = threading.Thread(target=add, args=("first", first_task, emails[0]))
    second = threading.Thread(target=add, args=("second", second_task, emails[1]))
    first.start()
    try:
        assert inside.wait(10)
        second.start()
        second.join(1.5)
        assert second.is_alive(), "the second request must wait for the first one's transaction"
    finally:
        release.set()
        first.join(10)
        if second.ident is not None:
            second.join(10)

    assert outcomes["first"]["results"][0]["error"] is None
    refused = outcomes["second"]
    assert isinstance(refused, HumanEvalServiceError)
    assert (refused.status, refused.code) == (409, "quota_exceeded")
    accounts = Participant.objects.filter(task__in=[first_task, second_task], user__isnull=False)
    assert list(accounts.values_list("user__email", flat=True)) == [emails[0]]
    if cap == "new_users":
        assert participants_service.users_created_today(researcher) == 1
    else:
        assert participants_service.emails_sent_today(researcher) == 1


def test_account_emails_are_sent_after_commit_with_public_links(
    api,
    task,
    settings,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    settings.FRONTEND_PUBLIC_ORIGIN = "https://lab.example.org"
    monkeypatch.setattr(
        "backend.common.threading.dispatch_in_thread",
        lambda target, *args, **kwargs: target(*args),
    )
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        res = _accounts(api, task, ["mailme@example.org"], send_email=True)
        assert mail.outbox == []  # nothing leaves before the transaction commits
    assert res.status_code == 200
    assert len(callbacks) == 1
    [message] = mail.outbox
    assert message.to == ["mailme@example.org"]
    assert "https://lab.example.org/auth/password/reset/" in message.body
    assert "https://lab.example.org/eval" in message.body
    assert task.title in message.subject


def test_accounts_input_rules(api, task):
    assert _accounts(api, task, []).status_code == 400
    assert _accounts(api, task, [f"u{n}@example.org" for n in range(201)]).status_code == 400
    _disable(task, "allow_accounts")
    res = _accounts(api, task, ["x@example.org"])
    assert res.status_code == 409
    assert res.json()["code"] == "access_disabled"
    assert not User.objects.filter(email="x@example.org").exists()


def test_setup_link_rules(api, researcher, task):
    created = _accounts(api, task, ["setup@example.org"]).json()["results"][0]
    participant_id = created["participant"]["id"]
    res = api.post(_url(task, f"{participant_id}/setup-link/"))
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["setup_path"].startswith("/auth/password/reset/")
    expires = dt.datetime.fromisoformat(body["expires_at"])
    expected = timezone.now() + dt.timedelta(seconds=django_settings.PASSWORD_RESET_TIMEOUT)
    assert abs((expires - expected).total_seconds()) < 60

    user = User.objects.get(email="setup@example.org")
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    res = api.post(_url(task, f"{participant_id}/setup-link/"))
    assert res.status_code == 409
    assert res.json()["code"] == "forbidden"

    linked = _accounts(api, task, [UserFactory(email="linked@example.org").email]).json()["results"][0]
    assert api.post(_url(task, f"{linked['participant']['id']}/setup-link/")).json()["code"] == "forbidden"

    someone_elses = EvaluatorProfileFactory(created_by=UserFactory()).user
    other = ParticipantFactory(task=task, number=50, kind=Participant.Kind.ACCOUNT, user=someone_elses)
    assert api.post(_url(task, f"{other.id}/setup-link/")).status_code == 409


# --- participant edits ---------------------------------------------------------------------------


def test_exclude_include_and_note(api, researcher, task):
    participant = ParticipantFactory(task=task, number=1)
    url = _url(task, f"{participant.id}/")
    res = api.patch(url, {"excluded": True, "exclusion_reason": "Speeder", "note": "check"}, content_type=JSON)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == PARTICIPANT_KEYS
    assert (body["excluded"], body["exclusion_reason"], body["note"]) == (True, "Speeder", "check")
    assert body["excluded_at"] is not None
    participant.refresh_from_db()
    assert participant.excluded_by_id == researcher.pk
    body = api.patch(url, {"excluded": False}, content_type=JSON).json()
    assert (body["excluded"], body["excluded_at"], body["exclusion_reason"]) == (False, None, "")
    assert api.patch(url, {"status": "active"}, content_type=JSON).status_code == 400
    assert api.patch(url, {"note": "x" * 5001}, content_type=JSON).status_code == 400


def test_withdraw_expires_open_assignments_and_ends_the_session(api, task):
    participant = ParticipantFactory(task=task, number=1)
    access.issue_session(participant)
    first, second = task.items.order_by("position")
    open_one = AssignmentFactory(task=task, participant=participant, item=first, status=Assignment.Status.IN_PROGRESS)
    done = AssignmentFactory(task=task, participant=participant, item=second, status=Assignment.Status.SUBMITTED)
    body = api.patch(_url(task, f"{participant.id}/"), {"status": "withdrawn"}, content_type=JSON).json()
    assert body["status"] == "withdrawn"
    participant.refresh_from_db()
    assert participant.withdrawn_at is not None
    assert participant.session_token_hash is None
    open_one.refresh_from_db()
    done.refresh_from_db()
    assert (open_one.status, done.status) == (Assignment.Status.EXPIRED, Assignment.Status.SUBMITTED)


def test_delete_participant_removes_their_data(api, task):
    participant = ParticipantFactory(task=task, number=1)
    assignment = AssignmentFactory(task=task, participant=participant, item=task.items.first())
    ResponseFactory(assignment=assignment)
    assert api.delete(_url(task, f"{participant.id}/")).status_code == 204
    assert not Participant.objects.filter(pk=participant.pk).exists()
    assert not Assignment.objects.filter(pk=assignment.pk).exists()
    assert not Response.objects.filter(assignment_id=assignment.pk).exists()


def test_deleting_a_rater_with_work_after_open_is_logged(api, task):
    """C45: results must not silently stay confirmatory after a rater's data is deleted."""
    assert task.opened_at is not None
    worked = ParticipantFactory(task=task, number=1)
    AssignmentFactory(task=task, participant=worked, item=task.items.first(), status=Assignment.Status.SUBMITTED)
    idle = ParticipantFactory(task=task, number=2)
    assert api.delete(_url(task, f"{idle.id}/")).status_code == 204
    task.refresh_from_db()
    assert not any(e["field"].startswith("participant_deleted") for e in task.protocol_log)
    assert api.delete(_url(task, f"{worked.id}/")).status_code == 204
    task.refresh_from_db()
    assert [e["field"] for e in task.protocol_log] == ["participant_deleted:1"]


def test_list_search_filters_counts_and_flags(api, task, monkeypatch):
    invite = EvalInviteFactory(task=task, label="Morning class")
    joined = ParticipantFactory(task=task, number=1, invite=invite)
    account = ParticipantFactory(
        task=task,
        number=2,
        kind=Participant.Kind.ACCOUNT,
        user=UserFactory(email="rater@example.org"),
    )
    # Fixed, digit-free emails: the search "2" below also matches email addresses.
    preview_user = UserFactory(email="preview.owner@example.org")
    ParticipantFactory(task=task, number=3, kind=Participant.Kind.PREVIEW, user=preview_user)
    first, second = task.items.order_by("position")
    AssignmentFactory(task=task, participant=joined, item=first, status=Assignment.Status.SUBMITTED)
    AssignmentFactory(task=task, participant=joined, item=second, status=Assignment.Status.IN_PROGRESS)
    monkeypatch.setattr(
        participants_service.quality,
        "participant_flags",
        lambda current: {str(joined.id): ["speeding"]},
    )

    body = api.get(_url(task)).json()
    assert body["total"] == 3
    rows = {row["label"]: row for row in body["items"]}
    assert rows["P001"]["invite"] == {"id": str(invite.id), "label": "Morning class"}
    assert (rows["P001"]["submitted"], rows["P001"]["in_progress"]) == (1, 1)
    assert rows["P001"]["quality_flags"] == ["speeding"]
    assert rows["P003"]["kind"] == "preview"

    def labels(**query):
        return [row["label"] for row in api.get(_url(task), query).json()["items"]]

    assert labels(search="P002") == ["P002"]
    assert labels(search="2") == ["P002"]
    assert labels(search="rater@") == ["P002"]
    assert labels(search="morning") == ["P001"]
    assert labels(kind="account") == ["P002"]
    assert labels(kind="nonsense") == []
    assert labels(status="active") == ["P001", "P002", "P003"]
    assert account.label == "P002"


# --- invites ---------------------------------------------------------------------------------------


def test_invites_crud(api, task):
    res = api.post(f"{API}/tasks/{task.id}/invites/", {"label": "Class A", "max_uses": 30}, content_type=JSON)
    assert res.status_code == 201
    invite = res.json()
    assert invite["path"] == f"/eval/join/{invite['token']}"
    assert (invite["label"], invite["max_uses"], invite["uses"], invite["is_active"]) == ("Class A", 30, 0, True)
    assert [row["id"] for row in api.get(f"{API}/tasks/{task.id}/invites/").json()] == [invite["id"]]

    url = f"{API}/tasks/{task.id}/invites/{invite['id']}/"
    body = api.patch(
        url,
        {"is_active": False, "expires_at": "2026-12-24T18:00:00Z", "max_uses": None},
        content_type=JSON,
    ).json()
    assert (body["is_active"], body["max_uses"]) == (False, None)
    assert body["expires_at"].startswith("2026-12-24T18:00:00")
    assert body["label"] == "Class A"
    res = api.patch(url, {"max_uses": 0}, content_type=JSON)
    assert res.status_code == 400
    assert res.json()["errors"][0]["path"] == "max_uses"
    assert api.delete(url).status_code == 204
    assert not EvalInvite.objects.filter(pk=invite["id"]).exists()


def test_invites_need_invite_links_and_are_task_scoped(api, task):
    other = make_open_task(task.created_by, n_apps=1)
    foreign_invite = EvalInviteFactory(task=other)
    assert (
        api.patch(f"{API}/tasks/{task.id}/invites/{foreign_invite.id}/", {"label": "x"}, content_type=JSON).status_code
        == 404
    )
    assert api.delete(f"{API}/tasks/{task.id}/invites/{foreign_invite.id}/").status_code == 404
    foreign_invite.refresh_from_db()
    assert foreign_invite.label != "x"
    _disable(task, "allow_invite_links")
    res = api.post(f"{API}/tasks/{task.id}/invites/", {"label": "Late"}, content_type=JSON)
    assert res.status_code == 409
    assert res.json()["code"] == "access_disabled"
    assert not EvalInvite.objects.filter(task=task).exists()


# --- scoping ---------------------------------------------------------------------------------------


def test_participant_endpoints_are_owner_and_task_scoped(api, researcher, task):
    foreign = make_open_task(UserFactory(), n_apps=1)
    foreign_participant = ParticipantFactory(task=foreign, number=1)
    for res in (
        api.get(_url(foreign)),
        api.post(_url(foreign, "codes/"), {"count": 1}, content_type=JSON),
        _accounts(api, foreign, ["x@example.org"]),
        api.get(f"{API}/tasks/{foreign.id}/invites/"),
        api.patch(_url(foreign, f"{foreign_participant.id}/"), {"note": "x"}, content_type=JSON),
    ):
        assert res.status_code == 404
    # a participant id from another task under my task's URL
    for res in (
        api.patch(_url(task, f"{foreign_participant.id}/"), {"note": "x"}, content_type=JSON),
        api.delete(_url(task, f"{foreign_participant.id}/")),
        api.post(_url(task, f"{foreign_participant.id}/setup-link/")),
    ):
        assert res.status_code == 404
    foreign_participant.refresh_from_db()
    assert foreign_participant.note == ""
    assert not Participant.objects.filter(task=foreign).exclude(pk=foreign_participant.pk).exists()


def test_secondary_addresses_link_the_existing_user(api, task):
    owner = UserFactory(email="primary@example.org")
    EmailAddress.objects.create(user=owner, email="alias@example.org", verified=True, primary=False)
    [result] = _accounts(api, task, ["Alias@example.org"]).json()["results"]
    assert result["user_created"] is False
    assert Participant.objects.get(task=task).user_id == owner.pk
    assert not User.objects.filter(email="alias@example.org").exists()


def test_a_lost_creation_race_is_a_per_email_error(api, task, monkeypatch):
    from django.db import IntegrityError

    real = participants_service._create_evaluator

    def racing(email, researcher):
        if email == "race@example.org":
            msg = "duplicate key value violates unique constraint"
            raise IntegrityError(msg)
        return real(email, researcher)

    monkeypatch.setattr(participants_service, "_create_evaluator", racing)
    raced, fine = _accounts(api, task, ["race@example.org", "calm@example.org"]).json()["results"]
    assert raced["error"] == "This account already exists"
    assert fine["user_created"] is True
    assert [p.label for p in Participant.objects.filter(task=task)] == [fine["participant"]["label"]]
