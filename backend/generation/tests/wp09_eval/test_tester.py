"""WP-09: the eval-tester against bundled mini-apps (spec 8.1, 8.3, 8.4, 8.5).

The reference app (``conformance/probe_mutants/app.py``) runs as a local process; the tester runs in-process
from the file the image ships. No docker, no network beyond localhost.
"""

from __future__ import annotations

import http.server
import json
import threading
from typing import Any

import pytest

from backend.generation.evaluation import conformance
from backend.generation.evaluation import metrics
from backend.generation.evaluation.tester import tester_module as load_tester
from backend.generation.tests.wp09_eval.helpers import app
from backend.generation.tests.wp09_eval.helpers import kit_plan
from backend.generation.tests.wp09_eval.helpers import run_tester
from backend.generation.tests.wp09_eval.helpers import statuses


def _rate(result: dict[str, Any]) -> float | None:
    return metrics.compute(result["checks"], result["probes"]).agent_pass_rate


@pytest.mark.parametrize("mode", ["contract_bearer", "cookie_session"])
def test_reference_app_passes_every_check_and_no_probe_is_exploited(mode: str) -> None:
    result = run_tester(mode)
    assert result["measured"] is True
    failing = {cid: s for cid, s in statuses(result).items() if s != "pass"}
    assert failing == {}
    assert _rate(result) == 1.0
    assert not [p for p in result["probes"] if p["status"] == "exploited"]
    # a persistence check restarted the app between its setup and its read
    assert any(c["category"] == "persistence" and c["status"] == "pass" for c in result["checks"])


def test_two_runs_are_equal() -> None:
    first, second = run_tester(), run_tester()
    assert statuses(first) == statuses(second)
    assert [(p["id"], p["status"]) for p in first["probes"]] == [(p["id"], p["status"]) for p in second["probes"]]


def test_idor_mutant_fails_the_ownership_checks() -> None:
    result = run_tester(LLMLAB_MUTANT="idor")
    failed = {cid for cid, s in statuses(result).items() if s != "pass"}
    assert {"todos.get.other_user", "todos.update.other_user", "todos.delete.other_user"} <= failed
    assert _rate(result) < 1.0


def test_wrong_status_mutant_fails_the_create_check() -> None:
    result = run_tester(LLMLAB_MUTANT="wrong_status")
    assert statuses(result)["todos.create.ok"] == "fail"
    assert statuses(result)["auth.register.ok"] == "fail"


def test_missing_validation_mutant_fails_the_validation_checks() -> None:
    result = run_tester(LLMLAB_MUTANT="no_validation")
    validation = {
        c["id"]: c["status"]
        for c in result["checks"]
        if c["category"] == "validation" and c["id"].startswith("todos.")
    }
    assert validation
    assert all(status == "fail" for status in validation.values())


def test_crash_on_start_never_becomes_healthy() -> None:
    with app(LLMLAB_MUTANT="crash") as (runtime, handle, _env):
        assert runtime.wait_healthy(handle, "/api/health", 5) is False


def test_tester_exception_is_not_measured_never_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_tester()

    def boom(self: Any, check: Any, *args: Any, **kwargs: Any) -> None:
        msg = "tester bug"
        raise KeyError(msg)

    monkeypatch.setattr(module.Tester, "run_check", boom)
    with app() as (runtime, handle, variables):
        assert runtime.wait_healthy(handle, "/api/health", 15)
        result = runtime.run_tester(handle, kit_plan(), variables, 60)
    assert result["measured"] is False
    assert "tester exception" in result["not_measured_reason"]
    assert result["checks"] == []


def test_bare_starter_skips_probes_on_precondition_never_not_exploited() -> None:
    result = run_tester(LLMLAB_MUTANT="bare")
    assert result["measured"] is True
    assert _rate(result) < 0.5
    endpoint_probes = [p for p in result["probes"] if p["endpoint"]]
    assert endpoint_probes
    assert all(p["status"] == "skipped" and p["skip_reason"] == "precondition_failed" for p in endpoint_probes)
    assert not [p for p in result["probes"] if p["status"] == "not_exploited"]


def test_positive_control_failure_skips_the_probe() -> None:
    plan = kit_plan()
    probe = next(p for p in plan["probes"] if p["kind"] == "sqli_boolean")
    probe["positive_control"] = {
        "method": "GET",
        "path": "/api/does-not-exist",
        "actor": "user_a",
        "expect_status": 200,
    }
    plan["probes"] = [probe]
    result = run_tester(plan=plan)
    assert result["probes"][0]["status"] == "skipped"
    assert result["probes"][0]["skip_reason"] == "positive_control_failed"


