"""WorkspaceMiddleware: a co-author's requests run as the workspace owner (parity), on both the sync and async path."""

from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest
from asgiref.sync import async_to_sync
from asgiref.sync import iscoroutinefunction
from django.conf import settings as django_settings
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.http import HttpResponse
from django.http import StreamingHttpResponse
from django.test import AsyncClient
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from backend.coauthors import middleware
from backend.coauthors import services
from backend.coauthors.identity import is_acting
from backend.coauthors.identity import real_user
from backend.coauthors.middleware import WorkspaceMiddleware
from backend.coauthors.models import CoauthorAction
from backend.coauthors.models import Coauthorship
from backend.coauthors.tests.conftest import login
from backend.coauthors.tests.conftest import post
from backend.coauthors.tests.factories import CoauthorshipFactory
from backend.coauthors.tests.factories import select
from backend.credentials.models import UserApiCredential
from backend.credentials.services.validator import ValidationResult
from backend.generation.testing.factories import StudyFactory
from backend.reports.models import Report
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

ENDED = {"detail": "You no longer have access to this workspace.", "code": "workspace_ended", "errors": []}
STREAM = "/api/realtime/stream"


def _report(user, title):
    return Report.objects.create(
        report_type="comprehensive",
        title=title,
        config={},
        created_by=user,
        status=Report.Status.COMPLETED,
    )


def test_registered_right_after_the_evaluator_gate():
    stack = list(django_settings.MIDDLEWARE)
    index = stack.index("backend.human_eval.middleware.EvaluatorScopeMiddleware")
    assert stack[index + 1] == "backend.coauthors.middleware.WorkspaceMiddleware"
    assert index > stack.index("django.contrib.auth.middleware.AuthenticationMiddleware")


# ---------------------------------------------------------------------------
# Parity
# ---------------------------------------------------------------------------


def test_me_is_the_owner_with_the_same_schema(acting_client, member_client, owner, member):
    acting = acting_client.get("/api/users/me/")
    own = member_client.get("/api/users/me/")
    assert acting.status_code == 200
    assert acting.json()["email"] == owner.email
    assert own.json()["email"] == member.email
    assert set(acting.json()) == set(own.json()) == {"email", "name", "url", "is_staff", "is_evaluator"}
    assert acting_client.get(f"/api/users/{owner.pk}/").json()["email"] == owner.email
    assert acting_client.get(f"/api/users/{member.pk}/").status_code == 404
    assert acting_client.get("/api/users/me/activity/").status_code == 200


def test_lists_show_the_owners_rows(acting_client, member_client, owner, member):
    study = StudyFactory(created_by=owner, name="Owner study")
    StudyFactory(created_by=member, name="Member study")
    _report(owner, "Owner report")
    _report(member, "Member report")

    studies = acting_client.get("/api/generation/studies/").json()["items"]
    assert [item["id"] for item in studies] == [str(study.pk)]
    reports = acting_client.get("/api/reports/").json()["reports"]
    assert [item["title"] for item in reports] == ["Owner report"]
    # Routes that declare auth=None and read request.user follow the swap too.
    exported = acting_client.get("/api/export/reports.json")
    assert exported.status_code == 200
    assert "Owner report" in exported.content.decode()
    assert "Member report" not in exported.content.decode()

    own = member_client.get("/api/reports/").json()["reports"]
    assert [item["title"] for item in own] == ["Member report"]
    assert "Owner report" not in member_client.get("/api/export/reports.json").content.decode()


