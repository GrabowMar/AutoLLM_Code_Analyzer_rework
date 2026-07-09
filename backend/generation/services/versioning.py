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
