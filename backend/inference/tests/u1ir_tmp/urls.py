"""TEMPORARY URLconf: only the inference, relay and runtime routes (the full URLconf imports v2 consumers)."""

from django.urls import include
from django.urls import path
from ninja import NinjaAPI
from ninja.security import SessionAuth

from backend.common.exceptions import APIError
from backend.runtime.proxy import app_proxy
from backend.runtime.proxy import app_tls_check
from backend.tokens.auth import TokenAuth

api = NinjaAPI(urls_namespace="api", auth=[TokenAuth(), SessionAuth()])


@api.exception_handler(APIError)
def handle_api_error(request, exc: APIError):
    return api.create_response(request, {"detail": exc.message}, status=exc.status_code)


api.add_router("/runtime/", "backend.runtime.api.views.router")
api.add_router("/inference/", "backend.inference.api.views.router")

urlpatterns = [
    path("apps/<slug:name>/", app_proxy, {"path": ""}),
    path("apps/<slug:name>/<path:path>", app_proxy),
    path("api/relay/v1/", include("backend.inference.relay.urls")),
    path("api/runtime/app-tls-check/", app_tls_check),
    path("api/", api.urls),
]
