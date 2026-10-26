"""Adapters replayed against REAL outputs of the pinned analyzer image.

``tests/fixtures/tool_outputs/<slug>/<case>.json`` are complete ExecOutcomes
captured by ``tests/support/capture_tool_outputs.py`` (production sandbox
constraints). Failure modes that cannot be captured on demand (missing or
corrupted reports, unexpected exit codes) are derived from real outcomes.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from backend.analysis.engine import catalog
from backend.analysis.engine.adapters import get_adapter
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import ToolFailure
from backend.analysis.tests.support import capture_tool_outputs as cap

STATIC_SLUGS = [s.slug for s in catalog.static_specs()]
ALL_FIXTURES = sorted(
    (path.parent.name, path.stem) for path in cap.FIXTURE_DIR.glob("*/*.json") if path.parent.name in STATIC_SLUGS
)
# Report written to /out by each tool (stdout-only tools: None).
REPORT_FILES = {
    "ruff": "ruff.json",
    "bandit": "bandit.json",
    "semgrep": "semgrep.json",
    "pylint": "pylint.json",
    "mypy": "mypy-files.json",
    "eslint": "eslint.json",
    "gitleaks": "gitleaks.json",
    "jscpd": "jscpd/jscpd-report.json",
    "radon": "radon-cc.json",
    "lizard": "lizard.csv",
    "vulture": None,
    "detect-secrets": None,
    "codespell": None,
    "hadolint": None,
    "flake8": "flake8.txt",
    "perflint": "perflint.json",
    "stylelint": "stylelint.json",
    "deptry": None,  # per-project files: test_adapters_ported
    "depcheck": None,
    "htmlhint": None,
    "shellcheck": None,
}
JSON_STDOUT = {"detect-secrets", "hadolint", "htmlhint", "shellcheck"}
# Exit status 2 is a valid result for these (pylint/perflint: error-message bit; stylelint: problems).
EXIT_2_IS_A_RESULT = {"pylint", "perflint", "stylelint"}


def interpret(slug, case, **overrides):
    fixture = cap.load_fixture(slug, case)
    files = cap.replay_files(fixture)
    return get_adapter(slug).interpret(cap.replay_outcome(fixture, **overrides), files, fixture["config"])


def keys(parse, prefix=""):
    return sorted((f.file_path, f.line, f.rule_id) for f in parse.findings if f.file_path.startswith(prefix))


def rules(parse):
    return {f.rule_id for f in parse.findings}


def failure(slug, case, **overrides):
    with pytest.raises(ToolFailure) as exc:
        interpret(slug, case, **overrides)
    return exc.value


# --------------------------------------------------------------------------
# Every captured fixture
# --------------------------------------------------------------------------


def test_every_tool_has_golden_outputs():
    captured = {slug for slug, _ in ALL_FIXTURES}
    assert captured == set(STATIC_SLUGS)
    for slug in STATIC_SLUGS:
        assert (slug, "findings") in ALL_FIXTURES
        assert (slug, "config") in ALL_FIXTURES


@pytest.mark.parametrize(("slug", "case"), ALL_FIXTURES)
def test_fixture_matches_the_current_adapter(slug, case):
    """The recorded argv is what the adapter builds today (else re-capture the fixtures)."""
    fixture = cap.load_fixture(slug, case)
    assert fixture["tool_version"] == catalog.get_spec(slug).version
    files = cap.replay_files(fixture)
    invocation = get_adapter(slug).invocation(files, fixture["config"])
    assert invocation.argv == fixture["invocation"]["argv"]
    assert list(invocation.report_files) == fixture["invocation"]["report_files"]
    assert invocation.env == fixture["invocation"]["env"]


@pytest.mark.parametrize(("slug", "case"), ALL_FIXTURES)
def test_fixture_selection_matches_select_files(slug, case):
    """The recorded files are what select_files picks today from the case's sample."""
    spec = next(c for c in cap.cases_for(slug) if c.name == case)
    adapter = get_adapter(slug)
    selected = adapter.select_files(cap.make_sample(spec.files), adapter.effective_config(spec.config))
    assert [f.path for f in selected] == [f["path"] for f in cap.load_fixture(slug, case)["files"]]


