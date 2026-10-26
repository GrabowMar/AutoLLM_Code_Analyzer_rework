"""Normalization (norm-v1): severity, category, CWE, scope, counted, fingerprint per tool."""

from __future__ import annotations

import hashlib

import pytest

from backend.analysis.engine import normalize
from backend.analysis.tests.support import measure as fx

BASE_APP = "import os\nfrom flask import Flask\n\napp = Flask(__name__)\n"
FINAL_APP = (
    "import os\n"  # 1 scaffold
    "import subprocess\n"  # 2 modified
    "from flask import Flask\n"  # 3 scaffold
    "\n"  # 4 scaffold
    "app = Flask(__name__)\n"  # 5 scaffold
    "SECRET_KEY = 'dev-secret-change-me'\n"  # 6 modified
    "API_TOKEN = 'ghp_realLookingToken1234567890abcdef'\n"  # 7 modified
    "subprocess.call(cmd, shell=True)\n"  # 8 modified
)


@pytest.fixture
def sample():
    return fx.build_sample(
        [
            ("app.py", FINAL_APP, "modified"),
            ("frontend/src/App.jsx", "export default function App() {\n  return <p/>;\n}\n", "added"),
            (".env.example", "SECRET_KEY=change-this-in-production\nAPI_KEY=abc123\n", "scaffold"),
            ("Dockerfile", "FROM python:latest\n", "scaffold"),
            ("frontend/package-lock.json", "{}\n", "added"),
            ("frontend/src/main.jsx", "import React from 'react';\n", "scaffold"),
        ],
        baseline={"app.py": BASE_APP.encode()},
    )


def norm(tool, raw, sample, outcomes=None, scope=None):
    return normalize.normalize(tool, raw, sample, scope or {}, outcomes=outcomes)


