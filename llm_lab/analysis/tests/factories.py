from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from llm_lab.analysis.models import AnalysisRun
from llm_lab.analysis.models import AnalyzerTool
from llm_lab.analysis.models import AnalyzerWorkspace
from llm_lab.analysis.models import Finding
from llm_lab.analysis.models import InstalledTool
from llm_lab.analysis.models import ToolResult
from llm_lab.users.tests.factories import UserFactory


class AnalyzerToolFactory(DjangoModelFactory):
    class Meta:
        model = AnalyzerTool
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"tool-{n}")
    name = factory.LazyAttribute(lambda o: o.slug.title())
    category = "lint"
    kind = "container"
    target_language = "python"
    install_cmd = "pip install --user demo"
    verify_cmd = "demo --version"
    run_cmd = "demo {target} --json"
    parser_key = "ruff"
    is_enabled = True


class AnalyzerWorkspaceFactory(DjangoModelFactory):
    class Meta:
        model = AnalyzerWorkspace

    user = factory.SubFactory(UserFactory)
    status = AnalyzerWorkspace.Status.READY


class InstalledToolFactory(DjangoModelFactory):
    class Meta:
        model = InstalledTool

    workspace = factory.SubFactory(AnalyzerWorkspaceFactory)
    tool = factory.SubFactory(AnalyzerToolFactory)
    status = InstalledTool.Status.INSTALLED


class AnalysisRunFactory(DjangoModelFactory):
    class Meta:
        model = AnalysisRun

    name = factory.Sequence(lambda n: f"run-{n}")
    created_by = factory.SubFactory(UserFactory)
    tool_slugs = factory.LazyFunction(lambda: ["bandit"])
    source_code = factory.LazyFunction(lambda: {"backend": "print('hi')"})


class ToolResultFactory(DjangoModelFactory):
    class Meta:
        model = ToolResult

    run = factory.SubFactory(AnalysisRunFactory)
    tool_slug = "bandit"
    category = "security"
    status = ToolResult.Status.COMPLETED


class FindingFactory(DjangoModelFactory):
    class Meta:
        model = Finding

    result = factory.SubFactory(ToolResultFactory)
    severity = "high"
    category = "security"
    title = factory.Sequence(lambda n: f"Finding {n}")
