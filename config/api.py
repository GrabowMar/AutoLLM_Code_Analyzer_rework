from django.contrib.admin.views.decorators import staff_member_required
from ninja import NinjaAPI
from ninja.security import SessionAuth

from backend.tokens.auth import TokenAuth

api = NinjaAPI(
    urls_namespace="api",
    auth=[TokenAuth(), SessionAuth()],
    docs_decorator=staff_member_required,
)

api.add_router("/users/", "backend.users.api.views.router")
api.add_router("/tokens/", "backend.tokens.api.views.router")
api.add_router("/credentials/", "backend.credentials.api.views.router")
api.add_router("/models/", "backend.llm_models.api.views.router")
api.add_router("/generation/", "backend.generation.api.views.router")
api.add_router("/analysis/", "backend.analysis.api.views.router")
api.add_router("/analyzers/", "backend.analysis.api.views.analyzers_router")
api.add_router("/statistics/", "backend.statistics.api.views.router")
api.add_router("/rankings/", "backend.rankings.api.views.router")
api.add_router("/reports/", "backend.reports.api.views.router")
api.add_router("/runtime/", "backend.runtime.api.views.router")
api.add_router("/export/", "backend.export.api.views.router")
api.add_router("/docs/", "backend.docs.api.views.router")
api.add_router("/system/", "backend.system.api.views.router")
api.add_router("/automation/", "backend.automation.api.views.router")
