"""Parsers for the DAST scanners and Trivy / OSV-Scanner / Retire.js / djLint.

Fixtures are trimmed from real runs of the pinned tool versions against a
generated Flask + React sample app.
"""

from __future__ import annotations

import json

import pytest

from backend.analysis.services import parsers


def _parse(key: str, raw: str):
    return parsers.parse(key, raw).findings


def test_zap_collapses_systemic_alerts_and_strips_html():
    report = {
        "@programName": "ZAP",
        "site": [
            {
                "@name": "http://172.17.0.2:8000",
                "alerts": [
                    {
                        "pluginid": "10038",
                        "name": "Content Security Policy (CSP) Header Not Set",
                        "riskcode": "2",
                        "confidence": "3",
                        "desc": "<p>CSP is an added layer &amp; more.</p>",
                        "solution": "<p>Set the header.</p>",
                        "instances": [
                            {"uri": "http://172.17.0.2:8000/", "method": "GET", "param": "", "evidence": ""},
                            {"uri": "http://172.17.0.2:8000/login", "method": "GET", "param": "", "evidence": ""},
                        ],
                        "count": "2",
                        "systemic": True,
                        "cweid": "693",
                    },
                    {
                        "pluginid": "40018",
                        "name": "SQL Injection",
                        "riskcode": "3",
                        "confidence": "2",
                        "desc": "<p>SQLi</p>",
                        "solution": "",
                        "instances": [
                            {"uri": "http://172.17.0.2:8000/api/items?q=1", "method": "GET", "param": "q"},
                            {"uri": "http://172.17.0.2:8000/api/items?q=1", "method": "POST", "param": "q"},
                        ],
                        "count": "2",
                        "systemic": False,
                        "cweid": "89",
                    },
                    {"pluginid": "1", "name": "Marked FP", "riskcode": "3", "confidence": "0", "instances": []},
                ],
            },
        ],
    }
    findings = _parse("zap", json.dumps(report))
    assert [(f.title, f.severity, f.file_path) for f in findings] == [
        ("Content Security Policy (CSP) Header Not Set", "medium", "/"),
        ("SQL Injection", "high", "/api/items"),
    ]
    csp, sqli = findings
    assert csp.description == "CSP is an added layer & more."
    assert csp.tool_specific_data["urls"] == ["http://172.17.0.2:8000/", "http://172.17.0.2:8000/login"]
    assert csp.tool_specific_data["cwe"] == "CWE-693"
    assert sqli.tool_specific_data["param"] == "q"
    assert sqli.confidence == "medium"


def test_zap_without_report_is_an_error():
    # The run_cmd prints the report file: no output is no scan, not a clean one.
    parsed = parsers.parse("zap", "")
    assert parsed.findings == []
    assert parsed.error == "ZAP wrote no report"


def test_nuclei_jsonl():
    hits = [
        {
            "template-id": "http-missing-security-headers",
            "info": {
                "name": "HTTP Missing Security Headers",
                "severity": "info",
                "tags": ["misconfig", "headers"],
                "classification": {"cwe-id": ["cwe-693"]},
            },
            "matcher-name": "content-security-policy",
            "matched-at": "http://172.17.0.2:8000",
        },
        {
            "template-id": "git-config",
            "info": {"name": "Git Config Disclosure", "severity": "medium", "reference": "https://example.org"},
            "matched-at": "http://172.17.0.2:8000/.git/config",
            "extracted-results": ["[core]"],
        },
    ]
    raw = "noise\n" + "\n".join(json.dumps(h) for h in hits) + "\n"
    findings = _parse("nuclei", raw)
    assert [(f.title, f.severity, f.file_path) for f in findings] == [
        ("HTTP Missing Security Headers: content-security-policy", "info", "/"),
        ("Git Config Disclosure", "medium", "/.git/config"),
    ]
    assert findings[0].tool_specific_data["cwe"] == "CWE-693"
    assert findings[1].code_snippet == "[core]"
    assert findings[1].tool_specific_data["references"] == ["https://example.org"]


def test_wapiti_vulnerabilities_and_anomalies():
    report = {
        "classifications": {
            "Cross Site Scripting": {
                "desc": "XSS desc",
                "sol": "Escape output",
                "ref": {"CWE-79: Improper Neutralization": "https://cwe.mitre.org/data/definitions/79.html"},
            },
            "Internal Server Error": {"desc": "500", "sol": "Handle errors", "ref": {}},
        },
        "vulnerabilities": {
            "Cross Site Scripting": [
                {
                    "method": "GET",
                    "path": "/search",
                    "info": "XSS via q",
                    "level": 3,
                    "parameter": "q",
                    "module": "xss",
                },
            ],
            "SQL Injection": [],
        },
        "anomalies": {
            "Internal Server Error": [
                {"method": "POST", "path": "/api/items", "info": "500 on fuzz", "level": 3, "module": "exec"},
            ],
        },
        "additionals": {"HTTP Methods": [{"path": "/", "info": "OPTIONS", "level": 0}]},
    }
    findings = _parse("wapiti", json.dumps(report))
    assert [(f.category, f.severity, f.file_path, f.rule_id) for f in findings] == [
        ("security", "high", "/search", "xss"),
        # An anomaly is named after what happened, not after the attack module that provoked it.
        ("reliability", "medium", "/api/items", "internal-server-error"),
    ]
    assert findings[1].tool_specific_data["module"] == "exec"
    assert findings[0].tool_specific_data["module"] == "xss"
    assert findings[0].tool_specific_data["cwe"] == "CWE-79"
    assert findings[0].suggestion == "Escape output"


def test_trivy_vulns_misconfigs_and_secrets():
    report = {
        "Results": [
            {
                "Target": "backend/requirements.txt",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2019-10906",
                        "PkgName": "Jinja2",
                        "InstalledVersion": "2.10",
                        "FixedVersion": "2.10.1",
                        "Severity": "HIGH",
                        "Title": "sandbox escape",
                        "CweIDs": ["CWE-693"],
                    },
                ],
            },
            {
                "Target": "backend/Dockerfile",
                "Misconfigurations": [
                    {
                        "ID": "DS-0001",
                        "Title": "':latest' tag used",
                        "Message": "Specify a tag",
                        "Resolution": "Add a tag",
                        "Severity": "MEDIUM",
                        "Status": "FAIL",
                        "CauseMetadata": {
                            "StartLine": 1,
                            "Code": {"Lines": [{"Number": 1, "Content": "FROM python:latest", "IsCause": True}]},
                        },
                    },
                    {"ID": "DS-0002", "Title": "passed", "Severity": "HIGH", "Status": "PASS"},
                ],
            },
            {
                "Target": "app.py",
                "Secrets": [
                    {
                        "RuleID": "aws-access-key-id",
                        "Title": "AWS Access Key",
                        "Severity": "CRITICAL",
                        "StartLine": 3,
                        "Match": "KEY=****",
                    },
                ],
            },
        ],
    }
    findings = _parse("trivy", json.dumps(report))
    assert [(f.category, f.severity, f.file_path, f.line_number) for f in findings] == [
        ("dependency", "high", "backend/requirements.txt", None),
        ("security", "medium", "backend/Dockerfile", 1),
        ("secrets", "critical", "app.py", 3),
    ]
    assert findings[0].suggestion == "Upgrade Jinja2 to 2.10.1"
    assert findings[1].code_snippet == "FROM python:latest"


