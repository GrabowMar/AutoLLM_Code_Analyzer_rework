"""Slots API (spec section 9.6); WP-12 adds the endpoints.

Mounted by ``backend.generation.api`` without a prefix, so routes declare their full path under
``/api/generation/`` (as in the spec's tables). Static paths are registered before parameterised ones.
"""

from ninja import Router

router = Router(tags=["generation: slots"])
