import pytest

from backend.users.models import User
from backend.users.tests.factories import UserFactory

# v2 generation code parked by WP-02a during the v3 cut-over; never collected (spec section 12.2).
collect_ignore = ["generation/_legacy"]


@pytest.fixture(autouse=True)
def _media_storage(settings, tmpdir) -> None:
    settings.MEDIA_ROOT = tmpdir.strpath


@pytest.fixture
def user(db) -> User:
    return UserFactory.create()
