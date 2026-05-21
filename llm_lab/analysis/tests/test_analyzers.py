from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock
from unittest.mock import patch

from llm_lab.analysis.services.ai_analyzers import LLMReviewAnalyzer
from llm_lab.analysis.services.base import AnalyzerRegistry
from llm_lab.analysis.services.static_analyzers import BanditAnalyzer
from llm_lab.analysis.services.static_analyzers import ESLintAnalyzer
from llm_lab.analysis.services.static_analyzers import PylintAnalyzer
from llm_lab.generation.services.openrouter_client import OpenRouterError

# ── Bandit ────────────────────────────────────────────────────────────


class TestBanditAnalyzer:
    def setup_method(self):
        self.analyzer = BanditAnalyzer()
        self.code = {"backend": "import os\nos.system('ls')\n", "frontend": ""}

    @patch("llm_lab.analysis.services.static_analyzers.subprocess.run")
    def test_analyze_clean_code(self, mock_run):
        # First call: check_available (--version)
        version_result = subprocess.CompletedProcess(
            args=["bandit", "--version"],
            returncode=0,
            stdout="bandit 1.7.5\n",
            stderr="",
        )
        # Second call: actual analysis
        analysis_result = subprocess.CompletedProcess(
            args=["bandit", "-f", "json", "-r", "file.py"],
            returncode=0,
            stdout=json.dumps({"results": [], "metrics": {}}),
            stderr="",
        )
        mock_run.side_effect = [version_result, analysis_result]

        output = self.analyzer.analyze(self.code)

        assert output.error is None
        assert len(output.findings) == 0
        assert output.summary["total_issues"] == 0

    @patch("llm_lab.analysis.services.static_analyzers.subprocess.run")
    def test_analyze_with_findings(self, mock_run):
        bandit_results = {
            "results": [
                {
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                    "issue_text": "Use of os.system detected",
                    "test_id": "B605",
                    "test_name": "start_process_with_a_shell",
                    "filename": "app.py",
                    "line_number": 2,
                    "col_offset": 0,
                    "code": "os.system('ls')\n",
                    "line_range": [2],
                    "more_info": "https://bandit.readthedocs.io/en/latest/",
                },
            ],
            "metrics": {},
        }
        version_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="bandit 1.7.5\n",
            stderr="",
        )
        analysis_result = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=json.dumps(bandit_results),
            stderr="",
        )
        mock_run.side_effect = [version_result, analysis_result]

        output = self.analyzer.analyze(self.code)

        assert output.error is None
        assert len(output.findings) == 1
        finding = output.findings[0]
        assert finding.severity == "high"
        assert finding.category == "security"
        assert finding.rule_id == "B605"
        assert finding.confidence == "high"
        expected_line = 2
        assert finding.line_number == expected_line

    @patch("llm_lab.analysis.services.static_analyzers.subprocess.run")
    def test_tool_not_installed(self, mock_run):
        mock_run.side_effect = FileNotFoundError("bandit not found")

        output = self.analyzer.analyze(self.code)

        assert output.error is not None
        assert "not installed" in output.error

    def test_no_python_code(self):
        output = self.analyzer.analyze({"backend": "", "frontend": "alert('hi')"})
        assert output.error is None
        assert len(output.findings) == 0
        assert output.summary.get("message") == "No Python code to analyze"


# ── ESLint ────────────────────────────────────────────────────────────


class TestESLintAnalyzer:
    def setup_method(self):
        self.analyzer = ESLintAnalyzer()
        self.code = {
            "backend": "",
            "frontend": "var x = 1;\nconsole.log(y);\n",
        }

    @patch("llm_lab.analysis.services.static_analyzers.subprocess.run")
    def test_analyze_clean_code(self, mock_run):
        version_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="v8.50.0\n",
            stderr="",
        )
        analysis_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps([{"filePath": "file.js", "messages": []}]),
            stderr="",
        )
        mock_run.side_effect = [version_result, analysis_result]

        output = self.analyzer.analyze(self.code)

        assert output.error is None
        assert len(output.findings) == 0

    @patch("llm_lab.analysis.services.static_analyzers.subprocess.run")
    def test_analyze_with_findings(self, mock_run):
        eslint_output = [
            {
                "filePath": "/code/app.js",
                "messages": [
                    {
                        "ruleId": "no-unused-vars",
                        "severity": 1,
                        "message": "'x' is defined but never used",
                        "line": 1,
                        "column": 5,
                        "source": "var x = 1;",
                        "nodeType": "Identifier",
                        "fatal": False,
                    },
                    {
                        "ruleId": "no-undef",
                        "severity": 2,
                        "message": "'y' is not defined",
                        "line": 2,
                        "column": 13,
                        "source": "console.log(y);",
                        "nodeType": "Identifier",
                        "fatal": False,
                    },
                ],
            },
        ]
        version_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="v8.50.0\n",
            stderr="",
        )
        analysis_result = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=json.dumps(eslint_output),
            stderr="",
        )
        mock_run.side_effect = [version_result, analysis_result]

        output = self.analyzer.analyze(self.code)

        assert output.error is None
        expected_findings = 2
        assert len(output.findings) == expected_findings
        assert output.findings[0].severity == "low"
        assert output.findings[0].rule_id == "no-unused-vars"
        assert output.findings[1].severity == "high"
        assert output.findings[1].rule_id == "no-undef"

    @patch("llm_lab.analysis.services.static_analyzers.subprocess.run")
    def test_tool_not_installed(self, mock_run):
        mock_run.side_effect = FileNotFoundError("npx not found")

        output = self.analyzer.analyze(self.code)

        assert output.error is not None
        assert "not installed" in output.error

    def test_no_frontend_code(self):
        output = self.analyzer.analyze({"backend": "print('hi')", "frontend": ""})
        assert output.error is None
        assert len(output.findings) == 0


