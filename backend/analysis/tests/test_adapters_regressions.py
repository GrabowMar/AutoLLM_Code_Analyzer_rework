"""Adapter regressions: files a tool passes over silently, and in-file markers an app could switch it off with.

``tests/fixtures/adapter_regressions.json`` holds real outputs of the pinned tools for each case (see its
"about"). The argv the adapter builds today must equal the recorded one, and the interpretation must
account for every selected file (scanned, skipped with a reason, or unparseable) and recover what the
markers would have hidden. The ``check_*`` functions are shared with the docker variant
(``test_adapters_regressions_docker.py``), which runs the same cases in the real image.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from backend.analysis.engine import sample as sample_mod
from backend.analysis.engine.adapters import base
from backend.analysis.engine.adapters import codespell as codespell_adapter
from backend.analysis.engine.adapters import get_adapter
from backend.analysis.engine.adapters import gitleaks as gitleaks_adapter
from backend.analysis.engine.adapters import jscpd as jscpd_adapter
from backend.analysis.engine.adapters import lizard as lizard_adapter
from backend.analysis.engine.adapters import pylint as pylint_adapter
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import ToolFailure
from backend.analysis.tests.support import capture_tool_outputs as cap

ANALYSIS_DIR = Path(__file__).resolve().parents[1]
DATA = json.loads((ANALYSIS_DIR / "tests" / "fixtures" / "adapter_regressions.json").read_text(encoding="utf-8"))
CASES = DATA["cases"]
IMAGE_DIR = ANALYSIS_DIR / "images" / "analyzer"


def prepare(name: str):
    """(entry, adapter, effective config, selected files) of a regression case."""
    entry = CASES[name]
    adapter = get_adapter(entry["slug"])
    config = adapter.effective_config(entry["config"])
    files = adapter.select_files(sample_mod.resolve_inline(entry["files"]), config)
    return entry, adapter, config, files


def recorded_outcome(entry: dict, **overrides) -> ExecOutcome:
    values = {**entry["outcome"], **overrides}
    return ExecOutcome(
        exit_code=values["exit_code"],
        stdout=values["stdout"].encode(),
        stderr=values["stderr"].encode(),
        out_files={name: data.encode() for name, data in values["out_files"].items()},
    )


def interpret(name: str, **overrides):
    entry, adapter, config, files = prepare(name)
    return adapter.interpret(recorded_outcome(entry, **overrides), files, config)


def keys(parse) -> list[tuple[str, int | None, str]]:
    return sorted((f.file_path, f.line, f.rule_id) for f in parse.findings)


def skipped(parse) -> list[str]:
    return [entry["path"] for entry in parse.coverage["skipped"]]


@pytest.mark.parametrize("name", sorted(CASES))
def test_recorded_argv_is_what_the_adapter_builds_today(name):
    entry, adapter, config, files = prepare(name)
    assert [f.path for f in files] == entry["selected"]
    if files:
        assert adapter.invocation(files, config).argv == entry["argv"]


# --------------------------------------------------------------------------
# Shared expectations (also applied to real-image runs by the docker variant)
# --------------------------------------------------------------------------


def check_pylint_pragmas(parse) -> None:
    """Whole-file pragmas are neutralized: those files are linted, their messages marked suppressed."""
    assert parse.coverage["skipped"] == []
    assert parse.coverage["files_scanned"] == 6
    by_file: dict[str, set[str]] = {}
    for finding in parse.findings:
        by_file.setdefault(finding.file_path, set()).add(finding.rule_id)
    for path in ("svc/legacy.py", "svc/late.py", "svc/listed.py"):
        assert {"eval-used", "unused-import"} <= by_file[path], path
        marked = [f for f in parse.findings if f.file_path == path]
        assert all(f.extra["suppressed_inline"] and f.extra["file_pragma_line"] for f in marked), path
    # A pragma inside a string literal is not a pragma (pylint reads comments only).
    assert "eval-used" in by_file["svc/text.py"]
    assert not any(f.extra.get("suppressed_inline") for f in parse.findings if f.file_path == "svc/text.py")
    # Per-message disables are still recovered through I0020.
    permsg = next(f for f in parse.findings if f.file_path == "svc/permsg.py" and f.rule_id == "eval-used")
    assert permsg.extra["suppressed_inline"]
    assert parse.metrics["file_pragmas_overridden"] == ["svc/late.py", "svc/legacy.py", "svc/listed.py"]


def check_pylint_pragmas_honoured(parse) -> None:
    """report_suppressed off: the app's whole-file pragmas hold, and those files are coverage gaps."""
    assert skipped(parse) == ["svc/late.py", "svc/legacy.py", "svc/listed.py"]
    assert all("pragma" in entry["reason"] for entry in parse.coverage["skipped"])
    assert parse.coverage["scanned"] == ["svc/app.py", "svc/permsg.py", "svc/text.py"]
    assert {f.file_path for f in parse.findings} <= {"svc/app.py", "svc/permsg.py", "svc/text.py"}


