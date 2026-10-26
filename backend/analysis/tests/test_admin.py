"""Django admin for the analysis engine (admin.py): a pre-registered study cannot be edited there."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.urls import reverse

from backend.analysis.models import AnalysisStudy
from backend.analysis.tests.factories import AnalysisStudyFactory

pytestmark = pytest.mark.django_db

QUESTIONS = [{"id": "RQ1", "text": "Do models differ?", "kind": "confirmatory"}]
PRE_REGISTERED = ("research_questions", "analysis_design", "target_spec", "exclusion_rules", "profile", "created_by")


def _model_admin():
    return admin.site._registry[AnalysisStudy]


@pytest.mark.parametrize("status", ["locked", "running", "completed", "cancelled"])
def test_a_study_past_draft_is_view_only(admin_client, rf, admin_user, status):
    study = AnalysisStudyFactory(status=status, research_questions=QUESTIONS, analysis_design={"group_by": ["model"]})
    url = reverse("admin:analysis_analysisstudy_change", args=[study.pk])
    request = rf.get(url)
    request.user = admin_user

    assert admin_client.get(url).status_code == 200  # still viewable
    assert _model_admin().has_change_permission(request, study) is False
    assert set(PRE_REGISTERED) <= set(_model_admin().get_readonly_fields(request, study))
    edited = admin_client.post(
        url,
        {"name": study.name, "research_questions": '[{"text": "post-hoc", "kind": "confirmatory"}]'},
    )

    assert edited.status_code == 403
    study.refresh_from_db()
    assert study.research_questions == QUESTIONS


def test_a_draft_study_stays_editable(admin_client, rf, admin_user):
    study = AnalysisStudyFactory(status="draft")
    request = rf.get("/")
    request.user = admin_user

    assert _model_admin().has_change_permission(request, study) is True
    readonly = set(_model_admin().get_readonly_fields(request, study))
    assert not readonly & set(PRE_REGISTERED)
    assert {"protocol", "plan", "report", "status"} <= readonly
    url = reverse("admin:analysis_analysisstudy_change", args=[study.pk])
    assert admin_client.get(url).status_code == 200
