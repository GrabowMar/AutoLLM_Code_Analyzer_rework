"""Analysis API views.

Submodules register endpoints on the shared routers. Static path segments must
be registered before dynamic ``{...}`` ones, so import order matters: tools and
workspace (static) before installed-tool detail routes, and run-stats before
``runs/{run_id}/``.
"""

from backend.analysis.api.views import installed  # noqa: F401  (register routes)
from backend.analysis.api.views import runs  # noqa: F401  (register routes)
from backend.analysis.api.views import tools  # noqa: F401  (register routes)
from backend.analysis.api.views import workspace  # noqa: F401  (register routes)
from backend.analysis.api.views._router import analyzers_router
from backend.analysis.api.views._router import router

__all__ = ["analyzers_router", "router"]
