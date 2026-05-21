from http import HTTPStatus

import pytest
from django.test import Client
from django.urls import reverse

from llm_lab.users.models import User
from llm_lab.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return UserFactory.create()


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
