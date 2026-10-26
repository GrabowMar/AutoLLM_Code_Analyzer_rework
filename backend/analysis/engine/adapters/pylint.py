"""Pylint adapter (json2 output, baked rcfile).

Exit status (pylint 4, no --exit-zero) is a bit mask: 1 fatal, 2 error,
4 warning, 8 refactor, 16 convention, 32 usage error. Bit 32 (e.g. an
unknown option, nothing to lint) fails the tool; the other bits only say
which message types occurred. Messages are classified:

* E0001 syntax-error and F-type (astroid crash, file not found) -> unparseable
  files (coverage), never findings;
* a message on a file outside /src (e.g. the rcfile, E0015) is a broken
  configuration -> failure;
* I0020 suppressed-message (enabled when ``report_suppressed``) -> the
  message the app disabled with ``# pylint: disable``, re-materialized as a
  finding with ``extra.suppressed_inline`` (pylint cannot be told to ignore
  inline pragmas, so this keeps the app from silencing its scanner);
* I0013 file-ignored (always enabled) -> pylint skipped the whole file
  because of a ``# pylint: skip-file`` / ``disable-all`` / ``disable=all``
  pragma: a coverage ``skipped`` entry, never "scanned, 0 messages";
* everything else -> a finding; rule_id is the message symbol (stable and
  readable), the message id is kept in ``extra.message_id``.

A whole-file pragma cannot be recovered through I0020: pylint returns before
any checker runs. With ``report_suppressed`` (the default) a sample that has
one is linted from staged copies in which those pragma comments are
neutralized (``staged_argv``; line numbers are kept), and every message of
such a file is marked like the other inline-suppressed messages
(``extra.suppressed_inline``, ``extra.file_pragma_line``). With
``report_suppressed`` off the app's pragmas are honoured and such files are
coverage gaps (I0013).
"""

from __future__ import annotations

import functools
import io
import json
import re
import tokenize
from pathlib import Path
from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import CONFIG
from backend.analysis.engine.adapters.base import OUT
from backend.analysis.engine.adapters.base import SRC
from backend.analysis.engine.adapters.base import STAGE
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_outcome
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import load_json
from backend.analysis.engine.adapters.base import read_report
from backend.analysis.engine.adapters.base import rel_path
from backend.analysis.engine.adapters.base import report_structure
from backend.analysis.engine.adapters.base import src_path
from backend.analysis.engine.adapters.base import staged_argv
from backend.analysis.engine.adapters.base import tail
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

REPORT = "pylint.json"
USAGE_ERROR_BIT = 32
MESSAGES_FILE = Path(__file__).with_name("pylint_messages.json")
MESSAGE_TYPES = {"C": "convention", "R": "refactor", "W": "warning", "E": "error", "F": "fatal", "I": "info"}
_SUPPRESSED_RE = re.compile(r"^Suppressed '?([a-z0-9-]+)'? \(from line (\d+)\)")
_PARSE_FAILURES = frozenset({"syntax-error", "E0001"})
FILE_IGNORED = "file-ignored"
# A pragma comment that switches pylint off for the whole file (pylint 4.1 message_state_handler:
# the skip-file or deprecated disable-all action, or "all" in a disable list).
_FILE_PRAGMA_RE = re.compile(
    r"#.*?\bpylint:(?:[^;#\n]*\b(?:skip-file|disable-all)\b|[^;#\n]*\bdisable\s*=[^;#\n]*\ball\b)",
)
# The same in sed -E: such a pragma's "pylint:" becomes "pylint-overridden:", which pylint does not read.
SED_NEUTRALIZE = [
    r"/#.*\bpylint:([^;#]*\b(skip-file|disable-all)\b|[^;#]*\bdisable[[:space:]]*=[^;#]*\ball\b)/"
    r" s/\bpylint:/pylint-overridden:/g",
]


@functools.cache
def message_ids() -> dict[str, str]:
    """symbol -> message id of the pinned pylint (``pylint --list-msgs`` of the image)."""
    return dict(json.loads(MESSAGES_FILE.read_text(encoding="utf-8"))["messages"])


def file_pragmas(files: list[SampleFile]) -> dict[str, int]:
    """``{path: line}`` of the files a comment pragma switches off for pylint entirely."""
    out = {}
    for file in files:
        if b"pylint" in file.content:
            line = _file_pragma_line(file.content)
            if line:
                out[file.path] = line
    return out


def _file_pragma_line(content: bytes) -> int | None:
    # Comments only, as pylint reads them; a file that does not tokenize does not parse
    # either (E0001, unparseable), so it has no pragma to neutralize.
    try:
        for token in tokenize.tokenize(io.BytesIO(content).readline):
            if token.type == tokenize.COMMENT and _FILE_PRAGMA_RE.search(token.string):
                return token.start[0]
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError, LookupError):
        return None
    return None