def test_osv_scanner_groups_and_dedupes_sources():
    package = {
        "package": {"name": "jinja2", "version": "2.10", "ecosystem": "PyPI"},
        "vulnerabilities": [
            {
                "id": "PYSEC-2019-217",
                "summary": "",
                "affected": [
                    {
                        "package": {"name": "jinja2"},
                        "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.10.1"}]}],
                    },
                ],
            },
        ],
        "groups": [
            {
                "ids": ["PYSEC-2019-217", "GHSA-462w-v97r-4m45"],
                "aliases": ["CVE-2019-10906", "GHSA-462w-v97r-4m45", "PYSEC-2019-217"],
                "max_severity": "8.6",
            },
        ],
    }
    source = {"path": "/work/backend/requirements.txt", "type": "lockfile"}
    report = {"results": [{"source": source, "packages": [package]}, {"source": source, "packages": [package]}]}
    findings = _parse("osv_scanner", json.dumps(report))
    assert len(findings) == 1
    f = findings[0]
    assert (f.severity, f.title, f.rule_id) == ("high", "jinja2 2.10: CVE-2019-10906", "PYSEC-2019-217")
    assert f.suggestion == "Upgrade jinja2 to 2.10.1"


def test_retire_vendored_library():
    report = {
        "version": "5.7.0",
        "data": [
            {
                "file": "/work/frontend/static/jquery.js",
                "results": [
                    {
                        "component": "jquery",
                        "version": "1.8.3",
                        "detection": "filecontent",
                        "vulnerabilities": [
                            {
                                "info": ["https://nvd.nist.gov/vuln/detail/CVE-2012-6708"],
                                "below": "1.9.0b1",
                                "severity": "medium",
                                "identifiers": {"summary": "Selector interpreted as HTML", "CVE": ["CVE-2012-6708"]},
                                "cwe": ["CWE-79"],
                            },
                        ],
                    },
                ],
            },
        ],
    }
    findings = _parse("retire", json.dumps(report))
    assert len(findings) == 1
    f = findings[0]
    assert (f.severity, f.rule_id, f.title) == (
        "medium",
        "CVE-2012-6708",
        "jquery 1.8.3: Selector interpreted as HTML",
    )
    assert f.tool_specific_data["cwe"] == "CWE-79"
    assert f.suggestion == "Upgrade jquery to 1.9.0b1 or later"


def test_djlint_line_format_ignores_progress_output():
    raw = (
        "\rLinting 1/1 files ━━━━ 00:00\n\n"
        "work/backend/templates/index.html:1:0:H005:Html tag should have lang attribute.\n"
        "work/backend/templates/index.html:2:36:H013:Img tag should have an alt attribute.\n"
        "work/backend/templates/index.html:1:0:H030:Consider adding a meta description.\n"
    )
    findings = _parse("djlint", raw)
    assert [(f.rule_id, f.severity, f.file_path, f.line_number, f.column_number) for f in findings] == [
        ("H005", "low", "backend/templates/index.html", 1, 0),
        ("H013", "low", "backend/templates/index.html", 2, 36),
        ("H030", "info", "backend/templates/index.html", 1, 0),
    ]


def test_dast_issue_keys_and_fingerprints():
    from backend.analysis.services import parsers as registry

    zap = json.dumps(
        {
            "site": [
                {
                    "@name": "http://app",
                    "alerts": [
                        {
                            "pluginid": "10038",
                            "name": "Content Security Policy (CSP) Header Not Set",
                            "riskcode": "2",
                            "confidence": "3",
                            "instances": [{"uri": "http://app/"}],
                        },
                        {
                            "pluginid": "10109",
                            "name": "Modern Web Application",
                            "riskcode": "0",
                            "confidence": "2",
                            "instances": [{"uri": "http://app/"}],
                        },
                    ],
                },
            ],
        },
    )
    zap_findings = registry.parse("zap", zap).findings
    assert [f.tool_specific_data.get("issue_key") for f in zap_findings] == ["missing-header:content-security-policy"]

    nuclei = "\n".join(
        json.dumps(h)
        for h in (
            {
                "template-id": "http-missing-security-headers",
                "matcher-name": "x-frame-options",
                "matched-at": "http://app/",
                "info": {"name": "Missing headers", "severity": "info", "tags": ["misconfig"]},
            },
            {
                "template-id": "tech-detect",
                "matcher-name": "uvicorn",
                "matched-at": "http://app/",
                "info": {"name": "Wappalyzer", "severity": "info", "tags": ["tech"]},
            },
        )
    )
    keys = [f.tool_specific_data.get("issue_key") for f in registry.parse("nuclei", nuclei).findings]
    assert keys == ["missing-header:x-frame-options"]


# --- Tool's own severity / confidence and enriched tool_specific_data --------


def test_zap_risk_and_confidence_words_and_instance_details():
    report = {
        "site": [
            {
                "@name": "http://app:8000",
                "alerts": [
                    {
                        "pluginid": "10098",
                        "name": "Cross-Domain Misconfiguration",
                        "riskcode": "2",
                        "confidence": "2",
                        "riskdesc": "Medium (Medium)",
                        "desc": "<p>CORS</p>",
                        "solution": "<p>Restrict origins.</p>",
                        "otherinfo": "",
                        "reference": "<p>https://vulncat.fortify.com/en/detail</p><p>https://owasp.org/cors</p>",
                        "instances": [
                            {
                                "uri": "http://app:8000/api",
                                "method": "GET",
                                "param": "",
                                "attack": "",
                                "evidence": "Access-Control-Allow-Origin: *",
                                "otherinfo": "The CORS misconfiguration permits cross-domain reads.",
                            },
                        ],
                        "count": "1",
                        "cweid": "264",
                    },
                    # No riskdesc: ZAP's words for the codes.
                    {
                        "pluginid": "40018",
                        "name": "SQL Injection",
                        "riskcode": "3",
                        "confidence": "4",
                        "instances": [],
                    },
                ],
            },
        ],
    }
    cors, sqli = _parse("zap", json.dumps(report))
    assert (cors.severity, cors.tool_severity) == ("medium", "Medium")
    assert cors.suggestion == "Restrict origins."
    data = cors.tool_specific_data
    assert data["tool_confidence"] == "Medium"
    assert data["evidence"] == "Access-Control-Allow-Origin: *"
    assert data["other_info"] == "The CORS misconfiguration permits cross-domain reads."
    assert data["references"] == ["https://vulncat.fortify.com/en/detail", "https://owasp.org/cors"]
    assert data["docs_url"] == "https://www.zaproxy.org/docs/alerts/10098/"
    assert (data["url"], data["method"], data["instances"]) == ("http://app:8000/api", "GET", 1)
    # Empty in the report: no key.
    assert "param" not in data
    assert "attack" not in data
    assert (sqli.tool_severity, sqli.tool_specific_data["tool_confidence"]) == ("High", "Confirmed")


