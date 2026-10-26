"""Parsers for the DAST scanners and Trivy / OSV-Scanner / Retire.js / djLint.

Fixtures are trimmed from real runs of the pinned tool versions against a
generated Flask + React sample app.
"""

from __future__ import annotations

import json

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


def test_zap_without_report_is_empty():
    assert _parse("zap", "") == []


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
        ("reliability", "medium", "/api/items", "exec"),
    ]
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
        ["GHSA-68rp-wp8r-4726"],
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
    assert (data["vuln_id"], data["fixed_versions"], data["manifest"]) == (
        "PYSEC-2026-2151",
        ["3.1.3"],
        "/work/requirements.txt",
    )


def test_osv_without_label_falls_back_to_its_cvss_column():
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
    assert (scored.tool_severity, scored.tool_specific_data["cvss_vector"]) == ("8.6", "CVSS:3.1/AV:N")
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
    assert data["aliases"] == ["GHSA-2pqj-h3vj-pqgw"]
    assert data["references"] == ["https://nvd.nist.gov/vuln/detail/CVE-2012-6708"]

    [dj] = _parse("djlint", "work/static/index.html:2:0:H031:Consider adding meta keywords.\n")
    assert (dj.tool_severity, dj.suggestion) == ("", "")
    assert dj.tool_specific_data == {"docs_url": "https://www.djlint.com/docs/linter/#rules"}
