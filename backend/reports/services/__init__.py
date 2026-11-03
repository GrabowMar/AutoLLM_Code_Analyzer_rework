"""Report services.

``loc`` is imported eagerly (no model imports; other apps use it as the LOC and test-code classifier).
The generators load the analysis and statistics services, so ``GENERATORS``, ``create_and_dispatch`` and
``list_reports`` are resolved lazily on first access.
"""

from __future__ import annotations

from typing import Any

from .loc import loc_by_run
from .loc import loc_for_runs
from .loc import loc_from_run
from .loc import source_loc

__all__ = [
    "GENERATORS",
    "create_and_dispatch",
    "list_reports",
    "loc_by_run",
    "loc_for_runs",
    "loc_from_run",
    "source_loc",
]

_LAZY = {
    "GENERATORS": ("backend.reports.services.generators", "GENERATORS"),
    "create_and_dispatch": ("backend.reports.services.report_service", "create_and_dispatch"),
    "list_reports": ("backend.reports.services.report_service", "list_reports"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        import importlib

        module, attr = _LAZY[name]
        return getattr(importlib.import_module(module), attr)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
