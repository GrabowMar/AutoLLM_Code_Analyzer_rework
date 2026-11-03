"""Per-call telemetry builders (contract C1), template-mode capture, agent ingestion and served summary."""

from __future__ import annotations

import copy
import logging
import threading
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from backend.generation.services import call_telemetry
from backend.generation.services.openrouter_client import OpenRouterClient
from backend.generation.services.openrouter_client import OpenRouterError
from backend.generation.tests.factories import GenerationRunFactory
from backend.users.tests.factories import UserFactory

# A real gateway llm_calls line (OpenRouter, opencode harness), content trimmed.
GATEWAY_LINE = {
    "seq": 1,
    "ts": 1793057151.3396952,
    "request": {"model": "deepseek/deepseek-v4.1-flash", "seed": 1179069785, "max_tokens": 32000},
    "policy_diff": {"forced": {"seed": None}},
    "status": 200,
    "response": {
        "id": "gen-1793057151-ktGXKg2sr0N5N3lEYrCo",
        "model": "deepseek/deepseek-v4.1-flash",
        "provider": "CoreWeave",
        "system_fingerprint": None,
        "content": "I'll start by exploring the project structure.",
        "reasoning": None,
        "tool_calls": [{"id": "t1", "name": "bash", "arguments": "{}"}],
        "finish_reason": "tool_calls",
        "usage": {
            "prompt_tokens": 2461,
            "completion_tokens": 159,
            "total_tokens": 2620,
            "cost": 0.00011086416,
            "is_byok": False,
            "prompt_tokens_details": {"cached_tokens": 2432, "cache_write_tokens": 0},
            "cost_details": {"upstream_inference_cost": 0.000111984},
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
        "error": None,
    },
    "duration_s": 1.207,
    "upstream_retries": 0,
}

# A real OpenCode Go call (subscription billing) as the gateway logs it.
OPENCODE_LINE = {
    "seq": 1,
    "ts": 1793599611.4,
    "status": 200,
    "response": {
        "id": "0217935996120616438f8a7da3edc67692fae0fdfd3f961d542dc",
        "model": "deepseek-v4-pro",
        "provider": "opencode-go",
        "finish_reason": "tool_calls",
        "usage": {
            "cost": 0.0,
            "prompt_tokens": 8221,
            "completion_tokens": 96,
            "total_tokens": 8317,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 10},
            "opencode": {
                "tokens": {
                    "input": 8221,
                    "output": 96,
                    "reasoning": 10,
                    "cache_read": 0,
                    "cache_write_1h": 0,
                    "cache_write_5m": 3,
                },
                "allowance": 0.04492752,
                "list_cost": 0.01123188,
                "billing_source": "go",
            },
        },
        "error": None,
    },
    "duration_s": 3.111,
    "upstream_retries": 0,
}

# A real single-shot chat completion (OpenRouter shape), content trimmed.
TEMPLATE_RESPONSE = {
    "id": "gen-1792681872-Xmc3qJ52o3c5FwuFOtyK",
    "provider": "Wafer",
    "model": "deepseek/deepseek-v4.1-flash",
    "object": "chat.completion",
    "system_fingerprint": "wafer-1",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "native_finish_reason": "stop",
            "message": {"role": "assistant", "content": "```jsx\n...\n```"},
        },
    ],
    "usage": {
        "cost": 0.0145157661,
        "is_byok": False,
        "total_tokens": 24147,
        "prompt_tokens": 2246,
        "completion_tokens": 21901,
        "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 1751},
    },
}


