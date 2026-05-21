"""Reports API tests."""

from __future__ import annotations

import pytest

from llm_lab.llm_models.tests.factories import LLMModelFactory
from llm_lab.reports.models import Report
from llm_lab.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_client(client):
    user = UserFactory()
    client.force_login(user)
    return client, user


def test_list_reports_empty(auth_client):
    client, _ = auth_client
    res = client.get("/api/reports/")
    assert res.status_code == 200
    body = res.json()
    assert body["pagination"]["total"] == 0
    assert body["reports"] == []


def test_generate_report_unknown_type_400(auth_client):
    client, _ = auth_client
    res = client.post(
        "/api/reports/generate/",
        data={"report_type": "garbage", "config": {}},
        content_type="application/json",
    )
    assert res.status_code == 400


def test_generate_report_missing_required_config_400(auth_client):
    client, _ = auth_client
    res = client.post(
        "/api/reports/generate/",
        data={"report_type": "model_analysis", "config": {}},
        content_type="application/json",
    )
    assert res.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_generate_report_happy_path(auth_client):
    client, _user = auth_client
    LLMModelFactory(model_id="openai/gpt-4o", model_name="GPT-4o")
    res = client.post(
        "/api/reports/generate/",
        data={
            "report_type": "model_analysis",
            "config": {"model_id": "openai/gpt-4o"},
            "title": "Test Model Report",
        },
        content_type="application/json",
    )
    assert res.status_code == 201, res.content
    body = res.json()
    assert body["title"] == "Test Model Report"
    assert body["report_type"] == "model_analysis"


def test_get_report_detail(auth_client):
    client, user = auth_client
    r = Report.objects.create(
        report_type="comprehensive",
        title="X",
        config={},
        created_by=user,
        status=Report.Status.COMPLETED,
        report_data={"k": "v"},
    )
    res = client.get(f"/api/reports/{r.report_id}/")
    assert res.status_code == 200
    body = res.json()
    assert body["report_id"] == r.report_id
    assert body["report_data"] == {"k": "v"}


def test_get_report_data_pending_400(auth_client):
    client, user = auth_client
    r = Report.objects.create(
        report_type="comprehensive",
        title="X",
        config={},
        created_by=user,
        status=Report.Status.PENDING,
    )
    res = client.get(f"/api/reports/{r.report_id}/data/")
    assert res.status_code == 400


def test_delete_report(auth_client):
    client, user = auth_client
    r = Report.objects.create(
        report_type="comprehensive",
        title="X",
        config={},
        created_by=user,
    )
    res = client.delete(f"/api/reports/{r.report_id}/")
    assert res.status_code == 200
    assert not Report.objects.filter(id=r.id).exists()


def test_unauthenticated_blocks_list(client):
    assert client.get("/api/reports/").status_code == 401
