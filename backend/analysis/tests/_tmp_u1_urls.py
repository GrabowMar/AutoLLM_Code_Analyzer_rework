"""TEMPORARY (see _tmp_u1_settings)."""

from django.urls import path
from ninja import NinjaAPI
from ninja.security import SessionAuth

from backend.common.exceptions import APIError

api = NinjaAPI(urls_namespace="api", auth=[SessionAuth()])


@api.exception_handler(APIError)
def handle_api_error(request, exc: APIError):
    return api.create_response(request, {"detail": exc.message}, status=exc.status_code)


api.add_router("/analysis/", "backend.analysis.api.views.router")
api.add_router("/analyzers/", "backend.analysis.api.views.analyzers_router")

urlpatterns = [path("api/", api.urls)]