class TestGatewayBuilder:
    def test_openrouter_line(self):
        t = call_telemetry.from_gateway_call(GATEWAY_LINE)
        assert t["schema"] == 1
        assert t["source"] == "gateway"
        assert t["started_at"].startswith("2026-10-2")
        assert t["duration_s"] == 1.207
        assert t["ended_at"] > t["started_at"]
        assert t["attempts"] == 1
        assert t["upstream_retries"] == 0
        assert t["provider"] == "CoreWeave"
        assert t["served_model"] == "deepseek/deepseek-v4.1-flash"
        assert t["finish_reason"] == "tool_calls"
        assert "system_fingerprint" not in t  # null upstream = unknown, not ""
        assert t["tokens"] == {"prompt": 2461, "completion": 159, "reasoning": 0, "cached": 2432, "cache_write": 0}
        assert t["cost"] == pytest.approx(0.00011086416)
        assert "list_cost" not in t
        assert "error" not in t

    def test_older_gateway_line_without_retries_leaves_attempts_unknown(self):
        line = copy.deepcopy(GATEWAY_LINE)
        del line["upstream_retries"]
        t = call_telemetry.from_gateway_call(line)
        assert "attempts" not in t
        assert "upstream_retries" not in t

    def test_opencode_go_reports_list_cost_separately(self):
        t = call_telemetry.from_gateway_call(OPENCODE_LINE)
        assert t["cost"] == 0.0
        assert t["list_cost"] == pytest.approx(0.01123188)
        assert t["billing_source"] == "go"
        assert t["provider"] == "opencode-go"
        # No cache_write_tokens in prompt_tokens_details: the provider's own counts fill it.
        assert t["tokens"]["cache_write"] == 3
        assert t["tokens"]["reasoning"] == 10

    def test_usage_without_details_is_unknown_not_zero(self):
        line = copy.deepcopy(GATEWAY_LINE)
        line["response"]["usage"] = {"prompt_tokens": 10, "completion_tokens": 5}
        t = call_telemetry.from_gateway_call(line)
        assert t["tokens"] == {"prompt": 10, "completion": 5, "reasoning": None, "cached": None, "cache_write": None}
        assert "cost" not in t

    def test_failed_call_has_error_block(self):
        line = {
            "seq": 7,
            "ts": 1793054132.1,
            "status": 429,
            "response": {"error": {"message": "limit_burst_rate", "code": 429}},
            "duration_s": 88.2,
            "upstream_retries": 4,
        }
        t = call_telemetry.from_gateway_call(line)
        assert t["status"] == 429
        assert t["attempts"] == 5
        assert t["error"] == {"status": 429, "code": "429", "message": "limit_burst_rate"}

    def test_transport_failure_line(self):
        t = call_telemetry.from_gateway_call({"seq": 2, "ts": 1.0, "status": 502, "error": "ConnectionResetError()"})
        assert t["error"]["message"] == "ConnectionResetError()"
        assert "tokens" not in t


class TestTemplateBuilders:
    def test_chat_response_with_timing_and_retries(self):
        t = call_telemetry.from_chat_response(
            TEMPLATE_RESPONSE,
            started_at=1792681872.0,
            ended_at=1792681990.5,
            attempts=2,
            failed_attempts=[{"status": 429, "kind": "rate_limited"}],
        )
        assert t["source"] == "orchestrator"
        assert t["duration_s"] == 118.5
        assert t["attempts"] == 2
        assert t["upstream_retries"] == 1
        assert t["failed_attempts"] == [{"status": 429, "kind": "rate_limited"}]
        assert t["provider"] == "Wafer"
        assert t["finish_reason"] == "stop"
        assert t["native_finish_reason"] == "stop"
        assert t["system_fingerprint"] == "wafer-1"
        assert t["tokens"]["reasoning"] == 1751
        assert t["status"] == 200

    def test_local_served_block(self):
        served = {
            "endpoint": "gpu-box",
            "model": "qwen2.5-coder:7b",
            "digest": "sha256:ab",
            "num_ctx": 32768,
            "load_ms": 1200,
            "upstream_ms": 5400,
            "eval_ms": None,
            "attempt": 1,
            "request_id": "7c1e",
        }
        t = call_telemetry.from_chat_response(
            {"choices": [{"finish_reason": "stop"}]},
            started_at=1.0,
            ended_at=2.0,
            served=served,
        )
        assert t["local"] == {
            "request_id": "7c1e",
            "endpoint": "gpu-box",
            "digest": "sha256:ab",
            "num_ctx": 32768,
            "upstream_ms": 5400,
            "load_ms": 1200,
            "relay_attempts": 2,
        }
        assert "attempts" not in t

    def test_builders_never_raise_into_the_call_path(self):
        assert call_telemetry.safe(lambda: 1 / 0) == {}
        t = call_telemetry.from_chat_response(TEMPLATE_RESPONSE, started_at=1.0, ended_at=2.0, served=mock.Mock())
        assert "local" not in t

    def test_failed_call(self):
        t = call_telemetry.from_failed_call(
            started_at=10.0,
            ended_at=16.0,
            status=503,
            message="Server error 503: " + "x" * 2000,
            attempts=3,
            failed_attempts=[{"status": 503, "kind": "server_error"}] * 3,
        )
        assert t["status"] == 503
        assert t["duration_s"] == 6.0
        assert len(t["error"]["message"]) == 500
        assert t["attempts"] == 3

    def test_from_artifact_payload(self):
        agent = call_telemetry.from_artifact_payload(
            "agent_call_0003",
            {"status": 200, "duration_s": 2.5, "id": "gen-1", "provider": "Novita", "usage": {"prompt_tokens": 3}},
        )
        assert agent["source"] == "gateway"
        assert agent["duration_s"] == 2.5
        assert agent["provider"] == "Novita"
        assert "started_at" not in agent
        template = call_telemetry.from_artifact_payload("frontend", TEMPLATE_RESPONSE)
        assert template["native_finish_reason"] == "stop"
        assert "duration_s" not in template
        failed = call_telemetry.from_artifact_payload("backend", {"error": "API call failed"})
        assert failed["error"] == {"message": "API call failed"}
        assert "status" not in failed


