"""Shared payload parsing: JSON + schema validation, grounding, normalization, AI invariants."""

from __future__ import annotations

import json

import pytest

from backend.analysis.engine.reviewer import findings
from backend.analysis.tests.reviewer_support import SECRET
from backend.analysis.tests.reviewer_support import SQLI
from backend.analysis.tests.reviewer_support import XSS
from backend.analysis.tests.reviewer_support import answer
from backend.analysis.tests.reviewer_support import app_sample
from backend.analysis.tests.reviewer_support import finding
from backend.analysis.tests.reviewer_support import frozen_section
from backend.analysis.tests.reviewer_support import patch_normalize


@pytest.fixture(autouse=True)
def _normalize(monkeypatch):
    patch_normalize(monkeypatch)


def parse(payload, **kw):
    return findings.parse_payload(
        payload,
        sample=app_sample(),
        frozen=frozen_section(),
        index=kw.pop("index", 1),
        **kw,
    )


def test_valid_payload_maps_and_grounds_findings():
    out, counts, info = parse(answer([SQLI, SECRET, XSS], ["backend/app.py", "frontend/src/App.jsx"]))
    assert counts == {"exact": 3, "relocated": 0, "ungrounded": 0, "invalid_file": 0, "out_of_range": 0}
    assert info["review_complete"] is True
    assert info["files_reviewed"] == ["backend/app.py", "frontend/src/App.jsx"]
    assert info["lenient_json"] is False
    sqli = out[0]
    assert sqli.tool_slug == "ai_review"
    assert sqli.is_ai is True
    assert sqli.counted is False  # only consensus counts AI findings
    assert sqli.is_consensus is False
    assert sqli.sample_index == 1
    assert sqli.grounding == "exact"
    assert (sqli.file_path, sqli.line, sqli.end_line) == ("backend/app.py", 12, 12)
    assert sqli.severity == "high"
    assert sqli.category == "security"
    assert sqli.cwe == ["CWE-89"]
    assert sqli.title == "SQL injection"
    assert sqli.description == "User input is formatted into SQL."
    assert sqli.suggestion == "Use a parameterized query."
    assert sqli.snippet == SQLI["evidence"]
    assert out[1].category == "secrets"
    assert out[1].cwe == ["CWE-798"]


def test_raw_finding_contract_for_the_measure_stage(monkeypatch):
    seen = []

    def capture(tool_slug, raw, sample, scope):
        seen.append((tool_slug, raw, scope))
        from backend.analysis.tests.reviewer_support import fake_normalize

        return fake_normalize(tool_slug, raw, sample, scope)

    from backend.analysis.engine import normalize

    monkeypatch.setattr(normalize, "normalize", capture)
    item = finding("backend/app.py", 2, 2, SQLI["evidence"], confidence=0.876, cwe=None, category="bug")
    parse(answer([item]), scope={"measured_origins": ["added"]})
    tool_slug, raw, scope = seen[0]
    assert tool_slug == "ai_review"
    assert scope == {"measured_origins": ["added"]}
    assert raw.rule_id == "bug"
    assert raw.native_severity == "high"
    assert raw.native_confidence == "0.88"
    assert raw.cwe == []
    assert (raw.line, raw.end_line) == (12, 12)  # relocated location
    assert raw.extra["cited_lines"] == [2, 2]
    assert raw.extra["grounding"] == "relocated"
    assert raw.extra["category"] == "bug"
    assert raw.extra["confidence"] == pytest.approx(0.876)
    assert raw.extra["reviewer"]["model"] == "deepseek_deepseek-v4.1-flash-20260910"


def test_scope_defaults_to_frozen_then_sample_default(monkeypatch):
    scopes = []

    def capture(tool_slug, raw, sample, scope):
        scopes.append(scope)
        from backend.analysis.tests.reviewer_support import fake_normalize

        return fake_normalize(tool_slug, raw, sample, scope)

    from backend.analysis.engine import normalize
    from backend.analysis.engine.sample import DEFAULT_SCOPE

    monkeypatch.setattr(normalize, "normalize", capture)
    findings.parse_payload(answer([SQLI]), sample=app_sample(), frozen=frozen_section(scope={"x": 1}), index=0)
    findings.parse_payload(answer([SQLI]), sample=app_sample(), frozen=frozen_section(), index=0)
    assert scopes == [{"x": 1}, DEFAULT_SCOPE]


def test_grounding_window_comes_from_the_frozen_section():
    cited_nearby = answer([finding("backend/app.py", 9, 9, SQLI["evidence"])])
    assert parse(cited_nearby)[0][0].grounding == "exact"
    narrow = frozen_section(consensus={"line_window": 3, "grounding_window": 1})
    out, _, _ = findings.parse_payload(cited_nearby, sample=app_sample(), frozen=narrow, index=0)
    assert (out[0].grounding, out[0].line) == ("relocated", 12)


def test_relocated_finding_moves_lines():
    out, counts, _ = parse(answer([finding("backend/app.py", 1, 1, SQLI["evidence"])]))
    assert counts["relocated"] == 1
    assert (out[0].line, out[0].end_line, out[0].grounding) == (12, 12, "relocated")


def test_invalid_file_and_out_of_range_are_kept_not_rejected():
    payload = answer(
        [
            finding("backend/nope.py", 1, 1, "x = 1"),
            finding("backend/app.py", 40, 41, "x = 1"),
            finding("backend/app.py", 12, 12, "totally invented code here"),
        ],
    )
    out, counts, _ = parse(payload)
    assert [f.grounding for f in out] == ["invalid_file", "out_of_range", "ungrounded"]
    assert counts == {"exact": 0, "relocated": 0, "ungrounded": 1, "invalid_file": 1, "out_of_range": 1}


