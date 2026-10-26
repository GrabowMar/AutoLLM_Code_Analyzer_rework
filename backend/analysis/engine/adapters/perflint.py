"""perflint adapter: Python performance anti-patterns (a pylint plugin, json2 report).

perflint 0.8.1 is a pylint checker plugin that requires pylint < 4, so it runs
the pylint 3.3 of its own venv (``venvs/perflint``) with its own baked rcfile
(``config/perflint.pylintrc``: every message off, perflint's four checkers on;
the adapter switches R8203 loop-try-except-usage off, perflint documents it as
applying to Python < 3.11 only). It is invoked through pylint, not perflint's CLI, so the
rcfile is explicit and no app configuration is read.

Everything else is the pylint adapter's contract (exit-status bit mask, bit 32
= usage error; E0001/F-type messages -> unparseable files; inline
``# pylint: disable=<perflint message>`` recovered through I0020; whole-file
pragmas neutralized in staged copies; I0013 -> skipped coverage).
"""

from __future__ import annotations

from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import CONFIG
from backend.analysis.engine.adapters.base import VENVS
from backend.analysis.engine.adapters.pylint import PylintAdapter

# perflint 0.8.1 messages (perflint/*_checker.py), symbol -> message id.
MESSAGES = {
    "unnecessary-list-cast": "W8101",
    "incorrect-dictionary-iterator": "W8102",
    "loop-invariant-statement": "W8201",
    "loop-global-usage": "W8202",
    "loop-try-except-usage": "R8203",
    "memoryview-over-bytes": "W8204",
    "dotted-import-in-loop": "W8205",
    "use-tuple-over-list": "W8301",
    "use-list-comprehension": "W8401",
    "use-list-copy": "W8402",
    "use-dict-comprehension": "W8403",
}


@register
class PerflintAdapter(PylintAdapter):
    slug = "perflint"
    executable = f"{VENVS}/perflint/bin/pylint"
    rcfile = f"{CONFIG}/perflint.pylintrc"
    report = "perflint.json"

    def message_ids(self) -> dict[str, str]:
        return dict(MESSAGES)

    def option_args(self, cfg: dict[str, Any]) -> list[str]:
        # Enabling a checker enables all its messages; this one documents itself as Python < 3.11 only.
        return ["--disable=loop-try-except-usage"]
