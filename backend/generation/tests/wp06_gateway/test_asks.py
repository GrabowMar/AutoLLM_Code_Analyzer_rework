"""Cross-WP asks: the local proxy books completed requests in the spend ledger (WP-11) and
``settings.GATEWAY_TRANSLATORS`` only lists protocols whose translator exists (WP-07/WP-10)."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from django.conf import settings

from backend.generation.budget import live
from backend.generation.tests.wp06_gateway.support import anthropic_messages
from backend.generation.tests.wp06_gateway.support import gemini_generate
from backend.generation.tests.wp06_gateway.support import openai_responses
from backend.inference import proxy_server

DONE, LEASED = "done", "leased"


def _row(*, status=DONE, response_status=200, job_id="run-1", pk="req-1"):
    return SimpleNamespace(
        pk=pk,
        job_id=job_id,
        job_attempt=0,
        status=status,
        response_status=response_status,
        Status=SimpleNamespace(DONE=DONE),
    )


@pytest.fixture
def booked(monkeypatch):
    calls = []
    monkeypatch.setattr(live, "record_proxy_usage", lambda row: calls.append(row.pk))
    return calls


def test_completed_request_is_booked(booked):
    proxy_server.record_ledger_usage(_row())
    assert booked == ["req-1"]


@pytest.mark.parametrize(
    "row",
    [
        _row(status=LEASED),
        _row(response_status=500),
        _row(response_status=429),
        _row(job_id=None),
        None,
    ],
)
def test_unfinished_failed_or_runless_requests_are_not_booked(booked, row):
    proxy_server.record_ledger_usage(row)
    assert booked == []


def test_ledger_failure_never_breaks_the_call(monkeypatch):
    def boom(_row):
        raise RuntimeError  # the ledger is down

    monkeypatch.setattr(live, "record_proxy_usage", boom)
    proxy_server.record_ledger_usage(_row())  # logged, not raised


def _handler(monkeypatch, row):
    handler = object.__new__(proxy_server.ProxyHandler)
    handler.wfile = io.BytesIO()
    sent = []
    monkeypatch.setattr(handler, "_send_json", lambda status, payload: sent.append(status), raising=False)
    monkeypatch.setattr(handler, "send_response", lambda *a: None, raising=False)
    monkeypatch.setattr(handler, "send_header", lambda *a: None, raising=False)
    monkeypatch.setattr(handler, "end_headers", lambda: None, raising=False)
    monkeypatch.setattr(handler, "_cancel_check", lambda auth: lambda: False)
    monkeypatch.setattr(handler, "_outcome", lambda auth, r: (200, {"choices": []}, None))
    monkeypatch.setattr("backend.inference.requests.wait_result", lambda rid, **kw: row)
    monkeypatch.setattr("backend.inference.ollama_native.stream_chunks", lambda payload: [])
    monkeypatch.setattr("backend.inference.timeouts.chain", lambda ep: SimpleNamespace(proxy_wait_s=1))
    return handler


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["_non_stream", "_stream"])
def test_proxy_books_after_each_answer(monkeypatch, booked, mode):
    handler = _handler(monkeypatch, _row(pk="req-9"))
    auth = SimpleNamespace(endpoint={"id": 0}, job_id="run-1", attempt=0)
    status, _payload = getattr(handler, mode)(auth, "req-9")
    assert status == 200
    assert booked == ["req-9"]


def test_gateway_translators_setting_is_defined_and_only_lists_working_translators():
    translators = settings.GATEWAY_TRANSLATORS
    assert isinstance(translators, list)
    modules = {m.NAME: m for m in (anthropic_messages, openai_responses, gemini_generate)}
    for name in translators:
        module = modules.get(name)
        assert module is not None, f"no translator module known for {name}"
        try:
            module.to_chat({})
        except module.TranslatorUnavailableError:  # pragma: no cover - the failure this guards
            pytest.fail(f"{name} is listed in GATEWAY_TRANSLATORS but its translator is unavailable")
        except Exception:  # noqa: BLE001,S110 -- a real translator may reject the empty body
            pass
