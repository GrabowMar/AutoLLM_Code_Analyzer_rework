"""Versioned prompt/schema files, rendering and scope selection of the AI reviewer."""

from __future__ import annotations

import hashlib

import pytest
from jsonschema import Draft202012Validator

from backend.analysis.engine import sloc
from backend.analysis.engine.reviewer import prompt
from backend.analysis.models import FindingCategory
from backend.analysis.tests.reviewer_support import app_sample
from backend.analysis.tests.reviewer_support import make_file
from backend.analysis.tests.reviewer_support import make_sample


def test_hashes_are_of_the_exact_file_bytes():
    prompt_bytes = (prompt.REVIEW_DIR / "prompt-v1.md").read_bytes()
    schema_bytes = (prompt.REVIEW_DIR / "findings-schema-v1.json").read_bytes()
    assert prompt.prompt_sha256() == hashlib.sha256(prompt_bytes).hexdigest()
    assert prompt.schema_sha256() == hashlib.sha256(schema_bytes).hexdigest()
    assert prompt.prompt_versions()[0]["version"] == "review-v1"
    assert prompt.schema_versions()[0]["sha256"] == prompt.schema_sha256()


def test_unknown_versions_raise():
    with pytest.raises(prompt.UnknownVersionError):
        prompt.system_prompt("review-v99")
    with pytest.raises(prompt.UnknownVersionError):
        prompt.schema_sha256("findings-v99")


def test_sections_render_without_leftover_placeholders():
    system = prompt.system_prompt()
    assert system.startswith("You are an independent senior code reviewer")
    assert "@section" not in system
    assert "<!--" not in system
    user = prompt.user_prompt(files_block="=== FILE: a.py ===", file_count=1, chunk_index=1, chunk_count=1)
    assert "=== FILE: a.py ===" in user
    assert "$" not in user
    assert "all of the application's reviewed files" in user
    multi = prompt.user_prompt(files_block="x", file_count=2, chunk_index=2, chunk_count=3)
    assert "part 2 of 3" in multi
    repair = prompt.repair_prompt(["findings/0: 'file' is a required property"])
    assert "- findings/0: 'file' is a required property" in repair


def test_code_with_dollar_signs_is_not_interpolated():
    user = prompt.user_prompt(files_block="cost = $price  # ${x}", file_count=1, chunk_index=1, chunk_count=1)
    assert "cost = $price  # ${x}" in user


def test_schema_matches_the_vocabulary_and_is_strict():
    schema = prompt.findings_schema()
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    item = schema["properties"]["findings"]["items"]
    assert item["additionalProperties"] is False
    # Strict structured outputs require every property to be required.
    assert set(item["required"]) == set(item["properties"])
    assert set(schema["required"]) == set(schema["properties"])
    assert tuple(item["properties"]["category"]["enum"]) == prompt.CATEGORIES
    assert tuple(item["properties"]["severity"]["enum"]) == prompt.FINDING_SEVERITIES
    assert set(prompt.CATEGORIES) <= set(FindingCategory.values)
    assert "enum" not in item["properties"]["file"]


def test_schema_with_allowed_files_is_a_copy_with_an_enum():
    constrained = prompt.findings_schema(["b.py", "a.py", "a.py"])
    assert constrained["properties"]["findings"]["items"]["properties"]["file"]["enum"] == ["a.py", "b.py"]
    # The cached base schema is untouched.
    assert "enum" not in prompt.findings_schema()["properties"]["findings"]["items"]["properties"]["file"]


def test_response_format_per_output_mode():
    strict = prompt.response_format("json_schema", ["a.py"])
    assert strict["type"] == "json_schema"
    assert strict["json_schema"]["strict"] is True
    assert strict["json_schema"]["name"] == prompt.RESPONSE_FORMAT_NAME
    assert strict["json_schema"]["schema"]["properties"]["findings"]["items"]["properties"]["file"]["enum"] == ["a.py"]
    assert prompt.response_format("json_object", ["a.py"]) == {"type": "json_object"}
    assert prompt.response_format("prompt", ["a.py"]) is None


def test_split_lines_is_the_platforms_one_numbering():
    # Regression: prompt kept its own copy of the line split; any drift from sloc.split_lines would make AI
    # findings cite other lines than the static tools, normalization and SLOC for the same code.
    assert prompt.split_lines is sloc.split_lines


def test_split_lines_numbers_like_editors():
    assert prompt.split_lines("") == []
    assert prompt.split_lines("a\nb\n") == ["a", "b"]
    assert prompt.split_lines("a\r\nb") == ["a", "b"]
    # Form feeds / unicode separators do not start new lines (unlike str.splitlines).
    assert prompt.split_lines("a\x0cb\u2028c\nd") == ["a\x0cb\u2028c", "d"]
    assert prompt.split_lines("a\n\n") == ["a", ""]


def test_render_lines_numbers_and_ranges():
    lines = [f"line {i}" for i in range(1, 13)]
    whole = prompt.render_lines("x.py", lines, language="python")
    assert whole.splitlines()[0] == "=== FILE: x.py (python, 12 lines) ==="
    assert whole.splitlines()[1] == "   1| line 1"
    assert whole.splitlines()[12] == "  12| line 12"
    assert whole.splitlines()[-1] == "=== END FILE: x.py ==="
    part = prompt.render_lines("x.py", lines, start=5, end=6)
    assert part.splitlines() == [
        "=== FILE: x.py (lines 5-6 of 12) ===",
        "   5| line 5",
        "   6| line 6",
        "=== END FILE: x.py ===",
    ]
    wide = prompt.render_lines("big.py", ["x"] * 12345)
    assert wide.splitlines()[1] == "    1| x"


def test_select_files_scope_rules():
    sample = app_sample()
    selected, skipped = prompt.select_files(sample)
    assert [f.path for f in selected] == ["backend/app.py", "frontend/src/App.jsx"]
    assert skipped == {"excluded:scope": 1, "kind:doc": 1, "origin:scaffold": 1}
    with_scaffold, _ = prompt.select_files(
        sample,
        include_origins=["added", "scaffold"],
        include_kinds=["source", "doc"],
    )
    assert [f.path for f in with_scaffold] == [
        "README.md",
        "backend/app.py",
        "backend/scaffold.py",
        "frontend/src/App.jsx",
    ]


def test_select_files_records_exclude_reasons():
    sample = make_sample([make_file("dist/x.js", "x", included=False, exclude_reason="build_artifact")])
    selected, skipped = prompt.select_files(sample)
    assert selected == []
    assert skipped == {"excluded:build_artifact": 1}


def test_file_text_is_strict_utf8():
    assert prompt.file_text(make_file("a.py", "ok")) == "ok"
    assert prompt.file_text(make_file("a.bin", b"\xff\xfe\x00")) is None


def test_overhead_chars_counts_prompt_and_schema():
    assert prompt.overhead_chars() > len(prompt.system_prompt())
