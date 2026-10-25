"""/api/relay/v1/ — relay protocol v1 (spec §E.2), mounted before ``api/`` in config/urls.py.

Foundation stub: every path answers 501 until Track B adds the async views
(``inference/relay/views.py``: hello, inventory, poll, requests/<rid>/result,
bye, relay.py, Dockerfile).
"""

from __future__ import annotations

from django.db import transaction
from django.http import JsonResponse
from django.urls import re_path
from django.views.decorators.csrf import csrf_exempt

app_name = "relay"


@csrf_exempt
@transaction.non_atomic_requests
def not_implemented(request, *args, **kwargs):
    return JsonResponse({"error": "not_implemented"}, status=501)


urlpatterns = [
    re_path(r"^.*$", not_implemented, name="not-implemented"),
]
