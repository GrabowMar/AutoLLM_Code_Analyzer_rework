"""The hidden conformance suite against toy apps, and the evaluator's bookkeeping."""

# Contract fixtures are kept one endpoint per line; passwords are throwaway test values.
# ruff: noqa: E501, S106, S107

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

from backend.generation.services.evaluation import contract as contract_mod

TESTER = Path(__file__).resolve().parents[1] / "images" / "conformance-tester" / "tester.py"

SPEC = {
    "backend_requirements": ["Access control: allow public read-only todos; require auth for create/update/delete"],
    "api_endpoints": [
        {"method": "GET", "path": "/api/todos", "request": None, "response": {"items": [{"id": 1, "title": "s"}], "total": 1}},
        {"method": "POST", "path": "/api/todos", "request": {"title": "string", "priority": "low|medium|high"}, "response": {"id": 1, "title": "s", "priority": "medium"}},
        {"method": "PUT", "path": "/api/todos/:id", "request": {"title": "string", "completed": True}, "response": {"id": 1, "completed": True}},
        {"method": "DELETE", "path": "/api/todos/:id", "request": None, "response": {}},
        {"method": "GET", "path": "/api/health", "request": None, "response": {"status": "healthy"}},
    ],
    "admin_api_endpoints": [
        {"method": "GET", "path": "/api/admin/todos", "request": None, "response": {"items": [], "total": 0}},
        {"method": "POST", "path": "/api/admin/todos/:id/toggle", "request": None, "response": {"id": 1, "is_active": False}},
    ],
}  # fmt: skip


