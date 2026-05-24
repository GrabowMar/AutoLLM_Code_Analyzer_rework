"""Pylint static analyzer for Python."""

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
from llm_lab.analysis.services.base import ConfigField
from llm_lab.analysis.services.base import FindingData
from llm_lab.analysis.services.base import _extract_code
from llm_lab.analysis.services.base import build_severity_counts
from llm_lab.analysis.services.static._common import PYTHON_EXTENSIONS

logger = logging.getLogger(__name__)


PYLINT_SEVERITY_MAP: dict[str, str] = {
    "C": "info",
    "R": "low",
    "W": "medium",
    "E": "high",
    "F": "critical",
}

PYLINT_CATEGORY_MAP: dict[str, str] = {
    "C": "style",
    "R": "quality",
    "W": "quality",
    "E": "quality",
    "F": "quality",
}


class PylintAnalyzer(BaseAnalyzer):
    """Runs Pylint code quality analysis on Python code."""

    name: ClassVar[str] = "pylint"
    analyzer_type: ClassVar[str] = "static"
    display_name: ClassVar[str] = "Pylint Code Quality Analyzer"
    description: ClassVar[str] = "Checks Python code for errors, style, and quality issues."
    supports_live_target: ClassVar[bool] = False
    supported_code_types: ClassVar[list[str]] = ["backend"]
    config_schema: ClassVar[list[ConfigField]] = [
        ConfigField(
            name="disable",
            type="multiselect",
            label="Disable message codes",
            description="Pylint message codes to suppress (e.g. C0114 for missing module docstring).",
            default=[],
            options=[
                {"value": "C0114", "label": "C0114 — missing-module-docstring"},
                {"value": "C0115", "label": "C0115 — missing-class-docstring"},
                {"value": "C0116", "label": "C0116 — missing-function-docstring"},
                {"value": "R0903", "label": "R0903 — too-few-public-methods"},
                {"value": "W0611", "label": "W0611 — unused-import"},
                {"value": "W0613", "label": "W0613 — unused-argument"},
            ],
        ),
        ConfigField(
            name="fail_under",
            type="number",
            label="Fail under score",
            description="Fail if the Pylint score is below this value (0-10, 0 to disable).",
            default=0,
            min=0,
            max=10,
        ),
    ]

    def check_available(self) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["pylint", "--version"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError:
            return False, "Pylint is not installed"
        except subprocess.TimeoutExpired:
            return False, "Pylint version check timed out"
        else:
            if result.returncode == 0:
                version = result.stdout.strip().split("\n")[0]
                return True, f"Pylint {version}"
            return False, "Pylint is not installed"

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
            return AnalyzerOutput(error="Pylint is not installed")

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
                [  # noqa: S607
                    "pylint",
                    "--output-format=json",
                    "--disable=C0114,C0115,C0116",
                    tmp_path,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            return self._parse_output(result)
        except subprocess.TimeoutExpired:
            logger.exception("Pylint analysis timed out")
            return AnalyzerOutput(error="Pylint analysis timed out")
        except Exception:
            logger.exception("Pylint analysis failed")
            return AnalyzerOutput(error="Pylint analysis failed unexpectedly")
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
            logger.exception("Failed to parse Pylint JSON output")
            return AnalyzerOutput(
                error="Failed to parse Pylint output",
                raw_output={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )

        findings: list[FindingData] = []
        for msg in data:
            msg_type = msg.get("type", "W")[0].upper()
            severity = PYLINT_SEVERITY_MAP.get(msg_type, "medium")
            category = PYLINT_CATEGORY_MAP.get(msg_type, "quality")
            msg_id = msg.get("message-id", "")
            symbol = msg.get("symbol", "")

            findings.append(
                FindingData(
                    severity=severity,
                    category=category,
                    title=msg.get("message", "Pylint issue"),
                    description=msg.get("message", ""),
                    suggestion=f"See Pylint message: {msg_id} ({symbol})",
                    file_path=msg.get("path", ""),
                    line_number=msg.get("line"),
                    column_number=msg.get("column"),
                    code_snippet="",
                    rule_id=msg_id,
                    confidence=("high" if msg_type in ("E", "F") else "medium"),
                    tool_specific_data={
                        "symbol": msg.get("symbol", ""),
                        "module": msg.get("module", ""),
                        "obj": msg.get("obj", ""),
                    },
                ),
            )

        severity_counts = build_severity_counts(findings)
        category_counts: dict[str, int] = {}
        for f in findings:
            category_counts[f.category] = category_counts.get(f.category, 0) + 1

        return AnalyzerOutput(
            findings=findings,
            summary={
                "total_issues": len(findings),
                "by_severity": severity_counts,
                "by_category": category_counts,
            },
            raw_output=data,
        )
