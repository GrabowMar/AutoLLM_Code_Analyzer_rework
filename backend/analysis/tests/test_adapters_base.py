"""Shared adapter helpers: file selection, outcome checks, strict parsing, coverage."""

from __future__ import annotations

import pytest

from backend.analysis.engine import catalog
from backend.analysis.engine.adapters import base
from backend.analysis.engine.adapters import get_adapter
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure


def _file(path, *, language="python", kind="source", content=b"x = 1\n", included=True):
    return SampleFile(
        path=path,
        content=content,
        sha256="0" * 64,
        size=len(content),
        language=language,
        kind=kind,
        origin="authored",
        included=included,
        exclude_reason="" if included else "lockfile",
    )


def _sample(*files):
    return Sample(files=list(files), source="inline", sample_hash="0" * 64)


SAMPLE = _sample(
    _file("z/app.py"),
    _file("a/settings.py", kind="config"),
    _file("web/App.jsx", language="jsx"),
    _file("web/api.ts", language="typescript"),
    _file("web/Card.vue", language="vue"),
    _file("Dockerfile", language="dockerfile", kind="config"),
    _file("README.md", language="markdown", kind="doc"),
    _file(".env.example", language="env", kind="env"),
    _file("web/package-lock.json", language="json", kind="lockfile", included=False),
    _file("logo.png", language="other", kind="asset", content=b"\x89PNG\0\0", included=False),
    _file("blob.bin", language="other", kind="other", content=b"\0\1\2"),
    _file("dist/app.min.js", language="javascript", kind="build", included=False),
)


# --------------------------------------------------------------------------
# select_files
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("ruff", ["a/settings.py", "z/app.py"]),
        ("bandit", ["a/settings.py", "z/app.py"]),
        ("eslint", ["web/App.jsx", "web/Card.vue", "web/api.ts"]),
        ("semgrep", ["a/settings.py", "web/App.jsx", "web/Card.vue", "web/api.ts", "z/app.py"]),
        ("hadolint", ["Dockerfile"]),
        ("lizard", ["web/App.jsx", "web/Card.vue", "web/api.ts", "z/app.py"]),
        ("jscpd", ["web/App.jsx", "web/Card.vue", "web/api.ts", "z/app.py"]),
        (
            "codespell",
            ["Dockerfile", "README.md", "a/settings.py", "web/App.jsx", "web/Card.vue", "web/api.ts", "z/app.py"],
        ),
        (
            "gitleaks",
            [
                ".env.example",
                "Dockerfile",
                "README.md",
                "a/settings.py",
                "web/App.jsx",
                "web/Card.vue",
                "web/api.ts",
                "z/app.py",
            ],
        ),
    ],
)
def test_select_files_by_language_and_kind(slug, expected):
    files = get_adapter(slug).select_files(SAMPLE, {})
    assert [f.path for f in files] == expected


def test_select_files_never_returns_excluded_or_binary_files():
    for spec in catalog.static_specs():
        paths = {f.path for f in get_adapter(spec.slug).select_files(SAMPLE, {})}
        assert not paths & {"web/package-lock.json", "logo.png", "blob.bin", "dist/app.min.js"}, spec.slug


def test_semgrep_selects_only_languages_its_rule_packs_cover():
    adapter = get_adapter("semgrep")
    python_only = adapter.select_files(SAMPLE, {"rulesets": ["python"]})
    assert [f.path for f in python_only] == ["a/settings.py", "z/app.py"]
    no_vue = adapter.select_files(SAMPLE, {"rulesets": ["javascript"]})
    assert [f.path for f in no_vue] == ["web/App.jsx", "web/api.ts"]
    js_sample = _sample(_file("web/App.jsx", language="jsx"))
    assert adapter.select_files(js_sample, {"rulesets": ["python", "python-audit"]}) == []


def test_select_files_empty_means_not_applicable():
    sample = _sample(_file("README.md", language="markdown", kind="doc"))
    assert get_adapter("ruff").select_files(sample, {}) == []
    assert get_adapter("hadolint").select_files(sample, {}) == []


