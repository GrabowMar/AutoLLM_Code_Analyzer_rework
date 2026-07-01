from django.urls import resolve
from django.urls import reverse

from backend.users.models import User


def test_user_detail(user: User):
    assert reverse("api:retrieve_user", kwargs={"pk": user.pk}) == f"/api/users/{user.pk}/"
    assert resolve(f"/api/users/{user.pk}/").view_name == "api:retrieve_user"


def test_user_list():
    assert reverse("api:list_users") == "/api/users/"
    assert resolve("/api/users/").view_name == "api:list_users"


def test_current_user():
    assert reverse("api:retrieve_current_user") == "/api/users/me/"
    assert resolve("/api/users/me/").view_name == "api:retrieve_current_user"


def test_bootstrap_status():
    assert reverse("api:bootstrap_status") == "/api/users/bootstrap-status/"
    assert resolve("/api/users/bootstrap-status/").view_name == "api:bootstrap_status"


def test_create_bootstrap_admin():
    assert reverse("api:create_bootstrap_admin") == "/api/users/bootstrap-admin/"
    assert resolve("/api/users/bootstrap-admin/").view_name == "api:create_bootstrap_admin"


def test_update_user():
    assert reverse("api:update_user", kwargs={"pk": 123}) == "/api/users/123/"
    assert resolve("/api/users/123/").view_name == "api:retrieve_user"
