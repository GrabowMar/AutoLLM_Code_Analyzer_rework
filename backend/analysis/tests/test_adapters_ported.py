"""Adapters of the tools ported from the v1 tool shop, replayed against REAL outputs of the pinned image.

flake8, perflint, deptry, depcheck, HTMLHint, Stylelint and ShellCheck; the
generic fixture checks (argv, selection, relative paths, clean/hostile
fixtures, runtime failures) live in ``test_adapters.py``.
"""

from __future__ import annotations

import json

import pytest
import yaml

from backend.analysis.engine import catalog
from backend.analysis.engine import plan
from backend.analysis.engine.adapters import _projects
from backend.analysis.engine.adapters import get_adapter
from backend.analysis.engine.adapters.base import STAGE
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import ToolFailure
from backend.analysis.tests.support import capture_tool_outputs as cap

PORTED = ("flake8", "perflint", "deptry", "depcheck", "htmlhint", "stylelint", "shellcheck")


def interpret(slug, case, **overrides):
    fixture = cap.load_fixture(slug, case)
    files = cap.replay_files(fixture)
    return get_adapter(slug).interpret(cap.replay_outcome(fixture, **overrides), files, fixture["config"])


def failure(slug, case, **overrides):
    with pytest.raises(ToolFailure) as exc:
        interpret(slug, case, **overrides)
    return exc.value


def found(parse):
    return sorted((f.file_path, f.line, f.rule_id) for f in parse.findings)


def out_files(slug, case):
    fixture = cap.load_fixture(slug, case)
    return {k: cap._unblob(v) for k, v in fixture["outcome"]["out_files"].items()}


# --------------------------------------------------------------------------
# Catalog / profiles
# --------------------------------------------------------------------------


@pytest.mark.parametrize("slug", PORTED)
def test_ported_tools_are_opt_in(slug):
    """The default profile config (and so every system profile's defaults) is unchanged."""
    spec = catalog.get_spec(slug)
    assert spec.kind == "static"
    assert spec.default_enabled is False
    assert spec.default_required is False
    assert slug not in {t["slug"] for t in plan.default_profile_config()["tools"]}


@pytest.mark.parametrize("slug", PORTED)
def test_ported_tools_have_mapping_tables(slug):
    """Severity and category come from a table of the tool's own (not the generic fallback)."""
    mappings = cap.ANALYSIS_DIR / "data" / "mappings"
    assert slug in yaml.safe_load((mappings / "severity.yaml").read_text())["tools"]
    categories = yaml.safe_load((mappings / "categories.yaml").read_text())
    assert slug in categories["tools"]
    assert categories["emits"][slug]


# --------------------------------------------------------------------------
# flake8
# --------------------------------------------------------------------------


def test_flake8_findings():
    parse = interpret("flake8", "findings")
    rules = {f.rule_id for f in parse.findings if f.file_path == "vulnerable/backend/app.py"}
    assert {"F401", "F841"} <= rules
    unused = next(f for f in parse.findings if f.rule_id == "F401")
    assert (unused.line, unused.column) == (9, 1)
    assert "json" in unused.message


def test_flake8_ignores_noqa_and_app_config():
    assert {f.rule_id for f in interpret("flake8", "suppressed").findings} >= {"F401"}
    hostile = {k for k in found(interpret("flake8", "hostile")) if k[0].startswith("vulnerable/")}
    plain = {k for k in found(interpret("flake8", "findings")) if k[0].startswith("vulnerable/")}
    assert hostile == plain


def test_flake8_config_reaches_argv_and_selects():
    fixture = cap.load_fixture("flake8", "config")
    argv = fixture["invocation"]["argv"]
    assert "--select=F,E9,C90" in argv
    assert "--max-complexity=10" in argv
    assert "--" not in argv  # flake8 would read the options before "--" as file names
    rules = {f.rule_id[:1] for f in interpret("flake8", "config").findings}
    assert rules <= {"F", "C"}


def test_flake8_syntax_errors_are_coverage():
    parse = interpret("flake8", "syntax")
    assert parse.findings == []
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["svc/broken.py"]


def test_flake8_report_is_strict():
    report = out_files("flake8", "findings")
    lines = report["flake8.txt"]
    assert failure("flake8", "findings", exit_code=0).reason == "parse_error"
    bad = {**report, "flake8.txt": lines + b"garbage without separators\n"}
    assert failure("flake8", "findings", out_files=bad).reason == "parse_error"
    cut = {**report, "flake8.txt": lines[:-1]}
    assert failure("flake8", "findings", out_files=cut).reason == "parse_error"
    other = {**report, "flake8.txt": b"/src/elsewhere.py\x1f1\x1f1\x1fF401\x1fx\n"}
    assert failure("flake8", "findings", out_files=other).reason == "parse_error"


