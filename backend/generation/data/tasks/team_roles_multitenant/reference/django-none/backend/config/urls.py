"""URL configuration: the team_roles_multitenant kit reference (logic in core.py; URLs end with a slash)."""

import json

import core
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import path


def health(request):
    return JsonResponse({"status": "ok"})


def index(request):
    return render(request, "index.html")


def body_of(request):
    try:
        return json.loads(request.body or b"null")
    except ValueError:
        return None


def reply(result):
    status, payload = result
    return HttpResponse(status=204) if payload is None else JsonResponse(payload, status=status)


def route(**methods):
    """A view dispatching on the method; ``public`` handlers get the request, the others (uid, request, ...)."""

    def view(request, **kwargs):
        handler = methods.get(request.method)
        if handler is None:
            return JsonResponse({"error": "method not allowed"}, status=405)
        if getattr(handler, "public", False):
            return reply(handler(request, **kwargs))
        uid = core.user_from_header(request.headers.get("Authorization"))
        if uid is None:
            return JsonResponse({"error": "authentication required"}, status=401)
        return reply(handler(uid, request, **kwargs))

    return view


def public(fn):
    fn.public = True
    return fn


urlpatterns = [
    path("api/health", health),
    path("api/auth/register/", route(POST=public(lambda r: core.register(body_of(r))))),
    path("api/auth/login/", route(POST=public(lambda r: core.login(body_of(r))))),
    path(
        "api/orgs/",
        route(GET=lambda u, r: core.list_orgs(u), POST=lambda u, r: core.create_org(u, body_of(r))),
    ),
    path(
        "api/orgs/<int:org_id>/members/",
        route(
            GET=lambda u, r, org_id: core.list_members(u, org_id),
            POST=lambda u, r, org_id: core.add_member(u, org_id, body_of(r)),
        ),
    ),
    path(
        "api/orgs/<int:org_id>/members/<str:username>/",
        route(
            PATCH=lambda u, r, org_id, username: core.update_member(u, org_id, username, body_of(r)),
            DELETE=lambda u, r, org_id, username: core.remove_member(u, org_id, username),
        ),
    ),
    path(
        "api/orgs/<int:org_id>/projects/",
        route(
            GET=lambda u, r, org_id: core.list_projects(u, org_id, r.GET.get("status")),
            POST=lambda u, r, org_id: core.create_project(u, org_id, body_of(r)),
        ),
    ),
    path(
        "api/projects/<int:project_id>/",
        route(
            GET=lambda u, r, project_id: core.get_project(u, project_id),
            PATCH=lambda u, r, project_id: core.update_project(u, project_id, body_of(r)),
            DELETE=lambda u, r, project_id: core.delete_project(u, project_id),
        ),
    ),
    path("", index),
]
