"""Unit checks of relay/llmlab_relay.py internals that the e2e test cannot pin down (spec §E.4)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import socket
import sys
import threading
import time
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


# ── Review fixes: redirects, transport errors, slots, show failures, exits ──


class _Handler:
    """A tiny threaded HTTP server for transport-level tests (localhost only)."""

    def __init__(self, respond):
        import http.server

        self.seen: list[dict] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _any(self):
                outer.seen.append({"path": self.path, "auth": self.headers.get("Authorization")})
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                respond(self)

            do_GET = _any  # noqa: N815
            do_POST = _any  # noqa: N815

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _server(module, url):
    args = argparse.Namespace(server=url, ca_file=None)
    return module.Server(args, "llr_abcdefgh.secret")


def test_server_never_follows_redirects(relay_module):
    target = _Handler(lambda h: (h.send_response(200), h.end_headers(), h.wfile.write(b"{}")))

    def redirect(h):
        h.send_response(302)
        h.send_header("Location", target.url + "/elsewhere")
        h.send_header("Content-Length", "0")
        h.end_headers()

    origin = _Handler(redirect)
    try:
        with pytest.raises(relay_module.ServerError) as exc:
            _server(relay_module, origin.url).post("hello", {})
        assert exc.value.status == 302
        assert target.seen == []  # the bearer token never left for the redirect target
    finally:
        origin.close()
        target.close()


def test_truncated_response_bodies_become_oserror(relay_module):
    def truncated(h):
        h.send_response(200)
        h.send_header("Content-Length", "100")
        h.end_headers()
        h.wfile.write(b'{"a": 1')
        h.wfile.flush()
        h.close_connection = True

    def bad_gzip(h):
        h.send_response(200)
        h.send_header("Content-Encoding", "gzip")
        h.send_header("Content-Length", "4")
        h.end_headers()
        h.wfile.write(b"\x1f\x8b\x08\x00")

    for respond in (truncated, bad_gzip):
        srv = _Handler(respond)
        try:
            with pytest.raises(OSError, match=r"."):
                _server(relay_module, srv.url).post("poll", {})
            upstream = relay_module.Upstream(srv.url, None)
            if respond is truncated:
                with pytest.raises(OSError, match="IncompleteRead"):
                    upstream.call("GET", "/api/ps")
        finally:
            srv.close()


def test_an_unexpected_error_while_posting_still_frees_the_slot(relay_module):
    import http.client

    relay = _relay(relay_module, FakeUpstream())
    job = relay_module.Inflight("r1", "c1", deadline=0)
    relay.inflight[("r1", "c1")] = job

    def boom(_job, _result):
        partial = b""
        raise http.client.IncompleteRead(partial)

    relay.post_result = boom
    relay.execute(job, _request())
    assert relay.inflight == {}


class FakeInventoryServer:
    def __init__(self):
        self.payloads: list[dict] = []

    def post(self, path, payload, **_kw):
        self.payloads.append(json.loads(json.dumps(payload)))
        missing = [m["name"] for m in payload["models"] if m["name"] not in payload["show"]]
        return {"accepted_models": len(payload["models"]), "need_show": missing}


class FakeOllama:
    def __init__(self, show):
        self.show = show

    def get_json(self, path, timeout=30.0):
        return {
            "/api/version": {"version": "0.12.3"},
            "/api/tags": {"models": [{"name": "a:1b", "digest": "aa"}, {"name": "b:1b", "digest": "bb"}]},
            "/api/ps": {"models": []},
        }[path]

    def call(self, method, path, body=None, *, timeout=30.0, holder=None):
        return self.show(body["model"])


def test_a_failed_show_is_not_reported_or_cached(relay_module):
    def show(name):
        if name == "a:1b":
            raise ConnectionRefusedError
        return 500, {"error": "boom"}, b'{"error": "boom"}'

    relay = _relay(relay_module, FakeOllama(show))
    relay.server = FakeInventoryServer()
    relay.show_cache = {}
    relay.push_inventory()
    assert relay.show_cache == {}
    assert all(p["show"] == {} for p in relay.server.payloads)
    assert len(relay.server.payloads) == 1  # no retry storm within one push

    good = {"capabilities": ["completion", "tools"], "model_info": {"x.context_length": 4096}}
    relay.upstream = FakeOllama(lambda name: (200, good, b"{}"))
    relay.push_inventory()
    assert relay.server.payloads[-1]["show"]["a:1b"]["capabilities"] == ["completion", "tools"]
    assert set(relay.show_cache) == {"sha256:aa", "sha256:bb"}


def test_backoff_is_short_while_requests_run(relay_module):
    relay = _relay(relay_module, FakeUpstream())
    assert relay.max_backoff() == relay_module.MAX_BACKOFF_S
    relay.inflight[("r1", "c1")] = relay_module.Inflight("r1", "c1", deadline=0)
    assert relay.max_backoff() <= 10
    # An idle disabled-endpoint poll stays well under the server's session TTL.
    assert relay_module.DISABLED_POLL_S * 2 < settings.RELAY_SESSION_TTL_S


def test_revoked_token_aborts_running_work_and_exits(relay_module):
    from concurrent.futures import ThreadPoolExecutor

    relay = _relay(relay_module, FakeUpstream())
    relay.args.on_unauthorized = "exit"
    relay.reread_token = lambda: False
    relay.pool = ThreadPoolExecutor(max_workers=1)
    job = relay_module.Inflight("r1", "c1", deadline=0)
    relay.inflight[("r1", "c1")] = job
    with pytest.raises(SystemExit) as exc:
        relay.handle_unauthorized()
    assert exc.value.code == relay_module.EXIT_UNAUTHORIZED
    assert relay.stop.is_set()
    assert job.cancelled


def test_post_result_gives_up_on_unauthorized_while_stopping(relay_module):
    relay = _relay(relay_module, FakeUpstream())
    del relay.post_result  # use the real method
    relay.reload_token = threading.Event()
    calls = []

    class Srv:
        def post(self, *a, **kw):
            calls.append(a)
            raise relay_module.Unauthorized

    relay.server = Srv()
    relay.stop.set()
    job = relay_module.Inflight("r1", "c1", deadline=time.time() + 3600)
    relay.post_result(job, {})
    assert len(calls) == 1
