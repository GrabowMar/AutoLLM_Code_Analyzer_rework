"""Issue registry (spec section 6.15): completeness against the spec text and severity rules (no database)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.generation.contracts import Issue
from backend.generation.contracts import issues
from backend.generation.contracts.issues import ISSUE_CODES
from backend.generation.contracts.issues import make_issue

SPEC = Path(__file__).resolve().parents[4] / "docs" / "design" / "SAMPLE_GEN_V3.md"

# Registered codes that the spec describes without naming (recorded in SAMPLE_GEN_V3_CHANGES.md, WP-01 decisions).
# Codes added after the spec froze, recorded in SAMPLE_GEN_V3_CHANGES.md (harness_exploratory_only: E2E-13).
CODES_NOT_NAMED_IN_SPEC = frozenset(
    {
        "combination_pruned",
        "mixed_config_fingerprint",
        "parent_draw_pending",
        "harness_exploratory_only",
        "cap_admits_fewer_blocks",
    },
)

# Phrases the spec uses when it names an issue code.
ISSUE_CONTEXT_PATTERNS = (
    r"issues? `([a-z][a-z0-9_]+)`",  # "issue `auth_flow_missing`", "lint issue `editor_harness_agent_brief`"
    r"`([a-z][a-z0-9_]+)` \((?:warn|error|info)\b",  # "`slot_leak_high` (warn)"
    r"\(`([a-z][a-z0-9_]+)`, (?:warn|error|info)\)",  # "(`brief_exceeds_call_cap`, error)"
    r"refused at plan time \(`([a-z_]+)`\)",
    r"warns `([a-z_]+)`",
    r"`([a-z_]+)` is an (?:error|warning)",
    r"`([a-z_]+)`: (?:warn|error|info)\b",
    r"deviation `([a-z_]+)`",
    r"`([a-z_]+)` is flagged",
)


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


def _first_cell_codes(table: str) -> set[str]:
    codes: set[str] = set()
    for line in table.splitlines():
        if not line.startswith("| `"):
            continue
        first = line.strip("|").split("|")[0]
        first = re.sub(r"\([^)]*\)", "", first)  # parenthetical remarks name other things (e.g. stop rules)
        codes.update(re.findall(r"`([a-z][a-z0-9_]*)`", first))
    return codes


def spec_issue_codes() -> dict[str, set[str]]:
    """Every issue code the spec names, with where it was found."""
    text = _spec()
    found: dict[str, set[str]] = {}
    for code in _first_cell_codes(_section(text, "### 6.15 Issue registry", "## 7. Slot mode")):
        found.setdefault(code, set()).add("6.15 table")
    for code in _first_cell_codes(_section(text, "Lint rules (issues", "**Per-study override.**")):
        found.setdefault(code, set()).add("4.3 lint table")
    for pattern in ISSUE_CONTEXT_PATTERNS:
        for code in re.findall(pattern, text):
            found.setdefault(code, set()).add(pattern)
    return found


SPEC_CODES = spec_issue_codes()


def test_spec_extraction_found_the_tables():
    sources = {source for where in SPEC_CODES.values() for source in where}
    assert "6.15 table" in sources
    assert "4.3 lint table" in sources
    assert len(SPEC_CODES) >= 45


@pytest.mark.parametrize("code", sorted(SPEC_CODES))
def test_every_issue_code_in_the_spec_is_registered(code):
    assert code in ISSUE_CODES, f"{code} is used as an issue in the spec ({sorted(SPEC_CODES[code])})"


def test_every_registered_code_is_named_in_the_spec_or_documented():
    backticked = set(re.findall(r"`([a-z][a-z0-9_]*)`", _spec()))
    for code in ISSUE_CODES:
        if code.startswith("lint_"):
            continue  # the library lint namespace (WP-03), recorded in SAMPLE_GEN_V3_CHANGES.md
        assert code in backticked or code in CODES_NOT_NAMED_IN_SPEC, code


def test_library_lint_codes_are_preregistered_for_wp03():
    lint_codes = {code: entry for code, entry in ISSUE_CODES.items() if code.startswith("lint_")}
    # The codes the WP-01 review asked for (library and starter lint, section 3.4 / 11.3 / 11.4).
    for code in (
        "lint_undeclared_variable",
        "lint_unknown_feature",
        "lint_prompt_role_missing",
        "lint_starter_forbidden_content",
        "lint_unknown_endpoint_ref",
        "lint_unknown_library_ref",
    ):
        assert code in lint_codes, code
    for code, entry in lint_codes.items():
        assert entry.owner == "library/lint.py", code
        assert entry.exploratory == entry.confirmatory, code  # lint does not depend on the study kind
        issue = issues.make_issue(code, message="fixture")
        assert issue.severity == entry.exploratory
    # Every library-lint-owned code outside the namespace predates it and is named in the spec.
    others = {code for code, entry in ISSUE_CODES.items() if entry.owner == "library/lint.py"} - set(lint_codes)
    assert others == {"editor_harness_agent_brief"}


def test_6_15_severities():
    expectations = {
        "harness_not_enabled": ("error", "error"),
        "harness_exploratory_only": ("warn", "error"),
        "mixed_harness_profiles": ("warn", "warn"),
        "vendor_affinity": ("info", "info"),
        "oracle_missing": ("warn", "error"),
        "conformance_missing": ("warn", "error"),
        "adaptive_stop_in_confirmatory": (None, "error"),
        "mixed_eval_suite": ("warn", "error"),
        "single_parent": ("info", "error"),
        "slot_tests_uninformative": ("warn", "error"),
        "slot_leak_verbatim": ("error", "error"),
        "few_clusters": ("info", "info"),
        "image_built_after_plan": ("info", "info"),
    }
    for code, (exploratory, confirmatory) in expectations.items():
        assert issues.severity_for(code, "exploratory") == exploratory, code
        assert issues.severity_for(code, "confirmatory") == confirmatory, code


def test_4_3_lint_severities():
    lint = _section(_spec(), "Lint rules (issues", "**Per-study override.**")
    for line in lint.splitlines():
        if not line.startswith("| `"):
            continue
        code_cell, severity_cell = (cell.strip() for cell in line.strip("|").split("|")[:2])
        code = re.findall(r"`([a-z_]+)`", code_cell)[0]
        entry = ISSUE_CODES[code]
        assert entry.owner == "budget/policy.py", code
        if severity_cell in {"error", "warn"}:
            # "*_in_confirmatory" codes only apply to confirmatory studies.
            expected_exploratory = None if code.endswith("_in_confirmatory") else severity_cell
            assert entry.exploratory == expected_exploratory, code
            assert entry.confirmatory == severity_cell, code
        elif "confirmatory" in severity_cell:
            assert entry.confirmatory == severity_cell.split()[0], code
            otherwise = re.search(r"(\w+) otherwise", severity_cell)
            assert entry.exploratory == (otherwise[1] if otherwise else None), code


def test_estimate_exceeds_cap_allows_both_severities():
    assert make_issue("estimate_exceeds_cap").severity == "warn"
    assert make_issue("estimate_exceeds_cap", severity="error").severity == "error"
    with pytest.raises(ValidationError, match="not allowed"):
        make_issue("estimate_exceeds_cap", severity="info")


def test_make_issue_uses_study_kind():
    assert make_issue("oracle_missing", "exploratory").severity == "warn"
    assert make_issue("oracle_missing", "confirmatory").severity == "error"
    with pytest.raises(ValueError, match="does not apply"):
        make_issue("adaptive_stop_in_confirmatory", "exploratory")
    with pytest.raises(KeyError, match="unregistered"):
        make_issue("no_such_code")


def test_issue_payload_round_trip():
    issue = make_issue(
        "estimate_exceeds_cap",
        "confirmatory",
        severity="error",
        message="expected $12 > cap $10",
        cells=["c0-u0-r0"],
        data={"expected_total": 12.0, "cap": 10.0},
        fix=[issues.IssueFix(label="raise cap to $13", action="set_cap", params={"usd": 13})],
    )
    dumped = issue.model_dump(mode="json")
    assert dumped["fix"][0]["params"] == {"usd": 13}
    assert Issue.model_validate(dumped) == issue


def test_issue_rejects_unregistered_codes_and_severities():
    with pytest.raises(ValidationError, match="unregistered issue code"):
        Issue(code="made_up", severity="warn")
    with pytest.raises(ValidationError, match="not allowed"):
        Issue(code="slot_leak_verbatim", severity="info")
    with pytest.raises(ValidationError):
        Issue(code="aliasing", severity="fatal")  # type: ignore[arg-type]


def test_registry_entries_are_well_formed():
    for code, entry in ISSUE_CODES.items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", code)
        assert entry.code == code
        assert entry.owner.endswith(".py")
        assert entry.summary
        assert entry.exploratory is not None or entry.confirmatory is not None, code
    payload = issues.registry_payload()
    assert len(payload) == len(ISSUE_CODES)
    json.dumps(payload)
