from __future__ import annotations

import json

from backend.analysis.services import parsers


def test_bandit_parser_maps_severity_and_rule():
    raw = json.dumps(
        {
            "results": [
                {
                    "filename": "app.py",
                    "issue_severity": "HIGH",
                    "issue_confidence": "MEDIUM",
                    "issue_text": "Use of insecure MD5 hash function",
                    "test_id": "B303",
                    "test_name": "md5",
                    "line_number": 4,
                    "code": "hashlib.md5(x)",
                },
            ],
        },
    )
    parsed = parsers.parse("bandit", raw)
    assert len(parsed.findings) == 1
    assert parsed.metrics == {}
    f = parsed.findings[0]
    assert f.severity == "high"
    assert f.category == "security"
    assert f.rule_id == "B303"
    assert f.line_number == 4


def test_ruff_parser():
    raw = json.dumps(
        [
            {
                "code": "F401",
                "message": "'os' imported but unused",
                "filename": "app.py",
                "location": {"row": 1, "column": 1},
            },
        ],
    )
    findings = parsers.parse("ruff", raw).findings
    assert len(findings) == 1
    assert findings[0].rule_id == "F401"
    assert findings[0].category == "quality"


def test_eslint_parser_severity_levels():
    raw = json.dumps(
        [
            {
                "filePath": "/work/frontend.js",
                "messages": [
                    {"ruleId": "no-undef", "severity": 2, "message": "x is not defined", "line": 3, "column": 5},
                    {"ruleId": "no-unused-vars", "severity": 1, "message": "unused", "line": 1, "column": 1},
                ],
            },
        ],
    )
    findings = parsers.parse("eslint", raw).findings
    assert {f.severity for f in findings} == {"high", "low"}
    assert findings[0].file_path == "/work/frontend.js"


def test_semgrep_parser():
    raw = json.dumps(
        {
            "results": [
                {
                    "check_id": "python.lang.security.dangerous-subprocess",
                    "path": "backend_code.py",
                    "start": {"line": 5, "col": 5},
                    "end": {"line": 5, "col": 40},
                    "extra": {
                        "severity": "ERROR",
                        "message": "Detected subprocess with shell=True",
                        "lines": "subprocess.run(cmd, shell=True)",
                        "metadata": {"confidence": "HIGH"},
                    },
                },
            ],
        },
    )
    findings = parsers.parse("semgrep", raw).findings
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "high"
    assert f.category == "security"
    assert f.rule_id == "python.lang.security.dangerous-subprocess"
    assert f.line_number == 5


def test_pylint_parser_severity_map():
    raw = json.dumps(
        [
            {
                "type": "error",
                "message": "undefined name 'foo'",
                "symbol": "undefined-variable",
                "path": "app.py",
                "line": 3,
                "column": 4,
            },
            {
                "type": "convention",
                "message": "missing docstring",
                "symbol": "missing-docstring",
                "path": "app.py",
                "line": 1,
                "column": 0,
            },
        ],
    )
    findings = parsers.parse("pylint", raw).findings
    assert {f.severity for f in findings} == {"high", "low"}
    assert findings[0].rule_id == "undefined-variable"
    assert findings[0].category == "quality"


def test_mypy_parser_text():
    raw = (
        'backend_code.py:4:1: error: Argument 1 to "greet" has incompatible type '
        '"int"; expected "str"  [arg-type]\n'
        "backend_code.py:4:1: note: see docs\n"
    )
    findings = parsers.parse("mypy", raw).findings
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "medium"
    assert f.line_number == 4
    assert f.rule_id == "arg-type"


def test_gitleaks_parser():
    raw = json.dumps(
        [
            {
                "RuleID": "aws-access-token",
                "Description": "AWS Access Key",
                "File": "backend_code.py",
                "StartLine": 1,
                "Match": "AKIA...REDACTED",
            },
        ],
    )
    findings = parsers.parse("gitleaks", raw).findings
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "high"
    assert f.category == "secrets"
    assert f.rule_id == "aws-access-token"