def test_nuclei_labels_template_link_and_cvss():
    hit = {
        "template-id": "CVE-2021-41773",
        "template-url": "https://cloud.projectdiscovery.io/public/CVE-2021-41773",
        "info": {
            "name": "Apache 2.4.49 - Path Traversal",
            "severity": "high",
            "reference": ["https://nvd.nist.gov/vuln/detail/CVE-2021-41773"],
            "classification": {
                "cve-id": ["cve-2021-41773"],
                "cwe-id": ["cwe-22"],
                "cvss-metrics": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N",
                "cvss-score": 7.5,
            },
            "tags": ["cve", "lfi"],
        },
        "matched-at": "http://app:8000/cgi-bin/.%2e/etc/passwd",
        "extracted-results": ["root:x:0:0"],
        "curl-command": "curl -X 'GET' 'http://app:8000/cgi-bin/.%2e/etc/passwd'",
    }
    [f] = _parse("nuclei", json.dumps(hit))
    assert f.tool_severity == "high"
    data = f.tool_specific_data
    assert data["docs_url"] == "https://cloud.projectdiscovery.io/public/CVE-2021-41773"
    assert data["rule_name"] == "Apache 2.4.49 - Path Traversal"
    assert (data["vuln_id"], data["cvss_score"], data["cwe"]) == ("CVE-2021-41773", 7.5, "CWE-22")
    assert data["cvss_vector"].startswith("CVSS:3.1/")
    assert data["evidence"] == "root:x:0:0"
    assert data["curl"].startswith("curl ")


def test_wapiti_numeric_level_and_classification_references():
    report = {
        "classifications": {
            "Cross Site Scripting": {
                "desc": "XSS",
                "sol": "Escape output",
                "ref": {
                    "OWASP: Cross Site Scripting": "https://owasp.org/xss",
                    "CWE-79: Improper Neutralization": "https://cwe.mitre.org/data/definitions/79.html",
                },
            },
        },
        "vulnerabilities": {
            "Cross Site Scripting": [
                {
                    "method": "GET",
                    "path": "/search",
                    "info": "XSS via q",
                    "level": 3,
                    "parameter": "q",
                    "module": "xss",
                },
            ],
        },
    }
    [f] = _parse("wapiti", json.dumps(report))
    assert (f.severity, f.tool_severity) == ("high", "3")
    assert f.tool_specific_data["rule_name"] == "Cross Site Scripting"
    assert f.tool_specific_data["references"] == [
        "https://owasp.org/xss",
        "https://cwe.mitre.org/data/definitions/79.html",
    ]


def test_trivy_labels_cvss_and_fix_versions():
    report = {
        "Results": [
            {
                "Target": "requirements.txt",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2026-27205",
                        "VendorIDs": ["GHSA-68rp-wp8r-4726"],
                        "PkgName": "Flask",
                        "InstalledVersion": "3.0.0",
                        "FixedVersion": "2.3.4, 3.1.3",
                        "SeveritySource": "ghsa",
                        "PrimaryURL": "https://avd.aquasec.com/nvd/cve-2026-27205",
                        "Severity": "LOW",
                        "CVSS": {
                            "ghsa": {"V40Vector": "CVSS:4.0/AV:N", "V40Score": 2.3},
                            "nvd": {"V3Vector": "CVSS:3.1/AV:N", "V3Score": 4.3},
                        },
                        "References": ["https://nvd.nist.gov/vuln/detail/CVE-2026-27205"],
                        "CweIDs": ["CWE-524"],
                    },
                    {"VulnerabilityID": "CVE-1", "PkgName": "x", "InstalledVersion": "1", "Severity": "UNKNOWN"},
                ],
            },
            {
                "Target": "config.py",
                "Secrets": [
                    {
                        "RuleID": "aws-access-key-id",
                        "Category": "AWS",
                        "Severity": "CRITICAL",
                        "Title": "AWS Access Key ID",
                        "StartLine": 1,
                        "EndLine": 1,
                        "Match": 'AWS_ACCESS_KEY_ID = "********************"',
                    },
                ],
            },
        ],
    }
    vuln, unknown, secret = _parse("trivy", json.dumps(report))
    assert (vuln.severity, vuln.tool_severity) == ("low", "LOW")
    data = vuln.tool_specific_data
    # Score and vector from the source Trivy took the severity from.
    assert (data["cvss_score"], data["cvss_vector"]) == (2.3, "CVSS:4.0/AV:N")
    assert data["fixed_versions"] == ["2.3.4", "3.1.3"]
    assert (data["installed_version"], data["vuln_id"], data["aliases"]) == (
        "3.0.0",
        "CVE-2026-27205",
        ["CVE-2026-27205", "GHSA-68rp-wp8r-4726"],
    )
    assert data["docs_url"] == "https://avd.aquasec.com/nvd/cve-2026-27205"
    assert (data["version"], data["fixed_version"]) == ("3.0.0", "2.3.4, 3.1.3")  # legacy keys
    assert (unknown.severity, unknown.tool_severity, unknown.suggestion) == ("info", "UNKNOWN", "")
    assert secret.tool_severity == "CRITICAL"
    assert secret.suggestion == ""  # no invented remediation
    assert secret.tool_specific_data == {"secret_type": "aws-access-key-id", "end_line": 1, "cwe": "CWE-798"}


