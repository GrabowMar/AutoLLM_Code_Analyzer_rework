"""Tool adapters: how each catalog tool is invoked and its output interpreted.

One module per tool (``<slug>.py``, hyphens as underscores) defines a
:class:`~backend.analysis.engine.adapters.base.ToolAdapter` subclass and
registers it with :func:`register`. :func:`get_adapter` imports every module
of this package on first use, so adding a tool is: a spec in
``data/tools/<slug>.yaml`` plus an adapter module here.
"""

from __future__ import annotations

import importlib
import pkgutil
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.analysis.engine.adapters.base import ToolAdapter

ADAPTERS: dict[str, ToolAdapter] = {}

_loaded = False
_lock = threading.Lock()
_NOT_ADAPTERS = frozenset({"base"})


def register(adapter_cls: type[ToolAdapter]) -> type[ToolAdapter]:
    if not adapter_cls.slug:
        msg = f"{adapter_cls.__name__} has no slug"
        raise ValueError(msg)
    ADAPTERS[adapter_cls.slug] = adapter_cls()
    return adapter_cls


def get_adapter(slug: str) -> ToolAdapter:
    """Adapter for *slug*; ``KeyError`` if none is registered."""
    _load_all()
    return ADAPTERS[slug]


def registered_slugs() -> list[str]:
    _load_all()
    return sorted(ADAPTERS)


def _load_all() -> None:
    """Import every adapter module so it registers itself."""
    global _loaded  # noqa: PLW0603 - import-once flag
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        for module in pkgutil.iter_modules(__path__):
            if module.name in _NOT_ADAPTERS or module.name.startswith("_") or module.ispkg:
                continue
            importlib.import_module(f"{__name__}.{module.name}")
        _loaded = True
