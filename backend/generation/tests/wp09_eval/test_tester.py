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


class _Refusing(http.server.BaseHTTPRequestHandler):
    """``/api/strict`` refuses every body with 400 (also the valid one); ``/api/items`` validates ``title``."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:
        return

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/strict":
            self._send(400, {"error": "kind must be one of: alpha"})
        elif self.path == "/api/wrong":
            self._send(200 if "title" in body else 400, {"id": 1})
        elif "title" not in body:
            self._send(422, {"error": "title required"})
        else:
            self._send(201, {"id": 1})


def test_an_invalid_check_is_not_credited_when_the_valid_request_was_refused() -> None:
    """Rules version 3: the refusal of ``.invalid.*`` must be attributable to the fault."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Refusing)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        plan = {
            "compiler_version": 2,
            "base_url": f"http://127.0.0.1:{server.server_address[1]}",
            "auth": {"mode": "contract_bearer"},
            "checks": [
                # The valid body is refused (400): the refusal of the faulty body proves nothing.
                _check("strict.create.ok", "/api/strict", {"title": "a", "kind": "beta"}, 201),
                _check("strict.create.invalid.missing", "/api/strict", {"kind": "beta"}, [400, 422], "validation"),
                # The valid body is accepted: the refusal is attributable.
                _check("items.create.ok", "/api/items", {"title": "a"}, 201),
                _check("items.create.invalid.missing", "/api/items", {}, [400, 422], "validation"),
                # ``.ok`` fails only on its exact success status (200 for 201): it was not refused.
                _check("wrong.create.ok", "/api/wrong", {"title": "a"}, 201),
                _check("wrong.create.invalid.missing", "/api/wrong", {}, [400, 422], "validation"),
            ],
            "probes": [],
        }
        result = load_tester().run_plan(plan)
    finally:
        server.shutdown()
        server.server_close()
    assert statuses(result) == {
        "strict.create.ok": "fail",
        "strict.create.invalid.missing": "skipped",
        "items.create.ok": "pass",
        "items.create.invalid.missing": "pass",
        "wrong.create.ok": "fail",
        "wrong.create.invalid.missing": "pass",
    }
    voided = next(c for c in result["checks"] if c["id"] == "strict.create.invalid.missing")
    assert voided["skipped_due_to"] == ["strict.create.ok"]
    assert "not measurable" in voided["note"]
    # Counted like every skipped check: 3 of 6 pass.
    assert _rate(result) == pytest.approx(3 / 6)


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


# =====================================================================================
# Tester v2 (the instrument fix): generators, the integer rule, fresh steps, the mock upstream
# =====================================================================================

RANDS = [f"{i * 2654435761 % 2**32:08x}" for i in range(300)]
EPOCH = 1_900_000_000  # a fixed run time: the generators are pure functions of (name, rand, epoch)


def test_versions_are_two() -> None:
    module = load_tester()
    assert (module.VERSION, module.TESTER_RULES_VERSION, module.PROBE_RULES_VERSION) == (2, 3, 2)


def test_isbn_generators_have_valid_check_digits() -> None:
    module = load_tester()
    seen = set()
    for rand in RANDS:
        isbn13 = module.generated("isbn13", rand)
        assert len(isbn13) == 13
        assert isbn13.isdigit()
        assert isbn13.startswith(("978", "979"))
        assert sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn13)) % 10 == 0
        isbn10 = module.generated("isbn10", rand)
        assert len(isbn10) == 10
        assert isbn10.isdigit()
        assert sum(int(d) * (10 - i) for i, d in enumerate(isbn10)) % 11 == 0
        seen.add(isbn13)
    assert len(seen) > 290  # per context, not a plan-wide constant


