"""Image build, pinning and digest resolution (spec section 6.3).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-08 implements it.
"""

from __future__ import annotations

_PENDING = "WP-08: not implemented yet"


def resolve_digest(image: str) -> str:
    """The pinned digest of ``image`` (built when missing; issue ``image_built_after_plan``)."""
    raise NotImplementedError(_PENDING)
