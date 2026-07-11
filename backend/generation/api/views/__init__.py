"""Django Ninja API views for generation.

Submodules register endpoints on a shared :data:`router`. Import order matters:
template CRUD first, then static job-creation routes (``/jobs/custom/`` etc.),
then dynamic ``/jobs/{job_id}/`` routes — Django Ninja matches in registration
order.
"""

from backend.generation.api.views import custom  # noqa: F401  (register routes)
from backend.generation.api.views import experiments  # noqa: F401  (register routes)
from backend.generation.api.views import jobs  # noqa: F401  (register routes)
from backend.generation.api.views import profiles  # noqa: F401  (register routes)
from backend.generation.api.views import templates  # noqa: F401  (register routes)
from backend.generation.api.views._router import router

__all__ = ["router"]
