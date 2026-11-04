"""OpenRouter generation stats, the post-run task and the telemetry endpoints (HTTP mocked)."""

from __future__ import annotations

import copy
import csv
import io
import json
from unittest import mock

import pytest
import requests

from backend.generation.models import GenerationArtifact
from backend.generation.services import openrouter_stats
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import RunManifestFactory
from backend.generation.tests.factories import StudyCellFactory
from backend.generation.tests.factories import StudyFactory
from backend.generation.tests.test_call_telemetry import GATEWAY_LINE
from backend.generation.tests.test_call_telemetry import OPENCODE_LINE
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

# A real /api/v1/generation record (ids shortened); privacy fields carry markers.
RECORD = {
    "created_at": "2026-10-26T23:28:05.751Z",
    "model": "deepseek/deepseek-v4.1-flash-20260910",
    "app_id": "APP-SECRET",
    "external_user": "EXT-SECRET",
    "streamed": True,
    "cancelled": False,
    "latency": 443,
    "moderation_latency": None,
    "generation_time": 667,
    "tokens_prompt": 21611,
    "tokens_completion": 52,
    "native_tokens_prompt": 33014,
    "native_tokens_completion": 82,
    "native_tokens_reasoning": 21,
    "native_tokens_cached": 32640,
    "origin": "https://example.com/",
    "is_byok": False,
    "finish_reason": "tool_calls",
    "native_finish_reason": "tool_calls",
    "service_tier": None,
    "usage": 0.000165528,
    "user_agent": "UA-SECRET",
    "http_referer": "REF-SECRET",
    "request_id": "req-1793057285-n64k",
    "session_id": "SESSION-SECRET",
    "api_type": "completions",
    "id": "gen-1793057285-LLKp",
    "upstream_id": "chatcmpl-4a07",
    "provider_responses": [
        {
            "endpoint_id": "e1",
            "id": "x",
            "latency": 900,
            "model_permaslug": "deepseek/v-1",
            "provider_name": "Novita",
            "status": 429,
        },
        {
            "endpoint_id": "e2",
            "id": "y",
            "latency": 310,
            "model_permaslug": "deepseek/v-1",
            "provider_name": "CoreWeave",
            "status": 200,
        },
    ],
    "total_cost": 0.000165528,
    "cache_discount": 0.00417792,
    "upstream_inference_cost": 0,
    "provider_name": "CoreWeave",
    "data_region": "global",
    "workspace_id": "WORKSPACE-SECRET",
}


def _resp(status, body=None):
    resp = mock.MagicMock()
    resp.status_code = status
    resp.json.return_value = body if body is not None else {}
    return resp


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class TestNormalize:
    def test_fields_and_derived_values(self):
        out = openrouter_stats.normalize({**RECORD, "generation_time": 743}, fetched_at="2026-11-02T00:00:00+00:00")
        assert out["status"] == "ok"
        assert out["provider_name"] == "CoreWeave"
        assert out["model_permaslug"] == "deepseek/deepseek-v4.1-flash-20260910"
        assert out["latency_ms"] == 443
        assert out["generation_time_ms"] == 743
        assert out["native_tokens_cached"] == 32640
        assert out["cache_discount"] == pytest.approx(0.00417792)
        # 82 tokens over (743 - 443) ms.
        assert out["decode_tps"] == pytest.approx(82 / 0.3, rel=1e-3)
        # Novita (429) -> CoreWeave (200): one fallback, one extra attempt.
        assert out["fallbacks"] == 1
        assert out["router_retries"] == 1
        assert out["provider_attempts"][0] == {
            "provider_name": "Novita",
            "status": 429,
            "latency_ms": 900,
            "model_permaslug": "deepseek/v-1",
        }

    def test_privacy_fields_are_dropped(self):
        out = openrouter_stats.normalize(RECORD)
        assert not set(out) & openrouter_stats.PRIVACY_FIELDS
        dumped = json.dumps(out)
        for marker in ("SECRET", "example.com", "endpoint_id"):
            assert marker not in dumped

    def test_decode_tps_unknown_without_positive_span(self):
        out = openrouter_stats.normalize({**RECORD, "generation_time": 400})
        assert out["decode_tps"] is None
        out = openrouter_stats.normalize({**RECORD, "latency": None})
        assert out["decode_tps"] is None
        out = openrouter_stats.normalize({k: v for k, v in RECORD.items() if k != "provider_responses"})
        assert out["fallbacks"] is None
        assert out["router_retries"] is None

    def test_burst_delivery_has_no_decode_speed(self):
        # Real Relace record: 624 tokens 2 ms after the first token (312,000 tok/s before the fix).
        out = openrouter_stats.normalize(
            {**RECORD, "latency": 5033, "generation_time": 5035, "native_tokens_completion": 624},
        )
        assert out["decode_tps"] is None
        # The 443 -> 667 ms span of RECORD is under the 250 ms floor too.
        assert openrouter_stats.normalize(RECORD)["decode_tps"] is None
        assert openrouter_stats.normalize({**RECORD, "generation_time": 693})["decode_tps"] == pytest.approx(328.0)

    def test_same_provider_retry_is_not_a_fallback(self):
        # Real record: OpenRouter retried a 429 on the same provider.
        attempts = [
            {"provider_name": "Alibaba", "status": 429, "latency": 3761},
            {"provider_name": "Alibaba", "status": 200, "latency": 1148},
        ]
        out = openrouter_stats.normalize({**RECORD, "provider_responses": attempts})
        assert out["fallbacks"] == 0
        assert out["router_retries"] == 1
        single = openrouter_stats.normalize({**RECORD, "provider_responses": attempts[1:]})
        assert (single["fallbacks"], single["router_retries"]) == (0, 0)


