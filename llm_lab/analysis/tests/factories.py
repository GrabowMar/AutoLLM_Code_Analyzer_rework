from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from llm_lab.analysis.models import AnalysisResult
from llm_lab.analysis.models import AnalysisTask
from llm_lab.analysis.models import AnalyzerConfig
from llm_lab.analysis.models import Finding
from llm_lab.users.tests.factories import UserFactory


class AnalysisTaskFactory(DjangoModelFactory):
    class Meta:
        model = AnalysisTask

    name = factory.Sequence(lambda n: f"Analysis Task {n}")
    status = AnalysisTask.Status.PENDING
    generation_job = None
    source_code = {
        "backend": (
            "from flask import Flask\napp = Flask(__name__)\n\n@app.route('/')\ndef index():\n    return 'Hello'"
        ),
        "frontend": "",
    }
    configuration = {"analyzers": ["bandit", "eslint"], "settings": {}}
    results_summary = {}
    created_by = factory.SubFactory(UserFactory)


class AnalysisResultFactory(DjangoModelFactory):
    class Meta:
        model = AnalysisResult

    task = factory.SubFactory(AnalysisTaskFactory)
    analyzer_type = AnalysisResult.AnalyzerType.STATIC
    analyzer_name = "bandit"
    status = AnalysisResult.Status.COMPLETED
    raw_output = {}
    summary = {"total_findings": 0}


class FindingFactory(DjangoModelFactory):
    class Meta:
        model = Finding

    result = factory.SubFactory(AnalysisResultFactory)
    severity = Finding.Severity.MEDIUM
    category = Finding.Category.SECURITY
    confidence = Finding.Confidence.MEDIUM
    title = factory.Sequence(lambda n: f"Finding {n}")
    description = factory.Faker("paragraph")
    suggestion = factory.Faker("sentence")
    file_path = "app.py"
    line_number = factory.Faker("pyint", min_value=1, max_value=500)
    rule_id = factory.Sequence(lambda n: f"RULE-{n:03d}")


class AnalyzerConfigFactory(DjangoModelFactory):
    class Meta:
        model = AnalyzerConfig

    name = factory.Sequence(lambda n: f"Config {n}")
    analyzer_name = "bandit"
    enabled = True
    default_settings = {}
    description = ""