@register
class PylintAdapter(ToolAdapter):
    slug = "pylint"

    def _overridden(self, files: list[SampleFile], cfg: dict[str, Any]) -> dict[str, int]:
        """Whole-file pragmas neutralized in staged copies (only when suppressed messages are reported)."""
        return file_pragmas(files) if cfg["report_suppressed"] else {}

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            f"{BIN}/pylint",
            f"--rcfile={CONFIG}/pylintrc",
            f"--output-format=json2:{OUT}/{REPORT}",
            "--persistent=n",
            "--jobs=1",
        ]
        # Order matters: pylint applies --enable/--disable left to right.
        if cfg["include_refactor"]:
            argv += ["--enable=R", "--disable=duplicate-code"]
        if cfg["include_convention"]:
            argv.append("--enable=C")
        if cfg["report_suppressed"]:
            argv.append("--enable=suppressed-message")
        if cfg["extra_disable"]:
            argv.append(f"--disable={cfg['extra_disable']}")
        # I0013, last so no option can switch it off: a file pylint skipped for a whole-file
        # pragma must never count as scanned.
        argv.append(f"--enable={FILE_IGNORED}")
        if self._overridden(files, cfg):
            return self.make_invocation(staged_argv([*argv, "--"], files, SED_NEUTRALIZE), report_files=[REPORT])
        return self.make_invocation([*argv, "--", *(src_path(f) for f in files)], report_files=[REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        check_outcome(outcome, "pylint")
        code = int(outcome.exit_code or 0)
        if code & USAGE_ERROR_BIT or code >= 2 * USAGE_ERROR_BIT:
            raise ToolFailure(
                reason="tool_error",
                message=f"pylint reported a usage error (exit {code}): {tail(outcome)}",
            )
        report = load_json(read_report(outcome, REPORT, "pylint"), what="pylint report", expect=dict)
        cfg = self.effective_config(config)
        overridden = self._overridden(files, cfg)
        root = STAGE if overridden else SRC
        findings: list[RawFinding] = []
        unparseable: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        suppressed = 0
        with report_structure("pylint report"):
            messages = report["messages"]
            stats = report.get("statistics") or {}
            for msg in messages:
                # absolutePath: "path" is relative to the cwd when the file lies below it.
                path = str(msg.get("absolutePath") or msg.get("path") or "")
                symbol = str(msg["symbol"])
                if not path.startswith(f"{root}/"):
                    raise ToolFailure(
                        reason="tool_error",
                        message=f"pylint configuration problem: {symbol}: {msg['message']}",
                    )
                rel = rel_path(path, root=root)
                if symbol in _PARSE_FAILURES or msg["type"] == "fatal":
                    unparseable.append({"path": rel, "reason": f"{msg['messageId']} {symbol}: {msg['message']}"})
                    continue
                if symbol == FILE_IGNORED:
                    skipped.append(
                        {
                            "path": rel,
                            "reason": "pylint skipped the whole file: a '# pylint: skip-file' / 'disable=all' "
                            f"pragma on line {msg.get('line')}",
                        },
                    )
                    continue
                if symbol == "suppressed-message":
                    if cfg["report_suppressed"]:
                        findings.append(_suppressed_finding(msg, rel))
                        suppressed += 1
                    continue
                finding = _finding(msg, rel)
                if rel in overridden:
                    # The app switched pylint off for this file: an inline-suppressed message.
                    finding.extra.update(suppressed_inline=True, file_pragma_line=overridden[rel])
                    suppressed += 1
                findings.append(finding)
        metrics = {
            "message_types": dict(stats.get("messageTypeCount") or {}),
            "modules_linted": stats.get("modulesLinted"),
            "suppressed_inline": suppressed,
            "file_pragmas_overridden": sorted(overridden),
        }
        notes = []
        if overridden:
            notes.append(f"whole-file pylint pragmas neutralized in {len(overridden)} files")
        cov = coverage(files, unparseable=unparseable, skipped=skipped)
        return build_parse("pylint", files, findings, cov, metrics=metrics, notes=notes)


def _column(value: Any) -> int | None:
    # pylint columns are 0-based
    return int(value) + 1 if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _finding(msg: dict[str, Any], rel: str) -> RawFinding:
    line = msg.get("line")
    end_line = msg.get("endLine")
    return RawFinding(
        rule_id=str(msg["symbol"]),
        message=str(msg["message"]),
        file_path=rel,
        line=int(line) if isinstance(line, int) and line > 0 else None,
        end_line=int(end_line) if isinstance(end_line, int) and end_line > 0 else None,
        column=_column(msg.get("column")),
        native_severity=str(msg["type"]),
        native_confidence=str(msg.get("confidence") or ""),
        title=str(msg["symbol"]),
        extra={
            "message_id": str(msg.get("messageId") or ""),
            "obj": str(msg.get("obj") or ""),
            "end_column": _column(msg.get("endColumn")),
        },
    )


def _suppressed_finding(msg: dict[str, Any], rel: str) -> RawFinding:
    """I0020 'Suppressed <symbol> (from line N)' -> the suppressed message as a finding."""
    match = _SUPPRESSED_RE.match(str(msg["message"]))
    if not match:
        raise ToolFailure(
            reason="parse_error",
            message=f"unexpected pylint suppressed-message text: {msg['message']!r}",
        )
    symbol, pragma_line = match.group(1), int(match.group(2))
    message_id = message_ids().get(symbol, "")
    line = msg.get("line")
    return RawFinding(
        rule_id=symbol,
        message=f"{symbol} (suppressed inline by a pylint pragma on line {pragma_line})",
        file_path=rel,
        line=int(line) if isinstance(line, int) and line > 0 else None,
        native_severity=MESSAGE_TYPES.get(message_id[:1], ""),
        title=symbol,
        extra={"message_id": message_id, "suppressed_inline": True, "pragma_line": pragma_line},
    )
