"""Reports API tests."""

from __future__ import annotations

import pytest

from backend.llm_models.tests.factories import LLMModelFactory
from backend.reports.models import Report
from backend.users.tests.factories import UserFactory

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


def test_get_report_other_user_404(auth_client):
    client, _ = auth_client
    other = UserFactory()
    r = Report.objects.create(
        report_type="comprehensive",
        title="Theirs",
        config={},
        created_by=other,
        status=Report.Status.COMPLETED,
    )
    assert client.get(f"/api/reports/{r.report_id}/").status_code == 404


def test_get_report_data_other_user_404(auth_client):
    client, _ = auth_client
    other = UserFactory()
    r = Report.objects.create(
        report_type="comprehensive",
        title="Theirs",
        config={},
        created_by=other,
        status=Report.Status.COMPLETED,
        report_data={"k": "v"},
    )
    assert client.get(f"/api/reports/{r.report_id}/data/").status_code == 404


def test_delete_report_other_user_404(auth_client):
    client, _ = auth_client
    other = UserFactory()
    r = Report.objects.create(
        report_type="comprehensive",
        title="Theirs",
        config={},
        created_by=other,
    )
    assert client.delete(f"/api/reports/{r.report_id}/").status_code == 404
    assert Report.objects.filter(id=r.id).exists()


def test_get_report_ends_a_report_stuck_pending(auth_client):
    from datetime import timedelta

    from django.utils import timezone

    client, user = auth_client
    r = Report.objects.create(report_type="comprehensive", title="X", config={}, created_by=user)
    Report.objects.filter(id=r.id).update(created_at=timezone.now() - timedelta(hours=1))
    body = client.get(f"/api/reports/{r.report_id}/").json()
    # The detail page polls while pending; a report whose thread died must end instead.
    assert body["status"] == "failed"
    assert body["error_message"].startswith("Interrupted")


def test_generate_report_dispatches_on_commit(auth_client, django_capture_on_commit_callbacks):
    from unittest import mock

    client, _user = auth_client
    LLMModelFactory(model_id="openai/gpt-4o")
    with (
        mock.patch("backend.reports.services.report_service._dispatch") as dispatch,
        django_capture_on_commit_callbacks(execute=True),
    ):
        res = client.post(
            "/api/reports/generate/",
            data={"report_type": "model_analysis", "config": {"model_id": "openai/gpt-4o"}},
            content_type="application/json",
        )
    assert res.status_code == 201, res.content
    dispatch.assert_called_once_with(Report.objects.get(report_id=res.json()["report_id"]).id)


def test_generate_tool_analysis_with_unknown_tool_400(auth_client):
    client, _ = auth_client
    res = client.post(
        "/api/reports/generate/",
        data={"report_type": "tool_analysis", "config": {"tool_name": "no-such-tool"}},
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "Unknown tool" in res.json()["message"]


def test_get_report_tables_returns_the_curated_tables(auth_client):
    client, user = auth_client
    r = Report.objects.create(
        report_type="tool_analysis",
        title="Tools",
        config={},
        created_by=user,
        status=Report.Status.COMPLETED,
        report_data={
            "tool_filter": None,
            "total_findings": None,
            "tools": [{"analyzer": "bandit", "total": 2, "findings_per_measured_execution": 0.5}],
        },
    )
    res = client.get(f"/api/reports/{r.report_id}/tables/")
    assert res.status_code == 200, res.content
    body = res.json()
    assert (body["report_id"], body["report_type"], body["title"]) == (r.report_id, "tool_analysis", "Tools")
    tables = {t["title"]: t for t in body["tables"]}
    assert set(tables) == {"Summary", "tools"}
    # Cells are formatted as in the Markdown export: unmeasured reads "-", never 0.
    assert tables["Summary"]["rows"] == [{"k": "tool_filter", "v": "-"}, {"k": "total_findings", "v": "-"}]
    tools = tables["tools"]
    assert [c["key"] for c in tools["columns"]] == ["analyzer", "total", "findings_per_measured_execution"]
    assert tools["rows"] == [{"analyzer": "bandit", "total": "2", "findings_per_measured_execution": "0.5"}]
    assert (tools["total_rows"], tools["note"]) == (1, "")


def test_get_report_tables_pending_400_and_other_user_404(auth_client):
    client, user = auth_client
    pending = Report.objects.create(report_type="comprehensive", title="X", config={}, created_by=user)
    assert client.get(f"/api/reports/{pending.report_id}/tables/").status_code == 400
    theirs = Report.objects.create(
        report_type="comprehensive",
        title="Theirs",
        config={},
        created_by=UserFactory(),
        status=Report.Status.COMPLETED,
        report_data={"k": "v"},
    )
    assert client.get(f"/api/reports/{theirs.report_id}/tables/").status_code == 404