def test_osv_reads_label_and_cwe_from_every_record_of_a_group():
    package = {
        "package": {"name": "flask", "version": "3.0.0", "ecosystem": "PyPI"},
        "vulnerabilities": [
            {
                "id": "PYSEC-2026-2151",
                "details": "PYSEC details",
                "references": [{"type": "FIX", "url": "https://github.com/pallets/flask/commit/089cb86"}],
                "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:N/A:N"}],
                "affected": [{"package": {"name": "flask"}, "ranges": [{"events": [{"fixed": "3.1.3"}]}]}],
            },
            {
                "id": "GHSA-68rp-wp8r-4726",
                "summary": "Flask session does not add `Vary: Cookie` header",
                "database_specific": {"cwe_ids": ["CWE-524"], "severity": "LOW"},
                "references": [{"type": "ADVISORY", "url": "https://nvd.nist.gov/vuln/detail/CVE-2026-27205"}],
                "severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/AV:N/AC:L/AT:P/PR:N/UI:P/VC:L"}],
            },
        ],
        "groups": [
            {
                "ids": ["PYSEC-2026-2151", "GHSA-68rp-wp8r-4726"],
                "aliases": ["CVE-2026-27205", "GHSA-68rp-wp8r-4726", "PYSEC-2026-2151"],
                "max_severity": "4.3",
            },
        ],
    }
    report = {"results": [{"source": {"path": "/work/requirements.txt"}, "packages": [package]}]}
    [f] = _parse("osv_scanner", json.dumps(report))
    assert f.tool_severity == "LOW"  # the GHSA record's label
    assert f.title == "flask 3.0.0: CVE-2026-27205 — Flask session does not add `Vary: Cookie` header"
    assert f.description == "PYSEC details"
    data = f.tool_specific_data
    assert data["cwe"] == "CWE-524"
    assert data["cvss_score"] == 4.3
    assert "cvss_vector" not in data  # two records, two vectors: ambiguous
    assert data["references"] == [
        "https://github.com/pallets/flask/commit/089cb86",
        "https://nvd.nist.gov/vuln/detail/CVE-2026-27205",
    ]
    # Relative, like file_path and the other scanners' manifests.
    assert (data["vuln_id"], data["fixed_versions"], data["manifest"]) == (
        "PYSEC-2026-2151",
        ["3.1.3"],
        "requirements.txt",
    )


def test_osv_without_label_is_unrated_and_keeps_its_cvss_score():
    package = {
        "package": {"name": "jinja2", "version": "2.10", "ecosystem": "PyPI"},
        "vulnerabilities": [{"id": "PYSEC-1", "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N"}]}],
        "groups": [{"ids": ["PYSEC-1"], "aliases": ["PYSEC-1"], "max_severity": "8.6"}],
    }
    unrated = {
        "package": {"name": "x", "version": "1", "ecosystem": "PyPI"},
        "vulnerabilities": [{"id": "PYSEC-2"}],
        "groups": [{"ids": ["PYSEC-2"], "aliases": ["PYSEC-2"], "max_severity": ""}],
    }
    report = {"results": [{"source": {"path": "requirements.txt"}, "packages": [package, unrated]}]}
    scored, plain = _parse("osv_scanner", json.dumps(report))
    # A CVSS number is not a label the tool printed: unrated, with the score
    # in cvss_score and the normalized level from it.
    assert (scored.tool_severity, scored.severity) == ("", "high")
    assert (scored.tool_specific_data["cvss_score"], scored.tool_specific_data["cvss_vector"]) == (
        8.6,
        "CVSS:3.1/AV:N",
    )
    assert (plain.tool_severity, plain.suggestion) == ("", "")
    assert "cvss_score" not in plain.tool_specific_data


def test_retire_and_djlint_labels():
    report = {
        "data": [
            {
                "file": "/work/static/jquery.js",
                "results": [
                    {
                        "component": "jquery",
                        "version": "1.8.3",
                        "vulnerabilities": [
                            {
                                "info": ["https://nvd.nist.gov/vuln/detail/CVE-2012-6708"],
                                "below": "1.9.0b1",
                                "severity": "medium",
                                "identifiers": {"CVE": ["CVE-2012-6708"], "githubID": "GHSA-2pqj-h3vj-pqgw"},
                            },
                        ],
                    },
                ],
            },
        ],
    }
    [f] = _parse("retire", json.dumps(report))
    assert f.tool_severity == "medium"
    data = f.tool_specific_data
    assert (data["installed_version"], data["fixed_versions"], data["vuln_id"]) == (
        "1.8.3",
        ["1.9.0b1"],
        "CVE-2012-6708",
    )
    assert data["aliases"] == ["CVE-2012-6708", "GHSA-2pqj-h3vj-pqgw"]
    assert data["references"] == ["https://nvd.nist.gov/vuln/detail/CVE-2012-6708"]

    [dj] = _parse("djlint", "work/static/index.html:2:0:H031:Consider adding meta keywords.\n")
    assert (dj.tool_severity, dj.suggestion) == ("", "")
    assert dj.tool_specific_data == {"docs_url": "https://www.djlint.com/docs/linter/#rules"}


def _zap_alert(pluginid: str, riskcode: str, instances: list[dict], **extra) -> dict:
    return {
        "pluginid": pluginid,
        "name": extra.pop("name", f"Alert {pluginid}"),
        "riskcode": riskcode,
        "confidence": "2",
        "instances": instances,
        "count": str(len(instances)),
        **extra,
    }


def test_zap_informational_alerts_are_not_security_and_skip_hashed_bundles():
    base = "http://172.17.0.6:8000"
    report = {
        "site": [
            {
                "@name": base,
                "alerts": [
                    # Suspicious comments only inside the Vite bundle: dropped.
                    _zap_alert(
                        "10027",
                        "0",
                        [{"uri": f"{base}/static/assets/index-DcMykpf9.js", "method": "GET", "evidence": "select"}],
                    ),
                    # Informational hit in the app's own script: kept, not security.
                    _zap_alert(
                        "10027",
                        "0",
                        [
                            {"uri": f"{base}/assets/index-C2K5CO6L.js", "method": "GET", "evidence": "from"},
                            {"uri": f"{base}/app.js", "method": "GET", "evidence": "admin"},
                        ],
                    ),
                    # A real (low) issue on a bundle: kept but marked as build output.
                    _zap_alert(
                        "10003",
                        "1",
                        [{"uri": f"{base}/static/js/main.3f2a1b9c.js", "method": "GET", "evidence": "jquery 1.8"}],
                    ),
                ],
            },
        ],
    }
    comment, library = _parse("zap", json.dumps(report))
    assert (comment.category, comment.severity, comment.tool_severity) == ("best_practice", "info", "Informational")
    assert comment.file_path == "/app.js"
    assert library.category == "security"
    assert library.tool_specific_data["build_asset"] is True
    assert "build_asset" not in comment.tool_specific_data