def test_only_runs_the_selection_plus_infra_and_dependencies() -> None:
    plan = kit_plan(probes="none")
    plan["only"] = ["todos.get.ok"]
    result = run_tester(plan=plan)
    assert set(statuses(result)) == {"health", "index_served", "spa_fallback", "todos.get.ok"}


def test_every_probe_kind_has_a_validated_mutant_pair() -> None:
    verdicts = conformance.validate_probe_kinds(write=False)
    assert {k for k, row in verdicts.items() if not row["validated"]} == set()


# --- tolerances (TESTER_RULES_VERSION 1) --------------------------------------------------------


class _Tolerant(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:
        return

    def _send(self, status: int, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._send(200, {"status": "ok"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if self.path == "/api/items":  # Django APPEND_SLASH style
            self._send(308, None, {"Location": "/api/items/"})
            return
        if self.path == "/api/upload":
            self._send(413, {"detail": "too large"})
            return
        body = json.loads(raw or b"{}")
        if "title" not in body:
            self._send(422, {"detail": "title required"})  # FastAPI style
            return
        self._send(201, {"id": 1, "title": body["title"]})


def _check(cid: str, path: str, body: Any, status: Any, category: str = "functional") -> dict[str, Any]:
    return {
        "id": cid,
        "category": category,
        "endpoint": None,
        "actor": "anon",
        "setup": [],
        "request": {"method": "POST", "path": path, "json": body},
        "expect": {"status": status},
        "depends_on": [],
        "restart_before": False,
    }


def test_tolerances_trailing_slash_422_and_413() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Tolerant)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        plan = {
            "compiler_version": 1,
            "base_url": f"http://127.0.0.1:{server.server_address[1]}",
            "auth": {"mode": "contract_bearer"},
            "checks": [
                _check("slash", "/api/items", {"title": "a"}, 201),
                _check("validation", "/api/items/", {}, 400, "validation"),
                _check("oversize", "/api/upload", {"blob": "{oversize}"}, 413, "negative"),
            ],
            "probes": [],
        }
        result = load_tester().run_plan(plan)
    finally:
        server.shutdown()
        server.server_close()
    assert statuses(result) == {"slash": "pass", "validation": "pass", "oversize": "pass"}


# --- metrics rules -----------------------------------------------------------------------------


def test_metrics_never_report_zero_for_nothing_measured() -> None:
    empty = metrics.compute([{"id": "health", "category": "infra", "status": "pass"}], [])
    assert empty.agent_pass_rate is None
    assert empty.sec_pass is None


def test_metrics_count_error_and_skipped_as_fail_and_drop_flaky() -> None:
    checks = [
        {"id": "health", "category": "infra", "status": "pass"},
        {"id": "a", "category": "functional", "status": "pass", "stated_in": ["standard"]},
        {"id": "b", "category": "functional", "status": "error", "stated_in": []},
        {"id": "c", "category": "auth", "status": "skipped", "stated_in": ["standard"]},
        {"id": "d", "category": "auth", "status": "fail"},
    ]
    probes = [
        {"id": "p1", "status": "not_exploited"},
        {"id": "p2", "status": "skipped", "skip_reason": "precondition_failed"},
    ]
    out = metrics.compute(checks, probes, flaky=["d"], prose_variant="standard", baseline_pass_rate=0.25)
    assert out.agent_checks == 3
    assert out.agent_pass_rate == pytest.approx(1 / 3)
    assert out.over_baseline == pytest.approx(1 / 3 - 0.25)
    assert out.stated_pass_rate == 0.5
    assert out.unstated_pass_rate == 0.0
    assert out.probes_tested == 1
    assert out.probes_skipped == 1
    assert out.sec_pass is False


class _Webhook(http.server.BaseHTTPRequestHandler):
    secret = b"hook-secret"

    def log_message(self, *_args: Any) -> None:
        return

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        import hashlib
        import hmac

        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        stamp = self.headers.get("X-Timestamp", "")
        want = "sha256=" + hmac.new(self.secret, stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        ok = hmac.compare_digest(want, self.headers.get("X-Signature", ""))
        body = b'{"result": "applied"}' if ok else b'{"detail": "bad signature"}'
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_webhook_routes_are_signed_except_in_auth_checks() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Webhook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        plan = {
            "compiler_version": 1,
            "base_url": f"http://127.0.0.1:{server.server_address[1]}",
            "auth": {"mode": "contract_bearer"},
            "signed_routes": [{"method": "POST", "path": "/api/webhooks/payments"}],
            "checks": [
                _check("webhooks.payments.ok", "/api/webhooks/payments", {"id": "evt_1"}, 200),
                _check("webhooks.unsigned", "/api/webhooks/payments", {"id": "evt_2"}, 401, "auth"),
            ],
            "probes": [],
        }
        result = load_tester().run_plan(plan, webhook_secret="hook-secret")  # noqa: S106 - a test value
    finally:
        server.shutdown()
        server.server_close()
    assert statuses(result) == {"webhooks.payments.ok": "pass", "webhooks.unsigned": "pass"}


# --- SPA catch-all (wave-3 conformance finding) -------------------------------------------------


class _CatchAll(http.server.BaseHTTPRequestHandler):
    """Answers every method and path with the SPA's index.html (200), like the Django minimal starter."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:
        return

    def _html(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        data = b"<!doctype html><title>app</title><div id=app></div>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = _html  # noqa: N815 - http.server handler names


@pytest.mark.parametrize("mode", ["contract_bearer", "cookie_session"])
def test_spa_catch_all_never_passes_the_login_check(mode: str) -> None:
    from backend.generation.evaluation import acceptance
    from backend.generation.tests.wp09_eval.test_compiler import STACK
    from backend.generation.tests.wp09_eval.test_compiler import task_content

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CatchAll)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        plan = acceptance.plan_json(acceptance.compile_content(task_content("crud_todo_list"), mode, STACK))
        plan["base_url"] = f"http://127.0.0.1:{server.server_address[1]}"
        plan["probes"] = []
        result = load_tester().run_plan(plan)
    finally:
        server.shutdown()
        server.server_close()
    got = statuses(result)
    assert got["auth.login.ok"] == "fail"
    assert "pass" not in {
        status for cid, status in got.items() if cid not in ("health", "index_served", "spa_fallback")
    }


# --- username placeholders register the named actor (wave-3 conformance finding) ----------------


class _Members(http.server.BaseHTTPRequestHandler):
    """Register/login plus ``POST /api/members`` that only accepts registered usernames."""

    protocol_version = "HTTP/1.1"
    users: set[str] = set()

    def log_message(self, *_args: Any) -> None:
        return

    def do_GET(self) -> None:
        self._send(200, {"status": "ok"})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/auth/register":
            self.users.add(body["username"])
            self._send(201, {"username": body["username"]})
        elif self.path == "/api/auth/login":
            self._send(200, {"token": "t-" + body["username"]})
        elif body.get("username") in self.users:
            self._send(201, {"username": body["username"]})
        else:
            self._send(404, {"error": "no such user"})

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_a_username_placeholder_registers_that_actor_first() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Members)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        check = {**_check("add_b", "/api/members", {"username": "{user_b_username}"}, 201), "actor": "user_a"}
        plan = {
            "compiler_version": 1,
            "base_url": f"http://127.0.0.1:{server.server_address[1]}",
            "auth": {
                "mode": "contract_bearer",
                "register_path": "/api/auth/register",
                "login_path": "/api/auth/login",
            },
            "checks": [check],
            "probes": [],
        }
        result = load_tester().run_plan(plan)
    finally:
        server.shutdown()
        server.server_close()
    assert statuses(result) == {"add_b": "pass"}


# --- an early 413 before the body is read (wave-3 conformance finding) --------------------------


class _EarlyReject(http.server.BaseHTTPRequestHandler):
    """Answers 413 from the headers alone and closes without reading the body (gunicorn/Flask behaviour)."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:
        return

    def do_POST(self) -> None:
        if int(self.headers.get("Content-Length") or 0) > 1024 * 1024:
            data = b'{"error": "too large"}'
            self.send_response(413)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
            self.close_connection = True
            return
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()


def test_an_early_413_is_read_not_a_broken_pipe() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _EarlyReject)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        plan = {
            "compiler_version": 1,
            "base_url": f"http://127.0.0.1:{server.server_address[1]}",
            "auth": {"mode": "contract_bearer"},
            "checks": [_check("oversize", "/api/upload", {"blob": "{oversize}"}, 413, "negative")],
            "probes": [],
        }
        result = load_tester().run_plan(plan)
    finally:
        server.shutdown()
        server.server_close()
    assert statuses(result) == {"oversize": "pass"}
