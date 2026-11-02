"""Ninja views of the human-evaluation module.

Every views module registers its operations on one of the two shared routers in
``_routers.py``; importing them here registers the routes. ``config/api.py``
mounts :data:`portal_router` at ``/api/human-eval/portal/`` (first) and
:data:`router` at ``/api/human-eval/``.
"""

from backend.human_eval.api.views import hosting  # noqa: F401  (register routes)
from backend.human_eval.api.views import participants  # noqa: F401  (register routes)
from backend.human_eval.api.views import pool  # noqa: F401  (register routes)
from backend.human_eval.api.views import portal_hosting  # noqa: F401  (register routes)
from backend.human_eval.api.views import portal_session  # noqa: F401  (register routes)
from backend.human_eval.api.views import portal_work  # noqa: F401  (register routes)
from backend.human_eval.api.views import results  # noqa: F401  (register routes)
from backend.human_eval.api.views import tasks  # noqa: F401  (register routes)
from backend.human_eval.api.views._routers import portal_router
from backend.human_eval.api.views._routers import router

__all__ = ["portal_router", "router"]
