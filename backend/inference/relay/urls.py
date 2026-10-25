"""/api/relay/v1/ — relay protocol v1 (spec §E.2), mounted before ``api/`` in config/urls.py."""

from __future__ import annotations

from django.urls import path

from backend.inference.relay import views

app_name = "relay"

urlpatterns = [
    path("hello", views.hello, name="hello"),
    path("inventory", views.inventory, name="inventory"),
    path("poll", views.poll, name="poll"),
    path("requests/<uuid:rid>/result", views.result, name="result"),
    path("bye", views.bye, name="bye"),
    path("relay.py", views.relay_script, name="script"),
    path("Dockerfile", views.relay_dockerfile, name="dockerfile"),
    path("llmlab-relay.service", views.relay_systemd_unit, name="systemd-unit"),
]