def test_mapping_hashes_are_file_digests():
    hashes = normalize.mapping_hashes()
    assert set(hashes) == {"severity", "categories", "cwe", "rule_equivalence"}
    for name, file in normalize.MAPPING_FILES.items():
        assert hashes[name] == hashlib.sha256((normalize.MAPPINGS_DIR / file).read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# Scope / origin / counted
# --------------------------------------------------------------------------


def test_line_level_origin_scope_and_counted(sample):
    on_model_line = norm("bandit", fx.raw("B602", "/src/app.py", 8, native_severity="HIGH"), sample)
    assert on_model_line.file_path == "app.py"
    assert on_model_line.file_origin == "modified"
    assert on_model_line.in_measured_scope is True
    assert on_model_line.counted is True

    on_scaffold_line = norm("ruff", fx.raw("F401", "./app.py", 1), sample)
    assert on_scaffold_line.file_origin == "scaffold"
    assert on_scaffold_line.in_measured_scope is False
    assert on_scaffold_line.counted is False

    file_level = norm("mypy", fx.raw("misc", "app.py", None, native_severity="error"), sample)
    assert file_level.file_origin == "modified"
    assert file_level.line is None


def test_non_measured_files_and_unknown_paths(sample):
    platform = norm("hadolint", fx.raw("DL3007", "Dockerfile", 1, native_severity="warning"), sample)
    assert (platform.file_origin, platform.in_measured_scope, platform.counted) == ("platform", False, False)
    assert platform.category == "container"
    assert platform.severity == "low"

    lock = norm("gitleaks", fx.raw("generic-api-key", "frontend/package-lock.json", 1), sample)
    assert lock.in_measured_scope is False  # excluded file

    ghost = norm("ruff", fx.raw("F821", "nowhere.py", 3), sample)
    assert ghost.file_origin == "unknown"
    assert ghost.in_measured_scope is False
    assert ghost.extra["unknown_file"] is True


def test_measured_origins_from_plan_scope(sample):
    finding = norm(
        "bandit",
        fx.raw("B602", "app.py", 8, native_severity="HIGH"),
        sample,
        scope={"measured_origins": []},
    )
    assert finding.in_measured_scope is False
    assert finding.counted is False


# --------------------------------------------------------------------------
# Per-tool mappings
# --------------------------------------------------------------------------


def test_bandit(sample):
    f = norm("bandit", fx.raw("B602", "app.py", 8, native_severity="HIGH", native_confidence="MEDIUM"), sample)
    assert (f.severity, f.category, f.confidence) == ("high", "security", "medium")
    assert f.cwe == ["CWE-78"]  # from the curated table when the adapter passed none
    own = norm("bandit", fx.raw("B602", "app.py", 8, native_severity="LOW", cwe=["CWE-77"]), sample)
    assert own.cwe == ["CWE-77"]
    assert own.severity == "low"
    password = norm("bandit", fx.raw("B105", "app.py", 7, native_severity="LOW"), sample)
    assert (password.category, password.cwe) == ("secrets", ["CWE-259"])


@pytest.mark.parametrize(
    ("rule", "severity", "category", "cwe", "counted"),
    [
        ("S608", "medium", "security", ["CWE-89"], True),  # bandit-equivalent B608
        ("S602", "high", "security", ["CWE-78"], True),
        ("S311", "low", "security", ["CWE-330"], True),
        ("S105", "low", "secrets", ["CWE-259"], True),
        ("F401", "low", "dead_code", [], True),
        ("F821", "high", "bug", [], True),
        ("F811", "medium", "bug", [], True),
        ("B006", "medium", "bug", [], True),
        ("PLE0604", "high", "bug", [], True),
        ("E501", "info", "style", [], False),
        ("I001", "info", "style", [], False),
        ("UP035", "info", "style", [], False),
        ("C901", "low", "complexity", [], False),
        ("SIM108", "info", "maintainability", [], True),
        ("ERA001", "info", "dead_code", [], True),
        ("ARG001", "low", "dead_code", [], True),
        ("PTH123", "info", "style", [], False),
        ("BLE001", "low", "bug", [], True),
        ("DTZ005", "low", "bug", [], True),
    ],
)
def test_ruff_by_rule_prefix(sample, rule, severity, category, cwe, counted):
    f = norm("ruff", fx.raw(rule, "app.py", 8), sample)
    assert (f.severity, f.category, f.cwe, f.counted) == (severity, category, cwe, counted)


@pytest.mark.parametrize("rule", ["E999", "E902", "", "invalid-syntax"])
def test_ruff_syntax_errors_are_never_counted_findings(sample, rule):
    raw = fx.raw(rule, "app.py", 8, message="SyntaxError: invalid syntax")
    assert normalize.is_parse_error("ruff", raw) is True
    f = norm("ruff", raw, sample)
    assert f.counted is False
    assert f.extra["parse_error"] is True
    findings, unparseable = normalize.normalize_all("ruff", [raw, fx.raw("F401", "app.py", 2)], sample, {})
    assert [x.rule_id for x in findings] == ["F401"]
    assert unparseable == [{"path": "app.py", "reason": "SyntaxError: invalid syntax"}]


@pytest.mark.parametrize(
    ("rule", "native", "severity", "category", "cwe", "counted"),
    [
        ("no-unused-vars", "2", "low", "dead_code", [], True),  # config level "error" is NOT high
        ("no-undef", "1", "high", "bug", [], True),
        ("no-dupe-keys", "2", "high", "bug", [], True),
        ("no-cond-assign", "2", "medium", "bug", [], True),
        ("security/detect-object-injection", "1", "low", "security", ["CWE-915"], True),
        ("security/detect-eval-with-expression", "2", "high", "security", ["CWE-95"], True),
        ("no-unsanitized/property", "2", "medium", "security", ["CWE-79"], True),
        ("react/no-danger", "1", "medium", "security", ["CWE-79"], True),
        ("react-hooks/exhaustive-deps", "1", "low", "bug", [], True),
        ("react/prop-types", "2", "info", "style", [], False),
        ("@typescript-eslint/no-explicit-any", "2", "info", "style", [], False),
        ("@typescript-eslint/no-floating-promises", "2", "low", "maintainability", [], True),
        ("no-some-new-core-rule", "2", "low", "bug", [], True),
        ("acme/odd-rule", "2", "low", "maintainability", [], True),
    ],
)
def test_eslint_by_rule_id_not_config_level(sample, rule, native, severity, category, cwe, counted):
    f = norm("eslint", fx.raw(rule, "frontend/src/App.jsx", 2, native_severity=native), sample)
    assert (f.severity, f.category, f.cwe, f.counted) == (severity, category, cwe, counted)


def test_eslint_fatal_parse_error(sample):
    raw = fx.raw("", "frontend/src/App.jsx", 1, native_severity="2", message="Parsing error: Unexpected token")
    assert normalize.is_parse_error("eslint", raw)
    assert normalize.is_parse_error("eslint", fx.raw("no-undef", "a.js", 1, extra={"fatal": True}))


@pytest.mark.parametrize(
    ("rule", "native", "severity", "category", "cwe", "counted"),
    [
        ("E1120", "error", "high", "bug", [], True),
        ("W0702", "warning", "medium", "bug", [], True),
        ("W0611", "warning", "low", "dead_code", [], True),
        ("unused-import", "warning", "low", "dead_code", [], True),
        ("eval-used", "warning", "medium", "security", ["CWE-95"], True),
        ("R0801", "refactor", "info", "duplication", [], False),
        ("R0912", "refactor", "low", "complexity", [], False),
        ("R1705", "refactor", "low", "maintainability", [], True),
        ("C0116", "convention", "info", "style", [], False),
    ],
)
def test_pylint_by_type_with_overrides(sample, rule, native, severity, category, cwe, counted):
    f = norm("pylint", fx.raw(rule, "app.py", 8, native_severity=native), sample)
    assert (f.severity, f.category, f.cwe, f.counted) == (severity, category, cwe, counted)


def test_pylint_and_mypy_parse_errors(sample):
    assert normalize.is_parse_error("pylint", fx.raw("E0001", "app.py", 1, native_severity="error"))
    assert normalize.is_parse_error("pylint", fx.raw("syntax-error", "app.py", 1))
    assert normalize.is_parse_error("mypy", fx.raw("syntax", "app.py", 1))
    f = norm("mypy", fx.raw("return-value", "app.py", 8, native_severity="error"), sample)
    assert (f.severity, f.category) == ("medium", "type")


@pytest.mark.parametrize(
    ("native", "metadata", "severity"),
    [
        ("ERROR", {}, "high"),
        ("WARNING", {}, "medium"),
        ("INFO", {}, "low"),
        ("CRITICAL", {}, "critical"),
        ("MEDIUM", {}, "medium"),
        ("ERROR", {"impact": "HIGH", "likelihood": "LOW"}, "medium"),
        ("WARNING", {"impact": "HIGH", "likelihood": "MEDIUM"}, "high"),
        ("ERROR", {"impact": "LOW", "likelihood": "HIGH"}, "low"),
        ("ERROR", {"impact": "HIGH"}, "high"),  # one of the two missing: the token decides
    ],
)
def test_semgrep_severity(sample, native, metadata, severity):
    raw = fx.raw("python.lang.security.audit.x", "app.py", 8, native_severity=native, extra={"metadata": metadata})
    assert norm("semgrep", raw, sample).severity == severity


def test_semgrep_category_cwe_and_confidence(sample):
    raw = fx.raw(
        "vendored.python.flask.security.injection.tainted-sql-string.tainted-sql-string",
        "app.py",
        8,
        native_severity="ERROR",
        cwe=["CWE-89: Improper Neutralization of Special Elements used in an SQL Command"],
        extra={"metadata": {"category": "security", "confidence": "HIGH"}},
    )
    f = norm("semgrep", raw, sample)
    assert (f.category, f.cwe, f.confidence) == ("security", ["CWE-89"], "high")
    assert f.extra["rule_family"] == "tainted-sql-string"
    correctness = norm(
        "semgrep",
        fx.raw("python.lang.correctness.useless-eqeq", "app.py", 8, native_severity="INFO", extra={"metadata": {}}),
        sample,
    )
    assert correctness.category == "bug"
    secret = norm(
        "semgrep",
        fx.raw("generic.secrets.security.detected-github-token", "app.py", 7, native_severity="ERROR"),
        sample,
    )
    assert secret.category == "secrets"
    meta_cwe = norm(
        "semgrep",
        fx.raw("x.y", "app.py", 8, native_severity="WARNING", extra={"metadata": {"cwe": ["CWE-78: OS Command"]}}),
        sample,
    )
    assert (meta_cwe.cwe, meta_cwe.category) == (["CWE-78"], "security")


@pytest.mark.parametrize(
    ("confidence", "severity", "conf"),
    [(100, "low", "high"), (90, "low", "high"), (60, "info", "low")],
)
def test_vulture_confidence(sample, confidence, severity, conf):
    f = norm("vulture", fx.raw("unused-function", "app.py", 8, extra={"confidence": confidence}), sample)
    assert (f.severity, f.category, f.confidence) == (severity, "dead_code", conf)


def test_secrets_severity_and_downgrades(sample):
    real = norm("gitleaks", fx.raw("github-pat", "app.py", 7, snippet="REDACTED"), sample)
    assert (real.severity, real.category, real.cwe, real.counted) == ("high", "secrets", ["CWE-798"], True)
    # A secret never carries code: not the source line, not even a snippet the tool supplied.
    assert real.snippet == ""
    generic = norm("gitleaks", fx.raw("generic-api-key", "app.py", 7), sample)
    assert generic.severity == "medium"
    assert generic.snippet == ""

    placeholder = norm("gitleaks", fx.raw("generic-api-key", "app.py", 6), sample)
    assert (placeholder.severity, placeholder.confidence) == ("low", "low")
    assert placeholder.extra["secret_downgrade"] == "placeholder_value"  # noqa: S105 - a reason code
    assert placeholder.extra["severity_before_downgrade"] == "medium"

    example = norm("detect-secrets", fx.raw("Secret Keyword", ".env.example", 2), sample)
    assert example.severity == "low"
    assert example.extra["secret_downgrade"] == "example_file"  # noqa: S105 - a reason code
    assert example.in_measured_scope is False  # env files are never measured

    entropy = norm("detect-secrets", fx.raw("Hex High Entropy String", "app.py", 7), sample)
    assert entropy.severity == "low"
    aws = norm("detect-secrets", fx.raw("AWS Access Key", "app.py", 7), sample)
    assert (aws.severity, aws.cwe) == ("high", ["CWE-798"])
    bandit_placeholder = norm("bandit", fx.raw("B105", "app.py", 6, native_severity="MEDIUM"), sample)
    assert bandit_placeholder.severity == "low"


def test_metric_findings_are_uncounted_by_default(sample):
    radon = norm("radon", fx.raw("cc", "app.py", 8, native_severity="F", extra={"complexity": 45}), sample)
    assert (radon.severity, radon.category, radon.counted) == ("medium", "complexity", False)
    lizard = norm("lizard", fx.raw("ccn", "app.py", 8, extra={"ccn": 25}), sample)
    assert (lizard.severity, lizard.category, lizard.counted) == ("low", "complexity", False)
    jscpd = norm("jscpd", fx.raw("duplicate", "app.py", 8, extra={"lines": 60}), sample)
    assert (jscpd.severity, jscpd.category, jscpd.counted) == ("medium", "duplication", False)
    counted = norm(
        "jscpd",
        fx.raw("duplicate", "app.py", 8, extra={"lines": 5}),
        sample,
        {"count_metric_findings": True},
    )
    assert (counted.severity, counted.counted) == ("info", True)


def test_style_counted_only_when_plan_says_so(sample):
    typo = norm("codespell", fx.raw("recieve", "app.py", 8), sample)
    assert (typo.severity, typo.category, typo.counted) == ("info", "style", False)
    assert norm("codespell", fx.raw("recieve", "app.py", 8), sample, {"count_style": True}).counted is True
    # The executor folds the plan's count_style into the scope it passes.
    in_scope = norm("codespell", fx.raw("recieve", "app.py", 8), sample, scope={"count_style": True})
    assert in_scope.counted is True
    overridden = norm(
        "codespell",
        fx.raw("recieve", "app.py", 8),
        sample,
        {"count_style": False},
        scope={"count_style": True},
    )
    assert overridden.counted is False


def test_unknown_tool_uses_generic_mapping(sample):
    f = norm("newtool", fx.raw("X1", "app.py", 8, native_severity="warning"), sample)
    assert (f.severity, f.category) == ("medium", "other")


def test_ai_review_mapping(sample):
    raw = fx.raw(
        "security",
        "app.py",
        8,
        native_severity="high",
        native_confidence="0.85",
        cwe=["CWE-78"],
        title="Shell injection",
        snippet="subprocess.call(cmd, shell=True)",
        extra={"category": "security", "grounding": "exact", "sample_index": 2},
    )
    f = norm("ai_review", raw, sample)
    assert (f.is_ai, f.counted, f.severity, f.category, f.cwe, f.confidence) == (
        True,
        False,
        "high",
        "security",
        ["CWE-78"],
        "high",
    )
    assert (f.grounding, f.sample_index, f.in_measured_scope, f.file_origin) == ("exact", 2, True, "modified")
    odd = norm("ai_review", fx.raw("nonsense", "app.py", 8, native_severity="EXTREME", native_confidence="x"), sample)
    assert (odd.severity, odd.category, odd.confidence, odd.cwe) == ("info", "other", "medium", [])
    # Secrets downgrades never rewrite the model's own label.
    secret = norm("ai_review", fx.raw("secrets", "app.py", 6, native_severity="high"), sample)
    assert secret.severity == "high"


def test_fingerprint_is_line_shift_stable(sample):
    shifted = fx.build_sample([("app.py", "\n\n" + FINAL_APP, "added")])
    a = norm("bandit", fx.raw("B602", "app.py", 8, native_severity="HIGH"), sample)
    b = norm("bandit", fx.raw("B602", "app.py", 10, native_severity="HIGH"), shifted)
    assert a.fingerprint == b.fingerprint
    other_rule = norm("bandit", fx.raw("B604", "app.py", 8, native_severity="HIGH"), sample)
    assert other_rule.fingerprint != a.fingerprint
    assert normalize.fingerprint(tool_slug="t", rule_family="r", path="p", line_text="a   b") == normalize.fingerprint(
        tool_slug="t",
        rule_family="r",
        path="p",
        line_text=" a b ",
    )
    assert len(a.fingerprint) == 64


def test_snippet_falls_back_to_source_line(sample):
    f = norm("semgrep", fx.raw("x.y", "app.py", 8, native_severity="ERROR", snippet="requires login"), sample)
    assert f.snippet == "subprocess.call(cmd, shell=True)"


def test_normalize_cwe():
    assert normalize.normalize_cwe(["CWE-89: SQLi", "cwe-89", 79, "CWE 22"]) == ["CWE-22", "CWE-79", "CWE-89"]
    assert normalize.normalize_cwe("CWE-798") == ["CWE-798"]
    assert normalize.normalize_cwe(None) == []


def test_equivalence_and_category_tables():
    assert "unused-import" in normalize.equivalence_classes("ruff", "F401")
    assert normalize.equivalence_classes("ruff", "F401") & normalize.equivalence_classes("pylint", "unused-import")
    assert "sql-injection" in normalize.equivalence_classes(
        "semgrep",
        "vendored.python.flask.security.injection.tainted-sql-string.tainted-sql-string",
    )
    assert normalize.equivalence_classes("ruff", "E501") == frozenset()
    assert "security" in normalize.emitted_categories("ruff")
    assert normalize.emitted_categories("brand-new", "secrets") == ["secrets"]
    assert normalize.uncounted_categories(None) == {"style", "complexity", "duplication"}
    assert normalize.uncounted_categories({"count_style": True, "count_metric_findings": True}) == set()


# --------------------------------------------------------------------------
# Context-aware ruff severities, secret redaction, mapping table freshness
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "message", "severity", "basis"),
    [
        (
            "S602",
            "`subprocess` call with `shell=True` seems safe, but may be changed in the future; "
            "consider rewriting without `shell`",
            "low",
            "message",
        ),
        ("S602", "`subprocess` call with `shell=True` identified, security issue", "high", "message"),
        (
            "S605",
            "Starting a process with a shell: seems safe, but may be changed in the future; "
            "consider rewriting without `shell`",
            "low",
            "message",
        ),
        ("S605", "Starting a process with a shell, possible injection detected", "high", "message"),
        ("S103", "`os.chmod` setting a permissive mask `0o777` on file or directory", "high", "message"),
        ("S103", "`os.chmod` setting a permissive mask `0o755` on file or directory", "medium", "message"),
        ("S505", "`RSA` key sizes below `2048` bits are considered breakable", "medium", None),
        ("S202", "Uses of `tarfile.extractall()`", "high", "bandit_default"),
    ],
)
def test_ruff_security_severity_follows_the_context_ruff_reports(sample, rule, message, severity, basis):
    """bandit rates a literal shell command LOW and a built one HIGH; ruff says which in its
    message. Only where ruff cannot tell does the bandit default stand in (marked as a proxy)."""
    f = norm("ruff", fx.raw(rule, "app.py", 8, message=message), sample)
    assert (f.severity, f.category) == (severity, "security")
    assert f.extra.get("severity_basis") == basis