@pytest.mark.parametrize(
    ("slug", "case"),
    [(s, c) for s, c in ALL_FIXTURES if c not in ("timeout", "oom")],
)
def test_successful_fixtures_parse_into_relative_paths(slug, case):
    fixture = cap.load_fixture(slug, case)
    parse = interpret(slug, case)
    selected = {f["path"] for f in fixture["files"]}
    for finding in parse.findings:
        assert finding.file_path in selected, finding
        assert not finding.file_path.startswith(("/", "./"))
        assert finding.line is None or finding.line >= 1
        assert finding.rule_id
        assert "requires login" not in finding.snippet
    cov = parse.coverage
    assert cov["files_selected"] == len(selected)
    assert cov["files_scanned"] == len(cov["scanned"]) >= 1
    assert set(cov["scanned"]) <= selected
    assert {u["path"] for u in cov["unparseable"]} <= selected
    assert json.dumps(parse.metrics)  # JSON-serializable for ToolResult.metrics


@pytest.mark.parametrize("slug", sorted({s for s, c in ALL_FIXTURES if c == "clean"}))
def test_clean_fixture_yields_no_findings(slug):
    parse = interpret(slug, "clean")
    assert parse.findings == []
    assert parse.coverage["unparseable"] == []


@pytest.mark.parametrize("slug", sorted({s for s, c in ALL_FIXTURES if c == "hostile"}))
def test_app_shipped_configs_cannot_change_findings(slug):
    """pyproject/setup.cfg/.semgrepignore/.gitleaksignore/.jscpd.json/eslint.config.js... are ignored."""
    plain = keys(interpret(slug, "findings"), "vulnerable/")
    hostile_parse = interpret(slug, "hostile")
    canary = {f.path for f in cap.replay_files(cap.load_fixture(slug, "findings"))}
    hostile = sorted(k for k in keys(hostile_parse, "vulnerable/") if k[0] in canary)
    assert hostile == plain
    assert plain


# --------------------------------------------------------------------------
# Failure modes (derived from real outcomes)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("slug", STATIC_SLUGS)
@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"timed_out": True, "exit_code": 137}, "timeout"),
        ({"oom_killed": True, "exit_code": 137}, "oom"),
        ({"exit_code": 137}, "oom"),
        ({"cancelled": True, "exit_code": None}, "cancelled"),
        ({"infra_error": "NotFound: no such container", "exit_code": None}, "infra_error"),
        ({"exit_code": 127, "stderr": b"sh: 1: tool: not found", "stdout": b"", "out_files": {}}, "unavailable"),
    ],
)
def test_runtime_failures_never_become_results(slug, overrides, reason):
    assert failure(slug, "findings", **overrides).reason == reason


@pytest.mark.parametrize("slug", [s for s in STATIC_SLUGS if s not in EXIT_2_IS_A_RESULT])
def test_unexpected_exit_code_fails(slug):
    assert failure(slug, "findings", exit_code=2, stderr=b"Oops! Something went wrong").reason == "tool_error"


@pytest.mark.parametrize("slug", [s for s, r in REPORT_FILES.items() if r])
def test_missing_report_fails(slug):
    fixture = cap.load_fixture(slug, "findings")
    out_files = {k: cap._unblob(v) for k, v in fixture["outcome"]["out_files"].items() if k != REPORT_FILES[slug]}
    assert failure(slug, "findings", out_files=out_files).reason in ("tool_error", "parse_error")


