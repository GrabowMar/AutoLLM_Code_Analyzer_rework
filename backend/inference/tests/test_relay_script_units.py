"""Unit checks of relay/llmlab_relay.py internals that the e2e test cannot pin down (spec §E.4)."""

from __future__ import annotations

import argparse
import importlib.util
import socket
import sys
import threading
from pathlib import Path

import pytest
from django.conf import settings


@pytest.fixture(scope="module")
def relay_module():
    path = Path(settings.BASE_DIR) / "relay" / "llmlab_relay.py"
    spec = importlib.util.spec_from_file_location("llmlab_relay_units", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


class FakeUpstream:
    def __init__(self, error: BaseException | None = None):
        self.calls: list[tuple[str, str]] = []
        self.error = error

    def call(self, method, path, body=None, *, timeout=30.0, holder=None):
        self.calls.append((method, path))
        if self.error is not None:
            raise self.error
        return 200, {"message": {"content": "ok"}}, b'{"message": {"content": "ok"}}'


def _relay(module, upstream, token="llr_abcdefgh.secret"):  # noqa: S107 — a fake token
    """A Relay without __init__'s file and network side effects."""
    relay = object.__new__(module.Relay)
    relay.args = argparse.Namespace(max_num_ctx=None, kind="ollama", parallel=1, token_file=None)
    relay.upstream = upstream
    relay.lock = threading.Lock()
    relay.inflight = {}
    relay.max_result = module.DEFAULT_MAX_RESULT
    relay.stop = threading.Event()
    relay.posted = []
    relay.post_result = lambda job, result: relay.posted.append(result)
    relay.server = argparse.Namespace(token=token)
    return relay


def _request():
    return {
        "id": "r1",
        "claim_token": "c1",
        "upstream": {"method": "POST", "path": "/api/chat", "json": {"model": "m"}},
        "timeout_s": 5,
    }


def test_a_request_cancelled_before_it_starts_never_reaches_upstream(relay_module):
    upstream = FakeUpstream()
    relay = _relay(relay_module, upstream)
    job = relay_module.Inflight("r1", "c1", deadline=0)
    relay.inflight[("r1", "c1")] = job
    job.cancel()

    relay.execute(job, _request())

    assert upstream.calls == []
    assert relay.posted == []
    assert relay.inflight == {}


def test_socket_timeouts_are_reported_as_timeout(relay_module):
    # socket.timeout is not a TimeoutError before Python 3.10 (the relay supports 3.9).
    relay = _relay(relay_module, FakeUpstream(error=socket.timeout("timed out")))  # noqa: UP041
    job = relay_module.Inflight("r1", "c1", deadline=0)
    relay.inflight[("r1", "c1")] = job

    relay.execute(job, _request())

    assert relay.posted[0]["error"]["kind"] == "timeout"


def test_reread_token_reports_whether_it_changed(relay_module, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("llr_abcdefgh.secret\n")
    token_file.chmod(0o600)
    relay = _relay(relay_module, FakeUpstream())
    relay.args.token_file = str(token_file)

    # SIGHUP with an unchanged file must not force a reconnect (in-flight work survives).
    assert relay.reread_token() is False
    token_file.write_text("llr_ijklmnop.other\n")
    assert relay.reread_token() is True
    assert relay.server.token == "llr_ijklmnop.other"  # noqa: S105