def test_zap_collapses_identical_passive_evidence_across_spa_fallback_paths():
    base = "http://app:8000"
    instances = [
        {"uri": f"{base}/", "method": "GET", "evidence": "<!-- TODO: remove admin -->"},
        {"uri": f"{base}/robots.txt", "method": "GET", "evidence": "<!-- TODO: remove admin -->"},
        {"uri": f"{base}/sitemap.xml", "method": "GET", "evidence": "<!-- TODO: remove admin -->"},
        {"uri": f"{base}/about", "method": "GET", "evidence": "something else"},
    ]
    active = [
        {"uri": f"{base}/a?q=1", "method": "GET", "param": "q", "attack": "' OR 1=1", "evidence": "SQL error"},
        {"uri": f"{base}/b?q=1", "method": "GET", "param": "q", "attack": "' OR 1=1", "evidence": "SQL error"},
    ]
    report = {
        "site": [
            {
                "@name": base,
                "alerts": [_zap_alert("10027", "0", instances), _zap_alert("40018", "3", active)],
            },
        ],
    }
    spa, about, sqli_a, sqli_b = _parse("zap", json.dumps(report))
    assert spa.file_path == "/"
    assert spa.tool_specific_data["urls"] == [f"{base}/", f"{base}/robots.txt", f"{base}/sitemap.xml"]
    assert about.file_path == "/about"
    assert "urls" not in about.tool_specific_data
    # Active findings with the same evidence are separate endpoints.
    assert (sqli_a.file_path, sqli_b.file_path) == ("/a", "/b")


def test_zap_missing_report_is_an_error():
    parsed = parsers.parse("zap", "cat: /tmp/zap/report.json: No such file or directory\n")
    assert parsed.error is not None


def test_vulnerability_aliases_hold_every_advisory_id():
    trivy = {
        "Results": [
            {
                "Target": "requirements.txt",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2026-1",
                        "VendorIDs": ["GHSA-aaaa", "CVE-2026-1"],
                        "PkgName": "flask",
                        "Severity": "HIGH",
                    },
                ],
            },
        ],
    }
    [t] = _parse("trivy", json.dumps(trivy))
    assert (t.tool_specific_data["vuln_id"], t.tool_specific_data["aliases"]) == (
        "CVE-2026-1",
        ["CVE-2026-1", "GHSA-aaaa"],
    )
    osv = {
        "results": [
            {
                "source": {"path": "requirements.txt"},
                "packages": [
                    {
                        "package": {"name": "flask", "version": "1"},
                        "vulnerabilities": [],
                        "groups": [{"ids": ["PYSEC-9"], "aliases": ["CVE-2026-9", "GHSA-bbbb"]}],
                    },
                ],
            },
        ],
    }
    [o] = _parse("osv_scanner", json.dumps(osv))
    assert o.tool_specific_data["aliases"] == ["PYSEC-9", "CVE-2026-9", "GHSA-bbbb"]


# --- Audit fixes: scope, severity labels, noise and silent zeros -------------


def _osv_report(packages: list[dict], path: str = "/work/frontend/package-lock.json") -> str:
    return json.dumps({"results": [{"source": {"path": path, "type": "lockfile"}, "packages": packages}]})


def _osv_package(name, version, ghsa, *, groups=None, ranges=None, label="MODERATE", max_severity="5.3"):
    record = {
        "id": ghsa,
        "database_specific": {"severity": label} if label else {},
        "affected": [{"package": {"name": name}, "ranges": ranges or []}],
    }
    package = {
        "package": {"name": name, "version": version, "ecosystem": "npm"},
        "vulnerabilities": [record],
        "groups": [{"ids": [ghsa], "aliases": [ghsa], "max_severity": max_severity}],
    }
    if groups is not None:
        package["dependency_groups"] = groups
    return package


def test_osv_leaves_out_packages_only_dev_dependencies_reach():
    report = _osv_report(
        [
            _osv_package("vite", "5.4.21", "GHSA-vite", groups=["dev"]),
            # Lockfile v2+: a dev tool's platform binary *or* npm's devOptional
            # (which can ship as an optional dep of a production package);
            # osv prints both the same way, so it is kept, tagged.
            _osv_package("@esbuild/linux-x64", "0.21.5", "GHSA-esb", groups=["dev", "optional"]),
            _osv_package("uuid", "9.0.1", "GHSA-uuid"),
            _osv_package("fsevents", "2.3.2", "GHSA-fse", groups=["optional"]),
        ],
    )
    parsed = parsers.parse("osv_scanner", report)
    assert [f.tool_specific_data["package"] for f in parsed.findings] == ["@esbuild/linux-x64", "uuid", "fsevents"]
    esbuild, uuid, fsevents = parsed.findings
    assert esbuild.tool_specific_data["dependency_groups"] == ["dev", "optional"]
    assert fsevents.tool_specific_data["dependency_groups"] == ["optional"]
    assert "dependency_groups" not in uuid.tool_specific_data
    assert parsed.metrics == {"dev_only_advisories_skipped": 1, "dev_only_packages_skipped": {"vite@5.4.21": 1}}
    assert parsed.error is None


def test_osv_with_only_dev_advisories_is_a_measurement_not_an_empty_report():
    parsed = parsers.parse("osv_scanner", _osv_report([_osv_package("vite", "5.4.21", "GHSA-v", groups=["dev"])]))
    assert (parsed.findings, parsed.error) == ([], None)
    assert parsed.metrics["dev_only_advisories_skipped"] == 1


def test_osv_clean_report_counts_zero_left_out():
    # The key is on every parsed report: averages over results include the
    # scans that left nothing out (absent = not measured).
    clean = json.dumps({"results": [], "experimental_config": {"licenses": {"summary": False, "allowlist": None}}})
    parsed = parsers.parse("osv_scanner", clean)
    assert (parsed.findings, parsed.error, parsed.metrics) == ([], None, {"dev_only_advisories_skipped": 0})


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("", "printed no report"),
        # Only the run_cmd's lines: osv itself printed nothing (exit 127/128).
        ("\nllmlab-warning: web/package.json has no lockfile beside it\n", "printed no report"),
        ("[]", "no results list"),
        ('{"experimental_config": {}}', "no results list"),
    ],
)
def test_osv_output_without_a_report_is_not_a_clean_scan(raw, error):
    # The run_cmd turns exit 1 ("vulnerabilities found") into 0, so the
    # exit code no longer flags a missing report: the parser must.
    parsed = parsers.parse("osv_scanner", raw)
    assert error in parsed.error
    assert (parsed.findings, parsed.metrics) == ([], {})
    if "llmlab-warning" in raw:
        assert parsed.warnings == ["web/package.json has no lockfile beside it"]


