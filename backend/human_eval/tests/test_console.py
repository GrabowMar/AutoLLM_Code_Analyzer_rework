"""HTTP console (services/console.py): validation, SSRF safety, limits, timeouts and logging.

``requests.Session`` is replaced by a fake that records every call; no network I/O happens.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from django.core.cache import cache
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import HttpExchange
from backend.human_eval.services import console
from backend.human_eval.services import hosting
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.presets import PRESETS
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import HostingContainerFactory
from backend.human_eval.tests.factories import HttpExchangeFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import make_open_task
from backend.runtime.models import ContainerAction

pytestmark = pytest.mark.django_db

BASE = "http://apps-dind:9011"


class FakeResponse:
    def __init__(self, *, status=200, reason="OK", headers=None, chunks=(b'{"ok": true}',), iterate=None):
        self.status_code = status
        self.reason = reason
        self.headers = requests.structures.CaseInsensitiveDict(headers or {"Content-Type": "application/json"})
        self._chunks = chunks
        self._iterate = iterate
        self.closed = False
        self.iterated = False

    def iter_content(self, chunk_size=1):
        self.iterated = True
        if self._iterate is not None:
            yield from self._iterate()
            return
        yield from self._chunks

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, recorder):
        self.recorder = recorder
        self.trust_env = True
        self.closed = False
        self.adapters = {}

    def mount(self, prefix, adapter):
        self.adapters[prefix] = adapter

    def request(self, method, url, **kwargs):
        self.recorder.calls.append(SimpleNamespace(method=method, url=url, trust_env=self.trust_env, **kwargs))
        outcome = self.recorder.respond(method, url, kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        self.recorder.responses.append(outcome)
        return outcome

    def close(self):
        self.closed = True
        self.recorder.sessions_closed += 1


@pytest.fixture
def http(monkeypatch):
    recorder = SimpleNamespace(calls=[], responses=[], sessions_closed=0, respond=lambda m, u, k: FakeResponse())
    monkeypatch.setattr(console.requests, "Session", lambda: FakeSession(recorder))
    return recorder


@pytest.fixture(autouse=True)
def _environment(settings, monkeypatch):
    settings.APPS_SUBDOMAIN_PROXY = True
    settings.APPS_DOMAIN = "apps.test"
    settings.TRAEFIK_DYNAMIC_DIR = ""
    settings.DOCKER_APPS_NETWORK = ""
    settings.APPS_UPSTREAM_HOST = "apps-dind"
    cache.clear()
    monkeypatch.setattr(hosting, "lazy_reap", lambda: None)


def _security_design():
    preset = next(p for p in PRESETS if p["slug"] == "security-assessment")
    return preset["design"], preset["settings"]


@pytest.fixture
def setup():
    design, settings = _security_design()
    task = make_open_task(n_apps=1, design=design, settings=settings)
    app = task.apps.get()
    container = HostingContainerFactory(generation_job=app.job, status="running", app_port=9011)
    app.container = container
    app.started_by_task = True
    app.save()
    participant = ParticipantFactory(task=task)
    assignment = AssignmentFactory(participant=participant, task=task, item=task.items.get())
    return SimpleNamespace(task=task, app=app, container=container, participant=participant, assignment=assignment)


def _send(setup, *, slot="single", method="GET", path="/api/items", headers=None, body=""):
    assignment = Assignment.objects.select_related("task", "participant", "item", "item__app_a").get(
        pk=setup.assignment.pk,
    )
    return console.send(assignment, slot, method, path, headers or {}, body)


def _error(excinfo):
    return excinfo.value.status, excinfo.value.code, [error["path"] for error in excinfo.value.errors]


def test_sends_to_the_container_upstream_and_logs_the_exchange(setup, http):
    http.respond = lambda m, u, k: FakeResponse(
        status=201,
        reason="Created",
        headers={"Content-Type": "application/json", "Set-Cookie": "session=abc"},
        chunks=(b'{"id": ', b"7}"),
    )
    result = _send(
        setup,
        method="post",
        path="/api/items?debug=1",
        headers={"Content-Type": "application/json", "Authorization": "Bearer t", "Cookie": "a=b"},
        body='{"name": "x"}',
    )
    [call] = http.calls
    assert call.method == "POST"
    assert call.url == f"{BASE}/api/items?debug=1"
    assert call.trust_env is False
    assert call.allow_redirects is False
    assert call.timeout == (5, 20)
    assert call.stream is True
    assert call.data == b'{"name": "x"}'
    assert call.headers == {"Content-Type": "application/json", "Authorization": "Bearer t", "Cookie": "a=b"}
    assert http.responses[0].closed
    assert http.sessions_closed == 1
    exchange = HttpExchange.objects.get()
    assert result == {
        "id": str(exchange.id),
        "status_code": 201,
        "reason": "Created",
        "headers": {"Content-Type": "application/json", "Set-Cookie": "session=abc"},
        "body": '{"id": 7}',
        "truncated": False,
        "elapsed_ms": exchange.elapsed_ms,
        "error": "",
    }
    assert exchange.assignment_id == setup.assignment.id
    assert exchange.task_app_id == setup.app.id
    assert exchange.method == "POST"
    assert exchange.path == "/api/items?debug=1"
    assert exchange.request_body == '{"name": "x"}'
    assert exchange.status_code == 201
    assert exchange.response_headers["Set-Cookie"] == "session=abc"
    assert exchange.response_body == '{"id": 7}'
    setup.app.refresh_from_db()
    assert setup.app.last_activity_at > timezone.now() - timedelta(seconds=30)
    assert Assignment.objects.get(pk=setup.assignment.pk).last_activity_at is not None


@pytest.mark.parametrize(
    "path",
    ["//evil.com/steal", "/@evil.com", "//user@evil.com:80/x", "/\\evil.com", "/%2F%2Fevil"],
)
def test_paths_can_never_change_the_upstream_host(setup, http, path):
    _send(setup, path=path)
    [call] = http.calls
    assert call.url == f"{BASE}{path}"
    assert urlsplit(call.url).netloc == "apps-dind:9011"
    assert call.allow_redirects is False
    assert call.trust_env is False


def test_redirect_responses_are_returned_not_followed(setup, http):
    http.respond = lambda m, u, k: FakeResponse(status=302, reason="Found", headers={"Location": "http://10.0.0.1/"})
    result = _send(setup, path="/login")
    assert result["status_code"] == 302
    assert result["headers"]["Location"] == "http://10.0.0.1/"
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    ("kwargs", "path"),
    [
        ({"method": "TRACE"}, "method"),
        ({"method": "CONNECT"}, "method"),
        ({"path": "@evil.com"}, "path"),
        ({"path": "http://evil.com/"}, "path"),
        ({"path": ""}, "path"),
        ({"path": "/" + "a" * 2048}, "path"),
        ({"path": "/a b"}, "path"),
        ({"path": "/a\r\nHost: evil"}, "path"),
        ({"path": "/a\tb"}, "path"),
        ({"path": "/a\x00b"}, "path"),
        ({"path": "/page#frag"}, "path"),
        ({"headers": {f"X-H{i}": "1" for i in range(31)}}, "headers"),
        ({"headers": {"Bad Header": "1"}}, "headers.Bad Header"),
        ({"headers": {"X-Ok": "line\r\nInjected: 1"}}, "headers.X-Ok"),
        ({"headers": {"X-Ok": "v" * 4097}}, "headers.X-Ok"),
        ({"body": "x" * (256 * 1024 + 1)}, "body"),
        ({"slot": "A"}, "slot"),
    ],
)
def test_invalid_requests_are_400_and_never_sent(setup, http, kwargs, path):
    with pytest.raises(HumanEvalServiceError) as raised:
        _send(setup, **kwargs)
    status, code, paths = _error(raised)
    assert (status, code) == (400, "invalid")
    assert path in paths
    assert http.calls == []
    assert HttpExchange.objects.count() == 0


def test_multibyte_body_limit_counts_utf8_bytes(setup, http):
    with pytest.raises(HumanEvalServiceError):
        _send(setup, method="POST", body="é" * (128 * 1024 + 1))
    _send(setup, method="POST", body="é" * (128 * 1024))
    assert len(http.calls[0].data) == 256 * 1024


def test_host_hop_by_hop_and_length_headers_are_dropped(setup, http):
    _send(
        setup,
        headers={
            "Host": "evil.com",
            "Connection": "close",
            "Content-Length": "999",
            "Transfer-Encoding": "chunked",
            "Proxy-Authorization": "Basic x",
            "Upgrade": "websocket",
            "X-Keep": "yes",
        },
    )
    assert http.calls[0].headers == {"X-Keep": "yes"}
    assert HttpExchange.objects.get().request_headers == {"X-Keep": "yes"}


def test_rate_limit_is_30_requests_per_minute_per_participant(setup, http):
    for _ in range(console.RATE_LIMIT):
        _send(setup)
    with pytest.raises(HumanEvalServiceError) as raised:
        _send(setup)
    assert (raised.value.status, raised.value.code) == (429, "rate_limited")
    assert len(http.calls) == console.RATE_LIMIT
    # Another participant has their own budget.
    other = AssignmentFactory(
        participant=ParticipantFactory(task=setup.task),
        task=setup.task,
        item=setup.assignment.item,
    )
    other = Assignment.objects.select_related("task", "participant", "item", "item__app_a").get(pk=other.pk)
    assert console.send(other, "single", "GET", "/", {}, "")["status_code"] == 200


def test_at_most_two_concurrent_requests_per_participant(setup, http):
    key = str(setup.participant.id)
    held = [console._acquire_slot(key), console._acquire_slot(key)]
    assert all(held)
    with pytest.raises(HumanEvalServiceError) as raised:
        _send(setup)
    assert (raised.value.status, raised.value.code) == (429, "rate_limited")
    assert http.calls == []
    console._release_slot(held[0])
    _send(setup)
    assert len(http.calls) == 1
    # The finished request gave its slot back.
    assert console._acquire_slot(key) is not None


def test_wall_clock_limit_stops_a_slow_response(setup, http, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(console, "time", SimpleNamespace(monotonic=lambda: clock.now))

    def slow():
        for chunk in (b"a" * 10, b"b" * 10, b"c" * 10, b"d" * 10):
            clock.now += 10  # each chunk arrives 10 s after the previous one
            yield chunk

    http.respond = lambda m, u, k: FakeResponse(iterate=slow)
    result = _send(setup)
    assert result["error"] == "timeout"
    assert result["truncated"] is True
    assert result["status_code"] == 200
    assert result["body"] == "a" * 10 + "b" * 10
    assert http.responses[0].closed
    exchange = HttpExchange.objects.get()
    assert exchange.error == "timeout"
    assert exchange.response_truncated is True


def test_response_is_capped_at_512_kib_and_excerpts_at_16_kib(setup, http):
    chunk = b"x" * (200 * 1024)
    http.respond = lambda m, u, k: FakeResponse(chunks=(chunk, chunk, chunk))
    result = _send(setup, method="POST", body="y" * (20 * 1024))
    assert result["truncated"] is True
    assert len(result["body"]) == 512 * 1024
    exchange = HttpExchange.objects.get()
    assert len(exchange.response_body) == 16 * 1024
    assert len(exchange.request_body) == 16 * 1024
    assert exchange.response_truncated is True


def test_binary_bodies_are_decoded_with_replacement(setup, http):
    http.respond = lambda m, u, k: FakeResponse(chunks=(b"ok \xff\xfe",))
    assert _send(setup)["body"] == "ok ��"


def test_head_requests_do_not_read_a_body(setup, http):
    def explode():
        msg = "HEAD bodies must not be read"
        raise AssertionError(msg)

    http.respond = lambda m, u, k: FakeResponse(iterate=explode)
    result = _send(setup, method="HEAD")
    assert result["status_code"] == 200
    assert result["body"] == ""
    assert not http.responses[0].iterated


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (
            requests.ConnectionError("HTTPConnectionPool(host='apps-dind', port=9011): Max retries exceeded"),
            "connection failed",
        ),
        (requests.Timeout("HTTPConnectionPool(host='apps-dind', port=9011): Read timed out."), "timeout"),
        (requests.RequestException("weird"), "request failed"),
    ],
)
def test_transport_errors_are_logged_with_a_generic_message(setup, http, exception, error):
    http.respond = lambda m, u, k: exception
    result = _send(setup)
    assert result["status_code"] is None
    assert result["error"] == error
    assert "apps-dind" not in str(result)
    exchange = HttpExchange.objects.get()
    assert exchange.status_code is None
    assert exchange.error == error
    assert http.sessions_closed == 1


def test_broken_body_keeps_the_status_and_marks_truncation(setup, http):
    def broken():
        yield b"partial"
        msg = "Connection broken: IncompleteRead"
        raise requests.exceptions.ChunkedEncodingError(msg)

    http.respond = lambda m, u, k: FakeResponse(iterate=broken)
    result = _send(setup)
    assert result["status_code"] == 200
    assert result["body"] == "partial"
    assert result["truncated"] is True
    assert result["error"] == "invalid response"


def test_quota_of_logged_exchanges_per_assignment(setup, http):
    HttpExchange.objects.bulk_create(
        [
            HttpExchangeFactory.build(assignment=setup.assignment, task_app=setup.app)
            for _ in range(console.QUOTA_PER_ASSIGNMENT)
        ],
    )
    with pytest.raises(HumanEvalServiceError) as raised:
        _send(setup)
    assert (raised.value.status, raised.value.code) == (409, "quota_exceeded")
    assert http.calls == []


def test_console_must_be_enabled_by_the_design(http):
    task = make_open_task(n_apps=1)
    app = task.apps.get()
    app.container = HostingContainerFactory(generation_job=app.job, status="running")
    app.save()
    assignment = AssignmentFactory(participant=ParticipantFactory(task=task), task=task, item=task.items.get())
    with pytest.raises(HumanEvalServiceError) as raised:
        console.send(assignment, "single", "GET", "/", {}, "")
    assert (raised.value.status, raised.value.code) == (409, "console_disabled")
    assert http.calls == []


def test_app_must_be_running(setup, http):
    setup.container.status = "stopped"
    setup.container.save(update_fields=["status"])
    with pytest.raises(HumanEvalServiceError) as raised:
        _send(setup)
    assert (raised.value.status, raised.value.code) == (409, "app_not_running")
    assert http.calls == []


def test_app_without_an_upstream_is_409(setup, http):
    setup.container.app_port = None
    setup.container.save(update_fields=["app_port"])
    with pytest.raises(HumanEvalServiceError) as raised:
        _send(setup)
    assert (raised.value.status, raised.value.code) == (409, "app_not_reachable")
    assert http.calls == []


def test_non_app_containers_are_never_targeted(setup, http):
    setup.container.name = "analyzer-0123456789"
    setup.container.save(update_fields=["name"])
    with pytest.raises(HumanEvalServiceError) as raised:
        _send(setup)
    assert raised.value.code == "app_not_running"
    assert http.calls == []


def test_read_only_assignments_cannot_use_the_console(setup, http):
    Assignment.objects.filter(pk=setup.assignment.pk).update(status=Assignment.Status.SUBMITTED)
    with pytest.raises(HumanEvalServiceError) as raised:
        _send(setup)
    assert (raised.value.status, raised.value.code) == (409, "read_only")
    assert http.calls == []


# ---------------------------------------------------------------------------
# NUL characters (Postgres text/JSONB reject them)
# ---------------------------------------------------------------------------


def test_nul_bytes_in_bodies_and_headers_are_logged_and_returned_replaced(setup, http):
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"  # every PNG has NULs in its IHDR length field
    http.respond = lambda m, u, k: FakeResponse(
        headers={"Content-Type": "image/png", "X-Odd": "a\x00b"},
        chunks=(png,),
    )
    result = _send(setup, method="POST", body="probe\x00")  # a classic null-byte injection probe
    assert http.calls[0].data == b"probe\x00"  # the app gets the raw body
    assert result["status_code"] == 200
    assert result["body"] == "\ufffdPNG\r\n\x1a\n\ufffd\ufffd\ufffd\rIHDR"
    assert result["headers"]["X-Odd"] == "a\ufffdb"
    exchange = HttpExchange.objects.get()
    assert exchange.response_body == result["body"]
    assert exchange.request_body == "probe\ufffd"
    assert exchange.response_headers["X-Odd"] == "a\ufffdb"


# ---------------------------------------------------------------------------
# Wall clock against a real (local) upstream that trickles bytes
# ---------------------------------------------------------------------------


@pytest.fixture
def drip_server():
    """Start a local upstream that answers ``prefix`` then sends one byte every 50 ms for 4 s.

    Each byte arrives well within the 20 s read timeout, so only a wall-clock deadline that does not
    wait for chunks can end the call early.
    """
    started: list[tuple[socket.socket, threading.Event, threading.Thread]] = []

    def start(prefix: bytes) -> str:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        stop = threading.Event()

        def serve():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(5)
                    request = b""
                    while b"\r\n\r\n" not in request:
                        data = conn.recv(4096)
                        if not data:
                            return
                        request += data
                    conn.sendall(prefix)
                    deadline = time.monotonic() + 4
                    while not stop.is_set() and time.monotonic() < deadline:
                        conn.sendall(b"a")
                        time.sleep(0.05)
                except OSError:
                    return  # the client shut the connection down

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        started.append((listener, stop, thread))
        return f"http://127.0.0.1:{listener.getsockname()[1]}"

    yield start
    for listener, stop, thread in started:
        stop.set()
        listener.close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("prefix", "status"),
    [
        (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 100000\r\n\r\n", 200),  # sized body
        (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n", 200),  # body until EOF
        (b"HTTP/1.1 200 OK\r\nX-Slow: ", None),  # headers
    ],
    ids=["sized-body", "eof-body", "headers"],
)
def test_wall_clock_limit_holds_while_the_upstream_trickles(drip_server, monkeypatch, prefix, status):
    monkeypatch.setattr(console, "WALL_CLOCK_SECONDS", 0.5)
    base = drip_server(prefix)
    started = time.monotonic()
    result = console.perform(base, "GET", "/slow", {}, b"")
    assert time.monotonic() - started < 2.5  # the drip goes on for 4 s
    assert result["error"] == "timeout"
    if status is not None:
        assert result["status_code"] == status
        assert result["truncated"] is True


def test_a_fast_real_response_is_unaffected_by_the_watchdog(monkeypatch):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        with conn:
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\npong")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        result = console.perform(f"http://127.0.0.1:{listener.getsockname()[1]}", "GET", "/ping", {}, b"")
    finally:
        thread.join(timeout=5)
        listener.close()
    assert (result["status_code"], result["body"], result["error"], result["truncated"]) == (200, "pong", "", False)


# ---------------------------------------------------------------------------
# Only the container the participant is shown running
# ---------------------------------------------------------------------------


def test_console_is_refused_while_the_per_assignment_reset_is_due(setup, http):
    assert hosting._hosting(setup.task)["reset"] == "per_assignment"
    earlier = AssignmentFactory(
        participant=ParticipantFactory(task=setup.task),
        task=setup.task,
        item=setup.assignment.item,
        status=Assignment.Status.SUBMITTED,
    )
    hosting._record_use(setup.container, earlier)  # the previous rater's state is still in the container
    with pytest.raises(HumanEvalServiceError) as raised:
        _send(setup)
    assert (raised.value.status, raised.value.code) == (409, "app_not_running")
    assert http.calls == []
    assert HttpExchange.objects.count() == 0


@pytest.mark.parametrize("busy", ["reset_in_flight", "stopping"])
def test_console_is_refused_while_the_app_is_reset_or_stopped(setup, http, busy):
    if busy == "reset_in_flight":
        EvalTaskApp.objects.filter(pk=setup.app.pk).update(start_dispatched_at=timezone.now())
    else:
        ContainerAction.objects.create(
            action_id="act_stopping001",
            container=setup.container,
            action_type=ContainerAction.ActionType.STOP,
            status=ContainerAction.Status.RUNNING,
        )
    with pytest.raises(HumanEvalServiceError) as raised:
        _send(setup)
    assert raised.value.code == "app_not_running"
    assert http.calls == []


def test_console_use_makes_the_next_rater_get_a_fresh_copy(setup, http):
    _send(setup, method="POST", body='{"name": "planted"}')
    setup.container.refresh_from_db()
    assert setup.container.metadata["human_eval"]["users"] == [str(setup.assignment.pk)]
    following = AssignmentFactory(
        participant=ParticipantFactory(task=setup.task),
        task=setup.task,
        item=setup.assignment.item,
    )
    assert hosting.needs_reset(setup.app, assignment=following)
    assert not hosting.needs_reset(setup.app, assignment=setup.assignment)
