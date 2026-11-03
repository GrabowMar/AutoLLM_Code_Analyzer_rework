"""PortalAuth: cookie participant, session user, both, idle expiry, withdrawal and CSRF on unsafe methods.

The portal router has no operations yet (stage-2 owners add them), so these tests
mount a tiny test-only NinjaAPI that uses :class:`PortalAuth`, served through a
module-object ``ROOT_URLCONF``.
"""

from __future__ import annotations

import sys
from datetime import timedelta

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.test import RequestFactory
from django.urls import path
from django.utils import timezone
from ninja import NinjaAPI
from ninja import Router

from backend.human_eval.auth import PortalAuth
from backend.human_eval.auth import PortalIdentity
from backend.human_eval.models import Participant
from backend.human_eval.services import access
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.urls_u1 import urlpatterns as project_urlpatterns
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CSRF_TOKEN = "a" * 32

_router = Router(auth=PortalAuth())


def _describe(request) -> dict:
    identity = request.auth
    return {
        "participant": str(identity.participant.id) if identity.participant else None,
        "user": identity.user.email if identity.user else None,
    }


@_router.get("/whoami/")
def whoami(request):
    return _describe(request)


@_router.post("/poke/")
def poke(request):
    return _describe(request)


@_router.post("/fail/")
def fail(request):
    raise HumanEvalServiceError(409, "Nope", "read_only", [{"path": "x", "message": "bad"}])


_test_api = NinjaAPI(urls_namespace="human_eval_portal_auth_test", docs_url=None, openapi_url=None)
_test_api.add_router("/portal/", _router)


@_test_api.exception_handler(HumanEvalServiceError)
def _handle(request, exc):
    return _test_api.create_response(request, exc.payload(), status=exc.status)


# The project's URLs stay mounted: Django's CSRF failure page (403_csrf.html → base.html) reverses
# ``users:detail`` for logged-in users.
urlpatterns = [path("t/", _test_api.urls), *project_urlpatterns]


@pytest.fixture
def portal_urls(settings):
    settings.ROOT_URLCONF = sys.modules[__name__]


def _csrf_client(*, logged_in=None, participant=None) -> Client:
    client = Client(enforce_csrf_checks=True)
    if logged_in is not None:
        client.force_login(logged_in)
    if participant is not None:
        client.cookies[access.cookie_name()] = access.issue_session(participant)
    return client


def _with_csrf(client: Client) -> dict:
    client.cookies[django_settings.CSRF_COOKIE_NAME] = CSRF_TOKEN
    return {"HTTP_X_CSRFTOKEN": CSRF_TOKEN}


# --- authenticate --------------------------------------------------------------


def test_no_cookie_and_no_user_returns_none_without_raising():
    request = RequestFactory().get("/")
    request.user = type("Anon", (), {"is_authenticated": False})()
    assert PortalAuth()(request) is None


def test_openapi_scheme_uses_the_configured_cookie_name():
    auth = PortalAuth()
    assert auth.openapi_security_schema == {"type": "apiKey", "in": "cookie", "name": access.cookie_name()}


def test_cookie_participant(portal_urls):
    participant = ParticipantFactory()
    response = _csrf_client(participant=participant).get("/t/portal/whoami/")
    assert response.status_code == 200
    assert response.json() == {"participant": str(participant.id), "user": None}


def test_session_user(portal_urls):
    user = UserFactory()
    response = _csrf_client(logged_in=user).get("/t/portal/whoami/")
    assert response.status_code == 200
    assert response.json() == {"participant": None, "user": user.email}


def test_both_identities_are_reported(portal_urls):
    user = UserFactory()
    participant = ParticipantFactory()
    response = _csrf_client(logged_in=user, participant=participant).get("/t/portal/whoami/")
    assert response.json() == {"participant": str(participant.id), "user": user.email}


def test_anonymous_gets_401(portal_urls):
    assert Client().get("/t/portal/whoami/").status_code == 401