def test_osv_normalized_severity_follows_the_advisory_label_not_the_max_cvss():
    # Real shape (uuid 9.0.1): GitHub rates it MODERATE, NVD's CVSS 3.1 is 7.5.
    package = _osv_package("uuid", "9.0.1", "GHSA-w5hq-g745-h8pq", label="MODERATE", max_severity="7.5")
    [f] = _parse("osv_scanner", _osv_report([package]))
    assert (f.tool_severity, f.severity, f.tool_specific_data["cvss_score"]) == ("MODERATE", "medium", 7.5)
    unknown_word = _osv_package("x", "1.0.0", "GHSA-x", label="SEVERE", max_severity="9.1")
    [g] = _parse("osv_scanner", _osv_report([unknown_word]))
    # A label osv printed is kept verbatim; a word we cannot map falls back to CVSS.
    assert (g.tool_severity, g.severity) == ("SEVERE", "critical")


def test_osv_suggests_the_fix_of_the_installed_release_line():
    ranges = [
        {"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "4.5.6"}]},
        {"type": "SEMVER", "events": [{"introduced": "5.0.0"}, {"fixed": "5.4.12"}]},
        {"type": "SEMVER", "events": [{"introduced": "6.0.0"}, {"fixed": "6.0.9"}]},
        {"type": "GIT", "events": [{"introduced": "0"}, {"fixed": "3f2a1b9c0d"}]},
    ]
    [vite] = _parse("osv_scanner", _osv_report([_osv_package("vite", "5.4.11", "GHSA-vite", ranges=ranges)]))
    assert vite.suggestion == "Upgrade vite to 5.4.12"
    # Every release line's fix stays listed, in version order (no commit hashes).
    assert vite.tool_specific_data["fixed_versions"] == ["4.5.6", "5.4.12", "6.0.9"]

    axios_ranges = [
        {"type": "SEMVER", "events": [{"introduced": "1.0.0"}, {"fixed": "1.15.1"}]},
        {"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "0.31.1"}]},
    ]
    [axios] = _parse("osv_scanner", _osv_report([_osv_package("axios", "1.6.8", "GHSA-ax", ranges=axios_ranges)]))
    assert axios.suggestion == "Upgrade axios to 1.15.1"
    assert axios.tool_specific_data["fixed_versions"] == ["0.31.1", "1.15.1"]

    # No range holds the installed version: the lowest fix above it.
    loose = [{"events": [{"fixed": "2.0.0"}]}, {"events": [{"introduced": "3.0.0"}, {"fixed": "3.1.0"}]}]
    [pkg] = _parse("osv_scanner", _osv_report([_osv_package("p", "2.5.0", "GHSA-p", ranges=loose)]))
    assert pkg.suggestion == "Upgrade p to 3.1.0"

    # Unparsable installed version: every fix, as before.
    [odd] = _parse("osv_scanner", _osv_report([_osv_package("q", "git+abc", "GHSA-q", ranges=ranges[:2])]))
    assert odd.suggestion == "Upgrade q to 4.5.6 or 5.4.12"


_LINE_SEPARATOR = chr(0x2028)


def test_osv_lockfile_gap_lines_become_warnings_and_keep_the_report():
    # A raw U+2028 inside a JSON string must survive the marker stripping.
    package = _osv_package("uuid", "9.0.1", "GHSA-uuid")
    package["vulnerabilities"][0]["summary"] = f"line{_LINE_SEPARATOR}separator"
    raw = (
        json.dumps({"results": [{"source": {"path": "/work/api/package-lock.json"}, "packages": [package]}]})
        + "\n\nllmlab-warning: web/package.json has no lockfile beside it, so its npm dependencies were not scanned\n"
    )
    parsed = parsers.parse("osv_scanner", raw.replace("\\u2028", _LINE_SEPARATOR))
    assert parsed.error is None
    [f] = parsed.findings
    assert f.title == f"uuid 9.0.1: GHSA-uuid — line{_LINE_SEPARATOR}separator"
    assert (f.file_path, f.tool_specific_data["manifest"]) == ("api/package-lock.json", "api/package-lock.json")
    assert parsed.warnings == [
        "web/package.json has no lockfile beside it, so its npm dependencies were not scanned",
    ]


def test_trivy_lockfile_gap_lines_become_warnings():
    raw = json.dumps({"Results": []}) + "\n\nllmlab-warning: package.json has no lockfile beside it\n"
    parsed = parsers.parse("trivy", raw)
    assert (parsed.findings, parsed.error, parsed.warnings) == ([], None, ["package.json has no lockfile beside it"])


def _nuclei_hit(template, matcher="", severity="info", url="http://172.17.0.6:8000"):
    return {
        "template-id": template,
        "matcher-name": matcher,
        "matched-at": url,
        "info": {"name": template, "severity": severity, "tags": ["misconfig"]},
    }


def _jsonl(*hits) -> str:
    return "\n".join(json.dumps(h) for h in hits) + "\n"


def test_nuclei_info_hits_are_best_practice_except_cors_and_hsts_over_http_is_dropped():
    raw = _jsonl(
        _nuclei_hit("http-missing-security-headers", "referrer-policy"),
        _nuclei_hit("http-missing-security-headers", "strict-transport-security"),
        _nuclei_hit("http-missing-security-headers", "strict-transport-security", url="https://app.example"),
        _nuclei_hit("cors-misconfig", "arbitrary-origin"),
        _nuclei_hit("git-config", severity="medium", url="http://172.17.0.6:8000/.git/config"),
    )
    parsed = parsers.parse("nuclei", raw)
    assert parsed.error is None
    assert [(f.tool_specific_data.get("issue_key", f.rule_id), f.category) for f in parsed.findings] == [
        ("missing-header:referrer-policy", "best_practice"),
        # HSTS is only meaningful over https.
        ("missing-header:strict-transport-security", "best_practice"),
        ("cors-misconfiguration", "security"),
        ("git-config", "security"),
    ]
    assert parsed.findings[1].tool_specific_data["url"] == "https://app.example"


def test_nuclei_info_header_merges_with_a_zap_medium_hit_into_a_security_issue():
    from backend.statistics.services.dedup import cluster_findings

    [nuclei] = _parse("nuclei", _jsonl(_nuclei_hit("http-missing-security-headers", "content-security-policy")))
    zap_report = {
        "site": [
            {
                "@name": "http://172.17.0.6:8000",
                "alerts": [
                    _zap_alert(
                        "10038",
                        "2",
                        [{"uri": "http://172.17.0.6:8000/", "method": "GET"}],
                        name="Content Security Policy (CSP) Header Not Set",
                        systemic=True,
                    ),
                ],
            },
        ],
    }
    [zap] = _parse("zap", json.dumps(zap_report))
    assert (nuclei.category, zap.category) == ("best_practice", "security")
    records = [
        {
            "id": i,
            "run_id": "r",
            "tool": tool,
            "rule_id": f.rule_id,
            "severity": f.severity,
            "category": f.category,
            "file_path": f.file_path,
            "line": None,
            "issue_key": f.tool_specific_data.get("issue_key", ""),
        }
        for i, (tool, f) in enumerate([("nuclei", nuclei), ("zap", zap)])
    ]
    [cluster] = cluster_findings(records)
    assert (cluster.tools, cluster.category) == (["nuclei", "zap"], "security")


def test_nuclei_printing_nothing_is_not_a_clean_scan():
    # No hit and no completion line: nuclei never ran to its end.
    assert parsers.parse("nuclei", "").error is not None
    assert parsers.parse("nuclei", "\n\n").error is not None
    # The run_cmd's completion line after a silent scan: a real zero.
    clean = parsers.parse("nuclei", "\nllmlab-done: nuclei exited with code 0\n")
    assert (clean.findings, clean.error, clean.warnings) == ([], None, [])
    # Hits without the completion line (an older run_cmd) still count.
    assert parsers.parse("nuclei", _jsonl(_nuclei_hit("git-config", severity="medium"))).error is None


def test_wapiti_info_level_is_best_practice_except_cors_and_empty_output_is_an_error():
    report = {
        "classifications": {},
        "vulnerabilities": {
            "Content Security Policy Configuration": [{"path": "/", "info": "CSP is not set", "level": 0}],
            "CORS": [{"path": "/api", "info": "CORS misconfiguration: any origin allowed", "level": 0}],
            "Clickjacking Protection": [{"path": "/", "info": "X-Frame-Options is not set", "level": 1}],
        },
    }
    findings = _parse("wapiti", json.dumps(report))
    assert [(f.tool_specific_data.get("issue_key"), f.category, f.tool_severity) for f in findings] == [
        ("missing-header:content-security-policy", "best_practice", "0"),
        ("cors-misconfiguration", "security", "0"),
        ("missing-header:x-frame-options", "security", "1"),
    ]
    assert parsers.parse("wapiti", "").error == "Wapiti wrote no report"


def test_zap_spa_fallback_with_several_comments_is_one_finding_showing_every_comment():
    # Real alert shape (ZAP 10027 on a Flask + React app): two suspicious
    # comments in index.html, served again for /robots.txt and /sitemap.xml.
    base = "http://172.17.0.8:8000"
    comments = [
        ("username", 'detected in likely comment: "// {username, is_admin}"'),
        ("Admin", 'detected in likely comment: "<!-- Admin (admin only) -->"'),
    ]
    instances = [
        {"uri": f"{base}{path}", "method": "GET", "param": "", "attack": "", "evidence": ev, "otherinfo": info}
        for ev, info in comments
        for path in ("/", "/robots.txt", "/sitemap.xml")
    ]
    report = {"site": [{"@name": base, "alerts": [_zap_alert("10027", "0", instances)]}]}
    [f] = _parse("zap", json.dumps(report))
    data = f.tool_specific_data
    assert f.file_path == "/"
    assert data["urls"] == [f"{base}/", f"{base}/robots.txt", f"{base}/sitemap.xml"]
    assert (f.code_snippet, data["evidence"]) == ("username\nAdmin", "username\nAdmin")
    assert data["other_info"] == "\n".join(info for _, info in comments)
    assert data["instances"] == 6


def test_zap_alert_split_into_findings_counts_each_findings_own_instances():
    base = "http://app:8000"
    instances = [
        {"uri": f"{base}/a?q=1", "method": "GET", "param": "q", "attack": "'", "evidence": "SQL error"},
        {"uri": f"{base}/a?q=1", "method": "POST", "param": "q", "attack": "'", "evidence": "SQL error"},
        {"uri": f"{base}/b?q=1", "method": "GET", "param": "q", "attack": "'", "evidence": "SQL error"},
    ]
    alert = {**_zap_alert("40018", "3", instances), "count": "9"}
    a, b = _parse("zap", json.dumps({"site": [{"@name": base, "alerts": [alert]}]}))
    assert (a.file_path, a.tool_specific_data["instances"]) == ("/a", 2)
    assert (b.file_path, b.tool_specific_data["instances"]) == ("/b", 1)


def test_zap_drops_missing_hsts_on_plain_http_only():
    alert = _zap_alert(
        "10035",
        "1",
        [{"uri": "http://app:8000/", "method": "GET"}, {"uri": "https://app.example/", "method": "GET"}],
        name="Strict-Transport-Security Header Not Set",
    )
    [f] = _parse("zap", json.dumps({"site": [{"@name": "http://app:8000", "alerts": [alert]}]}))
    assert f.tool_specific_data["url"] == "https://app.example/"
    only_http = {**alert, "instances": alert["instances"][:1]}
    assert _parse("zap", json.dumps({"site": [{"@name": "http://app:8000", "alerts": [only_http]}]})) == []


def test_zap_build_bundle_rule_spares_hand_written_files():
    base = "http://app:8000"
    hand_written = f"{base}/static/js/todo-ListView.js"
    report = {
        "site": [
            {
                "@name": base,
                "alerts": [
                    # The model's own script: an informational hit is kept…
                    _zap_alert("10027", "0", [{"uri": hand_written, "method": "GET", "evidence": "TODO admin"}]),
                    # …and a real issue there is not labelled build output.
                    _zap_alert("10003", "1", [{"uri": hand_written, "method": "GET", "evidence": "x"}]),
                    # A Vite lazy chunk is build output: dropped.
                    _zap_alert("10027", "0", [{"uri": f"{base}/assets/TodoList-Ab12Cd34.js", "evidence": "from"}]),
                    # A Vite hash without a digit is still a hash.
                    _zap_alert("10027", "0", [{"uri": f"{base}/static/assets/index-BKBQyzMV.css", "evidence": "a"}]),
                    # CRA chunk.
                    _zap_alert("10027", "0", [{"uri": f"{base}/static/js/2.3f2a1b9c.chunk.js", "evidence": "b"}]),
                ],
            },
        ],
    }
    comment, library = _parse("zap", json.dumps(report))
    assert (comment.file_path, comment.category) == ("/static/js/todo-ListView.js", "best_practice")
    assert library.file_path == "/static/js/todo-ListView.js"
    assert "build_asset" not in library.tool_specific_data


def test_djlint_output_that_linted_nothing_is_an_error():
    no_files = parsers.parse("djlint", "No files to check! 😢\n")
    assert no_files.findings == []
    assert "No files to check" in no_files.error
    assert parsers.parse("djlint", "").error is not None
    clean = parsers.parse("djlint", "\n\nLinted 1 file, found 0 errors.\n\nNo files to check! 😢\n")
    assert (clean.findings, clean.error) == ([], None)
    crashed_pass = parsers.parse(
        "djlint",
        "templates/a.jinja2:1:0:H005:Html tag should have lang attribute.\n"
        "llmlab-warning: djlint --extension=html exited with code 2\n",
    )
    assert len(crashed_pass.findings) == 1
    assert crashed_pass.warnings == ["djlint --extension=html exited with code 2"]


# --- CodeQL (SARIF) -----------------------------------------------------------


def _codeql_log(results=(), *, extracted=("backend/app.py",), notes=(), lines=120):
    """A SARIF 2.1.0 log shaped like ``codeql database analyze --format=sarif-latest`` (2.27)."""
    rules = [
        {
            "id": "py/path-injection",
            "shortDescription": {"text": "Uncontrolled data used in path expression"},
            "fullDescription": {"text": "Accessing paths influenced by users can allow an attacker to access files."},
            "defaultConfiguration": {"enabled": True, "level": "error"},
            "properties": {
                "tags": ["correctness", "security", "external/cwe/cwe-022", "external/cwe/cwe-023"],
                "kind": "path-problem",
                "precision": "high",
                "problem.severity": "error",
                "security-severity": "7.5",
            },
        },
        {
            "id": "py/empty-except",
            "shortDescription": {"text": "Empty except"},
            "defaultConfiguration": {"enabled": True, "level": "note"},
            "properties": {
                "tags": ["quality", "reliability", "error-handling", "external/cwe/cwe-390"],
                "precision": "high",
                "problem.severity": "recommendation",
            },
        },
    ]
    notifications = [
        {
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": path, "uriBaseId": "%SRCROOT%"}}}],
            "message": {"text": ""},
            "level": "none",
            "descriptor": {"id": "py/diagnostics/successfully-extracted-files"},
        }
        for path in extracted
    ]
    return json.dumps(
        {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {"name": "CodeQL", "rules": rules},
                        "extensions": [{"name": "codeql/python-all"}],
                    },
                    "invocations": [{"toolExecutionNotifications": [*notifications, *notes]}],
                    "results": list(results),
                    "properties": {
                        "metricResults": [
                            {"ruleId": "py/summary/lines-of-user-code", "value": lines},
                            {"ruleId": "py/summary/lines-of-code", "value": lines + 9},
                        ],
                    },
                },
            ],
        },
    )