def test_radon_parser_findings_only_for_e_and_f_ranks():
    raw = json.dumps(
        {
            "app.py": [
                {
                    "type": "class",
                    "name": "Dispatcher",
                    "rank": "C",
                    "complexity": 12,
                    "lineno": 1,
                    "col_offset": 0,
                    "endline": 40,
                    "methods": [
                        {
                            "type": "method",
                            "name": "dispatch",
                            "rank": "F",
                            "complexity": 44,
                            "lineno": 3,
                            "col_offset": 4,
                            "endline": 39,
                        },
                    ],
                },
            ],
            "broken.py": {"error": "invalid syntax"},
        },
    )
    findings = parsers.parse("radon", raw).findings
    # The C-rank class is metric data, not a finding; only E/F blocks surface.
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "CC-F"
    assert f.severity == "critical"
    assert f.line_number == 3
    assert f.category == "performance"


def test_radon_parser_metrics():
    raw = json.dumps(
        {
            "app.py": [
                {"type": "function", "name": "simple", "rank": "A", "complexity": 2, "lineno": 1},
                {"type": "function", "name": "medium", "rank": "B", "complexity": 8, "lineno": 10},
                {
                    "type": "class",
                    "name": "Dispatcher",
                    "rank": "C",
                    "complexity": 12,
                    "lineno": 20,
                    "methods": [
                        {"type": "method", "name": "dispatch", "rank": "F", "complexity": 42, "lineno": 22},
                    ],
                },
            ],
        },
    )
    metrics = parsers.parse("radon", raw).metrics
    assert metrics["total_blocks"] == 4
    assert metrics["max_complexity"] == 42
    assert metrics["average_complexity"] == 16.0  # (2+8+12+42)/4
    assert metrics["rank_distribution"] == {"A": 1, "B": 1, "C": 1, "D": 0, "E": 0, "F": 1}
    top = metrics["top_functions"]
    assert top[0]["name"] == "dispatch"
    assert top[0]["complexity"] == 42
    assert top[0]["file"] == "app.py"


def test_vulture_parser_text():
    raw = (
        "app.py:1: unused import 'os' (90% confidence)\n"
        "app.py:4: unused function 'never_called' (60% confidence)\n"
        "vulture: some note line\n"
    )
    findings = parsers.parse("vulture", raw).findings
    assert len(findings) == 2
    assert findings[0].confidence == "high"
    assert findings[0].rule_id == "unused-import"
    assert findings[1].confidence == "medium"
    assert findings[1].line_number == 4
    assert all(f.category == "quality" for f in findings)


def test_detect_secrets_parser():
    raw = json.dumps(
        {
            "version": "1.5.0",
            "results": {
                "config.py": [
                    {
                        "type": "AWS Access Key",
                        "filename": "config.py",
                        "line_number": 2,
                        "hashed_secret": "abc123",
                        "is_verified": False,
                    },
                ],
            },
        },
    )
    findings = parsers.parse("detect_secrets", raw).findings
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "high"
    assert f.category == "secrets"
    assert f.rule_id == "AWS Access Key"
    assert f.line_number == 2
    assert f.tool_specific_data == {"secret_type": "AWS Access Key", "hashed_secret": "abc123", "is_verified": False}
    assert f.code_snippet == ""
    assert f.tool_severity == ""  # detect-secrets rates nothing


def test_jscpd_parser_truncates_fragment():
    raw = json.dumps(
        {
            "duplicates": [
                {
                    "format": "python",
                    "lines": 30,
                    "tokens": 120,
                    "fragment": "x" * 900,
                    "firstFile": {"name": "a.py", "start": 3, "end": 32},
                    "secondFile": {"name": "b.py", "start": 10, "end": 39},
                },
            ],
            "statistics": {},
        },
    )
    parsed = parsers.parse("jscpd", raw)
    assert len(parsed.findings) == 1
    assert parsed.metrics == {}  # no statistics.total in payload
    f = parsed.findings[0]
    assert f.severity == "medium"  # >= 25 lines
    assert f.file_path == "a.py"
    assert f.line_number == 3
    assert "b.py:10" in f.title
    assert len(f.code_snippet) == 500
    assert f.rule_id == "duplicate-code"