# ── Pylint ────────────────────────────────────────────────────────────


class TestPylintAnalyzer:
    def setup_method(self):
        self.analyzer = PylintAnalyzer()
        self.code = {"backend": "import os\nx = 1\n", "frontend": ""}

    @patch("llm_lab.analysis.services.static_analyzers.subprocess.run")
    def test_analyze_clean_code(self, mock_run):
        version_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pylint 3.0.3\n",
            stderr="",
        )
        analysis_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps([]),
            stderr="",
        )
        mock_run.side_effect = [version_result, analysis_result]

        output = self.analyzer.analyze(self.code)

        assert output.error is None
        assert len(output.findings) == 0

    @patch("llm_lab.analysis.services.static_analyzers.subprocess.run")
    def test_analyze_with_findings(self, mock_run):
        pylint_output = [
            {
                "type": "error",
                "module": "app",
                "obj": "",
                "line": 5,
                "column": 0,
                "path": "app.py",
                "symbol": "undefined-variable",
                "message": "Undefined variable 'foo'",
                "message-id": "E0602",
            },
            {
                "type": "warning",
                "module": "app",
                "obj": "",
                "line": 1,
                "column": 0,
                "path": "app.py",
                "symbol": "unused-import",
                "message": "Unused import os",
                "message-id": "W0611",
            },
        ]
        version_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pylint 3.0.3\n",
            stderr="",
        )
        analysis_result = subprocess.CompletedProcess(
            args=[],
            returncode=4,
            stdout=json.dumps(pylint_output),
            stderr="",
        )
        mock_run.side_effect = [version_result, analysis_result]

        output = self.analyzer.analyze(self.code)

        assert output.error is None
        expected_findings = 2
        assert len(output.findings) == expected_findings
        assert output.findings[0].severity == "high"
        assert output.findings[0].rule_id == "E0602"
        assert output.findings[1].severity == "medium"
        assert output.findings[1].rule_id == "W0611"

    @patch("llm_lab.analysis.services.static_analyzers.subprocess.run")
    def test_tool_not_installed(self, mock_run):
        mock_run.side_effect = FileNotFoundError("pylint not found")

        output = self.analyzer.analyze(self.code)

        assert output.error is not None
        assert "not installed" in output.error

    def test_no_python_code(self):
        output = self.analyzer.analyze({"backend": "   ", "frontend": ""})
        assert output.error is None
        assert len(output.findings) == 0


# ── LLM Review ────────────────────────────────────────────────────────


class TestLLMReviewAnalyzer:
    def _get_analyzer(self):
        return LLMReviewAnalyzer()

    @patch("llm_lab.analysis.services.ai_analyzers.OpenRouterClient")
    def test_analyze_returns_findings(self, mock_client_cls, settings):
        settings.OPENROUTER_API_KEY = "test-key"
        llm_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "findings": [
                                    {
                                        "severity": "high",
                                        "category": "security",
                                        "title": "SQL Injection",
                                        "description": "User input not sanitized",
                                        "suggestion": "Use parameterized queries",
                                        "file_path": "app.py",
                                        "line_number": 10,
                                        "code_snippet": "query = f'SELECT ...'",
                                        "rule_id": "AI-SEC-001",
                                        "confidence": "high",
                                    },
                                ],
                                "summary": {
                                    "overall_quality": 6.0,
                                    "security_score": 3.0,
                                },
                            },
                        ),
                    },
                },
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 200,
            },
        }

        mock_client = MagicMock()
        mock_client.chat_completion.return_value = llm_response
        mock_client.extract_content.return_value = llm_response["choices"][0]["message"]["content"]
        mock_client.extract_usage.return_value = llm_response["usage"]
        mock_client_cls.return_value = mock_client

        analyzer = self._get_analyzer()
        code = {"backend": "import sqlite3\nquery = 'SELECT * FROM users'"}
        output = analyzer.analyze(code)

        assert output.error is None
        assert len(output.findings) == 1
        assert output.findings[0].severity == "high"
        assert output.findings[0].category == "security"
        assert output.findings[0].title == "SQL Injection"

    def test_api_key_not_configured(self, settings):
        settings.OPENROUTER_API_KEY = ""

        analyzer = self._get_analyzer()
        output = analyzer.analyze({"backend": "print('hello')"})

        assert output.error is not None
        assert "API key" in output.error

    @patch("llm_lab.analysis.services.ai_analyzers.OpenRouterClient")
    def test_api_error_handling(self, mock_client_cls, settings):
        settings.OPENROUTER_API_KEY = "test-key"

        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = OpenRouterError(
            "Rate limited",
            429,
        )
        mock_client_cls.return_value = mock_client

        analyzer = self._get_analyzer()
        output = analyzer.analyze({"backend": "print('hello')"})

        assert output.error is not None
        assert "AI review failed" in output.error


# ── Registry ──────────────────────────────────────────────────────────


class TestAnalyzerRegistry:
    def test_list_available(self):
        available = AnalyzerRegistry.list_available()
        names = [a["name"] for a in available]
        assert "bandit" in names
        assert "eslint" in names
        assert "pylint" in names
        assert "llm_review" in names

    def test_get_instance(self):
        instance = AnalyzerRegistry.get_instance("bandit")
        assert instance is not None
        assert instance.name == "bandit"

    def test_get_nonexistent(self):
        result = AnalyzerRegistry.get_instance("nonexistent_analyzer")
        assert result is None
