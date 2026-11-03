"""Loaders for the image scripts (``images/eval-tester/tester.py``, ``images/adherence-checker/checker.py``).

The image directories carry hyphens, so they are not importable packages. The backend loads the same files the
images run: the in-process runtime (conformance kit, tests) and ``kind_validated`` read the tester's versions
from the exact code that ships in the image.
"""

from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Final

__all__ = ["CHECKER_PATH", "TESTER_PATH", "checker_module", "tester_module"]

_IMAGES: Final = Path(__file__).resolve().parent.parent / "images"
TESTER_PATH: Final = _IMAGES / "eval-tester" / "tester.py"
CHECKER_PATH: Final = _IMAGES / "adherence-checker" / "checker.py"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - the files ship with the package
        msg = f"cannot load {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def tester_module() -> ModuleType:
    """``images/eval-tester/tester.py`` as a module (``run_plan``, ``VERSION``, ``PROBE_RULES_VERSION``)."""
    return _load("llmlab_eval_tester", TESTER_PATH)


@lru_cache(maxsize=1)
def checker_module() -> ModuleType:
    """``images/adherence-checker/checker.py`` as a module (``analyse``, ``CHECKER_VERSION``)."""
    return _load("llmlab_adherence_checker", CHECKER_PATH)
