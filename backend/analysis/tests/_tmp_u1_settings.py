"""TEMPORARY test settings for the analysis suite while U1 is in progress. DELETE at the U1 gate.

Loads ``backend.statistics.services`` without its ``__init__`` (still importing v2 models in the
statistics WIP) and mounts only the analysis routers (``config/api.py`` still mounts routers of
not-yet-re-pointed apps), so the analysis suite runs before the other consumers are ported::

    .wt-test.sh u1an -- pytest -q --ds=backend.analysis.tests._tmp_u1_settings backend/analysis
"""

import sys
import types
from pathlib import Path

import backend.statistics as _stats

_pkg = types.ModuleType("backend.statistics.services")
_pkg.__path__ = [str(Path(_stats.__file__).parent / "services")]
sys.modules["backend.statistics.services"] = _pkg

from backend.generation.tests.wp01_foundation.settings_u1 import *  # noqa: E402,F403

ROOT_URLCONF = "backend.analysis.tests._tmp_u1_urls"