# --------------------------------------------------------------------------
# perflint
# --------------------------------------------------------------------------


def test_perflint_findings():
    parse = interpret("perflint", "findings")
    rules = {f.rule_id for f in parse.findings}
    assert {"use-list-copy", "unnecessary-list-cast", "loop-invariant-statement"} <= rules
    assert {f.file_path for f in parse.findings} == {"vulnerable/backend/reports.py"}
    assert all(f.extra["message_id"].startswith("W8") for f in parse.findings)


def test_perflint_runs_its_own_pylint_and_rcfile():
    argv = cap.load_fixture("perflint", "findings")["invocation"]["argv"]
    assert argv[0] == "/opt/analyzer/venvs/perflint/bin/pylint"
    assert "--rcfile=/opt/analyzer/config/perflint.pylintrc" in argv
    assert "--disable=loop-try-except-usage" in argv
    assert "--output-format=json2:/out/perflint.json" in argv


def test_perflint_recovers_inline_suppressed_messages():
    parse = interpret("perflint", "suppressed")
    hit = next(f for f in parse.findings if f.file_path == "suppressed/perf.py")
    assert hit.rule_id == "use-list-copy"
    assert hit.extra["suppressed_inline"] is True
    assert hit.extra["message_id"] == "W8402"


def test_perflint_config_disables_messages():
    parse = interpret("perflint", "config")  # extra_disable=loop-invariant-statement, report_suppressed off
    assert "loop-invariant-statement" not in {f.rule_id for f in parse.findings}
    assert parse.findings


def test_perflint_syntax_error_is_coverage():
    parse = interpret("perflint", "syntax")
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["svc/broken.py"]


def test_pylint_adapter_is_unchanged_by_the_refactor():
    """perflint reuses the pylint adapter; pylint's own argv keeps its binary, rcfile and report."""
    argv = cap.load_fixture("pylint", "findings")["invocation"]["argv"]
    assert argv[:3] == [
        "/opt/analyzer/bin/pylint",
        "--rcfile=/opt/analyzer/config/pylintrc",
        "--output-format=json2:/out/pylint.json",
    ]


# --------------------------------------------------------------------------
# deptry
# --------------------------------------------------------------------------


def test_deptry_findings_per_project():
    parse = interpret("deptry", "findings")
    assert found(parse) == [
        ("vulnerable/backend/app.py", 15, "DEP001"),  # import yaml, not declared
        ("vulnerable/backend/requirements.txt", 2, "DEP002"),  # requests, never imported
    ]
    assert parse.metrics["projects"] == ["clean/backend", "vulnerable/backend"]
    missing = parse.findings[0]
    assert missing.extra == {"module": "yaml", "project": "vulnerable/backend"}


def test_deptry_stages_each_project_and_ignores_app_config():
    argv = cap.load_fixture("deptry", "findings")["invocation"]["argv"]
    script = argv[2]
    assert f"{STAGE}/p0" in script
    assert f"{STAGE}/p1" in script
    assert "--config /opt/analyzer/config/deptry.toml" in script
    assert "--exclude '^$'" in script
    assert found(interpret("deptry", "hostile")) == found(interpret("deptry", "findings"))


def test_deptry_config_ignores_rules():
    assert {f.rule_id for f in interpret("deptry", "config").findings} == {"DEP001"}


def test_deptry_unparseable_and_orphan_files():
    parse = interpret("deptry", "syntax")
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["svc/broken.py"]
    assert [s["path"] for s in parse.coverage["skipped"]] == ["tools/orphan.py"]


def test_deptry_needs_a_requirements_file():
    sample = cap.make_sample({"app.py": "import flask\n", "pyproject.toml": "[project]\nname = 'x'\n"})
    adapter = get_adapter("deptry")
    assert adapter.select_files(sample, adapter.effective_config({})) == []


def test_deptry_project_status_and_report_are_strict():
    files = out_files("deptry", "findings")
    index = next(k for k, v in files.items() if k.endswith(".json") and json.loads(v))
    base = index.removesuffix(".json")
    assert failure("deptry", "findings", out_files={**files, f"{base}.rc": b"2\n"}).reason == "tool_error"
    assert failure("deptry", "findings", out_files={**files, f"{base}.rc": b"0\n"}).reason == "parse_error"
    no_rc = {k: v for k, v in files.items() if k != f"{base}.rc"}
    assert failure("deptry", "findings", out_files=no_rc).reason == "tool_error"
    no_report = {k: v for k, v in files.items() if k != index}
    assert failure("deptry", "findings", out_files=no_report).reason == "tool_error"
    assert failure("deptry", "findings", out_files={**files, index: b"[{"}).reason == "parse_error"


