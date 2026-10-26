from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include
from django.urls import path
from django.views import defaults as default_views

from backend.integrations.mcp.views import mcp_endpoint
from backend.integrations.mcp.views import mcp_endpoint_url_token
from backend.realtime.api.views import sse_stream
from backend.runtime.proxy import app_proxy
from backend.runtime.proxy import app_tls_check

from .api import api

urlpatterns = [
    # Django Admin, use {% url 'admin:index' %}
    path(settings.ADMIN_URL, admin.site.urls),
    # Sample-app reverse proxy (path routing): /apps/<name>/<path>
    path("apps/<slug:name>/", app_proxy, {"path": ""}),
    path("apps/<slug:name>/<path:path>", app_proxy),
    # User management
    path("users/", include("backend.users.urls", namespace="users")),
    path("accounts/", include("allauth.urls")),
    path("_allauth/", include("allauth.headless.urls")),
    # Your stuff: custom urls includes go here
    # ...
    # Media files
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]
if settings.DEBUG:
    # Static file serving when using Gunicorn + Uvicorn for local web socket development
    urlpatterns += staticfiles_urlpatterns()


# API URLS
urlpatterns += [
    # Relay protocol v1 (plain async Django views, spec §E.1); before "api/".
    path("api/relay/v1/", include("backend.inference.relay.urls")),
    # MCP server (Streamable HTTP, plain Django view); before "api/".
    path("api/mcp", mcp_endpoint),
    path("api/mcp/", mcp_endpoint),
    path("api/mcp/t/<str:token>", mcp_endpoint_url_token),
    # Edge on-demand TLS ask hook for app subdomains (plain Django view); before "api/".
    path("api/runtime/app-tls-check/", app_tls_check),
    # API base url
    path("api/", api.urls),
    # Realtime SSE stream (plain Django view — Ninja doesn't support streaming)
    path("api/realtime/stream", sse_stream),
]

if settings.DEBUG:
    # This allows the error pages to be debugged during development, just visit
    # these url in browser to see how these error pages look like.
    urlpatterns += [
        path(
            "400/",
            default_views.bad_request,
            kwargs={"exception": Exception("Bad Request!")},
        ),
        path(
            "403/",
            default_views.permission_denied,
            kwargs={"exception": Exception("Permission Denied")},
        ),
        path(
            "404/",
            default_views.page_not_found,
            kwargs={"exception": Exception("Page not Found")},
        ),
        path("500/", default_views.server_error),
    ]
    if "debug_toolbar" in settings.INSTALLED_APPS:
        import debug_toolbar

        urlpatterns = [
            path("__debug__/", include(debug_toolbar.urls)),
            *urlpatterns,
        ]
