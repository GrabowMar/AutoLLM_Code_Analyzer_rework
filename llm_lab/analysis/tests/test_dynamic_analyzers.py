"""Tests for dynamic analyzers — ZAP and PortScanner."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from llm_lab.analysis.services.base import _safe_int
from llm_lab.analysis.services.base import build_severity_counts
from llm_lab.analysis.services.dynamic_analyzers import PortScanAnalyzer
from llm_lab.analysis.services.dynamic_analyzers import ZAPAnalyzer
from llm_lab.analysis.services.dynamic_analyzers import _slug
from llm_lab.analysis.services.dynamic_analyzers import _zap_confidence

EXPECTED_SAFE_INT_5 = 5
EXPECTED_SAFE_INT_42 = 42
EXPECTED_SAFE_INT_DEFAULT = 99
EXPECTED_ZAP_ALERT_COUNT = 2
EXPECTED_FILES_SCANNED = 3
EXPECTED_OPEN_PORTS = 2
PORT_HTTP = 80
PORT_HTTPS = 443


# -- Helper functions --------------------------------------------------


class TestSafeInt:
    def test_safe_int_valid(self):
        assert _safe_int(5) == EXPECTED_SAFE_INT_5
        assert _safe_int("42") == EXPECTED_SAFE_INT_42
        assert _safe_int(0) == 0

    def test_safe_int_invalid(self):
        assert _safe_int("abc") == 0
        assert _safe_int(None) == 0
        assert _safe_int("abc", default=99) == EXPECTED_SAFE_INT_DEFAULT


class TestBuildSeverityCounts:
    def test_empty_severity_counts(self):
        counts = build_severity_counts([])
        assert counts == {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "info": 0,
        }
        # Ensure it returns a new dict each time
        assert build_severity_counts([]) is not counts


class TestSlug:
    def test_slug(self):
        assert _slug("Potential SQL injection") == "potential-sql-injection"
        assert _slug("Use of eval/exec") == "use-of-eval-exec"
        assert _slug("  Hello World  ") == "hello-world"
        assert _slug("UPPER--CASE") == "upper-case"


class TestZapConfidence:
    def test_zap_confidence(self):
        assert _zap_confidence(3) == "high"
        assert _zap_confidence(4) == "high"
        assert _zap_confidence(2) == "medium"
        assert _zap_confidence(1) == "low"
        assert _zap_confidence(0) == "low"


# -- ZAPAnalyzer -------------------------------------------------------


class TestZAPAnalyzerStatic:
    def test_zap_static_analysis_finds_sql_injection(self):
        analyzer = ZAPAnalyzer()
        code = {
            "app.py": 'cursor.execute(f"SELECT * FROM users WHERE id={uid}")',
        }
        output = analyzer.analyze(code)

        assert not output.has_error
        assert len(output.findings) >= 1
        titles = [f.title for f in output.findings]
        assert any("SQL injection" in t for t in titles)

    def test_zap_static_analysis_finds_eval(self):
        analyzer = ZAPAnalyzer()
        code = {"script.py": "result = eval(user_input)"}
        output = analyzer.analyze(code)

        assert not output.has_error
        assert len(output.findings) >= 1
        titles = [f.title for f in output.findings]
        assert any("eval" in t.lower() for t in titles)

    def test_zap_static_analysis_clean_code(self):
        analyzer = ZAPAnalyzer()
        code = {"app.py": "def hello():\n    return 'world'\n"}
        output = analyzer.analyze(code)

        assert not output.has_error
        assert len(output.findings) == 0
        assert output.summary["total_alerts"] == 0

    def test_zap_static_analysis_multiple_files(self):
        analyzer = ZAPAnalyzer()
        code = {
            "clean.py": "x = 1 + 2",
            "vuln.py": "eval(data)",
            "another.py": "print('safe')",
        }
        output = analyzer.analyze(code)

        assert not output.has_error
        assert output.summary["files_scanned"] == EXPECTED_FILES_SCANNED
        assert len(output.findings) >= 1
        vuln_findings = [f for f in output.findings if f.file_path == "vuln.py"]
        assert len(vuln_findings) >= 1


class TestZAPAnalyzerLive:
    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=0,
            stdout="",
            stderr="",
        ),
    )
    def test_zap_live_rejects_blocked_url(self, mock_run, mock_which):
        analyzer = ZAPAnalyzer()
        output = analyzer.analyze(
            {},
            config={"target_url": "http://localhost:8080"},
        )

        assert output.has_error
        assert "Blocked hostname" in output.error or "Invalid target URL" in output.error

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=0,
            stdout="",
            stderr="",
        ),
    )
    def test_zap_live_rejects_invalid_url(self, mock_run, mock_which):
        analyzer = ZAPAnalyzer()
        output = analyzer.analyze(
            {},
            config={"target_url": "ftp://example.com"},
        )

        assert output.has_error
        assert "Invalid" in output.error

    def test_zap_parse_output_with_alerts(self):
        analyzer = ZAPAnalyzer()
        zap_report = {
            "site": [
                {
                    "alerts": [
                        {
                            "name": "Cross-Site Scripting",
                            "riskcode": "3",
                            "confidence": "2",
                            "desc": "XSS found",
                            "solution": "Encode output",
                            "pluginid": "40012",
                            "cweid": "79",
                            "wascid": "8",
                            "reference": "https://owasp.org",
                            "instances": [
                                {"uri": "https://example.com/page"},
                            ],
                        },
                        {
                            "name": "Missing Header",
                            "riskcode": "1",
                            "confidence": "3",
                            "desc": "Header missing",
                            "solution": "Add header",
                            "pluginid": "10020",
                            "instances": [],
                        },
                    ],
                },
            ],
        }
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(zap_report),
            stderr="",
        )
        output = analyzer._parse_zap_output(result)

        assert not output.has_error
        assert len(output.findings) == EXPECTED_ZAP_ALERT_COUNT
        assert output.findings[0].severity == "high"
        assert output.findings[0].title == "Cross-Site Scripting"
        assert output.findings[0].confidence == "medium"
        assert output.findings[1].severity == "low"
        assert output.findings[1].confidence == "high"
        assert output.summary["total_alerts"] == EXPECTED_ZAP_ALERT_COUNT

    @patch("shutil.which", return_value=None)
    def test_zap_check_available_no_docker(self, mock_which):
        analyzer = ZAPAnalyzer()
        available, msg = analyzer.check_available()

        assert available is False
        assert "Docker" in msg


# -- PortScanAnalyzer --------------------------------------------------


class TestPortScanAnalyzerStatic:
    def test_port_scan_static_finds_bind_all(self):
        analyzer = PortScanAnalyzer()
        code = {"settings.py": "HOST = '0.0.0.0'"}
        output = analyzer.analyze(code)

        assert not output.has_error
        assert len(output.findings) >= 1
        titles = [f.title for f in output.findings]
        assert any("bound to all interfaces" in t.lower() for t in titles)

    def test_port_scan_static_finds_debug_mode(self):
        analyzer = PortScanAnalyzer()
        code = {"settings.py": "DEBUG = True"}
        output = analyzer.analyze(code)

        assert not output.has_error
        assert len(output.findings) >= 1
        titles = [f.title for f in output.findings]
        assert any("debug" in t.lower() for t in titles)

    def test_port_scan_static_clean_code(self):
        analyzer = PortScanAnalyzer()
        code = {
            "app.py": "def greet(name):\n    return f'Hello {name}'\n",
        }
        output = analyzer.analyze(code)

        assert not output.has_error
        assert len(output.findings) == 0


class TestPortScanAnalyzerHost:
    def test_port_scan_host_blocked(self):
        analyzer = PortScanAnalyzer()
        output = analyzer.analyze(
            {},
            config={"target_host": "localhost"},
        )

        assert output.has_error
        assert "Blocked host" in output.error

    @patch.object(PortScanAnalyzer, "_is_port_open")
    def test_port_scan_host_scan(self, mock_is_open):
        mock_is_open.side_effect = lambda host, port: port in (PORT_HTTP, PORT_HTTPS)

        analyzer = PortScanAnalyzer()
        output = analyzer.analyze(
            {},
            config={
                "target_host": "example.com",
                "ports": [22, 80, 443, 3306],
            },
        )

        assert not output.has_error
        assert output.summary["open_ports_count"] == EXPECTED_OPEN_PORTS
        assert PORT_HTTP in output.summary["open_ports"]
        assert PORT_HTTPS in output.summary["open_ports"]
        assert len(output.findings) == EXPECTED_OPEN_PORTS

    def test_port_scan_check_available(self):
        analyzer = PortScanAnalyzer()
        available, msg = analyzer.check_available()

        assert available is True
        assert "socket" in msg.lower() or "Available" in msg
