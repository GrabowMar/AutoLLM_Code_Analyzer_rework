"""The mypy pre-filter of the analyzer image (``images/analyzer/scripts/mypy_prefilter.py``).

One file mypy cannot read must not abort the whole check: not when it is passed, not when another file
imports it, not when it parses but cannot compile, not when mypy still blocks on a syntax error the
pre-filter did not predict. The end-to-end cases run the real script with the mypy of the test
environment (the image pins 2.4; the same cases were checked against 2.4.0 outside the image);
the docker variant runs them in the image.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.analysis.engine.adapters import get_adapter
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import ToolFailure

IMAGE_DIR = Path(__file__).resolve().parents[1] / "images" / "analyzer"
SCRIPT = IMAGE_DIR / "scripts" / "mypy_prefilter.py"
BAKED_CONFIG = IMAGE_DIR / "config" / "mypy.ini"
RETURN_ERROR = 'def add(a: int, b: int) -> int:\n    return "x"\n'
TRUNCATED = "class User:\n    def __init__(self, name:\n"


def _load():
    spec = importlib.util.spec_from_file_location("mypy_prefilter", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prefilter = _load()


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "module"),
    [
        ("models.py", "models"),
        ("pkg/models.py", "pkg.models"),
        ("pkg/__init__.py", "pkg"),
        ("pkg/sub/types.pyi", "pkg.sub.types"),
        ("my-app/models.py", None),
        ("README.md", None),
    ],
)
def test_module_names_follow_explicit_package_bases(rel, module):
    assert prefilter.module_name(f"/src/{rel}", "/src") == module


def test_config_flag_is_split_from_the_other_arguments():
    assert prefilter.split_config(["--config-file=/c.ini", "--output=json"]) == ("/c.ini", ["--output=json"])
    assert prefilter.split_config(["--config-file", "/c.ini", "-x"]) == ("/c.ini", ["-x"])


def test_derived_config_skips_broken_modules(tmp_path):
    path = prefilter.write_config(str(BAKED_CONFIG), {"models", "pkg.models"}, str(tmp_path))
    text = Path(path).read_text()
    assert text.startswith(BAKED_CONFIG.read_text().rstrip("\n"))
    assert "[mypy-models]\nfollow_imports = skip\n" in text
    assert "[mypy-pkg.models]\nfollow_imports = skip\n" in text


@pytest.mark.parametrize(
    ("source", "broken"),
    [
        ("x = 1\n", False),
        (TRUNCATED, True),
        ("def f():\n    pass\nbreak\n", True),  # parses, does not compile (mypy: blocking error)
        ("yield 1\n", True),
        ('x = "\\d"\n', False),  # a SyntaxWarning is not an error
    ],
)
def test_check_parses_and_compiles(tmp_path, source, broken):
    path = tmp_path / "m.py"
    path.write_text(source)
    assert (prefilter.check(str(path), (3, 12)) is not None) is broken


def test_inline_config_lines_are_neutralized_in_staged_copies(tmp_path):
    root = tmp_path / "src"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_bytes(b"# mypy: ignore-errors\nx: int = 'a'\n  # mypy: not at column 0\n")
    (root / "b.txt").write_text("ignored")
    staged = Path(prefilter.stage(str(root), str(tmp_path / "work")))
    text = (staged / "pkg" / "a.py").read_bytes()
    assert text == b"# mypy-inline-config-ignored: ignore-errors\nx: int = 'a'\n  # mypy: not at column 0\n"
    assert not (staged / "b.txt").exists()
    assert prefilter.has_inline_config(str(root / "pkg" / "a.py"))


def test_blocking_syntax_errors_are_recognised_only_when_nothing_else_was_printed():
    syntax = {"file": "/src/a.py", "line": 1, "severity": "error", "code": "syntax", "message": "invalid syntax"}
    other = {**syntax, "code": "return-value"}
    assert prefilter.blocking_syntax_errors(json.dumps(syntax) + "\n", "/src") == {"/src/a.py": syntax}
    assert prefilter.blocking_syntax_errors(json.dumps(syntax) + "\n" + json.dumps(other), "/src") is None
    assert prefilter.blocking_syntax_errors("Traceback (most recent call last):\n", "/src") is None
    assert prefilter.blocking_syntax_errors(json.dumps({**syntax, "file": "/usr/lib/x.py"}), "/src") is None
    assert prefilter.blocking_syntax_errors("", "/src") is None


# --------------------------------------------------------------------------
# End to end with a real mypy
# --------------------------------------------------------------------------


def _mypy_available() -> bool:
    return importlib.util.find_spec("mypy") is not None


needs_mypy = pytest.mark.skipif(not _mypy_available(), reason="mypy is not installed in this environment")


def run_prefilter(tmp_path: Path, files: dict[str, str], *, stage_in_cwd: bool = False):
    """(exit code, report, JSON lines, stderr) of the pre-filter over *files* (sample-relative paths).

    *stage_in_cwd*: staged copies go below mypy's working directory, as in the image (cwd and
    TMPDIR are both /tmp there), so mypy prints their paths relative to it.
    """
    root = tmp_path / "src"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    report = tmp_path / "report.json"
    argv = [
        sys.executable,
        str(SCRIPT),
        str(report),
        "3.12",
        f"--config-file={BAKED_CONFIG}",
        "--output=json",
        "--check-untyped-defs",
        "--",
        *(str(root / rel) for rel in sorted(files)),
    ]
    env = {key: value for key, value in os.environ.items() if key != "MYPYPATH"}
    if stage_in_cwd:
        env["TMPDIR"] = str(cwd)
    proc = subprocess.run(  # noqa: S603 - the pre-filter script with this interpreter
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**env, "MYPYPATH": str(root)},
        check=False,
    )
    payload = json.loads(report.read_text())

    def rel(path: str) -> str:
        return os.path.relpath(path, root)

    payload["checked"] = [rel(p) for p in payload["checked"]]
    payload["unparseable"] = [{**u, "path": rel(u["path"])} for u in payload["unparseable"]]
    payload["inline_config_neutralized"] = [rel(p) for p in payload["inline_config_neutralized"]]
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    for item in lines:
        # Paths mypy printed relative to its cwd resolve against it (never against this process's cwd).
        item["file"] = rel(str(cwd / item["file"]))
    return proc.returncode, payload, lines, proc.stderr


def errors(lines):
    return sorted((item["file"], item["code"]) for item in lines if item["severity"] == "error")


@needs_mypy
@pytest.mark.parametrize(
    ("files", "broken", "skipped_module", "checked_error"),
    [
        # flat scaffold layout: app.py at the root imports a truncated sibling module
        (
            {"app.py": "from models import User\n\n\n" + RETURN_ERROR, "models.py": TRUNCATED},
            "models.py",
            "models",
            "app.py",
        ),
        # package with a relative import
        (
            {
                "pkg/__init__.py": "",
                "pkg/app.py": "from .models import User\n\n\n" + RETURN_ERROR,
                "pkg/models.py": TRUNCATED,
            },
            "pkg/models.py",
            "pkg.models",
            "pkg/app.py",
        ),
        # the Flask circular pattern: the entry point itself is broken
        (
            {
                "app.py": "from flask import Flask\napp = Flask(\n",
                "routes.py": "from app import app\n\n\n" + RETURN_ERROR,
            },
            "app.py",
            "app",
            "routes.py",
        ),
        # parses but does not compile, imported by a checked file
        (
            {"app.py": "import helpers\n\n\n" + RETURN_ERROR, "helpers.py": "def f():\n    pass\nbreak\n"},
            "helpers.py",
            "helpers",
            "app.py",
        ),
    ],
)
def test_an_imported_broken_module_does_not_hide_the_other_errors(
    tmp_path,
    files,
    broken,
    skipped_module,
    checked_error,
):
    code, report, lines, stderr = run_prefilter(tmp_path, files)
    assert code == 1, (stderr, lines)
    assert [u["path"] for u in report["unparseable"]] == [broken]
    assert skipped_module in report["skipped_imports"]
    assert (checked_error, "return-value") in errors(lines)
    assert all(item["file"] != broken for item in lines)


@needs_mypy
def test_free_text_type_comment_does_not_abort_the_check(tmp_path):
    """Blocking on older mypy (re-run without the file), a non-blocking error on 2.4: never exit 2."""
    code, report, lines, stderr = run_prefilter(
        tmp_path,
        {"models.py": 'role = "admin"  # type: list of user ids\n', "ok.py": RETURN_ERROR},
    )
    assert code == 1, (stderr, lines)
    assert ("ok.py", "return-value") in errors(lines)
    if report["runs"] > 1:
        assert [u["path"] for u in report["unparseable"]] == ["models.py"]


@needs_mypy
def test_inline_mypy_configuration_cannot_silence_the_check(tmp_path):
    code, report, lines, _stderr = run_prefilter(
        tmp_path,
        {
            "app.py": "# mypy: ignore-errors\n" + RETURN_ERROR,
            "other.py": '# mypy: disable-error-code="return-value"\n' + RETURN_ERROR,
            "plain.py": RETURN_ERROR,
        },
    )
    assert code == 1
    assert report["inline_config_neutralized"] == ["app.py", "other.py"]
    # Paths are mapped back from the staged copies; line numbers are the originals.
    assert sorted((item["file"], item["line"]) for item in lines) == [("app.py", 3), ("other.py", 3), ("plain.py", 2)]


@needs_mypy
def test_inline_config_paths_are_mapped_back_when_mypy_prints_them_relative(tmp_path):
    """Regression: in the image the staged copies live below mypy's cwd (/tmp), so mypy printed
    ``llmlab-mypy-xxx/src/app.py`` and the pre-filter mapped back only the absolute form."""
    code, report, lines, _stderr = run_prefilter(
        tmp_path,
        {"app.py": "# mypy: ignore-errors\n" + RETURN_ERROR},
        stage_in_cwd=True,
    )
    assert code == 1
    assert report["inline_config_neutralized"] == ["app.py"]
    assert [(item["file"], item["line"]) for item in lines] == [("app.py", 3)]


def test_adapter_rejects_findings_on_files_it_did_not_select():
    from backend.analysis.engine import sample as sample_mod

    sample = sample_mod.resolve_inline({"app.py": RETURN_ERROR})
    adapter = get_adapter("mypy")
    files = adapter.select_files(sample, adapter.effective_config({}))
    report = {"checked": ["/src/app.py"], "unparseable": [], "skipped_imports": [], "runs": 1}
    line = {
        "file": "llmlab-mypy-abc/src/app.py",
        "line": 2,
        "column": 11,
        "severity": "error",
        "message": "Incompatible return value",
        "code": "return-value",
        "hint": None,
    }
    outcome = ExecOutcome(
        exit_code=1,
        stdout=(json.dumps(line) + "\n").encode(),
        out_files={"mypy-files.json": json.dumps(report).encode()},
    )
    with pytest.raises(ToolFailure) as exc:
        adapter.interpret(outcome, files, adapter.effective_config({}))
    assert exc.value.reason == "parse_error"


@needs_mypy
def test_without_broken_files_the_output_is_mypys_own(tmp_path):
    code, report, lines, _stderr = run_prefilter(tmp_path, {"app.py": RETURN_ERROR, "lib.py": "X: int = 1\n"})
    assert code == 1
    assert report["runs"] == 1
    assert report["skipped_imports"] == []
    assert errors(lines) == [("app.py", "return-value")]


@needs_mypy
def test_nothing_checkable_runs_nothing(tmp_path):
    code, report, lines, _stderr = run_prefilter(tmp_path, {"broken.py": TRUNCATED})
    assert (code, report["checked"], report["runs"], lines) == (0, [], 0, [])


# --------------------------------------------------------------------------
# The adapter reads the extended report
# --------------------------------------------------------------------------


def test_adapter_reports_skipped_imports_and_neutralized_inline_config():
    from backend.analysis.engine import sample as sample_mod

    adapter = get_adapter("mypy")
    files = adapter.select_files(
        sample_mod.resolve_inline({"app.py": "# mypy: ignore-errors\n" + RETURN_ERROR, "models.py": TRUNCATED}),
        {},
    )
    report = {
        "python": "3.12.15",
        "target": "3.12",
        "checked": ["/src/app.py"],
        "unparseable": [{"path": "/src/models.py", "reason": "'(' was never closed", "line": 2}],
        "skipped_imports": ["models"],
        "inline_config_neutralized": ["/src/app.py"],
        "runs": 1,
    }
    error = {
        "file": "/src/app.py",
        "line": 3,
        "column": 11,
        "message": 'Incompatible return value type (got "str", expected "int")',
        "hint": None,
        "code": "return-value",
        "severity": "error",
    }
    outcome = ExecOutcome(
        exit_code=1,
        stdout=(json.dumps(error) + "\n").encode(),
        out_files={"mypy-files.json": json.dumps(report).encode()},
    )
    parse = adapter.interpret(outcome, files, {})
    assert parse.coverage["scanned"] == ["app.py"]
    assert parse.coverage["unparseable"] == [{"path": "models.py", "reason": "line 2: '(' was never closed"}]
    assert parse.metrics["skipped_imports"] == ["models"]
    assert parse.metrics["inline_config_neutralized"] == ["app.py"]
    assert any("inline configuration" in note for note in parse.notes)
