"""Tests for backend.realtime."""

from __future__ import annotations

import json
from http import HTTPStatus
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from django.http import StreamingHttpResponse
from django.test import RequestFactory

from backend.realtime.api.views import sse_stream
from backend.realtime.events import publish
from backend.realtime.subscribers import _build_sse

# ---------------------------------------------------------------------------
# events.py tests
# ---------------------------------------------------------------------------


class TestPublish:
    @patch("backend.realtime.events._get_redis")
    def test_publish_serializes_json(self, mock_get_redis):
        """publish() serialises the payload as JSON and forwards to redis."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish("generation:42", {"type": "status", "status": "running"})

        assert mock_redis.publish.called
        channel_arg, payload_arg = mock_redis.publish.call_args[0]
        assert channel_arg == "generation:42"
        parsed = json.loads(payload_arg)
        assert parsed["type"] == "status"
        assert parsed["status"] == "running"
        assert parsed["channel"] == "generation:42"
        assert "published_at" in parsed

    @patch("backend.realtime.events._get_redis")
    def test_publish_includes_channel_and_timestamp(self, mock_get_redis):
        """publish() always injects channel and published_at into the payload."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish("dashboard", {"type": "update"})

        _, payload_arg = mock_redis.publish.call_args[0]
        parsed = json.loads(payload_arg)
        assert parsed["channel"] == "dashboard"
        assert "published_at" in parsed

    @patch("backend.realtime.events._get_redis")
    def test_publish_does_not_raise_on_redis_error(self, mock_get_redis):
        """publish() swallows Redis errors so the caller is not disrupted."""
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = Exception("Redis down")
        mock_get_redis.return_value = mock_redis

        # Must not raise
        publish("analysis:1", {"type": "status", "status": "failed"})

    @patch("backend.realtime.events._get_redis")
    def test_publish_runtime_channel(self, mock_get_redis):
        """publish() works with runtime:<container_id> channels."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish("runtime:99", {"type": "status", "status": "running"})

        channel_arg, _ = mock_redis.publish.call_args[0]
        assert channel_arg == "runtime:99"


# ---------------------------------------------------------------------------
# subscribers.py tests
# ---------------------------------------------------------------------------


class TestBuildSse:
    def test_sse_format(self):
        """_build_sse produces the standard event/data double-newline format."""
        msg = _build_sse("status", {"status": "running"})
        assert msg.startswith("event: status\n")
        assert "data: " in msg
        assert msg.endswith("\n\n")

    def test_sse_data_is_json(self):
        """_build_sse serialises the payload as JSON in the data line."""
        payload = {"type": "result", "analyzer": "mypy", "finding_count": 3}
        msg = _build_sse("result", payload)
        data_line = next(line for line in msg.splitlines() if line.startswith("data: "))
        parsed = json.loads(data_line[len("data: ") :])
        assert parsed["analyzer"] == "mypy"
        assert parsed["finding_count"] == pytest.approx(3)


# ---------------------------------------------------------------------------
# api/views.py tests
# ---------------------------------------------------------------------------


class TestSseStreamView:
    def setup_method(self):
        self.factory = RequestFactory()

    def test_unauthenticated_returns_401(self):
        """Unauthenticated requests must be rejected with HTTP 401."""
        request = self.factory.get("/api/realtime/stream", {"channels": "generation:1"})
        request.user = MagicMock(is_authenticated=False)
        response = sse_stream(request)
        assert response.status_code == HTTPStatus.UNAUTHORIZED

    def test_missing_channels_returns_400(self):
        """A request without ?channels= must return HTTP 400."""
        request = self.factory.get("/api/realtime/stream")
        request.user = MagicMock(is_authenticated=True)
        response = sse_stream(request)
        assert response.status_code == HTTPStatus.BAD_REQUEST

    @patch("backend.realtime.api.views.event_stream")
    def test_authenticated_returns_streaming_response(self, mock_stream):
        """Valid authenticated request returns a StreamingHttpResponse."""
        mock_stream.return_value = iter([": heartbeat\n\n"])
        request = self.factory.get("/api/realtime/stream", {"channels": "generation:1"})
        request.user = MagicMock(is_authenticated=True)
        response = sse_stream(request)
        assert isinstance(response, StreamingHttpResponse)
        assert response["Content-Type"] == "text/event-stream"
        assert response["Cache-Control"] == "no-cache"
        assert response["X-Accel-Buffering"] == "no"
