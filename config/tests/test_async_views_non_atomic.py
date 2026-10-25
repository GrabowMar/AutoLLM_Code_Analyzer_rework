"""Every coroutine view must opt out of ``ATOMIC_REQUESTS`` (spec §0.2, §I).

Django wraps a view in ``transaction.atomic`` when ``ATOMIC_REQUESTS`` is on.
For an async view that would hold a transaction (and a DB connection) across
every ``await``: relay long-polls and SSE streams would pin Postgres
connections for minutes. The resolver walk below catches any new async view
that forgets ``@transaction.non_atomic_requests``.
"""

from __future__ import annotations

from asgiref.sync import iscoroutinefunction
from django.db import DEFAULT_DB_ALIAS
from django.urls import URLPattern
from django.urls import URLResolver
from django.urls import get_resolver


def _walk(patterns, prefix=""):
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, route)
        elif isinstance(pattern, URLPattern):
            yield route, pattern.callback


def _async_views():
    return [(route, view) for route, view in _walk(get_resolver().url_patterns) if iscoroutinefunction(view)]


def test_the_walk_finds_the_known_async_views():
    routes = [route for route, _view in _async_views()]
    assert any(route.startswith("api/relay/v1/") for route in routes), routes
    assert any("realtime" in route or "stream" in route for route in routes), routes


def test_every_async_view_is_non_atomic():
    offenders = [
        route for route, view in _async_views() if DEFAULT_DB_ALIAS not in getattr(view, "_non_atomic_requests", set())
    ]
    assert offenders == []
