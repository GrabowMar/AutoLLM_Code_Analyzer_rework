"""Async SSE view, its generator and per-channel authorization (spec §F.6)."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import redis
from asgiref.sync import async_to_sync
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connection
from django.test import AsyncClient

from backend.generation.tests.factories import ExperimentFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.inference.models import LLMEndpoint
from backend.realtime import authz
from backend.realtime.subscribers import event_stream
from backend.users.tests.factories import UserFactory

STREAM = "/api/realtime/stream"


def _endpoint(owner, **kw):
    kw.setdefault("slug", f"box-{uuid.uuid4().hex[:6]}")
    kw.setdefault("name", "Box")
    kw.setdefault("kind", LLMEndpoint.Kind.OLLAMA)
    return LLMEndpoint.objects.create(owner=owner, **kw)


def _redis_or_skip() -> redis.Redis:
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip("Redis is not reachable")
    return client


# ── authorization (sync) ──────────────────────────────────────────────


@pytest.mark.django_db
class TestResolveChannel:
    def test_owner_and_staff_get_their_objects(self):
        owner = UserFactory()
        experiment = ExperimentFactory(created_by=owner)
        job = GenerationJobFactory(created_by=owner)
        assert authz.resolve_channel(owner, f"experiment:{experiment.id}") == f"experiment:{experiment.id}"
        assert authz.resolve_channel(owner, f"generation:{job.id}") == f"generation:{job.id}"
        staff = UserFactory(is_staff=True)
        assert authz.resolve_channel(staff, f"experiment:{experiment.id}") == f"experiment:{experiment.id}"

    def test_foreign_unknown_and_malformed_channels_are_refused(self):
        owner, stranger = UserFactory(), UserFactory()
        experiment = ExperimentFactory(created_by=owner)
        job = GenerationJobFactory(created_by=owner)
        for channel in (
            f"experiment:{experiment.id}",
            f"generation:{job.id}",
            f"analysis:{uuid.uuid4()}",
            f"runtime:{uuid.uuid4()}",
            "analysis:12",  # analysis runs have UUID ids
            "experiment:not-a-uuid",
            f"experiment:{experiment.id}:extra",
            "automation:1",
            "secrets",
            "",
        ):
            assert authz.resolve_channel(stranger, channel) is None, channel

    def test_aggregate_channels_need_only_a_login(self):
        user = UserFactory()
        assert authz.resolve_channel(user, "capacity") == "capacity"
        assert authz.resolve_channel(user, "dashboard") == "dashboard"
        assert authz.resolve_channel(None, "capacity") is None

    def test_endpoint_non_owners_are_mapped_to_the_public_variant(self):
        owner, other = UserFactory(), UserFactory()
        private = _endpoint(owner)
        shared = _endpoint(owner, visibility=LLMEndpoint.Visibility.SHARED)
        assert authz.resolve_channel(owner, f"endpoint:{private.id}") == f"endpoint:{private.id}"
        assert authz.resolve_channel(other, f"endpoint:{private.id}") is None
        assert authz.resolve_channel(other, f"endpoint:{shared.id}") == f"endpoint:{shared.id}:public"
        assert authz.resolve_channel(owner, f"endpoint:{shared.id}:public") == f"endpoint:{shared.id}:public"
        assert authz.resolve_channel(UserFactory(is_staff=True), f"endpoint:{private.id}") == f"endpoint:{private.id}"

    def test_one_refused_channel_refuses_the_stream(self):
        owner = UserFactory()
        experiment = ExperimentFactory(created_by=owner)
        mine = f"experiment:{experiment.id}"
        assert authz.authorize_channels(owner, [mine, "capacity", mine]) == [mine, "capacity"]
        assert authz.authorize_channels(owner, [mine, f"experiment:{uuid.uuid4()}"]) is None


# ── the async view ────────────────────────────────────────────────────


def _get(user, query: str):
    async def run():
        client = AsyncClient()
        if user is not None:
            await client.aforce_login(user)
        return await client.get(f"{STREAM}?{query}")

    return async_to_sync(run)()


@pytest.mark.django_db(transaction=True)
class TestSseView:
    def test_unauthenticated_is_401(self):
        assert _get(None, "channels=capacity").status_code == 401

    def test_missing_and_too_many_channels_are_400(self):
        user = UserFactory()
        assert _get(user, "").status_code == 400
        many = ",".join(["capacity"] * (authz.MAX_CHANNELS + 1))
        assert _get(user, f"channels={many}").status_code == 400

    def test_foreign_or_malformed_channel_is_403(self):
        user = UserFactory()
        foreign = ExperimentFactory(created_by=UserFactory())
        assert _get(user, f"channels=experiment:{foreign.id}").status_code == 403
        assert _get(user, "channels=experiment:nope").status_code == 403
        assert _get(user, f"channels=capacity,runtime:{uuid.uuid4()}").status_code == 403

    def test_view_is_async_and_non_atomic(self):
        from backend.realtime.api.views import sse_stream

        assert asyncio.iscoroutinefunction(sse_stream)
        assert getattr(sse_stream, "_non_atomic_requests", None)

    def test_closes_its_db_connection_before_streaming(self, monkeypatch):
        user = UserFactory()
        seen: dict[str, object] = {}

        async def fake_stream(channels):
            # The thread-sensitive executor runs the view's DB work; its connection must be closed.
            seen["closed"] = await sync_to_async(lambda: connection.connection is None)()
            seen["channels"] = channels
            yield ": heartbeat\n\n"

        monkeypatch.setattr("backend.realtime.api.views.event_stream", fake_stream)

        async def run():
            client = AsyncClient()
            await client.aforce_login(user)
            response = await client.get(f"{STREAM}?channels=capacity")
            body = [chunk async for chunk in response.streaming_content]
            return response, body

        response, body = async_to_sync(run)()
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"
        assert response["Cache-Control"] == "no-cache"
        assert body == [b": heartbeat\n\n"]
        assert seen == {"closed": True, "channels": ["capacity"]}

    def test_non_owner_endpoint_stream_subscribes_to_public(self, monkeypatch):
        owner, other = UserFactory(), UserFactory()
        shared = _endpoint(owner, visibility=LLMEndpoint.Visibility.SHARED)
        seen = {}

        async def fake_stream(channels):
            seen["channels"] = channels
            yield ": heartbeat\n\n"

        monkeypatch.setattr("backend.realtime.api.views.event_stream", fake_stream)

        async def run():
            client = AsyncClient()
            await client.aforce_login(other)
            response = await client.get(f"{STREAM}?channels=endpoint:{shared.id}")
            return [chunk async for chunk in response.streaming_content]

        async_to_sync(run)()
        assert seen["channels"] == [f"endpoint:{shared.id}:public"]


# ── the async generator against Redis ─────────────────────────────────


def test_event_stream_yields_events_published_after_connect_and_heartbeats():
    publisher = _redis_or_skip()
    channel = f"experiment:{uuid.uuid4()}"

    async def run():
        stream = event_stream([channel], heartbeat_interval=0.5)
        frames = [await anext(stream)]  # the subscription is live once the first frame is out
        await asyncio.to_thread(publisher.publish, channel, json.dumps({"type": "state", "status": "paused"}))
        deadline = asyncio.get_running_loop().time() + 5
        while len(frames) < 4 and asyncio.get_running_loop().time() < deadline:
            frames.append(await asyncio.wait_for(anext(stream), timeout=5))
        await stream.aclose()
        return frames

    frames = async_to_sync(run)()
    assert frames[0] == ": heartbeat\n\n"
    events = [f for f in frames if f.startswith("event: ")]
    assert events, frames
    assert events[0].startswith("event: state\n")
    assert json.loads(events[0].split("data: ", 1)[1])["status"] == "paused"
    assert frames.count(": heartbeat\n\n") >= 2