class TestFetchOne:
    def _fetch(self, responses):
        session = mock.MagicMock()
        session.get.side_effect = responses
        clock = _Clock()
        throttle = openrouter_stats.Throttle(rate=5, clock=clock, sleep=clock.sleep)
        out = openrouter_stats.fetch_one(session, "test-key", "gen-1", throttle=throttle, sleep=clock.sleep)
        return out, session, clock

    def test_ok(self):
        out, session, _ = self._fetch([_resp(200, {"data": RECORD})])
        assert out["status"] == "ok"
        args, kwargs = session.get.call_args
        assert args[0] == "https://openrouter.ai/api/v1/generation"
        assert kwargs["params"] == {"id": "gen-1"}
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"

    def test_not_found_is_unavailable(self):
        out, session, _ = self._fetch([_resp(404, {"error": {"code": 404}})])
        assert out["status"] == "unavailable"
        assert out["http_status"] == 404
        assert session.get.call_count == 1

    def test_rate_limit_backs_off_then_succeeds(self):
        out, session, clock = self._fetch([_resp(429), _resp(429), _resp(200, {"data": RECORD})])
        assert out["status"] == "ok"
        assert session.get.call_count == 3
        assert 2.0 in clock.sleeps
        assert 4.0 in clock.sleeps

    def test_server_errors_end_as_error(self):
        out, session, _ = self._fetch([_resp(502)] * 4)
        assert out == {"status": "error", "fetched_at": out["fetched_at"], "http_status": 502}
        assert session.get.call_count == 4

    def test_network_error(self):
        out, _, _ = self._fetch([requests.ConnectionError("boom")] * 4)
        assert out["status"] == "error"
        assert out["message"] == "ConnectionError"

    def test_auth_error_raises(self):
        with pytest.raises(openrouter_stats.StatsAuthError):
            self._fetch([_resp(401)])

    def test_non_object_body_is_an_error_not_a_crash(self):
        out, _, _ = self._fetch([_resp(200, ["unexpected"])])
        assert out["status"] == "error"
        assert out["message"] == "no data"

    def test_throttle_spaces_requests(self):
        clock = _Clock()
        throttle = openrouter_stats.Throttle(rate=5, clock=clock, sleep=clock.sleep)
        for _ in range(3):
            throttle.wait()
        assert clock.sleeps == [pytest.approx(0.2), pytest.approx(0.2)]


def _artifact(run, stage, gen_id, telemetry=None, **payload):
    return GenerationArtifact.objects.create(
        run=run,
        stage=stage,
        response_payload={"id": gen_id, "status": 200, **payload},
        telemetry=telemetry or {},
    )


