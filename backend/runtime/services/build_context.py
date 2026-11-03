"""Shim between the BuildKit sample builds and the v3 build seam (spec sections 2.2 and 6.10).

``runtime/services/sample_builds.py`` imports ``BuildContextReport``, ``build_identity`` and
``prepare_build_dir`` from here. ``BaseNotAllowedError`` (a free-choice Dockerfile outside the allowlist:
outcome ``build_fail``, reason ``base_not_allowed``), ``AllowedBasesChangedError`` (infra) and
``VariantNotAssembledError`` propagate out of ``prepare_build_dir`` to the caller (the eval suite).
``BuildContextReport.build_context_args()`` must be passed to ``docker buildx build`` (free-choice base
pins), and ``BuildContextReport.locks_incomplete`` marks a lock the lock step must replace.
Created by WP-01; owned by WP-05 after U1.
"""

from backend.generation.build.context import ALLOWED_BASES_CHANGED
from backend.generation.build.context import BASE_NOT_ALLOWED
from backend.generation.build.context import LOCK_INCOMPLETE
from backend.generation.build.context import AllowedBasesChangedError
from backend.generation.build.context import BaseNotAllowedError
from backend.generation.build.context import BuildContextReport
from backend.generation.build.context import VariantNotAssembledError
from backend.generation.build.context import build_identity
from backend.generation.build.context import prepare_build_dir

__all__ = [
    "ALLOWED_BASES_CHANGED",
    "BASE_NOT_ALLOWED",
    "LOCK_INCOMPLETE",
    "AllowedBasesChangedError",
    "BaseNotAllowedError",
    "BuildContextReport",
    "VariantNotAssembledError",
    "build_identity",
    "prepare_build_dir",
]
