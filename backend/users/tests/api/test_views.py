from http import HTTPStatus

import pytest
from django.test import Client
from django.urls import reverse

from backend.users.models import User
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return UserFactory.create()


def test_bootstrap_status_without_users(client: Client):
    response = client.get(reverse("api:bootstrap_status"))

    assert response.status_code == HTTPStatus.OK
    assert response.json()["requires_bootstrap"] is True
    assert response.json()["default_email"]


def test_bootstrap_status_with_users(client: Client, user: User):
    response = client.get(reverse("api:bootstrap_status"))

    assert response.status_code == HTTPStatus.OK
    assert response.json()["requires_bootstrap"] is False
    assert response.json()["default_email"].startswith("admin@")


def test_create_bootstrap_admin_without_email(client: Client, settings):
    settings.DJANGO_DOMAIN = "dev1.grabowmar.ovh"

    response = client.post(
        reverse("api:create_bootstrap_admin"),
        data='{"name": "Bootstrap Admin", "password": "Monitor69!", "remember": true}',
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.OK, response.json()
    created_user = User.objects.get()
    assert created_user.email == "admin@dev1.grabowmar.ovh"
    assert created_user.name == "Bootstrap Admin"
    assert created_user.is_staff is True
    assert created_user.is_superuser is True
    assert str(created_user.pk) == client.session["_auth_user_id"]


def test_create_bootstrap_admin_rejected_when_user_exists(client: Client, user: User):
    response = client.post(
        reverse("api:create_bootstrap_admin"),
        data='{"name": "Bootstrap Admin", "password": "Monitor69!"}',
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {"detail": "Bootstrap admin already exists."}


def test_list_users_as_anonymous_user(client: Client):
    response = client.get(reverse("api:list_users"))

    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_list_users_as_authenticated_user(client: Client, user: User):
    client.force_login(user)
    # Another user, excluded from the response
    UserFactory.create()

    response = client.get(reverse("api:list_users"))

    assert response.status_code == HTTPStatus.OK
    assert response.json() == [
        {
            "email": user.email,
            "name": user.name,
            "url": f"/api/users/{user.pk}/",
            "is_staff": user.is_staff,
        },
    ]


def test_retrieve_current_user(client: Client, user: User):
    client.force_login(user)

    response = client.get(
        reverse("api:retrieve_current_user"),
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        "email": user.email,
        "name": user.name,
        "url": f"/api/users/{user.pk}/",
        "is_staff": user.is_staff,
    }


def test_retrieve_user(client: Client, user: User):
    client.force_login(user)

    response = client.get(
        reverse("api:retrieve_user", kwargs={"pk": user.pk}),
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        "email": user.email,
        "name": user.name,
        "url": f"/api/users/{user.pk}/",
        "is_staff": user.is_staff,
    }


def test_retrieve_another_user(client: Client, user: User):
    client.force_login(user)
    user_2 = UserFactory.create()

    response = client.get(
        reverse("api:retrieve_user", kwargs={"pk": user_2.pk}),
    )

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"detail": "Not Found"}


def test_update_current_user(client: Client):
    user = UserFactory.create(name="Old")
    client.force_login(user)

    response = client.patch(
        reverse("api:update_current_user"),
        data='{"name": "New Name"}',
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.OK, response.json()
    assert response.json() == {
        "email": user.email,
        "name": "New Name",
        "url": f"/api/users/{user.pk}/",
        "is_staff": user.is_staff,
    }


def test_update_user(client: Client):
    user = UserFactory.create(name="Old")
    client.force_login(user)

    response = client.patch(
        reverse("api:update_user", kwargs={"pk": user.pk}),
        data='{"name": "New Name"}',
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.OK, response.json()
    assert response.json() == {
        "email": user.email,
        "name": "New Name",
        "url": f"/api/users/{user.pk}/",
        "is_staff": user.is_staff,
    }