# --------------------------------------------------------------------------
# depcheck
# --------------------------------------------------------------------------


def test_depcheck_findings_per_project():
    parse = interpret("depcheck", "findings")
    assert found(parse) == [
        ("vulnerable/frontend/package.json", 11, "unused-dependency"),  # lodash
        ("vulnerable/frontend/src/main.jsx", 2, "missing-dependency"),  # axios
    ]
    assert parse.metrics["projects"] == ["clean/frontend", "vulnerable/frontend"]
    assert {f.extra["package"] for f in parse.findings} == {"lodash", "axios"}


def test_depcheck_ignores_app_config_and_gitignore():
    assert found(interpret("depcheck", "hostile")) == found(interpret("depcheck", "findings"))
    script = cap.load_fixture("depcheck", "findings")["invocation"]["argv"][2]
    assert "--config=/opt/analyzer/config/depcheck.json" in script


def test_depcheck_skip_missing_config():
    assert {f.rule_id for f in interpret("depcheck", "config").findings} == {"unused-dependency"}


def test_depcheck_unparseable_and_orphan_files():
    parse = interpret("depcheck", "syntax")
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["web/broken.jsx"]
    assert [s["path"] for s in parse.coverage["skipped"]] == ["lib/orphan.js"]


def test_depcheck_exit_status_must_agree_with_report():
    files = out_files("depcheck", "findings")
    issues = next(k for k, v in files.items() if k.endswith(".out") and b"lodash" in v)
    base = issues.removesuffix(".out")
    assert failure("depcheck", "findings", out_files={**files, f"{base}.rc": b"0\n"}).reason == "parse_error"
    # depcheck exits 255 for "issues" and for "could not run" alike: only valid JSON is a result.
    broken = {**files, f"{base}.out": b"", f"{base}.err": b"Path /tmp/x does not exist\n"}
    assert failure("depcheck", "findings", out_files=broken).reason == "parse_error"
    assert failure("depcheck", "findings", out_files={**files, f"{base}.rc": b"1\n"}).reason == "tool_error"
    report = json.loads(files[issues])
    report["invalidDirs"] = {f"{STAGE}/p1/x": "EACCES"}
    bad_dir = {**files, issues: json.dumps(report).encode()}
    assert failure("depcheck", "findings", out_files=bad_dir).reason == "tool_error"


# --------------------------------------------------------------------------
# HTMLHint
# --------------------------------------------------------------------------


def test_htmlhint_findings():
    parse = interpret("htmlhint", "findings")
    assert found(parse) == [
        ("vulnerable/frontend/index.html", 1, "doctype-first"),
        ("vulnerable/frontend/index.html", 4, "title-require"),
        ("vulnerable/frontend/index.html", 6, "alt-require"),
        ("vulnerable/frontend/index.html", 8, "id-unique"),
        ("vulnerable/frontend/index.html", 9, "tag-pair"),
    ]
    assert parse.coverage["files_scanned"] == 2  # clean/ index.html scanned, nothing to report


def test_htmlhint_inline_rule_comments_are_neutralized():
    fixture = cap.load_fixture("htmlhint", "suppressed")
    assert fixture["invocation"]["argv"][:2] == ["sh", "-c"]
    parse = interpret("htmlhint", "suppressed")
    assert found(parse) == [("suppressed/page.html", 3, "alt-require")]
    assert parse.metrics["markers_neutralized"] == ["suppressed/page.html"]


def test_htmlhint_exit_code_must_agree_and_output_is_strict():
    assert failure("htmlhint", "findings", exit_code=0).reason == "parse_error"
    assert failure("htmlhint", "clean", exit_code=1).reason == "parse_error"
    assert failure("htmlhint", "findings", stdout=b"").reason == "parse_error"
    other = json.dumps([{"file": "/src/elsewhere.html", "messages": []}]).encode()
    assert failure("htmlhint", "findings", stdout=other).reason == "parse_error"


# --------------------------------------------------------------------------
# Stylelint
# --------------------------------------------------------------------------


def test_stylelint_findings():
    assert found(interpret("stylelint", "findings")) == [
        ("vulnerable/frontend/src/styles.css", 2, "property-no-unknown"),
        ("vulnerable/frontend/src/styles.css", 6, "no-duplicate-selectors"),
        ("vulnerable/frontend/src/styles.css", 11, "declaration-property-value-no-unknown"),
    ]