def test_jscpd_parser_metrics_from_statistics():
    raw = json.dumps(
        {
            "duplicates": [],
            "statistics": {
                "total": {
                    "lines": 200,
                    "tokens": 1600,
                    "sources": 4,
                    "clones": 2,
                    "duplicatedLines": 24,
                    "duplicatedTokens": 190,
                    "percentage": 12.0,
                    "percentageTokens": 11.875,
                },
            },
        },
    )
    parsed = parsers.parse("jscpd", raw)
    assert parsed.findings == []
    assert parsed.metrics == {
        "clones": 2,
        "duplicated_lines": 24,
        "duplicated_tokens": 190,
        "total_lines": 200,
        "total_sources": 4,
        "duplication_percentage": 12.0,
        "token_duplication_percentage": 11.88,
    }


def test_hadolint_parser_level_map():
    raw = json.dumps(
        [
            {"file": "Dockerfile", "line": 1, "code": "DL3007", "level": "warning", "message": "latest tag"},
            {"file": "Dockerfile", "line": 2, "code": "DL3009", "level": "info", "message": "apt lists"},
            {"file": "Dockerfile", "line": 3, "code": "DL4000", "level": "style", "message": "style"},
        ],
    )
    findings = parsers.parse("hadolint", raw).findings
    assert [f.severity for f in findings] == ["medium", "low", "info"]
    assert findings[0].rule_id == "DL3007"
    # Non-list payload (e.g. an error object) yields no findings.
    assert parsers.parse("hadolint", json.dumps({"error": "x"})).findings == []


def test_codespell_parser_text():
    raw = "app.py:3:  recieve ==> receive\napp.py:7:  seperate ==> separate\n"
    findings = parsers.parse("codespell", raw).findings
    assert len(findings) == 2
    f = findings[0]
    assert f.severity == "info"
    assert f.line_number == 3
    assert f.suggestion == "Replace 'recieve' with 'receive'"
    assert f.rule_id == "typo"


def test_unknown_parser_returns_empty():
    parsed = parsers.parse("does-not-exist", "{}")
    assert parsed.findings == []
    assert parsed.metrics == {}


def test_parser_tolerates_leading_noise():
    raw = "warning: something\n" + json.dumps({"results": []})
    assert parsers.parse("bandit", raw).findings == []


def test_parser_tolerates_empty_and_non_json_output():
    # A tool that scanned nothing or printed a CLI error must yield nothing quietly.
    for key in ("eslint", "gitleaks", "radon", "vulture", "detect_secrets", "jscpd", "hadolint", "codespell"):
        for raw in ("", "No files matching the pattern were found."):
            parsed = parsers.parse(key, raw)
            assert parsed.findings == []
            assert parsed.metrics == {}


def test_bandit_and_semgrep_record_cwe():
    import json

    from backend.analysis.services.parsers import parse

    bandit = parse(
        "bandit",
        json.dumps(
            {"results": [{"issue_severity": "HIGH", "test_id": "B608", "issue_cwe": {"id": 89}, "line_number": 3}]},
        ),
    )
    assert bandit.findings[0].tool_specific_data["cwe"] == "CWE-89"
    semgrep = parse(
        "semgrep",
        json.dumps(
            {
                "results": [
                    {
                        "check_id": "x.sql",
                        "start": {"line": 3},
                        "extra": {"metadata": {"cwe": ["CWE-89: Improper Neutralization"]}},
                    },
                ],
            },
        ),
    )
    assert semgrep.findings[0].tool_specific_data["cwe"] == "CWE-89"
    no_cwe = parse("bandit", json.dumps({"results": [{"test_id": "B101"}]}))
    assert "cwe" not in no_cwe.findings[0].tool_specific_data


# --- Tool's own severity / confidence and enriched tool_specific_data --------
#
# ``tool_severity`` is the label exactly as the tool printed it, "" for tools
# that rate nothing; ``severity`` stays our normalization for statistics.


