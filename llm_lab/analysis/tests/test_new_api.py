"""Tests for new analysis API endpoints: profiles, suppression, schema, thresholds."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import patch

import pytest
from ninja.testing import TestClient

from config.api import api
from llm_lab.analysis.tests.factories import AnalysisProfileFactory
from llm_lab.analysis.tests.factories import AnalysisResultFactory
from llm_lab.analysis.tests.factories import AnalysisTaskFactory
from llm_lab.analysis.tests.factories import FindingFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client(user):
    client = TestClient(api)
    # Ninja's TestClient doesn't inject user/auth automatically — patch it in.
    # SessionAuth sets both request.user and request.auth to the same object.
    _original_build = client._build_request

    def _build_with_user(method, path, data, request_params):
        req = _original_build(method, path, data, request_params)
        req.user = user
        req.auth = user
        return req

    client._build_request = _build_with_user
    return client


class TestAnalyzerSchema:
    def test_analyzers_include_config_schema(self, api_client):
        resp = api_client.get("/analysis/analyzers/")
        assert resp.status_code == HTTPStatus.OK
        analyzers = resp.json()
        assert len(analyzers) > 0
        # Every analyzer must have config_schema, supports_live_target, supported_code_types
        for a in analyzers:
            assert "config_schema" in a, f"{a['name']} missing config_schema"
            assert "supports_live_target" in a
            assert "supported_code_types" in a
            # Each schema field must have required keys
            for field in a["config_schema"]:
                for key in ("name", "type", "label", "default", "options"):
                    assert key in field, f"{a['name']}.{field.get('name')} missing {key}"

    def test_bandit_schema_fields(self, api_client):
        resp = api_client.get("/analysis/analyzers/")
        assert resp.status_code == HTTPStatus.OK
        bandit = next(a for a in resp.json() if a["name"] == "bandit")
        assert bandit["supports_live_target"] is False
        assert "backend" in bandit["supported_code_types"]
        field_names = [f["name"] for f in bandit["config_schema"]]
        assert "level" in field_names
        assert "confidence" in field_names
        assert "skip_tests" in field_names

    def test_zap_is_live_target(self, api_client):
        resp = api_client.get("/analysis/analyzers/")
        zap = next(a for a in resp.json() if a["name"] == "zap")
        assert zap["supports_live_target"] is True


class TestProfileCRUD:
    def test_list_profiles_empty(self, api_client):
        resp = api_client.get("/analysis/profiles/")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json() == []

    def test_create_profile(self, api_client):
        resp = api_client.post(
            "/analysis/profiles/",
            json={
                "name": "Security Scan",
                "description": "Runs Bandit and ESLint",
                "analyzers": ["bandit", "eslint"],
                "settings": {"bandit": {"level": "HIGH"}},
                "is_default": False,
            },
        )
        assert resp.status_code == HTTPStatus.OK
        p = resp.json()
        assert p["name"] == "Security Scan"
        assert p["analyzers"] == ["bandit", "eslint"]
        assert p["settings"]["bandit"]["level"] == "HIGH"
        assert p["is_default"] is False

    def test_create_default_profile_clears_others(self, api_client, user):
        # First profile as default
        p1 = AnalysisProfileFactory(name="Profile A", is_default=True, created_by=user)
        # Create a second profile as default via API
        resp = api_client.post(
            "/analysis/profiles/",
            json={
                "name": "Profile B",
                "analyzers": ["pylint"],
                "settings": {},
                "is_default": True,
            },
        )
        assert resp.status_code == HTTPStatus.OK
        p1.refresh_from_db()
        assert p1.is_default is False, "Previous default should have been cleared"

    def test_get_profile(self, api_client, user):
        p = AnalysisProfileFactory(name="My Profile", created_by=user)
        resp = api_client.get(f"/analysis/profiles/{p.id}/")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["name"] == "My Profile"

    def test_update_profile(self, api_client, user):
        p = AnalysisProfileFactory(name="Old Name", created_by=user)
        resp = api_client.put(
            f"/analysis/profiles/{p.id}/",
            json={
                "name": "New Name",
                "analyzers": ["bandit"],
                "settings": {},
                "is_default": False,
            },
        )
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["name"] == "New Name"

    def test_delete_profile(self, api_client, user):
        p = AnalysisProfileFactory(name="To Delete", created_by=user)
        resp = api_client.delete(f"/analysis/profiles/{p.id}/")
        assert resp.status_code == HTTPStatus.OK
        # Confirm gone
        resp2 = api_client.get(f"/analysis/profiles/{p.id}/")
        assert resp2.status_code == HTTPStatus.NOT_FOUND

    def test_cannot_access_other_users_profile(self, api_client):
        # Profile owned by a different user (factory creates its own user)
        other_profile = AnalysisProfileFactory(name="Other Profile")
        resp = api_client.get(f"/analysis/profiles/{other_profile.id}/")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_list_only_own_profiles(self, api_client, user):
        AnalysisProfileFactory(name="Mine", created_by=user)
        AnalysisProfileFactory(name="Not Mine")  # different user
        resp = api_client.get("/analysis/profiles/")
        assert resp.status_code == HTTPStatus.OK
        names = [p["name"] for p in resp.json()]
        assert "Mine" in names
        assert "Not Mine" not in names


class TestFindingSuppression:
    def test_suppress_finding(self, api_client, user):
        task = AnalysisTaskFactory(created_by=user)
        result = AnalysisResultFactory(task=task)
        finding = FindingFactory(result=result)
        assert finding.suppressed is False

        resp = api_client.post(
            f"/analysis/tasks/{task.id}/findings/{finding.id}/suppress/",
            json={"reason": "False positive in test code"},
        )
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["suppressed"] is True
        assert data["suppression_reason"] == "False positive in test code"

    def test_suppress_hides_from_default_list(self, api_client, user):
        task = AnalysisTaskFactory(created_by=user)
        result = AnalysisResultFactory(task=task)
        FindingFactory(result=result, title="Visible")
        FindingFactory(result=result, title="Hidden", suppressed=True)

        resp = api_client.get(f"/analysis/tasks/{task.id}/findings/")
        assert resp.status_code == HTTPStatus.OK
        titles = [f["title"] for f in resp.json()["items"]]
        assert "Visible" in titles
        assert "Hidden" not in titles

    def test_include_suppressed_shows_all(self, api_client, user):
        task = AnalysisTaskFactory(created_by=user)
        result = AnalysisResultFactory(task=task)
        FindingFactory(result=result, title="Visible")
        FindingFactory(result=result, title="Hidden", suppressed=True)

        resp = api_client.get(
            f"/analysis/tasks/{task.id}/findings/?include_suppressed=true",
        )
        assert resp.status_code == HTTPStatus.OK
        titles = [f["title"] for f in resp.json()["items"]]
        assert "Visible" in titles
        assert "Hidden" in titles

    def test_unsuppress_finding(self, api_client, user):
        task = AnalysisTaskFactory(created_by=user)
        result = AnalysisResultFactory(task=task)
        finding = FindingFactory(result=result, suppressed=True, suppression_reason="oops")

        resp = api_client.post(
            f"/analysis/tasks/{task.id}/findings/{finding.id}/unsuppress/",
        )
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["suppressed"] is False
        assert data["suppression_reason"] == ""

    def test_file_path_filter(self, api_client, user):
        task = AnalysisTaskFactory(created_by=user)
        result = AnalysisResultFactory(task=task)
        FindingFactory(result=result, file_path="backend/app.py")
        FindingFactory(result=result, file_path="frontend/index.js")

        resp = api_client.get(f"/analysis/tasks/{task.id}/findings/?file_path=backend")
        assert resp.status_code == HTTPStatus.OK
        items = resp.json()["items"]
        assert all("backend" in f["file_path"] for f in items)


class TestTaskWithProfile:
    @patch("llm_lab.analysis.api.views._dispatch_task")
    def test_create_task_with_profile_id(self, mock_dispatch, api_client, user):
        mock_dispatch.return_value = None
        profile = AnalysisProfileFactory(
            name="Test",
            analyzers=["bandit"],
            settings={"bandit": {"level": "HIGH"}},
            created_by=user,
        )
        resp = api_client.post(
            "/analysis/tasks/",
            json={
                "analyzers": [],
                "profile_id": profile.id,
                "auto_start": False,
            },
        )
        assert resp.status_code == HTTPStatus.OK
        task = resp.json()
        assert task["profile_id"] == profile.id
        # Config should include profile's analyzers
        assert "bandit" in task["configuration"]["analyzers"]

    @patch("llm_lab.analysis.api.views._dispatch_task")
    def test_create_task_with_thresholds(self, mock_dispatch, api_client):
        mock_dispatch.return_value = None
        resp = api_client.post(
            "/analysis/tasks/",
            json={
                "analyzers": ["bandit"],
                "thresholds": {"critical": 0, "high": 5},
                "auto_start": False,
            },
        )
        assert resp.status_code == HTTPStatus.OK
        task = resp.json()
        assert task["configuration"]["thresholds"] == {"critical": 0, "high": 5}

    @patch("llm_lab.analysis.api.views._dispatch_task")
    def test_invalid_analyzer_settings_rejected(self, mock_dispatch, api_client):
        mock_dispatch.return_value = None
        resp = api_client.post(
            "/analysis/tasks/",
            json={
                "analyzers": ["bandit"],
                "settings": {"bandit": {"level": "INVALID_LEVEL"}},
                "auto_start": False,
            },
        )
        assert resp.status_code == 422
