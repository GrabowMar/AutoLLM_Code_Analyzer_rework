"""A fake Semgrep runner for pattern tests (WP-13's area fake; no docker, no semgrep binary).

``FakeSemgrep`` implements :class:`backend.analysis.services.rulepack.SemgrepRunner`. It emulates the
subset of Semgrep that regex-based rules need: every rule with a top-level ``pattern-regex`` is applied to
each file in its ``languages``' extensions (all files when the rule says ``generic`` or the language is
unknown), and matches are reported in Semgrep's JSON shape (``check_id``, ``path`` under ``src/``,
``start``/``end`` line and column, ``extra.severity``/``message``/``metadata``). Scripted behaviour:

- ``errors``: extra ``errors[]`` entries (paths relative to the files, like Semgrep's own report);
- ``exit_code``: the exit code to report (default 0 -- Semgrep exits 0 even with parse errors);
- ``extra_results``: canned results added verbatim (for rules the fake cannot interpret, e.g. taint);
- ``unscanned``: paths left out of ``paths.scanned``;
- ``fail``: raise :class:`RulepackError` (the run could not execute).

``calls`` records ``(files, config_yaml)`` of every invocation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

import yaml

from backend.analysis.services.rulepack import SRC_DIR
from backend.analysis.services.rulepack import RawOutput
from backend.analysis.services.rulepack import RulepackError

if TYPE_CHECKING:
    from collections.abc import Mapping

_EXTENSIONS = {
    "python": (".py",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte"),
    "js": (".js", ".jsx", ".mjs", ".cjs"),
    "typescript": (".ts", ".tsx", ".vue", ".svelte"),
    "ts": (".ts", ".tsx"),
}
FAKE_VERSION = "1.99.0-fake"


def _line_col(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    col = offset - (text.rfind("\n", 0, offset) + 1) + 1
    return line, col


@dataclass
class FakeSemgrep:
    errors: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int = 0
    extra_results: list[dict[str, Any]] = field(default_factory=list)
    unscanned: list[str] = field(default_factory=list)
    fail: str = ""
    calls: list[tuple[dict[str, str], str]] = field(default_factory=list)
    name: str = "fake"

    def __call__(self, files: Mapping[str, str], config_yaml: str, *, timeout_s: int) -> RawOutput:
        self.calls.append((dict(files), config_yaml))
        if self.fail:
            raise RulepackError(self.fail)
        rules = (yaml.safe_load(config_yaml) or {}).get("rules") or []
        results: list[dict[str, Any]] = []
        for rule in rules:
            regex = rule.get("pattern-regex")
            if not regex:
                continue
            languages = [str(lang).lower() for lang in rule.get("languages") or []]
            compiled = re.compile(regex, re.MULTILINE)
            for path, text in files.items():
                if not _applies(path, languages):
                    continue
                for match in compiled.finditer(text):
                    sl, sc = _line_col(text, match.start())
                    el, ec = _line_col(text, max(match.start(), match.end()))
                    results.append(
                        {
                            "check_id": rule.get("id", "rule"),
                            "path": f"{SRC_DIR}/{path}",
                            "start": {"line": sl, "col": sc, "offset": match.start()},
                            "end": {"line": el, "col": ec, "offset": match.end()},
                            "extra": {
                                "message": rule.get("message", ""),
                                "severity": rule.get("severity", "WARNING"),
                                "metadata": rule.get("metadata") or {},
                                "lines": match.group(0),
                            },
                        },
                    )
        results.extend(self.extra_results)
        errors = [dict(err, path=f"{SRC_DIR}/{err['path']}") if err.get("path") else dict(err) for err in self.errors]
        report = {
            "version": FAKE_VERSION,
            "results": results,
            "errors": errors,
            "paths": {"scanned": [f"{SRC_DIR}/{p}" for p in files if p not in self.unscanned]},
        }
        return RawOutput(exit_code=self.exit_code, stdout=json.dumps(report), version=FAKE_VERSION)


def _applies(path: str, languages: list[str]) -> bool:
    if not languages or "generic" in languages or "regex" in languages:
        return True
    suffixes = [ext for lang in languages for ext in _EXTENSIONS.get(lang, ())]
    if not suffixes:
        return True
    return path.endswith(tuple(suffixes))


def install(monkeypatch: Any, fake: FakeSemgrep | None = None) -> FakeSemgrep:
    """Make every rule pack run in this test use ``fake`` (patched at ``rulepack.default_runner``)."""
    from backend.analysis.services import rulepack

    fake = fake or FakeSemgrep()
    monkeypatch.setattr(rulepack, "default_runner", lambda user=None: fake)
    return fake
