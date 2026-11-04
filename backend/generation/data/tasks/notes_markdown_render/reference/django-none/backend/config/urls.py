"""URL configuration: the notes_markdown_render oracle reference (logic in core.py; URLs end with a slash)."""

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
    if payload is None:
        return HttpResponse(status=204)
    if isinstance(payload, core.Html):
        return HttpResponse(str(payload), status=status, content_type="text/html; charset=utf-8")
    return JsonResponse(payload, status=status)


def route(**methods):
    """A view dispatching on the method; ``public`` handlers get the request, the others (uid, request, ...)."""

    def view(request, **kwargs):
        handler = methods.get(request.method)
        if handler is None:
            return JsonResponse({"detail": "method not allowed"}, status=405)
        if getattr(handler, "public", False):
            return reply(handler(request, **kwargs))
        uid = core.user_from_header(request.headers.get("Authorization"))
        if uid is None:
            return JsonResponse({"detail": "authentication required"}, status=401)
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
        "api/notes/",
        route(GET=lambda u, r: core.list_notes(u), POST=lambda u, r: core.create_note(u, body_of(r))),
    ),
    path(
        "api/notes/<int:note_id>/",
        route(
            GET=lambda u, r, note_id: core.get_note(u, note_id),
            PATCH=lambda u, r, note_id: core.update_note(u, note_id, body_of(r)),
            DELETE=lambda u, r, note_id: core.delete_note(u, note_id),
        ),
    ),
    path("api/notes/<int:note_id>/html/", route(GET=lambda u, r, note_id: core.render_note(u, note_id))),
    path(
        "api/public/notes/<int:note_id>/html/",
        route(GET=public(lambda r, note_id: core.render_public(note_id))),
    ),
    path("", index),
]