class TestMergeAndLink:
    def test_merge_additive_never_overwrites(self):
        existing = {"duration_s": 1.2, "tokens": {"prompt": 10, "cached": None}, "provider": None}
        new = {"duration_s": 9.9, "tokens": {"prompt": 99, "cached": 4, "reasoning": 1}, "provider": "X", "cost": None}
        merged = call_telemetry.merge_additive(existing, new)
        assert merged == {
            "duration_s": 1.2,
            "tokens": {"prompt": 10, "cached": 4, "reasoning": 1},
            "provider": "X",
        }
        assert existing["tokens"]["cached"] is None  # input untouched

    def test_link_llm_requests_by_tokens_and_time(self):
        now = timezone.now()
        base = now.timestamp()
        rows = [
            SimpleNamespace(pk="b", created_at=now + timedelta(seconds=5), prompt_tokens=200, completion_tokens=20),
            SimpleNamespace(pk="a", created_at=now, prompt_tokens=100, completion_tokens=10),
            SimpleNamespace(pk="far", created_at=now + timedelta(hours=1), prompt_tokens=300, completion_tokens=30),
        ]

        def call(seq, ts, prompt, completion, status=200):
            usage = {"prompt_tokens": prompt, "completion_tokens": completion}
            return {"seq": seq, "ts": ts, "duration_s": 1.0, "status": status, "response": {"usage": usage}}

        calls = [
            call(1, base, 100, 10),
            call(2, base + 5, 200, 20),
            call(3, base + 9, 300, 30),  # the only token match is an hour away: no link
            call(4, base + 10, 100, 10, status=500),
        ]
        links = call_telemetry.link_llm_requests(calls, rows)
        assert {k: v.pk for k, v in links.items()} == {1: "a", 2: "b"}


class TestAttemptRecorder:
    @staticmethod
    def _resp(status, body=None):
        resp = mock.MagicMock()
        resp.status_code = status
        resp.text = "err"
        resp.json.return_value = body or {}
        return resp

    def _call(self, statuses):
        responses = [self._resp(s, TEMPLATE_RESPONSE if s == 200 else None) for s in statuses]
        client = OpenRouterClient(api_key="test-key")
        recorder = call_telemetry.OpenRouterAttemptRecorder()
        with (
            mock.patch("backend.generation.services.openrouter_client.requests.post", side_effect=responses),
            mock.patch("backend.generation.services.openrouter_client.time.sleep"),
            recorder,
        ):
            try:
                client.chat_completion(model="m", messages=[{"role": "user", "content": "hi"}])
            except OpenRouterError as exc:
                return recorder, exc
        return recorder, None

    def test_success_after_rate_limit(self):
        recorder, exc = self._call([429, 200])
        assert exc is None
        assert recorder.attempts(succeeded=True) == 2
        assert recorder.failed == [{"status": 429, "kind": "rate_limited"}]

    def test_all_server_errors(self):
        recorder, exc = self._call([500, 502, 503])
        assert exc is not None
        assert exc.status_code == 503
        assert recorder.attempts(succeeded=False, error=exc) == 3
        assert [f["status"] for f in recorder.failed] == [500, 502, 503]

    def test_client_error_after_retry(self):
        recorder, exc = self._call([429, 400])
        assert exc.status_code == 400
        assert recorder.attempts(succeeded=False, error=exc) == 2

    def test_other_threads_are_ignored(self):
        recorder = call_telemetry.OpenRouterAttemptRecorder()
        log = logging.getLogger("backend.generation.services.openrouter_client")
        with recorder:
            worker = threading.Thread(target=lambda: log.warning("Rate limited, waiting %ds", 2))
            worker.start()
            worker.join()
        assert recorder.failed == []
        assert recorder.attempts(succeeded=True) == 1


