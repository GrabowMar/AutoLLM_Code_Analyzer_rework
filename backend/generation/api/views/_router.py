"""Shared Ninja router for all generation API submodules.

A single router is used so that route ordering across submodules is deterministic
(static path segments must be registered before dynamic ones — e.g.
``/jobs/custom/`` before ``/jobs/{job_id}/``). Submodules import this router and
register their endpoints onto it; ``views/__init__.py`` controls import order.
"""

from ninja import Router

router = Router(tags=["generation"])