def check_vulture_noqa(parse) -> None:
    """``# noqa`` and ``# noqa: F401,F841`` no longer hide dead code."""
    assert {(f.line, f.rule_id) for f in parse.findings} == {
        (1, "unused-import"),
        (2, "unused-import"),
        (3, "unused-import"),
        (4, "unused-import"),
        (5, "unused-import"),
        (8, "unused-variable"),
        (10, "unreachable-code"),
    }


def check_vulture_unreadable(parse) -> None:
    """Files vulture could not read are unparseable even when other files have dead code (exit 3)."""
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["v/asc.py", "v/gz.py"]
    assert parse.coverage["scanned"] == ["v/dead.py"]
    assert keys(parse) == [("v/dead.py", 1, "unused-import")]


def check_codespell_hidden(parse) -> None:
    assert sorted(f.extra["wrong"] for f in parse.findings if f.file_path == "frontend/.prettierrc") == [
        "occurence",
        "recieve",
        "seperate",
    ]
    assert parse.coverage["scanned"] == ["backend/app.py", "frontend/.prettierrc"]


def check_codespell_inline_ignore(parse) -> None:
    """``codespell:ignore`` and ``codespell:ignore-next-line`` no longer hide misspellings."""
    assert {(f.file_path, f.line, f.extra["wrong"]) for f in parse.findings} == {
        ("app.py", 1, "recieve"),
        ("app.py", 3, "seperate"),
        ("notes.md", 1, "occurence"),
    }
    assert parse.metrics["markers_neutralized"] == ["app.py"]


def check_radon_pyi(parse) -> None:
    assert skipped(parse) == ["backend/types.pyi", "gui.pyw"]
    assert parse.coverage["scanned"] == ["backend/app.py"]
    assert [f["path"] for f in parse.metrics["functions"]] == ["backend/app.py"]


def check_lizard_markers(parse) -> None:
    names = {(f["path"], f["name"]) for f in parse.metrics["functions"]}
    # "#lizard forgive" would drop forgiven(); the scaffold's "...GENERATED CODE" header would drop App.jsx.
    assert {("backend/app.py", "forgiven"), ("frontend/src/App.jsx", "pick"), ("frontend/src/App.jsx", "App")} <= names
    assert skipped(parse) == ["backend/stub.pyi"]
    assert parse.metrics["markers_neutralized"] == ["backend/app.py", "frontend/src/App.jsx"]


def check_jscpd_ignore(parse) -> None:
    assert parse.metrics["clones"] == 1
    clone = parse.findings[0]
    assert {clone.file_path, clone.extra["second_file"]} == {"web/a.js", "web/b.js"}
    assert parse.metrics["markers_neutralized"] == ["web/b.js"]


def check_detect_secrets_files(parse) -> None:
    assert skipped(parse) == ["Gemfile.lock", "static/theme.css"]
    assert {f.file_path for f in parse.findings} == {"backend/swagger_config.py", "docs/swagger.yaml", "static/api.js"}


def check_gitleaks_allowlist(parse) -> None:
    assert skipped(parse) == ["frontend/src/bootstrap.js"]
    assert keys(parse) == [("backend/app.py", 1, "aws-access-token")]


def check_bandit_paths(parse) -> None:
    assert parse.coverage["scanned"] == [".github/scripts/deploy.py", "app/CVSSScore.py", "app/ok.py"]
    assert ("app/CVSSScore.py", 5, "B301") in keys(parse)
    assert (".github/scripts/deploy.py", 5, "B602") in keys(parse)


CHECKS = {
    "pylint_pragmas": check_pylint_pragmas,
    "pylint_pragmas_honoured": check_pylint_pragmas_honoured,
    "vulture_noqa": check_vulture_noqa,
    "vulture_unreadable": check_vulture_unreadable,
    "codespell_hidden": check_codespell_hidden,
    "codespell_inline_ignore": check_codespell_inline_ignore,
    "radon_pyi": check_radon_pyi,
    "lizard_markers": check_lizard_markers,
    "jscpd_ignore": check_jscpd_ignore,
    "detect_secrets_files": check_detect_secrets_files,
    "gitleaks_allowlist": check_gitleaks_allowlist,
    "bandit_paths": check_bandit_paths,
}


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_real_outputs_meet_the_expectations(name):
    CHECKS[name](interpret(name))


