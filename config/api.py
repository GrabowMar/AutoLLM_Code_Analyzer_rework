from django.contrib.admin.views.decorators import staff_member_required
from ninja import NinjaAPI
from ninja.security import SessionAuth

from backend.common.exceptions import APIError
from backend.tokens.auth import TokenAuth

api = NinjaAPI(
    urls_namespace="api",
    auth=[TokenAuth(), SessionAuth()],
    docs_decorator=staff_member_required,
)


# Error-handling convention: views raise ninja's HttpError (or use
# get_object_or_404); services raise backend.common.exceptions.* so they stay
# free of ninja imports — this handler maps those to HTTP responses. Typed
# {4xx: schema} tuple returns are reserved for structured validation payloads
# (e.g. automation's DslValidationResult).
@api.exception_handler(APIError)
def handle_api_error(request, exc: APIError):
    return api.create_response(
        request,
        {"detail": exc.message},
        status=exc.status_code,
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
