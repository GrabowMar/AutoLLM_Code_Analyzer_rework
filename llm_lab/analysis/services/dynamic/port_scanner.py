"""Port scanner analyzer."""

from __future__ import annotations

import logging
import re
import socket
from typing import Any
from typing import ClassVar

from llm_lab.analysis.services.base import AnalyzerOutput
from llm_lab.analysis.services.base import BaseAnalyzer
from llm_lab.analysis.services.base import FindingData
from llm_lab.analysis.services.base import build_severity_counts
from llm_lab.analysis.services.dynamic._common import _slug

logger = logging.getLogger(__name__)

DANGEROUS_PORT_MAP: dict[int, tuple[str, str]] = {
    21: ("high", "FTP — unencrypted file transfer"),
    22: ("medium", "SSH — secure shell access"),
    23: ("high", "Telnet — unencrypted remote access"),
    25: ("medium", "SMTP — mail server"),
    53: ("low", "DNS — domain name service"),
    80: ("info", "HTTP — web server"),
    443: ("info", "HTTPS — secure web server"),
    3306: ("high", "MySQL — database exposed"),
    5432: ("high", "PostgreSQL — database exposed"),
    6379: ("high", "Redis — in-memory store exposed"),
    8080: ("low", "HTTP alt — development server"),
    8443: ("low", "HTTPS alt — development server"),
    27017: ("high", "MongoDB — database exposed"),
}

DEFAULT_SCAN_PORTS: list[int] = sorted(DANGEROUS_PORT_MAP.keys())

_PORT_CODE_PATTERNS: list[tuple[str, str, str, str, str]] = [
    (
        r"""0\.0\.0\.0""",
        "Service bound to all interfaces",
        "Binding to 0.0.0.0 exposes the service widely.",
        "Bind to 127.0.0.1 or a specific interface.",
        "medium",
    ),
    (
        r"""(?i)DEBUG\s*=\s*True""",
        "Debug mode enabled",
        "Debug mode may expose diagnostic endpoints.",
        "Disable debug mode in production.",
        "high",
    ),
    (
        r"""(?i)CORS_ALLOW_ALL_ORIGINS\s*=\s*True""",
        "CORS wildcard enabled",
        "All origins are allowed cross-origin requests.",
        "Restrict allowed origins to trusted domains.",
        "medium",
    ),
    (
        r"""(?i)ALLOWED_HOSTS\s*=\s*\[\s*["']\*["']\s*\]""",
        "Wildcard ALLOWED_HOSTS",
        "Any host header is accepted.",
        "Set ALLOWED_HOSTS to specific domain names.",
        "medium",
    ),
    (
        r""":(\d{4,5})(?:[/"'\s]|$)""",
        "Hardcoded port reference",
        "A port number is hardcoded in source code.",
        "Use config or env vars for port numbers.",
        "low",
    ),
]


class PortScanAnalyzer(BaseAnalyzer):
    """Port scanner — scans for open ports or analyzes code for exposed services."""

    name: ClassVar[str] = "port_scanner"
    analyzer_type: ClassVar[str] = "dynamic"
    display_name: ClassVar[str] = "Port Scanner"
    description: ClassVar[str] = "Scans for open ports and identifies potentially dangerous exposed services"

    def check_available(self) -> tuple[bool, str]:
        return True, "Available (uses Python built-in socket module)"

    def analyze(
        self,
        code: dict[str, str],
        config: dict[str, Any] | None = None,
    ) -> AnalyzerOutput:
        config = config or {}
        target_host: str | None = config.get("target_host")
        ports: list[int] = config.get("ports", DEFAULT_SCAN_PORTS)

        if target_host:
            blocked = (
                "localhost",
                "127.0.0.1",
                "0.0.0.0",  # noqa: S104
                "::1",
                "169.254.169.254",
            )
            if target_host.lower() in blocked:
                return AnalyzerOutput(error=f"Blocked host: {target_host}")
            return self._scan_host(target_host, ports)
        return self._analyze_static(code)

    def _scan_host(
        self,
        host: str,
        ports: list[int],
    ) -> AnalyzerOutput:
        findings: list[FindingData] = []
        open_ports: list[int] = []
        high_risk_ports: list[int] = []

        for port in ports:
            if self._is_port_open(host, port):
                open_ports.append(port)
                severity, port_desc = DANGEROUS_PORT_MAP.get(
                    port,
                    ("info", f"Port {port}"),
                )

                if severity in ("high", "critical"):
                    high_risk_ports.append(port)

                findings.append(
                    FindingData(
                        severity=severity,
                        category="security",
                        title=f"Open port {port} — {port_desc}",
                        description=(f"Port {port} is open on {host}: {port_desc}."),
                        suggestion=_port_suggestion(port),
                        rule_id=f"port-{port}",
                        confidence="high",
                        tool_specific_data={
                            "host": host,
                            "port": port,
                            "port_description": port_desc,
                        },
                    ),
                )

        summary: dict[str, Any] = {
            "target_host": host,
            "total_ports_scanned": len(ports),
            "open_ports_count": len(open_ports),
            "open_ports": open_ports,
            "high_risk_ports": high_risk_ports,
        }

        return AnalyzerOutput(
            findings=findings,
            summary=summary,
            raw_output={
                "host": host,
                "ports_scanned": ports,
                "open_ports": open_ports,
            },
        )

    @staticmethod
    def _is_port_open(
        host: str,
        port: int,
        timeout: float = 1.0,
    ) -> bool:
        try:
            with socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM,
            ) as sock:
                sock.settimeout(timeout)
                return sock.connect_ex((host, port)) == 0
        except OSError:
            return False

    def _analyze_static(self, code: dict[str, str]) -> AnalyzerOutput:
        findings: list[FindingData] = []

        for file_key, content in code.items():
            lines = content.splitlines()
            for line_number, line_text in enumerate(lines, start=1):
                for entry in _PORT_CODE_PATTERNS:
                    pat, title, desc, suggestion, sev = entry
                    if re.search(pat, line_text):
                        findings.append(
                            FindingData(
                                severity=sev,
                                category="security",
                                title=title,
                                description=desc,
                                suggestion=suggestion,
                                file_path=file_key,
                                line_number=line_number,
                                code_snippet=line_text.strip(),
                                rule_id=(f"portscan-static-{_slug(title)}"),
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


def _port_suggestion(port: int) -> str:
    suggestions: dict[int, str] = {
        21: "Disable FTP; use SFTP or SCP instead.",
        22: ("Restrict SSH access with key-based auth and firewall rules."),
        23: "Disable Telnet immediately; use SSH.",
        25: ("Restrict SMTP to trusted networks or use a managed email service."),
        53: "Ensure DNS is intentionally exposed and hardened.",
        80: "Consider redirecting HTTP to HTTPS.",
        443: "Ensure TLS certificates are valid and current.",
        3306: ("Do not expose MySQL publicly; use a firewall or SSH tunnel."),
        5432: ("Do not expose PostgreSQL publicly; use a firewall or SSH tunnel."),
        6379: ("Do not expose Redis publicly; enable auth and use a firewall."),
        8080: "Ensure this dev server is not in production.",
        8443: "Ensure this dev server is not in production.",
        27017: ("Do not expose MongoDB publicly; enable auth and use a firewall."),
    }
    return suggestions.get(
        port,
        "Review whether this port needs to be accessible.",
    )