def test_bandit_keeps_its_own_labels_and_rule_docs():
    raw = json.dumps(
        {
            "results": [
                {
                    "filename": "/work/app.py",
                    "issue_severity": "MEDIUM",
                    "issue_confidence": "LOW",
                    "issue_text": "Possible SQL injection vector through string-based query construction.",
                    "issue_cwe": {"id": 89, "link": "https://cwe.mitre.org/data/definitions/89.html"},
                    "test_id": "B608",
                    "test_name": "hardcoded_sql_expressions",
                    "line_number": 407,
                    "line_range": [406, 407, 408],
                    "more_info": "https://bandit.readthedocs.io/en/1.7.10/plugins/b608_hardcoded_sql_expressions.html",
                    "code": "db.execute(f'...')",
                },
            ],
        },
    )
    [f] = parsers.parse("bandit", raw).findings
    assert (f.severity, f.confidence) == ("medium", "low")  # normalization unchanged
    assert f.tool_severity == "MEDIUM"
    data = f.tool_specific_data
    assert data["tool_confidence"] == "LOW"
    assert data["rule_name"] == "hardcoded_sql_expressions"
    assert data["docs_url"].endswith("b608_hardcoded_sql_expressions.html")
    assert (data["end_line"], data["cwe"]) == (408, "CWE-89")


def test_ruff_rates_nothing_but_reports_docs_and_fix():
    raw = json.dumps(
        [
            {
                "code": "F401",
                "message": "`typing.List` imported but unused",
                "filename": "/work/app.py",
                "location": {"row": 8, "column": 20},
                "end_location": {"row": 8, "column": 24},
                "fix": {"applicability": "safe", "edits": [], "message": "Remove unused import: `typing.List`"},
                "url": "https://docs.astral.sh/ruff/rules/unused-import",
            },
        ],
    )
    [f] = parsers.parse("ruff", raw).findings
    assert f.severity == "low"  # heuristic normalization, kept for statistics
    assert f.tool_severity == ""  # ruff prints no level
    assert "tool_confidence" not in f.tool_specific_data
    assert f.tool_specific_data == {
        "rule_name": "unused-import",
        "docs_url": "https://docs.astral.sh/ruff/rules/unused-import",
        "fix": "Remove unused import: `typing.List`",
        "end_line": 8,
        "end_column": 24,
    }


def test_flake8_and_vulture_labels():
    [flake] = parsers.parse("flake8", "/work/app.py:8:1: F401 'typing.List' imported but unused\n").findings
    assert flake.tool_severity == ""
    [dead] = parsers.parse("vulture", "schemas.py:25: unused variable 'items' (60% confidence)\n").findings
    assert dead.tool_severity == ""
    assert dead.tool_specific_data["tool_confidence"] == "60%"


def test_eslint_levels_core_rule_docs_and_suggestions():
    raw = json.dumps(
        [
            {
                "filePath": "/work/x.js",
                "messages": [
                    {
                        "ruleId": "no-empty",
                        "severity": 2,
                        "message": "Empty block statement.",
                        "line": 3,
                        "column": 8,
                        "endLine": 3,
                        "endColumn": 10,
                        "suggestions": [
                            {
                                "desc": "Add comment inside empty block statement.",
                                "fix": {"range": [1, 1], "text": ""},
                            },
                        ],
                    },
                    {"ruleId": "react-hooks/exhaustive-deps", "severity": 1, "message": "deps", "line": 9},
                    {"ruleId": None, "fatal": True, "severity": 2, "message": "Parsing error: x", "line": 1},
                ],
            },
        ],
    )
    parsed = parsers.parse("eslint", raw)
    # The parsing error is a coverage gap, not a finding.
    core, plugin = parsed.findings
    assert [f.tool_severity for f in (core, plugin)] == ["error", "warning"]
    assert parsed.warnings == ["Not parsed (1 of 1 file(s)): /work/x.js:1: Parsing error: x"]
    assert core.tool_specific_data == {
        "docs_url": "https://eslint.org/docs/latest/rules/no-empty",
        "fix": "Add comment inside empty block statement.",
        "end_line": 3,
        "end_column": 10,
    }
    # Plugin rules get no guessed documentation link.
    assert "docs_url" not in plugin.tool_specific_data


