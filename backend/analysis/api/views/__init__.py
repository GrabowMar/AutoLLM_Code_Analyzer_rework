"""Analysis API views (v2), mounted at ``/api/analysis/``.

Submodules register endpoints on ``router``; Django Ninja matches routes in
registration order, and a matching path without the request's method answers
405 instead of falling through, so static path segments are registered before
dynamic ones (``/runs/preview/`` before ``/runs/{uuid:run_id}/``, the
``/profiles/defaults/`` and ``/profiles/validate/`` routes before
``/profiles/{uuid:profile_id}/``). Ids are typed with the ``uuid`` path
converter, so a malformed id is a 404, never a 500.
"""

from backend.analysis.api.views import profiles  # noqa: F401  (register routes)
from backend.analysis.api.views import runs  # noqa: F401  (register routes)
from backend.analysis.api.views import studies  # noqa: F401  (register routes)
from backend.analysis.api.views import targets  # noqa: F401  (register routes)
from backend.analysis.api.views import tools  # noqa: F401  (tools, image, self-test, reviewer options)
from backend.analysis.api.views._router import router

__all__ = ["router"]