def test_stylelint_ignores_disable_comments():
    assert found(interpret("stylelint", "suppressed")) == [("suppressed/site.css", 3, "property-no-unknown")]
    argv = cap.load_fixture("stylelint", "findings")["invocation"]["argv"]
    assert "--ignore-disables" in argv


def test_stylelint_syntax_errors_and_scss():
    parse = interpret("stylelint", "syntax")
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["web/broken.css"]
    assert [s["path"] for s in parse.coverage["skipped"]] == ["web/theme.scss"]
    assert "web/theme.scss" not in cap.load_fixture("stylelint", "syntax")["invocation"]["argv"][-1]


def test_stylelint_failures():
    # 2 means "problems found" for stylelint: only 1 (fatal), 64 (usage) and 78 (config) fail.
    assert failure("stylelint", "findings", exit_code=1, stderr=b"Error: ENOENT").reason == "tool_error"
    assert failure("stylelint", "findings", exit_code=78).reason == "tool_error"
    assert failure("stylelint", "findings", exit_code=0).reason == "parse_error"
    report = json.loads(out_files("stylelint", "findings")["stylelint.json"])
    report[0]["invalidOptionWarnings"] = [{"text": "Invalid option value"}]
    bad = {"stylelint.json": json.dumps(report).encode()}
    assert failure("stylelint", "findings", out_files=bad).reason == "tool_error"
    assert failure("stylelint", "findings", out_files={"stylelint.json": b"[]"}).reason == "parse_error"


# --------------------------------------------------------------------------
# ShellCheck
# --------------------------------------------------------------------------


def test_shellcheck_findings():
    parse = interpret("shellcheck", "findings")
    assert found(parse) == [
        ("vulnerable/scripts/backup.sh", 3, "SC2086"),
        ("vulnerable/scripts/backup.sh", 3, "SC2164"),
        ("vulnerable/scripts/backup.sh", 4, "SC2086"),
    ]
    assert {f.native_severity for f in parse.findings} == {"info", "warning"}


def test_shellcheck_directives_are_neutralized():
    parse = interpret("shellcheck", "suppressed")
    assert found(parse) == [("suppressed/run.sh", 3, "SC2086")]
    assert parse.metrics["markers_neutralized"] == ["suppressed/run.sh"]


def test_shellcheck_config_and_parse_failures():
    assert interpret("shellcheck", "config").findings == []  # severity=warning, SC2164 excluded
    parse = interpret("shellcheck", "syntax")
    assert parse.findings == []
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["bin/broken.sh"]
    assert [s["path"] for s in parse.coverage["skipped"]] == ["bin/prompt.zsh"]


def test_shellcheck_exit_code_must_agree_with_report():
    assert failure("shellcheck", "findings", exit_code=0).reason == "parse_error"
    assert failure("shellcheck", "clean", exit_code=1).reason == "parse_error"
    assert failure("shellcheck", "findings", exit_code=2).reason == "tool_error"


# --------------------------------------------------------------------------
# Project staging helper
# --------------------------------------------------------------------------


def test_projects_assign_files_to_the_deepest_root():
    sample = cap.make_sample(
        {
            "requirements.txt": "flask\n",
            "app.py": "import flask\n",
            "svc/requirements.txt": "requests\n",
            "svc/api.py": "import requests\n",
            "svc/sub/util.py": "x = 1\n",
        },
    )
    items, orphans = _projects.projects(list(sample.files), frozenset({"requirements.txt"}))
    assert orphans == []
    assert [(p.root, [f.path for f in p.files]) for p in items] == [
        ("", ["app.py", "requirements.txt"]),
        ("svc", ["svc/api.py", "svc/requirements.txt", "svc/sub/util.py"]),
    ]
    assert items[1].sample_path(f"{STAGE}/p1/sub/util.py") == "svc/sub/util.py"
    assert items[0].sample_path("./app.py") == "app.py"


def test_project_driver_quotes_paths_and_records_status():
    sample = cap.make_sample({"my app/requirements.txt": "flask\n", "my app/a b.py": "import flask\n"})
    items, _ = _projects.projects(list(sample.files), frozenset({"requirements.txt"}))
    argv = _projects.driver_argv("deptry", items, ["deptry", ".", "--json-output", "/out/deptry-{index}.json"])
    script = argv[2]
    assert "cd '/src/my app'" in script
    assert "'a b.py'" in script
    assert "/out/deptry-0.json" in script
    assert "echo $rc > /out/deptry-0.rc" in script
    outcome = ExecOutcome(exit_code=0, out_files={"deptry-0.out": b"", "deptry-0.rc": b"1\n"})
    assert _projects.project_outcome(outcome, "deptry", items[0], frozenset({0, 1})).rc == 1
