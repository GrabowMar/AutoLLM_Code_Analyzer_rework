"""Detector engines: scopes, probe and manual mapping, and Semgrep's no-silent-clean rules (spec 7.9 step 3).

Invariant (13.4, WP-13): a detector error -- including Semgrep ``errors[]`` with exit 0 -- is ``error``,
never ``absent``; ``tool_severity`` is copied verbatim.
"""

from __future__ import annotations

from backend.analysis.services import rulepack
from backend.generation.patterns import detectors as det
from backend.generation.testing.fake_semgrep import FakeSemgrep

FILE = """from flask import request


def build_query(name):
    return "SELECT * FROM users WHERE name = '" + name + "'"


def search(db):
    name = request.args.get("name")
    return db.execute(build_query(name)).fetchall()


def other(db, uid):
    return db.execute(f"SELECT * FROM t WHERE u = {uid}")
"""
SPAN = (8, 10)  # the regenerated ``search`` handler; the helper and ``other`` belong to the parent


def spec(engine="regex", rule=r"execute\(\s*f[\"']", feature="py-sql-fstring", **extra):
    content = {
        "engine": engine,
        "languages": extra.pop("languages", ["python"]),
        "rule": rule,
        "tool": {"name": "x", "version": "1"},
        "feature_key": feature,
        "polarity": "insecure",
        "description": "d",
        "scopes": ["span", "file"],
        **extra,
    }
    return det.DetectorSpec(slug=feature, version=1, content_hash="a" * 64, content=content, pk=1)


def variant(**kw):
    defaults = {"run_id": "r1", "path": "app/routes.py", "language": "python", "file_text": FILE, "span": SPAN}
    defaults.update(kw)
    return det.VariantInput(**defaults)


def by_scope(verdicts):
    return {v.scope: v for v in verdicts}


def test_regex_reports_span_and_file_scopes_separately():
    out = by_scope(det.run_in_process(spec(), variant(), ["span", "file"]))
    assert out["file"].status == "present"
    assert out["file"].matches[0]["start_line"] == 14
    assert out["span"].status == "absent"
    assert out["file"].tool_severity == ""  # in-process engines have no severity of their own


def test_whole_file_target_span_is_the_file():
    out = by_scope(det.run_in_process(spec(), variant(span=None), ["span", "file"]))
    assert out["span"].status == out["file"].status == "present"


def test_invalid_regex_and_unreadable_file_are_errors():
    assert det.run_in_process(spec(rule="("), variant(), ["span"])[0].status == "error"
    unreadable = det.run_in_process(spec(), variant(file_text=None), ["span"])[0]
    assert (unreadable.status, unreadable.error_reason) == ("error", "tool_error")


def test_ts_query_matches_and_unparsed_file_is_an_error():
    query = '(call function: (attribute attribute: (identifier) @fn) (#eq? @fn "execute")) @match'
    out = by_scope(det.run_in_process(spec("ts_query", query, "exec"), variant(), ["span", "file"]))
    assert out["file"].status == "present"
    assert [m["start_line"] for m in out["file"].matches] == [10, 14]
    assert out["span"].status == "present"
    broken = det.run_in_process(spec("ts_query", query, "exec"), variant(file_text=FILE + "def (:\n"), ["span"])[0]
    assert (broken.status, broken.error_reason) == ("error", "file_unparsed")


def test_language_not_covered_is_not_applicable():
    out = det.run_in_process(spec(languages=["javascript"]), variant(), ["span"])
    assert out[0].status == "not_applicable"


def test_probe_mapping():
    probe = spec("probe", "", "probe-sqli", languages=[], cwe=["cwe-89"])
    cases = [
        (None, "not_applicable"),
        ([{"id": "p1", "cwe": "cwe-89", "status": "exploited"}], "present"),
        ([{"id": "p1", "cwe": "cwe-89", "status": "not_exploited"}], "absent"),
        ([{"id": "p1", "cwe": "cwe-89", "status": "skipped", "skip_reason": "precondition_failed"}], "not_applicable"),
        (
            [
                {"id": "p1", "cwe": "cwe-89", "status": "error"},
                {"id": "p2", "cwe": "cwe-89", "status": "not_exploited"},
            ],
            "error",
        ),
        ([{"id": "p9", "cwe": "cwe-79", "status": "exploited"}], "not_applicable"),
    ]
    for probes, expected in cases:
        verdicts = det.run_in_process(probe, variant(probes=probes), det.requested_scopes(probe, ["span", "file"]))
        assert [v.scope for v in verdicts] == ["span"]
        assert verdicts[0].status == expected, probes
    linked = variant(probes=[{"id": "p1", "cwe": "cwe-89", "status": "exploited"}], linked_probes=["p2"])
    assert det.run_in_process(probe, linked, ["span"])[0].status == "not_applicable"


def test_manual_labels_majority():
    manual = spec("manual", "", "owner-check", languages=[])
    labels = [det.Label("present", "alice"), det.Label("present", "bob"), det.Label("absent", "carol")]
    v = variant(normalized_sha="b" * 64, labels={manual.ref: labels})
    out = det.run_in_process(manual, v, ["span"])[0]
    assert out.status == "present"
    assert out.message == "manual coding by alice, bob"
    tie = variant(normalized_sha="b" * 64, labels={manual.ref: labels[1:]})
    assert det.run_in_process(manual, tie, ["span"])[0].status == "not_applicable"
    assert det.run_in_process(manual, variant(normalized_sha=""), ["span"])[0].status == "not_applicable"


