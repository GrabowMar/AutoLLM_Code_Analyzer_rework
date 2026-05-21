"""Parameter resolution for pipeline step configs.

Supports mustache-style references: ``{{steps.<step_name>.output.<key>}}``.
Top-level run params are also accessible via ``{{params.<key>}}``.
"""

from __future__ import annotations

import re
from typing import Any

_REF_RE = re.compile(r"\{\{([^}]+)\}\}")


def _resolve_ref(ref: str, context: dict[str, Any]) -> Any:
    """Resolve a single dotted path against *context*.

    Returns the ref string unchanged if not found.
    """
    parts = ref.strip().split(".")
    node: Any = context
    for part in parts:
        if isinstance(node, dict):
            node = node.get(part)
        else:
            return f"{{{{{ref}}}}}"
        if node is None:
            return f"{{{{{ref}}}}}"
    return node


def _resolve_value(value: Any, context: dict[str, Any]) -> Any:
    """Recursively resolve template references in a value."""
    if isinstance(value, str):
        matches = _REF_RE.findall(value)
        if not matches:
            return value
        # If the entire string is one reference, return the raw resolved value
        # (so we can pass non-string types like lists/dicts through).
        single_ref = f"{{{{{matches[0]}}}}}"
        if len(matches) == 1 and value.strip() == single_ref:
            return _resolve_ref(matches[0], context)

        # Otherwise do string substitution.
        def _sub(m: re.Match) -> str:
            resolved = _resolve_ref(m.group(1), context)
            return str(resolved) if resolved is not None else m.group(0)

        return _REF_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_value(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, context) for item in value]
    return value


def resolve_params(
    config: dict[str, Any],
    prior_outputs: dict[str, dict[str, Any]],
    run_params: dict[str, Any],
) -> dict[str, Any]:
    """Return a copy of *config* with all template references resolved.

    Args:
        config: The step's raw config dict (may contain ``{{...}}`` refs).
        prior_outputs: Map of step_name → output dict from completed step runs.
        run_params: The top-level run params dict.

    Returns:
        A new dict with all resolvable references substituted.
    """
    context: dict[str, Any] = {
        "steps": prior_outputs,
        "params": run_params,
    }
    return _resolve_value(config, context)  # type: ignore[return-value]