def test_every_case_with_files_has_a_check():
    assert {name for name, entry in CASES.items() if entry["selected"]} == set(CHECKS)


# --------------------------------------------------------------------------
# Selection: what a tool cannot read is skipped, never scanned
# --------------------------------------------------------------------------


def test_a_tool_that_can_read_none_of_its_files_is_not_applicable():
    entry, _adapter, _config, files = prepare("radon_only_pyi")
    assert entry["selected"] == []
    assert files == []


def test_unsupported_files_are_never_passed_to_the_tool():
    for name in ("radon_pyi", "lizard_markers", "detect_secrets_files", "gitleaks_allowlist"):
        _entry, adapter, config, files = prepare(name)
        argv = " ".join(adapter.invocation(files, config).argv)
        for path in [e["path"] for e in adapter.unsupported_coverage(files)]:
            assert path not in argv, (name, path)


def test_all_files_skipped_would_be_a_failure_not_zero_findings():
    entry, adapter, config, files = prepare("radon_pyi")
    only_unsupported = [f for f in files if adapter.unsupported(f)]
    with pytest.raises(ToolFailure) as exc:
        adapter.interpret(recorded_outcome(entry), only_unsupported, config)
    assert exc.value.reason == "tool_error"


@pytest.mark.parametrize(
    ("path", "allowlisted"),
    [
        ("frontend/src/bootstrap.js", True),
        ("static/jquery-3.7.1.min.js", True),
        ("public/swagger-ui-bundle.js", True),
        ("angular.js", True),
        ("go.sum", True),
        ("gradlew", True),
        ("frontend/src/App.jsx", False),
        ("backend/swagger_config.py", False),
        ("frontend/src/bootstrapApp.ts", False),
    ],
)
def test_gitleaks_default_path_allowlist_mirror(path, allowlisted):
    file = sample_mod.resolve_inline({path: "x = 1\n"}).files[0]
    assert (get_adapter("gitleaks").unsupported(file) is not None) is allowlisted
    assert all(isinstance(p, re.Pattern) for p in gitleaks_adapter.DEFAULT_ALLOWLIST_PATHS)


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("static/theme.css", "is_non_text_file"),
        ("assets/app.js.map", "is_non_text_file"),
        ("Gemfile.lock", "is_lock_file"),
        ("ios/Podfile.lock", "is_lock_file"),
        ("docs/swagger.yaml", None),
        ("static/THEME.CSS", None),  # detect-secrets compares the suffix case-sensitively
    ],
)
def test_detect_secrets_mirrors_its_filename_filters(path, reason):
    file = sample_mod.resolve_inline({path: "x = 1\n"}).files[0]
    found = get_adapter("detect-secrets").unsupported(file)
    assert (reason in found) if reason else found is None


# --------------------------------------------------------------------------
# Derived failure modes (from the image's golden outputs)
# --------------------------------------------------------------------------


def _golden(slug: str, case: str):
    fixture = cap.load_fixture(slug, case)
    return fixture, cap.replay_files(fixture)


def test_pylint_file_ignored_is_skipped_not_clean():
    fixture, files = _golden("pylint", "findings")
    report = json.loads(cap._unblob(fixture["outcome"]["out_files"]["pylint.json"]))
    target = "/src/vulnerable/backend/app.py"
    report["messages"] = [m for m in report["messages"] if m["path"] != target]
    report["messages"].append(
        {
            "type": "info",
            "symbol": "file-ignored",
            "message": "Ignoring entire file",
            "messageId": "I0013",
            "confidence": "UNDEFINED",
            "module": "app",
            "obj": "",
            "line": 1,
            "column": 0,
            "endLine": None,
            "endColumn": None,
            "path": target,
            "absolutePath": target,
        },
    )
    outcome = cap.replay_outcome(fixture, out_files={"pylint.json": json.dumps(report).encode()})
    parse = get_adapter("pylint").interpret(outcome, files, fixture["config"])
    assert skipped(parse) == ["vulnerable/backend/app.py"]
    assert "vulnerable/backend/app.py" not in parse.coverage["scanned"]
    assert not any(f.file_path == "vulnerable/backend/app.py" for f in parse.findings)


