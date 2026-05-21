"""Reports app — service tests."""

from __future__ import annotations

import time

import pytest

from llm_lab.generation.models import GenerationJob
from llm_lab.generation.tests.factories import AppRequirementTemplateFactory
from llm_lab.generation.tests.factories import GenerationJobFactory
from llm_lab.llm_models.tests.factories import LLMModelFactory
from llm_lab.reports import services
from llm_lab.reports.models import Report
from llm_lab.reports.services import generators
from llm_lab.reports.services import loc as loc_mod
from llm_lab.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_loc_from_job_counts_non_blank_non_comment():
    user = UserFactory()
    job = GenerationJobFactory(
        created_by=user,
        result_data={
            "backend_code": "import os\n# comment\n\nprint('hi')\n",
            "frontend_code": "import React from 'react';\n// comment\n\nReact.foo();\n",
        },
    )
    out = loc_mod.loc_from_job(job)
    assert out["backend_loc"] == 2
    assert out["frontend_loc"] == 2
    assert out["total_loc"] == 4


def test_loc_for_jobs_aggregates():
    user = UserFactory()
    j1 = GenerationJobFactory(
        created_by=user,
        result_data={"backend_code": "a\nb\n", "frontend_code": "x\n"},
    )
    j2 = GenerationJobFactory(
        created_by=user,
        result_data={"backend_code": "c\nd\ne\n"},
    )
    out = loc_mod.loc_for_jobs(GenerationJob.objects.filter(id__in=[j1.id, j2.id]))
    assert out["total_loc"] == 6
    assert out["counted"] == 2
    assert len(out["per_job"]) == 2


def test_generate_model_analysis_returns_aggregates():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o", model_name="GPT-4o")
    GenerationJobFactory.create_batch(
        2,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        duration_seconds=10.0,
        result_data={"backend_code": "x\ny\n"},
    )
    GenerationJobFactory(
        created_by=user,
        model=model,
        status=GenerationJob.Status.FAILED,
    )
    data = generators.generate_model_analysis({"model_id": model.model_id})
    assert data["model"]["model_id"] == model.model_id
    assert data["generation"]["total_jobs"] == 3
    assert data["generation"]["completed_jobs"] == 2
    assert data["generation"]["failed_jobs"] == 1
    assert data["loc"]["total_loc"] == 4
    assert data["findings"] == {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0,
    }


def test_generate_model_analysis_missing_id_raises():
    with pytest.raises(ValueError, match="model_id"):
        generators.generate_model_analysis({})


def test_generate_model_analysis_unknown_model_raises():
    with pytest.raises(ValueError, match="not found"):
        generators.generate_model_analysis({"model_id": "x/none"})


def test_generate_template_comparison():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    m1 = LLMModelFactory(model_id="m1")
    m2 = LLMModelFactory(model_id="m2")
    GenerationJobFactory.create_batch(
        2,
        created_by=user,
        model=m1,
        app_requirement=template,
        status=GenerationJob.Status.COMPLETED,
    )
    GenerationJobFactory(
        created_by=user,
        model=m2,
        app_requirement=template,
        status=GenerationJob.Status.FAILED,
    )

    data = generators.generate_template_comparison({"template_slug": template.slug})
    assert data["template"]["slug"] == template.slug
    assert data["total_models"] == 2


def test_generate_tool_analysis_empty_returns_zero():
    data = generators.generate_tool_analysis({})
    assert data["total_findings"] == 0
    assert data["tools"] == []


def test_generate_generation_analytics_includes_summary_and_by_model():
    user = UserFactory()
    model = LLMModelFactory(model_id="m1")
    GenerationJobFactory.create_batch(
        3,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
    )
    data = generators.generate_generation_analytics({"days": 7})
    assert data["window_days"] == 7
    assert data["summary"]["total_jobs"] >= 3
    assert any(row["model_id"] == "m1" for row in data["by_model"])


def test_generate_comprehensive_returns_platform_metrics():
    user = UserFactory()
    LLMModelFactory(model_id="m1")
    GenerationJobFactory(
        created_by=user,
        model=LLMModelFactory(model_id="m2"),
    )
    data = generators.generate_comprehensive({"days": 30})
    assert "generation_analytics" in data
    assert "tool_analysis" in data
    assert data["platform"]["total_models"] >= 2


@pytest.mark.django_db(transaction=True)
def test_create_and_dispatch_generates_report():
    user = UserFactory()
    LLMModelFactory(model_id="m1")
    GenerationJobFactory(
        created_by=user,
        model=LLMModelFactory(model_id="m2"),
        status=GenerationJob.Status.COMPLETED,
    )

    report = services.create_and_dispatch(
        report_type="generation_analytics",
        config={"days": 30},
        user=user,
    )
    # Wait for the daemon thread to finish (max ~5s).
    deadline = time.time() + 5.0
    while time.time() < deadline:
        report.refresh_from_db()
        if report.status in (Report.Status.COMPLETED, Report.Status.FAILED):
            break
        time.sleep(0.1)

    assert report.status == Report.Status.COMPLETED, report.error_message
    assert report.report_data["summary"]["total_jobs"] >= 1
    assert report.summary["window_days"] == 30


def test_create_and_dispatch_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown report_type"):
        services.create_and_dispatch(report_type="nope", config={})


def test_list_reports_filters_by_user():
    u1 = UserFactory()
    u2 = UserFactory()
    Report.objects.create(
        report_type="comprehensive",
        title="A",
        config={},
        created_by=u1,
    )
    Report.objects.create(
        report_type="comprehensive",
        title="B",
        config={},
        created_by=u2,
    )
    qs, total = services.list_reports(user=u1)
    assert total == 1
    assert next(iter(qs)).title == "A"