def test_unknown_cookie_is_ignored(portal_urls):
    client = Client()
    client.cookies[access.cookie_name()] = "not-a-real-token"
    assert client.get("/t/portal/whoami/").status_code == 401
    user = UserFactory()
    client.force_login(user)
    assert client.get("/t/portal/whoami/").json() == {"participant": None, "user": user.email}


def test_withdrawn_participant_is_not_authenticated(portal_urls):
    participant = ParticipantFactory(status=Participant.Status.WITHDRAWN)
    assert _csrf_client(participant=participant).get("/t/portal/whoami/").status_code == 401


def test_idle_session_expires_and_is_revoked(portal_urls, settings):
    settings.HUMAN_EVAL_SESSION_IDLE_HOURS = 24
    participant = ParticipantFactory()
    client = _csrf_client(participant=participant)
    Participant.objects.filter(pk=participant.pk).update(last_seen_at=timezone.now() - timedelta(hours=25))
    assert client.get("/t/portal/whoami/").status_code == 401
    participant.refresh_from_db()
    assert participant.session_token_hash is None


def test_last_seen_is_refreshed_at_most_once_a_minute(portal_urls, django_capture_on_commit_callbacks):
    participant = ParticipantFactory()
    client = _csrf_client(participant=participant)
    old = timezone.now() - timedelta(hours=2)
    Participant.objects.filter(pk=participant.pk).update(last_seen_at=old)
    # Written after commit, so a long request (the console) never holds the participant row lock.
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        assert client.get("/t/portal/whoami/").status_code == 200
    assert len(callbacks) == 1
    participant.refresh_from_db()
    refreshed = participant.last_seen_at
    assert refreshed > old
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        assert client.get("/t/portal/whoami/").status_code == 200
    assert callbacks == []
    participant.refresh_from_db()
    assert participant.last_seen_at == refreshed


def test_rotated_token_stops_the_old_cookie(portal_urls):
    participant = ParticipantFactory()
    client = _csrf_client(participant=participant)
    access.issue_session(participant)
    assert client.get("/t/portal/whoami/").status_code == 401


# --- CSRF ----------------------------------------------------------------------


def test_post_without_csrf_token_is_403_with_cookie(portal_urls):
    participant = ParticipantFactory()
    response = _csrf_client(participant=participant).post("/t/portal/poke/", data={}, content_type="application/json")
    assert response.status_code == 403


def test_post_without_csrf_token_is_403_even_without_the_cookie(portal_urls):
    assert Client(enforce_csrf_checks=True).post("/t/portal/poke/").status_code == 403
    user = UserFactory()
    assert _csrf_client(logged_in=user).post("/t/portal/poke/").status_code == 403


def test_post_with_csrf_token_passes(portal_urls):
    participant = ParticipantFactory()
    client = _csrf_client(participant=participant)
    response = client.post("/t/portal/poke/", data={}, content_type="application/json", **_with_csrf(client))
    assert response.status_code == 200
    assert response.json()["participant"] == str(participant.id)
    anonymous = Client(enforce_csrf_checks=True)
    assert anonymous.post("/t/portal/poke/", **_with_csrf(anonymous)).status_code == 401


def test_safe_methods_need_no_csrf_token(portal_urls):
    user = UserFactory()
    assert _csrf_client(logged_in=user).get("/t/portal/whoami/").status_code == 200


def test_service_errors_render_the_structured_body(portal_urls):
    client = _csrf_client(logged_in=UserFactory())
    response = client.post("/t/portal/fail/", **_with_csrf(client))
    assert response.status_code == 409
    assert response.json() == {"detail": "Nope", "code": "read_only", "errors": [{"path": "x", "message": "bad"}]}


def test_project_handler_maps_service_errors():
    from config.api import api
    from config.api import handle_human_eval_error

    request = RequestFactory().get("/")
    response = handle_human_eval_error(request, HumanEvalServiceError(404, "Task not found", "not_found"))
    assert response.status_code == 404
    assert response["Content-Type"].startswith("application/json")
    assert api.renderer is not None
    import json

    assert json.loads(response.content) == {"detail": "Task not found", "code": "not_found", "errors": []}


