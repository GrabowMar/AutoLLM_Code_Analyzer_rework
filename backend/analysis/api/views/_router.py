"""Shared Ninja router for the analysis app (mounted at ``/analysis/``).

Submodules import ``router`` and register endpoints onto it; route order
follows the import order in ``views/__init__.py`` (static path segments
before dynamic ``{...}`` ones).
"""

from ninja import Router

from backend.analysis.api.common import reject_unstorable_query

router = Router(tags=["analysis"])
# Before parameter validation: NUL in a query string is a 422 for every endpoint.
router.add_decorator(reject_unstorable_query, mode="view")
