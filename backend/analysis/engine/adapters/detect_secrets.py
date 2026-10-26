"""detect-secrets adapter (``detect-secrets scan`` JSON on stdout).

detect-secrets filters paths relative to the cwd (absolute paths are
silently dropped), so it runs from /src (``env -C /src``, read-only) on the
selected files with ``--all-files`` (/src is not a git repository). Network
verification is off (``--no-verify``: deterministic, no egress) and the inline
allowlist filter is disabled, so "pragma: allowlist secret" cannot hide a
secret. ``scan`` exits 0 whenever it ran; anything else is a failure, and the
JSON on stdout must parse. The plugins and filters used are recorded.

detect-secrets silently passes over some file names whatever it is told:
any path matching ``.*swagger.*`` (``is_swagger_file``: disabled here, such
files are ordinary code and config), files with an extension it treats as
non-text (``is_non_text_file``: .css, .map, .svg, .lock, ...) and lock files
(``is_lock_file``). The last two are mirrored below: those files are never
passed to it and are coverage ``skipped`` entries, never "scanned, clean".
"""

from __future__ import annotations

import posixpath
from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import SRC
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import as_int
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import load_json
from backend.analysis.engine.adapters.base import rel_path
from backend.analysis.engine.adapters.base import report_structure
from backend.analysis.engine.adapters.base import stdout_of
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolParse

ALLOWLIST_FILTER = "detect_secrets.filters.allowlist.is_line_allowlisted"
SWAGGER_FILTER = "detect_secrets.filters.heuristic.is_swagger_file"
# detect-secrets 1.5.0 filters/heuristic.py: IGNORED_FILE_EXTENSIONS (is_non_text_file, case-sensitive
# os.path.splitext suffix) and the base names of is_lock_file.
NON_TEXT_EXTENSIONS = frozenset(
    {
        ".7z", ".bin", ".bmp", ".bz2", ".class", ".css", ".dmg", ".doc", ".eot", ".exe", ".gif", ".gz", ".ico",
        ".iml", ".ipr", ".iws", ".jar", ".jpg", ".jpeg", ".lock", ".map", ".mo", ".pdf", ".png", ".prefs", ".psd",
        ".rar", ".realm", ".s7z", ".sum", ".svg", ".tar", ".tif", ".tiff", ".ttf", ".webp", ".woff", ".xls",
        ".xlsx", ".zip",
    },
)  # fmt: skip
LOCK_FILE_NAMES = frozenset(
    {
        "Brewfile.lock.json", "Cartfile.resolved", "composer.lock", "Gemfile.lock", "Package.resolved",
        "package-lock.json", "Podfile.lock", "yarn.lock", "Pipfile.lock", "poetry.lock", "Cargo.lock",
        "packages.lock.json",
    },
)  # fmt: skip


@register
class DetectSecretsAdapter(ToolAdapter):
    slug = "detect-secrets"

    def unsupported(self, file: SampleFile) -> str | None:
        name = posixpath.basename(file.path)
        if name in LOCK_FILE_NAMES:
            return "detect-secrets never scans lock files (is_lock_file)"
        suffix = posixpath.splitext(file.path)[1]
        if suffix in NON_TEXT_EXTENSIONS:
            return f"detect-secrets never scans {suffix} files (is_non_text_file)"
        return None

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            "env",
            "-C",
            SRC,
            f"{BIN}/detect-secrets",
            "scan",
            "--all-files",
            "--no-verify",
            "--disable-filter",
            ALLOWLIST_FILTER,
            "--disable-filter",
            SWAGGER_FILTER,
            "--base64-limit",
            str(cfg["base64_limit"]),
            "--hex-limit",
            str(cfg["hex_limit"]),
            "--",
            *(f.path for f in self.analysable(files)),
        ]
        return self.make_invocation(argv)

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        check_exit(outcome, {0}, "detect-secrets")
        report = load_json(stdout_of(outcome, "detect-secrets"), what="detect-secrets output", expect=dict)
        findings = []
        with report_structure("detect-secrets output"):
            results = report["results"]
            for reported_path, secrets in sorted(results.items()):
                path = rel_path(reported_path)
                for secret in secrets:
                    kind = str(secret["type"])
                    findings.append(
                        RawFinding(
                            rule_id=kind,
                            message=f"{kind} detected",
                            file_path=path,
                            line=as_int(secret.get("line_number")),
                            title=kind,
                            extra={
                                "hashed_secret": secret.get("hashed_secret") or "",
                                "is_verified": bool(secret.get("is_verified")),
                            },
                        ),
                    )
            metrics = {
                "version": report.get("version") or "",
                "plugins_used": [p["name"] for p in report.get("plugins_used") or []],
                "filters_used": [f["path"] for f in report.get("filters_used") or []],
            }
        cov = coverage(files, skipped=self.unsupported_coverage(files))
        return build_parse("detect-secrets", files, findings, cov, metrics=metrics)
