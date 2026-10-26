"""codespell adapter (text output).

Exit codes (codespell 2.4): 0 = no misspellings, 65 = misspellings found,
64 = usage error -> failure. The exit code must agree with the output. The
"Used config files" banner (stdout) is skipped; every other non-empty line
must be ``path:line: wrong ==> suggestion[, suggestion][ | reason]``.
Files codespell reports it could not read (``WARNING: Binary file: <path>``,
``WARNING: <OS error>: <path>``) become skipped files; its utf-8 ->
iso-8859-1 fallback warnings are not skips (the file is still checked).

``--check-hidden``: codespell otherwise silently passes over every explicit
path whose name starts with a dot (.eslintrc.json, .prettierrc, ...).

codespell honours inline ``codespell:ignore`` / ``codespell:ignore-next-line``
comments and has no option to turn them off; when a selected file contains
one, codespell runs on staged copies with the tags neutralized
(``staged_argv``; line numbers are kept).
"""

from __future__ import annotations

import re
from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import CONFIG
from backend.analysis.engine.adapters.base import SRC
from backend.analysis.engine.adapters.base import STAGE
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import rel_path
from backend.analysis.engine.adapters.base import src_path
from backend.analysis.engine.adapters.base import staged_argv
from backend.analysis.engine.adapters.base import stdout_of
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

FINDINGS_EXIT = 65
_LINE = r"^(?P<path>{root}/.+?):(?P<line>\d+): (?P<wrong>.+?) ==> (?P<right>.+?)(?: \| (?P<reason>.+))?$"
_LINE_RE = {root: re.compile(_LINE.format(root=re.escape(root))) for root in (SRC, STAGE)}
_BANNER_RE = re.compile(r"^(Used config files:|\s+\d+: /)")
# WARNING: Binary file: /src/x.bin | WARNING: Permission denied: /src/x.py (codespell 2.4 _codespell.py)
_SKIP = r"^WARNING: (?P<reason>[^:]+): (?P<path>{root}/.+)$"
_SKIP_RE = {root: re.compile(_SKIP.format(root=re.escape(root))) for root in (SRC, STAGE)}
IGNORE_TAG = b"codespell:ignore"
SED_NEUTRALIZE = ["s/codespell:ignore/codespell-ignore/g"]


def marked_files(files: list[SampleFile]) -> list[str]:
    """Paths of the files with an inline ``codespell:ignore`` tag."""
    return [f.path for f in files if IGNORE_TAG in f.content]


# The utf-8 -> iso-8859-1 fallback: the file is still checked.
_ENCODING_RE = re.compile(r"^WARNING: (?:Cannot decode file using encoding|Trying next encoding)")


@register
class CodespellAdapter(ToolAdapter):
    slug = "codespell"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            f"{BIN}/codespell",
            "--config",
            f"{CONFIG}/codespell.cfg",
            "--builtin",
            ",".join(cfg["builtin"]),
            "--disable-colors",
            "--check-hidden",
            "--",
        ]
        if marked_files(files):
            return self.make_invocation(staged_argv(argv, files, SED_NEUTRALIZE))
        return self.make_invocation([*argv, *(src_path(f) for f in files)])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        code = check_exit(outcome, {0, FINDINGS_EXIT}, "codespell")
        neutralized = marked_files(files)
        root = STAGE if neutralized else SRC
        findings: list[RawFinding] = []
        for line in stdout_of(outcome, "codespell").decode("utf-8", errors="replace").splitlines():
            if not line.strip() or _BANNER_RE.match(line):
                continue
            match = _LINE_RE[root].match(line.rstrip())
            if not match:
                raise ToolFailure(reason="parse_error", message=f"unexpected codespell output line: {line[:200]!r}")
            wrong, right = match.group("wrong"), match.group("right")
            findings.append(
                RawFinding(
                    rule_id="misspelling",
                    message=f"{wrong} ==> {right}",
                    file_path=rel_path(match.group("path"), root=root),
                    line=int(match.group("line")),
                    title=f"Misspelling: {wrong}",
                    suggestion=right,
                    extra={
                        "wrong": wrong,
                        "suggestions": [s.strip() for s in right.split(",")],
                        "reason": match.group("reason") or "",
                    },
                ),
            )
        if (code == FINDINGS_EXIT) != bool(findings):
            raise ToolFailure(
                reason="parse_error",
                message=f"codespell exited {code} but reported {len(findings)} misspellings",
            )
        skipped = []
        for line in outcome.stderr.decode("utf-8", errors="replace").splitlines():
            text = line.strip()
            if _ENCODING_RE.match(text):
                continue
            match = _SKIP_RE[root].match(text)
            if match:
                skipped.append({"path": rel_path(match.group("path"), root=root), "reason": text})
        notes = [f"codespell:ignore tags neutralized in {len(neutralized)} files"] if neutralized else []
        cov = coverage(files, skipped=skipped)
        return build_parse(
            "codespell",
            files,
            findings,
            cov,
            metrics={"markers_neutralized": neutralized},
            notes=notes,
        )
