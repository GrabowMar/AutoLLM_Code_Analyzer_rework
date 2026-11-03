"""Token-stream normalizers per language (``normalized_sha = text_sha(normalize(...))``).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-13 implements it.
"""

from __future__ import annotations

_PENDING = "WP-13: not implemented yet"


def normalize(code: str, language: str, normalizer_version: int) -> str:
    """Renamed locals, comments, whitespace and import order do not change the output."""
    raise NotImplementedError(_PENDING)
