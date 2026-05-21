"""OWASP ZAP security scanner analyzer."""

from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import subprocess
from typing import Any
from typing import ClassVar

from llm_lab.analysis.services.base import AnalyzerOutput
from llm_lab.analysis.services.base import BaseAnalyzer
from llm_lab.analysis.services.base import FindingData
from llm_lab.analysis.services.base import _safe_int
from llm_lab.analysis.services.base import build_severity_counts
from llm_lab.analysis.services.base import validate_target_url
from llm_lab.analysis.services.dynamic._common import _slug

logger = logging.getLogger(__name__)

ZAP_RISK_TO_SEVERITY: dict[int, str] = {
    0: "info",
    1: "low",
    2: "medium",
    3: "high",
}

_ZAP_CONFIDENCE_HIGH = 3
_ZAP_CONFIDENCE_MEDIUM = 2

_SQL_INJECTION_PAT = (
    r"""(?i)(?:execute|cursor\.execute|raw\()"""
    r"""\s*\(\s*(?:f["']|["']\s*%\s*|["']\s*\.\s*format\s*\()"""
)

_STATIC_VULN_PATTERNS: list[tuple[str, str, str, str, str, str]] = [
    (
        _SQL_INJECTION_PAT,
        "Potential SQL injection",
        "String formatting used directly in a SQL query.",
        "Use parameterized queries or an ORM.",
        "high",
        "security",
    ),
    (
        r"""(?i)(?:innerHTML|outerHTML|document\.write)\s*[=(]""",
        "Potential XSS via DOM manipulation",
        "Direct DOM manipulation with user-controlled data.",
        "Use safe DOM APIs or framework escaping.",
        "high",
        "security",
    ),
    (
        r"""(?i)\{%\s*autoescape\s+off\s*%\}""",
        "Template auto-escaping disabled",
        "Disabling auto-escaping may introduce XSS.",
        "Keep auto-escaping enabled.",
        "high",
        "security",
    ),
    (
        r"""(?i)@csrf_exempt""",
        "CSRF protection disabled",
        "Endpoint is exempt from CSRF protection.",
        "Remove @csrf_exempt or add alternative protection.",
        "medium",
        "security",
    ),
    (
        r"""(?i)(?:api[_-]?key|secret[_-]?key|password|token"""
        r"""|aws[_-]?secret)\s*[:=]\s*["'][A-Za-z0-9+/=_\-]{8,}["']""",
        "Hardcoded secret or credential",
        "A secret value appears hardcoded in source code.",
        "Use environment variables or a secrets manager.",
        "critical",
        "security",
    ),
    (
        r"""(?i)CORS_ALLOW_ALL_ORIGINS\s*=\s*True"""
        r"""|Access-Control-Allow-Origin['":\s]+\*""",
        "Permissive CORS configuration",
        "Wildcard CORS allows any origin.",
        "Restrict CORS to specific trusted origins.",
        "medium",
        "security",
    ),
    (
        r"""(?i)DEBUG\s*=\s*True""",
        "Debug mode enabled",
        "Debug mode exposes sensitive error details.",
        "Ensure DEBUG is False in production.",
        "high",
        "security",
    ),
    (
        r"""(?i)(?:eval|exec)\s*\(""",
        "Use of eval/exec",
        "Dynamic code execution can introduce injection.",
        "Avoid eval/exec; use safer alternatives.",
        "high",
        "security",
    ),
    (
        r"""(?i)verify\s*=\s*False""",
        "SSL verification disabled",
        "Disabling SSL verification enables MITM attacks.",
        "Enable SSL certificate verification.",
        "medium",
        "security",
    ),
    (
        r"""(?i)pickle\.loads?\(""",
        "Insecure deserialization",
        "Pickle deserialization can execute arbitrary code.",
        "Use safe serialization formats like JSON.",
        "high",
        "security",
    ),
]


