from __future__ import annotations

import json

from llm_lab.analysis.services import parsers


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
    findings = parsers.parse("bandit", raw)
    assert len(findings) == 1
    f = findings[0]
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
    findings = parsers.parse("ruff", raw)
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
    findings = parsers.parse("eslint", raw)
    assert {f.severity for f in findings} == {"high", "low"}
    assert findings[0].file_path == "/work/frontend.js"


def test_unknown_parser_returns_empty():
    assert parsers.parse("does-not-exist", "{}") == []


def test_parser_tolerates_leading_noise():
    raw = "warning: something\n" + json.dumps({"results": []})
    assert parsers.parse("bandit", raw) == []
