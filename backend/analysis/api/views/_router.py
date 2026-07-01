"""Shared Ninja routers for the analysis app.

Two routers are exposed:

* ``analyzers_router`` (mounted at ``/analyzers/``) — the tool catalog/shop,
  the per-user workspace, and installed-tool management.
* ``router`` (mounted at ``/analysis/``) — analysis runs and their findings.

Submodules import these routers and register endpoints onto them; route order
within a router follows the import order in ``views/__init__.py``.
"""

from ninja import Router

analyzers_router = Router(tags=["analyzers"])
router = Router(tags=["analysis"])