@pytest.fixture(scope="module")
def tester():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("conformance_tester_under_test", TESTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.dont_write_bytecode = False
    return module


def _toy_app(*, broken: bool = False, admin_password: str = "s3cret"):
    """A tiny in-memory app implementing (or, if *broken*, ignoring) the contract."""
    state = {"users": {}, "tokens": {}, "todos": {}, "next": 1}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, payload, ctype="application/json"):
            data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def _user(self):
            auth = self.headers.get("Authorization", "")
            return state["tokens"].get(auth.removeprefix("Bearer "))

        def do_GET(self):
            if self.path == "/api/health":
                return self._send(200, {"status": "healthy"})
            if self.path == "/api/todos":
                items = list(state["todos"].values())
                return self._send(200, {"items": items, "total": len(items)})
            if self.path == "/api/admin/todos":
                if self._user() != "admin":
                    return self._send(403, {"error": "admin only"})
                return self._send(200, {"items": list(state["todos"].values()), "total": len(state["todos"])})
            if not self.path.startswith("/api/"):
                return self._send(200, "<!doctype html><div id=root></div>", "text/html")
            return self._send(404, {"error": "nf"})

        def do_POST(self):  # noqa: PLR0911 - one branch per toy route
            body = self._body()
            if self.path == "/api/auth/register":
                state["users"][body["username"]] = body["password"]
                return self._send(201, {"id": len(state["users"]), "username": body["username"]})
            if self.path == "/api/auth/login":
                users = {**state["users"], "admin": admin_password}
                if users.get(body.get("username")) != body.get("password"):
                    return self._send(401, {"error": "bad creds"})
                token = f"t-{body['username']}"
                state["tokens"][token] = body["username"]
                return self._send(200, {"token": token})
            if self.path == "/api/todos":
                if not broken and not self._user():
                    return self._send(401, {"error": "auth"})
                todo = {"id": state["next"], "title": body.get("title"), "priority": body.get("priority", "medium")}
                if broken:
                    todo.pop("priority")
                state["todos"][state["next"]] = {**todo, "completed": False, "is_active": True}
                state["next"] += 1
                return self._send(201, todo)
            if self.path.startswith("/api/admin/todos/") and self.path.endswith("/toggle"):
                todo = state["todos"].get(int(self.path.split("/")[4]))
                if not todo or self._user() != "admin":
                    return self._send(404 if not todo else 403, {})
                todo["is_active"] = not todo["is_active"]
                return self._send(200, {"id": todo["id"], "is_active": todo["is_active"]})
            return self._send(404, {})

        def do_PUT(self):
            todo = state["todos"].get(int(self.path.rsplit("/", 1)[1]))
            if not todo:
                return self._send(404, {})
            todo.update(self._body())
            return self._send(200, todo)

        def do_DELETE(self):
            state["todos"].pop(int(self.path.rsplit("/", 1)[1]), None)
            return self._send(200, {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _plan(server, *, has_frontend=True, admin_password="s3cret"):
    endpoints = [{**e, "admin": False} for e in SPEC["api_endpoints"]]
    endpoints += [{**e, "admin": True} for e in SPEC["admin_api_endpoints"]]
    return {
        "base_url": f"http://127.0.0.1:{server.server_address[1]}",
        "has_frontend": has_frontend,
        "contract": contract_mod.describe(SPEC),
        "admin_password": admin_password,
        "endpoints": endpoints,
        "startup_timeout_s": 5,
        "nonce": "abc123",
    }


def test_compliant_app_passes_every_check(tester):
    server = _toy_app()
    try:
        report = tester.run(_plan(server))
    finally:
        server.shutdown()
    failed = [c for c in report["checks"] if not c["passed"]]
    assert failed == []
    names = {c["name"] for c in report["checks"]}
    assert {"register account", "login returns token", "admin account signs in"} <= names
    assert "anonymous POST /api/todos is rejected" in names
    assert "POST /api/admin/todos/:id/toggle" in names
    assert report["summary"]["pass_rate"] == 1.0


def test_contract_violations_are_caught(tester):
    server = _toy_app(broken=True)
    try:
        report = tester.run(_plan(server, admin_password="wrong"))
    finally:
        server.shutdown()
    failed = {c["name"]: c["detail"] for c in report["checks"] if not c["passed"]}
    assert "anonymous POST /api/todos is rejected" in failed  # no auth enforcement
    assert "admin account signs in" in failed  # ignored ADMIN_PASSWORD
    assert "priority" in failed["POST /api/todos"]  # documented field missing
    assert "GET /api/admin/todos" in failed  # no admin token
    assert report["summary"]["pass_rate"] < 1.0


def test_unreachable_app_fails_everything(tester):
    plan = {
        "base_url": "http://127.0.0.1:9",
        "has_frontend": False,
        "contract": contract_mod.describe(SPEC),
        "admin_password": "x",
        "endpoints": [{**e, "admin": False} for e in SPEC["api_endpoints"]],
        "startup_timeout_s": 1,
    }
    report = tester.run(plan)
    assert report["summary"]["passed"] == 0
    assert report["summary"]["total"] == 2 + len(SPEC["api_endpoints"])


def test_shape_and_synthesis_helpers(tester):
    spec = {"items": [{"id": 1, "title": "s"}], "total": 1}
    assert tester.shape_problems(spec, {"items": [{"id": 3, "title": "x", "extra": 1}], "total": 1, "more": 2}) == []
    assert tester.shape_problems(spec, {"items": [{"id": 3}]}) == [
        "response lacks field 'total'",
        "response.items[0] lacks field 'title'",
    ]
    synth = tester.Synth("t")
    body = synth.value(
        {"title": "string", "priority": "low|medium|high", "email": "string", "due_at": "ISO8601", "n": 3},
    )
    assert body["priority"] == "low"
    assert body["email"].endswith("@example.com")
    assert body["n"] == 3
    assert "T" in body["due_at"]


@pytest.mark.django_db
class TestEvaluatorBookkeeping:
    def test_build_plan_uses_snapshot_contract_and_stack(self):
        from backend.generation.models import GenerationJob
        from backend.generation.services.evaluation.runner import build_plan
        from backend.generation.tests.factories import AppRequirementTemplateFactory
        from backend.users.tests.factories import UserFactory

        app = AppRequirementTemplateFactory(**SPEC)
        job = GenerationJob.objects.create(mode="scaffolding", created_by=UserFactory(), app_requirement=app)
        plan = build_plan(job, admin_password="pw", has_frontend=True)
        assert plan["contract"]["writes_require_auth"] is True
        assert [e["admin"] for e in plan["endpoints"]].count(True) == 2
        assert plan["nonce"] == str(job.id).replace("-", "")[:8]

    def test_evaluate_job_records_outcome_and_functional_metrics(self):
        from backend.generation.models import GenerationJob
        from backend.generation.services.evaluation import runner
        from backend.users.tests.factories import UserFactory

        job = GenerationJob.objects.create(
            mode="agent",
            created_by=UserFactory(),
            result_data={"agent": {"served_models": {}}},
            resolved_bundle={"contract": {"version": 1}, "gateway": {}, "llm": {}},
        )
        outcome = {
            **runner.tester_identity(),
            "status": "failed",
            "summary": {"total": 4, "passed": 3, "failed": 1, "pass_rate": 0.75},
            "checks": [
                {"category": "runtime", "name": "health responds 200", "passed": True},
                {"category": "api", "name": "GET /api/x", "passed": True},
                {"category": "api", "name": "POST /api/x", "passed": False},
                {"category": "admin", "name": "GET /api/admin/x", "passed": True},
            ],
        }
        with mock.patch.object(runner.Evaluation, "run", return_value=outcome):
            runner.evaluate_job(job)
        job.refresh_from_db()
        functional = job.metrics["functional"]
        assert functional["pass_rate"] == 0.75
        assert functional["passed"] is False
        assert (functional["endpoints_ok"], functional["endpoints_checked"]) == (2, 3)
        assert functional["contract_stated_in_prompt"] is True
        assert job.result_data["agent"]["validity"]["valid"] is True

    def test_infra_error_has_no_pass_rate(self):
        from backend.generation.models import GenerationJob
        from backend.generation.services.evaluation import runner
        from backend.users.tests.factories import UserFactory

        job = GenerationJob.objects.create(mode="agent", created_by=UserFactory(), result_data={"agent": {}})
        with mock.patch.object(
            runner.Evaluation,
            "run",
            return_value={"status": "infra_error", "error": "daemon down"},
        ):
            runner.evaluate_job(job)
        job.refresh_from_db()
        assert job.metrics["functional"]["pass_rate"] is None
        assert job.result_data["agent"]["validity"]["valid"] is False

    def test_infra_patterns(self):
        from backend.generation.services.evaluation.runner import _INFRA_PATTERNS

        assert _INFRA_PATTERNS.search("npm ERR! code ECONNRESET")
        assert not _INFRA_PATTERNS.search("SyntaxError: invalid syntax")


@pytest.mark.django_db
class TestHealingAblation:
    def test_ablation_stores_each_mode_beside_the_evaluation(self):
        from backend.generation.models import GenerationJob
        from backend.generation.services.evaluation import runner
        from backend.users.tests.factories import UserFactory

        job = GenerationJob.objects.create(
            mode="scaffolding",
            created_by=UserFactory(),
            result_data={"evaluation": {"status": "passed"}, "files": {"app.py": "x"}},
        )
        seen = []

        def fake_run(self):
            seen.append(self.healing)
            status = "build_failed" if self.healing == "none" else "passed"
            return {"status": status, "build": {"healing": {"mode": str(self.healing), "changed": False}}}

        with mock.patch.object(runner.Evaluation, "run", fake_run):
            out = runner.evaluate_healing_ablation(job, ("none", "no_deps"))
        job.refresh_from_db()
        assert seen == ["none", "no_deps"]
        assert out["none"]["deployed"] is False
        assert job.result_data["healing_ablation"]["no_deps"]["deployed"] is True
        assert job.result_data["evaluation"] == {"status": "passed"}

    def test_evaluation_names_differ_per_mode(self):
        from backend.generation.models import GenerationJob
        from backend.generation.services.evaluation import runner
        from backend.users.tests.factories import UserFactory

        job = GenerationJob.objects.create(mode="scaffolding", created_by=UserFactory())
        assert runner.Evaluation(job).image_tag != runner.Evaluation(job, healing="none").image_tag

    def test_evaluate_job_records_deployed_and_healing(self):
        from backend.generation.models import GenerationJob
        from backend.generation.services.evaluation import runner
        from backend.users.tests.factories import UserFactory

        job = GenerationJob.objects.create(mode="scaffolding", created_by=UserFactory())
        outcome = {"status": "start_failed", "build": {"healing": {"mode": "full", "changed": True}}, "checks": []}
        with mock.patch.object(runner.Evaluation, "run", return_value=outcome):
            runner.evaluate_job(job)
        job.refresh_from_db()
        functional = job.metrics["functional"]
        assert (functional["deployed"], functional["healing_mode"], functional["healing_changed"]) == (
            False,
            "full",
            True,
        )
        assert runner.is_deployed("infra_error") is None