class ZAPAnalyzer(BaseAnalyzer):
    """OWASP ZAP security scanner — runs ZAP baseline scan or static code analysis."""

    name: ClassVar[str] = "zap"
    analyzer_type: ClassVar[str] = "dynamic"
    display_name: ClassVar[str] = "OWASP ZAP Security Scanner"
    description: ClassVar[str] = "Scans web applications for security vulnerabilities using OWASP ZAP baseline scan"

    def check_available(self) -> tuple[bool, str]:
        if shutil.which("docker") is None:
            return False, "Docker is not installed or not on PATH"
        try:
            result = subprocess.run(
                ["docker", "info"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return False, "Docker daemon is not running"
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, f"Cannot reach Docker daemon: {exc}"
        return True, "Docker is available"

    def analyze(
        self,
        code: dict[str, str],
        config: dict[str, Any] | None = None,
    ) -> AnalyzerOutput:
        config = config or {}
        target_url: str | None = config.get("target_url")
        is_live_task: bool = bool(config.get("live_target"))

        if target_url:
            return self._analyze_live(target_url, is_live_task=is_live_task)
        return self._analyze_static(code)

    def _analyze_live(
        self,
        target_url: str,
        *,
        is_live_task: bool = False,
    ) -> AnalyzerOutput:
        available, msg = self.check_available()
        if not available:
            return AnalyzerOutput(error=f"ZAP unavailable: {msg}")

        if is_live_task:
            from llm_lab.analysis.services.live_target import validate_live_target_url

            valid, err = validate_live_target_url(target_url)
        else:
            valid, err = validate_target_url(target_url)
        if not valid:
            return AnalyzerOutput(error=f"Invalid target URL: {err}")

        cmd = [
            "docker",
            "run",
            "--rm",
            "-t",
            "ghcr.io/zaproxy/zaproxy:stable",
            "zap-baseline.py",
            "-t",
            target_url,
            "-J",
            "report.json",
        ]

        try:
            result = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return AnalyzerOutput(
                error="ZAP scan timed out after 120 seconds",
            )
        except OSError as exc:
            return AnalyzerOutput(error=f"Failed to start ZAP: {exc}")

        return self._parse_zap_output(result)

    def _parse_zap_output(
        self,
        result: subprocess.CompletedProcess[str],
    ) -> AnalyzerOutput:
        findings: list[FindingData] = []
        raw: dict[str, Any] = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

        report: dict[str, Any] = {}
        for raw_line in result.stdout.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("{"):
                try:
                    report = json.loads(stripped)
                    break
                except json.JSONDecodeError:
                    continue

        if not report:
            with contextlib.suppress(json.JSONDecodeError, ValueError):
                report = json.loads(result.stdout)

        raw["report"] = report

        alert_counts: dict[str, int] = {
            "info": 0,
            "low": 0,
            "medium": 0,
            "high": 0,
        }

        for site in report.get("site", []):
            for alert in site.get("alerts", []):
                try:
                    risk_code = _safe_int(alert.get("riskcode", 0))
                    severity = ZAP_RISK_TO_SEVERITY.get(
                        risk_code,
                        "info",
                    )
                    alert_counts[severity] += 1

                    instances = alert.get("instances") or []
                    urls = [inst.get("uri", "") for inst in instances]

                    findings.append(
                        FindingData(
                            severity=severity,
                            category="security",
                            title=alert.get("name", "ZAP Alert"),
                            description=alert.get("desc", ""),
                            suggestion=alert.get("solution", ""),
                            rule_id=str(alert.get("pluginid", "")),
                            confidence=_zap_confidence(
                                _safe_int(alert.get("confidence", 1), default=1),
                            ),
                            tool_specific_data={
                                "risk_code": risk_code,
                                "cweid": alert.get("cweid", ""),
                                "wascid": alert.get("wascid", ""),
                                "reference": alert.get(
                                    "reference",
                                    "",
                                ),
                                "urls": urls,
                                "count": alert.get(
                                    "count",
                                    len(instances),
                                ),
                            },
                        ),
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Skipping malformed ZAP alert: %s",
                        alert.get("name", "<unknown>"),
                        exc_info=True,
                    )

        total_urls = sum(len(site.get("alerts", [])) for site in report.get("site", []))

        summary: dict[str, Any] = {
            "alert_counts": alert_counts,
            "total_alerts": len(findings),
            "total_urls_scanned": total_urls,
        }

        return AnalyzerOutput(
            findings=findings,
            summary=summary,
            raw_output=raw,
        )

    def _analyze_static(self, code: dict[str, str]) -> AnalyzerOutput:
        findings: list[FindingData] = []

        for file_key, content in code.items():
            lines = content.splitlines()
            for line_number, line_text in enumerate(lines, start=1):
                for entry in _STATIC_VULN_PATTERNS:
                    pat, title, desc, suggestion, sev, cat = entry
                    if re.search(pat, line_text):
                        findings.append(
                            FindingData(
                                severity=sev,
                                category=cat,
                                title=title,
                                description=desc,
                                suggestion=suggestion,
                                file_path=file_key,
                                line_number=line_number,
                                code_snippet=line_text.strip(),
                                rule_id=f"zap-static-{_slug(title)}",
                                confidence="medium",
                            ),
                        )

        counts = build_severity_counts(findings)

        summary: dict[str, Any] = {
            "mode": "static",
            "alert_counts": counts,
            "total_alerts": len(findings),
            "files_scanned": len(code),
        }

        return AnalyzerOutput(
            findings=findings,
            summary=summary,
            raw_output={"mode": "static"},
        )


def _zap_confidence(level: int) -> str:
    if level >= _ZAP_CONFIDENCE_HIGH:
        return "high"
    if level == _ZAP_CONFIDENCE_MEDIUM:
        return "medium"
    return "low"