def test_merged_issue_takes_bandits_own_severity_over_a_ruff_proxy(sample):
    from backend.analysis.engine import dedup

    literal = "subprocess.call('ls', shell=True)"
    smp = fx.build_sample([("app.py", f"import subprocess\n{literal}\n", "added")])
    bandit = norm("bandit", fx.raw("B602", "app.py", 2, native_severity="LOW", native_confidence="HIGH"), smp)
    # A ruff finding without the context words (e.g. an older message) falls back to the proxy...
    proxy = norm("ruff", fx.raw("S602", "app.py", 2, message="S602"), smp)
    assert (proxy.severity, proxy.extra["severity_basis"]) == ("high", "bandit_default")
    dedup.assign_issue_keys([bandit, proxy])
    assert bandit.issue_key == proxy.issue_key
    # ...which never outranks the severity bandit derived from the code.
    assert dedup.issue_summary([bandit, proxy])["severity"] == "low"
    semgrep = norm("semgrep", fx.raw("x.subprocess-shell-true", "app.py", 2, native_severity="ERROR"), smp)
    assert dedup.issue_summary([bandit, proxy, semgrep])["severity"] == "high"  # another tool's own evidence


def test_secret_findings_never_carry_the_secret():
    text = (
        "import os\n"
        'SECRET_KEY = "flask-canary-signing-key-3f9a"\n'  # 2
        'DB_PASSWORD = "Tr0ub4dor&3-canary"\n'  # 3
        'AWS_ACCESS_KEY_ID = "AKIAZ7QX3LKD5PMW2N4H"\n'  # 4
        "def login(password='hunter2realpass'):\n"  # 5
        "    return password\n"
    )
    smp = fx.build_sample([("app.py", text, "added")])
    context = '2 SECRET_KEY = "flask-canary-signing-key-3f9a"\n3 DB_PASSWORD = "Tr0ub4dor&3-canary"\n'
    b105 = norm(
        "bandit",
        fx.raw(
            "B105",
            "app.py",
            3,
            native_severity="LOW",
            message="Possible hardcoded password: 'Tr0ub4dor&3-canary'",
            title="hardcoded_password_string",
            snippet=context,
        ),
        smp,
    )
    b107 = norm(
        "bandit",
        fx.raw("B107", "app.py", 5, native_severity="LOW", message="Possible hardcoded password: 'hunter2realpass'"),
        smp,
    )
    ai = norm(
        "ai_review",
        fx.raw(
            "secrets",
            "app.py",
            4,
            native_severity="high",
            title="AWS key AKIAZ7QX3LKD5PMW2N4H committed",
            description="`AKIAZ7QX3LKD5PMW2N4H` is hardcoded",
            suggestion="Read it from os.environ['AWS_ACCESS_KEY_ID']",
            snippet='AWS_ACCESS_KEY_ID = "AKIAZ7QX3LKD5PMW2N4H"',
            extra={"category": "secrets"},
        ),
        smp,
    )
    secrets = ("flask-canary-signing-key-3f9a", "Tr0ub4dor&3-canary", "AKIAZ7QX3LKD5PMW2N4H", "hunter2realpass")
    for f in (b105, b107, ai):
        assert f.category == "secrets"
        assert f.snippet == ""
        fields = (f.title, f.description, f.suggestion, f.snippet, str(f.extra.get("message", "")))
        assert not any(secret in field for secret in secrets for field in fields), fields
    assert b105.description == "Possible hardcoded password: '***'"
    assert ai.description == "`***` is hardcoded"
    assert ai.suggestion == "Read it from os.environ['AWS_ACCESS_KEY_ID']"  # a model's identifiers stay
    assert b105.title == "hardcoded_password_string"

    # Any other finding's snippet loses the values of credential-looking lines too.
    aws_line = 'AWS_ACCESS_KEY_ID = "AKIAZ7QX3LKD5PMW2N4H"'
    unused = norm("eslint", fx.raw("no-unused-vars", "app.py", 4, snippet=aws_line), smp)
    assert unused.snippet == 'AWS_ACCESS_KEY_ID = "***"'
    refilled = norm("ruff", fx.raw("F841", "app.py", 3), smp)
    assert refilled.snippet == 'DB_PASSWORD = "***"'
    plain = norm("ruff", fx.raw("F401", "app.py", 1), smp)
    assert plain.snippet == "import os"