def test_what_a_member_creates_belongs_to_the_owner(acting_client, owner, member, monkeypatch):
    monkeypatch.setattr(
        "backend.credentials.api.views.validate_for_provider",
        lambda provider, raw: ValidationResult(status="valid", message="ok"),
    )
    response = acting_client.put(
        "/api/credentials/openrouter/",
        data=json.dumps({"api_key": "sk-or-v1-test-key"}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert UserApiCredential.objects.filter(user=owner, provider="openrouter").exists()
    assert not UserApiCredential.objects.filter(user=member).exists()
    assert "sk-or-v1-test-key" not in response.content.decode()


def test_other_peoples_objects_stay_hidden(acting_client):
    stranger_report = _report(UserFactory(), "Stranger report")
    assert acting_client.get(f"/api/reports/{stranger_report.report_id}/").status_code in (403, 404)


# ---------------------------------------------------------------------------
# Trail and last activity
# ---------------------------------------------------------------------------


def test_actions_are_recorded_for_writes_only(acting_client, coauthorship):
    acting_client.get("/api/users/me/")
    acting_client.get("/api/reports/?limit=5")
    assert CoauthorAction.objects.count() == 0
    deleted = acting_client.delete("/api/credentials/openrouter/?x=secret")
    assert deleted.status_code == 200
    action = CoauthorAction.objects.get()
    assert (action.coauthorship, action.method, action.path, action.status_code) == (
        coauthorship,
        "DELETE",
        "/api/credentials/openrouter/",
        200,
    )


def test_last_active_is_touched_at_most_once_a_minute(acting_client, coauthorship):
    assert coauthorship.last_active_at is None
    acting_client.get("/api/users/me/")
    coauthorship.refresh_from_db()
    first = coauthorship.last_active_at
    assert first is not None
    acting_client.get("/api/users/me/")
    coauthorship.refresh_from_db()
    assert coauthorship.last_active_at == first
    Coauthorship.objects.filter(pk=coauthorship.pk).update(last_active_at=first - dt.timedelta(minutes=2))
    acting_client.get("/api/users/me/")
    coauthorship.refresh_from_db()
    assert coauthorship.last_active_at > first


def test_real_and_denied_requests_do_not_count_as_activity(acting_client, coauthorship):
    acting_client.get("/api/coauthors/context/")
    acting_client.get("/api/tokens/")
    coauthorship.refresh_from_db()
    assert coauthorship.last_active_at is None


# ---------------------------------------------------------------------------
# Users without a selection are untouched
# ---------------------------------------------------------------------------


def _coauthor_queries(context) -> list[str]:
    return [query["sql"] for query in context.captured_queries if "coauthors_" in query["sql"]]


def test_no_coauthor_query_without_a_selection(member_client, client, coauthorship):
    with CaptureQueriesContext(connection) as context:
        assert member_client.get("/api/users/me/").status_code == 200
        assert member_client.get("/api/reports/").status_code == 200
        assert member_client.get("/api/tokens/").status_code == 200
        assert member_client.delete("/api/credentials/openrouter/").status_code == 200
        assert client.get("/api/users/me/").status_code == 401
        assert client.get("/api/users/bootstrap-status/").status_code == 200
    assert _coauthor_queries(context) == []
    assert CoauthorAction.objects.count() == 0


def test_one_coauthor_query_per_request_while_acting(acting_client, coauthorship):
    Coauthorship.objects.filter(pk=coauthorship.pk).update(last_active_at=timezone.now())
    with CaptureQueriesContext(connection) as context:
        assert acting_client.get("/api/users/me/").status_code == 200
    assert len(_coauthor_queries(context)) == 1


def test_responses_are_identical_for_users_without_coauthorships(member, member_client):
    """The same request with and without the middleware in the stack gives the same bytes."""
    stack = [name for name in django_settings.MIDDLEWARE if name != "backend.coauthors.middleware.WorkspaceMiddleware"]
    with_middleware = member_client.get("/api/users/me/")
    tokens_with = member_client.get("/api/tokens/")
    from django.test import override_settings

    with override_settings(MIDDLEWARE=stack):
        without = login(member)
        plain = without.get("/api/users/me/")
        tokens_plain = without.get("/api/tokens/")
    assert with_middleware.content == plain.content
    assert tokens_with.content == tokens_plain.content
    assert with_middleware.status_code == plain.status_code == 200


# ---------------------------------------------------------------------------
# The middleware itself, both paths
# ---------------------------------------------------------------------------


def _request(path, user, selected=None, method="get"):
    request = getattr(RequestFactory(), method)(path)
    request.user = user
    session = SessionStore()
    if selected is not None:
        session[services.SESSION_KEY] = selected
    request.session = session

    async def auser():
        return user

    request.auser = auser
    return request


def _sync_view(seen):
    def view(request):
        seen.update(user=request.user, actor=getattr(request, "actor", None), acting=is_acting(request))
        seen["real"] = real_user(request)
        return HttpResponse("ok")

    return view


def _async_view(seen):
    async def view(request):
        await asyncio.sleep(0)
        seen.update(user=request.user, auser=await request.auser(), actor=getattr(request, "actor", None))
        return HttpResponse("ok")

    return view


def test_capability_flags():
    assert WorkspaceMiddleware.sync_capable
    assert WorkspaceMiddleware.async_capable
    assert not iscoroutinefunction(WorkspaceMiddleware(_sync_view({})))
    assert iscoroutinefunction(WorkspaceMiddleware(_async_view({})))


def test_swap_and_restore_on_the_sync_path(owner, member, coauthorship):
    seen: dict = {}
    request = _request("/api/reports/", member, coauthorship.pk)
    original_auser = request.auser
    response = WorkspaceMiddleware(_sync_view(seen))(request)
    assert response.content == b"ok"
    assert (seen["user"], seen["actor"], seen["real"], seen["acting"]) == (owner, member, member, True)
    # Outer middleware sees the real person again.
    assert request.user == member
    assert request.auser is original_auser
    assert not is_acting(request)
    assert request.coauthorship == coauthorship


def test_swap_and_restore_on_the_async_path(owner, member, coauthorship):
    seen: dict = {}
    request = _request("/api/reports/", member, coauthorship.pk)
    original_auser = request.auser
    response = async_to_sync(WorkspaceMiddleware(_async_view(seen)))(request)
    assert response.content == b"ok"
    assert (seen["user"], seen["auser"], seen["actor"]) == (owner, owner, member)
    assert request.user == member
    assert request.auser is original_auser
    assert not is_acting(request)


def test_the_real_person_is_restored_when_the_view_raises(owner, member, coauthorship):
    def boom(request):
        assert request.user == owner
        raise RuntimeError

    request = _request("/api/reports/", member, coauthorship.pk)
    with pytest.raises(RuntimeError):
        WorkspaceMiddleware(boom)(request)
    assert request.user == member


@pytest.mark.parametrize("path", ["/api/coauthors/context/", "/api/users/bootstrap-status/"])
def test_real_mode_keeps_the_real_person_on_both_paths(path, member, coauthorship):
    for runner, view in ((lambda mw, r: mw(r), _sync_view), (lambda mw, r: async_to_sync(mw)(r), _async_view)):
        seen: dict = {}
        request = _request(path, member, coauthorship.pk)
        assert runner(WorkspaceMiddleware(view(seen)), request).content == b"ok"
        assert seen["user"] == member
        assert seen["actor"] is None
        assert request.coauthorship == coauthorship


def test_denied_and_ended_on_both_paths(member, coauthorship):
    foreign = CoauthorshipFactory()
    for runner, view in ((lambda mw, r: mw(r), _sync_view), (lambda mw, r: async_to_sync(mw)(r), _async_view)):
        seen: dict = {}
        denied = runner(WorkspaceMiddleware(view(seen)), _request("/api/tokens/", member, coauthorship.pk))
        assert denied.status_code == 403
        assert json.loads(denied.content)["code"] == "acting_denied"
        stale = _request("/api/reports/", member, foreign.pk)
        ended = runner(WorkspaceMiddleware(view(seen)), stale)
        assert ended.status_code == 409
        assert json.loads(ended.content) == ENDED
        assert services.SESSION_KEY not in stale.session
        assert seen == {}  # the view never ran


CHANGED = {
    "detail": "The workspace was switched in another tab. Reload to continue.",
    "code": "workspace_changed",
    "errors": [],
}


def test_a_tab_behind_a_workspace_switch_is_refused_on_both_paths(member, coauthorship):
    """``X-Workspace`` names the workspace the tab shows; a mismatch never reaches the view."""
    selection_cases = [
        (coauthorship.pk, "own"),  # the tab shows the own account, the session moved into a workspace
        (None, str(coauthorship.pk)),  # the tab shows the workspace, the session moved back
        (coauthorship.pk, str(coauthorship.pk + 1)),  # another workspace
    ]
    for runner, view in ((lambda mw, r: mw(r), _sync_view), (lambda mw, r: async_to_sync(mw)(r), _async_view)):
        for selected, claimed in selection_cases:
            seen: dict = {}
            request = _request("/api/credentials/openrouter/", member, selected, method="put")
            request.META["HTTP_X_WORKSPACE"] = claimed
            with CaptureQueriesContext(connection) as context:
                response = runner(WorkspaceMiddleware(view(seen)), request)
            assert response.status_code == 409, (selected, claimed)
            assert json.loads(response.content) == CHANGED
            assert seen == {}
            assert _coauthor_queries(context) == []  # decided from the session alone
            assert request.session.get(services.SESSION_KEY) == selected  # the choice is left alone


def test_a_matching_or_missing_workspace_header_changes_nothing(member, coauthorship):
    for runner, view in ((lambda mw, r: mw(r), _sync_view), (lambda mw, r: async_to_sync(mw)(r), _async_view)):
        for selected, claimed in ((coauthorship.pk, str(coauthorship.pk)), (None, "own"), (coauthorship.pk, None)):
            seen: dict = {}
            request = _request("/api/reports/", member, selected, method="post")
            if claimed is not None:
                request.META["HTTP_X_WORKSPACE"] = claimed
            assert runner(WorkspaceMiddleware(view(seen)), request).content == b"ok"
            assert (seen["actor"] == member) is (selected is not None)


def test_switching_and_joining_are_not_refused_by_a_stale_header(member, coauthorship):
    """``REAL`` routes run as the real person anyway, so a tab that is behind can still switch."""
    for path in ("/api/coauthors/workspace/", "/api/coauthors/join/accept/"):
        seen: dict = {}
        request = _request(path, member, None, method="post")
        request.META["HTTP_X_WORKSPACE"] = str(coauthorship.pk)
        assert WorkspaceMiddleware(_sync_view(seen))(request).content == b"ok"


def test_the_workspace_header_through_the_stack(acting_client, member_client, coauthorship):
    claim = {"HTTP_X_WORKSPACE": "own"}
    stale = acting_client.delete("/api/credentials/openrouter/", **claim)
    assert stale.status_code == 409
    assert stale.json() == CHANGED
    assert acting_client.get("/api/users/me/").status_code == 200  # the session is untouched
    assert member_client.delete("/api/credentials/openrouter/", **claim).status_code == 200
    behind = member_client.delete("/api/credentials/openrouter/", HTTP_X_WORKSPACE=str(coauthorship.pk))
    assert behind.json()["code"] == "workspace_changed"
    assert CoauthorAction.objects.count() == 0  # refused before anything ran or was recorded


def test_out_of_scope_and_anonymous_requests_pass_untouched(member, coauthorship):
    from django.contrib.auth.models import AnonymousUser

    cases = [
        _request("/_allauth/browser/v1/auth/session", member, coauthorship.pk),
        _request("/users/~update/", member, coauthorship.pk),
        _request("/api/human-eval/portal/me/", member, coauthorship.pk),
        _request("/api/mcp", member, coauthorship.pk),
        _request("/api/reports/", member, None),
        _request("/api/reports/", AnonymousUser(), coauthorship.pk),
    ]
    bearer = _request("/api/reports/", member, coauthorship.pk)
    bearer.META["HTTP_AUTHORIZATION"] = "Bearer llml_whatever"
    for request in [*cases, bearer]:
        seen: dict = {}
        with CaptureQueriesContext(connection) as context:
            assert WorkspaceMiddleware(_sync_view(seen))(request).content == b"ok"
        assert seen["actor"] is None, request.path
        assert not hasattr(request, "coauthorship")
        assert _coauthor_queries(context) == []


# ---------------------------------------------------------------------------
# Event stream (async view, AsyncClient)
# ---------------------------------------------------------------------------


async def aselect(client, coauthorship) -> None:
    """Async twin of ``factories.select``."""
    session = await client.asession()
    await session.aset(services.SESSION_KEY, coauthorship.pk)
    await session.asave()


@pytest.mark.django_db(transaction=True)
class TestEventStream:
    def _open(self, user, coauthorship, channel):
        async def run():
            client = AsyncClient()
            await client.aforce_login(user)
            if coauthorship is not None:
                await aselect(client, coauthorship)
            response = await client.get(f"{STREAM}?channels={channel}")
            body = [chunk async for chunk in response.streaming_content] if response.streaming else None
            return response, body

        return async_to_sync(run)()

    def test_member_gets_the_owners_channel_until_removed(self, monkeypatch):
        owner, member = UserFactory(), UserFactory()
        coauthorship = CoauthorshipFactory(owner=owner, member=member)
        study = StudyFactory(created_by=owner)
        seen: dict = {}

        async def fake_stream(channels):
            seen["channels"] = channels
            yield ": heartbeat\n\n"

        monkeypatch.setattr("backend.realtime.api.views.event_stream", fake_stream)
        channel = f"study:{study.id}"

        refused, _ = self._open(member, None, channel)
        assert refused.status_code == 403
        response, body = self._open(member, coauthorship, channel)
        assert response.status_code == 200
        assert body == [b": heartbeat\n\n"]
        assert seen["channels"] == [channel]

        services.remove_member(owner, coauthorship.pk)
        ended, _ = self._open(member, coauthorship, channel)
        assert ended.status_code == 409
        assert json.loads(ended.content) == ENDED

    def test_async_api_request_runs_as_the_owner(self):
        owner, member = UserFactory(), UserFactory()
        coauthorship = CoauthorshipFactory(owner=owner, member=member)

        async def run():
            client = AsyncClient()
            await client.aforce_login(member)
            await aselect(client, coauthorship)
            me = await client.get("/api/users/me/")
            denied = await client.post("/api/tokens/", data="{}", content_type="application/json")
            session_view = await client.get("/_allauth/browser/v1/auth/session")
            return me, denied, session_view

        me, denied, session_view = async_to_sync(run)()
        assert me.json()["email"] == owner.email
        assert denied.status_code == 403
        assert denied.json()["code"] == "acting_denied"
        assert session_view.json()["data"]["user"]["email"] == member.email
        assert list(CoauthorAction.objects.values_list("method", "path", "status_code")) == [
            ("POST", "/api/tokens/", 403),
        ]


# ---------------------------------------------------------------------------
# guard_stream
# ---------------------------------------------------------------------------


def test_guard_stream_leaves_other_responses_alone(coauthorship, member):
    plain = HttpResponse("x")
    assert middleware.guard_stream(plain, coauthorship, member) is plain
    sync_stream = StreamingHttpResponse(iter([b"a", b"b"]))
    assert middleware.guard_stream(sync_stream, coauthorship, member) is sync_stream
    assert b"".join(sync_stream.streaming_content) == b"ab"


def test_select_helper_and_post_helper_work(member, coauthorship):
    """The fixtures' own plumbing: a selection put into the session is what the middleware reads."""
    client = login(member)
    select(client, coauthorship)
    assert client.session[services.SESSION_KEY] == coauthorship.pk
    assert post(client, "/api/coauthors/workspace/", {"coauthorship_id": None}).status_code == 200
