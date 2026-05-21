from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from llm_lab.analysis.models import AnalysisResult
from llm_lab.analysis.models import AnalysisTask
from llm_lab.analysis.models import Finding
from llm_lab.analysis.services.analysis_service import AnalysisService
from llm_lab.analysis.services.base import AnalyzerOutput
from llm_lab.analysis.services.base import FindingData
from llm_lab.analysis.tests.factories import AnalysisTaskFactory

pytestmark = pytest.mark.django_db(transaction=True)


def _make_success_output(findings: list[FindingData] | None = None) -> AnalyzerOutput:
    """Build a successful AnalyzerOutput with optional findings."""
    findings = findings or []
    return AnalyzerOutput(
        findings=findings,
        summary={"total_issues": len(findings)},
        raw_output={"tool": "mock"},
    )


def _make_error_output(message: str = "Tool crashed") -> AnalyzerOutput:
    return AnalyzerOutput(error=message)


def _make_finding(
    severity: str = "medium",
    title: str = "Test finding",
) -> FindingData:
    return FindingData(
        severity=severity,
        category="security",
        title=title,
        description="A test finding",
        suggestion="Fix it",
        file_path="app.py",
        line_number=10,
        rule_id="TEST-001",
    )


@pytest.fixture
def task():
    return AnalysisTaskFactory(
        source_code={"backend": "import os\nos.system('ls')\n", "frontend": ""},
        configuration={"analyzers": ["bandit", "eslint"], "settings": {}},
    )


def _mock_analyzer(name: str, analyzer_type: str, output: AnalyzerOutput):
    """Build a mock analyzer with the given output."""
    analyzer = MagicMock()
    analyzer.name = name
    analyzer.analyzer_type = analyzer_type
    analyzer.analyze.return_value = output
    return analyzer


class TestAnalysisService:
    @patch(
        "llm_lab.analysis.services.result_service.AnalyzerRegistry.get_instance",
    )
    def test_execute_success(self, mock_get_instance, task):
        finding = _make_finding(severity="high", title="Security issue")
        bandit_output = _make_success_output([finding])
        eslint_output = _make_success_output()

        def side_effect(name):
            return {
                "bandit": _mock_analyzer("bandit", "static", bandit_output),
                "eslint": _mock_analyzer("eslint", "static", eslint_output),
            }.get(name)

        mock_get_instance.side_effect = side_effect

        service = AnalysisService()
        service.execute(task)

        task.refresh_from_db()
        assert task.status == AnalysisTask.Status.COMPLETED
        assert task.completed_at is not None
        assert task.duration_seconds is not None

        results = AnalysisResult.objects.filter(task=task)
        expected_results = 2
        assert results.count() == expected_results
        completed = results.filter(status=AnalysisResult.Status.COMPLETED).count()
        assert completed == expected_results

        findings = Finding.objects.filter(result__task=task)
        assert findings.count() == 1
        assert findings.first().title == "Security issue"

    @patch(
        "llm_lab.analysis.services.result_service.AnalyzerRegistry.get_instance",
    )
    def test_execute_partial_failure(self, mock_get_instance, task):
        bandit_output = _make_success_output()
        eslint_output = _make_error_output("ESLint crashed")

        def side_effect(name):
            return {
                "bandit": _mock_analyzer("bandit", "static", bandit_output),
                "eslint": _mock_analyzer("eslint", "static", eslint_output),
            }.get(name)

        mock_get_instance.side_effect = side_effect

        service = AnalysisService()
        service.execute(task)

        task.refresh_from_db()
        assert task.status == AnalysisTask.Status.PARTIAL

        results = AnalysisResult.objects.filter(task=task)
        assert results.filter(status=AnalysisResult.Status.COMPLETED).count() == 1
        assert results.filter(status=AnalysisResult.Status.FAILED).count() == 1

    @patch(
        "llm_lab.analysis.services.result_service.AnalyzerRegistry.get_instance",
    )
    def test_execute_all_fail(self, mock_get_instance, task):
        def side_effect(name):
            return {
                "bandit": _mock_analyzer(
                    "bandit",
                    "static",
                    _make_error_output("Bandit crashed"),
                ),
                "eslint": _mock_analyzer(
                    "eslint",
                    "static",
                    _make_error_output("ESLint crashed"),
                ),
            }.get(name)

        mock_get_instance.side_effect = side_effect

        service = AnalysisService()
        service.execute(task)

        task.refresh_from_db()
        assert task.status == AnalysisTask.Status.FAILED

    def test_execute_no_analyzers(self):
        task = AnalysisTaskFactory(
            source_code={"backend": "print('hi')", "frontend": ""},
            configuration={"analyzers": [], "settings": {}},
        )

        service = AnalysisService()
        service.execute(task)

        task.refresh_from_db()
        assert task.status == AnalysisTask.Status.FAILED
        assert "No analyzers" in task.error_message

    @patch(
        "llm_lab.analysis.services.result_service.AnalyzerRegistry.get_instance",
    )
    def test_execute_unknown_analyzer(self, mock_get_instance):
        task = AnalysisTaskFactory(
            source_code={"backend": "print('hi')", "frontend": ""},
            configuration={
                "analyzers": ["unknown_tool", "bandit"],
                "settings": {},
            },
        )

        bandit_output = _make_success_output()

        def side_effect(name):
            if name == "bandit":
                return _mock_analyzer("bandit", "static", bandit_output)
            return None

        mock_get_instance.side_effect = side_effect

        service = AnalysisService()
        service.execute(task)

        task.refresh_from_db()
        assert task.status == AnalysisTask.Status.PARTIAL

        results = AnalysisResult.objects.filter(task=task)
        skipped = results.filter(status=AnalysisResult.Status.SKIPPED)
        assert skipped.count() == 1
        assert skipped.first().analyzer_name == "unknown_tool"

    @patch(
        "llm_lab.analysis.services.result_service.AnalyzerRegistry.get_instance",
    )
    def test_aggregate_results(self, mock_get_instance, task):
        findings = [
            _make_finding(severity="critical", title="Critical bug"),
            _make_finding(severity="high", title="High issue"),
            _make_finding(severity="medium", title="Medium issue"),
        ]
        bandit_output = _make_success_output(findings)
        eslint_output = _make_success_output()

        def side_effect(name):
            return {
                "bandit": _mock_analyzer("bandit", "static", bandit_output),
                "eslint": _mock_analyzer("eslint", "static", eslint_output),
            }.get(name)

        mock_get_instance.side_effect = side_effect

        service = AnalysisService()
        service.execute(task)

        task.refresh_from_db()
        summary = task.results_summary

        expected_findings = 3
        assert summary["total_findings"] == expected_findings
        assert summary["by_severity"]["critical"] == 1
        assert summary["by_severity"]["high"] == 1
        assert summary["by_severity"]["medium"] == 1
        expected_completed = 2
        assert summary["analyzers_completed"] == expected_completed
        assert summary["analyzers_failed"] == 0
