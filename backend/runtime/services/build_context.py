"""Shim between the BuildKit sample builds and the v3 build seam (spec sections 2.2 and 6.10).

``runtime/services/sample_builds.py`` imports ``BuildContextReport``, ``build_identity`` and
``prepare_build_dir`` from here. Created by WP-01; owned by WP-05 after U1.
"""

from backend.generation.build.context import BuildContextReport
from backend.generation.build.context import build_identity
from backend.generation.build.context import prepare_build_dir

__all__ = ["BuildContextReport", "build_identity", "prepare_build_dir"]
