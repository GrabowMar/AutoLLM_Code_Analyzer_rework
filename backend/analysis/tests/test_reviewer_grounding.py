"""Grounding of AI findings: exact / relocated / ungrounded / invalid_file / out_of_range."""

from __future__ import annotations

import pytest

from backend.analysis.engine import sloc
from backend.analysis.engine.reviewer import grounding
from backend.analysis.engine.reviewer import single_shot
from backend.analysis.tests.reviewer_support import app_sample
from backend.analysis.tests.reviewer_support import make_file
from backend.analysis.tests.reviewer_support import make_sample

SQL_LINE = "rows = conn.execute(f\"SELECT * FROM users WHERE name = '{name}'\").fetchall()"


def ground(path="backend/app.py", start=12, end=12, evidence=SQL_LINE, sample=None, **kw):
    return grounding.ground(
        file_path=path,
        start_line=start,
        end_line=end,
        evidence=evidence,
        sample=sample or app_sample(),
        **kw,
    )


def test_exact_on_the_cited_line():
    assert ground() == ("exact", 12, 12)


def test_exact_within_the_window_keeps_cited_lines():
    assert ground(start=9, end=9) == ("exact", 9, 9)
    assert ground(start=15, end=15) == ("exact", 15, 15)


def test_relocated_outside_the_window_moves_to_the_occurrence():
    assert ground(start=2, end=2) == ("relocated", 12, 12)


def test_window_is_configurable():
    assert ground(start=9, end=9, window=1) == ("relocated", 12, 12)


def test_whitespace_and_indentation_are_normalized():
    assert ground(evidence="   rows   =  conn.execute(f\"SELECT * FROM users WHERE name = '{name}'\").fetchall()  ")[
        0
    ] == ("exact")


def test_copied_line_number_prefixes_are_stripped():
    evidence = '  11|     conn = sqlite3.connect("app.db")\n  12|     ' + SQL_LINE
    assert ground(start=11, end=12, evidence=evidence) == ("exact", 11, 12)


def test_prefix_is_kept_when_not_every_line_has_one():
    sample = make_sample([make_file("a.js", "const x = 1;\n0| mask;\n", language="javascript")])
    # Only the second line starts like a listing prefix: it is code and must stay part of the evidence.
    assert ground(path="a.js", start=1, end=2, evidence="const x = 1;\n0| mask;", sample=sample)[0] == "exact"
    assert grounding.evidence_lines("const x = 1;\n0| mask;") == ["const x = 1;", "0| mask;"]


def test_multiline_evidence_with_partial_first_and_last_lines():
    evidence = 'connect("app.db")\n    rows = conn.execute(f"SELECT'
    assert ground(start=11, end=12, evidence=evidence) == ("exact", 11, 12)


def test_multiline_relocation_spans_lines():
    evidence = 'name = request.args.get("name")\nconn = sqlite3.connect("app.db")'
    assert ground(start=1, end=1, evidence=evidence) == ("relocated", 10, 11)


def test_blank_lines_between_evidence_lines_are_ignored():
    evidence = 'SECRET_KEY = "hunter2-super-secret"\n@app.route("/users")'
    assert ground(start=5, end=8, evidence=evidence) == ("exact", 5, 8)


def test_paraphrase_is_ungrounded():
    assert ground(evidence="rows = conn.execute(query)") == ("ungrounded", 12, 12)


def test_ellipsis_is_ungrounded():
    assert ground(evidence='rows = conn.execute(f"SELECT ... ").fetchall()')[0] == "ungrounded"


def test_empty_or_trivial_evidence_is_ungrounded():
    assert ground(evidence="")[0] == "ungrounded"
    assert ground(evidence="  \n ")[0] == "ungrounded"
    assert ground(evidence="}")[0] == "ungrounded"


def test_short_evidence_grounds_exactly_but_never_relocates():
    # "app.run" occurs on line 17 only.
    assert ground(start=17, end=17, evidence="app.run")[0] == "exact"
    assert ground(start=2, end=2, evidence="app.run")[0] == "ungrounded"


def test_relocation_picks_the_nearest_occurrence():
    text = "\n".join(["token = load_secret_value()" if i in (2, 20) else f"x{i} = {i}" for i in range(1, 30)]) + "\n"
    sample = make_sample([make_file("m.py", text)])
    assert ground(path="m.py", start=16, end=16, evidence="token = load_secret_value()", sample=sample) == (
        "relocated",
        20,
        20,
    )
    assert ground(path="m.py", start=8, end=8, evidence="token = load_secret_value()", sample=sample) == (
        "relocated",
        2,
        2,
    )


def test_invalid_file():
    assert ground(path="backend/missing.py") == ("invalid_file", 12, 12)


@pytest.mark.parametrize(
    ("start", "end"),
    [(0, 1), (12, 11), (17, 18), (99, 99), (None, None)],
)
def test_out_of_range(start, end):
    assert ground(start=start, end=end)[0] == "out_of_range"


def test_missing_end_line_means_single_line():
    assert ground(end=None) == ("exact", 12, 12)


def test_cited_paths_are_normalized():
    assert ground(path="./backend/app.py")[0] == "exact"
    assert ground(path="/backend/app.py")[0] == "exact"
    assert grounding.normalize_path(".\\backend\\app.py") == "backend/app.py"


