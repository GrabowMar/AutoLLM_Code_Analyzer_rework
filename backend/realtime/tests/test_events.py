"""Tests for backend.realtime."""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from backend.generation.contracts.events import run_channel as contract_run_channel
from backend.generation.contracts.events import study_channel as contract_study_channel
from backend.realtime import events
from backend.realtime.events import publish
from backend.realtime.subscribers import _build_sse

RUN_ID = "0b9c6a8e-3f8e-4c43-9a55-0f5d3f1d2a77"
STUDY_ID = "5a1d2e3f-4b5c-4d6e-8f70-112233445566"

# ---------------------------------------------------------------------------
# events.py tests
# ---------------------------------------------------------------------------


class TestRunAndStudyChannels:
    def test_channel_names_match_the_contract(self):
        assert events.run_channel(RUN_ID) == f"run:{RUN_ID}" == contract_run_channel(RUN_ID)
        assert events.study_channel(STUDY_ID) == f"study:{STUDY_ID}" == contract_study_channel(STUDY_ID)

    def test_v2_publishers_are_gone(self):
        assert not hasattr(events, "publish_experiment_event")
        assert not hasattr(events, "experiment_channel")

    def test_publish_run_event_validates_and_publishes(self):
        with patch.object(events, "publish") as pub:
            events.publish_run_event(RUN_ID, {"type": "status", "run_id": RUN_ID, "status": "running"})
        pub.assert_called_once_with(f"run:{RUN_ID}", {"type": "status", "run_id": RUN_ID, "status": "running"})

    def test_publish_study_event_validates_and_publishes(self):
        event = {"type": "state", "status": "completed", "pause_reason": ""}
        with patch.object(events, "publish") as pub:
            events.publish_study_event(STUDY_ID, event)
        pub.assert_called_once_with(f"study:{STUDY_ID}", event)

    @pytest.mark.parametrize(
        ("publisher", "event"),
        [
            ("publish_run_event", {"type": "status", "run_id": RUN_ID, "status": "exploded"}),
            ("publish_run_event", {"type": "tick"}),  # a study event on a run channel
            ("publish_study_event", {"type": "job", "status": "completed"}),  # the v2 "job" event
            ("publish_study_event", {"type": "state", "status": "completed", "extra": 1}),
        ],
    )
    def test_malformed_events_are_not_published(self, publisher, event):
        with patch.object(events, "publish") as pub:
            getattr(events, publisher)(STUDY_ID, event)  # never raises
        pub.assert_not_called()


class TestPublish:
    @patch("backend.realtime.events._get_redis")
    def test_publish_serializes_json(self, mock_get_redis):
        """publish() serialises the payload as JSON and forwards to redis."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish(f"run:{RUN_ID}", {"type": "status", "status": "running"})

        assert mock_redis.publish.called
        channel_arg, payload_arg = mock_redis.publish.call_args[0]
        assert channel_arg == f"run:{RUN_ID}"
        parsed = json.loads(payload_arg)
        assert parsed["type"] == "status"
        assert parsed["status"] == "running"
        assert parsed["channel"] == f"run:{RUN_ID}"
        assert "published_at" in parsed

    @patch("backend.realtime.events._get_redis")
    def test_publish_includes_channel_and_timestamp(self, mock_get_redis):
        """publish() always injects channel and published_at into the payload."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish("capacity", {"type": "update"})

        _, payload_arg = mock_redis.publish.call_args[0]
        parsed = json.loads(payload_arg)
        assert parsed["channel"] == "capacity"
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
