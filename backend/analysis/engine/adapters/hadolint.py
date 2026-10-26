"""hadolint adapter (JSON on stdout).

With ``--no-fail`` hadolint exits 0 whatever it finds; any other status is a
failure. Rule DL1000 means the Dockerfile could not be parsed: coverage, not a
finding. ``--disable-ignore-pragma`` and the explicit ``--config`` keep an
app's ``# hadolint ignore=`` comments and .hadolint.yaml from applying.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import CONFIG
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import as_int
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import load_json
from backend.analysis.engine.adapters.base import rel_path
from backend.analysis.engine.adapters.base import report_structure
from backend.analysis.engine.adapters.base import src_path
from backend.analysis.engine.adapters.base import stdout_of
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolParse

PARSE_FAILURE_RULES = frozenset({"DL1000"})


@register
class HadolintAdapter(ToolAdapter):
    slug = "hadolint"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            f"{BIN}/hadolint",
            "--config",
            f"{CONFIG}/hadolint.yaml",
            "--format",
            "json",
            "--no-fail",
            "--no-color",
            "--disable-ignore-pragma",
        ]
        for rule in filter(None, (r.strip() for r in cfg["ignore_rules"].split(","))):
            argv += ["--ignore", rule]
        return self.make_invocation([*argv, *(src_path(f) for f in files)])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        check_exit(outcome, {0}, "hadolint")
        items = load_json(stdout_of(outcome, "hadolint"), what="hadolint output", expect=list)
        findings: list[RawFinding] = []
        unparseable: list[dict[str, Any]] = []
        with report_structure("hadolint output"):
            for item in items:
                path = rel_path(item["file"])
                rule = str(item["code"])
                if rule in PARSE_FAILURE_RULES:
                    unparseable.append({"path": path, "reason": f"line {item.get('line')}: {item['message']}"})
                    continue
                findings.append(
                    RawFinding(
                        rule_id=rule,
                        message=str(item["message"]),
                        file_path=path,
                        line=as_int(item.get("line")),
                        column=as_int(item.get("column")),
                        native_severity=str(item["level"]),
                        title=rule,
                    ),
                )
        return build_parse("hadolint", files, findings, coverage(files, unparseable=unparseable))