def test_semgrep_labels_metadata_and_login_placeholder():
    raw = json.dumps(
        {
            "results": [
                {
                    "check_id": "python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host",
                    "path": "/work/app.py",
                    "start": {"line": 484, "col": 5},
                    "end": {"line": 484, "col": 52},
                    "extra": {
                        "message": "Running flask app with host 0.0.0.0 could expose the server publicly.",
                        "metadata": {
                            "cwe": ["CWE-668: Exposure of Resource to Wrong Sphere"],
                            "owasp": ["A01:2021 - Broken Access Control"],
                            "category": "security",
                            "references": ["https://owasp.org/Top10/A01_2021-Broken_Access_Control"],
                            "likelihood": "HIGH",
                            "impact": "MEDIUM",
                            "confidence": "LOW",
                            "source": "https://semgrep.dev/r/python.flask.security.audit.app-run-param-config",
                            "shortlink": "https://sg.run/eLby",
                        },
                        "severity": "WARNING",
                        "fingerprint": "requires login",
                        "lines": "requires login",
                    },
                },
            ],
        },
    )
    [f] = parsers.parse("semgrep", raw).findings
    assert (f.severity, f.tool_severity) == ("medium", "WARNING")
    assert f.code_snippet == ""  # semgrep OSS placeholder, not code
    data = f.tool_specific_data
    assert data["tool_confidence"] == "LOW"
    assert data["rule_name"] == "avoid_app_run_with_bad_host"
    assert data["docs_url"] == "https://semgrep.dev/r/python.flask.security.audit.app-run-param-config"
    assert data["references"] == ["https://owasp.org/Top10/A01_2021-Broken_Access_Control"]
    assert data["owasp"] == ["A01:2021 - Broken Access Control"]
    assert (data["likelihood"], data["impact"]) == ("HIGH", "MEDIUM")
    assert (data["end_line"], data["end_column"], data["cwe"]) == (484, 52, "CWE-668")


def test_pylint_message_type_and_docs_url():
    raw = json.dumps(
        [
            {
                "type": "convention",
                "obj": "",
                "line": 251,
                "column": 0,
                "endLine": None,
                "endColumn": None,
                "path": "app.py",
                "symbol": "line-too-long",
                "message": "Line too long (102/100)",
                "message-id": "C0301",
            },
            {
                "type": "info",
                "obj": "f",
                "line": 3,
                "column": 0,
                "path": "app.py",
                "symbol": "locally-disabled",
                "message": "Locally disabling x",
                "message-id": "I0011",
            },
        ],
    )
    conv, info = parsers.parse("pylint", raw).findings
    assert (conv.tool_severity, info.tool_severity) == ("convention", "info")
    assert conv.tool_specific_data == {
        "message_id": "C0301",
        "docs_url": "https://pylint.readthedocs.io/en/stable/user_guide/messages/convention/line-too-long.html",
    }
    # Pylint's "info" messages live under "information" in its docs.
    assert info.tool_specific_data["docs_url"].endswith("/messages/information/locally-disabled.html")


def test_mypy_attaches_notes_to_their_error():
    raw = (
        'app.py:50: error: Variable "app.Base" is not valid as a type  [valid-type]\n'
        "app.py:50: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#variables-vs-type-aliases\n"
        'app.py:60: error: No overload variant of "get" matches argument types "int"  [call-overload]\n'
        "app.py:60: note: Possible overload variants:\n"
        "app.py:99: note: Revealed type is 'int'\n"
    )
    first, second = parsers.parse("mypy", raw).findings
    assert first.tool_severity == "error"
    assert first.tool_specific_data["docs_url"].endswith("#variables-vs-type-aliases")
    assert second.description.endswith("\nPossible overload variants:")
    assert "docs_url" not in second.tool_specific_data


def test_hadolint_levels_and_rule_docs():
    raw = json.dumps(
        [
            {"file": "Dockerfile", "line": 1, "code": "DL3007", "level": "warning", "message": "latest"},
            {"file": "Dockerfile", "line": 3, "code": "SC2164", "level": "style", "message": "cd || exit"},
        ],
    )
    dl, sc = parsers.parse("hadolint", raw).findings
    assert (dl.tool_severity, sc.tool_severity) == ("warning", "style")
    assert dl.tool_specific_data["docs_url"] == "https://github.com/hadolint/hadolint/wiki/DL3007"
    assert sc.tool_specific_data["docs_url"] == "https://www.shellcheck.net/wiki/SC2164"