def test_portal_identity_truthiness():
    assert not PortalIdentity(participant=None, user=None)
    assert PortalIdentity(participant=None, user=UserFactory())


# --- signals and tasks wired by HumanEvalConfig.ready() --------------------------------


def test_restricting_an_evaluator_revokes_their_api_tokens():
    from backend.human_eval.tests.factories import EvaluatorProfileFactory
    from backend.tokens.models import ApiToken
    from backend.tokens.services import generate_token

    user = UserFactory()
    _, active = generate_token(user, "cli")
    _, revoked = generate_token(user, "old")
    earlier = timezone.now() - timedelta(days=3)
    ApiToken.objects.filter(pk=revoked.pk).update(revoked_at=earlier)
    other_user = UserFactory()
    _, unrelated = generate_token(other_user, "cli")

    EvaluatorProfileFactory(user=user, is_restricted=True)

    active.refresh_from_db()
    revoked.refresh_from_db()
    unrelated.refresh_from_db()
    assert active.revoked_at is not None
    assert revoked.revoked_at == earlier
    assert unrelated.revoked_at is None


def test_unrestricted_profiles_and_staff_keep_their_tokens():
    from backend.human_eval.tests.factories import EvaluatorProfileFactory
    from backend.tokens.services import generate_token

    user = UserFactory()
    _, token = generate_token(user, "cli")
    profile = EvaluatorProfileFactory(user=user, is_restricted=False)
    token.refresh_from_db()
    assert token.revoked_at is None
    profile.is_restricted = True
    profile.save()
    token.refresh_from_db()
    assert token.revoked_at is not None

    staff = UserFactory(is_staff=True)
    _, staff_token = generate_token(staff, "cli")
    EvaluatorProfileFactory(user=staff, is_restricted=True)
    staff_token.refresh_from_db()
    assert staff_token.revoked_at is None


def test_deleting_an_attachment_removes_its_file_after_commit(django_capture_on_commit_callbacks):
    from backend.human_eval.tests.factories import AttachmentFactory

    attachment = AttachmentFactory()
    storage, name = attachment.file.storage, attachment.file.name
    assert storage.exists(name)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        attachment.delete()
    assert storage.exists(name)  # nothing happens before the transaction commits
    for callback in callbacks:
        callback()
    assert not storage.exists(name)


def test_cascade_deletes_remove_attachment_files(django_capture_on_commit_callbacks):
    from backend.human_eval.tests.factories import AttachmentFactory

    attachment = AttachmentFactory()
    storage, name = attachment.file.storage, attachment.file.name
    with django_capture_on_commit_callbacks(execute=True):
        attachment.observation.assignment.task.delete()
    assert not storage.exists(name)


def test_reaper_schedule_is_registered_idempotently():
    from django_celery_beat.models import PeriodicTask

    from backend.human_eval import signals

    PeriodicTask.objects.filter(name=signals.REAPER_TASK_NAME).delete()
    signals.ensure_reaper_schedule()
    signals.ensure_reaper_schedule()
    tasks = PeriodicTask.objects.filter(name=signals.REAPER_TASK_NAME)
    assert tasks.count() == 1
    task = tasks.get()
    assert task.task == "backend.human_eval.tasks.reap_idle_apps_task"
    assert task.enabled
    assert (task.interval.every, task.interval.period) == (120, "seconds")


def test_reap_idle_apps_task_never_raises(monkeypatch):
    from backend.human_eval import tasks
    from backend.human_eval.services import hosting

    monkeypatch.setattr(hosting, "reap_idle_apps", lambda: 3)
    assert tasks.reap_idle_apps_task() == 3

    def boom():
        msg = "docker down"
        raise RuntimeError(msg)

    monkeypatch.setattr(hosting, "reap_idle_apps", boom)
    assert tasks.reap_idle_apps_task() == 0
