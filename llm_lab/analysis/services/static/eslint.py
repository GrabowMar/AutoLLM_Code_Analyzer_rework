"""ESLint static analyzer for JavaScript/TypeScript."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from typing import ClassVar

from llm_lab.analysis.services.base import AnalyzerOutput
from llm_lab.analysis.services.base import BaseAnalyzer
from llm_lab.analysis.services.base import FindingData
from llm_lab.analysis.services.base import _extract_code
from llm_lab.analysis.services.base import build_severity_counts
from llm_lab.analysis.services.static._common import JS_TS_EXTENSIONS

logger = logging.getLogger(__name__)


ESLINT_SEVERITY_MAP: dict[int, str] = {
    1: "low",
    2: "high",
}


class ESLintAnalyzer(BaseAnalyzer):
    """Runs ESLint on JavaScript/TypeScript code."""

    name: ClassVar[str] = "eslint"
    analyzer_type: ClassVar[str] = "static"
    display_name: ClassVar[str] = "ESLint Code Analyzer"
    description: ClassVar[str] = "Finds problems in JavaScript and TypeScript code."

    def check_available(self) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["npx", "eslint", "--version"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError:
            return False, "ESLint is not installed"
        except subprocess.TimeoutExpired:
            return False, "ESLint version check timed out"
        else:
            if result.returncode == 0:
                version = result.stdout.strip()
                return True, f"ESLint {version}"
            return False, "ESLint is not installed"

    def analyze(
        self,
        code: dict[str, str],
        config: dict[str, Any] | None = None,
    ) -> AnalyzerOutput:
        source = _extract_code(code, "frontend", JS_TS_EXTENSIONS)
        if not source.strip():
            return AnalyzerOutput(
                summary={
                    "message": "No JavaScript/TypeScript code to analyze",
                },
            )

        available, _message = self.check_available()
        if not available:
            return AnalyzerOutput(error="ESLint is not installed")

        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".js",
                delete=False,
            ) as tmp:
                tmp.write(source)
                tmp_path = tmp.name

            rules = json.dumps(
                {
                    "no-unused-vars": "warn",
                    "no-undef": "error",
                    "no-eval": "error",
                    "no-implied-eval": "error",
                    "no-new-func": "error",
                },
            )
            result = subprocess.run(  # noqa: S603
                [  # noqa: S607
                    "npx",
                    "eslint",
                    "--format",
                    "json",
                    tmp_path,
                    "--no-eslintrc",
                    "--rule",
                    rules,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            return self._parse_output(result)
        except subprocess.TimeoutExpired:
            logger.exception("ESLint analysis timed out")
            return AnalyzerOutput(error="ESLint analysis timed out")
        except Exception:
            logger.exception("ESLint analysis failed")
            return AnalyzerOutput(error="ESLint analysis failed unexpectedly")
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    def _parse_output(
        self,
        result: subprocess.CompletedProcess[str],
    ) -> AnalyzerOutput:
        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            logger.exception("Failed to parse ESLint JSON output")
            return AnalyzerOutput(
                error="Failed to parse ESLint output",
                raw_output={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )

        security_rules = {
            "no-eval",
            "no-implied-eval",
            "no-new-func",
        }
        findings: list[FindingData] = []
        for file_result in data:
            file_path = file_result.get("filePath", "")
            for msg in file_result.get("messages", []):
                raw_severity = msg.get("severity", 1)
                severity = ESLINT_SEVERITY_MAP.get(raw_severity, "medium")

                rule_id = msg.get("ruleId") or ""
                category = "security" if rule_id in security_rules else "quality"

                suggestion = f"See ESLint rule: {rule_id}" if rule_id else ""
                findings.append(
                    FindingData(
                        severity=severity,
                        category=category,
                        title=msg.get("message", "ESLint issue"),
                        description=msg.get("message", ""),
                        suggestion=suggestion,
                        file_path=file_path,
                        line_number=msg.get("line"),
                        column_number=msg.get("column"),
                        code_snippet=msg.get("source", ""),
                        rule_id=rule_id,
                        confidence="high",
                        tool_specific_data={
                            "node_type": msg.get("nodeType", ""),
                            "fatal": msg.get("fatal", False),
                        },
                    ),
                )

        severity_counts = build_severity_counts(findings)

        return AnalyzerOutput(
            findings=findings,
            summary={
                "total_issues": len(findings),
                "by_severity": severity_counts,
            },
            raw_output=data,
        )
