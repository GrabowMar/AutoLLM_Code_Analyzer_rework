"""ShellCheck adapter (json1 on stdout).

Exit codes: 0 = no comment, 1 = comments (parse errors included); 2 (a file
could not be processed), 3 (bad syntax of the invocation) and 4 (bad options)
fail the tool. The comment list must agree with the exit code.

Isolation: ``--norc`` (no .shellcheckrc of the app). ShellCheck also reads
``# shellcheck disable=...`` directives and has no option to ignore them:
when a selected file contains one, ShellCheck runs on staged copies with the
directives neutralized (``staged_argv``; line numbers are kept). A file that
does not parse stops ShellCheck's analysis of it; its comments then end with
SC1072 ("Fix any mentioned problems and try again"), so a file with SC1072 is
unparseable (its parse comments are coverage, not findings).

zsh scripts are not analysed (ShellCheck supports sh, bash, dash and ksh):
coverage ``skipped``.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import SRC
from backend.analysis.engine.adapters.base import STAGE
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import as_int
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import load_json
from backend.analysis.engine.adapters.base import rel_path
from backend.analysis.engine.adapters.base import report_structure
from backend.analysis.engine.adapters.base import src_path
from backend.analysis.engine.adapters.base import staged_argv
from backend.analysis.engine.adapters.base import stdout_of
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

PARSE_FAILURE_CODE = 1072
UNSUPPORTED_SUFFIXES = frozenset({".zsh"})
MARKER = b"shellcheck"
SED_NEUTRALIZE = [r"s/#([[:space:]]*)shellcheck([[:space:]])/#\1llmlab-overridden\2/g"]


def marked_files(files: list[SampleFile]) -> list[str]:
    """Paths of the files that may carry a ``# shellcheck`` directive."""
    return [f.path for f in files if MARKER in f.content]


@register
class ShellcheckAdapter(ToolAdapter):
    slug = "shellcheck"

    def unsupported(self, file: SampleFile) -> str | None:
        if PurePosixPath(file.path).suffix.lower() in UNSUPPORTED_SUFFIXES:
            return "ShellCheck supports sh, bash, dash and ksh scripts only"
        return None

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        files = self.analysable(files)
        argv = [f"{BIN}/shellcheck", "--norc", "--format=json1", f"--severity={cfg['severity']}"]
        if cfg["exclude"]:
            argv.append(f"--exclude={cfg['exclude']}")
        if marked_files(files):
            return self.make_invocation(staged_argv([*argv, "--"], files, SED_NEUTRALIZE))
        return self.make_invocation([*argv, "--", *(src_path(f) for f in files)])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        code = check_exit(outcome, {0, 1}, "shellcheck")
        neutralized = marked_files(self.analysable(files))
        root = STAGE if neutralized else SRC
        report = load_json(stdout_of(outcome, "shellcheck"), what="shellcheck output", expect=dict)
        given = {f.path for f in self.analysable(files)}
        comments: list[tuple[str, dict[str, Any]]] = []
        with report_structure("shellcheck output"):
            for item in report["comments"]:
                path = rel_path(item["file"], root=root)
                if path not in given:
                    raise ToolFailure(
                        reason="parse_error",
                        message=f"shellcheck reported a file it was not given: {path}",
                    )
                comments.append((path, item))
            if (code == 1) != bool(comments):
                raise ToolFailure(
                    reason="parse_error",
                    message=f"shellcheck exited {code} but its report holds {len(comments)} comments",
                )
            broken = {path for path, item in comments if int(item["code"]) == PARSE_FAILURE_CODE}
            unparseable = []
            findings: list[RawFinding] = []
            for path, item in comments:
                rule = f"SC{int(item['code'])}"
                if path in broken:
                    if int(item["code"]) == PARSE_FAILURE_CODE:
                        continue
                    unparseable.append({"path": path, "reason": f"line {item.get('line')}: {rule} {item['message']}"})
                    continue
                findings.append(
                    RawFinding(
                        rule_id=rule,
                        message=str(item["message"]),
                        file_path=path,
                        line=as_int(item.get("line")),
                        end_line=as_int(item.get("endLine")),
                        column=as_int(item.get("column")),
                        native_severity=str(item["level"]),
                        title=rule,
                        extra={"url": f"https://www.shellcheck.net/wiki/{rule}", "fixable": bool(item.get("fix"))},
                    ),
                )
        for path in sorted(broken - {u["path"] for u in unparseable}):
            unparseable.append({"path": path, "reason": "ShellCheck could not parse the script (SC1072)"})
        notes = []
        if neutralized:
            notes.append(f"'# shellcheck' directives neutralized in {len(neutralized)} files")
        cov = coverage(files, unparseable=unparseable, skipped=self.unsupported_coverage(files))
        return build_parse(
            "shellcheck",
            files,
            findings,
            cov,
            metrics={"markers_neutralized": neutralized},
            notes=notes,
        )