@pytest.mark.django_db
class TestFetchAndStore:
    def test_fetch_for_artifacts_stores_and_skips(self):
        run = GenerationRunFactory()
        fresh = _artifact(run, "agent_call_0001", "gen-a", {"schema": 1, "duration_s": 2.0})
        done = _artifact(run, "agent_call_0002", "gen-b", {"openrouter": {"status": "ok", "latency_ms": 1}})
        gone = _artifact(run, "agent_call_0003", "gen-c", {"openrouter": {"status": "unavailable"}})
        local = _artifact(run, "agent_call_0004", "0217abc")
        session = mock.MagicMock()
        session.get.return_value = _resp(200, {"data": RECORD})
        rows = openrouter_stats.candidate_artifacts([run.pk]).values_list("id", "response_payload__id", "telemetry")
        counts = openrouter_stats.fetch_for_artifacts(list(rows), "k", session=session, sleep=lambda s: None)
        assert counts["ok"] == 1
        assert session.get.call_count == 1
        fresh.refresh_from_db()
        assert fresh.telemetry["duration_s"] == 2.0  # untouched
        assert fresh.telemetry["openrouter"]["provider_name"] == "CoreWeave"
        done.refresh_from_db()
        assert done.telemetry["openrouter"] == {"status": "ok", "latency_ms": 1}
        gone.refresh_from_db()
        assert gone.telemetry["openrouter"] == {"status": "unavailable"}
        local.refresh_from_db()
        assert local.telemetry == {}

    def test_deferred_not_found_is_not_stored(self):
        run = GenerationRunFactory()
        art = _artifact(run, "agent_call_0001", "gen-a")
        session = mock.MagicMock()
        session.get.return_value = _resp(404)
        counts = openrouter_stats.fetch_for_artifacts(
            [(art.pk, "gen-a", {})],
            "k",
            not_found="defer",
            session=session,
            sleep=lambda s: None,
        )
        assert counts["pending"] == 1
        art.refresh_from_db()
        assert art.telemetry == {}

    def test_error_block_is_retried_later(self):
        run = GenerationRunFactory()
        art = _artifact(run, "agent_call_0001", "gen-a", {"openrouter": {"status": "error", "http_status": 502}})
        session = mock.MagicMock()
        session.get.return_value = _resp(200, {"data": RECORD})
        openrouter_stats.fetch_for_artifacts(
            [(art.pk, "gen-a", art.telemetry)],
            "k",
            session=session,
            sleep=lambda s: None,
        )
        art.refresh_from_db()
        assert art.telemetry["openrouter"]["status"] == "ok"

    def test_rederive_stored_recomputes_derived_keys_only(self):
        run = GenerationRunFactory()
        # A block normalized before the fix: burst delivery timed as 312,000 tok/s and a
        # same-provider 429 retry counted as a fallback.
        stale = {
            "status": "ok",
            "latency_ms": 5033,
            "generation_time_ms": 5035,
            "native_tokens_completion": 624,
            "provider_name": "Alibaba",
            "provider_attempts": [
                {"provider_name": "Alibaba", "status": 429, "latency_ms": 3761},
                {"provider_name": "Alibaba", "status": 200, "latency_ms": 1148},
            ],
            "decode_tps": 312000.0,
            "fallbacks": 1,
        }
        art = _artifact(run, "agent_call_0001", "gen-a", {"duration_s": 7.0, "openrouter": dict(stale)})
        assert openrouter_stats.rederive_stored(art.pk, dry_run=True) == "rederived"
        art.refresh_from_db()
        assert art.telemetry["openrouter"]["decode_tps"] == 312000.0  # dry run writes nothing
        assert openrouter_stats.rederive_stored(art.pk) == "rederived"
        art.refresh_from_db()
        block = art.telemetry["openrouter"]
        assert (block["decode_tps"], block["fallbacks"], block["router_retries"]) == (None, 0, 1)
        # Measured fields and the rest of the telemetry are untouched.
        assert {k: block[k] for k in ("latency_ms", "generation_time_ms", "provider_attempts")} == {
            k: stale[k] for k in ("latency_ms", "generation_time_ms", "provider_attempts")
        }
        assert art.telemetry["duration_s"] == 7.0
        assert openrouter_stats.rederive_stored(art.pk) == "unchanged"
        gone = _artifact(run, "agent_call_0002", "gen-b", {"openrouter": {"status": "unavailable"}})
        assert openrouter_stats.rederive_stored(gone.pk) == "skipped"

    def test_fetch_for_run_without_key(self, settings):
        settings.OPENROUTER_API_KEY = ""
        run = GenerationRunFactory()
        _artifact(run, "agent_call_0001", "gen-a")
        assert openrouter_stats.fetch_for_run(run.pk) == {"skipped": "no_key"}

    def test_fetch_for_run_uses_owner_key_resolution(self, settings):
        settings.OPENROUTER_API_KEY = "global-test-key"
        settings.OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK = True
        run = GenerationRunFactory()
        art = _artifact(run, "agent_call_0001", "gen-a")
        with mock.patch.object(openrouter_stats.requests, "Session") as session_cls:
            session_cls.return_value.get.return_value = _resp(200, {"data": RECORD})
            with mock.patch.object(openrouter_stats.time, "sleep"):
                result = openrouter_stats.fetch_for_run(run.pk)
        assert result == {"fetched": 1, "ok": 1}
        headers = session_cls.return_value.get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer global-test-key"
        art.refresh_from_db()
        assert art.telemetry["openrouter"]["status"] == "ok"

    def test_schedule_is_off_under_tests_and_on_when_enabled(self, settings, django_capture_on_commit_callbacks):
        run = GenerationRunFactory()
        _artifact(run, "agent_call_0001", "gen-a")
        with mock.patch("backend.generation.services.openrouter_stats.fetch_call_stats_task.apply_async") as send:
            assert openrouter_stats.schedule_for_run(run.pk) is False
            settings.CALL_TELEMETRY_AUTO_FETCH = True
            with django_capture_on_commit_callbacks(execute=True):
                assert openrouter_stats.schedule_for_run(run.pk) is True
        send.assert_called_once_with(args=[str(run.pk)], countdown=90)

    def test_schedule_skips_runs_without_openrouter_calls(self, settings):
        settings.CALL_TELEMETRY_AUTO_FETCH = True
        run = GenerationRunFactory()
        _artifact(run, "agent_call_0001", "0217abc")
        assert openrouter_stats.schedule_for_run(run.pk) is False

    def test_task_retries_pending_then_marks_final(self, settings):
        fetch_call_stats_task = openrouter_stats.fetch_call_stats_task

        settings.CELERY_TASK_ALWAYS_EAGER = True
        calls = []

        def fake(run_id, *, final):
            calls.append(final)
            return {"pending": 1} if not final else {"unavailable": 1}

        with mock.patch.object(openrouter_stats, "fetch_for_run", side_effect=fake):
            fetch_call_stats_task.apply(args=["00000000-0000-0000-0000-000000000000"])
        assert calls == [False, False, True]

    def test_task_swallows_errors(self):
        fetch_call_stats_task = openrouter_stats.fetch_call_stats_task

        with mock.patch.object(openrouter_stats, "fetch_for_run", side_effect=RuntimeError("x")):
            result = fetch_call_stats_task.apply(args=["00000000-0000-0000-0000-000000000000"])
        assert result.result == {"error": True}


