"""Router assembly for ``/api/generation/`` (spec section 9).

The area routers are attached in the fixed order of section 9 (library, prompts, harnesses, budget,
studies, runs, slots, patterns, eval), each without a prefix: every area declares its full paths, as in
the spec's tables (``/library/{kind}/``, ``/config/``, ``/runs/{id}/``). Django Ninja matches routes in
registration order, so the order also keeps static paths ahead of parameterised ones across areas.

The retained routers (``telemetry``, ``trajectory``, ``variability``, ``job_insights``) are appended after
the areas by WP-02a once they are re-pointed and moved from ``api/views/`` to ``api/``. The v2 view
modules are deliberately not imported: they read the deleted v2 models. ``config/api.py`` mounts
``backend.generation.api.router``.
"""

from ninja import Router

from backend.generation.budget import api as budget_api
from backend.generation.evaluation import api as evaluation_api
from backend.generation.harnesses import api as harnesses_api
from backend.generation.library import api as library_api
from backend.generation.patterns import api as patterns_api
from backend.generation.prompts import api as prompts_api
from backend.generation.runs import api as runs_api
from backend.generation.slot import api as slot_api
from backend.generation.study import api as study_api

__all__ = ["AREA_ROUTERS", "router"]

AREA_ROUTERS: tuple[tuple[str, Router], ...] = (
    ("library", library_api.router),
    ("prompts", prompts_api.router),
    ("harnesses", harnesses_api.router),
    ("budget", budget_api.router),
    ("studies", study_api.router),
    ("runs", runs_api.router),
    ("slots", slot_api.router),
    ("patterns", patterns_api.router),
    ("eval", evaluation_api.router),
)

router = Router(tags=["generation"])
for _area, _area_router in AREA_ROUTERS:
    router.add_router("", _area_router)