@pytest.mark.parametrize(
    ("source", "line"),
    [
        (b"# pylint: skip-file\nimport os\n", 1),
        (b"import os\n#pylint:disable=all\n", 2),
        (b"x = 1  # pylint: disable = unused-import , all\n", 1),
        (b"x = 1  # noqa  # pylint: disable-all\n", 1),
        (b"# pylint: disable=unused-import, skip-file\n", 1),
        (b'NOTE = "# pylint: skip-file"\n', None),
        (b"# pylint: disable=call-arg\n", None),
        (b"# pylint: disable-next=all\nx = 1\n", None),
        (b"x = '''unterminated\n# pylint: skip-file\n", None),  # does not tokenize: no comment, no pragma
    ],
)
def test_pylint_whole_file_pragma_detection(source, line):
    file = sample_mod.resolve_inline({"m.py": source.decode()}).files[0]
    assert pylint_adapter.file_pragmas([file]) == ({"m.py": line} if line else {})


def test_bandit_files_missing_from_the_report_metrics_are_skipped():
    fixture, files = _golden("bandit", "findings")
    report = json.loads(cap._unblob(fixture["outcome"]["out_files"]["bandit.json"]))
    target = "/src/clean/backend/bookmarks.py"
    del report["metrics"][target]
    report["results"] = [r for r in report["results"] if r["filename"] != target]
    outcome = cap.replay_outcome(fixture, out_files={"bandit.json": json.dumps(report).encode()})
    parse = get_adapter("bandit").interpret(outcome, files, fixture["config"])
    assert skipped(parse) == ["clean/backend/bookmarks.py"]
    assert "clean/backend/bookmarks.py" not in parse.coverage["scanned"]


def test_bandit_runs_without_default_excludes():
    argv = cap.load_fixture("bandit", "findings")["invocation"]["argv"]
    assert argv[argv.index("--exclude") + 1] == ""


def test_codespell_encoding_fallback_is_not_a_skip():
    fixture, files = _golden("codespell", "findings")
    stderr = (
        b'WARNING: Cannot decode file using encoding "utf-8": /src/vulnerable/backend/app.py\n'
        b'WARNING: Trying next encoding "iso-8859-1"\n'
        b"WARNING: Binary file: /src/clean/backend/app.py\n"
    )
    parse = get_adapter("codespell").interpret(cap.replay_outcome(fixture, stderr=stderr), files, fixture["config"])
    assert skipped(parse) == ["clean/backend/app.py"]
    assert "vulnerable/backend/app.py" in parse.coverage["scanned"]


def test_codespell_and_detect_secrets_flags():
    assert "--check-hidden" in cap.load_fixture("codespell", "findings")["invocation"]["argv"]
    argv = cap.load_fixture("detect-secrets", "findings")["invocation"]["argv"]
    assert "detect_secrets.filters.heuristic.is_swagger_file" in argv


def test_vulture_read_error_with_exit_1_alone_is_unparseable():
    entry, adapter, config, files = prepare("vulture_unreadable")
    unreadable = [f for f in files if f.path != "v/dead.py"]
    with pytest.raises(ToolFailure):  # nothing left that vulture could analyse
        adapter.interpret(recorded_outcome(entry, exit_code=1, stdout=""), unreadable, config)
    parse = adapter.interpret(recorded_outcome(entry, exit_code=1, stdout=""), files, config)
    assert [u["path"] for u in parse.coverage["unparseable"]] == ["v/asc.py", "v/gz.py"]


# --------------------------------------------------------------------------
# Staged copies: markers neutralized with real sh + GNU sed, line numbers kept
# --------------------------------------------------------------------------