def test_codespell_fix_and_jscpd_duplicate_location():
    [typo] = parsers.parse("codespell", "/work/App.jsx:1353: floatIn ==> floating, float in\n").findings
    assert typo.tool_specific_data == {"fix": "floating, float in"}
    raw = json.dumps(
        {
            "duplicates": [
                {
                    "lines": 15,
                    "tokens": 124,
                    "fragment": "x",
                    "firstFile": {"name": "src/App.jsx", "start": 2023, "end": 2037},
                    "secondFile": {"name": "src/App.jsx", "start": 1818, "end": 1829},
                },
            ],
        },
    )
    [dup] = parsers.parse("jscpd", raw).findings
    assert dup.tool_severity == ""
    data = dup.tool_specific_data
    assert data["duplicate_of"] == {"file": "src/App.jsx", "start_line": 1818, "end_line": 1829}
    assert (data["lines"], data["tokens"], data["end_line"]) == (15, 124, 2037)
    assert (data["second_file"], data["second_start"]) == ("src/App.jsx", 1818)  # legacy keys kept


def test_radon_complexity_data():
    raw = json.dumps(
        {
            "app.py": [
                {
                    "type": "class",
                    "name": "Task",
                    "rank": "A",
                    "complexity": 3,
                    "lineno": 1,
                    "endline": 90,
                    "methods": [
                        {
                            "type": "method",
                            "name": "to_dict",
                            "classname": "Task",
                            "rank": "F",
                            "complexity": 44,
                            "lineno": 5,
                            "endline": 80,
                        },
                    ],
                },
            ],
        },
    )
    [f] = parsers.parse("radon", raw).findings
    assert f.tool_severity == ""  # a complexity rank, not a severity
    assert f.tool_specific_data == {
        "complexity": 44,
        "rank": "F",
        "function": "Task.to_dict",
        "end_line": 80,
        "endline": 80,
    }


def test_gitleaks_never_stores_the_secret_value():
    secret = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"  # noqa: S105 - a fake token  gitleaks:allow
    item = {
        "RuleID": "github-pat",
        "Description": "Uncovered a GitHub Personal Access Token",
        "File": "config.py",
        "StartLine": 2,
        "EndLine": 2,
        "StartColumn": 16,
        "EndColumn": 57,
        "Entropy": 5.17,
    }
    # As the run command asks for it (--redact) ...
    [redacted] = parsers.parse(
        "gitleaks",
        json.dumps([{**item, "Match": 'TOKEN = "REDACTED"', "Secret": "REDACTED"}]),
    ).findings
    assert redacted.code_snippet == 'TOKEN = "REDACTED"'
    assert redacted.tool_specific_data == {
        "secret_type": "github-pat",
        "entropy": 5.17,
        "end_line": 2,
        "end_column": 57,
    }
    # ... and a report that arrives unredacted: the value is masked anyway.
    leaky = [
        {**item, "Match": f'GITHUB_TOKEN = "{secret}"', "Secret": secret},
        {**item, "Match": "", "Secret": secret},
        {**item, "Match": "token\n<cut>", "Secret": "token\n<cut>\nrest"},
    ]
    findings = parsers.parse("gitleaks", json.dumps(leaky)).findings
    assert findings[0].code_snippet == 'GITHUB_TOKEN = "REDACTED"'
    assert findings[1].code_snippet == ""
    assert findings[2].code_snippet == ""  # only part of the secret: nothing to mask it with
    for f in findings:
        assert secret not in repr(f)
        assert "rest" not in f.code_snippet


# --- Output that is not a clean, complete report ---------------------------


def test_json_report_followed_by_stderr_text_is_still_parsed():
    # exec_in merges stderr into stdout: pylint's JSON, then a warning line.
    report = [{"type": "warning", "symbol": "unused-import", "message": "Unused import os", "path": "app.py"}]
    raw = json.dumps(report, indent=2) + "\nWARNING: something on stderr [x]\n"
    parsed = parsers.parse("pylint", raw)
    assert parsed.error is None
    assert [f.rule_id for f in parsed.findings] == ["unused-import"]


