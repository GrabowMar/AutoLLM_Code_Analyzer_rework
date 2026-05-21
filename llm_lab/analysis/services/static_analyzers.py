"""Backwards-compatible shim — re-exports from the static analyzers package.

`subprocess` is re-exported because some tests patch
``llm_lab.analysis.services.static_analyzers.subprocess.run`` to intercept
analyzer subprocess calls; since ``subprocess`` is a singleton module,
exposing it here makes such patches effective for the new submodules too.
"""

import subprocess  # noqa: F401

from llm_lab.analysis.services.static import *  # noqa: F403