def test_date_and_time_generators_are_ordered_and_well_formed() -> None:
    import datetime as dt

    module = load_tester()
    today = dt.datetime.fromtimestamp(EPOCH, tz=dt.UTC).date()
    for rand in RANDS:
        ctx = {"rand": rand, "_epoch": EPOCH}
        value = {name: module.resolve(name, ctx) for name in module._DATE_NAMES}
        future, future2 = (dt.date.fromisoformat(value[n]) for n in ("future_date", "future_date2"))
        past = dt.date.fromisoformat(value["past_date"])
        assert value["today"] == today.isoformat()
        assert today + dt.timedelta(days=30) <= future < future2 <= today + dt.timedelta(days=335)
        assert future.weekday() < 5
        assert future2.weekday() < 5
        assert today - dt.timedelta(days=331) <= past <= today - dt.timedelta(days=30)
        assert "09:00" <= value["time_hhmm"] < value["time_hhmm2"] <= "17:00"
        assert value["time_hhmm"][3:] in ("00", "30")
        start = dt.datetime.fromisoformat(value["future_datetime"].replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(value["future_datetime2"].replace("Z", "+00:00"))
        assert end - start == dt.timedelta(hours=1)
        assert start.date() == future
        assert dt.datetime.fromisoformat(value["past_datetime"].replace("Z", "+00:00")).date() == past
        assert module.schema_errors(value["future_datetime"], {"type": "string", "format": "date-time"}) == []
        assert module.schema_errors(value["future_date"], {"type": "string", "format": "date"}) == []


def test_generators_are_deterministic_per_context() -> None:
    module = load_tester()
    for name in ("alpha", "uuid", "digits4", "digits9", "isbn13", "future_date", "time_hhmm"):
        assert module.generated(name, "aaaa1111", EPOCH) == module.generated(name, "aaaa1111", EPOCH)
    assert module.generated("uuid", "aaaa1111") != module.generated("uuid", "bbbb2222")
    for rand in RANDS[:50]:
        digits = module.generated("digits4", rand)
        assert len(digits) == 4
        assert digits.isdigit()
        assert digits[0] != "0"
        assert len(module.generated("digits18", rand)) == 18
        alpha = module.generated("alpha", rand)
        assert len(alpha) == 8
        assert alpha.isalpha()
        assert alpha.islower()
        uuid = module.generated("uuid", rand)
        assert [len(part) for part in uuid.split("-")] == [8, 4, 4, 4, 12]
        assert uuid[14] == "4"
        assert uuid[19] in "89ab"
    assert module.generated("nonsense", "aaaa1111") is module._MISSING


def test_file_generators_are_real_files() -> None:
    import base64
    import struct
    import zlib

    module = load_tester()
    png = base64.b64decode(module.generated("png_b64", "x"), validate=True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, []
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        kind, data = png[pos + 4 : pos + 8], png[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", png[pos + 8 + length : pos + 12 + length])
        assert zlib.crc32(kind + data) == crc
        chunks.append((kind, data))
        pos += 12 + length
    assert [k for k, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    width, height, depth, colour = struct.unpack(">IIBB", chunks[0][1][:10])
    assert (width, height, depth, colour) == (64, 64, 8, 2)
    assert len(zlib.decompress(chunks[1][1])) == height * (1 + width * 3)
    jpeg = base64.b64decode(module.generated("jpeg_b64", "x"), validate=True)
    assert jpeg[:3] == b"\xff\xd8\xff"
    assert jpeg[-2:] == b"\xff\xd9"
    assert b"JFIF" in jpeg[:16]
    gif = base64.b64decode(module.generated("gif_b64", "x"), validate=True)
    assert gif[:6] == b"GIF89a"
    assert gif[-1:] == b";"
    pdf = base64.b64decode(module.generated("pdf_b64", "x"), validate=True)
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    xref = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
    assert pdf[xref : xref + 4] == b"xref"
    wav = base64.b64decode(module.generated("wav_b64", "x"), validate=True)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert struct.unpack("<I", wav[4:8])[0] == len(wav) - 8


def test_integer_rule_follows_json_schema() -> None:
    module = load_tester()
    integer = {"type": "integer"}
    assert module.schema_errors(31, integer) == []
    assert module.schema_errors(31.0, integer) == []  # rules v2: a zero fractional part is an integer
    assert module.schema_errors(31.5, integer) != []
    assert module.schema_errors(True, integer) != []  # noqa: FBT003
    assert module.schema_errors("31", integer) != []
    assert module.schema_errors(31.0, {"type": ["integer", "null"]}) == []
    assert module.schema_errors(31.5, {"type": "number"}) == []


def test_substitution_is_typed_and_leaves_unknown_names() -> None:
    module = load_tester()
    ctx = {"rand": "aaaa1111", "_epoch": EPOCH, "id": 7, "user_c_username": "usercaaaa1111"}
    assert module.substitute("{id}", ctx) == 7  # a captured integer stays an integer
    assert module.substitute("/x/{id}", ctx) == "/x/7"
    assert module.substitute({"a": ["{rand}", "{nope}"], "to": "{user_c_username}"}, ctx) == {
        "a": ["aaaa1111", "{nope}"],
        "to": "usercaaaa1111",
    }
    assert module.substitute("+1202555{digits4}", ctx) == "+1202555" + module.generated("digits4", "aaaa1111")
    # a capture wins over a generator of the same name
    assert module.substitute("{uuid}", {**ctx, "uuid": "captured"}) == "captured"
    # without a context rand only the constant files resolve
    assert module.substitute("{isbn13}", {}) == "{isbn13}"


def test_fresh_setup_steps_get_their_own_rand() -> None:
    module = load_tester()
    tester = module.Tester(kit_plan())
    try:
        ctx = tester.new_ctx()
        ctx["id"] = 5
        fresh = tester.fresh_ctx(ctx)
        assert fresh["rand"] != ctx["rand"]
        assert fresh["id"] == 5
        assert fresh["user_a_username"] == ctx["user_a_username"]
        assert module.substitute("{isbn13}", fresh) != module.substitute("{isbn13}", ctx)
        assert "user_c_username" in ctx
        assert len({ctx[f"user_{u}_username"] for u in "abc"}) == 3
    finally:
        tester.close()


def test_repeated_creates_of_a_unique_field_pass_on_the_reference_app() -> None:
    """Three ``fresh`` creates and a third account in one plan, against the bundled app."""
    plan = kit_plan()
    check = next(c for c in plan["checks"] if c["id"] == "todos.list.page_size")
    assert all(step["fresh"] for step in check["setup"])
    for i, step in enumerate(check["setup"]):
        step["request"]["json"]["title"] = "llmlab {isbn13} {rand}"
        step["capture"] = {f"title{i}": "$.title"}
    result = run_tester(plan=plan)
    assert statuses(result)["todos.list.page_size"] == "pass"


def test_upstream_pages_are_pure_and_served() -> None:
    import urllib.request

    module = load_tester()
    status, headers, body, delay = module.upstream_response("http://example.com", "/page/llmlab-abc?x=1")
    assert (status, delay) == (200, 0.0)
    assert headers["Content-Type"].startswith("text/html")
    assert b"<title>Example page llmlab-abc</title>" in body
    assert b'property="og:image" content="http://example.com/image.png"' in body
    assert module.upstream_response("http://example.com", "/status/503")[0] == 503
    assert module.upstream_response("http://example.com", "/redirect/2")[1]["Location"].endswith("/redirect/1")
    assert module.upstream_response("http://example.com", "/nope")[0] == 404
    assert len(module.upstream_response("http://example.com", "/big")[2]) == module.UPSTREAM_BIG_BYTES
    server = module.UpstreamServer(0, public_host="127.0.0.1", bind="127.0.0.1")
    try:
        assert server.self_check() is None
        assert server.hits == []
        long_path = "/page/" + "y" * 5000
        with urllib.request.urlopen(server.base + long_path, timeout=5) as resp:  # noqa: S310
            assert resp.status == 200
        assert server.saw("y" * 100)
    finally:
        server.close()


def test_a_plan_with_an_upstream_substitutes_upstream_base() -> None:
    plan = kit_plan()
    plan["upstream"] = {"hosts": ["127.0.0.1"], "port": 0, "bind": "127.0.0.1"}
    check = next(c for c in plan["checks"] if c["id"] == "todos.create.ok")
    check["request"]["json"]["title"] = "{upstream_base}/page/llmlab-{rand}"
    check["expect"]["json_contains"] = {"title": "{upstream_base}/page/llmlab-{rand}"}
    result = run_tester(plan=plan)
    assert result["measured"] is True
    assert statuses(result)["todos.create.ok"] == "pass"


def test_csrf_probe_is_decided_before_its_positive_control() -> None:
    """A bearer plan never sends the state-changing control of a probe that cannot apply."""
    result = run_tester("contract_bearer")
    csrf = [p for p in result["probes"] if p["kind"] == "csrf_missing"]
    assert csrf
    assert all(p["status"] == "skipped" and "not applicable" in (p["evidence"] or "") for p in csrf)


# --- round 2 of the instrument fix: probes that can be trusted ------------------------------------


class _Scripted(http.server.BaseHTTPRequestHandler):
    """Register / login (bearer tokens, an admin) plus whatever ``routes`` answers; ``restart()`` drops sessions."""

    protocol_version = "HTTP/1.1"
    admin_password = "scripted-admin-pw"  # noqa: S105 - a test value
    tokens: dict[str, str] = {}
    users: dict[str, str] = {}
    log: list[tuple[str, str, str | None, Any]] = []
    routes: Any = None

    def log_message(self, *_args: Any) -> None:
        return

    def _user(self) -> str | None:
        return self.tokens.get((self.headers.get("Authorization") or "").removeprefix("Bearer "))

    def _handle(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        cls = type(self)
        path = self.path.split("?")[0]
        cls.log.append((self.command, self.path, self._user(), body))
        if path == "/api/auth/register":
            cls.users[body["username"]] = body["password"]
            return self._send(201, {"username": body["username"]})
        if path == "/api/auth/login":
            answer = cls.routes(self.command, path, None, body) if cls.routes else None
            if answer is not None:
                return self._send(*answer)
            known = {**cls.users, "admin": cls.admin_password}
            if known.get(body.get("username")) != body.get("password"):
                return self._send(401, {"error": "bad credentials"})
            token = f"t-{body['username']}-{len(cls.tokens)}"
            cls.tokens[token] = body["username"]
            return self._send(200, {"token": token})
        answer = cls.routes(self.command, path, self._user(), body) if cls.routes else None
        return self._send(*(answer or (404, {"error": "not found"})))

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle  # noqa: N815

    def _send(self, status: int, body: Any, ctype: str | None = None, headers: dict[str, str] | None = None) -> None:
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype or ("text/html" if isinstance(body, str) else "application/json"))
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)


def _scripted(routes: Any, probe: dict[str, Any], checks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run ``probe`` (and ``checks``) against a ``_Scripted`` app as the docker runtime does: phase pre, a restart
    that ends every session, then phase post in a new tester that only has the saved state."""
    module = load_tester()
    _Scripted.routes = staticmethod(routes)
    _Scripted.tokens, _Scripted.users, _Scripted.log = {}, {}, []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Scripted)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    plan = {
        "compiler_version": 2,
        "base_url": f"http://127.0.0.1:{server.server_address[1]}",
        "auth": {"mode": "contract_bearer", "register_path": "/api/auth/register", "login_path": "/api/auth/login"},
        "checks": checks or [],
        "probes": [{"id": "probe.t", "cwe": "cwe-79", "endpoint": None, "requires_checks": [], **probe}],
    }
    try:
        pre = module.Tester(plan, admin_password=_Scripted.admin_password)
        pre.phase_pre()
        state = json.loads(json.dumps(pre.state()))
        _Scripted.tokens = {}  # the restart: no session survives
        post = module.Tester(plan, admin_password=_Scripted.admin_password)
        post.load_state(state)
        post.phase_post()
        return post.result()
    finally:
        server.shutdown()
        server.server_close()


def _stored_xss_app(view: Any) -> Any:
    stored: list[str] = []

    def routes(method: str, path: str, user: str | None, body: Any) -> Any:
        if (method, path) == ("POST", "/api/admin/broadcast"):
            if user != "admin":
                return (401 if user is None else 403), {"error": "admin only"}
            stored.append(body["message"])
            return 200, {"delivered": 1}  # the spec's exhaustive prose says 201: the probe must not care
        if (method, path) == ("GET", "/page"):
            return view(stored)
        return None

    return routes


_BROADCAST = {
    "kind": "xss_stored",
    "params": {
        "field": "message",
        "target": {"method": "POST", "path": "/api/admin/broadcast", "json": {"message": "hello"}, "actor": "admin"},
        "view": {"method": "GET", "path": "/page"},
    },
    "positive_control": {
        "method": "POST",
        "path": "/api/admin/broadcast",
        "json": {"message": "hello"},
        "actor": "admin",
        "expect_status": None,
    },
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def test_an_admin_probe_is_sent_as_the_admin_after_the_restart() -> None:
    """Round 2, request 1: the control and the store request of an admin endpoint carry the admin's credential."""
    result = _scripted(
        _stored_xss_app(lambda stored: (200, "<ul>" + "".join(map(_escape, stored)) + "</ul>")),
        _BROADCAST,
    )
    probe = result["probes"][0]
    assert (probe["status"], probe["skip_reason"]) == ("not_exploited", None), probe
    senders = [user for method, path, user, _ in _Scripted.log if path == "/api/admin/broadcast"]
    assert senders == ["admin", "admin"]  # the control and the store request
    raw = _scripted(_stored_xss_app(lambda stored: (200, "<ul>" + "".join(stored) + "</ul>")), _BROADCAST)
    assert raw["probes"][0]["status"] == "exploited"


@pytest.mark.parametrize(
    ("view", "wanted"),
    [
        (lambda stored: (200, {"items": stored}), "is application/json, not an HTML page"),
        (lambda stored: (404, {"error": "not published"}), "the view answered 404"),
        (lambda stored: (200, "<html><body>app shell</body></html>"), "does not show the stored value"),
    ],
)
def test_stored_xss_that_cannot_be_observed_is_not_a_clean_result(view: Any, wanted: str) -> None:
    """Round 2, request 4b: a JSON view, a view that was never served and a page without the value."""
    probe = _scripted(_stored_xss_app(view), _BROADCAST)["probes"][0]
    assert (probe["status"], probe["skip_reason"]) == ("skipped", "not_measurable"), probe
    assert wanted in probe["evidence"]


def test_a_sanitiser_that_drops_the_script_element_is_not_exploited() -> None:
    import re

    def view(stored: list[str]) -> Any:
        return 200, "<ul>" + "".join(re.sub(r"<script>.*?</script>", "", text) for text in stored) + "</ul>"

    probe = _scripted(_stored_xss_app(view), _BROADCAST)["probes"][0]
    assert (probe["status"], probe["skip_reason"]) == ("not_exploited", None), probe


def test_open_redirect_needs_an_endpoint_that_redirects() -> None:
    """Round 2, request 4c: an endpoint that only stores an address is not measured (and not reported clean)."""

    def routes(method: str, path: str, user: str | None, body: Any) -> Any:
        if (method, path) == ("POST", "/api/links"):
            return 201, {"id": 1, "short_code": "abc", "original_url": body["original_url"]}
        return None

    target = {"method": "POST", "path": "/api/links", "json": {"original_url": "https://example.com/a"}}
    probe = {
        "kind": "open_redirect",
        "params": {"field": "original_url", "target": {**target, "actor": "user_a"}},
        "positive_control": {**target, "actor": "user_a", "expect_status": None},
    }
    row = _scripted(routes, probe)["probes"][0]
    assert (row["status"], row["skip_reason"]) == ("skipped", "not_measurable"), row
    assert "does not answer with a redirect (control answered 201)" in row["evidence"]


def _login_app(limit_after: int | None) -> Any:
    failures: list[str] = []

    def routes(method: str, path: str, user: str | None, body: Any) -> Any:
        if path != "/api/auth/login" or body.get("username") != "admin":
            return None
        if body.get("password") == _Scripted.admin_password:
            return None  # the normal sign-in
        failures.append(body["password"])
        if limit_after is not None and len(failures) > limit_after:
            return 429, {"error": "locked"}
        return 401, {"error": "bad credentials"}

    return routes


_DEFAULT_ADMIN = {
    "kind": "default_credential",
    "params": {},
    "positive_control": {
        "method": "POST",
        "path": "/api/auth/login",
        "json": {"username": "{admin_username}", "password": "{admin_password}"},
        "actor": "anon",
        "expect_status": 200,
    },
}


def test_default_credential_is_not_clean_when_sign_in_was_limited() -> None:
    """Round 2, request 4e: a 429 says nothing about the password it answered."""
    clean = _scripted(_login_app(None), _DEFAULT_ADMIN)["probes"][0]
    assert (clean["status"], clean["evidence"]) == ("not_exploited", "none of 6 default passwords accepted")
    locked = _scripted(_login_app(5), _DEFAULT_ADMIN)["probes"][0]
    assert (locked["status"], locked["skip_reason"]) == ("skipped", "not_measurable"), locked
    assert locked["evidence"].startswith("1 of 6 candidate passwords were answered 429")


def test_default_credential_runs_before_the_rate_limit_probe() -> None:
    module = load_tester()
    order = module._LATE_KINDS
    assert order.index("default_credential") < order.index("rate_limit_missing")


def _reset_app(limit_after: int | None, status: int = 200) -> Any:
    seen: list[Any] = []

    def routes(method: str, path: str, user: str | None, body: Any) -> Any:
        if (method, path) != ("POST", "/api/auth/password-reset"):
            return None
        seen.append(body)
        if limit_after is not None and len(seen) > limit_after:
            return 429, {"error": "too many requests"}
        return status, {"ok": True}

    return routes


_RESET_LIMIT = {
    "kind": "rate_limit_missing",
    "params": {
        "target": {
            "method": "POST",
            "path": "/api/auth/password-reset",
            "json": {"username": "{user_a_username}"},
            "actor": "anon",
        },
    },
    "positive_control": {
        "method": "POST",
        "path": "/api/auth/password-reset",
        "json": {"username": "{user_a_username}"},
        "actor": "anon",
        "expect_status": None,
    },
}


def test_rate_limit_probe_on_a_reset_endpoint_measures() -> None:
    """Round 2, request 4d: the benign request of a reset endpoint is a success (200 or the spec's 202)."""
    for status in (200, 202):
        open_app = _scripted(_reset_app(None, status), _RESET_LIMIT)["probes"][0]
        assert open_app["status"] == "exploited", open_app
        assert "30 rapid attempts, none limited" in open_app["evidence"]
    limited = _scripted(_reset_app(5), _RESET_LIMIT)["probes"][0]
    assert (limited["status"], limited["evidence"]) == ("not_exploited", "limited after 5 attempts (429)")
    # the limiter already answers the benign request: that is the limiter, not a broken endpoint
    already = _scripted(_reset_app(0), _RESET_LIMIT)["probes"][0]
    assert (already["status"], already["skip_reason"]) == ("not_exploited", None), already
    assert "already limited at the control request (429)" in already["evidence"]


def _reset_outbox_app(mode: str) -> Any:
    """A reset endpoint that always answers 202 and an outbox. ``mode``: ``every`` request mails the account,
    ``first`` mails once per account (a silent limit), ``never`` mails nobody, ``closed`` has no readable outbox."""
    emails: dict[str, str] = {}
    outbox: list[dict[str, str]] = []

    def routes(method: str, path: str, user: str | None, body: Any) -> Any:
        if (method, path) == ("PATCH", "/api/me"):
            if user is None:
                return 401, {"error": "sign in"}
            emails[user] = body["email"]
            return 200, {"username": user, "email": body["email"]}
        if (method, path) == ("POST", "/api/auth/password-reset"):
            address = emails.get(body.get("username"))
            already = any(message["to"] == address for message in outbox)
            if address and (mode == "every" or (mode == "first" and not already)):
                outbox.append({"to": address, "subject": "reset"})
            return 202, {"ok": True}
        if (method, path) == ("GET", "/api/outbox"):
            if mode == "closed":
                return 500, {"error": "boom"}
            return 200, {"items": list(outbox)}
        return None

    return routes


_RESET_OBSERVED = {
    **_RESET_LIMIT,
    "params": {
        **_RESET_LIMIT["params"],
        "silent_limit": True,
        "setup": [
            {
                "actor": "user_a",
                "request": {"method": "PATCH", "path": "/api/me", "json": {"email": "llmlab{rand}@example.com"}},
                "expect_status": [200, 204],
            },
        ],
        "observe": {
            "method": "GET",
            "path": "/api/outbox",
            "query": {"to": "llmlab{rand}@example.com"},
            "actor": "anon",
        },
    },
}


def test_rate_limit_probe_on_a_same_response_endpoint_counts_what_the_requests_did() -> None:
    """Round 3, M8: "no 429" is not a finding where the spec demands the same answer for every request."""
    flooded = _scripted(_reset_outbox_app("every"), _RESET_OBSERVED)["probes"][0]
    assert flooded["status"] == "exploited", flooded
    assert "every one of them put a message in the outbox (30)" in flooded["evidence"]
    quiet = _scripted(_reset_outbox_app("first"), _RESET_OBSERVED)["probes"][0]
    assert (quiet["status"], quiet["skip_reason"]) == ("not_exploited", None), quiet
    assert "only 0 of 30 repeated requests put a message in the outbox" in quiet["evidence"]
    # nothing to count: the benign request left no message, or the outbox cannot be read
    for mode in ("never", "closed"):
        blind = _scripted(_reset_outbox_app(mode), _RESET_OBSERVED)["probes"][0]
        assert (blind["status"], blind["skip_reason"]) == ("skipped", "not_measurable"), (mode, blind)
    # a task without an outbox: the compiler sets only ``silent_limit``
    no_outbox = {**_RESET_OBSERVED, "params": {**_RESET_LIMIT["params"], "silent_limit": True}}
    unseen = _scripted(_reset_app(None, 202), no_outbox)["probes"][0]
    assert (unseen["status"], unseen["skip_reason"]) == ("skipped", "not_measurable"), unseen
    assert "cannot be told from none" in unseen["evidence"]
    # a "too many requests" answer is still the limiter, with or without an outbox
    limited = _scripted(_reset_app(5, 202), no_outbox)["probes"][0]
    assert (limited["status"], limited["evidence"]) == ("not_exploited", "limited after 5 attempts (429)")


def test_a_probe_precondition_is_the_endpoint_answering_not_its_exact_status_or_schema() -> None:
    """Round 2, requests 3 and 4a: 200 where the spec says 201, or an unstated enum in the response schema."""
    module = load_tester()
    tester = module.Tester({"base_url": "http://127.0.0.1:1", "checks": [], "probes": []})
    tester.results = {
        "passed": {"status": "pass"},
        "other_2xx": {"status": "fail", "expected": {"status": [201]}, "observed": {"status": 200}},
        "schema": {"status": "fail", "expected": {"status": [200]}, "observed": {"status": 200, "body": "{}"}},
        "refused": {"status": "fail", "expected": {"status": [201]}, "observed": {"status": 400}},
        "setup_failed": {"status": "fail", "expected": None, "observed": None},
        "negative": {"status": "fail", "expected": {"status": [400, 422]}, "observed": {"status": 201}},
        "skipped": {"status": "skipped"},
    }
    reached = {name for name in [*tester.results, "never_ran"] if tester._reached(name)}
    assert reached == {"passed", "other_2xx", "schema"}


def test_sqli_probe_without_rows_to_return_is_not_a_clean_result() -> None:
    def routes(method: str, path: str, user: str | None, body: Any) -> Any:
        return (200, {"entries": {}}) if (method, path) == ("GET", "/api/weather") else None

    target = {"method": "GET", "path": "/api/weather", "actor": "user_a"}
    probe = {
        "kind": "sqli_boolean",
        "params": {"param": "city", "target": target},
        "positive_control": {**target, "expect_status": None},
    }
    row = _scripted(routes, probe)["probes"][0]
    assert (row["status"], row["skip_reason"]) == ("skipped", "not_measurable"), row


def test_csrf_under_a_bearer_credential_is_not_applicable() -> None:
    result = run_tester("contract_bearer")
    csrf = [p for p in result["probes"] if p["kind"] == "csrf_missing"]
    assert csrf
    assert all(p["skip_reason"] == "not_applicable" for p in csrf)


def test_a_check_with_further_requests_passes_only_when_all_meet_the_expectation() -> None:
    """Round 2, request 5: ``.invalid.too_long`` sends the over-long value of every limited field."""
    limits = {"name": 10, "sku": 5}

    def app_with(enforced: set[str]) -> Any:
        def routes(method: str, path: str, user: str | None, body: Any) -> Any:
            if (method, path) != ("POST", "/api/items"):
                return None
            if any(len(body[field]) > limits[field] for field in enforced):
                return 400, {"error": "too long"}
            return 201, {"id": 1}

        return routes

    check = {
        "id": "items.create.invalid.too_long",
        "category": "validation",
        "actor": "user_a",
        "label": "name",
        "request": {"method": "POST", "path": "/api/items", "json": {"name": "y" * 11, "sku": "ok"}},
        "also": [
            {
                "label": "sku",
                "request": {"method": "POST", "path": "/api/items", "json": {"name": "ok", "sku": "y" * 6}},
            },
        ],
        "expect": {"status": [400, 422]},
        "depends_on": [],
    }
    none = {"kind": "plaintext_password_db", "params": {"disabled": True}, "cwe": "cwe-256"}
    both = _scripted(app_with({"name", "sku"}), none, [check])["checks"][0]
    assert both["status"] == "pass"
    assert both["observed"]["requests"] == [
        {"label": "name", "status": 400, "ok": True},
        {"label": "sku", "status": 400, "ok": True},
    ]
    one = _scripted(app_with({"name"}), none, [check])["checks"][0]
    assert (one["status"], one["note"]) == ("fail", "sku: status 201")
    assert one["observed"]["status"] == 201
    neither = _scripted(app_with(set()), none, [check])["checks"][0]
    assert (neither["status"], neither["note"]) == ("fail", "name: status 201 | sku: status 201")


def test_a_bare_list_of_type_names_is_a_type_schema() -> None:
    """Round 2, request 8: ``average_rating: [number, 'null']`` is checked, not ignored."""
    module = load_tester()
    schema = {"type": "object", "properties": {"customer_id": ["integer", "string", "null"]}}
    assert module.schema_errors({"customer_id": 4}, schema) == []
    assert module.schema_errors({"customer_id": None}, schema) == []
    assert module.schema_errors({"customer_id": [4]}, schema) == [
        "$.customer_id: expected integer/string/null, got list",
    ]
    assert module.schema_errors({"customer_id": 4.5}, {"properties": {"customer_id": ["integer", "null"]}}) != []


def test_oversize_file_generators_are_valid_files_over_nine_mib() -> None:
    import base64
    import io
    import struct
    import wave
    import zlib

    module = load_tester()
    png = base64.b64decode(module.generated("png_oversize_b64", "x"), validate=True)
    small = base64.b64decode(module.generated("png_b64", "x"))
    assert len(png) > 9 * 1024 * 1024
    pos, chunks = 8, []
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        kind, data = png[pos + 4 : pos + 8], png[pos + 8 : pos + 8 + length]
        assert zlib.crc32(kind + data) == struct.unpack(">I", png[pos + 8 + length : pos + 12 + length])[0]
        chunks.append((kind, len(data)))
        pos += 12 + length
    assert [kind for kind, _ in chunks] == [b"IHDR", b"juNk", b"IDAT", b"IEND"]
    assert png[:33] == small[:33]  # the same picture: only an ancillary chunk a decoder skips is added
    assert png[-(len(small) - 33) :] == small[33:]
    wav = base64.b64decode(module.generated("wav_oversize_b64", "x"), validate=True)
    with wave.open(io.BytesIO(wav)) as audio:
        assert (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) == (1, 8000, 1)
        assert audio.getnframes() == 9 * 1024 * 1024
    # resolved without a context too (a file spec outside a check), never substituted into a path
    assert module.resolve("png_oversize_b64", {}) == module.generated("png_oversize_b64", "y")
    assert module.substitute_path("/x/{png_oversize_b64}", {"rand": "ab"}) == "/x/{png_oversize_b64}"


def test_upstream_requests_are_counted_over_both_phases() -> None:
    """Round 2, request 8: the post-restart tester adds the requests the pre-restart one saw."""
    module = load_tester()
    plan = {"base_url": "http://127.0.0.1:1", "checks": [], "probes": [], "upstream": None}
    pre = module.Tester(plan)
    pre.upstream = type("Up", (), {"hits": [1, 2, 3], "close": lambda self: None})()
    pre.upstream_base = "http://example.com"
    state = json.loads(json.dumps(pre.state()))
    assert state["upstream_requests"] == 3
    post = module.Tester(plan)
    post.load_state(state)
    post.upstream = type("Up", (), {"hits": [1], "close": lambda self: None})()
    post.upstream_base = "http://example.com"
    assert post.result()["upstream"] == {"base": "http://example.com", "requests": 4}
