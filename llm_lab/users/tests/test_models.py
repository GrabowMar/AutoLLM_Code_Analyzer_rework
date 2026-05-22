import pytest

from llm_lab.users.models import User
from llm_lab.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_user_get_absolute_url(user: User):
    assert user.get_absolute_url() == f"/users/{user.pk}/"


def test_marcin_becomes_admin_even_if_not_first():
    """Verify that a user with the email 'marcin27059@gmail.com' always becomes admin."""
    assert User.objects.count() == 0
    # First user is NOT marcin. They should NOT become admin because first-user promotion is skipped in tests.
    first_user = UserFactory.create(email="first@test.com")
    assert first_user.is_staff is False

    # A second user with a different email is NOT promoted.
    second_user = UserFactory.create(email="second@test.com")
    assert second_user.is_staff is False

    # A third user with marcin's email IS promoted.
    marcin_user = UserFactory.create(email="marcin27059@gmail.com")
    assert marcin_user.is_staff is True
    assert marcin_user.is_superuser is True