@pytest.mark.parametrize("slug", [s for s, r in REPORT_FILES.items() if r and s not in ("lizard", "flake8")])
def test_truncated_report_fails(slug):
    fixture = cap.load_fixture(slug, "findings")
    out_files = {k: cap._unblob(v) for k, v in fixture["outcome"]["out_files"].items()}
    report = out_files[REPORT_FILES[slug]]
    out_files[REPORT_FILES[slug]] = report[: len(report) // 2]
    assert failure(slug, "findings", out_files=out_files).reason == "parse_error"


@pytest.mark.parametrize("slug", sorted(JSON_STDOUT))
def test_invalid_stdout_json_fails(slug):
    assert failure(slug, "findings", stdout=b"Traceback (most recent call last):\n").reason == "parse_error"
    assert failure(slug, "findings", stdout=b"").reason == "parse_error"


@pytest.mark.parametrize(
    "slug",
    ["vulture", "codespell", "mypy", "hadolint", "detect-secrets", "htmlhint", "shellcheck"],
)
def test_truncated_stdout_fails_tools_that_print_their_results(slug):
    """A capped stdout would silently lose findings of text/JSON-lines formats."""
    assert failure(slug, "findings", stdout_truncated=True).reason == "parse_error"


@pytest.mark.parametrize(("slug", "case"), [("semgrep", "timeout"), ("semgrep", "oom"), ("eslint", "oom")])
def test_real_timeout_and_oom_outcomes(slug, case):
    fixture = cap.load_fixture(slug, case)
    expected = "timeout" if case == "timeout" else "oom"
    assert fixture["outcome"]["timed_out"] is (case == "timeout")
    assert fixture["outcome"]["oom_killed"] is (case == "oom")
    assert failure(slug, case).reason == expected


@pytest.mark.parametrize(
    ("slug", "case"),
    [
        (s, "syntax")
        for s in (
            "ruff",
            "bandit",
            "pylint",
            "mypy",
            "vulture",
            "radon",
            "eslint",
            "hadolint",
            "flake8",
            "perflint",
            "deptry",
            "depcheck",
            "shellcheck",
        )
    ],
)
def test_all_files_unparseable_is_a_failure(slug, case):
    """A tool that could analyse none of its files measured nothing: failed, not 0 findings."""
    fixture = cap.load_fixture(slug, case)
    parse = interpret(slug, case)
    bad = {u["path"] for u in parse.coverage["unparseable"]}
    assert bad
    only_bad = [f for f in cap.replay_files(fixture) if f.path in bad]
    with pytest.raises(ToolFailure) as exc:
        get_adapter(slug).interpret(cap.replay_outcome(fixture), only_bad, fixture["config"])
    assert exc.value.reason == "tool_error"


# --------------------------------------------------------------------------
# ruff
# --------------------------------------------------------------------------


def test_ruff_findings():
    parse = interpret("ruff", "findings")
    assert rules(parse) == {"F401", "F841", "S105", "S608", "S602", "S301", "S506", "S307", "S324", "S104", "S201"}
    sql = next(f for f in parse.findings if f.rule_id == "S608")
    assert (sql.file_path, sql.line, sql.column) == ("vulnerable/backend/app.py", 38, 20)
    assert sql.native_severity == ""
    assert sql.extra["url"].startswith("https://docs.astral.sh/ruff/rules/")


def test_ruff_syntax_errors_are_coverage_not_findings():
    parse = interpret("ruff", "syntax")
    assert parse.findings == []
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["svc/broken.py"]
    assert parse.coverage["scanned"] == ["svc/ok.py"]


def test_ruff_ignores_noqa_and_config():
    assert rules(interpret("ruff", "suppressed")) == {"F401", "S307", "S105"}
    assert rules(interpret("ruff", "config")) == {"F401", "F841"}  # select=F


def test_ruff_exit_code_must_agree_with_report():
    assert failure("ruff", "findings", exit_code=0).reason == "parse_error"
    assert failure("ruff", "clean", exit_code=1).reason == "parse_error"


# --------------------------------------------------------------------------
# bandit
# --------------------------------------------------------------------------


def test_bandit_findings_with_cwe():
    parse = interpret("bandit", "findings")
    by_rule = {f.rule_id: f for f in parse.findings}
    assert set(by_rule) >= {"B608", "B602", "B301", "B506", "B307", "B324", "B105", "B201", "B104", "B403"}
    shell = by_rule["B602"]
    assert (shell.native_severity, shell.native_confidence, shell.cwe) == ("HIGH", "HIGH", ["CWE-78"])
    assert shell.line == 45
    assert shell.snippet
    assert parse.metrics["loc"] > 0


def test_bandit_errors_are_unparseable_files():
    parse = interpret("bandit", "syntax")
    assert parse.coverage["unparseable"] == [
        {"path": "svc/broken.py", "reason": "syntax error while parsing AST from file"},
    ]


def test_bandit_ignores_nosec_and_applies_levels():
    assert rules(interpret("bandit", "suppressed")) == {"B307", "B105"}
    high = interpret("bandit", "config")  # severity=high, confidence=high
    assert {(f.native_severity, f.native_confidence) for f in high.findings} == {("HIGH", "HIGH")}


def test_bandit_exit_code_must_agree_with_report():
    assert failure("bandit", "findings", exit_code=0).reason == "parse_error"


# --------------------------------------------------------------------------
# semgrep
# --------------------------------------------------------------------------


def test_semgrep_findings():
    parse = interpret("semgrep", "findings")
    assert len(parse.findings) == 24
    react_rule = "typescript.react.security.audit.react-dangerouslysetinnerhtml.react-dangerouslysetinnerhtml"
    react_hits = sorted((f.file_path, f.line) for f in parse.findings if f.rule_id == react_rule)
    assert react_hits == [("vulnerable/frontend/src/App.jsx", 8), ("vulnerable/frontend/src/Banner.tsx", 16)]
    react = next(f for f in parse.findings if f.rule_id == react_rule)
    assert react.cwe == ["CWE-79"]
    assert react.title == "react-dangerouslysetinnerhtml"
    assert react.native_severity == "WARNING"
    assert react.extra["metadata"]["category"] == "security"
    assert react.extra["metadata"]["likelihood"] == "MEDIUM"
    assert "avoid-v-html" in " ".join(f.rule_id for f in parse.findings if f.file_path.endswith("Card.vue"))
    # "requires login" placeholders are dropped, never stored as code.
    assert all(f.snippet == "" and f.extra["fingerprint"] == "" for f in parse.findings)
    # 15 canary files + reports.py, main.jsx and the two index.html of the ported-tool controls.
    assert parse.coverage["files_scanned"] == 19


def test_semgrep_nosemgrep_is_ignored():
    assert len(interpret("semgrep", "suppressed").findings) == 3


def test_semgrep_parse_errors_and_partial_parses():
    parse = interpret("semgrep", "syntax")
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["web/broken.js"]
    assert [p["path"] for p in parse.coverage["partial"]] == ["svc/broken.py"]
    assert parse.coverage["scanned"] == ["svc/broken.py", "svc/ok.py"]


def test_semgrep_rulesets_config_limits_what_is_scanned():
    fixture = cap.load_fixture("semgrep", "config")  # rulesets=[python]
    assert {f["language"] for f in fixture["files"]} == {"python"}
    parse = interpret("semgrep", "config")
    assert all(f.rule_id.startswith("python.") for f in parse.findings)
    assert parse.coverage["skipped"] == []
    assert parse.metrics["rulesets"] == ["python"]


def test_semgrep_rule_errors_fail_the_tool():
    fixture = cap.load_fixture("semgrep", "findings")
    report = json.loads(cap._unblob(fixture["outcome"]["out_files"]["semgrep.json"]))
    report["errors"] = [{"code": 7, "level": "error", "type": "Rule parse error", "message": "Invalid rule schema"}]
    out = {"semgrep.json": json.dumps(report).encode()}
    exc = failure("semgrep", "findings", out_files=out)
    assert exc.reason == "tool_error"
    assert "Invalid rule schema" in exc.message
    del report["paths"]
    assert failure("semgrep", "findings", out_files={"semgrep.json": json.dumps(report).encode()}).reason == (
        "tool_error"
    )


# --------------------------------------------------------------------------
# pylint
# --------------------------------------------------------------------------


def test_pylint_findings():
    parse = interpret("pylint", "findings")
    assert keys(parse) == [
        ("vulnerable/backend/app.py", 9, "unused-import"),
        ("vulnerable/backend/app.py", 64, "eval-used"),
        ("vulnerable/backend/app.py", 79, "unreachable"),
    ]
    eval_used = next(f for f in parse.findings if f.rule_id == "eval-used")
    assert (eval_used.native_severity, eval_used.extra["message_id"]) == ("warning", "W0123")


def test_pylint_recovers_inline_suppressed_messages():
    parse = interpret("pylint", "suppressed")
    assert keys(parse, "suppressed/app.py") == [
        ("suppressed/app.py", 1, "unused-import"),
        ("suppressed/app.py", 5, "eval-used"),
    ]
    app = [f for f in parse.findings if f.file_path == "suppressed/app.py"]
    assert all(f.extra["suppressed_inline"] for f in app)
    assert {f.native_severity for f in app} == {"warning"}
    assert parse.metrics["suppressed_inline"] == 2
    # suppressed/perf.py disables a perflint message pylint 4 does not know: pylint's own warning.
    assert keys(parse, "suppressed/perf.py") == [("suppressed/perf.py", 3, "unknown-option-value")]


def test_pylint_config_enables_refactor_messages():
    parse = interpret("pylint", "config")  # include_refactor, extra_disable=unused-import
    assert {"too-many-branches", "too-many-statements"} <= rules(parse)
    assert "unused-import" not in rules(parse)


def test_pylint_syntax_error_is_coverage():
    parse = interpret("pylint", "syntax")
    assert parse.findings == []
    assert parse.coverage["unparseable"][0]["path"] == "svc/broken.py"
    assert "E0001" in parse.coverage["unparseable"][0]["reason"]


def test_pylint_usage_error_and_config_problems_fail():
    assert failure("pylint", "findings", exit_code=32).reason == "tool_error"
    fixture = cap.load_fixture("pylint", "findings")
    report = json.loads(cap._unblob(fixture["outcome"]["out_files"]["pylint.json"]))
    report["messages"].insert(
        0,
        {
            "type": "error",
            "symbol": "unrecognized-option",
            "message": "Unrecognized option found: suggestion-mode",
            "messageId": "E0015",
            "path": "/opt/analyzer/config/pylintrc",
            "line": 1,
            "column": 0,
        },
    )
    out = {"pylint.json": json.dumps(report).encode()}
    assert failure("pylint", "findings", exit_code=2, out_files=out).reason == "tool_error"


def test_pylint_message_map_matches_the_pinned_version():
    from backend.analysis.engine.adapters import pylint as pylint_adapter

    data = json.loads(pylint_adapter.MESSAGES_FILE.read_text())
    assert data["pylint"] == catalog.get_spec("pylint").version
    assert pylint_adapter.message_ids()["unused-import"] == "W0611"


# --------------------------------------------------------------------------
# mypy
# --------------------------------------------------------------------------


def test_mypy_findings():
    parse = interpret("mypy", "findings")
    assert keys(parse) == [("vulnerable/backend/app.py", 74, "return-value")]
    finding = parse.findings[0]
    assert finding.native_severity == "error"
    assert finding.column == 12


def test_mypy_prefilter_keeps_checking_parseable_files():
    parse = interpret("mypy", "syntax")
    assert parse.coverage["unparseable"] == [{"path": "svc/broken.py", "reason": "line 1: '(' was never closed"}]
    assert parse.coverage["scanned"] == ["svc/ok.py"]


def test_mypy_config_disables_untyped_checks():
    fixture = cap.load_fixture("mypy", "config")
    assert "--no-check-untyped-defs" in fixture["invocation"]["argv"]
    assert fixture["invocation"]["env"] == {"MYPYPATH": "/src"}


def test_mypy_output_must_be_json_lines():
    assert failure("mypy", "findings", stdout=b"app.py:1: error: nope\n").reason == "parse_error"
    assert failure("mypy", "findings", stdout=b"").reason == "parse_error"  # exit 1 without errors
    assert failure("mypy", "findings", exit_code=2, stdout=b"app.py: error: Duplicate module").reason == ("tool_error")


def test_mypy_notes_attach_to_the_previous_error():
    fixture = cap.load_fixture("mypy", "findings")
    note = {
        "file": "/src/vulnerable/backend/app.py",
        "line": 74,
        "column": 11,
        "message": "See https://mypy.readthedocs.io",
        "hint": None,
        "code": None,
        "severity": "note",
    }
    stdout = cap._unblob(fixture["outcome"]["stdout"]) + json.dumps(note).encode() + b"\n"
    parse = interpret("mypy", "findings", stdout=stdout)
    assert len(parse.findings) == 1
    assert "See https://mypy.readthedocs.io" in parse.findings[0].description


# --------------------------------------------------------------------------
# vulture
# --------------------------------------------------------------------------


def test_vulture_findings():
    parse = interpret("vulture", "findings")
    assert keys(parse) == [
        ("vulnerable/backend/app.py", 9, "unused-import"),
        ("vulnerable/backend/app.py", 79, "unreachable-code"),
    ]
    assert {f.extra["confidence"] for f in parse.findings} == {90, 100}


def test_vulture_min_confidence_config():
    parse = interpret("vulture", "config")  # min_confidence=60
    assert "unused-function" in rules(parse)
    legacy = [f for f in parse.findings if "_legacy_export" in f.message]
    assert legacy
    assert legacy[0].extra["confidence"] == 60


def test_vulture_syntax_errors_are_coverage():
    parse = interpret("vulture", "syntax")
    assert parse.coverage["unparseable"][0]["path"] == "svc/broken.py"


def test_vulture_output_is_strict():
    assert failure("vulture", "findings", stdout=b"garbage line\n").reason == "parse_error"
    assert failure("vulture", "findings", exit_code=0).reason == "parse_error"
    assert failure("vulture", "clean", exit_code=3).reason == "parse_error"
    assert failure("vulture", "clean", exit_code=1).reason == "tool_error"


# --------------------------------------------------------------------------
# eslint
# --------------------------------------------------------------------------


def test_eslint_findings_and_jsx_awareness():
    parse = interpret("eslint", "findings")
    assert keys(parse) == [
        ("vulnerable/frontend/src/App.jsx", 13, "no-unused-vars"),
        ("vulnerable/frontend/src/App.jsx", 14, "no-eval"),
        ("vulnerable/frontend/src/App.jsx", 14, "security/detect-eval-with-expression"),
        ("vulnerable/frontend/src/App.jsx", 17, "no-unsanitized/property"),
        ("vulnerable/frontend/src/Banner.tsx", 12, "@typescript-eslint/no-unused-vars"),
        ("vulnerable/frontend/src/Card.vue", 5, "vue/no-v-html"),
        ("vulnerable/frontend/src/api.ts", 8, "@typescript-eslint/no-unused-vars"),
        ("vulnerable/frontend/src/api.ts", 9, "no-eval"),
        ("vulnerable/frontend/src/api.ts", 9, "security/detect-eval-with-expression"),
    ]
    # Header, Footer, Preview and Icon are used only as JSX elements: never "unused".
    unused = [f.message for f in parse.findings if "unused" in f.rule_id]
    assert not any(name in m for m in unused for name in ("Header", "Footer", "Preview", "Icon"))
    assert {f.native_severity for f in parse.findings} == {"2"}
    assert parse.coverage["files_scanned"] == 12  # .jsx, .ts, .tsx and .vue files are all linted (main.jsx too)


def test_eslint_inline_disables_and_vue_template_comments_are_ignored():
    assert keys(interpret("eslint", "suppressed")) == [
        ("suppressed/Raw.vue", 3, "vue/no-v-html"),
        ("suppressed/run.js", 2, "no-eval"),
        ("suppressed/run.js", 2, "security/detect-eval-with-expression"),
        ("suppressed/run.js", 4, "no-unused-vars"),
    ]


def test_eslint_parse_errors_are_coverage():
    parse = interpret("eslint", "syntax")
    assert parse.findings == []
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["web/Broken.vue", "web/broken.jsx", "web/broken.ts"]
    assert parse.coverage["scanned"] == ["web/ok.jsx"]


def test_eslint_ignored_files_are_skipped_not_clean():
    fixture = cap.load_fixture("eslint", "clean")
    report = json.loads(cap._unblob(fixture["outcome"]["out_files"]["eslint.json"]))
    for result in report:
        result["messages"] = [{"fatal": False, "severity": 1, "message": "File ignored because outside of base path."}]
        result["warningCount"] = 1
    out = {"eslint.json": json.dumps(report).encode()}
    with pytest.raises(ToolFailure) as exc:
        get_adapter("eslint").interpret(
            cap.replay_outcome(fixture, out_files=out),
            cap.replay_files(fixture),
            fixture["config"],
        )
    assert exc.value.reason == "tool_error"
    assert "outside of base path" in exc.value.message


def test_eslint_exit_code_must_agree_with_report():
    assert failure("eslint", "findings", exit_code=0).reason == "parse_error"
    assert failure("eslint", "clean", exit_code=1).reason == "parse_error"


def test_eslint_opt_in_rules_reach_argv():
    argv = cap.load_fixture("eslint", "config")["invocation"]["argv"]
    assert '{"security/detect-object-injection": "error"}' in argv


# --------------------------------------------------------------------------
# gitleaks / detect-secrets
# --------------------------------------------------------------------------


def test_gitleaks_findings_and_staging():
    parse = interpret("gitleaks", "findings")
    assert keys(parse) == [
        ("vulnerable/backend/app.py", 25, "aws-access-token"),
        ("vulnerable/backend/app.py", 26, "generic-api-key"),
    ]
    assert parse.findings[0].snippet == ""  # redacted at the source
    assert all(f.extra["fingerprint"].startswith("vulnerable/backend/app.py:") for f in parse.findings)
    argv = cap.load_fixture("gitleaks", "findings")["invocation"]["argv"]
    assert "--ignore-gitleaks-allow" in argv[2]
    assert "--redact" in argv[2]


def test_gitleaks_allow_comments_and_ignore_files_do_not_hide_leaks():
    assert keys(interpret("gitleaks", "suppressed")) == [
        ("suppressed/app.py", 8, "generic-api-key"),
        ("suppressed/settings.py", 1, "aws-access-token"),
    ]
    files = {f["path"] for f in cap.load_fixture("gitleaks", "hostile")["files"]}
    assert ".gitleaksignore" not in files


def test_gitleaks_any_nonzero_exit_fails():
    assert failure("gitleaks", "findings", exit_code=1).reason == "tool_error"


def test_detect_secrets_findings():
    parse = interpret("detect-secrets", "findings")
    assert keys(parse) == [
        ("vulnerable/backend/app.py", 23, "Secret Keyword"),
        ("vulnerable/backend/app.py", 24, "Secret Keyword"),
        ("vulnerable/backend/app.py", 25, "AWS Access Key"),
        ("vulnerable/backend/app.py", 26, "Base64 High Entropy String"),
        ("vulnerable/backend/app.py", 26, "Secret Keyword"),
    ]
    assert "detect_secrets.filters.allowlist.is_line_allowlisted" not in parse.metrics["filters_used"]
    assert "AWSKeyDetector" in parse.metrics["plugins_used"]


def test_detect_secrets_allowlist_pragma_is_ignored():
    assert keys(interpret("detect-secrets", "suppressed")) == [
        ("suppressed/app.py", 8, "Secret Keyword"),
        ("suppressed/settings.py", 1, "AWS Access Key"),
    ]


def test_detect_secrets_entropy_limits_apply():
    assert "Base64 High Entropy String" not in rules(interpret("detect-secrets", "config"))


# --------------------------------------------------------------------------
# codespell / hadolint
# --------------------------------------------------------------------------


def test_codespell_findings():
    parse = interpret("codespell", "findings")
    assert sorted(f.extra["wrong"] for f in parse.findings) == ["recieve", "seperate"]
    assert {f.rule_id for f in parse.findings} == {"misspelling"}
    assert all(f.line == 73 for f in parse.findings)


def test_codespell_exit_code_must_agree_and_output_is_strict():
    assert failure("codespell", "findings", exit_code=0).reason == "parse_error"
    assert failure("codespell", "findings", exit_code=64).reason == "tool_error"
    assert failure("codespell", "clean", stdout=b"something unexpected\n").reason == "parse_error"


def test_hadolint_findings_and_pragmas():
    assert rules(interpret("hadolint", "findings")) == {"DL3007", "DL3008", "DL3009", "DL3015", "DL3020", "DL3025"}
    assert rules(interpret("hadolint", "suppressed")) == {"DL3007"}  # "# hadolint ignore=DL3007" ignored
    assert rules(interpret("hadolint", "config")).isdisjoint({"DL3007", "DL3008"})  # --ignore


def test_hadolint_unparseable_dockerfile_is_coverage():
    parse = interpret("hadolint", "syntax")
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["docker/Dockerfile"]
    assert {f.file_path for f in parse.findings} == {"Dockerfile"}


# --------------------------------------------------------------------------
# metrics tools
# --------------------------------------------------------------------------


def test_jscpd_clone_finding_and_duplication_metrics():
    parse = interpret("jscpd", "findings")
    assert len(parse.findings) == 1
    clone = parse.findings[0]
    assert clone.file_path == "vulnerable/backend/utils.py"
    assert clone.extra["second_file"] == "vulnerable/backend/utils.py"
    assert clone.extra["lines"] == 14
    assert clone.extra["tokens"] > 100
    dup = parse.metrics
    assert dup["clones"] == 1
    assert dup["duplicated_lines"] == 28  # both copies, each line once
    assert dup["files"]["vulnerable/backend/utils.py"] == {"lines": 37, "duplicated_lines": 28}
    assert dup["percentage"] == round(100 * 28 / dup["total_lines"], 4)
    assert dup["total_lines"] == sum(entry["lines"] for entry in dup["files"].values())
    # 15 canary files + reports.py, main.jsx and the index.html / styles.css pairs of the ported-tool controls.
    assert len(dup["files"]) == parse.coverage["files_scanned"] == 21


def test_jscpd_clean_metrics_are_zero_not_missing():
    dup = interpret("jscpd", "clean").metrics
    assert dup["clones"] == 0
    assert dup["percentage"] == 0.0
    assert dup["total_lines"] > 0


def _source(path: str, content: bytes, language: str = "python"):
    from backend.analysis.engine.types import SampleFile

    return SampleFile(
        path=path,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        language=language,
        kind="source",
        origin="authored",
    )


def test_jscpd_lines_are_numbered_like_its_clone_ranges():
    """jscpd numbers clone lines on "\n" only (its tokenizer counts "\n"), as every tool and
    sloc.split_lines do: a lone "\r" or a form feed inside a line is not a line of its own, so the
    duplicated-lines denominator and the too-small rule use the same lines as the clone ranges."""
    tokenized = _source("app.py", b"a = 1\rb = 2\nc = 3\r\nd = 4\n")
    # One physical line split by form feeds: five lines to str.splitlines, one to the tools.
    tiny = _source("tiny.py", b"a = 1\x0cb = 2\x0cc = 3\x0cd = 4\x0ce = 5\n")
    report = {
        "statistics": {"formats": {"python": {"sources": {"/src/app.py": {}}}}, "total": {}},
        "duplicates": [],
    }
    outcome = ExecOutcome(exit_code=0, out_files={"jscpd/jscpd-report.json": json.dumps(report).encode()})
    parse = get_adapter("jscpd").interpret(outcome, [tokenized, tiny], {})
    assert parse.metrics["files"]["app.py"] == {"lines": 3, "duplicated_lines": 0}
    # Shorter than the minimum clone (5 lines): analysed with nothing to find, not "not tokenized".
    assert parse.metrics["files"]["tiny.py"] == {"lines": 1, "duplicated_lines": 0}
    assert parse.coverage["skipped"] == []


def test_mypy_output_lines_split_on_newlines_only():
    """A message quoting source text with a form feed or U+2028 is one JSON line, not two."""
    fixture = cap.load_fixture("mypy", "findings")
    item = json.loads(cap._unblob(fixture["outcome"]["stdout"]).decode().split("\n")[0])
    item["message"] = "Incompatible return value \x0c (got \u2028 str)"
    stdout = json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\r\n"
    parse = interpret("mypy", "findings", stdout=stdout)
    assert len(parse.findings) == 1
    assert parse.findings[0].message == item["message"]


def test_radon_complexity():
    parse = interpret("radon", "findings")
    assert keys(parse) == [("vulnerable/backend/app.py", 86, "cyclomatic-complexity")]
    finding = parse.findings[0]
    assert (finding.native_severity, finding.extra["complexity"]) == ("E", 36)
    complexity = parse.metrics["complexity"]
    assert complexity["max"] == 36
    assert complexity["rank_distribution"]["E"] == 1
    assert parse.metrics["maintainability"]["per_file"]["vulnerable/backend/app.py"]["mi"] > 0
    assert parse.metrics["raw"]["files"] == 5  # app.py, utils.py, reports.py + clean/ app.py, bookmarks.py


def test_radon_finding_rank_is_an_interpretation_option():
    fixture = cap.load_fixture("radon", "findings")
    files = cap.replay_files(fixture)
    adapter = get_adapter("radon")
    outcome = cap.replay_outcome(fixture)
    assert len(adapter.interpret(outcome, files, {}).findings) == 1  # E: classify_order (CC 36)
    assert len(adapter.interpret(outcome, files, {"finding_min_rank": "F"}).findings) == 0
    # The argv does not change: every block is needed for the metrics.
    assert adapter.invocation(files, {"finding_min_rank": "F"}).argv == fixture["invocation"]["argv"]


def test_radon_syntax_errors_are_coverage():
    parse = interpret("radon", "syntax")
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["svc/broken.py"]


def test_lizard_complexity():
    parse = interpret("lizard", "findings")
    assert keys(parse) == [("vulnerable/backend/app.py", 86, "cyclomatic-complexity")]
    assert parse.findings[0].extra["ccn"] == 36
    assert parse.metrics["complexity"]["functions"] == len(parse.metrics["functions"]) > 20
    languages = {f["path"].rsplit(".", 1)[-1] for f in parse.metrics["functions"]}
    assert {"py", "jsx", "ts", "tsx"} <= languages


def test_lizard_threshold_and_modified_ccn():
    fixture = cap.load_fixture("lizard", "config")
    assert {"-C", "5", "-m"} <= set(fixture["invocation"]["argv"])
    assert len(interpret("lizard", "config").findings) >= 1


def test_lizard_csv_is_strict():
    assert failure("lizard", "findings", out_files={"lizard.csv": b"1,2,3\n"}).reason == "parse_error"
    assert failure("lizard", "findings", out_files={}).reason == "tool_error"
    bad = b'x,1,2,3,4,"f@1-2@/src/a.py","/src/a.py","f","f( )",1,2\n'
    assert failure("lizard", "findings", out_files={"lizard.csv": bad}).reason == "parse_error"
