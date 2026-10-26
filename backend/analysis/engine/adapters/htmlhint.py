"""HTMLHint adapter (JSON on stdout).

Exit codes: 0 = no message, 1 = at least one message (warnings included);
anything else fails the tool, and the report must agree with the exit code.
The JSON lists only files with messages, each by the path it was given;
a path that was not given fails the parse.

Isolation: ``--config`` names the baked ``config/htmlhint.json`` (HTMLHint's
defaults plus alt-require), so an app's ``.htmlhintrc`` is never looked up.
HTMLHint also reads rule switches from ``<!-- htmlhint rule:false -->``
comments and has no option to ignore them: when a selected file contains one,
HTMLHint runs on staged copies with those comments neutralized
(``staged_argv``; line numbers are kept). HTMLHint does not parse strictly, so
there are no unparseable files; a broken document yields tag-pair findings.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import CONFIG
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

MARKER = b"htmlhint"
SED_NEUTRALIZE = [r"s/<!--([[:space:]]*)htmlhint/<!--\1llmlab-overridden/g"]


def marked_files(files: list[SampleFile]) -> list[str]:
    """Paths of the files that may carry an inline ``<!-- htmlhint ... -->`` configuration."""
    return [f.path for f in files if MARKER in f.content.lower()]


@register
class HtmlhintAdapter(ToolAdapter):
    slug = "htmlhint"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        self.effective_config(config)
        argv = [f"{BIN}/htmlhint", "--config", f"{CONFIG}/htmlhint.json", "--format", "json", "--nocolor"]
        if marked_files(files):
            return self.make_invocation(staged_argv([*argv, "--"], files, SED_NEUTRALIZE))
        return self.make_invocation([*argv, "--", *(src_path(f) for f in files)])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        code = check_exit(outcome, {0, 1}, "htmlhint")
        neutralized = marked_files(files)
        root = STAGE if neutralized else SRC
        items = load_json(stdout_of(outcome, "htmlhint"), what="htmlhint output", expect=list)
        selected = {f.path for f in files}
        findings: list[RawFinding] = []
        with report_structure("htmlhint output"):
            for entry in items:
                path = rel_path(entry["file"], root=root)
                if path not in selected:
                    raise ToolFailure(
                        reason="parse_error",
                        message=f"htmlhint reported a file it was not given: {path}",
                    )
                for msg in entry["messages"]:
                    rule = msg["rule"]
                    findings.append(
                        RawFinding(
                            rule_id=str(rule["id"]),
                            message=str(msg["message"]),
                            file_path=path,
                            line=as_int(msg.get("line")),
                            column=as_int(msg.get("col")),
                            native_severity=str(msg["type"]),
                            title=str(rule["id"]),
                            snippet=str(msg.get("evidence") or "")[:500],
                            extra={"url": str(rule.get("link") or "")},
                        ),
                    )
        if (code == 1) != bool(findings):
            raise ToolFailure(
                reason="parse_error",
                message=f"htmlhint exited {code} but its report holds {len(findings)} messages",
            )
        notes = []
        if neutralized:
            notes.append(f"inline <!-- htmlhint --> comments neutralized in {len(neutralized)} files")
        metrics = {"markers_neutralized": neutralized}
        return build_parse("htmlhint", files, findings, coverage(files), metrics=metrics, notes=notes)
