"""The scanner YAMLs' run_cmd shell and their parsers, end to end.

Each test renders a real ``data/tools/*.yaml`` run_cmd, runs it with ``sh``
(dash, as in the analyzer workspace) against stub tool binaries, and parses
what it printed (stdout and stderr merged, as ``exec_in`` returns them).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from backend.analysis.models import AnalyzerTool
from backend.analysis.seeding import DATA_DIR
from backend.analysis.services import parsers
from backend.analysis.services import runner

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")

APP_URL = "http://app:8000"

_PRINT = """#!/bin/sh
# Prints $STUB_DIR/<name>.out if present and exits with $STUB_DIR/<name>.exit (default 0).
name=$(basename "$0")
[ -f "$STUB_DIR/$name.out" ] && cat "$STUB_DIR/$name.out"
exit "$(cat "$STUB_DIR/$name.exit" 2>/dev/null || echo 0)"
"""
_DJLINT = """#!/bin/sh
# One pass per --extension: prints djlint.<ext>.out, exits djlint.<ext>.exit.
for arg in "$@"; do case "$arg" in --extension=*) ext="${arg#--extension=}";; esac; done
echo "$ext" >> "$STUB_DIR/djlint.calls"
[ -f "$STUB_DIR/djlint.$ext.out" ] && cat "$STUB_DIR/djlint.$ext.out"
exit "$(cat "$STUB_DIR/djlint.$ext.exit" 2>/dev/null || echo 0)"
"""


_ZAP = """#!/bin/sh
# Writes $STUB_DIR/zap.out to the -quickout report path.
while [ $# -gt 0 ]; do [ "$1" = "-quickout" ] && out="$2"; shift; done
cp "$STUB_DIR/zap.out" "$out"
"""


def _tool(slug: str) -> AnalyzerTool:
    data = yaml.safe_load((Path(DATA_DIR) / f"{slug}.yaml").read_text())
    return AnalyzerTool(slug=slug, run_cmd=data["run_cmd"], default_config=data.get("default_config") or {})


class _Shell:
    """Runs a tool's rendered run_cmd with stub binaries first on PATH."""

    def __init__(self, root: Path):
        self.root = root
        self.stubs, self.bin, self.work = root / "stubs", root / "bin", root / "work"
        for d in (self.stubs, self.bin, self.work):
            d.mkdir()
        scripts = {"djlint": _DJLINT, "zap": _ZAP}
        for name in ("nuclei", "zap", "wapiti", "osv-scanner", "trivy", "djlint"):
            path = self.bin / name
            path.write_text(scripts.get(name, _PRINT))
            path.chmod(0o755)

    def __call__(self, slug: str, *, config: dict | None = None) -> tuple[int, str]:
        return self.run(runner.render_command(_tool(slug), target=str(self.work), url=APP_URL, config=config))

    def run(self, cmd: str) -> tuple[int, str]:
        """Run an already rendered command; ``(exit code, stdout + stderr)``."""
        # The DAST run_cmds scratch in /tmp/<tool>: keep that inside the test's dir.
        for tool_dir in ("zap", "wapiti"):
            cmd = cmd.replace(f"/tmp/{tool_dir}", f"{self.root}/{tool_dir}")  # noqa: S108 - text replaced
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
        env["STUB_DIR"] = str(self.stubs)
        done = subprocess.run(  # noqa: S603 - the rendered command, run the way the runner execs it
            ["sh", "-c", cmd],  # noqa: S607
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=self.work,
            timeout=30,
            check=False,
        )
        return done.returncode, done.stdout


@pytest.fixture
def shell(tmp_path) -> _Shell:
    return _Shell(tmp_path)


def _stub(shell: _Shell, name: str, out: str = "", exit_code: int = 0) -> None:
    (shell.stubs / f"{name}.out").write_text(out)
    (shell.stubs / f"{name}.exit").write_text(str(exit_code))


def _hit(severity="medium") -> str:
    hit = {"template-id": "git-config", "matched-at": f"{APP_URL}/.git/config", "info": {"severity": severity}}
    return json.dumps(hit) + "\n"


# --- DAST ----------------------------------------------------------------------


def test_nuclei_clean_scan_is_a_measured_zero(shell):
    _stub(shell, "nuclei", "")  # -silent: a clean scan prints nothing, exits 0
    code, out = shell("nuclei")
    assert code == 0
    parsed = parsers.parse("nuclei", out)
    assert (parsed.findings, parsed.error, parsed.warnings) == ([], None, [])


def test_nuclei_keeps_its_exit_code_and_hits(shell):
    _stub(shell, "nuclei", _hit(), exit_code=2)
    code, out = shell("nuclei")
    assert code == 2  # the runner judges a non-zero exit with what was parsed
    parsed = parsers.parse("nuclei", out)
    assert [f.rule_id for f in parsed.findings] == ["git-config"]


def test_zap_report_is_parsed_and_a_missing_one_fails(shell):
    _stub(shell, "zap", json.dumps({"site": [{"@name": APP_URL, "alerts": []}]}))
    code, out = shell("zap")
    assert code == 0
    assert parsers.parse("zap", out).error is None
    (shell.bin / "zap").write_text("#!/bin/sh\nexit 0\n")  # writes no report
    code, out = shell("zap")
    assert code == 1
    assert parsers.parse("zap", out).error is not None


# --- Dependency scanners: lockfile gaps ---------------------------------------


def _manifests(work: Path) -> None:
    (work / "web").mkdir()
    (work / "web" / "package.json").write_text("{}")
    (work / "api").mkdir()
    (work / "api" / "package.json").write_text("{}")
    (work / "api" / "package-lock.json").write_text("{}")
    (work / "web" / "node_modules" / "x").mkdir(parents=True)
    (work / "web" / "node_modules" / "x" / "package.json").write_text("{}")


def _osv_package(name: str, groups: list[str] | None = None) -> dict:
    package = {
        "package": {"name": name, "version": "9.0.1", "ecosystem": "npm"},
        "vulnerabilities": [{"id": f"GHSA-{name}", "database_specific": {"severity": "MODERATE"}}],
        "groups": [{"ids": [f"GHSA-{name}"], "aliases": [f"GHSA-{name}"], "max_severity": "7.5"}],
    }
    if groups is not None:
        package["dependency_groups"] = groups
    return package


def _osv_report(work: Path, *packages: dict) -> str:
    return json.dumps(
        {"results": [{"source": {"path": f"{work}/api/package-lock.json"}, "packages": list(packages)}]},
        indent=2,
    )


def test_osv_warns_about_package_json_without_lockfile_and_turns_exit_1_into_0(shell):
    _manifests(shell.work)
    _stub(shell, "osv-scanner", _osv_report(shell.work, _osv_package("uuid")), exit_code=1)
    code, out = shell("osv-scanner")
    assert code == 0  # osv's "vulnerabilities found", like npm-audit / trivy / retire
    parsed = parsers.parse("osv_scanner", out)
    assert [f.tool_specific_data["package"] for f in parsed.findings] == ["uuid"]
    assert parsed.warnings == [
        "web/package.json has no lockfile beside it, so its npm dependencies were not scanned",
    ]


def test_osv_failure_without_report_stays_a_failure(shell):
    _stub(shell, "osv-scanner", "", exit_code=128)  # no package sources found
    code, out = shell("osv-scanner")
    assert code == 128
    parsed = parsers.parse("osv_scanner", out)
    assert "printed no report" in parsed.error
    assert (parsed.findings, parsed.metrics) == ([], {})


def _run_through_runner(shell: _Shell, monkeypatch, slug: str, parser_key: str):
    """The runner's container path (exec, exit-code checks, parse) over the real run_cmd."""
    monkeypatch.setattr(runner.workspace_service, "WORK_DIR", str(shell.work))

    def exec_in(_container, cmd, **_kwargs):
        code, out = shell.run(cmd)
        return {"exit_code": code, "output": out}

    monkeypatch.setattr(runner.docker_manager, "exec_in", exec_in)
    tool = _tool(slug)
    tool.parser_key, tool.run_timeout = parser_key, 30
    return runner._run_container_tool(tool, "workspace")


@pytest.mark.parametrize(
    ("exit_code", "packages", "expected"),
    [
        # Every advisory dev-only: a measurement (nothing ships), not a crash.
        (1, [_osv_package("vite", ["dev"])], {"error": None, "findings": 0, "left_out": 1}),
        (1, [_osv_package("uuid"), _osv_package("vite", ["dev"])], {"error": None, "findings": 1, "left_out": 1}),
        (0, [], {"error": None, "findings": 0, "left_out": 0}),
    ],
)
def test_osv_results_through_the_runner(shell, monkeypatch, exit_code, packages, expected):
    _stub(shell, "osv-scanner", _osv_report(shell.work, *packages), exit_code=exit_code)
    output = _run_through_runner(shell, monkeypatch, "osv-scanner", "osv_scanner")
    assert output.error == expected["error"]
    assert len(output.findings) == expected["findings"]
    assert output.metrics["dev_only_advisories_skipped"] == expected["left_out"]


@pytest.mark.parametrize("exit_code", [127, 128])
def test_osv_without_report_fails_through_the_runner(shell, monkeypatch, exit_code):
    _manifests(shell.work)  # the lockfile-gap lines alone are no report
    _stub(shell, "osv-scanner", "", exit_code=exit_code)
    output = _run_through_runner(shell, monkeypatch, "osv-scanner", "osv_scanner")
    assert output.error.startswith(f"Exited with code {exit_code}: osv-scanner printed no report")
    assert output.warnings == ["web/package.json has no lockfile beside it, so its npm dependencies were not scanned"]


@pytest.mark.parametrize(("scanners", "warned"), [("vuln,misconfig,secret", True), ("misconfig,secret", False)])
def test_trivy_warns_about_lockfile_gaps_only_when_scanning_packages(shell, scanners, warned):
    _manifests(shell.work)
    _stub(shell, "trivy", json.dumps({"Results": []}))
    code, out = shell("trivy", config={"scanners": scanners})
    assert code == 0
    parsed = parsers.parse("trivy", out)
    assert parsed.error is None
    assert bool(parsed.warnings) is warned


# --- djLint: one pass per template extension -----------------------------------


def test_djlint_lints_each_template_extension_present(shell):
    (shell.work / "templates").mkdir()
    (shell.work / "templates" / "page.jinja2").write_text("<html></html>")
    (shell.work / "templates" / "base.html").write_text("<html></html>")
    (shell.work / "node_modules" / "x").mkdir(parents=True)
    (shell.work / "node_modules" / "x" / "doc.j2").write_text("")
    _stub(shell, "djlint.html", "\nLinted 1 file, found 0 errors.\n")
    _stub(
        shell,
        "djlint.jinja2",
        "work/templates/page.jinja2:1:0:H005:Html tag should have lang attribute.\n\nLinted 1 file, found 1 error.\n",
        exit_code=1,
    )
    code, out = shell("djlint")
    assert (shell.stubs / "djlint.calls").read_text().split() == ["html", "jinja2"]
    assert code == 1  # lint errors, as djLint exits
    parsed = parsers.parse("djlint", out)
    assert [(f.rule_id, f.file_path) for f in parsed.findings] == [("H005", "templates/page.jinja2")]
    assert (parsed.error, parsed.warnings) == (None, [])


def test_djlint_crashed_pass_is_a_warning_and_its_exit_code_wins(shell):
    (shell.work / "a.html").write_text("")
    (shell.work / "b.jinja").write_text("")
    _stub(shell, "djlint.html", "", exit_code=2)
    _stub(shell, "djlint.jinja", "work/b.jinja:1:0:H005:Html tag should have lang attribute.\n", exit_code=1)
    code, out = shell("djlint")
    assert code == 2
    parsed = parsers.parse("djlint", out)
    assert len(parsed.findings) == 1
    assert parsed.warnings == ["djlint --extension=html exited with code 2"]


def test_djlint_with_nothing_linted_is_not_a_clean_result(shell):
    (shell.work / "page.j2").write_text("")
    _stub(shell, "djlint.j2", "No files to check! \U0001f622\n")  # djLint 1.36 exits 0 here
    code, out = shell("djlint")
    assert code == 0
    assert "No files to check" in parsers.parse("djlint", out).error
