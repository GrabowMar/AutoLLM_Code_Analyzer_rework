from __future__ import annotations

import pytest
from django.db import IntegrityError

from llm_lab.analysis.models import AnalysisResult
from llm_lab.analysis.models import AnalysisTask
from llm_lab.analysis.models import Finding
from llm_lab.analysis.tests.factories import AnalysisResultFactory
from llm_lab.analysis.tests.factories import AnalysisTaskFactory
from llm_lab.analysis.tests.factories import AnalyzerConfigFactory
from llm_lab.analysis.tests.factories import FindingFactory
from llm_lab.generation.tests.factories import GenerationJobFactory

pytestmark = pytest.mark.django_db


class TestAnalysisTask:
    def test_str(self):
        task = AnalysisTaskFactory(name="My Analysis")
        assert str(task) == "Analysis: My Analysis (pending)"

    def test_str_without_name_inline(self):
        task = AnalysisTaskFactory(name="")
        assert str(task) == "Analysis: inline code (pending)"

    def test_str_without_name_with_job(self):
        job = GenerationJobFactory()
        task = AnalysisTaskFactory(name="", generation_job=job)
        assert str(task) == f"Analysis: Job {job.id} (pending)"

    def test_get_code_for_analysis_from_source_code(self):
        task = AnalysisTaskFactory(
            source_code={"backend": "print('hello')", "frontend": "alert('hi')"},
        )
        code = task.get_code_for_analysis()
        assert code["backend"] == "print('hello')"
        assert code["frontend"] == "alert('hi')"

    def test_get_code_for_analysis_from_job(self):
        job = GenerationJobFactory(
            result_data={
                "backend_code": "from flask import Flask",
                "frontend_code": "import React from 'react'",
            },
        )
        task = AnalysisTaskFactory(
            source_code={},
            generation_job=job,
        )
        code = task.get_code_for_analysis()
        assert code["backend"] == "from flask import Flask"
        assert code["frontend"] == "import React from 'react'"

    def test_get_code_for_analysis_empty(self):
        task = AnalysisTaskFactory(
            source_code={},
            generation_job=None,
        )
        code = task.get_code_for_analysis()
        assert code == {}

    def test_default_status(self):
        task = AnalysisTaskFactory()
        assert task.status == AnalysisTask.Status.PENDING


class TestAnalysisResult:
    def test_str(self):
        result = AnalysisResultFactory(
            analyzer_name="bandit",
            status=AnalysisResult.Status.COMPLETED,
        )
        expected = f"bandit (completed) → Task {result.task_id}"
        assert str(result) == expected

    def test_unique_together(self):
        result = AnalysisResultFactory(analyzer_name="bandit")
        with pytest.raises(IntegrityError):
            AnalysisResultFactory(
                task=result.task,
                analyzer_name="bandit",
            )


class TestFinding:
    def test_str(self):
        finding = FindingFactory(
            severity=Finding.Severity.HIGH,
            title="SQL Injection",
            file_path="app.py",
            line_number=42,
        )
        assert str(finding) == "[HIGH] SQL Injection (app.py:42)"

    def test_str_without_file(self):
        finding = FindingFactory(
            severity=Finding.Severity.CRITICAL,
            title="Hardcoded password",
            file_path="",
        )
        assert str(finding) == "[CRITICAL] Hardcoded password"

    def test_ordering(self):
        result = AnalysisResultFactory()
        FindingFactory(result=result, severity=Finding.Severity.LOW, title="Low issue")
        FindingFactory(
            result=result,
            severity=Finding.Severity.CRITICAL,
            title="Critical issue",
        )
        FindingFactory(
            result=result,
            severity=Finding.Severity.MEDIUM,
            title="Medium issue",
        )

        findings = list(Finding.objects.filter(result=result))
        assert findings[0].severity == "critical"
        assert findings[1].severity == "medium"
        assert findings[2].severity == "low"


class TestAnalyzerConfig:
    def test_str(self):
        config = AnalyzerConfigFactory(
            name="Bandit Config",
            analyzer_name="bandit",
            enabled=True,
        )
        assert str(config) == "Bandit Config (bandit) [enabled]"

    def test_str_disabled(self):
        config = AnalyzerConfigFactory(
            name="Disabled Config",
            analyzer_name="eslint",
            enabled=False,
        )
        assert str(config) == "Disabled Config (eslint) [disabled]"

    def test_unique_name(self):
        AnalyzerConfigFactory(name="unique-name")
        with pytest.raises(IntegrityError):
            AnalyzerConfigFactory(name="unique-name")