def _seed_calls(run, *, provider="CoreWeave", n=3, stats=True):
    for i in range(1, n + 1):
        line = copy.deepcopy(GATEWAY_LINE)
        line["seq"] = i
        line["response"]["provider"] = provider
        line["response"]["id"] = f"gen-{run.pk.hex[:6]}-{i}"
        line["duration_s"] = float(i)
        from backend.generation.services.call_telemetry import from_gateway_call

        telemetry = from_gateway_call(line)
        if stats:
            telemetry["openrouter"] = openrouter_stats.normalize(
                {**RECORD, "latency": 100 * i, "generation_time": 100 * i + 500},
            )
        GenerationArtifact.objects.create(
            run=run,
            stage=f"agent_call_{i:04d}",
            response_payload={
                "status": 200,
                "id": line["response"]["id"],
                "provider": provider,
                "content": "SECRET CONTENT",
                "usage": line["response"]["usage"],
            },
            telemetry=telemetry,
        )


@pytest.mark.django_db
class TestModelCallTelemetryEndpoint:
    def test_provider_table(self, client):
        user = UserFactory()
        client.force_login(user)
        model = LLMModelFactory(model_id="vendor/model-a")
        run1 = GenerationRunFactory(created_by=user, model=model)
        run2 = GenerationRunFactory(created_by=user, model=model)
        _seed_calls(run1, provider="CoreWeave", n=3)
        _seed_calls(run2, provider="Novita", n=2, stats=False)
        # A row without stored telemetry is derived from its payload.
        GenerationArtifact.objects.create(
            run=run2,
            stage="agent_call_0009",
            response_payload={"status": 429, "provider": "Novita", "error": {"message": "rate"}, "duration_s": 3},
        )
        other = GenerationRunFactory(model=model)  # someone else's run
        _seed_calls(other, provider="Parasail", n=2)

        resp = client.get(f"/api/generation/models/{model.pk}/call-telemetry/")
        assert resp.status_code == 200
        data = resp.json()
        overall = data["overall"]
        assert overall["n_calls"] == 6
        assert overall["n_runs"] == 2
        assert overall["kinds"] == {"app_agentic": 6}
        assert overall["error_rate"] == {"value": pytest.approx(1 / 6, rel=1e-3), "n": 6}
        providers = {p["provider"]: p for p in data["providers"]}
        assert set(providers) == {"CoreWeave", "Novita"}
        core = providers["CoreWeave"]
        assert core["ttft_ms"] == {"n": 3, "median": 200.0, "p90": 280.0}
        assert core["decode_tps"]["n"] == 3
        assert core["duration_s"]["median"] == 2.0
        assert core["coverage"]["share"] == 1.0
        assert core["cache_hit_ratio"]["value"] == pytest.approx(2432 / 2461, rel=1e-3)
        assert core["finish_reasons"] == {"tool_calls": 3}
        novita = providers["Novita"]
        assert novita["ttft_ms"]["median"] is None  # no OpenRouter stats: unknown, not 0
        assert novita["coverage"] == {
            "openrouter_ok": 0,
            "openrouter_unavailable": 0,
            "openrouter_eligible": 2,
            "share": 0.0,
            "share_of_eligible": 0.0,
        }
        assert data["notes"]

    def test_invisible_or_unknown_model_is_404(self, client):
        user = UserFactory()
        client.force_login(user)
        resp = client.get("/api/generation/models/999999/call-telemetry/")
        assert resp.status_code == 404

    def test_bad_kind_is_400(self, client):
        user = UserFactory()
        client.force_login(user)
        model = LLMModelFactory(model_id="vendor/model-b")
        resp = client.get(f"/api/generation/models/{model.pk}/call-telemetry/?kind=nope")
        assert resp.status_code == 400


