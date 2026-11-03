"""URL configuration."""

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import path

from todos import views


def health(request):
    return JsonResponse({"status": "ok"})


def index(request):
    return render(request, "index.html")


urlpatterns = [
    path("api/health", health),
    path("api/auth/register/", views.register),
    path("api/auth/login/", views.login),
    path("api/todos/", views.todos),
    path("api/todos/<int:todo_id>/", views.todo),
    path("", index),
]