def test_gitleaks_never_receives_its_own_control_files():
    sample = _sample(
        _file(".gitleaksignore", language="other", kind="other"),
        _file("sub/.gitleaks.toml", language="toml", kind="config"),
        _file("app.py"),
    )
    assert [f.path for f in get_adapter("gitleaks").select_files(sample, {})] == ["app.py"]


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def test_rel_path():
    assert base.rel_path("/src/a/b.py") == "a/b.py"
    assert base.rel_path("./a/b.py") == "a/b.py"
    assert base.rel_path("/tmp/scan/x.py", root="/tmp/scan") == "x.py"  # noqa: S108
    assert base.rel_path("a\\b.py") == "a/b.py"
    assert base.src_path("-rf.py") == "/src/-rf.py"  # a file name can never become an option


# --------------------------------------------------------------------------
# check_outcome / check_exit
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (ExecOutcome(exit_code=None, infra_error="daemon gone"), "infra_error"),
        (ExecOutcome(exit_code=None, cancelled=True), "cancelled"),
        (ExecOutcome(exit_code=137, timed_out=True), "timeout"),
        (ExecOutcome(exit_code=137, oom_killed=True), "oom"),
        (ExecOutcome(exit_code=137), "oom"),
        (ExecOutcome(exit_code=None), "infra_error"),
        (ExecOutcome(exit_code=127, stderr=b"sh: 1: ruff: not found"), "unavailable"),
        (ExecOutcome(exit_code=126), "unavailable"),
        (ExecOutcome(exit_code=139), "tool_error"),
    ],
)
def test_check_outcome(outcome, reason):
    with pytest.raises(ToolFailure) as exc:
        base.check_outcome(outcome, "tool")
    assert exc.value.reason == reason


def test_check_outcome_order_infra_before_timeout():
    with pytest.raises(ToolFailure) as exc:
        base.check_outcome(ExecOutcome(exit_code=None, infra_error="x", timed_out=True), "tool")
    assert exc.value.reason == "infra_error"


def test_check_exit():
    assert base.check_exit(ExecOutcome(exit_code=1), {0, 1}, "tool") == 1
    with pytest.raises(ToolFailure) as exc:
        base.check_exit(ExecOutcome(exit_code=2, stderr=b"usage: tool"), {0, 1}, "tool")
    assert exc.value.reason == "tool_error"
    assert "usage: tool" in exc.value.message
    assert "expected 0, 1" in exc.value.message


def test_tail_prefers_stderr():
    assert base.tail(ExecOutcome(exit_code=1, stdout=b"out", stderr=b"err")) == "err"
    assert base.tail(ExecOutcome(exit_code=1, stdout=b"out")) == "out"
    assert base.tail(ExecOutcome(exit_code=1)) == "(no output)"
    assert len(base.tail(ExecOutcome(exit_code=1, stderr=b"x" * 5000))) == base.TAIL_CHARS


# --------------------------------------------------------------------------
# Strict parsing
# --------------------------------------------------------------------------


def test_read_report():
    outcome = ExecOutcome(exit_code=0, out_files={"r.json": b"[]", "empty.json": b"  \n"})
    assert base.read_report(outcome, "r.json", "tool") == b"[]"
    with pytest.raises(ToolFailure) as exc:
        base.read_report(outcome, "missing.json", "tool")
    assert exc.value.reason == "tool_error"
    with pytest.raises(ToolFailure) as exc:
        base.read_report(outcome, "empty.json", "tool")
    assert exc.value.reason == "parse_error"


@pytest.mark.parametrize(
    "data",
    [b"", b"   ", b"{", b"[1, 2] trailing", b"\xff\xfe", b'"a string"', b"WARNING: x\n[]"],
)
def test_load_json_is_strict(data):
    with pytest.raises(ToolFailure) as exc:
        base.load_json(data, what="report")
    assert exc.value.reason == "parse_error"


