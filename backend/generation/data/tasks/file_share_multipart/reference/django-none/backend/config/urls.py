"""URL configuration."""

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import path

from files_app import views


def health(request):
    return JsonResponse({"status": "ok"})


def index(request):
    return render(request, "index.html")


urlpatterns = [
    path("api/health", health),
    path("api/auth/register/", views.register),
    path("api/auth/login/", views.login),
    path("api/files/", views.files),
    path("api/files/<int:file_id>/", views.file_detail),
    path("api/files/<int:file_id>/download/", views.download),
    path("api/files/<int:file_id>/shares/", views.shares),
    path("api/files/<int:file_id>/shares/<str:token>/", views.share_detail),
    path("api/shared/<str:token>/", views.shared),
    path("", index),
]