def test_allowed_files_restrict_to_what_the_model_was_shown():
    out, counts, _ = parse(answer([SQLI, XSS]), allowed_files=["frontend/src/App.jsx"])
    assert [f.grounding for f in out] == ["invalid_file", "exact"]
    assert counts["invalid_file"] == 1


def test_empty_findings_list_is_a_valid_answer():
    out, counts, info = parse(answer([], ["backend/app.py"]))
    assert out == []
    assert sum(counts.values()) == 0
    assert info["findings"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "   ",
        "not json at all",
        '{"findings": [',  # truncated JSON is never salvaged
        "[]",
        json.dumps({"findings": []}),  # missing required keys
        json.dumps({"findings": [], "files_reviewed": [], "review_complete": True, "extra": 1}),
        answer([{**SQLI, "severity": "urgent"}]),
        answer([{**SQLI, "category": "performance"}]),
        answer([{**SQLI, "cwe": "89"}]),
        answer([{**SQLI, "confidence": 1.5}]),
        answer([{**SQLI, "start_line": 0}]),
        answer([{k: v for k, v in SQLI.items() if k != "evidence"}]),
        answer([{**SQLI, "line": 12}]),
    ],
)
def test_invalid_payloads_raise(payload):
    with pytest.raises(findings.InvalidReview) as exc:
        parse(payload)
    assert exc.value.errors


def test_schema_errors_are_readable_for_the_repair_prompt():
    with pytest.raises(findings.InvalidReview) as exc:
        parse(answer([{**SQLI, "severity": "urgent"}, {**XSS, "confidence": "high"}]))
    joined = "\n".join(exc.value.errors)
    assert "findings/0/severity" in joined
    assert "findings/1/confidence" in joined


def test_fenced_or_wrapped_json_is_accepted_leniently():
    doc = answer([SQLI])
    out, _, info = parse(f"```json\n{doc}\n```")
    assert len(out) == 1
    assert info["lenient_json"] is True
    out, _, info = parse(f"Here is my review:\n{doc}\nThanks!")
    assert len(out) == 1
    assert info["lenient_json"] is True


def test_dict_payload_is_accepted():
    out, _, _ = parse(json.loads(answer([SQLI])))
    assert len(out) == 1


def test_long_title_is_capped_and_blank_title_replaced():
    out, _, _ = parse(answer([{**SQLI, "title": "x" * 900}, {**XSS, "title": "  "}]))
    assert len(out[0].title) == findings.MAX_TITLE_CHARS
    assert out[1].title == "AI review: security"


def test_evidence_over_three_lines_is_counted():
    long = finding(
        "backend/app.py",
        10,
        13,
        "\n".join(app_sample().by_path()["backend/app.py"].text.split("\n")[9:13]),
    )
    _, counts, info = parse(answer([long]))
    assert counts["exact"] == 1
    assert info["evidence_over_limit"] == 1


def test_invalid_mapping_values_fall_back_to_the_model_vocabulary(monkeypatch):
    from backend.analysis.engine import normalize
    from backend.analysis.tests.reviewer_support import fake_normalize

    def broken(tool_slug, raw, sample, scope):
        nf = fake_normalize(tool_slug, raw, sample, scope)
        nf.severity, nf.category, nf.confidence, nf.cwe = "HIGH!", "nonsense", "0.9", []
        return nf

    monkeypatch.setattr(normalize, "normalize", broken)
    out, _, _ = parse(answer([SQLI]))
    assert (out[0].severity, out[0].category, out[0].confidence, out[0].cwe) == (
        "high",
        "security",
        "high",
        ["CWE-89"],
    )


def test_files_reviewed_paths_are_normalized_and_deduplicated():
    _, _, info = parse(answer([], ["./backend/app.py", "backend/app.py", ""]))
    assert info["files_reviewed"] == ["backend/app.py"]


def test_validate_document_helper():
    assert findings.validate_document(answer([SQLI]))["findings"][0]["file"] == "backend/app.py"
    with pytest.raises(findings.InvalidReview):
        findings.validate_document("{}")


def test_evidence_location_is_recorded_for_consensus():
    wide = finding("backend/app.py", 1, 17, SQLI["evidence"])
    out, _, _ = parse(answer([wide, finding("backend/app.py", 2, 2, "os.system(cmd)")]))
    assert out[0].grounding == "exact"
    assert (out[0].line, out[0].end_line) == (1, 17)  # the cited range stays the finding's location
    assert out[0].extra["evidence_at"] == [12, 12]  # where the quote is: what consensus clusters on
    assert out[1].grounding == "ungrounded"
    assert out[1].extra["evidence_at"] is None


def test_paths_under_the_review_root_are_workspace_relative():
    # Regression: an agent citing "/workspace/backend/app.py" (as its tools print paths) had every
    # finding graded invalid_file while the replicate still succeeded.
    item = finding("/workspace/backend/app.py", 12, 12, SQLI["evidence"])
    out, counts, info = parse(
        answer([item], ["/workspace/backend/app.py"]),
        allowed_files=["backend/app.py"],
        path_root="/workspace",
    )
    assert counts["exact"] == 1
    assert out[0].file_path == "backend/app.py"
    assert info["files_reviewed"] == ["backend/app.py"]
    # Without the root (single-shot), the same citation is not a file the model was shown.
    _, counts, _ = parse(answer([item]), allowed_files=["backend/app.py"])
    assert counts["invalid_file"] == 1
