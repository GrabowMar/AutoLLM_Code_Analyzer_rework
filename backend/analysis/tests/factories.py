from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import AnalyzerWorkspace
from backend.analysis.models import Finding
from backend.analysis.models import InstalledTool
from backend.analysis.models import ToolResult
from backend.users.tests.factories import UserFactory


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


def generation_run(created_by=None, files: dict[str, str] | None = None, origins: dict[str, str] | None = None, **kw):
    """A tested ``GenerationRun`` whose collected tree is *files* (``{path: text}``, real blobs).

    *origins* maps a path to its ``RunFile.origin`` (default ``authored``); extra keyword arguments go to
    ``GenerationRunFactory``.
    """
    from backend.generation import blobs
    from backend.generation.runs.view import source_loc
    from backend.generation.testing import factories as gen_factories

    if created_by is not None:
        kw["created_by"] = created_by
    kw.setdefault("tested", True)
    run = gen_factories.GenerationRunFactory(**kw)
    for path, text in (files or {}).items():
        gen_factories.RunFileFactory(
            run=run,
            path=path,
            blob=blobs.put_text(text),
            size=len(text.encode()),
            origin=(origins or {}).get(path, "authored"),
            loc=source_loc(path, text),
        )
    return run
