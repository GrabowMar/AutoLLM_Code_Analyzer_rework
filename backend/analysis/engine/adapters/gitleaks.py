"""Gitleaks adapter (``gitleaks dir`` over a staged copy of the selected files).

``gitleaks dir`` takes one directory and reads ``.gitleaksignore`` from it
(and from the ignore path), so an app could suppress its own leaks by
shipping fingerprints. The selected files are therefore copied into a fresh
tmpfs directory (``/tmp/scan``) and gitleaks runs from /tmp with an explicit
--config, --gitleaks-ignore-path pointing at the baked config directory and
--ignore-gitleaks-allow. Gitleaks' own control files are never copied.

``--exit-code 0`` makes "leaks found" exit 0, so ANY non-zero status is an
error (a missing binary, a crash, an unreadable config). The report is
written to a path in /out that no previous run can have left behind (each
tool container gets a fresh /out volume) and must be valid JSON.

The baked config extends gitleaks' default one, whose global path allowlist
silently skips, among others, ``bootstrap*.js``, ``jquery*.js``,
``angular*.js``, ``swagger-ui*.js``, ``gradlew``, ``go.sum`` and lock files.
Selected files matching it are never passed to gitleaks and are coverage
``skipped`` entries, never "scanned, no leaks".
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import CONFIG
from backend.analysis.engine.adapters.base import OUT
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import as_int
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import load_json
from backend.analysis.engine.adapters.base import read_report
from backend.analysis.engine.adapters.base import rel_path
from backend.analysis.engine.adapters.base import report_structure
from backend.analysis.engine.adapters.base import select_files
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolParse

REPORT = "gitleaks.json"
STAGE = "/tmp/scan"  # noqa: S108 - the tool container's private tmpfs
# Files that configure gitleaks itself: never handed to it.
CONTROL_FILES = frozenset({".gitleaksignore", ".gitleaks.toml", ".gitleaks.yml", ".gitleaks.yaml"})
# gitleaks v8.30.1 config/gitleaks.toml (sha256 e163e53b9e7e8a85...fa329caf), [allowlist] paths: the
# default global path allowlist the baked config inherits (useDefault), matched against the staged path.
DEFAULT_ALLOWLIST_PATHS = tuple(
    re.compile(pattern)
    for pattern in (
        r"gitleaks\.toml",
        r"(?i)\.(?:bmp|gif|jpe?g|png|svg|tiff?)$",
        r"(?i)\.(?:eot|[ot]tf|woff2?)$",
        r"(?i)\.(?:docx?|xlsx?|pdf|bin|socket|vsidx|v2|suo|wsuo|.dll|pdb|exe|gltf)$",
        r"go\.(?:mod|sum|work(?:\.sum)?)$",
        r"(?:^|/)vendor/modules\.txt$",
        r"(?:^|/)vendor/(?:github\.com|golang\.org/x|google\.golang\.org|gopkg\.in|istio\.io|k8s\.io|sigs\.k8s\.io)(?:/.*)?$",
        r"(?:^|/)gradlew(?:\.bat)?$",
        r"(?:^|/)gradle\.lockfile$",
        r"(?:^|/)mvnw(?:\.cmd)?$",
        r"(?:^|/)\.mvn/wrapper/MavenWrapperDownloader\.java$",
        r"(?:^|/)node_modules(?:/.*)?$",
        r"(?:^|/)(?:deno\.lock|npm-shrinkwrap\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock)$",
        r"(?:^|/)bower_components(?:/.*)?$",
        r"(?:^|/)(?:angular|bootstrap|jquery(?:-?ui)?|plotly|swagger-?ui)[a-zA-Z0-9.-]*(?:\.min)?\.js(?:\.map)?$",
        r"(?:^|/)javascript\.json$",
        r"(?:^|/)(?:Pipfile|poetry)\.lock$",
        r"(?i)(?:^|/)(?:v?env|virtualenv)/lib(?:64)?(?:/.*)?$",
        r"(?i)(?:^|/)(?:lib(?:64)?/python[23](?:\.\d{1,2})+|python/[23](?:\.\d{1,2})+/lib(?:64)?)(?:/.*)?$",
        r"(?i)(?:^|/)[a-z0-9_.]+-[0-9.]+\.dist-info(?:/.+)?$",
        r"(?:^|/)vendor/(?:bundle|ruby)(?:/.*?)?$",
        r"\.gem$",
        r"verification-metadata\.xml",
        r"Database.refactorlog",
        r"(?:^|/)\.git$",
    )
)
# Copy the selected files ("$@", relative to /src) into the stage, then scan
# the stage from /tmp so no ignore file of the app is in reach.
SCRIPT = (
    "set -eu; "
    f"mkdir {STAGE}; "
    f'cd /src; cp --parents -- "$@" {STAGE}/; cd /tmp; '
    f"exec {BIN}/gitleaks dir {STAGE} "
    f"--config {CONFIG}/gitleaks.toml --gitleaks-ignore-path {CONFIG} --ignore-gitleaks-allow "
    f"--report-format json --report-path {OUT}/{REPORT} --exit-code 0 --redact "
    "--no-banner --no-color --log-level warn"
)


@register
class GitleaksAdapter(ToolAdapter):
    slug = "gitleaks"

    def select_files(self, sample, config: dict[str, Any]) -> list[SampleFile]:
        files = select_files(sample, self.spec)
        return self.applicable([f for f in files if PurePosixPath(f.path).name not in CONTROL_FILES])

    def unsupported(self, file: SampleFile) -> str | None:
        staged = f"{STAGE}/{file.path}"
        if any(pattern.search(staged) for pattern in DEFAULT_ALLOWLIST_PATHS):
            return "gitleaks' default global path allowlist always skips this path"
        return None

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        self.effective_config(config)
        argv = ["sh", "-c", SCRIPT, "gitleaks", *(f"./{f.path}" for f in self.analysable(files))]
        return self.make_invocation(argv, report_files=[REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        check_exit(outcome, {0}, "gitleaks")
        leaks = load_json(read_report(outcome, REPORT, "gitleaks"), what="gitleaks report", expect=list)
        findings = []
        with report_structure("gitleaks report"):
            for leak in leaks:
                path = rel_path(leak["File"], root=STAGE)
                rule = str(leak["RuleID"])
                line = as_int(leak.get("StartLine"))
                findings.append(
                    RawFinding(
                        rule_id=rule,
                        message=" ".join(str(leak.get("Description") or rule).split()),
                        file_path=path,
                        line=line,
                        end_line=as_int(leak.get("EndLine")),
                        column=as_int(leak.get("StartColumn")),
                        title=rule,
                        extra={
                            "entropy": leak.get("Entropy"),
                            "end_column": as_int(leak.get("EndColumn")),
                            "tags": list(leak.get("Tags") or []),
                            # Path-relative fingerprint (gitleaks' own uses the stage path).
                            "fingerprint": f"{path}:{rule}:{line}",
                        },
                    ),
                )
        cov = coverage(files, skipped=self.unsupported_coverage(files))
        return build_parse("gitleaks", files, findings, cov, metrics={"leaks": len(findings)})