def test_load_json_checks_the_top_level_type():
    assert base.load_json(b'{"a": 1}', what="report", expect=dict) == {"a": 1}
    with pytest.raises(ToolFailure):
        base.load_json(b"[]", what="report", expect=dict)


def test_report_structure_converts_shape_errors():
    with pytest.raises(ToolFailure) as exc, base.report_structure("report"):
        _ = {}["missing"]
    assert exc.value.reason == "parse_error"
    kept = "kept"
    with pytest.raises(ToolFailure) as exc, base.report_structure("report"):
        raise ToolFailure(reason="timeout", message=kept)
    assert exc.value.reason == "timeout"


def test_as_int():
    assert base.as_int(3) == 3
    assert base.as_int("4") == 4
    assert base.as_int(0) is None
    assert base.as_int(-1) is None
    assert base.as_int(None) is None
    assert base.as_int(value=True) is None


# --------------------------------------------------------------------------
# Coverage / build_parse
# --------------------------------------------------------------------------


def test_coverage_and_build_parse():
    files = [_file("a.py"), _file("b.py"), _file("c.py")]
    cov = base.coverage(
        files,
        unparseable=[{"path": "b.py", "reason": "line 1:  bad\n syntax"}, {"path": "b.py", "reason": "dup"}],
        skipped=[{"path": "c.py", "reason": "ignored"}, {"path": "b.py", "reason": "also"}],
        partial=[{"path": "a.py", "reason": "timeout"}],
    )
    assert cov == {
        "files_selected": 3,
        "files_scanned": 1,
        "scanned": ["a.py"],
        "unparseable": [{"path": "b.py", "reason": "line 1: bad syntax"}],
        "skipped": [{"path": "c.py", "reason": "ignored"}],
        "partial": [{"path": "a.py", "reason": "timeout"}],
    }
    parse = base.build_parse("tool", files, [], cov, metrics={"m": 1})
    assert parse.findings == []
    assert parse.metrics == {"m": 1}
    assert parse.notes == ["1 of 3 files could not be parsed", "1 of 3 files were skipped"]


def test_build_parse_fails_when_nothing_was_analysed():
    files = [_file("a.py")]
    cov = base.coverage(files, unparseable=[{"path": "a.py", "reason": "syntax"}])
    with pytest.raises(ToolFailure) as exc:
        base.build_parse("tool", files, [], cov)
    assert exc.value.reason == "tool_error"
    assert "a.py: syntax" in exc.value.message


def test_complexity_summary():
    summary = base.complexity_summary([1, 2, 3, 4, 41, 12])
    assert summary == {
        "functions": 6,
        "max": 41,
        "mean": 10.5,
        "p90": 26.5,
        "rank_distribution": {"A": 4, "B": 0, "C": 1, "D": 0, "E": 0, "F": 1},
    }
    empty = base.complexity_summary([])
    assert empty["max"] is None
    assert empty["mean"] is None
    assert empty["p90"] is None
    assert base.complexity_summary([7])["p90"] == 7.0


@pytest.mark.parametrize(("value", "rank"), [(1, "A"), (5, "A"), (6, "B"), (11, "C"), (21, "D"), (31, "E"), (41, "F")])
def test_cc_rank_matches_radon(value, rank):
    assert base.cc_rank(value) == rank


def test_effective_config_rejects_stale_options():
    with pytest.raises(ToolFailure) as exc:
        get_adapter("ruff").effective_config({"removed_option": True})
    assert exc.value.reason == "invalid_config"


def test_make_invocation_uses_spec_resources():
    adapter = get_adapter("semgrep")
    invocation = adapter.make_invocation(["semgrep", 1], env={"A": "b"}, report_files=["r.json"])
    assert invocation.argv == ["semgrep", "1"]
    assert invocation.timeout_s == adapter.spec.timeout_s
    assert invocation.mem_limit == adapter.spec.mem_limit
    assert invocation.env == {"A": "b"}
    assert invocation.report_files == ("r.json",)