SEMGREP_RULE = """
rules:
  - id: sql-fstring
    languages: [python]
    severity: ERROR
    message: f-string SQL
    pattern-regex: 'execute\\(f'
"""


def semgrep_spec(rule=SEMGREP_RULE, **extra):
    return spec("semgrep", rule, "sg-sql", **extra)


def run_semgrep(fake, variants, specs=None):
    return det.run_semgrep(specs or [semgrep_spec()], variants, ["span", "file"], runner=fake).verdicts


def test_semgrep_finds_matches_and_copies_tool_severity_verbatim():
    out = by_scope(run_semgrep(FakeSemgrep(), [variant()])[("r1", "sg-sql@1")])
    assert out["file"].status == "present"
    assert out["file"].tool_severity == "ERROR"
    assert out["span"].status == "absent"
    assert out["span"].tool_severity == ""


def test_semgrep_errors_with_exit_zero_are_errors_never_absent():
    fake = FakeSemgrep(errors=[{"type": "Syntax error", "message": "bad", "path": "r1/app/routes.py"}])
    verdicts = run_semgrep(fake, [variant(), variant(run_id="r2")])
    assert fake.exit_code == 0
    assert {v.status for v in verdicts[("r1", "sg-sql@1")]} == {"error"}
    assert {v.error_reason for v in verdicts[("r1", "sg-sql@1")]} == {"parse_error"}
    assert {v.status for v in verdicts[("r2", "sg-sql@1")]} == {"present", "absent"}  # the other file is fine


def test_semgrep_error_without_a_path_touches_every_file():
    fake = FakeSemgrep(errors=[{"type": "Rule parse error", "message": "invalid rule"}])
    verdicts = run_semgrep(fake, [variant(), variant(run_id="r2")])
    assert all(v.status == "error" for items in verdicts.values() for v in items)


def test_semgrep_nonzero_exit_unscanned_file_runner_failure_and_unparsed_slot_are_errors():
    assert {v.status for v in run_semgrep(FakeSemgrep(exit_code=2), [variant()])[("r1", "sg-sql@1")]} == {"error"}
    unscanned = run_semgrep(FakeSemgrep(unscanned=["r1/app/routes.py"]), [variant()])
    assert {v.status for v in unscanned[("r1", "sg-sql@1")]} == {"error"}
    failed = run_semgrep(FakeSemgrep(fail="semgrep is not installed"), [variant()])
    assert {(v.status, v.error_reason) for v in failed[("r1", "sg-sql@1")]} == {("error", "tool_error")}
    unparsed = run_semgrep(FakeSemgrep(), [variant(file_parse_error=True)])
    assert {(v.status, v.error_reason) for v in unparsed[("r1", "sg-sql@1")]} == {("error", "file_unparsed")}


def test_semgrep_rule_without_severity_is_invalid_not_defaulted():
    rule = "rules:\n  - id: x\n    languages: [python]\n    message: m\n    pattern-regex: 'a'\n"
    out = run_semgrep(FakeSemgrep(), [variant()], [semgrep_spec(rule)])[("r1", "sg-sql@1")]
    assert {v.status for v in out} == {"error"}
    assert det.validate_content(semgrep_spec(rule).content)


def test_taint_rule_through_a_parent_helper_is_found_at_file_scope():
    # Semgrep sees the whole assembled file, so a flow through the parent's build_query() is reported. The
    # fake reports the sink inside the parent helper (line 5), as real taint mode does for that flow.
    taint = """
rules:
  - id: flask-request-to-sql
    mode: taint
    languages: [python]
    severity: ERROR
    message: request data reaches SQL
    pattern-sources: [{pattern: request.$X}]
    pattern-sinks: [{pattern: $DB.execute(...)}]
"""
    sink = {
        "check_id": "llmlab-d0-r0",
        "path": "src/r1/app/routes.py",
        "start": {"line": 5, "col": 12},
        "end": {"line": 5, "col": 70},
        "extra": {"message": "request data reaches SQL", "severity": "ERROR", "lines": "return ..."},
    }
    fake = FakeSemgrep(extra_results=[sink])
    out = by_scope(run_semgrep(fake, [variant()], [semgrep_spec(taint, taint=True)])[("r1", "sg-sql@1")])
    files, config = fake.calls[0]
    assert files["r1/app/routes.py"] == FILE  # the whole assembled file, parent helper included
    assert "mode: taint" in config
    assert out["file"].status == "present"
    assert out["span"].status == "absent"


def test_rulepack_reports_a_missing_report_as_an_error():
    run = rulepack.parse_report(rulepack.RawOutput(exit_code=0, stdout="not json"))
    assert run.error
    assert run.file_error("x.py")


def test_detector_set_hash_is_order_independent_and_content_bound():
    a, b = spec(feature="a"), spec(feature="b")
    assert det.detector_set_hash([a, b]) == det.detector_set_hash([b, a])
    changed = det.DetectorSpec(slug="a", version=1, content_hash="c" * 64, content=a.content)
    assert det.detector_set_hash([changed, b]) != det.detector_set_hash([a, b])
