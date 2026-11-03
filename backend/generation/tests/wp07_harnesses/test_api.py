"""Section 9.3 endpoints: ``/harnesses/``, ``/harnesses/{name}/facts/`` and ``/config/``."""

from __future__ import annotations

import pytest

from backend.generation.testing.factories import StackProfileFactory
from backend.users.tests.factories import UserFactory

from .conftest import ALL_HARNESSES

pytestmark = pytest.mark.django_db


@pytest.fixture
def api(client):
    client.force_login(UserFactory.create())
    return client


def test_harnesses_list(api):
    resp = api.get("/api/generation/harnesses/")
    assert resp.status_code == 200
    rows = {row["name"]: row for row in resp.json()}
    assert set(rows) == set(ALL_HARNESSES)
    opencode = rows["opencode"]
    assert opencode["enabled"] is True
    assert opencode["blocked_reason"] is None
    assert opencode["versions"] == ["1.18.35"]
    assert opencode["protocol"] == "openai_chat"
    assert opencode["capabilities"]["agentic"] is True
    assert rows["aider"]["option_schema"][0]["key"] == "edit_format"
    gemini = rows["gemini-cli"]
    assert gemini["enabled"] is False
    assert gemini["blocked_reason"]
    assert gemini["open_questions"]


def test_facts(api):
    resp = api.get("/api/generation/harnesses/qwen-code/facts/")
    assert resp.status_code == 200
    facts = resp.json()
    assert facts["exit_codes"] == {"53": "max_turns", "55": "budget_exhausted"}
    assert facts["front_protocol"] == "openai_chat"
    assert facts["min_call_output_tokens"] == 4096
    missing = api.get("/api/generation/harnesses/cursor/facts/")
    assert missing.status_code == 404
    assert missing.json()["code"] == "harness_unknown"


def test_config(api):
    StackProfileFactory.create(system=True)
    resp = api.get("/api/generation/config/")
    assert resp.status_code == 200
    body = resp.json()
    assert "harness_preset" in body["factor_vocabulary"]["keys"]
    assert "hole_mode" in body["factor_vocabulary"]["slot"]
    assert "hole_mode" not in body["factor_vocabulary"]["app"]
    assert body["stacks_matrix"]
    assert {m["value"] for m in body["auth_modes"]} == {"contract_bearer", "cookie_session", "spec_defined"}
    assert body["prose_variants"] == ["minimal", "standard", "exhaustive"]
    assert body["hole_modes"] == ["stub", "remove", "rewrite"]
    assert "related_files" in body["context_modes"]
    assert body["executors"] == ["single_shot", "restricted_agent"]
    assert "security" in body["eval_levels"]
    assert "auto_batch" in body["serving_tiers"]
    assert set(body["knob_classes"]) == {"C", "R", "O", "S"}
    assert body["limits"]["max_repeats"] == 200
    assert body["limits"]["max_cells"] == 20000
    assert set(body["limits"]["sandbox_max"]) == {"memory", "nano_cpus"}
    # WP-15 ask: the issue registry (contracts.issues.registry_payload) served for SummaryIssues/PlanRail.
    from backend.generation.contracts.issues import registry_payload

    assert body["issue_codes"] == registry_payload()
    assert body["issue_codes"]
    assert set(body["issue_codes"][0]) == {"code", "exploratory", "confirmatory", "alternatives", "owner", "summary"}
