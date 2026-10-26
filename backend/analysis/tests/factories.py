from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from backend.analysis.models import AnalysisProfile
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalysisStudy
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ReviewSample
from backend.analysis.models import ToolResult
from backend.users.tests.factories import UserFactory


class AnalyzerToolFactory(DjangoModelFactory):
    class Meta:
        model = AnalyzerTool
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"tool-{n}")
    name = factory.LazyAttribute(lambda o: o.slug.title())
    category = "lint"
    kind = "static"
    languages = factory.LazyFunction(lambda: ["python"])
    is_enabled = True


class AnalysisProfileFactory(DjangoModelFactory):
    class Meta:
        model = AnalysisProfile

    slug = factory.Sequence(lambda n: f"profile-{n}")
    name = factory.LazyAttribute(lambda o: o.slug.title())
    config = factory.LazyFunction(lambda: {"tools": [{"slug": "bandit", "required": True, "config": {}}]})


class AnalysisStudyFactory(DjangoModelFactory):
    class Meta:
        model = AnalysisStudy

    name = factory.Sequence(lambda n: f"study-{n}")
    created_by = factory.SubFactory(UserFactory)


class AnalysisRunFactory(DjangoModelFactory):
    class Meta:
        model = AnalysisRun

    name = factory.Sequence(lambda n: f"run-{n}")
    created_by = factory.SubFactory(UserFactory)
    plan = factory.LazyFunction(lambda: {"schema_version": 1, "tools": [{"slug": "bandit", "required": True}]})
    plan_hash = factory.Sequence(lambda n: f"{n:064x}")
    source_code = factory.LazyFunction(lambda: {"app.py": "print('hi')"})
    status = AnalysisRun.Status.COMPLETED
    is_complete = True


class ToolResultFactory(DjangoModelFactory):
    class Meta:
        model = ToolResult

    run = factory.SubFactory(AnalysisRunFactory)
    tool_slug = "bandit"
    tool_kind = "static"
    category = "security"
    status = ToolResult.Status.SUCCEEDED


class ReviewSampleFactory(DjangoModelFactory):
    class Meta:
        model = ReviewSample

    result = factory.SubFactory(ToolResultFactory, tool_slug="ai_review", tool_kind="ai", category="ai")
    index = factory.Sequence(lambda n: n)
    engine = "single_shot"
    model_slug = "deepseek/deepseek-v4.1-flash"
    status = ReviewSample.Status.SUCCEEDED


class FindingFactory(DjangoModelFactory):
    class Meta:
        model = Finding

    result = factory.SubFactory(ToolResultFactory)
    run = factory.LazyAttribute(lambda o: o.result.run)
    tool_slug = factory.LazyAttribute(lambda o: o.result.tool_slug)
    severity = "high"
    category = "security"
    title = factory.Sequence(lambda n: f"Finding {n}")
    file_path = "app.py"
    line_number = 1
    counted = True