def _run_staged(tmp_path: Path, files: dict[str, str], sed: list[str]) -> dict[str, str]:
    """Run staged_argv's script with /src and the stage mapped into tmp_path; the staged copies."""
    if not shutil.which("sed") or not shutil.which("sh"):
        pytest.skip("sh/sed not available")
    src, stage = tmp_path / "src", tmp_path / "stage"
    for path, text in files.items():
        (src / path).parent.mkdir(parents=True, exist_ok=True)
        (src / path).write_text(text, encoding="utf-8")
    sample_files = sample_mod.resolve_inline(files).files
    argv = base.staged_argv(["printf", "%s\\n", "--"], sample_files, sed)
    assert f"cd {base.STAGE_CWD};" in argv[2]  # the tool never runs from a parent of the stage
    script = argv[2].replace(base.STAGE, str(stage)).replace(base.STAGE_CWD, str(tmp_path / "cwd"))
    script = script.replace(f"cd {base.SRC};", f"cd {src};")
    proc = subprocess.run(["sh", "-c", script, *argv[3:]], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert proc.returncode == 0, proc.stderr
    listed = proc.stdout.splitlines()
    assert listed[0] == "--"
    assert listed[1:] == [f"{stage}/{f.path}" for f in sample_files]
    return {path: (stage / path).read_text(encoding="utf-8") for path in files}


def test_staged_pylint_pragmas_are_neutralized_and_lines_kept(tmp_path):
    files = {
        "a.py": "# pylint: disable=all\nimport os\n",
        "b.py": "import os  # pylint: disable=unused-import\n",
        "-odd name.py": "x = 1  # pylint: skip-file\n",
    }
    staged = _run_staged(tmp_path, files, pylint_adapter.SED_NEUTRALIZE)
    assert staged["a.py"] == "# pylint-overridden: disable=all\nimport os\n"
    assert staged["b.py"] == files["b.py"]  # per-message disables are recovered through I0020 instead
    assert staged["-odd name.py"] == "x = 1  # pylint-overridden: skip-file\n"
    for path, text in staged.items():
        assert text.count("\n") == files[path].count("\n")
        assert pylint_adapter.file_pragmas(sample_mod.resolve_inline({path: text}).files) == {}


def test_staged_lizard_jscpd_and_codespell_markers_are_neutralized(tmp_path):
    files = {
        "App.jsx": "// LLM: REPLACE THIS ENTIRE FILE WITH YOUR GENERATED CODE\nexport const a = 1;\n",
        "app.py": "def f():  #lizard forgive\n    return 1\n# lizard forgives(CCN)\n",
        "b.js": "/* jscpd:ignore-start */\nconst x = 1;\n// jscpd:ignore-end\n",
    }
    files["c.py"] = "x = 1  # codespell:ignore recieve\n# codespell:ignore-next-line\n"
    seds = [*lizard_adapter.SED_NEUTRALIZE, *jscpd_adapter.SED_NEUTRALIZE, *codespell_adapter.SED_NEUTRALIZE]
    staged = _run_staged(tmp_path, files, seds)
    joined = "".join(staged.values())
    assert "GENERATED CODE" not in joined
    assert "lizard forgive" not in joined
    assert "jscpd:ignore" not in joined
    assert "codespell:ignore" not in joined
    for path, text in staged.items():
        assert text.count("\n") == files[path].count("\n")


# --------------------------------------------------------------------------
# Image scripts and configs
# --------------------------------------------------------------------------


def test_vulture_wrapper_disables_noqa_parsing(monkeypatch):
    """vulture_isolated.py replaces vulture.noqa.parse_noqa with a parser that finds no suppression."""
    noqa = types.ModuleType("vulture.noqa")
    noqa.parse_noqa = lambda code: {"all": {1}}
    core = types.ModuleType("vulture.core")
    core.main = lambda: None
    package = types.ModuleType("vulture")
    package.noqa, package.core = noqa, core
    monkeypatch.setitem(sys.modules, "vulture", package)
    monkeypatch.setitem(sys.modules, "vulture.noqa", noqa)
    monkeypatch.setitem(sys.modules, "vulture.core", core)
    spec = importlib.util.spec_from_file_location("vulture_isolated", IMAGE_DIR / "scripts" / "vulture_isolated.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.disable_noqa(noqa)
    lines = noqa.parse_noqa(["import json  # noqa"])
    assert lines["all"] == set()
    assert lines["V104"] == set()


def test_eslint_config_has_language_independent_rule_semantics():
    """Static guard; the behaviour is checked in the image by the docker variant."""
    text = (IMAGE_DIR / "config" / "eslint.config.mjs").read_text(encoding="utf-8")
    core = re.search(r'name: "llmlab/core",\s*files: \[([^\]]*)\]', text)
    assert core
    assert core.group(1).split(", ") == ["...JS_FILES", "...TS_FILES", "...VUE_FILES"]
    assert text.count("jsxPragma: null") == 2  # .ts/.tsx and TypeScript inside .vue
    assert re.search(r'name: "llmlab/typescript-rules",\s*files: \[\.\.\.TS_FILES, isTypeScriptSfc\]', text)
    for rule in ("no-undef", "no-redeclare", "no-dupe-class-members", "prefer-const", "no-var"):
        assert f'"{rule}"' in text
