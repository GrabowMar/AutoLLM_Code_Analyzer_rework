"""Evaluation API (spec section 9.9); WP-09 adds the endpoints.

Mounted by ``backend.generation.api`` without a prefix, so routes declare their full path under
``/api/generation/`` (as in the spec's tables). Static paths are registered before parameterised ones.
"""

from ninja import Router

router = Router(tags=["generation: eval"])
