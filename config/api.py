from django.contrib.admin.views.decorators import staff_member_required
from ninja import NinjaAPI
from ninja.security import SessionAuth

from llm_lab.tokens.auth import TokenAuth

api = NinjaAPI(
    urls_namespace="api",
    auth=[TokenAuth(), SessionAuth()],
    docs_decorator=staff_member_required,
)

api.add_router("/users/", "llm_lab.users.api.views.router")
api.add_router("/tokens/", "llm_lab.tokens.api.views.router")
api.add_router("/credentials/", "llm_lab.credentials.api.views.router")
api.add_router("/models/", "llm_lab.llm_models.api.views.router")
api.add_router("/generation/", "llm_lab.generation.api.views.router")
api.add_router("/analysis/", "llm_lab.analysis.api.views.router")
api.add_router("/analyzers/", "llm_lab.analysis.api.views.analyzers_router")
api.add_router("/statistics/", "llm_lab.statistics.api.views.router")
api.add_router("/rankings/", "llm_lab.rankings.api.views.router")
api.add_router("/reports/", "llm_lab.reports.api.views.router")
api.add_router("/runtime/", "llm_lab.runtime.api.views.router")
api.add_router("/export/", "llm_lab.export.api.views.router")
api.add_router("/docs/", "llm_lab.docs.api.views.router")
api.add_router("/system/", "llm_lab.system.api.views.router")
api.add_router("/automation/", "llm_lab.automation.api.views.router")
