"""Content-hash helper for versioned seed data (blocks, bundles, requirements).

The seeder never overwrites a version's content: when a YAML/JSON source
changes, it creates a new version instead. A job that captured version N in
its ``resolved_bundle`` snapshot keeps resolving to exactly what it used,
even after the source file moves on to version N+1.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def content_hash(payload: dict[str, Any]) -> str:
    """Stable hash of the fields that define a version's rendering behavior.

    Callers pass only the semantic fields (content, refs, requirements —
    not cosmetic ones like name/description) so cosmetic edits don't force
    a version bump.
    """
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _canonical(value: Any) -> Any:
    """JSON-ready copy where integral floats become ints (``0`` and ``0.0`` hash alike)."""
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def canonical_json(value: Any) -> str:
    """Compact, key-sorted JSON of *value* for content addressing (run presets, method keys)."""
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def short_hash(value: Any, length: int = 8) -> str:
    """First *length* hex digits of the sha256 of :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]