def test_crlf_files_ground_on_the_same_numbering():
    sample = make_sample([make_file("w.py", "a = 1\r\nb = 2\r\npassword = 'swordfish'\r\n")])
    assert ground(path="w.py", start=3, end=3, evidence="password = 'swordfish'", sample=sample) == ("exact", 3, 3)


def test_evidence_lines_helper():
    assert grounding.evidence_lines("  1| a  b\n\n  2| c") == ["a b", "c"]
    assert grounding.evidence_lines("x\n  2| y") == ["x", "2| y"]


# --------------------------------------------------------------------------
# Short fragments, copied prefixes of every reader, evidence location, workspace paths
# --------------------------------------------------------------------------


def test_short_fragments_must_not_cut_an_identifier():
    # Regression: "con" / "req" grounded exactly as substrings of conn / request near any cited line.
    assert ground(start=12, end=12, evidence="con")[0] == "ungrounded"
    assert ground(start=9, end=9, evidence="req")[0] == "ungrounded"
    # A short quote of whole identifiers still grounds where it is.
    assert ground(start=12, end=12, evidence="conn")[0] == "exact"
    assert ground(start=17, end=17, evidence="debug=True")[0] == "exact"


@pytest.mark.parametrize(
    "evidence",
    [
        "    12\t" + SQL_LINE,  # nl -ba / cat -n (mini-swe-agent's template reads with nl -ba)
        "    12      " + SQL_LINE,  # the same with the tab turned into spaces
        "12: " + SQL_LINE,  # OpenCode's read tool
        "00012| " + SQL_LINE,  # padded listing
        '    11\t    conn = sqlite3.connect("app.db")\n    12\t    ' + SQL_LINE,
        '11: conn = sqlite3.connect("app.db")\n12: ' + SQL_LINE,
    ],
)
def test_line_number_prefixes_of_every_reader_are_stripped(evidence):
    assert ground(start=11, end=12, evidence=evidence)[0] == "exact"


def test_code_that_looks_like_a_prefix_is_matched_as_written_first():
    sample = make_sample([make_file("codes.yaml", "errors:\n  404: not_found_page\n  500: server_down\n")])
    found = grounding.locate(
        file_path="codes.yaml",
        start_line=2,
        end_line=3,
        evidence="404: not_found_page\n500: server_down",
        sample=sample,
    )
    assert (found.status, found.evidence_line, found.evidence_end_line) == ("exact", 2, 3)


def test_locate_reports_where_the_evidence_is_inside_a_wide_range():
    found = grounding.locate(
        file_path="backend/app.py",
        start_line=1,
        end_line=17,
        evidence="app.run(debug=True)",
        sample=app_sample(),
    )
    assert found == grounding.Grounding("exact", 1, 17, 17, 17)
    relocated = grounding.locate(
        file_path="backend/app.py",
        start_line=2,
        end_line=2,
        evidence=SQL_LINE,
        sample=app_sample(),
    )
    assert relocated == grounding.Grounding("relocated", 12, 12, 12, 12)
    missing = grounding.locate(
        file_path="backend/app.py",
        start_line=2,
        end_line=2,
        evidence="os.system(cmd)",
        sample=app_sample(),
    )
    assert (missing.status, missing.evidence_line) == ("ungrounded", None)


def test_paths_under_the_review_root_are_relative_to_it():
    assert grounding.normalize_path("/workspace/backend/app.py", "/workspace") == "backend/app.py"
    assert grounding.normalize_path("/workspace/./backend/app.py", "/workspace/") == "backend/app.py"
    # Only the absolute root: a relative "workspace/" directory is the app's own.
    assert grounding.normalize_path("workspace/app.py", "/workspace") == "workspace/app.py"
    assert grounding.normalize_path("/workspace/backend/app.py") == "workspace/backend/app.py"
    assert ground(path="/workspace/backend/app.py", root="/workspace")[0] == "exact"


def test_listing_grounding_and_sloc_share_one_line_numbering():
    # A form feed, U+2028 and U+0085 inside lines and a CRLF ending: str.splitlines would see 8 lines. The
    # numbered listing the model is shown, grounding and SLOC (and so normalization and the static tools)
    # must all see the same 5, or an AI finding and a static finding on one line would cite different lines.
    text = "x = 1\x0c\ns = 'a\u2028b'\nt = '\x85'\npassword = 'hunter22'\r\ny = 2\n"
    f = make_file("odd.py", text)
    sample = make_sample([f])
    assert len(text.splitlines()) == 8
    assert len(sloc.split_lines(text)) == grounding.line_count(f.content) == 5
    chunks, _ = single_shot.plan_chunks([f], char_budget=100_000, seed=1)
    listing = chunks[0].units[0].text.split("\n")
    assert "   4| password = 'hunter22'" in listing
    assert "   5| y = 2" in listing
    found = grounding.locate(
        file_path="odd.py",
        start_line=4,
        end_line=4,
        evidence="password = 'hunter22'",
        sample=sample,
    )
    assert found == grounding.Grounding("exact", 4, 4, 4, 4)
    assert ground(path="odd.py", start=5, end=5, evidence="y = 2", sample=sample) == ("exact", 5, 5)
    assert ground(path="odd.py", start=8, end=8, evidence="y = 2", sample=sample)[0] == "out_of_range"
    measured = sloc.code_lines(text, "python")
    assert {4, 5} <= measured <= set(range(1, 6))
