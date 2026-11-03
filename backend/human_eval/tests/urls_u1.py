"""Test URLconf for the human-eval API while merge unit U1 is in progress. DELETE at the U1 gate.

``config.urls`` mounts every app's router; until the other U1 consumers (reports, statistics, ...)
stop importing removed v2 generation models, importing it fails. The human-eval tests then run
against this URLconf: the project's ``urlpatterns`` when they import, else an API with the same
auth, error handlers and mount points as ``config/api.py`` holding the human-eval routers (plus the
users and tokens routers when they import) and the project's non-API URLs (admin, users, allauth).
``conftest.py`` points ``ROOT_URLCONF`` here for every human-eval test.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib import admin
from django.urls import include
from django.urls import path

try:
    from config.urls import urlpatterns
except Exception:  # noqa: BLE001 - any import failure of the project URLconf (U1 only)
    from django.contrib.admin.views.decorators import staff_member_required
    from ninja import NinjaAPI
    from ninja.security import SessionAuth

    from backend.common.exceptions import APIError
    from backend.human_eval.services.errors import HumanEvalServiceError
    from backend.tokens.auth import TokenAuth

    api = NinjaAPI(urls_namespace="api", auth=[TokenAuth(), SessionAuth()], docs_decorator=staff_member_required)

    @api.exception_handler(APIError)
    def _api_error(request: Any, exc: APIError) -> Any:
        return api.create_response(request, {"detail": exc.message}, status=exc.status_code)

    @api.exception_handler(HumanEvalServiceError)
    def _human_eval_error(request: Any, exc: HumanEvalServiceError) -> Any:
        return api.create_response(request, exc.payload(), status=exc.status)

    for prefix, router in (
        ("/users/", "backend.users.api.views.router"),
        ("/tokens/", "backend.tokens.api.views.router"),
    ):
        try:
            api.add_router(prefix, router)
        except Exception:  # noqa: BLE001, S112 - optional routers; the human-eval ones below are required
            continue
    api.add_router("/human-eval/portal/", "backend.human_eval.api.views.portal_router")
    api.add_router("/human-eval/", "backend.human_eval.api.views.router")
    urlpatterns = [
        path(settings.ADMIN_URL, admin.site.urls),
        path("users/", include("backend.users.urls", namespace="users")),
        path("accounts/", include("allauth.urls")),
        path("_allauth/", include("allauth.headless.urls")),
        path("api/", api.urls),
    ]

__all__ = ["urlpatterns"]