@pytest.mark.django_db
class TestAgentIngestion:
    def _endpoint(self, owner):
        from backend.inference.models import LLMEndpoint

        return LLMEndpoint.objects.create(
            slug="opencode-go",
            name="OpenCode Go",
            kind="openai_compatible",
            connection="direct",
            api_flavor="openai",
            max_concurrency=2,
            owner=owner,
        )

    def test_telemetry_for_calls_links_proxy_rows(self):
        from backend.inference.models import LLMRequest

        owner = UserFactory()
        run = GenerationRunFactory(created_by=owner)
        row = LLMRequest.objects.create(
            endpoint=self._endpoint(owner),
            job=run,
            job_attempt=run.attempt,
            source=LLMRequest.Source.AGENT_PROXY,
            path="/v1/chat/completions",
            status=LLMRequest.Status.DONE,
            response_status=200,
            prompt_tokens=8221,
            completion_tokens=96,
            upstream_ms=2955,
            deadline_at=timezone.now() + timedelta(hours=1),
        )
        LLMRequest.objects.filter(pk=row.pk).update(leased_at=row.created_at + timedelta(milliseconds=7))
        local_line = copy.deepcopy(OPENCODE_LINE)
        local_line["ts"] = row.created_at.timestamp() - 0.01
        openrouter_line = copy.deepcopy(GATEWAY_LINE)
        openrouter_line["seq"] = 2

        first, second = call_telemetry.telemetry_for_calls(run, [local_line, openrouter_line])

        assert first["list_cost"] == pytest.approx(0.01123188)
        assert first["local"]["request_id"] == str(row.pk)
        assert first["local"]["upstream_ms"] == 2955
        assert first["local"]["queue_wait_s"] == pytest.approx(0.007)
        assert first["local"]["endpoint"] == "opencode-go"
        assert second["provider"] == "CoreWeave"
        assert "local" not in second

    def test_telemetry_failure_never_breaks_ingestion(self):
        run = GenerationRunFactory()
        with mock.patch.object(call_telemetry, "proxy_requests", side_effect=RuntimeError("db down")):
            assert call_telemetry.telemetry_for_calls(run, [GATEWAY_LINE]) == [{}]


class TestSummarizeCalls:
    def test_duration_only_from_calls_known_not_to_be_retried(self):
        known = call_telemetry.from_gateway_call(GATEWAY_LINE)  # upstream_retries 0, 1.207 s
        retried = call_telemetry.from_gateway_call({**GATEWAY_LINE, "duration_s": 15.0, "upstream_retries": 2})
        older = {k: v for k, v in GATEWAY_LINE.items() if k != "upstream_retries"}
        unknown = call_telemetry.from_gateway_call({**older, "duration_s": 30.0})  # older gateway: retries unknown
        calls = [{"run_id": 1, "telemetry": t, "response_id": None} for t in (known, retried, unknown)]
        summary = call_telemetry.summarize_calls(calls)
        assert summary["duration_s"] == {"n": 1, "median": 1.207, "p90": 1.207}
        assert summary["e2e_output_tps"]["n"] == 1
        assert summary["retry_rate"] == {"value": 0.5, "n": 2}
        assert summary["n_runs"] == 1


class TestCallSeq:
    @pytest.mark.parametrize(
        ("stage", "seq"),
        [("agent_call_0042", 42), ("slot_call_0007", 7), ("agent_call_x", None), ("custom", None)],
    )
    def test_agent_and_slot_call_stages(self, stage, seq):
        assert call_telemetry.call_seq(stage) == seq