def _csv(resp):
    return list(csv.DictReader(io.StringIO(b"".join(resp.streaming_content).decode())))


@pytest.mark.django_db
class TestCallsCsv:
    def test_study_export(self, client):
        user = UserFactory()
        client.force_login(user)
        study = StudyFactory(created_by=user)
        model = LLMModelFactory(model_id="vendor/model-c")
        cell = StudyCellFactory(
            condition__plan__study=study,
            condition__label="=HYPERLINK(evil)",
            condition__model=model,
        )
        run = GenerationRunFactory(
            created_by=user,
            model=model,
            study=study,
            condition=cell.condition,
            cell=cell,
            repeat_index=1,
        )
        RunManifestFactory(run=run)  # harness opencode, pricing 0.1 / 0.3 USD per Mtok
        _seed_calls(run, n=2)
        GenerationRunFactory(created_by=user, model=model)  # outside the study

        resp = client.get(f"/api/generation/calls.csv?study={study.pk}")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")
        rows = _csv(resp)
        assert len(rows) == 2
        first = rows[0]
        assert first["run_id"] == str(run.pk)
        assert first["study_id"] == str(study.pk)
        assert first["kind"] == "app_agentic"
        assert first["condition_label"] == "'=HYPERLINK(evil)"  # spreadsheet-safe
        assert first["repeat_index"] == "1"
        assert first["harness"] == "opencode"
        assert first["seq"] == "1"
        assert first["provider"] == "CoreWeave"
        assert first["ttft_ms"] == "100"
        assert first["duration_s"] == "1.0"
        assert first["list_cost"] == ""  # unknown stays empty, never 0
        assert (first["fallbacks"], first["router_retries"]) == ("1", "1")
        assert float(first["catalog_cost"]) == pytest.approx(2461 * 0.1e-6 + 159 * 0.3e-6)
        body = json.dumps(rows)
        assert "SECRET CONTENT" not in body

    def test_run_export_opencode_list_cost(self, client):
        user = UserFactory()
        client.force_login(user)
        run = GenerationRunFactory(created_by=user)
        from backend.generation.services.call_telemetry import from_gateway_call

        GenerationArtifact.objects.create(
            run=run,
            stage="agent_call_0001",
            response_payload={"status": 200, "id": OPENCODE_LINE["response"]["id"]},
            telemetry=from_gateway_call(OPENCODE_LINE),
        )
        rows = _csv(client.get(f"/api/generation/calls.csv?run={run.pk}"))
        assert rows[0]["cost"] == "0.0"
        assert rows[0]["list_cost"] == "0.01123188"
        assert rows[0]["billing_source"] == "go"
        assert rows[0]["ttft_ms"] == ""
        assert rows[0]["catalog_cost"] == ""  # no manifest, no frozen price: not 0

    def test_access_and_validation(self, client):
        user = UserFactory()
        client.force_login(user)
        foreign = StudyFactory()
        foreign_run = GenerationRunFactory()
        assert client.get("/api/generation/calls.csv").status_code == 400
        assert client.get(f"/api/generation/calls.csv?study={foreign.pk}").status_code == 404
        assert client.get(f"/api/generation/calls.csv?run={foreign_run.pk}").status_code == 404
        assert client.get("/api/generation/calls.csv?run=not-a-uuid").status_code == 404
        client.logout()
        assert client.get(f"/api/generation/calls.csv?run={foreign_run.pk}").status_code == 401
