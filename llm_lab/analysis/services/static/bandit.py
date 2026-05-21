"""Bandit security scanner analyzer."""

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
from llm_lab.analysis.services.static._common import PYTHON_EXTENSIONS

logger = logging.getLogger(__name__)


BANDIT_SEVERITY_MAP: dict[str, str] = {
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}

BANDIT_CONFIDENCE_MAP: dict[str, str] = {
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}


class BanditAnalyzer(BaseAnalyzer):
    """Runs Bandit security scanner on Python code."""

    name: ClassVar[str] = "bandit"
    analyzer_type: ClassVar[str] = "static"
    display_name: ClassVar[str] = "Bandit Security Scanner"
    description: ClassVar[str] = "Finds common security issues in Python code."

    def check_available(self) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["bandit", "--version"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError:
            return False, "Bandit is not installed"
        except subprocess.TimeoutExpired:
            return False, "Bandit version check timed out"
        else:
            if result.returncode == 0:
                version = result.stdout.strip().split("\n")[0]
                return True, f"Bandit {version}"
            return False, "Bandit is not installed"

    def analyze(
        self,
        code: dict[str, str],
        config: dict[str, Any] | None = None,
    ) -> AnalyzerOutput:
        source = _extract_code(code, "backend", PYTHON_EXTENSIONS)
        if not source.strip():
            return AnalyzerOutput(
                summary={"message": "No Python code to analyze"},
            )

        available, _message = self.check_available()
        if not available:
            return AnalyzerOutput(error="Bandit is not installed")

        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
            ) as tmp:
                tmp.write(source)
                tmp_path = tmp.name

            result = subprocess.run(  # noqa: S603
                ["bandit", "-f", "json", "-r", tmp_path],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            return self._parse_output(result)
        except subprocess.TimeoutExpired:
            logger.exception("Bandit analysis timed out")
            return AnalyzerOutput(error="Bandit analysis timed out")
        except Exception:
            logger.exception("Bandit analysis failed")
            return AnalyzerOutput(error="Bandit analysis failed unexpectedly")
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
            logger.exception("Failed to parse Bandit JSON output")
            return AnalyzerOutput(
                error="Failed to parse Bandit output",
                raw_output={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )

        findings: list[FindingData] = []
        for issue in data.get("results", []):
            sev = issue.get("issue_severity", "")
            conf = issue.get("issue_confidence", "")
            test_id = issue.get("test_id", "")
            findings.append(
                FindingData(
                    severity=BANDIT_SEVERITY_MAP.get(sev, "medium"),
                    category="security",
                    title=issue.get("issue_text", "Security issue"),
                    description=issue.get("issue_text", ""),
                    suggestion=f"See Bandit docs for {test_id or 'this issue'}.",
                    file_path=issue.get("filename", ""),
                    line_number=issue.get("line_number"),
                    column_number=issue.get("col_offset"),
                    code_snippet=issue.get("code", ""),
                    rule_id=test_id,
                    confidence=BANDIT_CONFIDENCE_MAP.get(conf, "medium"),
                    tool_specific_data={
                        "test_name": issue.get("test_name", ""),
                        "line_range": issue.get("line_range", []),
                        "more_info": issue.get("more_info", ""),
                    },
                ),
            )

        severity_counts = build_severity_counts(findings)
        confidence_counts = {"high": 0, "medium": 0, "low": 0}
        for f in findings:
            if f.confidence in confidence_counts:
                confidence_counts[f.confidence] += 1

        return AnalyzerOutput(
            findings=findings,
            summary={
                "total_issues": len(findings),
                "by_severity": severity_counts,
                "by_confidence": confidence_counts,
            },
            raw_output=data,
        )
