"""WP-09: the evaluation API (spec 9.9)."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client

from backend.generation.evaluation import baseline
from backend.generation.models import EvalBaseline
from backend.generation.models import choices
from backend.generation.testing.golden_library import install_golden_library
from backend.generation.tests.wp09_eval.helpers import kit_tree
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
BASE = "/api/generation"


@pytest.fixture
def client() -> Client:
    client = Client()
    client.force_login(UserFactory())
    return client


@pytest.fixture
def golden() -> Any:
    return install_golden_library()


def test_probe_catalog(client: Client) -> None:
    rows = client.get(f"{BASE}/eval/probes/catalog/").json()
    assert len(rows) == 17
    sqli = next(r for r in rows if r["kind"] == "sqli_boolean")
    assert sqli["cwe"] == "cwe-89"
    assert sqli["validated"] is True
    assert sqli["requires"]


def test_conformance_status(client: Client, golden: Any) -> None:
    rows = client.get(f"{BASE}/eval/conformance/").json()
    assert {r["framework"] for r in rows} == {"flask", "fastapi"}
    assert all(r["ok"] is False for r in rows)
    assert {t["task"] for t in rows[0]["tasks"]} == {
        "crud_todo_list",
        "team_roles_multitenant",
        "file_share_multipart",
    }


def test_suite_checks_preview(client: Client, golden: Any) -> None:
    suite = golden.get("eval_suite", "eval-v2")
    resp = client.get(
        f"{BASE}/eval/suites/{suite.ref}/checks/",
        {"task": "crud_todo_list@1", "auth_mode": "cookie_session", "stack": "flask-react@1"},
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["plan"]["auth"]["mode"] == "cookie_session"
    assert body["n_checks"] == len(body["plan"]["checks"]) > 5
    assert body["n_probes"] == 2
    assert body["disabled_probes"] == []
    assert (
        client.get(
            f"{BASE}/eval/suites/{suite.ref}/checks/",
            {"task": "crud_todo_list@1", "auth_mode": "x"},
        ).status_code
        == 400
    )


def test_baselines(client: Client, golden: Any) -> None:
    starter = golden.get("starter", "flask-react-minimal")
    version = golden.get("task", "crud_todo_list")
    suite = golden.get("eval_suite", "eval-v2")
    ref = baseline.reference_run(
        reference_of=choices.ReferenceOf.STARTER_BASELINE,
        task_version=version,
        stack=starter.stack_profile,
        starter=starter,
        auth_mode="contract_bearer",
        files=kit_tree(),
        suite=suite,
    )
    EvalBaseline.objects.create(
        starter=starter,
        task_version=version,
        auth_mode="contract_bearer",
        eval_suite=suite,
        reference_run=ref,
        agent_pass_rate=0.2,
    )
    rows = client.get(f"{BASE}/eval/baselines/", {"task": "crud_todo_list@1"}).json()
    assert [(r["starter"], r["agent_pass_rate"]) for r in rows] == [(starter.pinned_ref, 0.2)]
    assert client.get(f"{BASE}/eval/baselines/", {"starter": "fastapi-vue-minimal@1"}).json() == []


def test_retest(client: Client) -> None:
    from backend.generation.testing.factories import StudyFactory

    study = StudyFactory()
    body = client.get(f"{BASE}/eval/retest/", {"study": str(study.pk)}).json()
    assert body == {"n": 0, "check_agreement": None, "run_agreement": None, "kappa": None}
    assert client.get(f"{BASE}/eval/retest/", {"study": "nope"}).status_code == 404


def test_library_oracle_endpoint_no_longer_answers_501(golden: Any) -> None:
    staff = UserFactory(is_staff=True)
    client = Client()
    client.force_login(staff)
    resp = client.post(
        f"{BASE}/library/tasks/crud_todo_list/versions/1/oracle/",
        {"stacks": ["fastapi-vue@1"]},
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    assert resp.json() == {"queued_runs": []}  # no fastapi-vue reference tree: recorded missing