def test_json_report_after_bracketed_log_line_is_found():
    # A bracket inside a log line must not be taken for the report.
    raw = "[INFO] scanning\n" + json.dumps({"results": [], "errors": []}) + "\n"
    assert parsers.parse("bandit", raw).error is None


def test_object_report_is_not_mistaken_for_its_inner_list():
    raw = 'note: {"partial": 1\n' + json.dumps({"results": [{"issue_text": "x", "test_id": "B1"}]})
    assert [f.rule_id for f in parsers.parse("bandit", raw).findings] == ["B1"]


def test_output_without_json_report_is_an_error_not_a_clean_result():
    parsed = parsers.parse("pylint", "Traceback (most recent call last):\n  ...\nKeyError: 'x'\n")
    assert parsed.findings == []
    assert parsed.error is not None
    assert "no JSON report" in parsed.error
    # Truncated JSON is no report either.
    assert parsers.parse("semgrep", '{"results": [{"check_id": "a"').error is not None
    # Empty output stays an empty result (the runner judges it by exit code).
    assert parsers.parse("pylint", "").error is None


def test_bandit_files_it_could_not_parse_are_warnings():
    raw = json.dumps(
        {
            "results": [{"issue_text": "md5", "test_id": "B303", "filename": "a.py"}],
            "errors": [{"filename": "b.py", "reason": "syntax error while parsing AST from file"}],
            "metrics": {"_totals": {}, "a.py": {}, "b.py": {}},
        },
    )
    parsed = parsers.parse("bandit", raw)
    assert parsed.error is None
    assert len(parsed.findings) == 1
    assert parsed.warnings == ["Not scanned (1 file(s)): b.py: syntax error while parsing AST from file"]


def test_bandit_that_could_parse_no_file_is_an_error():
    raw = json.dumps(
        {
            "results": [],
            "errors": [{"filename": "a.py", "reason": "syntax error while parsing AST from file"}],
            "metrics": {"_totals": {}, "a.py": {}},
        },
    )
    parsed = parsers.parse("bandit", raw)
    assert parsed.error is not None
    assert "a.py" in parsed.error


def test_semgrep_errors_and_skipped_files_are_warnings():
    raw = json.dumps(
        {
            "results": [],
            "errors": [
                {
                    "code": 3,
                    "level": "warn",
                    "type": ["PartialParsing", [{"path": "a.js"}]],
                    "message": "Syntax error at line a.js:3:\n `<` was unexpected",
                    "path": "a.js",
                },
            ],
            "paths": {
                "scanned": ["a.js", "b.py"],
                "skipped": [
                    {"path": "node_modules/x.js", "reason": "semgrepignore_patterns_match"},
                    {"path": "big.js", "reason": "exceeded_size_limit"},
                ],
            },
        },
    )
    parsed = parsers.parse("semgrep", raw)
    assert parsed.error is None
    assert parsed.warnings == [
        "Semgrep errors (1): a.js: PartialParsing: Syntax error at line a.js:3:",
        # Ignore-pattern skips are by design: only the size-limit skip is a gap.
        "Not scanned (1 file(s)): big.js: exceeded_size_limit",
    ]


def test_semgrep_that_analyzed_no_file_is_an_error():
    raw = json.dumps(
        {
            "results": [],
            "errors": [{"level": "error", "type": "Syntax error", "message": "Syntax error", "path": "a.py"}],
            "paths": {"scanned": ["a.py"]},
        },
    )
    assert parsers.parse("semgrep", raw).error is not None
    # Nothing scanned and nothing reported as wrong: a plain empty result.
    assert parsers.parse("semgrep", json.dumps({"results": [], "errors": [], "paths": {"scanned": []}})).error is None


def test_mypy_blocking_error_is_not_a_measurement():
    raw = "app.py: error: Duplicate module named 'app' (also at './app.py')\n"
    parsed = parsers.parse("mypy", raw)
    assert parsed.findings == []
    assert "Duplicate module named" in parsed.error

    syntax = parsers.parse("mypy", "models.py:12: error: invalid syntax  [syntax]\n")
    assert [f.rule_id for f in syntax.findings] == ["syntax"]  # kept on the failed result
    assert "models.py:12" in syntax.error

    summary = parsers.parse("mypy", "Found 1 error in 1 file (errors prevented further checking)\n")
    assert summary.error is not None

    ok = parsers.parse("mypy", "app.py:3: error: Incompatible types  [assignment]\n")
    assert ok.error is None
    assert len(ok.findings) == 1


