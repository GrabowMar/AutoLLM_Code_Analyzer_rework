"""Hidden conformance suite: request-body synthesis and fresh resources for destructive checks."""

from __future__ import annotations

import base64
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from backend.generation.tests.test_conformance import _plan
from backend.generation.tests.test_conformance import _toy_app

TESTER = Path(__file__).resolve().parents[1] / "images" / "conformance-tester" / "tester.py"


@pytest.fixture(scope="module")
def tester():
    spec = importlib.util.spec_from_file_location("conformance_tester_synth", TESTER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_type_descriptors_from_shipped_specs_become_typed_values(tester):
    body = tester.Synth("t").value(
        {
            "title": "string",
            "isbn": "string (optional)",
            "year": "integer (optional)",
            "quantity": "integer",
            "amount": "number",
            "tags": "array (optional)",
            "status": "string (todo|in_progress|done)",
            "type": "string (info|warning|error)",
            "encoded": "string (base64)",
            "date": "YYYY-MM-DD",
            "time": "HH:MM",
            "host": "example.com",
        },
    )
    assert body["title"].startswith("title t")
    assert body["isbn"].startswith("isbn t")
    assert isinstance(body["year"], int)
    assert body["year"] > 2000
    assert body["quantity"] == 1
    assert body["amount"] == 1.5
    assert body["tags"] == []
    assert body["status"] == "todo"
    assert body["type"] == "info"
    assert base64.b64decode(body["encoded"]).decode() == "eval t"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", body["date"])
    assert re.fullmatch(r"\d{2}:\d{2}", body["time"])
    assert body["host"] == "example.com"


def test_v2_behaviour_is_kept_for_plain_samples(tester):
    body = tester.Synth("t").value(
        {"priority": "low|medium|high", "due_at": "ISO8601", "done": True, "n": 3, "label": "A"},
    )
    assert body == {"priority": "low", "due_at": body["due_at"], "done": True, "n": 3, "label": "A"}
    assert "T" in body["due_at"]


def test_synthesised_strings_stay_short(tester):
    synth = tester.Synth("e5195f2e")
    values = [synth.value({"isbn": "string"})["isbn"] for _ in range(120)]
    assert max(len(v) for v in values) <= 20
    assert len(set(values)) == len(values)


def test_failed_fresh_create_does_not_reuse_a_consumed_item(tester):
    server = _toy_app()
    handler = server.RequestHandlerClass
    toy_post = handler.do_POST
    creates = {"n": 0}

    def do_post(self):
        if self.path == "/api/todos" and self.headers.get("Authorization"):
            creates["n"] += 1
            if creates["n"] > 1:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                return self._send(400, {"error": "quota"})
        return toy_post(self)

    handler.do_POST = do_post
    try:
        report = tester.run(_plan(server))
    finally:
        server.shutdown()
    failed = {c["name"]: c["detail"] for c in report["checks"] if not c["passed"]}
    assert "could not create a fresh resource" in failed["DELETE /api/todos/:id"]
    assert "could not create a fresh resource" in failed["POST /api/admin/todos/:id/toggle"]
    assert "PUT /api/todos/:id" not in failed