def test_mapping_tables_follow_the_files_and_hashes_describe_what_is_used(tmp_path, monkeypatch, sample):
    """Tables are not frozen per process: an edit takes effect at once, and mapping_hashes (what a
    plan records) always describes the tables this process maps with; findings carry the digest."""
    import os
    import shutil

    mappings = tmp_path / "mappings"
    shutil.copytree(normalize.MAPPINGS_DIR, mappings)
    monkeypatch.setattr(normalize, "MAPPINGS_DIR", mappings)
    monkeypatch.setattr(normalize, "_LOADED", {})
    before = normalize.mapping_hashes()
    finding = fx.raw("B602", "app.py", 8, native_severity="HIGH")
    first = norm("bandit", finding, sample)
    assert first.severity == "high"
    assert first.extra["mapping_digest"] == normalize.mapping_digest(before)

    severity = mappings / "severity.yaml"
    bandit_tokens = "native: {HIGH: high, MEDIUM: medium, LOW: low}"
    severity.write_text(
        severity.read_text().replace(bandit_tokens, "native: {HIGH: low, MEDIUM: medium, LOW: low}", 1),
    )
    stat = severity.stat()
    os.utime(severity, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    after = normalize.mapping_hashes()
    assert after["severity"] != before["severity"]
    assert after["severity"] == hashlib.sha256(severity.read_bytes()).hexdigest()
    second = norm("bandit", finding, sample)
    assert second.severity == "low"  # same process, no restart
    assert second.extra["mapping_digest"] == normalize.mapping_digest(after) != first.extra["mapping_digest"]
    assert normalize.mapping_drift(before) == ["severity"]
    assert normalize.mapping_drift(after) == []
    assert normalize.mapping_drift({}) == []