_CODEQL_PATH_INJECTION = {
    "ruleId": "py/path-injection",
    "rule": {"id": "py/path-injection", "index": 0},
    "message": {"text": "This path depends on a [user-provided value](1)."},
    "locations": [
        {
            "physicalLocation": {
                "artifactLocation": {"uri": "backend/routes/files%20api.py", "uriBaseId": "%SRCROOT%"},
                "region": {
                    "startLine": 41,
                    "startColumn": 22,
                    "endColumn": 26,
                    "snippet": {"text": "    return send_file(path)"},
                },
            },
        },
    ],
    "codeFlows": [
        {
            "threadFlows": [
                {
                    "locations": [
                        {
                            "location": {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "backend/routes/files%20api.py"},
                                    "region": {"startLine": 38},
                                },
                            },
                        },
                        {
                            "location": {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "backend/routes/files%20api.py"},
                                    "region": {"startLine": 41},
                                },
                            },
                        },
                    ],
                },
            ],
        },
    ],
}
_CODEQL_EMPTY_EXCEPT = {
    "ruleId": "py/empty-except",
    "message": {"text": "'except' clause does nothing but pass and there is no explanatory comment."},
    "locations": [
        {"physicalLocation": {"artifactLocation": {"uri": "backend/app.py"}, "region": {"startLine": 12}}},
    ],
}


