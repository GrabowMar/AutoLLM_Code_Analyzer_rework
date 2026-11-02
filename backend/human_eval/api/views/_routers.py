"""The two shared ninja routers of the human-evaluation API.

Every researcher views module registers on :data:`router` (mounted at
``/api/human-eval/``, global TokenAuth + SessionAuth, owner-scoped lookups via
``services.access.researcher_task``). Every portal views module registers on
:data:`portal_router` (mounted first, at ``/api/human-eval/portal/``) whose auth
is :class:`~backend.human_eval.auth.PortalAuth`; public portal operations
(join, resume, logout, me) pass ``auth=None`` and enforce CSRF themselves with
``auth.enforce_csrf``.

Paths are relative to the mounts, end with ``/`` and use the ``{uuid:...}``
converter for every UUID parameter. Within one router, register literal routes
before parameterised siblings and never register two colliding patterns.
"""

from __future__ import annotations

from ninja import Router

from backend.human_eval.auth import PortalAuth

router = Router(tags=["human-eval"])
portal_router = Router(tags=["human-eval-portal"], auth=PortalAuth())
