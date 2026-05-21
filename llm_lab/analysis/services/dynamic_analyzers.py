"""Backwards-compatible shim — re-exports from the dynamic analyzers package."""

import subprocess  # noqa: F401

from llm_lab.analysis.services.dynamic import *  # noqa: F403
from llm_lab.analysis.services.dynamic._common import _slug  # noqa: F401
from llm_lab.analysis.services.dynamic.port_scanner import _port_suggestion  # noqa: F401
from llm_lab.analysis.services.dynamic.zap import _zap_confidence  # noqa: F401