def test_eslint_fatal_parse_errors_are_warnings_not_findings():
    raw = json.dumps(
        [
            {
                "filePath": "/work/a.jsx",
                "messages": [
                    {"ruleId": None, "fatal": True, "severity": 2, "message": "Parsing error: Unexpected token <"},
                ],
            },
            {"filePath": "/work/b.js", "messages": [{"ruleId": "no-undef", "severity": 2, "message": "x", "line": 2}]},
        ],
    )
    parsed = parsers.parse("eslint", raw)
    assert [f.rule_id for f in parsed.findings] == ["no-undef"]
    assert parsed.error is None
    assert parsed.warnings == ["Not parsed (1 of 2 file(s)): /work/a.jsx:?: Parsing error: Unexpected token <"]


def test_eslint_with_every_file_fatal_is_an_error():
    raw = json.dumps(
        [{"filePath": "/work/a.jsx", "messages": [{"ruleId": None, "severity": 2, "message": "Parsing error: x"}]}],
    )
    parsed = parsers.parse("eslint", raw)
    assert parsed.findings == []
    assert parsed.error is not None
    # The security and react flavours share the parser.
    assert parsers.parse("eslint_security", raw).error is not None


def _jscpd_clone(first: tuple[int, int], second: tuple[int, int], lines: int, fmt: str, name: str = "src/App.jsx"):
    return {
        "format": fmt,
        "lines": lines,
        "tokens": lines * 10,
        "fragment": "x",
        "firstFile": {"name": name, "start": first[0], "end": first[1]},
        "secondFile": {"name": name, "start": second[0], "end": second[1]},
    }


def test_jscpd_drops_clones_repeated_by_embedded_language_streams():
    # Run b214ced5: App.jsx was tokenized as javascript, jsx and css, so the
    # same copied block came back once per stream with shifted ranges.
    report = {
        "duplicates": [
            _jscpd_clone((2104, 2141), (1571, 1598), 38, "javascript"),
            _jscpd_clone((2120, 2140), (1573, 1595), 21, "jsx"),
            _jscpd_clone((3519, 3525), (3423, 3429), 7, "javascript"),
            # Backwards range: an artifact of a non-contiguous jsx stream.
            _jscpd_clone((3519, 3535), (3423, 2155), 17, "jsx"),
            # Same lines on one side only: a different clone.
            _jscpd_clone((3519, 3525), (100, 106), 7, "javascript"),
            _jscpd_clone((10, 20), (30, 40), 11, "python", name="app.py"),
        ],
        "statistics": {
            "formats": {
                "css": {"sources": {"src/App.jsx": {"lines": 37, "tokens": 330}}},
                "javascript": {"sources": {"src/App.jsx": {"lines": 3163, "tokens": 32428}}},
                "jsx": {"sources": {"src/App.jsx": {"lines": 3600, "tokens": 5000}}},
                "python": {"sources": {"app.py": {"lines": 400, "tokens": 2242}}},
            },
            "total": {"lines": 7200, "sources": 4, "clones": 6},
        },
    }
    parsed = parsers.parse("jscpd", json.dumps(report))
    assert [(f.file_path, f.line_number, f.tool_specific_data["lines"]) for f in parsed.findings] == [
        ("src/App.jsx", 2104, 38),
        ("src/App.jsx", 3519, 7),
        ("src/App.jsx", 3519, 7),
        ("app.py", 10, 11),
    ]
    assert parsed.findings[2].tool_specific_data["duplicate_of"]["start_line"] == 100
    # One source per file; a file's lines are its longest stream, not the sum.
    assert parsed.metrics == {
        "clones": 4,
        "duplicated_lines": 63,
        "duplicated_tokens": 630,
        "total_lines": 4000,
        "total_sources": 2,
        "duplication_percentage": 1.57,
        "token_duplication_percentage": 1.57,
    }