def test_codeql_maps_security_and_quality_results():
    raw = "### python\n" + _codeql_log([_CODEQL_PATH_INJECTION, _CODEQL_EMPTY_EXCEPT]) + "\n"
    parsed = parsers.parse("codeql", raw)
    assert parsed.error is None
    assert parsed.warnings == []
    injection, empty = parsed.findings

    assert injection.rule_id == "py/path-injection"
    assert injection.category == "security"
    assert injection.severity == "high"  # security-severity 7.5
    assert injection.tool_severity == "error"  # CodeQL's own problem.severity
    assert injection.confidence == "high"
    assert injection.title == "Uncontrolled data used in path expression"
    assert injection.description == "This path depends on a user-provided value."
    assert injection.file_path == "backend/routes/files api.py"
    assert (injection.line_number, injection.column_number) == (41, 22)
    assert injection.code_snippet == "    return send_file(path)"
    data = injection.tool_specific_data
    assert data["cwe"] == "CWE-22"
    assert data["cvss_score"] == 7.5
    assert data["tool_confidence"] == "high"
    assert data["tags"] == ["correctness", "security"]
    assert data["data_flow"] == ["backend/routes/files api.py:38", "backend/routes/files api.py:41"]
    assert data["docs_url"] == "https://codeql.github.com/codeql-query-help/python/py-path-injection/"

    assert empty.category == "quality"
    assert empty.severity == "info"
    assert empty.tool_severity == "recommendation"
    assert "cvss_score" not in empty.tool_specific_data
    assert parsed.metrics == {"languages": {"python": {"files_extracted": 1, "lines_of_user_code": 120}}}


def test_codeql_clean_language_is_a_measurement():
    raw = "### python\n" + _codeql_log() + "\n### javascript\n" + _codeql_log(extracted=("frontend/src/App.tsx",))
    parsed = parsers.parse("codeql", raw)
    assert parsed.error is None
    assert parsed.findings == []
    assert set(parsed.metrics["languages"]) == {"python", "javascript"}


def test_codeql_failed_language_is_a_coverage_gap():
    raw = "### python\n" + _codeql_log() + "\n### javascript\n!! codeql javascript failed: Out of memory\n"
    parsed = parsers.parse("codeql", raw)
    assert parsed.error is None
    assert any("javascript: codeql javascript failed: Out of memory" in w for w in parsed.warnings)


def test_codeql_nothing_analyzed_is_an_error_not_a_clean_result():
    failed = parsers.parse("codeql", "### python\n!! codeql python failed: no source code seen\n")
    assert failed.error
    assert "python" in failed.error

    # A database that extracted no file measured nothing either.
    empty = parsers.parse("codeql", "### python\n" + _codeql_log(extracted=()))
    assert empty.error
    assert "extracted no source file" in empty.error


def test_codeql_reports_extraction_errors():
    note = {
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": "backend/broken.py"}}}],
        "message": {"text": "Syntax error at line 3"},
        "level": "warning",
        "descriptor": {"id": "py/diagnostics/extraction-errors"},
    }
    parsed = parsers.parse("codeql", "### python\n" + _codeql_log(notes=[note]))
    assert parsed.error is None
    assert any("backend/broken.py: Syntax error at line 3" in w for w in parsed.warnings)
